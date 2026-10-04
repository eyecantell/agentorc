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
