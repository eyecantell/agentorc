"""TD-218 slice 1, design §4.10 *An entry handed to a seat* and §4.9b `asks_waiting`: a handed entry
counts toward the seat it is addressed to while it owes its outcome, read or not, and not while a
question of the seat's own to the person on its thread is open; a person's answer on that thread
reads *fills this seat* to a seat on call, and the tick fills it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks

from sessionorc import mail, modes
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import PERSON, MailEntry, Session

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _seat(state: str = "exited") -> Session:
    return Session(
        id="ao-tl", name="tl", kind="interactive", adapter="claude-code", dir="/r", state=state,
        unattended=True, seat={"trigger": "asks"},
    )  # fmt: skip


def _handed(mid: str = "m-entry", to: str = "ao-tl") -> MailEntry:
    e = MailEntry(id=mid, from_=PERSON, to=[to], at="2026-09-29T11:00:00Z", kind="ask", text="the parser drops …")
    e.handed, e.entry = True, {"repo": "agentorc", "type": "debt"}
    return e


@pytest.mark.unit
def test_a_handed_entry_counts_while_it_owes_read_or_not():
    s = _seat()
    e = _handed()
    s.inbox.append(e)
    assert e.handed_entry and s.asks_waiting() == 1
    e.read_at = "2026-09-29T11:05:00Z"
    assert s.asks_waiting() == 1, "read is not settled: it owes its outcome"
    e.closed_reason, e.closed_at = "replied", "2026-09-29T11:10:00Z"
    assert s.asks_waiting() == 1, "a reply does not settle a handed entry"
    e.outcome = {"state": "done", "text": "TD-240, PR #800", "at": "t", "by": "m-x"}
    assert s.asks_waiting() == 0


@pytest.mark.unit
def test_a_question_of_the_seats_own_on_its_thread_stops_the_count_until_answered():
    s = _seat()
    s.inbox.append(_handed())
    q = MailEntry(id="m-q", from_="ao-tl", to=[PERSON], at="2026-09-29T11:20:00Z", kind="ask", text="which repo?")
    q.root = "m-entry"
    s.outbox.append(q)
    assert s.asks_waiting() == 0, "the seat waits on the person"
    other = MailEntry(id="m-o", from_="ao-tl", to=[PERSON], at="2026-09-29T11:21:00Z", kind="ask", text="unrelated")
    s.outbox.append(other)
    assert s.asks_waiting() == 0
    q.closed_reason = "replied"
    assert s.asks_waiting() == 1, "the person's answer counts it again"


@pytest.mark.unit
def test_a_handed_note_and_an_overrule_do_not_count():
    s = _seat()
    n = _handed()
    n.kind = "note"  # a board reply's handed note owes an outcome but fills no seat
    s.inbox.append(n)
    assert not n.handed_entry and s.asks_waiting() == 0


@pytest.mark.unit
def test_a_persons_answer_on_a_handed_thread_fills_a_seat_on_call():
    rw = mail.read_when
    seat = _seat()
    fills = "fills this seat: a session starts on the next tick and reads it first"
    assert rw(seat, "reply", NOW, refills=True) == fills
    assert rw(seat, "reply", NOW).startswith("waits in the seat's mailbox")
    idle = _seat("idle")
    idle.confidence = "hook"
    assert rw(idle, "reply", NOW, refills=True) == rw(idle, "note", NOW), "only a seat on call reads it so"


@pytest.mark.integration
async def test_the_seat_is_filled_for_a_handed_entry_and_waits_on_its_own_question(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="tl",
                dir=str(tmp_path),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                supervised=True,
                prompt="the seat's brief",
                seat={"trigger": "asks"},
            )  # fmt: skip
        )["id"]
        rec = agent.sessions[sid]
        rec.inbox.append(_handed(to=sid))
        async with LocalClient(caller=sid) as tl:
            q = (await tl.call("msg", to=PERSON, kind="ask", text="debt or feature?"))["entry"]
        for box in (rec.outbox, agent.person_inbox):  # on the entry's thread, as `--thread` will put it
            for e in box:
                if e.id == q["id"]:
                    e.root = "m-entry"
        rec.state, rec.pane, rec.exit_code = "exited", True, 0
        agent.store.save(rec)
        now = datetime.now(UTC)
        await agent._keep_running(now)
        assert agent.sessions[sid] is rec and rec.seat_due is None, "waiting on the person: not due"
        view = next(v for v in await person.call("list") if v["id"] == sid)
        assert view["read_when"]["refill"].startswith("fills this seat")
        mine = (await person.call("inbox"))["entries"]
        assert next(e for e in mine if e["id"] == q["id"])["on_handed"] is True
        got = await person.call("msg", to=sid, kind="reply", reply_to=q["id"], text="debt")
        assert got["read_when"][sid].startswith("fills this seat")
        await agent._keep_running(now)
        assert agent.sessions[sid] is not rec, "the answer counts the entry again, and the tick fills the seat"
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_reply_on_the_thread_that_closes_no_question_of_the_seats_reads_as_a_note(agent, tmp_path):
    """Review of #760: an outcome or an FYI from the seat on the entry's thread, answered by the
    person, refills nothing — the sentence says so, and the Reply dialog is not told otherwise."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = (
            await person.call(
                "create",
                name="tl",
                dir=str(tmp_path),
                adapter="shell",
                argv=["bash", "--norc", "--noprofile"],
                unattended=True,
                supervised=True,
                prompt="the seat's brief",
                seat={"trigger": "asks"},
            )  # fmt: skip
        )["id"]
        rec = agent.sessions[sid]
        rec.inbox.append(_handed(to=sid))
        async with LocalClient(caller=sid) as tl:
            fyi = (await tl.call("msg", to=PERSON, kind="note", text="drafting it on td240-x"))["entry"]
        for box in (rec.outbox, agent.person_inbox):
            for e in box:
                if e.id == fyi["id"]:
                    e.root = "m-entry"
        rec.state, rec.pane, rec.exit_code = "exited", True, 0
        agent.store.save(rec)
        mine = (await person.call("inbox"))["entries"]
        assert next(e for e in mine if e["id"] == fyi["id"])["on_handed"] is False
        got = await person.call("msg", to=sid, kind="reply", reply_to=fyi["id"], text="thanks")
        assert got["read_when"][sid].startswith("waits in the seat's mailbox")
        await person.call("kill", id=sid)


