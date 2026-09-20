"""Messages between sessions (design §4.10, TD-052 step 1) and the address choices that came with
them (design §4.4a, TD-057 step 1): the record, the `msg` and `inbox` RPCs, the message gate
beside the acting gate, threads and their bounds, copies, `sends`, the lifecycle of an entry, and
the move on resume."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import wait_state

from sessionorc import mail, paths
from sessionorc.client import AgentError, LocalClient

pytestmark = pytest.mark.integration


def _mk(person, tmp_path):
    async def mk(n: str, **kw):
        return (await person.call("create", name=n, dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], **kw))[
            "id"
        ]

    return mk


async def test_the_message_gate_reads_the_graph_and_names_its_own_rule(agent, tmp_path):
    """Design §4.10 "The message gate is weaker than `control`": a session may message upward (its
    controllers), downward (its members), sideways (the same `team` badge — invariant 9's one
    exception — or a shared controlled target), and nothing else; the refusal names §4.10's graph,
    never invariant 11; no grant is needed; a person may message anyone; the graph is read on
    every call, so a membership edit changes who may talk on the next one."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, lead2, worker, peer, stranger = [
            await mk(n, unattended=True) for n in ("lead", "lead2", "w", "peer", "x")
        ]
        await person.call("set_controllers", id=worker, add=[lead, lead2])
        for a, b in ((worker, lead), (lead, worker), (lead, lead2)):
            async with LocalClient(caller=a) as c:
                got = await c.call("msg", to=b, text=f"hello from {a}")
                assert got["delivered"] == [b] and got["entry"]["from"] == a and got["entry"]["kind"] == "note"
        # a worker → its lead: upward, and no grant on the worker anywhere
        assert (await person.call("get", id=worker))["capabilities"] == []
        # nothing else: refused naming the graph, not the acting gate
        async with LocalClient(caller=stranger) as x:
            with pytest.raises(AgentError, match=r"design §4\.10") as e:
                await x.call("msg", to=worker, text="hi")
            assert "invariant 11" not in str(e.value) and "control grant" not in str(e.value)
            with pytest.raises(AgentError, match="cannot message itself"):
                await x.call("msg", to=stranger, text="note to self")
        # the team badge is the one sideways edge that keys on `team`
        async with LocalClient(caller=peer) as p:
            with pytest.raises(AgentError, match=r"design §4\.10"):
                await p.call("msg", to=worker, text="hi")
        team = await mk("t1", team="alpha")
        team2 = await mk("t2", team="alpha")
        async with LocalClient(caller=team) as t:
            assert (await t.call("msg", to=team2, text="same team"))["delivered"] == [team2]
        # a membership edit changes the answer on the next call, nothing cached
        await person.call("set_controllers", id=worker, remove=[lead2])
        async with LocalClient(caller=lead2) as l2:
            with pytest.raises(AgentError, match=r"design §4\.10"):
                await l2.call("msg", to=worker, text="hi")
            # …and lead ↔ lead2 no longer share a target either
            with pytest.raises(AgentError, match=r"design §4\.10"):
                await l2.call("msg", to=lead, text="hi")
        # a caller this agent has no record of has no edges
        async with LocalClient(caller="ao-nobody") as nobody:
            with pytest.raises(AgentError, match="no record of it"):
                await nobody.call("msg", to=worker, text="hi")
        # a person may message anyone, and reads as `person`
        got = await person.call("msg", to=stranger, text="from the person")
        assert got["entry"]["from"] == "person"
        # multi-addressee is all or nothing: the refusal names the failing addressee
        async with LocalClient(caller=lead) as ld:
            with pytest.raises(AgentError, match=stranger):
                await ld.call("msg", to=[worker, stranger], text="both")
        assert (await person.call("get", id=worker))["unread"] == 1  # lead's hello, nothing from the refused send
        # no broadcast: a cap on the addressees the sender named
        async with LocalClient(caller=lead) as ld:
            with pytest.raises(AgentError, match="no broadcast"):
                await ld.call("msg", to=[f"ao-{i}" for i in range(mail.RECIPIENT_CAP + 1)], text="all")
            with pytest.raises(AgentError, match="no broadcast"):
                await ld.call("msg", to=[], text="nobody")
        for sid in (lead, lead2, worker, peer, stranger, team, team2):
            await person.call("kill", id=sid)


async def test_a_message_reaches_an_interactive_session_and_an_act_does_not(agent, tmp_path):
    """§9 invariant 5's split: an acting RPC from a session onto a person's session is refused
    whatever the membership; a message to it lands (and, in step 1, nothing wakes anything)."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        orc = await mk("orc", unattended=True)
        await person.call("set_grants", id=orc, add=["control"])
        mine = await mk("mine")  # interactive by default: the person's own
        await person.call("set_controllers", id=mine, add=[orc])
        async with LocalClient(caller=orc) as o:
            with pytest.raises(AgentError, match="invariant 5"):
                await o.call("send", id=mine, text="echo no")
            got = await o.call("msg", to=mine, text="may I?", kind="ask")
            assert got["delivered"] == [mine]
        rec = await person.call("get", id=mine)
        assert rec["unread"] == 1 and rec["state"] != "closed" and rec["mail"]["open_asks"] == [got["entry"]["id"]]
        for sid in (orc, mine):
            await person.call("kill", id=sid)


async def test_inbox_reads_set_read_at_and_only_they_do(agent, tmp_path):
    """Lifecycle stage 2 (design §4.10): `read_at` is set when, and only when, `inbox` returns the
    entry to its own caller — a person's read of the panel sets nothing, nobody reads another
    session's inbox, and every entry names its sender's role for the reader."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker, other = await mk("lead", unattended=True), await mk("w", unattended=True), await mk("o")
        await person.call("set_controllers", id=worker, add=[lead])
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=worker, text="first")
        await person.call("msg", to=worker, text="from me")
        # a person's read (the Inbox panel) sets nothing
        seen = await person.call("inbox", id=worker)
        assert [e["read_at"] for e in seen["entries"]] == [None, None]
        assert [e["from_role"] for e in seen["entries"]] == ["controller", "person"]
        assert (await person.call("get", id=worker))["unread"] == 2
        # bodies never ride the record: `get` carries counts, `inbox` the text
        assert "inbox" not in (await person.call("get", id=worker))
        assert json.loads((paths.sessions_dir() / f"{worker}.json").read_text())["inbox"][0]["text"] == "first"
        # nobody reads another session's inbox
        async with LocalClient(caller=other) as o:
            with pytest.raises(AgentError, match="nobody reads another session's inbox"):
                await o.call("inbox", id=worker)
        # the session's own read marks them, and `--unread` then shows nothing
        async with LocalClient(caller=worker) as w:
            mine = await w.call("inbox")
            assert all(e["read_at"] for e in mine["entries"]) and mine["unread"] == 0
            assert (await w.call("inbox", unread=True))["entries"] == []
        assert (await person.call("get", id=worker))["unread"] == 0
        for sid in (lead, worker, other):
            await person.call("kill", id=sid)


