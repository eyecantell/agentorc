"""The host agent's link half (TD-108 step 1): the container supervisor at the home (design §4.4a
*The home supervises it*) and the link itself (§4.4a *The link's protocol*), as a mixin `HostAgent`
inherits. Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    containers,
    hosts,
    link,
    mail,
    naming,
    shots,
)
from sessionorc import settings as settings_mod
from sessionorc import usage as usage_mod
from sessionorc.agent_common import (
    ACT_TIMEOUT,
    HOME_EDITS,
    INTENT_FIELDS,
    NODE_ACTS,
    NODE_READS,
    REPORT_EVERY,
    RpcError,
    _drop_unknown,
    _urgent,
    _usage_key,
    log,
    read_checkout,
    stat_dir,
)
from sessionorc.models import (
    NotTheSameSession,
    Session,
    apply_home,
    now_iso,
)


class LinkMixin:
    # -- the container supervisor (design §4.4a "The home supervises it", TD-057 step 3c.3) -----

    def _supervise_containers(self) -> None:
        """For each container node whose link is down and whose turn has come: observe, decide,
        act — in a task, so a build never holds the tick — and say on the overlay what is being
        done. A node whose link is up is left alone and its record cleared."""
        now = time.monotonic()
        for name, n in containers.container_nodes().items():
            state = self.links.get(name)
            if state and state["up"] and "build" in state:
                # Asked again on every tick, not only at `hello`: a link that outlives a new wheel
                # at the home (today a promote restarts the home and so drops it — nothing promises
                # that) must not leave the node behind for good (review of PR #225).
                self._note_build(name, str(state["build"]))
            if state and state["up"] and not state.get("stale"):
                if self.supervision.pop(name, None) is not None:
                    self._note_link(name)
                continue
            if name not in self.supervision:
                # A linked node that is merely behind waits one grace before anything is done about
                # it: a promote writes the new wheel about a second before it restarts this agent,
                # and the process about to be stopped must not start an install it cannot finish
                # (seen live at the promote of PR #227: a `docker exec pip` left running by the stop,
                # no outcome logged, and the new process installing again 30 s later). A node whose
                # link is down is looked at at once, as before.
                linked = bool(state and state["up"])
                first = now + containers.SUPERVISE_GRACE if linked else 0.0
                self.supervision[name] = {"doing": "", "since": now_iso(), "attempts": 0, "next": first, "error": ""}
            sup = self.supervision[name]
            if not (state and state["up"]) and sup["attempts"] == 0 and not sup["doing"]:
                # the link dropped while that grace was still running — the container may really be
                # gone: looked at at once, as a down link always is. A record that has already
                # acted keeps its own backoff (review of PR #228).
                sup["next"] = 0.0
            if name in self._supervising and not self._supervising[name][0].done():
                continue
            if now < sup["next"]:
                continue
            r = self.container_runner()
            task = asyncio.create_task(self._supervise_one(n, sup, r))
            self._supervising[name] = (task, r)
            task.add_done_callback(lambda t, name=name: self._supervising.pop(name, None))

    def _stop_supervising(self, name: str) -> None:
        """Cancel the node's action in flight, process and all, and forget its record."""
        entry = self._supervising.pop(name, None)
        if entry is not None:
            task, r = entry
            r.cancel()
            task.cancel()
        self.supervision.pop(name, None)

    async def _supervise_one(self, n: containers.ContainerNode, sup: dict[str, Any], r: containers.Runner) -> None:
        try:
            cstate, alive, user = await asyncio.to_thread(containers.observe, n, r)
        except Exception as e:  # noqa: BLE001 — docker itself failing is a reason on the card, not a crash
            self._supervised(n.name, sup, f"cannot observe the container: {e}", failed=True)
            return
        state = self.links.get(n.name) or {}
        why = state.get("why", "")
        stale = bool(state.get("up") and state.get("stale"))  # linked, and behind this home's build
        action, doing = containers.decide(cstate, alive, str(why), volatile=n.volatile, stale=stale)
        if action == "wait":
            # nothing to mend — the agent is dialing, or the person stopped a volatile container:
            # give it the grace, then look again
            sup["next"] = time.monotonic() + containers.SUPERVISE_GRACE
            if sup["doing"] != doing:
                sup["doing"], sup["since"], sup["error"], sup["attempts"] = doing, now_iso(), "", 0
                self._note_link(n.name)
            return
        sup["doing"], sup["since"], sup["error"] = doing, now_iso(), ""
        self._note_link(n.name)
        log.info("supervisor %s: %s", n.name, doing)
        try:
            await asyncio.to_thread(containers.act, n, r, action, user)
        except Exception as e:  # noqa: BLE001
            self._supervised(n.name, sup, f"{doing}: failed — {e}", failed=True)
            return
        self._supervised(n.name, sup, f"{doing}: done, waiting for it to dial in", failed=False)

    def _supervised(self, name: str, sup: dict[str, Any], doing: str, *, failed: bool) -> None:
        # the first failure waits SUPERVISE_FIRST, then doubling to SUPERVISE_MAX; a success resets
        delay = min(containers.SUPERVISE_FIRST * (2 ** sup["attempts"]), containers.SUPERVISE_MAX)
        sup["attempts"] = sup["attempts"] + 1 if failed else 0
        sup["next"] = time.monotonic() + (delay if failed else containers.SUPERVISE_GRACE)
        sup["doing"], sup["since"], sup["error"] = doing, now_iso(), doing if failed else ""
        (log.warning if failed else log.info)("supervisor %s: %s", name, doing)
        self._note_link(name)

    def _note_link(self, name: str) -> None:
        """The overlay changed for that host's cards: push it."""
        task = asyncio.ensure_future(self._push_changes())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def rpc_forget_host(self, host: str, caller: Any = None) -> dict[str, Any]:
        """`ao host forget` (design §4.4a "A container node"): the host's records at the home are
        closed as a closed session is kept — never deleted, their run logs are the node's volume —
        and its link, if up, is dropped. A person's act: a session may not forget a host."""
        agent_common.person_only(caller, "forget a host", "§4.4a")
        if self.mode != "home":
            raise RpcError(f"{self.host} is a node of {self.home}: hosts are forgotten at the home")
        if host == self.host:
            raise RpcError(f"{host} is this home: it cannot forget itself")
        closed = 0
        for s in self.remote.get(host, {}).values():
            if s.state != "closed":
                s.set_state("closed", confidence="tick")
                self._save(s)
                closed += 1
        mux = self._link_muxes.pop(host, None)
        if mux is not None:
            mux.close("the host was forgotten")
        self.links.pop(host, None)
        self._stop_supervising(host)  # `ao host forget` removed the `nodes:` entry: nothing to mend
        await self._push_changes()
        return {"host": host, "closed": closed, "kept": len(self.remote.get(host, {}))}

    def home_reachable(self) -> bool:
        """The home is always in reach of itself; a node reaches it while its link is up (§4.4a)."""
        return self.mode == "home" or bool(self.home_link["up"])

    # -- the link (design §4.4a "The link's protocol", TD-057 step 3a) ---------------------------

    async def _dial_home(self) -> None:
        """A node's dialer: keeps one link to the home up, forever, with backoff."""

        def on_state(up: bool, why: str, mux: link.Mux | None) -> None:
            if up != self.home_link["up"] or why != self.home_link["why"]:
                (log.info if up else log.warning)("link to %s: %s", self.home, why)
            self.home_link = {"up": up, "since": now_iso(), "why": why}
            self._home_mux = mux
            self._snapshot_sent = False
            self._spend_link.clear()  # the first `spend` on a new link reads the home's cursors again
            if up:
                task = asyncio.ensure_future(self._send_snapshot(mux))
                self._bg.add(task)
                task.add_done_callback(self._bg.discard)

        await link.dial(
            hosts.link_socket() or hosts.link_command(),
            host=self.host,
            handler=self._from_home,
            on_state=on_state,
            first=link.BACKOFF_FIRST,
            top=link.BACKOFF_MAX,
        )

    async def _send_snapshot(self, mux: link.Mux | None) -> None:
        """First thing on a new link (§4.4a): every record of this host, whole. Reports start only
        once it is acknowledged, so the home never applies a change to a record it has not got."""
        if mux is None:
            return
        # What is marked as told is exactly what was sent: a record created while the request is out
        # is not in it, and must go in the first report rather than be taken as known.
        sending = {s.id: (_urgent(s), s.to_dict()) for s in self.sessions.values()}
        try:
            await mux.request("snapshot", timeout=link.LINK_SILENCE, records=[d for _, d in sending.values()])
        except (link.LinkClosed, link.LinkError, TimeoutError) as e:
            log.warning("snapshot to %s failed: %s", self.home, e)
            mux.close(f"snapshot failed: {e}")  # start over: a link whose snapshot did not land reports into a void
            return
        now = time.monotonic()
        self._reported = {sid: (urgent, json.dumps(d, sort_keys=True), now) for sid, (urgent, d) in sending.items()}
        self._snapshot_sent = True

    async def _report_home(self) -> None:
        """What changed since the home was last told (§4.4a "Snapshot, then reports"): at once when
        `state`, `pending`, `exit_code` or `pane` moved, else at most every `REPORT_EVERY` per
        record; and the ids this node has forgotten."""
        mux = self._home_mux
        if self.mode != "node" or mux is None or not self._snapshot_sent:
            return
        now, changed, marks = time.monotonic(), [], []
        for s in self.sessions.values():
            urgent, payload = _urgent(s), json.dumps(s.to_dict(), sort_keys=True)
            was = self._reported.get(s.id)
            if was is None or was[0] != urgent or (was[1] != payload and now - was[2] >= REPORT_EVERY):
                marks.append((s.id, (urgent, payload, now)))
                changed.append(s.to_dict())
        forgotten = [sid for sid in self._reported if sid not in self.sessions]
        # Bounded: this runs inside the tick and inside every RPC that pushes, and a write to a peer
        # that has gone blocks once the pipe is full — for `LINK_SILENCE`, were nothing to stop it.
        try:
            async with asyncio.timeout(agent_common.REPORT_WRITE):
                if changed:
                    await mux.notify("report", records=changed)
                if forgotten:
                    await mux.notify("gone", ids=forgotten)
        except link.LinkClosed:
            return
        except link.LinkError as e:
            # a frame this end refused to write, because the home could not have read it (TD-066).
            # The link is started over, exactly as a failed snapshot above is: nothing is marked
            # told over a write that did not happen, and the reconnect's snapshot repairs the gap.
            log.error("a report to %s was not written: %s", self.home, e)
            mux.close(f"a report could not be written: {e}")
            return
        except TimeoutError:
            mux.close(
                f"a report could not be written within {agent_common.REPORT_WRITE:g} s"
            )  # the reconnect's snapshot repairs it
            return
        for sid, mark in marks:  # marked as told only once it went, as the intent push below is
            self._reported[sid] = mark
        for sid in forgotten:
            del self._reported[sid]

    async def _from_home(self, method: str, params: dict[str, Any]) -> Any:
        """What the home may ask of this node: a ping; an `act` (step 4a) — an RPC the home has
        already gated, run here through the same handler a local caller reaches, with no gate of
        its own; `stat`, whether a directory exists here (a team start's checkout check) and the checkout
        it is in; `occupancy` and `worktrees`, the New session form's readings of a place (TD-294); `files`,
        a checkout's own files read here (a team's brief on a machine node, TD-057 step 4b.3); and
        `repos`, this node's registry (the home's `host_repos`, §4.9, TD-229); `shot`, one look's screenshot
        from it (the home's `host_shot`, TD-300). Beside them what the
        home hands down: `read`, `intent`, `settings`, `usage` and `usage_reading`."""
        if method == "ping":
            return "pong"
        if method == "act":
            return await self._act(params)
        if method == "read":
            return await self._read(params)
        if method == "intent":
            await self._take_intent(params.get("records") or [])
            return None
        if method == "settings":
            await self._take_settings(params.get("doc"))
            return None
        if method == "usage":
            self._take_usage(params)
            return None
        if method == "usage_reading":
            await self._take_usage_reading(params)
            return None
        if method == "files":
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(read_checkout, params.get("dir"), params.get("paths")), ACT_TIMEOUT
                )
            except ValueError as e:
                raise link.LinkError(str(e)) from None
        if method == "repos":  # this node's registry, for the home's `host_repos` (§4.9, TD-229)
            return {"repos": await asyncio.to_thread(lambda: hosts.local_host().repos())}
        if method == "stat":
            return await asyncio.to_thread(stat_dir, str(params.get("dir") or ""))
        if method == "shot":  # one look's screenshot from this node's registry, for the home's `host_shot` (TD-300)
            name = str(params.get("name") or "")
            if not mail.SHOT_NAME.fullmatch(name):
                raise link.LinkError(f"{name!r} is not a screenshot a look can name")
            return await asyncio.to_thread(shots.reading, str(params.get("repo") or ""), name, bool(params.get("head")))
        if method == "occupancy":  # the home's `host_occupancy` (§4.4a, TD-294)
            d = str(params.get("dir") or "")
            return await self.rpc_occupancy(d) if d.strip() else {"dir": "", "occupants": [], "git": False}
        if method == "worktrees":  # the home's `host_worktrees` (§4.4a, TD-294)
            return {"worktrees": await asyncio.to_thread(self.worktrees_here, str(params.get("repo") or ""))}
        raise link.LinkError(f"unknown link method {method!r}")

    async def _take_settings(self, doc: Any) -> None:
        """The home's `settings.yml`, whole (§4.4a *Settings, replicated*, TD-147): written as this
        node's own file, which its tick, `gate` and `settings` read as the home reads its own —
        offline included, until the next dial sends it again. A hand edit here is overwritten."""
        if self.mode == "home" or not isinstance(doc, dict):
            return
        await asyncio.to_thread(settings_mod.save, doc)
        log.info("settings.yml replicated from the home")

    async def _act(self, params: dict[str, Any]) -> dict[str, Any]:
        """An act the home routed here (design §4.4a "A node reports and executes; the home
        decides"): `{rpc, params, caller}`, every address in it already written from this host's
        point of view. The home is the gate and this node the executor, so neither `_gate` nor
        the offline table runs — the request came over the link, which is the home. Refused by
        name when the record is another host's (*not my host*). The reply carries the RPC's
        result and the record as it now stands, so the home applies the outcome before it answers
        the caller rather than a report later."""
        rpc = str(params.get("rpc") or "")
        if rpc not in NODE_ACTS and rpc not in HOME_EDITS:
            raise link.LinkError(f"{rpc!r} is not an act a node executes")
        p = dict(params.get("params") or {})
        caller = params.get("caller")
        rid = str(p["id"]) if p.get("id") is not None else None
        if rid is not None:
            bare, where = naming.split_address(rid)
            if where and where != self.host:
                raise link.LinkError(f"{rid} is not on {self.host}: not my host")
            p["id"] = rid = bare
        if rpc in ("create", "name_check"):
            asked = p.pop("host", None)
            if asked and asked != self.host:
                raise link.LinkError(f"a {rpc} for {asked} is not {self.host}'s: not my host")
        method = getattr(self, f"rpc_{rpc}")
        if ignored := _drop_unknown(method, p):
            log.warning("act %s from the home: ignored unknown params %s", rpc, ", ".join(ignored))
        if "caller" in inspect.signature(method).parameters:
            p["caller"] = caller
        try:
            result = await method(**p)
        except RpcError as e:
            raise link.LinkError(str(e)) from None
        except TypeError as e:
            raise link.LinkError(f"bad params: {e}") from None
        sid = rid if rid is not None else (result.get("id") if isinstance(result, dict) else None)
        record = self.sessions.get(str(sid)) if sid else None
        return {
            "result": result,
            "record": record.to_dict() if record is not None else None,
            "gone": bool(sid) and record is None and rpc == "remove",
        }

    async def _take_intent(self, records: list[Any]) -> None:
        """The home's intent for this node's records (§4.4a, step 4b.2): `apply_home` over exactly
        `INTENT_FIELDS` — whatever else a push carries is not taken — so the stopping policies read
        what the home holds; the unread count kept apart as a hint for the mail line, never an
        inbox. A record of another host, or one this node does not hold, is not this node's."""
        changed = False
        for raw in records:
            if not isinstance(raw, dict) or raw.get("host") != self.host:
                log.warning("intent from %s: dropped a record that is not %s's", self.home, self.host)
                continue
            s = self.sessions.get(str(raw.get("id") or ""))
            if s is None:
                continue
            with contextlib.suppress(TypeError, ValueError):
                self._mail_hints[s.id] = (
                    int(raw.get("unread") or 0),
                    bool(raw.get("wake_budget_spent")),
                    [str(x) for x in (raw.get("owed") or [])],
                )
                self._asks_hints[s.id] = int(raw.get("asks_waiting") or 0)
            fields = {k: raw[k] for k in INTENT_FIELDS if k in raw}
            before, stop_before = s.to_dict(), s.run_until
            try:
                apply_home(s, {**fields, "id": s.id, "host": s.host})
            except (NotTheSameSession, TypeError, ValueError, KeyError) as e:
                log.warning("intent from %s: could not take %s: %s", self.home, s.id, e)
                continue
            if s.run_until != stop_before:
                s.wrapup_sent_at = None  # a new stop time is a new run, as `set_stop` has it
            if s.to_dict() != before:
                self.store.save(s)
                changed = True
        if changed:
            await self._push_changes()

    async def _take_usage_reading(self, params: dict[str, Any]) -> None:
        """`usage_reading {account, reading}` from the home (§4.4 *A node's sessions report to their
        node*): the account's reading as every host's reports made it, taken into this node's by
        `usage.adopt` — each window the home confirmed later, or saw roll, is the home's — for an
        account one of this node's live tool sessions is keyed to, and nothing else. Its age is
        the one this node's fallback and gate read, so an idle node on an account another host's
        sessions report through is not asked for."""
        key, reading = str(params.get("account") or ""), params.get("reading")
        if self.mode != "node" or not key or not isinstance(reading, dict):
            return
        profs = sorted(
            {
                s.profile
                for s in self._usage_live()
                if s.profile not in self._metered
                and getattr(ad := adapters.get(s.adapter), "usage_for", None)
                and _usage_key(ad, s.adapter, s.profile)[0] == key
            }
        )
        if not profs:
            return
        self._usage_seed(key, profs)
        was = self._usage_acct.get(key)
        merged = usage_mod.adopt(was, reading)
        if merged != was:
            self._usage_acct[key] = merged
            await self._usage_spread(key)
            self._usage_limits(self._usage_live(), self._metered)
            await self._push_changes()

    def _forward_usage_report(self, s: Session, key: str, windows: list[dict[str, Any]], fresh: bool) -> None:
        """A node sends each report its sessions made on to the home (§4.4 *A node's sessions report
        to their node*, TD-233 slice 2): `usage_report {id, account, windows, fresh}`, the session
        and the account key this node, which holds the profile's credentials, keyed it by — a
        profile of the same name at the home may be another login. Off the RPC's path, since the
        status line that sent it waits a second at most; not queued, since a report delivered late would be read
        as new —
        a link that is down, or not yet past its snapshot, drops it."""
        mux = self._home_mux
        if self.mode != "node" or mux is None or not self._snapshot_sent:
            return

        async def send() -> None:
            try:
                await asyncio.wait_for(
                    mux.notify("usage_report", id=s.id, account=key, windows=windows, fresh=fresh),
                    agent_common.REPORT_WRITE,
                )
            except (link.LinkError, link.LinkClosed, TimeoutError, OSError) as e:
                log.warning("a usage report from %s did not reach %s: %s", s.id, self.home, e)

        task = asyncio.ensure_future(send())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _send_derived(self, s: Session, progress: list[Any], findings: list[Any], retire: list[str]) -> None:
        """A node's tick derived these for `s` (§4.4a, step 4b.2): sent to the home — whose fields
        they are — as `derived`, and applied there exactly as a home's own tick applies them. Not
        queued: a link that is gone, or a home that refuses, is a log line, and the next derive
        (`DERIVE_EVERY`) says it again."""
        mux = self._home_mux
        if mux is None or not (progress or findings or retire):
            return
        try:
            await mux.request(
                "derived",
                timeout=agent_common.REPORT_WRITE,
                id=s.id,
                progress=[e.to_dict() for e in progress],
                findings=[e.to_dict() for e in findings],
                retire=list(retire),
            )
        except (link.LinkError, link.LinkClosed, TimeoutError) as e:
            log.warning("derived reports for %s did not reach %s: %s", s.id, self.home, e)

    async def _read(self, params: dict[str, Any]) -> Any:
        """A read the home routed here (§4.4a, step 4b.1): `{rpc, params}`, `rpc` one of
        `NODE_READS` and nothing else — ungated, as on one host, so no caller rides along. Refused
        by name when the record is another host's (*not my host*). The reply is the RPC's own."""
        rpc = str(params.get("rpc") or "")
        if rpc not in NODE_READS:
            raise link.LinkError(f"{rpc!r} is not a read a node serves the home")
        p = dict(params.get("params") or {})
        bare, where = naming.split_address(str(p.get("id") or ""))
        if where and where != self.host:
            raise link.LinkError(f"{p.get('id')} is not on {self.host}: not my host")
        p["id"] = bare
        method = getattr(self, f"rpc_{rpc}")
        if ignored := _drop_unknown(method, p):
            log.warning("read %s from the home: ignored unknown params %s", rpc, ", ".join(ignored))
        try:
            return await method(**p)
        except RpcError as e:
            raise link.LinkError(str(e)) from None
        except TypeError as e:
            raise link.LinkError(f"bad params: {e}") from None
