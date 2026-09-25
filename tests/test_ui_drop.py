"""**Drop** refused while a claim is in review (design §4.5a *Focus side panel → Reports*, TD-150
slice 2): the route asks the record, and a claim whose PR is open is not let go."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_review_pr_is_the_panels_rule():
    from agentorc.ui.app import review_pr

    claimed = {"ref": "TD-127", "status": "claimed", "source": "declared"}
    assert review_pr([claimed], "TD-127") is None
    assert review_pr([{**claimed, "pr": 532}], "td-127") == 532
    assert review_pr([{**claimed, "ref": "TD-027", "pr": 9}], "td-27") == 9  # the agent's own normal form
    assert review_pr([{**claimed, "review_pr": 533}], "TD-127") == 533  # its branch's (slice 3)
    assert review_pr([{**claimed, "source": "derived", "pr": 534}], "TD-127") is None  # nothing declared
    assert review_pr([{**claimed, "status": "done", "pr": 532}], "TD-127") is None


@pytest.mark.unit
def test_the_route_refuses_a_drop_in_review_and_lets_one_in_progress_go(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui import app as uiapp

    calls = []
    record = {"id": "ao-w", "progress": []}

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            if method == "get":
                return record
            if method == "progress":
                calls.append(kw)
            return {}

    monkeypatch.setattr(uiapp, "LocalClient", Fake)
    with TestClient(uiapp.create_app()) as c:
        record["progress"] = [{"ref": "TD-127", "status": "claimed", "source": "declared", "pr": 532}]
        r = c.post("/api/sessions/ao-w/drop", json={"ref": "TD-127"})
        assert r.status_code == 409 and "in review as PR #532" in r.json()["detail"] and not calls
        record["progress"] = [{"ref": "TD-128", "status": "claimed", "source": "declared"}]
        assert c.post("/api/sessions/ao-w/drop", json={"ref": "TD-128"}).status_code == 200
        assert calls[-1]["why"] == "dropped from Focus" and calls[-1]["status"] == "dropped"