async def test_an_ask_is_closed_by_its_first_reply_uncounted_and_expires_on_its_bound(agent, tmp_path):
    """Design §4.10 "How the count works, exactly" and "An `ask` carries its bound": the first
    reply to an open ask is never counted and closes it on every copy; a later reply counts as a
    note; `reply_to` must name an entry in the replier's own inbox; a reply belongs to its root's
    thread whatever `about` it carries; the bound is wall-clock from `at`, read or not, and the
    expired mark lands on both records."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker = await mk("lead", unattended=True), await mk("w", unattended=True)
        await person.call("set_controllers", id=worker, add=[lead])
        async with LocalClient(caller=worker) as w, LocalClient(caller=lead) as ld:
            ask = (await w.call("msg", to=lead, text="which branch?", kind="ask", about="TD-001"))["entry"]
            assert ask["bound"] and ask["root"] == ask["id"]
            assert (await person.call("get", id=lead))["threads"][ask["id"]]["count"] == 1
            # a reply must name an entry the replier holds
            with pytest.raises(AgentError, match="holds no entry"):
                await w.call("msg", text="answering my own", kind="reply", reply_to=ask["id"])
            with pytest.raises(AgentError, match="--reply-to"):
                await ld.call("msg", to=worker, text="no id", kind="reply")
            # the first reply: free, closes the ask on both copies, defaults its addressee to the asker
            reply = await ld.call("msg", text="main", kind="reply", reply_to=ask["id"], about="something else")
            assert reply["delivered"] == [worker] and reply["closed"] == ask["id"]
            assert reply["entry"]["root"] == ask["id"]
            for sid in (lead, worker):
                assert (await person.call("get", id=sid))["threads"][ask["id"]]["count"] == 1
            held = (await person.call("inbox", id=lead))["entries"][0]
            assert held["closed_by"] == reply["entry"]["id"] and held["closed_at"]
            assert (await person.call("get", id=worker))["mail"]["open_asks"] == []
            # a later reply to the closed ask counts as a note, in the same thread
            await ld.call("msg", text="…and rebase first", kind="reply", reply_to=ask["id"])
            assert (await person.call("get", id=worker))["threads"][ask["id"]]["count"] == 2
            # an ask expires on its bound, read or not, on every copy
            short = (await w.call("msg", to=lead, text="quick?", kind="ask", bound=0.5))["entry"]
            await asyncio.sleep(0.7)
            await agent._sweep_mail(datetime.now(UTC))
            assert (await person.call("get", id=worker))["mail"]["expired"] == [short["id"]]
            lead_copy = [e for e in (await person.call("inbox", id=lead))["entries"] if e["id"] == short["id"]][0]
            assert lead_copy["expired_at"]
            # …and the reply that comes too late counts as a note rather than closing anything
            late = await ld.call("msg", text="too late", kind="reply", reply_to=short["id"])
            assert late["closed"] is None
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_the_exchange_bound_refuses_and_marks_both_sides_and_a_person_resets_it(agent, tmp_path, monkeypatch):
    """Design §4.10 "A bounded exchange": past the bound the send is refused naming the bound and
    the thread, `bound_hit` is written on every record holding it, a copy recipient's tally never
    refuses, a reply-less pair is counted inside a rolling window on both records, and a person's
    message into the thread is uncounted and resets it."""
    monkeypatch.setattr(mail, "THREAD_BOUND", 2)
    monkeypatch.setattr(mail, "PAIR_BOUND", 2)  # its own number since step 6: a pair outlives any thread
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, lead2, worker = [await mk(n, unattended=True) for n in ("lead", "lead2", "w")]
        await person.call("set_controllers", id=worker, add=[lead, lead2])
        async with LocalClient(caller=lead) as ld, LocalClient(caller=worker) as w:
            # `about` the worker, from one of its controllers: copied to the other controller
            root = (await ld.call("msg", to=worker, text="do X", about=worker))["entry"]
            assert root["copies"] == [lead2]
            assert (await person.call("get", id=lead2))["threads"][root["id"]]["count"] == 1
            # a reply to a note counts; the lead replies to the reply it holds, never to its own note
            done = (await w.call("msg", text="X is done", kind="reply", reply_to=root["id"]))["entry"]
            with pytest.raises(AgentError, match="holds no entry"):
                await ld.call("msg", text="then Y", kind="reply", reply_to=root["id"])
            with pytest.raises(AgentError, match="at its bound of 2") as e:
                await ld.call("msg", text="then Y", kind="reply", reply_to=done["id"])
            assert root["id"] in str(e.value)
            for sid in (lead, lead2, worker):
                assert (await person.call("get", id=sid))["threads"][root["id"]]["bound_hit"] is True
                assert (await person.call("get", id=sid))["mail"]["bound_hit"] == [root["id"]]
            # the person rules: uncounted, and the thread is reset on every record holding it
            ruling = await person.call(
                "msg", to=[lead, worker], text="Y after review", kind="reply", reply_to=root["id"]
            )
            assert ruling["entry"]["root"] == root["id"]
            for sid in (lead, lead2, worker):
                assert (await person.call("get", id=sid))["threads"][root["id"]] == {
                    "count": 0,
                    "bound_hit": False,
                    "at": [],
                }
            assert (await ld.call("msg", text="then Y", kind="reply", reply_to=done["id"]))["delivered"] == [worker]
            # a reply-less pair is counted in its window, on both records, and refused at the bound:
            # the root note above was one, in either direction
            await w.call("msg", to=lead, text="ping 1")
            assert (await person.call("get", id=worker))["threads"][f"pair:{lead}"]["count"] == 2
            with pytest.raises(AgentError, match="replying to nothing"):
                await ld.call("msg", to=worker, text="ping 2")
            assert (await person.call("get", id=lead))["threads"][f"pair:{worker}"]["bound_hit"] is True
            # the window rolls: old entries fall out and the pair may talk again
            monkeypatch.setattr(mail, "PAIR_WINDOW", timedelta(seconds=0))
            assert (await w.call("msg", to=lead, text="ping 3"))["delivered"] == [lead]
        for sid in (lead, lead2, worker):
            await person.call("kill", id=sid)


async def test_copies_are_exempt_from_the_cap_and_never_sink_a_send(agent, tmp_path, monkeypatch):
    """Design §4.10 "A bounded mailbox": a full inbox refuses a send naming it, never drops; a copy
    that cannot land is dropped and recorded on the sender's entry as `copies_failed`; replies in a
    copied thread are copied to the same set."""
    monkeypatch.setattr(mail, "MAILBOX_DEPTH", 1)
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, lead2, worker = [await mk(n, unattended=True) for n in ("lead", "lead2", "w")]
        await person.call("set_controllers", id=worker, add=[lead, lead2])
        await person.call("msg", to=lead2, text="lead2 is full now")
        async with LocalClient(caller=lead) as ld, LocalClient(caller=worker) as w:
            got = await ld.call("msg", to=worker, text="do X", about=worker)
            assert got["copies"] == [] and got["copies_failed"] == [lead2]
            assert (await person.call("get", id=lead))["mail"]["copies_failed"] == [got["entry"]["id"]]
            # the worker's inbox is at depth now: a named send to it is refused, not dropped
            with pytest.raises(AgentError, match="refused, not dropped"):
                await ld.call("msg", to=worker, text="and Y")
            await w.call("inbox")  # read: room again
            reply = await w.call("msg", text="done", kind="reply", reply_to=got["entry"]["id"])
            assert reply["copies_failed"] == [lead2]  # the same set, still full
            async with LocalClient(caller=lead2) as l2:
                await l2.call("inbox")
            await ld.call("inbox")  # the lead reads the first reply: room for the second
            reply2 = await w.call("msg", text="really done", kind="reply", reply_to=got["entry"]["id"])
            assert reply2["copies"] == [lead2]
        for sid in (lead, lead2, worker):
            await person.call("kill", id=sid)


async def test_sends_are_recorded_with_who_typed_and_a_conflict_cites_them(agent, tmp_path):
    """Design §4.10 "A `send` is recorded on the record it lands on": every `send` and `keys`
    that reached the pane is on the record as `sends` with `from`, bounded; a `conflict` names
    two controllers and cites `sends` ids the sender holds, lands with one id in each inbox, and
    its first reply closes it on every addressee's copy."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, lead2, worker = [await mk(n, unattended=True) for n in ("lead", "lead2", "w")]
        for sid in (lead, lead2):
            await person.call("set_grants", id=sid, add=["control"])
        await person.call("set_controllers", id=worker, add=[lead, lead2])
        await wait_state(person, worker, "idle")
        async with LocalClient(caller=lead) as ld, LocalClient(caller=lead2) as l2:
            await ld.call("send", id=worker, text="echo use main")
            await l2.call("send", id=worker, text="echo use the branch")
            await person.call("keys", id=worker, keys=["Enter"])
        sends = (await person.call("get", id=worker))["sends"]
        assert [(e["from"], e["text"]) for e in sends] == [
            (lead, "echo use main"),
            (lead2, "echo use the branch"),
            ("person", "Enter"),
        ]
        assert all(e["id"].startswith("s-") and e["at"] and e["verdict"] == "submitted" for e in sends)
        async with LocalClient(caller=worker) as w:
            with pytest.raises(AgentError, match="two or more controllers"):
                await w.call("msg", to=lead, text="?", kind="conflict", cites=[sends[0]["id"]])
            with pytest.raises(AgentError, match="cites the `sends`"):
                await w.call("msg", to=[lead, lead2], text="?", kind="conflict", cites=["s-nope"])
            got = await w.call(
                "msg", to=[lead, lead2], text="main or branch?", kind="conflict", cites=[sends[0]["id"], sends[1]["id"]]
            )
            assert got["delivered"] == [lead, lead2] and got["entry"]["bound"]
            # the fixed header of `ao inbox` names the caller of the most recent send
            assert (await w.call("inbox"))["sends"][-1]["from"] == "person"
        cid = got["entry"]["id"]
        assert [e["id"] for e in (await person.call("inbox", id=lead2))["entries"]] == [cid]
        async with LocalClient(caller=lead) as ld:
            reply = await ld.call("msg", text="main", kind="reply", reply_to=cid)
            assert reply["closed"] == cid and reply["copies"] == [lead2]  # the other lead sees how it was settled
        for sid in (lead, lead2):
            copy = [e for e in (await person.call("inbox", id=sid))["entries"] if e["id"] == cid][0]
            assert copy["closed_by"] == reply["entry"]["id"]
        for sid in (lead, lead2, worker):
            await person.call("kill", id=sid)


