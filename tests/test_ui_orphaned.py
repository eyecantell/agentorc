"""The orphaned question's row (design §4.5a *Inbox row: orphaned question*, §4.10 *A question about a
reference outlives its asker*; TD-216 slice 2): the standing — where an answer goes, said before the
press — the section and the count, the row's controls, the reply route's result, and `ao inbox`.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from test_ui_inbox import entry, iso, rows

from sessionorc import mail

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
ORPHANED = {"at": "2026-09-28T10:00:00Z", "how": "closed", "ref": "TD-149", "name": "grinder-ao-2",
            "repo": "/home/k/agentorc", "host": "kmaster", "team": "ao-grind"}  # fmt: skip


def rec(sid, name, state="working", *progress):
    return {"id": sid, "name": name, "state": state, "progress": list(progress)}


def claim(ref, at=NOW - timedelta(hours=1), status="claimed", source="declared"):
    return {"ref": ref, "status": status, "source": source, "at": iso(at)}


def orphan(mid="m-1", kind="ask", **kw):
    return entry(mid, kind, about="TD-149", orphaned={**ORPHANED, **kw.pop("orphaned", {})}, **kw)


# -- the standing (§4.5a) -------------------------------------------------------------------------


@pytest.mark.unit
def test_the_standing_names_the_holder_or_says_nobody_holds_it():
    """A live record with an unexpired declared lease on the reference is who the answer reaches;
    an expired lease, a derived claim, a `done` and a record that is not live hold nothing."""
    e = orphan()
    fleet = [
        rec("ao-a", "grinder-ao-1", "working", claim("TD-149")),
        rec("ao-b", "old", "working", claim("TD-149", at=NOW - timedelta(hours=13))),
        rec("ao-c", "derived", "working", claim("TD-149", source="derived")),
        rec("ao-d", "gone", "exited", claim("TD-149")),
        rec("ao-e", "finished", "idle", claim("TD-149", status="done")),
    ]
    st = mail.orphan_standing(e, fleet, NOW)
    assert st["holders"] == [{"id": "ao-a", "name": "grinder-ao-1"}]
    assert (
        st["text"] == "its session was closed — grinder-ao-1 holds TD-149: your answer reaches it, and agentorc's board"
    )
    none = mail.orphan_standing(orphan(orphaned={"how": "forgotten"}), fleet[1:], NOW)
    assert none["holders"] == [] and none["text"] == (
        "its session was forgotten — nobody holds TD-149: your answer is written on agentorc's board"
    )
    two = mail.orphan_standing(e, [fleet[0], rec("ao-f", "grinder-ao-3", "idle", claim("TD-149"))], NOW)
    assert "grinder-ao-1, grinder-ao-3 hold TD-149: your answer reaches them" in two["text"]
    assert mail.orphan_standing(entry("m-2", "ask", about="TD-149"), fleet, NOW) is None  # not orphaned


# -- the section and the count (§4.10 *An orphaned steer does not lapse*) ---------------------------


@pytest.mark.unit
def test_an_orphaned_steer_is_steering_while_its_clock_runs_and_counted_once_it_is_cleared():
    from agentorc.ui.app import inbox_sections

    running = orphan("m-1", "steer", default="drop it", bound=iso(NOW + timedelta(minutes=21)))
    cleared = orphan("m-2", "steer", default="drop it", bound=None)
    ask = orphan("m-3", "ask")
    plain = entry("m-4", "steer", default="x", bound=None)  # not orphaned: no bound is still Steering
    got = inbox_sections([running, cleared, ask, plain], now=NOW)
    assert [e["id"] for e in got["steering"]] == ["m-1", "m-4"]
    assert sorted(e["id"] for e in got["needs"]) == ["m-2", "m-3"] and got["count"] == 2


# -- the row (§4.5a) ------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_orphaned_row_draws_the_standing_and_offers_no_pause_and_no_open():
    std = {"text": "its session was closed — nobody holds TD-149: your answer is written on agentorc's board"}
    ask = orphan("m-1", "ask", from_open="", from_name="grinder-ao-2", standing=std, answers=["this", "that"])
    html = rows("needs", [ask])
    assert "nobody holds TD-149: your answer is written on agentorc&#39;s board" in html
    assert 'data-act="reply"' in html and 'data-act="answer"' in html and 'data-act="unmail"' in html
    assert 'data-act="snooze"' in html  # an `ask` has no clock
    assert 'data-act="pause"' not in html and 'href="/focus/' not in html and 'data-act="gowithit"' not in html
    assert 'class="st rowerr"' in html  # where a refused write is drawn

    running = orphan("m-2", "steer", default="drop it", bound=iso(NOW + timedelta(minutes=21)), left="21m 0s",
                     from_open="", standing=std)  # fmt: skip
    html = rows("steering", [running])
    assert "its asker would have gone with: drop it" in html
    assert "21m 0s left, then it waits on you" in html and 'data-then="then it waits on you"' in html
    assert 'data-act="gowithit"' in html and 'data-act="pause"' not in html
    assert 'data-act="snooze"' not in html  # Snooze once the clock is gone

    cleared = orphan("m-3", "steer", default="drop it", bound=None, from_open="", standing=std)
    html = rows("needs", [cleared])
    assert 'data-act="snooze"' in html and 'data-act="gowithit"' in html and "timeleft" not in html


@pytest.mark.unit
def test_only_a_failed_write_is_drawn_as_not_written():
    """TD-234: *not written* is the refused board line (§4.5a), so only Reply, a suggested answer and
    Go with it draw it on the row; a failed Snooze or Delete wrote nothing and keeps only its toast.
    Read from the source: the row's error is gated by the three presses that write."""
    js = (pathlib.Path(__file__).parents[1] / "src/agentorc/ui/static/app.js").read_text()
    catch = js[js.index("const named = { identity_log:") : js.index("not written: ${e.message}")]
    assert 'const writes = ["reply", "answer", "gowithit"].includes(action);' in catch
    assert "const rowerr = writes && " in catch
    for press in ("snooze", "unmail", "pause"):
        assert f'"{press}"' not in catch.split("const writes = ")[1].split(";")[0]


