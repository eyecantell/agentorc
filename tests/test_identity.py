"""Who is calling (design §4.8a, TD-077): the classification over fabricated process trees, the rule
table, and `enforce` against real panes on the suite's private tmux server.

Every other test runs with identity `off` (conftest `_identity_off`): the suite calls the socket
from the pytest process, under no pane, as sessions — which is what the table calls a forgery.
"""

from __future__ import annotations

import json
import shlex
import sys
import textwrap

import pytest
from conftest import wait_for, wait_state

from sessionorc import identity, paths
from sessionorc.client import AgentError, LocalClient
from sessionorc.identity import Channel, Pane, Proc

# -- a fabricated /proc ---------------------------------------------------------------------------


class FakeProc:
    """pid → Proc, pid → cgroup. `script` lets a test change what a pid reads as between reads —
    the race the double read exists for."""

    def __init__(self, procs: list[Proc], cgroups: dict[int, str] | None = None):
        self.procs = {p.pid: p for p in procs}
        self.cgroups = cgroups or {}
        self.script: dict[int, list[Proc | None]] = {}
        self.comms: dict[int, str] = {1: "systemd"}

    def stat(self, pid: int) -> Proc | None:
        if self.script.get(pid):
            return self.script[pid].pop(0)
        return self.procs.get(pid)

    def cgroup(self, pid: int) -> str | None:
        return self.cgroups.get(pid)

    def comm(self, pid: int) -> str | None:
        return self.comms.get(pid)


def P(pid: int, ppid: int, sid: int, tty: int = 0, start: int | None = None) -> Proc:
    return Proc(pid=pid, ppid=ppid, sid=sid, tty_nr=tty, start=start if start is not None else pid)


# Two panes: X is pid 100 on tty 0x8801, Y is pid 200 on tty 0x8802. 50 is the tmux server.
PANES = [Pane("ao-x", 100, 0x8801), Pane("ao-y", 200, 0x8802)]
BASE = [P(1, 0, 1), P(50, 1, 50), P(100, 50, 100, 0x8801), P(200, 50, 200, 0x8802)]


def test_ancestry_names_the_pane_and_the_pane_itself_is_its_own():
    fp = FakeProc([*BASE, P(110, 100, 100, 0x8801), P(111, 110, 100, 0x8801)])
    assert identity.classify(111, PANES, fp) == Channel("session", "ao-x", "ancestry")
    assert identity.classify(100, PANES, fp) == Channel("session", "ao-x", "ancestry")
    assert identity.classify(200, PANES, fp).session == "ao-y"


def test_a_reparented_process_is_still_its_panes_by_session_id_then_by_terminal():
    # a background `ao wait` whose parent shell exited: ppid is init, the chain is gone
    fp = FakeProc([*BASE, P(120, 1, 100, 0x8801)])
    assert identity.classify(120, PANES, fp) == Channel("session", "ao-x", "sid")
    # a session id that is no pane's (the tool started its shell as a session leader of its own,
    # keeping the terminal): the controlling terminal still names the pane
    fp = FakeProc([*BASE, P(121, 1, 9999, 0x8802)])
    assert identity.classify(121, PANES, fp) == Channel("session", "ao-y", "tty")


def test_outside_is_a_peer_under_no_pane_and_unknown_is_one_that_cannot_be_read():
    fp = FakeProc([*BASE, P(300, 1, 300, 0x8810), P(301, 300, 300, 0x8810)])
    assert identity.classify(301, PANES, fp) == identity.OUTSIDE  # a person's terminal
    assert identity.classify(4242, PANES, fp) == Channel("unknown", signal="unreadable")  # gone before the walk


def test_a_fully_detached_process_is_outside_unless_the_cgroup_check_is_on():
    """Double fork and `setsid`: no chain, a session id of its own, no terminal. Read as *outside*
    it would be the person with no `caller` — so where the agent's service cgroup holds every pane,
    it is *unknown*."""
    svc = "/user.slice/user-1000.slice/user@1000.service/app.slice/agentorc-agent.service"
    cg = {400: svc, 50: svc, 7: svc, 300: "/user.slice/session-3.scope"}
    fp = FakeProc([*BASE, P(7, 1, 7), P(400, 1, 400, 0)], cgroups=cg)  # 7 is the agent, started by systemd
    assert identity.classify(400, PANES, fp) == identity.OUTSIDE
    on = identity.detached_check(fp, agent_pid=7, tmux_pid=50)
    assert on == svc
    assert identity.classify(400, PANES, fp, detached=on) == Channel("unknown", signal="cgroup")
    fp.procs[300] = P(300, 1, 300, 0x8810)
    assert identity.classify(300, PANES, fp, detached=on) == identity.OUTSIDE  # the person is in another cgroup


