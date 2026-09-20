"""The link between a node and its home (design §4.4a "The link's protocol", TD-057 step 3a).

Three pieces, none of which knows what a session is:

- `Mux` — one JSON object per line over a reader/writer pair, multiplexed in both directions:
  `{"id", "method", "params"}` is a request, `{"re", "result"|"error"}` its reply, a method with no
  `id` a notification. `re`, not a shared `id`, because both ends number their own requests from
  one. Requests are served concurrently; silence longer than `LINK_SILENCE` ends the link.
- `bridge` — what sshd's forced command runs at the home: announce the host name the key is bound
  to, then copy lines between stdio and the home agent's socket.
- `dial` — the node's loop: open the transport, say `hello`, ping, and start over with backoff when
  it ends, keeping *why* it is down in words (§4.6: ssh failed / agent down / refused). The
  transport is a command (ssh to the home, the bridge at its end) or, for a container node on the
  home's own machine, the home's per-node link socket reached through a mounted directory
  (§4.4a "A container node", step 3c) — *cannot connect* in place of *ssh failed*.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sessionorc import paths
from sessionorc.client import LINE_LIMIT

log = logging.getLogger("agentorc.link")

PROTOCOL = 1
LINK_PING = 15.0  # seconds between a node's pings
LINK_SILENCE = 45.0  # seconds without a frame before either end gives the link up
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0
# One frame is one line, and asyncio's default line limit is 64 KiB — a node's snapshot of its
# records (step 3b) is larger than that. Every stream a frame crosses is opened with this limit,
# and it is the client's, so a reply the agent writes is one every reader can read (TD-066).
FRAME_LIMIT = LINE_LIMIT
STDERR_KEPT = 20  # lines of the transport's stderr kept for the diagnosis

Handler = Callable[[str, dict[str, Any]], Awaitable[Any]]
Target = list[str] | Path  # a transport command, or the home's link socket


class LinkError(Exception):
    """The other end answered a request with an error."""


class LinkClosed(Exception):
    """The link ended while a request was outstanding, or before one could be sent."""


class FrameTooLarge(LinkError):
    """A frame this end refused to write because the other end could not have read it."""


class Mux:
    """One end of a link. `handler(method, params)` serves the other end's requests; an exception
    it raises is sent back as that request's error, never allowed to end the link."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        handler: Handler,
        *,
        silence: float | None = None,
        stray: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.reader, self.writer, self.handler = reader, writer, handler
        self.stray = stray  # a frame that is neither a reply nor a request: the bridge's `link_error`
        self.silence = LINK_SILENCE if silence is None else silence
        self._next = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._serving: set[asyncio.Task[None]] = set()
        self._heard = time.monotonic()
        self.closed = False

    async def run(self) -> str:
        """Read frames until the link ends; return why, in words. Always leaves the link closed and
        every outstanding request failed with `LinkClosed`."""
        why = "closed by the other end"
        try:
            while True:
                try:
                    left = self.silence - (time.monotonic() - self._heard)
                    line = await asyncio.wait_for(self.reader.readline(), timeout=max(left, 0.05))
                except TimeoutError:
                    why = f"no frame for {self.silence:g} s"
                    break
                except ValueError:
                    # a line past `FRAME_LIMIT`: the stream cannot be re-framed after it, so the link
                    # ends — with a reason, never an exception that would end the dialer for good
                    why = f"a frame longer than {FRAME_LIMIT} bytes"
                    break
                if not line:
                    break
                self._heard = time.monotonic()
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue  # a stray line (an ssh banner, a login message) is not a frame
                if isinstance(frame, dict):
                    self._take(frame)
        except (ConnectionError, asyncio.IncompleteReadError) as e:
            why = f"connection lost: {e}"
        finally:
            self.close(why)
        return why

    def _take(self, frame: dict[str, Any]) -> None:
        if "re" in frame:
            fut = self._pending.pop(frame["re"], None)
            if fut is not None and not fut.done():
                if "error" in frame:
                    fut.set_exception(LinkError(str(frame["error"])))
                else:
                    fut.set_result(frame.get("result"))
            return
        method = frame.get("method")
        if not isinstance(method, str):
            if self.stray is not None:
                self.stray(frame)
            return
        task = asyncio.ensure_future(self._serve(frame.get("id"), method, frame.get("params") or {}))
        self._serving.add(task)
        task.add_done_callback(self._serving.discard)

    async def _serve(self, rid: Any, method: str, params: dict[str, Any]) -> None:
        try:
            reply: dict[str, Any] = {"re": rid, "result": await self.handler(method, params)}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a handler's failure is that request's error
            if not isinstance(e, LinkError):
                log.exception("link request %s failed", method)
            reply = {"re": rid, "error": str(e) or type(e).__name__}
        if rid is not None:
            with contextlib.suppress(LinkClosed):
                await self._write(reply)

    async def _write(self, frame: dict[str, Any]) -> None:
        if self.closed:
            raise LinkClosed("the link is closed")
        line = (json.dumps(frame) + "\n").encode()
        if len(line) > FRAME_LIMIT:
            # A frame past the limit is one the other end cannot read: its reader raises and the
            # link ends there, with nothing on this side to say which frame did it. So it is
            # refused here instead, where the method is still in hand (TD-066). A reply becomes an
            # error reply — the request is answered, and the link survives; a request or a
            # notification raises, which every caller of `request` already handles as a LinkError.
            why = f"{len(line)} bytes, past the {FRAME_LIMIT}-byte frame limit"
            what = frame.get("method") or f"the reply to {frame.get('re')}"
            log.error("refusing to write %s: %s", what, why)
            if "re" in frame and "error" not in frame:
                await self._write({"re": frame["re"], "error": f"the reply is too large to send: {why}"})
                return
            raise FrameTooLarge(f"{what} is too large to send: {why}")
        try:
            self.writer.write(line)
            await self.writer.drain()
        except (ConnectionError, RuntimeError, OSError) as e:
            self.close(f"write failed: {e}")
            raise LinkClosed(str(e)) from e

    async def request(self, method: str, timeout: float | None = None, **params: Any) -> Any:
        self._next += 1
        rid = self._next
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._write({"id": rid, "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, **params: Any) -> None:
        await self._write({"method": method, "params": params})

    def close(self, why: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(LinkClosed(why))
        self._pending.clear()
        for task in list(self._serving):
            task.cancel()
        with contextlib.suppress(Exception):
            self.writer.close()


# -- the home's end of the transport: sshd's forced command ----------------------------------------


async def bridge(host: str) -> int:
    """`agentorc-agent link --host <name>`: tell the home agent which host this connection is bound
    to — the name comes from `authorized_keys`, never from the node — then copy lines both ways.
    Exit 1 with one error frame when the agent's socket cannot be reached, which is how the node
    tells *agent down* from *ssh failed*."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(paths.socket_path()), limit=FRAME_LIMIT)
    except (ConnectionError, FileNotFoundError, OSError) as e:
        sys.stdout.write(json.dumps({"link_error": "agent down", "detail": str(e)}) + "\n")
        sys.stdout.flush()
        return 1
    writer.write((json.dumps({"link": {"host": host}}) + "\n").encode())
    await writer.drain()
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader(limit=FRAME_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async def up() -> None:
        with contextlib.suppress(ValueError):  # a line past the limit ends the bridge, and so the link
            while line := await stdin.readline():
                writer.write(line)
                await writer.drain()

    async def down() -> None:
        with contextlib.suppress(ValueError):
            while line := await reader.readline():
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()

    # Either direction ending ends the link: the node went away, or the home closed it.
    tasks = [asyncio.ensure_future(up()), asyncio.ensure_future(down())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    with contextlib.suppress(Exception):
        writer.close()
    return 0


# -- the node's end: the dialer ---------------------------------------------------------------------


class _PipeWriter:
    """A subprocess's stdin, with the two methods `Mux` uses."""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc

    def write(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)

    async def drain(self) -> None:
        assert self.proc.stdin is not None
        await self.proc.stdin.drain()

    def close(self) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            self.proc.terminate()


def backoff_delays(first: float = BACKOFF_FIRST, top: float = BACKOFF_MAX):
    """1 s doubling to 60 s, each with up to 25 % jitter so two nodes never retry in step."""
    delay = first
    while True:
        yield delay * (1 + random.random() / 4)
        delay = min(delay * 2, top)


async def dial(
    target: Target,
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None = None,
    silence: float | None = None,
    first: float = BACKOFF_FIRST,
    top: float = BACKOFF_MAX,
) -> None:
    """Keep a link to the home up, forever: open `target` (run a command, or connect to a socket),
    say `hello`, ping, and when it ends say why and try again. `on_state(up, why, mux)` is called
    on every change. Runs until cancelled."""
    delays = backoff_delays(first, top)
    while True:
        try:
            was_up, why = await _dial_once(
                target, host=host, handler=handler, on_state=on_state, ping=ping, silence=silence
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — nothing may end the dialer: a node with no dialer never returns
            log.exception("link attempt failed")
            was_up, why = False, f"dialer error: {type(e).__name__}: {e}"
        if was_up:  # the link had been up: start the backoff over
            delays = backoff_delays(first, top)
            why = f"the link dropped: {why}"
        on_state(False, why, None)
        await asyncio.sleep(next(delays))


async def _dial_once(
    target: Target,
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None,
    silence: float | None,
) -> tuple[bool, str]:
    """One attempt: whether the link came up, and why it is down now."""
    if isinstance(target, Path):
        return await _dial_socket(target, host=host, handler=handler, on_state=on_state, ping=ping, silence=silence)
    return await _dial_command(target, host=host, handler=handler, on_state=on_state, ping=ping, silence=silence)


async def _dial_socket(
    path: Path,
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None,
    silence: float | None,
) -> tuple[bool, str]:
    """A container node's attempt (§4.4a): the home's per-node socket, straight to `_serve_link` at
    its end — there is no bridge, so *agent down* and *ssh failed* collapse into *cannot connect*:
    the home is not running, or the directory is not mounted."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(path), limit=FRAME_LIMIT)
    except (ConnectionError, OSError) as e:
        return False, f"cannot connect: {path}: {e}"

    async def closed_early() -> str:
        return f"cannot connect: {path} closed before answering hello"

    mux = Mux(reader, writer, handler, silence=silence)
    try:
        return await _converse(mux, host=host, on_state=on_state, ping=ping, silence=silence, closed_early=closed_early)
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def _dial_command(
    command: list[str],
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None,
    silence: float | None,
) -> tuple[bool, str]:
    """An ssh node's attempt: run the transport, whose far end is the bridge."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=FRAME_LIMIT,
        )
    except OSError as e:
        return False, f"ssh failed: cannot run {command[0]}: {e}"
    assert proc.stdout is not None
    # Read for as long as the process lives: a transport that fills an unread stderr pipe blocks,
    # and the link with it, days after it came up. The last lines are the diagnosis.
    errors: list[str] = []
    drain = asyncio.ensure_future(_drain_stderr(proc, errors))
    refusal: list[str] = []

    def stray(frame: dict[str, Any]) -> None:
        # The bridge's one non-frame: it reached the machine and not the agent's socket.
        if "link_error" in frame:
            refusal.append(f"agent down on the home: {frame.get('detail') or frame['link_error']}")

    async def closed_early() -> str:
        if refusal:
            return refusal[0]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(drain), timeout=1)
        return "ssh failed" + (f": {errors[-1]}" if errors else f" (exit {proc.returncode})")

    mux = Mux(proc.stdout, _PipeWriter(proc), handler, silence=silence, stray=stray)
    try:
        return await _converse(mux, host=host, on_state=on_state, ping=ping, silence=silence, closed_early=closed_early)
    finally:
        drain.cancel()
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()


async def _converse(
    mux: Mux,
    *,
    host: str,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None,
    silence: float | None,
    closed_early: Callable[[], Awaitable[str]],
) -> tuple[bool, str]:
    """The link itself, whatever carries it: `hello`, then ping until it ends. `closed_early` names
    the reason when the other end went away before answering hello."""
    runner = asyncio.ensure_future(mux.run())
    try:
        try:
            # `build`: what this agent was started on, when a home provisions it (a container node):
            # the home re-provisions one that is behind — a promote changes no protocol number
            answer = await mux.request(
                "hello",
                timeout=silence or LINK_SILENCE,
                protocol=PROTOCOL,
                host=host,
                build=os.environ.get("AGENTORC_BUILD", ""),
            )
        except LinkError as e:
            return False, f"refused: {e}"
        except (LinkClosed, TimeoutError):
            return False, await closed_early()
        on_state(True, f"linked to {(answer or {}).get('home', 'home')} as {(answer or {}).get('host', host)}", mux)
        pinger = asyncio.ensure_future(_ping(mux, LINK_PING if ping is None else ping))
        try:
            return True, await runner
        finally:
            pinger.cancel()
    finally:
        mux.close()
        runner.cancel()


async def _ping(mux: Mux, every: float) -> None:
    with contextlib.suppress(LinkClosed, LinkError, TimeoutError):
        while True:
            await asyncio.sleep(every)
            await mux.request("ping", timeout=mux.silence)


async def _drain_stderr(proc: asyncio.subprocess.Process, kept: list[str]) -> None:
    if proc.stderr is None:
        return
    with contextlib.suppress(ValueError, ConnectionError):
        while line := await proc.stderr.readline():
            text = line.decode(errors="replace").strip()
            if text:
                kept.append(text)
                del kept[:-STDERR_KEPT]
