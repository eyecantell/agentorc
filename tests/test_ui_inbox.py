"""The Inbox page (design §4.5 screen 6, §4.5a **Inbox page** and the three **Inbox row** rows,
§4.10; TD-069 step 1): the sections and their order, the count that means *what is waiting on a
person*, the row controls and the routes behind them, the team filter's data, and what the page
must never do to a message a session wrote — parse it, or mark it read by looking at it.

The pure half (`inbox_sections` and the templates) is unit; the routes run against a live host
agent through the sync `TestClient`, as the rest of the UI's tests do.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

UI = pathlib.Path(__file__).parents[1] / "src" / "agentorc" / "ui"


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def entry(mid, kind="note", **kw):
    """A person-inbox entry as `/api/person/inbox` hands it to the sections and the templates."""
    e = {
        "id": mid,
        "from": kw.pop("from_", "ao-w1"),
        "from_name": kw.pop("from_name", "w1"),
        "from_open": kw.pop("from_open", "ao-w1"),
        "from_role": "other",
        "to": ["person"],
        "at": kw.pop("at", "2026-09-19T10:00:00Z"),
        "age": "1h 0m",
        "kind": kind,
        "text": kw.pop("text", "a message"),
        "about": None,
        "read_at": None,
        "reply_to": None,
        "bound": None,
        "closed_by": None,
        "closed_at": None,
        "expired_at": None,
        "closed_reason": None,
        "default": None,
        "team": None,
        "snoozed_until": None,
        "paused_at": None,
    }
    e.update(kw)
    return e


# -- the sections (design §4.5 screen 6) ----------------------------------------------------------


@pytest.mark.unit
def test_the_three_sections_their_order_and_what_the_count_is():
    """§4.5 screen 6: **Needs you** is open `ask`s to the person and paused `steer`s, oldest first;
    **Steering** is the `steer`s whose clock runs, soonest first; **FYI** is everything else. The
    count is the first section and nothing else — a running `steer` and a `note` are never in it."""
    from agentorc.ui.app import inbox_sections

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    ask_old = entry("m-1", "ask", at="2026-09-19T08:00:00Z")
    ask_new = entry("m-2", "ask", at="2026-09-19T11:00:00Z")
    paused = entry("m-3", "steer", at="2026-09-19T09:00:00Z", default="off main",
                   bound=iso(now + timedelta(hours=2)), paused_at="2026-09-19T10:00:00Z")  # fmt: skip
    soon = entry("m-4", "steer", default="off main", bound=iso(now + timedelta(minutes=30)))
    later = entry("m-5", "steer", default="squash", bound=iso(now + timedelta(hours=6)))
    note = entry("m-6", "note", at="2026-09-19T07:00:00Z")
    lapsed = entry("m-7", "steer", at="2026-09-19T09:30:00Z", closed_reason="lapsed", closed_at="2026-09-19T11:00:00Z")
    reply = entry("m-8", "reply", at="2026-09-19T11:30:00Z", reply_to="m-9")

    got = inbox_sections([note, later, ask_new, paused, soon, ask_old, lapsed, reply], now=now)
    assert [e["id"] for e in got["needs"]] == ["m-1", "m-3", "m-2"]  # oldest first, the paused steer among them
    assert [e["id"] for e in got["steering"]] == ["m-4", "m-5"]  # soonest first
    assert [e["id"] for e in got["fyi"]] == ["m-8", "m-7", "m-6"]  # newest first
    # the count is **Needs you**: the two asks and the paused steer — never the running ones
    assert got["count"] == 3 and got["snoozed"] == []


@pytest.mark.unit
def test_a_snoozed_entry_is_in_no_section_and_in_no_count_until_its_time():
    """§4.10 *Snooze*: the entry leaves its section and the page's count until that time — and is
    listed under *n snoozed*, because a snooze is never a way to lose mail. A `snoozed_until` that
    has passed, or that cannot be read at all, puts the entry back where it belongs."""
    from agentorc.ui.app import inbox_sections

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    ahead = entry("m-1", "ask", snoozed_until=iso(now + timedelta(hours=1)))
    past = entry("m-2", "ask", snoozed_until=iso(now - timedelta(hours=1)))
    junk = entry("m-3", "ask", snoozed_until="half six")
    got = inbox_sections([ahead, past, junk], now=now)
    assert [e["id"] for e in got["snoozed"]] == ["m-1"]
    assert [e["id"] for e in got["needs"]] == ["m-2", "m-3"] and got["count"] == 2


@pytest.mark.unit
def test_every_closed_shape_lands_in_fyi_and_nothing_is_dropped():
    """§4.5a **Inbox row: `note` and the rest of FYI**: lapsed `steer`s, declined `ask`s and late
    replies are listed until retention prunes them. Every entry is in exactly one section: a page
    that silently drops mail is worse than a full one."""
    from agentorc.ui.app import inbox_sections

    es = [
        entry("m-1", "ask", closed_reason="declined", closed_at="2026-09-19T11:00:00Z"),
        entry("m-2", "ask", closed_reason="asker_gone", closed_at="2026-09-19T11:00:00Z"),
        entry("m-3", "steer", closed_reason="go_with_it", closed_at="2026-09-19T11:00:00Z"),
        entry("m-4", "ask", closed_by="m-9", closed_at="2026-09-19T11:00:00Z"),  # written before closed_reason
        entry("m-5", "ask", expired_at="2026-09-19T11:00:00Z"),
        entry("m-6", "note", from_="system", from_name="system"),
    ]
    got = inbox_sections(es, now=datetime(2026, 9, 19, 12, tzinfo=UTC))
    assert sorted(e["id"] for e in got["fyi"]) == [f"m-{i}" for i in range(1, 7)]
    assert got["count"] == 0 and got["steering"] == []


# -- the rows (design §4.5a) ----------------------------------------------------------------------


def needs_line(html):
    """The rail's *Needs you* line's number (§4.5 screen 6 *The rail*, TD-135): the needs-you pill
    that stood beside the title went with the rail, and this line is the page's own copy of the top
    bar's number while nothing is picked."""
    m = re.search(r'data-group="sec" data-value="needs"[^>]*>.*?<span class="railn">([^<]*)</span>', html, re.S)
    return m.group(1) if m else None


def rows(section, es):
    from agentorc.ui.app import templates

    return templates.get_template("inbox_rows.html").render(rows=es, section=section)


@pytest.mark.unit
def test_a_shaped_message_draws_its_verdict_and_folds_the_reading():
    """§4.5a **Inbox row: details** (§4.10 *How a message to a person is written*, TD-138): the first
    paragraph drawn, the rest under a closed `<details>` whose summary reads *details*, rendered
    from the closed subset; the row's controls sit outside the fold, so a row is answered folded,
    and the find still holds the whole text."""
    text = "merged #517 — one gap left, not a blocker\n\n- read against **§4.2a**\n- the gate is green"
    html = rows("needs", [entry("m-1", "ask", text=text)])
    assert '<div class="body md"><p>merged #517 — one gap left, not a blocker</p></div>' in html
    assert '<details class="fold" data-fold="m-1"><summary>details</summary>' in html
    assert "<ul><li>read against <strong>§4.2a</strong></li><li>the gate is green</li></ul>" in html
    assert '<details class="fold" data-fold="m-1" open' not in html  # closed by default
    fold_at, reply_at = html.index('class="fold"'), html.index('data-act="reply"')
    assert fold_at < html.index("</details>", fold_at) < reply_at  # the controls follow the fold, outside it
    assert 'data-find="w1 ask merged #517' in html and "the gate is green" in html.split('data-find="')[1].split('"')[0]


@pytest.mark.unit
def test_a_short_message_has_no_disclosure_and_an_old_long_one_folds_at_a_sentence():
    """A 200-character text draws whole; an old one-paragraph entry from before the rule folds at
    the last sentence end before `FOLD_CHARS` (§4.5a *Inbox row: details*, the backstop)."""
    short = rows("fyi", [entry("m-1", "note", text="a" * 200)])
    assert 'class="fold"' not in short and "a" * 200 in short
    old = ("A worker said something at length here. " * 40).strip()
    html = rows("fyi", [entry("m-2", "note", text=old)])
    lead = html.split('<div class="body md"><p>')[1].split("</p>")[0]
    assert lead.endswith(".") and len(lead) <= 300 and '<details class="fold" data-fold="m-2">' in html


@pytest.mark.unit
def test_a_message_never_draws_markup_or_a_link_to_the_page_itself():
    """§4.10: raw HTML is its characters, and a link is drawn only when absolute, `http(s)` and not
    the page's own origin — which the row knows from the page it is rendered into."""
    from agentorc.ui.app import templates

    text = "<script>x</script> [Allow](/api/x) [kill](http://ui:8765/api/k) [docs](https://example.com/p)"
    html = templates.get_template("inbox_rows.html").render(
        rows=[entry("m-1", "note", text=text)], section="fyi", origin="http://ui:8765"
    )
    body = html.split('<div class="body md">')[1].split("</div>")[0]
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "[Allow](/api/x)" in body and "[kill](http://ui:8765/api/k)" in body
    assert body.count("<a ") == 1 and '<small class="lhost">example.com</small>' in body


@pytest.mark.unit
def test_answered_for_you_quotes_the_questions_first_paragraph():
    """§4.5a *Inbox row: details*: the *answered for you* row's quotation is the question's first
    paragraph, and the answer below it folds as any row does."""
    e = entry(
        "m-1",
        "reply",
        text="merged #517\n\nthe reading",
        answered={"asker": "ao-w2", "question": "merge #517?\n\nwhy I ask", "source": "design §4.2a"},
    )
    html = rows("answered", [e])
    assert (
        '<div class="body md quoted"><p>merge #517?</p></div>' in html
        and "why I ask" not in html.split("quoted")[1].split("</div>")[0]
    )
    assert '<details class="fold" data-fold="m-1">' in html


@pytest.mark.unit
def test_an_ask_row_carries_reply_delete_and_snooze_and_no_countdown():
    """§4.5a **Inbox row: `ask`**: Reply, Delete (which confirms, and declines), Snooze with its
    three times — and no countdown, because an `ask` to the person does not expire (§4.10)."""
    html = rows("needs", [entry("m-1", "ask", text="merge PR 9?", about="TD-052", team="ao-grind")])
    assert 'data-act="reply" data-id="person" data-msg="m-1"' in html
    assert 'data-act="unmail"' in html and "That declines it: the sender is told" in html
    for when in ("1h", "tomorrow", "pick"):
        assert f'data-act="snooze" data-id="person" data-msg="m-1" data-when="{when}"' in html
    assert "open · no bound" in html and 'class="st timeleft"' not in html
    assert 'href="/focus/ao-w1"' in html and ">Open<" in html  # a row opens the session that needs you
    assert "re TD-052" in html


@pytest.mark.unit
def test_a_steer_row_says_the_default_and_the_time_left_and_offers_no_snooze():
    """§4.5a **Inbox row: `steer`**: the text, the default it will take, the time left (ticking on
    the client), Reply · Go with it · Pause. No Snooze — pause stops the clock, snooze hides a
    running one, and both on one row invite the wrong press (§4.10)."""
    html = rows("steering", [entry("m-2", "steer", default="branch off main", bound="2026-09-20T09:00:00Z")])
    assert "unless you say otherwise: branch off main" in html
    assert 'class="st timeleft" data-deadline="2026-09-20T09:00:00Z"' in html
    assert 'data-act="gowithit"' in html and 'data-act="pause"' in html and 'data-act="reply"' in html
    assert 'data-act="snooze"' not in html and 'data-act="resume"' not in html


@pytest.mark.unit
def test_a_paused_steer_reads_as_paused_and_offers_resume_not_pause():
    """§4.10 *Pause*: the row moves to *Needs you*, the clock is stopped and the sender was told;
    **Resume** gives back the time that was left. Reply and Go with it still close it."""
    e = entry("m-3", "steer", default="off main", bound="2026-09-20T09:00:00Z", paused_at="2026-09-19T11:00:00Z")
    html = rows("needs", [e])
    assert "paused · the clock is stopped" in html and 'data-act="resume" data-id="person" data-msg="m-3"' in html
    assert 'data-act="pause"' not in html and 'class="st timeleft"' not in html
    assert 'data-act="gowithit"' in html and 'data-act="reply"' in html


@pytest.mark.unit
def test_an_fyi_row_dismisses_and_a_system_note_has_no_reply():
    """§4.5a **Inbox row: `note` and the rest of FYI**: Dismiss and nothing else — and never Reply
    on a `system` note: it reports what happened to your own message, and there is nobody to reply
    to (§4.10). A closed `steer` in FYI must not still offer Pause or Go with it."""
    note = rows("fyi", [entry("m-1", "note")])
    assert 'data-act="unmail"' in note and ">Dismiss<" in note
    assert 'data-act="reply"' not in note  # §4.5a gives an FYI row one control, and it is Dismiss

    sysnote = rows("fyi", [entry("m-2", "note", from_="system", from_name="system", from_open="")])
    assert 'data-act="reply"' not in sysnote and ">Dismiss<" in sysnote
    assert "/focus/" not in sysnote  # no record to open: `system` is the home, not a session

    lapsed = rows("fyi", [entry("m-3", "steer", default="off main", closed_reason="lapsed")])
    assert "lapsed · the sender went with its default" in lapsed
    assert 'data-act="pause"' not in lapsed and 'data-act="gowithit"' not in lapsed


@pytest.mark.unit
def test_a_snoozed_row_offers_unsnooze_and_says_when_in_the_persons_own_clock():
    """§4.10 *Snooze* — and the rule every clock on this page follows: the entry stores a UTC
    instant, and the person set that time in their own clock, so the row hands the instant to the
    browser to format and never prints the bare `Z` string. The raw instant stays on hover."""
    html = rows("snoozed", [entry("m-1", "ask", snoozed_until="2026-09-19T18:00:00Z")])
    assert 'data-act="unsnooze" data-id="person" data-msg="m-1"' in html
    assert 'class="localtime" data-at="2026-09-19T18:00:00Z" title="2026-09-19T18:00:00Z"' in html
    assert ">2026-09-19T18:00:00Z<" not in html  # never the stored instant as the visible text
    assert 'data-act="snooze"' not in html
    js = (UI / "static" / "app.js").read_text()
    assert ".localtime[data-at]" in js and "toLocaleString()" in js


@pytest.mark.unit
def test_every_field_a_session_wrote_is_text_and_a_kind_mark_is_not_a_control():
    """TD-071 item 8, §4.5 screen 6: nothing on the page is built from what an agent printed except
    as text. `<b>` in a message, a `default` or an `about` stays `<b>` on the screen — and the kind
    mark is a span, never a button: a state that is not pressable must not look pressable."""
    e = entry("m-1", "steer", text="run <b>rm -rf</b> now", default="<i>off main</i>", about="<script>x</script>",
              bound="2026-09-20T09:00:00Z")  # fmt: skip
    html = rows("steering", [e])
    assert "<b>rm -rf</b>" not in html and "&lt;b&gt;rm -rf&lt;/b&gt;" in html
    assert "<i>off main</i>" not in html and "&lt;i&gt;off main&lt;/i&gt;" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<span class="badge kindmark k-steer">steer</span>' in html


@pytest.mark.unit
def test_a_two_thousand_character_message_renders_whole_with_no_scroll_box():
    """§4.5 screen 6 *Done when*: a 2,000-character message reads in full. The dialog this page
    replaces put the body in a `max-height: 12em` scroll box; the page's row must not."""
    long = "x" * 1700 + " " + "y" * 299
    html = rows("needs", [entry("m-1", "ask", text=long)])
    assert long in html and len(long) == 2000
    css = (UI / "static" / "app.css").read_text()
    body = next(ln for ln in css.splitlines() if ".inboxpage .mailrow .body" in ln)
    assert "max-height" not in body and "overflow: auto" not in body
    assert "white-space: pre-wrap" in body and "overflow-wrap: anywhere" in body  # a long token wraps


@pytest.mark.unit
def test_the_team_filter_has_the_data_it_filters_on_and_no_team_is_a_badge_too():
    """§4.5a: a team filter narrows all three sections, `team:name` as on the Org, and an entry
    from a session with no team (or sent before the stamp existed) shows under *No team*. The
    free-text filter matches sender, text and about, which `data-find` carries in one place."""
    html = rows("needs", [
        entry("m-1", "ask", team="ao-grind", text="Merge IT?", about="TD-069", from_name="w1"),
        entry("m-2", "ask", team=None),
    ])  # fmt: skip
    assert 'data-team="ao-grind"' in html and 'data-msg="m-1"' in html
    assert 'data-team=""' in html and ">No team<" in html
    # lowercased once, on the server; with its team and kind since the rail (TD-135)
    assert 'data-find="w1 ao-grind ask td-069 merge it?"' in html


@pytest.mark.unit
def test_the_page_renders_its_three_sections_the_count_and_the_snoozed_affordance(monkeypatch, tmp_path):
    """§4.5 screen 6: three sections, FYI folded by default, and a small *n snoozed — show* that
    lists what is set aside. The Steering section says doing nothing is a valid answer."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import inbox_sections, templates

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    es = [
        entry("m-1", "ask"),
        entry("m-2", "steer", default="off main", bound=iso(now + timedelta(hours=1))),
        entry("m-3", "note"),
        entry("m-4", "ask", snoozed_until=iso(now + timedelta(hours=3))),
    ]
    sections = inbox_sections(es, now=now)
    html = templates.get_template("inbox.html").render(
        sections=sections, person_needs=sections["count"], host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert ">Needs you<" in html and ">Steering<" in html and ">FYI<" in html
    assert "Doing nothing is a valid answer" in html
    assert needs_line(html) == "1"  # the snoozed ask is not in it
    assert "1 snoozed — show" in html
    fyi = html[html.index('id="sec-fyi"') : html.index('id="sec-fyi"') + 40]
    assert " open" not in fyi  # folded by default; the browser remembers what the person did
    assert 'class="btn light on" href="/inbox"' in html  # the top bar's control is this page's nav item


@pytest.mark.unit
def test_the_retired_dialog_leaves_nothing_behind_that_still_points_at_it():
    """TD-069 step 1 retires the `personbox` dialog. What the card's **unread** chip and the Focus
    Inbox panel use — `AO.mailEntry`, `/api/sessions/<id>/inbox`, `.badge.unread` — stays."""
    js = (UI / "static" / "app.js").read_text()
    base = (UI / "templates" / "base.html").read_text()
    css = (UI / "static" / "app.css").read_text()
    for dead in ("personbox", "personlist", "personcount", "personclose", "personunread"):
        assert f'id="{dead}"' not in base and f"#{dead}" not in js and f"#{dead}" not in css, dead
    assert "<dialog" in base and 'id="mailbox"' in base  # the composer dialog stays: Message and Reply use it
    assert "refreshPersonInbox" not in js and 'owner === "person"' not in js  # and no half of it is left
    assert "AO.mailEntry" in js and "/api/sessions/" in js and ".badge.unread" in css
    assert "AO.refreshInboxPage" in js and 'href="/inbox"' in base


def test_message_composer_opens_on_ask():
    """Design §4.5a **Message** (2026-09-25): the composer's kind is `ask` unless the person picks
    `note` — a person's message is usually a question, and only an `ask` fills an on-call seat."""
    js = (UI / "static" / "app.js").read_text()
    base = (UI / "templates" / "base.html").read_text()
    assert '$("#mailkind").value = "ask"' in js
    assert '$("#mailkind").value = "note"' not in js
    sel = base[base.index('id="mailkind"') :]
    assert sel.index('value="ask" selected') < sel.index('value="note"')


