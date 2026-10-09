"""TD-227 slice 3, design §4.5a *Inbox row: team start* and the team card's *work waiting* note
(§6 rule 8): the row drawn from the home's `work_waiting` alone — counted, the ids linked to the
Repo page, the bound that held a start back said in words — its Snooze by time in the attention
store, Dismiss through `clear_work`, and the card's note beside *wound down*."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentorc.ui import app as uiapp
from agentorc.ui.app import inbox_sections
from agentorc.ui.inbox import work_held, work_note, work_rows, work_started

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
AT = "2026-09-30T11:00:00+00:00"


def mark(**kw):
    return {
        "at": AT,
        "repo": "/home/k/agentorc",
        "members": {"grinder-ao-1": ["TD-213", "TD-214"], "designer-ao-1": ["TD-214", "TD-223"]},
        **kw,
    }


def test_a_mark_is_one_counted_row_with_its_ids_once_and_aged_from_at():
    (r,) = work_rows({"ao-grind": mark()}, {"ao-grind": "2026-09-30T00:56:00+00:00"}, NOW)
    assert r["row"] == "work" and r["sid"] == "work:ao-grind" and r["team"] == "ao-grind"
    assert r["ids"] == ["TD-213", "TD-214", "TD-223"] and r["n"] == 3 and r["more"] == 0
    assert r["repo"] == "agentorc" and r["at"] == AT and r["age"] == "1h 0m" and r["held"] == ""
    assert r["wound_down"] == "2026-09-30T00:56:00+00:00"
    assert "its lanes gained 3 entries: TD-213, TD-214, TD-223" in r["text"]
    s = inbox_sections([], states=[r])
    assert s["count"] == 1 and s["needs"][0]["row"] == "work"
    assert work_rows({}) == [] and work_rows(None) == [] and work_rows({"g": {"members": {}}}) == []
    assert work_rows({"g": "junk"}) == []


def test_a_running_team_names_its_finished_members_and_is_running():
    """§6 rule 8 *A member that finished while its team runs on* (TD-466): a team the card does not read
    as wound down, its mark naming members, reads *ao-grind · grinder-ao-1 finished <t> · its lane
    gained …*, each member by its record's name and the instant it closed."""
    recs = [
        {"id": "ao-g1", "name": "grinder-ao-1", "team": "ao-grind", "closed_at": "2026-09-30T00:56:00Z"},
        {"id": "ao-g1-old", "name": "grinder-ao-1", "team": "ao-grind", "closed_at": "2026-09-29T08:00:00Z"},
        {"id": "ao-g2", "name": "grinder-ao-2", "team": "ao-grind", "closed_at": "2026-09-30T01:10:00Z"},
        {"id": "ao-x1", "name": "grinder-ao-1", "team": "other", "closed_at": "2026-09-30T05:00:00Z"},
    ]
    # the mark keys its members by name (`_work_mark`); the time is the team's latest close under it
    (r,) = work_rows({"ao-grind": mark(members={"grinder-ao-1": ["TD-428"]})}, {}, NOW, records=recs)
    assert r["running"] and r["lanes"] == "its lane"
    assert r["finished"] == [{"name": "grinder-ao-1", "at": "2026-09-30T00:56:00Z"}]
    assert r["text"] == "ao-grind · grinder-ao-1 finished 2026-09-30T00:56:00Z · its lane gained 1 entry: TD-428"
    two_mark = mark(members={"grinder-ao-1": ["TD-1"], "grinder-ao-2": ["TD-2"]})
    (two,) = work_rows({"ao-grind": two_mark}, {}, NOW, records=recs)
    assert "grinder-ao-1 finished 2026-09-30T00:56:00Z, grinder-ao-2 finished 2026-09-30T01:10:00Z" in two["text"]
    assert "their lanes gained 2 entries: TD-1, TD-2" in two["text"]
    # a member the page holds no closed record of is drawn with no time; a wound-down team is the old row
    (gone,) = work_rows({"ao-grind": mark(members={"gone-ao-1": ["TD-1"]})}, {}, NOW)
    assert gone["text"].startswith("ao-grind · gone-ao-1 finished · its lane gained")
    (down,) = work_rows({"ao-grind": mark()}, {"ao-grind": AT}, NOW, records=recs)
    assert not down["running"] and down["text"].startswith("ao-grind · wound down · its lanes gained")


