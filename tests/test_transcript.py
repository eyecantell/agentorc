"""The transcript read (design §4.3 `read_transcript`, §4.5 screen 9, §4.7 *Transcript*; TD-165): Claude
Code's file as the neutral entries, read backwards by byte offset; the `transcript` RPC on the
record's host; `ao transcript`'s text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentorc.adapters.claude_code import ClaudeCodeAdapter, munge
from agentorc.adapters.claude_code import transcript as tr
from agentorc.cli import transcript_text
from sessionorc import adapters
from sessionorc.adapters import Transcript, TranscriptEntry
from sessionorc.client import AgentError, LocalClient


def _at(n: int) -> str:
    return f"2026-09-25T10:{n:02d}:00Z"


def fixture_lines() -> list[dict]:
    """A prompt, text, a thought, two tool calls with results, an Agent call whose subagent sits
    beside the session, a compaction, a second prompt — and the tool's bookkeeping, which is skipped."""
    return [
        {"type": "permission-mode", "permissionMode": "default"},
        {"type": "user", "timestamp": _at(0), "message": {"content": "fix the tests"}},
        {
            "type": "assistant",
            "timestamp": _at(1),
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "first\nsecond\nthird"},
                    {"type": "text", "text": "Looking."},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pdm run test\n# more"}},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/r/a.py"}},
                ]
            },
        },
        {
            "type": "user",
            "timestamp": _at(2),
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "3 failed\nmore"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "x = 1"}]},
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": _at(3),
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t3", "name": "Agent",
                     "input": {"description": "review", "prompt": "go"}}
                ]
            },
        },
        {"type": "user", "timestamp": _at(4),
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t3", "content": "SHIP"}]}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": _at(5), "content": "Conversation compacted"},
        {"type": "user", "isCompactSummary": True, "timestamp": _at(5), "message": {"content": "summary of before"}},
        {"type": "file-history-snapshot", "snapshot": {}},
        {"type": "user", "timestamp": _at(6), "message": {"content": [{"type": "text", "text": "now the docs"}]}},
        {"type": "assistant", "timestamp": _at(7), "message": {"content": [{"type": "text", "text": "Done."}]}},
    ]  # fmt: skip


def write_fixture(tmp_path: Path, lines: list[dict] | None = None) -> Path:
    f = tmp_path / "s1.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in (lines or fixture_lines())))
    sub = tmp_path / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.meta.json").write_text(json.dumps({"toolUseId": "t3", "agentType": "general-purpose"}))
    inner = [
        {"type": "user", "isSidechain": True, "timestamp": _at(3), "message": {"content": "go"}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "reading"}]}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "SHIP"}]}},
    ]
    (sub / "agent-a1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in inner))
    return f


@pytest.mark.unit
def test_the_file_reads_as_neutral_entries(tmp_path):
    f = write_fixture(tmp_path)
    t = tr.read(f, subagents=tmp_path / "s1" / "subagents")
    kinds = [e.kind for e in t.entries]
    assert kinds == ["prompt", "thought", "text", "tool", "tool", "tool", "compaction", "prompt", "text"]
    prompt, thought, _text, bash, read, agent, compaction, second, done = t.entries
    assert (prompt.text, prompt.at) == ("fix the tests", _at(0))
    assert (thought.lines, thought.text) == (3, "first\nsecond\nthird")
    assert (bash.name, bash.call, bash.result) == ("Bash", "pdm run test", "3 failed\nmore")
    assert (read.call, read.result) == ("/r/a.py", "x = 1")
    assert agent.result == "SHIP" and agent.sidechain is not None and agent.sidechain.count == 3
    assert [e.kind for e in agent.sidechain.entries] == ["prompt", "text", "text"]
    assert compaction.at == _at(5)  # the summary the tool writes after it is not a prompt
    assert second.text == "now the docs" and done.text == "Done."
    assert (t.turns, t.before, t.first_at, t.last_at) == (2, None, _at(0), _at(7))
    assert t.size == f.stat().st_size
    # no field name of the tool's is on the shape a client reads (TD-073's rule)
    flat = json.dumps(t.to_dict())
    for word in ("tool_use", "tool_result", "isSidechain", "message", "compact_boundary", "thinking"):
        assert word not in flat


@pytest.mark.unit
def test_it_pages_backwards_by_offset_and_carries_a_line_cut_by_a_chunk(tmp_path, monkeypatch):
    f = write_fixture(tmp_path)
    last = tr.read(f, turns=1)
    assert [e.kind for e in last.entries] == ["prompt", "text"] and last.turns == 1
    assert last.before is not None and f.read_bytes()[last.before - 1 : last.before] == b"\n"
    earlier = tr.read(f, turns=1, before=last.before)
    assert earlier.entries[0].text == "fix the tests" and earlier.before is None  # the file's start
    assert earlier.entries[-1].kind == "compaction"  # up to, not past, the offset asked for
    # a chunk far smaller than a line: every line crosses a boundary, and none is lost or cut
    monkeypatch.setattr(tr, "CHUNK", 7)
    small = tr.read(f, subagents=tmp_path / "s1" / "subagents")
    assert [e.to_dict() for e in small.entries] == [
        e.to_dict() for e in tr.read(f, subagents=tmp_path / "s1" / "subagents").entries
    ]
    raw = tr.read(f, turns=2, raw=True)
    assert raw.raw.splitlines() == [json.dumps(e) for e in fixture_lines()[-2:]] and raw.entries == []


@pytest.mark.unit
def test_an_inline_sidechain_folds_under_the_agent_call_before_it(tmp_path):
    """Older builds wrote a subagent's turns into the session's own file, marked `isSidechain`."""
    lines = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Task", "input": {}}]}},
        {"type": "user", "isSidechain": True, "message": {"content": "sub prompt"}},
        {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "sub says"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "ok"}]}},
    ]
    f = tmp_path / "old.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in lines))
    t = tr.read(f)
    assert [e.kind for e in t.entries] == ["prompt", "tool"] and t.turns == 1  # a sidechain prompt is not a turn
    side = t.entries[1].sidechain
    assert side.count == 2 and [e.text for e in side.entries] == ["sub prompt", "sub says"]