SWAP_PROBE = """
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
global.location = { pathname: "/inbox", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const may = window.AO.maySwapSection;
const quiet = el();
const menu = el({ querySelector: (s) => (s === "details[open]:not(.fold)" ? el() : null) });
// only a *details* fold is open (TD-138): the selector above does not see it, and the swap goes on
const folded = el({ querySelector: (s) => (s === "details[open]" ? el() : null) });
const button = el();
const busy = el({ contains: (x) => x === button });
console.log(JSON.stringify({
  quiet: may(quiet, document.body),
  missing: may(null, null),
  menu_open: may(menu, null),
  fold_open: may(folded, null),
  fold_body: window.AO.foldBody({ id: "m-1", lead_html: "<p>lead</p>", rest_html: "<p>rest</p>" }),
  bare_body: window.AO.foldBody({ id: "m-2", lead_html: "<p>all</p>", rest_html: "" }),
  old_body: window.AO.foldBody({ id: "m-3", text: "<b>x</b>" }),
  focus_inside: may(busy, button),
  focus_elsewhere: may(quiet, button),
}));
"""


@pytest.mark.unit
def test_a_poll_never_swaps_rows_out_from_under_the_person():
    """The 20 s poll replaces a section's markup, which would close a Snooze menu mid-press and
    take the focus of someone tabbing through a row's controls. The rule that decides is pure, so
    it is run here as itself: no swap while a `details` in the section is open or while the focus
    is inside it — the next poll does it, and the count above never waits."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "swap_probe.js"
    probe.write_text(SWAP_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {
        "quiet": True,  # nobody is in it: swap
        "missing": False,  # no such section on this page
        "menu_open": False,  # a Snooze menu the person opened
        "fold_open": True,  # an open *details* fold is put back after the swap (TD-138)
        # the Focus panel's entry, from the halves the server rendered (§4.5a *Inbox row: details*)
        "fold_body": '<div class="body md"><p>lead</p></div><details class="fold" data-fold="m-1">'
        '<summary>details</summary><div class="body md"><p>rest</p></div></details>',
        "bare_body": '<div class="body md"><p>all</p></div>',
        "old_body": '<div class="body">&lt;b&gt;x&lt;/b&gt;</div>',  # an older UI's entry: escaped, whole
        "focus_inside": False,  # tabbing through this section's controls
        "focus_elsewhere": True,  # the focus is in another section, or the filter box
    }


# -- the routes behind the controls ---------------------------------------------------------------


@pytest.fixture
def client(subprocess_agent):
    """The agent is module-scoped, so the person inbox carries over between tests: each starts from
    an empty one — a decline first (an open question does not strip), then the strip."""
    from agentorc.ui.app import create_app

    with TestClient(create_app()) as c:
        for _ in range(2):
            for e in c.get("/api/person/inbox").json()["entries"]:
                c.post("/api/person/unmail", json={"msg": e["id"]})
        assert c.get("/api/person/inbox").json()["entries"] == []
        yield c


def sender_session(client, tmp_path, name="asker"):
    r = client.post("/shell", data={"dir": str(tmp_path), "name": name}, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[-1]


def send(sender, **kw):
    from sessionorc.client import LocalClient

    async def go():
        async with LocalClient(caller=sender) as c:
            return await c.call("msg", to="person", **kw)

    return asyncio.run(go())["entry"]


@pytest.mark.integration
def test_the_page_the_count_and_the_poll_agree_and_reading_marks_nothing(client, tmp_path):
    """§4.5a **Inbox page**: the number is computed server-side in one place and used by both the
    top bar and the page, so they cannot disagree. And the page polls every few seconds: that read
    is a person's, which sets no `read_at` — otherwise the card's unread chip and the person
    inbox's depths (which count *unread or open*, §4.10) would be emptied by looking at the page."""
    sender = sender_session(client, tmp_path)
    ask = send(sender, text="merge PR 9?", kind="ask")["id"]
    send(sender, text="I will branch off main", kind="steer", default="off main")
    send(sender, text="for your information", kind="note")

    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 1 and got["sections"]["needs"] == [ask]
    assert len(got["sections"]["steering"]) == 1 and len(got["sections"]["fyi"]) == 1
    page = client.get("/inbox")
    assert page.status_code == 200 and needs_line(page.text) == "1"
    assert 'class="badge needs" id="personneeds">1</span>' in page.text
    assert 'class="badge needs" id="personneeds">1</span>' in client.get("/").text  # the Org's top bar

    for _ in range(3):  # the poll, three times over
        assert all(e["read_at"] is None for e in client.get("/api/person/inbox").json()["entries"])


@pytest.mark.integration
def test_snooze_hides_and_uncounts_an_entry_and_unsnooze_brings_it_back(client, tmp_path):
    """§4.10 *Snooze*: `/api/person/snooze` is `inbox_snooze` caller-less; the entry leaves its
    section and the count until its time, is listed under *snoozed*, and no `until` clears it. A
    `steer` is refused a snooze by the agent, and that refusal reaches the page as an error."""
    sender = sender_session(client, tmp_path)
    ask = send(sender, text="merge PR 9?", kind="ask")["id"]
    steer = send(sender, text="branching", kind="steer", default="off main")["id"]
    later = iso(datetime.now(UTC) + timedelta(hours=1))

    assert client.post("/api/person/snooze", json={}).status_code == 400  # it names the entry
    r = client.post("/api/person/snooze", json={"msg": ask, "until": later})
    assert r.status_code == 200 and r.json()["snoozed_until"] == later
    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 0 and got["sections"]["needs"] == [] and got["sections"]["snoozed"] == [ask]
    assert got["snoozed_n"] == 1 and ">Unsnooze<" in got["html"]["snoozed"]
    assert ask not in got["sections"]["fyi"] and ask not in got["sections"]["steering"]

    assert client.post("/api/person/snooze", json={"msg": ask}).json()["snoozed_until"] is None
    assert client.get("/api/person/inbox").json()["sections"]["needs"] == [ask]

    bad = client.post("/api/person/snooze", json={"msg": steer, "until": later})
    assert bad.status_code == 400 and "Pause" in bad.json()["detail"]  # the agent's rule, not the UI's


@pytest.mark.integration
def test_pause_moves_a_steer_into_needs_you_and_counts_it_and_resume_gives_the_time_back(client, tmp_path):
    """§4.10 *Pause*: a paused `steer` is counted — a session is now held on the person — and the
    sender is told by a `system` note. Resume moves the bound later by the time it was held."""
    sender = sender_session(client, tmp_path)
    steer = send(sender, text="branching", kind="steer", default="off main")["id"]
    assert client.get("/api/person/inbox").json()["needs"] == 0  # a running steer is not counted

    r = client.post("/api/person/pause", json={"msg": steer})
    assert r.status_code == 200 and r.json()["paused_at"]
    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 1 and got["sections"]["needs"] == [steer] and got["sections"]["steering"] == []
    assert ">Resume<" in got["html"]["needs"] and "paused · the clock is stopped" in got["html"]["needs"]
    # the sender was told, and the note that told it is not an instruction (§4.10)
    [told] = [e for e in client.get(f"/api/sessions/{sender}/inbox").json()["entries"] if e["from"] == "system"]
    assert "paused by the person" in told["text"]

    back = client.post("/api/person/resume", json={"msg": steer})
    assert back.status_code == 200 and back.json()["paused_at"] is None
    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 0 and got["sections"]["steering"] == [steer]
    assert client.post("/api/person/pause", json={"msg": "m-nope"}).status_code == 400


@pytest.mark.integration
def test_go_with_it_closes_a_steer_now_and_the_row_moves_to_waiting_on_them(client, tmp_path):
    """§4.5a **Inbox row: `steer`** → **Go with it**: closes it `go_with_it`, the sender is told and
    need not wait out the bound; doing nothing would have lapsed to the same end.

    From TD-079 step 2 the row lands in **Waiting on them**, not FYI: *a `go_with_it` close owes an
    outcome too* (§4.10 *Outcomes*), so the sender still has to say what came of it — and the page
    says so where the agent already enforces it. Still in **neither** number, as it was in FYI."""
    sender = sender_session(client, tmp_path)
    steer = send(sender, text="branching", kind="steer", default="off main")["id"]
    r = client.post("/api/person/gowithit", json={"msg": steer})
    assert r.status_code == 200 and r.json()["closed_reason"] == "go_with_it"
    got = client.get("/api/person/inbox").json()
    assert got["sections"]["waiting"] == [steer] and got["sections"]["fyi"] == [] and got["needs"] == 0
    assert "you let it go with its default" in got["html"]["waiting"]
    assert client.post("/api/person/gowithit", json={"msg": steer}).status_code == 400  # already closed


@pytest.mark.integration
def test_delete_on_an_open_ask_declines_it_and_dismiss_strips_a_note(client, tmp_path):
    """§4.10 *Deleting is declining, and nothing vanishes at once*: the row's **Delete** closes the
    question as `declined` — the sender is told — and it stays in FYI for the retention window;
    **Dismiss**, on a `note`, removes the entry outright."""
    sender = sender_session(client, tmp_path)
    ask = send(sender, text="merge PR 9?", kind="ask")["id"]
    note = send(sender, text="fyi", kind="note")["id"]

    r = client.post("/api/person/unmail", json={"msg": ask})
    assert r.status_code == 200 and r.json()["declined"] is True
    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 0 and ask in got["sections"]["fyi"]
    assert "declined · the sender was told" in got["html"]["fyi"]
    [told] = [e for e in client.get(f"/api/sessions/{sender}/inbox").json()["entries"] if e["from"] == "system"]
    assert "declined by the person" in told["text"]

    assert client.post("/api/person/unmail", json={"msg": note}).json()["declined"] is False
    assert note not in [e["id"] for e in client.get("/api/person/inbox").json()["entries"]]


@pytest.mark.integration
def test_the_page_opens_the_session_a_row_is_about_only_while_its_record_is_here(client, tmp_path):
    """§4.5 screen 6: a row opens the session that needs the person — and when that record is gone
    the row is still readable, which is the whole reason the envelope carries its own `team` and
    the page never joins to the sender's record for anything it must show (§4.10). Forgetting the
    asker also closes its open `ask` as `asker_gone`, so the row moves to FYI and leaves the count:
    nobody is waiting on that answer any more."""
    sender = sender_session(client, tmp_path)
    ask = send(sender, text="merge PR 9?", kind="ask")["id"]
    got = client.get("/api/person/inbox").json()
    assert f'href="/focus/{sender}"' in got["html"]["needs"]

    client.post(f"/api/sessions/{sender}/kill")
    for _ in range(60):
        if next(s for s in client.get("/api/sessions").json() if s["id"] == sender)["state"] == "exited":
            break
        time.sleep(0.1)
    assert client.post(f"/api/sessions/{sender}/remove").status_code == 200
    got = client.get("/api/person/inbox").json()
    assert got["needs"] == 0 and got["sections"]["fyi"] == [ask]
    assert "/focus/" not in got["html"]["fyi"] and "closed · the asker is gone" in got["html"]["fyi"]
    assert "merge PR 9?" in got["html"]["fyi"]  # still readable: the record is gone, the mail is not


# -- the state rows (design §4.5 screen 6, §4.5a **Inbox row: state**; TD-069 step 2) -------------


def rec(sid, state, **kw):
    """A session record as `list` hands it to `view()`."""
    s = {
        "id": sid, "name": kw.pop("name", sid.replace("ao-", "")), "kind": "interactive",
        "adapter": "claude-code", "dir": "/w", "repo": "/w", "state": state, "pane": True, "tail": [],
        "since": kw.pop("since", "2026-09-19T10:00:00Z"), "confidence": kw.pop("confidence", "hook"),
    }  # fmt: skip
    s.update(kw)
    return s


def state_rows_of(records, **kw):
    from agentorc.ui.app import state_rows, view

    return state_rows([view(r, list(records)) for r in records], **kw)


PERMISSION = {"kind": "permission", "text": "run rm -rf /tmp/x?", "tool_use_id": "t-1",
              "deadline": "2026-09-19T12:05:00Z"}  # fmt: skip


_ahead3 = {"ahead": 3, "dirty": 0, "upstream": "origin/main", "unpushed": 3, "pushed_against": "origin/main"}


@pytest.mark.unit
def test_each_state_row_kind_carries_its_own_controls_and_no_others(tmp_path, monkeypatch):
    """§4.5a **Inbox row: state**: a permission gets **Allow / Deny** on the hook channel and the
    time left; a question and `stalled?` get the text and **Open**; `limited` says the cap holds
    it; an exited session with unpushed work says what Ready to close says. No row offers another
    row's control, and none offers Snooze — a state has nowhere to keep one (TD-069 step 2)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    records = [
        rec("ao-p", "needs-you", pending=PERMISSION, team="ao-grind"),
        rec("ao-q", "needs-you", pending={"kind": "question", "text": "which branch?"}),
        rec("ao-s", "stalled?", pending={"kind": "note", "text": "stood down: another device took over"}),
        rec("ao-l", "limited", pending={"kind": "limit", "text": "resets 14:00"}),
        rec("ao-e", "exited", exit_code=0, git=_ahead3),
    ]
    by = {r["row"]: r for r in state_rows_of(records)}
    assert sorted(by) == ["limited", "permission", "question", "stalled", "unpushed"]

    perm = rows("needs", [by["permission"]])
    assert 'data-act="allow" data-id="ao-p"' in perm and 'data-act="deny" data-id="ao-p"' in perm
    assert 'class="meta countdown" data-deadline="2026-09-19T12:05:00Z"' in perm
    assert "run rm -rf /tmp/x?" in perm and 'data-act="snooze"' not in perm and ">Open<" not in perm

    for kind, text in (("question", "which branch?"), ("stalled", "stood down"), ("limited", "resets 14:00")):
        html = rows("needs", [by[kind]])
        assert text in html and ">Open<" in html, kind
        assert 'data-act="allow"' not in html and 'data-act="snooze"' not in html, kind

    gone = rows("needs", [by["unpushed"]])
    assert "3 unpushed" in gone and "branch pushed" in gone  # what Ready to close says (§4.2)
    assert ">Details<" in gone and 'href="/focus/ao-e"' in gone and 'data-act="allow"' not in gone


@pytest.mark.unit
def test_the_restart_row_names_what_the_tick_could_not_restart(tmp_path, monkeypatch):
    """§4.5a **Inbox row: restart** (§6 *Keeping a team running*, TD-103 slice 5): a record at the
    crash or the fill ceiling, one whose wanted restart is held by work left, and an `early`
    `restart_wanted` each raise one row — as old as the mark, beside any state row the record also
    has — with **Resume**, **Open** and **Snooze**. A superseded record raises none."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    at = "2026-09-19T11:00:00Z"
    records = [
        rec("ao-c", "exited", exit_code=1, git=_ahead3, restart_ceiling={"at": at, "count": 3}),
        rec("ao-f", "exited", restart_ceiling={"at": at, "count": 6, "why": "fill"}),
        rec(
            "ao-h",
            "idle",
            restart_wanted={"at": at, "why": "long"},
            restart_blocked={"at": at, "dirty": 2, "unpushed": 1},
        ),
        rec("ao-e", "idle", restart_wanted={"at": at, "why": "context full\nmore", "early": True}),
        rec("ao-x", "exited", restart_ceiling={"at": at, "count": 3}, superseded_by="ao-y"),
        rec("ao-n", "idle", restart_wanted={"at": at, "why": "long"}),  # not early: the tick's to act on
    ]
    got = state_rows_of(records)
    restart = {r["sid"]: r for r in got if r["row"] == "restart"}
    assert sorted(restart) == ["ao-c", "ao-e", "ao-f", "ao-h"]
    assert {r["row"] for r in got if r["sid"] == "ao-c"} == {"restart", "unpushed"}, "beside its state row"
    assert restart["ao-c"]["text"].startswith("restarts exhausted · 3 in 2 h") and restart["ao-c"]["at"] == at
    assert restart["ao-f"]["text"].startswith("fills exhausted · 6 in 1 h")
    assert "2 uncommitted and 1 unpushed" in restart["ao-h"]["text"]
    assert restart["ao-e"]["text"] == "restart wanted · early — asked inside its first half hour: context full"
    html = rows("needs", [restart["ao-c"]])
    assert 'data-act="resume" data-id="ao-c"' in html and ">Open<" in html
    assert 'data-act="attention_snooze"' in html and 'data-row="restart"' in html and 'data-act="allow"' not in html
    assert f'data-until="dismissed:{at}"' in html and ">Dismiss<" in html
    # **Dismiss** hides that one mark's row; a new mark (a new `at`) raises a new row
    from agentorc.ui.app import inbox_sections

    gone = inbox_sections([], states=[restart["ao-c"]], attention_snoozed={"ao-c|restart": f"dismissed:{at}"})
    assert all(restart["ao-c"] not in rows_ for rows_ in gone.values() if isinstance(rows_, list))
    again = inbox_sections(
        [],
        states=[{**restart["ao-c"], "at": "2026-09-19T15:00:00Z"}],
        attention_snoozed={"ao-c|restart": f"dismissed:{at}"},
    )
    assert [r["sid"] for r in again["needs"]] == ["ao-c"]