# -- the reply route and `ao inbox`, end to end ---------------------------------------------------


def _git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_the_inbox_row_and_the_reply_route_for_an_orphaned_question(subprocess_agent, tmp_path, capsys):
    """The page's person read carries the standing under the asker's name with nothing to open; a
    Reply through the page's route writes the board and answers the toast's sentence — no reply
    entry, so the route does not look for one — and `ao inbox` prints the standing beside it."""
    from fastapi.testclient import TestClient

    from agentorc import cli
    from agentorc.ui.app import create_app
    from sessionorc.client import call_sync

    repo = tmp_path / "agentorc"
    (repo / "docs").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "docs" / "user_attention.md").write_text(
        "# User attention\n\n## Needs the user\n\n- [ ] x. Due: 2026-09-22.\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    home = pathlib.Path(os.environ["AGENTORC_HOME"])
    (home / "repos.txt").write_text(f"{repo}\n")
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")

    w = call_sync("create", name="asker", dir=str(repo), adapter="shell", argv=["bash", "--norc"])["id"]
    q = call_sync("msg", caller=w, to="person", text="Which fetcher first?", about="TD-149", kind="ask")["entry"]["id"]
    call_sync("close", id=w)
    try:
        with TestClient(create_app()) as c:
            e = next(e for e in c.get("/api/person/inbox").json()["entries"] if e["id"] == q)
            assert e["from_name"] == "asker" and e["from_open"] == ""
            assert e["standing"]["text"] == (
                "its session was closed — nobody holds TD-149: your answer is written on agentorc's board"
            )
            assert "your answer is written on agentorc&#39;s board" in c.get("/inbox").text

            assert cli.main(["inbox"]) == 0
            out = capsys.readouterr().out
            assert "  its session was closed — nobody holds TD-149" in out

            r = c.post("/api/person/reply", json={"reply_to": q, "text": "the DIU one"})
            assert r.status_code == 200, r.text
            got = r.json()
            assert got["note"].startswith("written on the board") and got["board"] and got["closed"] == q
        assert ": the DIU one. Context: TD-149." in (repo / "docs" / "user_attention.md").read_text()
        # `ao msg --reply-to` by a person takes the same road, and says where the answer went
        w = call_sync("create", name="asker", dir=str(repo), adapter="shell", argv=["bash", "--norc"])["id"]
        q2 = call_sync("msg", caller=w, to="person", text="Drop it?", about="#702", kind="ask")["entry"]["id"]
        call_sync("close", id=w)
        assert cli.main(["msg", "--reply-to", q2, "drop it"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("written on the board\ncommitted ")
    finally:
        (home / "hosts.yml").unlink()
