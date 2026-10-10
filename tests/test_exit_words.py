"""The exited pill's cause (design §4.5 row 5 (b), §4.5a **state pill hover**, §4.7 `ao status -v`; TD-490,
built by TD-498 slice 2): `ending.exit_words` for each `ended` shape, the pill's one hover text in §4.5a's
order, the card, the Inbox row and the Focus header drawing it, and `ao status -v`'s line."""

from __future__ import annotations

import datetime
import re

import pytest

from agentorc import cli
from agentorc.ending import clock, ending_hover, exit_words

UTC = datetime.UTC
NOW = datetime.datetime(2026, 10, 9, 18, 0, tzinfo=UTC)


def _at(hours: float = 0, minutes: float = 0) -> str:
    return (NOW - datetime.timedelta(hours=hours, minutes=minutes)).isoformat().replace("+00:00", "Z")


def _exited(**ended):
    return {"id": "ao-r-w-1", "name": "w", "state": "exited", "ended": {"at": _at(minutes=30), **ended}}


@pytest.mark.unit
def test_each_ended_shape_has_its_words():
    """§4.5 row 5 (b): the tool's end with a code or a reason, a dead pane, a kill by a person, by a
    session (by its name), by itself and by the tick, a gone pane found now, and one found on the
    agent's first tick after a start, with when nobody was looking since."""
    names = {"ao-r-manager-1": "manager-1"}
    found, down = _at(minutes=10), _at(hours=4)
    cases = [
        (_exited(how="tool", reason="prompt_input_exit"), "exited · the composer closed"),
        (_exited(how="tool", reason="logout"), "exited · logout"),
        (_exited(how="tool", reason="other", code=2), "exited · code 2"),
        (_exited(how="tool"), "exited"),
        (_exited(how="pane", code=1), "exited · code 1"),
        (_exited(how="pane", code=0), "exited · code 0"),
        (_exited(how="kill", by="person"), "killed by you"),
        (_exited(how="kill", by="ao-r-manager-1"), "killed by manager-1"),
        (_exited(how="kill", by="ao-r-other-9"), "killed by ao-r-other-9"),
        (_exited(how="kill", by="ao-r-w-1"), "killed itself"),
        (_exited(how="kill", by="tick", why="stop time"), "killed by the tick · stop time"),
        (_exited(how="gone", found=found), f"pane gone · found {clock(found, NOW)}"),
        (
            _exited(how="gone", found=found, down_since=down),
            f"pane gone · found {clock(found, NOW)} by a host agent down since {clock(down, NOW)}",
        ),
    ]
    for rec, words in cases:
        assert exit_words(rec, names, NOW) == words, rec["ended"]


@pytest.mark.unit
def test_an_older_record_and_a_malformed_one_still_read():
    """A record with no `ended` (older than the field, a node older than it) reads *exited*, with its
    `exit_code` where it has one; a malformed `ended` never raises."""
    assert exit_words({"state": "exited", "exit_code": 3}, None, NOW) == "exited · code 3"
    assert exit_words({"state": "exited"}, None, NOW) == "exited"
    assert exit_words({"state": "exited", "ended": "pane"}, None, NOW) == "exited"
    assert exit_words({"state": "exited", "ended": {"how": "pane", "code": "1"}}, None, NOW) == "exited"
    assert exit_words({"state": "exited", "ended": {"how": "gone", "found": "soon"}}, None, NOW) == "pane gone"


@pytest.mark.unit
def test_a_wrap_up_before_the_exit_is_said():
    """*· after wrap-up* when the record's `wrapup_at` precedes the exit, and not when it came after."""
    rec = {**_exited(how="pane", code=0), "wrapup_at": _at(hours=1)}
    assert exit_words(rec, None, NOW) == "exited · code 0 · after wrap-up"
    rec = {**_exited(how="kill", by="person"), "wrapup_at": _at(minutes=5)}
    assert exit_words(rec, None, NOW) == "killed by you"


@pytest.mark.unit
def test_the_hover_is_the_words_and_the_time():
    """§4.5a **state pill hover**: on `exited` and `closed`, row 5 (b)'s words and the time; a gone
    pane's words carry their own; any other state has no ending."""
    kill = _exited(how="kill", by="person")
    assert ending_hover(kill, None, NOW) == f"killed by you · {clock(kill['ended']['at'], NOW)}"
    found = _at(minutes=10)
    gone = _exited(how="gone", found=found)
    assert ending_hover(gone, None, NOW) == f"pane gone · found {clock(found, NOW)}"
    closed = {"id": "x", "state": "closed", "closer": {"by": "tick", "why": "finished"}, "closed_at": _at(minutes=3)}
    assert ending_hover(closed, None, NOW) == f"closed by the tick · team finished · {clock(_at(minutes=3), NOW)}"
    assert ending_hover({"state": "idle"}, None, NOW) == ""


def _view(rec, fleet=None, **kw):
    from agentorc.ui.app import view

    base = {"kind": "agent", "adapter": "claude-code", "dir": "/tmp/x", "since": _at(minutes=30), "pane": False,
            "tail": [], "created": _at(hours=2), "confidence": "tick"}  # fmt: skip
    return view({**base, **rec}, fleet, **kw)


