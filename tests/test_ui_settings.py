"""The Settings page (design §4.5 screen 8, §4.5a *Settings page*; TD-148): the one page that writes a
setting — through the host agent's `set_settings`, never the file — and draws every other configured
value with the file it lives in."""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentorc import profiles as profiles_mod
from agentorc.ui import settings_page as setmod

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(subprocess_agent):
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.unit
def test_a_reserve_field_takes_what_ao_gate_takes():
    assert setmod.parse_reserve_text("30") == 30
    assert setmod.parse_reserve_text(" 10/day ") == {"per_day": 10}
    assert setmod.parse_reserve_text("") is None and setmod.parse_reserve_text(None) is None
    with pytest.raises(ValueError, match="whole percent"):
        setmod.parse_reserve_text("thirty")
    assert setmod.reserve_text({"per_day": 10}) == "10/day" and setmod.reserve_text(30) == "30"
    assert setmod.reserve_text(None) == ""


@pytest.mark.unit
def test_usage_cards_group_by_account_and_draw_each_reported_window():
    """A card per profile under its account, as the chip groups them (TD-122); a row per window the
    adapter reports, with the reserve as the field takes it and the line it makes today; a profile
    with no reading draws its reserves' labels and says they are not checked; a metered profile's
    card carries the home's three windows, each an amount with the account's spend beside it."""
    profiles = {
        "grind": profiles_mod.Profile(name="grind", account="paul"),
        "paul": profiles_mod.Profile(name="paul", account="paul"),
        "cold": profiles_mod.Profile(name="cold", account="other"),
        "api": profiles_mod.Profile(name="api", account="key", billing="metered", prices={"input": 3, "output": 15}),
    }
    resets = (NOW + timedelta(days=3, hours=19)).isoformat()
    usage = {
        "grind": {"tool": "claude-code", "account": "paul", "windows": [
            {"label": "5h", "pct": 41, "resets": NOW.isoformat()}, {"label": "week", "pct": 58, "resets": resets}]},
        "paul": {"tool": "claude-code", "account": "paul", "windows": [{"label": "5h", "pct": 41}]},
        "api": {"tool": "claude-code", "account": "key", "windows": [
            {"label": "day", "pct": 64, "resets": resets, "spent": {"total": 9, "cost": 3.2}},
            {"label": "week", "pct": None, "resets": resets, "spent": {"total": 1_200_000, "cost": 9.8}}]},
    }  # fmt: skip
    gate = {
        "grind": {"reserves": {"5h": 30, "week": {"per_day": 10}}, "windows": [
            {"label": "5h", "line": 70, "reserve": 30, "resets": None, "next": None},
            {"label": "week", "line": 60, "reserve": {"per_day": 10}, "resets": resets, "next": None}]},
        "cold": {"reserves": {"5h": 20}, "windows": []},
        "api": {"reserves": {"day": "$5", "week": "2M tok"}, "windows": [], "metered": True},
    }  # fmt: skip
    groups = setmod.usage_cards(profiles, gate, usage, NOW)
    assert [g["account"] for g in groups] == ["claude-code · paul", "claude-code · other", "claude-code · key"]
    grind, paul = groups[0]["cards"]
    assert [(r["label"], r["value"], r["line"]) for r in grind["rows"]] == [
        ("5h", "30", "→ line 70%"),
        ("week", "10/day", "→ line 60% · 4 days left"),
    ]
    assert grind["rows"][1]["resets"] == resets and not grind["unchecked"]
    assert [(r["label"], r["value"], r["line"]) for r in paul["rows"]] == [("5h", "", "no line")]
    cold = groups[1]["cards"][0]
    assert cold["rows"] == [{"label": "5h", "value": "20", "line": "no reading yet", "resets": "", "pct": None}]
    assert cold["unchecked"]
    api = groups[2]["cards"][0]
    assert api["metered"] and "metered · $3 in / $15 out per M" in api["badge"]
    at = datetime.fromisoformat(resets).astimezone().strftime("%H:%M")
    assert [(r["label"], r["value"], r["line"]) for r in api["rows"]] == [
        ("day", "$5", f"spent $3.20 · 64% · resets {at}"),
        ("week", "2M tok", f"spent 1.2M tok · resets {at}"),
        ("month", "", "no reading yet"),
    ]
    assert setmod.parse_reserve_text(" $5 ") == "$5" and setmod.parse_reserve_text("20M tok") == "20M tok"