@pytest.mark.unit
def test_the_adapter_locates_the_file_from_the_record(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profiles.yml").write_text(
        f"default: t\nprofiles:\n  t: {{account: t, config_dir: {tmp_path / 'cc'}}}\n"
    )
    ad = ClaudeCodeAdapter()
    assert ad.read_transcript("s1", tmp_path / "repo", "t") is None  # no file: None, never an error
    d = tmp_path / "cc" / "projects" / munge(tmp_path / "repo")
    d.mkdir(parents=True)
    write_fixture(d)
    t = ad.read_transcript("s1", tmp_path / "repo", "t", turns=5)
    assert t is not None and t.turns == 2 and t.entries[5].sidechain.count == 3
    assert ad.read_transcript("s1", tmp_path / "repo", "nope") is None  # an unknown profile


@pytest.mark.unit
def test_ao_transcript_draws_the_entries_as_the_pane_does(tmp_path):
    t = tr.read(write_fixture(tmp_path), subagents=tmp_path / "s1" / "subagents").to_dict()
    text = "\n".join(transcript_text(t))
    for line in (
        "> fix the tests",
        "✻ thought · 3 lines",
        "⏺ Bash(pdm run test)",
        "  ⎿ 3 failed",
        "⏺ Read(/r/a.py)",
        "⏺ Agent(review)",
        "  ⎿ 3 subagent turns",
        f"── compacted {_at(5)} ──",
        "> now the docs",
        "Done.",
    ):
        assert line in text.splitlines(), line
    assert "more" not in text  # a result is folded to its first line


class TranscriptStub:
    """An adapter that reads a transcript: what the RPC is handed back, by record or by row."""

    name, label, state_source = "tstub", "Stub", "hook"

    def launch(self, **_):
        return adapters.LaunchSpec(argv=["bash", "--norc", "--noprofile"], adapter_id="tool-1")

    def classify(self, pane, tail):
        return None

    def read_transcript(self, session_id, cwd, profile, *, before=None, turns=20, raw=False):
        if session_id == "gone":
            return None
        e = TranscriptEntry(kind="prompt", text=f"{session_id} {cwd} {turns} {before}")
        return Transcript(path="/f.jsonl", size=10, turns=1, entries=[e])


@pytest.mark.integration
async def test_the_rpc_reads_by_record_or_by_row_and_refuses_what_has_none(agent, tmp_path, monkeypatch):
    from sessionorc.agent import NODE_READS

    assert {"tail", "explain", "log_tail", "transcript", "paths"} == NODE_READS  # a node's record reads through `read`
    adapters.load_all()
    monkeypatch.setitem(adapters._REGISTRY, "tstub", TranscriptStub())
    async with LocalClient() as person:
        sh = (await person.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        with pytest.raises(AgentError, match="carries no tool session id: a shell has no transcript"):
            await person.call("transcript", id=sh)
        sid = (await person.call("create", name="st", dir=str(tmp_path), adapter="tstub"))["id"]
        got = await person.call("transcript", id=sid, turns=3, before=7)
        assert got["entries"] == [{"kind": "prompt", "text": f"tool-1 {tmp_path} 3 7"}] and got["turns"] == 1
        # a Resumable row with no record: the four fields in a record's place
        row = await person.call("transcript", adapter="tstub", adapter_id="x", dir="/d")
        assert row["entries"][0]["text"] == "x /d 20 None"
        with pytest.raises(AgentError, match="the tool's file is not on"):
            await person.call("transcript", adapter="tstub", adapter_id="gone", dir="/d")
        with pytest.raises(AgentError, match="needs id, or adapter, adapter_id and dir"):
            await person.call("transcript")
        with pytest.raises(AgentError, match="the shell adapter reads no transcript"):
            await person.call("transcript", adapter="shell", adapter_id="x", dir="/d")
        async with LocalClient(caller=sh) as other:
            assert (await other.call("transcript", id=sid))["turns"] == 1  # a read: gated by nobody
        for s in (sh, sid):
            await person.call("kill", id=s)
            await person.call("remove", id=s)
