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


class AgentError(Exception):
    pass


class AgentUnavailable(AgentError):
    pass


class LocalClient:
    """One connection, sequential requests. Cheap enough to open per CLI call."""

    def __init__(self, sock: Path | None = None):
        self.sock = sock or paths.socket_path()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._n = 0

    async def __aenter__(self) -> LocalClient:
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(str(self.sock))
        except (ConnectionError, FileNotFoundError, OSError) as e:
            raise AgentUnavailable(f"host agent not reachable at {self.sock}: {e}") from e
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

    async def call(self, method: str, **params: Any) -> Any:
        assert self._reader and self._writer
        self._n += 1
        self._writer.write((json.dumps({"id": self._n, "method": method, "params": params}) + "\n").encode())
        await self._writer.drain()
        line = await self._reader.readline()
        if not line:
            raise AgentUnavailable("host agent closed the connection")
        resp = json.loads(line)
        if "error" in resp:
            raise AgentError(resp["error"])
        return resp.get("result")

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Yield session/gone events until the connection drops."""
        await self.call("subscribe")
        assert self._reader
        while line := await self._reader.readline():
            yield json.loads(line)


def call_sync(method: str, **params: Any) -> Any:
    async def _go() -> Any:
        async with LocalClient() as c:
            return await c.call(method, **params)

    return asyncio.run(_go())


async def bridge_stdio() -> int:
    """Pump stdin → socket → stdout, line for line. `ssh host agentorc-agent rpc` is this."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(paths.socket_path()))
    except (ConnectionError, FileNotFoundError, OSError) as e:
        sys.stdout.write(json.dumps({"error": f"agent down: {e}"}) + "\n")
        sys.stdout.flush()
        return 1
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async def up() -> None:
        while line := await stdin.readline():
            writer.write(line)
            await writer.drain()
        writer.close()

    async def down() -> None:
        while line := await reader.readline():
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    await asyncio.gather(up(), down(), return_exceptions=True)
    return 0
