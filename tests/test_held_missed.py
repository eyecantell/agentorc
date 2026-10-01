"""TD-258 slice 2, design §6 *Keeping a team running* rule 11: merged without its read. The home
reads a merged PR's files against the member's `held:` globs and looks for the reader's reply in
the member's mail; a held PR with none is a crossing on `held_missed`, one fixed line to the
member (or the clause on its next `ao` reply) and one `system` note to the person."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from sessionorc import held
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import PERSON, SYSTEM, MailEntry, ProgressEntry, Session

REVIEW = {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}
CREATED = "2026-09-29T12:00:00Z"
MERGED = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)
LATER = MERGED + held.GRACE + timedelta(minutes=1)
HELD, FREE = ["src/sessionorc/agent_tick.py", "docs/design.md"], ["src/agentorc/cli.py"]


def _rec(reader: str = "techlead") -> Session:
    s = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="", created=CREATED)
    s.review = {**REVIEW, "reader": reader}
    return s


def _ask(s: Session, to: str, pr: int = 845, root: str = "m-1", id: str = "m-1") -> None:
    s.outbox.append(MailEntry(id=id, from_=s.id, to=[to], at="t", kind="ask", text="read it", root=root, pr=pr))


def _reply(s: Session, frm: str, root: str = "m-1") -> None:
    s.inbox.append(MailEntry(id="m-r", from_=frm, to=[s.id], at="t", kind="reply", text="merged", root=root))


def test_the_read_is_a_named_readers_reply_on_the_prs_ask():
    s = _rec()
    assert held.read_by(s, 845) is None
    _ask(s, "ao-x-tl")
    assert held.read_by(s, 845) is None, "an ask is not a read"
    _reply(s, "ao-x-other")
    assert held.read_by(s, 845) is None, "a reply from someone the ask did not name is not its reader's"
    _reply(s, "ao-x-tl")
    assert held.read_by(s, 845) == "ao-x-tl" and held.read_by(s, 846) is None


def test_an_ask_passed_to_the_person_on_its_thread_is_read_by_the_person():
    s = _rec()
    _ask(s, "ao-x-tl")
    _ask(s, PERSON, id="m-2")  # the seat did not answer in its bound: asked again on the thread
    _reply(s, PERSON)
    assert held.read_by(s, 845) == PERSON


def test_reader_person_takes_the_persons_reply_alone():
    s = _rec("person")
    _ask(s, "ao-x-tl")
    _reply(s, "ao-x-tl")
    assert held.read_by(s, 845) is None
    s.checks = [{"pr": 845, "read_by": "ao-x-tl"}]
    assert held.read_by(s, 845) is None
    _ask(s, PERSON, id="m-2")
    _reply(s, PERSON)
    assert held.read_by(s, 845) == PERSON


def test_a_pruned_reply_is_still_the_read_rule_10_kept():
    s = _rec()
    s.checks = [{"pr": 845, "read_by": "ao-x-tl"}]
    assert held.read_by(s, 845) == "ao-x-tl" and held.read_by(s, 846) is None


def test_the_fixed_words():
    c = held.crossing(845, ["src/sessionorc/agent_tick.py"], "t")
    assert c == {"pr": 845, "at": "t", "paths": ["src/sessionorc/agent_tick.py"]}
    assert held.line(c, "techlead") == (
        "[agentorc] PR #845 touched held paths (`src/sessionorc/agent_tick.py`) and merged without the "
        'techlead\'s read — a held PR waits for `ao msg --kind ask --pr <n> <seat> "…"` and the reply before the merge'
    )
    many = held.crossing(9, [f"src/sessionorc/{n}.py" for n in "abcde"], "t")
    assert "(`src/sessionorc/a.py`, `src/sessionorc/b.py`, `src/sessionorc/c.py` and 2 more)" in held.said(many, "x")
    assert "without the person's read" in held.line(c, "person") and "--pr <n> person" in held.line(c, "person")
    assert held.clause([c, {**many, "told": "t"}], "techlead") == (
        "PR #845 touched held paths (`src/sessionorc/agent_tick.py`) and merged without the techlead's read"
    )
    assert held.note(c, "techlead", "ao-x-w").startswith("ao-x-w: PR #845 touched held paths")


def test_the_read_of_a_pr_gives_its_files_and_its_merge(monkeypatch):
    import json
    import subprocess

    def gh(body):
        return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    files = [{"path": "src/x.py"}]
    monkeypatch.setattr(held.subprocess, "run", gh({"files": files, "changedFiles": 1, "mergedAt": None}))
    assert held.pr_read(7) == (["src/x.py"], None) and held.pr_files(7) == ["src/x.py"]
    merged = {"files": files, "changedFiles": 1, "mergedAt": "2026-09-29T13:00:00Z"}
    monkeypatch.setattr(held.subprocess, "run", gh(merged))
    assert held.pr_read(7) == (["src/x.py"], MERGED)
    monkeypatch.setattr(held.subprocess, "run", gh({**merged, "mergedAt": "yesterday"}))
    with pytest.raises(RuntimeError):
        held.pr_read(7)


# ── the tick ────────────────────────────────────────────────────────────────────────────────────


class _Gh:
    """What `gh` would say of each PR, and what was asked."""

    def __init__(self, monkeypatch):
        self.prs: dict[int, tuple[list[str], datetime | None]] = {}
        self.asked: list[int] = []
        monkeypatch.setattr(held, "pr_read", self._read)

    def _read(self, pr, cwd=None):
        self.asked.append(pr)
        if pr not in self.prs:
            raise RuntimeError("gh could not read it")
        return self.prs[pr]


def _member(agent, name: str = "w", root: str = "/repo", **fields) -> Session:
    s = Session(id=f"ao-x-{name}", name=name, kind="agent", adapter="shell", dir="", repo=root, created=CREATED)
    s.host, s.supervised, s.unattended, s.state, s.review = agent.host, True, True, "working", dict(REVIEW)
    for k, v in fields.items():
        setattr(s, k, v)
    agent.sessions[s.id] = s
    agent._repos[root] = {}
    return s


def _done(s: Session, pr: int, source: str = "derived") -> None:
    s.progress = [*s.progress, ProgressEntry(ref=f"TD-{pr}", status="done", pr=pr, source=source)]


def _fyi(agent) -> list[str]:
    return [e.text for e in agent.person_inbox if e.from_ == SYSTEM]


async def test_a_held_pr_the_seat_replied_on_is_no_crossing(agent, monkeypatch):
    gh, s = _Gh(monkeypatch), _member(agent)
    _done(s, 845)
    gh.prs[845] = (HELD, MERGED)
    _ask(s, "ao-x-tl")
    _reply(s, "ao-x-tl")
    await agent._held_pass([s], LATER)
    assert s.held_missed == [] and _fyi(agent) == [] and gh.asked == [845]
    await agent._held_pass([s], LATER)
    assert gh.asked == [845], "settled: read once"


async def test_a_held_pr_with_no_reply_is_an_entry_a_note_and_the_second_is_two(agent, monkeypatch):
    gh, s = _Gh(monkeypatch), _member(agent)
    _done(s, 845)
    gh.prs[845] = (HELD, MERGED)
    await agent._held_pass([s], MERGED + timedelta(minutes=2))
    assert s.held_missed == [], "the reader merges, then replies: inside the grace nothing is written"
    await agent._held_pass([s], LATER)
    (c,) = s.held_missed
    assert (c["pr"], c["paths"]) == (845, ["src/sessionorc/agent_tick.py"]) and "told" not in c
    assert _fyi(agent) == [held.note(c, "techlead", s.id)]
    await agent._held_pass([s], LATER)
    assert len(s.held_missed) == 1 and len(_fyi(agent)) == 1 and gh.asked == [845, 845], "a crossing is read no more"
    _done(s, 851, source="declared")  # the member's own word is read as a derived one is
    gh.prs[851] = (HELD, MERGED)
    await agent._held_pass([s], LATER)
    assert [c["pr"] for c in s.held_missed] == [845, 851] and len(_fyi(agent)) == 2, "two entries: the row's mark"


async def test_what_is_not_a_crossing(agent, monkeypatch):
    gh = _Gh(monkeypatch)
    free = _member(agent, "free")
    _done(free, 1)
    gh.prs[1] = (FREE, MERGED)
    plain = _member(agent, "plain", review=None)
    _done(plain, 2)
    gh.prs[2] = (HELD, MERGED)
    early = _member(agent, "early")
    _done(early, 3)
    gh.prs[3] = (HELD, datetime(2026, 9, 29, 11, 0, tzinfo=UTC))  # merged before the record was created
    unread = _member(agent, "unread")
    _done(unread, 4)  # gh cannot say: no reading
    open_ = _member(agent, "open")
    _done(open_, 5, source="declared")
    gh.prs[5] = (HELD, None)  # reported done, not merged yet
    noroot = _member(agent, "noroot")
    noroot.repo = "/elsewhere"
    _done(noroot, 6)
    gh.prs[6] = (HELD, MERGED)
    recs = [free, plain, early, unread, open_, noroot]
    await agent._held_pass(recs, LATER)
    assert all(r.held_missed == [] for r in recs) and _fyi(agent) == []
    assert sorted(gh.asked) == [1, 3, 4, 5], "no review, no registry root: not asked"
    await agent._held_pass(recs, LATER)
    assert sorted(gh.asked) == [1, 3, 4, 4, 5, 5], "a failed read and an open PR are read again; the settled are not"
    gh.prs[5] = (HELD, MERGED)
    await agent._held_pass(recs, LATER)
    assert [c["pr"] for c in open_.held_missed] == [5], "merged since, with no read"


async def test_a_pass_reads_a_handful_the_longest_unread_first(agent, monkeypatch):
    gh, s = _Gh(monkeypatch), _member(agent)
    for pr in range(1, held.READS + 3):
        _done(s, pr)  # gh fails on each: read again every pass
    await agent._held_pass([s], LATER)
    assert gh.asked == list(range(1, held.READS + 1))
    await agent._held_pass([s], LATER)
    assert gh.asked[held.READS :] == [held.READS + 1, held.READS + 2, 1, 2, 3]


async def test_a_working_member_reads_the_clause_once_on_its_next_reply(agent, tmp_path):
    from sessionorc import client as clientmod

    async with LocalClient() as person:
        params = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}
        w = (await person.call("create", name="w", supervised=True, prompt="the brief", **params))["id"]
    rec = agent.sessions[w]
    rec.review = dict(REVIEW)
    rec.held_missed = [held.crossing(845, ["src/sessionorc/agent_tick.py"], "t")]
    await agent._held_line(rec, datetime.now(UTC))
    assert "told" not in rec.held_missed[0], "not idle at a composer: nothing is typed"
    async with LocalClient(caller=w) as wc:
        got = await wc.call("get", id=w)
        assert clientmod.last_mail["cadence"] == (
            "PR #845 touched held paths (`src/sessionorc/agent_tick.py`) and merged without the techlead's read"
        )
        assert got["held_missed"][0]["pr"] == 845, "the record's view carries its crossings"
        assert rec.held_missed[0]["told"]
        await wc.call("get", id=w)
        assert not (clientmod.last_mail or {}).get("cadence"), "said once"
    clientmod.last_mail = None


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


@pytest.mark.integration
async def test_an_idle_member_is_typed_the_line_once(agent, composerstubs, tmp_path):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        params = {"name": "w", "dir": str(tmp_path), "adapter": "composer0", "unattended": True, "supervised": True}
        sid = (await person.call("create", prompt="the brief", **params))["id"]
        assert await wait_for(lambda: _painted(agent, sid), timeout=5), "the composer child never painted its prompt"
        rec = agent.sessions[sid]
        rec.review = dict(REVIEW)
        await agent.rpc_hook(sid, state="idle")
        rec.held_missed = [held.crossing(845, ["src/sessionorc/agent_tick.py"], "t")]
        await agent._keep_running(now)
        await agent._keep_running(now)
        lines = [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]
        assert (
            len(lines) == 1 and "PR #845 touched held paths" in lines[0] and "without the techlead's read" in lines[0]
        )
        assert rec.held_missed[0]["told"] and rec.sends[-1].from_ == "system"
        await person.call("kill", id=sid)


def test_the_field_is_the_homes_and_the_match_is_one():
    from agentorc import review
    from sessionorc import models

    assert "held_missed" in models.HOME_OWNED
    assert review.held_paths is held.held_paths and review.pr_files is held.pr_files and review.matches is held.matches


async def test_a_pr_merged_longer_ago_than_the_mail_is_kept_is_not_judged(agent, monkeypatch):
    """A restarted home reads each PR once more, and by then the reader's reply may be pruned."""
    from sessionorc import mail

    gh, s = _Gh(monkeypatch), _member(agent)
    _done(s, 845)
    gh.prs[845] = (HELD, MERGED)
    await agent._held_pass([s], MERGED + mail.MAIL_RETENTION / 2 + timedelta(minutes=1))
    assert s.held_missed == [] and _fyi(agent) == []
    await agent._held_pass([s], LATER)
    assert gh.asked == [845], "settled, not read again"


