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


class AgentStuck(AgentUnavailable):
    """The agent is **there and did not answer** — a call's bound ran out on a connection that
    never dropped (TD-063). A subclass, so every caller that handles *unavailable* keeps working,
    and so the one caller that retries can tell the two apart: a connection that dropped is a
    restart and is worth remaking, and a call that timed out on a live connection is a wedged
    agent, which remaking only hides (review of PR #315)."""


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


# How long a call waits for its reply before it is an error with a method attached (TD-063).
# **A hang is worth less than a failure**: `call` had no bound at all, so an RPC that never
# answered blocked for ever — a CI job that died after fourteen minutes naming the test *before*
# the one that stopped, and, worse, a live worker sitting in `ao send` that never comes back
# while its lead reads it as working. The number is the one the agent already gives an act over
# its own link (`agent.ACT_TIMEOUT`), so a call from here and a call one layer down wait alike;
# the RPCs that legitimately block longer pass their own.
CALL_TIMEOUT = 120.0


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

    async def call(self, method: str, *, _timeout: float | None = CALL_TIMEOUT, **params: Any) -> Any:
        """One request, one reply, **bounded** (`_timeout`, TD-063).

        **A parameter that is not set is not sent** (design §4.4, TD-062 fix (a)): every optional
        RPC parameter defaults to `None` on the agent side and means the same thing absent, so a
        `None` here is dropped from the envelope. That is what keeps a client newer than the
        running host agent working — a call that does not use a new parameter never mentions it,
        and so cannot be refused as an unexpected keyword. It is a rule of this one choke point on
        purpose: no command can forget it, and none may hand-roll the filtering instead.

        **A reply that never comes is an error naming the method**, not a wait without end. The
        default is the same bound the agent gives an act over its own link, and it is generous
        by a wide margin: every RPC but the ones below answers in milliseconds. The blocking
        ones pass their own — `wait` its timeout, `send --wait` its timeout plus slack — which
        is the shape `HostAgent._route_act` already gives those two calls one layer down (`_forward`,
        the other direction, gives `wait` no bound at all); `_timeout=None` waits for ever here
        too, deliberately, for a caller that means to. It is keyword-only and underscored
        because every other keyword here is an RPC parameter on its way to the wire.
        """
        assert self._reader and self._writer
        self._n += 1
        sent = {k: v for k, v in params.items() if v is not None}
        req: dict[str, Any] = {"id": self._n, "method": method, "params": sent}
        if self.caller:
            req["caller"] = self.caller
        self._writer.write((json.dumps(req) + "\n").encode())
        await self._writer.drain()
        try:
            line = await asyncio.wait_for(self._reader.readline(), _timeout)
        except TimeoutError:
            raise AgentStuck(
                f"the host agent did not answer {method!r} within {_timeout:g}s — it is stuck, not gone; "
                f"look at its journal (journalctl --user -u agentorc-agent) before retrying"
            ) from None
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


# How long a `wait` keeps trying to reconnect after the socket goes under it (design §4.8
# *Waking a lead*, TD-086). A promote restarts `agentorc-agent` and the unit is back in seconds
# (TD-062); eight promotes in one evening cost a lead its wake channel three times. Long enough
# for a restart under load, short enough that a real outage is still an error a person sees.
RECONNECT_GRACE = 30.0
RECONNECT_STEP = 0.25  # between attempts, so a restart that takes a moment is not a busy loop


async def wait_rpc(
    *,
    caller: str | None,
    timeout: float,
    scope: str | None = None,
    sock: Path | None = None,
    grace: float = RECONNECT_GRACE,
) -> tuple[Any, int]:
    """The `wait` RPC, **carried across a host-agent restart** (TD-086 item 1). Returns
    `(what wait returned, how many times the connection was actually remade)` — remade, not
    attempted: a restart takes a few tries at a quarter-second each, and counting those would
    report one promote as four (review of PR #303).

    A promote restarts the unit under every blocked wait, and the wait's own connection dies with
    it: a lead's wake channel then stays gone until its next round, by a routine act of the
    anchor's. Here a drop is not the end of the wait — the call is reissued, on a new connection,
    with **the time that is left of the caller's own timeout**, so what `wait` promises is
    unchanged: it returns on the first thing in scope, or at the timeout, and never later.

    **Nothing is missed across the gap.** The cursor is the agent's, per caller, written on every
    way out of `rpc_wait` and holding only what that wait *compared and found unchanged* — a
    change that arrived while nobody was connected is still ahead of it, so the reissued wait
    returns it at once. A `SIGKILL`, which runs no `finally`, leaves the cursor where the last
    wait that **returned** left it, which is the same answer from the other side. And **a
    reconnect is not a wake**: it decides nothing itself; the next wait takes the same decision
    the last one would have, against the same `mail_decided` watermark (§4.10 *the wake budget*).
    The one thing a reconnect cannot recover is a reply that was **composed and not delivered** —
    the agent died between returning a result, which advances the cursor, and the bytes reaching
    the socket. That window is a write to a local socket wide and is inherent to a request and a
    reply without an ack; it is named here rather than papered over (review of PR #303).

    **A drop is told apart from an agent that is not there.** The first connection is not retried:
    if nothing answers the socket, that is an agent which is down and the caller is told so, as
    before. Only a connection that was *made and then lost* is remade, and only within `grace` —
    past that the same refusal is raised, because at some point a restart is an outage.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(float(timeout), 0.0)
    remakes = 0
    connected = False
    lost_at: float | None = None  # when the socket went, so the grace is time and not attempts
    while True:
        left = deadline - loop.time()
        if left <= 0:
            return {"changed": [], "mail": [], "wake": None}, remakes
        try:
            async with LocalClient(sock=sock, caller=caller) as c:
                if lost_at is not None:
                    remakes += 1  # one per connection actually remade, not one per attempt
                connected, lost_at = True, None  # back: a later drop gets a grace of its own
                # its own bound plus slack: a `wait` is meant to block, and the client must not
                # give up on the very thing it is waiting for (TD-063)
                return await c.call("wait", timeout=left, scope=scope, _timeout=left + CALL_TIMEOUT), remakes
        except AgentStuck:
            # the agent is there and did not answer: remaking the connection would hide a wedged
            # agent behind a *nothing changed* at the deadline, which is worse than the hang this
            # whole function is about (review of PR #315)
            raise
        except AgentUnavailable:
            if not connected:
                raise  # nothing ever answered: an agent that is down, not one that restarted
            now = loop.time()
            lost_at = now if lost_at is None else lost_at
            if now - lost_at > grace:
                raise  # at some point a restart is an outage, and a person should be told
            await asyncio.sleep(min(RECONNECT_STEP, max(deadline - loop.time(), 0.0)))


def call_sync(method: str, *, caller: str | None = None, _timeout: float | None = CALL_TIMEOUT, **params: Any) -> Any:
    """One call on its own connection. **A `send --wait` is given its own bound plus slack**
    (TD-063): it is the one ordinary command that means to block, and a client that gave up at
    the default would abandon the very thing it asked for — the same rule `HostAgent._forward`
    gives the same call one layer down, in `_route_act`."""

    if method == "send" and params.get("wait") and _timeout is not None:
        theirs = params.get("timeout")
        _timeout = None if theirs is None else float(theirs) + CALL_TIMEOUT

    async def _go() -> Any:
        async with LocalClient(caller=caller) as c:
            return await c.call(method, _timeout=_timeout, **params)

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
