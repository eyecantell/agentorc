"""`ao service install|uninstall|status`: systemd user units for the host agent and the UI
(design §4.1, §8 "a reboot must not need a human").

Two units, `agentorc-agent` and `agentorc-ui`, under `~/.config/systemd/user/`. `KillMode=process`
on the agent is load-bearing: tmux daemonises inside the service's cgroup, and the default
`control-group` kill mode would take the tmux server — and every session in it — down with any
agent restart or stop. With `process`, only the agent's own process is signalled; the tmux server
is deliberately left running when the unit stops.

A third unit, `agentorc-tmux`, is a **system** unit (design §4.1, TD-488): the tmux server under
`system.slice`, outside the user manager's subtree, so a stop of `systemd --user` takes the agent
and the UI down and no session. It is root's to write: `install` stages its text under the home and
prints the one command the person runs as root, `ao service install --system`, which copies it in.
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
TMUX_UNIT = "agentorc-tmux"  # the system unit (design §4.1, TD-495)
SYSTEM_DIR = Path("/etc/systemd/system")
# Where the UI listens (design §4.5: localhost, never the LAN) — the one source `ao ui`, `agentorc-ui`
# and `ao service install` default to (TD-149 (7)); the unit carries whatever the install was given.
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765


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


def unit_text(name: str, *, bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT, home: str | None = None) -> str:
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


def tmux_unit_text(user: str, tmux: str | None = None) -> str:
    """The tmux server's system unit (design §4.1): `tmux -D` in the foreground on the default socket
    (`-D` is no daemon and turns `exit-empty` off), as the person, restarted always; its own stop is
    the one deliberate way to end every session at once (`KillMode=control-group`); `Delegate=yes`
    lets the host agent give each pane a cgroup of its own (§4.8a). No `XDG_RUNTIME_DIR` and no
    `DBUS_SESSION_BUS_ADDRESS`, so tmux opens no scope under the user manager."""
    return f"""[Unit]
Description=agentorc tmux server (every session's panes, outside the user manager)

[Service]
Type=simple
User={user}
ExecStart={tmux or shutil.which("tmux") or "/usr/bin/tmux"} -D
Restart=always
RestartSec=2
KillMode=control-group
Delegate=yes
Environment=PATH={_path_env()}

[Install]
WantedBy=multi-user.target
"""


def _staged() -> Path:
    from sessionorc import paths

    return paths.home() / "systemd" / f"{TMUX_UNIT}.service"


def system_line() -> str:
    """The one command the person runs as root, by its absolute path: root's PATH has no venv."""
    return f"sudo {_bin('ao')} service install --system"


def stage_tmux_unit(user: str | None = None) -> Path | None:
    """`ao service install`'s third step (design §4.1, TD-495): when the installed system unit is
    absent or differs from the text this install would write, the text is written under the home
    for `install --system` to copy in, and its path returned; None when the installed one is current."""
    text = tmux_unit_text(user or getpass.getuser())
    try:
        if (SYSTEM_DIR / f"{TMUX_UNIT}.service").read_text() == text:
            return None
    except OSError:
        pass
    staged = _staged()
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text)
    return staged


def install_system() -> str:
    """`ao service install --system`, as root: the staged unit copied into `/etc/systemd/system`,
    then `daemon-reload` and `enable --now`. Refused for anyone else, naming the line. The staged
    file is read from the person's home: `sudo` keeps `SUDO_USER`, not `AGENTORC_HOME`."""
    if os.geteuid() != 0:
        raise PermissionError(f"the tmux system unit is root's to install: run `{system_line()}`")
    staged = _staged()
    if not os.environ.get("AGENTORC_HOME") and (person := os.environ.get("SUDO_USER")):
        import pwd

        staged = Path(pwd.getpwnam(person).pw_dir) / ".agentorc" / "systemd" / f"{TMUX_UNIT}.service"
    if not staged.is_file():
        raise FileNotFoundError(f"nothing staged at {staged}: run `ao service install` as yourself first")
    target = SYSTEM_DIR / f"{TMUX_UNIT}.service"
    shutil.copyfile(staged, target)
    target.chmod(0o644)
    for args in (("daemon-reload",), ("enable", "--now", f"{TMUX_UNIT}.service")):
        cp = subprocess.run(["systemctl", *args], capture_output=True, text=True)
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
    return str(target)


