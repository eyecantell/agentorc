"""The installed home's tmux server as a system unit (design §4.1, §4.8a; TD-488, TD-495): where the
server runs, the detached-process check under each placement, and the pane's own cgroup the host
agent makes under the unit's delegated subtree."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import park_ticks
from test_identity import FakeProc, P

from sessionorc import identity

SYSTEM = "/system.slice/agentorc-tmux.service"
USER = "/user.slice/user-1000.slice/user@1000.service/app.slice/agentorc-agent.service"
RUNNER = "/system.slice/actions.runner.eyecantell-agentorc.service"
SCOPE = "/user.slice/user-1000.slice/session-3.scope"


def test_where_the_server_runs():
    assert identity.server_placement(SYSTEM) == "system"
    assert identity.server_placement(SYSTEM + "/") == "system"
    assert identity.server_placement(USER) == "user"
    assert (
        identity.server_placement("/user.slice/user-1000.slice/user@1000.service/app.slice/tmux-spawn-x.scope")
        == "user"
    )
    assert identity.server_placement(SCOPE) == "none"  # a server started from a person's shell
    assert identity.server_placement(RUNNER) == "none"
    assert identity.server_placement("/") == "none"  # a container
    assert identity.server_placement(None) is None and identity.server_placement("") is None


def test_the_detached_check_under_each_placement():
    # the system unit: systemd started the server itself; the agent is a user unit elsewhere — on
    unit = FakeProc([P(1, 0, 1), P(50, 1, 50), P(7, 2, 7)], {50: SYSTEM, 7: USER})
    unit.comms[2] = "systemd"
    assert identity.detached_check(unit, agent_pid=7, tmux_pid=50) == SYSTEM
    # the user units: the server inside the agent's own service, the agent systemd's — on, as before
    user = FakeProc([P(1, 0, 1), P(50, 1, 50), P(7, 2, 7)], {50: USER, 7: USER})
    user.comms[2] = "systemd"
    assert identity.detached_check(user, agent_pid=7, tmux_pid=50) == USER
    # a dev run: a server and an agent started from a shell — off
    dev = FakeProc([P(1, 0, 1), P(50, 1, 50), P(30, 1, 30), P(7, 30, 30)], {50: SCOPE, 7: SCOPE})
    dev.comms[30] = "bash"
    assert identity.detached_check(dev, agent_pid=7, tmux_pid=50) is None
    # a test runner's `.service`: the daemonised server reparented to init, the agent under pytest — off
    ci = FakeProc([P(1, 0, 1), P(50, 1, 50), P(30, 1, 30), P(7, 30, 30)], {50: RUNNER, 7: RUNNER})
    ci.comms[30] = "python"
    assert identity.detached_check(ci, agent_pid=7, tmux_pid=50) is None
    # a server in the unit's cgroup that systemd did not start (a pane's `tmux -L x`): not the unit — off
    odd = FakeProc([P(1, 0, 1), P(50, 60, 50), P(60, 1, 60), P(7, 2, 7)], {50: SYSTEM, 7: USER})
    odd.comms[60] = "bash"
    assert identity.detached_check(odd, agent_pid=7, tmux_pid=50) is None


def _procs(base: Path) -> str:
    return (base / "cgroup.procs").read_text()


def test_a_pane_gets_a_cgroup_of_its_own_and_stale_ones_go(tmp_path):
    server = tmp_path / SYSTEM.strip("/")
    server.mkdir(parents=True)
    (server / "pane-ao-gone").mkdir()  # an empty one the pane list no longer names: removed
    (server / "pane-ao-live").mkdir()  # one a live session holds: kept
    (server / "pane-ao-busy").mkdir()
    (server / "pane-ao-busy" / "x").write_text("")  # not empty (a live process would refuse the rmdir): kept
    got = identity.pane_cgroup(tmp_path, SYSTEM, "ao-new", 4242, live={"ao-live", "ao-new"})
    assert got == f"{SYSTEM}/pane-ao-new"
    assert _procs(server / "pane-ao-new") == "4242\n"
    assert sorted(p.name for p in server.iterdir()) == ["pane-ao-busy", "pane-ao-live", "pane-ao-new"]


def test_a_pane_cgroup_that_cannot_be_written_leaves_nothing(tmp_path, monkeypatch):
    assert identity.pane_cgroup(tmp_path, SYSTEM, "ao-x", 1, live=()) is None  # no server cgroup
    server = tmp_path / SYSTEM.strip("/")
    server.mkdir(parents=True)

    def refused(*a, **kw):
        raise PermissionError("cgroup.procs: permission denied")

    monkeypatch.setattr(identity, "open", refused, raising=False)
    assert identity.pane_cgroup(tmp_path, SYSTEM, "ao-x", 1, live=()) is None
    assert list(server.iterdir()) == [], "the directory it made goes with the failed write"
    if os.geteuid() != 0:  # root writes through a read-only mode
        monkeypatch.undo()
        server.chmod(0o500)
        try:
            assert identity.pane_cgroup(tmp_path, SYSTEM, "ao-x", 1, live=()) is None
        finally:
            server.chmod(0o700)
        assert list(server.iterdir()) == []


async def test_the_create_path_makes_the_pane_cgroup_only_under_the_unit(agent, tmp_path, monkeypatch):
    await park_ticks(agent)
    root = tmp_path / "cgroup"
    (root / SYSTEM.strip("/")).mkdir(parents=True)
    agent.cgroup_root = str(root)
    agent.tmux.ensure_server()
    agent.tmux.new_session("ao-unit", tmp_path, ["sleep", "30"], {})
    try:
        pane = agent.tmux.pane_pid("ao-unit")
        assert pane and pane > 1
        where = {"cg": USER}
        monkeypatch.setattr(agent.proc, "cgroup", lambda pid: where["cg"])
        agent._pane_cgroup("ao-unit")  # under the user manager: tmux's scope stands, nothing made
        assert not (root / SYSTEM.strip("/") / "pane-ao-unit").exists()
        where["cg"] = SYSTEM
        agent._pane_cgroup("ao-unit")
        assert _procs(root / SYSTEM.strip("/") / "pane-ao-unit") == f"{pane}\n"
    finally:
        agent.tmux.kill_session("ao-unit")


async def test_the_doctor_says_where_the_server_runs(agent, monkeypatch):
    me = os.getpid()
    monkeypatch.setattr(agent.tmux, "server_pid", lambda: me)
    for cg, runs in ((SYSTEM, "system"), (USER, "user"), (SCOPE, "none")):
        monkeypatch.setattr(agent.proc, "cgroup", lambda pid, cg=cg: cg)
        got = (await agent.rpc_doctor())["tmux"]
        assert got["cgroup"] == cg and got["runs"] == runs
    monkeypatch.setattr(agent.tmux, "server_pid", lambda: None)
    assert (await agent.rpc_doctor())["tmux"]["runs"] is None
