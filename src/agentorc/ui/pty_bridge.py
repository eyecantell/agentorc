"""The terminal bridge (design §4.6): one pty per open Focus terminal, wrapping `tmux attach`
(local transport) or `ssh -tt host tmux attach` (phase 2), pumped to a websocket.

`ptyprocess` owns the child pty (controlling tty, SIGWINCH, teardown); this module adds only the
asyncio read loop and the websocket framing. Resize is `setwinsize` on the local pty.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable

import ptyprocess


class PtySession:
    def __init__(self, argv: list[str], *, cols: int = 120, rows: int = 32, env: dict[str, str] | None = None):
        self.proc = ptyprocess.PtyProcess.spawn(
            argv, dimensions=(rows, cols), env={**os.environ, "TERM": "xterm-256color", **(env or {})}
        )
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._loop.add_reader(self.proc.fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self.proc.fd, 65536)
        except OSError:
            data = b""
        if not data:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self.proc.fd)
            self._queue.put_nowait(None)
            return
        self._queue.put_nowait(data)

    async def read(self) -> bytes | None:
        """Next chunk, or None when the child is gone."""
        return await self._queue.get()

    def write(self, data: bytes) -> None:
        if self.proc.isalive():
            self.proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        with contextlib.suppress(Exception):
            self.proc.setwinsize(max(2, rows), max(10, cols))

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._loop.remove_reader(self.proc.fd)
        with contextlib.suppress(Exception):
            self.proc.terminate(force=True)


def attach_argv(session_id: str, *, socket_name: str | None = None) -> list[str]:
    argv = ["tmux"]
    if socket_name:
        argv += ["-L", socket_name]
    return argv + ["attach", "-t", f"={session_id}:"]


async def pump(pty: PtySession, send: Callable[[bytes], object], recv: Callable[[], object]) -> None:
    """Run both directions until either side ends. `recv` yields str (keys), bytes, or a dict
    with `resize: [cols, rows]`; `send` takes raw bytes for xterm.js."""

    async def down() -> None:
        while (chunk := await pty.read()) is not None:
            await send(chunk)  # type: ignore[misc]

    async def up() -> None:
        while True:
            msg = await recv()  # type: ignore[misc]
            if msg is None:
                return
            if isinstance(msg, dict):
                if "resize" in msg:
                    cols, rows = msg["resize"]
                    pty.resize(int(cols), int(rows))
                continue
            pty.write(msg.encode() if isinstance(msg, str) else msg)

    d = asyncio.create_task(down())
    u = asyncio.create_task(up())
    try:
        await asyncio.wait({d, u}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (d, u):
            t.cancel()
        pty.close()