async def test_an_exited_addressee_leaves_its_asks_pending_and_a_closed_one_expires_them(agent, tmp_path):
    """Design §4.10 lifecycle: an addressee that exits leaves the asks addressed to it pending, with
    *addressee exited* on the asker's record; close or forget expires them."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, w1, w2 = [await mk(n, unattended=True) for n in ("lead", "w1", "w2")]
        for sid in (w1, w2):
            await person.call("set_controllers", id=sid, add=[lead])
        async with LocalClient(caller=lead) as ld:
            a1 = (await ld.call("msg", to=w1, text="status?", kind="ask"))["entry"]
            a2 = (await ld.call("msg", to=w2, text="status?", kind="ask"))["entry"]
        await person.call("kill", id=w1)
        await wait_state(person, w1, "exited")
        await agent._sweep_mail(datetime.now(UTC))
        lead_rec = await person.call("get", id=lead)
        assert lead_rec["mail"]["addressee_exited"] == [a1["id"]] and lead_rec["mail"]["expired"] == []
        await person.call("close", id=w2)
        await agent._sweep_mail(datetime.now(UTC))
        assert (await person.call("get", id=lead))["mail"]["expired"] == [a2["id"]]
        await person.call("remove", id=w1)
        assert sorted((await person.call("get", id=lead))["mail"]["expired"]) == sorted([a1["id"], a2["id"]])
        await person.call("kill", id=lead)


async def test_resume_carries_mail_forward_and_the_old_id_forwards(agent, hookstub, tmp_path):
    """Design §4.10 lifecycle "Resume carries mail forward": every entry, the tallies and `sends`
    move to the resuming record with the old id rewritten in `to` and in other records' pair
    tallies and pending asks; a `send` to the superseded id is refused; a `msg` to it is forwarded
    to the successor and the reply says so."""
    async with LocalClient() as person:
        for d in ("l", "w", "w2"):
            (tmp_path / d).mkdir()
        lead = (await person.call("create", name="lead", dir=str(tmp_path / "l"), adapter="shell", argv=["bash"]))["id"]
        w = (await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True))[
            "id"
        ]
        await person.call("hook", session=w, adapter_id="conv-9", state="idle")
        await person.call("set_controllers", id=w, add=[lead])
        await person.call("set_grants", id=lead, add=["control"])
        async with LocalClient(caller=lead) as ld, LocalClient(caller=w) as worker:
            await ld.call("send", id=w, text="echo hi")
            ask = (await ld.call("msg", to=w, text="branch?", kind="ask"))["entry"]
            await worker.call("inbox")  # read it, then crash before answering
            note = (await worker.call("msg", to=lead, text="fyi"))["entry"]
        await person.call("kill", id=w)
        await wait_state(person, w, "exited")
        await agent._sweep_mail(datetime.now(UTC))
        assert (await person.call("get", id=lead))["mail"]["addressee_exited"] == [ask["id"]]
        w2 = (
            await person.call(
                "create",
                name="w2",
                dir=str(tmp_path / "w2"),
                adapter=hookstub.name,
                unattended=True,
                resume="conv-9",
                controllers=[lead],  # as a lead resuming its own worker is, by create adding the creator
            )
        )["id"]
        assert w2 != w
        old, new = await person.call("get", id=w), await person.call("get", id=w2)
        assert old["state"] == "closed" and old["superseded_by"] == w2 and old["sends"] == []
        assert [e["text"] for e in new["sends"]] == ["echo hi"]
        moved = (await person.call("inbox", id=w2))["entries"]
        assert [(e["id"], e["read_at"] is not None, e["to"]) for e in moved] == [(ask["id"], True, [w2])]
        assert f"pair:{w}" not in new["threads"] and new["threads"][f"pair:{lead}"]["count"] == 2  # ask + note
        assert new["threads"][note["id"]]["count"] == 1  # the tally moved, not recounted
        lead_rec = await person.call("get", id=lead)
        assert lead_rec["threads"][f"pair:{w2}"]["count"] == 2 and f"pair:{w}" not in lead_rec["threads"]
        assert lead_rec["mail"]["addressee_exited"] == []  # open again, addressed to the successor
        lead_copy = [e for e in json.loads((paths.sessions_dir() / f"{lead}.json").read_text())["outbox"]][0]
        assert lead_copy["to"] == [w2] and lead_copy["pending"] == []
        async with LocalClient(caller=lead) as ld:
            with pytest.raises(AgentError, match=f"closed; it was resumed as {w2}"):
                await ld.call("send", id=w, text="echo no")
            fwd = await ld.call("msg", to=w, text="still there?")
            assert fwd["forwarded"] == {w: w2} and fwd["delivered"] == [w2]
        # the worker answers from its new record, under the moved thread
        async with LocalClient(caller=w2) as worker:
            assert (await worker.call("msg", text="main", kind="reply", reply_to=ask["id"]))["closed"] == ask["id"]
        for sid in (lead, w2):
            await person.call("kill", id=sid)


async def test_a_same_nonce_retry_returns_the_original_verdict(agent, tmp_path):
    """Design §4.4a "Delivery and time": a retry carrying the same client nonce never lands twice
    and is not an identical repeat — it returns the first send's verdict, refusal included."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker, x = [await mk(n, unattended=True) for n in ("lead", "w", "x")]
        await person.call("set_controllers", id=worker, add=[lead])
        async with LocalClient(caller=lead) as ld:
            first = await ld.call("msg", to=worker, text="once", nonce="n1")
            again = await ld.call("msg", to=worker, text="once", nonce="n1")
            assert again == first and (await person.call("get", id=worker))["unread"] == 1
            with pytest.raises(AgentError, match=r"design §4\.10"):
                await ld.call("msg", to=x, text="no", nonce="n2")
            with pytest.raises(AgentError, match=r"design §4\.10"):
                await ld.call("msg", to=x, text="no", nonce="n2")
        for sid in (lead, worker, x):
            await person.call("kill", id=sid)


async def test_addresses_are_qualified_on_the_way_in_and_identity_comes_from_the_channel(agent, tmp_path):
    """Design §4.4a, TD-057 step 1: every record carries `host`; an id naming this host is stored
    bare and another host's as `id@host`; a caller's `@host` is dropped because identity is the
    channel's, never a field's; a message body is capped."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker = await mk("lead", unattended=True), await mk("w", unattended=True)
        rec = await person.call("get", id=worker)
        assert rec["host"] == agent.host and agent.host
        got = await person.call("set_controllers", id=worker, add=[f"{lead}@{agent.host}", "ao-far@laptop"])
        assert got["controllers"] == [lead, "ao-far@laptop"]
        assert json.loads((paths.sessions_dir() / f"{worker}.json").read_text())["host"] == agent.host
        async with LocalClient(caller=f"{lead}@somewhere-else") as ld:
            assert (await ld.call("msg", to=f"{worker}@{agent.host}", text="hi"))["entry"]["from"] == lead
            with pytest.raises(AgentError, match="over"):
                await ld.call("msg", to=worker, text="x" * (mail.TEXT_CAP + 1))
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_read_entries_are_pruned_after_retention_and_open_asks_never(agent, tmp_path, monkeypatch):
    """Lifecycle stage 3 (design §4.10): a read entry is kept for the retention window and then
    removed; an unread entry never ages out; an open `ask` is never pruned, and becomes prunable
    only once it closes or expires; the tally survives the pruning."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker = await mk("lead", unattended=True), await mk("w", unattended=True)
        await person.call("set_controllers", id=worker, add=[lead])
        async with LocalClient(caller=lead) as ld, LocalClient(caller=worker) as w:
            note = (await ld.call("msg", to=worker, text="read me"))["entry"]
            ask = (await ld.call("msg", to=worker, text="answer me", kind="ask"))["entry"]
            unread = (await ld.call("msg", to=worker, text="never read"))["entry"]
            await w.call("inbox")  # marks all three read
            entry_ids = {e["id"] for e in (await w.call("inbox"))["entries"]}
            assert entry_ids == {note["id"], ask["id"], unread["id"]}
            # `unread` un-read again, by hand: an unread entry never ages out
            agent.sessions[worker].inbox[-1].read_at = None
            monkeypatch.setattr(mail, "MAIL_RETENTION", timedelta(seconds=0))
            await agent._sweep_mail(datetime.now(UTC) + timedelta(seconds=1))
            left = [e["id"] for e in (await person.call("inbox", id=worker))["entries"]]
            assert left == [ask["id"], unread["id"]]  # the read note went; the open ask and the unread one stay
            # …and its tally with it (TD-066): a reply must name an entry the replier holds, so a pruned
            # thread cannot be revived and its tally would only ever grow the record
            threads = (await person.call("get", id=worker))["threads"]
            assert note["id"] not in threads and ask["id"] in threads
            await w.call("msg", text="here", kind="reply", reply_to=ask["id"])
            await agent._sweep_mail(datetime.now(UTC) + timedelta(seconds=1))
            assert [e["id"] for e in (await person.call("inbox", id=worker))["entries"]] == [unread["id"]]
            # …and a person's delete is the other way an entry leaves: the tally goes with it too
            await person.call("inbox_delete", msg=unread["id"], id=worker)
            assert unread["id"] not in (await person.call("get", id=worker))["threads"]
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_person_deletes_one_copy_and_no_session_may(agent, tmp_path):
    """Design §4.10 lifecycle: outside its stages an entry leaves only with its record or by a
    person's hand — the Inbox panel's delete. It removes that record's copy only (the sender's
    stays), and is refused to every session, the inbox's own included."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, worker = await mk("lead", unattended=True), await mk("w", unattended=True)
        await person.call("set_controllers", id=worker, add=[lead])
        async with LocalClient(caller=lead) as ld:
            got = await ld.call("msg", to=worker, text="done yet?", kind="ask")
        mid = got["entry"]["id"]
        for who in (worker, lead):
            async with LocalClient(caller=who) as c:
                with pytest.raises(AgentError, match="only by a person"):
                    await c.call("inbox_delete", id=worker, msg=mid)
        assert (await person.call("get", id=worker))["unread"] == 1
        with pytest.raises(AgentError, match="holds no entry"):
            await person.call("inbox_delete", id=worker, msg="m-nope")
        assert (await person.call("inbox_delete", id=worker, msg=mid)) == {"id": worker, "deleted": mid, "unread": 0}
        assert (await person.call("inbox", id=worker))["entries"] == []
        assert (await person.call("get", id=worker))["mail"]["open_asks"] == []
        # the sender's copy is its own and stays
        stored = json.loads((paths.sessions_dir() / f"{lead}.json").read_text())
        assert [e["id"] for e in stored["outbox"]] == [mid]
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_the_person_inbox_is_ungated_persisted_and_read_without_marking(agent, tmp_path):
    """Design §4.10 "A session reaches a person through the org's person inbox" (TD-052 step 2):
    any session lands there with no edge to anyone; the inbox is its own file in the store and a
    fresh host agent on the same store reloads it; a person (no caller, no id) reads it and sets
    nothing; a person's reply from it lands in the sender's inbox as a person's, closing the
    sender's `ask` on every copy; and a session answering a person's message answers into the
    person inbox. (Until 2026-09-19 this test ended on *an `ask` to the person expires on its bound
    like any other*; §4.10 *What a person is asked* is the rule that changed, and
    `test_an_ask_to_the_person_carries_no_bound_and_never_expires` is where it is now pinned.)"""
    from sessionorc.agent import HostAgent

    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        loner, other = await mk("loner", unattended=True), await mk("other", unattended=True)
        async with LocalClient(caller=loner) as lo:
            note = await lo.call("msg", to="person", text="fyi, TD-001 is done", about="TD-001")
            assert note["delivered"] == ["person"] and note["entry"]["to"] == ["person"]
            ask = (await lo.call("msg", to=["person"], text="merge it?", kind="ask"))["entry"]
        # a person cannot write to their own inbox: that is a board line
        with pytest.raises(AgentError, match="board line"):
            await person.call("msg", to="person", text="note to me")
        # persisted in its own file, and a fresh host agent on the same store reloads it
        assert paths.person_inbox_file().is_file()
        saved = json.loads(paths.person_inbox_file().read_text())["entries"]
        assert [e["id"] for e in saved] == [note["entry"]["id"], ask["id"]]
        fresh = HostAgent(tmux=agent.tmux)
        assert [e.id for e in fresh.person_inbox] == [note["entry"]["id"], ask["id"]]
        # a person reads it: the sender's role is shown, nothing is marked read
        seen = await person.call("inbox")
        assert seen["id"] == "person" and [e["from"] for e in seen["entries"]] == [loner, loner]
        assert all(e["from_role"] == "other" and e["read_at"] is None for e in seen["entries"])
        assert (await person.call("inbox", unread=True))["unread"] == 2
        # a person's reply from it lands in the sender's inbox as a person's and closes the ask
        reply = await person.call("msg", text="yes", kind="reply", reply_to=ask["id"])
        assert reply["delivered"] == [loner] and reply["closed"] == ask["id"]
        async with LocalClient(caller=loner) as lo:
            got = (await lo.call("inbox"))["entries"]
            assert [(e["from"], e["from_role"]) for e in got] == [("person", "person")]
            assert (await person.call("get", id=loner))["mail"]["open_asks"] == []
            closed = [e for e in (await person.call("inbox"))["entries"] if e["id"] == ask["id"]][0]
            assert closed["closed_by"] == reply["entry"]["id"]
            # a session answering the person's message answers into the person inbox
            back = await lo.call("msg", text="merged", kind="reply", reply_to=reply["entry"]["id"])
            assert back["delivered"] == ["person"]
            # a steer to the person does carry one, and lapses on it (§4.10, 2026-09-19)
            short = (await lo.call("msg", to="person", text="which?", kind="steer", default="A", bound=0.2))["entry"]
        await asyncio.sleep(0.4)
        await agent._sweep_mail(datetime.now(UTC))
        held = [e for e in (await person.call("inbox"))["entries"] if e["id"] == short["id"]][0]
        assert held["closed_reason"] == "lapsed" and not held["expired_at"]
        assert (await person.call("get", id=loner))["mail"]["open_asks"] == []
        # a session with no edge to `loner` still reaches the person: the person inbox is ungated
        async with LocalClient(caller=other) as o:
            assert (await o.call("msg", to="person", text="me too"))["delivered"] == ["person"]
        for sid in (loner, other):
            await person.call("kill", id=sid)


async def test_a_person_deletes_from_the_person_inbox_and_no_session_may(agent, tmp_path):
    """Design §4.10 lifecycle and §4.5a's top-bar **person inbox** delete: `inbox_delete` naming no
    session (or `person`) removes one entry from the org's person inbox, persisted; the sender's
    own copy stays; every session is refused, the sender included."""
    from sessionorc.agent import HostAgent

    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w = await mk("w", unattended=True)
        async with LocalClient(caller=w) as c:
            one = (await c.call("msg", to="person", text="one"))["entry"]["id"]
            two = (await c.call("msg", to="person", text="two", kind="ask"))["entry"]["id"]
            for target in (None, "person"):
                with pytest.raises(AgentError, match="only by a person"):
                    await c.call("inbox_delete", msg=one, **({"id": target} if target else {}))
        with pytest.raises(AgentError, match="person inbox holds no entry"):
            await person.call("inbox_delete", msg="m-nope")
        assert await person.call("inbox_delete", msg=one) == {
            "id": "person",
            "deleted": one,
            "declined": False,
            "unread": 1,
        }
        # `two` is an open `ask`: deleting it declines it rather than stripping it (§4.10, 2026-09-19),
        # so it stays in the inbox — closed, and read as such
        assert (await person.call("inbox_delete", id="person", msg=two))["declined"] is True
        assert [e["closed_reason"] for e in (await person.call("inbox"))["entries"]] == ["declined"]
        await person.call("inbox_delete", id="person", msg=two)  # a closed entry is then stripped
        assert (await person.call("inbox"))["entries"] == []
        assert HostAgent(tmux=agent.tmux).person_inbox == []  # persisted
        stored = json.loads((paths.sessions_dir() / f"{w}.json").read_text())
        assert [e["id"] for e in stored["outbox"]] == [one, two]  # the sender's copies are its own
        await person.call("kill", id=w)


async def test_the_person_inbox_depth_refuses_naming_the_board(agent, tmp_path, monkeypatch):
    """Design §4.10 (fourth review): the person inbox's depth fills exactly when the person is
    away, so its refusal — total or per sender — names user_attention.md with a Due: date."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        a, b = await mk("a", unattended=True), await mk("b", unattended=True)
        monkeypatch.setattr(mail, "PERSON_SENDER_DEPTH", 1)
        async with LocalClient(caller=a) as ca, LocalClient(caller=b) as cb:
            await ca.call("msg", to="person", text="one")
            with pytest.raises(AgentError, match=r"user_attention\.md with a Due: date") as e:
                await ca.call("msg", to="person", text="two")
            assert f"from {a}" in str(e.value)
            await cb.call("msg", to="person", text="b's first")  # the per-sender depth is per sender
            monkeypatch.setattr(mail, "PERSON_INBOX_DEPTH", 2)
            with pytest.raises(AgentError, match=r"user_attention\.md with a Due: date"):
                await cb.call("msg", to=["person"], text="full")
        assert len((await person.call("inbox"))["entries"]) == 2
        for sid in (a, b):
            await person.call("kill", id=sid)


