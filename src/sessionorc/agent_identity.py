"""Who is calling (TD-108 step 1): the host agent's half of design §4.8a — the channel that decides, the
alarms, the tally, the pane-server recheck — as a mixin `HostAgent` inherits. Moved as written; the state
it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import Counter
from typing import Any

from sessionorc import (
    agent_common,
    identity,
    mail,
    naming,
)
from sessionorc.agent_common import (
    RpcError,
    _alarm_report,
    _alarm_words,
    log,
)
from sessionorc.models import (
    PERSON,
    now_iso,
)
from sessionorc.tmux import PaneInfo


class IdentityMixin:
    # -- who is calling (design §4.8a, TD-077) -----------------------------------------------------

    def _id_note_panes(self, panes: dict[str, PaneInfo]) -> None:
        """The live panes of this host's records, as the classification reads them. On the loop,
        from a pane list a thread already took."""
        was = self._id_panes
        live = {sid: p for sid, p in panes.items() if not p.dead and sid in self.sessions}
        # each pane's own scope (TD-360), read once per pane pid: a pane's cgroup does not move
        if self._id_own_cg is None:
            self._id_own_cg = self.proc.cgroup(os.getpid()) or ""
        # the server's panes that are no record's are read too: a scope of theirs is ours to call
        # *unknown*, never the person's (TD-362)
        others = [p for sid, p in panes.items() if not p.dead and sid not in self.sessions]
        scopes = {
            p.pane_pid: self._id_scopes[p.pane_pid]
            if p.pane_pid in self._id_scopes
            else identity.own_scope(p.pane_pid, self.proc, {self._id_own_cg} - {""})
            for p in [*live.values(), *others]
        }
        self._id_scopes = scopes
        counts = Counter(scopes.values())
        shared = {cg for cg, n in counts.items() if cg and n > 1}  # a cgroup two panes share is neither's own
        self._id_other_scopes = {scopes[p.pane_pid] for p in others} - shared - {""}
        self._id_panes = [
            identity.Pane(
                sid,
                p.pane_pid,
                identity.tty_nr_of(p.tty) if p.tty else 0,
                "" if scopes[p.pane_pid] in shared else scopes[p.pane_pid],
            )
            for sid, p in live.items()
        ]
        now = self._id_listed_at = time.monotonic()
        # A pane is gone when its record lists no pane **or another one**: a restart or supersede
        # reuses the session's name, so the record never leaves the list and only its pane pid
        # moves — and the old run's last hook then matched neither pane (TD-225).
        listed = {p.session: p.pid for p in self._id_panes}
        for p in was:
            if listed.get(p.session) != p.pid:
                self._id_gone[p.session] = (p, now)
        self._id_gone = {
            sid: (p, at)
            for sid, (p, at) in self._id_gone.items()
            if listed.get(sid) != p.pid and now - at < identity.PANE_GONE_GRACE
        }

    def _id_pane_replaced(self, session: str) -> None:
        """A restart or supersede under the same name has just killed the record's pane: the pane
        is gone **now**, not at the next list (TD-341). Left listed, the record read as one whose
        pane is known, so the new run's first hook — from a pane no list had shown yet — was judged
        *outside* without waiting for a fresh list, refused, and its `idle` never arrived; and the
        old run's last hook matched a pane the list still held. Moved to the gone table, the old
        pane is under the grace, and the record has a pane the last list did not show."""
        old = next((p for p in self._id_panes if p.session == session), None)
        if old is None:
            return
        self._id_panes = [p for p in self._id_panes if p.session != session]
        self._id_gone[session] = (old, time.monotonic())

    def _id_gone_channel(self, peer: int, session: str | None) -> identity.Channel | None:
        """A `hook` that matched no live pane, against the pane of the record it names if that pane
        left the list inside the grace (§4.8a *A hook just after its pane ended*, TD-115)."""
        gone = self._id_gone.get(session) if session else None
        if gone is None or time.monotonic() - gone[1] >= identity.PANE_GONE_GRACE:
            return None
        return identity.classify_gone(peer, gone[0], self.proc)

    def _id_log_late_hook(self, peer: int, session: str) -> None:
        """What a hook that matched no pane of the record it names looked like (TD-225): the peer's
        pid and start, the record's listed pane, and its gone one with its age — so an alarm says
        whether it fell outside the grace, at a pane that never left the list, or from a process
        that was never that session's."""
        st = self.proc.stat(peer)
        now_pane = next((p.pid for p in self._id_panes if p.session == session), None)
        gone = self._id_gone.get(session)
        log.info(
            "hook for %s matched no pane: peer %s (start %s, sid %s), listed pane %s, gone pane %s",
            session,
            peer,
            st.start if st else "?",
            st.sid if st else "?",
            now_pane,
            f"{gone[0].pid} {time.monotonic() - gone[1]:.1f}s ago" if gone else None,
        )

    async def _id_read_detached(self, tmux_pid: int | None) -> None:
        """Compute the detached-process check against the tmux server now running. `None` while
        there is no server, which is *not yet known* rather than *off*: the next connection asks
        again."""
        self._id_tmux, self._id_rechecked = self._id_server(tmux_pid), time.monotonic()
        self._id_tmux_first = self._id_tmux_first or self._id_tmux
        if not tmux_pid:
            self._id_detached = None
            return
        was = self._id_detached
        self._id_detached = identity.detached_check(self.proc, agent_pid=os.getpid(), tmux_pid=tmux_pid) or ""
        if (was or "") != self._id_detached:
            log.info("detached-process check %s (tmux server pid %s)", "on" if self._id_detached else "off", tmux_pid)

    async def _id_recheck_detached(self) -> None:
        """A tmux server can be **replaced** while the agent runs — kmaster's was, on 2026-09-20 —
        and the check is a fact about *that* server's cgroup. Computed once and kept, it went on
        describing a server that no longer existed until the agent was restarted, which is a host
        reading `enforce` off a dead process's cgroup (TD-077). So the tick re-reads the server's
        pid on a cadence of its own, and only a pid that moved costs the check itself. One
        `display-message` every `ID_RECHECK` seconds, in a thread, beside the pane list the tick
        already takes."""
        if time.monotonic() - self._id_rechecked < agent_common.ID_RECHECK:
            return
        pid = await asyncio.to_thread(self.tmux.server_pid)
        if self._id_server(pid) == self._id_tmux and self._id_detached is not None:
            self._id_rechecked = time.monotonic()
            return
        await self._id_read_detached(pid)

    def _id_server(self, pid: int | None) -> tuple[int, int] | None:
        """A server's name for life: its pid **and its start time**. A pid alone would read a
        replacement that landed on the same pid inside one `ID_RECHECK` window as the same server
        — the identity.py walk pairs the two for exactly this reason (review of PR #265)."""
        if not pid:
            return None
        st = self.proc.stat(pid)
        return (pid, st.start if st else 0)

    async def _id_channel(self, peer: int) -> identity.Channel:
        """Classify one connection's peer. A peer that matches no pane we know may belong to one the
        tick has not listed yet — a session's first hook can beat the first tick after `create` —
        so it waits for a fresh list (one at a time, at most one a second) before it is judged
        *outside* or *unknown*; never against the old one."""
        if self._id_detached is None:
            await self._id_read_detached(await asyncio.to_thread(self.tmux.server_pid))
        detached = self._id_detached or None
        ch = identity.classify(peer, self._id_panes, self.proc, detached=detached, ours=self._id_ours())
        listed = {p.session for p in self._id_panes}
        unlisted = any(
            sid not in listed and r.pane and (r.host or self.host) == self.host and r.state not in ("exited", "closed")
            for sid, r in self.sessions.items()
        )
        if ch.kind == "session" or not unlisted:
            # Every live record's pane is known, so a peer that matched none is under none: the
            # person's terminal and the UI — nearly every such connection — never wait for a list.
            return ch
        asked = time.monotonic()
        async with self._id_list_lock:
            if self._id_listed_at <= asked:  # nobody listed while this one waited for the lock
                wait = 1.0 - (time.monotonic() - self._id_listed_at)
                if self._id_listed_at and wait > 0:
                    await asyncio.sleep(wait)
                self._id_note_panes(await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX))
        return identity.classify(peer, self._id_panes, self.proc, detached=detached, ours=self._id_ours())

    def _id_ours(self) -> set[str]:
        """The scopes of this server's panes that no live record's pane holds (TD-362): a pane that is
        no record's, and a record's gone pane inside the grace. A peer in one is *unknown* under the
        detached check, never the person; any other `tmux-spawn-*.scope` may be the person's own tmux."""
        now = time.monotonic()
        gone = {p.cgroup for p, at in self._id_gone.values() if p.cgroup and now - at < identity.PANE_GONE_GRACE}
        return self._id_other_scopes | gone

    async def _identify(self, req: dict[str, Any], peer: int, conn: Any = None) -> dict[str, Any] | None:
        """The one step at the head of dispatch (§4.8a *Where it lives*): judge the envelope's
        `caller` against the channel, record an alarm when they disagree, and — under `enforce` —
        replace the claim with the verdict or refuse. Under `observe` nothing a caller sees
        changes. Returns the refusal to send, or None to go on."""
        rpc = str(req.get("method") or "")
        # The connection is classified once, at its first request, and keeps that for its life: the
        # peer pid is the one that connected, and asking `/proc` about it again later could be asking
        # about whoever holds that pid *now* — a process that connects, hands the socket to a child
        # and exits must not become whatever reuses its pid.
        ch = self._id_conns.get(conn) if conn is not None else None
        if ch is None:
            ch = await self._id_channel(peer)
            self.identity_tally[f"{ch.kind}:{ch.signal}" if ch.signal else ch.kind] += 1
            if conn is not None:
                self._id_conns[conn] = ch
        if rpc == "whoami":
            return {"id": req.get("id"), "result": {"channel": ch.kind, "session": ch.session, "signal": ch.signal}}
        claimed = req.get("caller")
        named = None if mail.is_person(claimed) else self._addr(claimed)
        params = req.get("params")
        hooked = params.get("session") if rpc == "hook" and isinstance(params, dict) else None
        hook_session = self._addr(hooked) if hooked else None
        if rpc == "hook" and ch.kind != "session" and (late := self._id_gone_channel(peer, hook_session)):
            # For this judgement only: the connection's classification stays what it was.
            log.info("hook for %s after its pane ended, matched by %s", hook_session, late.signal)
            ch = late
        verdict = identity.judge(ch, named, rpc, hook_session=hook_session)
        if verdict.alarm is not None:
            if rpc == "hook" and hook_session:
                self._id_log_late_hook(peer, hook_session)
            self._id_alarm(verdict.alarm, verdict.about)
        if self.identity_mode != "enforce":
            return None
        if verdict.refusal is not None:
            return {"id": req.get("id"), "error": verdict.refusal}
        if rpc not in identity.READS:
            if verdict.caller is None:
                req.pop("caller", None)
            else:
                req["caller"] = verdict.caller
        elif ch.kind == "session":
            # A read is served whatever it claims, but from under a pane it runs as that pane's
            # session all the same — no refusal and no alarm, only no borrowed name (the unread-mail
            # line on a reply is the caller's, for one).
            req["caller"] = ch.session
        return None

    def _id_alarm(self, entry: dict[str, Any], about: str | None) -> None:
        at = now_iso()
        log.warning("identity alarm (%s): %s claimed %r on %s", self.identity_mode, *entry.values())
        # Each alarm carries the mode it was raised under (review of PR #318): the list is the
        # raising host's, and a report the home composes for a node's record must say the node's
        # mode — *observe* records what *enforce* would have refused — never the home's.
        entry = {**entry, "mode": self.identity_mode}
        s = self.sessions.get(about) if about else None
        if s is None:
            # The host's own list follows the record's rule (§4.8a): a *new* alarm is written at
            # once, a repeat moves a count in memory and the next tick writes it, so a loop of
            # forgeries is not a disk write each.
            known = len(self.identity_alarms)
            self.identity_alarms = identity.coalesce(self.identity_alarms, entry, at)
            if len(self.identity_alarms) != known:
                self.identity_store.save(self.identity_alarms)
                self._id_host_dirty = False
            else:
                self._id_host_dirty = True
            return
        known = len(s.identity_alarms)
        s.identity_alarms = identity.coalesce(s.identity_alarms, entry, at)
        # A new alarm is written at once; a repeat only bumps a count, and a loop of forged requests
        # must not become a disk write each — the tick writes what is left (`_id_flush`).
        if len(s.identity_alarms) != known:
            self._save(s)
        else:
            self._id_dirty.add(s.id)

    def _id_flush(self) -> None:
        for sid in list(self._id_dirty):
            self._id_dirty.discard(sid)
            if (s := self.sessions.get(sid)) is not None:
                self._save(s)
        if self._id_host_dirty:
            self._id_host_dirty = False
            self.identity_store.save(self.identity_alarms)

    async def rpc_identity_ack(self, id: str | None = None, caller: Any = None) -> dict[str, Any]:
        """**Acknowledge** an identity alarm list (design §4.8a, §4.5a **Inbox row: identity
        alarm**): clears one record's `identity_alarms`, or — with no `id` — the host's own list,
        so the row leaves the person's Inbox. *A person has seen this and decided what it was.*

        **A person's only**, refused to every session exactly as `inbox_delete` is, and deliberately
        **not** in `identity.READS`: a session that could clear the list could erase the evidence of
        its own forgery, which is the one thing the alarm exists to prevent. Nothing is lost either
        way — the host agent's log keeps every alarm, one line each (§4.8a).

        **A node's record is acknowledged at that node** (§4.8a): alarms are node-owned, so an `id`
        naming another host is routed there like any other act (`NODE_ACTS`, §4.4a step 4a), the
        node clears its own list and the home takes the cleared record from the reply — a home that
        cleared its replica would have the alarms back on the node's next report. A person at a
        node may clear only that node's records (`PERSON_NODE_BOUND`); the host's own list is
        whichever host was asked, and never travels."""
        agent_common.person_only(caller, "acknowledge an identity alarm", "§4.8a")
        if not id or id == PERSON:
            self.identity_alarms = []
            self._id_host_dirty = False
            self.identity_store.save(self.identity_alarms)
            return {"id": PERSON, "cleared": True, "alarms": []}
        s = self._get(self._addr(id))
        # the trail says who ended it (§4.10), in the control's own word: **Dismiss**, not the wire
        # name this RPC keeps (§4.8a *An alarm's answers*, TD-077 a1)
        self._attention_ended(s.id, "dismissed by you", "alarm")
        s.identity_alarms = []
        self._id_dirty.discard(s.id)
        self._save(s)
        await self._push_changes()
        return {"id": s.id, "cleared": True, "alarms": []}

    async def rpc_identity_log(self, id: str, caller: Any = None) -> dict[str, Any]:
        """**Log TD** on an identity-alarm row (design §4.8a *An alarm's answers*, TD-077 b):
        file the alarm where work is picked up.

        The host agent does not write a repo's ledger — it never commits on a session's behalf
        (§4.10 *A bounded exchange*) and board write-back is unbuilt — so *filing* is **handing
        it to the session that answers for this one**: the record's first live controller, read
        from the control graph and never from a badge (§9 invariant 9). The message is composed
        **here**, from the alarm's own fields and the record's — channel, claim, RPC, count, the
        first and last time, the host's mode — plus the session's `doing` line and its report
        line, which are the only two things in it a model wrote and are quoted as text.

        It goes as mail **from the person**, marked `handed`, so it **owes an outcome** the way
        an answered question does (§4.10 *Outcomes*, extended by this): the controller reports
        `done`, `blocked` or `dropped` on it, is refused `ao progress none` while it stands, and
        it shows under *Ready to close*. Then the list is cleared and the trail says *logged by
        you → `<controller>`*.

        A person's only, gated exactly as `identity_ack` is, and **offered only where there is a
        session to hand it to**: a record with no live controller — one a person started alone, a
        lead's own — is refused in words, and the page reads `alarm_to` so it does not draw the
        control there at all."""
        agent_common.person_only(caller, "file an identity alarm", "§4.8a")
        s = self._graph().get(self._addr(id))
        if s is None:
            raise RpcError(f"no session {self._addr(id)}")
        if not s.identity_alarms:
            raise RpcError(f"{s.id} has no identity alarms to file (design §4.8a)")
        to = self._answers_for(s)
        if to is None:
            raise RpcError(
                f"no session answers for {s.id}: **Log TD** hands an alarm to the record's first live "
                "controller, and this one has none — dismiss it, or suspend the session, or open it "
                "(design §4.8a *An alarm's answers*)"
            )
        if s.host != self.host:
            # the clearing below is routed to the node, so the node must be there **before**
            # anything is sent: a send that then failed to clear would leave the row standing, and
            # the person's second press would hand the controller a second copy and a second debt
            # (review of PR #318). Refused in words, nothing sent — the `_node_mux` refusal.
            self._node_mux(s.host)
        mode = None if s.host != self.host else self.identity_mode  # a node's mode is on its alarms
        sent = await self._msg(PERSON, _alarm_report(s, mode), [to["id"]], "note", None, None, None, None)
        entry = sent.get("entry") or {}
        if mid := entry.get("id"):
            self._mark(mid, handed=True)  # the debt: it is work the person handed on (§4.10)
        # **The alarms are the node's** (§4.8a; review of PR #318). The message, the debt and the
        # trail are the home's, and this call is not in `NODE_ACTS` because of them — but clearing
        # a node's list here would clear a replica the node's next report puts straight back, the
        # very race `identity_ack` is routed to avoid. So the clearing is routed, as that act, and
        # the home takes the cleared record from the reply; the word is written after, because
        # routing an `identity_ack` writes *dismissed by you* and this ending is not a dismissal.
        if s.host != self.host:
            await self._route_act("identity_ack", {"id": self._address(s)}, None, s.host)
        else:
            s.identity_alarms = []
            self._id_dirty.discard(s.id)
            self._save(s)
        self._attention_ended(self._address(s), f"logged by you → {to['name']}", "alarm")
        await self._push_changes()
        return {"id": self._address(s), "filed": True, "to": to, "entry": mid, "alarms": []}

    async def rpc_suspend(self, id: str, why: str | None = None, caller: Any = None) -> dict[str, Any]:
        """**Suspend** a session over an identity alarm (design §4.8a *An alarm's answers*, §4.5a
        **Inbox row: identity alarm**; TD-077 a2): stop it now and keep it stopped.

        It marks the record `suspended: {at, by, why}` — `why` being the alarm in words unless the
        person gives their own — and then kills it as `ao kill` does: **no wrap-up**, because a
        session under suspicion is not asked to tidy, and nothing is lost by that — the worktree,
        the conversation and the run log are kept, and whatever is unpushed shows under *Ready to
        close* as it would after any kill.

        **A person's only**, refused to every session exactly as `identity_ack` is and for the same
        reason turned around: a session that could suspend could stop its rival. It is offered only
        while the session is live; suspending what is already stopped would be a mark with no act.

        **`suspended` is the home's field** (§9 invariant 15 — intent, like `controllers`), because
        a `create` is gated at the home and that is where the mark is read. So a node's record is
        marked *here* and only the `kill` is routed; the alarms stay the node's.

        **There is no unsuspend.** §4.8a: a suspension is lifted only by a person's own resume of
        that conversation or their **Forget** of the record — both of which already exist and both
        of which are refused to every session while the mark stands (`_refuse_suspended`). A verb
        to clear it would be a fourth road back, and the whole point of the mark is that there are
        only the two a person walks themselves."""
        agent_common.person_only(caller, "suspend a session", "§4.8a")
        # the graph, not `self.sessions`: a node's record is marked **here**, since the field is
        # the home's, and `_get` refuses an address that names another host (§4.4a)
        s = self._graph().get(self._addr(id))
        if s is None:
            raise RpcError(f"no session {self._addr(id)}")
        if s.suspended:
            raise RpcError(f"{s.id} is already suspended (since {s.suspended.get('at')}): only a person lifts it")
        if s.state in ("exited", "closed"):
            raise RpcError(
                f"{s.id} is {s.state} — there is nothing to stop; Forget it, or resume it, "
                "which is what lifts a suspension anyway (design §4.8a)"
            )
        if s.state == "scheduled":
            raise RpcError(
                f"{s.id} has not started — there is nothing to stop; Cancel forgets it (design §6 Start time)"
            )
        s.suspended = {"at": now_iso(), "by": PERSON, "why": str(why or _alarm_words(s))}
        self._save(s)
        # The mark is the home's and the kill is the pane's host's. A suspension ends no row, so
        # nothing is written to the trail: the mark on the row and the card is its record (§4.8a).
        #
        # **The mark is written first, and it stays if the kill fails** (review of PR #301). The
        # two are not one act and cannot be made one: the kill may be a call over a link that is
        # down. Marked-but-running is the safer half to be left holding — no session can restart
        # it, and the person is told in words that it is still running and why — where killed-but-
        # unmarked would stop it and then let any session bring it straight back.
        try:
            if s.host != self.host:
                await self._route_act("kill", {"id": self._address(s)}, None, s.host)
            else:
                await self.rpc_kill(s.id)
        except RpcError as e:
            await self._push_changes()
            raise RpcError(
                f"{s.id} is marked suspended — no session can take its name or resume it — but it could "
                f"not be stopped: {e}. It is still running; suspend it again when {s.host} answers, or "
                f"stop it there."
            ) from None
        await self._push_changes()
        return self._view(self._graph()[self._addr(id)])

    async def rpc_identity(self) -> dict[str, Any]:
        """`ao identity` (design §4.8a): this host's mode, whether the detached-process check is on,
        the tally of connections by class and deciding signal since the agent started, and the
        alarms — the host's own and each record's. A never-gated read: it tells a session nothing
        it could not learn by trying."""
        return {
            "host": self.host,
            "mode": self.identity_mode,
            "detached_check": bool(self._id_detached),
            "tally": dict(sorted(self.identity_tally.items())),
            "alarms": list(self.identity_alarms),
            "sessions": {sid: list(s.identity_alarms) for sid, s in self.sessions.items() if s.identity_alarms},
        }

    async def rpc_whoami(self) -> dict[str, Any]:
        """Answered at the head of dispatch, where the channel is known; reached here only when
        identity is `off` or the call was made in-process."""
        return {"channel": None, "session": None, "signal": None}