def test_five_ids_at_most_and_one_entry_is_singular():
    many = mark(members={"g": [f"TD-{i:03}" for i in range(1, 9)]})
    (r,) = work_rows({"g": many}, now=NOW)
    assert len(r["ids"]) == 5 and r["more"] == 3 and r["n"] == 8 and r["text"].endswith("TD-005 and 3 more")
    (one,) = work_rows({"g": mark(members={"g": ["TD-001"]})}, now=NOW)
    assert "gained 1 entry: TD-001" in one["text"] and one["wound_down"] == ""


def test_a_questions_end_is_its_own_clause_before_the_ids_and_one_of_the_notes_n():
    """§4.5a *Inbox row: team start* and *work waiting* note (§6 rule 8 *A question's end is work*,
    TD-274): `work_waiting.questions` is a clause of its own before the ids where both stand — *<name>'s
    steer about TD-222 lapsed to its default*, *… ask about TD-223 was answered* — its reference out
    of the lanes' count; alone, the row stands with no *its lanes gained*; the card's note counts it as
    one of the n and names it in the tooltip. A mark with no kind reads a lapse as a steer."""
    q = [
        {"id": "m-s", "ref": "TD-222", "name": "designer-ao-1", "how": "lapsed"},
        {"id": "m-a", "ref": "TD-223", "name": "designer-ao-1", "how": "answered", "kind": "ask"},
    ]
    both = mark(members={"designer-ao-1": ["TD-222", "TD-223", "TD-300"]}, questions=q)
    (r,) = work_rows({"g": both}, now=NOW)
    assert r["ids"] == ["TD-300"] and r["n"] == 1
    assert r["text"] == (
        "g · wound down · designer-ao-1's steer about TD-222 lapsed to its default; designer-ao-1's ask about "
        "TD-223 was answered; its lanes gained 1 entry: TD-300"
    )
    (alone,) = work_rows({"g": mark(members={"designer-ao-1": ["TD-222"]}, questions=q[:1])}, now=NOW)
    assert (
        alone["n"] == 0 and alone["text"] == "g · wound down · designer-ao-1's steer about TD-222 lapsed to its default"
    )
    note = work_note(both, NOW)
    assert note["n"] == 3 and note["title"].endswith("designer-ao-1's ask about TD-223 was answered")
    html = uiapp.templates.get_template("inbox_row.html").module.work_row({**alone, "snoozed_until": ""}, "needs")
    assert '<a class="mono" href="/repo/agentorc#TD-222">TD-222</a> lapsed to its default' in str(html)
    assert "entries:" not in str(html) and "entry:" not in str(html)  # no lanes clause
    both_html = str(uiapp.templates.get_template("inbox_row.html").module.work_row({**r, "snoozed_until": ""}, "needs"))
    assert (
        'was answered; its lanes gained 1 entry: <a class="mono" href="/repo/agentorc#TD-300">TD-300</a>' in both_html
    )


def test_a_held_start_says_which_bound():
    early = (NOW - timedelta(minutes=12)).isoformat()
    cases = {
        "grind is over its line": {"why": "usage", "profile": "grind"},
        "grind is over its line, resets in 3h 0m": {
            "why": "usage",
            "profile": "grind",
            "resets": (NOW + timedelta(hours=3)).isoformat(),
        },
        "its stop time has passed": {"why": "until", "until": AT},
        "started 3 times today": {"why": "day", "count": 3},
        "started 12m ago and wound down again": {"why": "early", "started": early},
        "laptop is unreachable": {"why": "link", "host": "laptop"},
        "laptop and nas are unreachable": {"why": "link", "host": "laptop", "hosts": ["laptop", "nas"]},
        "no record to start": {"why": "nothing"},
    }
    for words, held in cases.items():
        assert work_held(held, NOW) == words
    crossed = [{"line": "prs", "value": 9, "limit": 8}]
    said = work_held({"why": "balance", "repo": "/r", "crossed": crossed}, NOW)
    assert said.startswith("its repo is over its line") and "the line is 8" in said
    assert work_held(None, NOW) == "" and work_held("junk", NOW) == ""
    past = {"why": "usage", "profile": "grind", "resets": AT}  # a reset already past names no time
    assert work_held(past, NOW) == "grind is over its line"
    (r,) = work_rows({"g": mark(held={"why": "nothing"})}, now=NOW)
    assert r["held"] == "no record to start" and r["text"].endswith("not started: no record to start")


