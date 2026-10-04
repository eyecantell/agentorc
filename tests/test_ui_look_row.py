"""A look's Inbox row (design §4.5a **Inbox row: a look**, §4.10 *A look*; TD-292 slice 3): the
screenshots under the text, served from one directory of a registered repo at origin's default
branch, and on an `ask` the pair *Works* / *Not right: <what>* drawn as **Works** and **Not right…**."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient
from test_ui_inbox import entry, rows

PNG = b"\x89PNG\r\n\x1a\n" + b"look" * 8


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def checkout(tmp_path, monkeypatch, name="proj", shots=("a.png",)):
    """A registered checkout `name` whose origin's `main` holds `shots` under docs/mockups/reviews/,
    with a working-tree file that origin does not hold (`local.png`)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    origin, root = tmp_path / "origin.git", tmp_path / name
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(root))
    git(root, "checkout", "-q", "-b", "main")
    d = root / "docs" / "mockups" / "reviews"
    d.mkdir(parents=True)
    for s in shots:
        (d / s).write_bytes(PNG + s.encode())
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "shots")
    git(root, "push", "-q", "origin", "main")
    (d / "local.png").write_bytes(PNG)  # in the working tree only
    roster = tmp_path / "repos.txt"
    roster.write_text(f"{root}\n")
    (tmp_path / "home" / "hosts.yml").write_text(
        f"local:\n  name: kmaster\n  local: true\n  repos_registry: {roster}\n"
    )
    from agentorc.ui import inbox

    inbox._shot_seen.clear()
    inbox._shot_host_down.clear()
    return root


def look(kind="ask", **kw):
    kw.setdefault("shots", ["docs/mockups/reviews/a.png"])
    kw.setdefault("look_shots", [{"name": "a.png", "url": "/repo/proj/shot/a.png"}])
    return entry("m-1", kind, text="the Settings card: is the Save where it should be?", **kw)


@pytest.mark.unit
def test_an_ask_look_draws_works_and_not_right_and_its_screenshots():
    """On the `ask`, *Works* replies at once (index 0, as any suggested answer) and **Not right…**
    opens the Reply composer with *Not right:* begun; the screenshots are under the text, each
    opening in a new tab, and one origin does not hold draws its name alone."""
    e = look(
        answers=["Works", "Not right: <what>"],
        look_shots=[{"name": "a.png", "url": "/repo/proj/shot/a.png"}, {"name": "gone.png", "url": ""}],
    )
    html = rows("needs", [e])
    assert 'data-act="answer" data-id="person" data-msg="m-1" data-index="0"' in html
    assert '<span class="alabel">Works</span>' in html
    assert 'data-act="reply"' in html and 'data-begun="Not right: "' in html and "Not right…" in html
    assert 'data-index="1"' not in html and "&ldquo;Not right: &lt;what&gt;&rdquo;" not in html
    assert '<a class="shot" href="/repo/proj/shot/a.png" target="_blank"' in html
    assert '<img src="/repo/proj/shot/a.png" alt="a.png"' in html
    assert '<span class="shot gone st"' in html and "gone.png" in html
    assert html.index('class="row gap wrap shots"') < html.index('class="row gap wrap sugg"')  # under the text


@pytest.mark.unit
def test_a_steer_look_keeps_its_kinds_row_and_a_pair_without_shots_is_ordinary():
    """A `steer` look changes nothing but the screenshots; an `ask` with the pair and no `shots` is
    not a look — a look is known by its envelope, never by its words."""
    html = rows("steering", [look("steer", default="Works", answers=["Works", "Not right: <what>"])])
    assert 'class="row gap wrap shots"' in html and "data-begun" not in html
    assert "&ldquo;Works&rdquo;" in html and "Go with it" in html
    plain = rows("needs", [entry("m-2", "ask", answers=["Works", "Not right: <what>"])])
    assert "data-begun" not in plain and 'class="row gap wrap shots"' not in plain
    assert "&ldquo;Works&rdquo;" in plain and 'data-index="1"' in plain


