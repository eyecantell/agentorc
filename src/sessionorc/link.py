"""The link between a node and its home (design §4.4a "The link's protocol", TD-057 step 3a).

Three pieces, none of which knows what a session is:

- `Mux` — one JSON object per line over a reader/writer pair, multiplexed in both directions:
  `{"id", "method", "params"}` is a request, `{"re", "result"|"error"}` its reply, a method with no
  `id` a notification. `re`, not a shared `id`, because both ends number their own requests from
  one. Requests are served concurrently; silence longer than `LINK_SILENCE` ends the link.
- `bridge` — what sshd's forced command runs at the home: announce the host name the key is bound
  to, then copy lines between stdio and the home agent's socket.
- `dial` — the node's loop: run the transport, say `hello`, ping, and start over with backoff when
  it ends, keeping *why* it is down in words (§4.6: ssh failed / agent down / refused).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sessionorc import paths

log = logging.getLogger("agentorc.link")

PROTOCOL = 1
LINK_PING = 15.0  # seconds between a node's pings
LINK_SILENCE = 45.0  # seconds without a frame before either end gives the link up
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0

Handler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class LinkError(Exception):
    """The other end answered a request with an error."""


class LinkClosed(Exception):
    """The link ended while a request was outstanding, or before one could be sent."""


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
        try:
            self.writer.write((json.dumps(frame) + "\n").encode())
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
        reader, writer = await asyncio.open_unix_connection(str(paths.socket_path()))
    except (ConnectionError, FileNotFoundError, OSError) as e:
        sys.stdout.write(json.dumps({"link_error": "agent down", "detail": str(e)}) + "\n")
        sys.stdout.flush()
        return 1
    writer.write((json.dumps({"link": {"host": host}}) + "\n").encode())
    await writer.drain()
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async def up() -> None:
        while line := await stdin.readline():
            writer.write(line)
            await writer.drain()

    async def down() -> None:
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
    command: list[str],
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None = None,
    silence: float | None = None,
    first: float = BACKOFF_FIRST,
    top: float = BACKOFF_MAX,
) -> None:
    """Keep a link to the home up, forever: run `command`, say `hello`, ping, and when it ends say
    why and try again. `on_state(up, why, mux)` is called on every change. Runs until cancelled."""
    delays = backoff_delays(first, top)
    while True:
        why = await _dial_once(command, host=host, handler=handler, on_state=on_state, ping=ping, silence=silence)
        if why is None:  # the link was up and ended: start the backoff over
            delays = backoff_delays(first, top)
            why = "the link dropped"
        on_state(False, why, None)
        await asyncio.sleep(next(delays))


async def _dial_once(
    command: list[str],
    *,
    host: str,
    handler: Handler,
    on_state: Callable[[bool, str, Mux | None], None],
    ping: float | None,
    silence: float | None,
) -> str | None:
    """One attempt. Returns why it failed, or None when the link came up and later ended."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as e:
        return f"ssh failed: cannot run {command[0]}: {e}"
    assert proc.stdout is not None
    refusal: list[str] = []

    def stray(frame: dict[str, Any]) -> None:
        # The bridge's one non-frame: it reached the machine and not the agent's socket.
        if "link_error" in frame:
            refusal.append(f"agent down on the home: {frame.get('detail') or frame['link_error']}")

    mux = Mux(proc.stdout, _PipeWriter(proc), handler, silence=silence, stray=stray)
    runner = asyncio.ensure_future(mux.run())
    up = False
    try:
        try:
            answer = await mux.request("hello", timeout=silence or LINK_SILENCE, protocol=PROTOCOL, host=host)
        except LinkError as e:
            return f"refused: {e}"
        except (LinkClosed, TimeoutError):
            if refusal:
                return refusal[0]
            await asyncio.wait({asyncio.ensure_future(proc.wait())}, timeout=2)
            err = (await _stderr(proc)).strip().splitlines()
            return "ssh failed" + (f": {err[-1]}" if err else f" (exit {proc.returncode})")
        up = True
        on_state(True, f"linked to {(answer or {}).get('home', 'home')} as {(answer or {}).get('host', host)}", mux)
        pinger = asyncio.ensure_future(_ping(mux, LINK_PING if ping is None else ping))
        try:
            await runner
        finally:
            pinger.cancel()
        return None
    finally:
        mux.close()
        runner.cancel()
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        if not up:
            log.debug("link attempt ended before hello")


async def _ping(mux: Mux, every: float) -> None:
    with contextlib.suppress(LinkClosed, LinkError, TimeoutError):
        while True:
            await asyncio.sleep(every)
            await mux.request("ping", timeout=mux.silence)


async def _stderr(proc: asyncio.subprocess.Process) -> str:
    if proc.stderr is None:
        return ""
    try:
        return (await asyncio.wait_for(proc.stderr.read(4096), timeout=1)).decode(errors="replace")
    except TimeoutError:
        return ""
