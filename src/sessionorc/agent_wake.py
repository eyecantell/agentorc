"""Waking and the doorbell (TD-108 step 1): design §4.8 *Waking a manager* and §4.10 *The host agent decides
each wake* and *How a Claude Code session is told it has mail*, as a mixin `HostAgent` inherits. The run
of `agent.py` that followed them came too, in its order: the hook's entry and the permission it waits on
(`rpc_hook`, `rpc_decide`), and the small reads and settings (`rpc_recent_dirs`, `rpc_repos`,
`rpc_doing_log`, `rpc_usage`, `rpc_gate`, `rpc_settings`, `rpc_set_settings`, `rpc_adapters`, `rpc_ping`,
`rpc_host`). Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sessionorc import (
    adapters,
    defs,
    mail,
    paths,
    waits,
)
from sessionorc import settings as settings_mod
from sessionorc import spend as spend_mod
from sessionorc import usage as usage_mod
from sessionorc.agent_common import (
    COMPOSER_LINES,
    DOORBELL_TRIES,
    RpcError,
    _parse,
    _usage_key,
    _Wait,
    log,
)
from sessionorc.models import (
    MailEntry,
    Pending,
    Session,
    now_iso,
)


class WakeMixin:
    # -- waking (design §4.8 "Waking a manager", §4.10 "The host agent decides each wake") ----------

    async def rpc_wait(
        self, timeout: float = 600.0, scope: str = "controlled", only_host: str | None = None, caller: Any = None
    ) -> dict[str, Any]:
        """`ao wait [--timeout N] [--scope controlled|all]` (TD-049, moved here by TD-052 step 3):
        block until something in the caller's scope changes, new mail wakes it, or the timeout
        passes — and **that timeout is the fallback poll**.

        A read, never gated (§9 invariant 11). The snapshot is this agent's own records, complete
        by construction, compared against the per-caller cursor under `waits/` exactly as the CLI
        did: an unreadable cursor wakes on everything in scope, a first wait records and wakes on
        nothing. Mail's half of the cursor is the caller's `mail_decided` watermark, on its record:
        while it is blocked here the caller is *reachable*, so every pass — each change, and each
        tick — takes the wake decision (`_decide_wake`), which may return the wait for mail. A
        member's change returns at once, carrying any undecided mail with it for free.

        Returns `{"changed": [records], "mail": [headers], "wake": decision | None}`. The mail is
        headers only — id, from, kind, about, at, the sender's role — because `read_at` means
        *`ao inbox` printed it* and nothing else (§4.10 lifecycle)."""
        if scope not in waits.SCOPES:
            raise RpcError(f"unknown scope {scope!r}; scopes are: {', '.join(waits.SCOPES)}")
        who = None if mail.is_person(caller) else self._addr(caller)
        before = waits.read_cursor(who)
        cursor: dict[str, str] | None = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.0)
        me = _Wait(who)
        self._waits.add(me)
        try:
            while True:
                me.poke.clear()
                views = self._views()
                if only_host:  # a person over a link watches that node's records only (§4.4a)
                    views = [v for v in views if v.get("host") == only_host]
                changed, cursor = waits.wake_changes(before, waits.wait_scope(views, who, scope))
                s = self._graph().get(who) if who is not None else None  # a node's session waits here too (step 5)
                wake = self._decide_wake(s, member_change=bool(changed)) if s is not None else None
                if changed or (wake is not None and wake["cause"] == "mail"):
                    covered = wake["entries"] if wake is not None else []
                    return {
                        "changed": changed,
                        "mail": [self._header(s, e) for e in covered] if s is not None else [],
                        "wake": {k: v for k, v in wake.items() if k != "entries"} if wake is not None else None,
                    }
                left = deadline - loop.time()
                if left <= 0:
                    return {"changed": [], "mail": [], "wake": None}
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(me.poke.wait(), left)
        finally:
            # Discarded before anything else can run: a cancelled wait (its connection closed) is
            # gone by the time the next tick decides. The cursor is written on every way out —
            # it holds only what this wait compared and found unchanged, or what it returned.
            self._waits.discard(me)
            if cursor is not None:
                waits.write_cursor(who, cursor)

    def _header(self, s: Session, e: MailEntry) -> dict[str, Any]:
        return {
            "id": e.id,
            "from": e.from_,
            "from_role": mail.from_role(self._graph(), self._address(s), e.from_, controllers=self._ctl),
            "kind": e.kind,
            "about": e.about,
            "at": e.at,
        }

    def _poke_waits(self) -> None:
        for w in self._waits:
            w.poke.set()

    def blocked_in_wait(self, sid: str) -> bool:
        """Reachable in step 3's sense: some connection holds a `wait` for this session now."""
        return any(w.caller == sid for w in self._waits)

    # -- the doorbell (design §4.10 "How a Claude Code session is told it has mail") -------------

    def _bell_blocked(self, s: Session, now: datetime) -> str | None:
        """Why the doorbell may not ring for `s` now, or None when it may. The order is the
        design's: a session that cannot be rung at all, then a pending stop, then — in `_ring`, the
        wake decision — the budget and new mail."""
        if s.state != "idle" or s.confidence != "hook":
            return "not hook-confirmed idle"  # never a scraped idle or `stalled?` (a takeover)
        if not mail.mail_wakes(s):
            return "a person's session"  # invariant 5: the chip and the line, nothing typed
        if getattr(adapters.get(s.adapter), "composer", None) is None:
            return "no composer"  # without a submit confirmation a ring could land on a half-typed line
        if s.wrapup_sent_at or s.wrapup_at or (s.run_until and now >= _parse(s.run_until)):
            return "a wrap-up under way"  # mail never pushes a session past its stop
        if s.gated:
            return "paused by the usage gate"  # mail never wakes a session the gate paused (§6)
        if self.blocked_in_wait(s.id):
            return "blocked in wait"  # reachable already: the wait takes the decision
        return None

    def _ring_doorbells(self) -> None:
        """Each tick: start a ring for every session the doorbell may ring. A ring is its own task,
        since a submit takes seconds and the tick must not wait on it; one per session at a time.
        One ring per idle stretch; a failed one is tried once more in the same stretch, then
        recorded, and nothing more is typed until the session's state next changes."""
        if self.mode == "node":
            return  # the mailbox is the home's; a node's doorbell is the forwarded `wait` (§4.4a)
        now = datetime.now(UTC)
        for s in list(self.sessions.values()):
            if s.id in self._ringing:
                continue
            bell = self._bells.get(s.id)
            if bell is not None and bell["rev"] != s.rev:
                del self._bells[s.id]  # its state changed: a new stretch
                bell = None
            if not s.unread() or self._bell_blocked(s, now):
                continue
            if bell is not None and (bell["rung"] or bell["failures"] >= DOORBELL_TRIES):
                continue
            self._ringing[s.id] = asyncio.create_task(self._ring(s.id, retry=bell is not None))

    async def _ring(self, sid: str, *, retry: bool) -> None:
        try:
            await self._ring_once(sid, retry=retry)
        except Exception:  # noqa: BLE001 — a detached task: a ring that breaks is a log line
            log.exception("doorbell for %s failed", sid)
        finally:
            self._ringing.pop(sid, None)

    async def _ring_once(self, sid: str, *, retry: bool) -> None:
        """One ring: the composer must read empty — a person's half-typed words would otherwise be
        submitted with the line appended — then the wake decision (the budget, and mail no wake has
        covered: *rung only when the count has risen*), then the fixed line through `_type`. A
        retry was decided and charged by the first try, so it takes no second decision; it rings
        only into an empty composer, since a stuck first try may have left the line there. All of
        it holds the pane's typing lock, and a pane a `send` is typing into is left to the next
        tick: read before that send's paste shows, the composer is empty, and a ring decided then
        would paste into the middle of it — one submitted line, and mail the watermark had already
        passed, so it was never rung again (TD-094)."""
        s = self.sessions.get(sid)
        if s is None:
            return
        typing = self._typing[sid]
        if typing.locked():
            return  # a `send` is typing into the pane: the next tick looks again
        async with typing:
            await self._ring_typing(s, retry=retry)

    async def _ring_typing(self, s: Session, *, retry: bool) -> None:
        sid = s.id
        adapter = adapters.get(s.adapter)
        rev = s.rev
        tail = await asyncio.to_thread(self.tmux.capture_tail, sid, COMPOSER_LINES, raw=True)
        s = self.sessions.get(sid)
        if s is None or s.rev != rev or self._bell_blocked(s, datetime.now(UTC)):
            return  # it moved while the screen was read: the next tick looks again
        composer = adapter.composer(tail)
        if composer is None or (composer and not retry):
            return  # a dialog, or someone's words: wait for the next tick
        if retry:
            bell = self._bells[sid]
            if composer:  # the first try's own line is still there: nothing more is typed
                self._bell_failed(s, bell, "prompt-stuck: the line is still in the composer")
                return
        elif self._decide_wake(s, member_change=False, via="doorbell") is None:
            return  # nothing new since the last wake, or the budget is spent: the mail waits
        else:
            # only a decided ring has a stretch to keep: an undecided tick is taken again next tick,
            # which is how a refilled budget rings for mail that landed while it was spent
            bell = self._bells[sid] = {"rev": rev, "rung": False, "failures": 0}
        try:
            await self._type(sid, adapter, mail.unread_line(s.unread()))  # the lock is held already
        except Exception as e:  # noqa: BLE001 — `prompt-stuck`, or tmux refusing the paste: both a failed ring
            if s.id in self.sessions:
                self._bell_failed(s, bell, str(e) or type(e).__name__)
            return
        bell["rung"] = True
        if s.doorbell_failed:
            s.doorbell_failed = None
            self._save(s)
        await self._push_changes()

    def _bell_failed(self, s: Session, bell: dict[str, Any], error: str) -> None:
        bell["failures"] += 1
        log.warning("doorbell for %s did not submit (%d of %d): %s", s.id, bell["failures"], DOORBELL_TRIES, error)
        if bell["failures"] >= DOORBELL_TRIES:
            s.doorbell_failed = {"at": now_iso(), "error": error}
            self._save(s)

    def _decide_wake(self, s: Session, *, member_change: bool, via: str = "wait") -> dict[str, Any] | None:
        """The one wake decision (design §4.10), taken when `s` is reachable — blocked in `wait`
        here; step 7's doorbell calls it at a hook-confirmed `idle`. Looks at the unread mail past
        the `mail_decided` watermark:

        - none, or `s` is a person's session (never woken by mail) → None, nothing recorded;
        - a member's change is waking it anyway → a **free** wake: recorded `charged: False`,
          watermark advanced over all of it;
        - one of the undecided entries is a note the home marked `uncharged` — a `steer` of its own
          **lapsed** → a free wake, free in the same sense and for the same reason it is not a
          refill: it is the home's clock, not another session's message (design §4.10), so a spent
          budget cannot hold a sender past its own bound. The mark is on the note, so it survives a
          resume and a restart between the lapse and the moment the session is next reachable;
        - the budget holds → one unit spent however many entries it covers, `charged: True`,
          watermark advanced;
        - the budget is spent → None: the mail has landed, nothing wakes, **the watermark stays**,
          so the same mail is still undecided when the window refills.

        Returns the decision as recorded, plus the `entries` it covered."""
        if not mail.mail_wakes(s):
            return None
        fresh = mail.undecided_mail(s)
        if not fresh:
            return None
        now = datetime.now(UTC)
        free = member_change or any(e.uncharged for e in fresh)
        if not free and mail.wake_budget_spent(s, now):
            return None
        decision = {
            "at": now.isoformat(timespec="microseconds"),
            "cause": "member" if member_change else "mail",
            "charged": not free,
            "covered": len(fresh),
            "via": via,
        }
        s.wakes = (s.wakes + [decision])[-mail.WAKES_KEEP :]
        s.mail_decided = {"id": fresh[-1].id, "at": fresh[-1].at}
        self._save(s)
        return {**decision, "entries": fresh}

    def _refill(self, s: Session | None) -> None:
        """A person's act toward `s` restores its wake budget in full (design §4.10 "Time and a
        person restore it"): a send or keys with no caller, a person's message, a decided
        permission. Never a session's traffic, never a person looking (`seen`, a panel read)."""
        if s is None:
            return
        s.wake_refilled_at = datetime.now(UTC).isoformat(timespec="microseconds")
        self._save(s)
        self._poke_waits()

    async def rpc_hook(self, session: str, **event: Any) -> dict[str, Any] | None:
        """Called by an adapter's hook script. A `permission` event blocks until answered or timed out."""
        if event.get("kind") == "permission":
            return await self._await_permission(session, event)
        self._apply_event(session, event)
        await self._push_changes()
        return None

    async def rpc_usage_report(self, windows: Any = None, fresh: bool = False, caller: Any = None) -> dict[str, Any]:
        """What a session of the tool was told about its account's limits (§4.4 *Usage, reported
        first and asked for last*, TD-233 slice 2), from its adapter's hook command: `windows`
        `[{label, pct, resets}]` as the adapter's `usage_report` read them, `fresh` whether the
        session had a response since its last report. The caller is the session, as the envelope
        names it. Merged into its account's reading by what a window can do (`sessionorc.usage`),
        never by who spoke last; a report that is not fresh moves no age. Answers `{taken}`: false
        for a caller that is no live tool session here, a metered profile, or no windows — a
        report is never refused in words, since the status line that sends it prints nothing."""
        s = self.sessions.get(str(caller or ""))
        cleaned = usage_mod.clean_windows(windows)
        if s is None or (key := await self._usage_merge_report(s, cleaned, bool(fresh))) is None:
            return {"taken": False}
        self._forward_usage_report(s, key, cleaned, bool(fresh))
        return {"taken": True}

    async def _usage_merge_report(
        self, s: Session, cleaned: list[dict[str, Any]], fresh: bool, key: str | None = None
    ) -> str | None:
        """One session's report merged into its account's reading, this host's session or a node's
        (§4.4 *A node's sessions report to their node*, TD-233 slice 2), under `key`, the account
        a node keyed its session's profile by, or the one this host keys it by. The account key it
        merged under, or None for a session that is no live tool session, a metered profile, or
        no windows."""
        if s.adapter == "shell" or s.state in ("exited", "closed") or not cleaned:
            return None
        ad = adapters.get(s.adapter)
        # `_metered` is read from this host's profile files, and a node's profile of the same name
        # may be another login there: a node withholds its own metered profiles (the techlead's
        # read of #783)
        if not getattr(ad, "usage_for", None) or (self.sessions.get(s.id) is s and s.profile in self._metered):
            return None
        key = key or _usage_key(ad, s.adapter, s.profile)[0]
        # an account first met since a restart takes the reading its profile held, if it is of this
        # account — a node's included, so its history does not start again (the techlead's read of #783)
        self._usage_seed(key, [s.profile], strict=self.sessions.get(s.id) is not s)
        was = self._usage_acct.get(key)
        merged = usage_mod.merge(was, cleaned, at=now_iso(), source="reported", fresh=fresh, by=s.name)
        if merged != was:
            self._usage_acct[key] = merged
            await self._usage_spread(key)
            self._usage_limits(self._usage_live(), self._metered)
            await self._push_changes()
            await self._push_usage_readings()
        return key

    async def _await_permission(self, session: str, event: dict[str, Any]) -> dict[str, Any] | None:
        s = self.sessions.get(session)
        if s is None:
            return None
        self._last_hook[session] = datetime.now(UTC)
        self._live_hook_at[session] = time.time()
        wait = float(event.get("wait_seconds") or 600)
        deadline = (datetime.now(UTC) + timedelta(seconds=wait)).replace(microsecond=0)
        tool_use_id = event.get("tool_use_id") or f"{session}:{now_iso()}"
        pending = Pending(
            kind="permission",
            text=event.get("text", ""),
            deadline=deadline.isoformat().replace("+00:00", "Z"),
            tool_use_id=tool_use_id,
        )
        s.set_state("needs-you", confidence="hook", pending=pending)
        self.store.save(s)
        # Register the waiter BEFORE the first await: a client reacting to the push must find it.
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (session, tool_use_id)
        self._waiters[key] = fut
        await self._push_changes()
        try:
            return await asyncio.wait_for(fut, timeout=wait)
        except TimeoutError:
            # Fell through to the terminal dialog: the decision now lives there (design §4.2).
            s.set_state("needs-you", confidence="hook", pending=Pending(kind="question", text=pending.text))
            self.store.save(s)
            await self._push_changes()
            return None
        finally:
            self._waiters.pop(key, None)

    async def rpc_decide(
        self, id: str, tool_use_id: str, behavior: str, reason: str | None = None, caller: Any = None
    ) -> None:
        """Answer a pending permission through the hook (design §4.2). An act, gated like `send`
        (§4.8, TD-116): a session answers another's only as one of its controllers holding `control`."""
        s = self._get(id)
        fut = self._waiters.get((id, tool_use_id))
        if fut is None or fut.done():
            raise RpcError("no pending permission with that id (answered, timed out, or in the terminal)")
        if behavior not in ("allow", "deny"):
            raise RpcError("behavior must be allow or deny")
        fut.set_result({"behavior": behavior, "reason": reason})
        # the row ends here and the home knows how (design §4.10 *The Inbox is a queue*): the trail
        # says who answered — *you* for a person, the controller's name for a session — and a
        # person's answer is never too quick to record
        self._attention_ended(s.id, self._answered_by(behavior, caller))
        s.set_state("working", confidence="hook")
        if mail.is_person(caller):
            self._refill(s)  # a person's answer to its permission; a controller's refills nothing (§4.10)
        self.store.save(s)
        await self._push_changes()

    def _answered_by(self, behavior: str, caller: Any) -> str:
        """The trail's word for a decided permission: *allowed by you* when a person pressed it,
        *allowed by <name>* when a controller did (TD-116) — which is not *by you*, so a policy's
        200 ms answer stays under the trail's floor."""
        who = "you"
        if not mail.is_person(caller):
            # the one graph, so a controller on another host is named too, not shown as `id@host`
            rec = self._graph().get(self._addr(caller))
            who = rec.name if rec is not None else str(caller)
        return f"{'allowed' if behavior == 'allow' else 'denied'} by {who}"

    async def rpc_recent_dirs(self) -> list[str]:
        p = paths.recent_dirs_file()
        return p.read_text().splitlines() if p.is_file() else []

    async def rpc_repos(self) -> dict[str, dict[str, Any]]:
        """The repo facts per registered checkout (design §4.4 *Repo facts*, TD-176), keyed by the
        checkout's path: what the team card's Repo facet, the rollup and the Repo page draw, with
        `balance` beside a reading whose repo a team is over its line in (§6 *Balance*). The home's;
        a node forwards it (§4.4a)."""
        return {root: v for root in self._repos if (v := self._repo_view(root)) is not None}

    async def rpc_doing_log(self, team: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """The doing log (design §4.8 *the doing log*, TD-176 slice 2): per team, its last fifty
        `ao doing` calls, oldest first — `{team, id, text, at}` each; one team's with `team`. The
        home's; a node forwards it (§4.4a). A read, and not the `doing` RPC, which writes the line."""
        rings = self.doing_log.rings
        if team is not None:
            return {team: list(rings.get(team, []))}
        return {t: list(r) for t, r in rings.items()}

    async def rpc_usage(self) -> dict[str, dict[str, Any]]:
        """Last known usage per profile (TD-001): what the top bar shows."""
        return dict(self._usage)

    async def rpc_gate(self) -> dict[str, Any]:
        """The usage gate as it stands (design §4.7 `ao gate`, §6, TD-100): every profile with a
        reserve in this host's `settings.yml`, each reserve, and the line it makes now against the
        profile's last reading — `{profiles: {profile: {reserves, windows, labels}}}`, `labels`
        being what the adapter reported (empty with no reading yet). A read: never gated.

        A profile with unattended sessions live is read as the gate reads it (`_gate_windows`): a
        window past `usage.max_age` carries its projection (`projected`) or `unknown: "rate"`, and
        `max_age` is the setting as kept (TD-233 slice 4). An idle profile's rows are its reading."""
        now = datetime.now(UTC)
        whole = settings_mod.load()
        doc = settings_mod.reserves(whole)
        watched = {
            s.profile
            for s in self.sessions.values()
            if s.unattended and s.state not in ("exited", "closed", "scheduled")
        }
        out: dict[str, Any] = {}
        for prof, by_label in sorted(doc.items()):
            windows = (self._usage.get(prof) or {}).get("windows")
            if prof in watched:
                windows = self._gate_windows(prof, now, whole)
            out[prof] = {
                "reserves": by_label,
                "windows": settings_mod.lines(by_label, windows, now),
                "labels": [str(w.get("label")) for w in windows or []],
            }
        # a metered profile's amounts (§6 *Usage gate*, TD-151 slice 5), as written, with the spend
        for prof, by_label in sorted(settings_mod.amounts(whole).items()):
            written = ((whole.get("usage_gate") or {}).get(prof)) or {}
            mine = {label: written[label] for label in by_label if label in written}
            out[prof] = {
                "reserves": mine,
                "windows": self._amount_rows(prof, mine),
                "labels": list(spend_mod.LABELS),
                "metered": True,
            }
        max_age = settings_mod.usage(whole).get("max_age", settings_mod.MAX_AGE_DEFAULT)
        return {"profiles": out, "file": str(settings_mod.settings_file()), "max_age": max_age}

    async def rpc_settings(self, caller: Any = None) -> dict[str, Any]:
        """`ao settings` and the Settings page's read (design §5, §4.7, TD-146): the home's
        `settings.yml`, each key as its reader keeps it, with what it makes today — each profile's
        lines against its last reading (as `gate` answers), each team's stop time with whether it
        has passed and its reserve priority. `migrate` names a `ui.yml` still on disk, which is no
        longer read. **A person's own**, refused to a session as `set_settings` is: `person:` is
        theirs, and what a session needs of the gate `gate` answers."""
        if not mail.is_person(caller):
            raise RpcError("settings is a person's own: refused to a session (design §5 settings.yml)")
        doc, now = settings_mod.load(), datetime.now(UTC)
        gate = (await self.rpc_gate())["profiles"]
        teams = {
            name: {**t, **({"passed": _parse(t["until"]) <= now} if "until" in t else {})}
            for name, t in settings_mod.teams(doc).items()
        }
        out = {
            "file": str(settings_mod.settings_file()),
            "usage_gate": gate,
            "teams": teams,
            "repos": settings_mod.repos(doc),
            "usage": {"max_age": settings_mod.usage(doc).get("max_age", settings_mod.MAX_AGE_DEFAULT)},
            "person": settings_mod.person(doc),
            "migrate": [],
        }
        if settings_mod.ui_yml().exists():
            out["migrate"].append(
                f"{settings_mod.ui_yml()}: ui.yml is no longer read — its open_in lives under person:"
            )
        return out

    async def rpc_set_settings(
        self,
        profile: str = "",
        reserves: dict[str, Any] | None = None,
        teams: dict[str, Any] | None = None,
        repos: dict[str, Any] | None = None,
        person: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        caller: Any = None,
    ) -> dict[str, Any]:
        """Write the home's `settings.yml` (design §5, §4.7 `ao gate` / `ao team until` / `ao team
        reserve`, the Settings page; TD-100, TD-146): any subset of its keys, each validated before
        anything is written, then the whole file rewritten. **A person's own**, refused to a session
        as `inbox_pause` is. Takes effect on the next tick. At the home alone: a node forwards it while
        its link is up and refuses it offline (`modes.HOME_EDITS`), and every linked node is sent
        the file after the write (§4.4a *Settings, replicated*, TD-147).

        - `profile` + `reserves`: a window label to a flat percent, to `{per_day: N}`, or to None,
          which clears that window's reserve. A label the profile's adapter does not report is
          refused, with the reported ones named — except before the profile has any reading, when
          nothing can be checked and the reply says `unchecked`.
        - `teams`: `{team: {schedule?, until?, reserve?, balance?, on_work?} | None}` — a field set to
          None is cleared, a team set to None removed. The team's name is the client's to check
          against the org's definitions; the agent takes the key. A stop time already past is refused, as `ao until`'s.
        - `repos`: `{repo: {promote: {auto: bool}} | None}`.
        - `person`: `{open_in?, terminal?: {size?, face?, copy_on_select?}, inbox?: {board_show?}}`, a None
          clearing that key (or that field of terminal or inbox).
        - `usage`: `{max_age: "1h" | "90m" | "off" | None}` (§6 *A reading the gate can no longer
          trust*, TD-233), None clearing it back to the default hour."""
        if not mail.is_person(caller):
            raise RpcError("set_settings is a person's own: refused to a session (design §5 settings.yml)")
        if reserves is None and teams is None and repos is None and person is None and usage is None:
            raise RpcError("set_settings needs reserves, teams, repos, person or usage (design §5 settings.yml)")
        doc = settings_mod.load()
        before = copy.deepcopy(doc)
        before_teams = settings_mod.teams(doc)
        out: dict[str, Any] = {}
        if reserves is not None:
            out.update(self._reserves_change(doc, str(profile or ""), reserves))
        now = datetime.now(UTC)
        for key, value, parse, known in (
            ("teams", teams, settings_mod.parse_team, settings_mod.TEAM_KEYS),
            ("repos", repos, settings_mod.parse_repo, ("promote",)),
        ):
            if value is None:
                continue
            if not isinstance(value, dict) or not value:
                raise RpcError(f"set_settings: {key} is a mapping of names (design §5 settings.yml)")
            change: dict[str, Any] = {}
            for name, fields in value.items():
                if not str(name or "").strip():
                    raise RpcError(f"set_settings: {key} needs a name")
                if fields is None:
                    change[str(name)] = None
                    continue
                if not isinstance(fields, dict):
                    raise RpcError(f"{key}.{name}: a mapping of fields, or null to remove it")
                if unknown := sorted(set(map(str, fields)) - set(known)):
                    # a clear of a mistyped key is refused too: a typo is never a silent no-op
                    raise RpcError(f"{key}.{name}: unknown key {', '.join(unknown)} (known: {', '.join(known)})")
                kept = {k: v for k, v in fields.items() if v is not None}
                try:
                    parsed = parse(kept)
                except ValueError as e:
                    raise RpcError(f"{key}.{name}: {e}") from None
                if "until" in parsed and _parse(parsed["until"]) <= now:
                    raise RpcError(f"{key}.{name}: until {parsed['until']} has passed — a stop time is ahead (§6)")
                change[str(name)] = {**{k: None for k in fields if fields[k] is None}, **parsed}
            doc[key] = settings_mod.merge(doc.get(key), change)
            out[key] = getattr(settings_mod, key)(doc)
        if person is not None:
            doc["person"] = self._person_change(doc.get("person"), person)
            out["person"] = settings_mod.person(doc)
        if usage is not None:
            if not isinstance(usage, dict) or not usage:
                raise RpcError("set_settings: usage is {max_age: 1h | 90m | off | null} (design §5 settings.yml)")
            if unknown := sorted(set(map(str, usage)) - set(settings_mod.USAGE_KEYS)):
                raise RpcError(f"usage: unknown key {', '.join(unknown)} (known: {', '.join(settings_mod.USAGE_KEYS)})")
            kept = dict(doc["usage"]) if isinstance(doc.get("usage"), dict) else {}
            if usage["max_age"] is None:
                kept.pop("max_age", None)
            else:
                try:
                    kept["max_age"] = settings_mod.parse_max_age(usage["max_age"])
                except ValueError as e:
                    raise RpcError(f"usage.max_age: {e}") from None
            doc["usage"] = kept
            out["usage"] = {"max_age": settings_mod.usage(doc).get("max_age", settings_mod.MAX_AGE_DEFAULT)}
        for key in ("teams", "repos", "person", "usage"):
            if key in doc and not doc[key]:
                doc.pop(key)
        settings_mod.save(doc)
        if teams is not None:
            after = settings_mod.teams(doc)
            for name in teams:
                await self._restamp_team(
                    str(name),
                    (before_teams.get(str(name)) or {}).get("until"),
                    (after.get(str(name)) or {}).get("until"),
                )
        held = [k for k in ("usage_gate", "usage", "teams", "repos", "person") if k in doc]
        log.info("settings.yml written; it holds %s", ", ".join(held) or "nothing")
        await self._push_settings()
        # detached, as the tick's hand-edit commit is: a wedged git must not hold Save up to its
        # timeouts, and the write it follows has already happened (the techlead's read of #800)
        task = asyncio.create_task(self._commit_defs(defs.settings_message(before, doc), ("settings.yml",)))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return out

    async def rpc_commit_defs(self, message: str = "", caller: Any = None) -> dict[str, Any]:
        """Commit the home's definition files with the act's words (design §4.9 *What is left at the
        home has a history*, TD-229 slice 5): a client calls it after it wrote `org.yml` itself —
        **Members…**'s `edit_members` — as `org: ao-grind +grinder-ao-3`. A person's own, and the
        home's alone (`modes.HOME_EDITS`). `{committed}`: false when nothing changed, the home is no
        work tree, or git failed (logged; the write it follows stands)."""
        if not mail.is_person(caller):
            raise RpcError("commit_defs is a person's own: refused to a session (design §4.9)")
        if self.mode != "home":
            raise RpcError("commit_defs runs at the home (design §4.9): this host is a node")
        message = " ".join(str(message or "").split())
        if not message:
            raise RpcError("commit_defs needs the act's words as its message")
        return {"committed": await self._commit_defs(message[:200])}

    async def _commit_defs(self, message: str, files: tuple[str, ...] = defs.TRACKED) -> bool:
        """The home's one committer (§4.9): at the home only, one git at a time, in a thread; never
        raises, since the write it follows has already happened."""
        if self.mode != "home":
            return False  # a node's replica of `settings.yml` is not tracked
        try:
            async with self._defs_lock:
                return await asyncio.to_thread(defs.commit, message, files=files)
        except Exception:  # noqa: BLE001 — a history that fails is a log line, never the write's failure
            log.exception("committing the home's definitions failed")
            return False

    async def rpc_clear_work(self, team: str = "", caller: Any = None) -> dict[str, Any]:
        """Dismiss's half of the **Inbox row: team start** (design §6 rule 8, §4.5a, TD-227): the ids
        of the team's `work_waiting` are added to each named member's `lane_seen`, so those entries
        do not ask again and a later one does, and the mark is removed. A person's own, refused to a
        session as `set_settings` is, and the home's alone (`modes.HOME_EDITS`). `{team, cleared,
        ids}`: `cleared` false when no work was waiting."""
        if not mail.is_person(caller):
            raise RpcError("clear_work is a person's own: refused to a session (design §6 rule 8)")
        if self.mode != "home":
            raise RpcError("clear_work runs at the home (design §6 rule 8): this host is a node")
        team = str(team or "").strip()
        if not team:
            raise RpcError("clear_work needs the team whose work to dismiss")
        teams = self._host_rec.get("teams") or {}
        rec = teams.get(team) or {}
        mark = rec.get("work_waiting")
        if not isinstance(mark, dict):
            return {"team": team, "cleared": False, "ids": []}
        named = mark.get("members") if isinstance(mark.get("members"), dict) else {}
        for r in self._graph().values():
            ids = named.get(r.name)
            if r.team != team or r.superseded_by or r.lane_seen is None or not isinstance(ids, list):
                continue
            held = list(r.lane_seen.get("ids") or [])
            if add := [str(i) for i in ids if str(i) not in held]:
                r.lane_seen = {"at": now_iso(), "ids": [*held, *add]}
                self._save(r)
        rec.pop("work_waiting", None)
        if not rec:
            teams.pop(team, None)
        try:
            self.host_store.save(self._host_rec)
        except OSError:
            log.exception("writing the home's host record failed")
        await self._push_changes()
        ids = sorted({str(i) for v in named.values() if isinstance(v, list) for i in v})
        log.info("rule 8: %s's work waiting dismissed by the person: %s", team, ", ".join(ids))
        return {"team": team, "cleared": True, "ids": ids}

    def _reserves_change(self, doc: dict[str, Any], prof: str, reserves: Any) -> dict[str, Any]:
        """`set_settings`'s usage-gate half, laid onto `doc` in place (the caller writes the file)."""
        if not isinstance(reserves, dict) or not reserves:
            raise RpcError("set_settings needs reserves: {label: percent | {per_day: N} | null}")
        billing = self._billing_of(prof)
        if billing is not None:
            return self._amounts_change(doc, prof, reserves, billing.get("prices") or {})
        windows = (self._usage.get(prof) or {}).get("windows")
        reported = [str(w.get("label")) for w in windows or []]
        if windows is not None and (unknown := sorted(set(map(str, reserves)) - set(reported))):
            raise RpcError(
                f"profile {prof or '(default)'} reports no window {', '.join(unknown)}: "
                f"its windows are {', '.join(reported) or 'none'} (design §4.7)"
            )
        parsed: dict[str, Any] = {}
        for label, value in reserves.items():
            if isinstance(value, str):
                raise RpcError(
                    f"{label}: profile {prof or '(default)'} is billed by subscription, so its reserve is a percent "
                    f"(30, 10/day), not an amount like {value!r} (design §6 Usage gate)"
                )
            try:
                parsed[str(label)] = None if value is None else settings_mod.parse_reserve(value)
            except ValueError as e:
                raise RpcError(f"{label}: {e}") from None
        self._lay_gate(doc, prof, parsed)
        mine = (doc["usage_gate"] or {}).get(prof) or {}
        log.info("usage gate for profile %s set to %s", prof or "(default)", mine or "no reserves")
        return {
            "profile": prof,
            "reserves": mine,
            "windows": settings_mod.lines(mine, windows, datetime.now(UTC)),
            "unchecked": windows is None,
        }

    def _amounts_change(
        self, doc: dict[str, Any], prof: str, reserves: dict[str, Any], prices: dict[str, Any]
    ) -> dict[str, Any]:
        """A metered profile's half (§6 *Usage gate*, TD-151 slice 5): an amount per window of the
        home's three — `$5` where the profile has prices, `2M tok` either way — or None to clear it;
        a percent refused by naming the billing, as is money on a profile with no prices, which would
        make no line."""
        who = prof or "(default)"
        if unknown := sorted(set(map(str, reserves)) - set(spend_mod.LABELS)):
            raise RpcError(
                f"profile {who} is metered: its windows are {', '.join(spend_mod.LABELS)}, "
                f"not {', '.join(unknown)} (design §4.2a)"
            )
        parsed: dict[str, Any] = {}
        for label, value in reserves.items():
            if value is None:
                parsed[str(label)] = None
                continue
            if not isinstance(value, str):
                raise RpcError(
                    f"{label}: profile {who} is metered, so its reserve is an amount ($5, 20M tok), "
                    f"not a percent like {value!r} (design §6 Usage gate)"
                )
            try:
                amount = settings_mod.parse_amount(value)
            except ValueError as e:
                raise RpcError(f"{label}: {e}") from None
            if amount["unit"] == "$" and not prices:
                raise RpcError(
                    f"{label}: profile {who} declares no prices, so its spend is tokens: an amount like 20M tok, "
                    f"not {value!r} (design §4.2a)"
                )
            parsed[str(label)] = value.strip()
        self._lay_gate(doc, prof, parsed)
        mine = (doc["usage_gate"] or {}).get(prof) or {}
        log.info("usage gate for metered profile %s set to %s", who, mine or "no amounts")
        return {"profile": prof, "reserves": mine, "windows": self._amount_rows(prof, mine), "metered": True}

    def _amount_rows(self, prof: str, mine: dict[str, Any]) -> list[dict[str, Any]]:
        """A metered profile's rows for `gate` and `set_settings`' reply: each window with an amount,
        its amount as written, and the account's spend and `pct` from the last reading — `unread`
        before there is one. The `pct` is the reading's, made at the amount it last read."""
        windows = {str(w.get("label")): w for w in (self._usage.get(prof) or {}).get("windows") or ()}
        rows = []
        for label in spend_mod.LABELS:
            if label not in mine:
                continue
            w = windows.get(label)
            row: dict[str, Any] = {"label": label, "reserve": mine[label], "metered": True}
            if w is None:
                row["unread"] = True
            else:
                row |= {"spent": w.get("spent"), "pct": w.get("pct"), "resets": w.get("resets")}
            rows.append(row)
        return rows

    def _lay_gate(self, doc: dict[str, Any], prof: str, parsed: dict[str, Any]) -> None:
        """`parsed` laid onto `doc`'s `usage_gate.<prof>` in place: None clears a window, a profile
        with none left leaves the key."""
        gate = doc.get("usage_gate") if isinstance(doc.get("usage_gate"), dict) else {}
        mine = dict(gate.get(prof) or {}) if isinstance(gate.get(prof), dict) else {}
        for label, value in parsed.items():
            if value is None:
                mine.pop(label, None)
            else:
                mine[label] = value
        if mine:
            gate[prof] = mine
        else:
            gate.pop(prof, None)
        doc["usage_gate"] = gate

    @staticmethod
    def _person_change(current: Any, change: Any) -> dict[str, Any]:
        """`person:` with `change` laid over it: a key set to None cleared; `terminal` and `inbox` merged
        field by field, a field set to None cleared. Validated whole before it is returned."""
        known = settings_mod.PERSON_KEYS
        nested = {"terminal": settings_mod.TERMINAL_KEYS, "inbox": settings_mod.INBOX_KEYS}
        if not isinstance(change, dict) or not change:
            raise RpcError(f"set_settings: person is a mapping of {', '.join(known)} (design §5)")
        if unknown := sorted(set(map(str, change)) - set(known)):
            raise RpcError(f"person: unknown key {', '.join(unknown)} (known: {', '.join(known)})")
        for key, keys in nested.items():
            sub = change.get(key)
            if isinstance(sub, dict) and (bad := sorted(set(map(str, sub)) - set(keys))):
                raise RpcError(f"person.{key}: unknown key {', '.join(bad)} (known: {', '.join(keys)})")
        out = dict(current) if isinstance(current, dict) else {}
        for key, value in change.items():
            if value is None:
                out.pop(key, None)
            elif key in nested and isinstance(value, dict):
                sub = dict(out.get(key) or {}) if isinstance(out.get(key), dict) else {}
                for f, v in value.items():
                    if v is None:
                        sub.pop(f, None)
                    else:
                        sub[f] = v
                out[key] = sub
                if not sub:
                    out.pop(key)
            else:
                out[key] = value
        try:
            return settings_mod.parse_person(out)  # as parsed: `07d` is written `7d`
        except ValueError as e:
            raise RpcError(f"person: {e}") from None

    async def rpc_adapters(self) -> list[str]:
        return adapters.names()

    async def rpc_ping(self) -> str:
        return "pong"

    async def rpc_host(self) -> dict[str, Any]:
        """Who this host agent is in the org (design §4.4a): its host, its home, its mode, and
        whether the home can be reached — which a client on a node needs before it labels what it
        shows *offline*; which build it runs and since when (§4.4, TD-062); and, at the home, the
        promote's readings per repo (`promotes`, §6 *Promote*) and each wound-down team's
        `work_waiting` as the `host` record holds it (`work: {<team>: mark}`, §6 rule 8), which is
        what draws the Inbox's team start row and the card's note."""
        out = {"host": self.host, "home": self.home, "mode": self.mode, "home_reachable": self.home_reachable()}
        out["built_from"], out["started_at"] = dict(self.build), self.started_at
        if self.mode == "home":
            out["links"] = {h: dict(v) for h, v in sorted(self.links.items())}
            out["promotes"] = self._promotes_view()
            out["work"] = {
                team: dict(rec["work_waiting"])
                for team, rec in sorted((self._host_rec.get("teams") or {}).items())
                if isinstance(rec, dict) and isinstance(rec.get("work_waiting"), dict)
            }
        else:
            out["link"] = dict(self.home_link)
        return out
