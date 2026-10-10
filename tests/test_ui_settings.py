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
    # **pull** on every card, block or not: on when absent, the home's reading beside it (§6 *Pull*)
    assert a["pull"] and b["pull"] and a["pull_reading"] == b["pull_reading"] == ""
    pulls = {"a": {"outcome": "waiting", "occupant": "main"}, "b": {"outcome": "off"}}
    a, b = setmod.repo_cards([str(with_block), str(without)], {"b": {"pull": False}}, pulls)
    assert a["pull"] and a["pull_reading"] == "waiting: main is mid-turn"
    assert not b["pull"] and b["pull_reading"] == "off"


@pytest.mark.unit
def test_the_pull_reading_in_the_designs_words():
    at = (NOW - timedelta(minutes=4)).isoformat()
    assert setmod.pull_reading({"outcome": "pulled", "at": at, "commits": 3}, NOW) == "last pulled 4m ago · 3 commits"
    assert setmod.pull_reading({"outcome": "pulled", "at": at, "commits": 1}, NOW) == "last pulled 4m ago · 1 commit"
    assert setmod.pull_reading({"outcome": "current"}) == "current"
    assert setmod.pull_reading({"outcome": "refused", "why": "on topic"}) == "refused: on topic"
    assert setmod.pull_reading({"outcome": "waiting", "occupant": None}).startswith("waiting: a session here")
    assert setmod.pull_reading(None) == ""


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
        assert client.post("/api/settings/repos", json={"repo": "agentorc", "pull": False}).json()["ok"]
        assert call_sync("settings")["repos"]["agentorc"] == {"promote": {"auto": True}, "pull": False}
        assert client.post("/api/settings/repos", json={"repo": "agentorc", "pull": "no"}).status_code == 400
        both = {"repo": "agentorc", "auto": True, "pull": True}  # one switch per press: never half-applied
        assert client.post("/api/settings/repos", json=both).status_code == 400

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

        # **attachment bound** (§4.5 screen 8, TD-478): the default beside an empty field, written, refused
        # out of 1M–4G in the RPC's words, and an empty Save (`max: null`) back to the default
        page = client.get("/settings").text
        assert 'name="attach_max" value="" placeholder="256M"' in page and "256M by default" in page
        assert client.post("/api/settings/you", json={"attach": {"max": "1G"}}).json()["ok"]
        assert call_sync("settings")["person"]["attach"] == {"max": "1G"}
        assert 'name="attach_max" value="1G"' in client.get("/settings").text
        bad = client.post("/api/settings/you", json={"attach": {"max": "9G"}})
        assert bad.status_code == 400 and "1M to 4G" in bad.json()["detail"]
        assert client.post("/api/settings/you", json={"attach": {"colour": "red"}}).status_code == 400
        assert client.post("/api/settings/you", json={"attach": {"max": None}}).json()["ok"]
        assert "attach" not in call_sync("settings")["person"]

        # **composer** (§4.5a *Focus composer* **the bar**, TD-500): folded until picked, written, refused in words
        assert '<option value="folded" selected>folded</option>' in client.get("/settings").text
        assert client.post("/api/settings/you", json={"composer": "open"}).json()["ok"]
        assert call_sync("settings")["person"]["composer"] == "open"
        assert '<option value="open" selected>always open</option>' in client.get("/settings").text
        bad = client.post("/api/settings/you", json={"composer": "sideways"})
        assert bad.status_code == 400 and "composer is folded or open, not 'sideways'" in bad.json()["detail"]
        assert call_sync("settings")["person"]["composer"] == "open"

        nope = client.post("/api/settings/teams", json={"team": "nobody", "reserve": "10"})
        assert nope.status_code == 400 and "the org defines" in nope.json()["detail"]
    finally:
        call_sync(
            "set_settings",
            person={"open_in": None, "terminal": None, "inbox": None, "attach": None, "composer": None},
            repos={"agentorc": None},
        )


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
        # **when work appears** (§4.5a, §6 rule 8, TD-227 slice 3): *start the team* is the default while the
        # key is absent (TD-457, TD-466), the pick is written as `on_work`, and anything else is refused
        page = client.get("/settings").text
        assert 'name="on_work" data-was="start"' in page and "start the team (default)" in page
        assert client.post("/api/settings/teams", json={"team": "sett-team", "on_work": "start"}).json()["ok"]
        assert call_sync("settings")["teams"]["sett-team"] == {"on_work": "start"}
        page = client.get("/settings").text
        assert 'data-was="start"' in page and '<option value="start" selected>' in page
        assert "start the team (default)" not in page and "the home starts it" in page
        # the note says all three choices, the picked one first (TD-286)
        note = re.search(r'<span class="note setonwork">([^<]*)</span>', page)[1]
        assert note.startswith("wound down — start the team: the home starts it · ask me: an Inbox row")
        assert note.endswith("do nothing: it waits for Start or its schedule")
        # the definition's file at the card's head, not a foot under Save (TD-286)
        card = page[page.index('data-team="sett-team"') :]
        card = card[: card.index("</form>")]
        assert card.index('defined in <span class="mono">org.yml</span>') < card.index("stop time")
        assert "setfoot" not in card
        bad = client.post("/api/settings/teams", json={"team": "sett-team", "on_work": "maybe"})
        assert bad.status_code == 400 and "ask, start or off" in bad.json()["detail"]
        assert call_sync("settings")["teams"]["sett-team"] == {"on_work": "start"}
    finally:
        org.unlink()
        call_sync("set_settings", teams={"sett-team": None})