def test_the_detached_check_is_off_when_the_tmux_server_is_not_in_the_agents_own_service():
    svc = "/system.slice/agentorc-agent.service"
    scope = "/user.slice/user-1000.slice/session-3.scope"
    assert identity.detached_check(FakeProc([], {7: svc, 50: scope}), agent_pid=7, tmux_pid=50) is None  # an old server
    assert identity.detached_check(FakeProc([], {7: scope, 50: scope}), agent_pid=7, tmux_pid=50) is None  # a dev run
    assert identity.detached_check(FakeProc([], {7: "/", 50: "/"}), agent_pid=7, tmux_pid=50) is None  # a container
    assert identity.detached_check(FakeProc([], {7: svc}), agent_pid=7, tmux_pid=None) is None  # no server yet
    # CI, and an agent a worker starts from inside an `ao` pane: one `.service` holds the agent, its
    # tmux server *and the person standing in it* — the cgroup tells nobody apart, so the check is
    # off unless systemd itself started the agent (the first push of this module failed CI on it)
    shared = FakeProc([P(1, 0, 1), P(30, 1, 30), P(7, 30, 30)], {7: svc, 50: svc})
    shared.comms[30] = "bash"
    assert identity.detached_check(shared, agent_pid=7, tmux_pid=50) is None
    shared.comms[30] = "systemd"
    assert identity.detached_check(shared, agent_pid=7, tmux_pid=50) == svc


def test_a_pid_reused_under_the_walk_ends_ancestry_and_never_lands_on_another_pane():
    """The peer's parent (110, under X) exits and its pid is taken by a process under Y between
    the reads. The double read sees the hop change; ancestry does not answer; the session id does —
    and it says X, which is the truth."""
    fp = FakeProc([*BASE, P(110, 100, 100, 0x8801), P(111, 110, 100, 0x8801)])
    reused = P(110, 200, 200, 0x8802, start=99999)  # same pid, another process, under Y
    fp.script[110] = [P(110, 100, 100, 0x8801), reused]  # first read: the real parent; re-read: the impostor
    got = identity.classify(111, PANES, fp)
    assert got == Channel("session", "ao-x", "sid")
    # and when the peer itself was reparented mid-walk, the re-read of its ppid catches it
    fp = FakeProc([*BASE, P(110, 100, 100, 0x8801), P(111, 110, 100, 0x8801)])
    fp.script[111] = [P(111, 110, 100, 0x8801), P(111, 1, 100, 0x8801)]
    assert identity.classify(111, PANES, fp).signal == "sid"


def test_start_times_are_never_compared_a_subreaper_may_be_younger_than_what_it_adopts():
    # 110 (start 500) was adopted by a subreaper 60 that started later (start 900), itself under X
    fp = FakeProc([*BASE, P(60, 100, 100, 0x8801, start=900), P(110, 60, 100, 0x8801, start=500)])
    assert identity.classify(110, PANES, fp) == Channel("session", "ao-x", "ancestry")


def test_the_walk_is_bounded():
    chain = [P(1000 + i, 1000 + i + 1, 5000 + i) for i in range(200)]
    chain.append(P(1200, 100, 100))
    assert identity.classify(1000, PANES, FakeProc([*BASE, *chain])) == identity.OUTSIDE


# -- the rule table -------------------------------------------------------------------------------

X = Channel("session", "ao-x", "ancestry")


def test_the_channel_decides_and_a_claim_that_disagrees_is_refused_and_alarmed():
    assert identity.judge(X, "ao-x", "msg") == identity.Verdict("ao-x")
    assert identity.judge(X, None, "msg") == identity.Verdict("ao-x")  # a script that forgot the variable
    v = identity.judge(X, "ao-y", "msg")
    assert v.refusal == identity.MISMATCH and v.about == "ao-x"  # kept on the record it is about: X, never Y
    assert v.alarm == {"channel": "session ao-x", "claimed": "ao-y", "rpc": "msg"}
    assert identity.judge(identity.OUTSIDE, None, "inbox_delete") == identity.Verdict(None)  # the person
    v = identity.judge(identity.OUTSIDE, "ao-x", "msg")
    assert v.refusal and v.about is None and v.alarm["claimed"] == "ao-x"  # nobody is framed by a claim
    for claim in (None, "ao-x"):
        v = identity.judge(Channel("unknown", signal="cgroup"), claim, "msg")
        assert v.refusal and v.about is None


def test_reads_are_served_on_every_channel_and_raise_nothing():
    for ch in (X, identity.OUTSIDE, Channel("unknown", signal="unreadable")):
        for claim in (None, "ao-x", "ao-y"):
            assert identity.judge(ch, claim, "list") == identity.Verdict(claim)


def test_a_hook_is_bound_to_its_pane():
    assert identity.judge(X, None, "hook", hook_session="ao-x").refusal is None
    v = identity.judge(X, None, "hook", hook_session="ao-y")
    assert v.refusal and v.about == "ao-x" and v.alarm["claimed"] == "ao-y"
    assert identity.judge(identity.OUTSIDE, None, "hook", hook_session="ao-x").refusal  # a hook runs under a pane


