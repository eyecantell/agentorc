"""Thin, synchronous wrapper over the tmux CLI. The host agent is its only caller (design §9.1).

Every call takes the socket into account so tests can run against a private server (`-L`).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HISTORY_LIMIT = 50000
_FMT = "#{session_name}\t#{session_created}\t#{pane_current_command}\t#{pane_pid}\t#{pane_dead}\t#{pane_dead_status}"


class TmuxError(RuntimeError):
    pass


@dataclass
class PaneInfo:
    session: str
    created: int  # epoch seconds
    current_command: str
    pane_pid: int
    dead: bool
    dead_status: int | None


class Tmux:
    def __init__(self, socket_name: str | None = None, binary: str | None = None):
        self.socket_name = socket_name
        self.binary = binary or shutil.which("tmux") or "tmux"

    # -- plumbing -------------------------------------------------------------------------------

    def _argv(self, *args: str) -> list[str]:
        argv = [self.binary]
        if self.socket_name:
            argv += ["-L", self.socket_name]
        return argv + list(args)

    def run(self, *args: str, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess[str]:
        cp = subprocess.run(self._argv(*args), capture_output=True, text=True, input=input, timeout=15)
        if check and cp.returncode != 0:
            raise TmuxError(f"tmux {' '.join(args)}: {cp.stderr.strip() or cp.stdout.strip()}")
        return cp

    # -- server ----------------------------------------------------------------------------------

    def ensure_server(self) -> None:
        """Start the server if needed and keep it alive with no sessions (design §4.1, §4.6)."""
        # One client invocation: a freshly started server with no sessions would exit before a
        # second command could turn exit-empty off. `;` chains commands inside tmux.
        self.run(
            "start-server",
            ";",
            "set-option",
            "-s",
            "exit-empty",
            "off",
            ";",
            "set-option",
            "-g",
            "history-limit",
            str(HISTORY_LIMIT),
        )

    def kill_server(self) -> None:
        self.run("kill-server", check=False)

    # -- sessions --------------------------------------------------------------------------------

    def has_session(self, name: str) -> bool:
        return self.run("has-session", "-t", f"={name}", check=False).returncode == 0

    def new_session(self, name: str, cwd: Path | str, argv: list[str] | None, env: dict[str, str]) -> None:
        args = ["new-session", "-d", "-s", name, "-c", str(cwd)]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        if argv:
            args += ["--", *argv]
        # Chained in the same invocation so a command that exits at once still leaves a dead pane
        # (exit status readable, last lines in the log); the agent reaps it (design §6 exit reap).
        args += [";", "set-option", "-w", "-t", f"={name}:", "remain-on-exit", "on"]
        self.run(*args)

    def kill_session(self, name: str) -> None:
        self.run("kill-session", "-t", f"={name}", check=False)

    def list_panes(self, prefix: str = "ao-") -> list[PaneInfo]:
        cp = self.run("list-panes", "-a", "-F", _FMT, check=False)
        out: list[PaneInfo] = []
        if cp.returncode != 0:
            return out  # no server
        for line in cp.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 6 or not parts[0].startswith(prefix):
                continue
            name, created, cmd, pid, dead, dead_status = parts
            out.append(
                PaneInfo(
                    session=name,
                    created=int(created or 0),
                    current_command=cmd,
                    pane_pid=int(pid or 0),
                    dead=dead == "1",
                    dead_status=int(dead_status) if dead == "1" and dead_status.lstrip("-").isdigit() else None,
                )
            )
        return out

    # -- I/O -------------------------------------------------------------------------------------

    def pipe_pane(self, name: str, logfile: Path) -> None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        self.run("pipe-pane", "-t", f"={name}:", f"cat >> '{logfile}'")

    def capture_tail(self, name: str, lines: int = 8) -> list[str]:
        cp = self.run("capture-pane", "-p", "-t", f"={name}:", "-S", f"-{lines}", check=False)
        if cp.returncode != 0:
            return []
        rows = [r.rstrip() for r in cp.stdout.split("\n")]
        while rows and not rows[-1]:
            rows.pop()
        return rows[-lines:]

    def send_enter(self, name: str) -> None:
        self.run("send-keys", "-t", f"={name}:", "Enter")

    def send_literal(self, name: str, text: str) -> None:
        self.run("send-keys", "-t", f"={name}:", "-l", text)

    def paste(self, name: str, text: str) -> None:
        """Bracketed paste via a named buffer: multi-line text lands as one prompt (design §4.3)."""
        self.run("load-buffer", "-b", "ao-paste", "-", input=text)
        self.run("paste-buffer", "-p", "-d", "-b", "ao-paste", "-t", f"={name}:")

    def send_prompt(self, name: str, text: str) -> None:
        self.paste(name, text)
        self.send_enter(name)
