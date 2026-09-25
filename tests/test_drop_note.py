"""A person's **Drop** tells the session (design §4.5a *Focus side panel → Reports*, §4.10; TD-150
slice 3): a `system` note in its inbox that wakes it as a person's act does — and the session's own
drop, which it already knows about, files none."""

import pytest

from sessionorc.client import LocalClient

pytestmark = pytest.mark.integration


async def test_a_persons_drop_files_a_system_note_and_a_sessions_own_does_not(agent, tmp_path):
    async with LocalClient() as person:
        w = (
            await person.call(
                "create", name="w", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"], unattended=True
            )
        )["id"]
        async with LocalClient(caller=w) as me:
            await me.call("progress", id=w, ref="TD-901", status="claimed")
            await me.call("progress", id=w, ref="TD-902", status="claimed")
            await me.call("progress", id=w, ref="TD-902", status="dropped", why="not mine after all")
        assert (await person.call("inbox", id=w))["entries"] == []  # its own drop: nothing to tell it
        await person.call("progress", id=w, ref="td-901", status="dropped", why="dropped from Focus")
        entries = (await person.call("inbox", id=w))["entries"]
        assert [(e["from"], e["kind"]) for e in entries] == [("system", "note")]
        assert entries[0]["text"] == (
            "your claim on TD-901 was dropped by the person — the lease is gone; claim again if you still hold the work"
        )
        rec = agent.sessions[w]
        assert rec.wake_refilled_at  # it wakes as a person's act does: the budget is refilled
        await person.call("kill", id=w)


@pytest.mark.unit
def test_a_declared_claim_carries_its_branchs_open_pr_beside_it_and_nothing_else():
    """TD-150 (§4.5a *Reports*): the tick's derived entry on a declared claim's reference is refused
    (§9 invariant 10) all but its PR, kept as `review_pr` while open and cleared once merged; the
    session's own fields never move, and a re-declaration keeps what the tick last saw."""
    from sessionorc.models import ProgressEntry, Session

    s = Session(id="ao-w", name="w", kind="interactive", adapter="shell", dir="/w")
    assert s.report_progress(ProgressEntry(ref="TD-901", why="mine"))
    derived = ProgressEntry(ref="TD-901", status="claimed", pr=77, source="derived", branch="td901-x")
    assert s.report_progress(derived) is False  # refused, as ever (§9 invariant 10)…
    assert s.note_review(derived) is True  # …but the tick's note of its PR changed the record
    (e,) = s.progress
    assert (e.source, e.status, e.pr, e.why, e.branch, e.review_pr) == ("declared", "claimed", None, "mine", None, 77)
    assert s.note_review(derived) is False  # the same again changes nothing
    assert s.report_progress(ProgressEntry(ref="TD-901", why="still mine"))  # re-declared
    assert s.progress[0].review_pr == 77 and s.progress[0].why == "still mine"
    assert s.note_review(ProgressEntry(ref="TD-901", status="done", pr=77, source="derived")) is True
    assert s.progress[0].review_pr is None and s.progress[0].status == "claimed"  # merged: no review held
    assert ProgressEntry.from_dict(s.progress[0].to_dict()) == s.progress[0]  # it round-trips
    # a PR closed without a merge holds no review either, and done or dropped carries none
    from sessionorc.models import PR_CLOSED

    assert s.note_review(derived) is True and s.progress[0].review_pr == 77
    closed = ProgressEntry(ref="TD-901", status="claimed", pr=77, source="derived", why=PR_CLOSED)
    assert s.note_review(closed) is True and s.progress[0].review_pr is None
    assert s.note_review(derived) is True
    assert s.report_progress(ProgressEntry(ref="TD-901", status="done")) and s.progress[0].review_pr is None
    # an RPC's refused derived entry is refused, and says so: only the tick notes its PR
    assert s.report_progress(ProgressEntry(ref="TD-901", status="claimed", pr=78, source="derived")) is False
