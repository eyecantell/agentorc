"""TD-465 slice 2, design §4.7 **`ao doctor`**: the client's verdicts over the `doctor` RPC's
readings — seven checks in order, each in `ao org check`'s words, every lack naming its cure, the
closing count, exit 1 on a lack and 3 with no host agent, `ao doctor <check>…`, `--json` with each
check's raw reading, *home only* on a node. The RPC is faked: what is judged is the reading."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from agentorc import cli, doctor

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _z(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def reading(**over) -> dict:
    """A healthy host: the server it first read, on its system unit; two hook-fed sessions; no
    alarms; one profile with credentials and a usage reading; no node; both files parse."""
    r = {
        "host": "kmaster",
        "tmux": {
            "pid": 41022,
            "started": "2026-10-06T07:02:00Z",
            "cgroup": "/system.slice/agentorc-tmux.service",
            "runs": "system",
            "first": {"pid": 41022, "started": "2026-10-06T07:02:00Z"},
            "replaced": False,
        },
        "hooks": {
            "layers": [
                {
                    "profile": "grind",
                    "layers": [
                        {"path": "/h/grind.json", "commands": [{"command": "/v/bin/agentorc-hook", "resolves": True}]}
                    ],
                }
            ],  # fmt: skip
            "sessions": [
                {"id": "ao-a", "confidence": "hook", "state": "working", "since": _z(NOW), "last_hook": _z(NOW)},
                {"id": "ao-b", "confidence": "hook", "state": "idle", "since": _z(NOW), "last_hook": _z(NOW)},
            ],
            "queue": {"lines": 0, "written": None},
        },
        "identity": {"host": "kmaster", "mode": "enforce", "detached_check": True, "alarms": [], "sessions": {}},
        "profiles": [
            {
                "adapter": "claude-code",
                "profile": "grind",
                "config_dir": "/c/grind",
                "metered": False,
                "credentials": True,
                "login": "CLAUDE_CONFIG_DIR=/c/grind claude",
                "usage": {"windows": [{"label": "5h", "pct": 23.4}, {"label": "week", "pct": 61}], "reason": "ok"},
            }
        ],
        "nodes": [],
        "files": {"settings.yml": {"path": "/h/settings.yml", "error": None}},
    }
    r.update(over)
    return r


def verdicts(rows):
    return [(r["verdict"], r["text"]) for r in rows]


def test_a_replaced_server_is_a_lack_naming_its_cure():
    t = {**reading()["tmux"], "pid": 58190, "replaced": True}
    [(v, text)] = verdicts(doctor.tmux(t))
    assert v == "lacking"
    assert text == "tmux — server replaced under the agent: was 41022, now 58190 (restart the host agent)"
    assert verdicts(doctor.tmux({"pid": None}))[0][0] == "lacking"


def test_the_server_under_the_user_manager_is_a_warning_and_its_system_unit_ok():
    t = {**reading()["tmux"], "cgroup": "/user.slice/user-1000.slice/user@1000.service/x", "runs": "user"}
    [(v, text)] = verdicts(doctor.tmux(t))
    assert v == "warning" and "a stop of `systemd --user` ends every session" in text
    [(v, text)] = verdicts(doctor.tmux(reading()["tmux"]))
    assert v == "ok" and text.startswith("tmux — server 41022 since ") and text.endswith("agentorc-tmux.service")


def test_a_scraped_session_warns_and_a_stale_queue_and_a_dead_hook_command_lack():
    h = reading()["hooks"]
    h["sessions"][1] = {**h["sessions"][1], "confidence": "scraped", "since": _z(NOW - timedelta(minutes=40))}
    assert verdicts(doctor.hooks(h, NOW)) == [("warning", "hooks — ao-b scraped for 40 min")]
    h["queue"] = {"lines": 3, "written": 400.0}
    h["layers"][0]["layers"][0]["commands"][0]["resolves"] = False
    got = verdicts(doctor.hooks(h, NOW))
    assert [v for v, _ in got] == ["lacking", "warning", "lacking"]
    assert "layer grind.json names no hook command that resolves" in got[0][1]
    assert "3 queued hook lines not applied" in got[2][1]
    # a queue the tick is draining now is no lack
    h = reading()["hooks"]
    h["queue"] = {"lines": 2, "written": 1.0}
    assert verdicts(doctor.hooks(h, NOW + timedelta(seconds=12))) == [
        ("ok", "hooks — 2 sessions fed by hooks, newest 12s ago")
    ]


def test_profiles_no_credentials_rate_limited_endpoint_error_and_metered():
    base = reading()["profiles"][0]
    rows = [
        {**base, "profile": "a", "credentials": None},
        {**base, "profile": "b", "usage": {"reason": "rate_limited", "retry_after": 40}},
        {**base, "profile": "c", "usage": {"reason": "error", "error": "HTTP 500"}},
        {**base, "profile": "d", "metered": True, "key": False, "credentials": None},
        {**base, "profile": "e", "metered": True, "key": True, "credentials": None},
        {"adapter": "claude-code", "error": "profiles.yml: bad"},
    ]
    assert verdicts(doctor.profiles(rows)) == [
        ("lacking", "profile a — no credentials (log in: `CLAUDE_CONFIG_DIR=/c/grind claude`)"),
        ("warning", "profile b — usage rate-limited, retry in 40s"),
        ("lacking", "profile c — usage endpoint error: HTTP 500"),
        ("lacking", "profile d — metered, no key"),
        ("ok", "profile e — metered, key set"),
        ("lacking", "profiles — profiles.yml: bad"),
    ]
    assert verdicts(doctor.profiles([base])) == [("ok", "profile grind — credentials good, usage 5h 23% · week 61%")]


def test_a_node_down_or_behind_lacks_with_its_cure():
    rows = [
        {"node": "cm", "link": None, "container": True},
        {"node": "dv", "link": {"up": True}, "container": True, "build": {"running": "0.3.0", "home": "0.3.1"}},
        {"node": "ok", "link": {"up": True}, "container": True, "build": {"running": "0.3.1", "home": "0.3.1"}},
    ]
    assert verdicts(doctor.nodes(rows, False)) == [
        ("lacking", "node cm — link down: never linked (`ao host up cm`)"),
        ("lacking", "node dv — build 0.3.0 behind the home's 0.3.1 (`ao host rebuild dv`)"),
        ("ok", "node ok — link up, build 0.3.1 = home"),
    ]
    assert verdicts(doctor.nodes([], False)) == [("ok", "nodes — none linked")]
    assert verdicts(doctor.nodes(rows, True))[0][0] == "home only"


def test_the_agent_line_reads_the_promote_and_behind_is_a_warning():
    same = {"live": "485d28bxx", "main": "485d28bxx", "ahead": 0, "auto": True}
    assert verdicts(doctor.agent("0.3.1", same, {}, False)) == [("ok", "agent — 0.3.1, live 485d28b = main")]
    behind = {"live": "485d28bxx", "main": "9c1e0f2xx", "ahead": 3, "auto": False}
    assert verdicts(doctor.agent("0.3.1", behind, {}, False)) == [
        ("warning", "agent — live 485d28b, main 9c1e0f2, 3 behind · auto off")
    ]
    # no promote: block, so the running build against origin/main
    assert verdicts(doctor.agent("0.3.1", None, {"ref": "origin/main", "ahead": 0}, False))[0][0] == "ok"
    assert verdicts(doctor.agent("0.3.1", None, {"ref": "origin/main", "ahead": 2}, False))[0][0] == "warning"


def test_identity_alarms_warn_and_org_and_files_lack():
    i = {**reading()["identity"], "alarms": [{}], "sessions": {"ao-a": [{}]}}
    assert verdicts(doctor.identity(i)) == [("warning", "identity — 2 alarms standing (`ao identity`)")]
    assert verdicts(doctor.identity(reading()["identity"])) == [("ok", "identity — enforce, detached-process check on")]
    files = {"settings.yml": {"error": "line 12: mapping values are not allowed here"}}
    got = verdicts(doctor.org({"lacks": ["no repo for x"], "warnings": ["w"]}, files, 2, False))
    assert got == [
        ("lacking", "settings.yml — line 12: mapping values are not allowed here"),
        ("lacking", "org — no repo for x"),
        ("warning", "org — w"),
    ]
    assert verdicts(doctor.org({"lacks": [], "warnings": []}, {}, 2, False)) == [("ok", "org — 2 teams")]


@pytest.fixture
def agent(monkeypatch):
    """The host agent answering `host` and `doctor` with whatever the test puts in `state`."""
    state = {"host": {"mode": "home", "promotes": {}, "built_from": {}}, "doctor": reading()}

    def fake(method, **params):
        if method not in state:
            raise cli.AgentError(f"unknown method: {method}")
        return state[method]

    monkeypatch.setattr(cli, "call_sync", fake)
    monkeypatch.setattr(cli, "_build_ahead", lambda host: {"ref": "origin/main", "ahead": 0})
    monkeypatch.setattr(cli, "_doctor_org", lambda files, node: doctor.org({}, files, 1, node))
    return state


def test_the_seven_checks_in_order_then_the_count(agent, capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(" — ")[0] for line in out[:-1]] == [
        "ok: agent",
        "ok: tmux",
        "ok: hooks",
        "ok: identity",
        "ok: profile grind",
        "ok: nodes",
        "ok: org",
    ]
    assert out[-1] == "ok: 7 checks"


def test_a_seeded_lack_exits_1_and_json_carries_the_raw_reading(agent, capsys):
    agent["doctor"]["tmux"] = {**agent["doctor"]["tmux"], "pid": 58190, "replaced": True}
    agent["doctor"]["hooks"]["sessions"][0]["confidence"] = "scraped"
    assert cli.main(["--json", "doctor"]) == 1
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] is False and got["lacks"] == 1 and got["warnings"] == 1
    tmux = next(c for c in got["checks"] if c["check"] == "tmux")
    assert tmux["verdict"] == "lacking" and tmux["tmux"]["pid"] == 58190
    assert cli.main(["doctor"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "1 lacking, 1 warning"


def test_named_checks_run_alone_and_an_unknown_one_is_refused(agent, capsys):
    assert cli.main(["doctor", "tmux", "identity"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(" — ")[0] for line in out] == ["ok: tmux", "ok: identity", "ok: 2 checks"]
    assert cli.main(["doctor", "tmuxx"]) == 1
    assert "no check tmuxx" in capsys.readouterr().err


def test_on_a_node_the_home_reads_say_home_only_and_count_as_neither(agent, capsys):
    agent["host"] = {"mode": "node", "built_from": {}}
    assert verdicts(doctor.org(None, {}, 0, True)) == [("home only", "org — read at the home")]
    assert cli.main(["doctor", "agent", "nodes"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("home only: agent — ") and out[1] == "home only: nodes — read at the home"
    assert out[-1] == "ok: 0 checks"


def test_a_session_in_its_first_seconds_is_not_scraped_yet():
    h = reading()["hooks"]
    h["sessions"][1] = {**h["sessions"][1], "confidence": "scraped", "since": _z(NOW), "last_hook": None}
    assert verdicts(doctor.hooks(h, NOW + timedelta(seconds=5)))[0][0] == "ok"
    assert verdicts(doctor.hooks(h, NOW + timedelta(minutes=2))) == [("warning", "hooks — ao-b scraped for 2 min")]


def test_a_layer_with_one_dead_command_among_live_ones_warns():
    h = reading()["hooks"]
    h["layers"][0]["layers"][0]["commands"].append({"command": "my-status", "resolves": False})
    assert verdicts(doctor.hooks(h, NOW)) == [
        ("warning", "hooks — layer grind.json names my-status, which does not resolve")
    ]


def test_this_repos_promote_is_the_cwds_checkout_or_the_only_one(monkeypatch):
    a, b = {"live": "a"}, {"live": "b"}
    monkeypatch.setattr(cli, "_main_checkout", lambda start: "/r/agentorc")
    assert cli._this_promote({"promotes": {"other": a, "agentorc": b}}) is b
    monkeypatch.setattr(cli, "_main_checkout", lambda start: None)
    assert cli._this_promote({"promotes": {"other": a}}) is a
    assert cli._this_promote({"promotes": {"other": a, "agentorc": b}}) is None
    assert cli._this_promote({}) is None


def test_the_promote_reading_drives_the_agent_line(agent, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_main_checkout", lambda start: "/r/agentorc")
    agent["host"]["promotes"] = {"agentorc": {"live": "485d28bxx", "main": "9c1e0f2xx", "ahead": 3, "auto": True}}
    assert cli.main(["doctor", "agent"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "warning: agent — live 485d28b, main 9c1e0f2, 3 behind · auto on",
        "ok: 1 check, 1 warning",
    ]


def test_the_org_line_reads_ao_org_check_and_an_unreadable_org_lacks(monkeypatch):
    files = {"settings.yml": {"error": None}}
    assert verdicts(cli._doctor_org(files, True)) == [("home only", "org — read at the home")]

    def unreadable(what):
        raise ValueError("org.yml: bad")

    monkeypatch.setattr(cli, "_org_notes", unreadable)
    assert verdicts(cli._doctor_org(files, False)) == [("lacking", "org — org.yml: bad")]
    monkeypatch.setattr(cli, "_org_notes", lambda what: (cli.orgmod.Org(), []))
    monkeypatch.setattr(cli.orgcheck, "check", lambda *a, **k: {"ok": False, "lacks": ["x"], "warnings": []})
    monkeypatch.setattr(cli.teamrun, "files_via", lambda call: None)
    monkeypatch.setattr(cli.teamrun, "repos_via", lambda call: None)
    assert verdicts(cli._doctor_org(files, False)) == [("lacking", "org — x")]


def test_an_older_host_agent_says_so(agent, capsys):
    del agent["doctor"]
    assert cli.main(["doctor"]) == 1
    assert "predates `ao doctor`" in capsys.readouterr().err


def test_no_host_agent_stops_at_the_first_line_exit_3(monkeypatch, capsys):
    def down(method, **params):
        raise cli.AgentUnavailable("connection refused")

    monkeypatch.setattr(cli, "call_sync", down)
    monkeypatch.setattr(cli, "_agent_answers", lambda: False)
    assert cli.main(["doctor"]) == 3
    assert capsys.readouterr().out.splitlines()[0] == "lacking: agent — no host agent answering"


@pytest.fixture
def probed(agent, monkeypatch):
    """`create`, `explain`, `kill` and `remove` for a probe; `hook_after` is how many reads of the
    record pass before its first hook (None: never)."""
    calls: list[tuple[str, dict]] = []
    state = {"hook_after": 1, "reads": 0}

    def fake(method, **params):
        calls.append((method, params))
        if method == "create":
            assert pathlib.Path(params["dir"]).is_dir() and "prompt" not in params
            return {"id": f"ao-{params['name']}"}
        if method == "explain":
            state["reads"] += 1
            hooked = state["hook_after"] is not None and state["reads"] > state["hook_after"]
            return {"last_hook": "2026-10-09T12:00:00Z" if hooked else None, "tail": ["", "Do you trust?"]}
        if method in ("kill", "remove"):
            return {}
        return agent[method]

    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    monkeypatch.setattr(cli, "call_sync", fake)
    monkeypatch.setattr(cli.doctor, "PROBE_WAIT", 0.3)
    monkeypatch.setattr(cli, "_probe", lambda row, wait=0.3, every=0.01, f=cli._probe: f(row, wait, every))
    return calls, state


def test_a_probe_fires_sessionstart_and_leaves_nothing(probed, capsys):
    calls, _ = probed
    assert cli.main(["doctor", "hooks", "--probe", "grind"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("ok: hooks — probe grind: SessionStart in ")
    made = next(p for m, p in calls if m == "create")
    assert made["name"] == "probe-grind" and made["profile"] == "grind" and made["adapter"] == "claude-code"
    assert [m for m, _ in calls if m in ("kill", "remove")] == ["kill", "remove"]
    assert not pathlib.Path(made["dir"]).exists()


def test_a_probe_with_no_hook_lacks_with_the_panes_last_lines(probed, capsys):
    calls, state = probed
    state["hook_after"] = None
    assert cli.main(["doctor", "hooks", "--probe"]) == 1
    out = capsys.readouterr().out
    assert "lacking: hooks — probe grind: no hook in 0s\n    Do you trust?" in out
    assert [m for m, _ in calls if m in ("kill", "remove")] == ["kill", "remove"]


def test_a_probe_is_refused_to_a_session_and_outside_hooks_and_for_an_unknown_profile(probed, monkeypatch, capsys):
    calls, _ = probed
    assert cli.main(["doctor", "tmux", "--probe"]) == 1
    assert cli.main(["doctor", "hooks", "--probe", "nope"]) == 1
    assert "no profile 'nope'" in capsys.readouterr().err
    monkeypatch.setenv("AGENTORC_SESSION", "ao-me")
    assert cli.main(["doctor", "--probe"]) == 1
    assert "a person's own" in capsys.readouterr().err
    assert not [m for m, _ in calls if m == "create"]
