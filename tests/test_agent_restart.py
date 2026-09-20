"""A host agent restart: sessions come back from the store, live panes reconcile, and a record
written before `prompt` pendings stopped being alerts (2026-09-06) comes back idle.

Fully in-process, on its own private tmux server: the second agent must not share a socket and
store with a still-running agent (design §9 invariant 1)."""

import asyncio
import contextlib
from datetime import timedelta

import pytest
from conftest import kill_private_server, private_socket_name, wait_for, wait_state

from sessionorc import paths
from sessionorc.agent import HostAgent
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import Pending, Session
from sessionorc.store import SessionStore
from sessionorc.tmux import Tmux

pytestmark = pytest.mark.integration


async def start(tmux: Tmux) -> tuple[HostAgent, asyncio.Task]:
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    assert await wait_for(lambda: paths.socket_path().exists(), timeout=5.0, step=0.05)
    return a, task


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    assert not paths.socket_path().exists()  # serve()'s finally unlinked it


async def test_restart_reloads_and_reconciles(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", 0.3)
    # Long enough that no tick can judge the paneless record below `exited` before the migration
    # is asserted, however loaded the machine is; the second half of the test shortens it on
    # purpose to exercise the grace passing (TD-078).
    monkeypatch.setattr("sessionorc.agent.CREATE_GRACE", timedelta(seconds=300))
    tmux = Tmux(socket_name=private_socket_name())
    try:
        first, task = await start(tmux)
        async with LocalClient() as c:
            sh = await c.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"])
            cmd = await c.call(
                "create", name="cmd", dir=str(tmp_path), adapter="command", kind="command", argv=["sleep", "30"]
            )
            await wait_state(c, sh["id"], "idle")
            await wait_state(c, cmd["id"], "working")
            await c.call("set_mode", id=sh["id"], unattended=True)
        await stop(task)

        # a pre-2026-09-06 record: pending prompt shown as needs-you
        store = SessionStore(paths.sessions_dir())
        old = Session(id="ao-old-prompt", name="old", kind="interactive", adapter="shell", dir=str(tmp_path))
        old.set_state("needs-you", confidence="hook", pending=Pending(kind="prompt", text="waiting"))
        store.save(old)

        second, task = await start(tmux)
        assert second is not first
        assert set(second.sessions) == {sh["id"], cmd["id"], "ao-old-prompt"}
        assert second.sessions[sh["id"]].unattended is True  # persisted field survives
        migrated = second.sessions["ao-old-prompt"]
        # What the *load* made of the old record, not what a tick has since made of it: the record
        # has no pane, so under the one-second grace this test used to run with, a tick that fired
        # between `start` and here read it as `exited` and the assertion failed — once in a loaded
        # full run, not reproducible alone (TD-078). The grace above is what makes this line about
        # the migration and nothing else.
        assert migrated.state == "idle" and migrated.pending is None and migrated.confidence == "hook"
        async with LocalClient() as c:
            await wait_state(c, sh["id"], "idle")
            await wait_state(c, cmd["id"], "working")
            # no pane behind the old record: it is judged exited once the grace has passed — which
            # is now made to happen rather than waited out, so the test bounds on the rule and not
            # on a race between one second of grace and the machine's load (TD-078)
            monkeypatch.setattr("sessionorc.agent.CREATE_GRACE", timedelta(seconds=0))
            await wait_state(c, "ao-old-prompt", "exited")
            with pytest.raises(AgentError, match="kill it first"):
                await c.call("remove", id=cmd["id"])
            await c.call("kill", id=cmd["id"])
            await wait_state(c, cmd["id"], "exited")
            await c.call("remove", id=cmd["id"])
            assert all(x["id"] != cmd["id"] for x in await c.call("list"))
            await c.call("kill", id=sh["id"])
        await stop(task)
    finally:
        kill_private_server(tmux)


async def test_a_record_holding_orchestrate_is_read_as_control_and_rewritten(tmp_path, monkeypatch, caplog):
    """TD-055 step 3: the `orchestrate` grant is `control`. A record stored before the rename is
    normalised on load, saved with the new name, and logged once; the gate honours it; a request
    that still says `orchestrate` is read as `control`."""
    import json

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    paths.ensure_layout()
    store = SessionStore()
    old = Session(id="ao-old-lead", name="old-lead", kind="interactive", adapter="shell", dir=str(tmp_path))
    old.capabilities = ["orchestrate"]
    store.save(old)
    raw = json.loads((paths.sessions_dir() / "ao-old-lead.json").read_text())
    assert raw["capabilities"] == ["orchestrate"]  # really stored under the old name
    tmux = Tmux(socket_name=private_socket_name())
    try:
        with caplog.at_level("WARNING"):
            a = HostAgent(tmux=tmux)
        assert a.sessions["ao-old-lead"].capabilities == ["control"]
        assert json.loads((paths.sessions_dir() / "ao-old-lead.json").read_text())["capabilities"] == ["control"]
        assert "grant orchestrate is now `control`" in caplog.text
        other = Session(id="ao-w", name="w", kind="interactive", adapter="shell", dir=str(tmp_path))
        a.sessions["ao-w"] = other
        await a.rpc_set_grants("ao-w", add=["orchestrate"])
        assert other.capabilities == ["control"]
        await a.rpc_set_grants("ao-w", remove=["orchestrate"])
        assert other.capabilities == []
    finally:
        kill_private_server(tmux)


def test_restart_seeds_hook_freshness(tmp_path, monkeypatch):
    """TD-015: after a restart a hook-confirmed record counts as freshly reported (as of the load),
    so a screen rule cannot outrank it until the stall window passes; a scraped record is cold."""
    from datetime import UTC, datetime

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    paths.ensure_layout()
    store = SessionStore()
    hooked = Session(id="ao-h", name="h", kind="interactive", adapter="hookstub", dir=str(tmp_path), confidence="hook")
    hooked.since = "2026-01-01T00:00:00Z"  # an old transition: not what freshness is about
    scraped = Session(id="ao-s", name="s", kind="interactive", adapter="shell", dir=str(tmp_path))
    store.save(hooked)
    store.save(scraped)
    tmux = Tmux(socket_name=private_socket_name())
    try:
        a = HostAgent(tmux=tmux)
        assert a._hook_fresh("ao-h", datetime.now(UTC)) and not a._hook_fresh("ao-s", datetime.now(UTC))
        assert a._hook_fresh("ao-h", datetime.now(UTC) + timedelta(minutes=19))
        assert not a._hook_fresh("ao-h", datetime.now(UTC) + timedelta(minutes=21))
    finally:
        kill_private_server(tmux)


def test_sigterm_stops_serve_cleanly(tmp_path, monkeypatch):
    """TD-024: SIGTERM cancels the serve task (`serve_until_signal`), so `serve()`'s `finally`
    unlinks the socket and the process exits 0 without asyncio's pending-task traceback. Runs the
    test child, which stops exactly the way `agentorc-agent serve` does."""
    import os
    import signal
    import subprocess
    import sys

    from conftest import CHILD, wait_for_sync

    home = tmp_path / "home"
    sock_name = private_socket_name()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    monkeypatch.setenv("AGENTORC_TMUX_SOCKET", sock_name)
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), sock_name],
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        sock = home / "agent.sock"
        assert wait_for_sync(lambda: sock.exists() or proc.poll() is not None, timeout=10.0)
        assert proc.poll() is None, proc.communicate(timeout=5)[1]
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, err
        assert not sock.exists(), "serve()'s finally did not unlink the socket"
        assert "Task was destroyed" not in err and "Traceback" not in err, err
    finally:
        if proc.poll() is None:
            proc.kill()
        kill_private_server(Tmux(socket_name=sock_name))


