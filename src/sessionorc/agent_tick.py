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
    naming,
    paths,
    reports,
)
from sessionorc import brief as brief_mod
from sessionorc import ledger as ledger_mod
from sessionorc import settings as settings_mod
from sessionorc.agent_common import (
    COMPOSER_LINES,
    CONTEXT_AGAIN,
    DERIVE_EVERY,
    FILL_CEILING,
    FILL_WINDOW,
    GIT_EVERY,
    IDLE_NUDGE,
    LANE_NEWS_NAMED,
    LAUNCH_KEYS,
    MODEL_EVERY,
    PRUNE_EVERY,
    REMOVED_GUARD_SECONDS,
    REPOS_EVERY,
    RESTART_CEILING,
    RESTART_WINDOW,
    RESUME_MIN,
    SEAT_IDLE_GRACE,
    SETTLED,
    STALL_AFTER,
    TAIL_LINES,
    USAGE_BACKOFF_MAX,
    RpcError,
    _cap,
    _clean,
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
from sessionorc.gitinfo import git_info
from sessionorc.models import (
    SYSTEM,
    Pending,
    Session,
    context_over_text,
    now_iso,
)
from sessionorc.tmux import PaneInfo


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
        self._note_attention(snapshot_at)
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
            await asyncio.to_thread(self._prune_runs, snapshot_at, live)
        if self.mode == "home":
            self._supervise_containers()
            day = datetime.now().astimezone().date().isoformat()
            if day != self._backed_up and (self._backup_task is None or self._backup_task.done()):
                self._backed_up = day  # tried once a day: a failure is a log line and tomorrow's retry
                self._backup_task = asyncio.create_task(self._backup(day))
        if self.mode == "home" and (self._repos_task is None or self._repos_task.done()):
            # detached as the usage refresh is: `gh` talks to the network (§4.4 *Repo facts*)
            self._repos_task = asyncio.create_task(self._refresh_repos())
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
        await self._sweep_mail(snapshot_at)
        self._poke_waits()  # the wake decision is re-taken every tick for a session blocked in `wait`
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
                    await self.rpc_kill(s.id)
                    continue
                if not s.wrapup_prompt:
                    # Nothing to say, so say nothing and stop it: a stop time with no wrap-up text is
                    # still a stop time, and silently running past it is the failure this fixes.
                    log.info("%s reached its run_until with no wrap-up prompt; stopping it", s.id)
                    await self.rpc_kill(s.id)
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
                await self.rpc_kill(s.id)

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
            reading = self._usage.get(s.profile) or {}
            windows = reading.get("windows")
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
                }
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
        if changed:
            await self._push_changes()

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
                await self._idle_nudge(s, now)
                await self._context_line(s, now)
                self._lane_news(s, now)
            except Exception:  # noqa: BLE001 — one record's failure is never the tick's (§6)
                log.exception("%s: the keep-running pass failed", self._address(s))
        if (
            self._seat_count_task is None or self._seat_count_task.done()
        ) and now - self._seat_counted_at > DERIVE_EVERY:
            # detached, as the reports are: `gh` talks to the network, and the tick must not wait on it
            self._seat_counted_at = now
            self._seat_count_task = asyncio.create_task(self._count_seats(records))

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
        """Whether `profile` is over a usage line now (§6 *Usage gate*): the gate's own reading, for a
        record the gate no longer marks — it clears `gated` on an exited one — so a policy does not
        restart or fill into a pause. No reading is no gate, as at the gate (a failure never gates).
        `team` is the record's: its reserve priority lowers the line exactly as it does at the gate
        (TD-146), or a teamed member would be restarted at 65% and paused on the next tick."""
        windows = (self._usage.get(profile) or {}).get("windows")
        if windows is None:
            return False
        whole = settings_mod.load()
        by_label = self._gate_reserves(whole, profile) or {}
        extra = settings_mod.team_extra(whole, team)
        return settings_mod.crossed(settings_mod.lines(by_label, windows, now, extra)) is not None

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
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s exited on its own with nothing declared: restarting it (%d in the window)", s.id, len(recent) + 1)
        await self._replay(s, "crash")

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
        if s.superseded_by or s.suspended or s.gated:
            return
        # A ceiling guards against a crash loop, not a member that has worked since (TD-186): a clean
        # declaration is acted on once the window holds fewer than the ceiling's restarts; the new
        # record carries no mark. Inside the window the mark stands, as it does for rule 1.
        if s.restart_ceiling and self._window_full(s, now):
            return
        # the tick's own close, then a replay that failed, leaves it `closed`: still the tick's to retry
        closed_by_tick = s.state == "closed" and bool(s.restarts) and s.restarts[-1].get("why") == "wanted"
        if not (s.state == "idle" or (s.state == "exited" and s.pane) or closed_by_tick):
            return
        if (s.run_until and now >= _parse(s.run_until)) or self._profile_gated(s.profile, now, s.team):
            return
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        git = s.git or {}
        if not isinstance(git.get("dirty"), int) or not isinstance(git.get("unpushed"), int):
            return  # an unknown git state is left alone (§6 rule 2)
        if git["dirty"] or git["unpushed"]:
            await self._restart_held(s, now, git["dirty"], git["unpushed"])
            return
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
        if len(recent) >= RESTART_CEILING:
            s.restart_ceiling = {"at": now_iso(), "count": len(recent)}
            log.warning("%s: %d restarts in %s — the ceiling; it is a person's now", s.id, len(recent), RESTART_WINDOW)
            self._save(s)
            await self._push_changes()
            return
        log.info("%s wants another run and its work is pushed: restarting it", s.id)
        if s.state == "idle":
            try:
                if s.host == self.host:
                    await self.rpc_close(s.id)
                else:
                    await self._route_act("close", {"id": s.id}, None, s.host)
            except Exception as e:  # noqa: BLE001 — a close that failed is a restart that failed, and counts
                # `rpc_close` marks the record closed before its own tail runs, so a failure there
                # leaves it `closed` with nothing replayed: the entry is what lets the next tick
                # retry it (`closed_by_tick`) rather than strand it (review of PR #461)
                s.restarts = [*s.restarts, {"at": now_iso(), "why": "wanted", "error": f"close: {e}"}]
                log.warning("%s: the close before a wanted restart failed: %s", s.id, e)
                self._save(s)
                await self._push_changes()
                return
        await self._replay(s, "wanted")

    @staticmethod
    def _window_full(s: Session, now: datetime) -> bool:
        recent = [r for r in s.restarts if isinstance(r, dict) and _recent(r.get("at"), now, RESTART_WINDOW)]
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
        — a lane reference not done or dropped, a declared claim not done or dropped, or for a seat a
        question waiting — and nothing declared, is sent one fixed line naming it, once per idle
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
        line = self._nudge_line(s)
        if line and await self._policy_send(s, line):
            s.nudged_at = now_iso()
            log.info("%s: idle %s with work open — nudged", s.id, IDLE_NUDGE)
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

    def _lane_news(self, s: Session, now: datetime) -> None:
        """Rule 6 (design §6, TD-195): a supervised member, not a seat, that declared out of work is
        told when its lane gains entries. The first tick that sees the declaration writes
        `lane_seen` — the ids in its repo's ledger reading that match its lane; a later reading
        holding a matching id not in it, while the member is live, not winding down, not gated and
        not suspended, becomes one `note` from `system` naming the new ids, which are then added,
        so each is told once. The doorbell does the waking; nothing is typed here. No reading —
        the repo not in this home's registry, or its file unreadable — writes nothing."""
        if not (s.supervised and s.out_of_work) or s.seat is not None or s.superseded_by:
            return
        led = (self._repos.get(s.repo or "") or {}).get("ledger") or {}
        if "error" in led or not isinstance(led.get("entries"), list):
            return
        ids = [
            str(e["id"])
            for e in led["entries"]
            if isinstance(e, dict) and e.get("id") and any(ledger_mod.lane_matches(w, e) for w in s.lane)
        ]
        if s.lane_seen is None:
            s.lane_seen = {"at": now_iso(), "ids": ids}
            self._save(s)
            return
        seen = set(s.lane_seen.get("ids") or [])
        new = [i for i in ids if i not in seen]
        if not new or s.state in ("exited", "closed") or s.suspended:
            return
        if s.wrapup_at or s.wrapup_sent_at or (s.run_until and now >= _parse(s.run_until)):
            return
        if s.gated or self._profile_gated(s.profile, now, s.team):
            return
        s.lane_seen = {"at": now_iso(), "ids": [*s.lane_seen.get("ids", []), *new]}
        named = ", ".join(new[:LANE_NEWS_NAMED]) + (
            f" and {len(new) - LANE_NEWS_NAMED} more" if len(new) > LANE_NEWS_NAMED else ""
        )
        count = f"{len(new)} entr{'y' if len(new) == 1 else 'ies'}"
        self._system_note(
            self._address(s),
            f"your lane gained {count} since you declared out of work: {named} — read the ledger on "
            "`origin/main`, then claim one or declare again",
        )
        self._save(s)
        log.info("%s: told of %s new in its lane", self._address(s), count)

    def _nudge_line(self, s: Session) -> str | None:
        if s.seat is not None:
            n = s.asks_waiting(home=self.host) if s.seat_due else 0
            return f"[agentorc] you have {n} questions waiting — run `ao inbox`" if n else None
        ended = {e.ref for e in s.progress if e.status in ("done", "dropped")}
        claimed = [e.ref for e in s.progress if e.source == "declared" and e.status == "claimed"]
        ref = next((r for r in [*s.lane, *claimed] if r != "free-pick" and r not in ended), None)
        if ref is None:
            return None
        return (
            f"[agentorc] you have been idle {int(IDLE_NUDGE.total_seconds() // 60)} minutes with `{ref}` open "
            f"— end the run with one of `ao progress done {ref} --pr N`, `ao progress drop {ref} --why`, "
            "`ao progress none --why` or `ao progress restart --why`"
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

    async def _replay(self, s: Session, why: str, **extra: Any) -> None:
        """One restart by the tick (§6): the record's launch record handed to `create` again — here,
        or at its node — the attempt appended to `restarts` and the list carried onto the new record,
        so the count survives the restart it counts. A replay that fails keeps its entry with `error`
        and counts all the same."""
        entry: dict[str, Any] = {"at": now_iso(), "why": why}
        history = [*s.restarts, entry]
        address = self._address(s)
        read: dict[str, Any] | None = None
        try:
            params = {**self._read_launch(address), **extra, "supervised": True}
            read = await self._refill_prompt(s, params, entry)
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
            if read is not None:  # the files as this replay read them, not as the create's check did
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
        due = self._seat_due(s, now)
        if due != s.seat_due:
            s.seat_due = due
            self._save(s)
            await self._push_changes()
        if s.host != self.host and s.host not in self._link_muxes:
            return  # its link is down: left as it is, looked at again next tick (§4.4a)
        if s.state in ("exited", "closed") and s.seat_due:
            await self._fill(s, now, records)
        elif s.state == "idle" and not s.seat_due and self._seat_has_run(s, now):
            log.info("%s: a seat with nothing due, idle and pushed — closing it (§6 rule 3)", s.id)
            if s.host == self.host:
                await self.rpc_close(s.id)
            else:
                await self._route_act("close", {"id": s.id}, None, s.host)

    def _seat_due(self, s: Session, now: datetime) -> dict[str, Any] | None:
        """`seat_due: {at, by}` (§6 rule 3): set once the trigger is met and kept until the fill — but
        `asks` is a question waiting now, so it clears again if none is. `prs` reads `seat_count`,
        which `_count_seats` keeps; `every` is the time since this record was created."""
        seat = s.seat or {}
        trigger = seat.get("trigger")
        if trigger == "asks":
            return (s.seat_due or {"at": now_iso(), "by": "asks"}) if s.asks_waiting(home=self.host) else None
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

    async def _fill(self, s: Session, now: datetime, records: list[Session]) -> None:
        """A due seat, ended, is filled — unless its profile is paused, or it or its fellows are at
        the fill ceiling: six fills an hour over all seats sharing a controller (the graph, never the
        team badge). The seat whose fill tripped it gets `restart_ceiling` and the Inbox row; its
        fellows are merely refused until the hour rolls. Fills never count toward `RESTART_CEILING`."""
        if s.restart_ceiling or self._profile_gated(s.profile, now, s.team):
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
        await self._replay(s, "fill", keep_mail=True)

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
            got = await asyncio.to_thread(self._read_repos, todo, prev, full) if todo else {}
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
            for r, v in changed.items():
                await self._broadcast({"event": "repos", "root": r, "repo": v})
            for r in gone:
                await self._broadcast({"event": "repos", "root": r, "repo": None})
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next tick tries again
            log.exception("reading the repo facts failed")

    @staticmethod
    def _ledger_mtimes(roots: list[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for root in roots:
            try:
                out[root] = (Path(root) / ledger_mod.ledger_path(root)).stat().st_mtime
            except OSError:
                out[root] = None
        return out

    @staticmethod
    def _read_repos(roots: list[str], prev: dict[str, dict[str, Any]], full: set[str]) -> dict[str, dict[str, Any]]:
        """Each checkout's reading, in a thread: `{name, root, remote, ledger, prs, at}`. A root in
        `full` has its PRs and the ledger's history read; the others their ledger's entries alone,
        the rest carried from `prev`. One checkout's read that raises keeps its last reading with
        the error beside it and never costs the others theirs."""
        now = datetime.now(UTC)
        stamp = now.isoformat()
        by_remote: dict[str, dict[str, Any]] = {}
        out: dict[str, dict[str, Any]] = {}
        for root in roots:
            old = prev.get(root) or {}
            try:
                out[root] = TickMixin._read_repo(root, old, root in full, now, by_remote)
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
        root: str, old: dict[str, Any], due: bool, now: datetime, by_remote: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """One checkout's reading (`_read_repos`): `by_remote` holds the PR readings taken in this
        pass, so two checkouts of one remote are one `gh` read."""
        stamp = now.isoformat()
        remote = reports._git(root, "remote", "get-url", "origin") if due else None
        remote = (remote or "").strip() if due else str(old.get("remote") or "")
        led = ledger_mod.reading(root, now, with_history=due)
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
        return {
            "name": Path(root).name,
            "root": root,
            "remote": remote,
            "ledger": led,
            "prs": prs,
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

    def _prune_runs(self, now: datetime, live: set[str]) -> None:
        """Run-log retention (design §4.6): a log older than `runs_keep_days` goes unless it is in
        `live`, the logs of sessions still running (never truncate a live log: invariant 3). Logs
        of forgotten sessions are the common case — Forget keeps the file until this sweep. `0`
        keeps everything. Runs in a thread: touches files, never `self.sessions`."""
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
        for s in due:
            self._git_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            info = infos.get(s.id)
            new = info.to_dict() if info else None
            if new != live.git:
                live.git = new
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
        """Ask each account a live agent session's profile names for its usage every
        `USAGE_EVERY`, once per account (§4.2a, TD-122), in a thread; a fetch failure keeps the
        last answer and never gates anything (design §6).
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
        for key, profs in groups.items():
            self._usage_seed(key, profs)
        due: dict[str, tuple[Any, str]] = {}
        for key in groups:
            wait = self._usage_wait.get(key, agent_common.USAGE_EVERY)
            if mono - self._usage_checked.get(key, -wait) >= wait:
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
        for key in [k for k in self._usage_acct if k not in groups]:
            self._usage_acct.pop(key, None)
            self._usage_checked.pop(key, None)
            self._usage_wait.pop(key, None)
        shown = {p for profs in groups.values() for p in profs} | metered
        if dropped := [p for p in self._usage if p not in shown]:
            for prof in dropped:
                self._usage.pop(prof, None)
                await self._broadcast({"event": "usage", "profile": prof, "usage": None})
            self.usage_store.save(self._usage)
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

    def _usage_seed(self, key: str, profs: list[str]) -> None:
        """An account the poll has not met since the agent started takes its reading, and its
        poll's allowance, from the newest reading its profiles hold (TD-087, TD-122): a restart
        keeps the chip and does not ask sooner than `fetched + USAGE_EVERY`. A reading with no
        readable time is polled at once."""
        held = [self._usage[p] for p in profs if isinstance(self._usage.get(p), dict)]
        if key not in self._usage_acct and held:
            newest = max(held, key=lambda r: str(r.get("fetched") or ""))
            self._usage_acct[key] = {k: v for k, v in newest.items() if k not in ("account", "tool")}
        if key not in self._usage_checked and (seen := [m for r in held if (m := _usage_checked_at(r)) is not None]):
            self._usage_checked[key] = max(seen)

    def _usage_reading(self, key: str, r: Any) -> dict[str, Any] | None:
        """One poll's answer folded into what this account already had (TD-087, TD-122: `key` is
        the account's, so a backoff holds every profile on it), or None when nothing changed and
        nothing need be said.

        An adapter now answers with a **reason** rather than a silence: `ok` with the windows,
        or `rate_limited` / `no_credentials` / `no_profile` / `error` with none. A reading is
        replaced only by a newer reading — a failure **keeps the last one**, with the reason
        beside it, because *the chip went out* and *the allowance is spent* are different things
        to a person and a five-hour window does not change while we are refused. The backoff is
        set here too: a 429 waits the endpoint's own `Retry-After`, or doubles to a ceiling; any
        other answer, good or bad, goes back to the ordinary cadence, since only a 429 is the
        endpoint telling us to ask less often."""
        if isinstance(r, BaseException) or not isinstance(r, dict):
            r = {"reason": "error"}
        reason = str(r.get("reason") or ("ok" if r.get("windows") is not None else "error"))
        if reason == "rate_limited":
            after = r.get("retry_after")
            prev = self._usage_wait.get(key, agent_common.USAGE_EVERY)
            # the endpoint's own word is floored at the ordinary cadence and **not** capped: a
            # server saying *an hour and a half* is telling us something our ceiling is guessing
            # at. The ceiling is for our own doubling, which has no such word behind it.
            self._usage_wait[key] = (
                max(float(after), agent_common.USAGE_EVERY)
                if isinstance(after, int | float)
                else min(prev * 2, USAGE_BACKOFF_MAX)
            )
        else:
            self._usage_wait.pop(key, None)
        was = self._usage_acct.get(key) or {}
        if was.get("reason") != reason:
            # once per change of reason, never per poll: a 429 every five minutes is one line
            log.info("usage for account %s: %s (was %s)", key, reason, was.get("reason") or "no reading yet")
        if reason == "ok":
            out = {"windows": r.get("windows"), "fetched": r.get("fetched"), "reason": "ok"}
        else:
            out = {**{k: v for k, v in was.items() if k in ("windows", "fetched")}, "reason": reason}
            if isinstance(r.get("retry_after"), int | float):
                out["retry_after"] = r["retry_after"]
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
                    s.set_state("exited", confidence="scraped")
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
            if s.state != "exited":
                s.set_state("exited", confidence="scraped")
        elif adapter.state_source == "scraped":
            st = adapter.classify(pane, tail)
            if st and st != s.state:
                s.set_state(st, confidence="scraped")
        elif (m := self._screen_verdict(s, adapter, now)) is not None:
            # Hook-fed adapter, screen rule fired, no fresher hook state: the labelled fallback
            if m.state != s.state or (m.pending and m.pending != s.pending):
                s.set_state(m.state, confidence="scraped", pending=m.pending)
        elif s.state == "working" and s.last_output and now - _parse(s.last_output) > STALL_AFTER:
            # Hook-fed adapters: the liveness cross-check applies to `working` alone — a
            # `needs-you` or `idle` session is silent by design.
            s.set_state("stalled?", confidence="scraped")
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
                s.set_state(state, confidence="hook", pending=pending)
        self.store.save(s)

    def _permission_waiting(self, sid: str) -> bool:
        return any(k[0] == sid and not f.done() for k, f in self._waiters.items())

    def _scrub(self, sid: str) -> None:
        """No cadence or hook key outlives the session it was about — whether the record is
        forgotten or replaced in place by a new session of the same name (§4.1)."""
        for side in (
            self._git_checked,
            self._derived_at,
            self._model_checked,
            self._context_checked,
            self._pre_limited,
            self._last_hook,
            self._live_hook_at,
            self._killed_at,
            self._mail_hints,
            self._asks_hints,
            self._bells,
        ):
            side.pop(sid, None)
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

    def _forget(self, sid: str) -> None:
        gone = self.sessions.get(sid)
        if gone is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        self._drop_launch(sid)  # the launch record goes with the record on Forget (§6)
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
