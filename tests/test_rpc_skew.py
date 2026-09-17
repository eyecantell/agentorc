"""Version skew between `ao` and the host agent it talks to (design §4.4, TD-062).

The live services run from an install a person promotes, so a merged RPC change reaches every
session's `ao` before the running host agent knows about it. Two halves keep that window harmless:
the client never sends a parameter it has not set, and the agent drops a parameter its method does
not take instead of refusing the whole call. The failure both close was real — PR #180 added
`force` to the `progress` RPC and every `ao progress` on two teams failed with *unexpected keyword
argument 'force'* for 31 minutes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from agentorc import cli
from sessionorc import client as clientmod
from sessionorc.client import LocalClient


@asynccontextmanager
async def recording_server(path, reply: dict) -> AsyncIterator[list[dict]]:
    """A stand-in host agent that records the envelope it was handed and answers `reply`.

    Torn down by hand rather than with `async with server`: on 3.12 `Server.close()` does not
    cancel the tasks running the handlers and `wait_closed()` then waits for them, so a handler
    still sitting in `readline()` hangs the test — a 15-minute CI hang on 3.12 while 3.13, which
    does cancel them, passed the same commit. Nothing here needs to await the handler: the client
    has its replies, and the test process is about to end.
    """
    seen: list[dict] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            req = json.loads(line)
            seen.append(req)
            writer.write((json.dumps({"id": req["id"], **reply}) + "\n").encode())
            await writer.drain()

    server = await asyncio.start_unix_server(handle, str(path))
    try:
        yield seen
    finally:
        server.close()


# ── the client half: a parameter that is not set is not sent ──────────────────────────────────


async def test_unset_parameters_are_left_out_of_the_envelope(tmp_path):
    async with recording_server(tmp_path / "sock", {"result": None}) as seen:
        async with LocalClient(sock=tmp_path / "sock") as c:
            await c.call("progress", id="ao-x", ref="TD-062", status="claimed", pr=None, why=None, force=None)
        assert seen[0]["params"] == {"id": "ao-x", "ref": "TD-062", "status": "claimed"}


async def test_a_set_but_falsy_parameter_is_still_sent(tmp_path):
    """The rule is *unset*, not *falsy*: `--lines 0`, `wait=False` and an empty string are choices
    the caller made, and an agent that defaults them differently must hear them."""
    async with recording_server(tmp_path / "sock", {"result": None}) as seen:
        async with LocalClient(sock=tmp_path / "sock") as c:
            await c.call("send", id="ao-x", text="", wait=False, timeout=0)
            await c.call("tail", id="ao-x", lines=0)
        assert seen[0]["params"] == {"id": "ao-x", "text": "", "wait": False, "timeout": 0}
        assert seen[1]["params"] == {"id": "ao-x", "lines": 0}  # not the agent's default of 40


def test_progress_leaves_force_unset_unless_asked(monkeypatch, tmp_path):
    """The command whose new parameter broke the teams: without `--force` it passes `None`, which
    the client then drops, so a host agent that predates `force` answers the call."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(cli, "call_sync", lambda m, **p: (calls.append((m, p)), {"id": "ao-x", "progress": []})[1])
    monkeypatch.setenv("AGENTORC_SESSION", "ao-x")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["progress", "claim", "TD-062", "--json"]) == 0
    assert calls[-1][1]["force"] is None
    assert cli.main(["progress", "claim", "TD-062", "--force", "--json"]) == 0
    assert calls[-1][1]["force"] is True


# ── the agent half: an unknown parameter is dropped and named, not refused ────────────────────


@pytest.mark.integration
async def test_agent_ignores_a_parameter_its_method_does_not_take(agent, tmp_path):
    """A client newer than this agent calls `progress` with a parameter added after it started."""
    clientmod.last_ignored = []
    async with LocalClient() as c:
        s = await c.call("create", name="skew", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        got = await c.call("progress", id=s["id"], ref="TD-062", status="claimed", from_the_future=True)
        assert got["progress"][0]["ref"] == "TD-062"
        assert clientmod.last_ignored == ["from_the_future"]
        # A later call in the same command that has nothing ignored must not clear it: the warning
        # belongs to the command, not to its last RPC (review of PR #197). `ao team start` and
        # `ao control … add <many>` are several calls, and the first is the one that skews.
        await c.call("get", id=s["id"])
        assert clientmod.last_ignored == ["from_the_future"]
        await c.call("get", id=s["id"], also_from_the_future=1)
        assert clientmod.last_ignored == ["from_the_future", "also_from_the_future"]
        await c.call("get", id=s["id"], also_from_the_future=1)  # deduped, so a long-lived client is bounded
        assert clientmod.last_ignored == ["from_the_future", "also_from_the_future"]
        await c.call("kill", id=s["id"])


@pytest.mark.integration
async def test_a_refusal_still_names_what_was_ignored(agent):
    """The drop happens before the gate, so an unknown parameter never turns a refusal into a
    `bad params` error that hides why the call was refused."""
    clientmod.last_ignored = []
    async with LocalClient() as c:
        with pytest.raises(Exception, match="no session ao-nope"):
            await c.call("get", id="ao-nope", from_the_future=True)
        assert clientmod.last_ignored == ["from_the_future"]


@pytest.mark.integration
async def test_a_method_taking_kwargs_keeps_everything(agent, tmp_path):
    """`rpc_hook` takes `**event`: every field of a hook payload is meant for it, so nothing is
    dropped and nothing is reported."""
    clientmod.last_ignored = []
    async with LocalClient() as c:
        s = await c.call("create", name="skewhook", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        await c.call("hook", session=s["id"], event="Stop", anything="kept")
        assert clientmod.last_ignored == []
        await c.call("kill", id=s["id"])
