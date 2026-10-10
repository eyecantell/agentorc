"""The terminal bridge (design §4.6): one pty per open Focus terminal, wrapping `tmux attach`
(local transport) or `ssh -tt host tmux attach` (phase 2), pumped to a websocket.

`ptyprocess` owns the child pty (controlling tty, SIGWINCH, teardown); this module adds only the
asyncio read loop and the websocket framing. Resize is `setwinsize` on the local pty.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable, Callable

import ptyprocess

from sessionorc.tmux import attach_argv as tmux_attach_argv


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

    def exit_status(self) -> int | None:
        """The attach process's exit code once it is gone, None while it is alive. `tmux attach`
        exits non-zero when the session it was given is not there, which is how the bridge tells a
        dead attach from a person detaching (TD-029)."""
        try:
            if not self.proc.isalive():  # isalive() reaps, so exitstatus is set after it
                return self.proc.exitstatus
        except Exception:  # noqa: BLE001 — already gone, or never started: nothing to report
            return None
        return None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._loop.remove_reader(self.proc.fd)
        with contextlib.suppress(Exception):
            self.proc.terminate(force=True)


def attach_argv(session_id: str, *, socket_name: str | None = None) -> list[str]:
    return tmux_attach_argv(session_id, socket_name=socket_name)


def scroll_argv(
    session_id: str, direction: str, lines: int | None = None, *, socket_name: str | None = None
) -> list[str]:
    """Scrollback, reached through tmux (TD-022): Shift+PageUp/PageDown in the browser, and the wheel
    (design §4.6 *The mouse is the browser's*, TD-174). Without `lines` it is a page: up enters copy
    mode and pages up (repeatable: each call pages further); down pages down and, thanks to `-e`,
    leaves copy mode on reaching the live screen. With `lines` — the wheel's notches of one animation
    frame — it enters copy mode (`-e`, a no-op when already in it) and scrolls that many lines up or
    down, so a notch is lines, not a page, and reaching the bottom leaves copy mode as before. Down
    outside copy mode is a harmless "not in a mode"."""
    argv = ["tmux", "-L", socket_name] if socket_name else ["tmux"]
    target = f"={session_id}:"
    if direction not in ("up", "down"):
        raise ValueError(f"scroll direction {direction!r}: expected 'up' or 'down'")
    if lines is not None:
        if isinstance(lines, bool) or not isinstance(lines, int) or lines < 1:
            raise ValueError(f"scroll lines {lines!r}: expected a whole number from 1")
        n = str(min(lines, SCROLL_MAX))
        return argv + [
            "copy-mode",
            "-e",
            "-t",
            target,
            ";",
            "send-keys",
            "-X",
            "-N",
            n,
            "-t",
            target,
            f"scroll-{direction}",
        ]
    if direction == "up":
        return argv + ["copy-mode", "-e", "-u", "-t", target]
    return argv + ["send-keys", "-X", "-t", target, "page-down"]


SCROLL_MAX = 500  # lines in one wheel message: a frame's notches are a handful, so this only bounds a bad client


CLIENTS_EVERY = 5.0  # seconds between the bridge's readings of tmux's clients and window (design §4.6)


def clients_argv(session_id: str, *, socket_name: str | None = None) -> list[str]:
    """The reading behind the terminal mark *resized by another client* (design §4.6 *Attach behaviour
    with another client present*, TD-480): how many clients the session has attached, the window's
    size, and how many lines tmux's status bar takes from it."""
    argv = ["tmux", "-L", socket_name] if socket_name else ["tmux"]
    fmt = "#{session_attached} #{window_width} #{window_height} #{status}"
    return argv + ["display-message", "-p", "-t", f"={session_id}:", fmt]


def clients_frame(out: str) -> dict | None:
    """`clients_argv`'s output as the page's frame, `{"clients": n, "window": [cols, rows]}`, the rows
    counted as a client's grid counts them — the window's height and the status bar's lines (tmux's
    `status` option: `on` one, `off` none, a number that many). None for anything else."""
    parts = out.split()
    if len(parts) != 4 or not all(p.isdigit() for p in parts[:3]):
        return None
    n, w, h = (int(p) for p in parts[:3])
    st = parts[3]
    bar = 1 if st == "on" else 0 if st == "off" else int(st) if st.isdigit() else 0
    return {"clients": n, "window": [w, h + bar]}


async def watch_clients(
    read: Callable[[], Awaitable[str | None]],
    send_text: Callable[[str], Awaitable[object]],
    *,
    every: float = CLIENTS_EVERY,
) -> None:
    """While a terminal is attached, read tmux's clients and window every `every` seconds and send the
    page a text frame when the reading changes from the last one sent (the first always): the attach's
    own word, as `read_only` is, never pane output. A failed read sends nothing. Runs until cancelled,
    or until a send fails."""
    last = None
    while True:
        out = None
        with contextlib.suppress(Exception):
            out = await read()
        frame = clients_frame(out) if out else None
        if frame is not None and frame != last:
            try:
                await send_text(json.dumps(frame))
            except Exception:  # noqa: BLE001 — the socket went away: the pump's end cancels this anyway
                return
            last = frame
        await asyncio.sleep(every)


async def pump(
    pty: PtySession,
    send: Callable[[bytes], object],
    recv: Callable[[], object],
    scroll: Callable[[str, int | None], Awaitable[None]] | None = None,
    *,
    read_only: bool = False,
) -> None:
    """Run both directions until either side ends. `recv` yields str (keys), bytes, or a dict
    with `resize: [cols, rows]` or `scroll: "up" | "down"` and, for the wheel, `lines: n`; `send`
    takes raw bytes for xterm.js; `scroll` (optional) is awaited for scroll messages, which need a
    tmux command, not keys.

    `read_only` (design §4.6 *A read-only attach*, TD-096): key frames are dropped — str and bytes
    alike, every one: the wheel is a scroll message, never a mouse report (TD-174). Resize and
    scroll messages pass as ever."""

    async def down() -> None:
        try:
            while (chunk := await pty.read()) is not None:
                await send(chunk)  # type: ignore[misc]
        except Exception:  # noqa: BLE001 — the socket went away mid-send: that ends the pump, quietly
            return

    async def up() -> None:
        while True:
            msg = await recv()  # type: ignore[misc]
            if msg is None:
                return
            if isinstance(msg, dict):
                if "resize" in msg:
                    cols, rows = msg["resize"]
                    pty.resize(int(cols), int(rows))
                elif "scroll" in msg and scroll is not None:
                    lines = msg.get("lines")
                    # returns once the tmux command is spawned, not done; a page when `lines` is absent
                    await scroll(
                        str(msg["scroll"]), lines if isinstance(lines, int) and not isinstance(lines, bool) else None
                    )
                continue
            data = msg.encode() if isinstance(msg, str) else msg
            if read_only:
                continue
            pty.write(data)

    d = asyncio.create_task(down())
    u = asyncio.create_task(up())
    try:
        await asyncio.wait({d, u}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (d, u):
            t.cancel()
        pty.close()
