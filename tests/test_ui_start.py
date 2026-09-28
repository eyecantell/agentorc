"""TD-152 slice 3, design §4.5a New session **At** field and the **starts** note (§6 *Start time*): the
form's At parsed as Until is and refused without Unattended, a scheduled record's Start lands on the
Org, the card carries the note and `more ⋯`'s Start now and Cancel, and Start now / the note's time go
to `set_start`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentorc.ui import app as uiapp

pytestmark = pytest.mark.unit

AT = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rec(**kw):
    return {"id": "ao-r-w", "name": "w", "state": "scheduled", "dir": "/tmp", "adapter": "shell", "kind": "interactive",
            "unattended": True, "start_at": AT, "controllers": [], "capabilities": [], "since": AT, **kw}  # fmt: skip


@pytest.fixture
def page(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            if method == "create":
                return rec(start_at=params.get("start_at"))
            if method == "set_start":
                return rec(start_at=AT if params["start_at"] != "now" else datetime.now(UTC).isoformat())
            if method == "seen":
                return None
            raise AssertionError(method)

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        yield c, calls, tmp_path


def test_the_at_field_schedules_and_lands_on_the_org(page):
    c, calls, tmp_path = page
    r = c.post("/new", data={"name": "w", "dir": str(tmp_path), "adapter": "shell", "unattended": "on", "at": "+2h"},
               follow_redirects=False)  # fmt: skip
    assert r.status_code == 303 and r.headers["location"] == "/"
    (made,) = [p for m, p in calls if m == "create"]
    assert made["start_at"].endswith("Z") and made["unattended"] is True
    r = c.post(
        "/new", data={"name": "w", "dir": str(tmp_path), "adapter": "shell", "at": "+2h"}, follow_redirects=False
    )
    assert r.status_code == 400 and "tick Unattended" in r.text


def test_start_now_and_the_notes_time_go_to_set_start(page):
    c, calls, _ = page
    assert c.post("/api/sessions/ao-r-w/start", json={"at": "now"}).json()["ok"]
    got = c.post("/api/sessions/ao-r-w/start", json={"at": "+2h"}).json()
    assert got["start_note"].startswith("starts ")
    assert [p["start_at"] for m, p in calls if m == "set_start"][0] == "now"
    assert c.post("/api/sessions/ao-r-w/start", json={"at": "someday"}).status_code == 400


def test_the_card_carries_the_note_and_start_now_and_cancel():
    v = uiapp.view(rec(), [rec()])
    assert v["start_note"].startswith("starts ") and v["state_class"] == "scheduled"
    html = uiapp.templates.get_template("card.html").render(s=v, help_title=lambda k: k, role_svg=lambda *a: "")
    assert 'class="meta startnote"' in html and 'data-act="start" data-id="ao-r-w" data-at="now"' in html
    assert "Cancel w&#39;s start?" in html or "Cancel w's start?" in html