def test_every_card_saves_only_a_change_and_cancels_back(client, subprocess_agent):
    """design §4.5a *Settings page* **Save** / **Cancel** (TD-286): every card with fields draws Save
    disabled and Cancel hidden until a field moves, and says the file its values live in; the page's
    script settles them from the form's drawn state and puts the defaults back on Cancel."""
    page = client.get("/settings").text
    forms = re.findall(r'<form class="card pad setcard".*?</form>', page, re.S)
    forms = [f for f in forms if "<input" in f]  # a usage card with no window reported has no field
    assert len(forms) >= 2  # trust a reading for, and You, at the least
    for f in forms:
        assert '<button class="btn sm primary setsave" type="submit" disabled>Save</button>' in f
        assert '<button class="btn sm ghost setcancel" type="button" hidden>Cancel</button>' in f
        assert 'saved to <span class="mono">settings.yml</span>' in f
    js = (UI / "static" / "app.js").read_text()
    assert "f.reset();" in js and "f.dataset.drawn = state(f); settle(f);" in js


def test_a_team_card_draws_balance_and_writes_its_lines_whole(client, subprocess_agent):
    """**balance** (§4.5a *Settings page: Teams*, §6 *Balance*, TD-239 slice 4): off, the key is absent
    and the fields are disabled; a Save writes the lines whole; a field left out is a line not drawn;
    a switch on with no line is refused in place; off removes the key."""
    from sessionorc.client import call_sync

    org = subprocess_agent.home / "org.yml"
    org.write_text(
        f"projects:\n  sp:\n    repos:\n      sp: {{kmaster: {subprocess_agent.home}}}\n"
        "teams:\n  bal-team:\n    projects: [sp]\n    manager: {role: manager}\n"
        "    members: [{role: grinder, count: 2}]\n"
    )
    post = lambda body: client.post("/api/settings/teams", json={"team": "bal-team", **body})  # noqa: E731
    try:
        page = client.get("/settings").text
        assert 'class="setrow setbalance" data-was="null"' in page
        assert re.search(r'name="balance_prs" value=""[^>]* disabled', page)
        assert "the techlead's queue past its bound</label>" in page  # the seat by its role (TD-428 slice 7)
        assert "no live member: a team with none is not read" in page and "open PRs: could not look (no line)" in page
        assert post({"balance": {"prs": "8", "oldest": "2d", "review": True}}).json()["ok"]
        assert call_sync("settings")["teams"]["bal-team"] == {"balance": {"prs": 8, "oldest": "2d", "review": True}}
        page = client.get("/settings").text
        assert 'data-was="{&#34;prs&#34;:8,&#34;oldest&#34;:&#34;2d&#34;,&#34;review&#34;:true}"' in page
        assert 'name="balance_prs" value="8"' in page and "open PRs: could not look (line 8)" in page
        assert not re.search(r'name="balance_prs"[^>]* disabled', page)
        # an empty field and an unticked box are lines not drawn
        assert post({"balance": {"prs": 3, "oldest": "", "review": False}}).json()["ok"]
        assert call_sync("settings")["teams"]["bal-team"] == {"balance": {"prs": 3}}
        refused = (
            ({}, "at least one line"),
            ({"prs": 0}, "1 or more"),
            ({"prs": "²"}, "balance.prs"),
            ({"prs": "many"}, "balance.prs"),
            ({"oldest": "soon"}, "12h or 2d"),
        )
        for bad, said in refused:
            got = post({"balance": bad})
            assert got.status_code == 400 and said in got.json()["detail"], got.json()
        assert post({"balance": "on"}).status_code == 400
        assert call_sync("settings")["teams"]["bal-team"] == {"balance": {"prs": 3}}
        assert post({"balance": None}).json()["ok"]
        assert "bal-team" not in (call_sync("settings")["teams"] or {})
    finally:
        org.unlink()
        call_sync("set_settings", teams={"bal-team": None})


