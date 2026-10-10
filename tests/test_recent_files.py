"""A run's recent files (design §4.2, TD-527): a main-thread edit's `PostToolUse` names its file, and the
record keeps `files` — newest first, a path once, the newest `RECENT_FILES` — for the Focus Session card."""

from __future__ import annotations

import time

import pytest
from conftest import wait_for

from agentorc.adapters.claude_code.hook import translate
from sessionorc.client import LocalClient
from sessionorc.models import NODE_OWNED, RECENT_FILES, Session


def post(tool: str, tool_input: dict, **more) -> dict | None:
    return translate({"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tool_input, **more})


def test_an_edit_names_its_file_and_nothing_else_does():
    for tool in ("Edit", "Write", "MultiEdit"):
        assert post(tool, {"file_path": "/r/src/a.py"})["file"] == "/r/src/a.py"
    assert post("NotebookEdit", {"notebook_path": "/r/n.ipynb"})["file"] == "/r/n.ipynb"
    for tool, ti in (("Read", {"file_path": "/r/a.py"}), ("Bash", {"command": "touch x"}), ("Glob", {"path": "/r"})):
        assert "file" not in post(tool, ti)  # a read, or a tool that names no edit
    assert "file" not in post("Edit", {})  # a hook that carries no path reports none
    # a subagent's edit says nothing, as its state says nothing (§4.2, TD-201)
    assert "file" not in (post("Edit", {"file_path": "/r/a.py"}, agent_id="sub-1") or {})
    # the edit is still a tool event: the state and the event name ride beside the file
    got = post("Write", {"file_path": "/r/b.md"})
    assert got["state"] == "working" and got["event"] == "PostToolUse:Write"
    pre = translate({"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "/r/a.py"}})
    assert "file" not in pre  # before the edit, nothing was edited


def test_files_is_the_nodes_and_new_records_start_empty():
    assert "files" in NODE_OWNED
    assert Session(id="ao-x", name="x", kind="interactive", dir="/d", adapter="shell").files == []
    assert (
        Session.from_dict({"id": "ao-x", "name": "x", "kind": "interactive", "dir": "/d", "adapter": "shell"}).files
        == []
    )


@pytest.mark.integration
async def test_the_record_keeps_the_newest_twenty_a_path_once(agent, tmp_path):
    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))[
            "id"
        ]
        for name in ("a.py", "b.py", "a.py"):  # a path edited again moves to the top, once
            await person.call("hook", session=sid, state="working", event="PostToolUse:Edit", file=f"/r/{name}")
        got = (await person.call("get", id=sid))["files"]
        assert [f["path"] for f in got] == ["/r/a.py", "/r/b.py"]
        assert all(f["at"].endswith("Z") for f in got)
        await person.call("hook", session=sid, state="working", event="PostToolUse:Read")  # no file: no change
        assert [f["path"] for f in (await person.call("get", id=sid))["files"]] == ["/r/a.py", "/r/b.py"]
        for i in range(RECENT_FILES + 5):
            await person.call("hook", session=sid, state="working", file=f"/r/{i}.py")
        got = (await person.call("list"))[0]["files"]  # on the pushed view too
        assert len(got) == RECENT_FILES and got[0]["path"] == f"/r/{RECENT_FILES + 4}.py"
        assert got[-1]["path"] == "/r/5.py"
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)


@pytest.mark.integration
async def test_a_stale_queued_edit_leaves_files_alone(agent, tmp_path):
    """A queued edit stamped before the last live hook is dropped (TD-533): at the top it would read
    newer than the edits after it. One stamped after the last live hook moves to the top as a live one does."""
    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))[
            "id"
        ]
        before = time.time()
        await person.call("hook", session=sid, state="working", event="PostToolUse:Edit", file="/r/new.py")
        # queued before that live edit: its adapter id still applies (the drain's marker), its file does not
        agent.events.append(sid, {"state": "working", "file": "/r/old.py", "adapter_id": "uuid-1", "at": before})

        async def drained():
            return (await person.call("get", id=sid))["adapter_id"] == "uuid-1"

        assert await wait_for(drained)
        assert [f["path"] for f in (await person.call("get", id=sid))["files"]] == ["/r/new.py"]
        # queued after the last live hook: applied, to the top
        agent.events.append(sid, {"state": "working", "file": "/r/later.py", "at": time.time()})

        async def moved():
            return [f["path"] for f in (await person.call("get", id=sid))["files"]] == ["/r/later.py", "/r/new.py"]

        assert await wait_for(moved)
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
