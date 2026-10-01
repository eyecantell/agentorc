"""TD-258 slice 5, design §4.5a *Inbox row: cadence check failed* and *merged without its read* (§6
rules 10 and 11): each row drawn from its mark on the member's record — `row` on a `checks` entry,
two `held_missed` entries not dismissed — counted under *Needs you*, each `#n` a link, and gone
when the mark is cleared; the first with Snooze keyed on the record and the PR, both with Dismiss
through `clear_mark`."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agentorc.ui import app as uiapp
from agentorc.ui import inbox as inboxmod
from agentorc.ui.app import inbox_sections, state_rows, templates, view
from sessionorc import cadence, held

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
FAIL = {"pr": 842, "at": "2026-10-01T14:02:00Z", "sha": "aaa", "verdict": "fail", "failed": ["review", "ledger"]}
ROWED = {**FAIL, "told": "2026-10-01T13:00:00Z", "row": "2026-10-01T14:02:00Z"}
REVIEW = {"reader": "techlead", "held": ["src/sessionorc/**"]}


def rec(sid="ao-x-grinder-ao-1", **kw):
    return {
        "id": sid, "name": sid.removeprefix("ao-x-"), "kind": "agent", "adapter": "claude-code", "dir": "/w",
        "repo": "/w", "state": "working", "pane": True, "tail": [], "since": "2026-10-01T10:00:00Z",
        "confidence": "hook", "team": "ao", "supervised": True, "unattended": True, **kw,
    }  # fmt: skip


def rows_of(*records):
    return [r for r in state_rows([view(r, list(records)) for r in records]) if r.get("mark")]


def html(rows):
    return templates.get_template("inbox_rows.html").render(rows=rows, section="needs")


@pytest.fixture(autouse=True)
def _links(monkeypatch):
    monkeypatch.setattr(inboxmod.review_mod, "pr_url", lambda d, pr: f"https://forge/{pr}" if d == "/w" else "")


def test_a_cadence_row_is_drawn_from_row_on_a_checks_entry_one_a_pr():
    merged = {**FAIL, "pr": 843, "failed": ["ledger"], "merged": True, "row": "2026-10-01T14:10:00Z"}
    r = rec(checks=[FAIL, ROWED | {"read_by": "ao-x-techlead-ao-1"}, merged, {**FAIL, "pr": 844, "verdict": "pass"}])
    first, second = rows_of(r, rec("ao-x-techlead-ao-1", seat={"trigger": "asks"}, state="closed", pane=False))
    assert (first["row"], first["id"], first["pr"]) == ("cadence:842", f"{r['id']}:cadence:842", 842)
    assert re.fullmatch(
        r"PR #842 fails the cadence check: review \(read by techlead-ao-1\), ledger · read (\w{3} )?\d\d:\d\d",
        first["text"],
    ), first["text"]
    assert first["at"] == ROWED["row"] and second["text"].startswith("PR #843 fails the cadence check: ledger · read")
    assert "(recorded)" in rows_of(rec(checks=[ROWED]))[0]["text"]
    assert rows_of(rec(checks=[FAIL])) == [], "a first fail is the member's line, not a row"
    assert rows_of(rec(checks=[ROWED], superseded_by="ao-x-new")) == []
    page = html([first])
    assert '<a class="ref" href="https://forge/842" target="_blank" rel="noopener"' in page and ">#842</a>" in page
    assert "cadence check failed — the host agent told the member once" in page
    assert f'data-sid="{r["id"]}" data-row="cadence:842" data-when="1h"' in page
    assert f'data-act="clear_mark" data-id="person" data-sid="{r["id"]}" data-kind="cadence" data-pr="842"' in page
    assert f'href="/focus/{r["id"]}"' in page and ">Open</a>" in page
    # counted under Needs you; set aside by its own key, the other PR's row untouched
    got = inbox_sections([], states=[first, second], now=NOW)
    assert [x["row"] for x in got["needs"]] == ["cadence:842", "cadence:843"]
    later = inbox_sections(
        [], states=[first, second], now=NOW, attention_snoozed={f"{r['id']}|cadence:842": "2026-10-01T16:00:00Z"}
    )
    assert [x["row"] for x in later["needs"]] == ["cadence:843"] and [x["row"] for x in later["snoozed"]] == [
        "cadence:842"
    ]
    # gone when the mark is cleared: Dismiss's write, and a later pass
    left, _ = cadence.dismiss(r["checks"], 842)
    assert [x["row"] for x in rows_of(rec(checks=left))] == ["cadence:843"]
    passed = cadence.record(ROWED, 842, "bbb", False, {"verdict": "pass", "failed": []}, "t")
    assert rows_of(rec(checks=[passed])) == []


def test_a_held_row_is_two_crossings_not_dismissed():
    a = held.crossing(845, ["src/sessionorc/agent_tick.py"], "2026-10-01T12:00:00Z")
    b = held.crossing(851, ["src/sessionorc/held.py", "src/sessionorc/agent_tick.py"], "2026-10-01T13:00:00Z")
    assert rows_of(rec(held_missed=[a], review=REVIEW)) == [], "the first is a note and a line"
    r = rec("ao-x-grinder-ao-2", held_missed=[a, b], review=REVIEW)
    (row,) = rows_of(r)
    assert row["text"] == (
        "PR #845 and #851 touched held paths and merged without the techlead's read"
        " · src/sessionorc/agent_tick.py, src/sessionorc/held.py"
    )
    assert (row["row"], row["at"], row["id"]) == ("held", b["at"], f"{r['id']}:held")
    many = held.crossing(860, [f"src/sessionorc/{n}.py" for n in "abc"], "t")
    (three,) = rows_of(rec(held_missed=[a, b, many], review={**REVIEW, "reader": "person"}))
    assert three["text"].startswith("PR #845, #851 and #860 touched held paths and merged without the person's read")
    assert three["text"].endswith("src/sessionorc/held.py, src/sessionorc/a.py and 2 more")
    page = html([row])
    assert ">#845</a> and <a" in page and 'href="https://forge/851"' in page
    assert "merged without its read — the host agent undoes nothing" in page
    assert f'data-act="clear_mark" data-id="person" data-sid="{r["id"]}" data-kind="held"' in page
    assert 'data-act="attention_snooze"' not in page, "no Snooze: a merge is done"
    assert [x["row"] for x in inbox_sections([], states=[row], now=NOW)["needs"]] == ["held"]
    marked, _ = held.dismiss(r["held_missed"], "t")
    assert rows_of(rec(held_missed=marked, review=REVIEW)) == []
    assert rows_of(rec(held_missed=[*marked, many], review=REVIEW)) == [], "one since the Dismiss is a note again"


def test_a_checkout_with_no_forge_draws_the_number_bare():
    (row,) = rows_of(rec(checks=[ROWED], repo="/elsewhere", dir="/elsewhere"))
    page = html([row])
    assert "PR #842 fails the cadence check" in page and "https://forge" not in page


def test_dismiss_is_clear_mark_with_the_session_the_mark_and_the_pr(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            return {"id": params.get("id"), "kind": params.get("kind"), "cleared": [842]}

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        got = c.post("/api/person/clear_mark", json={"sid": "ao-x-g", "kind": "cadence", "pr": 842})
        assert got.json()["cleared"] == [842]
        assert c.post("/api/person/clear_mark", json={"sid": "ao-x-g", "kind": "held", "pr": 7}).json()["ok"]
        for bad in ({"kind": "held"}, {"sid": "ao-x-g", "kind": "other"}, {"sid": "ao-x-g", "kind": "cadence"}):
            assert c.post("/api/person/clear_mark", json=bad).status_code == 400
    marks = [p for m, p in calls if m == "clear_mark"]
    assert marks == [{"id": "ao-x-g", "kind": "cadence", "pr": 842}, {"id": "ao-x-g", "kind": "held", "pr": None}]