async def test_stop_closes_a_subscriber_and_a_blocked_wait(tmp_path, monkeypatch):
    """TD-058: leaving `async with server` awaits `wait_closed()`, which waits for every open client
    connection — the UI's subscription and a lead blocked in `wait` never end on their own, so a
    stop hung until systemd's SIGKILL. The serve task must finish within a second of its cancel."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    tmux = Tmux(socket_name=private_socket_name())
    try:
        _, task = await start(tmux)
        sock = str(paths.socket_path())
        sub_r, sub_w = await asyncio.open_unix_connection(sock)
        sub_w.write(b'{"id": 1, "method": "subscribe"}\n')
        await sub_w.drain()
        assert b"subscribed" in await asyncio.wait_for(sub_r.readline(), 5)
        wait_r, wait_w = await asyncio.open_unix_connection(sock)
        wait_w.write(b'{"id": 2, "method": "wait", "params": {"timeout": 600}}\n')
        await wait_w.drain()
        await asyncio.sleep(0.3)  # the wait is blocked on the agent side
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=1.0)  # never `await task` bare: unfixed, it hangs
        assert task in done, "the serve task did not finish within a second of its cancel"
        assert not paths.socket_path().exists()  # serve()'s finally unlinked it
        assert await asyncio.wait_for(wait_r.read(), 1) == b""  # the agent closed the wait's connection
        for w in (sub_w, wait_w):
            w.close()
    finally:
        kill_private_server(tmux)


def test_prune_runs_keeps_live_logs(tmp_path, monkeypatch):
    """Run-log retention (design §4.6, TD-004): logs older than `runs_keep_days` go, except a running
    session's; `0` keeps everything."""
    import os
    from datetime import UTC, datetime

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    paths.ensure_layout()
    (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 7\n")
    old_t = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    live_log, dead_log, orphan, fresh = (paths.runs_dir() / n for n in ("live.log", "dead.log", "gone.log", "new.log"))
    for f in (live_log, dead_log, orphan, fresh):
        f.write_text("x")
    for f in (live_log, dead_log, orphan):
        os.utime(f, (old_t, old_t))
    store = SessionStore()
    live = Session(id="ao-l", name="l", kind="interactive", adapter="shell", dir=str(tmp_path), run_log=str(live_log))
    dead = Session(id="ao-d", name="d", kind="interactive", adapter="shell", dir=str(tmp_path), run_log=str(dead_log))
    dead.set_state("exited", confidence="scraped")
    store.save(live)
    store.save(dead)
    tmux = Tmux(socket_name=private_socket_name())
    try:
        a = HostAgent(tmux=tmux)
        asyncio.run(a.tick())  # the first tick sweeps
        assert live_log.exists() and fresh.exists()  # running session's log; a recent file
        assert not dead_log.exists() and not orphan.exists()  # exited session's; a forgotten session's
        os.utime(fresh, (old_t, old_t))
        asyncio.run(a.tick())
        assert fresh.exists()  # within the hour: no sweep
        a._pruned_at -= timedelta(hours=2)
        (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 0\n")
        asyncio.run(a.tick())
        assert fresh.exists()  # due, but 0 keeps everything
        (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 7\n")
        a._pruned_at -= timedelta(hours=2)
        asyncio.run(a.tick())
        assert not fresh.exists() and live_log.exists()
    finally:
        kill_private_server(tmux)