# -- waking: the `wait` RPC and the wake decision (design §4.10, TD-052 step 3) ------------------


async def _team(person, tmp_path):
    """A lead and one worker it controls, both unattended: the worker may mail its lead (upward)."""
    mk = _mk(person, tmp_path)
    lead, worker = await mk("lead", unattended=True), await mk("w", unattended=True)
    await person.call("set_controllers", id=worker, add=[lead])
    for sid in (lead, worker):  # settled, so a shell starting up is not a member's change mid-test
        await wait_state(person, sid, "idle")
    return lead, worker


async def _blocked(agent, sid, timeout=6.0):
    end = asyncio.get_running_loop().time() + timeout
    while not agent.blocked_in_wait(sid):
        assert asyncio.get_running_loop().time() < end, f"{sid} never blocked in wait"
        await asyncio.sleep(0.05)


@pytest.fixture
async def start_wait(agent):
    """A lead's `ao wait`, on its own connection, running until it returns. Depends on `agent` so
    every connection it opened is closed before the agent is torn down: a server does not finish
    closing while a client still holds one, so a failed assertion would otherwise hang the run."""
    opened: list[tuple[LocalClient, asyncio.Future]] = []

    async def start(lead, timeout=30.0):
        client = LocalClient(caller=lead)
        await client.__aenter__()
        task = asyncio.ensure_future(client.call("wait", timeout=timeout))
        opened.append((client, task))
        await _blocked(agent, lead)
        return client, task

    yield start
    for client, task in opened:
        task.cancel()
        with contextlib.suppress(BaseException):
            await client.__aexit__()


