"""Thin, synchronous wrapper over the tmux CLI. The host agent is its only caller (design §9.1).

Every call takes the socket into account so tests can run against a private server (`-L`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

HISTORY_LIMIT = 50000
_FMT = (
    "#{session_name}\t#{session_created}\t#{pane_current_command}\t#{pane_pid}\t#{pane_dead}\t#{pane_dead_status}"
    "\t#{window_index}\t#{pane_index}"
)
MIN_VERSION = (3, 2)  # `new-session -e` and `paste-buffer -p`


class TmuxError(RuntimeError):
    pass


class DuplicateSession(TmuxError):
    pass


@dataclass
class PaneInfo:
    session: str
    created: int  # epoch seconds
    current_command: str
    pane_pid: int
    dead: bool
    dead_status: int | None
    window: int = 0
    pane: int = 0


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

    def version(self) -> tuple[int, int]:
        out = self.run("-V", check=False).stdout.strip()  # "tmux 3.5a"
        m = re.search(r"(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    def ensure_server(self) -> None:
        """Start the server if needed and keep it alive with no sessions (design §4.1, §4.6)."""
        v = self.version()
        if v < MIN_VERSION:
            raise TmuxError(f"tmux {v[0]}.{v[1]} is too old; need {MIN_VERSION[0]}.{MIN_VERSION[1]}+")
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

    def new_session(
        self, name: str, cwd: Path | str, argv: list[str] | None, env: dict[str, str], logfile: Path | None = None
    ) -> None:
        """Create a detached session. `remain-on-exit` and the run-log `pipe-pane` are chained into
        the same tmux invocation: a command that exits at once still leaves a dead pane (exit status
        readable) and the log has its first byte (design §9 invariant 3) — a second invocation
        would find the pane already gone."""
        args = ["new-session", "-d", "-s", name, "-c", str(cwd)]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        if argv:
            args += ["--", *argv]
        args += [";", "set-option", "-w", "-t", f"={name}:", "remain-on-exit", "on"]
        if logfile is not None:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            args += [";", "pipe-pane", "-t", f"={name}:", f"cat >> '{logfile}'"]
        cp = self.run(*args, check=False)
        if cp.returncode != 0:
            err = cp.stderr.strip() or cp.stdout.strip()
            if "duplicate session" in err:
                raise DuplicateSession(err)
            raise TmuxError(f"tmux new-session {name}: {err}")

    def kill_session(self, name: str) -> None:
        self.run("kill-session", "-t", f"={name}", check=False)

    def list_panes(self, prefix: str = "ao-") -> list[PaneInfo]:
        cp = self.run("list-panes", "-a", "-F", _FMT, check=False)
        out: list[PaneInfo] = []
        if cp.returncode != 0:
            return out  # no server
        for line in cp.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 8 or not parts[0].startswith(prefix):
                continue
            name, created, cmd, pid, dead, dead_status, win, pane = parts
            out.append(
                PaneInfo(
                    session=name,
                    created=int(created or 0),
                    current_command=cmd,
                    pane_pid=int(pid or 0),
                    dead=dead == "1",
                    dead_status=int(dead_status) if dead == "1" and dead_status.lstrip("-").isdigit() else None,
                    window=int(win or 0),
                    pane=int(pane or 0),
                )
            )
        return out

    def main_panes(self, prefix: str = "ao-") -> dict[str, PaneInfo]:
        """One pane per session — the lowest window/pane index, deterministically."""
        best: dict[str, PaneInfo] = {}
        for p in self.list_panes(prefix):
            cur = best.get(p.session)
            if cur is None or (p.window, p.pane) < (cur.window, cur.pane):
                best[p.session] = p
        return best

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