async def test_a_crossing_cleared_from_the_record_is_not_written_again(agent, monkeypatch):
    gh, s = _Gh(monkeypatch), _member(agent)
    _done(s, 845)
    gh.prs[845] = (HELD, MERGED)
    await agent._held_pass([s], LATER)
    assert len(s.held_missed) == 1
    s.held_missed = []
    await agent._held_pass([s], LATER)
    assert s.held_missed == [] and len(_fyi(agent)) == 1 and gh.asked == [845]


# ── the row's mark and its Dismiss (slice 5) ────────────────────────────────────────────────────


def test_the_row_is_two_crossings_not_dismissed():
    a, b, c = (held.crossing(n, ["src/sessionorc/x.py"], "t") for n in (845, 851, 860))
    assert held.row([]) == [] and held.row([a]) == [], "the first is a note and a line"
    assert held.row([a, b]) == [a, b]
    marked, prs = held.dismiss([a, b], "t2")
    assert prs == [845, 851] and all(m["dismissed"] == "t2" for m in marked)
    assert held.row(marked) == [] and held.row([*marked, c]) == [], "one since the Dismiss is the first again"
    assert held.untold(marked) == marked, "a Dismiss is the person's: the member is still told once"
    again, prs = held.dismiss([*marked, c], "t3")
    assert prs == [860] and [m["dismissed"] for m in again] == ["t2", "t2", "t3"], "a dismissed entry keeps its date"
    assert held.dismiss(again, "t4") == (again, [])