@pytest.mark.unit
def test_the_pill_hover_takes_the_first_that_applies(tmp_path, monkeypatch):
    """§4.5a's order: an unreachable host's reason, a *waiting* pill's reason, a seat on call, then the
    ending on an exited record (never *guessed*: it is the tick's own reading), *guessed from the
    screen* on a scraped state, *reported by the tool* on a hook one, and a `stalled?` the tick
    observed said as observed."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    kill = {**_exited(how="kill", by="ao-r-manager-1"), "id": "ao-r-w-1"}
    v = _view(kill, [{"id": "ao-r-manager-1", "name": "manager-1"}])
    assert v["pill_title"].startswith("killed by manager-1 · ") and v["exit_text"] == "killed by manager-1"
    assert v["slot"]["text"] == "killed by manager-1" and v["slot"]["full"] == v["pill_title"]
    gone = _view({**kill, "ended": {"how": "gone", "at": _at(minutes=10), "found": _at(minutes=10)}})
    assert gone["pill_title"].startswith("pane gone · found ") and not gone["scraped"]
    reach = _view(
        {**kill, "host_link": {"supervisor": {"doing": "restarting the node"}}},
        [{"id": "ao-r-manager-1", "name": "manager-1"}],
    )
    assert reach["pill_title"] == "restarting the node"
    # the end banner's first line is the ending whatever outranks it on the pill (review of #1415)
    assert reach["ending_text"] == v["pill_title"]
    idle = {"id": "ao-r-i-1", "name": "i", "state": "idle"}
    assert _view({**idle, "confidence": "scraped"})["pill_title"] == "guessed from the screen"
    assert _view({**idle, "confidence": "hook"})["pill_title"] == "reported by the tool"
    stalled = _view({**idle, "state": "stalled?", "confidence": "tick"})
    assert stalled["pill_title"] == "observed by the host agent, not reported by the tool"
    # a *waiting* pill says its reason, and a seat on call is read from its record — before the ending
    from agentorc.ui.cards import pill_title

    waiting = {"state_class": "waiting", "wait_reason": "waiting · review #1302", "confidence": "hook"}
    assert pill_title(waiting) == "waiting · review #1302 — idle in every payload, on someone else’s move"
    seat = "a seat on call: read from its record, not from a screen"
    assert pill_title({"seat": True, "confidence": "hook"}) == seat
    assert pill_title({"seat": True, "state": "exited"}, "killed by you · 12:31") == seat
    assert pill_title({"host_note": "restarting the node", "seat": True}) == "restarting the node"
    # TD-515: two inputs at once — a host note outranks a waiting reason, and a waiting reason a seat
    # and the ending
    reason = pill_title(waiting)
    assert pill_title({**waiting, "host_note": "restarting the node"}) == "restarting the node"
    assert pill_title({**waiting, "seat": True}) == reason
    assert pill_title({**waiting, "state": "exited"}, "killed by you · 12:31") == reason


@pytest.mark.unit
def test_an_ending_before_today_says_its_day():
    """*Thu 12:31* once the instant is not today, so an ending from two days back never reads as today's."""
    then = NOW - datetime.timedelta(days=2)
    assert clock(then.isoformat(), NOW) == then.astimezone().strftime("%a %H:%M")
    assert ending_hover(_exited(how="kill", at=then.isoformat()), None, NOW).endswith(
        f" · {then.astimezone():%a %H:%M}"
    )


@pytest.mark.unit
def test_the_card_and_the_inbox_row_draw_the_one_text(tmp_path, monkeypatch):
    """The card's pill (compact and full) and the Inbox row's carry `pill_title`, so all three say
    the same; the Focus header draws it from the same view in the script (`v.pill_title`)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates
    from agentorc.ui.inbox import state_rows

    v = _view({**_exited(how="kill", by="person"), "id": "ao-r-w-1"})
    title = v["pill_title"]
    assert title.startswith("killed by you · ")
    html = templates.get_template("card.html").render(s=v)
    assert f'title="{title}"' in html
    rows = state_rows([{**v, "state": "needs-you", "pending": {"kind": "question", "text": "?"}}])
    assert rows and all(r["pill_title"] == title for r in rows)


@pytest.mark.unit
def test_the_focus_script_draws_the_hover_and_the_banners_first_line():
    """The Focus header's pill takes `v.pill_title` as its title, and the exited banner's first line
    is the same words (§4.5a **state pill hover**: *the Focus terminal's end overlay draws the same
    ending words as its first line*)."""
    from pathlib import Path

    js = (Path(cli.__file__).parent / "ui" / "static" / "app.js").read_text()
    assert 'title="${esc(v.pill_title || "")}"><span class="dot">' in js
    assert '<div class="endwords"><b>${esc(v.ending_text)}</b></div>' in js


@pytest.mark.unit
def test_status_v_says_how_an_exited_record_ended(monkeypatch, capsys):
    """design §4.7: `ao status -v` prints an exited record's ending in the card's words and its age,
    and `ao status` marks `~` a scraped state alone, never a `tick` one."""
    at = (datetime.datetime.now(UTC) - datetime.timedelta(minutes=5)).isoformat()

    def rec(sid, name, **kw):
        return {"id": sid, "name": name, "state": "exited", "confidence": "tick", "since": at, "adapter": "shell", **kw}

    records = [
        rec("ao-r-manager-1", "manager-1", state="idle", confidence="scraped"),
        rec("ao-r-grinder-1", "grinder-1", ended={"how": "kill", "by": "ao-r-manager-1", "at": at}),
        rec("ao-r-grinder-2", "grinder-2", ended={"how": "pane", "code": 1, "at": at}),
        rec("ao-r-old", "old", exit_code=4),
    ]
    monkeypatch.setattr(cli, "call_sync", lambda method, **_: records if method == "list" else {})
    assert cli.main(["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^\s+killed by manager-1 5m ago$", out, re.M)
    assert re.search(r"^\s+exited · code 1 5m ago$", out, re.M)
    assert re.search(r"^\s+exited · code 4$", out, re.M)  # no `ended`: no age to give
    assert re.search(r"^ao-r-manager-1\s+idle\s+~", out, re.M)
    assert not re.search(r"^ao-r-grinder-1\s+exited\s+~", out, re.M)
