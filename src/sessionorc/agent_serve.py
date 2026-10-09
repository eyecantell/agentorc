"""Serving clients (TD-108 step 1): one org to a client (design §4.4a *A node's records at the home*), the
event stream, and the socket's connection handling — the JSON-lines RPC, the identity check at the door, the
reply line — as a mixin `HostAgent` inherits. Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    identity,
    link,
    mail,
    modes,
    naming,
    paths,
)
from sessionorc import cadence as cadence_mod
from sessionorc import held as held_mod
from sessionorc.agent_common import (
    BRIEF_CLAUSE,
    FLOW_CLAUSE,
    NODE_READS,
    PUSH_OPEN,
    RpcError,
    _drop_unknown,
    _peer_pid,
    _reply_line,
    log,
)
from sessionorc.models import (
    Session,
    context_over_text,
    now_iso,
)


class ServeMixin:
    # -- one org, to a client (design §4.4a "A node's records at the home") -----------------------

    def _view(
        self, s: Session, *, bookkeeping: bool = False, graph: dict[str, Session] | None = None
    ) -> dict[str, Any]:
        """The view a client gets. This host's record is `s.view()`. Another host's carries its
        address as `id`, and while that host's link is down reads `unreachable` — an overlay on the
        view, never a state on the record."""
        v = s.view(bookkeeping=bookkeeping)
        # design §4.8a *An alarm's answers* (TD-077 b): **who answers for this record** — its
        # first live controller, read from the control graph and never from a badge (§9 invariant
        # 9). The page draws **Log TD** only where this is non-null and says *no session answers
        # for this one* where it is, which it can only do if it knows before it draws; and the
        # RPC reads the same function, so drawn-or-not and refused-or-not cannot disagree.
        v["alarm_to"] = self._answers_for(s, graph)
        # §4.10 *When it is read* (TD-168): the composer's sentence for each kind, as a person reads
        # it — computed here so the dialog opens with it and asks nothing; a `reply` reads as a note
        now = datetime.now(UTC)
        down = s.host != self.host and not (self.links.get(s.host) or {}).get("up")
        rings = getattr(adapters.get(s.adapter), "composer", None) is not None
        cache = s.host == self.host and agent_common.cache_restarts(s, now)  # a lapsed cache (TD-467)
        v["read_when"] = {
            k: mail.read_when(s, k, now, unreachable=down, rings=rings, cache=cache) for k in ("ask", "note")
        }
        # the Reply composer's line for a person's answer on a handed entry's thread (TD-218)
        v["read_when"]["refill"] = mail.read_when(s, "reply", now, unreachable=down, rings=rings, refills=True)
        if s.host == self.host:
            if self.mode == "node":
                v["asks_waiting"] = self._asks_hints.get(s.id, 0)  # the mailbox is the home's (§4.4a)
                v["prs_waiting"] = None  # the home's to count: a node holds no inbox (§4.4a)
            return v
        v["asks_waiting"] = s.asks_waiting(home=self.host)  # a bare `to` here names this host's session
        v["prs_waiting"] = s.prs_waiting(home=self.host)
        v["id"] = f"{s.id}@{s.host}"
        v["controllers"] = self._ctl(s)  # as this home addresses them
        state = self.links.get(s.host) or {"up": False, "since": None, "why": "not connected since the home started"}
        v["host_link"] = dict(state)
        if (sup := self.supervision.get(s.host)) and sup.get("doing"):
            v["host_link"]["supervisor"] = {k: sup[k] for k in ("doing", "since", "attempts")}
        if not state["up"]:
            v["last_state"], v["state"] = v["state"], "unreachable"
            if v.get("pending"):
                # §4.4a "Permission prompts follow the same line": the hook still blocks on its node
                # and nothing answered here can reach it, so the card says so and sends the person
                # to the tool's own dialog at that host. An overlay too — the record keeps its pending.
                v["pending"] = {**v["pending"], "host_unreachable": True}
        return v

    def _views(self) -> list[dict[str, Any]]:
        # one graph for the batch, not one per record: `_views` runs on nearly every RPC through
        # `_push_changes`, and `_answers_for` reading its own would make that quadratic in the
        # org's size (review of PR #318)
        graph = self._graph()
        return [
            self._view(s, graph=graph)
            for s in (*self.sessions.values(), *(r for h in self.remote.values() for r in h.values()))
        ]

    def _remember_dir(self, directory: Path) -> None:
        p = paths.recent_dirs_file()
        lines = p.read_text().splitlines() if p.is_file() else []
        lines = [str(directory)] + [ln for ln in lines if ln != str(directory)]
        p.write_text("\n".join(lines[:20]) + "\n")

    # -- streaming -------------------------------------------------------------------------------

    async def _push_changes(self) -> None:
        """Send each subscriber what changed since *it* was last told. One payload per session is
        serialised once; the per-subscriber comparison is a string compare."""
        self._poke_waits()
        await self._report_home()  # a node tells its home, whether or not a browser is watching
        if self._intent_sent:
            await self._push_intent()  # and the home tells each node what it holds of its records
        gone, self._gone = self._gone, []
        if not self._subscribers:
            return
        # sort_keys: the payload is the comparison key too (the UI reads fields by name, never order)
        payloads = {v["id"]: json.dumps(v, sort_keys=True) for v in self._views()}
        # A push is a line like a reply, and one past the limit ends every open subscription at
        # once — every tab, not just the card that grew (TD-066). One card is dropped from the
        # stream instead, and named in the log; `last` is left untouched, so the card returns to
        # the stream the moment it fits again. Logged once per card, not once a tick.
        room = link.FRAME_LIMIT - len(PUSH_OPEN) - len(b"}\n")  # the envelope `_send` puts around it
        big = {sid for sid, payload in payloads.items() if len(payload.encode()) > room}
        for sid in big - self._oversize:
            log.error("the view of %s is past the %d-byte line limit: not pushed", sid, link.FRAME_LIMIT)
        for sid in self._oversize & (payloads.keys() - big):
            log.info("the view of %s fits again: pushing", sid)
        self._oversize = big  # so a record that has gone leaves the set with its view
        payloads = {sid: payload for sid, payload in payloads.items() if sid not in big}
        for w, last in list(self._subscribers.items()):
            for sid, payload in payloads.items():
                if last.get(sid) != payload:
                    last[sid] = payload
                    await self._send(w, PUSH_OPEN.decode() + payload + "}")
            for sid in gone:
                await self._send(w, json.dumps({"event": "gone", "id": sid}))

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg)
        for w in list(self._subscribers):
            await self._send(w, line)

    async def _send(self, w: asyncio.StreamWriter, line: str) -> None:
        try:
            w.write((line + "\n").encode())
            await w.drain()
        except (ConnectionError, OSError):
            self._subscribers.pop(w, None)

    # -- connection handling ---------------------------------------------------------------------

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._conns.add(writer)
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except ValueError:
                    writer.write(b'{"error": "bad json"}\n')
                    continue
                if "link" in req and "method" not in req:
                    # sshd's forced command, announcing the host its key is bound to (§4.4a): from
                    # here on this connection is a link, not a client.
                    await self._serve_link(req["link"], reader, writer)
                    return
                if req.get("method") == "wait":
                    if writer in self._subscribers:
                        writer.write(b'{"error": "a wait runs on its own connection, never a subscribed one"}\n')
                        continue
                    await self._serve_wait(req, reader, writer)
                    continue
                if req.get("method") == "subscribe":
                    self._subscribers[writer] = {}  # empty: the first push is this tab's full snapshot
                    writer.write((json.dumps({"id": req.get("id"), "result": "subscribed"}) + "\n").encode())
                    await writer.drain()
                    for prof, u in self._usage.items():  # the top bar's figure, before the cards
                        await self._send(writer, json.dumps({"event": "usage", "profile": prof, "usage": u}))
                    for root in list(self._repos):  # the repo facts, as a change would push them, marks and all
                        view = self._repo_view(root)
                        await self._send(writer, json.dumps({"event": "repos", "root": root, "repo": view}))
                    await self._push_changes()
                    continue
                writer.write(_reply_line(req, await self._dispatch(req, peer=_peer_pid(writer), conn=writer)))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._conns.discard(writer)
            self._id_conns.pop(writer, None)
            self._subscribers.pop(writer, None)
            writer.close()

    async def _serve_wait(
        self, req: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """A `wait` holds its connection (design §4.10): the RPC runs while this reads the socket
        for the close. The moment the client goes away — a Ctrl-C, a cancelled turn — the wait is
        cancelled, so no ghost wait is left to be charged a wake and hand the mail to nobody.
        Requests are serial per connection; one sent mid-wait is answered with an error."""
        task = asyncio.ensure_future(self._dispatch(req, peer=_peer_pid(writer), conn=writer))
        try:
            while True:
                line = asyncio.ensure_future(reader.readline())
                done, _ = await asyncio.wait({task, line}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    # readline leaves unconsumed bytes in the buffer when cancelled, so a request
                    # already on its way is read by the connection loop after the reply
                    line.cancel()
                    with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                        await line
                    writer.write(_reply_line(req, task.result()))
                    await writer.drain()
                    return
                try:
                    got = line.result()
                except (ConnectionError, asyncio.IncompleteReadError, ValueError):
                    got = b""
                if not got:
                    raise ConnectionResetError("the waiting client closed its connection")
                with contextlib.suppress(ValueError, AttributeError):
                    rid = json.loads(got).get("id")
                    writer.write(
                        (json.dumps({"id": rid, "error": "this connection is blocked in wait"}) + "\n").encode()
                    )
                    await writer.drain()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _dispatch(
        self, req: dict[str, Any], *, link_host: str | None = None, peer: int | None = None, conn: Any = None
    ) -> dict[str, Any]:
        """`link_host`: the request came over that node's link (step 5), so its caller is that
        host's session — `id@host` here — and never this socket's. `peer`: the pid at the other end
        of this host's own socket, from its credentials — what design §4.8a judges the envelope's
        `caller` against; None for a link (identified by its key, never classified) and for a call
        made in-process. `conn`: the connection, which is what is classified — once, for its life."""
        if peer is not None and link_host is None and self.identity_mode != "off":
            try:
                refused = await self._identify(req, peer, conn)
            except Exception:  # noqa: BLE001 — a bug in the check must never take the socket down with it
                # `observe` promises that nothing a caller sees changes, so the request is served as
                # before. Under `enforce` a check that can be made to fail would be a way round it, so
                # everything but a read is refused — and a person recovers with `identity: observe`
                # in hosts.yml, which needs no RPC.
                log.exception("identity check failed on %s (%s)", req.get("method"), self.identity_mode)
                refused = None
                if self.identity_mode == "enforce" and str(req.get("method") or "") not in identity.READS:
                    refused = {"id": req.get("id"), "error": identity.CHECK_FAILED}
            if refused is not None:
                return refused
        resp = await self._dispatch_inner(req, link_host=link_host)
        via_home = resp.pop("_via_home", False)
        caller = req.get("caller")
        if not mail.is_person(caller) and self.mode == "node" and not via_home and "mail" not in resp:
            # A read this node served alone (§4.4a, step 4b.2): the inbox is at the home, and the
            # count it last pushed is what the line says — a hint, as fresh as the link.
            unread, spent, owed = self._mail_hints.get(naming.split_address(str(caller))[0], (0, False, []))
            if unread or owed:
                resp["mail"] = {"unread": unread, "wake_budget_spent": spent}
                if owed:
                    resp["mail"]["owed"] = owed
        if not mail.is_person(caller) and self.mode == "home":
            # The line on every `ao` reply (design §4.10): the response to a session with unread
            # mail says so — result or refusal alike — read after the method ran, so an `ao inbox`
            # that just read everything carries no line. It types nothing and starts nothing.
            s = self._graph().get(self._caller_address(caller, link_host))
            owed = s.owed() if s is not None else []
            # and the context bound's clause (§6 rule 5, TD-190): a member working past its bound is
            # not interrupted, so every reply it reads says so
            over = context_over_text({"context": s.context, "context_bound": s.context_bound}) if s else ""
            # and rule 7's clause (TD-217 slice 4): a member whose brief changed is not interrupted either
            declared = s is not None and (s.restart_wanted or s.out_of_work or s.seat is not None)
            changed = ""
            if s is not None and not declared:
                changed = BRIEF_CLAUSE if s.brief_changed else FLOW_CLAUSE if s.relaunch else ""
            # and rule 10's clause (TD-258): an open PR of its own fails the cadence check
            failing = cadence_mod.clause(s.checks) if s is not None and s.supervised and s.seat is None else ""
            # and rule 11's (TD-258): a held PR of its own merged without its read — said once, on
            # this reply, to a member the tick has not typed it to; it rides the same field
            if s is not None and s.supervised and (crossed := held_mod.untold(s.held_missed)):
                reader = str((s.review or {}).get("reader") or "")
                failing = "; ".join(x for x in (failing, held_mod.clause(crossed, reader)) if x)
                for c in crossed:
                    c["told"] = now_iso()
                self._save(s)
            if s is not None and ((n := s.unread()) or owed or over or changed or failing):
                # The same line carries the debt (design §4.10 *Outcomes*): *briefs are skimmed, a
                # refusal is not*, and this is the cheapest thing that is neither.
                resp["mail"] = {"unread": n, "wake_budget_spent": s.wake_budget_spent()}
                if owed:
                    resp["mail"]["owed"] = owed
                if over:
                    resp["mail"]["context"] = over
                if changed:
                    resp["mail"]["brief"] = changed
                if failing:
                    resp["mail"]["cadence"] = failing
        return resp

    def _caller_address(self, caller: Any, link_host: str | None) -> str:
        """A request's identity comes from the channel it arrived on, never from a field it
        carries (design §4.4a): this socket is this host's, so any `@host` a client wrote is
        dropped and the caller is this host's bare id; a node's link is that node's, so the
        caller is `id@node` here."""
        bare = naming.split_address(str(caller))[0]
        return naming.qualify(f"{bare}@{link_host}", local=self.host) if link_host else bare

    async def _dispatch_inner(self, req: dict[str, Any], *, link_host: str | None = None) -> dict[str, Any]:
        rid = req.get("id")
        name = req.get("method")
        method = getattr(self, f"rpc_{name}", None)
        if method is None:
            return {"id": rid, "error": f"unknown method {name!r}"}
        if not isinstance(req.get("params") or {}, dict):
            # raw JSON from any local process: `"params": 5` used to raise outside the handler's
            # `try` and drop the connection without a reply (red-team of PR #248)
            return {"id": rid, "error": "params must be an object"}
        params = dict(req.get("params") or {})
        if ignored := _drop_unknown(method, params):
            # logged with the method, because the reply cannot tell a client newer than this agent
            # from a caller bug of the same age, and the second is worth finding in a log
            log.warning("rpc %s: ignored unknown params %s", name, ", ".join(ignored))
        caller = req.get("caller")
        if not mail.is_person(caller):
            caller = self._caller_address(caller, link_host)
        try:
            if self.mode == "node":
                # A node (design §4.4a, the call-by-call table): decided before the gate, because
                # the gate's graph is at the home. What the table refuses — the mailbox, reports,
                # home-owned edits, a session's acts on others — is forwarded to the home while
                # the link is up (step 5) and refused as unreachable while it is down; never
                # served from the replica. A `wait` goes to the home too: that is where the mail
                # and the other hosts' members are.
                refusal = modes.offline_refusal(str(name), caller, params, host=self.host, home=self.home)
                if refusal or name == "wait":
                    if not self.home_reachable():
                        raise RpcError(
                            refusal
                            or f"a wait sees the org at the home: {self.home} (home) is unreachable from {self.host}"
                        )
                    return {**await self._forward(rid, str(name), params, caller), "_via_home": True}
            self._gate(caller, str(name), params)
            if (target := self._act_host(str(name), params)) is not None:
                # Another host's record (design §4.4a, step 4a): gated above over the one graph,
                # executed by that host's node, its verdict returned — or refused as unreachable.
                if name in NODE_READS:
                    return {"id": rid, "result": await self._route_read(str(name), params, target)}
                return {"id": rid, "result": await self._route_act(str(name), params, caller, target)}
            if "caller" in inspect.signature(method).parameters:
                # The methods that need to know who called (`create` seeds the new record's
                # controllers with its creator; `send` and `keys` record who typed; `msg` and
                # `inbox` are the caller's own) get the envelope's caller, set unconditionally and
                # after the gate: a client that put its own `caller` in `params` does not get to
                # choose who it is.
                params["caller"] = caller
            resp: dict[str, Any] = {"id": rid, "result": await method(**params)}
        except RpcError as e:
            resp = {"id": rid, "error": str(e), **({"error_data": e.data} if e.data else {})}
        except TypeError as e:
            resp = {"id": rid, "error": f"bad params: {e}"}
        except Exception as e:  # noqa: BLE001
            log.exception("rpc %s failed", req.get("method"))
            resp = {"id": rid, "error": f"{type(e).__name__}: {e}"}
        if ignored:
            resp["ignored"] = ignored
        return resp