async def test_wait_returns_on_a_members_change_and_on_new_mail_and_a_first_wait_on_nothing(
    agent, tmp_path, start_wait
):
    """The `wait` RPC keeps `ao wait`'s semantics (TD-049) and gains mail: a first wait records and
    wakes on nothing, a member's change returns it with the record, and new mail in the caller's
    own inbox returns it with headers only — `read_at` stays `ao inbox`'s — as a charged wake."""
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        async with LocalClient(caller=lead) as ld:
            first = await ld.call("wait", timeout=0.3)
        assert first == {"changed": [], "mail": [], "wake": None}
        # a member's change
        client, task = await start_wait(lead)
        await person.call("progress", id=worker, ref="TD-900", status="done", pr=7)
        got = await asyncio.wait_for(task, 10)
        await client.__aexit__()
        assert [s["id"] for s in got["changed"]] == [worker] and got["wake"] is None
        # new mail
        client, task = await start_wait(lead)
        async with LocalClient(caller=worker) as w:
            sent = await w.call("msg", to=lead, text="done with TD-900")
        got = await asyncio.wait_for(task, 10)
        await client.__aexit__()
        assert got["changed"] == []
        assert [m["id"] for m in got["mail"]] == [sent["entry"]["id"]] and "text" not in got["mail"][0]
        assert got["mail"][0]["from_role"] == "other"  # the worker is not the lead's controller
        assert got["wake"]["cause"] == "mail" and got["wake"]["charged"] and got["wake"]["covered"] == 1
        rec = await person.call("get", id=lead)
        assert rec["unread"] == 1 and rec["mail_decided"]["id"] == sent["entry"]["id"]
        # covered once: the next wait does not return for the same mail
        async with LocalClient(caller=lead) as ld:
            assert await ld.call("wait", timeout=0.5) == {"changed": [], "mail": [], "wake": None}
        # a wait is a read: a session with no grant waits, and an unknown scope is refused
        async with LocalClient(caller=worker) as w:
            assert (await w.call("wait", timeout=0))["wake"] is None
            with pytest.raises(AgentError, match="unknown scope"):
                await w.call("wait", timeout=0, scope="mine")
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_the_cursor_and_the_watermark_survive_a_fresh_host_agent(agent, tmp_path):
    """The per-caller cursor stays on disk under `waits/` and the watermark on the record, so a
    host-agent restart reads neither as a first wait: a change made while nobody waited returns
    the first wait on the new agent, and mail a wake already covered is not decided again."""
    from sessionorc.agent import HostAgent

    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        async with LocalClient(caller=lead) as ld:
            await ld.call("wait", timeout=0)  # records the cursor
        async with LocalClient(caller=worker) as w:
            await w.call("msg", to=lead, text="covered before the restart")
        async with LocalClient(caller=lead) as ld:
            assert (await ld.call("wait", timeout=5))["wake"]["cause"] == "mail"
        await person.call("progress", id=worker, ref="TD-901", status="claimed")
        fresh = HostAgent(tmux=agent.tmux)
        got = await fresh.rpc_wait(timeout=0, caller=lead)
        assert [s["id"] for s in got["changed"]] == [worker]
        assert got["mail"] == [] and got["wake"] is None  # the watermark came back with the record
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_wait_whose_connection_closes_is_dropped_and_never_charged(agent, tmp_path, start_wait):
    """No ghost waits (design §4.10): a CLI killed mid-wait closes its connection, the host agent
    cancels the wait, and mail that lands afterwards finds nobody blocked — no wake is recorded
    and the watermark does not move, so the lead's next real wait still gets it."""
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        client, task = await start_wait(lead)
        task.cancel()
        await client.__aexit__()  # the Ctrl-C
        for _ in range(100):
            if not agent.blocked_in_wait(lead):
                break
            await asyncio.sleep(0.05)
        assert not agent.blocked_in_wait(lead)
        async with LocalClient(caller=worker) as w:
            await w.call("msg", to=lead, text="into the void?")
        await agent.tick()
        await asyncio.sleep(0.3)
        rec = await person.call("get", id=lead)
        assert rec["wakes"] == [] and rec["mail_decided"] is None
        async with LocalClient(caller=lead) as ld:
            assert (await ld.call("wait", timeout=5))["wake"]["covered"] == 1
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_spent_budget_lands_mail_without_waking_and_the_window_refills_it(
    agent, tmp_path, start_wait, monkeypatch
):
    """Design §4.10 with `WAKE_BUDGET` at 1: the first mail returns the wait, charged; the second
    lands and the wait does not return for it, the watermark stays where it was, the record says
    the budget is spent and so does the sender's reply; roll the window to zero and the next tick
    returns the still-blocked wait for that same mail."""
    monkeypatch.setattr(mail, "WAKE_BUDGET", 1)
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        async with LocalClient(caller=worker) as w:
            first = await w.call("msg", to=lead, text="one")
            assert first["wake_budget_spent"] == []
            async with LocalClient(caller=lead) as ld:
                assert (await ld.call("wait", timeout=5))["wake"]["charged"] is True
            client, task = await start_wait(lead)
            second = await w.call("msg", to=lead, text="two")
            assert second["wake_budget_spent"] == [lead]  # the sender is told the mail wakes nobody
        await agent.tick()
        await asyncio.sleep(0.5)
        assert not task.done(), "a spent budget must not return the wait"
        rec = await person.call("get", id=lead)
        assert rec["mail_decided"]["id"] == first["entry"]["id"]  # a non-wake moves nothing
        assert rec["mail"]["wake_budget_spent"] is True and rec["unread"] == 2
        assert len(rec["wakes"]) == 1
        monkeypatch.setattr(mail, "WAKE_WINDOW", timedelta(seconds=0))
        await agent.tick()
        got = await asyncio.wait_for(task, 10)
        await client.__aexit__()
        assert [m["id"] for m in got["mail"]] == [second["entry"]["id"]] and got["wake"]["charged"] is True
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_members_change_with_mail_alongside_is_a_free_wake(agent, tmp_path, start_wait, monkeypatch):
    """A `wait` returning for a member's change spends nothing, even with the budget spent: it
    returns the mail too, advances the watermark, and is recorded `charged: False` — step 5
    measures these apart from mail-caused wakes."""
    monkeypatch.setattr(mail, "WAKE_BUDGET", 0)
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        async with LocalClient(caller=lead) as ld:
            await ld.call("wait", timeout=0)  # the cursor, so a member's change is a change
        client, task = await start_wait(lead)
        async with LocalClient(caller=worker) as w:
            sent = await w.call("msg", to=lead, text="done, PR up")
        await asyncio.sleep(0.3)
        assert not task.done()
        await person.call("progress", id=worker, ref="TD-902", status="done", pr=8)
        got = await asyncio.wait_for(task, 10)
        await client.__aexit__()
        assert [s["id"] for s in got["changed"]] == [worker]
        assert [m["id"] for m in got["mail"]] == [sent["entry"]["id"]]
        assert got["wake"]["cause"] == "member" and got["wake"]["charged"] is False
        rec = await person.call("get", id=lead)
        assert rec["mail_decided"]["id"] == sent["entry"]["id"]
        assert [(x["cause"], x["charged"], x["covered"]) for x in rec["wakes"]] == [("member", False, 1)]
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_persons_act_refills_the_budget_and_nothing_a_session_does(agent, tmp_path, monkeypatch):
    """Design §4.10 "Time and a person restore it; nothing a session does does": a person's
    message refills in full; a controller's message, a session's own traffic and a person looking
    (`seen`, reading the Inbox panel) refill nothing."""
    monkeypatch.setattr(mail, "WAKE_BUDGET", 1)
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=worker, text="go")
        async with LocalClient(caller=worker) as w:
            assert (await w.call("wait", timeout=5))["wake"]["charged"] is True
        assert (await person.call("get", id=worker))["mail"]["wake_budget_spent"] is True
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=worker, text="and again")  # a controller's mail refills nothing
        await person.call("seen", id=worker)
        await person.call("inbox", id=worker)
        assert (await person.call("get", id=worker))["mail"]["wake_budget_spent"] is True
        await person.call("msg", to=worker, text="from the person")
        rec = await person.call("get", id=worker)
        assert rec["mail"]["wake_budget_spent"] is False and rec["wake_refilled_at"]
        # the refill wakes it for everything still undecided, in one unit
        async with LocalClient(caller=worker) as w:
            assert (await w.call("wait", timeout=5))["wake"]["covered"] == 2
        for sid in (lead, worker):
            await person.call("kill", id=sid)


async def test_a_persons_session_is_never_woken_by_mail(agent, tmp_path):
    """§9 invariant 5: an interactive session blocked in `wait` is not returned by mail, and no
    wake is recorded; it has the unread count and the line, and nothing else."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        mine = await mk("mine")
        await person.call("msg", to=mine, text="for you")
        async with LocalClient(caller=mine) as m:
            assert await m.call("wait", timeout=0.5) == {"changed": [], "mail": [], "wake": None}
        assert (await person.call("get", id=mine))["wakes"] == []
        await person.call("kill", id=mine)


async def test_every_reply_to_a_session_with_unread_mail_carries_the_count(agent, tmp_path, monkeypatch):
    """Design §4.10 "a line on every `ao` reply": the host agent adds `mail` to the response
    envelope of a session with unread mail — on a result and on a refusal — and not to a person's,
    nor once the session has read it."""
    import json as _json

    async def raw(caller, method, **params):
        reader, writer = await asyncio.open_unix_connection(str(paths.socket_path()))
        writer.write((_json.dumps({"id": 1, "method": method, "params": params, "caller": caller}) + "\n").encode())
        await writer.drain()
        resp = _json.loads(await reader.readline())
        writer.close()
        return resp

    monkeypatch.setattr(mail, "WAKE_BUDGET", 0)
    async with LocalClient() as person:
        lead, worker = await _team(person, tmp_path)
        assert "mail" not in await raw(worker, "ping")
        await person.call("msg", to=worker, text="hello")
        ok = await raw(worker, "ping")
        assert ok["result"] == "pong" and ok["mail"] == {"unread": 1, "wake_budget_spent": True}
        refused = await raw(worker, "kill", id=lead)
        assert "error" in refused and refused["mail"]["unread"] == 1
        assert "mail" not in await raw(None, "ping")
        async with LocalClient(caller=worker) as w:
            await w.call("inbox")
        assert "mail" not in await raw(worker, "ping")
        for sid in (lead, worker):
            await person.call("kill", id=sid)


# -- what a person is asked: needed, steering, FYI (design §4.10, 2026-09-19; TD-069 step 0) -----


async def test_an_ask_to_the_person_is_asked_alone_carries_no_bound_and_never_expires(agent, tmp_path):
    """Design §4.10 *What a person is asked* — **Needed**. An `ask` to the person carries no bound
    and never expires: `--bound` on one is refused and the refusal names `steer`; the person is
    asked alone, so an `ask` or a `steer` naming the person names nobody else (a `note` may) and a
    `conflict` never names the person; both refusals say what to do instead. No sweep, however
    late, closes it."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w, other = await mk("w", unattended=True, team="alpha"), await mk("o", unattended=True, team="alpha")
        async with LocalClient(caller=w) as c:
            ask = (await c.call("msg", to="person", text="merge PR 9?", kind="ask"))["entry"]
            assert ask["bound"] is None and ask["closed_reason"] is None
            with pytest.raises(AgentError, match="never expires") as e:
                await c.call("msg", to="person", text="merge PR 9?", kind="ask", bound=60)
            assert "steer" in str(e.value) and "--default" in str(e.value)
            # the person is asked alone — and the refusal says to send the others a note
            for kind, extra in (("ask", {}), ("steer", {"default": "merge it"})):
                with pytest.raises(AgentError, match="asked alone") as e:
                    await c.call("msg", to=["person", other], text="both", kind=kind, **extra)
                assert "note to the others" in str(e.value)
            # a `note` may name both
            got = await c.call("msg", to=["person", other], text="fyi")
            assert sorted(got["delivered"]) == sorted(["person", other])
            # a conflict never names the person: it goes to the controllers, and the ask is the way up
            with pytest.raises(AgentError, match="a conflict never names the person") as e:
                await c.call("msg", to=["person", other], text="settle this", kind="conflict", cites=["s-1"])
            assert "--kind ask" in str(e.value)
        # nothing on the home's clock closes it, however late the sweep runs
        await agent._sweep_mail(datetime.now(UTC) + timedelta(days=30))
        held = [x for x in (await person.call("inbox"))["entries"] if x["id"] == ask["id"]][0]
        assert held["closed_reason"] is None and held["expired_at"] is None and held["bound"] is None
        for sid in (w, other):
            await person.call("kill", id=sid)


