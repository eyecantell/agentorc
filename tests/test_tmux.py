"""Integration tests against a private tmux server (`-L`), never the user's."""

import time
import uuid

import pytest

from sessionorc.tmux import Tmux


@pytest.fixture
def tmux():
    t = Tmux(socket_name=f"ao-test-{uuid.uuid4().hex[:8]}")
    t.ensure_server()
    yield t
    t.kill_server()


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


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