@pytest.mark.unit
def test_a_state_row_carries_the_cards_own_marks_and_none_of_them_is_pressable(tmp_path, monkeypatch):
    """§4.5 screen 6: the row shows the session's name, team, role badge, its `title` and its
    `doing` line with age — the card's own view, so the two cannot drift. TD-071 item 8: the state
    mark is the card's pill, a `<span>`, and what a session wrote is text and nothing else."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    r = rec("ao-p", "needs-you", pending={"kind": "permission", "text": "run <b>rm</b>?", "tool_use_id": "t"},
            team="ao-grind", role="grinder", title="Error <i>Checker</i>",
            doing={"text": "reading <script>x</script>", "at": "2026-09-19T09:50:00Z"})  # fmt: skip
    [row] = state_rows_of([r])
    html = rows("needs", [row])
    assert '<span class="pill s-needs' in html and '<button class="pill' not in html
    assert ">w1<" not in html and 'href="/focus/ao-p"' in html  # the name links to Focus
    assert "ao-grind" in html and "grinder" in html
    assert "&lt;i&gt;Checker&lt;/i&gt;" in html and "<i>Checker</i>" not in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "&lt;b&gt;rm&lt;/b&gt;" in html and "<b>rm</b>" not in html
    assert "doing: reading" in html and "says" in html


@pytest.mark.unit
def test_needs_you_is_the_tools_clock_first_then_oldest_across_states_and_mail(tmp_path, monkeypatch):
    """§4.5 screen 6: *what is on the tool's clock first (a permission's countdown), then oldest
    first* — and the order is one order over states and mail together, not two lists stapled."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import inbox_sections

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    soon = {**PERMISSION, "deadline": iso(now + timedelta(minutes=2))}
    later = {**PERMISSION, "deadline": iso(now + timedelta(minutes=5))}
    states = state_rows_of([
        rec("ao-p", "needs-you", since="2026-09-19T11:50:00Z", pending=later),
        rec("ao-p2", "needs-you", since="2026-09-19T11:55:00Z", pending=soon),
        rec("ao-s", "stalled?", since="2026-09-19T07:00:00Z", pending={"kind": "note", "text": "quiet"}),
    ])  # fmt: skip
    mail = [entry("m-1", "ask", at="2026-09-19T09:00:00Z"), entry("m-2", "ask", at="2026-09-19T06:00:00Z")]
    got = inbox_sections(mail, states=states, now=now)
    assert [e["id"] for e in got["needs"]] == ["ao-p2:permission", "ao-p:permission", "m-2", "ao-s:stalled", "m-1"]
    assert got["count"] == 5  # states + open asks; nothing snoozed, nothing paused


@pytest.mark.unit
def test_the_count_is_states_plus_asks_plus_paused_steers_minus_snoozed(tmp_path, monkeypatch):
    """§4.5a **Inbox page**: one computation, and what it counts. A running `steer`, a `note` and a
    snoozed entry are outside it; a state row is inside it and cannot be snoozed away."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import inbox_sections

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    hour = iso(now + timedelta(hours=1))
    states = state_rows_of([rec("ao-p", "needs-you", pending=PERMISSION), rec("ao-w", "working")])
    assert len(states) == 1  # a working session needs nobody
    mail = [
        entry("m-1", "ask"),
        entry("m-2", "steer", default="off main", bound=hour),
        entry("m-3", "steer", default="off main", bound=hour, paused_at="2026-09-19T11:00:00Z"),
        entry("m-4", "note"),
        entry("m-5", "ask", snoozed_until=iso(now + timedelta(hours=2))),
    ]
    got = inbox_sections(mail, states=states, now=now)
    assert got["count"] == 3 and sorted(e["id"] for e in got["needs"]) == ["ao-p:permission", "m-1", "m-3"]
    assert [e["id"] for e in got["steering"]] == ["m-2"] and [e["id"] for e in got["snoozed"]] == ["m-5"]


@pytest.mark.unit
def test_the_filters_cover_state_rows(tmp_path, monkeypatch):
    """§4.5 screen 6: the team filter narrows all three sections — a state carries its session's
    badge — and the free-text filter matches the name, the title, the `doing` line and the pending
    text, which `data-find` carries in one place, lowercased on the server."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    r = rec("ao-p", "needs-you", name="w1", team="ao-grind", title="Error Checker",
            doing={"text": "reading the fetcher", "at": "2026-09-19T09:00:00Z"},
            pending={"kind": "permission", "text": "run PYTEST?", "tool_use_id": "t"})  # fmt: skip
    html = rows("needs", state_rows_of([r]))
    assert 'data-team="ao-grind"' in html
    find = html.split('data-find="')[1].split('"')[0]
    assert find == "w1 error checker reading the fetcher run pytest? ao-grind needs you"  # team, pill: TD-135
    none = rows("needs", state_rows_of([rec("ao-q", "needs-you", pending={"kind": "q", "text": "?"})]))
    assert 'data-team=""' in none and ">No team<" in none


@pytest.mark.unit
def test_a_state_row_is_not_a_mail_row_and_nothing_a_session_wrote_becomes_a_control(tmp_path, monkeypatch):
    """One row renderer per kind (§4.5 screen 6): a state row is dispatched by `row`, marked as a
    state, and carries none of the mail controls — Reply, Delete, Dismiss, Snooze, Pause."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    html = rows("needs", state_rows_of([rec("ao-p", "needs-you", pending=PERMISSION)]))
    assert 'class="mailrow staterow"' in html and 'data-kind="state"' in html and 'data-row="permission"' in html
    for dead in ("reply", "unmail", "snooze", "pause", "gowithit", "resume"):
        assert f'data-act="{dead}"' not in html, dead


# -- identity alarms on the card and in the Inbox (design §4.8a; TD-077 step 2) --------------------


ALARM = {"channel": "session ao-x", "claimed": "ao-y", "rpc": "msg", "count": 3, "at": "2026-09-19T10:00:00Z",
         "last": "2026-09-19T10:30:00Z"}  # fmt: skip


@pytest.mark.unit
def test_the_card_marks_a_record_with_identity_alarms_and_the_mark_is_not_a_control(tmp_path, monkeypatch):
    """§4.8a: *a mark on the card*. It says the newest alarm in words on hover, it is a `<span>` —
    never pressable (TD-071 item 8) — and a malformed alarm entry costs that card its mark and not
    the grid, exactly as a malformed `doing` or `run_until` does."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    card = templates.get_template("card.html")
    assert "alarmmark" not in card.render(s=view(rec("ao-x", "idle")))
    html = card.render(s=view(rec("ao-x", "idle", identity_alarms=[ALARM])))
    assert '<span class="badge alarmmark"' in html and "<button" not in html.split("alarmmark")[1].split(">")[0]
    assert "session ao-x claimed to be ao-y on msg ×3" in html
    assert 'data-act="identity_ack"' not in html  # the control lives on the Inbox row, not here
    # …and the mark's hover names no control by a name the row no longer uses: the card and the
    # row are the two places a person reads about an alarm, and a rename that reached one and not
    # the other would send them looking for a button that is not there (review of PR #298)
    assert "Acknowledge" not in html and "what can be done about it" in html

    junk = view(rec("ao-x", "idle", identity_alarms=["not a dict", None, {"claimed": "ao-y"}, ALARM]))
    assert len(junk["alarms"]) == 2 and "ao-y" in junk["alarm_note"]
    assert "alarmmark" in card.render(s=junk)
    assert view(rec("ao-x", "idle", identity_alarms="broken"))["alarms"] == []  # not even a list
    assert view(rec("ao-x", "idle", identity_alarms=[{"claimed": "ao-y", "rpc": "msg", "count": "lots"}]))["alarms"]


