"""Integration tests against a private tmux server (`-L`), never the user's."""

import os
import subprocess

import conftest
import pytest
from conftest import kill_private_server, private_socket_name
from conftest import wait_for_sync as wait_for

from sessionorc.tmux import Tmux

pytestmark = pytest.mark.integration


@pytest.fixture
def tmux():
    t = Tmux(socket_name=private_socket_name())
    t.ensure_server()
    yield t
    kill_private_server(t)


def test_server_survives_empty(tmux):
    tmux.new_session("ao-t-a", "/", ["sleep", "0.1"], {})
    assert wait_for(lambda: any(p.dead for p in tmux.list_panes()))
    tmux.kill_session("ao-t-a")
    assert tmux.list_panes() == []
    assert tmux.run("list-sessions", check=False).returncode == 0  # server still up: exit-empty off


def test_env_pipe_pane_and_paste(tmux, tmp_path):
    log = tmp_path / "run.log"
    tmux.new_session("ao-t-b", str(tmp_path), ["bash", "--norc", "--noprofile"], {"AGENTORC_SESSION": "ao-t-b"})
    tmux.pipe_pane("ao-t-b", log)
    tmux.send_literal("ao-t-b", "echo env=$AGENTORC_SESSION")
    tmux.send_enter("ao-t-b")
    assert wait_for(lambda: log.exists() and "env=ao-t-b" in log.read_text())
    tmux.paste("ao-t-b", "echo one\necho two")
    tmux.send_enter("ao-t-b")
    assert wait_for(lambda: "one" in log.read_text() and "two" in log.read_text())
    tail = tmux.capture_tail("ao-t-b", 4)
    assert any("two" in row for row in tail)
    info = {p.session: p for p in tmux.list_panes()}["ao-t-b"]
    assert info.current_command == "bash" and not info.dead


def test_exit_status_readable(tmux):
    tmux.new_session("ao-t-c", "/", ["sh", "-c", "exit 3"], {})
    assert wait_for(lambda: any(p.dead and p.session == "ao-t-c" for p in tmux.list_panes()))
    info = {p.session: p for p in tmux.list_panes()}["ao-t-c"]
    assert info.dead_status == 3


def test_the_stale_server_sweep_leaves_a_concurrent_runs_server_alone():
    """TD-025: the sweep exists for servers a killed run left behind, and it used to kill every live
    `ao-test-*` server — including the one a *concurrent* pytest process was in the middle of using,
    which is how a suite run beside a review lost its panes mid-test. A server whose owning process
    is still alive is now left alone; once that process is gone it is swept as before."""
    name = private_socket_name()
    sock = f"/tmp/tmux-{os.getuid()}/{name}"
    t = Tmux(socket_name=name)
    t.ensure_server()
    owner = subprocess.Popen(["sleep", "60"])
    try:
        (conftest.OWNERS / name).write_text(str(owner.pid))  # as another pytest process would
        conftest.sweep_stale_test_servers()
        assert os.path.exists(sock) and t.run("list-sessions", check=False).returncode == 0
        owner.terminate()
        owner.wait(timeout=5)
        # the owner is gone: the server is a leak, and the sweep takes it and its bookkeeping
        conftest.sweep_stale_test_servers()
        assert not os.path.exists(sock) and not (conftest.OWNERS / name).exists()
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=5)
        kill_private_server(t)  # a failed assertion above must not leave a server behind