def test_identical_alarms_coalesce_so_a_loop_cannot_evict_a_different_one():
    a = {"channel": "session ao-x", "claimed": "ao-y", "rpc": "msg"}
    b = {"channel": "outside", "claimed": "ao-x", "rpc": "kill"}
    alarms = identity.coalesce([], b, "t0")
    for i in range(1000):
        alarms = identity.coalesce(alarms, a, f"t{i + 1}")
    assert len(alarms) == 2
    assert alarms[0]["rpc"] == "kill" and alarms[0]["count"] == 1
    assert alarms[1]["count"] == 1000 and alarms[1]["at"] == "t1" and alarms[1]["last"] == "t1000"
    # a flood of *distinct* alarms cannot bury the first ones either: the list keeps the earliest
    # and counts the rest in one closing entry (red-team of PR #248)
    many = identity.coalesce([], {"channel": "session ao-a", "claimed": "ao-REAL-TARGET", "rpc": "msg"}, "t0")
    for i in range(identity.ALARMS_KEEP + 30):
        many = identity.coalesce(many, {"channel": "session ao-a", "claimed": f"ao-junk-{i}", "rpc": "msg"}, f"t{i}")
    assert len(many) == identity.ALARMS_KEEP and many[0]["claimed"] == "ao-REAL-TARGET"
    assert many[-1]["claimed"] == identity.OTHERS and many[-1]["count"] == 30 + 1 + 1


def test_the_cgroup_line_and_the_mode():
    assert identity.cgroup_path(["0::/user.slice/a.service"]) == "/user.slice/a.service"
    assert identity.cgroup_path(["12:pids:/x", "1:name=systemd:/system.slice/a.service"]) == "/system.slice/a.service"
    assert identity.cgroup_path(["12:pids:/x"]) is None
    assert identity.mode_of("enforce") == "enforce" and identity.mode_of("Observe") == "observe"
    assert identity.mode_of("nonsense") == identity.DEFAULT_MODE  # never silently off


def test_proc_is_read_as_it_is():
    import os

    me = identity.LinuxProc().stat(os.getpid())
    assert me is not None and me.pid == os.getpid() and me.ppid == os.getppid() and me.sid == os.getsid(0)
    assert identity.LinuxProc().stat(2**22 + 12345) is None


# -- enforce, against real panes ------------------------------------------------------------------

PROBE = textwrap.dedent(
    """
    import json, os, socket, sys, time
    sock_path, out, delay, req = sys.argv[1], sys.argv[2], float(sys.argv[3]), json.loads(sys.argv[4])
    time.sleep(delay)
    s = socket.socket(socket.AF_UNIX); s.connect(sock_path)
    s.sendall((json.dumps(req) + "\\n").encode())
    buf = b""
    while not buf.endswith(b"\\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    # Written beside the path and renamed onto it, so `out` exists only once it is **whole**:
    # `open(out, "w")` creates the file empty, and the waiter — which polls for the path — then
    # read nothing and died in the JSON decoder (TD-078, a third sighting 2026-09-20). A rename
    # within one directory is atomic, so existence is the right signal again.
    open(out + ".part", "w").write(buf.decode())
    os.replace(out + ".part", out)
    """
)


async def _probe(client, tmp_path, pane: str, tag: str, req: dict, *, detach: bool = False) -> dict:
    """Run one request from a process inside `pane`, and return the agent's answer."""
    script, out = tmp_path / "probe.py", tmp_path / f"{tag}.json"
    script.write_text(PROBE)
    argv = [sys.executable, str(script), str(paths.socket_path()), str(out), "0.6" if detach else "0", json.dumps(req)]
    line = " ".join(shlex.quote(a) for a in argv)
    if detach:  # double fork + setsid, no terminal: the subshell exits at once, the probe connects after
        line = f"(setsid {line} </dev/null >/dev/null 2>&1 &)"
    await client.call("send", id=pane, text=line)
    assert await wait_for(out.exists, timeout=15.0, step=0.1), f"the probe {tag} never answered"
    return json.loads(out.read_text())


