"""TD-258 slice 1, design §6 *Keeping a team running* rule 10: the cadence check as a policy of the
tick. The home runs `scripts/check_cadence.py --pr <n> --json` on each `done` a supervised member
reports with a PR, keeps the reading on the record's `checks`, tells a first fail in one fixed
line (or the clause on every `ao` reply) and marks the Inbox row on a second fail or a merged one."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from conftest import park_ticks, wait_for

from sessionorc import cadence
from sessionorc.client import LocalClient
from sessionorc.models import MailEntry, ProgressEntry, Session

PASS = {"verdict": "pass", "failed": []}
FAIL = {"verdict": "fail", "failed": ["review", "ledger"]}
UNKNOWN = {"verdict": "unknown", "failed": []}


def _script_json(number: int, verdict: str, **rows: str) -> str:
    listed = [{"rule": k, "status": v, "detail": "never read"} for k, v in rows.items()]
    one = {"pr": number, "state": "OPEN", "rows": listed, "verdict": verdict}
    return json.dumps({"prs": [one], "verdict": verdict})


def test_the_scripts_json_is_read_by_rule_and_never_by_detail():
    got = cadence.parse(_script_json(842, "fail", pr="pass", review="fail", ci="pass", ledger="fail"), 842)
    assert got == FAIL
    assert cadence.parse(_script_json(842, "pass", pr="pass"), 842) == PASS
    assert cadence.parse(_script_json(842, "unknown", ci="unknown"), 842) == {"verdict": "unknown", "failed": []}
    for bad in ("", "not json", "[]", _script_json(7, "pass"), _script_json(842, "maybe")):
        assert cadence.parse(bad, 842) is None, bad


def test_a_read_leaves_the_entry_and_its_marks():
    first = cadence.record(None, 842, "aaa", False, FAIL, "t1")
    assert first == {"pr": 842, "at": "t1", "sha": "aaa", "verdict": "fail", "failed": ["review", "ledger"]}
    assert cadence.untold(first)
    # read again before the member was told: still the first fail
    assert "row" not in cadence.record(first, 842, "bbb", False, FAIL, "t2")
    told = {**first, "told": "t1"}
    second = cadence.record(told, 842, "bbb", False, FAIL, "t2")
    assert second["row"] == "t2" and second["told"] == "t1" and not cadence.untold(second)
    # an unknown read tells nothing and moves no mark
    unknown = cadence.record(second, 842, "ccc", False, UNKNOWN, "t3")
    assert unknown["row"] == "t2" and unknown["verdict"] == "unknown"
    # a later pass removes the row and leaves the entry
    passed = cadence.record(second, 842, "ccc", False, PASS, "t3")
    assert "row" not in passed and "told" not in passed and passed["verdict"] == "pass"
    assert cadence.untold(cadence.record(passed, 842, "ddd", False, FAIL, "t4")), "a fail after a pass is told anew"
    # an unknown between two fails changes nothing: the second fail is still the row
    again = cadence.record(cadence.record(told, 842, "bbb", False, UNKNOWN, "t2"), 842, "ccc", False, FAIL, "t3")
    assert again["row"] == "t3"
    # a fail read on a merged PR is the row at once, and never a line
    merged = cadence.record(None, 843, "ddd", True, FAIL, "t4")
    assert merged["row"] == "t4" and merged["merged"] is True and not cadence.untold(merged)


def test_the_fixed_words():
    c = {"pr": 842, "verdict": "fail", "failed": ["review", "ledger"]}
    assert cadence.line(c, "TD-257") == (
        "[agentorc] PR #842 failed the cadence check: review, ledger — fix it, then report "
        "`ao progress done TD-257 --pr 842` again"
    )
    assert cadence.clause([c, {"pr": 9, "verdict": "pass", "failed": []}]) == (
        "PR #842 fails the cadence check: review, ledger"
    )
    assert cadence.clause([{**c, "merged": True}]) == "", "no re-report cures a merged PR: the row says it"
    assert cadence.said(c) == "#842 fail: review, ledger · review recorded"
    assert cadence.said({"pr": 9, "verdict": "pass", "read_by": "ao-x-techlead"}) == (
        "#9 pass · review read by ao-x-techlead"
    )


def test_read_by_is_a_named_readers_reply_on_the_prs_ask():
    s = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="")
    ask = MailEntry(id="m-1", from_="ao-x-w", to=["ao-x-tl"], at="t", kind="ask", text="read it", root="m-1", pr=842)
    s.outbox = [ask]
    assert cadence.read_by(s, 842) is None
    s.inbox = [MailEntry(id="m-2", from_="ao-x-other", to=["ao-x-w"], at="t", kind="reply", text="hi", root="m-1")]
    assert cadence.read_by(s, 842) is None, "a reply from someone the ask did not name is not its reader's"
    s.inbox.append(MailEntry(id="m-3", from_="ao-x-tl", to=["ao-x-w"], at="t", kind="reply", text="merged", root="m-1"))
    assert cadence.read_by(s, 842) == "ao-x-tl" and cadence.read_by(s, 843) is None


def test_no_script_is_no_reading(tmp_path):
    assert not cadence.has_script(tmp_path) and cadence.check(tmp_path, 1) is None
    assert not cadence.has_script(tmp_path / "not-here")


# ── the tick ────────────────────────────────────────────────────────────────────────────────────


class _Gh:
    """What `gh` and the script would say, and what was asked of them."""

    def __init__(self, monkeypatch):
        self.heads: dict[int, tuple[str, str] | None] = {}
        self.verdicts: dict[int, dict | None] = {}
        self.ran: list[int] = []
        self.asked: list[int] = []
        monkeypatch.setattr(cadence, "head", self._head)
        monkeypatch.setattr(cadence, "check", self._check)

    def _head(self, root, pr, **kw):
        self.asked.append(pr)
        return self.heads.get(pr)

    def _check(self, root, pr, **kw):
        self.ran.append(pr)
        return self.verdicts.get(pr)


def _root(agent, tmp_path, script: bool = True) -> str:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    if script:
        (root / "scripts" / "check_cadence.py").write_text("# the stub stands in for it\n")
    agent._repos[str(root)] = {}
    return str(root)


async def _member(agent, person, tmp_path, root: str, name="w", **kw) -> Session:
    (tmp_path / name).mkdir()
    params = {
        "name": name, "dir": str(tmp_path / name), "adapter": "composer0", "unattended": True,
        "supervised": True, "prompt": "the brief",
    }  # fmt: skip
    sid = (await person.call("create", **{**params, **kw}))["id"]
    assert await wait_for(lambda: _painted(agent, sid), timeout=5), "the composer child never painted its prompt"
    rec = agent.sessions[sid]
    rec.repo = root
    return rec


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


def _done(rec: Session, ref: str, pr: int) -> None:
    rec.progress = [*rec.progress, ProgressEntry(ref=ref, status="done", pr=pr)]


@pytest.mark.integration
async def test_a_pass_writes_the_entry_and_nothing_else(agent, composerstubs, tmp_path, monkeypatch):
    await park_ticks(agent)
    gh, root, now = _Gh(monkeypatch), _root(agent, tmp_path), datetime.now(UTC)
    async with LocalClient() as person:
        rec = await _member(agent, person, tmp_path, root)
        await agent.rpc_hook(rec.id, state="idle")
        await agent._cadence_pass([rec])
        assert rec.checks == [] and gh.ran == [], "no `done` with a PR: nothing to read"
        _done(rec, "TD-257", 842)
        await agent._cadence_pass([rec])
        assert rec.checks == [] and gh.ran == [], "gh gave no head: no reading"
        gh.heads[842], gh.verdicts[842] = ("aaa", "open"), None
        await agent._cadence_pass([rec])
        assert rec.checks == [] and gh.ran == [842], "the script gave no reading (exit 2): no entry"
        gh.verdicts[842] = PASS
        await agent._cadence_pass([rec])
        (c,) = rec.checks
        assert (c["pr"], c["sha"], c["verdict"], c["failed"]) == (842, "aaa", "pass", [])
        assert "told" not in c and "row" not in c and "merged" not in c
        await agent._cadence_pass([rec])
        assert gh.ran == [842, 842], "read at that head already: the script is not run again"
        gh.heads[842] = ("bbb", "open")
        await agent._cadence_pass([rec])
        assert gh.ran == [842, 842, 842] and rec.checks[0]["sha"] == "bbb", "the head moved: read again"
        gh.heads[842] = ("bbb", "merged")
        await agent._cadence_pass([rec])
        assert rec.checks[0]["merged"] is True
        gh.heads[842] = None  # a merged PR's settled read asks nothing more, not even its head
        await agent._cadence_pass([rec])
        assert len(gh.ran) == 4
        await agent._keep_running(now)
        assert await _submitted(agent, rec.id) == [], "a pass tells nobody"
        await person.call("kill", id=rec.id)


@pytest.mark.integration
async def test_a_fail_types_the_line_once_and_a_second_fail_marks_the_row(agent, composerstubs, tmp_path, monkeypatch):
    await park_ticks(agent)
    gh, root, now = _Gh(monkeypatch), _root(agent, tmp_path), datetime.now(UTC)
    async with LocalClient() as person:
        rec = await _member(agent, person, tmp_path, root)
        await agent.rpc_hook(rec.id, state="idle")
        _done(rec, "TD-257", 842)
        gh.heads[842], gh.verdicts[842] = ("aaa", "open"), FAIL
        await agent._cadence_pass([rec])
        await agent._keep_running(now)
        lines = await _submitted(agent, rec.id)
        assert len(lines) == 1 and "PR #842 failed the cadence check: review, ledger — fix it" in lines[0]
        assert "`ao progress done TD-257 --pr 842` again" in lines[0]
        assert rec.checks[0]["told"] and "row" not in rec.checks[0] and rec.sends[-1].from_ == "system"
        await agent._keep_running(now)
        await agent._cadence_pass([rec])
        assert len(await _submitted(agent, rec.id)) == 1 and gh.ran == [842], "told once, read once at that head"
        _done(rec, "TD-257", 842)  # reported again, and it still fails
        rec.progress[-1].at = "9999-01-01T00:00:00Z"
        await agent._cadence_pass([rec])
        assert rec.checks[0]["row"] and gh.ran == [842, 842]
        await agent._keep_running(now)
        assert len(await _submitted(agent, rec.id)) == 1, "the second fail is the row's, not another line"
        gh.verdicts[842], gh.heads[842] = UNKNOWN, ("bbb", "open")
        await agent._cadence_pass([rec])
        await agent._cadence_pass([rec])
        assert gh.ran == [842] * 4 and rec.checks[0]["row"], "an unknown is read again each cadence, no mark moved"
        await agent._keep_running(now)
        assert len(await _submitted(agent, rec.id)) == 1, "and nothing is told"
        gh.verdicts[842], gh.heads[842] = PASS, ("ccc", "open")
        await agent._cadence_pass([rec])
        assert "row" not in rec.checks[0] and rec.checks[0]["verdict"] == "pass", "a later pass removes the row"
        # a derived `done` is written anew at every derivation: its date is no new report
        rec.progress = [ProgressEntry(ref="TD-257", status="done", pr=842, source="derived", at="9999-01-02T00:00:00Z")]
        await agent._cadence_pass([rec])
        assert gh.ran == [842] * 5, "read at that head: not run again"
        await person.call("kill", id=rec.id)


@pytest.mark.integration
async def test_a_merged_fail_marks_the_row_at_once_and_one_pr_is_read_a_run(
    agent, composerstubs, tmp_path, monkeypatch
):
    await park_ticks(agent)
    gh, root, now = _Gh(monkeypatch), _root(agent, tmp_path), datetime.now(UTC)
    async with LocalClient() as person:
        rec = await _member(agent, person, tmp_path, root)
        await agent.rpc_hook(rec.id, state="idle")
        _done(rec, "TD-257", 842)
        _done(rec, "TD-258", 843)
        gh.heads = {842: ("aaa", "merged"), 843: ("bbb", "open")}
        gh.verdicts = {842: FAIL, 843: PASS}
        await agent._cadence_pass([rec])
        assert gh.ran == [842] and [c["pr"] for c in rec.checks] == [842], "one PR per run"
        assert rec.checks[0]["row"] and rec.checks[0]["merged"] is True
        await agent._keep_running(now)
        assert await _submitted(agent, rec.id) == [] and "told" not in rec.checks[0], "no re-report cures it: no line"
        await agent._cadence_pass([rec])
        assert gh.ran == [842, 843] and {c["pr"]: c["verdict"] for c in rec.checks} == {842: "fail", 843: "pass"}
        await person.call("kill", id=rec.id)


@pytest.mark.integration
async def test_who_is_read_and_who_is_told(agent, composerstubs, tmp_path, monkeypatch):
    """No script, no registry root, a seat, an unsupervised session: no reading. A working member
    and an attended one are told by the clause alone."""
    await park_ticks(agent)
    gh, now = _Gh(monkeypatch), datetime.now(UTC)
    root = _root(agent, tmp_path)
    bare = tmp_path / "bare"
    bare.mkdir()
    agent._repos[str(bare)] = {}
    gh.heads[842], gh.verdicts[842] = ("aaa", "open"), FAIL
    async with LocalClient() as person:
        unread = {
            "noscript": {"repo": str(bare)},
            "noroot": {"repo": str(tmp_path / "elsewhere")},
            "seat": {"seat": {"trigger": "asks"}},
            "unsupervised": {"supervised": False},
        }
        recs = {}
        for name, fields in unread.items():
            rec = recs[name] = await _member(agent, person, tmp_path, root, name=name)
            for k, v in fields.items():
                setattr(rec, k, v)
            _done(rec, "TD-257", 842)
        await agent._cadence_pass(list(recs.values()))
        assert gh.ran == [] and all(r.checks == [] for r in recs.values())

        working = await _member(agent, person, tmp_path, root, name="working")
        attended = await _member(agent, person, tmp_path, root, name="attended")
        attended.unattended = False
        await agent.rpc_hook(working.id, state="working")
        await agent.rpc_hook(attended.id, state="idle")
        for rec in (working, attended):
            _done(rec, "TD-257", 842)
            await agent._cadence_pass([rec])
            assert rec.checks[0]["verdict"] == "fail"
        await agent._keep_running(now)
        for rec in (working, attended):
            assert await _submitted(agent, rec.id) == [], "never typed mid-turn, never into a person's session"
            assert rec.checks[0]["told"], "the clause is the telling"
        for rec in [*recs.values(), working, attended]:
            await person.call("kill", id=rec.id)


async def test_a_nodes_member_gets_the_clause_and_no_line(agent, monkeypatch):
    """Its pane is on another host: nothing is typed from the home, and the reading is told by the
    clause its `ao` replies carry, which the home adds."""
    typed: list[str] = []

    async def never(s, text):
        typed.append(text)
        return True

    monkeypatch.setattr(agent, "_policy_send", never)
    rec = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="", host="laptop")
    rec.supervised, rec.unattended, rec.state, rec.confidence = True, True, "idle", "hook"
    rec.checks = [{"pr": 842, "at": "t", "sha": "aaa", **FAIL}]
    await agent._cadence_line(rec, datetime.now(UTC))
    assert typed == [] and rec.checks[0]["told"]
    assert cadence.clause(rec.checks) == "PR #842 fails the cadence check: review, ledger"


def test_the_rows_mark_and_its_dismiss():
    told = {"pr": 842, "at": "t1", "sha": "aaa", **FAIL, "told": "t1", "row": "t2"}
    fine = {"pr": 843, "at": "t1", "sha": "bbb", **PASS}
    assert cadence.rows([told, fine]) == [told] and cadence.rows([fine]) == []
    left, stood = cadence.dismiss([told, fine], 842)
    assert stood and cadence.rows(left) == [] and left[1] is fine
    assert left[0] == {k: v for k, v in told.items() if k != "row"}, "the entry stays as the record of the read"
    assert cadence.dismiss(left, 842) == (left, False) and cadence.dismiss(left, 9) == (left, False)
    # a fail read later — a new head, or a new `done` naming the PR — is the row again
    assert cadence.record(left[0], 842, "ccc", False, FAIL, "t3")["row"] == "t3"


async def test_dismiss_takes_the_row_off_and_its_snooze_with_it(agent, tmp_path, monkeypatch):
    """Design §4.5a *Inbox row: cadence check failed* (slice 5): Dismiss is the person's, at the
    home, on one PR's entry; the row's snooze is keyed on the record and the PR."""
    from sessionorc import modes
    from sessionorc.client import AgentError

    gh, root = _Gh(monkeypatch), _root(agent, tmp_path)
    rec = Session(id="ao-x-w", name="w", kind="agent", adapter="shell", dir="", repo=root, host=agent.host)
    rec.supervised, rec.unattended, rec.state = True, True, "working"
    agent.sessions[rec.id] = rec
    _done(rec, "TD-257", 842)
    _done(rec, "TD-258", 843)
    gh.heads = {842: ("aaa", "merged"), 843: ("bbb", "merged")}
    gh.verdicts = {842: FAIL, 843: FAIL}
    await agent._cadence_pass([rec])
    await agent._cadence_pass([rec])
    assert [c["pr"] for c in cadence.rows(rec.checks)] == [842, 843], "a merged fail is the row at once"
    until = "2026-10-02T00:00:00Z"
    async with LocalClient(caller=rec.id) as w:
        with pytest.raises(AgentError, match="a person's own"):
            await w.call("clear_mark", id=rec.id, kind="cadence", pr=842)
    async with LocalClient() as person:
        for bad in ("cadence:", "cadence:x", "cadence:-1", "cadence:0842"):
            with pytest.raises(AgentError, match="unknown row kind"):
                await person.call("attention_snooze", id=rec.id, kind=bad, until=until)
        for pr in (842, 843):
            got = await person.call("attention_snooze", id=rec.id, kind=f"cadence:{pr}", until=until)
            assert got["row"] == f"{rec.id}|cadence:{pr}"
        with pytest.raises(AgentError, match="needs the PR"):
            await person.call("clear_mark", id=rec.id, kind="cadence")
        got = await person.call("clear_mark", id=rec.id, kind="cadence", pr=842)
        assert got == {"id": rec.id, "kind": "cadence", "cleared": [842]}
        assert (await person.call("clear_mark", id=rec.id, kind="cadence", pr=842))["cleared"] == []
        assert (await person.call("inbox"))["attention_snoozed"] == {f"{rec.id}|cadence:843": until}
    assert [c["pr"] for c in cadence.rows(rec.checks)] == [843] and len(rec.checks) == 2
    await agent._cadence_pass([rec])
    assert gh.ran == [842, 843] and len(cadence.rows(rec.checks)) == 1, "a merged PR's read stands: no row again"
    assert "clear_mark" in modes.HOME_EDITS
    del agent.sessions[rec.id]


async def test_every_reply_to_a_member_with_a_failing_pr_carries_the_clause(agent, tmp_path):
    from sessionorc import client as clientmod

    async with LocalClient() as person:
        params = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}
        w = (await person.call("create", name="w", supervised=True, prompt="the brief", **params))["id"]
    async with LocalClient(caller=w) as wc:
        await wc.call("get", id=w)
        assert not (clientmod.last_mail or {}).get("cadence")
        agent.sessions[w].checks = [{"pr": 842, "at": "t", "sha": "aaa", **FAIL}]
        got = await wc.call("get", id=w)
        assert clientmod.last_mail["cadence"] == "PR #842 fails the cadence check: review, ledger"
        assert got["checks"][0]["pr"] == 842, "the record's view carries its checks"
        agent.sessions[w].checks[0]["verdict"] = "pass"
        await wc.call("get", id=w)
        assert not (clientmod.last_mail or {}).get("cadence")
    clientmod.last_mail = None


def test_the_cadence_clause_rides_the_unread_line_or_stands_alone(capsys):
    import argparse

    from agentorc import cli
    from sessionorc import client as clientmod

    args = argparse.Namespace(json=False)
    fails = "PR #842 fails the cadence check: review, ledger"
    clientmod.last_mail = {"unread": 0, "wake_budget_spent": False, "cadence": fails}
    cli.unread_line(args)
    assert capsys.readouterr().out == f"[agentorc] ({fails})\n"
    clientmod.last_mail = {"unread": 2, "wake_budget_spent": False, "cadence": fails}
    cli.unread_line(args)
    assert capsys.readouterr().out == f"[agentorc] you have 2 unread messages — run ao inbox ({fails})\n"
    over = "context 231k over the 200k bound"
    clientmod.last_mail = {"unread": 0, "wake_budget_spent": False, "context": over, "cadence": fails}
    cli.unread_line(args)
    assert capsys.readouterr().out == f"[agentorc] ({over}; {fails}) — finish the entry in hand, then declare\n"
    clientmod.last_mail = None


def test_head_from_reads_the_repo_readings_open_and_recent_prs():
    reading = {
        "open": [{"number": 842, "head": "aaa", "state": "open"}, {"number": 9, "head": "", "state": "open"}],
        "recent": [
            {"number": 843, "head": "bbb", "state": "closed"},
            {"number": 844, "head": "ccc", "state": "merged"},
        ],
    }
    assert cadence.head_from(reading, 842) == ("aaa", "open")
    assert cadence.head_from(reading, 843) == ("bbb", "closed") and cadence.head_from(reading, 844) == ("ccc", "merged")
    assert cadence.head_from(reading, 9) is None, "a reading written before it kept heads: gh is asked"
    assert cadence.head_from(reading, 1) is None and cadence.head_from({"error": "x"}, 842) is None
    assert cadence.settled({"closed": True}) and cadence.settled({"merged": True}) and not cadence.settled({})


@pytest.mark.integration
async def test_a_closed_pr_settles_and_an_open_ones_head_comes_from_the_repo_reading(
    agent, composerstubs, tmp_path, monkeypatch
):
    """TD-332: a PR closed unmerged is marked settled, as a merged one is, and its head is not asked
    for again; an open PR's head is read from the repo's PR reading, `gh` asked only for one the
    reading lacks."""
    await park_ticks(agent)
    gh, root = _Gh(monkeypatch), _root(agent, tmp_path)
    async with LocalClient() as person:
        rec = await _member(agent, person, tmp_path, root)
        _done(rec, "TD-257", 842)
        gh.heads[842], gh.verdicts[842] = ("aaa", "open"), FAIL
        await agent._cadence_pass([rec])
        assert gh.asked == [842] and gh.ran == [842]
        gh.heads[842] = ("aaa", "closed")  # closed at the head it was read at: settled, not read again
        await agent._cadence_pass([rec])
        assert rec.checks[0]["closed"] is True and gh.ran == [842] and gh.asked == [842, 842]
        await agent._cadence_pass([rec])
        assert gh.asked == [842, 842], "a closed PR's settled read asks nothing more, not even its head"
        # a record replaced while the pass was out is never written: the mark goes to the record under the address
        gone = Session(id=rec.id, name="w", kind="agent", adapter="shell", dir="", repo=root, host=agent.host)
        gone.supervised, gone.unattended = True, True
        gone.progress = [ProgressEntry(ref="TD-259", status="done", pr=845)]
        gone.checks = [{"pr": 845, "at": "t", "sha": "eee", "verdict": "pass", "failed": []}]
        gh.heads[845] = ("eee", "closed")
        await agent._cadence_pass([gone])
        assert "closed" not in gone.checks[0] and agent.sessions[rec.id] is rec
        # an open PR the repo reading holds: its head is read there, and gh is not asked
        _done(rec, "TD-258", 843)
        agent._repos[root] = {"prs": {"open": [{"number": 843, "head": "ddd", "state": "open"}], "recent": []}}
        gh.verdicts[843] = PASS
        await agent._cadence_pass([rec])
        await agent._cadence_pass([rec])
        assert gh.asked == [842, 842, 845] and gh.ran == [842, 843]
        assert cadence.entry_of(rec.checks, 843)["sha"] == "ddd"
        await person.call("kill", id=rec.id)