@pytest.mark.unit
def test_the_inbox_has_a_row_per_record_with_alarms_and_one_for_the_hosts_own_list(tmp_path, monkeypatch):
    """§4.8a: *a row under Needs you*, counted, because it is either a bug of ours or a session
    misbehaving and a person should know which. The row lists the alarms in words, says which mode
    the host is in, and offers **Dismiss**; the host's own list is a row of its own, blaming no
    record. `(others)` is read as what it stands for, never printed as a row of empty fields."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import alarm_view, inbox_sections

    others = {"channel": "", "claimed": "(others)", "rpc": "", "count": 7,
              "at": "2026-09-19T10:00:00Z", "last": "2026-09-19T11:00:00Z"}  # fmt: skip
    host_alarm = {"channel": "outside", "claimed": "ao-x", "rpc": "kill", "count": 1,
                  "at": "2026-09-19T09:00:00Z", "last": "2026-09-19T09:00:00Z"}  # fmt: skip
    states = state_rows_of(
        [rec("ao-x", "idle", identity_alarms=[ALARM, others])],
        host_alarms=alarm_view([host_alarm]),
        host="kmaster",
        identity_mode="observe",
    )
    assert [r["row"] for r in states] == ["alarm", "alarm_host"]
    got = inbox_sections([], states=states, now=datetime(2026, 9, 19, 12, tzinfo=UTC))
    assert got["count"] == 2 and [e["id"] for e in got["needs"]] == ["host:alarm", "ao-x:alarm"]

    html = rows("needs", got["needs"])
    assert "session ao-x claimed to be ao-y on msg ×3" in html
    assert "and 7 more distinct claims" in html
    assert "outside claimed to be ao-x on kill" in html and ">kmaster<" in html
    assert "identity: observe" in html
    assert 'data-act="identity_ack" data-id="person" data-who="ao-x"' in html  # the record's list
    assert 'data-act="identity_ack" data-id="person" data-who=""' in html  # the host's own
    # §4.5a, renamed 2026-09-20 on Paul's direction (*"Acknowledge" seems like a dismiss*): the
    # control is **Dismiss**, as it is on every other row, and the **wire name stays** — a wire
    # name is not a control, and `identity_ack` is what the agent's own tests drive
    assert ">Dismiss</button>" in html and "Acknowledge" not in html
    # …and of the two §4.5a gives the row beside it, **Suspend** arrived with `rpc_suspend`
    # (TD-077 a2) and **Log TD** with `identity_log` (TD-077 b) — but only where a session answers
    # for the record, and this one's record names nobody (no `alarm_to`), so the row says so
    assert ">Suspend</button>" in html and 'data-act="identity_log"' not in html
    assert "no session answers for this one" in html and "no session answers for the host's own list" in html
    # every instant is handed to the browser to put in the person's own clock, as the snoozed row is
    assert 'class="localtime" data-at="2026-09-19T10:30:00Z"' in html
    assert ">2026-09-19T10:30:00Z<" not in html


@pytest.mark.unit
def test_the_two_counts_say_how_they_differ(tmp_path, monkeypatch):
    """§4.5a **Inbox page**: the top bar and the page both say the Inbox number is not the Org's
    needs-you count — and from step 2 they say precisely how: the states are in both, the asks and
    paused steers only in this one."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import inbox_sections, templates

    sections = inbox_sections([], states=[])
    html = templates.get_template("inbox.html").render(
        sections=sections, person_needs=0, host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    for text in ("the session states the Org counts too", "open questions sessions asked you", "anything snoozed"):
        assert text in html, text
    base = (UI / "templates" / "base.html").read_text()
    assert "the session states the Org counts too" in base and "the states alone" in base


# -- the routes, against a live agent -------------------------------------------------------------


@pytest.mark.integration
def test_a_permission_is_a_row_the_page_the_poll_and_the_top_bar_all_count(client, tmp_path):
    """§4.5 screen 6 / §4.5a **Inbox row: state**: a session waiting on a permission is a row of
    the Inbox, counted in the same number the Org's top bar shows — one `inbox_sections`, so the
    page, the poll and the bar cannot disagree. **Allow** posts to the card's own route, through
    the hook channel, and the row leaves with the state; answering it a second time is the 409 the
    page turns into *already answered* rather than a failure the person has to read."""
    import os
    import subprocess
    import sys

    before = client.get("/api/person/inbox").json()["needs"]
    r = client.post("/new", data={"dir": str(tmp_path), "name": "perm", "adapter": "hookstub"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    env = {**os.environ, "AGENTORC_SESSION": sid, "AGENTORC_PERMISSION_WAIT": "30"}
    payload = {"hook_event_name": "PermissionRequest", "tool_name": "Bash",
               "tool_input": {"command": "rm -rf x"}, "tool_use_id": "tu-inbox"}  # fmt: skip
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentorc.adapters.claude_code.hook"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()
    try:
        for _ in range(60):
            got = client.get("/api/person/inbox").json()
            if f"{sid}:permission" in got["sections"]["needs"]:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the pending permission never became an Inbox row")
        assert got["needs"] == before + 1
        assert f'data-act="allow" data-id="{sid}"' in got["html"]["needs"] and "rm -rf x" in got["html"]["needs"]
        assert f'class="badge needs" id="personneeds">{got["needs"]}</span>' in client.get("/").text
        assert needs_line(client.get("/inbox").text) == str(got["needs"])

        assert client.post(f"/api/sessions/{sid}/allow", json={}).json() == {"ok": True}
        assert proc.wait(timeout=15) == 0
        assert json.loads(proc.stdout.read())["hookSpecificOutput"]["decision"]["behavior"] == "allow"
        for _ in range(60):
            after = client.get("/api/person/inbox").json()
            if f"{sid}:permission" not in after["sections"]["needs"]:
                break
            time.sleep(0.1)
        assert f"{sid}:permission" not in after["sections"]["needs"] and after["needs"] == before
        again = client.post(f"/api/sessions/{sid}/allow", json={})
        assert again.status_code == 409 and "no pending permission" in again.json()["detail"]
        js = (UI / "static" / "app.js").read_text()
        assert "no pending permission" in js and "already answered" in js  # the page says so, not *failed*
    finally:
        if proc.poll() is None:
            proc.kill()
        client.post(f"/api/sessions/{sid}/kill")


@pytest.mark.integration
def test_an_exited_session_with_unpushed_work_is_a_row_until_it_is_forgotten(client, tmp_path):
    """§4.5a **Inbox row: state**: *exited with unpushed work* — what Ready to close says, and
    **Details**. Only a person resolves it, so it is counted; it goes when the session is
    forgotten, which is one of the two ways §4.5a says it leaves."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)
    (repo / "f.txt").write_text("work nobody else has\n")
    before = client.get("/api/person/inbox").json()["needs"]
    sid = sender_session(client, repo, name="dirty")
    client.post(f"/api/sessions/{sid}/kill")
    for _ in range(60):
        got = client.get("/api/person/inbox").json()
        if f"{sid}:unpushed" in got["sections"]["needs"]:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("an exited session with work only here never became a row")
    assert got["needs"] == before + 1
    assert "dirty" in got["html"]["needs"] and "tree clean" in got["html"]["needs"]  # Ready to close's words
    assert f'href="/focus/{sid}"' in got["html"]["needs"] and ">Details<" in got["html"]["needs"]
    assert client.post(f"/api/sessions/{sid}/remove").status_code == 200
    got = client.get("/api/person/inbox").json()
    assert f"{sid}:unpushed" not in got["sections"]["needs"] and got["needs"] == before


@pytest.mark.integration
def test_dismiss_on_an_alarm_is_a_persons_own_route_and_the_agents_rule_decides(client, tmp_path):
    """§4.5a **Inbox row: identity alarm** → **Dismiss** (the wire name `identity_ack` is unchanged
    by the 2026-09-20 rename: a wire name is not a control): `/api/person/identity_ack` calls the
    `identity_ack` RPC caller-less, exactly as every other control on this page calls its own. The
    UI adds no rule of its own — a record this host does not have comes back as the agent's
    refusal, in the toast every other error uses. (That a session is refused the RPC, and that the
    list is really cleared, are `tests/test_identity.py`'s.)"""
    ok = client.post("/api/person/identity_ack", json={})
    assert ok.status_code == 200 and ok.json()["cleared"] is True and ok.json()["id"] == "person"
    bad = client.post("/api/person/identity_ack", json={"id": "ao-nope"})
    assert bad.status_code == 400 and "no session ao-nope" in bad.json()["detail"]


def test_log_td_is_a_persons_own_route_and_a_controller_gone_since_the_draw_is_the_agents_refusal(client):
    """§4.5a **Inbox row: identity alarm** → **Log TD** (TD-077 b): `/api/person/identity_log`
    calls `identity_log` caller-less and adds no rule of its own. What the page most has to get
    right is the press that can no longer succeed — the controller exited between the draw and the
    press — and that is the agent's refusal, in words, carried as the 400 the toast prints. (That
    the mail goes, owes an outcome and clears the list is `tests/test_identity.py`'s.)"""
    assert client.post("/api/person/identity_log", json={}).status_code == 400  # it names a session
    bad = client.post("/api/person/identity_log", json={"id": "ao-nope"})
    assert bad.status_code == 400 and "no session ao-nope" in bad.json()["detail"]


MANAGER = {"id": "ao-agentorc-manager-ao-1", "name": "manager-ao-1"}


@pytest.mark.unit
def test_log_td_is_drawn_exactly_where_the_home_says_a_session_answers_for_the_record(tmp_path, monkeypatch):
    """§4.8a *An alarm's answers*: **Log TD** hands the alarms to the record's first live
    controller, and is **offered only where there is such a session**. The page does not work that
    out — the home does, as `alarm_to` on the record's view, and the RPC reads the same answer — so
    the button is here exactly when a press can succeed, and the row says so in words where not."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    states = state_rows_of(
        # the home's own shape, `_answers_for`: `{"id", "name"}` (tests/test_identity.py holds it)
        [rec("ao-x", "working", identity_alarms=[ALARM], alarm_to=MANAGER)],
        host="kmaster", identity_mode="enforce",
    )  # fmt: skip
    assert states[0]["alarm_to"] == {"id": "ao-agentorc-manager-ao-1", "name": "manager-ao-1"}
    html = rows("needs", states)
    assert 'data-act="identity_log" data-id="person" data-who="ao-x" data-to="manager-ao-1"' in html
    assert ">Log TD</button>" in html and "Hand these alarms to manager-ao-1 (ao-agentorc-manager-ao-1)?" in html
    assert "owes you an outcome" in html and "no session answers" not in html
    # a record's alarm is on the session that made the request, not the one it named (TD-077)
    assert "Requests this session made under another session's name." in html
    # nobody answers — null, empty, or a shape another build wrote: words, never a control
    for odd in (None, "", "ao-m", 7, ["ao-m"], {}, {"id": "  "}, {"name": "m"}):
        st = state_rows_of([rec("ao-x", "working", identity_alarms=[ALARM], alarm_to=odd)], host="kmaster")
        assert st[0]["alarm_to"] is None, odd
        out = rows("needs", st)
        assert 'data-act="identity_log"' not in out and "no session answers for this one" in out, odd
    # the host's own list is about no record, whatever it carries
    host_row = {**states[0], "row": "alarm_host", "sid": "", "name": "kmaster"}
    assert 'data-act="identity_log"' not in rows("needs", [host_row])

    # a name missing from the home's answer falls back to the id, never to nothing
    bare = state_rows_of([rec("ao-x", "working", identity_alarms=[ALARM], alarm_to={"id": "ao-m"})], host="kmaster")
    assert bare[0]["alarm_to"] == {"id": "ao-m", "name": "ao-m"}

    js = (UI / "static" / "app.js").read_text()
    assert 'if (action === "identity_log") body = { id: b.dataset.who || "" };' in js
    assert "res.to.name || res.to.id" in js  # the RPC answers `to` as {id, name}, never a bare string
    # an answer, so the row goes (only Suspend leaves it standing), and a refusal names the control
    assert 'if (staterow && action !== "suspend") { AO.handRing(staterow); staterow.remove(); }' in js
    assert 'identity_log: "Log TD"' in js and "${named[action] || action} failed" in js


# -- what the review of PR #251 found -------------------------------------------------------------


@pytest.mark.unit
def test_one_poll_reads_the_fleet_once(tmp_path, monkeypatch):
    """The Inbox's mail needs the fleet for its senders' names and its states need the records
    themselves, and both run on every page load and every poll. One `list` per request, then —
    two on the hot path would be two for no reason (review of PR #251)."""
    from fastapi.testclient import TestClient

    from agentorc.ui import app as uiapp

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    calls: list[str] = []
    idreport = {"host": "kmaster", "mode": "off", "detached_check": False, "tally": {}}
    answers = {
        "list": [rec("ao-p", "needs-you", pending=PERMISSION)],
        "inbox": {"id": "person", "entries": [], "threads": {}, "sends": [], "unread": 0},
        "identity": {**idreport, "alarms": [], "sessions": {}},
        "usage": {},
        "host": {"host": "kmaster", "home": "kmaster", "mode": "home", "home_reachable": True, "links": {}},
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append(method)
            return answers[method]

    monkeypatch.setattr(uiapp, "LocalClient", FakeClient)
    with TestClient(uiapp.create_app()) as c:
        got = c.get("/api/person/inbox")
        assert got.status_code == 200 and got.json()["needs"] == 1
        assert calls.count("list") == 1
        calls.clear()
        assert c.get("/inbox").status_code == 200
        assert calls.count("list") == 1


@pytest.mark.unit
def test_every_session_the_org_counts_as_needs_you_has_exactly_one_row(tmp_path, monkeypatch):
    """The Inbox says it counts *the session states the Org counts too*, so a `needs-you` record
    the Inbox does not list would be the two pages disagreeing in public. One predicate
    (`state_kind`) answers for both — including the edges: an empty `pending`, a `pending` that is
    not a dict at all, and a kind this build does not know. A plain *needs you* row carries
    **Open** and no Allow / Deny: nothing structured came with it, and a control built from what is
    not there is what §4.2 forbids (review of PR #251)."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import NEEDS_YOU_ROWS, state_kind, view

    records = [
        rec("ao-a", "needs-you", pending=PERMISSION),
        rec("ao-b", "needs-you", pending={"kind": "question", "text": "a or b?"}),
        rec("ao-c", "needs-you", pending={}),
        rec("ao-d", "needs-you"),
        rec("ao-e", "needs-you", pending="a string, from another build"),
        rec("ao-f", "needs-you", pending={"kind": "somethingnew", "text": "?"}),
        rec("ao-g", "needs-you", pending={"kind": "permission", "text": "no id came with it"}),
        rec("ao-h", "working"),
    ]
    vs = [view(r, records) for r in records]
    assert [state_kind(v) for v in vs] == [
        "permission", "question", "needs", "needs", "needs", "question", "question", "",
    ]  # fmt: skip
    org_counts = sum(1 for v in vs if state_kind(v) in NEEDS_YOU_ROWS)
    rows_out = state_rows_of(records)
    assert org_counts == sum(1 for v in vs if v["state"] == "needs-you") == 7
    assert len([r for r in rows_out if r["row"] in NEEDS_YOU_ROWS]) == org_counts
    assert sorted(r["sid"] for r in rows_out) == ["ao-a", "ao-b", "ao-c", "ao-d", "ao-e", "ao-f", "ao-g"]

    plain = rows("needs", [r for r in rows_out if r["row"] == "needs"][:1])
    assert ">Open<" in plain and 'data-act="allow"' not in plain and 'data-act="deny"' not in plain
    assert "nothing came with it saying what for" in plain
    # the one with a permission kind but no tool_use_id gets no Allow either: the route would 409
    no_id = rows("needs", [r for r in rows_out if r["sid"] == "ao-g"])
    assert 'data-act="allow"' not in no_id and "no id came with it" in no_id


# -- suggested answers (design §4.5a **Inbox row: suggested answers**, §4.10; TD-070 step 2) -------


@pytest.mark.unit
def test_the_answers_are_a_group_of_their_own_apart_from_the_rows_controls():
    """§4.5a **Inbox row: suggested answers**: one real `<button>` per answer, **drawn apart from
    the row's own controls** — a group of its own, labelled *suggested by <sender>*, each label in
    quotation marks — so a sender's chosen words never sit among the controls a person reads as
    the page's. A press carries the entry and the **index**, never the label."""
    html = rows("needs", [entry("m-1", "ask", text="merge PR 9?", answers=["merge it", "hold it"])])
    assert 'class="row gap wrap sugg"' in html and "suggested by w1" in html
    assert html.index('class="row gap wrap sugg"') < html.index('class="row gap wrap mfoot"')  # its own row, first
    for i, a in enumerate(("merge it", "hold it")):
        assert f'data-act="answer" data-id="person" data-msg="m-1" data-index="{i}"' in html
        assert f"&ldquo;{a}&rdquo;" in html  # in quotation marks, so the words read as the sender's
        assert f'title="{a}"' in html  # cut with an ellipsis in CSS, whole on hover
    assert "<button" in html and "<a " not in html.split('class="row gap wrap sugg"')[1].split("</div>")[0]
    css = (UI / "static" / "app.css").read_text()
    assert ".inboxpage .btn.answer .alabel { overflow: hidden; text-overflow: ellipsis;" in css
    assert "#" not in css.split(".inboxpage .sugg")[1].split("\n")[0]  # colours are tokens, never literals


@pytest.mark.unit
def test_a_steers_default_answer_is_marked_and_its_answers_sit_above_its_controls():
    """§4.5a: on a `steer` the answer that is the `default` **word for word** is marked *default* —
    pressing it is a reply like any other, and *Go with it* stays the person's separate act."""
    e = entry("m-2", "steer", default="off main", bound="2026-09-19T20:00:00Z", answers=["off main", "off develop"])
    html = rows("steering", [e])
    first = html.split('data-index="1"')[0]
    assert "default</span>" in first.split('data-index="0"')[1]  # marked on the one that matches
    assert html.split('data-index="1"')[1].split("</button>")[0].count("default") == 0
    # and a steer whose default is worded differently marks nothing
    other = rows("steering", [{**e, "default": "off  main"}])
    assert "adflt" not in other


@pytest.mark.unit
def test_answers_are_offered_exactly_where_reply_is_and_nowhere_else():
    """§4.10 *Suggested answers*: **buttons follow Reply exactly** — present wherever Reply is,
    absent wherever only Dismiss is. So a closed question in **FYI**, the snoozed list, a `system`
    note and a session's state row carry none, whatever their envelope says."""
    answers = ["merge it", "hold it"]
    for section, e in (
        ("fyi", entry("m-1", "ask", answers=answers, closed_reason="replied", closed_by="m-9")),
        ("fyi", entry("m-2", "steer", answers=answers, default="off main", closed_reason="lapsed")),
        ("fyi", entry("m-3", "note", answers=answers)),
        ("snoozed", entry("m-4", "ask", answers=answers, snoozed_until="2026-09-20T10:00:00Z")),
        ("needs", entry("m-5", "ask", answers=answers, from_="system", from_name="system")),
    ):
        html = rows(section, [e])
        assert 'data-act="answer"' not in html, (section, e["id"])
        assert "suggested by" not in html, (section, e["id"])
        # …and none of them offers Reply either, which is the rule they follow
        assert 'data-act="reply"' not in html, (section, e["id"])
    # where Reply is, they are: an open ask, a running steer, a paused one
    for section, e in (
        ("needs", entry("m-6", "ask", answers=answers)),
        ("steering", entry("m-7", "steer", answers=answers, default="off main")),
        ("needs", entry("m-8", "steer", answers=answers, default="off main", paused_at="2026-09-19T11:00:00Z")),
    ):
        html = rows(section, [e])
        assert 'data-act="reply"' in html and html.count('data-act="answer"') == 2, e["id"]


@pytest.mark.unit
def test_a_hostile_label_is_text_and_a_malformed_answers_field_shows_nothing():
    """TD-071 item 8: what a session sent is **text**, escaped by the template and never markup —
    the one control built from it is built from `answers`, a structured field, and the page must
    survive a record whose field is not what the agent writes (a non-list, an item that is not a
    string). `Cf` characters the agent strips never reach here; the escaping is what answers the
    rest."""
    html = rows("needs", [entry("m-1", "ask", answers=["<b>Delete</b>", 'say "yes"', "a & b"])])
    assert "<b>Delete</b>" not in html and "&lt;b&gt;Delete&lt;/b&gt;" in html
    assert 'say "yes"' not in html and "say &#34;yes&#34;" in html
    assert "a &amp; b" in html and html.count('data-act="answer"') == 3
    for junk in ("merge it", {"a": 1}, [{"pressable": True}], [""], ["ok", 7], None, 0):
        row = rows("needs", [entry("m-2", "ask", answers=junk)])
        assert "<div" in row and 'data-act="reply"' in row, junk  # the row is still a row
        assert row.count('data-act="answer"') == (1 if junk == ["ok", 7] else 0), junk
    # a record that somehow carries more than the bound shows the bound's worth
    many = rows("needs", [entry("m-3", "ask", answers=[f"a{i}" for i in range(9)])])
    assert many.count('data-act="answer"') == 4


@pytest.mark.integration
def test_a_press_sends_the_index_and_the_server_looks_up_what_it_means(client, tmp_path):
    """§4.5a and §4.10: the press POSTs the entry and the **index** to the same `/api/person/reply`
    the typed Reply uses; **the server looks the text up from the entry it holds**, so a tampered
    DOM cannot make the person say something else under a given index, and the RPC re-checks. The
    reply closes the question as `replied` and reaches the sender with its index."""
    sender = sender_session(client, tmp_path)
    ask = send(sender, text="merge PR 9?", kind="ask", answers=["merge it", "hold it"])["id"]
    steer = send(sender, text="which branch?", kind="steer", default="off main", answers=["off main", "off develop"])

    got = client.get("/api/person/inbox").json()
    assert got["html"]["needs"].count('data-act="answer"') == 2
    assert got["html"]["steering"].count('data-act="answer"') == 2

    r = client.post("/api/person/reply", json={"reply_to": ask, "answer": 1})
    assert r.status_code == 200 and r.json()["delivered"] == [sender]
    closed = [e for e in client.get("/api/person/inbox").json()["entries"] if e["id"] == ask][0]
    assert closed["closed_reason"] == "replied"
    # the sender holds the reply, with the answer the person pressed — its own text, not the DOM's
    held = asyncio.run(_inbox_of(sender))
    picked = [e for e in held if e["kind"] == "reply"][0]
    assert picked["text"] == "hold it" and picked["answer"] == 1

    # an index that is not one of them, of the wrong type, or on an entry carrying none: refused,
    # and nothing is sent — the row is still open
    for body in (
        {"reply_to": steer["id"], "answer": 9},
        {"reply_to": steer["id"], "answer": -1},
        {"reply_to": steer["id"], "answer": "1"},
        {"reply_to": steer["id"], "answer": True},
    ):
        assert client.post("/api/person/reply", json=body).status_code == 400, body
    still = [e for e in client.get("/api/person/inbox").json()["entries"] if e["id"] == steer["id"]][0]
    assert still["closed_reason"] is None
    # an absent index is the typed Reply, unchanged
    assert client.post("/api/person/reply", json={"reply_to": steer["id"], "text": "off develop"}).status_code == 200
    after = [e for e in asyncio.run(_inbox_of(sender)) if e["kind"] == "reply"]
    assert [e["answer"] for e in after] == [1, None]


async def _inbox_of(sid):
    from sessionorc.client import LocalClient

    async with LocalClient() as person:
        return (await person.call("inbox", id=sid))["entries"]


def test_a_press_folds_its_own_menu_and_never_the_section_it_sits_in():
    """Paul, 2026-09-20 (TD-079): dismissing an FYI entry closed the FYI list. The click handler
    closed the nearest `<details>` of any kind, and a control that sits in no menu has the *section*
    as its nearest one. It closes a `details.more` and nothing else — and every menu is one. The
    third kind is a message's *details* fold (TD-138), which holds text and never a control."""
    js = (UI / "static" / "app.js").read_text(encoding="utf-8")
    assert 'b.closest("details.more")' in js and 'b.closest("details")' not in js
    for tpl in (UI / "templates").glob("*.html"):
        for tag in re.findall(r"<details[^>]*>", tpl.read_text(encoding="utf-8")):
            menu = 'class="more"' in tag
            section = any(f'id="{k}"' in tag for k in ("sec-fyi", "sec-answered", "snoozedbox"))
            fold = 'class="fold"' in tag
            # …or a Focus side card (TD-156): a fold the Focus page remembers, never closed by a press
            side = "data-side=" in tag
            assert menu + section + fold + side == 1, f"{tpl.name}: {tag} is not a menu, a known section or a fold"


# -- design round 2: one centred column, sections as headings, rows as cards (TD-082) -------------


@pytest.mark.unit
def test_the_page_is_one_centred_column_and_a_section_is_a_heading_not_a_box(monkeypatch, tmp_path):
    """§4.5 screen 6 *Layout* (TD-082 findings 1–3): the page is one centred column of at most
    1100 px — so a message's text runs the width of its row instead of wrapping at a measure of its
    own inside an empty box — and a section is a heading, not a bordered card."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import inbox_sections, templates

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    sections = inbox_sections([entry("m-1", "ask")], now=now)
    html = templates.get_template("inbox.html").render(
        sections=sections, person_needs=sections["count"], host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    # a section is a heading: no `card pad` on any of the three, and no closing paragraph
    assert 'class="isec" id="sec-needs"' in html and 'class="card pad isec"' not in html
    assert "A state row is the session" not in html.split('id="sec-needs"')[0]  # not above the rows
    css = (UI / "static" / "app.css").read_text()
    col = next(ln for ln in css.splitlines() if ln.startswith(".inboxpage {"))
    assert "margin: 0 auto" in col
    wrap = next(ln for ln in css.splitlines() if ln.startswith(".inboxwrap {"))
    assert "minmax(0, 1100px)" in wrap  # the column keeps its 1100 px beside the rail (TD-135)
    body = next(ln for ln in css.splitlines() if ".inboxpage .mailrow .body" in ln)
    assert "max-width" not in body  # the column is the measure now, not the paragraph
    row = next(ln for ln in css.splitlines() if ln.startswith(".inboxpage .mailrow {"))
    assert "border:" in row and "background: var(--card)" in row  # a row is a card
    assert ".inboxpage .mailrow:focus-visible" in css  # …with a focus ring, because it is a tab stop
    assert 'class="mailrow" tabindex="0"' in rows("needs", [entry("m-1", "ask")])


@pytest.mark.unit
def test_each_section_keeps_its_blurb_behind_an_i_mark_that_a_screen_reader_can_hear(monkeypatch, tmp_path):
    """§4.5a **Inbox: section heading, the i mark**: the paragraph that said what a section is used
    to sit above every row for ever. It is now the **i** mark's — a `<button>`, so Enter and Space
    press it, carrying `aria-expanded` and `aria-controls`; its `title` is the same text as the
    paragraph it describes, and the paragraph is in the page **always**, merely `hidden`, because
    `aria-describedby` may point at a hidden node and never at a missing one."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import inbox_sections, templates

    now = datetime(2026, 9, 19, 12, tzinfo=UTC)
    sections = inbox_sections([entry("m-1", "ask")], now=now)
    html = templates.get_template("inbox.html").render(
        sections=sections, person_needs=sections["count"], host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    for sec, name in (("needs", "Needs you"), ("steering", "Steering"), ("fyi", "FYI")):
        assert f'aria-controls="info-{sec}" aria-expanded="false" aria-describedby="info-{sec}"' in html
        assert f'aria-label="About {name}"' in html
        assert f'<p class="note secinfo" id="info-{sec}" hidden>' in html
    # the same words twice: the button's hover and the paragraph it opens
    para = html.split('id="info-steering" hidden>')[1].split("</p>")[0]
    assert "Doing nothing is a valid answer" in para and f'title="{para}"' in html
    js = (UI / "static" / "app.js").read_text()
    assert "setupInfoMarks" in js and "stopPropagation" in js  # the i inside FYI's summary
    assert "store.set(key, on)" in js  # which are open is remembered in the browser
    # *a tooltip on hover **and keyboard focus***: `title` is the hover one, and because no browser
    # shows a `title` to a keyboard, focus floats the very paragraph instead — the same node, so
    # the tooltip and the description cannot drift apart (review of PR #281)
    assert 'b.matches(":focus-visible")' in js and 'para.classList.add("peek")' in js
    css = (UI / "static" / "app.css").read_text()
    peek = next(ln for ln in css.splitlines() if ".inboxpage .secinfo.peek[hidden]" in ln)
    assert "position: absolute" in peek and "display: block !important" in peek  # floats, never pushes


@pytest.mark.unit
def test_a_time_left_is_words_from_the_server_and_never_a_placeholder():
    """§4.5 screen 6 *Layout* (TD-082 finding 4): a duration needs no time zone, so the server
    renders it — a screenshot, a slow phone and a script error all read the number rather than the
    `…` the row showed until its script ran. The words are `fmtLeft`'s, so nothing jumps."""
    from agentorc.ui.app import _countdown, _left

    now = datetime(2026, 9, 20, 12, tzinfo=UTC)
    assert _left(iso(now + timedelta(minutes=21, seconds=5)), now) == "21m 05s"
    assert _left(iso(now + timedelta(hours=1, minutes=5)), now) == "1h 5m"
    assert _left(iso(now - timedelta(minutes=1)), now) == ""  # already past: the sentence says so
    assert _left("half six", now) == "" and _left(None, now) == ""  # the `_age` rule, unraised
    # a well-formed instant with **no offset** parses naive, and naive minus aware raises
    # `TypeError` — which no `except ValueError` catches. One record from another build would have
    # taken the page down rather than costing its row a line (review of PR #281). Read as UTC,
    # which is what every stamp in the store means.
    from agentorc.ui.app import _age

    assert _left("2026-09-20T13:00:00", now) == "1h 0m" and _age("2026-09-20T11:00:00", now) == "1h 0m"
    assert _left(12345, now) == "" and _age({"at": "x"}, now) == ""  # not even a string
    assert _countdown(iso(now + timedelta(minutes=3)), now) == "via hook · 3m 00s left"
    assert _countdown(iso(now - timedelta(minutes=3)), now) == "via hook · falling through to the terminal"

    html = rows("steering", [entry("m-2", "steer", default="off main", bound="x", left="21m 05s")])
    assert "21m 05s left — then it goes with its default" in html and "…" not in html
    lapsed = rows("steering", [entry("m-3", "steer", default="off main", bound="x", left="")])
    assert "the time is up: the sender goes with its default" in lapsed
    snoozed = rows("snoozed", [entry("m-4", "ask", snoozed_until="2026-09-21T09:00:00Z", until_words="21h 0m")])
    assert "snoozed until <span" in snoozed and ">in 21h 0m<" in snoozed and "…" not in snoozed
    js = (UI / "static" / "app.js").read_text()
    assert "function fmtLeft(iso)" in js and 'String(s % 60).padStart(2, "0")' in js


@pytest.mark.unit
def test_a_state_row_prints_the_session_name_once(tmp_path, monkeypatch):
    """§4.5 screen 6 *Layout* (TD-082 finding 4): on Paul's screenshot a session named `push` read
    `push  push` — its name, then the tool's own title, which happened to be the same word. A title
    that only repeats the name is left out; a title that says something else stays."""
    same = {"row": "question", "id": "s:question", "sid": "s", "name": "push", "title": "push",
            "text": "a or b?", "state_label": "needs you", "state_class": "needs", "at": "", "age": ""}  # fmt: skip
    assert rows("needs", [same]).count(">push<") == 1
    other = {**same, "title": "Error Checker"}
    html = rows("needs", [other])
    assert ">push<" in html and ">Error Checker<" in html


# -- the queue: outcomes, the trail, the second number, Dismiss all (TD-079 step 2) ---------------


def answered(mid, **kw):
    """A question the person answered, which therefore owes an outcome (§4.10 *Outcomes*)."""
    e = entry(mid, "ask", closed_reason=kw.pop("closed_reason", "replied"), closed_by="person",
              closed_at=kw.pop("closed_at", "2026-09-19T11:00:00Z"), **kw)  # fmt: skip
    return e


def sections_of(entries, *, states=(), trail=(), snoozed=None, fleet=None, now=None):
    """`inbox_sections` over entries the page has already annotated, as `person_inbox` does."""
    from agentorc.ui.app import _owing, inbox_sections

    at = now or datetime(2026, 9, 19, 12, tzinfo=UTC)
    records = {r["id"]: r for r in (fleet or [])}
    for e in entries:
        _owing(e, records.get(e["from"]), at)
    return inbox_sections(entries, now=at, states=states, trail=trail, attention_snoozed=snoozed)


@pytest.mark.unit
def test_an_answered_question_waits_on_them_and_is_in_neither_number():
    """§4.10 *Outcomes*: a question the person answered owes an outcome back. While its asker is
    live the row is under **Waiting on them** — never counted, because it waits on a session, not
    on the person — with the answer given and how long it has owed it."""
    e = answered("m-1", text="merge it?", answers=["merge it", "hold"], answer=0)
    got = sections_of([e], fleet=[{"id": "ao-w1", "state": "working"}])
    assert [x["id"] for x in got["waiting"]] == ["m-1"] and got["count"] == 0
    assert not got["needs"] and not got["fyi"]
    html = rows("waiting", got["waiting"])
    assert "you answered &ldquo;merge it&rdquo;" in html and "1h 0m ago" in html
    assert "waiting on w1 to report the outcome" in html
    assert 'data-act="dismiss" data-id="person" data-msg="m-1"' in html
    # a question still open owes nothing yet, and a declined or lapsed one never will (§4.10)
    assert sections_of([entry("m-2", "ask")])["waiting"] == []
    assert sections_of([answered("m-3", closed_reason="declined")])["waiting"] == []
    assert sections_of([answered("m-4", closed_reason="lapsed")])["waiting"] == []


@pytest.mark.unit
def test_a_blocked_outcome_and_an_asker_that_exited_are_counted_under_needs_you():
    """§4.10: *`blocked` is not a dead end* — work stopped on something only a person can move — and
    a debt whose asker **exited without reporting** is the same shape: only a person, or its
    manager, can find out what happened. Both are in *Needs you*, and both are counted."""
    blocked = answered("m-1", outcome={"state": "blocked", "text": "needs a token", "at": "2026-09-19T11:30:00Z"})
    gone = answered("m-2", from_="ao-w2", from_name="w2")
    got = sections_of([blocked, gone], fleet=[{"id": "ao-w1", "state": "working"}, {"id": "ao-w2", "state": "exited"}])
    assert sorted(x["id"] for x in got["needs"]) == ["m-1", "m-2"] and got["count"] == 2
    assert not got["waiting"] and not got["fyi"]
    html = rows("needs", got["needs"])
    assert "outcome · blocked" in html and "needs a token" in html
    assert "its asker exited without reporting" in html
    assert "blocked on something only a person can move" in html
    # neither is an `ask` row: an answered question is not declined with Delete or set aside
    assert 'data-act="unmail"' not in html and 'data-act="snooze"' not in html
    assert html.count('data-act="dismiss"') == 2


@pytest.mark.unit
def test_a_done_outcome_is_fyi_under_the_question_and_its_note_is_not_a_row_of_its_own():
    """§4.10: a `done` or `dropped` outcome is shown **under the question it closes** —
    *you said "merge it" → done: merged as #261* — and the reporting `note`, which is an ordinary
    entry in its own right, is listed under that question rather than beside it."""
    done = {"state": "done", "text": "merged as #261", "at": "2026-09-19T11:30:00Z", "by": "m-2"}
    q = answered("m-1", text="merge it?", answers=["merge it"], answer=0, outcome=done)
    note = entry("m-2", "note", text="done: merged as #261")
    got = sections_of([q, note], fleet=[{"id": "ao-w1", "state": "working"}])
    assert [x["id"] for x in got["fyi"]] == ["m-1"]  # the note is not a second row
    assert got["count"] == 0 and got["fyi_n"] == 1 and not got["waiting"]
    html = rows("fyi", got["fyi"])
    assert "you answered &ldquo;merge it&rdquo;" in html and "outcome · done" in html and "merged as #261" in html


@pytest.mark.unit
def test_the_trail_puts_a_row_that_resolved_without_you_into_fyi():
    """§4.10 *The Inbox is a queue*: a state row that went away by some other road — the session
    was resumed, the permission was answered in the terminal — does not simply vanish. The home
    writes the ending; the page draws it in FYI, and **Dismiss** is the only way it leaves."""
    t = {"id": "t-1", "sid": "ao-w1", "name": "w1", "team": "ao-grind", "kind": "permission",
         "text": "run git push?", "since": "2026-09-19T11:00:00Z", "resolved_at": "2026-09-19T11:30:00Z",
         "how": "answered in the terminal", "count": 2, "first": "2026-09-19T11:10:00Z",
         "last": "2026-09-19T11:30:00Z"}  # fmt: skip
    got = sections_of([], trail=[t])
    assert [x["id"] for x in got["fyi"]] == ["t-1"] and got["fyi_n"] == 1 and got["count"] == 0
    html = rows("fyi", got["fyi"])
    assert "resolved: answered in the terminal" in html and "&times;2" in html
    assert "it was a <b>permission</b> row, up for 30m" in html
    assert 'data-act="dismiss" data-id="person" data-msg="t-1"' in html
    assert 'data-kind="trail"' in html and 'class="badge kindmark k-trail"' in html  # a mark, not a control
    # a malformed trail entry costs its row, never the page (the `_age` rule)
    assert sections_of([], trail=["nonsense", {}, t])["fyi_n"] == 1


@pytest.mark.unit
def test_a_state_row_can_be_snoozed_and_a_snoozed_one_is_in_no_section_and_no_count():
    """§4.5a **Inbox row: state** / §4.10: `stalled?` and unpushed work are the two rows not on the
    tool's clock, so they are the two that may be set aside. The snooze is the home's, keyed on the
    record **and the row kind** — a session's permission and its stalled row are two rows."""
    stalled = {"row": "stalled", "id": "ao-w1:stalled", "sid": "ao-w1", "name": "w1", "text": "quiet for 40m",
               "state_label": "stalled?", "state_class": "stalled", "at": "2026-09-19T11:00:00Z"}  # fmt: skip
    perm = {"row": "permission", "id": "ao-w1:permission", "sid": "ao-w1", "name": "w1", "text": "run git push?",
            "state_label": "needs you", "state_class": "needs", "at": "2026-09-19T11:00:00Z", "age": "1h"}  # fmt: skip
    html = rows("needs", [stalled, perm])
    assert 'data-act="attention_snooze" data-id="person" data-sid="ao-w1" data-row="stalled"' in html
    assert 'data-row="permission" data-when' not in html  # never on the tool's own clock
    # snoozing the stalled row leaves the permission where it was: two rows, one key each
    got = sections_of([], states=[stalled, perm], snoozed={"ao-w1|stalled": "2026-09-19T15:00:00Z"})
    assert [x["id"] for x in got["needs"]] == ["ao-w1:permission"] and got["count"] == 1
    assert [x["id"] for x in got["snoozed"]] == ["ao-w1:stalled"]
    sn = rows("snoozed", got["snoozed"])
    assert "in 3h 0m" in sn and "the state itself is untouched" in sn
    assert 'data-act="attention_snooze" data-id="person" data-sid="ao-w1" data-row="stalled"' in sn
    assert "…" not in sn
    # a snooze whose time has passed is no snooze: the row is back, and nothing was lost
    back = sections_of([], states=[stalled], snoozed={"ao-w1|stalled": "2026-09-19T09:00:00Z"})
    assert [x["id"] for x in back["needs"]] == ["ao-w1:stalled"]


@pytest.mark.unit
def test_fyi_carries_its_own_quiet_number_which_is_never_added_to_the_first(monkeypatch, tmp_path):
    """§4.10: *Inbox 1 · 5* — the second number is FYI's entries and is **never** added to the
    first, which is what needs a person. The section opens itself when its count is higher than
    this browser last saw, which is how a folded, uncounted FYI stops hiding mail."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates

    got = sections_of([entry("m-1", "ask"), entry("m-2", "note"), entry("m-3", "note")])
    assert got["count"] == 1 and got["fyi_n"] == 2
    html = templates.get_template("inbox.html").render(
        sections=got, person_needs=got["count"], person_fyi=got["fyi_n"], host="kmaster", active="Inbox",
        agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert needs_line(html) == "1"
    assert 'id="personneeds">1</span>' in html and 'id="personfyi"' in html and ">· 2</span>" in html
    assert 'id="dismissall"' in html
    js = (UI / "static" / "app.js").read_text()
    assert 'store.get("inboxfyiseen", 0)' in js and 'store.set("inboxfyiseen", n)' in js
    assert 'fyi.textContent = m ? `· ${m}` : ""' in js  # the second number, its own chip
    assert "got.fyi_n !== null" in js  # not known is not zero, as for the first
    css = (UI / "static" / "app.css").read_text()
    assert "#personinbox .badge.fyin" in css and "border-color: transparent" in css  # a mark, never a control


@pytest.mark.unit
def test_an_fyi_entry_newer_than_this_browser_last_saw_is_marked_new():
    """§4.5 screen 6 *Layout* / §4.10: the same comparison that opens the section by itself also
    marks the entries that are why. It is the **browser's** own memory — nothing on the entry and
    nothing at the home changes, so reading still changes no row — and it is a mark, never a
    control: no border of its own, nothing to press."""
    html = rows("fyi", [entry("m-1", "note", at="2026-09-19T10:00:00Z")])
    assert 'data-at="2026-09-19T10:00:00Z"' in html  # what the comparison reads
    js = (UI / "static" / "app.js").read_text()
    assert 'store.get("inboxfyiseenat", "")' in js and 'store.set("inboxfyiseenat", newest)' in js
    assert 'r.classList.toggle("isnew", !!seen && !!at && at > seen)' in js  # never on a first visit
    css = (UI / "static" / "app.css").read_text()
    mark = next(ln for ln in css.splitlines() if ".mailrow.isnew .mhead::after" in ln)
    assert 'content: "new"' in mark and "cursor" not in mark and "border" not in mark


@pytest.mark.unit
def test_a_row_control_carries_its_border_and_the_hosts_alarm_row_has_no_team(tmp_path, monkeypatch):
    """Two things the live page at 1920 showed on 2026-09-20 that no test held. §4.5 screen 6
    *Layout* reads *marks are flat, controls are bordered* — but `.btn.ghost` is borderless
    site-wide, so on a row where every control is a ghost button nothing looked pressable. And the
    host's own alarm list is about **no record** (§4.8a), so *No team* on it said nothing."""
    css = (UI / "static" / "app.css").read_text()
    ctl = next(ln for ln in css.splitlines() if ".inboxpage .mailrow .btn.ghost" in ln)
    assert "border-color: var(--border)" in ctl
    host_row = {"row": "alarm_host", "id": "host:alarm", "sid": "", "name": "kmaster", "text": "",
                "alarms": [{"words": "a request named ao-w1", "at": "2026-09-19T11:00:00Z", "count": 1}],
                "mode": "observe", "at": "2026-09-19T11:00:00Z"}  # fmt: skip
    html = rows("needs", [host_row])
    assert "No team" not in html and 'class="badge team"' not in html
    assert "kmaster" in html and 'data-act="identity_ack"' in html  # the row itself is unchanged
    # every other state row keeps the badge, which is also its team filter
    mine = {**host_row, "row": "alarm", "sid": "ao-w1", "name": "w1", "id": "ao-w1:alarm"}
    assert 'class="badge team"' in rows("needs", [mine])


@pytest.mark.unit
def test_a_steer_the_person_let_go_owes_an_outcome_exactly_as_a_question_does():
    """§4.10 *Outcomes*: *a `go_with_it` close owes one too* — and `go_with_it` is a `steer`'s own
    close reason. The agent enforces the debt on `ASK_KINDS`, `steer` included (`MailEntry.owes`),
    so the page must read the same set: a narrower one left a `steer`'s debt in FYI, uncounted,
    while `ao progress none` was still refusing its sender (review of PR #287)."""
    from agentorc.ui.app import OWING_KINDS
    from sessionorc.models import ASK_KINDS

    assert set(OWING_KINDS) == set(ASK_KINDS)  # one rule, two readings of it
    gone = entry("m-1", "steer", default="off main", closed_reason="go_with_it",
                 closed_at="2026-09-19T11:00:00Z", closed_by="person")  # fmt: skip
    got = sections_of([gone], fleet=[{"id": "ao-w1", "state": "working"}])
    assert [x["id"] for x in got["waiting"]] == ["m-1"] and not got["fyi"] and got["count"] == 0
    assert "you let it go with its default" in rows("waiting", got["waiting"])
    # and the same steer whose asker has exited is counted, as an answered question's would be
    quiet = sections_of([dict(gone)], fleet=[{"id": "ao-w1", "state": "exited"}])
    assert [x["id"] for x in quiet["needs"]] == ["m-1"] and quiet["count"] == 1
    # a steer nobody answered owes nothing: it lapsed, and the sender took its default unasked
    assert sections_of([entry("m-2", "steer", closed_reason="lapsed")])["waiting"] == []


@pytest.mark.unit
def test_the_asker_doing_line_is_the_shape_every_other_surface_draws():
    """The fleet record the page reads is the raw one off `list`, whose `doing` is `{text, at}` —
    a row wants `{text, age}`, as the card and the Focus header draw it (§4.8, TD-074). Read the
    raw shape straight and the *says … ago* half of the line silently never renders."""
    e = answered("m-1")
    got = sections_of([e], fleet=[{"id": "ao-w1", "state": "working",
                                   "doing": {"text": "rebasing #269", "at": "2026-09-19T11:30:00Z"}}])  # fmt: skip
    html = rows("waiting", got["waiting"])
    assert "doing: rebasing #269 · says 30m ago" in html
    # no `doing`, no line — and a malformed one costs the line, never the page
    assert "doing:" not in rows("waiting", sections_of([answered("m-2")], fleet=[{"id": "ao-w1"}])["waiting"])
    assert "doing:" not in rows(
        "waiting", sections_of([answered("m-3")], fleet=[{"id": "ao-w1", "doing": 7}])["waiting"]
    )


@pytest.mark.unit
def test_a_person_act_refreshes_the_page_once():
    """Every control on this page carries `data-id="person"`, and the dispatcher already refreshes
    on that — a second, per-action refresh meant two `/inbox` fetches per press (review of #287)."""
    js = (UI / "static" / "app.js").read_text()
    assert '(staterow || id === "person") && typeof AO.refreshInboxPage === "function"' in js
    assert '["dismiss", "attention_snooze"].includes(action) && typeof AO.refreshInboxPage' not in js


# -- TD-081 step 2: Resume, Resume with changes…, Reopen and push ---------------------------------


@pytest.mark.unit
def test_a_one_press_resume_carries_the_record_and_never_its_unattended_flag(tmp_path, monkeypatch):
    """§4.5a **Focus (exited / closed)** → **Resume**: the record's own `name` above all — that is
    what makes the name check answer `supersede`, so the resumed session takes the bare name *and*
    the record's id, is replaced in place, and keeps its mail (§4.10, built by step 1).

    **Never `unattended`.** The session a press starts is attended, whatever the record was: an
    unattended session answers its own permission prompts, and a press with no form is no place to
    grant that. Nor `run_until`, `wrapup_prompt` or the old `prompt`."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import resume_create

    rec = {
        "id": "ao-w1", "name": "tdgrind-ao-2", "dir": str(tmp_path), "adapter": "claude-code",
        "profile": "grind", "role": "grinder", "team": "ao-grind", "project": "agentorc",
        "lane": ["TD-073"], "controllers": ["ao-lead"], "adapter_id": "u-1", "state": "exited",
        "unattended": True, "run_until": "2026-09-20T06:00:00Z", "wrapup_prompt": "wrap up",
        "prompt": "the old brief", "capabilities": ["control"],
    }  # fmt: skip
    # a repo whose `grinder` preset grants nothing — so what the record carried cannot survive
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {lane: [TD-9]}}\n")
    got = resume_create(rec)
    assert got["name"] == "tdgrind-ao-2" and got["resume"] == "u-1"
    assert got["team"] == "ao-grind" and got["project"] == "agentorc" and got["controllers"] == ["ao-lead"]
    assert got["lane"] == ["TD-073"] and got["dir"] == str(tmp_path)
    assert got["unattended"] is False  # the whole point of the rule
    assert "run_until" not in got and "wrapup_prompt" not in got and "prompt" not in got
    # the grants come from the **role preset**, not off the old record: the record carried
    # `control` and this preset grants nothing, so a copy would show and an empty list is the proof
    assert got["capabilities"] == []
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {grants: [control], lane: [TD-9]}}\n")
    assert resume_create(rec)["capabilities"] == ["control"]  # …and one it grants is granted
    # the record's own lane wins over the preset's; a record with none takes the preset's
    assert resume_create({**rec, "lane": []})["lane"] == ["TD-9"]
    # a record of a node's creates **on that node** — without this the session lands on the home,
    # under the name that should have superseded the node's record
    assert "host" not in got  # this record is the local host's: a parameter never set is never sent
    assert resume_create({**rec, "host": "worker1"})["host"] == "worker1"
    # *Reopen and push* is the same create with a first prompt **the page wrote**
    from agentorc.ui.app import REOPEN_AND_PUSH

    assert resume_create(rec, prompt=REOPEN_AND_PUSH)["prompt"] == REOPEN_AND_PUSH
    assert "push" in REOPEN_AND_PUSH.lower() and "--outcome" in REOPEN_AND_PUSH


@pytest.mark.unit
def test_a_resume_of_a_worktree_record_checks_the_name_in_the_records_scope(tmp_path, monkeypatch):
    """TD-145 (2026-09-24): a techlead in `<repo>/.claude/worktrees/techlead-ao-1` was resumed with
    `dir` alone, so the scope became the worktree's basename — `ao-techlead-ao-1-techlead-ao-1`,
    collapsed to `ao-techlead-ao-1`, an id nobody held — and a second record appeared beside
    `ao-agentorc-techlead-ao-1` instead of replacing it. The create must carry the record's `repo`,
    so the id the name check computes is the record's own; and *Resume with changes…* must land on
    the form the way the form's own Start would send it back: Where = new worktree, its name, and
    the repo in the directory field."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import resume_create, resume_form_url
    from sessionorc import naming

    repo = tmp_path / "agentorc"
    wt = repo / ".claude" / "worktrees" / "techlead-ao-1"
    wt.mkdir(parents=True)
    rec = {
        "id": "ao-agentorc-techlead-ao-1", "name": "techlead-ao-1", "dir": str(wt), "repo": str(repo),
        "worktree": "techlead-ao-1", "adapter": "claude-code", "profile": "grind-fable", "role": "techlead",
        "team": "ao-grind", "project": "agentorc", "lane": [], "controllers": ["ao-agentorc-manager-ao-1"],
        "adapter_id": "u-1", "state": "exited", "unattended": True,
    }  # fmt: skip
    got = resume_create(rec)
    assert got["repo"] == str(repo) and got["dir"] == str(wt)
    # the proof: the id the agent's name check would compute is the record's own
    assert naming.base_id(got["dir"], got["repo"], got["name"]) == rec["id"]
    assert naming.base_id(got["dir"], None, got["name"]) == "ao-techlead-ao-1"  # what it was without `repo`
    # a record with no repo sends none (§4.4: a parameter never set is never sent)
    assert "repo" not in resume_create({**rec, "repo": None, "dir": str(tmp_path)})
    # Resume with changes…: the form's fields, the way its Start sends them back
    url = resume_form_url(rec)
    q = dict(x.split("=", 1) for x in url.removeprefix("/new?").split("&"))
    from urllib.parse import unquote_plus

    q = {k: unquote_plus(v) for k, v in q.items()}
    assert q["dir"] == str(repo) and q["where"] == "worktree" and q["worktree"] == "techlead-ao-1"
    assert "repo" not in q  # the form has no repo field; it derives it from Where
    plain = resume_form_url({**rec, "repo": None, "dir": str(tmp_path)})
    assert "where=" not in plain and "worktree=" not in plain
    # `ao new --repo` scopes a directory by a repo without agentorc's worktree flow (`worktree` unset):
    # the form keeps that directory — keying on `dir != repo` would have moved it (review of #543)
    outside = {**rec, "worktree": None, "dir": str(tmp_path / "elsewhere")}
    q = {
        k: unquote_plus(v)
        for k, v in (x.split("=", 1) for x in resume_form_url(outside).removeprefix("/new?").split("&"))
    }
    assert q["dir"] == str(tmp_path / "elsewhere") and "where" not in q and "worktree" not in q
    assert resume_create(outside)["repo"] == str(repo)  # the one-press path still scopes by the repo


@pytest.mark.unit
def test_when_a_resume_cannot_be_silent_it_is_the_filled_in_form_and_not_a_guess(tmp_path):
    """§4.5a: *when it cannot be silent it is not a guess*. What the record itself settles is
    answered before anything is created; everything else is the agent's own refusal, and either
    way the answer is the same — the filled-in form, with the reason on it."""
    from agentorc.ui.app import resume_blocked, resume_form_url

    live = {"state": "working", "name": "w", "dir": "/tmp", "adapter_id": "u-1"}
    assert "still running" in resume_blocked(live)
    shell = {"state": "exited", "name": "w", "dir": "/tmp", "adapter_id": ""}
    assert "no tool session id" in resume_blocked(shell)
    nameless = {"state": "exited", "name": "", "dir": "/tmp", "adapter_id": "u-1"}
    assert "no name or no directory" in resume_blocked(nameless)
    ok = {"state": "closed", "name": "w", "dir": "/tmp", "adapter_id": "u-1"}
    assert resume_blocked(ok) == ""  # a `closed` record resumes too (§4.5a)
    url = resume_form_url({**ok, "role": "grinder", "lane": ["TD-1"], "unattended": True}, "the directory is gone")
    assert url.startswith("/new?") and "name=w" in url and "resume=u-1" in url and "role=grinder" in url
    assert "lane=TD-1" in url and "unattended=on" in url and "why=the+directory+is+gone" in url
    # the form is told it **is** a prefill, which is what lets an empty controllers list mean
    # *this record had none* rather than *nothing was said* (review of PR #290)
    assert "prefilled=1" in url
    # and it carries only what the form takes: `capabilities` is not carried at all (§4.5a), and
    # `ledger` and `host` are the create's, derived on the form from its own Role and host
    assert "capabilities=" not in url and "ledger=" not in url and "host=" not in url


@pytest.mark.unit
def test_the_form_says_which_control_was_pressed_and_fills_in_what_it_knew(tmp_path, monkeypatch):
    """**Resume with changes…** is the same create as a form; a press that could not be silent
    lands on the same page with its reason, so a person is never left wondering which of the two
    controls they pressed or what stopped it."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import templates

    def page(prefill):
        return templates.get_template("new.html").render(
            host="kmaster", active="Org", profiles={}, default_profile="default", recent=[],
            adapters=["claude-code"], control_holders=[{"id": "ao-lead", "name": "lead"}], grants_all=[],
            grant_notes={}, roles=[{"name": "plain", "source": "built-in", "lane": [], "controllers": [],
                                    "grants": []},
                                   {"name": "grinder", "source": "built-in", "lane": [], "controllers": [],
                                    "grants": []}],
            default_controllers=[], projects=[], prefill=prefill,
        )  # fmt: skip

    filled = page({"dir": "/repo", "adapter": "claude-code", "resume": "u-1", "project": "", "name": "w1",
                   "profile": "", "role": "grinder", "team": "", "lane": "TD-1", "controllers": ["ao-lead"],
                   "unattended": True, "prefilled": True, "why": "the directory is gone"})  # fmt: skip
    assert 'name="name" value="w1"' in filled and 'value="u-1"' in filled and 'name="lane" value="TD-1"' in filled
    assert 'value="grinder" selected' in filled and 'name="unattended" checked' in filled
    assert 'value="ao-lead" data-name="lead" checked' in filled
    assert 'id="resumewhy"' in filled and "the directory is gone" in filled
    # and an ordinary New session is untouched: no banner, nothing prefilled, `plain` selected
    plain = page({"dir": "", "adapter": "claude-code", "resume": "", "project": "", "name": "", "profile": "",
                  "role": "", "team": "", "lane": "", "controllers": [], "unattended": False,
                  "prefilled": False, "why": ""})  # fmt: skip
    assert 'id="resumewhy"' not in plain and 'name="name" value=""' in plain
    assert 'value="plain" selected' in plain and 'name="unattended" checked' not in plain

    # **a record that had no controllers keeps none.** An ordinary New session takes the repo's
    # default; a form filled in from a record with an empty list must not re-tick it, or a person
    # who does not notice Starts a session with a controller the record never had (review of #290)
    def ctl(prefill):
        return templates.get_template("new.html").render(
            host="kmaster", active="Org", profiles={}, default_profile="default", recent=[],
            adapters=["claude-code"], control_holders=[{"id": "ao-lead", "name": "lead"}], grants_all=[],
            grant_notes={}, roles=[{"name": "plain", "source": "built-in", "lane": [], "controllers": [],
                                    "grants": []}],
            default_controllers=["ao-lead"], projects=[],
            prefill={"dir": "", "adapter": "claude-code", "resume": "", "project": "", "name": "",
                     "profile": "", "role": "", "team": "", "lane": "", "why": "", **prefill},
        )  # fmt: skip

    assert 'value="ao-lead" data-name="lead" checked' in ctl({"controllers": [], "prefilled": False})
    assert 'value="ao-lead" data-name="lead" checked' not in ctl({"controllers": [], "prefilled": True})
    assert 'value="ao-lead" data-name="lead" checked' in ctl({"controllers": ["ao-lead"], "prefilled": True})


@pytest.mark.unit
def test_the_unpushed_row_can_be_answered_from_the_row():
    """§4.5a **Inbox row: state** (TD-081): *exited with unpushed work* offered only **Details**,
    which is not an answer to it. It now offers **Reopen and push** — the one-press Resume with a
    first prompt the **page** wrote, never anything a session said — and **Resume** beside it."""
    row = {"row": "unpushed", "id": "ao-w1:unpushed", "sid": "ao-w1", "name": "w1",
           "text": "3 unpushed vs origin/w1", "state_label": "exited", "state_class": "exited",
           "at": "2026-09-19T11:00:00Z"}  # fmt: skip
    html = rows("needs", [row])
    assert 'data-act="reopen-push" data-id="ao-w1"' in html and 'data-act="resume" data-id="ao-w1"' in html
    assert "data-confirm=" in html and "under its own name" in html
    assert "Details" in html  # what was there stays
    js = (UI / "static" / "app.js").read_text()
    assert 'act(id, "resume", { push: action === "reopen-push" })' in js
    assert 'data-act="resume-form"' in js  # the Focus banner's second control
    assert "Resume this conversation" not in js  # the link that carried no name is gone


@pytest.mark.integration
def test_a_one_press_resume_takes_the_name_back_and_the_old_record_keeps_its_place(client, tmp_path):
    """The whole of TD-081: on 2026-09-20 resuming `orchestrator-ao-1` meant typing a name, and
    what was typed became a second record beside the one being resumed. One press now, and the
    record is replaced in place — same id, same name (§4.10 *a resume under the same name*)."""
    r = client.post("/new", data={"name": "w1", "dir": str(tmp_path), "adapter": "shell"}, follow_redirects=False)
    sid = r.headers["location"].rsplit("/", 1)[-1]
    # a shell holds no tool session id, so the press is the form, with its reason — never a guess
    client.post(f"/api/sessions/{sid}/kill")
    got = client.post(f"/api/sessions/{sid}/resume", json={}).json()
    assert got["ok"] is False and "no tool session id" in got["why"] and got["form"].startswith("/new?")
    assert "name=w1" in got["form"]
    # and **Resume with changes…** is the same form, asked for, with nothing created
    form = client.post(f"/api/sessions/{sid}/resume", json={"form": True}).json()
    assert form["ok"] is True and "id" not in form and "name=w1" in form["form"]
    # nothing was created by either press. Counted in this test's own directory, never over the
    # whole fleet: the module's other tests leave records behind and a bare count would pass or
    # fail on the order they ran in.
    here = [x for x in client.get("/api/sessions").json() if x["dir"] == str(tmp_path)]
    assert [x["id"] for x in here] == [sid]


# -- TD-077 (a2), the page side: the suspended mark, and the New session verdict ------------------


@pytest.mark.unit
def test_the_suspended_mark_is_drawn_wherever_the_record_is_and_is_never_a_control(tmp_path, monkeypatch):
    """§4.8a *An alarm's answers*: a suspension **ends no row and so writes no trail**, which makes
    this mark its only record on a page — if it is not drawn, nothing says it happened. So it is
    drawn wherever the record is (the card, the Focus header, the Inbox's state row), it is flat
    and never pressable, and its words carry the when, the who and the why on hover.

    The two ways out are a person's **Resume** and **Forget**, which exist already — which is why
    there is no Unsuspend anywhere here, and why this is a mark rather than a control."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import suspended_note, templates, view

    got = suspended_note({"at": "2026-09-20T22:00:00Z", "by": "person", "why": "claimed to be ao-y"})
    assert "suspended at 2026-09-20T22:00:00Z by person" in got and "claimed to be ao-y" in got
    assert "only a person lifts it" in got and "resuming it or forgetting it" in got
    # tolerant, like every derived chip: a record from another build costs its card a mark, not the grid
    assert suspended_note(None) == "" and suspended_note("nonsense") == "" and suspended_note([]) == ""
    assert "no detail recorded" in suspended_note({})
    assert "no reason recorded" in suspended_note({"at": "x"})

    base = {"id": "ao-x", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "exited", "since": "2026-09-19T16:00:00Z", "confidence": "hook", "pane": False,
            "tail": ["…"], "created": "2026-09-19T15:00:00Z"}  # fmt: skip
    assert view(base)["suspended_note"] == ""
    v = view({**base, "suspended": {"at": "2026-09-20T22:00:00Z", "by": "person", "why": "claimed to be ao-y"}})
    assert v["suspended_note"]

    card = templates.get_template("card.html").render(s=v)
    focus = templates.get_template("focus.html").render(s={**v, "grants_all": [], "ready": []}, host="h", active="Org")
    row = rows("needs", [{"row": "alarm", "id": "ao-x:alarm", "sid": "ao-x", "name": "w", "alarms": [],
                          "mode": "enforce", "at": "", "suspended_note": v["suspended_note"]}])  # fmt: skip
    for where, html in (("card", card), ("focus", focus), ("inbox row", row)):
        assert "badge suspendedmark" in html, where
        assert ">suspended<" in html, where
        assert "data-act" not in html.split("suspendedmark")[1].split("</span>")[0], where  # never a control
    # …and on Focus it **rides the pushed delta**, because that page re-renders its header in place
    # rather than being replaced whole like a card: a person watching the very session that is
    # suspended from somewhere else would otherwise see the state change and not the mark, and the
    # mark is the suspension's only record (review of PR #306). Always drawn, hidden until true.
    assert 'id="fsuspended"' in focus and "hidden" not in focus.split('id="fsuspended"')[0][-60:]
    plain = templates.get_template("focus.html").render(
        s={**view(base), "grants_all": [], "ready": []}, host="h", active="Org"
    )
    assert 'id="fsuspended"' in plain and "suspendedmark hidden" in plain  # in the page, not shown
    js = (UI / "static" / "app.js").read_text()
    assert 'susp.classList.toggle("hidden", !v.suspended_note)' in js
    css = (UI / "static" / "app.css").read_text()
    mark = next(ln for ln in css.splitlines() if ln.startswith(".badge.suspendedmark"))
    assert "cursor: default" in mark  # flat: nothing to press, and nothing that looks pressable
    js = (UI / "static" / "app.js").read_text()
    assert "Unsuspend" not in js and "unsuspend" not in js  # Resume and Forget are the way out


@pytest.mark.unit
def test_the_new_session_form_says_a_suspended_name_is_a_lift_and_leaves_start_enabled():
    """§4.8a: **a person's create is the lift**, so Start stays enabled where a `live` holder
    disables it, and the form says what pressing it does. The agent's own sentence is printed as it
    wrote it — the why, the when and the two ways out are all inside it, and a page that recomposed
    them from parts is how two surfaces come to say different things about one record."""
    js = (UI / "static" / "app.js").read_text()
    block = js.split('if (o.verdict === "suspended")')[1].split("} else")[0]
    assert "esc(o.message)" in block  # the agent's sentence, escaped, never rebuilt from fields
    assert "lifts the suspension" in block and "Look at it first" in block
    assert "start.disabled" not in block  # only `live` disables Start; this one is a person's act
    assert 'start.disabled = o.verdict === "live";' in js  # …and that rule is untouched


@pytest.mark.unit
def test_suspend_is_offered_only_where_there_is_something_to_stop_and_leaves_the_row_standing():
    """§4.8a *An alarm's answers*: **Suspend** stops the session at once — no wrap-up, because a
    session under suspicion is not asked to tidy — and is *a person's own act*, refused to every
    session by the agent for `identity_ack`'s reason turned around: one that could suspend could
    stop its rival.

    Three rules the row has to get right, and each is a case below: it is **not** on the host's own
    list, which is about no record; it is **not** on a record that is already suspended or already
    stopped, where the mark would have no act behind it; and it **leaves the row standing**,
    because it acts on the session and does not answer the alarm."""
    live = {"row": "alarm", "id": "ao-x:alarm", "sid": "ao-x", "name": "x", "state": "working",
            "alarms": [{"words": "a claim", "at": "2026-09-19T10:00:00Z", "count": 1}],
            "mode": "enforce", "at": "2026-09-19T10:00:00Z"}  # fmt: skip
    html = rows("needs", [live])
    assert 'data-act="suspend" data-id="person" data-who="ao-x"' in html
    assert "It is stopped at once, with no wrap-up" in html and "only you lift it" in html
    assert "data-confirm=" in html  # a person's act, and not one to mis-press

    ctl = 'data-act="suspend"'  # the control itself — `suspended`, the mark, is a different word
    # the host's own list is about no record: nothing to stop
    assert ctl not in rows("needs", [{**live, "row": "alarm_host", "sid": "", "name": "kmaster"}])
    # already suspended: the mark is there, and a second suspend would be a mark with no act
    marked = rows("needs", [{**live, "suspended_note": "suspended by a person"}])
    assert ctl not in marked and "badge suspendedmark" in marked  # the mark, not the control
    # already stopped: §4.8a — Forget it, or resume it, which is what lifts a suspension anyway
    for dead in ("exited", "closed"):
        assert ctl not in rows("needs", [{**live, "state": dead}]), dead

    js = (UI / "static" / "app.js").read_text()
    # it is the one act on this page that does **not** take its row away
    assert 'if (staterow && action !== "suspend") { AO.handRing(staterow); staterow.remove(); }' in js
    assert 'if (action === "suspend") body = { id: b.dataset.who || "" };' in js
    assert "why:" not in js.split('action === "suspend"')[1][:200]  # the agent composes the reason


# -- TD-029: the terminal's two client rules, which had no harness until there was one ------------


TERM_PROBE = """
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
global.location = { pathname: "/focus/ao-x", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const gone = window.AO.paneIsGone, close = window.AO.termClose, wheel = window.AO.isWheel;
console.log(JSON.stringify({
  wheel: ["\x1b[<64;5;5M", "\x1b[<65;1;1M\x1b[<65;1;1M", "\x1b[<81;3;3M", "\x1b[<0;5;5M", "\x1b[<96;5;5M",
          "a", "\x1b[<64;5;5Mx", ""].map(wheel),
  gone_closed: gone({state: "closed", pane: true}),
  gone_pane_false: gone({state: "working", pane: false}),
  gone_working: gone({state: "working", pane: true}),
  gone_exited_with_pane: gone({state: "exited", pane: true}),
  gone_nothing: gone(null),
  final_4404: close(4404, true, 500),
  first_1006_before_open: close(1006, false, 500),
  first_1006_after_open: close(1006, true, 500),
  normal_1000: close(1000, true, 500),
  reason_kept: close(1011, true, 500, "server went away"),
  reason_absent: close(1011, true, 500, ""),
  reason_on_1006_after_open: close(1006, true, 500, "odd but carried"),
  reason_on_1006_before_open: close(1006, false, 500, "should not be shown"),
  backoff: [500, 1000, 2000, 4000, 8000, 16000].map((d) => close(1000, true, d).delay),
}));
"""


@pytest.mark.unit
def test_the_terminals_two_client_rules_are_reachable_and_right():
    """TD-029. Paul pressed **Close** on a Focus page on 2026-09-10 and the terminal reconnected
    twice a second for sixteen seconds, printing tmux's *can't find session* until he pressed
    Forget. Three hardenings landed in PR #71; two of them are **JavaScript**, and the entry has
    said ever since that they cannot be tested *because it is JavaScript, which this repo has no
    harness for*. That is no longer true — `tests/test_ui_inbox.py` has run a node probe over
    `app.js` since TD-069 — so what was missing was not a harness but a rule a test could reach.

    Both are pure now, for the reason `maySwapSection` is: **a rule inside a closure is a rule no
    test can call.** What remains of TD-029 is what a browser alone can show, and it is narrower
    for this.

    The rule that mattered: *a connection the server accepts and then ends is not a working
    terminal*, so the backoff **doubles** there and resets only on the first byte of pane output.
    Resetting on open is what made it retry twice a second for ever."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rules are JavaScript, and nothing else runs them")
    probe = pathlib.Path(tempfile.mkdtemp()) / "term_probe.js"
    probe.write_text(TERM_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    # (a) the pane is gone for good — the two shapes a record says it with, and the three it does not
    assert got["gone_closed"] is True and got["gone_pane_false"] is True
    assert got["gone_working"] is False and got["gone_nothing"] is False
    # an `exited` session that still has its pane is *not* gone: its last screen and run log are
    # kept on purpose (TD-023), and ending the terminal there would throw them away
    assert got["gone_exited_with_pane"] is False

    # (c) 4404 is the server saying the pane is gone: final, and never retried
    assert got["final_4404"] == {"retry": False, "final": True, "delay": 500,
                                 "why": "no terminal for this session"}  # fmt: skip
    # everything else retries — and **the delay doubles**, which is the whole fix: it must not
    # reset here, because the socket opening is not evidence that a terminal is there
    assert got["normal_1000"]["retry"] is True and got["normal_1000"]["final"] is False
    assert got["normal_1000"]["delay"] == 1000
    assert got["backoff"] == [1000, 2000, 4000, 8000, 10000, 10000]  # doubling, to a 10s ceiling
    # a 1006 before the socket ever opened is a handshake that never reached the server, which is a
    # different thing to tell a person than a server that closed on them
    before = got["first_1006_before_open"]["why"]
    assert "handshake failed" in before and "ssh -L" in before
    assert "handshake" not in got["first_1006_after_open"]["why"] and "1006" in got["first_1006_after_open"]["why"]
    # the close frame's own `reason` is **part of the sentence, so part of the rule** — the caller
    # appending it separately is how the two came apart in review: the call site suppressed it for
    # every 1006 where the original suppressed it only for one the socket never opened. Nothing can
    # reach that difference today (a 1006 is client-synthesised and carries none, and this server
    # sends none), which is why it is closed here rather than left as a comment.
    assert got["reason_kept"]["why"] == "closed (code 1011, server went away)"
    assert got["reason_absent"]["why"] == "closed (code 1011)"
    assert "odd but carried" in got["reason_on_1006_after_open"]["why"]  # as the original did
    assert "should not be shown" not in got["reason_on_1006_before_open"]["why"]  # as it did not

    # …and the page really uses them, rather than keeping a second copy of the rule inline
    js = (UI / "static" / "app.js").read_text()
    assert "AO.termClose(e.code, opened, delay, e.reason)" in js and "AO.paneIsGone(ev.session)" in js
    assert "e.code !== 1006" not in js  # the reason rule lives in `termClose`, not beside it
    # the one place the backoff resets: pane output, after the attach's own read-only word (TD-096),
    # which is not output and must not reset it
    assert js.count("delay = 500; term.write(") == 1
    assert js.index('"read_only" in c') < js.index("delay = 500; term.write(")

    # TD-096: the one frame a read-only attach passes is the wheel — buttons 64/65 with any modifier
    # bits, as the server's `WHEEL_ONLY` has it; a click, wheel-with-motion, a key or a mix is not
    assert got["wheel"] == [True, True, True, False, False, False, False, False]
    assert "ws.onopen = () => { delay = 500; }" not in js.split("AO.focus")[-1]  # never on open


# -- design §4.5a **Inbox row: answered for you** (§4.9b; TD-075 step 2, the page half) -----------


def answered_fyi(mid, **kw):
    """The FYI the home files when a teammate answers from the record: a `note` from the answerer
    carrying `answered: {question, asker, answerer, source}` (§4.9b)."""
    a = {
        "question": "does step 3 wait for step 2?",
        "asker": "ao-w1",
        "answerer": "ao-tl",
        "source": "TD-075 build order",
    }
    a.update(kw.pop("answered", {}))
    kw.setdefault("text", "yes, it builds on it")
    kw.setdefault("asker_name", "w1")
    kw.setdefault("asker_open", "ao-w1")
    return entry(mid, "note", from_="ao-tl", from_name="tl", from_open="ao-tl", answered=a, **kw)


@pytest.mark.unit
def test_answered_for_you_is_its_own_section_uncounted_newest_first_and_apart_from_fyi():
    """§4.9b / §4.5a: an entry carrying `answered` is under *Answered for you* — never in FYI with
    the notes, never in either number — newest first. A plain note stays FYI, and a snoozed one is
    snoozed like anything else."""
    from agentorc.ui.app import INBOX_SECTIONS

    assert INBOX_SECTIONS.index("waiting") < INBOX_SECTIONS.index("answered") < INBOX_SECTIONS.index("fyi")
    old = answered_fyi("m-1", at="2026-09-19T09:00:00Z")
    new = answered_fyi("m-2", at="2026-09-19T11:00:00Z")
    note = entry("m-3", "note")
    later = answered_fyi("m-4", snoozed_until="2026-09-20T08:00:00Z")
    got = sections_of([old, new, note, later])
    assert [e["id"] for e in got["answered"]] == ["m-2", "m-1"]
    assert [e["id"] for e in got["fyi"]] == ["m-3"] and got["fyi_n"] == 1
    assert [e["id"] for e in got["snoozed"]] == ["m-4"]
    assert got["count"] == 0 and not got["needs"]
    # a malformed `answered` costs the row its group, never the page: it reads as a plain note
    assert [e["id"] for e in sections_of([entry("m-5", "note", answered="yes")])["fyi"]] == ["m-5"]
    assert [e["id"] for e in sections_of([entry("m-6", "note", answered={})])["fyi"]] == ["m-6"]


@pytest.mark.unit
def test_an_answered_row_says_who_asked_what_who_answered_and_where_and_offers_overrule_and_dismiss():
    """§4.5a **Inbox row: answered for you**: the question, the answer, the source, who asked and
    who answered — all text. **Overrule** is a Reply to this entry that the compose names **the
    asker** for, since the home sends it there (copied to the answerer); **Dismiss** ends the row."""
    html = rows("answered", [answered_fyi("m-1", team="ao-grind", about="TD-075")])
    assert "answered for you" in html and "re TD-075" in html
    assert "w1</span> asked" in html and "does step 3 wait for step 2?" in html
    assert "tl answered" in html and "yes, it builds on it" in html
    assert "source: TD-075 build order" in html
    assert 'href="/focus/ao-w1"' in html and 'href="/focus/ao-tl"' in html
    over = re.search(r"<button[^>]*>Overrule</button>", html).group(0)
    assert 'data-act="reply" data-id="person" data-msg="m-1"' in over
    assert 'data-name="w1"' in over and 'data-quote="does step 3 wait for step 2?"' in over
    assert 'data-act="dismiss" data-id="person" data-msg="m-1"' in html
    # nothing else a reply-able row offers: no plain Reply to the answerer, no Snooze, no answers
    assert ">Reply<" not in html and 'data-act="snooze"' not in html and 'data-act="answer"' not in html
    # the filter finds a row by the asker, the question and the source too
    find = re.search(r'data-find="([^"]*)"', html).group(1)
    assert "w1" in find and "does step 3" in find and "td-075 build order" in find


@pytest.mark.unit
def test_overrule_is_drawn_only_on_an_answered_entry_and_everything_in_it_is_text():
    """§4.5a: *neither is offered on anything but this kind* — it keys on the structured field, not
    on who sent the entry; a note from the same answerer is an ordinary FYI row. And every field a
    session wrote — the question, the answer, the source — is escaped text, never markup."""
    got = sections_of([entry("m-1", "note", from_="ao-tl", from_name="tl", text="nothing answered")])
    assert "Overrule" not in rows("fyi", got["fyi"])
    hostile = answered_fyi(
        "m-2",
        text="<b>merge</b>",
        answered={"question": "<script>x()</script>", "source": '"><img src=x>'},
    )
    html = rows("answered", [hostile])
    assert "<script>" not in html and "<b>merge</b>" not in html and "<img" not in html
    assert "&lt;script&gt;x()&lt;/script&gt;" in html and "&lt;b&gt;merge&lt;/b&gt;" in html


@pytest.mark.unit
def test_the_page_draws_the_group_only_when_it_holds_something_and_the_poll_swaps_it():
    """The group is a fold whose heading is its disclosure, drawn only when it has rows (as *Waiting
    on them* is); the poll swaps its rows and hides it again when it empties."""
    tpl = (UI / "templates" / "inbox.html").read_text()
    assert 'id="sec-answered"' in tpl and 'id="rows-answered"' in tpl and "imark('answered'" in tpl
    js = (UI / "static" / "app.js").read_text()
    assert '"answered"' in js.split("const IN_SECS")[1].split("\n")[0]
    assert '$("#sec-answered")' in js and "got.sections.answered" in js


@pytest.mark.integration
def test_an_answer_from_the_record_is_a_row_and_overrule_reaches_the_asker(client, tmp_path):
    """End to end (§4.9b): a teammate's reply with `source` lands under *Answered for you* on the
    page, naming the asker; **Overrule** — the same `/api/person/reply` Reply uses — reaches the
    asker, copied to the answerer; **Dismiss** ends the row."""
    from sessionorc.client import LocalClient

    async def setup():
        async with LocalClient() as person:
            ids = []
            for n in ("askr", "answr"):
                got = await person.call(
                    "create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="ao-t"
                )
                ids.append(got["id"])
        w, tl = ids
        async with LocalClient(caller=w) as wc, LocalClient(caller=tl) as tc:
            q = (await wc.call("msg", to=tl, text="does step 3 wait?", kind="ask"))["entry"]
            r = await tc.call("msg", reply_to=q["id"], kind="reply", text="yes", source="design §4.9b")
        return w, tl, r["answered_for_you"]

    w, tl, fyi = asyncio.run(setup())
    got = client.get("/api/person/inbox").json()
    assert got["sections"]["answered"] == [fyi] and fyi not in got["sections"]["fyi"] and got["needs"] == 0
    html = got["html"]["answered"]
    assert "askr</span> asked" in html and "source: design §4.9b" in html and ">Overrule</button>" in html
    assert 'id="sec-answered"' in client.get("/inbox").text

    r = client.post("/api/person/reply", json={"reply_to": fyi, "text": "no — step 3 can start now"})
    assert r.status_code == 200 and r.json()["delivered"] == [w]
    held = asyncio.run(_inbox_of(w))
    assert [e["text"] for e in held if e["kind"] == "reply" and e["from"] == "person"] == ["no — step 3 can start now"]
    assert any(e["text"] == "no — step 3 can start now" for e in asyncio.run(_inbox_of(tl)))  # the copy

    assert client.post("/api/person/dismiss", json={"msg": [fyi]}).status_code == 200
    assert fyi not in client.get("/api/person/inbox").json()["sections"]["answered"]


# -- design §4.5a **Inbox row: passed up** (§4.9b; TD-075 step 3, the page half) ------------------


def passed_up(mid, kind="ask", **kw):
    """The asker's own question in the person inbox, passed up with the passer's recommendation and
    its suggested answers, the recommendation first (§4.9b)."""
    kw.setdefault("text", "rename the flag?")
    kw.setdefault("recommend", {"by": "ao-tl", "text": "keep the old name"})
    kw.setdefault("passer_name", "tl")
    kw.setdefault("answers", ["keep the old name", "rename it"])
    return entry(mid, kind, passed_up="2026-09-19T10:30:00Z", **kw)


@pytest.mark.unit
def test_a_passed_up_question_is_the_askers_row_with_the_recommendation_drawn_as_text():
    """§4.5a **Inbox row: passed up**: the asker's question under its own heading — an `ask` in
    *Needs you*, counted; a `steer` in *Steering* with its time left — with *`<passer>` recommends:
    `<line>`* **labelled and drawn as text**, and the passer's suggested answers as the row's
    answer buttons, the recommendation first, labelled as the passer's. Reply goes to the asker."""
    ask = passed_up("m-1")
    steer = passed_up("m-2", "steer", default="rename it", bound="2026-09-19T12:30:00Z")
    got = sections_of([ask, steer])
    assert [e["id"] for e in got["needs"]] == ["m-1"] and got["count"] == 1
    assert [e["id"] for e in got["steering"]] == ["m-2"]
    for sec, e in (("needs", ask), ("steering", steer)):
        html = rows(sec, [e])
        assert "tl recommends: keep the old name" in html
        rec = re.search(r'<div class="st dflt recommends"[^>]*>', html).group(0)
        assert "<button" not in rec and "data-act" not in rec  # text, never a control
        assert "suggested by tl" in html and "suggested by w1" not in html
        assert html.index("&ldquo;keep the old name&rdquo;") < html.index("&ldquo;rename it&rdquo;")
        assert 'data-act="reply" data-id="person" data-msg="' + e["id"] + '" data-name="w1"' in html  # to the asker
        assert html.index("recommends:") < html.index('class="row gap wrap sugg"')


@pytest.mark.unit
def test_a_question_not_passed_up_has_no_recommendation_and_its_answers_are_the_senders():
    """The line keys on `passed_up` and a structured `recommend` together; an ordinary question —
    or one whose `recommend` is malformed — draws neither the line nor another name on its answers,
    and a hostile recommendation is text."""
    plain = rows("needs", [entry("m-1", "ask", answers=["yes", "no"])])
    assert "recommends" not in plain and "suggested by w1" in plain
    odd = rows("needs", [entry("m-2", "ask", passed_up="2026-09-19T10:30:00Z", recommend="keep it")])
    assert "recommends" not in odd
    hostile = passed_up("m-3", recommend={"by": "ao-tl", "text": "<b>keep</b>"})
    html = rows("needs", [hostile])
    assert "<b>keep</b>" not in html and "tl recommends: &lt;b&gt;keep&lt;/b&gt;" in html


@pytest.mark.integration
def test_a_question_passed_up_reaches_the_page_as_the_askers_with_the_passers_recommendation(client, tmp_path):
    """End to end (§4.9b): `pass_up` puts the asker's own question in the person inbox; the page draws
    it in *Needs you* from the asker, with the passer's recommendation and answers; the first
    answer press is the recommendation and reaches the asker."""
    from sessionorc.client import LocalClient

    async def setup():
        async with LocalClient() as person:
            ids = []
            for n in ("askr2", "passr"):
                got = await person.call(
                    "create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="ao-t2"
                )
                ids.append(got["id"])
        w, tl = ids
        async with LocalClient(caller=w) as wc, LocalClient(caller=tl) as tc:
            q = (await wc.call("msg", to=tl, text="rename the flag?", kind="ask"))["entry"]
            await tc.call("pass_up", id=q["id"], recommend="keep the old name", answers=["rename it"])
        return w, q["id"]

    w, qid = asyncio.run(setup())
    got = client.get("/api/person/inbox").json()
    assert got["sections"]["needs"] == [qid] and got["needs"] == 1
    html = got["html"]["needs"]
    assert "askr2</span>" in html and "passr recommends: keep the old name" in html and "suggested by passr" in html
    r = client.post("/api/person/reply", json={"reply_to": qid, "answer": 0})
    assert r.status_code == 200 and r.json()["delivered"] == [w]
    picked = [e for e in asyncio.run(_inbox_of(w)) if e["kind"] == "reply"]
    assert [(e["text"], e["answer"]) for e in picked] == [("keep the old name", 0)]


# -- design §4.5a **team header** → *answered for you* count (§4.9b; TD-075, the page half) --------

ANSWERED_PROBE = (
    TERM_PROBE.split("const gone = ")[0]
    + """
const count = window.AO.answeredCounts;
const marks = [
  {team: "ao-grind", at: "2026-09-21T10:00:00Z"},
  {team: "ao-grind", at: "2026-09-21T12:00:00Z"},
  {team: "", at: "2026-09-21T12:30:00Z"},
  {team: "other", at: "2026-09-21T09:00:00Z"},
];
console.log(JSON.stringify({
  never: count(marks, ""),
  since: count(marks, "2026-09-21T11:00:00Z"),
  all_seen: count(marks, "2026-09-21T13:00:00Z"),
  none: count(null, ""),
}));
"""
)


@pytest.mark.unit
def test_the_answered_count_is_what_arrived_since_the_group_was_last_opened_per_team():
    """§4.5a **team header**: *the number of answered-for-you entries from this team's sessions
    since the person last opened that group*. The rule is pure (`AO.answeredCounts`), for the
    reason `termClose` is: a rule inside a closure is a rule no test can call. Never opened, every
    row counts; opened, only what is newer; per team, and a row with no team under ``."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "answered_probe.js"
    probe.write_text(ANSWERED_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["never"] == {"ao-grind": 2, "": 1, "other": 1}
    assert got["since"] == {"ao-grind": 1, "": 1}
    assert got["all_seen"] == {} and got["none"] == {}


@pytest.mark.unit
def test_the_team_header_carries_the_answered_mark_as_a_mark_and_the_pages_fill_it():
    """The header renders an empty, hidden `<span>` per team — never a button or a link — and the
    poll fills it; the Inbox page records *last opened* only while the group is open and in view,
    and the Org reads the poll at load so the mark does not wait a whole interval."""
    from agentorc.ui.app import templates

    head = templates.get_template("group_head.html")
    html = head.render(g={"team": "ao-grind", "label": "ao-grind", "members": [], "live": 1})
    mark = re.search(r'<span class="meta answeredmark hidden" data-answered-team="ao-grind"[^>]*></span>', html)
    assert mark and "<button" not in mark.group(0) and "href" not in mark.group(0)
    assert "data-answered-team" not in head.render(g={"team": "", "label": "", "members": [], "live": 1})
    js = (UI / "static" / "app.js").read_text()
    assert "syncAnsweredMarks();" in js.split("function syncTeams()")[1].split("\n  }\n")[0]
    seen = js.split("function markAnsweredSeen()")[1].split("\n  }\n")[0]
    assert "!sec.open" in seen and 'document.visibilityState === "hidden"' in seen
    assert 'if (location.pathname !== "/inbox") AO.refreshInboxCount();' in js


@pytest.mark.integration
def test_the_poll_carries_each_answered_rows_team_and_time_and_nothing_it_says(client, tmp_path):
    """`/api/person/inbox` gives `answered_marks` — the team and time of each *Answered for you*
    row, and not its text: the browser counts, and the home keeps no read state for it."""
    from sessionorc.client import LocalClient

    async def setup():
        async with LocalClient() as person:
            ids = []
            for n in ("askr3", "answr3"):
                got = await person.call(
                    "create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], team="ao-t3"
                )
                ids.append(got["id"])
        w, tl = ids
        async with LocalClient(caller=w) as wc, LocalClient(caller=tl) as tc:
            q = (await wc.call("msg", to=tl, text="is it written down?", kind="ask"))["entry"]
            r = await tc.call("msg", reply_to=q["id"], kind="reply", text="yes", source="design §4.9b")
        return r["answered_for_you"]

    fyi = asyncio.run(setup())
    got = client.get("/api/person/inbox").json()
    at = [e["at"] for e in got["entries"] if e["id"] == fyi][0]
    assert got["answered_marks"] == [{"team": "ao-t3", "at": at}]
    assert 'data-answered-team="ao-t3"' in client.get("/").text  # the team's header carries the mark


# -- TD-117: Deny with a reason (design §4.5a) ---------------------------------------------------


DENY_PROBE = """
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
global.location = { pathname: "/inbox", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const box = (id, value) => {
  const i = { dataset: { id }, value, focused: false };
  i.focus = () => { i.focused = true; };
  return i;
};
const typed = box("ao-a", "use the fixture instead"), empty = box("ao-b", ""), mid = box("ao-c", "");
document.activeElement = mid;  // the person is in a box they have not typed in yet
const kept = AO.denyWhys(el({ querySelectorAll: () => [typed, empty, mid] }));
const a = box("ao-a", ""), b = box("ao-b", ""), c = box("ao-c", ""), d = box("ao-d", "");
AO.restoreDenyWhys(el({ querySelectorAll: () => [a, b, c, d] }), kept);
console.log(JSON.stringify({
  typed: AO.denyBody("  use the fixture instead  "),
  blank: AO.denyBody("   "),
  none: AO.denyBody(undefined),
  kept,
  after: [a, b, c, d].map((i) => [i.value, i.focused]),
}));
"""


@pytest.mark.unit
def test_deny_carries_an_optional_reason_from_every_place_it_is_offered(tmp_path, monkeypatch):
    """§4.5a **Allow / Deny** (TD-117): beside every Deny — the card, the Focus header, the Inbox's
    permission row — one optional line the session reads with the refusal. Never required: an empty
    box is a bare Deny, and the body then carries no `reason` at all. The box survives the redraws a
    pushed delta or a poll makes, so a half-typed reason is not taken from under the person. That
    the reason reaches the hook's decision is `test_ui.py`'s permission round trip."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    card = templates.get_template("card.html").render(s=view(rec("ao-p", "needs-you", pending=PERMISSION)))
    (row,) = state_rows_of([rec("ao-p", "needs-you", pending=PERMISSION)])
    perm = rows("needs", [row])
    for html in (card, perm):
        deny = html.index('data-act="deny" data-id="ao-p"')
        assert html.index('<input class="denywhy"', deny) - deny < 200, "the box sits beside Deny"
        assert 'class="denywhy" type="text" maxlength="200" data-id="ao-p"' in html and "optional" in html
    # no box where there is no Deny: a permission without a hook id, and a plain question
    for pending in ({**PERMISSION, "tool_use_id": None}, {"kind": "question", "text": "which branch?"}):
        html = templates.get_template("card.html").render(s=view(rec("ao-q", "needs-you", pending=pending)))
        assert "denywhy" not in html

    js = (UI / "static" / "app.js").read_text()
    # the Focus header draws the same box, and every press and every redraw goes through the helpers
    assert 'data-act="deny" data-id="${id}">Deny</button> <input class="denywhy"' in js
    assert "body = AO.denyBody(w && w.value)" in js
    # the card's delta, the Focus header, the Inbox poll, a team summary's Answer needed facet (TD-176)
    assert js.count("AO.restoreDenyWhys(") == 4

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "deny_probe.js"
    probe.write_text(DENY_PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["typed"] == {"reason": "use the fixture instead"}
    assert got["blank"] == {} and got["none"] == {}
    # what was typed, and the box the person was in, come back; an untouched box stays untouched
    assert got["kept"] == {"ao-a": {"value": "use the fixture instead", "focused": False},
                           "ao-c": {"value": "", "focused": True}}  # fmt: skip
    assert got["after"] == [["use the fixture instead", False], ["", False], ["", True], ["", False]]


# -- the rail (design §4.5 screen 6 *The rail* and *Find*; TD-129, built by TD-135) ---------------


def _rail_fixture():
    """Two teams' mail and states in every section — the case TD-135's *Done when* is written for."""
    from agentorc.ui.app import inbox_sections

    es = [
        entry("m-1", "ask", team="grind", text="merge #517?", from_name="jeffrey"),
        entry("m-2", "ask", team="cm", text="which branch?"),
        entry("m-3", "steer", team="grind", text="off main", default="off main", bound="2026-09-20T09:00:00Z"),
        entry("m-4", "note", team="grind", text="merged #517, one gap"),
        entry("m-5", "note", team="cm", text="a note"),
        entry("m-6", "note", team=None, text="from the home", from_="system", from_name="system"),
    ]
    states = [{"row": "permission", "id": "ao-x:permission", "sid": "ao-x", "name": "w9", "team": "cm",
               "find": "w9 run pytest?", "at": "2026-09-19T09:00:00Z", "text": "run pytest?"}]  # fmt: skip
    return inbox_sections(es, states=states, now=datetime(2026, 9, 19, 12, tzinfo=UTC))


@pytest.mark.unit
def test_every_row_kind_has_its_rail_kind():
    """§4.5 screen 6 *The rail*: *questions*, *steering*, *session states*, *board items*, *notes*,
    *trail* — one per row, from the row's own fields."""
    from agentorc.ui.app import rail_kind

    assert rail_kind({"kind": "ask"}) == rail_kind({"kind": "conflict"}) == "questions"
    assert rail_kind({"kind": "note", "passed_up": "ao-t"}) == "questions"
    assert rail_kind({"kind": "steer"}) == "steering"
    for k in ("note", "reply"):
        assert rail_kind({"kind": k}) == "notes"
    assert rail_kind({"row": "permission"}) == rail_kind({"row": "alarm_host"}) == "states"
    assert rail_kind({"row": "board"}) == "board" and rail_kind({"row": "trail"}) == "trail"


@pytest.mark.unit
def test_the_picks_come_from_the_url_and_unknown_lines_are_dropped():
    from agentorc.ui.app import rail_picks

    got = rail_picks({"team": "grind,none", "sec": "needs,bogus", "kind": "questions", "find": "  jeff  "})
    assert got == {"team": ["grind", "none"], "sec": ["needs"], "kind": ["questions"], "find": "jeff"}
    assert rail_picks({}) == {"team": [], "sec": [], "kind": [], "find": ""}


@pytest.mark.unit
def test_the_rail_counts_rows_on_the_page_under_the_picks():
    """TD-135 *Done when* (1) and (3): one team pressed shows its rows in every section and the team
    line reads what it needs from the person; a second team adds its rows; *FYI* alone shows both
    teams' FYI; an unpicked team reads 0 of n; every count reads *n of all*; nothing picked is the
    whole, as a plain number."""
    from agentorc.ui.app import rail_counts, rail_picks, rail_rows

    rows = rail_rows(_rail_fixture())
    whole = rail_counts(rows, rail_picks({}))
    assert not whole["filtered"] and whole["team_order"] == ["cm", "grind", "none"]
    assert {t: c["all"] for t, c in whole["teams"].items()} == {"cm": 2, "grind": 1, "none": 0}
    assert whole["sections"]["needs"] == {"shown": 3, "all": 3} and whole["sections"]["fyi"] == {"shown": 3, "all": 3}
    one = rail_counts(rows, rail_picks({"team": "grind"}))
    assert one["filtered"] and one["teams"]["grind"] == {"shown": 1, "all": 1}
    assert one["teams"]["cm"] == {"shown": 0, "all": 2}  # unpicked in a group with a pick: 0 of n
    assert one["heads"]["needs"] == {"shown": 1, "all": 3} and one["heads"]["fyi"] == {"shown": 1, "all": 3}
    assert one["sections"]["steering"] == {"shown": 1, "all": 1}  # its share, under the team pick
    two = rail_counts(rows, rail_picks({"team": "grind,cm"}))
    assert two["heads"]["needs"] == {"shown": 3, "all": 3}
    fyi = rail_counts(rows, rail_picks({"sec": "fyi"}))
    assert fyi["heads"]["fyi"] == {"shown": 3, "all": 3} and fyi["sections"]["needs"] == {"shown": 0, "all": 3}
    assert fyi["kinds"]["notes"] == {"shown": 3, "all": 3} and fyi["kinds"]["questions"] == {"shown": 0, "all": 2}
    for r in (one, two, fyi):  # no count ever exceeds the rows on the page
        for group in ("sections", "teams", "kinds", "heads"):
            assert all(c["shown"] <= c["all"] for c in r[group].values())


@pytest.mark.unit
def test_the_find_matches_every_word_in_any_order_and_numbers_with_or_without_their_mark():
    """TD-135 *Done when* (4): two words from a body in the other order, `#517`, `517,`, and *jeff*
    finding *jeffrey*; a team pressed and a word typed shows only that team's matching rows, and
    the team counts change with the word."""
    from agentorc.ui.app import rail_counts, rail_picks, rail_rows

    rows = rail_rows(_rail_fixture())

    def shown(q):
        return sum(c["shown"] for c in rail_counts(rows, rail_picks(q))["heads"].values())

    assert shown({"find": "#517"}) == shown({"find": "517,"}) == shown({"find": "517"}) == 2
    assert shown({"find": "gap merged"}) == 1  # the other order
    assert shown({"find": "jeff"}) == 1  # a substring: *jeffrey*
    both = rail_counts(rows, rail_picks({"team": "grind", "find": "517"}))
    assert both["heads"]["needs"]["shown"] == 1 and both["heads"]["fyi"]["shown"] == 1
    assert rail_counts(rows, rail_picks({"find": "branch"}))["teams"]["grind"]["shown"] == 0


@pytest.mark.unit
def test_the_page_draws_the_rail_pressed_from_the_url(monkeypatch, tmp_path):
    """§4.5 screen 6: the picks are the page's URL, so the first paint is the filtered page — its
    lines pressed, **Clear filters** drawn, the counts *n of all*, the find box holding the words —
    and the title row is *Inbox* alone."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import rail_counts, rail_picks, rail_rows, templates

    sections = _rail_fixture()
    picks = rail_picks({"team": "grind", "find": "517"})
    html = templates.get_template("inbox.html").render(
        sections=sections, picks=picks, rail=rail_counts(rail_rows(sections), picks), host="kmaster",
        active="Inbox", agent_down=False, volatile=False, usage={},
    )  # fmt: skip
    assert 'data-group="team" data-value="grind" aria-pressed="true"' in html
    assert 'data-group="team" data-value="cm" aria-pressed="false"' in html
    assert 'id="railclear"' in html and 'class="btn sm railclear hidden"' not in html
    assert 'value="517"' in html and 'id="needspill"' not in html and "team:name" not in html
    assert '<span id="n-needs">1 of 3</span>' in html.replace('class="meta" ', "")


RAIL_PROBE = """
const fs = require("fs");
const noop = () => {};
const el = (o) => Object.assign({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false }, o);
const document = { documentElement: el(), body: el(), activeElement: null,
  querySelector: () => null, querySelectorAll: () => [], addEventListener: noop, createElement: () => el() };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/inbox", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO, cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify({
  counts: cases.picks.map((q) => AO.railCounts(cases.rows, AO.railPicks(q))),
  queries: cases.picks.map((q) => AO.railQuery(AO.railPicks(q))),
  words: AO.findWords("  Merge, #517 (jeff) "),
}));
"""


@pytest.mark.unit
def test_the_script_counts_the_rail_as_the_server_does():
    """`AO.railCounts` is `rail_counts` again, over the rows in the DOM (the page recounts on every
    press and every poll): the same rows and picks give the same counts, the picks survive their
    own URL, and the find's words are cut the same way."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    from agentorc.ui.app import find_words, rail_counts, rail_picks, rail_rows

    rows = rail_rows(_rail_fixture())
    queries = ["", "?team=grind", "?team=grind,cm&sec=needs", "?sec=fyi&kind=notes", "?team=none&find=home",
               "?kind=questions&find=517%2C", "?team=gone"]  # fmt: skip
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "probe.js").write_text(RAIL_PROBE)
    (tmp / "cases.json").write_text(json.dumps({"rows": rows, "picks": queries}))
    out = subprocess.run(
        [node, str(tmp / "probe.js"), str(UI / "static" / "app.js"), str(tmp / "cases.json")],
        capture_output=True, text=True, timeout=30,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    from urllib.parse import parse_qsl

    for q, js, query in zip(queries, got["counts"], got["queries"], strict=True):
        picks = rail_picks(dict(parse_qsl(q.lstrip("?"))))
        assert js == rail_counts(rows, picks), q
        assert rail_picks(dict(parse_qsl(query.lstrip("?")))) == picks, (q, query)  # round trip
    assert got["words"] == find_words("  Merge, #517 (jeff) ") == ["merge", "#517", "jeff"]


@pytest.mark.unit
def test_below_720_the_rail_is_a_chip_row_and_a_sheet_holding_the_same_toggles(monkeypatch, tmp_path):
    """§4.5 screen 6 *Narrow* (TD-137): the page carries a chip row — **Filters ▾** first, the team
    chips after it — and a native `<dialog>` the rail itself moves into, so the chips and the sheet
    are the rail's toggles and never a second list; below 720 px the rail beside the column is not
    drawn, and a row's controls are 44 px high."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates

    html = templates.get_template("inbox.html").render(
        sections=_rail_fixture(), host="kmaster", active="Inbox", agent_down=False, volatile=False, usage={}
    )
    chips, sheet = html.index('id="railchips"'), html.index('<dialog class="railsheet" id="railsheet"')
    assert chips < sheet < html.index('id="rail"')  # the sheet is empty until the rail moves in
    assert 'id="railsheetbtn" aria-haspopup="dialog"' in html and 'id="railsheetdone"' in html
    assert html.count('data-group="team"') == 3  # the rail's own team lines, once: the chips are the script's
    js = (UI / "static" / "app.js").read_text()
    assert '$("#railsheetbody").appendChild(railEl)' in js and "home.insertBefore(railEl" in js
    css = (UI / "static" / "app.css").read_text()
    narrow = css[css.index("@media (max-width: 720px)") :]
    assert ".inboxwrap > .rail { display: none; }" in narrow and ".railchips { display: flex;" in narrow
    assert ".inboxpage .mailrow .btn, .inboxpage .mailrow input.denywhy { height: 44px; }" in narrow


# -- the message page (design §4.5 screen 6 *The message page*; TD-129, built by TD-136) ----------


def test_the_message_page_draws_the_entry_its_answer_and_its_thread(client, tmp_path):
    """TD-136 *Done when* (1): a 2,000-character `ask` reads whole on its page, its answer controls
    directly under it (the row's own, so Reply is the list's Reply), then its thread, oldest first,
    with the person's own reply in it — which the person inbox does not hold. **Back** carries the
    list's picks and returns to this row; nothing on the page marks anything read."""
    sid = sender_session(client, tmp_path)
    long = ("Which branch should the fix land on? " * 60).strip()
    ask = send(sid, text=f"merge #9?\n\n{long}", kind="ask", about="TD-136")
    open_page = client.get(f"/inbox/{ask['id']}?back=%3Fteam%3Dgrind")
    assert open_page.status_code == 200
    html = open_page.text
    assert 'id="msgback" href="/inbox?team=grind#' + ask["id"] + '"' in html
    entry = html.split('class="msgentry"')[1].split('class="isec msgthread"')[0]
    body = entry.split('<div class="body md">', 1)[1].split("</details>")[0]
    assert "<p>merge #9?</p>" in body and body.count("Which branch should the fix land on?") == 60  # whole
    assert f'data-fold="{ask["id"]}" open' in entry  # *details* open: a page is for reading
    assert 'data-act="reply"' in entry and 'data-act="unmail"' in entry  # the answer, under the entry
    assert "whole entry ›" not in entry  # the page does not link to itself
    assert "Nothing else in its thread." in html
    # the person answers; the question closes, and its page now carries the reply in its thread
    assert client.post("/api/person/reply", json={"reply_to": ask["id"], "text": "off main, please"}).status_code == 200
    html = client.get(f"/inbox/{ask['id']}").text
    thread = html.split('class="isec msgthread"')[1]
    assert "off main, please" in thread and ">person<" in thread and "data-act=" not in thread  # read, never pressed


def test_the_message_page_of_a_gone_entry_and_of_another_hosts_reads_words(client):
    """TD-136 *Done when* (3): an entry the person inbox no longer holds, and one another host holds,
    read their words — never a blank page or a 500."""
    gone = client.get("/inbox/m-000000000000")
    assert gone.status_code == 200 and "gone: m-000000000000 is no longer in the person inbox" in gone.text
    elsewhere = client.get("/inbox/m-000000000000@far")
    assert elsewhere.status_code == 200 and 'class="note msggone"' in elsewhere.text


@pytest.mark.unit
def test_a_mail_row_links_its_page_and_a_state_or_board_row_does_not():
    """TD-136 *Done when* (4): a mail row's text and its *whole entry ›* reach its page (`Enter` on
    the ringed row presses the link); a permission row and a board row are unchanged — their
    **Open** and **Open board** stay theirs."""
    mail = rows("needs", [entry("m-1", "ask", text="merge it?")])
    assert 'data-page="/inbox/m-1"' in mail and '<a class="pagelink st" href="/inbox/m-1"' in mail
    state = rows("needs", state_rows_of([rec("ao-p", "needs-you", pending={"kind": "permission", "text": "x"})]))
    assert "data-page=" not in state and "pagelink" not in state
