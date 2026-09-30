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
