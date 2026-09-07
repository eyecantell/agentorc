"""The two clients of the host agent: `call_sync` (one connection per call) and the stdio bridge
that `ssh host agentorc-agent rpc` carries (design §4.6)."""

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