@pytest.mark.unit
def test_a_usage_cards_rows_say_how_old_the_reading_is():
    """design §4.5a *Settings page: Usage*, *The reading's age* (TD-230, TD-233 slice 1): beside each
    window's reading, its age and source as the chip's hover gives them; past three hours, or past
    the window's reset, *unknown since …* with the number it was."""
    profiles = {"grind": profiles_mod.Profile(name="grind", account="paul")}
    later = (NOW + timedelta(days=2)).isoformat()

    def rows(fetched: str, resets: str = later) -> list[dict]:
        usage = {"grind": {"fetched": fetched, "windows": [{"label": "week", "pct": 58, "resets": resets}]}}
        return setmod.usage_cards(profiles, {}, usage, NOW)[0]["cards"][0]["rows"]

    assert rows((NOW - timedelta(minutes=7)).isoformat())[0]["age"] == "58% · read 7m ago, asked of the endpoint"
    was = NOW - timedelta(hours=4)
    assert rows(was.isoformat())[0]["age"] == (
        f"unknown since {was.astimezone():%H:%M} (was 58%), asked of the endpoint"
    )
    gone = NOW - timedelta(minutes=30)
    got = rows(NOW.isoformat(), gone.isoformat())[0]["age"]
    assert got == f"unknown since its reset at {gone.astimezone():%H:%M} (was 58%)"
    assert rows("x")[0]["age"] == ""  # no time on the reading: nothing said


@pytest.mark.unit
def test_the_line_text_is_ao_gates():
    row = {"line": 60, "reserve": {"per_day": 10}, "resets": (NOW + timedelta(days=1)).isoformat(), "next": None}
    assert setmod.line_text(row, NOW) == "→ line 60% · 1 day left"
    assert setmod.line_text({"line": None}, NOW) == "no line — the window reports no reset"
    assert setmod.line_text(None, NOW) == "no line"
    assert setmod.line_text({"line": 95, "pct": 99, "unknown": "reset"}, NOW).startswith("unknown since its reset")
    # past `usage.max_age` (TD-233): the projection the gate reads, or why there is none
    pr = {"line": 95, "pct": 96.0, "reserve": 5, "projected": {"from": 88, "rate": 1.3, "age": 21600}}
    assert setmod.line_text(pr, NOW) == "→ line 95% · projected 96%"
    assert setmod.line_text({"line": 95, "pct": 88, "reserve": 5, "unknown": "rate"}, NOW).endswith(
        "no rate to project by"
    )


def test_trust_a_reading_for_writes_usage_max_age(client, subprocess_agent):
    """§4.5a *Settings page: Usage* **trust a reading for** (TD-233): drawn at the hour until set,
    written through `set_settings {usage}`, refused in place out of its bounds, empty clearing it."""
    from sessionorc.client import call_sync

    try:
        assert 'id="setmaxage"' in (page := client.get("/settings").text) and 'name="max_age" value="1h"' in page
        assert client.post("/api/settings/max_age", json={"max_age": "off"}).json()["ok"]
        assert call_sync("settings")["usage"] == {"max_age": "off"}
        page = client.get("/settings").text
        assert 'name="max_age" value="off"' in page and "never project" in page
        bad = client.post("/api/settings/max_age", json={"max_age": "2m"})
        assert bad.status_code == 400 and "from 5m to 7d" in bad.json()["detail"]
    finally:
        assert client.post("/api/settings/max_age", json={"max_age": ""}).json()["ok"]
    assert call_sync("settings")["usage"] == {"max_age": "1h"}


@pytest.mark.unit
def test_read_only_cards_mark_what_the_file_leaves_to_its_default(tmp_path):
    from sessionorc import hosts

    local = hosts.Host(name="kmaster", vscode_host="kmaster", volatile=True)
    card = setmod.host_card({"name": "kmaster", "volatile": True}, local, "kmaster", {"cm": {"container": True}})
    by = {r["key"]: r for r in card["rows"]}
    assert not by["name"]["default"] and by["volatile"]["value"] == "True" and not by["volatile"]["default"]
    assert by["vscode_host"]["default"] and by["runs_keep_days"]["default"] and by["home"]["default"]
    assert by["nodes"]["value"] == "cm (container)"
    (tmp_path / "hosts.yml").write_text("local: {name: kmaster, volatile: true}\nhome: kmaster\n")
    assert setmod.local_entry(tmp_path / "hosts.yml") == {"name": "kmaster", "volatile": True}
    assert setmod.local_entry(tmp_path / "missing.yml") == {}
    prof = setmod.profile_cards({"grind": profiles_mod.Profile(name="grind", account="paul")}, "grind")[0]
    pby = {r["key"]: r for r in prof["rows"]}
    assert prof["default"] and pby["adapter"]["default"] and not pby["account"]["default"]


