"""`scripts/look_home.py`, the scratch home a builder looks at its UI change in (design §4.9b, TD-291)."""

from __future__ import annotations

import importlib.util
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "look_home.py"


def _load():
    spec = importlib.util.spec_from_file_location("look_home", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_live_home_and_a_long_path_are_refused(tmp_path):
    lh = _load()
    live = Path("~/.agentorc").expanduser()
    assert "live agentorc home" in lh.refuse_live(live)
    assert "live agentorc home" in lh.refuse_live(live / "look")
    assert "too long" in lh.refuse_live(Path("/tmp") / ("x" * 120))
    assert lh.refuse_live(Path("/tmp/aolook-x")) is None
    # the command refuses before it makes anything
    assert lh.main(["--home", str(live / "look")]) == 2
    assert not (live / "look").exists()
    (tmp_path / "there").mkdir()
    assert lh.main(["--home", str(tmp_path / "there")]) == 2  # never an existing directory


def test_it_serves_the_org_with_fixture_sessions_and_leaves_nothing(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "AGENTORC_SESSION"}
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--sessions", "2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    home = sock = None
    try:
        end = time.monotonic() + 60
        line = ""
        while time.monotonic() < end and proc.poll() is None:
            line = proc.stdout.readline()
            if line.startswith("look home:"):
                break
        m = re.match(r"look home: (http://127\.0\.0\.1:\d+/)\s+\(AGENTORC_HOME=(\S+), tmux -L (\S+)\)", line)
        assert m, f"no URL printed: {line!r}"
        url, home, sock = m.group(1), Path(m.group(2)), m.group(3)
        assert home.is_relative_to("/tmp") and (home / "agent.sock").exists()
        assert sock.startswith("ao-look-")
        page = urllib.request.urlopen(url, timeout=20).read().decode()
        assert "look-shell-1" in page and "look-shell-2" in page and "look-shell-3" not in page
        inbox = urllib.request.urlopen(url + "inbox", timeout=30).read().decode()
        assert "A fixture act line." in inbox  # the fixture board, read by the reader the fixture repo carries
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    assert home is not None and not home.exists()  # the home it made is gone
    gone = subprocess.run(["tmux", "-L", sock, "list-sessions"], capture_output=True)
    assert gone.returncode != 0  # and its tmux server with it