async def test_a_steer_requires_a_default_cleaned_and_capped_and_lapses_at_its_bound(agent, tmp_path):
    """Design §4.10 — **Steering**. A `steer` carries `default`, the one line it will go with,
    required and cleaned and capped as a `doing` line is (§4.8); only a `steer` carries one. At the
    bound it **lapses**: `closed_reason: lapsed` and `closed_at`, never `expired_at` — nothing
    failed — and the home tells the sender with a `system` note naming the entry."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w = await mk("w", unattended=True)
        async with LocalClient(caller=w) as c:
            with pytest.raises(AgentError, match="a steer says what it will do"):
                await c.call("msg", to="person", text="which branch?", kind="steer")
            with pytest.raises(AgentError, match="only a steer carries a default"):
                await c.call("msg", to="person", text="fyi", default="something")
            messy = "  branch off main\x07" + "x" * 400 + "\nand also rebase"
            steer = (await c.call("msg", to="person", text="which?", kind="steer", default=messy, bound=0.2))["entry"]
            # one line, control bytes stripped, capped — exactly as `ao doing` cleans its line (§4.8)
            assert steer["default"] == ("  branch off main" + "x" * 400)[: mail.DEFAULT_CAP].strip()
            assert "\n" not in steer["default"] and "\x07" not in steer["default"] and steer["bound"]
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))
        held = [x for x in (await person.call("inbox"))["entries"] if x["id"] == steer["id"]][0]
        assert held["closed_reason"] == "lapsed" and held["closed_at"] and held["expired_at"] is None
        rec = await person.call("get", id=w)
        assert rec["mail"]["expired"] == [] and rec["mail"]["open_asks"] == []  # nothing failed
        # the sender hears it, from `system`, naming the entry
        note = (await person.call("inbox", id=w))["entries"][-1]
        assert (note["from"], note["kind"], note["from_role"]) == ("system", "note", "system")
        assert note["text"] == f"steer {steer['id']} lapsed: go with your default"
        await person.call("kill", id=w)


async def test_a_steer_is_an_ask_for_every_other_rule(agent, tmp_path, monkeypatch):
    """Design §4.10: "**A `steer` is an `ask` for every other rule in this section**" — it counts
    toward the exchange tallies as an `ask` does, its first `reply` closes it uncounted, it is
    never pruned while open, and a reply after it closed is delivered as a `note`. The one rule
    that differs: **its bound runs whatever becomes of the addressee** — an addressee that exits
    leaves it no `pending`, and it lapses on time."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, w1, w2 = [await mk(n, unattended=True) for n in ("lead", "w1", "w2")]
        for sid in (w1, w2):
            await person.call("set_controllers", id=sid, add=[lead])
        async with LocalClient(caller=lead) as ld, LocalClient(caller=w1) as c1:
            steer = (await ld.call("msg", to=w1, text="rebase?", kind="steer", default="I will rebase"))["entry"]
            assert (await person.call("get", id=w1))["mail"]["open_asks"] == [steer["id"]]
            threads = (await person.call("get", id=lead))["threads"]
            assert threads[steer["id"]]["count"] == 1  # counted as an `ask` is
            # never pruned while open, however long it has been read
            await c1.call("inbox")
            monkeypatch.setattr(mail, "MAIL_RETENTION", timedelta(seconds=0))
            await agent._sweep_mail(datetime.now(UTC) + timedelta(seconds=1))
            assert [e["id"] for e in (await person.call("inbox", id=w1))["entries"]] == [steer["id"]]
            # the first reply closes it, uncounted
            first = await c1.call("msg", text="no, merge", kind="reply", reply_to=steer["id"])
            assert first["closed"] == steer["id"]
            assert (await person.call("get", id=lead))["threads"][steer["id"]]["count"] == 1
            held = [e for e in (await person.call("inbox", id=w1))["entries"] if e["id"] == steer["id"]][0]
            assert held["closed_reason"] == "replied" and held["closed_by"] == first["entry"]["id"]
            # a reply after it closed is a `note`: it counts
            await c1.call("msg", text="…or hold", kind="reply", reply_to=steer["id"])
            assert (await person.call("get", id=lead))["threads"][steer["id"]]["count"] == 2
            # an addressee that exits leaves an `ask` pending and a `steer` not: its bound runs on
            ask2 = (await ld.call("msg", to=w2, text="status?", kind="ask"))["entry"]
            s2 = (await ld.call("msg", to=w2, text="rebase?", kind="steer", default="rebase", bound=0.2))["entry"]
        monkeypatch.setattr(mail, "MAIL_RETENTION", timedelta(hours=12))  # back to normal: nothing pruned below
        await person.call("kill", id=w2)
        await wait_state(person, w2, "exited")
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))
        marks = (await person.call("get", id=lead))["mail"]
        assert marks["addressee_exited"] == [ask2["id"]] and marks["expired"] == []
        out = [
            e for e in json.loads((paths.sessions_dir() / f"{lead}.json").read_text())["outbox"] if e["id"] == s2["id"]
        ][0]  # noqa: E501
        assert out["closed_reason"] == "lapsed" and out["pending"] == []
        for sid in (lead, w1):
            await person.call("kill", id=sid)


async def test_every_close_path_writes_its_reason_and_the_fields_that_came_before_it(agent, tmp_path):
    """Design §4.10 "**One way of being closed**": `closed_reason` is set whenever an entry closes,
    by whatever path — `replied`, `declined`, `asker_gone`, `lapsed`, `go_with_it`, `expired` — and
    the fields that existed before it are kept and still written: `replied` sets `closed_by` and
    `closed_at`; `expired` sets `expired_at`; the other four set `closed_at` alone. An entry is
    open exactly when it is an `ask`, `steer` or `conflict` with no `closed_reason`."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        lead, w = await mk("lead", unattended=True), await mk("w", unattended=True)
        await person.call("set_controllers", id=w, add=[lead])
        seen: dict[str, dict] = {}

        async def person_entry(mid: str) -> dict:
            return [e for e in (await person.call("inbox"))["entries"] if e["id"] == mid][0]

        async with LocalClient(caller=w) as c, LocalClient(caller=lead) as ld:
            # replied
            a = (await c.call("msg", to="person", text="merge?", kind="ask"))["entry"]
            rep = await person.call("msg", text="yes", kind="reply", reply_to=a["id"])
            seen["replied"] = await person_entry(a["id"])
            assert seen["replied"]["closed_by"] == rep["entry"]["id"] and seen["replied"]["closed_at"]
            # declined: the person's delete on an open one
            b = (await c.call("msg", to="person", text="and this?", kind="ask"))["entry"]
            await person.call("inbox_delete", msg=b["id"])
            seen["declined"] = await person_entry(b["id"])
            # go_with_it
            g = (await c.call("msg", to="person", text="branch?", kind="steer", default="off main"))["entry"]
            await person.call("inbox_go_with_it", msg=g["id"])
            seen["go_with_it"] = await person_entry(g["id"])
            # lapsed
            lp = (await c.call("msg", to="person", text="fmt?", kind="steer", default="black", bound=0.2))["entry"]
            # expired: a session-to-session ask whose bound ran out
            x = (await ld.call("msg", to=w, text="status?", kind="ask", bound=0.2))["entry"]
            # asker_gone: the asker's record is closed
            k = (await c.call("msg", to="person", text="and finally?", kind="ask"))["entry"]
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))
        seen["lapsed"] = await person_entry(lp["id"])
        seen["expired"] = [e for e in (await person.call("inbox", id=w))["entries"] if e["id"] == x["id"]][0]
        await person.call("close", id=w)
        seen["asker_gone"] = await person_entry(k["id"])
        assert {r: seen[r]["closed_reason"] for r in seen} == {r: r for r in seen}
        # the legacy fields, exactly as the paragraph lists them
        assert seen["expired"]["expired_at"] and seen["expired"]["closed_at"] is None
        for r in ("declined", "asker_gone", "lapsed", "go_with_it"):
            assert seen[r]["closed_at"] and seen[r]["expired_at"] is None and seen[r]["closed_by"] is None
        # none of them is open any more, and nothing else in the person inbox closed
        assert (await person.call("get", id=lead))["mail"]["open_asks"] == []
        await person.call("kill", id=lead)


def test_an_entry_written_before_closed_reason_still_reads_as_closed():
    """Design §4.10: "Entries written before this date have no `closed_reason`; they read as closed
    when `closed_by` or `expired_at` is set, which is the rule until now." An old persisted inbox
    must load and read the same way it did."""
    from sessionorc.models import MailEntry

    old_open = MailEntry.from_dict({"id": "m-1", "from": "ao-a", "to": ["ao-b"], "at": "x", "kind": "ask", "text": "?"})
    old_replied = MailEntry.from_dict(
        {"id": "m-2", "from": "ao-a", "to": ["ao-b"], "at": "x", "kind": "ask", "text": "?", "closed_by": "m-9"}
    )
    old_expired = MailEntry.from_dict(
        {"id": "m-3", "from": "ao-a", "to": ["ao-b"], "at": "x", "kind": "ask", "text": "?", "expired_at": "y"}
    )
    assert old_open.open and not old_replied.open and not old_expired.open
    assert old_open.closed_reason is None and old_open.default is None and old_open.paused_at is None
    # an unknown field on an old record is dropped, as it always was, and the new ones default
    assert MailEntry.from_dict({**old_open.to_dict(), "who_knows": 1}).id == "m-1"


async def test_asker_gone_closes_the_persons_questions_on_close_and_forget_but_not_on_exit(agent, tmp_path):
    """Design §4.10 — an `ask` to the person cannot expire, so the other half is the **asker's**:
    closing or forgetting a record closes the open `ask`s and `steer`s it put to the person
    (`asker_gone`), and an asker that merely **exited** leaves them open, since a resume may still
    want the answer. Nothing is told: there is no one left to tell."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        a, b, c = [await mk(n, unattended=True) for n in ("a", "b", "c")]
        asks = {}
        for sid in (a, b, c):
            async with LocalClient(caller=sid) as s:
                asks[sid] = (await s.call("msg", to="person", text=f"from {sid}", kind="ask"))["entry"]["id"]
                asks[sid + ":steer"] = (await s.call("msg", to="person", text="or?", kind="steer", default="this"))[
                    "entry"
                ]["id"]

        async def reason(mid: str):
            return [e for e in (await person.call("inbox"))["entries"] if e["id"] == mid][0]["closed_reason"]

        # exited: still open
        await person.call("kill", id=a)
        await wait_state(person, a, "exited")
        await agent._sweep_mail(datetime.now(UTC))
        assert await reason(asks[a]) is None and await reason(asks[a + ":steer"]) is None
        # closed: both close, and nothing is written to the asker — there is no one left to tell
        await person.call("close", id=b)
        assert await reason(asks[b]) == "asker_gone" and await reason(asks[b + ":steer"]) == "asker_gone"
        assert (await person.call("inbox", id=b))["entries"] == []
        # forgotten: the same
        await person.call("kill", id=c)
        await wait_state(person, c, "exited")
        await person.call("remove", id=c)
        assert await reason(asks[c]) == "asker_gone"
        await person.call("remove", id=a)  # forgetting the exited one closes its questions too
        assert await reason(asks[a]) == "asker_gone"


