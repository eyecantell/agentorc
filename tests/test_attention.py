"""The attention trail, the state rows' snooze, and Dismiss (design §4.10 *The Inbox is a queue*,
TD-079 step 1b). A state row is a view of a record and leaves no entry behind, so the home records
what became of it; these are the rules that keeps the Inbox a queue rather than a list that empties
itself."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import wait_state

from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Pending, Session, attention_kind

pytestmark = pytest.mark.integration


def _rec(**kw) -> Session:
    s = Session(id="ao-x", name="x", kind="agent", adapter="shell", dir="/tmp/x")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_the_home_and_the_page_name_the_same_state_rows():
    """`models.attention_kind` and `agentorc.ui.app.state_kind` are a **parity pair** (§4.5a *Inbox
    row: state*): the home needs the predicate to know when a row ends, the page to draw it, and a
    kind added to one must be added to the other or the trail would miss endings the page showed."""
    from agentorc.ui.app import state_kind

    cases = [
        (_rec(state="needs-you", pending=Pending(kind="permission", text="rm?", tool_use_id="t1")), "permission"),
        (_rec(state="needs-you", pending=Pending(kind="question", text="which?")), "question"),
        (_rec(state="needs-you"), "needs"),
        (_rec(state="stalled?"), "stalled"),
        (_rec(state="limited", pending=Pending(kind="limit", text="resets at 5")), "limited"),
        (_rec(state="idle"), ""),
        (_rec(state="working"), ""),
        (_rec(state="exited"), ""),
    ]
    for s, want in cases:
        assert attention_kind(s) == want, s.state
        view = {"state": s.state, "pending": s.pending.to_dict() if s.pending else None}
        assert state_kind(view) == want, s.state
    # `unpushed` is the one they read differently and deliberately: the page adds its own checklist
    exited = _rec(state="exited", git={"dirty": 2, "ahead": 0, "upstream": "origin/x"})
    assert attention_kind(exited) == "unpushed"
    assert state_kind({"state": "exited", "flag": "dirty", "ready": [("tree clean", False)]}) == "unpushed"
    assert attention_kind(_rec(state="exited", git={"dirty": 0, "ahead": 0})) == ""


async def test_a_row_that_resolves_itself_leaves_a_trail(agent, hookstub, tmp_path):
    """Design §4.10 rule 2: *what resolves itself leaves a trail*. A permission answered in the
    terminal, a stall that cleared, a session that was resumed — each used to leave the row with no
    trace, which is what *it disappeared when I read it* was."""
    async with LocalClient() as person, LocalClient() as feeder:
        s = await person.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name)
        sid = s["id"]
        await feeder.call("hook", session=sid, state="needs-you", pending={"kind": "question", "text": "which one?"})
        await wait_state(person, sid, "needs-you")
        await agent.tick()
        assert agent._attention[f"{sid}|state"][0] == "question"
        # old enough to leave a trail (the floor), with the words the row began with
        agent._attention[f"{sid}|state"] = ("question", "2026-09-20T00:00:00Z", "which one?")
        await feeder.call("hook", session=sid, state="idle")
        await wait_state(person, sid, "idle")
        await agent.tick()
        got = (await person.call("inbox"))["trail"]
        assert [e["kind"] for e in got] == ["question"]
        assert got[0]["sid"] == sid and got[0]["how"] == "resolved" and got[0]["text"] == "which one?"
        assert got[0]["id"].startswith("t-") and got[0]["count"] == 1
        # it is kept, not counted twice: the same ending again coalesces
        for _ in range(2):
            await feeder.call("hook", session=sid, state="needs-you", pending={"kind": "question", "text": "which?"})
            await wait_state(person, sid, "needs-you")
            await agent.tick()
            agent._attention[f"{sid}|state"] = ("question", "2026-09-20T00:00:00Z", "which?")
            await feeder.call("hook", session=sid, state="idle")
            await wait_state(person, sid, "idle")
            await agent.tick()
        got = (await person.call("inbox"))["trail"]
        assert len(got) == 1 and got[0]["count"] == 3, "a session flapping cannot push the trail out"
        await person.call("kill", id=sid)


async def test_a_row_under_the_floor_leaves_nothing_unless_a_person_ended_it(agent, hookstub, tmp_path):
    """Design §4.10 rule 2: *a state that lasted under five seconds leaves no trail unless a person
    ended it* — a permission a policy answered in 200 ms is not news, and a person's own press
    always is."""
    async with LocalClient() as person, LocalClient() as feeder:
        s = await person.call("create", name="quick", dir=str(tmp_path), adapter=hookstub.name)
        sid = s["id"]
        await feeder.call("hook", session=sid, state="needs-you", pending={"kind": "question", "text": "quick?"})
        await wait_state(person, sid, "needs-you")
        await agent.tick()
        await feeder.call("hook", session=sid, state="idle")
        await wait_state(person, sid, "idle")
        await agent.tick()
        assert (await person.call("inbox"))["trail"] == []  # under the floor, and nobody pressed anything
        # the same row, ended by a person: recorded however short it was
        await feeder.call("hook", session=sid, state="needs-you", pending={"kind": "question", "text": "quick?"})
        await wait_state(person, sid, "needs-you")
        await agent.tick()
        agent._attention_ended(sid, "allowed by you")
        await feeder.call("hook", session=sid, state="idle")
        await wait_state(person, sid, "idle")
        await agent.tick()
        got = (await person.call("inbox"))["trail"]
        assert len(got) == 1 and got[0]["how"] == "allowed by you"
        await person.call("kill", id=sid)


async def test_dismiss_takes_the_ids_on_screen_and_refuses_an_open_question(agent, tmp_path):
    """Design §4.10 rule 3: a `note` leaves the person's Inbox only by **Dismiss**, which takes the
    ids **this browser has on screen** — mail and trail alike — never *everything FYI holds now*.
    An open question is refused, an id already gone is skipped, and a session is refused outright."""
    async with LocalClient() as person:
        s = await person.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        async with LocalClient(caller=sid) as w:
            note = (await w.call("msg", to="person", text="the venv is broken"))["entry"]
            asked = (await w.call("msg", to="person", text="which one?", kind="ask"))["entry"]
            with pytest.raises(AgentError, match="cannot dismiss the person's rows"):
                await w.call("inbox_dismiss", msg=[note["id"]])
        agent.trail = [{"id": "t-1", "sid": sid, "kind": "question", "how": "resolved"}]
        with pytest.raises(AgentError, match="is still open") as refused:
            await person.call("inbox_dismiss", msg=[note["id"], asked["id"]])
        assert refused.value.data["open"] == [asked["id"]]
        assert [e["id"] for e in (await person.call("inbox"))["entries"]] == [note["id"], asked["id"]]
        got = await person.call("inbox_dismiss", msg=[note["id"], "t-1", "m-gone"])
        assert got["dismissed"] == [note["id"], "t-1"] and got["skipped"] == ["m-gone"]
        left = await person.call("inbox")
        assert [e["id"] for e in left["entries"]] == [asked["id"]] and left["trail"] == []
        await person.call("kill", id=sid)


async def test_dismissing_an_answered_question_ends_the_debt_and_tells_the_asker(agent, tmp_path):
    """Design §4.10 *Outcomes*: *the debt ends when the person **Dismisses** the row — I do not
    need to hear back — and the asker is told by a `system` note.* The two halves of step 1 meet
    here: dismissing is rule 3's, the debt is *Outcomes*'."""
    async with LocalClient() as person:
        s = await person.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        async with LocalClient(caller=sid) as w:
            asked = (await w.call("msg", to="person", text="rebase or merge?", kind="ask"))["entry"]
            await person.call("msg", to=sid, text="merge it", kind="reply", reply_to=asked["id"])
            assert (await person.call("get", id=sid))["mail"]["owed"] == [asked["id"]]
            await person.call("inbox_dismiss", msg=[asked["id"]])
            assert (await person.call("get", id=sid))["mail"]["owed"] == []  # nothing is owed on it now
            said = [e for e in (await w.call("inbox"))["entries"] if e["from"] == "system"]
            assert said and asked["id"] in said[-1]["text"] and "no outcome is owed" in said[-1]["text"]
        await person.call("kill", id=sid)


async def test_a_state_rows_snooze_lives_in_the_homes_own_store(agent, tmp_path):
    """TD-069's open gap, closed by §4.10 rule 2: a state has no mail entry to carry a
    `snoozed_until`, so the home keeps it — per record **and row kind**, since one session may
    raise two rows and snoozing one is not snoozing the other."""
    async with LocalClient() as person:
        s = await person.call("create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
        sid = s["id"]
        async with LocalClient(caller=sid) as w:
            with pytest.raises(AgentError, match="a snooze is the person's own"):
                await w.call("attention_snooze", id=sid, kind="stalled", until="2026-09-21T00:00:00Z")
        with pytest.raises(AgentError, match="unknown row kind"):
            await person.call("attention_snooze", id=sid, kind="whenever", until="2026-09-21T00:00:00Z")
        await person.call("attention_snooze", id=sid, kind="stalled", until="2026-09-21T00:00:00Z")
        snoozed = (await person.call("inbox"))["attention_snoozed"]
        assert snoozed == {f"{sid}|stalled": "2026-09-21T00:00:00Z"}  # the other rows are untouched
        # it survives a restart: the store is the home's own file, like the person inbox
        from sessionorc.store import AttentionStore

        assert AttentionStore().load()[1] == snoozed
        await person.call("attention_snooze", id=sid, kind="stalled")  # no `until` clears it
        assert (await person.call("inbox"))["attention_snoozed"] == {}
        await person.call("kill", id=sid)


async def test_the_trail_is_kept_for_the_retention_window_and_bounded(agent, tmp_path, monkeypatch):
    """Design §4.10 rule 2: the newest `TRAIL_KEEP`, each kept for `MAIL_RETENTION`. What retention
    prunes is a *trail*, never an item a person has not answered."""
    import sessionorc.agent as agent_mod

    old = datetime.now(UTC) - timedelta(days=1)
    stamp = old.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    agent.trail = [
        {"id": "t-old", "sid": "ao-a", "kind": "stalled", "how": "resolved", "resolved_at": stamp, "last": stamp},
        {"id": "t-new", "sid": "ao-b", "kind": "stalled", "how": "resolved", "resolved_at": "", "last": ""},
    ]
    await agent._sweep_mail(datetime.now(UTC))
    assert [e["id"] for e in agent.trail] == ["t-new"]  # a stamp that cannot be read is kept, not lost
    monkeypatch.setattr(agent_mod, "TRAIL_KEEP", 3)
    s = _rec()
    for i in range(5):
        agent._trail_append(s, "stalled", "", datetime.now(UTC), how=f"resolved {i}")
    assert len(agent.trail) == 3 and agent.trail[0]["how"] == "resolved 4"  # newest first, bounded


async def test_a_new_row_records_its_own_beginning(agent, tmp_path):
    """Review of PR #269: a record may go from one row kind straight to another — `needs-you` to
    `stalled?` — without passing through *no row at all*. The row that begins then is a new row,
    and must record when **it** began: one that inherited the row before it would misreport how
    long it had been up when it ends."""
    s = Session(id="ao-a", name="a", kind="agent", adapter="shell", dir=str(tmp_path))
    s.state, s.pending, s.since = "needs-you", Pending(kind="question", text="which?"), "2026-09-20T09:00:00Z"
    agent.sessions[s.id] = s
    try:
        agent._note_attention(datetime.now(UTC))
        assert agent._attention[f"{s.id}|state"] == ("question", "2026-09-20T09:00:00Z", "which?")
        s.state, s.pending, s.since = "stalled?", None, "2026-09-20T11:00:00Z"
        agent._note_attention(datetime.now(UTC))
        assert agent._attention[f"{s.id}|state"][1] == "2026-09-20T11:00:00Z", "the new row's own beginning"
        assert [e["kind"] for e in agent.trail] == ["question"]
        assert agent.trail[0]["since"] == "2026-09-20T09:00:00Z"  # and the one that ended kept its own
    finally:
        agent.sessions.pop(s.id, None)
        agent.trail.clear()


async def test_a_nodes_row_is_trailed_under_its_address_and_keeps_by_you(agent, tmp_path):
    """Review of PR #269, against §4.10 rule 2 (*for a node's session the home sees the replica
    change and knows its own `decide`s, so `by you` is always known*). A node's record is keyed
    `id@host` in the graph, and the act that ends its row runs **at the node** — so the home writes
    the word itself, under the same address, or every node-hosted row would read *resolved*."""
    r = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="/tmp/x", host="laptop")
    r.state, r.pending, r.since = "needs-you", Pending(kind="permission", text="rm -r?", tool_use_id="t1"), "2026-09-20T09:00:00Z"  # noqa: E501
    agent.remote["laptop"] = {r.id: r}
    try:
        agent._note_attention(datetime.now(UTC))
        assert agent._attention["ao-x-w@laptop|state"][0] == "permission"
        agent._attention_ended("ao-x-w@laptop", "allowed by you")  # what `_route_act` writes at the home
        r.state, r.pending = "working", None
        agent._note_attention(datetime.now(UTC))
        assert [(e["sid"], e["how"]) for e in agent.trail] == [("ao-x-w@laptop", "allowed by you")]
        # …and a record the node resumed says so from its own face, with no act the home ever saw
        agent.trail.clear()
        r.state, r.pending, r.superseded_by = "needs-you", Pending(kind="question", text="which?"), "ao-x-w2"
        r.since = "2026-09-20T09:00:00Z"
        agent._note_attention(datetime.now(UTC))
        r.state, r.pending = "closed", None
        agent._note_attention(datetime.now(UTC))
        assert [e["how"] for e in agent.trail] == ["resumed"]
    finally:
        agent.remote.pop("laptop", None)
        agent.trail.clear()