@pytest.mark.unit
def test_look_pair_is_the_exact_pair_on_an_ask_with_shots():
    from agentorc.ui.inbox import look_pair

    s = ["docs/mockups/reviews/a.png"]
    assert look_pair({"kind": "ask", "shots": s, "answers": ["Works", "Not right: <what>"]})
    assert look_pair({"kind": "ask", "shots": s, "answers": ["Works", "Not right:"]})
    assert not look_pair({"kind": "steer", "shots": s, "answers": ["Works", "Not right: <what>"]})
    assert not look_pair({"kind": "ask", "shots": [], "answers": ["Works", "Not right: <what>"]})
    assert not look_pair({"kind": "ask", "shots": s, "answers": ["Works", "Not right: wrong repo"]})
    assert not look_pair({"kind": "ask", "shots": s, "answers": ["Works", "Not right: <what>", "Later"]})


@pytest.mark.integration
def test_look_shots_address_only_what_origin_holds_in_the_one_directory(tmp_path, monkeypatch):
    checkout(tmp_path, monkeypatch, shots=("a.png", "b.png"))
    from agentorc.ui.inbox import look_shots

    got = look_shots(
        [
            "docs/mockups/reviews/a.png",
            "./docs/mockups/reviews/local.png",  # the working tree's, not origin's
            "docs/other/b.png",  # outside the directory
            "docs/mockups/reviews/b.png",
            "docs/mockups/reviews/c.png",  # a fifth: dropped
        ],
        "proj",
    )
    assert got == [
        {"name": "a.png", "url": "/repo/proj/shot/a.png"},
        {"name": "local.png", "url": ""},
        {"name": "b.png", "url": ""},
        {"name": "b.png", "url": "/repo/proj/shot/b.png"},
    ]
    assert look_shots(["docs/mockups/reviews/a.png"], "elsewhere") == [{"name": "a.png", "url": ""}]
    assert look_shots("docs/mockups/reviews/a.png", "proj") == []


@pytest.mark.unit
def test_an_image_past_the_bound_draws_its_name_alone(tmp_path, monkeypatch):
    from sessionorc import shots

    monkeypatch.setattr(shots, "SHOT_BYTES_MAX", len(PNG) + len("a.png"))
    root = checkout(tmp_path, monkeypatch, shots=("a.png", "big.png.png"))
    from agentorc.ui.inbox import look_shots

    assert shots.exists(root.resolve(), "a.png") and shots.read(root.resolve(), "a.png")
    assert not shots.exists(root.resolve(), "big.png.png") and shots.read(root.resolve(), "big.png.png") is None
    got = look_shots(["docs/mockups/reviews/a.png", "docs/mockups/reviews/big.png.png"], "proj")
    assert got == [{"name": "a.png", "url": "/repo/proj/shot/a.png"}, {"name": "big.png.png", "url": ""}]


