"""Waking and the doorbell (TD-108 step 1): design §4.8 *Waking a manager* and §4.10 *The host agent decides
each wake* and *How a Claude Code session is told it has mail*, as a mixin `HostAgent` inherits — `rpc_wait`
to `_refill`, and nothing else since TD-317 slice 2 moved its passengers out: the hook's entry to
`agent_hook.py`, settings and usage to `agent_settings.py`, the Inbox rows' Dismiss to `agent_inbox.py`,
the Restart and the small reads to `agent.py`. Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from datetime import UTC, datetime
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    mail,
    waits,
)
from sessionorc.agent_common import (
    COMPOSER_LINES,
    DOORBELL_TRIES,
    FIRST_PROMPT_TRIES,
    LeadTyped,
    RpcError,
    _parse,
    _Wait,
    log,
)
from sessionorc.models import (
    SYSTEM,
    MailEntry,
    SendEntry,
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

    def _bell_blocked(self, s: Session, now: datetime) -> str | None:
        """Why the doorbell may not ring for `s` now, or None when it may. The order is the
        design's: a session that cannot be rung at all, then a pending stop, then — in `_ring`, the
        wake decision — the budget and new mail."""
        if s.state != "idle" or s.confidence != "hook":
            return "not hook-confirmed idle"  # never a scraped idle or `stalled?` (a takeover)
        if self._first_prompt_due(s):
            return "its brief is not typed yet"  # the first prompt is the brief, never the doorbell's line
        if not mail.mail_wakes(s):
            return "a person's session"  # invariant 5: the chip and the line, nothing typed
        if s.doorbell_held:
            return "holding its mail unread"  # rung and answered with nothing read, DOORBELL_HELD times (TD-347)
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
            if await self._cache_restart(s, datetime.now(UTC)):
                # a lapsed cache (§4.10, TD-467): the restart is the ring — the wake decided and charged, one
                # per stretch, nothing typed and no ring to judge at a Stop; the new run reads its mail first
                bell["rung"] = True
                await self._push_changes()
                return
        # judged at its turn's Stop (TD-347); set before typing, since the tool's UserPromptSubmit can
        # land before the submit confirmation does
        self._rang[sid] = {"count": s.unread(), "turned": False}
        try:
            await self._type(sid, adapter, mail.unread_line(s.unread()))  # the lock is held already
        except Exception as e:  # noqa: BLE001 — `prompt-stuck`, or tmux refusing the paste: both a failed ring
            self._rang.pop(sid, None)
            if s.id in self.sessions:
                self._bell_failed(s, bell, str(e) or type(e).__name__)
            return
        bell["rung"] = True
        if s.doorbell_failed:
            s.doorbell_failed = None
            self._save(s)
        await self._push_changes()

    # -- the brief typed at the composer (design §4.1 *No prose in the argv*, TD-339) ------------------

    @staticmethod
    def _first_prompt_due(s: Session) -> bool:
        """The record holds a brief that is neither sent nor given up on."""
        return bool(s.first_prompt) and not s.first_prompt_sent_at and not s.first_prompt_error

    def _first_prompt_road(self, s: Session, now: datetime) -> str | None:
        """How the brief may be sent now: `hook` at a hook-reported `idle` — the composer the launch
        landed at — or, once, `scraped` (§4.1 *A brief whose first hook is lost*, TD-348): no hook
        since the launch, `FIRST_PROMPT_HOOK_WAIT` passed since `created`, a scraped `idle`, and no try
        made yet. None otherwise."""
        if s.state != "idle" or s.superseded_by:
            return None
        if s.confidence == "hook" and s.id in self._last_hook:
            return "hook"
        if s.id in self._last_hook or s.first_prompt_tries:
            return None
        age = (now - _parse(s.created)).total_seconds()
        return "scraped" if age >= agent_common.FIRST_PROMPT_HOOK_WAIT else None

    def _send_first_prompts(self) -> None:
        """Each tick, on the host that holds the pane: start one send of the brief for every record
        whose first prompt is due and that may be sent now (`_first_prompt_road`). One task per
        session, as a ring is: a submit takes seconds and the tick never waits. A record no hook has
        reached by `FIRST_PROMPT_BOUND` after its launch, the brief unsent, is marked *no hook since
        launch* (§4.1, TD-348) — a hook arriving later clears nothing by itself."""
        now = datetime.now(UTC)
        for s in list(self.sessions.values()):
            if s.id in self._prompting or not self._first_prompt_due(s):
                continue
            if (
                s.id not in self._last_hook
                and not s.superseded_by
                and (now - _parse(s.created)).total_seconds() >= agent_common.FIRST_PROMPT_BOUND
            ):
                s.first_prompt_error = agent_common.FIRST_PROMPT_NO_HOOK
                log.warning("the brief of %s was not sent: no hook since its launch", s.id)
                self._save(s)
                continue
            if self._first_prompt_road(s, now) is None:
                continue
            self._prompting[s.id] = asyncio.create_task(self._first_prompt(s.id))

    async def _first_prompt(self, sid: str) -> None:
        try:
            typing = self._typing[sid]
            if typing.locked():
                return  # someone is typing into the pane: the next tick looks again
            async with typing:
                await self._first_prompt_typing(sid)
        except Exception:  # noqa: BLE001 — a detached task: a send that breaks is a log line
            log.exception("the first prompt of %s failed", sid)
        finally:
            self._prompting.pop(sid, None)

    async def _first_prompt_typing(self, sid: str) -> None:
        """One try, under the pane's typing lock. The composer must read empty — a dialog (None) or
        someone's words wait for the next tick, uncounted — unless an earlier try left the brief
        there, when only Enter is pressed again (the text is never typed twice, §4.2). A try the
        composer does not take is counted; the `FIRST_PROMPT_TRIES`th writes `first_prompt_error`."""
        s = self.sessions.get(sid)
        if s is None or not self._first_prompt_due(s):
            return
        road = self._first_prompt_road(s, datetime.now(UTC))
        if road is None:
            return
        adapter = adapters.get(s.adapter)
        reader = getattr(adapter, "composer", None)
        rev = s.rev
        left = None
        if reader is not None:
            tail = await asyncio.to_thread(self.tmux.capture_tail, sid, COMPOSER_LINES, raw=True)
            if not tail:
                return  # a failed capture is no evidence of an empty composer: never a second paste on it
            left = reader(tail)
            if self.sessions.get(sid) is not s or s.rev != rev or not self._first_prompt_due(s):
                return  # it moved while the screen was read: the next tick looks again
            if left is None or (left and not s.first_prompt_tries):
                return  # a dialog, or someone's words: wait for the next tick
        elif road == "scraped":
            return  # a scraped idle is trusted only with a composer that reads empty (§4.1, TD-348)
        try:
            if left and sid in self._lead_typed:
                # the home's line went in and the paste was refused: the brief goes after it, never an
                # Enter on the line alone, which would submit no brief and mark it sent. Known from the
                # failed step, not read back: the tool paints a long line over two rows (review of PR #1158)
                await self._type(sid, adapter, s.first_prompt, lead_in=True)
            elif left:
                await self._enter_again(sid, reader)  # the last try's brief is still in the composer
            else:
                # the home's line, then the brief as the paste: one prompt (§4.1, TD-347); the lock is held already
                await self._type(sid, adapter, s.first_prompt, lead=mail.BRIEF_LINE)
        except Exception as e:  # noqa: BLE001 — `prompt-stuck`, or tmux refusing the paste
            if self.sessions.get(sid) is not s:
                return  # forgotten, or its name taken by a new record, while it typed
            if isinstance(e, LeadTyped):
                self._lead_typed.add(sid)  # the line alone is in the composer
            else:
                self._lead_typed.discard(sid)  # the paste landed: what is left holds the brief, or nothing
            s.first_prompt_tries += 1
            why = str(e) or type(e).__name__
            log.warning(
                "the brief of %s did not submit (%d of %d): %s", sid, s.first_prompt_tries, FIRST_PROMPT_TRIES, why
            )
            if s.first_prompt_tries >= FIRST_PROMPT_TRIES:
                s.first_prompt_error = why
            self._save(s)
            await self._push_changes()
            return
        self._lead_typed.discard(sid)
        if self.sessions.get(sid) is not s:
            return  # forgotten, or its name taken by a new record, while it typed
        s.first_prompt_sent_at = now_iso()
        s.first_prompt = None  # kept until sent (§4.1); the transcript holds it from here
        # what was typed, and by whom (§4.10 `sends`): the home, the brief — named, never its text again
        sent = SendEntry(
            id="s-" + secrets.token_hex(6),
            from_=SYSTEM,
            at=s.first_prompt_sent_at,
            text="(the brief)",
            scraped=road == "scraped",
        )
        s.sends = (s.sends + [sent])[-mail.SENDS_KEEP :]
        self._save(s)
        await self._push_changes()

    async def _enter_again(self, sid: str, reader: Any) -> None:
        """Enter and C-m on a composer still holding an earlier try's brief, confirmed as `_type`
        confirms its own (TD-027); `prompt-stuck` when it still holds it."""
        for key in ("Enter", "C-m"):
            await asyncio.to_thread(self.tmux.send_key, sid, key)
            if await self._poll(lambda: _empty(reader, self.tmux, sid), agent_common.SUBMIT_SECONDS):
                return
        raise RpcError(f"prompt-stuck: {sid} still shows its brief in the composer after Enter and C-m")

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

    def _bell_answered(self, s: Session, event: dict[str, Any]) -> None:
        """A rung session's hook, judged against the ring (design §4.10 *A ring is answered by a read,
        or the bell stops*, TD-347). The ring's turn starts with its prompt (`UserPromptSubmit`); at
        that turn's `Stop` — the next hook `idle` — an unread count no lower than the ring's is one
        more unread ring, a lower one ends the run. `DOORBELL_HELD` in a row set `doorbell_held`."""
        rang = self._rang.get(s.id)
        if rang is None:
            return
        if event.get("prompt"):
            rang["turned"] = True
            return
        if event.get("state") != "idle" or not rang["turned"]:
            return
        del self._rang[s.id]
        if s.unread() < rang["count"]:
            self._unread_rings.pop(s.id, None)  # it read: the run ends
            return
        run = self._unread_rings[s.id] = self._unread_rings.get(s.id, 0) + 1
        if run >= mail.DOORBELL_HELD and not s.doorbell_held:
            s.doorbell_held = {"at": now_iso(), "rings": run}
            log.info("%s: doorbell held after %d rings answered with nothing read", s.id, run)

    def _bell_cleared(self, s: Session) -> bool:
        """A read, or a person's act toward it, ends the run and lifts `doorbell_held` (TD-347).
        True when the record changed, for the caller to save."""
        self._rang.pop(s.id, None)
        self._unread_rings.pop(s.id, None)
        if not s.doorbell_held:
            return False
        s.doorbell_held = None
        return True

    def _refill(self, s: Session | None) -> None:
        """A person's act toward `s` restores its wake budget in full (design §4.10 "Time and a
        person restore it"): a send or keys with no caller, a person's message, a decided
        permission. Never a session's traffic, never a person looking (`seen`, a panel read). It
        lifts a held doorbell too (TD-347)."""
        if s is None:
            return
        self._bell_cleared(s)
        s.wake_refilled_at = datetime.now(UTC).isoformat(timespec="microseconds")
        self._save(s)
        self._poke_waits()


async def _empty(reader: Any, tmux: Any, sid: str) -> bool:
    return not reader(await asyncio.to_thread(tmux.capture_tail, sid, COMPOSER_LINES, raw=True))
