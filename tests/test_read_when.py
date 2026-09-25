"""*When it is read* (design §4.10 *When it is read: the sentence the sender sees*, TD-158, built by
TD-168): `mail.read_when` case by case, the pair on the record's view, the `msg` reply's
`read_when`, `ao msg`'s printed line, and the composer's line under node."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from sessionorc import mail
from sessionorc.client import LocalClient
from sessionorc.models import Pending, Session

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
UI = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"


def rec(state="idle", **kw):
    kw.setdefault("confidence", "hook")
    kw.setdefault("unattended", True)
    return Session(id="ao-w", name="w", kind="interactive", adapter="claude-code", dir="/w", state=state, **kw)


@pytest.mark.unit
def test_each_case_of_the_table_in_the_doorbells_order():
    """The table's rows, first match wins — so a person's session is never *rung*, a seat's sentence
    beats *exited*, a stop beats a cap, and the idle rows split on whether a hook said so."""
    rw = mail.read_when
    person = rec("idle", unattended=False)
    assert rw(person, "note", NOW).startswith("lands in its inbox and wakes nothing")
    seat = rec("exited", seat={"trigger": "asks"})
    assert rw(seat, "ask", NOW).startswith("fills this seat")
    assert rw(seat, "note", NOW).startswith("waits in the seat's mailbox") and rw(seat, "reply", NOW) == rw(
        seat, "note", NOW
    )
    assert rw(None, "ask", NOW, seat=True).startswith("fills this seat")  # the placeholder card
    for st in ("exited", "closed"):
        assert rw(rec(st), "note", NOW) == "read when this session is resumed, or started again under this name"
    assert rw(rec("unreachable"), "note", NOW).startswith("lands at the home; its host cannot be reached")
    assert rw(rec("idle"), "note", NOW, unreachable=True).startswith("lands at the home")
    assert "it is wrapping up" in rw(rec("idle", wrapup_sent_at="2026-09-25T11:00:00Z"), "note", NOW)
    assert "it is wrapping up" in rw(rec("working", run_until="2026-09-25T11:00:00Z"), "note", NOW)
    assert "it is wrapping up" not in rw(rec("working", run_until="2026-09-25T13:00:00Z"), "note", NOW)
    # the gate before the cap
    assert "paused for usage" in rw(rec("limited", gated={"profile": "p"}), "note", NOW)
    capped = rec("limited", pending=Pending(kind="limit", text="week cap · resets 14:00Z"))
    assert rw(capped, "note", NOW) == (
        "lands and waits: its account is capped (week cap · resets 14:00Z), and it is rung after"
    )
    assert rw(rec("needs-you", pending=Pending(kind="question", text="?")), "note", NOW) == (
        "read once its question is answered and its turn ends"
    )
    asked = rec("needs-you", pending=Pending(kind="permission", text="x"))
    assert "its permission is answered" in rw(asked, "note", NOW)
    for st in ("working", "stalled?"):
        assert rw(rec(st), "note", NOW) == "read when its turn ends: it is rung on the tick after its Stop"
    assert rw(rec("idle"), "note", NOW).startswith("rung within a tick")
    assert rw(rec("idle", confidence="scraped"), "note", NOW).startswith("lands; its idle is a guess from the screen")


@pytest.mark.unit
def test_an_ask_names_its_bound_and_only_a_sessions_sender_hears_of_a_spent_budget():
    rw = mail.read_when
    assert rw(rec("working"), "ask", NOW).endswith(" — an ask takes the default bound of 24 h")
    assert rw(rec("working"), "ask", NOW, bound=timedelta(minutes=30)).endswith("default bound of 30 min")
    assert "bound" not in rw(rec("working"), "note", NOW)
    spent = rec("idle", wakes=[{"at": (NOW - timedelta(minutes=5)).isoformat(), "charged": True}] * mail.WAKE_BUDGET)
    assert rw(spent, "note", NOW).startswith("rung within a tick")  # a person's message refills it
    assert rw(spent, "note", NOW, person=False) == (
        "lands without waking it: its wake budget is spent, read on its next look"
    )
    # the budget gates the ring alone: busy, it reads its mail at its Stop (review of PR #583)
    busy = rec("working", wakes=spent.wakes)
    assert rw(busy, "note", NOW, person=False).startswith("read when its turn ends")
    # no composer, no ring: nothing is promised to be typed (`_bell_blocked`)
    assert rw(rec("idle"), "note", NOW, rings=False).startswith("lands; nothing is typed into this tool's pane")


@pytest.mark.integration
async def test_the_msg_reply_and_the_records_view_carry_the_sentence(agent, tmp_path):
    """`msg` answers `read_when` per addressee after delivery; every record's view carries the pair
    the composer opens with, so opening it asks nothing."""
    async with LocalClient() as person:
        w = (
            await person.call(
                "create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], unattended=True
            )
        )["id"]
        await person.call("kill", id=w)
        from conftest import wait_state

        await wait_state(person, w, "exited")
        got = await person.call("msg", to=w, text="are you there?", kind="ask")
        assert got["read_when"][w].startswith("read when this session is resumed, or started again under this name")
        assert got["read_when"][w].endswith("default bound of 24 h")
        view = await person.call("get", id=w)
        assert view["read_when"]["note"] == "read when this session is resumed, or started again under this name"
        await person.call("remove", id=w)


@pytest.mark.unit
def test_ao_msg_ends_with_when_each_addressee_reads_it(monkeypatch, capsys):
    from agentorc import cli

    reply = {
        "entry": {"id": "m-1", "kind": "note", "text": "x"},
        "delivered": ["ao-w"],
        "read_when": {"ao-w": "read when this session is resumed, or started again under this name"},
    }
    monkeypatch.setattr(cli, "call_sync", lambda method, **_: reply)
    monkeypatch.setenv("AGENTORC_SESSION", "ao-me")
    assert cli.main(["msg", "ao-w", "x"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "ao-w: read when this session is resumed, or started again under this name"


@pytest.mark.unit
def test_the_card_and_focus_hand_the_pair_to_the_composer_and_a_seat_reads_as_one(tmp_path, monkeypatch):
    """The view's pair rides on the Message buttons; a seat the definition names takes the seat's
    sentences whatever the record says, and an older agent's record without a pair draws no line."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-w", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": "/w", "state": "exited",
        "pane": True, "tail": [], "since": "2026-09-25T10:00:00Z", "confidence": "hook",
        "read_when": {"ask": "read when resumed — ask", "note": "read when resumed"},
    }  # fmt: skip
    v = view(s, [s])
    assert v["read_when"] == {"ask": "read when resumed — ask", "note": "read when resumed"}
    html = templates.get_template("card.html").render(s=v)
    assert 'data-when-ask="read when resumed — ask" data-when-note="read when resumed"' in html
    seat = view(s, [s], seats={"ao-w": "comes on the next question"})
    assert seat["read_when"]["ask"].startswith("fills this seat") and seat["read_when"]["note"].startswith("waits")
    old = view({k: x for k, x in s.items() if k != "read_when"}, [s])
    assert old["read_when"] == {"ask": "", "note": ""}


WHEN_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = (o) => Object.assign({
  dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false,
}, o);
const document = { documentElement: el(), body: el(), activeElement: null,
  querySelector: () => null, querySelectorAll: () => [], addEventListener: noop, createElement: () => el() };
const window = {};
global.window = window; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const w = window.AO.whenLine, pair = { ask: "fills this seat", note: "waits in the seat's mailbox" };
console.log(JSON.stringify({
  ask: w(pair, "ask", false), note: w(pair, "note", false), reply: w(pair, "ask", true),
  none: w(null, "ask", false), empty: w({ ask: "", note: "" }, "note", false),
}));
"""


@pytest.mark.unit
def test_the_composers_line_swaps_with_the_kind_and_a_reply_reads_as_a_note():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the composer's rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "when_probe.js"
    probe.write_text(WHEN_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["ask"] == "When it is read: fills this seat."
    assert got["note"] == got["reply"] == "When it is read: waits in the seat's mailbox."
    assert got["none"] == "" and got["empty"] == ""
    js = (UI / "static" / "app.js").read_text()
    assert '$("#mailkind").onchange = show' in js and "when: { ask: b.dataset.whenAsk" in js
    assert 'id="mailwhen"' in (UI / "templates" / "base.html").read_text()