async def test_enforce_against_real_panes(agent, tmp_path):
    agent.identity_mode = "enforce"
    async with LocalClient() as me:  # the pytest process: under no pane, no caller — the person
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        b = (await me.call("create", name="b", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]

        def doing(caller: str | None, target: str, line: str) -> dict:
            req = {"id": 1, "method": "doing", "params": {"id": target, "text": line}}
            return {**req, "caller": caller} if caller else req

        # as itself
        got = await _probe(me, tmp_path, a, "self", doing(a, a, "as myself"))
        assert got["result"]["doing"]["text"] == "as myself"
        # no caller, under a pane: a script that forgot the variable is still that session, not a person
        got = await _probe(me, tmp_path, a, "bare", doing(None, a, "forgot the variable"))
        assert got["result"]["doing"]["text"] == "forgot the variable"
        # as another session: refused, and the alarm is on the pane it came from — never on the claim
        got = await _probe(me, tmp_path, a, "forge", doing(b, b, "I am b"))
        assert got["error"] == identity.MISMATCH
        assert (await me.call("get", id=b))["doing"] is None
        alarms = (await me.call("get", id=a))["identity_alarms"]
        assert [(x["channel"], x["claimed"], x["rpc"], x["count"]) for x in alarms] == [(f"session {a}", b, "doing", 1)]
        assert (await me.call("get", id=b))["identity_alarms"] == []
        # a person-only act from under a pane with no caller is a session's, and refused as one
        got = await _probe(me, tmp_path, a, "person", {"id": 1, "method": "inbox_delete", "params": {"msg": "m-0"}})
        assert "error" in got and got["error"] != identity.MISMATCH
        # whoami, from the pane and from here
        got = await _probe(me, tmp_path, a, "who", {"id": 1, "method": "whoami", "params": {}})
        assert got["result"]["channel"] == "session" and got["result"]["session"] == a
        assert (await me.call("whoami"))["channel"] == "outside"
        # detached completely — double fork, setsid, no terminal — it has shed every signal: outside
        # here (the cgroup check is off under a test), and outside naming a session is refused
        got = await _probe(me, tmp_path, a, "detached", doing(a, a, "from the void"), detach=True)
        assert got["error"] == identity.MISMATCH
        assert (await me.call("get", id=a))["doing"]["text"] == "forgot the variable"

    # the pytest process naming a session: outside + a claim — refused, the alarm blames no record
    async with LocalClient(caller=a) as forged:
        try:
            await forged.call("doing", id=a, text="pytest says it is a")
            raise AssertionError("an outside process naming a session was served")
        except Exception as e:  # noqa: BLE001 — the client's error type is not what is under test
            assert identity.MISMATCH in str(e)
        assert (await forged.call("list")) is not None  # a read is served on every channel
    async with LocalClient() as me:
        report = await me.call("identity")
        assert report["mode"] == "enforce" and report["detached_check"] is False
        claimed = {(x["channel"], x["claimed"]) for x in report["alarms"]}
        assert ("outside", a) in claimed
        assert report["tally"].get("session:ancestry", 0) >= 4 and report["tally"].get("outside", 0) >= 3


async def test_observe_records_the_alarm_and_refuses_nothing(agent, tmp_path):
    agent.identity_mode = "observe"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    async with LocalClient(caller=a) as as_a:  # what every other test in the suite does
        got = await as_a.call("doing", id=a, text="served, as before")
        assert got["doing"]["text"] == "served, as before"
        report = await as_a.call("identity")
    assert report["mode"] == "observe"
    # a connection is classified once, for its life: two connections here, however many requests
    assert report["tally"] == {"outside": 2}
    assert [(x["channel"], x["claimed"], x["rpc"]) for x in report["alarms"]] == [("outside", a, "doing")]


async def test_the_detached_check_follows_the_tmux_server_it_is_about(agent, monkeypatch):
    """TD-077: the check is a fact about *that* tmux server's cgroup, and a server can be replaced
    while the agent runs — kmaster's was, on 2026-09-20. Computed once and kept, the answer went on
    describing a process that no longer existed until the agent restarted. The tick re-reads the
    server's pid on its own cadence; only a pid that moved costs the check itself."""
    import sessionorc.agent as agent_mod
    from sessionorc import identity

    pid, checks = 50, []

    def check(_reader, *, agent_pid, tmux_pid):
        checks.append(tmux_pid)
        return "/system.slice/agentorc-agent.service" if tmux_pid == 50 else None

    monkeypatch.setattr(agent_mod.identity, "detached_check", check)
    monkeypatch.setattr(agent.tmux, "server_pid", lambda: pid)
    monkeypatch.setattr(agent_mod, "ID_RECHECK", 0.0)  # every tick, so the test is not a sleep

    await agent._id_recheck_detached()
    assert checks == [50] and agent._id_detached  # on, against this server
    await agent._id_recheck_detached()
    assert checks == [50], "the same server is not re-checked, only its pid re-read"

    pid = 51  # the server was replaced under the running agent
    await agent._id_recheck_detached()
    assert checks == [50, 51] and agent._id_detached == "", "off: the new server is judged on its own cgroup"

    # a replacement that lands on the same pid is still a different server: the start time is
    # read with it, as the ancestry walk pairs the two (review of PR #265)
    pid, started = 50, 4242
    monkeypatch.setattr(agent.proc, "stat", lambda p: identity.Proc(pid=p, ppid=1, sid=p, tty_nr=0, start=started))
    await agent._id_recheck_detached()
    assert checks == [50, 51, 50] and agent._id_detached  # on again: this is pid 50's cgroup
    started = 9999  # the same pid, a server that started later
    await agent._id_recheck_detached()
    assert checks == [50, 51, 50, 50], "a reused pid is not taken for the server it replaced"

    pid = None  # and no server at all is *not yet known*, so the next connection asks again
    await agent._id_recheck_detached()
    assert agent._id_detached is None


async def test_off_classifies_nothing(agent, tmp_path):
    assert agent.identity_mode == "off"  # conftest: the suite's default
    async with LocalClient(caller="ao-anything") as c:
        await c.call("list")
        assert (await c.call("identity"))["tally"] == {}
        assert (await c.call("whoami")) == {"channel": None, "session": None, "signal": None}


async def test_a_bug_in_the_check_serves_as_before_in_observe_and_refuses_in_enforce(agent, tmp_path, monkeypatch):
    """`observe` promises that nothing a caller sees changes — a check that raises included. Under
    `enforce` a check that can be made to fail would be a way round it: all but a read is refused."""

    async def boom(*_a, **_k):
        raise RuntimeError("the classification fell over")

    monkeypatch.setattr(agent, "_id_channel", boom)
    agent.identity_mode = "observe"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    async with LocalClient(caller=a) as as_a:
        assert (await as_a.call("doing", id=a, text="served"))["doing"]["text"] == "served"
    agent.identity_mode = "enforce"
    async with LocalClient() as me:
        assert await me.call("list") is not None  # a read is still served
        try:
            await me.call("doing", id=a, text="nope")
            raise AssertionError("a request was served past a failed check under enforce")
        except Exception as e:  # noqa: BLE001
            assert "identity check failed" in str(e)


async def test_a_loop_of_forgeries_is_one_write_then_counts_in_memory_until_the_tick(agent, tmp_path, monkeypatch):
    agent.identity_mode = "observe"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    s = agent.sessions[a]
    saves: list[str] = []
    real = agent._save
    monkeypatch.setattr(agent, "_save", lambda rec: (saves.append(rec.id), real(rec))[1])
    entry = {"channel": f"session {a}", "claimed": "ao-b", "rpc": "msg"}
    for _ in range(500):
        agent._id_alarm(dict(entry), a)
    assert saves.count(a) == 1 and s.identity_alarms[0]["count"] == 500 and a in agent._id_dirty
    agent._id_alarm({**entry, "rpc": "kill"}, a)  # a different alarm is written at once
    assert saves.count(a) == 2
    agent._id_flush()
    assert saves.count(a) == 3 and not agent._id_dirty


async def test_a_hook_is_bound_to_its_pane_end_to_end(agent, tmp_path):
    agent.identity_mode = "enforce"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        b = (await me.call("create", name="b", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]

        def hook(session: str) -> dict:  # what hook.py sends: no `caller`, the session as a parameter
            return {"id": 1, "method": "hook", "params": {"session": session, "state": "idle"}}

        got = await _probe(me, tmp_path, a, "hook-own", hook(a))
        assert got.get("error") != identity.MISMATCH
        got = await _probe(me, tmp_path, a, "hook-other", hook(b))
        assert got["error"] == identity.MISMATCH
        alarms = (await me.call("get", id=a))["identity_alarms"]
        assert [(x["claimed"], x["rpc"]) for x in alarms] == [(b, "hook")]
        assert (await me.call("get", id=b))["identity_alarms"] == []
    # and from outside every pane a hook is never a person's: refused
    async with LocalClient() as me:
        try:
            await me.call("hook", session=a, state="idle")
            raise AssertionError("a hook from outside every pane was served")
        except Exception as e:  # noqa: BLE001
            assert identity.MISMATCH in str(e)


def test_no_read_decides_anything_on_its_caller():
    """The rule `identity.READS` rests on: a read is served under any claim, so it must not
    authorise on one. `host_files` was listed until the red-team of PR #248 — it serves the person
    or a `control` holder, so a session that left `caller` out read a checkout as the person."""
    import inspect

    from sessionorc.agent import HostAgent

    for name in sorted(identity.READS):
        fn = getattr(HostAgent, f"rpc_{name}")
        assert "caller" not in inspect.signature(fn).parameters, f"{name} takes a caller: it is not a plain read"
    assert "host_files" not in identity.READS
    # and from under a pane even a read runs as that pane's session (judge leaves the claim; the
    # agent's step replaces it) — checked end to end below


async def test_host_files_from_under_a_pane_is_that_session_never_the_person(agent, tmp_path):
    agent.identity_mode = "enforce"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        req = {"id": 1, "method": "host_files", "params": {"host": agent.host, "dir": str(tmp_path), "paths": []}}
        got = await _probe(me, tmp_path, a, "hostfiles", req)  # no caller: it used to pass as the person
        assert "error" in got and "control" in got["error"]
        got = await _probe(me, tmp_path, a, "badparams", {"id": 1, "method": "list", "params": 5})
        assert got["error"] == "params must be an object"  # and the connection answered, not dropped


# -- the host's own list persists, and Acknowledge (TD-077 step 2) ---------------------------------


async def test_the_hosts_own_alarms_persist_across_a_restart_and_the_tally_does_not(agent, tmp_path):
    """§4.8a (step 2): an alarm about **no record** — a claim from outside every pane — has nowhere
    but the host's own list to live, and evidence that died with the process would make the page
    say *no alarms* about the night the agent was restarted. It follows the records' own write
    rule: a new alarm at once, a repeat counted in memory until the tick. The **tally** is not
    persisted: it says *since the agent started*, and means it."""
    from sessionorc.agent import HostAgent
    from sessionorc.store import IdentityAlarmStore

    entry = {"channel": "outside", "claimed": "ao-x", "rpc": "msg"}
    agent._id_alarm(dict(entry), None)
    assert paths.identity_alarms_file().is_file()
    assert paths.identity_alarms_file().stat().st_mode & 0o777 == 0o600
    assert [a["claimed"] for a in IdentityAlarmStore().load()] == ["ao-x"]

    for _ in range(50):  # a loop of repeats is one count in memory, not fifty disk writes
        agent._id_alarm(dict(entry), None)
    assert agent.identity_alarms[0]["count"] == 51 and agent._id_host_dirty
    assert IdentityAlarmStore().load()[0]["count"] == 1  # not written yet
    agent._id_flush()
    assert IdentityAlarmStore().load()[0]["count"] == 51 and not agent._id_host_dirty

    agent.identity_tally["outside"] += 3
    fresh = HostAgent(tmux=agent.tmux)  # what a restart reads
    assert [(a["claimed"], a["count"]) for a in fresh.identity_alarms] == [("ao-x", 51)]
    assert dict(fresh.identity_tally) == {}


async def test_dismiss_clears_a_list_for_a_person_and_for_nobody_else(agent, tmp_path):
    """§4.5a **Inbox row: identity alarm** → **Dismiss** (`identity_ack` — the control was renamed
    from *Acknowledge* on 2026-09-20, TD-077 a1, and the **wire name stays**: it is in `NODE_ACTS`,
    where a rename is a protocol change that buys a person nothing). A person clears one record's
    alarms, or — naming none — the host's own, and the row leaves the Inbox, leaving a trail entry
    that says **dismissed by you**. It is refused to **every** session, its own alarms included: a
    session that could clear the list could erase the evidence of its own forgery. The agent's log
    keeps every alarm, so nothing is lost by dismissing."""
    from sessionorc.store import IdentityAlarmStore

    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
    agent._id_alarm({"channel": f"session {a}", "claimed": "ao-b", "rpc": "msg"}, a)
    agent._id_alarm({"channel": "outside", "claimed": a, "rpc": "kill"}, None)

    # a session is refused — including the one the alarms are about, and with identity `off`, where
    # the person gate is the only thing standing between it and the list
    assert agent.identity_mode == "off"
    for who in (a, "ao-somebody-else"):
        async with LocalClient(caller=who) as session:
            for params in ({"id": a}, {}):
                try:
                    await session.call("identity_ack", **params)
                    raise AssertionError(f"{who} cleared an alarm list")
                except AssertionError:
                    raise
                except Exception as e:  # noqa: BLE001 — the client's error type is not under test
                    assert "only by a person" in str(e)
    assert (await agent.rpc_identity())["sessions"][a]  # nothing was cleared by any of that

    async with LocalClient() as person:
        got = await person.call("identity_ack", id=a)
        assert got["cleared"] is True and got["alarms"] == []
        # the word the trail will carry when the alarm row ends, in the control's own name
        assert agent._attention_how[f"{a}|alarm"] == "dismissed by you"
        report = await person.call("identity")
        assert a not in report["sessions"] and [x["claimed"] for x in report["alarms"]] == [a]
        assert (await person.call("identity_ack"))["id"] == "person"
        assert (await person.call("identity"))["alarms"] == []
    assert IdentityAlarmStore().load() == []  # and the cleared list is what a restart reads


async def test_a_session_under_a_pane_cannot_acknowledge_its_own_alarms_under_enforce(agent, tmp_path):
    """The same rule where the channel decides rather than the envelope (§4.8a): from inside its own
    pane, with no `caller` at all, a session is still a session — and `identity_ack` is not among
    the never-gated reads, so the evidence stays where it is."""
    agent.identity_mode = "enforce"
    async with LocalClient() as me:
        a = (await me.call("create", name="a", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        agent._id_alarm({"channel": f"session {a}", "claimed": "ao-b", "rpc": "msg"}, a)
        got = await _probe(me, tmp_path, a, "ack", {"id": 1, "method": "identity_ack", "params": {"id": a}})
        assert "error" in got and "only by a person" in got["error"]
        assert (await me.call("get", id=a))["identity_alarms"]
        assert "identity_ack" not in identity.READS


def test_a_nodes_session_alarms_reach_the_home_on_the_replica():
    """§4.4a / §4.8a: alarms are **observed where the socket is**, so they are node-owned on the
    record like `tail` — and they reach the home's card and Inbox row through the ordinary replica,
    with nothing built for them. The host's own list of a node is that node's: `identity` names no
    `id`, so nothing routes it over a link, and the home's page shows the home's own (TD-077)."""
    from sessionorc.models import NODE_OWNED, Session, apply_node

    assert "identity_alarms" in NODE_OWNED
    here = Session(id="ao-x@node", name="x", kind="agent", adapter="shell", dir="/w", host="node")
    alarm = {"channel": "session ao-x", "claimed": "ao-y", "rpc": "msg", "count": 2, "at": "t0", "last": "t1"}
    assert apply_node(here, {"identity_alarms": [alarm]}).identity_alarms == [alarm]


async def test_suspend_stops_a_session_and_keeps_it_stopped(agent, hookstub, tmp_path):
    """§4.8a *An alarm's answers* → **Suspend** (TD-077 a2): a person stops a session under
    suspicion at once — no wrap-up, a session under suspicion is not asked to tidy — and the
    record wears `suspended: {at, by, why}`, the home's own field. Then every road a **session**
    has to bring it back is closed: a create under its name and a resume of its conversation, the
    one exception to §4.1's rule that an exited holder is superseded. A **person** walks either
    road, and doing so is what lifts it."""
    (tmp_path / "w").mkdir()
    async with LocalClient() as person, LocalClient() as feeder:
        # unattended, which is the class an identity alarm most often fires on — and the class
        # invariant 5 does *not* shield from a controller's acts, so the suspension is what has
        # to refuse them (review of PR #301)
        s = await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True)
        sid = s["id"]
        await feeder.call("hook", session=sid, adapter_id="conv-77", state="idle")
        await wait_state(person, sid, "idle")
        agent._id_alarm({"channel": f"session {sid}", "claimed": "ao-b", "rpc": "msg"}, sid)

        # a session cannot suspend — the mirror of `identity_ack`: one could stop its rival
        async with LocalClient(caller="ao-somebody") as session:  # no grant is needed to be refused
            with pytest.raises(AgentError, match="a person's own act"):
                await session.call("suspend", id=sid)
        assert (await person.call("get", id=sid))["suspended"] is None

        got = await person.call("suspend", id=sid)
        assert got["suspended"]["by"] == "person" and got["suspended"]["why"] == "ao-b"
        assert got["state"] == "exited" and got["pane"] is False  # stopped as a kill stops it
        assert (await person.call("get", id=sid))["identity_alarms"]  # the alarms stay: it is not an answer
        assert (await person.call("inbox"))["trail"] == []  # a suspension ends no row, so it writes none

        # both roads are refused to a session, by name and by conversation — under a caller that
        # holds `control`, so the refusal it meets is the suspension's and not the create gate's
        (tmp_path / "b").mkdir()
        boss = (await person.call("create", name="b", dir=str(tmp_path / "b"), adapter="shell", argv=["bash"]))["id"]
        await person.call("set_grants", id=boss, add=["control"])
        async with LocalClient(caller=boss) as session:
            with pytest.raises(AgentError, match="suspended by a person.*a create under that name"):
                await session.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name)
            with pytest.raises(AgentError, match="suspended by a person.*a resume of that conversation"):
                await session.call("create", name="w2", dir=str(tmp_path), adapter=hookstub.name, resume="conv-77")
        # and the name check says so in its own verdict, which is what `ao team start` reads
        v = await person.call("name_check", dir=str(tmp_path / "w"), name="w")
        assert v["verdict"] == "suspended" and "a person lifts it" in v["message"]

        # twice is refused, and so is suspending what is already stopped
        with pytest.raises(AgentError, match="already suspended"):
            await person.call("suspend", id=sid)

        # **Forget is the other road out, so it is a person's too** (review of PR #301): the
        # acting gate would otherwise let a controller remove an unattended member's record,
        # which frees the name and lets the suspect be started again, unmarked
        await person.call("set_controllers", id=sid, add=[boss])
        async with LocalClient(caller=boss) as session:
            with pytest.raises(AgentError, match="suspended by a person.*forgetting it"):
                await session.call("remove", id=sid)
        assert (await person.call("get", id=sid))["suspended"]

        # the person's own create under that name goes through, and takes the mark with it
        again = await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name)
        assert again["id"] == sid and again["suspended"] is None
        for x in (sid, boss):
            await person.call("kill", id=x)


async def test_a_persons_resume_elsewhere_lifts_a_suspension_too(agent, hookstub, tmp_path):
    """§4.8a: a suspension is lifted by *a person's own resume of that conversation* — under any
    name, not only its old one. Resumed elsewhere the old record stays standing, so the mark has
    to be cleared where the create is, or that name would read `suspended` for ever with nothing
    left able to clear it (review of PR #301)."""
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    async with LocalClient() as person, LocalClient() as feeder:
        sid = (await person.call("create", name="a", dir=str(tmp_path / "a"), adapter=hookstub.name))["id"]
        await feeder.call("hook", session=sid, adapter_id="conv-lift", state="idle")
        await wait_state(person, sid, "idle")
        await person.call("suspend", id=sid, why="claimed another session's id")
        assert (await person.call("get", id=sid))["suspended"]
        moved = await person.call(
            "create", name="b", dir=str(tmp_path / "b"), adapter=hookstub.name, resume="conv-lift"
        )
        assert moved["id"] != sid and moved["suspended"] is None
        # the old record is closed by the supersede, and its name is free again
        old = await person.call("get", id=sid)
        assert old["state"] == "closed" and old["suspended"] is None
        assert (await person.call("name_check", dir=str(tmp_path / "a"), name="a"))["verdict"] == "supersede"
        await person.call("kill", id=moved["id"])


async def test_suspend_is_refused_on_a_record_that_is_already_stopped(agent, tmp_path):
    """It is offered only while the session is live (§4.5a): a mark with no act to go with it
    would say a person stopped something that had already stopped."""
    async with LocalClient() as person:
        sid = (await person.call("create", name="q", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        await person.call("kill", id=sid)
        await wait_state(person, sid, "exited")
        with pytest.raises(AgentError, match="there is nothing to stop"):
            await person.call("suspend", id=sid)
        assert (await person.call("get", id=sid))["suspended"] is None


async def test_log_td_hands_an_alarm_to_the_session_that_answers_for_it(agent, hookstub, tmp_path):
    """§4.8a *An alarm's answers* → **Log TD** (TD-077 b): the host agent does not write a repo's
    ledger, so *filing* is handing the alarm to the session that answers for this one — the
    record's **first live controller**, read from the control graph and never from a badge. It
    goes as mail from the person, **marked `handed`, so it owes an outcome**, which is new:
    TD-079's debt existed only on a session's own question to the person, so work handed the
    other way could be dropped in silence. Then the list is cleared and the trail says so."""
    for d in ("l", "w"):
        (tmp_path / d).mkdir()
    async with LocalClient() as person, LocalClient() as feeder:
        lead = (await person.call("create", name="l", dir=str(tmp_path / "l"), adapter="shell", argv=["bash"]))["id"]
        w = (await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True))[
            "id"
        ]
        await feeder.call("hook", session=w, state="idle")
        await wait_state(person, w, "idle")
        async with LocalClient(caller=w) as itself:
            await itself.call("doing", id=w, text="TD-431: reproducing the race")

        # with no controller there is nobody to hand it to, and the page is told before it draws
        for _ in range(3):  # the same claim three times: the list keeps the first and counts the rest
            agent._id_alarm({"channel": f"session {w}", "claimed": "ao-b", "rpc": "msg"}, w)
        assert (await person.call("get", id=w))["alarm_to"] is None
        with pytest.raises(AgentError, match="no session answers for"):
            await person.call("identity_log", id=w)

        await person.call("set_controllers", id=w, add=[lead])
        assert (await person.call("get", id=w))["alarm_to"] == {"id": lead, "name": "l"}
        async with LocalClient(caller=lead) as session:  # a session may not file one
            with pytest.raises(AgentError, match="a person's own act"):
                await session.call("identity_log", id=w)

        got = await person.call("identity_log", id=w)
        assert got["filed"] is True and got["to"]["id"] == lead
        assert (await person.call("get", id=w))["identity_alarms"] == []  # cleared, and the row ends
        assert agent._attention_how[f"{w}|alarm"] == "logged by you → l"

        # the lead has it, from the person, and it says what the alarm was — not what a model wrote
        landed = [e for e in (await person.call("inbox", id=lead))["entries"] if e["from"] == "person"]
        assert len(landed) == 1 and landed[0]["id"] == got["entry"]
        body = landed[0]["text"]
        assert "claimed: ao-b" in body and "rpc: msg" in body and "seen 3×" in body
        assert 'it said it was doing: "TD-431: reproducing the race"' in body
        assert "identity off" in body  # which mode the host is in: observe records what enforce refuses

        # **and it owes an outcome** — the debt the person handed on
        assert (await person.call("get", id=lead))["mail"]["owed"] == [got["entry"]]
        async with LocalClient(caller=lead) as session:
            with pytest.raises(AgentError, match="owes 1 outcome"):
                await session.call("progress", id=lead, status="none", why="nothing open")
            await session.call("msg", to="person", text="filed as TD-999", outcome="done", for_=got["entry"])
        assert (await person.call("get", id=lead))["mail"]["owed"] == []
        for sid in (w, lead):
            await person.call("kill", id=sid)


async def test_a_handed_debt_is_not_deleted_away(agent, hookstub, tmp_path):
    """Review of PR #318: a handed entry is the first debt-bearing mail that lives in a session's
    own **inbox** rather than in the asker's outbox, so it is the first that `inbox_delete` can
    reach — and `owes` is computed from the entry, so deleting the object would discharge the
    debt with no outcome, no trail and nobody told. It is refused, with the two roads that do
    end it."""
    for d in ("l", "w"):
        (tmp_path / d).mkdir()
    async with LocalClient() as person, LocalClient() as feeder:
        lead = (await person.call("create", name="l", dir=str(tmp_path / "l"), adapter="shell", argv=["bash"]))["id"]
        w = (await person.call("create", name="w", dir=str(tmp_path / "w"), adapter=hookstub.name, unattended=True))[
            "id"
        ]
        await feeder.call("hook", session=w, state="idle")
        await wait_state(person, w, "idle")
        await person.call("set_controllers", id=w, add=[lead])
        agent._id_alarm({"channel": f"session {w}", "claimed": "ao-b", "rpc": "msg"}, w)
        filed = await person.call("identity_log", id=w)

        with pytest.raises(AgentError, match="still owes an outcome"):
            await person.call("inbox_delete", msg=filed["entry"], id=lead)
        assert (await person.call("get", id=lead))["mail"]["owed"] == [filed["entry"]]

        # settled, and then it is an ordinary entry a person may remove
        async with LocalClient(caller=lead) as session:
            await session.call("msg", to="person", text="filed as TD-999", outcome="done", for_=filed["entry"])
        await person.call("inbox_delete", msg=filed["entry"], id=lead)
        assert [e["id"] for e in (await person.call("inbox", id=lead))["entries"]] == []
        for sid in (w, lead):
            await person.call("kill", id=sid)
