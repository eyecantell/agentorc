"""The two clients of the host agent: `call_sync` (one connection per call) and the stdio bridge
that `ssh host agentorc-agent rpc` carries (design §4.6)."""

import contextlib
import json
import os
import subprocess
import sys

import pytest

from sessionorc.client import AgentUnavailable, call_sync

pytestmark = pytest.mark.integration


def test_call_sync_roundtrip(subprocess_agent, tmp_path):
    assert call_sync("ping") == "pong"
    s = call_sync("create", name="cs", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
    assert s["id"] in [x["id"] for x in call_sync("list")]
    call_sync("kill", id=s["id"])


def test_call_sync_agent_down(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "nohome"))
    with pytest.raises(AgentUnavailable, match="not reachable"):
        call_sync("ping")


def rpc_bridge(lines: list[dict], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """One bridge process, JSON lines on stdin, JSON lines back. The child reads `AGENTORC_HOME`
    from its own environment: with `env=None` it inherits the test process's (the fixture's)."""
    return subprocess.run(
        [sys.executable, "-m", "sessionorc.agent", "rpc"],
        input="".join(json.dumps(x) + "\n" for x in lines),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_rpc_bridge_pumps_lines(subprocess_agent, tmp_path):
    cp = rpc_bridge(
        [
            {"id": 1, "method": "ping", "params": {}},
            {"id": 2, "method": "get", "params": {"id": "ao-nope"}},
            {"id": 3, "method": "list", "params": {}},
        ]
    )
    assert cp.returncode == 0, cp.stderr
    out = [json.loads(line) for line in cp.stdout.splitlines()]
    assert out[0]["id"] == 1 and out[0]["result"] == "pong"
    assert out[1]["id"] == 2 and "error" in out[1]
    assert out[2]["id"] == 3 and isinstance(out[2]["result"], list)


def test_rpc_bridge_agent_down(tmp_path):
    env = {**os.environ, "AGENTORC_HOME": str(tmp_path / "nohome")}
    cp = rpc_bridge([{"id": 1, "method": "ping", "params": {}}], env=env)
    assert cp.returncode == 1
    assert json.loads(cp.stdout.strip())["error"].startswith("agent down")


async def test_a_call_that_is_never_answered_is_an_error_naming_the_method(tmp_path, monkeypatch):
    """TD-063: `call` had **no bound at all**, so an RPC whose reply never came blocked for ever
    in `readline()`. That is why a hang leaves no evidence — every bound in a test is on the
    test's own side, so it cannot hang inside its own assertions, only inside a call — and it is
    worse than a test problem: a live worker sitting in `ao send` never comes back, while its
    lead reads it as working. A bound turns it into an error that names the method.

    The blocking calls keep their own: `wait` passes its timeout plus slack, and so does a
    `send --wait`, which is the rule the agent already follows for an act over its own link."""
    import asyncio

    from sessionorc import client as clientmod

    sock = tmp_path / "quiet.sock"

    async def never_answers(reader, writer):
        await reader.readline()  # take the request and say nothing at all
        with contextlib.suppress(Exception):
            await asyncio.sleep(5)  # hold it open — a close is a different error — but not for ever

    server = await asyncio.start_unix_server(never_answers, path=str(sock))
    try:
        async with clientmod.LocalClient(sock=sock) as c:
            with pytest.raises(clientmod.AgentUnavailable, match="did not answer 'list' within 0.2s"):
                await c.call("list", _timeout=0.2)
            # and a caller that means to wait for ever still may
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(c.call("list", _timeout=None), 0.3)
    finally:
        # not `wait_closed()`: on 3.12 it waits for the handlers, and this one is asleep by
        # design — the very shape TD-058 fixed in the agent (review of PR #315)
        server.close()


def test_send_with_wait_is_given_its_own_bound_plus_slack(monkeypatch):
    """TD-063: the one ordinary command that means to block. A client that gave up at the default
    would abandon the very thing it asked for, so the bound is the caller's own plus slack."""
    from sessionorc import client as clientmod

    seen = {}

    class Fake:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def call(self, method, **params):
            seen.update({"method": method, **params})
            return None

    monkeypatch.setattr(clientmod, "LocalClient", lambda **kw: Fake())
    clientmod.call_sync("send", id="ao-x", text="go", wait=True, timeout=5)
    assert seen["_timeout"] == 5 + clientmod.CALL_TIMEOUT
    clientmod.call_sync("send", id="ao-x", text="go", wait=True, timeout=None)
    assert seen["_timeout"] is None  # no timeout of its own: the caller means to wait
    clientmod.call_sync("send", id="ao-x", text="go")
    assert seen["_timeout"] == clientmod.CALL_TIMEOUT  # without --wait it is an ordinary call
