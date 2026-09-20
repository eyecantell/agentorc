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

from conftest import wait_for

from sessionorc import identity, paths
from sessionorc.client import LocalClient
from sessionorc.identity import Channel, Pane, Proc

# -- a fabricated /proc ---------------------------------------------------------------------------


class FakeProc:
    """pid → Proc, pid → cgroup. `script` lets a test change what a pid reads as between reads —
    the race the double read exists for."""

    def __init__(self, procs: list[Proc], cgroups: dict[int, str] | None = None):
        self.procs = {p.pid: p for p in procs}
        self.cgroups = cgroups or {}
        self.script: dict[int, list[Proc | None]] = {}

    def stat(self, pid: int) -> Proc | None:
        if self.script.get(pid):
            return self.script[pid].pop(0)
        return self.procs.get(pid)

    def cgroup(self, pid: int) -> str | None:
        return self.cgroups.get(pid)


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
    fp = FakeProc([*BASE, P(400, 1, 400, 0)], cgroups={400: svc, 50: svc, 7: svc, 300: "/user.slice/session-3.scope"})
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
    import json, socket, sys, time
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
    open(out, "w").write(buf.decode())
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