def test_a_snoozed_row_is_in_no_count_and_later_entries_do_not_wake_it():
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    for m in (mark(), mark(members={"g": ["TD-900"]})):
        s = inbox_sections([], states=work_rows({"ao-grind": m}), attention_snoozed={"work:ao-grind|work": until})
        assert s["count"] == 0 and [e["row"] for e in s["snoozed"]] == ["work"]


def test_the_cards_note_and_the_started_line():
    n = work_note(mark(), NOW)
    assert n["n"] == 3 and n["at"] == AT and n["age"] == "1h 0m"
    assert n["title"] == "grinder-ao-1: TD-213, TD-214; designer-ao-1: TD-214, TD-223"
    assert work_note(None) is None and work_note({"members": {}}) is None
    older = {"at": "2026-09-30T09:00:00+00:00", "why": "work", "ids": ["TD-100"]}
    newer = {"at": "2026-09-30T11:30:00+00:00", "why": "work", "ids": ["TD-213", "TD-214", "TD-223"]}
    live = {"state": "idle", "restarts": [older, {"at": "2026-09-30T11:45:00+00:00", "why": "crash"}, newer]}
    got = work_started(
        [live, {"state": "exited", "restarts": [{"at": "2026-09-30T11:50:00+00:00", "why": "work"}]}], NOW
    )
    assert got == {"at": newer["at"], "age": "30m", "first": "TD-213", "more": 2}
    assert work_started([{"state": "idle", "restarts": [{"at": AT, "why": "crash"}]}]) is None
    assert work_started([{"state": "idle", "restarts": [{"at": AT, "why": "work", "error": "x"}]}]) is None
    # a start whose replays partly failed says how many came up and which did not (TD-227)
    one = {"why": "work", "ids": ["TD-213"], "start": AT, "of": 3}
    crew = [
        {"name": "grinder-ao-1", "state": "working", "restarts": [{**one, "at": AT}]},
        {"name": "grinder-ao-2", "state": "idle", "restarts": [{**one, "at": AT}]},
        {"name": "manager-ao-1", "state": "exited", "restarts": [{**one, "at": AT, "error": "no launch record"}]},
    ]
    got = work_started(crew, NOW)
    assert (got["n"], got["of"], got["failed"]) == (2, 3, ["manager-ao-1"])
    assert "failed" not in work_started(crew[:2], NOW), "every replay came up: the note is as it was"
    # started later by a crash restart or a person: the errored entry rides its history, and it is up
    back = {"name": "manager-ao-1", "state": "idle", "restarts": [*crew[2]["restarts"], {"at": AT, "why": "start"}]}
    assert "failed" not in work_started([*crew, back], NOW) and "failed" not in work_started([*crew[:2], back], NOW)


def _session(name: str, **kw) -> dict:
    return {
        "id": f"ao-r-{name}",
        "name": name,
        "host": "kmaster",
        "dir": "/tmp/r",
        "adapter": "claude",
        "state": "exited",
        "team": "wt",
        "unattended": True,
        "controllers": [],
        "capabilities": [],
        "created": "2026-09-30T00:00:00+00:00",
        **kw,
    }


