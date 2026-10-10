"""Reconcile and the tick's policies (TD-108 step 1): what the host agent does every tick — observe the
panes, the stop times, the usage gate, keeping a team running (design §6), git and model reads, the reports
— as a mixin `HostAgent` inherits. Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    hosts,
    mail,
    naming,
    paths,
    reports,
    workorders,
)
from sessionorc import balance as balance_mod
from sessionorc import brief as brief_mod
from sessionorc import cadence as cadence_mod
from sessionorc import conventions as conventions_mod
from sessionorc import held as held_mod
from sessionorc import ledger as ledger_mod
from sessionorc import promote as promote_mod
from sessionorc import settings as settings_mod
from sessionorc import usage as usage_mod
from sessionorc import work as work_mod
from sessionorc.agent_common import (
    BRIEF_SETTLE,
    COMPOSER_LINES,
    CONTEXT_AGAIN,
    DERIVE_EVERY,
    FILL_CEILING,
    FILL_WINDOW,
    GIT_EVERY,
    IDLE_NUDGE,
    LANE_NEWS_NAMED,
    LAUNCH_KEYS,
    LEASE_TTL,
    MODEL_EVERY,
    OWED_NAMED,
    PRUNE_EVERY,
    REMOVED_GUARD_SECONDS,
    REPOS_EVERY,
    RESTART_CEILING,
    RESTART_WINDOW,
    RESUME_MIN,
    SEAT_IDLE_GRACE,
    SEAT_PR_WAIT,
    SETTLED,
    STALL_AFTER,
    TAIL_LINES,
    RpcError,
    _cap,
    _clean,
    _cool_left,
    _duration,
    _is_branch_claim,
    _pane_title,
    _parse,
    _recent,
    _usage_checked_at,
    _usage_key,
    backup_store,
    log,
)
from sessionorc.agent_spend import _metered_of
from sessionorc.gitinfo import UNKNOWN as GIT_UNKNOWN
from sessionorc.gitinfo import changed_files, git_info, work_left
from sessionorc.models import (
    PERSON,
    PR_CLOSED,
    RECENT_FILES,
    SYSTEM,
    Pending,
    Session,
    context_over_text,
    lane_refs,
    now_iso,
)
from sessionorc.tmux import PaneInfo


def _asked(questions: list[Any], members: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """A `work_waiting` mark's members with each question's reference under the name that asked it
    (§6 rule 8 *A question's end is work*, TD-274): `members` (a lane's new ids) copied, or none."""
    out = {m: list(ids) for m, ids in (members or {}).items()}
    for q in questions:
        got = out.setdefault(str(q.get("name")), [])
        if str(q.get("ref")) not in got:
            got.append(str(q.get("ref")))
    return out


FILLED_FOR = "[agentorc] you are filled for: "
# `restarts` entries that are not the tick's restarts, left out of rule 9's note (§4.9a, TD-468): a
# person's Restart, and a team's start by a schedule or by rule 8
NOT_RESTARTS = ("person", "start", "work")


def filled_for(cause: dict[str, Any] | None) -> str | None:
    """The closing line of a manager on call's fill prompt (§6 rule 3 *A fill says why it came*,
    TD-410), in fixed words from its `seat_due`: *[agentorc] you are filled for: <by> — <member>*, the
    question's id for `asks`. None for any other fill, which says nothing more."""
    if not cause or not cause.get("by"):
        return None
    what = cause.get("ask") if cause["by"] == "asks" else cause.get("member")
    return f"{FILLED_FOR}{cause['by']}" + (f" — {what}" if what else "")


def _carry_live(reading: dict[str, Any], old: dict[str, Any]) -> None:
    """A live check in `reading` takes the `live` the same id held in `old`, the checkout's last
    reading, when the live commit is unknown (`_read_repos`, TD-516); one `old` does not hold keeps
    the `no` it was read with."""
    was = {
        str(e.get("id")): e["live"]
        for e in (old.get("ledger") or {}).get("entries") or []
        if isinstance(e, dict) and e.get("kind") == "live-check" and e.get("live") in ("yes", "no")
    }
    for e in (reading.get("ledger") or {}).get("entries") or []:
        if isinstance(e, dict) and e.get("kind") == "live-check" and str(e.get("id")) in was:
            e["live"] = was[str(e.get("id"))]