def test_balance_card_reads_the_numbers_and_the_mark():
    """The card's own reading (`balance_card`): the form's `was`, the three lines against the repo's
    numbers, and the mark while one stands."""
    now = datetime.now(UTC)
    fleet = [{"id": "g", "team": "t", "state": "idle", "repo": "/r"}]
    open_ = [{"number": n, "created": (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")} for n in (1, 2, 3)]
    mark = {"since": "", "repo": "/r", "crossed": [{"line": "prs", "value": 3, "limit": 2}]}
    repos = {"/r": {"prs": {"open": open_}, "balance": {"t": mark}}}
    card = setmod.balance_card("t", {"prs": 2, "review": False}, fleet, repos)
    assert card["on"] and card["was"] == '{"prs":2}' and card["prs"] == "2" and not card["review"]
    assert card["live"] and card["now"][0] == "open PRs: 3 (line 2) — over"
    assert card["now"][1].startswith("oldest PR: 5h") and card["now"][1].endswith("(no line)")
    assert card["mark"] == "over its line: 3 open PRs, line 2"
    off = setmod.balance_card("t", None, fleet, repos)
    assert not off["on"] and off["was"] == "null" and off["mark"] == "" and off["now"][0] == "open PRs: 3 (no line)"
    assert setmod.team_cards({"t": object()}, {"t": {"balance": {"prs": 2}}}, sessions=fleet, repos=repos)[0][
        "balance"
    ]["mark"]
    # a mark with no repo: only the home's `host` read carries it (TD-330)
    bare = {"/r": {"prs": {"open": open_}}}
    assert setmod.balance_card("t", {"prs": 2}, fleet, bare)["mark"] == ""
    host = {"balance": {"t": {**mark, "repo": ""}}}
    assert setmod.balance_card("t", {"prs": 2}, fleet, bare, host)["mark"] == "over its line: 3 open PRs, line 2"
    assert setmod.team_cards({"t": object()}, {"t": {"balance": {"prs": 2}}}, sessions=fleet, repos=bare, host=host)[0][
        "balance"
    ]["mark"]


def test_every_page_draws_the_usage_chip(client, subprocess_agent, monkeypatch):
    """design §4.5a *Org top bar* **usage** chip (TD-287): the top bar is one bar, so Settings and the
    other pages draw the account's chip as the Org does, read by one helper."""
    from agentorc.ui import app as appmod

    reading = {"paul": {"windows": [{"label": "week", "pct": 42, "resets": None}], "fetched": "x", "profiles": []}}

    async def chip(call, sessions=None):
        return reading

    monkeypatch.setattr(appmod, "chip_usage", chip)
    for path in ("/settings", "/help", "/inbox", "/new"):
        page = client.get(path).text
        assert 'data-account="paul"' in page and "week 42%" in page, path


@pytest.mark.unit
def test_the_chip_reading_fails_quietly():
    """A host agent that cannot answer gives no chip, never a page that fails; one without `gate`
    still gives the reading."""
    import asyncio

    from agentorc.ui.app import chip_usage

    async def down(method, **kw):
        raise RuntimeError("down")

    async def no_gate(method, **kw):
        if method == "gate":
            raise RuntimeError("unknown method")
        return [] if method == "list" else {}

    assert asyncio.run(chip_usage(down)) == {}
    assert asyncio.run(chip_usage(no_gate)) == {}


@pytest.mark.unit
def test_a_hidden_button_is_not_drawn_whatever_its_class_sets():
    """TD-296 #2: Settings' **Cancel** carries `hidden` until a field changes, but `.btn` sets
    `display: inline-flex`, which beats the UA's `[hidden] { display: none }`, so it showed always
    and pushed Save to its own line on a narrow card. `.btn[hidden]` puts it back."""
    css = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.css").read_text()
    assert ".btn[hidden] { display: none; }" in css
    html = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/templates/settings.html").read_text()
    assert 'class="btn sm ghost setcancel" type="button" hidden' in html


@pytest.mark.unit
def test_the_telegram_card_reads_the_setting_and_the_last_send():
    """**You**: **Telegram** (§4.5a, §4.10; TD-319 slice 3): the switch, the secrets' name and the link as
    `notify.telegram` keeps them, and the later of the home's last send and last failure in its words."""
    off = setmod.telegram(None, None, NOW)
    assert off["on"] is False and off["secrets"] == "" and off["link"] == "" and off["last"] == ""
    assert "a blocked outcome" in off["told"] and "no text a session wrote is sent" in off["when"]
    tg = {"telegram": {"on": True, "secrets": "samscrape/prd", "link": "http://kmaster:8765"}}
    at = (NOW - timedelta(minutes=3)).isoformat()
    got = setmod.telegram(tg, {"last_ok": at}, NOW)
    assert (got["on"], got["secrets"], got["link"]) == (True, "samscrape/prd", "http://kmaster:8765")
    assert got["last"].startswith("last sent ") and not got["last_failed"]
    later = (NOW - timedelta(minutes=1)).isoformat()
    bad = setmod.telegram(tg, {"last_ok": at, "last_error": {"at": later, "reason": "doppler: not logged in"}}, NOW)
    assert (
        bad["last_failed"] and bad["last"].startswith("last send failed ") and "doppler: not logged in" in bad["last"]
    )
    # a send after the failure is the line again
    assert not setmod.telegram(tg, {"last_ok": later, "last_error": {"at": at, "reason": "x"}}, NOW)["last_failed"]


def test_the_telegram_card_saves_through_set_settings_and_sends_a_test(client, subprocess_agent):
    """**Save** writes `notify.telegram` through `set_settings` — an empty field cleared, `on` with no
    secrets refused in the RPC's words — and **Send a test** is `notify_test`'s answer, refused while no
    secrets are saved (§4.10; TD-319 slice 3)."""
    from sessionorc.client import call_sync

    try:
        page = client.get("/settings").text
        assert 'id="settelegram"' in page and 'name="tg_on" ' in page and "Send a test" in page
        assert 'name="tg_on" title="Has the home send you one Telegram message' in page
        no = client.post("/api/settings/notify", json={"telegram": {"on": True, "secrets": "", "link": ""}})
        assert no.status_code == 400 and "secrets" in no.json()["detail"]
        test = client.post("/api/settings/notify_test", json={})
        assert test.status_code == 400 and "secrets is not set" in test.json()["detail"]
        body = {"telegram": {"on": False, "secrets": "look/dev", "link": "http://127.0.0.1:1/"}}
        assert client.post("/api/settings/notify", json=body).json()["ok"]
        assert call_sync("settings")["notify"] == {
            "telegram": {"on": False, "secrets": "look/dev", "link": "http://127.0.0.1:1"}
        }
        page = client.get("/settings").text
        assert 'name="tg_secrets" value="look/dev"' in page and 'name="tg_on" title=' in page
        assert client.post("/api/settings/notify", json={"telegram": {"link": ""}}).json()["ok"]
        assert call_sync("settings")["notify"] == {"telegram": {"on": False, "secrets": "look/dev"}}
        bad = client.post("/api/settings/notify", json={"telegram": {"link": "ftp://x"}})
        assert bad.status_code == 400 and "http://" in bad.json()["detail"]
        assert client.post("/api/settings/notify", json={"telegram": {"token": "x"}}).status_code == 400
    finally:
        call_sync("set_settings", notify={"telegram": None})


@pytest.mark.unit
def test_a_telegram_rows_key_is_the_inboxs_row():
    """`/inbox?row=<key>` (§4.10, TD-319 slice 3): the home's key, `<session>|<kind>` or `work:<team>`, is
    the page's `data-msg` — `<session>:<kind>`, `work:<team>` as it is — by `AO.rowOfKey`, run under node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    from test_ui_menu import PROBE

    probe = pathlib.Path(tempfile.mkdtemp()) / "row_probe.js"
    head = PROBE[: PROBE.index("const P =")]
    probe.write_text(
        head
        + """
const k = (x) => window.AO.rowOfKey(x);
console.log(JSON.stringify([k("ao-x-1|permission"), k("ao-x-1|alarm"), k("work:ao-grind"), k(""), k(null)]));
"""
    )
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == ["ao-x-1:permission", "ao-x-1:alarm", "work:ao-grind", "", ""]