@pytest.fixture
def page(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    (home / "org.yml").write_text(
        f"projects:\n  p:\n    repos:\n      r: {{kmaster: {tmp_path}}}\n"
        "teams:\n  wt:\n    projects: [p]\n    manager: {role: manager, name: m}\n"
        "    members: [{role: grinder, name: g}]\n"
    )
    calls: list[tuple[str, dict]] = []
    work = {"wt": mark(members={"g": ["TD-213", "TD-214"]})}
    out = {"at": "2026-09-30T00:56:00+00:00", "why": "nothing to pick"}
    answers = {
        "list": [_session("m", out_of_work=out), _session("g", out_of_work=out)],
        "inbox": {"id": "person", "entries": [], "threads": {}, "sends": [], "unread": 0},
        "identity": {
            "host": "kmaster",
            "mode": "off",
            "detached_check": False,
            "tally": {},
            "alarms": [],
            "sessions": {},
        },
        "usage": {},
        "host": {
            "host": "kmaster",
            "home": "kmaster",
            "mode": "home",
            "home_reachable": True,
            "links": {},
            "work": work,
        },
        "clear_work": {"team": "wt", "cleared": True, "ids": ["TD-213", "TD-214"]},
        "attention_snooze": {"id": "person", "row": "work:wt|work", "snoozed_until": "x"},
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            return answers[method]

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        c.app.state.answers = answers  # a test that reads a running team moves the list
        yield c, calls, work


def test_the_inbox_draws_the_row_with_its_three_controls_and_counts_it(page):
    c, calls, work = page
    html = c.get("/inbox").text
    assert 'data-row="work"' in html and "its lanes gained 2 entries:" in html
    assert '<a class="mono" href="/repo/agentorc#TD-213">TD-213</a>' in html
    assert 'data-at="2026-09-30T00:56:00+00:00"' in html  # wound down <t>, the card's own reading
    assert 'data-act="work_start" data-id="person" data-team="wt"' in html
    assert 'data-sid="work:wt" data-row="work" data-when="1w"' in html
    assert 'data-act="clear_work" data-id="person" data-team="wt"' in html and "not started:" not in html
    assert c.get("/api/person/inbox").json()["needs"] == 1
    work["wt"]["held"] = {"why": "day", "count": 3}
    assert "not started: started 3 times today" in c.get("/inbox").text
    assert c.post("/api/person/clear_work", json={"team": "wt"}).json()["cleared"] is True
    assert c.post("/api/person/clear_work", json={}).status_code == 400
    assert c.post("/api/person/attention_snooze", json={"id": "work:wt", "kind": "work", "until": "x"}).json()["ok"]
    assert ("clear_work", {"team": "wt"}) in calls
    assert ("attention_snooze", {"id": "work:wt", "kind": "work", "until": "x"}) in calls


def test_the_org_counts_the_row_and_the_card_says_work_waiting(page):
    c, _calls, work = page
    got = c.get("/")
    assert got.status_code == 200 and 'id="personneeds">1<' in got.text
    assert "wound down" in got.text and "2 entries waiting since" in got.text
    assert 'title="g: TD-213, TD-214"' in got.text
    work.clear()
    again = c.get("/").text
    assert "entries waiting since" not in again and 'id="personneeds">1<' not in again


def test_a_running_teams_row_starts_its_members_through_work_start(page):
    """The row for a team that runs on (§4.5a **Inbox row: team start**, TD-466): its finished member
    named, its **Start** marked running so the page posts `work_start`, which the server refuses in
    the row's words when a bound holds it back."""
    c, calls, work = page
    answers = c.app.state.answers
    out = {"at": "2026-09-30T00:56:00+00:00", "why": "nothing to pick"}
    answers["list"] = [
        _session("m", state="idle"),
        _session("g", state="closed", out_of_work=out, closed_at="2026-09-30T00:56:00+00:00"),
    ]
    work["wt"] = mark(members={"g": ["TD-213"]})
    html = c.get("/inbox").text
    assert re.search(r"· g finished <span class=\"localtime\" data-at=\"2026-09-30T00:56:00\+00:00\"", html)
    assert "its lane gained 1 entry:" in html and "wound down" not in html
    assert 'data-act="work_start" data-id="person" data-team="wt" data-running="1"' in html
    answers["work_start"] = {"team": "wt", "started": True, "ids": ["TD-213"], "held": None}
    assert c.post("/api/person/work_start", json={"team": "wt"}).json()["started"] is True
    assert ("work_start", {"team": "wt"}) in calls
    answers["work_start"] = {"team": "wt", "started": False, "ids": ["TD-213"], "held": {"why": "day", "count": 3}}
    held = c.post("/api/person/work_start", json={"team": "wt"})
    assert held.status_code == 409 and held.json()["detail"] == "started 3 times today"
    answers["work_start"] = {"team": "wt", "started": False, "ids": [], "held": None}
    assert c.post("/api/person/work_start", json={"team": "wt"}).status_code == 409
    assert c.post("/api/person/work_start", json={}).status_code == 400