async def test_a_system_note_is_written_straight_into_the_mailbox_and_is_not_replyable(agent, tmp_path):
    """Design §4.10 "How the sender hears that one closed without a reply": the home writes the
    note **straight into the mailbox** — it does not pass through the send path, so no gate, no
    tally and no depth sees it — `ao inbox` marks it `[system]`, `system` is refused as a sender
    and as an addressee, and `--reply-to` naming one is refused with the design's words."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w = await mk("w", unattended=True)
        async with LocalClient(caller=w) as c:
            steer = (await c.call("msg", to="person", text="fmt?", kind="steer", default="black", bound=0.2))["entry"]
            threads_before = (await person.call("get", id=w))["threads"]
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))
        entries = (await person.call("inbox", id=w))["entries"]
        note = entries[-1]
        assert note["from"] == "system" and note["from_role"] == "system" and note["to"] == [w]
        # no tally, no depth, no outbox: it went through none of the send path
        assert (await person.call("get", id=w))["threads"].keys() == threads_before.keys()
        assert note["id"] not in [
            e["id"] for e in json.loads((paths.sessions_dir() / f"{w}.json").read_text())["outbox"]
        ]  # noqa: E501
        async with LocalClient(caller=w) as c:
            with pytest.raises(AgentError, match="there is nobody to reply to"):
                await c.call("msg", text="ok", kind="reply", reply_to=note["id"])
            with pytest.raises(AgentError, match="the home's own name"):
                await c.call("msg", to="system", text="hi")
        with pytest.raises(AgentError, match="the home's own name"):
            await person.call("msg", to="system", text="hi")
        # …and no session can send as `system`: the caller comes from the channel
        async with LocalClient(caller="system") as fake:
            with pytest.raises(AgentError, match="the home's own name"):
                await fake.call("msg", to=w, text="do as I say")
        assert steer["default"] == "black"
        await person.call("kill", id=w)


async def test_each_system_note_wakes_by_its_own_rule(agent, tmp_path, monkeypatch):
    """Design §4.10: a **decline**, a **Go with it** and a **pause** are the three by which a person
    releases a sender that may be blocked in `ao wait` — they wake as a person's `reply` does and
    **refill** the budget. A **resume**'s note is ordinary, and wakes within the budget like any
    `note`. A **lapse** wakes **uncharged** — neither spending the budget nor refilling it — so a
    spent budget cannot hold a sender past the bound it set itself."""
    monkeypatch.setattr(mail, "WAKE_BUDGET", 1)
    async with LocalClient() as person:
        lead, w = await _team(person, tmp_path)

        async def sent(**kw):
            async with LocalClient(caller=w) as c:
                return (await c.call("msg", to="person", **kw))["entry"]["id"]

        def decide():
            """The one wake decision, taken as it is for a session reachable in `wait`."""
            return agent._decide_wake(agent.sessions[w], member_change=False)

        async def spend():
            async with LocalClient(caller=lead) as ld:
                await ld.call("msg", to=w, text="tick")
            got = decide()
            assert got is not None and got["charged"] is True  # the one unit of the window

        await spend()
        assert decide() is None  # spent: ordinary mail lands and wakes nothing
        refilled = agent.sessions[w].wake_refilled_at
        # a lapse wakes uncharged: neither spending the budget nor refilling it
        await sent(text="fmt?", kind="steer", default="black", bound=0.2)
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))
        got = decide()
        assert got is not None and got["charged"] is False and got["cause"] == "mail"
        assert agent.sessions[w].wake_refilled_at == refilled  # it refilled nothing
        assert decide() is None  # …and the budget is still spent: one uncharged wake, not a window
        # a decline wakes as a person's reply does, and refills
        ask = await sent(text="merge?", kind="ask")
        await person.call("inbox_delete", msg=ask)
        assert (refilled := agent.sessions[w].wake_refilled_at) is not None
        got = decide()
        assert got is not None and got["charged"] is True
        assert decide() is None  # the refilled unit is spent again
        # a pause refills too
        st = await sent(text="branch?", kind="steer", default="off main", bound=600)
        await person.call("inbox_pause", msg=st)
        assert agent.sessions[w].wake_refilled_at > refilled
        refilled = agent.sessions[w].wake_refilled_at
        assert decide()["charged"] is True
        # a resume's note is ordinary: it refills nothing and wakes within the budget, which is spent
        await person.call("inbox_resume", msg=st)
        assert agent.sessions[w].wake_refilled_at == refilled and decide() is None
        # *Go with it* refills, like a decline
        await person.call("inbox_go_with_it", msg=st)
        assert agent.sessions[w].wake_refilled_at > refilled
        assert decide()["charged"] is True
        for sid in (lead, w):
            await person.call("kill", id=sid)


async def test_the_persons_own_bookkeeping_is_refused_to_every_session_and_persists(agent, tmp_path):
    """Design §4.10 **Snooze** and **Pause**: `inbox_snooze`, `inbox_pause`, `inbox_resume` and
    *Go with it* are the person's alone, refused to every session as `inbox_delete` is; a snooze
    persists with the person inbox, tells the sender nothing, and changes nothing but what a page
    shows — the entry is still unread, still occupies the depths and, if it was open, stays open. A
    `steer` has **Pause** instead of Snooze, and only a person-addressed `steer` can be paused."""
    from sessionorc.agent import HostAgent

    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w, w2 = await mk("w", unattended=True), await mk("w2", unattended=True)
        await person.call("set_controllers", id=w2, add=[w])
        async with LocalClient(caller=w) as c:
            ask = (await c.call("msg", to="person", text="merge?", kind="ask"))["entry"]["id"]
            steer = (await c.call("msg", to="person", text="fmt?", kind="steer", default="black"))["entry"]["id"]
            to_session = (await c.call("msg", to=w2, text="fmt?", kind="steer", default="black"))["entry"]["id"]
            for rpc, kw in (
                ("inbox_snooze", {"until": "2026-09-20T08:00:00Z"}),
                ("inbox_pause", {}),
                ("inbox_resume", {}),
                ("inbox_go_with_it", {}),
            ):
                with pytest.raises(AgentError, match="the person's own bookkeeping"):
                    await c.call(rpc, msg=steer, **kw)
        # a snooze is set, persisted, and cleared by the same RPC with no `until`
        got = await person.call("inbox_snooze", msg=ask, until="2026-09-20T08:00:00Z")
        assert got == {"id": "person", "msg": ask, "snoozed_until": "2026-09-20T08:00:00Z"}
        held = [e for e in (await person.call("inbox"))["entries"] if e["id"] == ask][0]
        assert held["snoozed_until"] == "2026-09-20T08:00:00Z" and held["read_at"] is None
        assert held["closed_reason"] is None  # a snoozed ask stays open
        assert [e.snoozed_until for e in HostAgent(tmux=agent.tmux).person_inbox if e.id == ask] == [
            "2026-09-20T08:00:00Z"
        ]
        assert (await person.call("inbox"))["unread"] == 2  # it is still unread, and still counted
        assert (await person.call("inbox_snooze", msg=ask))["snoozed_until"] is None
        # a steer has Pause, not Snooze; an ask has no clock to pause
        with pytest.raises(AgentError, match="it has Pause"):
            await person.call("inbox_snooze", msg=steer, until="2026-09-20T08:00:00Z")
        with pytest.raises(AgentError, match="only a steer can be paused"):
            await person.call("inbox_pause", msg=ask)
        with pytest.raises(AgentError, match="Go with it answers a steer"):
            await person.call("inbox_go_with_it", msg=ask)
        # a `steer` addressed to a session is not in the person inbox at all: the pause is the person's
        for rpc in ("inbox_pause", "inbox_snooze", "inbox_go_with_it"):
            with pytest.raises(AgentError, match="the person inbox holds no entry"):
                await person.call(rpc, msg=to_session)
        for sid in (w, w2):
            await person.call("kill", id=sid)


async def test_pause_stops_the_clock_and_resume_gives_back_what_was_left(agent, tmp_path):
    """Design §4.10 **Pause**: `paused_at` stops the bound running and **the sweep skips the entry
    outright, whatever `bound` reads**; **Resume** moves `bound` later by the time it was held and
    clears `paused_at` **in one step**, so the sweep never sees a resumed entry with its old bound;
    and **Reply** or *Go with it* closes a paused `steer` as it closes a running one."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w = await mk("w", unattended=True)
        async with LocalClient(caller=w) as c:
            st = (await c.call("msg", to="person", text="fmt?", kind="steer", default="black", bound=0.2))["entry"]
        await person.call("inbox_pause", msg=st["id"])
        await asyncio.sleep(1.3)
        await agent._sweep_mail(datetime.now(UTC))  # long past its bound, and skipped

        async def now_held():
            return [e for e in (await person.call("inbox"))["entries"] if e["id"] == st["id"]][0]

        paused = await now_held()
        assert paused["paused_at"] and paused["closed_reason"] is None and paused["bound"] == st["bound"]
        with pytest.raises(AgentError, match="already paused"):
            await person.call("inbox_pause", msg=st["id"])
        # resume gives back what was left: the bound moves later by the time it was held
        got = await person.call("inbox_resume", msg=st["id"])
        after = await now_held()
        assert after["paused_at"] is None and got["bound"] == after["bound"] > st["bound"]
        with pytest.raises(AgentError, match="is not paused"):
            await person.call("inbox_resume", msg=st["id"])
        # a paused steer is closed by Go with it exactly as a running one is
        async with LocalClient(caller=w) as c:
            st2 = (await c.call("msg", to="person", text="branch?", kind="steer", default="main", bound=600))["entry"]
        await person.call("inbox_pause", msg=st2["id"])
        await person.call("inbox_go_with_it", msg=st2["id"])
        closed = [e for e in (await person.call("inbox"))["entries"] if e["id"] == st2["id"]][0]
        assert closed["closed_reason"] == "go_with_it" and closed["paused_at"]
        await person.call("kill", id=w)


