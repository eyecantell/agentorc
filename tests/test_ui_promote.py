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


def test_a_held_row_is_counted_whatever_auto_says_and_reads_rolled_back():
    """§4.5a *After a rollback* (§6 *The hold*, TD-226 slice 3): drawn while the home's `held` stands,
    under auto too and with live behind main, under *Needs you*, reading *rolled back from*, never
    *behind*; Dismiss is drawn on it, each control titled by its help paragraph."""
    from agentorc.ui.app import templates
    from agentorc.ui.help import first_sentence

    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    held = {"sha": LIVE, "from": "7" * 40, "main": MAIN, "at": "2026-09-29T09:00:00+00:00"}
    for auto in (True, False):
        (r,) = promote_rows({"agentorc": reading(auto=auto, held=held)}, now)
        assert r["text"] == (
            "agentorc · live 4444444, rolled back from 7777777 3h 0m ago · main 9999999, 3 commits ahead"
            f" · checks green · auto {'on' if auto else 'off'} · held"
        )
        assert not r["fyi"] and r["held"] == held and inbox_sections([], states=[r])["count"] == 1
    # held with live at main's head (main has not moved since): still drawn, the hold is the person's
    (r,) = promote_rows({"agentorc": reading(auto=True, live=MAIN, ahead=0, held=held)}, now)
    assert "rolled back from 7777777" in r["text"]
    html = templates.get_template("inbox_rows.html").render(rows=[r], section="needs")
    assert 'data-act="clear_promote"' in html and 'data-act="promote"' in html
    for key in ("promote", "promote-snooze", "promote-dismiss"):
        assert f'title="{first_sentence(key)}"'.replace("'", "&#39;") in html, key
    # neither failed nor held: no Dismiss
    (r,) = promote_rows({"agentorc": reading()}, now)
    html = templates.get_template("inbox_rows.html").render(rows=[r], section="needs")
    assert 'data-act="clear_promote"' not in html


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


def test_the_org_top_bar_counts_the_row_as_the_inbox_does(page):
    """The review of #668: the Org's first render builds its own count, and it must agree with the
    Inbox's (the two numbers cannot drift apart, review of PR #251)."""
    c, _calls, _ = page
    got = c.get("/")
    assert got.status_code == 200 and 'id="personneeds">1<' in got.text


def test_every_page_and_the_inbox_poll_draw_the_build_chip(page, monkeypatch):
    """Design §4.5a top bar **build** chip (TD-539): on every page, always drawn; the agent here
    reports no build, so it reads *build unknown* with `build.line` on hover. A live build carries
    `data-at` for the page to reprint in the browser's zone, and the Inbox poll brings it again."""
    c, _calls, _ = page
    for path in ("/", "/inbox"):
        html = c.get(path).text
        assert 'id="buildchip" title="host agent: build unknown' in html and ">build unknown</span>" in html, path
    assert c.get("/api/person/inbox").json()["build_chip"]["text"] == "build unknown"
    live = {"text": "live 10-10 15:41 · main +2", "title": "a\nb", "cls": "behind", "at": "2026-10-10T15:41:00+00:00",
            "rest": " · main +2"}  # fmt: skip
    monkeypatch.setattr(uiapp, "build_chip", lambda info: live)
    html = c.get("/inbox").text
    want = 'class="mono behind" id="buildchip" title="a\nb" data-at="2026-10-10T15:41:00+00:00" data-rest=" · main +2"'
    assert want in html
    monkeypatch.setattr(uiapp, "build_chip", lambda info: None)  # the agent did not answer
    assert 'id="buildchip"' not in c.get("/").text
