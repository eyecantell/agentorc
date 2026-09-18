"""Clients of the host agent: local (Unix socket) and the stdio bridge that ssh carries (design §4.6)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sessionorc import paths

# One reply is one line, and asyncio's default line limit is 64 KiB. A `list` of six records after a
# day of two teams' mail crossed it on 2026-09-17 and every `ao` on the machine failed with
# `Separator is found, but chunk is longer than limit` (TD-066). The same limit the link uses.
LINE_LIMIT = 8 * 1024 * 1024


class AgentError(Exception):
    """An error the agent returned. `data` is whatever it sent alongside the message — the id of
    the session holding a name, say — so a caller can act on it instead of parsing prose."""

    def __init__(self, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.data = data or {}


class AgentUnavailable(AgentError):
    pass


# The `mail` field of the last response any client in this process read (design §4.10 "a line on
# every `ao` reply"): `{"unread": N, "wake_budget_spent": bool}` while the calling session has
# unread mail, None otherwise. A CLI makes one or a few calls per command and prints the line once,
# from the last of them, after its own output.
last_mail: dict[str, Any] | None = None

# Every parameter name the running host agent did not take, across every call this process has made
# since it last reset this (design §4.4, TD-062): dropped by the agent rather than refused. Non-empty
# means the agent is older than this client and a CLI prints one line from it. **Accumulated**, not
# last-call-wins like `last_mail`: a command that makes several calls — `ao team start` (a
# `name_check` and a `create` per member), `ao control … add <many>` — would otherwise have the
# warning from its first call cleared by its last, which is exactly where an operator most needs it.
# Deduped, so a long-lived client (the UI) is bounded by the number of distinct parameter names.
last_ignored: list[str] = []


class LocalClient:
    """One connection, sequential requests. Cheap enough to open per CLI call."""

    def __init__(self, sock: Path | None = None, caller: str | None = None):
        self.sock = sock or paths.socket_path()
        # The calling session's id, sent as the envelope's `caller` (design §4.8): the agent gates
        # acting RPCs on another session by it. None is a person at a terminal or the UI.
        self.caller = caller
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._n = 0

    async def __aenter__(self) -> LocalClient:
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(str(self.sock), limit=LINE_LIMIT)
        except (ConnectionError, FileNotFoundError, OSError) as e:
            raise AgentUnavailable(f"host agent not reachable at {self.sock}: {e}") from e
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

    async def call(self, method: str, **params: Any) -> Any:
        """One request, one reply.

        **A parameter that is not set is not sent** (design §4.4, TD-062 fix (a)): every optional
        RPC parameter defaults to `None` on the agent side and means the same thing absent, so a
        `None` here is dropped from the envelope. That is what keeps a client newer than the
        running host agent working — a call that does not use a new parameter never mentions it,
        and so cannot be refused as an unexpected keyword. It is a rule of this one choke point on
        purpose: no command can forget it, and none may hand-roll the filtering instead.
        """
        assert self._reader and self._writer
        self._n += 1
        sent = {k: v for k, v in params.items() if v is not None}
        req: dict[str, Any] = {"id": self._n, "method": method, "params": sent}
        if self.caller:
            req["caller"] = self.caller
        self._writer.write((json.dumps(req) + "\n").encode())
        await self._writer.drain()
        line = await self._reader.readline()
        if not line:
            raise AgentUnavailable("host agent closed the connection")
        resp = json.loads(line)
        global last_mail, last_ignored
        last_mail = resp.get("mail") if isinstance(resp, dict) else None
        for name in (resp.get("ignored") or []) if isinstance(resp, dict) else []:
            if name not in last_ignored:
                last_ignored.append(name)
        if "error" in resp:
            raise AgentError(resp["error"], resp.get("error_data"))
        return resp.get("result")

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield session/gone events until the connection drops."""
        await self.call("subscribe")
        assert self._reader
        while line := await self._reader.readline():
            yield json.loads(line)


def call_sync(method: str, *, caller: str | None = None, **params: Any) -> Any:
    async def _go() -> Any:
        async with LocalClient(caller=caller) as c:
            return await c.call(method, **params)

    return asyncio.run(_go())


async def bridge_stdio() -> int:
    """Pump stdin → socket → stdout, line for line. `ssh host agentorc-agent rpc` is this."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(paths.socket_path()), limit=LINE_LIMIT)
    except (ConnectionError, FileNotFoundError, OSError) as e:
        sys.stdout.write(json.dumps({"error": f"agent down: {e}"}) + "\n")
        sys.stdout.flush()
        return 1
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader(limit=LINE_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async def up() -> None:
        while line := await stdin.readline():
            writer.write(line)
            await writer.drain()
        # Half-close: stdin at EOF means no more requests, but replies to the ones already sent
        # may still be on their way. A full close here raced them (a piped `rpc` returned nothing).
        writer.write_eof()

    async def down() -> None:
        while line := await reader.readline():
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    await asyncio.gather(up(), down(), return_exceptions=True)
    return 0