async def test_the_person_inbox_depths_count_read_but_unanswered_questions(agent, tmp_path, monkeypatch):
    """Design §4.10: from 2026-09-19 the two depths count **every entry that is unread or is an
    open `ask` or `steer`** — one set, each entry once — so reading the page frees no slot an
    unanswered question still holds, and one worker cannot fill the Inbox with asks that never
    lapse. The refusal still names the board."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w = await mk("w", unattended=True)
        monkeypatch.setattr(mail, "PERSON_SENDER_DEPTH", 3)
        async with LocalClient(caller=w) as c:
            await c.call("msg", to="person", text="fyi")  # a note: it frees its slot once read
            one = (await c.call("msg", to="person", text="a?", kind="ask"))["entry"]["id"]
            await c.call("msg", to="person", text="b?", kind="steer", default="x")
        # the person reads the page: nothing is unread, and the two open questions still hold theirs
        for e in agent.person_inbox:
            e.read_at = datetime.now(UTC).isoformat()
        agent.person_store.save(agent.person_inbox)
        assert (await person.call("inbox"))["unread"] == 0
        async with LocalClient(caller=w) as c:
            # under the old rule (unread only) the count would be zero here and this would be the
            # first of many; it is the third slot, and the fourth question is refused
            assert (await c.call("msg", to="person", text="c?", kind="ask"))["delivered"] == ["person"]
            with pytest.raises(AgentError, match=r"user_attention\.md with a Due: date") as err:
                await c.call("msg", to="person", text="d?", kind="ask")
            assert "unread or unanswered" in str(err.value)
            # answering one frees its slot; the read note never held one
            await person.call("msg", text="yes", kind="reply", reply_to=one)
            assert (await c.call("msg", to="person", text="d?", kind="ask"))["delivered"] == ["person"]
        await person.call("kill", id=w)


async def test_three_open_asks_earn_one_line_of_advice_beside_the_id(agent, tmp_path):
    """Design §4.10 "Which to send is the brief's to teach": one line of advice from the home, not
    a gate — when a session sends an `ask` to the person while it already holds three or more open
    ones, counted **before** this send, the reply carries it beside the id, **every time** that is
    so. It is never a refusal: the entry lands either way."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        w, other = await mk("w", unattended=True), await mk("o", unattended=True)
        async with LocalClient(caller=w) as c, LocalClient(caller=other) as o:
            for i in range(3):
                got = await c.call("msg", to="person", text=f"q{i}?", kind="ask")
                assert got["advice"] is None  # counted before the send: the third one is still clear
            fourth = await c.call("msg", to="person", text="q3?", kind="ask")
            assert fourth["advice"] == "you have 3 open asks to the person: is this one needed, or a steer?"
            assert fourth["entry"]["id"] and fourth["delivered"] == ["person"]
            fifth = await c.call("msg", to="person", text="q4?", kind="ask")
            assert "4 open asks" in fifth["advice"]  # every time that is so, not once
            # a `steer` and a `note` earn none, and the count is this sender's
            assert (await c.call("msg", to="person", text="?", kind="steer", default="x"))["advice"] is None
            assert (await c.call("msg", to="person", text="fyi"))["advice"] is None
            assert (await o.call("msg", to="person", text="q?", kind="ask"))["advice"] is None
            # closing one takes it back below the line
            for mid in [e["id"] for e in (await person.call("inbox"))["entries"] if e["from"] == w][:3]:
                await person.call("inbox_delete", msg=mid)
            assert (await c.call("msg", to="person", text="q5?", kind="ask"))["advice"] is None
        for sid in (w, other):
            await person.call("kill", id=sid)


async def test_an_envelope_carries_its_senders_team(agent, tmp_path):
    """Design §4.10: "**An envelope carries its sender's `team`**" from this date, stamped by the
    home at send beside `from` — a join to the sender's record fails exactly when the page most
    needs it, after that record is gone. A session with no team, and a person, stamp none."""
    async with LocalClient() as person:
        mk = _mk(person, tmp_path)
        badged, plain = await mk("t1", unattended=True, team="alpha"), await mk("p", unattended=True)
        async with LocalClient(caller=badged) as t, LocalClient(caller=plain) as p:
            assert (await t.call("msg", to="person", text="from a team"))["entry"]["team"] == "alpha"
            assert (await p.call("msg", to="person", text="from nobody"))["entry"]["team"] is None
        assert (await person.call("msg", to=plain, text="from the person"))["entry"]["team"] is None
        # it survives the sender: the stamp is on the envelope, not a join
        await person.call("kill", id=badged)
        await wait_state(person, badged, "exited")
        await person.call("remove", id=badged)
        left = [e for e in (await person.call("inbox"))["entries"] if e["from"] == badged][0]
        assert left["team"] == "alpha"
        await person.call("kill", id=plain)


# -- resume, and the two halves of it step 0 got wrong (review of PR #245) -----------------------


async def test_a_resumed_askers_questions_to_the_person_are_not_closed_as_asker_gone(agent, hookstub, tmp_path):
    """Design §4.10 "Ids follow the move" against *What a person is asked*: an `ask` to the person
    never expires, so it outlives the record that sent it, and `asker_gone` is the only thing that
    closes it. A **resume** is not a gone asker — the conversation continues under the new id — so
    the person inbox's copies follow the move: their `from` is rewritten, the superseded record is
    skipped when it is forgotten a day later, the person's reply reaches the **new** record, a
    `system` note about the entry lands there, and the per-sender depth counts them under the new
    id. (Step 0 closed all of them the moment `CLOSED_KEEP` expired the old record.)"""
    async with LocalClient() as person:
        for d in ("w", "w2"):
            (tmp_path / d).mkdir()
        w = (await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True))[
            "id"
        ]
        await person.call("hook", session=w, adapter_id="conv-69", state="idle")
        async with LocalClient(caller=w) as c:
            ask = (await c.call("msg", to="person", text="merge PR 9?", kind="ask"))["entry"]["id"]
            steer = (await c.call("msg", to="person", text="fmt?", kind="steer", default="black", bound=600))["entry"]
        await person.call("kill", id=w)
        await wait_state(person, w, "exited")
        w2 = (
            await person.call(
                "create",
                name="w2",
                dir=str(tmp_path / "w2"),
                adapter=hookstub.name,
                unattended=True,
                resume="conv-69",
            )
        )["id"]
        assert w2 != w and (await person.call("get", id=w))["superseded_by"] == w2
        # the ids followed the move: the person inbox names the conversation that is running
        held = {e["id"]: e for e in (await person.call("inbox"))["entries"]}
        assert [held[ask]["from"], held[steer["id"]]["from"]] == [w2, w2]
        # the superseded record is forgotten a day later, and closes nothing
        agent.sessions[w].closed_at = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
        await agent.tick()
        assert w not in agent.sessions
        held = {e["id"]: e for e in (await person.call("inbox"))["entries"]}
        assert [held[ask]["closed_reason"], held[steer["id"]]["closed_reason"]] == [None, None]
        # a `system` note about the steer reaches the new record, not the ghost of the old one
        await person.call("inbox_pause", msg=steer["id"])
        told = (await person.call("inbox", id=w2))["entries"]
        assert [(e["from"], e["text"]) for e in told] == [
            ("system", f"steer {steer['id']} paused by the person: do not take your default yet")
        ]
        # the person's reply reaches the new record too, and closes the question there
        rep = await person.call("msg", text="yes, merge it", kind="reply", reply_to=ask)
        assert rep["delivered"] == [w2] and rep["closed"] == ask
        assert [e["text"] for e in (await person.call("inbox", id=w2))["entries"]][-1] == "yes, merge it"
        # …and the per-sender depth counts them under the new id, and none under the old one

        def counted(sid):
            return sum(1 for e in agent.person_inbox if e.from_ == sid and (not e.read_at or e.open))

        assert (counted(w2), counted(w)) == (2, 0)  # the paused steer, still open, and the unread ask
        await person.call("kill", id=w2)


async def test_a_lapse_wakes_uncharged_across_a_resume_and_a_restart(agent, hookstub, tmp_path, monkeypatch):
    """Design §4.10: a lapse wakes **uncharged**, so a spent budget cannot hold a sender past the
    bound it set itself. The mark is on the note, not beside the records, so it survives the two
    things that can happen between the lapse and the wake it earns: the sender being **resumed**
    (the note moves with the conversation) and the host agent **restarting** (it is reloaded).
    Step 0 kept it in a set on the agent, and lost it to both."""
    from sessionorc.agent import HostAgent

    monkeypatch.setattr(mail, "WAKE_BUDGET", 0)  # spent: nothing but a free wake fires
    async with LocalClient() as person:
        for d in ("w", "w2"):
            (tmp_path / d).mkdir()
        w = (await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True))[
            "id"
        ]
        await person.call("hook", session=w, adapter_id="conv-70", state="idle")
        async with LocalClient(caller=w) as c:
            await c.call("msg", to="person", text="fmt?", kind="steer", default="black", bound=0.2)
        await person.call("kill", id=w)
        await wait_state(person, w, "exited")
        await asyncio.sleep(0.3)
        await agent._sweep_mail(datetime.now(UTC))  # it lapses on time, exited or not
        assert [e.uncharged for e in agent.sessions[w].inbox] == [True]
        w2 = (
            await person.call(
                "create", name="w2", dir=str(tmp_path / "w2"), adapter=hookstub.name, unattended=True, resume="conv-70"
            )
        )["id"]
        # the note moved with the conversation, its mark with it
        assert [(e.from_, e.uncharged) for e in agent.sessions[w2].inbox] == [("system", True)]
        # a fresh host agent on the same store reloads the mark: a restart does not charge the wake
        assert [e.uncharged for e in HostAgent(tmux=agent.tmux).sessions[w2].inbox] == [True]
        got = agent._decide_wake(agent.sessions[w2], member_change=False)
        assert got is not None and got["charged"] is False and got["cause"] == "mail"
        assert agent.sessions[w2].wake_refilled_at is None  # it refilled nothing either
        await person.call("kill", id=w2)