@pytest.mark.unit
def test_a_repo_card_draws_the_switch_only_where_the_file_carries_promote(tmp_path):
    with_block, without = tmp_path / "a", tmp_path / "b"
    with_block.mkdir(), without.mkdir()
    (with_block / ".agentorc.yml").write_text("promote:\n  run: make install\n  check: make which\n")
    cards = setmod.repo_cards([str(with_block), str(without)], {"a": {"promote": {"auto": True}}})
    a, b = cards
    assert a["promote"] and a["auto"] and {"promote.run", "promote.check"} <= {r["key"] for r in a["rows"]}
    assert not b["promote"] and not b["exists"] and not b["auto"]


@pytest.mark.unit
def test_the_line_preview_is_the_gates_arithmetic():
    """`AO.reserveLine` draws a new line before the press lands (§4.5a *Usage*): the same arithmetic
    as `sessionorc.settings.line`, run as itself under node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    from test_ui_menu import PROBE

    probe = pathlib.Path(tempfile.mkdtemp()) / "set_probe.js"
    now = int(NOW.timestamp() * 1000)
    resets = (NOW + timedelta(days=3, hours=19)).isoformat()
    head = PROBE[: PROBE.index("const P =")]  # the page's stubs and the eval, without the menu's own probe
    probe.write_text(
        head
        + f"""
const r = (t, at) => window.AO.reserveLine(t, at, {now});
const a = (t) => window.AO.amountSays(t);
console.log(JSON.stringify({{flat: r("30"), day: r("10/day", "{resets}"), none: r(""), bad: r("x"),
  big: r("120"), noreset: r("10/day", ""), usd: a("$5"), tok: a("20M tok"), pct: a("30"), clear: a("")}}));