# -- slice 2: `entry_add`, the person's one RPC for Hand to the techlead and `ao td add` --------------


def _registry(tmp_path, *repos):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "repos.txt").write_text("".join(f"{r}\n" for r in repos))
    (home / "hosts.yml").write_text(f"local:\n  repos_registry: {home / 'repos.txt'}\n")


async def _seat_session(person, tmp_path) -> str:
    return (
        await person.call(
            "create", name="tl", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc", "--noprofile"],
            unattended=True, supervised=True, prompt="the seat's brief", seat={"trigger": "asks"},
        )  # fmt: skip
    )["id"]


@pytest.mark.unit
def test_entry_add_is_the_homes_edit():
    """Asked at a node it is forwarded to the home, and refused while the link is down: the mail and
    its debt are the home's (design §4.4a), as `identity_log`'s are."""
    assert "entry_add" in modes.HOME_EDITS
    why = modes.offline_refusal("entry_add", None, {}, host="laptop", home="kmaster")
    assert why and "kmaster (home) is unreachable" in why


@pytest.mark.integration
async def test_entry_add_hands_the_words_to_the_seat_with_no_bound(agent, tmp_path):
    repo = tmp_path / "agentorc"
    repo.mkdir()
    _registry(tmp_path, repo)
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat_session(person, tmp_path)
        teams = [{"team": "ao-grind", "seat": sid}, {"team": "other", "seat": "ao-x"}]
        got = await person.call(
            "entry_add", repo="agentorc", type="Debt", text="  the parser drops a trailing line  ", teams=teams
        )
        assert (got["to"], got["team"], got["repo"], got["type"]) == (sid, "ao-grind", "agentorc", "debt")
        assert "default bound" not in got["read_when"], "a handed entry never lapses, so the sentence names none"
        rec = agent.sessions[sid]
        e = next(e for e in rec.inbox if e.id == got["id"])
        assert e.kind == "ask" and e.from_ == PERSON and e.text == "the parser drops a trailing line"
        assert e.handed and e.entry == {"repo": "agentorc", "type": "debt"} and e.bound is None
        assert e.handed_entry and rec.asks_waiting() == 1
        # three days on, past any `ask`'s default bound, the sweep leaves it standing
        await agent._sweep_mail(datetime.now(UTC) + timedelta(days=3))
        assert e.open and e.expired_at is None
        # by path as well as by name — `ao td add` hands the checkout it runs in
        again = await person.call("entry_add", repo=str(repo), type="feature", text="a second", teams=teams)
        assert again["repo"] == "agentorc" and again["type"] == "feature"
        # a seat on call — its record exited — reads *fills this seat*, with no bound after it
        rec.state, rec.pane, rec.exit_code = "exited", True, 0
        agent.store.save(rec)
        third = await person.call("entry_add", repo="agentorc", type="debt", text="a third", teams=teams)
        assert third["read_when"] == "fills this seat: a session starts on the next tick and reads it first"
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_entry_add_is_refused_in_words(agent, tmp_path):
    repo = tmp_path / "agentorc"
    repo.mkdir()
    _registry(tmp_path, repo)
    teams = [{"team": "ao-grind", "seat": "ao-agentorc-techlead-ao-1"}]
    ok = {"repo": "agentorc", "type": "debt", "text": "x", "teams": teams}
    async with LocalClient(caller="ao-some-worker") as worker:
        with pytest.raises(AgentError, match="a person's own act"):
            await worker.call("entry_add", **ok)
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="no registered repo is 'samscrape'"):
            await person.call("entry_add", **{**ok, "repo": "samscrape"})
        with pytest.raises(AgentError, match="debt or feature, not 'chore'"):
            await person.call("entry_add", **{**ok, "type": "chore"})
        with pytest.raises(AgentError, match="has no words"):
            await person.call("entry_add", **{**ok, "text": "   "})
        with pytest.raises(AgentError, match="no team services agentorc"):
            await person.call("entry_add", **{**ok, "teams": []})
        with pytest.raises(AgentError, match="ao-grind has no techlead seat"):
            await person.call("entry_add", **{**ok, "teams": [{"team": "ao-grind", "seat": ""}]})


