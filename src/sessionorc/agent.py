"""The host agent: one process per host, the only writer to `ao-*` tmux sessions (design §4.4, §9).

JSON-lines RPC over a Unix socket: `{"id": n, "method": "...", "params": {...}}` →
`{"id": n, "result": ...}` or `{"id": n, "error": "..."}`. `subscribe` turns the connection into
a stream of `{"event": "session", "session": {...}}` / `{"event": "gone", "id": ...}` lines.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import adapters, naming, paths
from sessionorc.models import Pending, Session, now_iso
from sessionorc.store import EventQueue, SessionStore
from sessionorc.tmux import DuplicateSession, PaneInfo, Tmux

log = logging.getLogger("agentorc.agent")

TICK_SECONDS = float(os.environ.get("AGENTORC_TICK", "2"))
TAIL_LINES = 6
CLOSED_KEEP = timedelta(days=1)
STALL_AFTER = timedelta(minutes=20)
CREATE_GRACE = timedelta(seconds=10)  # a pane snapshot older than a session cannot judge it


class RpcError(Exception):
    pass


class HostAgent:
    def __init__(
        self, *, tmux: Tmux | None = None, store: SessionStore | None = None, events: EventQueue | None = None
    ):
        paths.ensure_layout()
        self.tmux = tmux or Tmux()
        self.store = store or SessionStore()
        self.events = events or EventQueue()
        self.sessions: dict[str, Session] = self.store.load_all()
        self._dir_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._last_pushed: dict[str, str] = {}
        # (session id, tool_use_id) → the hook's pending decision
        self._waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}

    # -- lifecycle ------------------------------------------------------------------------------

    async def serve(self, sock: Path | None = None) -> None:
        sock = sock or paths.socket_path()
        sock.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()
        self.tmux.ensure_server()
        server = await asyncio.start_unix_server(self._handle_conn, path=str(sock))
        os.chmod(sock, 0o600)
        log.info("listening on %s", sock)
        ticker = asyncio.create_task(self._tick_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            ticker.cancel()
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
                await self._push_changes()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # -- reconcile -------------------------------------------------------------------------------
    #
    # Concurrency model: every mutation of `self.sessions` and of Session objects happens on the
    # event loop, never in a thread. Only the tmux subprocess calls run in threads, and they
    # return plain data. So a tick's reconcile step and an RPC handler can never interleave
    # inside a check-then-act sequence — the loop runs them one at a time.

    async def tick(self) -> None:
        snapshot_at = datetime.now(UTC)
        panes = await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)
        tails = await asyncio.to_thread(lambda: {sid: self.tmux.capture_tail(sid, TAIL_LINES) for sid in panes})
        self._reconcile(panes, tails, snapshot_at)

    def _reconcile(self, panes: dict[str, PaneInfo], tails: dict[str, list[str]], snapshot_at: datetime) -> None:
        for sid, event in self.events.drain():
            self._apply_event(sid, event)
        now = datetime.now(UTC)
        for sid, s in list(self.sessions.items()):
            if s.state == "closed":
                if s.closed_at and _parse(s.closed_at) + CLOSED_KEEP < now:
                    self._forget(sid)
                continue
            pane = panes.get(sid)
            if pane is None:
                # A session created after the pane snapshot was taken is not judged by it.
                if s.state != "exited" and _parse(s.created) + CREATE_GRACE < snapshot_at:
                    s.set_state("exited", confidence="scraped")
                    self.store.save(s)
                continue
            self._observe(s, pane, tails.get(sid, []), now)
        # tmux sessions with our prefix that we have no record of (created by hand, or the
        # store was lost): adopt them minimally as shells so they appear in the Herd.
        for name, pane in panes.items():
            if name not in self.sessions:
                s = Session(id=name, name=name[len(naming.PREFIX) :], kind="interactive", adapter="shell", dir="")
                s.created = datetime.fromtimestamp(pane.created, UTC).isoformat().replace("+00:00", "Z")
                self.sessions[name] = s
                self._observe(s, pane, tails.get(name, []), now)

    def _observe(self, s: Session, pane: PaneInfo, tail: list[str], now: datetime) -> None:
        adapter = adapters.get(s.adapter)
        s.tail = [_clean(t) for t in tail]
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
        elif s.state == "working" and s.last_output and now - _parse(s.last_output) > STALL_AFTER:
            # Hook-fed adapters: the only scraped verdict is the liveness cross-check, and it
            # applies to `working` alone — a `needs-you` or `idle` session is silent by design.
            s.set_state("stalled?", confidence="scraped")
        self.store.save(s)

    def _apply_event(self, sid: str, event: dict[str, Any]) -> None:
        """A hook-fed state transition (design §4.2 table). Adapters map hook names to these."""
        s = self.sessions.get(sid)
        if s is None:
            return
        if aid := event.get("adapter_id"):
            s.adapter_id = aid
        if delta := event.get("subagent_delta"):
            s.subagents = max(0, s.subagents + int(delta))
        state = event.get("state")
        if state:
            pending = Pending.from_dict(event["pending"]) if event.get("pending") else None
            s.set_state(state, confidence="hook", pending=pending)
        self.store.save(s)

    def _forget(self, sid: str) -> None:
        self.sessions.pop(sid, None)
        self.store.delete(sid)
        self._last_pushed.pop(sid, None)

    # -- RPC methods -----------------------------------------------------------------------------

    async def rpc_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.sessions.values()]

    async def rpc_get(self, id: str) -> dict[str, Any]:
        return self._get(id).to_dict()

    async def rpc_create(
        self,
        *,
        name: str,
        dir: str,
        adapter: str = "shell",
        profile: str = "",
        kind: str = "interactive",
        repo: str | None = None,
        worktree: str | None = None,
        argv: list[str] | None = None,
        unattended: bool = False,
        resume: str | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        directory = Path(dir).expanduser().resolve()
        if not directory.is_dir():
            raise RpcError(f"not a directory: {directory}")
        try:
            ad = adapters.get(adapter)
        except KeyError as e:
            raise RpcError(str(e).strip('"')) from None
        async with self._dir_locks[str(directory)]:
            if kind == "interactive" and adapter != "shell":
                for other in list(self.sessions.values()):
                    if (
                        other.kind == "interactive"
                        and other.adapter != "shell"
                        and other.state not in ("exited", "closed")
                        and Path(other.dir) == directory
                    ):
                        raise RpcError(f"{directory} already has agent session {other.id} ({other.state}); anchor rule")
            live = await asyncio.to_thread(lambda: [p.session for p in self.tmux.list_panes()])
            try:
                spec = ad.launch(
                    profile=profile, resume=resume, prompt=prompt, unattended=unattended, cwd=directory, name=name
                )
            except (KeyError, ValueError) as e:
                raise RpcError(str(e).strip('"')) from None
            if argv:
                spec.argv = argv
            taken = set(self.sessions) | set(live)
            for _attempt in range(5):
                sid = naming.session_id(directory, repo, name, taken)
                # Ours win, whatever an adapter sets. AGENTORC_HOME is explicit because the tmux
                # server may predate this agent and carry a different environment; a hook script
                # inside the session must find *this* agent's socket (found the hard way, 2026-09-06).
                env = {**spec.env, "AGENTORC_SESSION": sid, "AGENTORC_HOME": str(paths.home())}
                run_log = paths.runs_dir() / f"{sid}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
                try:
                    await asyncio.to_thread(self._start, sid, directory, spec.argv, env, run_log)
                    break
                except DuplicateSession:
                    taken.add(sid)  # design §4.1: handle tmux's own verdict, not just our check
            else:
                raise RpcError(f"could not find a free session name for {name!r} in {directory}")
            s = Session(
                id=sid,
                name=name,
                kind=kind,  # type: ignore[arg-type]
                adapter=adapter,
                dir=str(directory),
                profile=profile,
                repo=repo,
                worktree=worktree,
                unattended=unattended,
                confidence=ad.state_source,
                run_log=str(run_log),
                adapter_id=spec.adapter_id,
            )
            self.sessions[sid] = s
            self.store.save(s)
            self._remember_dir(directory)
        return s.to_dict()

    def _start(self, sid: str, cwd: Path, argv: list[str] | None, env: dict[str, str], run_log: Path) -> None:
        self.tmux.ensure_server()
        self.tmux.new_session(sid, cwd, argv, env, logfile=run_log)  # log from the first byte (invariant 3)

    async def rpc_kill(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("exited", confidence="scraped")
        self.store.save(s)
        return s.to_dict()

    async def rpc_close(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("closed", confidence="scraped")
        s.closed_at = now_iso()
        self.store.save(s)
        return s.to_dict()

    async def rpc_remove(self, id: str) -> None:
        s = self._get(id)
        if s.state not in ("exited", "closed"):
            raise RpcError(f"{id} is {s.state}; kill it first")
        self._forget(id)
        await self._push_gone(id)

    async def rpc_send(self, id: str, text: str) -> None:
        s = self._get(id)
        if s.pending and s.pending.kind in ("permission", "question"):
            raise RpcError(f"{id} has a pending {s.pending.kind}; answer it in the terminal")
        await asyncio.to_thread(self.tmux.send_prompt, id, text)

    async def rpc_keys(self, id: str, keys: list[str]) -> None:
        self._get(id)
        await asyncio.to_thread(lambda: self.tmux.run("send-keys", "-t", f"={id}:", *keys))

    async def rpc_tail(self, id: str, lines: int = 40) -> list[str]:
        self._get(id)
        return await asyncio.to_thread(self.tmux.capture_tail, id, lines)

    async def rpc_set_mode(self, id: str, unattended: bool) -> dict[str, Any]:
        s = self._get(id)
        s.unattended = bool(unattended)
        self.store.save(s)
        return s.to_dict()

    async def rpc_hook(self, session: str, **event: Any) -> dict[str, Any] | None:
        """Called by an adapter's hook script. A `permission` event blocks until answered or timed out."""
        if event.get("kind") == "permission":
            return await self._await_permission(session, event)
        self._apply_event(session, event)
        await self._push_changes()
        return None

    async def _await_permission(self, session: str, event: dict[str, Any]) -> dict[str, Any] | None:
        s = self.sessions.get(session)
        if s is None:
            return None
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

    async def rpc_decide(self, id: str, tool_use_id: str, behavior: str, reason: str | None = None) -> None:
        s = self._get(id)
        fut = self._waiters.get((id, tool_use_id))
        if fut is None or fut.done():
            raise RpcError("no pending permission with that id (answered, timed out, or in the terminal)")
        if behavior not in ("allow", "deny"):
            raise RpcError("behavior must be allow or deny")
        fut.set_result({"behavior": behavior, "reason": reason})
        s.set_state("working", confidence="hook")
        self.store.save(s)
        await self._push_changes()

    async def rpc_recent_dirs(self) -> list[str]:
        p = paths.recent_dirs_file()
        return p.read_text().splitlines() if p.is_file() else []

    async def rpc_adapters(self) -> list[str]:
        return adapters.names()

    async def rpc_ping(self) -> str:
        return "pong"

    # -- helpers ---------------------------------------------------------------------------------

    def _get(self, sid: str) -> Session:
        try:
            return self.sessions[sid]
        except KeyError:
            raise RpcError(f"no session {sid}") from None

    def _remember_dir(self, directory: Path) -> None:
        p = paths.recent_dirs_file()
        lines = p.read_text().splitlines() if p.is_file() else []
        lines = [str(directory)] + [ln for ln in lines if ln != str(directory)]
        p.write_text("\n".join(lines[:20]) + "\n")

    # -- streaming -------------------------------------------------------------------------------

    async def _push_changes(self) -> None:
        if not self._subscribers:
            return
        for sid, s in self.sessions.items():
            payload = json.dumps(s.to_dict(), sort_keys=True)
            if self._last_pushed.get(sid) != payload:
                self._last_pushed[sid] = payload
                await self._broadcast({"event": "session", "session": s.to_dict()})
        for sid in list(self._last_pushed):
            if sid not in self.sessions:
                self._last_pushed.pop(sid)
                await self._broadcast({"event": "gone", "id": sid})

    async def _push_gone(self, sid: str) -> None:
        await self._broadcast({"event": "gone", "id": sid})

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        line = (json.dumps(msg) + "\n").encode()
        for w in list(self._subscribers):
            try:
                w.write(line)
                await w.drain()
            except (ConnectionError, OSError):
                self._subscribers.discard(w)

    # -- connection handling ---------------------------------------------------------------------

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except ValueError:
                    writer.write(b'{"error": "bad json"}\n')
                    continue
                if req.get("method") == "subscribe":
                    self._subscribers.add(writer)
                    self._last_pushed = {}  # resend everything to the new subscriber
                    writer.write((json.dumps({"id": req.get("id"), "result": "subscribed"}) + "\n").encode())
                    await writer.drain()
                    await self._push_changes()
                    continue
                writer.write((json.dumps(await self._dispatch(req)) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._subscribers.discard(writer)
            writer.close()

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        rid = req.get("id")
        method = getattr(self, f"rpc_{req.get('method')}", None)
        if method is None:
            return {"id": rid, "error": f"unknown method {req.get('method')!r}"}
        try:
            return {"id": rid, "result": await method(**(req.get("params") or {}))}
        except RpcError as e:
            return {"id": rid, "error": str(e)}
        except TypeError as e:
            return {"id": rid, "error": f"bad params: {e}"}
        except Exception as e:  # noqa: BLE001
            log.exception("rpc %s failed", req.get("method"))
            return {"id": rid, "error": f"{type(e).__name__}: {e}"}


_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # title sets etc.
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ESC_OTHER = re.compile(r"\x1b[ -/]*[0-~]")  # remaining ESC sequences (charset, keypad, …)


def _clean(text: str) -> str:
    """Strip ANSI/control bytes and cap width: pane output is untrusted everywhere but xterm.js."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return text[:200]


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentorc-agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the host agent (foreground)")
    sub.add_parser("rpc", help="stdin/stdout JSON-lines bridge to the local socket (used over ssh)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        agent = HostAgent()
        loop = asyncio.new_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, loop.stop)
        with contextlib.suppress(RuntimeError):
            loop.run_until_complete(agent.serve())
        return 0
    if args.cmd == "rpc":
        from sessionorc.client import bridge_stdio

        return asyncio.run(bridge_stdio())
    return 2


if __name__ == "__main__":
    sys.exit(main())