class TickMixin:
    # -- reconcile -------------------------------------------------------------------------------
    #
    # Concurrency model: every mutation of `self.sessions` and of Session objects happens on the
    # event loop, never in a thread. Only the tmux subprocess calls run in threads, and they
    # return plain data. So a tick's reconcile step and an RPC handler can never interleave
    # inside a check-then-act sequence — the loop runs them one at a time.

    async def tick(self) -> None:
        snapshot_at = datetime.now(UTC)
        panes = await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)
        self._id_note_panes(panes)
        self._id_flush()
        await self._id_recheck_detached()
        tails = await asyncio.to_thread(lambda: {sid: self.tmux.capture_tail(sid, TAIL_LINES) for sid in panes})
        self._reconcile(panes, tails, snapshot_at)
        self._note_tick(snapshot_at)
        self._note_attention(snapshot_at)
        try:
            self._notify_pass(snapshot_at)
        except Exception:  # noqa: BLE001 — telling the person is never worth a tick
            log.exception("the Telegram pass failed")
        await self._refresh_git(snapshot_at)
        await self._refresh_model(snapshot_at)
        await self._refresh_context(snapshot_at)
        if self._derive_task is None or self._derive_task.done():
            # detached for the same reason the usage refresh is: `gh` talks to the network, and the
            # tick and its push must not wait on it (review 2026-09-11)
            self._derive_task = asyncio.create_task(self._derive_reports(snapshot_at))
        if snapshot_at - self._pruned_at > PRUNE_EVERY:
            self._pruned_at = snapshot_at
            # the live set is read here, on the loop (the class's one-writer rule); only the file
            # work goes to the thread
            ended = ("exited", "closed")
            live = {s.run_log for s in self.sessions.values() if s.run_log and s.state not in ended}
            # a round log goes with the last record of its name (§4.6, TD-191): kept while one is live
            live |= {str(self._rounds_log(s)) for s in self.sessions.values() if s.state not in ended}
            # an attachment's folder is kept whole while any record of its session is live (§4.4, TD-469)
            live_ids = {s.id for s in self.sessions.values() if s.state not in ended}
            await asyncio.to_thread(self._prune_runs, snapshot_at, live, live_ids)
        if self.mode == "home":
            self._supervise_containers()
            day = datetime.now().astimezone().date().isoformat()
            if day != self._backed_up and (self._backup_task is None or self._backup_task.done()):
                self._backed_up = day  # tried once a day: a failure is a log line and tomorrow's retry
                self._backup_task = asyncio.create_task(self._backup(day))
        if self.mode == "home" and (self._repos_task is None or self._repos_task.done()):
            # detached as the usage refresh is: `gh` talks to the network (§4.4 *Repo facts*)
            self._repos_task = asyncio.create_task(self._refresh_repos())
        if self.mode == "home":
            await self._balance_marks(snapshot_at)
        if self.mode == "home" and (self._promote_task is None or self._promote_task.done()):
            # detached as the repo facts are: a fetch, `gh` and a repo's own `check` (§6 *Promote*)
            self._promote_task = asyncio.create_task(self._refresh_promotes())
        if self._usage_task is None or self._usage_task.done():
            # detached: a slow usage endpoint (10 s timeout) must not hold up the tick or its push
            self._usage_task = asyncio.create_task(self._refresh_usage())
        if self._spend_task is None or self._spend_task.done():
            # detached as the usage poll is: it reads transcripts (§4.4 *Usage*, TD-151); a node's
            # pass sends its turns home over the link (slice 4)
            self._spend_task = asyncio.create_task(self._refresh_spend())
        await self._team_stop_times(snapshot_at)
        await self._enforce_stop_times(snapshot_at)
        await self._enforce_usage_gate(snapshot_at)
        await self._keep_running(snapshot_at)
        await self._finished_pass(snapshot_at)
        await self._work_marks(snapshot_at)
        await self._sweep_mail(snapshot_at)
        self._poke_waits()  # the wake decision is re-taken every tick for a session blocked in `wait`
        self._send_first_prompts()  # before the doorbell, which a brief not yet typed holds off
        self._ring_doorbells()

    async def _team_stop_times(self, now: datetime) -> None:
        """Design §6 *Team stop time* (TD-146): each live, unattended session carrying a team's badge
        — members and seats, this host's and every node's — whose `run_until` is unset or later than
        its team's `teams.<team>.until` takes the team's instant, exactly as `set_stop` gives one. A
        session's own earlier stop time is kept. A record created after the instant had passed is not
        given it: a team started again after its stop time is the person's word, not a stop.
        **At the home**, whose file it is; a node's record takes it through `set_stop` over the link,
        and waits for the link when it is down."""
        if self.mode != "home":
            return
        untils = {t: v["until"] for t, v in settings_mod.teams(settings_mod.load()).items() if v.get("until")}
        if not untils:
            return
        changed = False
        for s in [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]:
            inst = untils.get(s.team) if s.team else None
            if not inst or not s.unattended or s.state in ("exited", "closed") or s.superseded_by:
                continue
            if s.run_until and _parse(s.run_until) <= _parse(inst):
                continue  # its own earlier stop time, or the team's already
            with contextlib.suppress(TypeError, ValueError):
                if _parse(s.created) >= _parse(inst):
                    continue
            if s.host == self.host:
                self._take_stop(s, inst)
                changed = True
            elif s.host in self._link_muxes:
                try:
                    await self._route_act("set_stop", {"id": self._address(s), "run_until": inst}, None, s.host)
                except Exception as e:  # noqa: BLE001 — tried again on the next tick
                    log.warning("%s: the team's stop time did not reach %s: %s", s.id, s.host, e)
        if changed:
            await self._push_changes()

    def _team_stamp(self, team: str, unattended: bool, run_until: str | None) -> str | None:
        """What a create's `run_until` is under its team's stop time (§6 *Team stop time*): the
        earlier of the two while the team's is still ahead; its own, or none, otherwise — a start
        after the instant has passed is not stopped by it."""
        if not (team and unattended):
            return run_until
        inst = (settings_mod.teams(settings_mod.load()).get(team) or {}).get("until")
        if not inst or _parse(inst) <= datetime.now(UTC):
            return run_until
        return inst if not run_until or _parse(inst) < _parse(run_until) else run_until

    def _take_stop(self, s: Session, when: str | None) -> None:
        """A stop time given by a policy as `set_stop` gives one: a new time is a new run, so a wrap-up
        already asked is spent."""
        if when != s.run_until:
            s.wrapup_sent_at = None
        s.run_until = when
        self._save(s)

    async def _restamp_team(self, team: str, old: str | None, new: str | None) -> None:
        """A team's stop time moved or was cleared (TD-146): the live members that carry the old
        instant took it from the team, so they follow — to the new one, or to none on **Clear**. A
        member's own earlier stop time is another instant and is not touched."""
        if not old or old == new:
            return
        changed = False
        for s in [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]:
            if s.team != team or s.run_until != old or s.state in ("exited", "closed") or s.superseded_by:
                continue
            if new and not s.unattended:
                # a person took it over: a move is a policy's, and policies leave it alone (§4.2); a Clear clears
                continue
            if s.host == self.host:
                self._take_stop(s, new)
                changed = True
            elif s.host in self._link_muxes:
                try:
                    await self._route_act("set_stop", {"id": self._address(s), "run_until": new}, None, s.host)
                except Exception as e:  # noqa: BLE001 — the tick's pass gives a new instant again; a clear waits
                    log.warning("%s: the team's changed stop time did not reach %s: %s", s.id, s.host, e)
        if changed:
            await self._push_changes()

    async def _enforce_stop_times(self, now: datetime) -> None:
        """Stop the unattended sessions whose time is up (design §6, TD-026 gap 1).

        A session started by hand with `--unattended` used to have no stopper at all: nothing wrapped
        it up at 06:00, at a usage cap or when its token lapsed, so "start it now so I can watch it"
        meant "remember to close it yourself". A `run_until` is the general form, and the weekly run
        window (phase 3) becomes one way of setting it rather than a second mechanism.

        Two steps, in the order a person would use: at the time, the session is asked to wrap up —
        once, with the words the client gave at create, because this package must not know what a
        brief is. Then it is killed when it settles (its work is done, or it is waiting on a person
        who is not there) or when `WRAPUP_GRACE` runs out, whichever comes first. Interactive
        sessions are never touched: a stop time is a policy, and policies apply to unattended
        sessions alone (§4.2).
        """
        for s in list(self.sessions.values()):
            if not (s.unattended and s.run_until) or s.state in ("exited", "closed", "scheduled"):
                continue  # a scheduled record has nothing running to stop yet (§6 *Start time*)
            if now < _parse(s.run_until):
                continue
            if not s.wrapup_sent_at:
                if s.pending and s.pending.kind in ("permission", "question"):
                    # A session stopped on a dialog cannot wrap up, and typing at it would answer
                    # the dialog rather than reach the composer (`send` refuses for this reason,
                    # §4.2). Nobody is coming to answer it either — that is what unattended means —
                    # so it is stopped now rather than asked something it cannot hear.
                    log.info("%s reached its run_until on a pending %s; stopping it", s.id, s.pending.kind)
                    await self.rpc_kill(s.id, killer={"by": "tick", "why": "stop time"})
                    continue
                if not s.wrapup_prompt:
                    # Nothing to say, so say nothing and stop it: a stop time with no wrap-up text is
                    # still a stop time, and silently running past it is the failure this fixes.
                    log.info("%s reached its run_until with no wrap-up prompt; stopping it", s.id)
                    await self.rpc_kill(s.id, killer={"by": "tick", "why": "stop time"})
                    continue
                log.info("%s reached its run_until (%s); asking it to wrap up", s.id, s.run_until)
                try:
                    await self._submit(s.id, adapters.get(s.adapter), s.wrapup_prompt)
                except Exception:  # noqa: BLE001 — a pane that will not take a prompt is killed below
                    log.warning("%s would not take the wrap-up prompt; it will be stopped anyway", s.id)
                s.wrapup_sent_at = now_iso()
                self.store.save(s)
                await self._push_changes()
                continue
            settled = s.state in SETTLED
            if settled or now - _parse(s.wrapup_sent_at) >= agent_common.WRAPUP_GRACE:
                log.info("%s stopped after its wrap-up (%s)", s.id, "settled" if settled else "grace ran out")
                await self.rpc_kill(s.id, killer={"by": "tick", "why": "stop time"})

    async def _enforce_usage_gate(self, now: datetime) -> None:
        """Pause the unattended sessions of a profile whose usage crossed a line, and resume them
        when every window is back under (design §6 *Usage gate*, TD-100).

        The lines come from the person's reserves in `settings.yml`, read here on every tick, and
        the windows from the profile's last good usage reading — a failed poll keeps the last one,
        and a profile with no reading at all neither pauses nor resumes anything. A pause is a
        **send, not a kill**: the mark `gated` is written the tick the line is crossed, the record's
        `pause_prompt` is typed once and retried every tick until it lands (`sent_at`), and a session
        sitting on a permission or a question is not typed at — waiting on a dialog, it is consuming
        nothing, so the mark stands and the send lands after the dialog clears. The resume is the
        `resume_prompt`, typed when every window is under its line and no sooner than `RESUME_MIN`
        after the pause; the mark goes with it. Interactive sessions are never gated: a session a
        person took over loses its mark on the next tick, with nothing typed.

        A session carrying a team's badge whose team has a **reserve priority** (`teams.<team>.
        reserve`, TD-146) pauses at its profile's line lowered by that much, on every window with a
        reserve, and its mark carries `team_extra: {team, n}` so the card can say which line it was."""
        whole = settings_mod.load()
        changed = False
        for s in list(self.sessions.values()):
            if not s.unattended or s.state in ("exited", "closed", "scheduled"):
                if s.gated:
                    s.gated = None  # the gate's reach is unattended, live sessions alone (§9 invariant 5)
                    self.store.save(s)
                    changed = True
                continue
            by_label = self._gate_reserves(whole, s.profile)
            windows = self._gate_windows(s.profile, now, whole)
            if windows is None and by_label:
                continue  # no reading: the last word stands, whatever it was (§6: a failure never gates)
            extra = settings_mod.team_extra(whole, s.team)
            rows = settings_mod.lines(by_label or {}, windows, now, extra)
            over = settings_mod.crossed(rows)
            if over is not None:
                mark = {
                    "profile": s.profile,
                    "label": over["label"],
                    "pct": over["pct"],
                    "line": over["line"],
                    "since": (s.gated or {}).get("since") or now_iso(),
                    "next": over["next"],
                    "resets": over["resets"],  # so a card can say *resets* when `next` is the reset
                    "sent_at": (s.gated or {}).get("sent_at"),
                    **({"team_extra": {"team": s.team, "n": extra}} if extra else {}),
                    **({"projected": over["projected"]} if over.get("projected") else {}),
                }
                was = s.gated or {}
                if (
                    was.get("projected")
                    and mark.get("projected")
                    and (was.get("label"), was.get("line")) == (mark["label"], mark["line"])
                    and abs(float(was.get("pct") or 0) - float(mark["pct"] or 0)) < 1
                ):
                    mark = was  # a projection grows by the second: rewrite the mark only as it moves a point
                if mark != s.gated:
                    if not s.gated:
                        log.info("%s paused by the usage gate: %s %s %s%% >= %s%%", s.id, s.profile,
                                 over["label"], over["pct"], over["line"])  # fmt: skip
                    s.gated = mark
                    self.store.save(s)
                    changed = True
                if not s.gated.get("sent_at") and s.pause_prompt and not self._on_a_dialog(s):
                    try:
                        await self._submit(s.id, adapters.get(s.adapter), s.pause_prompt)
                    except Exception:  # noqa: BLE001 — retried on the next tick (§6)
                        log.warning("%s would not take the pause prompt; retrying next tick", s.id)
                    else:
                        s.gated = {**s.gated, "sent_at": now_iso()}
                        self.store.save(s)
                        changed = True
                continue
            if not s.gated:
                continue
            if now - _parse(s.gated["since"]) < RESUME_MIN:
                continue
            if any(row["label"] == s.gated.get("label") and row.get("unknown") == "rate" for row in rows):
                continue  # §6: a pause ends on a reading, never on an old one that has no rate to project by
            if s.gated.get("sent_at") and s.resume_prompt:
                if self._on_a_dialog(s):
                    continue
                try:
                    await self._submit(s.id, adapters.get(s.adapter), s.resume_prompt)
                except Exception:  # noqa: BLE001 — the mark stands and the resume is tried again
                    log.warning("%s would not take the resume prompt; retrying next tick", s.id)
                    continue
            log.info("%s resumed by the usage gate", s.id)
            s.gated = None
            self.store.save(s)
            changed = True
        self._projection_notes(whole, now)
        if changed:
            await self._push_changes()

    def _gate_windows(self, profile: str, now: datetime, whole: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The gate's **one reader** (§6 *A reading the gate can no longer trust*, TD-233 slice 4):
        the profile's windows as the gate reads them — the reading, or past `usage.max_age` its
        projection (`usage.project`) — which the gate's pass, `_profile_gated` and `gate` all ask, so
        a profile paused on a projection is not restarted or filled the next tick. A metered
        profile's windows are its spend, which does not age, and are never projected."""
        reading = self._gate_reading(profile)
        windows = reading.get("windows")
        if windows is None or self._is_metered(profile):
            return windows
        return usage_mod.project(windows, now, settings_mod.max_age(whole), reading.get("fetched"))

    def _gate_reading(self, profile: str) -> dict[str, Any]:
        """The reading the gate judges `profile` by: its own copy, else its **account's** (TD-456).
        A copy is kept only under a profile a live session runs under (the chip's rule, TD-073), so a
        team wound down while a person's session keeps the account read would start under its own
        profile with *no reading*, which is no gate. A metered profile's reading is its own spend."""
        own = self._usage.get(profile)
        if own is not None or self._is_metered(profile):
            return own or {}
        # the profile's own tool first — the adapter of a record under it — so another tool's
        # account of the same name is never read for it; every polling adapter only for a profile no
        # record names
        mine = list(dict.fromkeys(s.adapter for s in self.sessions.values() if s.profile == profile))
        for name in mine or adapters.names():
            if name not in adapters.names():
                continue
            ad = adapters.get(name)
            if not getattr(ad, "usage_for", None):
                continue
            key, account = _usage_key(ad, name, profile)
            if (acct := self._usage_acct.get(key)) is not None:
                return {**acct, "account": account, "tool": str(getattr(ad, "label", "") or name)}
        return {}

    def _projection_notes(self, whole: dict[str, Any], now: datetime) -> None:
        """The person's two FYI notes on an old reading (§6 *A reading the gate can no longer
        trust*): *pausing on a projection*, once per account while a pause on a projection stands,
        and *usage unknown*, once per account per day, for a window with a reserve that is unknown
        (no rate to project by, or past its reset for longer than `max_age`) while its unattended
        sessions work. Neither under `max_age: off`."""
        limit = settings_mod.max_age(whole)
        if limit is None:
            self._projection_noted.clear()
            return
        paused: dict[str, dict[str, Any]] = {}
        unknown: dict[str, tuple[dict[str, Any], int]] = {}
        for s in self.sessions.values():
            if not s.unattended or s.state in ("exited", "closed", "scheduled"):
                continue
            name = self._usage_name(s.profile)
            if (s.gated or {}).get("projected"):
                paused.setdefault(name, s.gated)
            if s.state != "working":
                continue
            by_label = self._gate_reserves(whole, s.profile) or {}
            for row in settings_mod.lines(by_label, self._gate_windows(s.profile, now, whole), now):
                gone = self._unknown_for(row, now)
                if gone is not None and gone >= limit:
                    got = unknown.get(name)
                    unknown[name] = ({**row, "for": gone}, (got[1] if got else 0) + 1)
                    break
        for name in [n for n in self._projection_noted if n not in paused]:
            self._projection_noted.discard(name)
        for name, mark in paused.items():
            if name in self._projection_noted:
                continue
            self._projection_noted.add(name)
            pr = mark["projected"]
            self._system_note(
                PERSON,
                f"pausing on a projection: no reading of {name} for {_span(pr['age'])} — projected "
                f"{mark['label']} {mark['pct']:g}% ≥ {mark['line']}%, from {pr['from']}% at {pr['rate']:g} a hour. "
                "The pause ends on a reading under the line, at the window's reset, or when max_age or a "
                "reserve is moved (design §6 Usage gate).",
            )
        today = now.date().isoformat()
        for name, (row, n) in unknown.items():
            if self._unknown_noted.get(name) == today:
                continue
            self._unknown_noted[name] = today
            self._system_note(
                PERSON,
                f"usage unknown for {_span(row['for'])}: {name} {row['label']} (was {row['pct']}%), "
                f"{n} unattended session{'s' if n != 1 else ''} working — the gate cannot see this window, "
                "so it pauses nothing on it (design §6 Usage gate).",
            )

    @staticmethod
    def _unknown_for(row: dict[str, Any], now: datetime) -> float | None:
        """How long, in seconds, a gate row has been unknown: its reading's age for `rate`, the
        time since its reset for `reset`; None for a row the gate can read."""
        if row.get("unknown") == "rate":
            return float(row.get("age") or 0)
        if row.get("unknown") == "reset" and (t := usage_mod._instant(row.get("resets"))) is not None:
            return (now - t).total_seconds()
        return None

    def _usage_name(self, profile: str) -> str:
        """The account's name as the chip prints it, *Claude · paul*, else the profile's own."""
        reading = self._usage.get(profile) or {}
        tool, account = reading.get("tool"), reading.get("account")
        return f"{tool} · {account}" if tool and account else str(account or profile)

    async def _keep_running(self, now: datetime) -> None:
        """Design §6 *Keeping a team running* (TD-103): rule 1, the crash restart; rule 2, the wanted
        restart; rule 3, the seats; rule 4, the idle nudge; rule 5, the context bound (TD-190);
        rule 6, new work in a lane (TD-195).
        **The restarts run at the home** (§4.4a: policies that start run at the home), over this
        host's records and every node's — a member on a host whose link is down is left as it is and
        looked at again on the next tick, refused rather than queued. **Each record's pass is
        isolated**: one's exception is logged and the tick goes on to the next."""
        if self.mode != "home":
            return
        records = [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]
        for s in records:
            try:
                await self._scheduled_start(s, now)
                await self._crash_restart(s, now)
                await self._wanted_restart(s, now)
                await self._seat_pass(s, now, records)
                await self._sit_out_close(s)
                await self._brief_restart(s, now)  # first: a member it restarts is typed nothing else
                await self._idle_nudge(s, now)
                await self._idle_open(s, now)
                await self._context_line(s, now)
                await self._cadence_line(s, now)
                await self._held_line(s, now)
                await self._lane_news(s, now)
            except Exception:  # noqa: BLE001 — one record's failure is never the tick's (§6)
                log.exception("%s: the keep-running pass failed", self._address(s))
        if (
            self._seat_count_task is None or self._seat_count_task.done()
        ) and now - self._seat_counted_at > DERIVE_EVERY:
            # detached, as the reports are: `gh` talks to the network, and the tick must not wait on it
            self._seat_counted_at = now
            self._seat_count_task = asyncio.create_task(self._count_seats(records))
        if (self._cadence_task is None or self._cadence_task.done()) and now - self._cadence_read_at > DERIVE_EVERY:
            self._cadence_read_at = now  # rule 10: the script talks to GitHub, so detached as well
            self._cadence_task = asyncio.create_task(self._cadence_pass(records))
        if (
            self._conventions_task is None or self._conventions_task.done()
        ) and now - self._conventions_read_at > DERIVE_EVERY:
            self._conventions_read_at = now  # rule 12: the script runs git, so detached as well
            self._conventions_task = asyncio.create_task(self._conventions_pass(records))
        if (self._held_task is None or self._held_task.done()) and now - self._held_read_at > DERIVE_EVERY:
            self._held_read_at = now  # rule 11: one `gh pr view` a merged PR, so detached as well
            self._held_task = asyncio.create_task(self._held_pass(records, now))
        if (self._brief_task is None or self._brief_task.done()) and now - self._brief_read_at > DERIVE_EVERY:
            self._brief_read_at = now
            self._brief_task = asyncio.create_task(self._brief_pass(now))
        if (
            self.mode == "home" and (self._defs_task is None or self._defs_task.done())
        ) and now - self._defs_read_at > DERIVE_EVERY:
            # a hand edit of the three files is committed on the reports' cadence (§4.9), detached
            self._defs_read_at = now
            self._defs_task = asyncio.create_task(self._commit_defs("edited by hand"))

    async def _brief_pass(self, now: datetime) -> None:
        """Rule 7's mark (design §6 *Keeping a team running*, TD-217 slice 3): each live record of
        this host with a `brief` has its sources read again as merged, in a thread; a difference
        that has stood `BRIEF_SETTLE` with the same blob ids writes `brief_changed: {at, paths}`,
        and files that read as recorded again take it away. A node's member is not read: its files
        are that host's (as the replay, slice 2)."""
        live = [
            s for s in self.sessions.values() if s.brief and s.state not in ("exited", "closed") and not s.superseded_by
        ]
        try:
            read = await asyncio.to_thread(lambda: {s.id: brief_mod.changed(s.brief) for s in live})
        except Exception:  # noqa: BLE001 — a failed read is no mark, never the tick's
            log.exception("the brief pass failed")
            return
        for sid in [k for k in self._brief_differs if k not in read]:
            del self._brief_differs[sid]
        dirty = False
        for s in live:
            if self.sessions.get(s.id) is not s or s.state in ("exited", "closed") or s.superseded_by:
                continue  # forgotten, replaced or ended while the files were read: never saved back
            paths, shas, whole = read[s.id]
            if not whole:
                continue  # a source not read this time says nothing: the mark and the settle stand
            if not paths:
                self._brief_differs.pop(s.id, None)
                if s.brief_changed is not None:
                    s.brief_changed, dirty = None, True
                    self._save(s)
                continue
            was = self._brief_differs.get(s.id)
            if was is None or was[0] != shas:  # a new difference, or another merge: the settle restarts
                self._brief_differs[s.id] = was = (shas, now)
            if now - was[1] >= BRIEF_SETTLE and (s.brief_changed or {}).get("paths") != paths:
                s.brief_changed, dirty = {"at": was[1].isoformat().replace("+00:00", "Z"), "paths": paths}, True
                self._save(s)
        if dirty:
            await self._push_changes()

    def _crashed(self, s: Session, now: datetime) -> bool:
        """Rule 1's test: a supervised, unattended member that is not a seat, `exited` by a
        **natural exit** — `pane` true, the tool left on its own; a kill takes the pane and is a
        person's or a controller's act, never undone — with no declaration, no wrap-up asked, no
        stop time passed, not gated, not suspended, and not already at its ceiling or superseded."""
        return bool(
            s.supervised
            and s.unattended
            and s.seat is None
            and s.state == "exited"
            and s.pane
            and not s.out_of_work
            and not s.restart_wanted
            and not s.wrapup_at
            and not s.wrapup_sent_at
            and not (s.run_until and now >= _parse(s.run_until))
            and not s.gated
            and not s.suspended
            and not s.restart_ceiling
            and not s.superseded_by
            and not s.sit_out
            and not work_mod.sat_out(s)
            and not self._profile_gated(s.profile, now, s.team)
            and not self._just_restarted(s, now)
        )

    @staticmethod
    def _just_restarted(s: Session, now: datetime) -> bool:
        """Whether the tick's last restart of `s` succeeded less than `RESTART_SETTLE` ago (TD-186):
        the record is the new run, whatever the ending run it replaced reports meanwhile."""
        last = s.restarts[-1] if s.restarts and isinstance(s.restarts[-1], dict) else None
        return bool(last and not last.get("error") and _recent(last.get("at"), now, agent_common.RESTART_SETTLE))

    def _profile_gated(self, profile: str, now: datetime, team: str = "") -> bool:
        """Whether `profile` is over a usage line now (`_profile_over`'s reading, as a yes or no)."""
        return self._profile_over(profile, now, team) is not None

    def _profile_over(self, profile: str, now: datetime, team: str = "") -> dict[str, Any] | None:
        """The window `profile` is over its usage line in now, or None (§6 *Usage gate*): the gate's own
        reading — the row `settings.crossed` names, with its `resets` — for a record the gate no longer
        marks (it clears `gated` on an exited one), so a policy does not restart or fill into a pause.
        No reading is no gate, as at the gate (a failure never gates).
        `team` is the record's: its reserve priority lowers the line exactly as it does at the gate
        (TD-146), or a teamed member would be restarted at 65% and paused on the next tick."""
        whole = settings_mod.load()
        windows = self._gate_windows(profile, now, whole)
        if windows is None:
            return None
        by_label = self._gate_reserves(whole, profile) or {}
        extra = settings_mod.team_extra(whole, team)
        return settings_mod.crossed(settings_mod.lines(by_label, windows, now, extra))

    async def _crash_restart(self, s: Session, now: datetime) -> None:
        """Rule 1 (design §6 *Keeping a team running*): restart a member that crashed by replaying
        its launch record — `create` again with what the last supervised create was handed, never
        the definition re-read — which supersedes the record in place under its name (§4.1). Each
        attempt is appended to `restarts` and the list is carried onto the new record, so the count
        survives the restart it counts; a replay that fails keeps its entry with `error` and counts
        all the same, so an unrepairable record reaches the ceiling within three ticks. At
        `RESTART_CEILING` inside `RESTART_WINDOW` the tick writes `restart_ceiling` and stops: the
        session is a person's."""
        if not self._crashed(s, now):
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        recent = agent_common._counted(s.restarts, now)
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s exited on its own with nothing declared: restarting it (%d in the window)", s.id, len(recent) + 1)
        await self._replay(s, "crash", keep_mail=True)

    async def _scheduled_start(self, s: Session, now: datetime) -> None:
        """Design §6 *Start time* (TD-152): a `scheduled` record whose instant has passed is created
        from its launch record — `restarts: [{why: start}]`, the record superseded in place with its
        mail — here or at its node; a node whose link is down waits and is looked at next tick. A
        failed start counts, as a failed replay does, and at `RESTART_CEILING` inside the window the
        tick stops trying and says so: the record is a person's."""
        if s.state != "scheduled" or not s.start_at or s.restart_ceiling:
            return
        try:
            due = _parse(s.start_at) <= now
        except ValueError:
            due = False
        if not due:
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        tries = [
            r
            for r in s.restarts
            if isinstance(r, dict) and r.get("why") == "start" and _recent(r.get("at"), now, RESTART_WINDOW)
        ]
        if len(tries) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(tries)}
            log.warning("%s: its scheduled start failed %d times — the ceiling; it is a person's now", s.id, len(tries))
            self._save(s)
            await self._push_changes()
            return
        log.info("%s: its start time %s has come — starting it", s.id, s.start_at)
        await self._replay(s, "start", start_of=s.id)

    async def _wanted_restart(self, s: Session, now: datetime) -> None:
        """Rule 2 (design §6, §4.9a): a supervised member that declared `restart_wanted` and is `idle`,
        or `exited` on its own — a kill or a Close is never undone — is closed if it is still there and
        restarted as rule 1 does, under the same ceiling, **only when its git fields are known and show
        nothing uncommitted and nothing unpushed**. With work left it is sent one fixed line naming the
        counts, and `IDLE_NUDGE` later carries `restart_blocked`; the moment the work reads pushed the
        restart runs. An `early` one is left to the person (§4.9a), and so is an unknown git state."""
        rw = s.restart_wanted
        if not (rw and s.supervised and s.unattended) or s.seat is not None or rw.get("early"):
            return
        if s.superseded_by or s.suspended or s.gated or s.sit_out or work_mod.sat_out(s):
            return  # a member its flow sits out is never started again by the tick (§4.9c)
        # A ceiling guards against a crash loop, not a member that has worked since (TD-186): a clean
        # declaration is acted on once the window holds fewer than the ceiling's restarts; the new
        # record carries no mark. Inside the window the mark stands, as it does for rule 1.
        if s.restart_ceiling and self._window_full(s, now):
            return
        closed_by_tick = self._closed_by_tick(s, "wanted")
        if not (s.state == "idle" or (s.state == "exited" and s.pane) or closed_by_tick):
            return
        if (s.run_until and now >= _parse(s.run_until)) or self._profile_gated(s.profile, now, s.team):
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        left = work_left(s.git)  # the one test a person's Restart and a team's stop make too (TD-250)
        if left == GIT_UNKNOWN:
            return  # an unknown git state is left alone (§6 rule 2)
        if left:
            await self._restart_held(s, now, s.git["dirty"], s.git["unpushed"])
            return
        recent = agent_common._counted(s.restarts, now)
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s wants another run and its work is pushed: restarting it", s.id)
        # the member's own why (*context bound*) kept on the entry, for rule 9's note (§4.9a, TD-468)
        said = {"said": str(rw["why"])} if rw.get("why") else {}
        if s.state == "idle":
            try:
                closer = {"by": "tick", "why": "wanted"}
                if s.host == self.host:
                    await self.rpc_close(s.id, closer=closer)
                else:
                    await self._route_act("close", {"id": s.id, "closer": closer}, None, s.host)
                self._mark_closed(s, "wanted")
            except Exception as e:  # noqa: BLE001 — a close that failed is a restart that failed, and counts
                # `rpc_close` marks the record closed before its own tail runs, so a failure there
                # leaves it `closed` with nothing replayed: the entry and `closed_for` are what let the
                # next tick retry it (`closed_by_tick`) rather than strand it (review of PR #461)
                if s.state == "closed":  # a close that failed before it marked the record leaves no mark
                    self._mark_closed(s, "wanted")
                s.restarts = [*s.restarts, {"at": now_iso(), "why": "wanted", **said, "error": f"close: {e}"}]
                log.warning("%s: the close before a wanted restart failed: %s", s.id, e)
                self._save(s)
                await self._push_changes()
                return
        await self._replay(s, "wanted", mark=said, keep_mail=True)

    async def _sit_out_close(self, s: Session) -> None:
        """A sit-out's end (design §4.9c *Switching*, TD-309 slice 5b): a record carrying `sit_out` — a
        person's Apply sent it the wrap-up — is closed by the tick with rule 9's close once it is
        settled (idle, its work known and pushed), and marked `closed_for: {why: sit_out, closed_at}`;
        one that exited, or that a person closed, in the meantime is marked as it stands, `closed_at`
        the close's or, for an exit, the time it exited. A working member finishes what it holds first,
        and one left with work stays open until it reads pushed: waiting tick after tick for it to
        settle is not a retry — no `restarts` entry is written and no replay follows. Never an
        interactive record (§9 invariant 5)."""
        if not s.sit_out or not s.unattended or s.superseded_by or work_mod.sat_out(s):
            return
        if s.state == "exited":
            s.closed_for = {"why": "sit_out", "closed_at": s.closed_at or s.since}
        elif s.state == "closed" or await self._finished_close(s, "sit_out"):
            self._mark_closed(s, "sit_out")
        else:
            return
        log.info("%s: sat out by its team's flow — %s", self._address(s), s.state)
        self._save(s)
        await self._push_changes()

    async def _brief_restart(self, s: Session, now: datetime) -> None:
        """Rule 7's second telling (design §6, TD-217 slice 4): a member whose record carries
        `brief_changed` and that is hook-confirmed `idle`, holds no claim in progress, has declared
        nothing, and has its git fields known and showing nothing uncommitted or unpushed is closed
        and replayed by the tick itself, `why: brief`, under the ceiling as every replay is. Never a
        seat, an interactive session, one past its stop time, into a wrap-up, a gate pause or a
        suspension, and not on a node yet. A working member is told on its `ao` replies instead.

        Its second trigger (§4.9c *Switching*, TD-309 slice 5): a record a person's Apply relaunched
        carries `relaunch`, and is restarted the same way under `why: flow` — the replay reads the
        launch record the relaunch wrote — whether or not the member declared out of work, since the run
        that declared is the one the Apply replaces (TD-358).

        Its third (§4.10 *A lapsed cache is started again, not rung*, TD-467) is the doorbell's, which
        closes and replays the member itself (`_cache_restart`); what reaches here is a `cache` restart
        whose replay failed, retried as the other two are, with the hours and tokens its first try read."""
        cache = self._closed_by_tick(s, "cache")
        why = "brief" if s.brief_changed else "flow" if s.relaunch else "cache" if cache else ""
        if not (why and s.supervised and s.unattended) or s.seat is not None or s.sit_out or work_mod.sat_out(s):
            return
        if s.superseded_by or s.suspended or s.gated or s.host != self.host:
            return
        if s.restart_wanted or (s.out_of_work and not s.relaunch):
            # it declared: rule 2 or the team's next start is what starts it — except that a person's
            # Apply is the person's word, and the old run's `none` does not stand against it (TD-358)
            return
        # either trigger's failed restart is retried under the one that stands now: a person's Apply on a
        # record a `brief` restart left closed clears `brief_changed`, and the mark it left is `brief` (TD-334)
        closed_by_tick = self._closed_by_tick(s, "brief") or self._closed_by_tick(s, "flow") or cache
        if not closed_by_tick:
            if s.state != "idle" or s.confidence != "hook" or s.pending:
                return
            if any(e.status == "claimed" and e.source == "declared" for e in s.progress):
                return  # a claim in progress: the clause on its `ao` replies says it
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if self._profile_gated(s.profile, now, s.team) or self._just_restarted(s, now):
            return
        git = s.git or {}
        clean = (
            git.get("dirty") == 0
            and git.get("unpushed") == 0
            and all(isinstance(git.get(k), int) for k in ("dirty", "unpushed"))
        )
        if not (closed_by_tick or clean):
            return  # work left, or not known: its replies keep saying it, and it declares when it can
        if self._window_full(s, now):
            if not s.restart_ceiling:
                recent = agent_common._counted(s.restarts, now)
                s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
                log.warning("%s: the %s changed at its restart ceiling — it is a person's now", s.id, why)
                self._save(s)
                await self._push_changes()
            return
        log.info("%s: its %s changed and it is idle with its work pushed: restarting it", s.id, why)
        if s.state == "idle":
            try:
                await self.rpc_close(s.id, closer={"by": "tick", "why": why})
                self._mark_closed(s, why)
            except Exception as e:  # noqa: BLE001 — a close that failed is a restart that failed, and counts
                if s.state == "closed":  # a close that failed before it marked the record leaves no mark
                    self._mark_closed(s, why)
                s.restarts = [*s.restarts, {"at": now_iso(), "why": why, "error": f"close: {e}"}]
                log.warning("%s: the close before a %s restart failed: %s", s.id, why, e)
                self._save(s)
                await self._push_changes()
                return
        elif not self._closed_by_tick(s, why):
            # a retry taken from the other trigger's mark: the mark follows the trigger it is replayed under,
            # just before the replay, so a replay that fails again is taken again next tick (TD-334)
            self._mark_closed(s, why)
        last = s.restarts[-1] if why == "cache" else {}
        await self._replay(s, why, mark={k: last[k] for k in ("idle", "context") if k in last}, keep_mail=True)

    async def _cache_restart(self, s: Session, now: datetime) -> bool:
        """§4.10 *A lapsed cache is started again, not rung* (TD-459, built by TD-467): the doorbell has
        decided and charged a ring for `s`; a member idle past `CACHE_LIFETIME` with a context over
        `CACHE_FLOOR` that meets rule 7's precondition is closed and replayed on its brief instead, `why:
        cache` with its `idle` hours and `context` tokens, under the ceiling as every replay is, its mail
        kept. True when the restart took the ring — the close was made, so a replay that fails is the
        tick's to retry (`_brief_restart`); False rings as today: the precondition fails, the window is
        full, a node's member (rule 7 reaches no node yet), or the close failed (its entry counts)."""
        lapsed = agent_common.cache_lapsed(s, now)
        if lapsed is None or s.host != self.host or not agent_common.tick_ready(s, now):
            return False
        if self._profile_gated(s.profile, now, s.team) or self._just_restarted(s, now) or self._window_full(s, now):
            return False
        log.info("%s: idle %sh with %d tokens: restarting it on its brief rather than ringing", s.id, *lapsed.values())
        try:
            await self.rpc_close(s.id, closer={"by": "tick", "why": "cache"})
        except Exception as e:  # noqa: BLE001 — a close that failed is a restart that failed, and counts
            if s.state != "closed":
                s.restarts = [*s.restarts, {"at": now_iso(), "why": "cache", **lapsed, "error": f"close: {e}"}]
                log.warning("%s: the close before a cache restart failed, ringing instead: %s", s.id, e)
                self._save(s)
                return False
        self._mark_closed(s, "cache")
        await self._replay(s, "cache", mark=lapsed, keep_mail=True)
        return True

    @staticmethod
    def _mark_closed(s: Session, why: str) -> None:
        """The tick's mark after its own close for a restart: the rule and the `closed_at` that close
        wrote — the node's own stamp for a node's member, applied from the close's reply — so a later
        Close, which writes its own `closed_at`, no longer matches, even one made at the node that the
        home never hears of as an act (TD-238)."""
        s.closed_for = {"why": why, "closed_at": s.closed_at}

    @staticmethod
    def _closed_by_tick(s: Session, why: str) -> bool:
        """A `closed` record the tick itself closed and failed to replay (`why` rule 2's `wanted` or rule
        7's `brief` or `flow`): it carries the tick's mark for that rule and the close the mark names is still the
        record's (`closed_at` unchanged), and the last `restarts` entry is that rule's and carries
        `error` — both failure paths, the close's and the replay's, write one. Any other close clears the
        mark or writes a new `closed_at`, so a person's Close is never undone (design §6 rule 2; TD-235
        to TD-238)."""
        last = s.restarts[-1] if s.restarts and isinstance(s.restarts[-1], dict) else {}
        mark = s.closed_for if isinstance(s.closed_for, dict) else {}
        return (
            s.state == "closed"
            and mark.get("why") == why
            and bool(s.closed_at)
            and mark.get("closed_at") == s.closed_at
            and last.get("why") == why
            and bool(last.get("error"))
        )

    @staticmethod
    def _window_full(s: Session, now: datetime) -> bool:
        recent = agent_common._counted(s.restarts, now)
        return len(recent) >= RESTART_CEILING

    async def _restart_held(self, s: Session, now: datetime, dirty: int, unpushed: int) -> None:
        """Rule 2 with work left: one fixed send, once, naming the counts from the record and never a
        session's words; `IDLE_NUDGE` after it (after the declaration, where nothing could be typed)
        the record carries `restart_blocked` and the Inbox lists it."""
        if not s.restart_blocked_sent_at and s.state == "idle" and s.host == self.host:
            line = (
                f"[agentorc] your restart is held: {dirty} uncommitted and {unpushed} unpushed in your checkout "
                "— commit and push them, and the host agent restarts you"
            )
            if await self._policy_send(s, line):
                s.restart_blocked_sent_at = now_iso()
                self._save(s)
        start = s.restart_blocked_sent_at or (s.restart_wanted or {}).get("at")
        if not s.restart_blocked and start and now - _parse(str(start)) >= IDLE_NUDGE:
            s.restart_blocked = {"at": now_iso(), "dirty": dirty, "unpushed": unpushed}
            log.info("%s: its restart is held by work left — the person's now", s.id)
            self._save(s)
            await self._push_changes()

    async def _idle_nudge(self, s: Session, now: datetime) -> None:
        """Rule 4 (design §6): a supervised member hook-confirmed `idle` for `IDLE_NUDGE` with open work
        — a lane reference not done or dropped, a declared claim not done or dropped, for a seat a
        question waiting, or an outcome owed to the person (TD-258) — and nothing declared, is sent
        one fixed line naming it, once per idle
        stretch (`nudged_at`; a stretch ends when the state changes). It spends no wake budget. The
        send needs the pane here: a node's member is not nudged yet (TD-103)."""
        if not (s.supervised and s.unattended) or s.superseded_by or s.suspended or s.host != self.host:
            return
        if s.state != "idle" or s.confidence != "hook" or s.pending or s.out_of_work or s.restart_wanted:
            return
        if not s.since or now - _parse(s.since) < IDLE_NUDGE:
            return
        if s.nudged_at and _parse(s.nudged_at) >= _parse(s.since):
            return  # this stretch was nudged already: never a second before the first is answered
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        if s.balance_refused and self._balance_of(s):
            return  # refused a claim over its team's line (§6 *Balance*): idling is what it was told to do
        line = self._nudge_line(s)
        if line and await self._policy_send(s, line):
            s.nudged_at = now_iso()
            log.info("%s: idle %s with work open — nudged", s.id, IDLE_NUDGE)
            self._save(s)
            await self._push_changes()

    @staticmethod
    def _open_ref(s: Session) -> str | None:
        """A member's first open reference (rule 4): a lane reference, then a declared claim, with
        no `done` or `dropped` entry."""
        ended = {e.ref for e in s.progress if e.status in ("done", "dropped")}
        claimed = [e.ref for e in s.progress if e.source == "declared" and e.status == "claimed"]
        return next((r for r in [*lane_refs(s.lane), *claimed] if r not in ended), None)

    async def _idle_open(self, s: Session, now: datetime) -> None:
        """*idle · open work* (design §6 rule 3, TD-259): `idle_open: {at, ref}` on a supervised
        member, not a seat, that rule 4 nudged in this idle stretch and that is still hook-confirmed
        idle `IDLE_NUDGE` after the nudge with work open and nothing declared. It stands while the
        stretch does — the reference it names is the one open when it was written, None where the
        work is an outcome owed alone — and is cleared
        when the state changes, the work closes or the member declares."""
        try:
            stretch = bool(
                s.state == "idle" and s.nudged_at and s.since and _parse(s.nudged_at) >= _parse(s.since)
            ) and not (s.out_of_work or s.restart_wanted or s.superseded_by)
        except ValueError:
            stretch = False
        ref = self._open_ref(s) if stretch and s.seat is None else None
        # an outcome owed is open work too, as rule 4's nudge counts it (TD-258): the mark names no reference then
        still = ref is not None or bool(stretch and s.seat is None and s.owed())
        if s.idle_open:
            if not still:
                s.idle_open = None
                self._save(s)
                await self._push_changes()
            return
        if not still or not (s.supervised and s.unattended) or s.suspended:
            return
        if s.confidence != "hook" or s.pending or now - _parse(str(s.nudged_at)) < IDLE_NUDGE:
            return
        s.idle_open = {"at": now_iso(), "ref": ref}
        log.info("%s: still idle %s after the nudge with %s open — idle · open work", s.id, IDLE_NUDGE, ref or "a debt")
        self._save(s)
        await self._push_changes()

    async def _context_line(self, s: Session, now: datetime) -> None:
        """Rule 5 (design §6, TD-190): a supervised member whose context reading is past its role's
        bound is told so by one fixed line once it is hook-confirmed `idle` and holds no claim in
        progress — between entries, never mid-turn — and again after `CONTEXT_AGAIN` while it is
        still idle and over (`context_sent_at`). A wrap-up under way or a gate pause beats it, as it
        beats the doorbell; a member that declared already (out of work, a restart wanted) is not
        told. The send needs the pane here: a node's member is not told yet, as rule 4's is not."""
        if not (s.supervised and s.unattended) or s.superseded_by or s.suspended or s.host != self.host:
            return
        if s.seat is not None or s.out_of_work or s.restart_wanted:
            return
        over = context_over_text({"context": s.context, "context_bound": s.context_bound})
        if not over:
            return
        if s.state != "idle" or s.confidence != "hook" or s.pending:
            return
        if any(e.status == "claimed" and e.source == "declared" for e in s.progress):
            return  # a claim in progress: the clause on its `ao` replies says it, and it declares after
        if s.context_sent_at and now - _parse(s.context_sent_at) < CONTEXT_AGAIN:
            return
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        line = f"[agentorc] {over.replace(' over the ', ', over your ', 1)} — take nothing new: push, ledger, then "
        line += '`ao progress restart --why "context bound"`'
        if await self._policy_send(s, line):
            s.context_sent_at = now_iso()
            log.info("%s: %s — told", s.id, over)
            self._save(s)
            await self._push_changes()

    @staticmethod
    def _cadence_member(s: Session) -> bool:
        """Rule 10's subject: a supervised member, not a seat, on the record that is its run now."""
        return bool(s.supervised and s.seat is None and not s.superseded_by)

    def _is_record(self, s: Session) -> bool:
        """Whether `s` is still the record under its address — not forgotten, replaced by a
        restart, or a node's replica read anew — so a read made off the loop is saved to it."""
        held = self.sessions if s.host == self.host else self.remote.get(s.host, {})
        return held.get(s.id) is s

    async def _cadence_pass(self, records: list[Session]) -> None:
        """Rule 10's read (design §6, TD-258), detached on the reports' cadence: for each supervised
        member, not a seat, each `progress` entry `done` with a `pr` — declared or derived — whose
        `checks` entry is missing, `unknown`, older than the `done`, or read at another head, the
        script is run in the registry root the record's `repo` names, **one PR per run**, the one
        longest unread first. A root the home holds no reading of or that carries no script, a
        head `gh` cannot give and a run with no reading write nothing."""
        try:
            due: list[tuple[str, Session, int, str]] = []
            for s in records:
                if not self._cadence_member(s) or s.repo not in self._repos:
                    continue
                dones: dict[int, str] = {}
                for e in s.progress:
                    if e.status == "done" and isinstance(e.pr, int):
                        # a derived entry is written anew at every derivation, so only the
                        # member's own word dates a new report
                        dones[e.pr] = max(dones.get(e.pr, ""), e.at if e.source == "declared" else "")
                for pr, done_at in dones.items():
                    old = cadence_mod.entry_of(s.checks, pr)
                    if cadence_mod.settled(old) and not cadence_mod.stale(old, done_at):
                        continue  # a merged or closed PR's head no longer moves: the read stands
                    due.append((str((old or {}).get("at") or ""), s, pr, done_at))
            due.sort(key=lambda d: (d[0], d[2]))
            scripted: dict[str, bool] = {}
            for _, s, pr, done_at in due:
                root = str(s.repo)
                if root not in scripted:
                    scripted[root] = await asyncio.to_thread(cadence_mod.has_script, root)
                if not scripted[root]:
                    continue
                # the repo reading's head first (TD-332): `gh` is asked only for a PR that reading lacks
                head = cadence_mod.head_from((self._repos.get(root) or {}).get("prs"), pr)
                if head is None:
                    head = await asyncio.to_thread(cadence_mod.head, root, pr)
                if head is None:
                    continue
                sha, state = head
                merged, closed = state == "merged", state == "closed"
                old = cadence_mod.entry_of(s.checks, pr)
                if old and not cadence_mod.stale(old, done_at) and old.get("sha") == sha:
                    if bool(old.get("merged")) == merged and bool(old.get("closed")) == closed:
                        continue
                    if closed and not old.get("merged"):
                        # closed unmerged at the head it was read at: the read stands, settled — written
                        # only to the record still under its address, as a read is
                        if self._is_record(s) and self._cadence_member(s):
                            old["closed"] = True
                            self._save(s)
                        continue
                got = await asyncio.to_thread(cadence_mod.check, root, pr)
                if got is None or not self._is_record(s) or not self._cadence_member(s):
                    continue
                old = cadence_mod.entry_of(s.checks, pr)
                new = cadence_mod.record(old, pr, sha, merged, got, now_iso(), closed=closed)
                s.checks = [*(c for c in s.checks if c.get("pr") != pr), new]
                log.info("%s: PR #%s read by the cadence check: %s", self._address(s), pr, cadence_mod.said(new))
                self._save(s)
                break  # one PR per run
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next pass tries again
            log.exception("the cadence check's pass failed")
        finally:
            await self._push_changes()

    async def _cadence_line(self, s: Session, now: datetime) -> None:
        """Rule 10's telling (design §6, TD-258), rule 5's two ways. A first fail of an open PR is
        one fixed line typed into the composer of an unattended member of this host that is
        hook-confirmed `idle` — never at a dialog, a wrap-up or a gate pause — and for a member
        that is working, on a node or attended it is the clause every `ao` reply carries
        (`cadence.clause`, read at the home); either way `told` marks it. `read_by` is written the
        first time a reader's reply on the PR's `ask` is seen in the record's mail, and kept."""
        if not self._cadence_member(s) or not s.checks:
            return
        dirty = False
        for pr in [int(c["pr"]) for c in s.checks]:
            c = cadence_mod.entry_of(s.checks, pr)
            if c is None:
                continue
            if not c.get("read_by") and (who := cadence_mod.read_by(s, pr)):
                c["read_by"], dirty = who, True
            if not cadence_mod.untold(c):
                continue
            typed = s.host == self.host and s.unattended and not s.suspended
            if typed and s.state == "idle":
                if s.confidence != "hook" or s.pending or s.wrapup_at or s.wrapup_sent_at:
                    continue
                if s.gated or self._profile_gated(s.profile, now, s.team):
                    continue
                ref = next((e.ref for e in reversed(s.progress) if e.status == "done" and e.pr == pr), "")
                if not await self._policy_send(s, cadence_mod.line(c, ref)):
                    continue  # not typed: the next tick looks again
                # the detached read may have replaced the list while the line was typed
                c = cadence_mod.entry_of(s.checks, pr)
                if c is None:
                    continue
            elif typed and s.state != "working":
                continue  # exited, closed or at a dialog: nobody reads a reply now
            c["told"], dirty = now_iso(), True
        if dirty:
            self._save(s)
            await self._push_changes()

    @staticmethod
    def _conventions_member(s: Session) -> bool:
        """Rule 12's subject: a supervised member, not a seat (every fill starts cold, and the hook
        tells it), not finished (never sent to; its next start is told at its start), on the record
        that is its run now — one not running (`scheduled`, `exited`, `closed`) starts again as a
        new record, which the hook tells."""
        if s.state in ("scheduled", "exited", "closed"):
            return False
        return bool(s.supervised and s.seat is None and not s.out_of_work and not s.superseded_by)

    async def _conventions_pass(self, records: list[Session]) -> None:
        """Rule 12 (design §6, TD-258), detached on the reports' cadence: `scripts/cadence_changes.py
        --json` is run once in each registry root that carries it and holds a member to tell, and
        each such member's `conventions_seen` is written from the reading — at the first one after
        its create, the headings landed at or before `created`; later, a heading it does not hold
        that landed after `created` is one `note` from `system` and joins it. The doorbell does
        the waking; nothing is typed here. A root with no script, or a run with no reading,
        writes nothing."""
        try:
            by_root: dict[str, list[Session]] = {}
            for s in records:
                if self._conventions_member(s) and s.repo and s.repo in self._repos:
                    by_root.setdefault(str(s.repo), []).append(s)
            for root, members in by_root.items():
                if not conventions_mod.has_script(root):
                    continue
                got = await asyncio.to_thread(conventions_mod.read, root)
                if got is None:
                    log.info("%s: %s gave no reading", root, conventions_mod.SCRIPT)
                    continue
                for s in members:
                    if not self._is_record(s) or not self._conventions_member(s):
                        continue  # replaced, or declared out of work, while the script ran
                    first = s.conventions_seen is None
                    sorted_ = conventions_mod.sort(
                        got["entries"], (s.conventions_seen or {}).get("headings"), s.created
                    )
                    if sorted_ is None:
                        continue
                    seen, told = sorted_
                    if not first and seen == (s.conventions_seen or {}).get("headings"):
                        continue
                    s.conventions_seen = {"at": now_iso(), "headings": seen}
                    if told:
                        self._system_note(self._address(s), conventions_mod.note(told, got["ref"]))
                        log.info("%s: told of %d new in docs/cadence-changes.md", self._address(s), len(told))
                    self._save(s)
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next pass tries again
            log.exception("the conventions pass failed")
        finally:
            await self._push_changes()

    @staticmethod
    def _held_member(s: Session) -> bool:
        """Rule 11's subject: a supervised member whose record carries `review`, on the record that
        is its run now."""
        return bool(s.supervised and s.review and not s.superseded_by)

    async def _held_pass(self, records: list[Session], now: datetime) -> None:
        """Rule 11's read (design §6, TD-258), detached on the reports' cadence: for each supervised
        member whose record carries `review`, each `progress` entry `done` with a `pr` is read once
        it has merged — its files against the record's `held:` globs, in the registry root the
        record's `repo` names, `held.READS` PRs a run, the longest unread first. A PR touching no
        held path, one merged before the record was created, and one its reader replied on (`held.
        read_by`) are settled, in memory: a restarted home reads each once more, and since mail
        is pruned, a PR merged longer ago than half `mail.MAIL_RETENTION` is settled unjudged. A
        held PR with no reply `held.GRACE` after its merge is a **crossing**: an entry on `held_missed` and one
        `system` note to the person. A PR not merged yet, a failed read and a root the home holds
        no reading of write nothing and are read again."""
        try:
            due: list[tuple[float, Session, int]] = []
            for s in records:
                if not self._held_member(s) or s.repo not in self._repos:
                    continue
                addr = self._address(s)
                crossed = {c.get("pr") for c in s.held_missed}
                for pr in {e.pr for e in s.progress if e.status == "done" and isinstance(e.pr, int)}:
                    if pr not in crossed and (addr, s.created, pr) not in self._held_settled:
                        due.append((self._held_tried.get((addr, pr), 0.0), s, pr))
            due.sort(key=lambda d: (d[0], d[2]))
            for _, s, pr in due[: held_mod.READS]:
                addr = self._address(s)
                self._held_tried[(addr, pr)] = time.monotonic()
                try:
                    files, merged = await asyncio.to_thread(held_mod.pr_read, pr, str(s.repo))
                except RuntimeError:
                    continue  # no reading: never a crossing by default
                if merged is None or not self._is_record(s) or not self._held_member(s):
                    continue
                paths = held_mod.held_paths(files, s.review)
                # mail is pruned: past half its retention the reply that would clear a PR may be
                # gone (a restarted home, a first promote), and that is no reading, never a crossing
                old = mail.MAIL_RETENTION is not None and now - merged > mail.MAIL_RETENTION / 2
                if not paths or merged <= _parse(s.created) or held_mod.read_by(s, pr, files) or old:
                    self._held_settled.add((addr, s.created, pr))
                    self._held_tried.pop((addr, pr), None)
                    continue
                if now - merged < held_mod.GRACE or any(c.get("pr") == pr for c in s.held_missed):
                    continue  # the reader merges, then replies: looked at again
                self._held_settled.add((addr, s.created, pr))  # told once, whatever clears the entry
                self._held_tried.pop((addr, pr), None)
                entry = held_mod.crossing(pr, paths, now_iso())
                s.held_missed = [*s.held_missed, entry]
                reader = str((s.review or {}).get("reader") or "")
                self._system_note(PERSON, held_mod.note(entry, reader, addr))
                log.info("%s: %s", addr, held_mod.said(entry, reader))
                self._save(s)
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next pass tries again
            log.exception("the held-path pass failed")
        finally:
            await self._push_changes()

    async def _held_line(self, s: Session, now: datetime) -> None:
        """Rule 11's telling (design §6, TD-258), rule 5's two ways. A crossing the member has not
        been told of is one fixed line typed into the composer of an unattended member of this host
        that is hook-confirmed `idle` — never at a dialog, a wrap-up or a gate pause; a member that
        is working, on a node or attended reads it as the clause on its next `ao` reply
        (`held.clause`, which the reply marks told). `told` marks it either way."""
        if not self._held_member(s) or not held_mod.untold(s.held_missed):
            return
        if not (s.host == self.host and s.unattended and not s.suspended and s.state == "idle"):
            return
        if s.confidence != "hook" or s.pending or s.wrapup_at or s.wrapup_sent_at:
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        reader = str((s.review or {}).get("reader") or "")
        for pr in [c.get("pr") for c in held_mod.untold(s.held_missed)]:
            c = next((c for c in held_mod.untold(s.held_missed) if c.get("pr") == pr), None)
            if c is None or not await self._policy_send(s, held_mod.line(c, reader)):
                return  # not typed: the next tick looks again
            # the detached read may have replaced the list while the line was typed
            for c in s.held_missed:
                if c.get("pr") == pr:
                    c["told"] = now_iso()
            self._save(s)
            await self._push_changes()
            return  # one line a tick: the composer holds the one just typed

    async def _lane_news(self, s: Session, now: datetime) -> None:
        """Rule 6 (design §6, TD-195): a supervised member, not a seat, that declared out of work is
        told when its lane gains entries. The first tick that sees the declaration writes
        `lane_seen` — the ids in its repo's ledger reading that match its lane; a later reading
        holding a matching id not in it, while the member is live, not winding down, not gated and
        not suspended, becomes one `note` from `system` naming the new ids, which are then added,
        so each is told once. The doorbell does the waking; nothing is typed here. No reading —
        the repo not in this home's registry, or its file unreadable — writes nothing.

        **The first write is the ledger at the declaration** (TD-227): the file as the last commit of
        `origin/<default>` before `out_of_work.at` held it, so an entry merged between the
        declaration and a first tick that comes late (a promote, a home down) is new and is told;
        where the history cannot be read, the reading at this tick, the log saying which."""
        if not (s.supervised and s.out_of_work) or s.seat is not None or s.superseded_by:
            return
        led = (self._repos.get(s.repo or "") or {}).get("ledger") or {}
        if "error" in led or not isinstance(led.get("entries"), list):
            return

        def matching(got: list[Any]) -> list[str]:
            return [
                str(e["id"]) for e in got if isinstance(e, dict) and e.get("id") and ledger_mod.lane_matches(s.lane, e)
            ]

        ledger_ids = matching(led["entries"])
        # the board's decided lines are work in the lane too (§6 rule 6, TD-384): never in the first
        # `lane_seen`, so one that sat unclaimed while its member declared is told on the next tick
        wos = (self._repos.get(s.repo or "") or {}).get("work_orders") or {}
        order_ids = [i for i in matching(wos.get("orders") or []) if i not in ledger_ids]
        ids = ledger_ids + order_ids
        if s.lane_seen is None:
            seen, read = list(ledger_ids), "the reading at this tick"
            root, rel = (self._repos.get(s.repo or "") or {}).get("root") or s.repo, s.ledger or led.get("path")
            try:
                at = _parse(str((s.out_of_work or {}).get("at")))
            except (ValueError, TypeError):
                at = None
            if root and rel and at is not None:
                decl = dict(s.out_of_work or {})

                name = Path(str(root)).name
                commit = self._live_commits().get(name)

                def both() -> tuple[Any, str, Any]:
                    resolve = ledger_mod.Registry(hosts.local_host().repos())
                    # a live check stood in the lane at the declaration when its build was live then
                    # (§4.9b, §6 rule 6): known when the promote that made today's live commit live
                    # concluded before it, so the ledger then is read against that commit; unknown,
                    # it is read without, and a check live now is told once as new
                    was = promote_mod.last(name) or {}
                    try:
                        before = bool(commit) and was.get("sha") == commit and _parse(str(was.get("at"))) <= at
                    except (ValueError, TypeError):
                        before = False
                    live = ledger_mod.LiveReader(root, commit)
                    then, why = ledger_mod.entries_before(
                        root, str(rel), at, resolve=resolve, live=live if before else None
                    )
                    if then is None:
                        return then, why, None
                    # the tip read as the checkout's reading is, with its live checks' `live`, or a
                    # live one would read as the checkout's own and be seen untold
                    return then, why, ledger_mod.entries_before(root, str(rel), None, resolve=resolve, live=live)[0]

                then, why, tip = await asyncio.to_thread(both)
                if s.out_of_work != decl or s.lane_seen is not None:
                    return  # the declaration moved while git was read: the next tick looks afresh
                if then is not None and tip is not None:
                    # an entry the checkout holds and origin's tip does not (a branch checked out here)
                    # is seen, untold, or every tick after this one, which reads the checkout, would
                    # tell it as new — and again after each `none`
                    seen = matching(then)
                    on_tip = set(matching(tip))
                    seen += [i for i in ledger_ids if i not in on_tip and i not in seen]
                    read = f"the ledger at the declaration ({why}), the checkout's own entries seen"
                else:
                    read = f"the reading at this tick ({why})"
            s.lane_seen = {"at": now_iso(), "ids": seen}
            self._save(s)
            log.info("%s: lane_seen from %s", self._address(s), read)
            # and on: an entry merged since the declaration is told on this tick
        # the lane's memory, never the ledger's (§6 rule 6, TD-407): an id the lane no longer takes
        # leaves it, written for a gone member as for a live one, so an entry that comes back is news
        pruned, new = work_mod.reread(s, [*led["entries"], *(wos.get("orders") or [])])
        if pruned is not s.lane_seen:
            s.lane_seen = pruned
            self._save(s)
        seen = set(s.lane_seen.get("ids") or [])
        drops = self._lane_drops(s, [i for i in ids if i in seen])
        if not (new or drops) or s.state in ("exited", "closed") or s.suspended:
            return
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        if s.balance_refused and self._balance_of(s):
            return  # its claims are refused while its team is over its line (§6 *Balance*): told once it clears
        s.lane_seen = {**s.lane_seen, "at": now_iso(), "ids": [*s.lane_seen.get("ids", []), *new]}
        if drops:
            s.lane_seen["dropped"] = {**(s.lane_seen.get("dropped") or {}), **{i: at for i, _, at in drops}}
        told = [*new, *(f"{i} (dropped by {who})" for i, who, _ in drops)]
        named = ", ".join(told[:LANE_NEWS_NAMED]) + (
            f" and {len(told) - LANE_NEWS_NAMED} more" if len(told) > LANE_NEWS_NAMED else ""
        )
        count = f"{len(told)} entr{'y' if len(told) == 1 else 'ies'}"
        self._system_note(
            self._address(s),
            f"your lane gained {count} since you declared out of work: {named} — read the "
            f"{'ledger and the board' if any(i in order_ids for i in new) else 'ledger'} on "
            "`origin/main`, then claim one or declare again",
        )
        self._save(s)
        log.info("%s: told of %s new in its lane", self._address(s), count)

    def _lane_drops(self, s: Session, held: list[str]) -> list[tuple[str, str, str]]:
        """Rule 6's dropped lease (design §6, TD-258): of the ids `s` has seen and its lane still
        matches, each one a record of the same repo let go — a `dropped` entry in its `progress`
        later than `s`'s declaration and than the drop `lane_seen` holds for the id — and that no
        live record holds a lease on now, as `(id, the dropper's name, the drop's instant)`, the
        latest drop of each. An instant that cannot be read is no drop."""
        if not held:
            return []
        told = s.lane_seen.get("dropped") if isinstance((s.lane_seen or {}).get("dropped"), dict) else {}

        def instant(v: Any) -> datetime | None:
            try:
                at = _parse(str(v))
            except ValueError:
                return None
            return at if at.tzinfo else at.replace(tzinfo=UTC)

        declared = instant((s.out_of_work or {}).get("at"))
        if declared is None:
            return []
        # a kept instant that cannot be read is no memory of that id's drop, and the others stand
        since = {i: max(declared, instant(told.get(i)) or declared) for i in held}
        last: dict[str, tuple[datetime, str, str]] = {}
        leased: set[str] = set()
        now = datetime.now(UTC)
        for r in (*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())):
            if r is s or not r.repo or r.repo != s.repo:
                continue
            live = r.state not in ("exited", "closed", "scheduled")
            for e in r.progress:
                if e.ref not in since:
                    continue
                at = instant(e.at)
                if at is None:
                    continue
                if e.status == "claimed" and e.source == "declared" and live and now - at < LEASE_TTL:
                    leased.add(e.ref)  # taken again since: the lease's holder is who it is news to
                elif e.status == "dropped" and at > since[e.ref] and (e.ref not in last or at > last[e.ref][0]):
                    last[e.ref] = (at, r.name, e.at)
        return [(i, last[i][1], last[i][2]) for i in held if i in last and i not in leased]

    async def _work_marks(self, now: datetime) -> None:
        """Rule 8 (design §6 *Work for a team that wound down*, TD-227): on every tick at the home,
        each team read as **wound down** from its records (`work.team_wound_down`, the card's own
        reading) whose members' lanes hold ids their `lane_seen` does not gets `work_waiting: {at,
        repo, members: {<name>: [ids]}}` on the home's `host` record under its name, once
        `WORK_SETTLE` has passed since the home first read the newest of them (a time kept in
        memory, so a restart settles again). It is removed when the team is no longer wound down —
        a crew session live again, or a member that never declared — when no id is new, and under
        `on_work: off`. `lane_seen` itself is rule 6's, written for a member that is gone as for a
        live one and pruned by it on the same tick, before this pass (`work.reread`, TD-407), which
        `work.gained` reads the same way. One team's surprise is a log line, never another's."""
        if self.mode != "home":
            return
        teams = self._host_rec.setdefault("teams", {})
        try:
            settings = settings_mod.teams(settings_mod.load())
        except Exception:  # noqa: BLE001 — a policy's surprise is a log line; the next tick reads again
            log.exception("reading the teams' settings for rule 8 failed")
            return
        by_team: dict[str, list[Session]] = {}
        for r in self._graph().values():
            if r.team:
                by_team.setdefault(r.team, []).append(r)
        firsts: dict[tuple[str, str], datetime] = {}
        dirty = False
        for team in sorted(set(by_team) | {t for t, rec in teams.items() if "work_waiting" in rec}):
            try:
                rec = teams.get(team) or {}
                old = rec.get("work_waiting")
                on_work = (settings.get(team) or {}).get("on_work", settings_mod.ON_WORK_DEFAULT)
                new = self._work_mark(team, old, by_team.get(team) or [], on_work, now, firsts)
                starts = rec.get("work_started")
                if new is not None and on_work == "start":
                    new = await self._work_start(team, new, rec, by_team.get(team) or [], settings.get(team) or {}, now)
                elif isinstance(new, dict) and "held" in new:
                    new = {k: v for k, v in new.items() if k != "held"}  # `ask` again: no start to hold back
                if new == old and rec.get("work_started") == starts:
                    continue
                dirty = True
                if new is None:
                    rec.pop("work_waiting", None)
                    log.info("rule 8: %s has no work waiting", team)
                else:
                    rec["work_waiting"] = new
                    log.info("rule 8: %s wound down with work waiting: %s", team, new["members"])
                if rec:
                    teams[team] = rec
                else:
                    teams.pop(team, None)
            except Exception:  # noqa: BLE001 — one team's surprise is a log line, never the others'
                log.exception("reading %s's work for rule 8 failed", team)
        self._work_first = firsts  # an id no longer new starts its settle again if it comes back
        if dirty:
            try:
                self.host_store.save(self._host_rec)
            except OSError:
                log.exception("writing the home's host record failed")
            await self._push_changes()

    def _work_mark(
        self,
        team: str,
        old: Any,
        records: list[Session],
        on_work: str,
        now: datetime,
        firsts: dict[tuple[str, str], datetime],
    ) -> dict[str, Any] | None:
        """One team's `work_waiting` as it reads now (`_work_marks`), or None. Its repo is the ledger's
        — a registry root, since two repos may hold one id — and a team whose members' news is in two
        repos is written for the first registry root in order; the other's waits for the next
        wind-down. A crew member's ledger that cannot be read keeps what stands: *could not look*
        is not *no id new* (the techlead's read of #788)."""
        if on_work == "off":
            return None
        wound = work_mod.team_wound_down(records) is not None
        # a team that runs on: only its members that finished alone are read (§6 rule 8 *A member that
        # finished while its team runs on*, TD-457); none, and there is nothing for this rule
        alone = None if wound else {r.id for r in work_mod.finished_alone(records)}
        if alone is not None and not alone:
            return None
        # a question's end (§6 rule 8 *A question's end is work*, TD-274) is written once, at the
        # lapse or the answer, and stands with the mark until a start, Dismiss or a live team clears it
        questions = list(old.get("questions") or []) if isinstance(old, dict) and wound else []
        news: dict[str, dict[str, list[str]]] = {}  # repo → member → ids
        for r in sorted(work_mod.crew(records), key=lambda r: (r.name, r.id)):
            if r.seat is not None or r.superseded_by or work_mod.sat_out(r):
                continue  # a member its flow sat out has no lane that is the team's work (§4.9c)
            if alone is not None and r.id not in alone:
                continue  # live, or not finished: rule 6's, or nobody's
            led = (self._repos.get(r.repo or "") or {}).get("ledger") or {}
            if "error" in led or not isinstance(led.get("entries"), list):
                if isinstance(old, dict):
                    # the settle's memory kept too, so the ids read back later need no second settle
                    firsts.update({k: t for k, t in self._work_first.items() if k[0] == team})
                    return old
                continue
            ids = news.setdefault(str(r.repo), {}).setdefault(r.name, [])
            # the repo's work orders count as entries do (§6 rule 8, TD-384): a decided line starts its team
            orders = ((self._repos.get(r.repo or "") or {}).get("work_orders") or {}).get("orders") or []
            ids.extend(i for i in work_mod.gained(r, [*led["entries"], *orders]) if i not in ids)
        news = {repo: {m: ids for m, ids in ms.items() if ids} for repo, ms in news.items()}
        news = {repo: ms for repo, ms in news.items() if ms}
        if not news:
            if questions:  # nothing new in the lanes: the questions' references alone, under their names
                at = old.get("at") or now_iso()
                return {"at": at, "repo": old.get("repo"), "members": _asked(questions), "questions": questions}
            return None
        repo = min(news)
        members = news[repo]
        newest = max(
            firsts.setdefault((team, i), self._work_first.get((team, i), now)) for ids in members.values() for i in ids
        )
        if now - newest < agent_common.WORK_SETTLE:
            return old if isinstance(old, dict) else None  # still settling: what stands, stands
        at = old.get("at") if isinstance(old, dict) and old.get("at") else now_iso()
        if questions:  # the questions' references and repo are the mark's own, the lanes' news beside them
            members = _asked(questions, members)
            return {"at": at, "repo": old.get("repo") or repo, "members": members, "questions": questions}
        return {"at": at, "repo": repo, "members": members}

    def _question_end(self, e: Any, how: str) -> None:
        """§6 rule 8 *A question's end is work* (TD-271, TD-274 slice 4): an orphaned question that lapsed (`how:
        lapsed`) or was answered (`answered`) while its asker's team is **wound down** writes `work_waiting` for the
        team as a lane's new id does — `members[<name>]` gaining the reference, `questions` gaining `{id, ref, name,
        how, kind}` (the Inbox row says *steer* or *ask*) — with no settle, a standing mark gaining the question and
        keeping its `at`. Under `on_work: off`, or for a team stopped rather than wound down, or one live again, nothing
        is written: a live member is waiting (§4.9a), and a stopped team's note waits in the mailbox for the person's
        Start. The next tick's `_work_marks` does what the team's `on_work` says. Never on a node."""
        o = e.orphaned if isinstance(e.orphaned, dict) else {}
        team, name, ref = str(o.get("team") or ""), str(o.get("name") or ""), str(o.get("ref") or "")
        if self.mode != "home" or not (team and name and ref):
            return
        try:
            on_work = (settings_mod.teams(settings_mod.load()).get(team) or {}).get(
                "on_work", settings_mod.ON_WORK_DEFAULT
            )
        except Exception:  # noqa: BLE001 — a policy's surprise is a log line, never the answer's failure
            log.exception("reading %s's settings for rule 8's question failed", team)
            return
        records = [r for r in self._graph().values() if r.team == team]
        if on_work == "off" or work_mod.team_wound_down(records) is None:
            return
        teams = self._host_rec.setdefault("teams", {})
        rec = teams.setdefault(team, {})
        old = rec.get("work_waiting") if isinstance(rec.get("work_waiting"), dict) else {}
        if any(isinstance(q, dict) and q.get("id") == e.id for q in old.get("questions") or []):
            return  # a question ends once
        members = {m: list(ids) for m, ids in (old.get("members") or {}).items()}
        if ref not in members.setdefault(name, []):
            members[name].append(ref)
        questions = [*(old.get("questions") or []), {"id": e.id, "ref": ref, "name": name, "how": how, "kind": e.kind}]
        rec["work_waiting"] = {
            **old,
            "at": old.get("at") or now_iso(),
            "repo": old.get("repo") or str(o.get("repo") or ""),
            "members": members,
            "questions": questions,
        }
        log.info("rule 8: %s's %s about %s %s; %s has work waiting", name, e.kind, ref, how, team)
        self._question_ended = True  # the sweep pushes it: the next tick reads the mark unchanged
        try:
            self.host_store.save(self._host_rec)
        except OSError:
            log.exception("writing the home's host record failed")

    async def _work_start(
        self,
        team: str,
        mark: dict[str, Any],
        rec: dict[str, Any],
        records: list[Session],
        conf: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Rule 8 under `on_work: start` (design §6, TD-227 slice 4): the person's standing press. The
        five bounds are read before the first replay, and one that holds the start back leaves the
        mark standing with `held: {why, …}`, which is what draws the row under `start`; otherwise the
        team's records are replayed (`_work_replays`, the lead first), each `restarts` entry
        `why: work` with the ids it was started for, the instant appended to `work_started` on the
        home's record once however many records it replayed, and None is returned: the mark goes.
        Every entry of one start carries the same `start` instant and `of`, the number of records it
        set out to replay, so a client counts *n of m* from the records when some replays failed
        (the techlead's read of #792). A member on a node whose link is down holds the whole start as
        `held: {why: link, host, hosts}` — every down host, `host` the first — looked at again on the
        next tick."""
        started = [t for t in rec.get("work_started") or [] if _recent(t, now, agent_common.WORK_DAY)]
        if started != (rec.get("work_started") or []):
            rec["work_started"] = started  # a start older than the day counts for nothing
        if not started:
            rec.pop("work_started", None)
        replays = self._work_replays(records, now)
        if work_mod.team_wound_down(records) is None:
            # what a start replays is read at the start (TD-457): a team that runs on has only the
            # members the mark names replayed, each alone; one left by `_work_replays` holds as `nothing`
            named = set(mark.get("members") or {})
            replays = [r for r in replays if r.name in named]
        standing = (rec.get("work_waiting") or {}).get("held")
        held = self._work_held(replays, started, conf, now, team, records, standing)
        bare = {k: v for k, v in mark.items() if k != "held"}
        if held is None:
            down = list(
                dict.fromkeys(r.host for r in replays if r.host != self.host and r.host not in self._link_muxes)
            )
            if down:  # the whole team waits for them, looked at again next tick (§4.4a); `host` for an older page
                held = {"why": "link", "host": down[0], "hosts": down}
        if held is not None:
            return {**bare, "held": held}
        ids = list(dict.fromkeys(i for got in (mark.get("members") or {}).values() for i in got))
        at = now_iso()
        rec["work_started"] = [*started, at]
        about = {"ids": ids, "start": at, "of": len(replays)}
        log.info("rule 8: starting %s again for %s (%d records)", team, ids, len(replays))
        for r in replays:
            try:
                # every record's mail kept, as a fill's and a Restart's is: a lapse note or an answer
                # written to the closed record waits there for this successor (§4.10, TD-271)
                await self._replay(r, "work", mark=dict(about), keep_mail=True)
            except Exception:  # noqa: BLE001 — one record's failure is never the team's start
                log.exception("%s: rule 8's replay failed", self._address(r))
        return None

    def _work_replays(self, records: list[Session], now: datetime) -> list[Session]:
        """What a start by rule 8 replays: the team's crew records that ended — the wound-down reading
        has every one that is not a seat declared — seats included, none superseded, suspended, at
        its ceiling, sat out by the team's flow (§4.9c: not started until a flow that uses it returns)
        or without a launch record (no launch record, no start: the rule never invents a
        team). A seat's `fill` entries are not counted toward `RESTART_CEILING`, as rule 3 never counts
        them (the techlead's read of #792). The records other ones name as a controller come first, so
        the lead is up before its members, as a person's start makes it."""
        out = []
        for r in work_mod.crew(records):
            if r.state not in work_mod.DEAD or r.superseded_by or r.suspended or r.restart_ceiling:
                continue
            if work_mod.sat_out(r):
                continue  # it did not end by the team's own ending: its flow sat it out (§4.9c)
            recent = agent_common._counted(r.restarts, now, fills=False)
            if len(recent) >= RESTART_CEILING:
                continue
            if not (paths.launch_dir() / f"{self._address(r)}.json").is_file():
                continue
            out.append(r)
        leads = {c for r in out for c in self._ctl(r)}
        return sorted(out, key=lambda r: (self._address(r) not in leads, r.name, r.id))

    def _work_held(
        self,
        replays: list[Session],
        started: list[str],
        conf: dict[str, Any],
        now: datetime,
        team: str,
        records: list[Session] | None = None,
        standing: Any = None,
    ) -> dict[str, Any] | None:
        """The bound that holds rule 8's start back, in the design's order, or None (§6 rule 8): a
        member's profile over its usage line; the team's stop time passed and not cleared;
        `WORK_STARTS_DAY` starts in the day; a start inside `WORK_EARLY`; its repo over the team's
        balance line (`_work_balance`). Nothing to replay holds it too: the row of `ask` is the
        person's way to start a team the rule cannot. A standing balance hold that an earlier bound
        displaces is kept beside it as `balance`, so a failed reading when that bound lifts does not
        start a team that was over its line (TD-330)."""
        prior = _balance_hold(standing)
        kept = {"balance": prior} if prior is not None else {}
        if not replays:
            return {"why": "nothing", **kept}
        for profile in dict.fromkeys(r.profile for r in replays):
            if (over := self._profile_over(profile, now, team)) is not None:
                # the window's reset, when the reading has one, so the row can say when the hold lifts
                resets = {"resets": over["resets"]} if over.get("resets") else {}
                return {"why": "usage", "profile": profile, **resets, **kept}
        until = conf.get("until")
        with contextlib.suppress(TypeError, ValueError):
            if until and _parse(str(until)) <= now:
                return {"why": "until", "until": str(until), **kept}
        if len(started) >= agent_common.WORK_STARTS_DAY:
            return {"why": "day", "count": len(started), **kept}
        if started and _recent(started[-1], now, agent_common.WORK_EARLY):
            return {"why": "early", "started": started[-1], **kept}
        return self._work_balance(conf.get("balance"), records or replays, now, prior)

    def _work_balance(
        self, bal: dict[str, Any] | None, records: list[Session], now: datetime, standing: Any = None
    ) -> dict[str, Any] | None:
        """Rule 8's fifth bound (§6 *Balance*): the team's lines read by the rule itself, since the mark
        went with the team's last live member — against every registry root its records name, either
        crossing counts, as `_balance_mark` reads a live team, and for `review` the queue at its seats,
        which keep their inbox when they end, against the shortest bound its records carry.
        `{why: balance, repo, crossed}` when a line is crossed. A reading that cannot be told writes no
        new hold and lifts none (a failed reading crosses nothing and clears nothing); a standing hold
        whose repo, lines and limits are unchanged is kept as it stands, so an age's `value` is the
        crossing's and the tick writes nothing while it holds (the techlead's read of #799)."""
        if not bal:
            return None
        records = [r for r in records if not r.superseded_by]  # a resumed record's successor holds its inbox
        roots = sorted({r.repo for r in records if r.repo and r.repo in self._repos})
        waiting = [str(w["oldest"]) for r in records if r.seat is not None and (w := r.prs_waiting(home=self.host))]
        bounds = [d for r in records if (d := balance_mod.span((r.review or {}).get("bound")))]
        got = balance_mod.crossed(
            bal, roots, self._repos, min(waiting) if waiting else None,
            min(bounds) if bounds else balance_mod.REVIEW_BOUND, now,
        )  # fmt: skip
        kept = _balance_hold(standing)
        if got is None:
            return kept
        if not got[0]:
            return None
        if kept is not None and kept.get("repo") == got[1] and _lines(kept.get("crossed")) == _lines(got[0]):
            return kept
        return {"why": "balance", "repo": got[1], "crossed": got[0]}

    async def _finished_pass(self, now: datetime) -> None:
        """Rule 9 (design §6 *Finished is the home's reading*, TD-241): on every tick at the home,
        each team whose records read **finished** (`work.finished`, the card's own *concluded*) with
        no restart wanted, for `FINISHED_SETTLE`, is wound down by the home itself — its finished
        members closed under the wrap-up's safety check, its manager told once and closed after
        `WRAPUP_GRACE`, and the person told what the manager did not say. When the reading first held
        is kept in memory per team and dropped the tick it stops holding. One team's surprise is a
        log line, never another's."""
        if self.mode != "home":
            return
        by_team: dict[str, list[Session]] = {}
        for r in self._graph().values():
            if r.team:
                by_team.setdefault(r.team, []).append(r)
        firsts: dict[str, datetime] = {}
        for team in sorted(by_team):
            try:
                first = await self._finished_team(team, by_team[team], now)
            except Exception:  # noqa: BLE001 — one team's surprise is a log line, never the others'
                log.exception("reading whether %s has finished failed", team)
                first = self._finished_first.get(team)  # a surprise is not the reading no longer holding
            if first is not None:
                firsts[team] = first
        self._finished_first = firsts
        for team in [t for t in self._finished_owed if t not in by_team]:
            del self._finished_owed[team]  # its records are forgotten: nothing left to announce

    def _finished_view(self, r: Session) -> dict[str, Any]:
        """What `work.finished` reads of a record, as this host addresses it: a node's member names
        its controllers in its own host's form (§4.4a), and the manager is found by address."""
        return {
            "id": self._address(r),
            "name": r.name,
            "state": r.state,
            "pane": r.pane,
            "unattended": r.unattended,
            "superseded_by": r.superseded_by,
            "seat": r.seat,
            "capabilities": r.capabilities,
            "controllers": self._ctl(r),
            "out_of_work": r.out_of_work,
            "restart_wanted": r.restart_wanted,
        }

    async def _finished_team(self, team: str, records: list[Session], now: datetime) -> datetime | None:
        """One team's pass of rule 9: when its reading first held, to keep for the next tick, or None
        when it does not hold or the wind-down is over."""
        by_address = {self._address(r): r for r in records}
        views = [self._finished_view(r) for r in records]
        mine = [v for v in views if v["unattended"] is not False and not v["superseded_by"]]
        top = work_mod.manager_of(mine)
        manager = by_address[top["id"]] if top is not None else None
        members = [
            by_address[v["id"]] for v in mine if v["seat"] is None and v is not top and v["state"] not in work_mod.DEAD
        ]
        first = self._finished_first.get(team)
        led = manager is not None and manager.state not in work_mod.DEAD
        if (
            team in self._finished_owed
            and not members
            and not led
            and not (manager is not None and manager.finished_sent_at)
        ):
            # the member left open with work is gone by another hand — a person's Close, a kill —
            # and nobody live announced the team: the note is written now, whoever ended it
            since = self._finished_owed.pop(team)
            if not work_mod.closed_finished(manager):
                self._finished_tell(team, records, manager, since)
                await self._push_changes()
            return None
        if manager is not None and manager.finished_sent_at:
            # the manager's half: from the send on the reading is no longer asked of it — its last
            # acts are work — and only a member live and not finished takes the wind-down back
            since = first or _parse(manager.finished_sent_at) - agent_common.FINISHED_SETTLE
            asked = work_mod.waiting_of(self.person_inbox)
            if any(
                m.state != "idle" or m.restart_wanted or not m.out_of_work or asked.get(self._address(m))
                for m in members
            ):
                manager.finished_sent_at = None
                self._save(manager)
                log.info("rule 9: %s has a member at work again — the wind-down is off", team)
                await self._push_changes()
                return None
            for m in members:
                # one left open with work is closed once it is pushed, the manager live or gone
                await self._finished_close(m)
            if manager.state in work_mod.DEAD:
                if manager.state == "closed" and not work_mod.closed_finished(manager):
                    # it closed itself, as the line asked: the mark is what lets the team read
                    # *wound down* without its declaration, and what says the person was told. One
                    # that `exited` — a crash, a kill — is rule 1's or a person's, and is left
                    self._mark_closed(manager, "finished")
                    self._save(manager)
                    self._finished_tell(team, records, manager, since)
                    await self._push_changes()
                return None
            grace = now - _parse(manager.finished_sent_at) >= agent_common.WRAPUP_GRACE
            if grace and await self._finished_close(manager):
                self._mark_closed(manager, "finished")
                self._save(manager)
                self._finished_tell(team, records, manager, since)
                await self._push_changes()
                return None
            return since
        reading = work_mod.finished(views, waiting=work_mod.waiting_of(self.person_inbox))
        if reading is None or reading["why"] or reading["restart"]:
            self._finished_owed.pop(team, None)  # at work again, or started again: nothing is owed
            return None  # not finished, or one that wants another run: rule 2's
        first = first or now
        if now - first < agent_common.FINISHED_SETTLE:
            return first
        closed = [m for m in members if await self._finished_close(m)]
        left = [m for m in members if m not in closed]
        if closed:
            log.info("rule 9: %s finished — closed %s", team, ", ".join(m.name for m in closed))
        if manager is None or manager.state in work_mod.DEAD:
            if left:
                # one left open with work: the note waits for the tick that finds the last one gone,
                # kept in memory as the settle is, so a close by a person is announced too (TD-256)
                self._finished_owed.setdefault(team, first)
                return first
            self._finished_owed.pop(team, None)
            if closed and not work_mod.closed_finished(manager):
                # nobody live to tell; one rule 9 closed was announced then, and is never told twice
                self._finished_tell(team, records, manager, first)
                await self._push_changes()
            return None
        if manager.suspended:
            return first
        if manager.host != self.host:
            # a node's manager gets no line and the close alone, routed as rule 2 routes one
            if await self._finished_close(manager):
                self._mark_closed(manager, "finished")
                self._save(manager)
                self._finished_tell(team, records, manager, first)
                await self._push_changes()
                return None
            return first
        line = (
            "[agentorc] your team is finished: every member has declared. Make your last acts — "
            "`ao progress none`, the note to the person — then `ao close` yourself"
        )
        if manager.state == "idle" and await self._policy_send(manager, line):
            manager.finished_sent_at = now_iso()
            log.info("rule 9: %s finished — its manager %s told", team, manager.name)
            self._save(manager)
            await self._push_changes()
        return first

    async def _finished_close(self, s: Session, why: str = "finished") -> bool:
        """Rule 9's close, under the wrap-up's own safety check in the tick's form (rule 2's): only an
        `idle` record whose git fields are known and show nothing uncommitted and nothing unpushed,
        never a suspended one, and a node's only while its link is up. True when it was closed. A
        sit-out's close is the same close (§4.9c), under `why: sit_out`."""
        if s.state != "idle" or s.suspended or not s.unattended:
            return False
        git = s.git or {}
        if not all(isinstance(git.get(k), int) and git[k] == 0 for k in ("dirty", "unpushed")):
            return False  # work left, or not known: left open, and the Inbox row it is after any wrap-up
        if s.host != self.host and s.host not in self._link_muxes:
            return False  # its link is down: looked at again next tick (§4.4a)
        try:
            closer = {"by": "tick", "why": why}
            if s.host == self.host:
                await self.rpc_close(s.id, closer=closer)
            else:
                await self._route_act("close", {"id": s.id, "closer": closer}, None, s.host)
        except Exception as e:  # noqa: BLE001 — a close that failed is tried again on the next tick
            log.warning("%s: the %s close failed: %s", self._address(s), why, e)
            return False
        return s.state == "closed"

    def _finished_tell(self, team: str, records: list[Session], manager: Session | None, since: datetime) -> None:
        """The announcement the manager did not make (design §6 rule 9, §4.9a): one `system` note to
        the person — the pull requests the members reported `done` since the team's start, the
        earliest `created` among its records that are not superseded, and each member's
        `out_of_work.why` — written only when no `note` from the manager reached the person inbox
        since the reading first held, so a team that dissolves is never quiet and never told twice."""
        if manager is not None:
            sender = self._address(manager)
            for e in self.person_inbox:
                try:
                    if e.from_ == sender and e.kind == "note" and _parse(e.at) >= since:
                        return
                except (ValueError, TypeError):
                    continue
        mine = [r for r in records if r.unattended and not r.superseded_by]
        start = min((r.created for r in mine if r.created), default="")
        members = [r for r in mine if r.seat is None and r is not manager]
        prs = sorted(
            {
                e.pr
                for r in members
                for e in r.progress
                if e.status == "done" and e.pr and (not start or str(e.at) >= start)
            }
        )
        said = "; its manager did not announce it" if manager is not None else ""
        lines = [
            f"{team} finished and the host agent wound it down: every member declared out of work{said}. "
            "Nothing is asked of you.",
            "",
            ("Pull requests reported done since the team started: " + ", ".join(f"#{n}" for n in prs) + ".")
            if prs
            else "No pull request was reported done since the team started.",
        ]
        for r in sorted(members, key=lambda r: (r.name, r.id)):
            why = (r.out_of_work or {}).get("why") if isinstance(r.out_of_work, dict) else None
            if why:
                lines.append(f"{r.name}: {why}")
        if more := self._finished_more(team, mine, members, start):
            lines += ["", *more]
        self._system_note(PERSON, "\n".join(lines), team=team)
        log.info("rule 9: %s wound down by the tick — the person told", team)

    def _finished_more(self, team: str, mine: list[Session], members: list[Session], start: str) -> list[str]:
        """What rule 9's note adds under its first lines (§4.9a *The home's note says more*, TD-468),
        each line only when it has something to say: the members' claims left standing and their
        drops since `start`, with each one's why; their restarts since `start` by `why`, and any at
        the restart ceiling (a `wanted` one under the member's own why; a person's Restart and a start
        are not the tick's restarts); the identity alarms standing on the team's records; each open
        `ask` or `steer` a member put to the person about a reference, with its bound; and each profile the
        records name, its window readings now beside `teams.<team>.usage_at_start`."""
        out: list[str] = []
        for r in sorted(members, key=lambda r: (r.name, r.id)):
            left = [f"{e.ref} left claimed" for e in r.progress if e.status == "claimed"]
            left += [
                f"{e.ref} dropped" + (f" — {e.why}" if e.why else "")
                for e in r.progress
                if e.status == "dropped" and (not start or str(e.at) >= start)
            ]
            if left:
                out.append(f"Claims left — {r.name}: {'; '.join(left)}")
        whys: dict[str, int] = {}
        for r in members:
            for e in r.restarts:
                if not isinstance(e, dict) or e.get("why") in NOT_RESTARTS:
                    continue
                if not start or str(e.get("at") or "") >= start:
                    word = str(e.get("said") or e.get("why") or "restarted")
                    word = "cache lapsed" if word == "cache" else word
                    whys[word] = whys.get(word, 0) + 1
        ceiling = sorted(r.name for r in members if r.restart_ceiling)
        if whys or ceiling:
            n = sum(whys.values())
            said = f"{n} restart{'' if n == 1 else 's'}: " + ", ".join(
                f"{c} {w}" for w, c in sorted(whys.items(), key=lambda x: (-x[1], x[0]))
            )
            if ceiling:
                said = (said + "; " if whys else "") + "at the restart ceiling: " + ", ".join(ceiling)
            out.append(f"Restarts — {said}")
        alarms = [r for r in mine if r.identity_alarms]
        if alarms:
            n = sum(len(r.identity_alarms) for r in alarms)
            names = ", ".join(sorted(r.name for r in alarms))
            out.append(f"Alarms — {n} identity alarm{'' if n == 1 else 's'} standing on {names} (§4.8a)")
        asked = work_mod.waiting_of(self.person_inbox)
        for r in sorted(members, key=lambda r: (r.name, r.id)):
            qs = asked.get(self._address(r)) or []
            if qs:
                each = [
                    f"{q['ref']} ({q['id']}"
                    + (
                        f", until {work_mod.bound_words(q.get('bound'))})"
                        if work_mod.bound_words(q.get("bound"))
                        else ")"
                    )
                    for q in qs
                ]
                out.append(f"Open to you — {r.name}: {'; '.join(each)}")
        at_start = ((self._host_rec.get("teams") or {}).get(team) or {}).get("usage_at_start") or {}
        for prof in sorted({r.profile for r in mine if r.profile}):
            now = [
                w for w in (self._usage.get(prof) or {}).get("windows") or [] if isinstance(w, dict) and w.get("label")
            ]
            was = at_start.get(prof) if isinstance(at_start.get(prof), dict) else {}
            each = []
            for w in now:
                if not isinstance(w.get("pct"), int | float):
                    continue
                then = was.get(str(w["label"]))
                from_ = f", from {round(then)}%" if isinstance(then, int | float) else ""
                each.append(f"{w['label']} {round(w['pct'])}%{from_}")
            if each:
                out.append(f"Usage — {prof}: {'; '.join(each)}")
        return out

    @staticmethod
    def _owed_part(ids: list[str]) -> str | None:
        """Rule 4's owed clause (design §6, TD-258): the outcomes a record owes the person, named
        apart from its open work — the ids from the record's own `owed()` reading, three at most."""
        if not ids:
            return None
        named = ", ".join(ids[:OWED_NAMED]) + (f" and {len(ids) - OWED_NAMED} more" if len(ids) > OWED_NAMED else "")
        return (
            f"you owe {len(ids)} outcome{'' if len(ids) == 1 else 's'} on {named} — "
            f'`ao msg person --outcome done|blocked|dropped "…" --for {ids[0] if len(ids) == 1 else "<id>"}`'
        )

    def _nudge_line(self, s: Session) -> str | None:
        if s.seat is not None:
            n = s.asks_waiting(home=self.host) if s.seat_due else 0
            # an entry the person handed the seat owes its outcome, read or not: named apart from the questions (TD-218)
            h = s.asks_waiting(home=self.host, handed_only=True) if n else 0
            parts = [f"you have {n - h} questions waiting — run `ao inbox`"] if n > h else []
            if h:
                parts.append(
                    f"{h} {'entry' if h == 1 else 'entries'} the person handed you "
                    f"{'owes its' if h == 1 else 'owe their'} outcome — "
                    '`ao msg person --outcome done|blocked|dropped "…" --for <id>`'
                )
            # the seat's own questions the person answered (TD-258): a handed entry is the part above
            owed = self._owed_part([e.id for e in s.outbox if e.owes_for(session_inbox=False)])
            parts += [owed] if owed else []
            return "[agentorc] " + "; ".join(parts) if parts else None
        ref = self._open_ref(s)
        owed = self._owed_part(s.owed())
        minutes = int(IDLE_NUDGE.total_seconds() // 60)
        if ref is None:
            return f"[agentorc] you have been idle {minutes} minutes: {owed}" if owed else None
        return (
            f"[agentorc] you have been idle {minutes} minutes with `{ref}` open "
            f"— end the run with one of `ao progress done {ref} --pr N`, `ao progress drop {ref} --why`, "
            "`ao progress none --why` or `ao progress restart --why`" + (f"; {owed}" if owed else "")
        )

    async def _policy_send(self, s: Session, text: str) -> bool:
        """A policy's fixed line, typed through `send`'s path under the doorbell's rules (§6, §4.10):
        one typist per pane, never at a dialog, only into an empty composer — someone's half-typed
        words would be submitted with it. Recorded on `sends` as the home's own (`system`). False
        when it was not typed: the tick looks again next time."""
        adapter = adapters.get(s.adapter)
        if getattr(adapter, "composer", None) is None or self._on_a_dialog(s):
            return False
        typing = self._typing[s.id]
        if typing.locked():
            return False
        async with typing:
            tail = await asyncio.to_thread(self.tmux.capture_tail, s.id, COMPOSER_LINES, raw=True)
            if adapter.composer(tail) != "":
                return False
            entry = self._record_send(s, SYSTEM, text)
            try:
                await self._type(s.id, adapter, text)
            except Exception as e:  # noqa: BLE001 — a failed send is not a sent one; next tick looks again
                entry.verdict = str(e) or type(e).__name__
                self.store.save(s)
                return False
        return True

    def _mark_team_start(self, team: str, but: Session | None = None) -> None:
        """A start of `team` (§4.9a *The home's note says more*, TD-468): `ao team start`'s creates, a
        schedule's start and rule 8's — each counts only when no unattended record of the team runs
        but `but`, the one being started. The home's usage readings now (the `usage` RPC's windows)
        are kept as `teams.<team>.usage_at_start: {profile: {window: pct}}` on the host record,
        replacing the last start's; with no reading the key goes. At the home only."""
        if self.mode != "home" or not team:
            return
        for r in self._graph().values():
            if r is but or r.team != team or not r.unattended or r.superseded_by:
                continue
            if r.state not in ("exited", "closed", "scheduled"):
                return
        readings: dict[str, dict[str, float]] = {}
        for prof, reading in self._usage.items():
            windows = {
                str(w["label"]): w["pct"]
                for w in (reading or {}).get("windows") or []
                if isinstance(w, dict) and w.get("label") and isinstance(w.get("pct"), int | float)
            }
            if windows:
                readings[prof] = windows
        teams = self._host_rec.setdefault("teams", {})
        rec = teams.get(team) if isinstance(teams.get(team), dict) else {}
        if readings:
            rec["usage_at_start"] = readings
        else:
            rec.pop("usage_at_start", None)
        if rec:
            teams[team] = rec
        else:
            teams.pop(team, None)
        try:
            self.host_store.save(self._host_rec)
        except OSError:
            log.exception("writing the home's host record failed")

    async def _replay(
        self, s: Session, why: str, *, mark: dict[str, Any] | None = None, closing: str | None = None, **extra: Any
    ) -> None:
        """One restart by the tick (§6): the record's launch record handed to `create` again — here,
        or at its node — the attempt appended to `restarts` and the list carried onto the new record,
        so the count survives the restart it counts. A replay that fails keeps its entry with `error`
        and counts all the same. `mark` adds to the entry (rule 8's `ids`, what a start was for).
        `closing` is a line the prompt ends with, after it is filled again or stored (a manager on
        call's cause, `filled_for`, TD-410); no other replay adds one.
        Every caller but a scheduled start (whose `start_of` moves the mail itself) passes `keep_mail`:
        the closed run's mail is the new record's, so a reply to its `ask` still has a thread (TD-352).
        Every entry, a failed one's too, carries `done` and `left` — what the run it replaces reported
        (`agent_common._reported`) — since the new record keeps none of the old one's `progress` (§4.9a, TD-245)."""
        if why in ("start", "work"):  # a schedule's start and rule 8's: the team's, if none of it runs
            self._mark_team_start(s.team, but=s)
        now = datetime.now(UTC)
        entry: dict[str, Any] = {
            "at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "why": why,
            **agent_common._reported(s, now),
            **(mark or {}),
        }
        history = [*s.restarts, entry]
        address = self._address(s)
        read: dict[str, Any] | None = None
        try:
            params = {**self._read_launch(address), **extra, "supervised": True}
            read = await self._refill_prompt(s, params, entry)
            if params.get("prompt"):
                # the launch record keeps the prompt as this create hands it, so a stored prompt carries
                # the last fill's line: it goes, whatever replays now, and a fill's own is added
                params["prompt"] = params["prompt"].split(f"\n\n{FILLED_FOR}", 1)[0]
                if closing:
                    params["prompt"] = f"{params['prompt']}\n\n{closing}"
            if s.host == self.host:
                view = await self.rpc_create(**params)
            else:
                view = await self._route_act("create", {**params, "host": s.host}, None, s.host)
        except Exception as e:  # noqa: BLE001 — a failed replay is a restart that failed, and counts (§6)
            entry["error"] = str(e) or type(e).__name__
            log.warning("%s: the %s restart failed: %s", s.id, why, entry["error"])
            s.restarts = history
            self._save(s)
            await self._push_changes()
            return
        rid, _h = naming.split_address(str((view or {}).get("id") or ""))
        new = self.sessions.get(rid) if s.host == self.host else self.remote.get(s.host, {}).get(rid)
        if new is not None:
            new.restarts = history
            # the files as this replay read them, not as the create's check did; a replay of the stored
            # prompt carries none, since the create's working-tree read is not what the prompt was made
            # from, and the mark must not compare it (the techlead's read of #745)
            new.brief = read
            self._save(new)
            await self._push_changes()

    async def _refill_prompt(self, s: Session, params: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
        """Rule 7's first half (design §6, TD-217 slice 2): a replay fills the brief again from what
        it was made from (`prompt_from`), with its files as merged, and hands `create` that prompt
        in place of the stored one; returns the record's `brief` as read. A launch record with no
        `prompt_from` (a prompt typed whole, a record written before it), a file that cannot be read,
        or a member on a node (its files are that host's, not the home's) replays the stored prompt,
        and the `restarts` entry says `prompt: stored`."""
        made = params.get("prompt_from")
        if not params.get("prompt"):
            return None  # nothing was typed at its start, and nothing is now
        if not isinstance(made, dict) or s.host != self.host:
            entry["prompt"] = "stored"
            return None
        try:
            text, sources = await asyncio.to_thread(brief_mod.fill, made, True)
        except brief_mod.Unreadable as e:
            log.info("%s: replaying the stored prompt: %s", s.id, e)
            entry["prompt"] = "stored"
            return None
        params["prompt"] = text
        return {"at": now_iso(), "sources": sources}

    def _read_launch(self, address: str) -> dict[str, Any]:
        """A launch record as `create`'s arguments (design §6): what `_write_launch` kept, less its own
        bookkeeping. Raises when there is none — a supervised record written before its launch record
        existed has nothing to replay, and that is a failed restart, said as one."""
        path = paths.launch_dir() / f"{address}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RpcError(f"no launch record for {address}: nothing to replay (design §6)") from None
        if not isinstance(record, dict):
            raise RpcError(f"the launch record of {address} is not a record")
        params = {k: record[k] for k in LAUNCH_KEYS if k in record}
        if isinstance(record.get("controllers"), list):
            params["controllers"] = list(record["controllers"])
        return params

    async def _seat_pass(self, s: Session, now: datetime, records: list[Session]) -> None:
        """Rule 3 (design §6 *Keeping a team running*, §4.9b): a supervised seat's `seat_due` from its
        trigger, the fill of an ended seat that is due — `create` with `keep_mail`, so a question that
        was waiting is still there — under `FILL_CEILING`, and the close of a seat that has run and
        sits idle with nothing due and nothing left unpushed."""
        if not (s.seat and s.supervised and s.unattended) or s.superseded_by or s.suspended:
            return
        filled, seen = s.seat_filled, s.lane_seen
        due = self._seat_due(s, now, records)
        held = s.seat_held if due else None  # nothing due, nothing waits on the checkout
        if due != s.seat_due or s.seat_filled != filled or s.lane_seen != seen or held != s.seat_held:
            s.seat_due, s.seat_held = due, held
            self._save(s)
            await self._push_changes()
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        if s.state in ("exited", "closed") and s.seat_due:
            await self._fill(s, now, records)
        elif s.state == "idle" and self._seat_done(s) and self._seat_has_run(s, now):
            waits = self._seat_prs(s, records)
            if waits and now - _parse(s.since) < SEAT_PR_WAIT:
                return  # it ended its turn to wait on its own PR's fact-check or CI (TD-366)
            if waits:
                log.info(
                    "%s: a seat idle %s on its own PR %s, which no reader holds — closing it (§6 rule 3)",
                    s.id,
                    SEAT_PR_WAIT,
                    ", ".join(waits),
                )
            else:
                log.info("%s: a seat with nothing due, idle and pushed — closing it (§6 rule 3)", s.id)
            closer = {"by": "tick", "why": "seat"}
            if s.host == self.host:
                await self.rpc_close(s.id, closer=closer)
            else:
                await self._route_act("close", {"id": s.id, "closer": closer}, None, s.host)

    @staticmethod
    def _seat_done(s: Session) -> bool:
        """Whether an idle seat has nothing left that this run of it will take: nothing due — or,
        for a manager on call, a reading of a member that came due after its fill, which the next
        fill is for, since a fill starts cold on the one reading its `seat_due` names (§6 rule 3,
        TD-259). A question waiting is the idle seat's own to read, as the techlead's is."""
        if not s.seat_due:
            return True
        # the anchor's (TD-386): ids its lane gained while it sat idle are the next fill's, from cold
        if (s.seat or {}).get("trigger") == "work":
            return True
        return (s.seat or {}).get("trigger") == "team" and s.seat_due.get("by") != "asks"

    def _team_causes(self, s: Session, records: list[Session]) -> list[dict[str, Any]]:
        """What a manager on call is filled for (§6 rule 3, TD-259), each a reading of the records
        and never of a screen, in this order: `asks` — each open `ask` or `steer` addressed to it,
        by id; then, of its members (the supervised records listing it in `controllers`), `pending`
        — hook-confirmed `needs-you` on a permission (a question or a menu is a person's);
        `stalled` — `stalled?`; `open` — carrying `idle_open`."""
        causes: list[dict[str, Any]] = [{"by": "asks", "ask": i} for i in s.asks_ids(home=self.host)]
        me = self._address(s)
        members = [
            r
            for r in records
            if r is not s and r.supervised and not r.superseded_by and not r.suspended and me in self._ctl(r)
        ]
        for by in ("pending", "stalled", "open"):
            for r in members:
                if by == "pending":
                    met = (
                        r.state == "needs-you"
                        and r.confidence == "hook"
                        and (r.pending or {}).get("kind") == "permission"
                    )
                elif by == "stalled":
                    met = r.state == "stalled?"
                else:
                    met = r.state == "idle" and bool(r.idle_open)
                if met:
                    causes.append({"by": by, "member": self._address(r)})
        return causes

    def _seat_due(self, s: Session, now: datetime, records: list[Session]) -> dict[str, Any] | None:
        """`seat_due: {at, by}` (§6 rule 3): set once the trigger is met and kept until the fill — but
        `asks` is a question waiting now, so it clears again if none is. `prs` reads `seat_count`,
        which `_count_seats` keeps; `every` is the time since this record was created. `team` is a
        manager on call's (TD-259): `{at, by, member}` — `ask` in `member`'s place for `asks` — the
        first cause standing that no fill was made for; `seat_filled` loses each entry whose cause
        has gone, here, and a due whose cause went before the fill is cleared as `asks` is."""
        seat = s.seat or {}
        trigger = seat.get("trigger")
        if trigger == "team":
            causes = self._team_causes(s, records)

            def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
                return all(a.get(k) == b.get(k) for k in ("by", "member", "ask"))

            kept = [e for e in s.seat_filled if isinstance(e, dict) and any(same(e, c) for c in causes)]
            if kept != s.seat_filled:
                s.seat_filled = kept
            fresh = [c for c in causes if not any(same(e, c) for e in kept)]
            if s.seat_due and any(same(s.seat_due, c) for c in fresh):
                return s.seat_due
            return {"at": now_iso(), **fresh[0]} if fresh else None
        if trigger == "asks":
            return (s.seat_due or {"at": now_iso(), "by": "asks"}) if s.asks_waiting(home=self.host) else None
        if trigger == "work":
            return self._work_due(s)
        if s.seat_due:
            return s.seat_due
        met = False
        if trigger == "prs":
            count = (s.seat_count or {}).get("prs")
            after = str(seat.get("after") or "")
            met = after.isdigit() and isinstance(count, int) and count >= int(after)
        elif trigger == "every":
            every = _duration(str(seat.get("after") or ""))
            met = every is not None and now - _parse(s.created) >= every
        return {"at": now_iso(), "by": trigger} if met else None

    def _seat_reading(self, s: Session) -> list[Any] | None:
        """The anchor seat's repo's reading — the ledger's entries, then the board's work orders —
        or None where the ledger was not read, which changes nothing (§6 rule 3, TD-386)."""
        r = self._repos.get(s.repo or "") or {}
        led = r.get("ledger") or {}
        if "error" in led or not isinstance(led.get("entries"), list):
            return None
        return [*led["entries"], *((r.get("work_orders") or {}).get("orders") or [])]

    @staticmethod
    def _seat_lane_word(s: Session) -> list[str]:
        """The anchor seat's lane, `[anchor]` as its preset writes it."""
        return list(s.lane or [ledger_mod.ANCHOR_OWNER])

    def _seat_lane(self, s: Session) -> list[str] | None:
        """The ids the anchor seat's lane takes of its repo's reading (§6 rule 3 and rule 6, TD-386):
        the ledger's entries and the board's work orders by `lane_matches`; None where the ledger
        was not read, which changes nothing."""
        got = self._seat_reading(s)
        if got is None:
            return None
        lane = self._seat_lane_word(s)
        ids = [str(e["id"]) for e in got if isinstance(e, dict) and e.get("id") and ledger_mod.lane_matches(lane, e)]
        return list(dict.fromkeys(ids))

    def _work_due(self, s: Session) -> dict[str, Any] | None:
        """The `work` trigger (§6 rule 3 *The anchor seat*, TD-386): `seat_due: {at, by: work, ids}`
        while the seat's lane holds ids its `lane_seen` does not. `lane_seen` is written from the
        reading the first time it is missing — at the record's create, or after a `none`, which
        clears it — so what stood then is the run's that saw it, and a cause fills once per stretch:
        the same ids after a `none` raise nothing. A due whose ids all left the lane before the fill
        is cleared. No reading keeps what stands. `lane_seen` is pruned as rule 6 prunes it
        (`work.reread`, TD-407): an id the lane no longer takes leaves it, so a live check that goes
        live after the seat saw it as a build is due again."""
        got = self._seat_reading(s)
        if got is None:
            return s.seat_due
        if s.lane_seen is None:
            s.lane_seen = {"at": now_iso(), "ids": self._seat_lane(s) or []}
        s.lane_seen, new = work_mod.reread(s, got, self._seat_lane_word(s))
        if not new:
            return None
        at = (s.seat_due or {}).get("at") if (s.seat_due or {}).get("by") == "work" else None
        return {"at": at or now_iso(), "by": "work", "ids": new}

    async def _checkout_held(self, s: Session) -> dict[str, Any] | None:
        """Why the anchor seat may not be filled into its checkout now (§6 rule 3, §4.9b, TD-386), as
        `seat_held: {by, why}`, or None: a session holding the checkout (§9 invariant 2's occupancy,
        a person's hand-started one included), else the tree itself dirty or off its default branch
        — the person's work, which the seat never touches. A node's checkout is read for occupancy
        alone, from its records, since its tree is not this host's to read."""
        directory = Path(s.dir)
        if s.host == self.host:
            holders = await asyncio.to_thread(self.occupants, directory)
        else:
            holders = [
                f"{r.id} ({r.state})"
                for r in (self.remote.get(s.host) or {}).values()
                if r is not s
                and r.kind == "interactive"
                and r.adapter != "shell"
                and r.state not in ("exited", "closed")
                and r.dir == s.dir
            ]
        holders = [h for h in holders if not h.startswith(f"{s.id} ")]
        return await self._checkout_why(directory, holders, s.host == self.host)

    async def _checkout_why(self, directory: Path, holders: list[str], local: bool) -> dict[str, Any] | None:
        """The one reading of a seat's checkout (§6 rule 3, §4.9b, TD-386, TD-395), the fill's and the
        Start's: the first holder, else — on this host — the tree's own reason, else None."""
        if holders:
            return {"by": holders[0].split(" ", 1)[0], "why": f"held by {holders[0]}"}
        if not local:
            return None
        why = await asyncio.to_thread(self._checkout_tree, directory)
        return {"by": "checkout", "why": why} if why else None

    def _checkout_tree(self, directory: Path) -> str | None:
        """Why a checkout's tree is not the anchor seat's to work in (TD-386): off its default branch,
        files uncommitted, or a git state that cannot be read — or None for a clean tree on its
        default branch. Occupancy is not read here: a seat's own `none` reads its tree alone (TD-395).
        Blocking: a thread's."""
        git = git_info(directory)
        if git is None:
            return "git state unknown"
        # cadence's default-branch rule (`origin/HEAD`, `init.defaultBranch`, main, master on origin);
        # a checkout with no origin to ask reads either usual name as its default
        ref = ledger_mod.default_ref(directory)
        defaults = (ref.removeprefix("origin/"),) if ref else ("main", "master")
        why = [f"branch {git.branch}"] if git.branch not in defaults else []
        if git.dirty:
            why.append(f"{git.dirty} file{'' if git.dirty == 1 else 's'} uncommitted")
        return ", ".join(why) or None

    async def rpc_checkout_held(self, dir: str, host: str | None = None) -> dict[str, Any]:
        """The Start's reading of the anchor seat's checkout (§4.9b *The anchor seat*, TD-395): the
        fill's own (`_checkout_why`), so a Start and a fill never disagree — `held: {by, why}` or
        None. On another host the occupancy is that host's (`host_occupancy`) and the tree is not
        read, as for a node's fill. In `modes.HOME_ONLY` as `host_occupancy` is."""
        local = not host or host == self.host
        directory = Path(dir).expanduser()
        if not directory.is_dir() and local:
            raise RpcError(f"not a directory: {directory}")
        if local:
            directory = directory.resolve()
            holders = await asyncio.to_thread(self.occupants, directory)
        else:
            holders = [str(o) for o in (await self.rpc_host_occupancy(str(host), dir)).get("occupants") or []]
        return {
            "dir": str(directory),
            "host": host or self.host,
            "held": await self._checkout_why(directory, holders, local),
        }

    async def _fill(self, s: Session, now: datetime, records: list[Session]) -> None:
        """A due seat, ended, is filled — unless its profile is paused, or it or its fellows are at
        the fill ceiling: six fills an hour over all seats sharing a controller (the graph, never the
        team badge). The seat whose fill tripped it gets `restart_ceiling` and the Inbox row; its
        fellows are merely refused until the hour rolls. Fills never count toward `RESTART_CEILING`."""
        if s.restart_ceiling or self._profile_gated(s.profile, now, s.team):
            return
        work = (s.seat or {}).get("trigger") == "work"
        if work:
            # the anchor's fill is a `create` in the checkout itself: never over a holder, never into
            # the person's uncommitted work or branch; `seat_due` stands and the next tick tries again
            held = await self._checkout_held(s)
            if held != s.seat_held:
                s.seat_held = held
                if held:
                    log.info("%s: the seat is due but its checkout is not free — %s", s.id, held["why"])
                self._save(s)
                await self._push_changes()
            if held:
                return
        mine = set(self._ctl(s))
        fellows = [
            r for r in records if r.seat and not r.superseded_by and (r is s or (mine and mine & set(self._ctl(r))))
        ]
        fills = [
            e
            for r in fellows
            for e in r.restarts
            if isinstance(e, dict) and e.get("why") == "fill" and _recent(e.get("at"), now, FILL_WINDOW)
        ]
        if len(fills) >= FILL_CEILING:
            tripped = any(
                r is not s
                and isinstance(r.restart_ceiling, dict)
                and _recent(r.restart_ceiling.get("at"), now, FILL_WINDOW)
                for r in fellows
            )
            if not tripped:
                s.restart_ceiling = {"at": now_iso(), "count": len(fills), "why": "fill"}
                log.warning(
                    "%s: %d seat fills in %s — the ceiling; it is a person's now", s.id, len(fills), FILL_WINDOW
                )
                self._save(s)
                await self._push_changes()
            return
        log.info("%s: the seat is due (%s) — filling it", s.id, (s.seat_due or {}).get("by"))
        cause = s.seat_due if (s.seat or {}).get("trigger") == "team" else None
        # a manager on call says why it came (§6 rule 3 *A fill says why it came*, TD-410): its first
        # act is the reading `seat_due` names, read back from the record, never `ao status` first
        said = filled_for(cause)
        if cause and any(e.id == cause.get("ask") and e.handed_entry for e in s.inbox):
            cause = None  # an entry the person handed it fills the seat while it owes, as any seat's (TD-218)
        await self._replay(s, "fill", mark={"for": "seat_due"} if said else None, closing=said, keep_mail=True)
        new = self.sessions.get(s.id) if s.host == self.host else self.remote.get(s.host, {}).get(s.id)
        if cause and new is not None and new is not s:
            # the fill was made: the cause is remembered on the record that took the seat
            new.seat_filled = [*s.seat_filled, {**{k: v for k, v in cause.items() if k != "at"}, "at": now_iso()}]
            self._save(new)
        if work and new is not None and new is not s:
            # the stretch it was filled for is the new run's: the same ids raise no second fill
            ids = [*((s.lane_seen or {}).get("ids") or []), *((s.seat_due or {}).get("ids") or [])]
            new.lane_seen = {"at": now_iso(), "ids": list(dict.fromkeys(ids))}
            new.seat_due = new.seat_held = None
            self._save(new)

    def _seat_prs(self, s: Session, records: list[Session]) -> list[str]:
        """What of its own an idle seat may be waiting on (§6 rule 3, TD-366): an open PR on the branch
        it has checked out, as the repo reading has it; a claim on its record carrying a PR not read
        merged or closed; and a claim derived from that branch whose PR no reading has yet — what a
        seat that opened its PR a minute ago holds. A PR it handed to a reader (an `ask` carrying it,
        still open in any record's inbox) is the reader's, and nothing it waits on."""
        branch = str((s.git or {}).get("branch") or "")
        found: list[str] = []
        opened = ((self._repos.get(s.repo or "") or {}).get("prs") or {}).get("open") or []
        for p in opened:
            if branch and isinstance(p, dict) and p.get("branch") == branch and isinstance(p.get("number"), int):
                found.append(f"#{p['number']}")
        for e in s.progress:
            if e.status != "claimed" or e.why == PR_CLOSED:
                continue
            number = e.pr or e.review_pr
            if number:
                found.append(f"#{number}")
            elif e.source == "derived" and branch and e.branch == branch:
                found.append(branch)
        handed: set[str] = set()
        for r in records:
            for a in (r.prs_waiting(home=self.host) or {}).get("asks", []):
                sid, host = naming.split_address(str(a.get("from") or ""))
                if (sid, host or self.host) == (s.id, s.host or self.host):
                    handed.add(f"#{a['pr']}")
        return sorted({w for w in found if w not in handed})

    def _seat_has_run(self, s: Session, now: datetime) -> bool:
        """A seat to close (§6 rule 3): hook-confirmed idle for `SEAT_IDLE_GRACE`, with its git known
        and nothing uncommitted or unpushed — a seat that left work is the board's, as today."""
        git = s.git or {}
        return bool(
            s.state == "idle"
            and s.confidence == "hook"
            and s.since
            and now - _parse(s.since) >= SEAT_IDLE_GRACE
            and git
            and not git.get("dirty")
            and not git.get("unpushed")
            and not s.pending
        )

    async def _refresh_repos(self) -> None:
        """The repo facts (design §4.4 *Repo facts*, TD-176): for every checkout the repos registry
        lists, the PRs and the ledger with its history every `REPOS_EVERY`, and the ledger alone on
        any tick its file's mtime moved. The PRs are read once per remote, so two checkouts of one
        repo are one `gh` read. A read that failed keeps the last reading with `error` and
        `failed_at` beside it — an outage is never zero PRs. A changed reading is saved and pushed
        as a `repos` event; a checkout the registry no longer lists is dropped with `repo: null`."""
        try:
            due = time.monotonic() - self._repos_read_at >= REPOS_EVERY
            roots = hosts.local_host().repos()
            mtimes = await asyncio.to_thread(self._ledger_mtimes, roots)
            todo = [r for r in roots if due or mtimes.get(r) != self._ledger_mtime.get(r) or r not in self._repos]
            gone = [r for r in self._repos if r not in roots]
            if not (todo or gone):
                return
            prev = {r: self._repos.get(r) or {} for r in todo}
            # a checkout read for the first time gets the whole reading at once, not in five minutes
            full = {r for r in todo if due or r not in self._repos}
            live = self._live_commits() if self._promotes_read else None  # None: unknown (TD-516)
            got = await asyncio.to_thread(self._read_repos, todo, prev, full, roots, live) if todo else {}
            if due:
                self._repos_read_at = time.monotonic()  # after the read: a read that raised is retried next tick
            for root in todo:
                self._ledger_mtime[root] = mtimes.get(root)
            changed = {r: v for r, v in got.items() if v != self._repos.get(r)}
            for r in gone:
                self._repos.pop(r, None)
                self._ledger_mtime.pop(r, None)
            self._repos.update(changed)
            if changed or gone:
                self.repos_store.save(self._repos)
            for r in changed:
                await self._broadcast({"event": "repos", "root": r, "repo": self._repo_view(r)})
            for r in gone:
                await self._broadcast({"event": "repos", "root": r, "repo": None})
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next tick tries again
            log.exception("reading the repo facts failed")

    def _repo_view(self, root: str) -> dict[str, Any] | None:
        """A checkout's reading as the `repos` RPC and event serve it: the reading, and under
        `balance` the marks of the teams over their line in this repo (§6 *Balance*), by team."""
        v = self._repos.get(root)
        if v is None:
            return None
        marks = {
            team: rec["balance"]
            for team, rec in (self._host_rec.get("teams") or {}).items()
            if isinstance(rec.get("balance"), dict) and rec["balance"].get("repo") == root
        }
        return {**v, "balance": marks} if marks else dict(v)

    async def _balance_marks(self, now: datetime) -> None:
        """Design §6 *Balance* (TD-239): on every tick at the home, each team whose `teams.<team>.balance`
        is set is read against the repo facts of the registered repos its live members name (`repo`)
        and the reader's queue on its seats, and its mark — `balance: {since, repo, crossed}` on the
        home's `host` record under the team's name — is written, kept (its `since` with it) or removed.
        A reading that cannot be told leaves the mark as it stands; a team with no key, or with no live
        member, has none. A change is saved, and pushed as a `repos` event for each repo it touches;
        one team's surprise costs that team's reading this tick, never another's or the save."""
        teams = self._host_rec.setdefault("teams", {})
        try:
            settings = settings_mod.teams(settings_mod.load())
        except Exception:  # noqa: BLE001 — a policy's surprise is a log line; the next tick reads again
            log.exception("reading the teams' settings for the balance failed")
            return
        if not teams and not any(v.get("balance") for v in settings.values()):
            return
        live: dict[str, list[Session]] = {}
        for r in self._graph().values():
            if r.team and r.state not in ("exited", "closed") and not r.superseded_by:
                live.setdefault(r.team, []).append(r)
        touched: set[str] = set()
        dirty = False
        for team in sorted(set(teams) | set(settings)):
            try:
                rec = teams.get(team) or {}
                old = rec.get("balance")
                members = live.get(team) or []
                new = self._balance_mark(old, (settings.get(team) or {}).get("balance"), members, now)
                if new is ...:
                    continue  # could not look: the mark stands as it was, or stays absent
                if new is None:
                    self._balance_ring(team)
                told = self._balance_tell(team, rec, new, members, now)
                if new == old and not told:
                    continue
                dirty = True
                touched |= {m["repo"] for m in (old, new) if isinstance(m, dict) and m.get("repo")}
                if new is None:
                    rec.pop("balance", None)
                else:
                    rec["balance"] = new
                if rec:
                    teams[team] = rec
                else:
                    teams.pop(team, None)
            except Exception:  # noqa: BLE001 — one team's surprise is a log line, never the others'
                log.exception("reading %s's balance failed", team)
        if not dirty:
            return
        try:
            self.host_store.save(self._host_rec)
        except OSError:
            log.exception("writing the home's host record failed")
        for root in sorted(touched):
            await self._broadcast({"event": "repos", "root": root, "repo": self._repo_view(root)})

    def _balance_tell(
        self, team: str, rec: dict[str, Any], mark: dict[str, Any] | None, members: list[Session], now: datetime
    ) -> bool:
        """Design §6 *Balance*, *Who is told*: one `system` note to the team's manager and one to the
        person on a crossing, and the same pair when it clears. What was last told is kept on the team's
        `host` record (`balance_told: {state, at, since}`, `at` the last note of either kind), so a restart
        tells nothing twice; a crossing inside `FLAP` of the last note waits until it is that far from
        it, so a mark that comes and goes tells one pair. A team that wound down loses its mark with
        nobody to tell, and its memory with it. True when `rec` changed."""
        told = rec.get("balance_told") if isinstance(rec.get("balance_told"), dict) else {}
        if not members:
            return rec.pop("balance_told", None) is not None
        at = _parse(told["at"]) if told.get("at") else None  # when the last note was sent, either kind
        sent = now.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")  # the tick's clock
        if mark is not None:
            if told.get("state") == "over" or (at is not None and now - at < balance_mod.FLAP):
                return False
            text, told = (
                balance_mod.crossing(team, mark),
                {"state": "over", "at": sent, "since": mark.get("since")},
            )
        elif told.get("state") == "over":
            text, told = balance_mod.clearing(team, told), {**told, "state": "clear", "at": sent}
        elif told and (at is None or now - at >= balance_mod.FLAP):
            return rec.pop("balance_told", None) is not None  # the flap window is over: nothing left to hold
        else:
            return False
        for lead in self._balance_leads(members):
            self._system_note(lead, text)
        self._system_note(PERSON, text)
        rec["balance_told"] = told  # after the notes: one that raised is sent again next tick
        log.info("balance: %s", text)
        return True

    def _balance_leads(self, members: list[Session]) -> list[str]:
        """The controllers the team's members share — its manager (§6 *Balance*) — read from the members
        the rule refuses (unattended, no seat) that control no teammate; none when a person leads."""
        leads = {c for m in members for c in self._ctl(m)}  # in the home's form: a node's member is `id@host`
        sets = [set(self._ctl(m)) for m in members if self._address(m) not in leads and m.unattended and m.seat is None]
        return sorted(set.intersection(*sets)) if sets else []

    def _balance_ring(self, team: str) -> None:
        """The mark went: each live member refused while it stood is rung, once, within its wake budget
        (§6 *Balance*), and the field that held it back from the nudge and the lane news goes. A node's
        member is refused at the home, so its record here carries the field and the note lands in the
        home's copy of it, read at its next forwarded `inbox` or `wait`: nothing rings a node's idle
        member yet (§4.4a, TD-057)."""
        for s in self._graph().values():
            if s.team != team or not s.balance_refused:
                continue
            s.balance_refused = None
            if s.state in ("exited", "closed"):
                self._save(s)
                continue
            self._system_note(self._address(s), balance_mod.CLEAR)  # saves the record

    def _balance_mark(
        self, old: Any, bal: dict[str, Any] | None, members: list[Session], now: datetime
    ) -> dict[str, Any] | None | Any:
        """One team's mark as it reads now (`_balance_marks`): None when it has none, `...` when it
        cannot be told. The team's repos are the ones its live members name **that the home reads**
        — a registry root (§6 *Balance*); a path the registry does not list is not the team's repo."""
        if not bal or not members:
            return None
        roots = sorted({m.repo for m in members if m.repo and m.repo in self._repos})
        waiting = [str(w["oldest"]) for m in members if m.seat and (w := m.prs_waiting(home=self.host))]
        bounds = [d for m in members if (d := balance_mod.span((m.review or {}).get("bound")))]
        got = balance_mod.crossed(
            bal, roots, self._repos, min(waiting) if waiting else None,
            min(bounds) if bounds else balance_mod.REVIEW_BOUND, now,
        )  # fmt: skip
        if got is None:
            return ...
        crossed, repo = got
        if not crossed:
            return None
        since = old["since"] if isinstance(old, dict) and old.get("since") else now_iso()
        return {"since": since, "repo": repo, "crossed": crossed}

    @staticmethod
    def _ledger_mtimes(roots: list[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for root in roots:
            try:
                out[root] = (Path(root) / ledger_mod.ledger_path(root)).stat().st_mtime
            except OSError:
                out[root] = None
        return out

    def _live_commits(self) -> dict[str, str]:
        """Each repo's live commit as the promote reading holds it (§6 *Promote*), by the repo's
        name, for a live check's `live` (§4.9b, TD-323): a reading that carries `live_why` — the live
        commit could not be read — names none, and unknown is never live."""
        return {name: str(r["live"]) for name, r in self._promotes.items() if r.get("live") and not r.get("live_why")}

    @staticmethod
    def _read_repos(
        roots: list[str],
        prev: dict[str, dict[str, Any]],
        full: set[str],
        registry: list[str] | None = None,
        live: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Each checkout's reading, in a thread: `{name, root, remote, ledger, prs, at}`. A root in
        `full` has its PRs and the ledger's history read; the others their ledger's entries alone,
        the rest carried from `prev`. One checkout's read that raises keeps its last reading with
        the error beside it and never costs the others theirs. `live` is `_live_commits`: a live
        check's build is read against its repo's (§4.9b). `None` — no promote reading since the
        host agent started — is unknown, not *not live*: each live check keeps the `live` its last
        reading gave it (§6 rule 6, TD-516), so a restart never takes one out of a lane and brings
        it back as new. A promote pass that keeps failing keeps it unknown, and nothing goes live
        while it does."""
        now = datetime.now(UTC)
        stamp = now.isoformat()
        by_remote: dict[str, dict[str, Any]] = {}
        out: dict[str, dict[str, Any]] = {}
        # `<repo>#TD-NNN` blockers resolve among every checkout the registry lists, not only the ones
        # re-read this pass (the techlead's read of #793); each repo read once per pass (TD-228)
        resolve = ledger_mod.Registry(registry if registry is not None else roots)
        for root in roots:
            old = prev.get(root) or {}
            try:
                reader = ledger_mod.LiveReader(root, (live or {}).get(Path(root).name))
                out[root] = TickMixin._read_repo(root, old, root in full, now, by_remote, resolve, reader)
                if live is None:  # no promote reading yet since the start: unknown is not *not live* (TD-516)
                    _carry_live(out[root], old)
            except Exception as e:  # noqa: BLE001 — one checkout's surprise is its reading's error, not the batch's
                log.exception("reading the repo facts of %s failed", root)
                why = f"the read failed: {type(e).__name__}"
                out[root] = {
                    "name": Path(root).name,
                    "root": root,
                    "remote": str(old.get("remote") or ""),
                    "ledger": {**(old.get("ledger") or {}), "error": why, "failed_at": stamp},
                    "prs": {**(old.get("prs") or {}), "error": why, "failed_at": stamp},
                    "at": str(old.get("at") or stamp),
                }
        return out

    @staticmethod
    def _read_repo(
        root: str,
        old: dict[str, Any],
        due: bool,
        now: datetime,
        by_remote: dict[str, dict[str, Any]],
        resolve: ledger_mod.Resolve | None = None,
        live: ledger_mod.Live | None = None,
    ) -> dict[str, Any]:
        """One checkout's reading (`_read_repos`): `by_remote` holds the PR readings taken in this
        pass, so two checkouts of one remote are one `gh` read."""
        stamp = now.isoformat()
        remote = reports._git(root, "remote", "get-url", "origin") if due else None
        remote = (remote or "").strip() if due else str(old.get("remote") or "")
        led = ledger_mod.reading(root, now, with_history=due, resolve=resolve, live=live)
        old_led = old.get("ledger") or {}
        if "error" in led:
            led = {**old_led, "error": led["error"], "failed_at": stamp}
        elif not due:
            led.update({k: old_led[k] for k in ("windows", "recent", "history_error") if k in old_led})
        elif "history_error" in led:
            led.update({k: old_led[k] for k in ("windows", "recent") if k in old_led})
        prs = old.get("prs")
        if due:
            if remote and remote in by_remote:
                prs = by_remote[remote]
            else:
                fresh = reports.pr_reading(root, now)
                prs = {**(prs or {}), "error": fresh["error"], "failed_at": stamp} if "error" in fresh else fresh
                if remote:
                    by_remote[remote] = prs
        # the board's open decided lines, the lanes' work orders (§4.4 *Board write-back*, TD-384): read
        # with the PRs, every `REPOS_EVERY`; a failed read keeps the last orders with the error beside them
        wos = old.get("work_orders")
        if due:
            got = workorders.read(root)
            if "error" in got:
                wos = {**(wos or {"orders": []}), "error": got["error"], "failed_at": stamp}
            else:
                wos = {"orders": [workorders.entry(o) for o in got["orders"]], "at": stamp}
        return {
            "name": Path(root).name,
            "root": root,
            "remote": remote,
            "ledger": led,
            "prs": prs,
            "work_orders": wos or {"orders": []},
            "at": stamp if due else str(old.get("at") or stamp),
        }

    async def _count_seats(self, records: list[Session]) -> None:
        """`seat_count` for every supervised `prs:` seat (§6 rule 3): the PRs merged to the seat's repo
        since its record was created, one `gh` read per repo, run at the home in the seat's checkout
        — a node's is at the same absolute path (§4.4a); a path that is not a directory here gives no
        reading. A read that failed leaves the last reading: an outage is never zero merges."""
        try:
            seats = [
                r
                for r in records
                if (r.seat or {}).get("trigger") == "prs" and r.supervised and r.unattended and not r.superseded_by
            ]
            where = {r.id: r.repo or r.dir for r in seats}
            dirs = sorted(set(where.values()))
            found = await asyncio.gather(*(asyncio.to_thread(reports.merged_prs, d) for d in dirs))
            merged = dict(zip(dirs, found, strict=True))
            for r in seats:
                got = merged.get(where[r.id])
                if got is None or r.superseded_by:
                    continue  # no reading; or a fill or restart replaced it while `gh` was out
                since = _parse(r.created)
                count = {"prs": sum(1 for at in got if at > since), "at": now_iso()}
                if (r.seat_count or {}).get("prs") != count["prs"]:
                    r.seat_count = count
                    self._save(r)
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next pass tries again
            log.exception("counting the seats' PRs failed")
        finally:
            await self._push_changes()

    @staticmethod
    def _on_a_dialog(s: Session) -> bool:
        """Typing at a session on a permission or a question would answer the dialog rather than
        reach the composer (§4.2) — the rule `send` refuses by."""
        return bool(s.pending and s.pending.kind in ("permission", "question"))

    async def _backup(self, day: str) -> None:
        """The nightly tarball (§4.4a "When the home is lost"): off the loop, and never an error
        anyone but the log hears of."""
        try:
            made = await asyncio.to_thread(backup_store, day)
            if made:
                log.info("backed up the store to %s", made)
        except Exception:  # noqa: BLE001 — a detached task: log, and try again tomorrow
            log.exception("the nightly backup of the store failed")

    def _prune_runs(self, now: datetime, live: set[str], live_ids: set[str] | None = None) -> None:
        """Run-log retention (design §4.6): a log older than `runs_keep_days` goes unless it is in
        `live`, the logs of sessions still running (never truncate a live log: invariant 3). Logs
        of forgotten sessions are the common case — Forget keeps the file until this sweep. `0`
        keeps everything. A session's attachments (`attachments/<session>/`, §4.4 *An attachment's
        life*, TD-469) go by the same bound unless its id is in `live_ids` — any record of it neither
        exited nor closed; a forgotten one has none — each folder removed once empty; `None` sweeps no
        attachment. An attachment's `.part` nothing has written to for an hour goes whatever
        the bound (§4.4 *Attachment drop*, TD-478): a browser closed mid-upload leaves nothing.
        Runs in a thread: touches files, never `self.sessions`."""
        idle = now.timestamp() - paths.ATTACH_PART_IDLE_S
        for f in paths.attachments_dir().glob("*/*.part"):
            try:
                # an upload's `.part` alone (`<name>.<16 hex>.part`): a finished file a person named
                # `notes.part` is an attachment, kept as any other is
                if paths.UPLOAD_PART.fullmatch(f.name) and f.stat().st_mtime < idle:
                    f.unlink()
                    log.info("pruned attachment upload %s (nothing written for an hour)", f)
            except OSError:
                continue
        keep = hosts.local_host().runs_keep_days
        if keep <= 0:
            return
        cutoff = (now - timedelta(days=keep)).timestamp()
        for f in paths.runs_dir().glob("*.log"):
            try:
                if str(f) not in live and f.stat().st_mtime < cutoff:
                    f.unlink()
                    log.info("pruned run log %s (older than %d days)", f.name, keep)
            except OSError:
                continue
        # a promote's log goes with the run logs (§6 *Promote*), unless its run is still in flight
        inflight = {str(r["inflight"].get("log")) for r in self._promotes.values() if r.get("inflight")}
        for f in (paths.home() / "promotes").glob("*/*.log"):
            try:
                if str(f) not in inflight and f.stat().st_mtime < cutoff:
                    f.unlink()
                    log.info("pruned promote log %s (older than %d days)", f, keep)
            except OSError:
                continue
        if live_ids is None:
            return
        for folder in paths.attachments_dir().glob("*"):
            if folder.name in live_ids or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                files = [f for f in folder.iterdir() if f.is_file() and not f.is_symlink()]
            except OSError:  # gone meanwhile
                continue
            for f in files:
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        log.info("pruned attachment %s (older than %d days)", f, keep)
                except OSError as e:
                    log.warning("could not prune attachment %s: %s", f, e)
            with contextlib.suppress(OSError):  # not empty: a younger file keeps it
                folder.rmdir()

    async def _refresh_git(self, now: datetime) -> None:
        due = [
            s
            for s in self.sessions.values()
            if s.dir
            and s.state not in ("closed",)
            and now - self._git_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) > GIT_EVERY
        ]
        if not due:
            return
        results = await asyncio.gather(*(asyncio.to_thread(git_info, s.dir) for s in due), return_exceptions=True)
        infos = {s.id: (r if not isinstance(r, BaseException) else None) for s, r in zip(due, results, strict=True)}
        # the recent files (§4.2 *The record's `files`*, TD-538): read again only when the status's
        # `oid` or its porcelain moved since the last read, or none was read for this record yet
        keys = {sid: (i.oid, i.dirty, tuple(i.files)) for sid, i in infos.items() if i is not None}
        stale = [s for s in due if s.id in keys and keys[s.id] != self._files_read.get(s.id)]
        read = await asyncio.gather(*(asyncio.to_thread(_recent_files, s.dir) for s in stale), return_exceptions=True)
        files = {s.id: r for s, r in zip(stale, read, strict=True) if isinstance(r, list)}
        for s in due:
            live = self.sessions.get(s.id)
            if live is None:
                continue  # forgotten while git ran: no side-table key outlives it (TD-463)
            self._git_checked[s.id] = now
            info = infos.get(s.id)
            new = info.to_dict() if info else None
            changed = new != live.git
            live.git = new
            if s.id in files:  # a read that failed keeps the list it had, and is taken again next time
                self._files_read[s.id] = keys[s.id]
                changed = changed or files[s.id] != live.files
                live.files = files[s.id]
            if changed:
                self.store.save(live)

    async def _derive_reports(self, now: datetime) -> None:
        """A detached task: log a failure, never let it vanish silently, and announce what changed
        (the tick's own push has been and gone by the time this finishes)."""
        try:
            await self._derive_reports_inner(now)
        except Exception:  # noqa: BLE001
            log.exception("deriving reports failed")
        finally:
            await self._push_changes()

    async def _derive_reports_inner(self, now: datetime) -> None:
        """Fill in the report channels the session did not declare (design §4.8, §9 invariant 10):
        its branch, the PRs from it, and the ledger rows those PRs put on main. Every entry is
        marked `derived`, so a declaration stands whatever this finds — the record refuses the
        write, which is why a refusal is not an error. Runs off the branch `_refresh_git` already
        read, on its own slow cadence, and never blocks the tick on a failure.

        What is checked out in a directory is derived only for the record that *holds* that
        directory now (TD-034, `reports.holds_directory`) — a worktree is reused run after run, and
        an exited predecessor sharing the `dir` would otherwise be credited with its successor's
        branch and PRs. Every record keeps the `pending`-by-PR re-check, which is attributed by a PR
        the record itself claimed, so a merge still lands on a worker that has since exited
        (TD-032).

        A branch-only claim the session has moved off is looked up one last time by the branch it
        came from and then *retired* if that branch never grew a PR (TD-045): nothing else could
        ever remove one — the re-check above works by PR number, the upsert has no delete branch,
        and invariant 10 only replaces a derived entry when the session declares the same reference
        — and a permanent false `claimed` is exactly what the idle-with-open-work send fires on."""
        if self.mode == "node" and not self.home_reachable():
            # `progress` and `findings` are the home's (§4.4a, step 4b.2): a claim written to the
            # replica is overwritten on reconnect and was never checked against the siblings' leases.
            return
        holders = reports.holds_directory(self.sessions.values())
        due: list[tuple[Session, str | None, list[tuple[str, int]], list[tuple[str, str | None]]]] = []
        for s in self.sessions.values():
            if not s.dir or s.state == "closed":
                continue
            if now - self._derived_at.get(s.id, datetime.min.replace(tzinfo=UTC)) <= DERIVE_EVERY:
                continue
            branch = (s.git or {}).get("branch") if s.id in holders else None
            pending = [e for e in s.progress if e.source != "declared" and e.status == "claimed" and e.pr]
            # …and a declared claim's `review_pr` (TD-150), re-checked by number, so a merge or a close
            # clears it after the session has moved to its next branch
            reviews = [
                (e.ref, e.review_pr)
                for e in s.progress
                if e.source == "declared" and e.status == "claimed" and e.review_pr and not e.pr
            ]
            # Branch-only claims from a branch this record is no longer on (TD-045). Only for a
            # record that still holds its directory: what is checked out elsewhere says nothing
            # about this one, and an exited worker's claims are its history, not a live question.
            left = (
                [(e.ref, e.branch) for e in s.progress if _is_branch_claim(e) and e.branch != branch]
                if s.id in holders
                else []
            )
            if not branch and not pending and not left and not reviews:
                continue
            due.append((s, branch, [(e.ref, e.pr) for e in pending if e.pr], left, reviews))
        if not due:
            return
        results = await asyncio.gather(
            *(
                # the ledger path the client read from the repo's config at create (design §5
                # `ledger:`), else the default — this package never reads `.agentorc.yml` itself
                asyncio.to_thread(
                    reports.derive, s.dir, branch, pend, s.ledger or reports.LEDGER_DEFAULT, left, reviews
                )
                for s, branch, pend, left, reviews in due
            ),
            return_exceptions=True,
        )
        for (s, _branch, _pending, _left, _reviews), result in zip(due, results, strict=True):
            self._derived_at[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            if isinstance(result, BaseException):
                log.warning("deriving reports for %s failed: %s", s.id, result)
                continue
            progress, findings, retire = result
            if self.mode == "node":
                # sent to the home as the derived reports they are; the home's push brings them back
                await self._send_derived(live, progress, findings, retire)
                continue
            # Lists, not generators: these upserts are the write, and `any()` over a generator
            # would stop at the first change and silently drop every later entry (review 2026-09-11).
            applied = [live.report_progress(e) for e in progress] + [live.report_finding(e) for e in findings]
            # a refused derived entry still leaves its PR beside a declared claim (TD-150)
            applied += [live.note_review(e) for e in progress]
            changed = any(applied) | live.retire_branch_claims(retire)
            if changed:
                self.store.save(live)

    async def _refresh_model(self, now: datetime) -> None:
        """The model each live agent session is running (TD-031, design §4.2a). The hook reports it
        at SessionStart and on a `/model` switch; this is the cross-check and the fallback for a
        session that started before the hook carried one — a read of the transcript's tail, in a
        thread, on its own cadence. An adapter that cannot tell leaves the field alone."""
        due = []
        for s in self.sessions.values():
            if not (s.adapter_id and s.dir) or s.state == "closed":
                continue
            if now - self._model_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) <= MODEL_EVERY:
                continue
            try:
                fn = getattr(adapters.get(s.adapter), "model_in_use", None)
            except KeyError:
                fn = None
            if fn:
                due.append((s, fn))
        if not due:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(fn, str(s.adapter_id), Path(s.dir), s.profile) for s, fn in due),
            return_exceptions=True,
        )
        for (s, _), result in zip(due, results, strict=True):
            self._model_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None or isinstance(result, BaseException) or not result:
                continue
            if live.model != result:
                live.model = str(result)
                self.store.save(live)

    async def _refresh_context(self, now: datetime) -> None:
        """Each unattended record's context reading (design §4.3 `context`, §6 rule 5, TD-190): the
        adapter's read of the transcript's tail, in a thread, once per CONTEXT_EVERY. An attended
        session is a person's, and they can see their own context; an adapter that cannot tell
        leaves the field as it was."""
        every = agent_common.CONTEXT_EVERY
        due = []
        for s in self.sessions.values():
            if not (s.unattended and s.adapter_id and s.dir) or s.state == "closed":
                continue
            if now - self._context_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) <= every:
                continue
            try:
                fn = getattr(adapters.get(s.adapter), "context", None)
            except KeyError:
                fn = None
            if fn:
                due.append((s, fn))
        if not due:
            return
        results = await asyncio.gather(
            *(asyncio.to_thread(fn, str(s.adapter_id), Path(s.dir), s.profile) for s, fn in due),
            return_exceptions=True,
        )
        for (s, _), result in zip(due, results, strict=True):
            self._context_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None or not isinstance(result, dict) or not result.get("tokens"):
                continue
            reading = {"tokens": int(result["tokens"]), "at": result.get("at"), "window": result.get("window")}
            if live.context != reading:
                live.context = reading
                self.store.save(live)

    async def _refresh_usage(self) -> None:
        """Ask each account a live agent session's profile names for its usage, once per account
        (§4.2a, TD-122), in a thread, and only on demand (§4.4 *Usage*, TD-233 slice 3): when its
        reading is older than `USAGE_FRESH`, at most once per `USAGE_FRESH`, and not for
        `USAGE_COOL` after a 429. A fetch failure keeps the last answer and never gates anything
        (design §6).
        Then the `limited` rule: an interactive session on a profile at 100% of a window shows
        `limited` with the reset time, and goes back to what it was once the window resets."""
        try:
            await self._refresh_usage_inner()
        except Exception:  # noqa: BLE001 — a detached task: log, never let it vanish silently
            log.exception("usage refresh failed")
        finally:
            await self._push_changes()

    def _usage_live(self) -> list[Session]:
        """The records whose profile has a reading: this host's live tool sessions. The quota poll and
        the spend pass read the same list, or one would drop what the other writes."""
        return [
            s
            for s in self.sessions.values()
            if s.kind == "interactive" and s.adapter != "shell" and s.state not in ("exited", "closed")
        ]

    def _usage_remote_live(self) -> list[Session]:
        """The nodes' live tool sessions, as this home holds them: shown on the chip by what they
        report (§4.4 *A node's sessions report to their node*, TD-233 slice 2), never asked for
        here, since the credentials are the node's, and never marked `limited` here, a node-owned
        field."""
        return [
            s
            for recs in self.remote.values()
            for s in recs.values()
            if s.kind == "interactive" and s.adapter != "shell" and s.state not in ("exited", "closed")
        ]

    def _usage_theirs(self, here: set[str]) -> list[tuple[Session, str]]:
        """The nodes' live sessions this home shows, each with the account key its node reported it
        under, for the poll and the spread alike. A reading is per profile and the chip per account,
        so one profile carries one account's: a profile a session here runs under (`here`) is this
        host's own, and one two nodes key to two accounts goes to the first by host, never to both,
        or the reading would flip between them on every pass."""
        claimed: dict[str, str] = {}
        out: list[tuple[Session, str]] = []
        for s in sorted(self._usage_remote_live(), key=lambda r: (r.host, r.id)):
            key = self._usage_remote_keys.get((s.host, s.profile))
            if s.profile in here or key is None or claimed.setdefault(s.profile, key) != key:
                continue
            out.append((s, key))
        return out

    async def _refresh_usage_inner(self) -> None:
        live = self._usage_live()
        # A metered profile is never polled (§4.2a): its reading is the spend pass's sum, and the cap
        # rule below skips it by its billing, read before the windows (TD-151)
        metered = {p for _, p in await asyncio.to_thread(_metered_of, {(s.adapter, s.profile) for s in live})}
        mono = time.monotonic()
        # One poll per account, never per profile (§4.2a, TD-122): four profiles split by role on
        # one login asked four times, and the endpoint answered `rate_limited` to all of them.
        groups: dict[str, list[str]] = {}  # account key → the live profiles sharing it
        meta: dict[str, dict[str, str]] = {}  # account key → what the chip names it by
        ask: dict[str, tuple[Any, str]] = {}  # account key → (usage_for, the profile asked through)
        for s in live:
            ad = adapters.get(s.adapter)
            fn = getattr(ad, "usage_for", None)
            if not fn or s.profile in metered:
                continue
            key, account = _usage_key(ad, s.adapter, s.profile)
            profs = groups.setdefault(key, [])
            if s.profile not in profs:
                profs.append(s.profile)
            meta.setdefault(key, {"account": account, "tool": str(getattr(ad, "label", "") or s.adapter)})
            ask.setdefault(key, (fn, s.profile))
        # a node's session is kept and shown while its node reports it, and never asked for here;
        # the key of a node's profile no live session of that node runs under any more is forgotten
        remote_live = {(s.host, s.profile) for s in self._usage_remote_live()}
        for pair in [p for p in self._usage_remote_keys if p not in remote_live]:
            del self._usage_remote_keys[pair]
        for s, key in self._usage_theirs({s.profile for s in live}):
            ad = adapters.get(s.adapter)
            account = key.split(":", 1)[1]
            profs = groups.setdefault(key, [])
            if s.profile not in profs:
                profs.append(s.profile)
            meta.setdefault(key, {"account": account, "tool": str(getattr(ad, "label", "") or s.adapter)})
        for key, profs in groups.items():
            self._usage_seed(key, profs)
        due: dict[str, tuple[Any, str]] = {}
        whole: dict[str, Any] | None = None  # the settings, read once and only for a young reading
        for key, profs in groups.items():
            if key not in ask:
                continue
            wait = self._usage_wait.get(key, agent_common.USAGE_FRESH)
            if mono - self._usage_checked.get(key, -wait) < wait:
                continue
            if not self._usage_young(key):
                due[key] = ask[key]
            elif mono - self._usage_only_at.get(key, float("-inf")) >= agent_common.USAGE_ONLY_EVERY:
                # once an hour for a window only the endpoint gives, by this clock and not the
                # window's `at`, which never moves if the endpoint stops naming it
                whole = settings_mod.load() if whole is None else whole
                if self._usage_only_due(key, profs, whole):
                    self._usage_only_at[key] = mono
                    due[key] = ask[key]
        changed = False
        if due:
            results = await asyncio.gather(
                *(asyncio.to_thread(fn, prof) for fn, prof in due.values()), return_exceptions=True
            )
            for key, r in zip(due, results, strict=True):
                self._usage_checked[key] = mono
                if (merged := self._usage_reading(key, r)) is not None:
                    self._usage_acct[key] = merged
        # every profile sharing an account carries its reading — windows, `fetched`, `reason` alike
        for key, profs in groups.items():
            if (acct := self._usage_acct.get(key)) is None:
                continue
            reading = {**acct, **meta[key]}
            for prof in profs:
                if self._usage.get(prof) != reading:
                    self._usage[prof] = reading
                    changed = True
                    await self._broadcast({"event": "usage", "profile": prof, "usage": reading})
        if changed:
            self.usage_store.save(self._usage)  # once for the batch: the file is whole either way
        # Only profiles a live session is running under are shown (TD-073, Paul 2026-09-19): one
        # account in use is one chip, and last night's profile does not sit in the top bar all day.
        # a node's account is kept while its session lives, shown or not (a profile a session here
        # runs under, or one a second node keys to another account), since the home sends it back
        keyed = {k for (h, p), k in self._usage_remote_keys.items()}
        for key in [k for k in self._usage_acct if k not in groups and k not in keyed]:
            self._usage_acct.pop(key, None)
            self._usage_checked.pop(key, None)
            self._usage_wait.pop(key, None)
            self._usage_only_at.pop(key, None)
        # …and a node's profile, restored from `usage.json` at the start and not keyed since, keeps
        # what it held while a live session of a node runs under it: its first report is merged onto
        # it (`_usage_seed`). Only until then — a profile keyed or dropped once is shown by the rule.
        self._usage_restored -= {p for _, p in self._usage_remote_keys}
        waiting = {s.profile for s in self._usage_remote_live()} & self._usage_restored
        shown = {p for profs in groups.values() for p in profs} | metered | waiting
        if dropped := [p for p in self._usage if p not in shown]:
            for prof in dropped:
                self._usage.pop(prof, None)
                self._usage_restored.discard(prof)
                await self._broadcast({"event": "usage", "profile": prof, "usage": None})
            self.usage_store.save(self._usage)
        self._usage_limits(live, metered)
        await self._push_usage_readings()

    def _usage_limits(self, live: list[Session], metered: set[str]) -> None:
        """The `limited` rule over the readings `_usage` holds (§4.2), for the poll and for a report
        between polls alike (TD-233 slice 2): a session's cap is marked when it is read, not on the
        next poll."""
        for s in live:
            cap = None if s.profile in metered else _cap(self._usage.get(s.profile))
            if cap and s.state not in ("limited", "needs-you"):
                # the tool's own endpoint, not the screen: reported, so `hook` (design §9 invariant 4)
                self._pre_limited[s.id] = s.state
                s.set_state("limited", confidence="hook", pending=Pending(kind="limit", text=cap))
                self.store.save(s)
            elif not cap and s.state == "limited" and s.pending and s.pending.kind == "limit":
                # back to what it was (idle stays idle: no hook will come to correct a wrong `working`)
                s.set_state(self._pre_limited.pop(s.id, "working"), confidence="hook")
                self.store.save(s)

    def _usage_only_due(self, key: str, profs: list[str], whole: dict[str, Any]) -> bool:
        """Whether a young reading still wants the endpoint, for a window only it gives (§4.4
        *Usage*, TD-233 slice 2): once per `USAGE_ONLY_EVERY` while the account reports and a
        reserve of a profile on it names the window, or the window stood within ten points of
        its cap."""
        watched = {str(label) for p in profs for label in self._gate_reserves(whole, p) or {}}
        return usage_mod.asked_only_due(
            self._usage_acct.get(key), datetime.now(UTC), agent_common.USAGE_ONLY_EVERY, watched
        )

    async def _usage_spread(self, key: str) -> None:
        """The account's reading copied under every live profile sharing it, pushed and written
        when it moved (the refresh's own step, for a reading a report changed between polls)."""
        acct = self._usage_acct.get(key)
        if acct is None:
            return
        changed = False
        mine = [
            (s, _usage_key(ad, s.adapter, s.profile))
            for s in self._usage_live()
            if getattr(ad := adapters.get(s.adapter), "usage_for", None)
        ]
        theirs = [(s, (k, k.split(":", 1)[1])) for s, k in self._usage_theirs({s.profile for s in self._usage_live()})]
        for s, (k, account) in [*mine, *theirs]:
            if k != key:
                continue
            ad = adapters.get(s.adapter)
            reading = {**acct, "account": account, "tool": str(getattr(ad, "label", "") or s.adapter)}
            if self._usage.get(s.profile) != reading:
                self._usage[s.profile] = reading
                changed = True
                await self._broadcast({"event": "usage", "profile": s.profile, "usage": reading})
        if changed:
            self.usage_store.save(self._usage)

    def _usage_young(self, key: str) -> bool:
        """Whether the account's reading is younger than `USAGE_FRESH` by its own time (§4.4
        *Usage*, TD-233 slice 3): an account whose reading is fresh is not asked, whatever put
        it there. A reading with no readable time is not young, nor is one stamped ahead of the
        clock: a clock that stepped back must not hold the poll off for as long as it is behind
        (`_usage_checked`, seeded at now for such a reading, still waits one period)."""
        try:
            age = (datetime.now(UTC) - _parse(str((self._usage_acct.get(key) or {}).get("fetched")))).total_seconds()
        except (ValueError, TypeError):
            return False
        return 0 <= age < agent_common.USAGE_FRESH

    def _usage_seed(self, key: str, profs: list[str], strict: bool = False) -> None:
        """An account the poll has not met since the agent started takes its reading, and its
        poll's allowance, from the newest reading its profiles hold (TD-087, TD-122): a restart
        keeps the chip and does not ask sooner than `fetched + USAGE_FRESH`. A reading with no
        readable time is polled at once. `strict`, for a node's account: a held reading that
        names no account is not taken, since the profile's name here may be another login's."""
        # only a reading of this account: a node's profile can change hands between two accounts
        account = key.split(":", 1)[-1]
        held = [
            r
            for p in profs
            if isinstance(r := self._usage.get(p), dict)
            and str(r.get("account") or ("" if strict else account)) == account
        ]
        if key not in self._usage_acct and held:
            newest = max(held, key=lambda r: str(r.get("fetched") or ""))
            self._usage_acct[key] = {k: v for k, v in newest.items() if k not in ("account", "tool")}
        first = key not in self._usage_checked
        if first and (seen := [m for r in held if (m := _usage_checked_at(r)) is not None]):
            self._usage_checked[key] = max(seen)
        # …and so does a cool-off (TD-233 slice 3): a promote inside the hour after a 429 does not
        # ask at once because the held reading's `fetched` is old
        if first and key not in self._usage_wait:
            left = [s for r in held if (s := _cool_left(r)) is not None]
            if left and max(left) > 0:
                self._usage_checked[key] = time.monotonic()
                self._usage_wait[key] = max(left)

    def _usage_reading(self, key: str, r: Any) -> dict[str, Any] | None:
        """One poll's answer folded into what this account already had (TD-087, TD-122: `key` is
        the account's, so a backoff holds every profile on it), or None when nothing changed and
        nothing need be said.

        An adapter now answers with a **reason** rather than a silence: `ok` with the windows,
        or `rate_limited` / `no_credentials` / `no_profile` / `error` with none. A reading is
        replaced only by a newer reading — a failure **keeps the last one**, with the reason
        beside it, because *the chip went out* and *the allowance is spent* are different things
        to a person and a five-hour window does not change while we are refused. The backoff is
        set here too: a 429 waits `USAGE_COOL`, or the endpoint's own `Retry-After` when that is
        longer (TD-233 slice 3; the doubling it replaced could not learn a window the endpoint
        never names, TD-231); any other answer, good or bad, goes back to the ordinary cadence,
        since only a 429 is the endpoint telling us to ask less often."""
        if isinstance(r, BaseException) or not isinstance(r, dict):
            r = {"reason": "error"}
        reason = str(r.get("reason") or ("ok" if r.get("windows") is not None else "error"))
        if reason == "rate_limited":
            after = r.get("retry_after")
            # a fixed hour, and the endpoint's own word only where it is longer: it answered with
            # `Retry-After` 0 or none while refusing for six hours (TD-231), so a shorter word is
            # not believed, and a longer one is kept, never capped
            cool = agent_common.USAGE_COOL
            self._usage_wait[key] = max(float(after), cool) if isinstance(after, int | float) else cool
            until = datetime.now(UTC) + timedelta(seconds=self._usage_wait[key])
        else:
            self._usage_wait.pop(key, None)
        was = self._usage_acct.get(key) or {}
        if was.get("reason") != reason:
            # once per change of reason, never per poll: a 429 every five minutes is one line
            log.info("usage for account %s: %s (was %s)", key, reason, was.get("reason") or "no reading yet")
        if reason == "ok":
            # merged with what the account's sessions reported, by the same rule (TD-233 slice 2)
            windows = usage_mod.clean_windows(r.get("windows"))
            fetched = r.get("fetched")
            at = fetched if isinstance(fetched, str) and fetched else now_iso()
            out = usage_mod.merge(was, windows, at=at, source="asked", fresh=True)
            out = {k: v for k, v in out.items() if k not in ("retry_after", "cool_until")} | {"reason": "ok"}
        else:
            out = {**{k: v for k, v in was.items() if k in ("windows", "fetched", "source", "by")}, "reason": reason}
            if isinstance(r.get("retry_after"), int | float):
                out["retry_after"] = r["retry_after"]
            if reason == "rate_limited":
                # when the cool-off ends, held in `usage.json` so a restart keeps it (`_usage_seed`)
                out["cool_until"] = until.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return out if out != was else None

    def _reconcile(self, panes: dict[str, PaneInfo], tails: dict[str, list[str]], snapshot_at: datetime) -> None:
        for sid, event in self.events.drain():
            self._apply_event(sid, event, queued=True)
        now = datetime.now(UTC)
        mono = time.monotonic()
        self._removed = {k: v for k, v in self._removed.items() if mono - v[1] < REMOVED_GUARD_SECONDS}
        for sid, s in list(self.sessions.items()):
            if s.state == "closed":
                if s.closed_at and _parse(s.closed_at) + agent_common.CLOSED_KEEP < now:
                    self._forget(sid)
                continue
            if s.state == "scheduled":
                continue  # no pane yet, by design (§6 *Start time*): the tick's start pass is its judge
            killed = self._killed_at.get(sid)
            if killed is not None and killed < snapshot_at:
                del self._killed_at[sid]  # this snapshot is newer than the kill: the guard is spent
                killed = None
            pane = panes.get(sid)
            if pane is not None and killed is not None:
                # The pane list was taken before the kill that ended this record, so it still lists
                # a tmux session that is gone. Observing it would set `pane` back to True and read a
                # state off the last screen — an `exited` record flipped back to `idle`, which then
                # refuses its own `remove` ("kill it first"). The window is the two `to_thread` hops
                # between the snapshot and here, and an RPC runs on the loop inside them (TD-063,
                # seen on a 3.12 CI runner 2026-09-17).
                continue
            if pane is None:
                # A session created after the pane snapshot was taken is not judged by it.
                if _parse(s.created) + agent_common.CREATE_GRACE < snapshot_at and (s.state != "exited" or s.pane):
                    if s.state != "exited" or s.ended is None:
                        s.ended = self._gone_ending(now)
                    s.set_state("exited", confidence="tick")
                    s.pane = False  # gone for good: killed, or the tmux server restarted (TD-023)
                    self.store.save(s)
                continue
            self._observe(s, pane, tails.get(sid, []), now)
        # tmux sessions with our prefix that we have no record of (created by hand, or the
        # store was lost): adopt them minimally as shells so they appear in the Org.
        for name, pane in panes.items():
            if name not in self.sessions and not self._is_removed_pane(name, pane):
                s = Session(
                    id=name,
                    name=name[len(naming.PREFIX) :],
                    kind="interactive",
                    adapter="shell",
                    dir="",
                    host=self.host,
                )
                s.created = datetime.fromtimestamp(pane.created, UTC).isoformat().replace("+00:00", "Z")
                self.sessions[name] = s
                self._observe(s, pane, tails.get(name, []), now)

    def _gone_ending(self, now: datetime) -> dict[str, Any]:
        """The record's `ended` for a tmux session the tick found gone (§4.2, §4.5 row 5 (b), TD-490):
        when it was found, and — on the agent's first tick after a start, the previous run's last
        tick in `host.json` older than the create grace — since when nobody was looking."""
        found = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ended: dict[str, Any] = {"how": "gone", "at": found, "found": found}
        prev = self._prev_last_tick
        if not self._ticked and isinstance(prev, str):
            with contextlib.suppress(ValueError):
                if now - _parse(prev) > agent_common.CREATE_GRACE:
                    ended["down_since"] = prev
        return ended

    def _note_tick(self, at: datetime) -> None:
        """`last_tick` in `host.json`, written each tick: the next run's measure of how long nobody
        was looking (§4.5 row 5 (b), TD-490). A failed write is a weaker ending, never a failed tick."""
        self._ticked = True
        self._host_rec["last_tick"] = at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        try:
            self.host_store.save(self._host_rec)
        except OSError:
            log.warning("could not write last_tick to %s", self.host_store.path)

    def _is_removed_pane(self, name: str, pane: PaneInfo) -> bool:
        """Is this the pane a recent `remove` killed (as a stale snapshot would still list it)? A
        pane born after the removed one is a new session and adopts normally (TD-021)."""
        rem = self._removed.get(name)
        if rem is None:
            return False
        created, _ = rem
        # `created` is tmux's `session_created`, whole seconds. Equal means "could be the same
        # pane", so it is skipped: a pane that was created, exited, observed, removed *and* had
        # its name reused inside one second is the only case this delays (until the guard
        # expires), and `None` (the pane was already gone at remove) is treated the same way.
        return created is None or pane.created <= created

    def _observe(self, s: Session, pane: PaneInfo, tail: list[str], now: datetime) -> None:
        adapter = adapters.get(s.adapter)
        s.tail = [_clean(t) for t in tail]
        s.title = _pane_title(adapter, pane.title)
        s.pane = True
        if s.run_log:
            with contextlib.suppress(OSError):
                mtime = datetime.fromtimestamp(Path(s.run_log).stat().st_mtime, UTC)
                s.last_output = mtime.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if pane.dead:
            s.exit_code = pane.dead_status
            if s.state != "exited" or s.ended is None:
                # the tool's own end, when its hook landed first, keeps its words (§4.2, TD-490)
                s.ended = {"how": "pane", "at": now_iso(), "code": pane.dead_status}
            elif s.ended.get("how") == "pane":
                s.ended["code"] = pane.dead_status  # tmux's status lags its dead flag, as `exit_code` does
            if s.state != "exited":
                s.set_state("exited", confidence="tick")
        elif adapter.state_source == "scraped":
            st = adapter.classify(pane, tail)
            if st and st != s.state:
                s.set_state(st, confidence="scraped")
        elif (m := self._screen_verdict(s, adapter, now)) is not None:
            # Hook-fed adapter, screen rule fired, no fresher hook state: the labelled fallback
            if m.state != s.state or (m.pending and m.pending != s.pending):
                if not self._screen_held(s):  # what the verdict replaces, unless it is a verdict itself
                    self._hook_state[s.id] = (s.state, s.pending, s.confidence)
                s.set_state(m.state, confidence="scraped", pending=m.pending)
        elif self._screen_gone(s, adapter, now):
            # The screen that set this state is gone and no hook has spoken since (TD-306): back to
            # what the last hook said, or `idle` when no hook has reported — not the scraped verdict
            # until the tool's next hook, which on a quiet session may be the next turn
            saved = self._hook_state.pop(s.id, None)
            if saved is None or (saved[2] == "hook" and s.id not in self._last_hook):
                s.set_state("idle", confidence="scraped")  # the launch's assumption is no hook's word
            else:
                s.set_state(saved[0], confidence=saved[2], pending=saved[1])
        elif s.state == "working" and s.last_output and now - _parse(s.last_output) > STALL_AFTER:
            # Hook-fed adapters: the liveness cross-check applies to `working` alone — a
            # `needs-you` or `idle` session is silent by design.
            s.set_state("stalled?", confidence="tick")
        self.store.save(s)

    def _hook_fresh(self, sid: str, now: datetime) -> bool:
        last = self._last_hook.get(sid)
        return last is not None and now - last <= STALL_AFTER

    def _screen_verdict(self, s: Session, adapter: Any, now: datetime) -> Any:
        """The adapter's screen-rule match for the session's tail, or None when there is no rule
        set, nothing matched, or a hook state is fresher (design §4.2: scraped never outranks it)."""
        explain = getattr(adapter, "explain", None)
        if explain is None or self._hook_fresh(s.id, now):
            return None
        return explain(s.tail)

    @staticmethod
    def _screen_held(s: Session) -> bool:
        """Whether a hook-fed record's state is a screen rule's verdict: scraped, and not one the
        tick reads without a rule (`exited`, `closed`, `stalled?`)."""
        return s.confidence == "scraped" and s.state not in ("exited", "closed", "stalled?")

    def _screen_gone(self, s: Session, adapter: Any, now: datetime) -> bool:
        """Whether a hook-fed record holds a screen rule's verdict that its screen no longer shows:
        no hook is fresher and no rule matches the tail (`_screen_verdict` was None)."""
        has_rules = getattr(adapter, "explain", None) is not None
        return self._screen_held(s) and has_rules and not self._hook_fresh(s.id, now)

    def _apply_event(self, sid: str, event: dict[str, Any], queued: bool = False) -> None:
        """A hook-fed state transition (design §4.2 table). Adapters map hook names to these.

        A **queued** event (the hook's call failed and it wrote `events/<session>.jsonl`, drained
        at the tick) carries `at`, when it happened. One stamped before an event that reached the
        record live is older than the state that event set, so its state is skipped; its adapter
        id, model and subagent count still apply (TD-169)."""
        s = self.sessions.get(sid)
        if s is None:
            return
        at = event.get("at") if queued else None  # a queue written before the stamp is applied as it was
        stale = isinstance(at, int | float) and at < self._live_hook_at.get(sid, 0.0)
        aid = event.get("adapter_id")
        if aid and s.adapter_id and aid != s.adapter_id and event.get("state") in ("exited", "closed"):
            # The end of a run this record no longer holds (TD-186): a restart closed the old run and
            # created this one under the same id, and the old run's SessionEnd landed after it. It is
            # not this run's exit, and its tool id is not this run's — applied, it marked a live run
            # `exited` and made its own tool session read as *outside agentorc* to the anchor rule.
            log.info("%s: ignored the end of a previous run (%s; this run is %s)", sid, aid, s.adapter_id)
            return
        if s.state == "closed":
            # Closed is final (§4.2 *Close*: the card kept a day, then forgotten). `ao close` kills
            # the pane, and the tool's own SessionEnd for the run it killed lands after it: applied,
            # it read `exited`, offered Forget, and the reconcile's day-long keep — which reads only
            # `closed` — never forgot the record (TD-200 (3)). Whatever a closed run still says is
            # the dying run's.
            log.info("%s: ignored %s on a closed record", sid, event.get("event") or event.get("state") or "a hook")
            return
        if not queued:
            self._live_hook_at[sid] = time.time()
        self._last_hook[sid] = datetime.now(UTC)  # apply time, also for events drained from the offline queue
        if aid:
            s.adapter_id = aid
        if model := event.get("model"):
            s.model = str(model)  # SessionStart's `model`, or a `/model` switch (TD-031)
        if delta := event.get("subagent_delta"):
            s.subagents = max(0, s.subagents + int(delta))
        if event.get("prompt") and not stale and (s.first_prompt or s.first_prompt_error):
            # a prompt went in (the tool's UserPromptSubmit, §4.1 *No prose in the argv*): the brief
            # typed by the tick, or a person's or a manager's send that cures *brief not sent*
            s.first_prompt_sent_at = s.first_prompt_sent_at or now_iso()
            s.first_prompt, s.first_prompt_error = None, None
        if not stale:
            self._bell_answered(s, event)  # a ring's turn, judged at its Stop (§4.10, TD-347)
        state = None if stale else event.get("state")
        if state:
            pending = Pending.from_dict(event["pending"]) if event.get("pending") else None
            if state == "needs-you" and self._permission_waiting(sid):
                # Claude Code draws its own dialog a few seconds into a PermissionRequest hook
                # and fires a permission_prompt Notification for it; the hook is still blocking
                # and its answer still wins (verified 2026-09-06). Keep Allow / Deny up.
                pass
            else:
                if state == "working" and s.state == "idle" and s.confidence == "hook" and event.get("event"):
                    # An event that is not a turn's start woke a session its Stop left idle: the
                    # capture TD-201 asks for, since the one that did it once is not yet named.
                    log.info("%s: %s turned a hook-confirmed idle session working", sid, event["event"])
                if state == "exited" and s.state != "exited":
                    # the tool's own end (§4.2 the `SessionEnd` row, TD-490): its `reason`, and its
                    # code when the event carries one
                    s.ended = {"how": "tool", "at": now_iso()}
                    for k in ("reason", "code"):
                        if event.get(k) not in (None, ""):
                            s.ended[k] = event[k]
                s.set_state(state, confidence="hook", pending=pending)
                self._hook_state.pop(sid, None)  # a hook's word: no screen's verdict to go back from
        self.store.save(s)

    def _permission_waiting(self, sid: str) -> bool:
        return any(k[0] == sid and not f.done() for k, f in self._waiters.items())

    def _scrub(self, sid: str) -> None:
        """No cadence or hook key outlives the session it was about — whether the record is
        forgotten or replaced in place by a new session of the same name (§4.1)."""
        for side in (
            self._git_checked,
            self._files_read,
            self._derived_at,
            self._model_checked,
            self._context_checked,
            self._pre_limited,
            self._last_hook,
            self._hook_state,
            self._live_hook_at,
            self._killed_at,
            self._mail_hints,
            self._asks_hints,
            self._bells,
            self._rang,  # a ring's count and the run of unread rings (TD-347): a reused id starts at none
            self._unread_rings,
        ):
            side.pop(sid, None)
        self._lead_typed.discard(sid)  # a reused id never starts with the old run's line in the composer (TD-347)
        if not (lock := self._typing.get(sid)) or not lock.locked():
            self._typing.pop(sid, None)  # a held one stays: its typist is mid-paste, the name reused or not
        for key in [k for k in self._attention_how if k.split("|", 1)[0] == sid]:
            del self._attention_how[key]
        for key in [k for k in self._attention if k.split("|", 1)[0] == sid]:
            del self._attention[key]  # belt and braces: `_attention_gone` wrote these out already

    def _write_launch(self, address: str, s: Session, params: dict[str, Any]) -> None:
        """The launch record of a supervised create (design §6 *Keeping a team running*): what the
        create was handed — adapter, profile, the prompt as handed, lane, name, directory, worktree,
        role, badges — with the record's own `controllers` (the creator added), written at the home
        as `launch/<address>.json`, which a restart replays rather than re-reading any definition.
        Written at every such create, so a resume with changes leaves the person's latest choices;
        owner-only, since it holds the brief. A failure to write is logged, never the create's."""
        record = {**params, "controllers": list(s.controllers), "supervised": True, "id": address, "at": now_iso()}
        path = paths.launch_dir() / f"{address}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)  # as the launch scripts beside it have it (tmux.py)
            tmp = path.with_suffix(".json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only from the start
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=1)
            tmp.replace(path)
        except OSError as e:
            log.warning("%s: could not write its launch record: %s", address, e)

    @staticmethod
    def _drop_launch(address: str) -> None:
        with contextlib.suppress(OSError):
            (paths.launch_dir() / f"{address}.json").unlink(missing_ok=True)

    def _drop_context(self, conversation: str | None, gone: Session) -> None:
        """A conversation's start-context file, once no record but `gone` holds it (§4.1 *No prose in
        the argv*, TD-339): on Forget, and when a create replaces a record in place with another
        conversation. A record still holding it would hand it again on a Resume."""
        if not conversation or any(r.adapter_id == conversation for r in self.sessions.values() if r is not gone):
            return
        with contextlib.suppress(OSError, ValueError):
            paths.context_file(conversation).unlink(missing_ok=True)

    def _forget(self, sid: str) -> None:
        gone = self.sessions.get(sid)
        if gone is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        self._drop_launch(sid)  # the launch record goes with the record on Forget (§6)
        self._drop_context(gone.adapter_id, gone)
        # Its open `ask`s expire with it (design §4.10 lifecycle): the record and its inbox go, and
        # every other holder of those asks — the askers — is told so. Done while it is still in the
        # map so `_mark` reaches it, harmlessly, along with the rest. A `steer` is the exception:
        # its bound runs whatever becomes of the addressee, so the sender's copy lapses on time.
        for e in [e for e in gone.inbox if e.open and e.kind != "steer"]:
            self._close_entry(e.id, "expired", now_iso())
        self._asker_gone(gone, self._address(gone), how="forgotten")
        self._attention_gone(gone, "forgotten")
        self.sessions.pop(sid, None)
        self.store.delete(sid)
        # Scrub the id from every subscriber's map and queue the one `gone`: whichever
        # `_push_changes` runs next (the caller's or a tick's) announces it exactly once.
        for last in self._subscribers.values():
            last.pop(sid, None)
        self._scrub(sid)
        self._gone.append(sid)


def _recent_files(directory: str) -> list[dict[str, Any]] | None:
    """A record's recent files (§4.2 *The record's `files`*, TD-538): what the work in `directory` changed
    against `merge-base HEAD origin/<default>` by the cadence's default-branch rule — the tree alone with
    no origin — or None when a read failed. Blocking: a thread's."""
    return changed_files(directory, ledger_mod.default_ref(directory), RECENT_FILES)


def _span(seconds: float) -> str:
    """An age as a person reads it: *40m*, *6h*, *2d*."""
    m = int(seconds // 60)
    if m < 120:
        return f"{m}m"
    return f"{m // 60}h" if m < 48 * 60 else f"{m // 1440}d"


def _lines(crossed: Any) -> list[tuple[Any, Any]]:
    """A balance crossing's lines and limits, without the numbers that move with the clock."""
    return [(c.get("line"), c.get("limit")) for c in crossed or [] if isinstance(c, dict)]


def _balance_hold(held: Any) -> dict[str, Any] | None:
    """The balance hold a standing `held` carries (§6 rule 8's fifth bound): the hold itself, or the one
    kept beside another bound that displaced it (`_work_held`); None when it carries none."""
    if not isinstance(held, dict):
        return None
    if held.get("why") == "balance":
        return held
    inner = held.get("balance")
    return inner if isinstance(inner, dict) and inner.get("why") == "balance" else None