@pytest.mark.integration
def test_the_shot_route_serves_origins_png_and_nothing_else(tmp_path, monkeypatch):
    checkout(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp
    from sessionorc.client import AgentUnavailable

    class Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise AgentUnavailable("down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(uiapp, "LocalClient", Down)
    with TestClient(uiapp.create_app()) as c:
        r = c.get("/repo/proj/shot/a.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG + b"a.png"
        for bad in ("/repo/proj/shot/local.png", "/repo/proj/shot/a.txt", "/repo/other/shot/a.png"):
            assert c.get(bad).status_code == 404, bad
        assert c.get("/repo/proj/shot/..%2F..%2F..%2FREADME.png").status_code == 404


@pytest.mark.integration
def test_a_look_from_a_sender_on_another_host_is_addressed_and_served_through_the_home(tmp_path, monkeypatch):
    """TD-300: a sender whose record runs on another host names a repo that host registers. The row
    asks that host (`host_shot` with `head`, kept `SHOT_TTL`) and addresses the image with `?host=`;
    the route reads it there, through the home; this host's registry is not consulted."""
    import base64

    checkout(tmp_path, monkeypatch)
    from agentorc.ui import app as uiapp
    from agentorc.ui import inbox

    asked: list[tuple[str, str, str, bool]] = []

    def call_sync(method, **kw):
        assert method == "host_shot" and kw["_timeout"] == inbox.SHOT_WAIT, "a hung node holds the Inbox SHOT_WAIT"
        asked.append((kw["host"], kw["repo"], kw["name"], kw.get("head", False)))
        if kw["host"] == "hung":
            raise TimeoutError("no answer")
        return {"exists": kw["name"] == "n.png", "png": ""}

    monkeypatch.setattr(inbox, "call_sync", call_sync)
    got = inbox.look_shots(["docs/mockups/reviews/n.png", "docs/mockups/reviews/a.png"], "proj", "laptop")
    assert got == [
        {"name": "n.png", "url": "/repo/proj/shot/n.png?host=laptop"},
        {"name": "a.png", "url": ""},  # this host holds a.png, but the sender's host does not
    ]
    inbox.look_shots(["docs/mockups/reviews/n.png"], "proj", "laptop")
    assert asked == [("laptop", "proj", "n.png", True), ("laptop", "proj", "a.png", True)], "kept SHOT_TTL"
    # a host that does not answer is asked once, not once per shot, and not again for SHOT_TTL
    asked.clear()
    two = ["docs/mockups/reviews/n.png", "docs/mockups/reviews/m.png"]
    assert [x["url"] for x in inbox.look_shots(two, "proj", "hung")] == ["", ""]
    assert [x["url"] for x in inbox.look_shots(two, "proj", "hung")] == ["", ""]
    assert asked == [("hung", "proj", "n.png", True)]
    # the page's own host is this host: read here, never asked
    assert inbox.look_shots(["docs/mockups/reviews/a.png"], "proj", "kmaster")[0]["url"] == "/repo/proj/shot/a.png"

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            assert method == "host_shot" and kw["host"] == "laptop"
            png = base64.b64encode(PNG + b"node").decode() if kw["name"] == "n.png" else ""
            return {"exists": bool(png), "png": png}

    monkeypatch.setattr(uiapp, "LocalClient", Client)
    with TestClient(uiapp.create_app()) as c:
        r = c.get("/repo/proj/shot/n.png?host=laptop")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG + b"node"
        assert c.get("/repo/proj/shot/a.png?host=laptop").status_code == 404, "the node's origin does not hold it"
        assert c.get("/repo/proj/shot/a.txt?host=laptop").status_code == 404
        assert c.get("/repo/proj/shot/a.png?host=kmaster").content == PNG + b"a.png", "this host: read here"
# -- §4.5a **Send to reviewer** (§4.10 *A look*, TD-292 slice 4b) --------------------------------


@pytest.mark.unit
def test_look_review_draws_the_button_only_on_an_open_ask_look_with_a_seat():
    from agentorc.ui.inbox import look_review

    e = look(team="ao-grind")
    look_review(e, "ao-proj-techlead-ao-1", "techlead-ao-1", {})
    assert (e["review_seat"], e["review_name"]) == ("ao-proj-techlead-ao-1", "techlead-ao-1")
    for other in (
        look(team="ao-grind", closed_reason="answered"),  # answered: nothing left to send
        look("steer", team="ao-grind"),  # a steer lapses to its default
        entry("m-2", "ask", team="ao-grind"),  # no shots: not a look
    ):
        look_review(other, "ao-proj-techlead-ao-1", "techlead-ao-1", {})
        assert "review_seat" not in other
    seatless = look(team="solo")
    look_review(seatless, "", "", {})
    assert "review_seat" not in seatless
    html = rows("needs", [e])
    assert 'data-act="hand_look" data-id="person" data-msg="m-1" data-team="ao-grind"' in html
    assert ">Send to reviewer</button>" in html
    assert "Send to reviewer" not in rows("needs", [seatless])


@pytest.mark.unit
def test_a_look_with_a_reviewer_is_listed_with_the_snoozed_as_with_the_seat_since():
    """`snoozed_for` sets it aside until the handed `ask` closes — in no count — and the row names
    the seat holding it, from the person's read's `handed` list, and when it went."""
    from agentorc.ui.inbox import inbox_sections, look_review

    e = look(team="ao-grind", snoozed_for="m-9")
    handed = {
        "m-9": {
            "id": "m-9",
            "holder": "ao-proj-techlead-ao-1",
            "holder_name": "techlead-ao-1",
            "at": "2026-09-19T10:00:00Z",
        }
    }
    look_review(e, "ao-proj-techlead-ao-1", "techlead-ao-1", handed)
    assert "review_seat" not in e and e["with_seat"] == "techlead-ao-1" and e["with_since"]
    got = inbox_sections([e])
    assert [x["id"] for x in got["snoozed"]] == ["m-1"] and got["count"] == 0 and not got["needs"]
    html = rows("snoozed", [e])
    assert "with techlead-ao-1 since" in html and "snoozed until" not in html
    assert 'data-act="unsnooze"' in html  # Unsnooze brings it back sooner
    gone = look(snoozed_for="m-8")  # the read no longer lists the handed entry
    look_review(gone, "", "", {})
    assert gone["with_seat"] == "a reviewer" and gone["with_since"] == ""
    # once the debt closed the home clears `snoozed_for`: the look is a question again
    assert inbox_sections([look()])["count"] == 1


@pytest.mark.unit
def test_the_seats_reading_is_drawn_above_the_answers_and_its_handed_ask_reads_as_a_look():
    e = look(answers=["Works", "Not right: <what>"], looked_by={"seat": "techlead-ao-1", "text": "matches §4.5a"})
    html = rows("needs", [e])
    assert "techlead-ao-1 read it: matches §4.5a" in html
    named = look(looked_by={"seat": "ao-proj-techlead-ao-1", "name": "techlead-ao-1", "text": "matches"})
    assert "techlead-ao-1 read it: matches" in rows("needs", [named]) and "ao-proj-" not in rows("needs", [named])
    assert html.index("read it: matches") < html.index('class="row gap wrap sugg"')
    from datetime import UTC, datetime

    from agentorc.ui.inbox import handed_rows

    copy = {**look(), "id": "m-9", "from": "person", "look": "m-1", "holder": "ao-t", "holder_name": "techlead-ao-1"}
    h = handed_rows([{**copy, "owes": True}], {}, datetime.now(UTC))
    html = rows("waiting", h)
    assert ">look</span>" in html and "waiting on techlead-ao-1 to read the look" in html


@pytest.mark.unit
def test_send_to_reviewer_passes_the_senders_teams_techlead_seat(tmp_path, monkeypatch):
    """The press posts the look and its team; the page reads that team's techlead seat from the
    definitions (the host agent reads no `org.yml`) and calls `inbox_hand` with it — "" where the
    team has none, so the agent's refusal is the toast."""
    from types import SimpleNamespace

    from agentorc.ui import app as uiapp

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    org = SimpleNamespace(
        teams={"ao-grind": SimpleNamespace(host=None, techlead=SimpleNamespace(name="techlead-ao-1"))}
    )
    monkeypatch.setattr(uiapp, "org_here", lambda: (org, []))
    monkeypatch.setattr(uiapp.teams, "seat_id", lambda o, t, host, here: "ao-proj-techlead-ao-1" if t.techlead else "")
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            return {"id": "person", "msg": params.get("msg"), "handed": "m-9", "to": params.get("seat")}

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        got = c.post("/api/person/hand_look", json={"msg": "m-1", "team": "ao-grind"})
        assert got.status_code == 200 and got.json()["to"] == "ao-proj-techlead-ao-1"
        assert ("inbox_hand", {"msg": "m-1", "seat": "ao-proj-techlead-ao-1"}) in calls
        calls.clear()
        c.post("/api/person/hand_look", json={"msg": "m-1", "team": "no-such-team"})
        assert ("inbox_hand", {"msg": "m-1", "seat": ""}) in calls
        assert c.post("/api/person/hand_look", json={"team": "ao-grind"}).status_code == 400
