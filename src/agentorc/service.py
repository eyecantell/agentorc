"""`ao service install|uninstall|status`: systemd user units for the host agent and the UI
(design §4.1, §8 "a reboot must not need a human").

Two units, `agentorc-agent` and `agentorc-ui`, under `~/.config/systemd/user/`. `KillMode=process`
on the agent is load-bearing: tmux daemonises inside the service's cgroup, and the default
`control-group` kill mode would take the tmux server — and every session in it — down with any
agent restart or stop. With `process`, only the agent's own process is signalled; the tmux server
is deliberately left running when the unit stops.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_DIR = Path("~/.config/systemd/user").expanduser()
UNITS = ("agentorc-agent", "agentorc-ui")


def _bin(name: str) -> str:
    """The console script next to this interpreter (pipx venv, pdm venv), else whatever PATH finds."""
    candidate = Path(sys.executable).parent / name
    if candidate.is_file():
        return str(candidate)
    return shutil.which(name) or name


def _path_env() -> str:
    """PATH for the units: the venv's bin (agentorc-hook must resolve at launch), the user's
    ~/.local/bin (where `claude` usually lives), then the system defaults."""
    parts = [
        str(Path(sys.executable).parent),
        str(Path("~/.local/bin").expanduser()),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    return ":".join(dict.fromkeys(parts))


def unit_text(name: str, *, bind: str = "127.0.0.1", port: int = 8765, home: str | None = None) -> str:
    env = [f"PATH={_path_env()}"]
    if home:
        env.append(f"AGENTORC_HOME={home}")
    env_lines = "\n".join(f"Environment={e}" for e in env)
    if name == "agentorc-agent":
        return f"""[Unit]
Description=agentorc host agent (tmux sessions, hooks, run logs)

[Service]
Type=simple
ExecStart={_bin("agentorc-agent")} serve
Restart=on-failure
RestartSec=2
KillMode=process
{env_lines}

[Install]
WantedBy=default.target
"""
    if name == "agentorc-ui":
        return f"""[Unit]
Description=agentorc web UI
After=agentorc-agent.service
Wants=agentorc-agent.service

[Service]
Type=simple
ExecStart={_bin("agentorc-ui")} --bind {bind} --port {port}
Restart=on-failure
RestartSec=2
{env_lines}

[Install]
WantedBy=default.target
"""
    raise ValueError(name)


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(*, bind: str = "127.0.0.1", port: int = 8765, home: str | None = None, start: bool = True) -> list[str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name in UNITS:
        p = UNIT_DIR / f"{name}.service"
        p.write_text(unit_text(name, bind=bind, port=port, home=home or os.environ.get("AGENTORC_HOME")))
        written.append(str(p))
    _systemctl("daemon-reload")
    # Always enable (a reboot must not need a human, design §8); --no-start only defers the start.
    cp = _systemctl("enable", *(["--now"] if start else []), *[f"{u}.service" for u in UNITS])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
    return written


def uninstall() -> None:
    _systemctl("disable", "--now", *[f"{u}.service" for u in UNITS])
    for name in UNITS:
        (UNIT_DIR / f"{name}.service").unlink(missing_ok=True)
    _systemctl("daemon-reload")


def status() -> str:
    lines = []
    for name in UNITS:
        cp = _systemctl("is-active", f"{name}.service")
        lines.append(f"{name}: {cp.stdout.strip() or cp.stderr.strip()}")
    linger = subprocess.run(
        ["loginctl", "show-user", getpass.getuser(), "-p", "Linger"], capture_output=True, text=True
    )
    lines.append(linger.stdout.strip() or "Linger: unknown")
    return "\n".join(lines)
