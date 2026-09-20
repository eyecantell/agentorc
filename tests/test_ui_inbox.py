"""The Inbox page (design §4.5 screen 6, §4.5a **Inbox page** and the three **Inbox row** rows,
§4.10; TD-069 step 1): the sections and their order, the count that means *what is waiting on a
person*, the row controls and the routes behind them, the team filter's data, and what the page
must never do to a message a session wrote — parse it, or mark it read by looking at it.

The pure half (`inbox_sections` and the templates) is unit; the routes run against a live host
agent through the sync `TestClient`, as the rest of the UI's tests do.
"""

from __future__ import annotations

import asyncio
import pathlib
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


def rows(section, es):
    from agentorc.ui.app import templates

    return templates.get_template("inbox_rows.html").render(rows=es, section=section)


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
def test_a_snoozed_row_offers_unsnooze_which_clears_it():
    html = rows("snoozed", [entry("m-1", "ask", snoozed_until="2026-09-19T18:00:00Z")])
    assert 'data-act="unsnooze" data-id="person" data-msg="m-1"' in html
    assert "snoozed until 2026-09-19T18:00:00Z" in html
    assert 'data-act="snooze"' not in html


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
    assert 'data-find="w1 merge it? td-069"' in html  # lowercased once, on the server


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
    assert '<span id="needsn">1</span> needs you' in html  # the snoozed ask is not in it
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
    assert page.status_code == 200 and '<span id="needsn">1</span> needs you' in page.text
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
def test_go_with_it_closes_a_steer_now_and_the_row_moves_to_fyi(client, tmp_path):
    """§4.5a **Inbox row: `steer`** → **Go with it**: closes it `go_with_it`, the sender is told and
    need not wait out the bound; doing nothing would have lapsed to the same end."""
    sender = sender_session(client, tmp_path)
    steer = send(sender, text="branching", kind="steer", default="off main")["id"]
    r = client.post("/api/person/gowithit", json={"msg": steer})
    assert r.status_code == 200 and r.json()["closed_reason"] == "go_with_it"
    got = client.get("/api/person/inbox").json()
    assert got["sections"]["fyi"] == [steer] and got["needs"] == 0
    assert "closed · go with it" in got["html"]["fyi"]
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
