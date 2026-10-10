"""TD-465 slice 1, design §4.7 **`ao doctor`**: the never-gated `doctor` RPC gathers the host-side
readings — tmux against the server first read, the hooks, identity, each profile's credentials and
usage (one request per account, only when the reading is stale and not cooling off), the nodes and
whether `settings.yml` / `hosts.yml` parse — and judges none of them."""

from __future__ import annotations

import json
import os
import stat
import time
from datetime import UTC, datetime, timedelta

from sessionorc import adapters, hosts, identity, paths
from sessionorc import settings as settings_mod
from sessionorc.client import LocalClient
from sessionorc.models import Session


def _z(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _Doc:
    """A hook-fed adapter as `doctor` reads one: its profiles, and a usage endpoint that counts its asks."""

    name = "docstub"
    label = "Doc"

    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def doctor_profiles(self):
        return self.rows

    def account_for(self, profile):
        return {"a": "paul", "b": "paul", "c": "heather"}.get(profile, profile)

    def usage_for(self, profile):
        self.asked.append(profile)
        return {
            "windows": [{"label": "5h", "pct": 23.0, "resets": None}],
            "fetched": _z(datetime.now(UTC)),
            "reason": "ok",
        }


def test_doctor_is_a_never_gated_read():
    assert "doctor" in identity.READS


async def test_tmux_reads_a_server_replaced_under_the_agent(agent, monkeypatch):
    me = os.getpid()
    monkeypatch.setattr(agent.tmux, "server_pid", lambda: me)
    agent._id_tmux_first = agent._id_server(me)  # the server the agent first read is the one there now
    got = (await agent.rpc_doctor())["tmux"]
    assert got["pid"] == me and got["first"]["pid"] == me and got["replaced"] is False
    assert got["started"] and got["started"].endswith("Z") and got["started"] == got["first"]["started"]
    agent._id_tmux_first = (1, 1)  # init's, long gone
    got = (await agent.rpc_doctor())["tmux"]
    assert got["replaced"] is True and got["first"]["pid"] == 1
    agent._id_tmux_first = (me, 1)  # the same pid, started at another instant: a replacement on a reused pid
    assert (await agent.rpc_doctor())["tmux"]["replaced"] is True
    monkeypatch.setattr(agent.proc, "stat", lambda pid: None)  # `/proc` cannot tell the start: the pids decide
    assert (await agent.rpc_doctor())["tmux"]["replaced"] is False
    monkeypatch.setattr(agent.tmux, "server_pid", lambda: None)
    got = (await agent.rpc_doctor())["tmux"]
    assert got["pid"] is None and got["replaced"] is False, "no server is not a replaced one"


async def test_hooks_read_each_live_agent_session_and_the_queue(agent, monkeypatch, tmp_path):
    monkeypatch.setitem(adapters._REGISTRY, "docstub", _Doc([]))
    t0 = datetime.now(UTC)
    for sid, ad, state, conf in (
        ("ao-d-fed", "docstub", "idle", "hook"),
        ("ao-d-scraped", "docstub", "working", "scraped"),
        ("ao-d-shell", "shell", "idle", "scraped"),
        ("ao-d-gone", "docstub", "closed", "hook"),
    ):
        agent.sessions[sid] = Session(
            id=sid, name=sid, kind="interactive", adapter=ad, dir=str(tmp_path), state=state, confidence=conf
        )
    agent._last_hook["ao-d-fed"] = t0 - timedelta(seconds=12)
    (paths.events_dir() / "ao-d-scraped.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    try:
        got = (await agent.rpc_doctor())["hooks"]
        by = {s["id"]: s for s in got["sessions"]}
        assert set(by) == {"ao-d-fed", "ao-d-scraped"}, "a shell has no hooks, a closed session is not live"
        assert by["ao-d-fed"]["confidence"] == "hook" and by["ao-d-fed"]["last_hook"] == _z(t0 - timedelta(seconds=12))
        assert by["ao-d-scraped"]["confidence"] == "scraped" and by["ao-d-scraped"]["last_hook"] is None
        assert got["queue"]["lines"] == 2 and got["queue"]["written"] >= 0
    finally:
        for sid in ("ao-d-fed", "ao-d-scraped", "ao-d-shell", "ao-d-gone"):
            agent.sessions.pop(sid, None)
        (paths.events_dir() / "ao-d-scraped.jsonl").unlink(missing_ok=True)


async def test_usage_is_asked_once_per_stale_account_never_when_fresh_cooling_or_metered(agent, monkeypatch):
    rows = [
        {"profile": p, "account": a, "config_dir": "/x", "metered": m, "credentials": True, "layers": []}
        for p, a, m in (("a", "paul", False), ("b", "paul", False), ("c", "heather", False), ("m", "m", True))
    ]
    doc = _Doc(rows)
    monkeypatch.setitem(adapters._REGISTRY, "docstub", doc)
    got = {r["profile"]: r for r in (await agent.rpc_doctor())["profiles"] if r.get("adapter") == "docstub"}
    assert sorted(doc.asked) == ["a", "c"], "one request per account; a metered profile is never polled"
    assert got["a"]["usage"]["source"] == "asked" and got["b"]["usage"] == got["a"]["usage"]
    assert "usage" not in got["m"] and got["m"]["metered"] is True
    # a fresh reading is read, not asked; an account cooling off a 429 keeps its last reading, not asked
    now = _z(datetime.now(UTC))
    agent._usage_acct["docstub:paul"] = {"windows": [{"label": "5h", "pct": 61.0}], "fetched": now, "reason": "ok"}
    agent._usage_acct["docstub:heather"] = {"windows": [], "fetched": _z(datetime.now(UTC) - timedelta(hours=2))}
    agent._usage_wait["docstub:heather"], agent._usage_checked["docstub:heather"] = 3600.0, time.monotonic()
    doc.asked.clear()
    try:
        got = {r["profile"]: r for r in (await agent.rpc_doctor())["profiles"] if r.get("adapter") == "docstub"}
        assert doc.asked == []
        assert got["a"]["usage"]["source"] == "reading" and got["a"]["usage"]["windows"][0]["pct"] == 61.0
        assert got["c"]["usage"]["source"] == "reading" and 0 < got["c"]["usage"]["cooling"] <= 3600
        # after a restart only the kept reading remembers the 429: its `cool_until` holds the ask off too
        agent._usage_wait.pop("docstub:heather")
        agent._usage_checked.pop("docstub:heather")
        agent._usage_acct["docstub:heather"]["cool_until"] = _z(datetime.now(UTC) + timedelta(minutes=10))
        got = {r["profile"]: r for r in (await agent.rpc_doctor())["profiles"] if r.get("adapter") == "docstub"}
        assert doc.asked == [] and 500 < got["c"]["usage"]["cooling"] <= 600
    finally:
        for k in ("docstub:paul", "docstub:heather"):
            agent._usage_acct.pop(k, None)
            agent._usage_wait.pop(k, None)
            agent._usage_checked.pop(k, None)


async def test_an_adapters_fault_is_a_reading_not_the_rpcs(agent, monkeypatch):
    class Broken(_Doc):
        def doctor_profiles(self):
            raise RuntimeError("profiles.yml: line 3")

    monkeypatch.setitem(adapters._REGISTRY, "docstub", Broken([]))
    got = [r for r in (await agent.rpc_doctor())["profiles"] if r.get("adapter") == "docstub"]
    assert got == [{"adapter": "docstub", "error": "docstub: profiles.yml: line 3"}]


async def test_nodes_and_the_two_files(agent):
    hosts.hosts_file().write_text("nodes: [laptop, desk]\n", encoding="utf-8")
    agent.links["laptop"] = {"up": True, "since": "2026-10-09T10:00:00Z", "why": "linked"}
    settings_mod.settings_file().write_text("teams: [unclosed\n", encoding="utf-8")
    try:
        got = await agent.rpc_doctor()
        assert got["nodes"] == [
            {"node": "desk", "link": None, "container": False},
            {
                "node": "laptop",
                "link": {"up": True, "since": "2026-10-09T10:00:00Z", "why": "linked"},
                "container": False,
            },
        ]
        assert got["files"]["settings.yml"]["error"] and "line" in got["files"]["settings.yml"]["error"]
        assert got["files"]["hosts.yml"]["error"] is None
        assert got["identity"]["mode"] == agent.identity_mode
    finally:
        agent.links.pop("laptop", None)
        settings_mod.settings_file().unlink(missing_ok=True)
        hosts.hosts_file().unlink(missing_ok=True)


async def test_a_session_may_call_it(agent, tmp_path):
    agent.sessions["ao-d-caller"] = Session(
        id="ao-d-caller", name="c", kind="interactive", adapter="shell", dir=str(tmp_path)
    )
    try:
        async with LocalClient(caller="ao-d-caller") as c:
            got = await c.call("doctor")
        assert set(got) == {"host", "tmux", "hooks", "identity", "profiles", "nodes", "files"}
    finally:
        agent.sessions.pop("ao-d-caller", None)


# -- the Claude Code adapter's reading of its profiles ---------------------------------------------


def test_claude_codes_profiles_read_their_layers_credentials_and_key(tmp_path, monkeypatch):
    from agentorc.adapters.claude_code import CADENCE_HOOK_LINE, ClaudeCodeAdapter

    home = tmp_path / "home"
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    home.mkdir()
    (home / "profiles.yml").write_text(
        "profiles:\n"
        f"  grind: {{config_dir: {tmp_path / 'g'}}}\n"
        f"  grind-api: {{config_dir: {tmp_path / 'api'}, billing: metered}}\n"
        f"  nobody: {{config_dir: {tmp_path / 'n'}}}\n",
        encoding="utf-8",
    )
    (tmp_path / "g").mkdir()
    exp = int((datetime.now(UTC) + timedelta(days=30)).timestamp() * 1000)
    (tmp_path / "g" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": exp}}))
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "settings.json").write_text(json.dumps({"apiKeyHelper": "pass show api"}))
    hook = tmp_path / "agentorc-hook"
    hook.write_text("#!/bin/sh\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    layers = home / "claude-hooks"
    layers.mkdir()

    def layer(name, cmd):
        hooks = {"Stop": [{"hooks": [{"type": "command", "command": cmd}, {"command": CADENCE_HOOK_LINE}]}]}
        (layers / name).write_text(json.dumps({"hooks": hooks, "statusLine": {"command": f"{cmd} --statusline"}}))

    layer("grind.json", str(hook))
    layer("grind+unattended.json", "/nowhere/agentorc-hook")
    layer("grind-api.json", str(hook))  # another profile's, whose name begins with this one's
    rows = {r["profile"]: r for r in ClaudeCodeAdapter().doctor_profiles()}
    g = rows["grind"]
    assert g["credentials"] is True and g["metered"] is False and "key" not in g
    assert [p["path"].rsplit("/", 1)[1] for p in g["layers"]] == ["grind+unattended.json", "grind.json"]
    assert g["layers"][1]["commands"] == [
        {"command": str(hook), "resolves": True},
        {"command": f"{hook} --statusline", "resolves": True},
    ], "dev-cadence's own line is not ours to read"
    assert {c["resolves"] for c in g["layers"][0]["commands"]} == {False}
    # a layer written before dev-cadence's runner line changed carries the older line: not ours to read either (TD-507)
    old_line = 'f="$CLAUDE_PROJECT_DIR/scripts/cadence_hooks.sh"; if [ -x "$f" ]; then "$f" --session-start; fi'
    nudge = "python3 scripts/nudge_user_attention.py --session-start"
    hooks = {"SessionStart": [{"hooks": [{"command": str(hook)}, {"command": old_line}, {"command": nudge}]}]}
    (layers / "grind+cadence.json").write_text(json.dumps({"hooks": hooks}))
    cad = next(r for r in ClaudeCodeAdapter().doctor_profiles() if r["profile"] == "grind")["layers"][0]
    assert cad["path"].endswith("grind+cadence.json")
    assert cad["commands"] == [{"command": str(hook), "resolves": True}]
    api = rows["grind-api"]
    assert api["metered"] is True and api["key"] is True and api["credentials"] is None
    assert rows["nobody"]["credentials"] is None and rows["nobody"]["layers"] == []
    (home / "profiles.yml").write_text("profiles:\n  x: {billing: nonsense}\n", encoding="utf-8")
    assert [set(r) for r in ClaudeCodeAdapter().doctor_profiles()] == [{"error"}]
