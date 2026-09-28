"""TD-132 slice 3, design §4.5a *Inbox row: promote* (§6 *Promote*): the row drawn from the home's
`promotes` reading alone — counted under `auto: false` while main is ahead and on a failure, FYI while
a run is in flight, nothing but a failure under `auto: true` — its Snooze by time in the attention
store, and Promote and Dismiss through `/api/person/<action>`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentorc.ui import app as uiapp
from agentorc.ui.app import inbox_sections, promote_rows

pytestmark = pytest.mark.unit

LIVE, MAIN = "4" * 40, "9" * 40


def reading(**kw):
    return {
        "live": LIVE,
        "main": MAIN,
        "ahead": 3,
        "checks": "green",
        "auto": False,
        "moved": "2026-09-27T10:00:00+00:00",
        **kw,
    }


def test_by_hand_main_ahead_is_a_counted_row_and_live_at_main_is_none():
    (r,) = promote_rows({"agentorc": reading()})
    assert r["row"] == "promote" and r["sid"] == "promote:agentorc" and not r["fyi"]
    assert r["text"] == "agentorc · live 4444444 · main 9999999, 3 commits ahead · checks green"
    assert r["at"] == "2026-09-27T10:00:00+00:00"  # aged from when main moved
    assert promote_rows({"agentorc": reading(live=MAIN, ahead=0)}) == []
    s = inbox_sections([], states=promote_rows({"agentorc": reading()}))
    assert s["count"] == 1 and s["needs"][0]["row"] == "promote"


def test_in_flight_is_fyi_and_uncounted():
    flight = {"sha": MAIN, "at": "2026-09-27T10:05:00+00:00", "by": "person", "log": "/l"}
    (r,) = promote_rows({"agentorc": reading(inflight=flight)})
    assert r["fyi"] and r["at"] == flight["at"]
    s = inbox_sections([], states=[r])
    assert s["count"] == 0 and [e["row"] for e in s["fyi"]] == ["promote"]


def test_under_auto_nothing_but_a_failure_and_a_failure_whatever_auto_says():
    assert promote_rows({"agentorc": reading(auto=True)}) == []
    failed = {
        "sha": MAIN,
        "at": "2026-09-27T10:30:00+00:00",
        "why": "it broke",
        "log": "/l",
        "exit": 3,
        "tail": ["boom"],
    }
    for auto in (True, False):
        (r,) = promote_rows({"agentorc": reading(auto=auto, failed=failed)})
        assert not r["fyi"] and r["failed"]["why"] == "it broke"
        assert inbox_sections([], states=[r])["count"] == 1


def test_a_snoozed_row_is_in_no_count_and_merges_do_not_wake_it():
    until = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    snoozed = {"promote:agentorc|promote": until}
    for n in (3, 5):  # a later merge moves main and the count; the row stays aside
        s = inbox_sections([], states=promote_rows({"agentorc": reading(ahead=n)}), attention_snoozed=snoozed)
        assert s["count"] == 0 and [e["row"] for e in s["snoozed"]] == ["promote"]


@pytest.fixture
def page(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    calls: list[tuple[str, dict]] = []
    promotes = {"agentorc": reading()}
    answers = {
        "list": [],
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
            "promotes": promotes,
        },
        "promote": {
            "repo": "agentorc",
            "sha": MAIN,
            "started": "t",
            "log": "/l",
            "checks": "green",
            "checks_why": None,
        },
        "clear_promote": {"repo": "agentorc", "cleared": True},
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
        yield c, calls, promotes


def test_the_inbox_draws_the_row_with_promote_and_snooze_and_counts_it(page):
    c, calls, promotes = page
    html = c.get("/inbox").text
    assert "agentorc · live 4444444 · main 9999999, 3 commits ahead · checks green" in html
    assert 'data-act="promote" data-id="person" data-repo="agentorc"' in html
    assert 'data-row="promote" data-when="1w"' in html and 'data-act="clear_promote"' not in html
    assert c.get("/api/person/inbox").json()["needs"] == 1
    promotes["agentorc"] = reading(
        failed={"sha": MAIN, "at": "t", "why": "it broke", "log": "/l", "exit": 3, "tail": ["boom"]}
    )
    html = c.get("/inbox").text
    assert "failed: promoting 9999999 — it broke (exit 3)" in html and 'data-act="clear_promote"' in html


def test_promote_and_dismiss_go_to_the_rpcs_with_the_repo(page):
    c, calls, _ = page
    assert c.post("/api/person/promote", json={"repo": "agentorc"}).json()["sha"] == MAIN
    assert c.post("/api/person/clear_promote", json={"repo": "agentorc"}).json()["cleared"] is True
    assert [(m, p) for m, p in calls if m in ("promote", "clear_promote")] == [
        ("promote", {"repo": "agentorc"}), ("clear_promote", {"repo": "agentorc"}),
    ]  # fmt: skip
    assert c.post("/api/person/promote", json={}).status_code == 400