# -- slice 3: closed by its outcome — `--for`, `--thread` and the person's Dismiss -------------------


async def _seat_with_entry(person, agent, tmp_path) -> str:
    sid = (
        await person.call(
            "create", name="tl", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc", "--noprofile"],
            unattended=True, supervised=True, prompt="the seat's brief", seat={"trigger": "asks"},
        )  # fmt: skip
    )["id"]
    agent.sessions[sid].inbox.append(_handed(to=sid))
    return sid


@pytest.mark.integration
async def test_a_question_on_a_handed_entrys_thread_leaves_its_debt_standing(agent, tmp_path):
    """Review of #760: `--thread <handed id>` took the entry as the caller's own question and wrote
    `asked_again` on it, so it stopped owing and never counted again. Now the question joins the
    entry's thread and settles nothing; the person's answer counts the entry again."""
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat_with_entry(person, agent, tmp_path)
        rec = agent.sessions[sid]
        async with LocalClient(caller=sid) as tl:
            q = (await tl.call("msg", to=PERSON, kind="ask", text="debt or feature?", thread="m-entry"))["entry"]
        e = next(x for x in rec.inbox if x.id == "m-entry")
        assert q["root"] == "m-entry", "on the entry's own thread"
        assert e.outcome is None and e.owes, "the entry still owes its outcome"
        assert rec.asks_waiting() == 0, "the seat waits on the person"
        await person.call("msg", to=sid, kind="reply", reply_to=q["id"], text="debt")
        assert rec.asks_waiting() == 1, "the answer counts the entry again"
        # a reply to the entry itself does not close it: an outcome does
        async with LocalClient(caller=sid) as tl:
            await tl.call("msg", to=PERSON, kind="note", outcome="done", for_="m-entry", text="TD-240, PR #800")
        assert e.outcome and e.outcome["state"] == "done" and rec.asks_waiting() == 0
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_the_persons_dismiss_ends_a_handed_entry_and_tells_the_seat(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        sid = await _seat_with_entry(person, agent, tmp_path)
        rec = agent.sessions[sid]
        listed = (await person.call("inbox"))["handed"]
        assert [(e["id"], e["holder"], e["holder_name"]) for e in listed] == [("m-entry", sid, "tl")]
        assert all(e["id"] != "m-entry" for e in (await person.call("inbox"))["entries"]), "one copy: the seat's"
        got = await person.call("inbox_dismiss", msg=["m-entry"])
        assert got["dismissed"] == ["m-entry"]
        e = next(x for x in rec.inbox if x.id == "m-entry")
        assert e.outcome["state"] == "dismissed" and not e.owes and rec.asks_waiting() == 0
        assert any(x.from_ == "system" and "dismissed m-entry" in x.text for x in rec.inbox)
        assert (await person.call("inbox_dismiss", msg=["m-entry"]))["skipped"] == ["m-entry"]
        assert (await person.call("inbox"))["handed"] == [], "settled: no row waits on it"
        blocked = _handed("m-blocked", to=sid)
        blocked.outcome = {"state": "blocked", "text": "no repo", "at": "t", "by": "m-y"}
        rec.inbox.append(blocked)
        assert [e["id"] for e in (await person.call("inbox"))["handed"]] == ["m-blocked"]
        assert (await person.call("inbox_dismiss", msg=["m-blocked"]))["dismissed"] == ["m-blocked"]
        assert blocked.outcome["state"] == "dismissed" and (await person.call("inbox"))["handed"] == []
        await person.call("kill", id=sid)


@pytest.mark.integration
def test_the_seats_nudge_names_a_handed_entry_by_its_outcome(agent):
    s = _seat("idle")
    s.seat_due = {"at": "t", "by": "asks"}
    e = _handed(to=s.id)
    e.read_at = "2026-09-29T11:05:00Z"
    s.inbox.append(e)
    line = agent._nudge_line(s)
    assert "questions waiting" not in line and "1 entry the person handed you owes its outcome" in line
    q = MailEntry(id="m-q", from_="ao-mgr", to=[s.id], at="2026-09-29T11:00:00Z", kind="ask", text="?")
    s.inbox.append(q)
    assert agent._nudge_line(s).startswith("[agentorc] you have 1 questions waiting — run `ao inbox`; 1 entry")


# -- TD-292 slice 4: Send to reviewer — a look handed to the techlead seat --------------------------


SHOT = "docs/mockups/reviews/2026-10-03-td292-row.png"


async def _look(person, tmp_path, *, kind: str = "ask") -> tuple[str, str, str]:
    async def mk(n: str, **kw) -> str:
        return (
            await person.call("create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], **kw)
        )["id"]

    builder, seat = await mk("builder", unattended=True), await mk("tl", unattended=True, seat={"trigger": "asks"})
    extra = {"default": "Works"} if kind == "steer" else {"answers": ["Works", "Not right: x"]}
    async with LocalClient(caller=builder) as c:
        look = (
            await c.call("msg", to="person", kind=kind, text="the row?", about="TD-292", shots=[SHOT], **extra)
        )["entry"]["id"]
    return builder, seat, look


@pytest.mark.integration
async def test_send_to_reviewer_hands_a_look_to_the_seat_and_its_outcome_brings_it_back(agent, tmp_path):
    """Design §4.10 *A look*, §4.5a **Send to reviewer**: `inbox_hand` sends the seat a `handed` `ask`
    from the person with the look's text, `shots` and id and no bound, and snoozes the look until that
    debt closes; the seat's outcome clears the snooze and rides back on the look as `looked_by`."""
    await park_ticks(agent)
    async with LocalClient() as person:
        builder, seat, look = await _look(person, tmp_path)
        got = await person.call("inbox_hand", msg=look, seat=seat)
        assert got["to"] == seat and got["msg"] == look
        rec = agent.sessions[seat]
        h = next(e for e in rec.inbox if e.id == got["handed"])
        assert (h.kind, h.from_, h.text, h.about) == ("ask", PERSON, "the row?", "TD-292")
        assert h.handed and h.shots == [SHOT] and h.look == look and h.bound is None
        assert h.handed_entry and rec.asks_waiting() == 1, "it fills the seat"
        mine = next(e for e in agent.person_inbox if e.id == look)
        assert mine.snoozed_for == h.id and mine.open, "set aside, still the person's to answer"
        assert agent.person_store.load()[-1].snoozed_for == h.id, "kept across a restart"
        with pytest.raises(AgentError, match="already with a reviewer"):
            await person.call("inbox_hand", msg=look, seat=seat)
        async with LocalClient(caller=seat) as tl:
            await tl.call("msg", to=PERSON, kind="note", outcome="done", for_=h.id, text="matches §4.5a Settings page")
        mine = next(e for e in agent.person_inbox if e.id == look)
        assert mine.snoozed_for is None and mine.open
        assert mine.looked_by == {"seat": seat, "text": "matches §4.5a Settings page"}
        assert rec.asks_waiting() == 0
        for sid in (builder, seat):
            await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_handed_looks_wait_ends_by_unsnooze_or_dismiss_and_blocked_is_said(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        builder, seat, look = await _look(person, tmp_path)
        first = (await person.call("inbox_hand", msg=look, seat=seat))["handed"]
        # Unsnooze brings it back sooner; the handed ask still owes, and its outcome still lands on the look
        await person.call("inbox_snooze", msg=look)
        mine = next(e for e in agent.person_inbox if e.id == look)
        assert mine.snoozed_for is None
        async with LocalClient(caller=seat) as tl:
            await tl.call("msg", to=PERSON, kind="note", outcome="blocked", for_=first, text="no screenshot on main")
        assert mine.looked_by == {"seat": seat, "text": "blocked: no screenshot on main"}
        # handed again, then the person dismisses the handed row: the look returns with no line
        mine.looked_by = None
        second = (await person.call("inbox_hand", msg=look, seat=seat))["handed"]
        assert mine.snoozed_for == second
        await person.call("inbox_dismiss", msg=[second])
        assert mine.snoozed_for is None and mine.looked_by is None
        for sid in (builder, seat):
            await person.call("kill", id=sid)


@pytest.mark.integration
async def test_send_to_reviewer_is_refused_in_words(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        builder, seat, look = await _look(person, tmp_path)
        async with LocalClient(caller=builder) as c:
            steer = (
                await c.call("msg", to="person", kind="steer", default="Works", text="the row?", shots=[SHOT])
            )["entry"]["id"]
            plain = (await c.call("msg", to="person", kind="ask", text="merge?"))["entry"]["id"]
        async with LocalClient(caller=seat) as tl:
            with pytest.raises(AgentError, match="the person's own bookkeeping"):
                await tl.call("inbox_hand", msg=look, seat=seat)
        with pytest.raises(AgentError, match="is not a look"):
            await person.call("inbox_hand", msg=plain, seat=seat)
        with pytest.raises(AgentError, match="is a steer"):
            await person.call("inbox_hand", msg=steer, seat=seat)
        with pytest.raises(AgentError, match="has no techlead seat"):
            await person.call("inbox_hand", msg=look, seat="")
        with pytest.raises(AgentError, match="holds no entry m-nope"):
            await person.call("inbox_hand", msg="m-nope", seat=seat)
        await person.call("msg", to=builder, kind="reply", reply_to=look, text="Works", answer=0)
        with pytest.raises(AgentError, match="already answered"):
            await person.call("inbox_hand", msg=look, seat=seat)
        assert not any(e.look for e in agent.sessions[seat].inbox), "nothing was sent"
        for sid in (builder, seat):
            await person.call("kill", id=sid)


@pytest.mark.unit
def test_inbox_hand_travels_with_the_mailbox():
    assert "inbox_hand" in modes.MAILBOX