async def test_dismiss_marks_the_entries_and_a_restarted_home_writes_none_again(agent, monkeypatch):
    """The settled set is in memory: an entry Dismiss removed would be a crossing again at a home
    restarted inside the mail's half retention (the review of #874), so Dismiss marks and keeps."""
    gh, s = _Gh(monkeypatch), _member(agent)
    for pr in (845, 851):
        _done(s, pr)
        gh.prs[pr] = (HELD, MERGED)
    await agent._held_pass([s], LATER)
    assert len(held.row(s.held_missed)) == 2 and len(_fyi(agent)) == 2
    async with LocalClient(caller=s.id) as w:
        with pytest.raises(AgentError, match="a person's own"):
            await w.call("clear_mark", id=s.id, kind="held")
    async with LocalClient() as person:
        with pytest.raises(AgentError, match="unknown mark"):
            await person.call("clear_mark", id=s.id, kind="whatever")
        got = await person.call("clear_mark", id=s.id, kind="held")
        assert got == {"id": s.id, "kind": "held", "cleared": [845, 851]}
        assert (await person.call("clear_mark", id=s.id, kind="held"))["cleared"] == []
    assert [c["pr"] for c in s.held_missed] == [845, 851] and held.row(s.held_missed) == []
    agent._held_settled.clear()  # a restarted home
    agent._held_tried.clear()
    await agent._held_pass([s], LATER)
    assert len(s.held_missed) == 2 and len(_fyi(agent)) == 2, "neither written nor told again"
    _done(s, 860)
    gh.prs[860] = (HELD, MERGED)
    await agent._held_pass([s], LATER)
    assert [c["pr"] for c in held.standing(s.held_missed)] == [860] and held.row(s.held_missed) == []
    assert len(_fyi(agent)) == 3, "the next crossing is a note again, and no row"