def tmux_placement() -> str:
    """Where the running tmux server is (design §4.1): its system unit, under the user manager (a
    warning: one stop of `systemd --user` ends every session), elsewhere, or no server."""
    from sessionorc import identity
    from sessionorc.tmux import Tmux

    try:
        pid = Tmux().server_pid()
    except Exception:  # noqa: BLE001 — no tmux at all reads as no server
        pid = None
    if not pid:
        return "tmux: no server"
    cgroup = identity.LinuxProc().cgroup(pid)
    runs = identity.server_placement(cgroup)
    if runs == "system":
        return f"ok: tmux — server {pid}, system unit {TMUX_UNIT}.service"
    if runs == "user":
        # §4.7's words for the doctor's warning
        return (
            f"warning: tmux — server {pid} under the user manager ({cgroup}): a stop of `systemd --user` "
            "ends every session; `ao service install` prints the system unit"
        )
    return f"tmux: server {pid}, not under systemd ({cgroup or 'cgroup unknown'})"


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(
    *, bind: str = DEFAULT_BIND, port: int = DEFAULT_PORT, home: str | None = None, start: bool = True
) -> list[str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name in UNITS:
        p = UNIT_DIR / f"{name}.service"
        p.write_text(unit_text(name, bind=bind, port=port, home=home or os.environ.get("AGENTORC_HOME")))
        written.append(str(p))
    # The wheel of what was just installed — what a container node is provisioned from (design
    # §4.4a "A container node", TD-057 step 3c.2) — is written **before** the units restart: the
    # restarted agent takes its nodes' `hello`s at once and names the build it would provision
    # from the newest wheel, so a wheel written afterwards left a window in which a node could be
    # re-provisioned from the previous build (seen live at the promote of PR #225, 2026-09-18).
    # Never fatal to the promote, whatever it raises (a disk that refuses the directory, a `pip`
    # that cannot start): first in line, it must not be what stops the units coming up on the new
    # code (review of PR #227). A node then stays on the wheel the home already had.
    from sessionorc import containers

    try:
        wheel = containers.write_wheel()
    except Exception as e:  # noqa: BLE001
        print(f"agentorc: the wheel could not be written ({type(e).__name__}: {e}); units go ahead", file=sys.stderr)
        wheel = None
    if wheel is not None:
        written.append(str(wheel))
    # At the home the directory becomes a work tree that tracks its three definition files (design
    # §4.9 *What is left at the home has a history*, TD-229); never on a node, never fatal to the units.
    try:
        tree = _home_history(home)
    except Exception as e:  # noqa: BLE001
        print(f"agentorc: the home's history could not be set up ({type(e).__name__}: {e})", file=sys.stderr)
        tree = None
    if tree is not None:
        print(f"agentorc: {tree} now keeps the history of org.yml, profiles.yml and settings.yml")
    _systemctl("daemon-reload")
    # Always enable (a reboot must not need a human, design §8); --no-start only defers the start.
    cp = _systemctl("enable", *(["--now"] if start else []), *[f"{u}.service" for u in UNITS])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
    if start:
        # `enable --now` leaves an already-running unit on its old ExecStart; a re-install that
        # re-points the units (dev venv → stable install) must restart to take effect. Safe: the
        # agent unit never takes tmux down (KillMode=process); the UI's terminals reconnect.
        _systemctl("restart", *[f"{u}.service" for u in UNITS])
    return written


def _home_history(home: str | None) -> str | None:
    """`~/.agentorc` made a git work tree tracking `org.yml`, `profiles.yml` and `settings.yml`, when
    this host is the home and it is not one yet: its `.git`, or None."""
    from sessionorc import defs, hosts, paths

    if hosts.home_name() != hosts.local_host().name:
        return None  # a node: its settings are the home's replica, and nothing of it is tracked
    root = Path(home).expanduser() if home else paths.home()
    return str(root / ".git") if defs.init(root) else None


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
    lines.append(tmux_placement())
    return "\n".join(lines)
