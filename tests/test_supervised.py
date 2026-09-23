"""TD-103 slice (1), design §6 *Keeping a team running*: `supervised` on the record and the launch
record a restart will replay — written at every supervised create, carried by a resume, gone with
Forget."""

from __future__ import annotations

import json
import stat

from sessionorc import paths
from sessionorc.client import LocalClient


def _launch(sid: str):
    return paths.launch_dir() / f"{sid}.json"


async def test_a_supervised_create_writes_its_launch_record_and_forget_removes_it(agent, tmp_path):
    shell = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"]}
    async with LocalClient() as person:
        plain = await person.call("create", name="plain", **shell)
        assert plain["supervised"] is False and not _launch(plain["id"]).exists()
        mgr = (await person.call("create", name="mgr", capabilities=["control"], **shell))["id"]
        async with LocalClient(caller=mgr) as mc:
            s = await mc.call(
                "create",
                name="kept",
                supervised=True,
                unattended=True,
                prompt="the brief, as handed",
                lane=["TD-1"],
                role="grinder",
                team="t",
                project="p",
                **shell,
            )
        assert s["supervised"] is True
        path = _launch(s["id"])
        rec = json.loads(path.read_text())
        assert stat.S_IMODE(path.stat().st_mode) == 0o600  # it holds the brief
        assert rec["id"] == s["id"] and rec["supervised"] is True
        assert (rec["name"], rec["adapter"], rec["prompt"], rec["lane"]) == (
            "kept",
            "shell",
            "the brief, as handed",
            ["TD-1"],
        )
        assert (rec["role"], rec["team"], rec["project"], rec["unattended"]) == ("grinder", "t", "p", True)
        assert rec["controllers"] == [mgr]  # the record's, with the creator added
        assert "resume" not in rec and "caller" not in rec and "keep_mail" not in rec
        for sid in (plain["id"], s["id"], mgr):
            await person.call("kill", id=sid)
        await person.call("remove", id=s["id"])
        assert not path.exists()


async def test_a_resume_stays_supervised_whether_or_not_it_says_so(agent, hookstub, tmp_path):
    """§6: `supervised` is carried by every resume, as `controllers` is, and cleared by Forget alone —
    so the one-press Resume, which sends no such field, keeps it, and rewrites the launch record."""
    async with LocalClient() as c:
        old = await c.call("create", name="conv", dir=str(tmp_path), adapter="hookstub", supervised=True)
        await c.call("hook", session=old["id"], adapter_id="cc-9")
        await c.call("kill", id=old["id"])
        before = json.loads(_launch(old["id"]).read_text())["at"]
        new = await c.call("create", name="conv", dir=str(tmp_path), adapter="hookstub", resume="cc-9", prompt="again")
        assert new["id"] == old["id"] and new["supervised"] is True
        rec = json.loads(_launch(new["id"]).read_text())
        assert rec["prompt"] == "again" and rec["at"] >= before  # the latest choices
        # a fresh start under the name is not a resume: it is what its own create says
        await c.call("kill", id=new["id"])
        fresh = await c.call("create", name="conv", dir=str(tmp_path), adapter="hookstub")
        assert fresh["supervised"] is False and not _launch(fresh["id"]).exists()
        await c.call("kill", id=fresh["id"])