"""
    )
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["flat"] == {"line": 70, "says": "→ line 70%"}
    assert got["day"] == {"line": 60, "says": "→ line 60% · 4 days left"}
    assert got["none"]["line"] is None and "error" in got["bad"] and "error" in got["big"]
    assert got["noreset"]["says"] == "no line — the window reports no reset"
    assert got["usd"] == {"says": "→ amount $5"} and got["tok"] == {"says": "→ amount 20M tok"}
    assert "not a percent" in got["pct"]["error"] and got["clear"] == {"says": "no amount"}


def test_the_page_draws_every_section_and_the_tab(client, subprocess_agent):
    r = client.get("/settings")
    assert r.status_code == 200
    for sec in ("sec-usage", "sec-teams", "sec-repos", "sec-you", "sec-hosts", "sec-profiles", "sec-org"):
        assert f'id="{sec}"' in r.text
    assert 'id="settingstab"' in r.text and "Reset this browser" in r.text
    assert "settings.yml" in r.text and str(subprocess_agent.home / "hosts.yml") in r.text
    # the tab is on every page, last after Inbox
    org = client.get("/").text
    assert org.index('id="personinbox"') < org.index('id="settingstab"')


def test_a_save_writes_settings_yml_through_set_settings(client, subprocess_agent):
    """Each section's write goes through `set_settings` (§5): a reserve in `ao gate`'s forms, the
    promote switch, the person's editor and terminal — the refusals in the same words, and a team the
    org does not define refused, naming the defined ones."""
    from sessionorc.client import call_sync

    try:
        ok = client.post("/api/settings/usage", json={"profile": "grind", "reserves": {"5h": "30", "week": "10/day"}})
        assert ok.status_code == 200 and ok.json()["ok"]
        assert call_sync("settings")["usage_gate"]["grind"]["reserves"] == {"5h": 30, "week": {"per_day": 10}}
        bad = client.post("/api/settings/usage", json={"profile": "grind", "reserves": {"5h": "lots"}})
        assert bad.status_code == 400 and "whole percent" in bad.json()["detail"]
        assert client.post("/api/settings/usage", json={"profile": "grind", "reserves": {"5h": "", "week": ""}}).json()[
            "ok"
        ]

        assert client.post("/api/settings/repos", json={"repo": "agentorc", "auto": True}).json()["ok"]
        assert call_sync("settings")["repos"]["agentorc"] == {"promote": {"auto": True}}
        assert client.post("/api/settings/repos", json={"repo": "agentorc", "auto": "yes"}).status_code == 400

        got = client.post("/api/settings/you", json={"open_in": "none", "terminal": {"size": 15, "face": "Fira Code"}})
        assert got.json()["ok"]
        person = call_sync("settings")["person"]
        assert person == {"open_in": "none", "terminal": {"size": 15, "face": "Fira Code"}}
        assert 'name="copy_on_select" checked' in client.get("/settings").text  # on by default
        page = client.get("/settings").text
        assert 'data-term-size="15"' in page and 'data-term-face="Fira Code"' in page
        refused = client.post("/api/settings/you", json={"open_in": {"label": "x", "url": "javascript://alert(1)"}})
        assert refused.status_code == 400 and "refused" in refused.json()["detail"]
        assert client.post("/api/settings/you", json={"terminal": {"copy_on_select": False}}).json()["ok"]
        assert 'name="copy_on_select">' in client.get("/settings").text  # drawn off, as Focus's toggle is
        assert client.post("/api/settings/you", json={"terminal": {"colour": "red"}}).status_code == 400

        # **board items shown** (§4.5a, TD-220 slice 4): drawn at next:10 until set, written, refused out of range
        page = client.get("/settings").text
        assert (
            'name="board_show" value="next" checked' in page and 'name="board_next"' in page and "due this week" in page
        )
        assert client.post("/api/settings/you", json={"inbox": {"board_show": "14d"}}).json()["ok"]
        assert call_sync("settings")["person"]["inbox"] == {"board_show": "14d"}
        page = client.get("/settings").text
        assert 'name="board_show" value="days" checked' in page and "due within 14 days" in page
        for bad in ("next:0", "next:51", "0d", "soon"):
            assert client.post("/api/settings/you", json={"inbox": {"board_show": bad}}).status_code == 400, bad
        assert client.post("/api/settings/you", json={"inbox": {"colour": "red"}}).status_code == 400
        assert call_sync("settings")["person"]["inbox"] == {"board_show": "14d"}

        nope = client.post("/api/settings/teams", json={"team": "nobody", "reserve": "10"})
        assert nope.status_code == 400 and "the org defines" in nope.json()["detail"]
    finally:
        call_sync("set_settings", person={"open_in": None, "terminal": None, "inbox": None}, repos={"agentorc": None})


def test_a_team_card_sets_the_stop_time_and_priority(client, subprocess_agent):
    from sessionorc.client import call_sync

    org = subprocess_agent.home / "org.yml"
    org.write_text(
        f"projects:\n  sp:\n    repos:\n      sp: {{kmaster: {subprocess_agent.home}}}\n"
        "teams:\n  sett-team:\n    projects: [sp]\n    manager: {role: manager}\n"
        "    members: [{role: grinder, count: 2}]\n"
    )
    try:
        page = client.get("/settings").text
        assert 'data-team="sett-team"' in page and "not built — TD-133" in page
        assert "grinder ×2" in page  # the Org section draws the definition, read-only
        assert client.post("/api/settings/teams", json={"team": "sett-team", "until": "+2h", "reserve": "10"}).json()[
            "ok"
        ]
        t = call_sync("settings")["teams"]["sett-team"]
        assert t["reserve"] == 10 and t["until"].endswith("Z") and not t["passed"]
        assert re.search(r"\+10 on the profile", client.get("/settings").text)
        bad = client.post("/api/settings/teams", json={"team": "sett-team", "until": "soon"})
        assert bad.status_code == 400 and "not a time" in bad.json()["detail"]
        assert client.post("/api/settings/teams", json={"team": "sett-team", "until": None, "reserve": "0"}).json()[
            "ok"
        ]
        assert "sett-team" not in (call_sync("settings")["teams"] or {})
        # **when work appears** (§4.5a, §6 rule 8, TD-227 slice 3): *ask me* is the default while the
        # key is absent, the pick is written as `on_work`, and anything else is refused
        page = client.get("/settings").text
        assert 'name="on_work" data-was="ask"' in page and "ask me (default)" in page
        assert client.post("/api/settings/teams", json={"team": "sett-team", "on_work": "start"}).json()["ok"]
        assert call_sync("settings")["teams"]["sett-team"] == {"on_work": "start"}
        page = client.get("/settings").text
        assert 'data-was="start"' in page and '<option value="start" selected>' in page
        assert "ask me (default)" not in page and "the home starts it" in page
        bad = client.post("/api/settings/teams", json={"team": "sett-team", "on_work": "maybe"})
        assert bad.status_code == 400 and "ask, start or off" in bad.json()["detail"]
        assert call_sync("settings")["teams"]["sett-team"] == {"on_work": "start"}
    finally:
        org.unlink()
        call_sync("set_settings", teams={"sett-team": None})
