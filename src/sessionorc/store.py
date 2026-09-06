"""On-disk session records and the hook event queue under `~/.agentorc/`.

The host agent is the only writer of `sessions/*.json`. Hook scripts talk to the agent's socket;
when the agent is down they append to `events/<session>.jsonl`, which the agent drains on its
next tick, so a hook never blocks on the agent and no state transition is lost.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sessionorc import paths
from sessionorc.models import Session


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class SessionStore:
    def __init__(self, root: Path | None = None):
        self.root = root or paths.sessions_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def load(self, session_id: str) -> Session | None:
        p = self.path(session_id)
        if not p.is_file():
            return None
        return Session.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def load_all(self) -> dict[str, Session]:
        out: dict[str, Session] = {}
        for p in sorted(self.root.glob("*.json")):
            try:
                s = Session.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # a half-written or foreign file never takes the agent down
            out[s.id] = s
        return out

    def save(self, session: Session) -> None:
        _atomic_write(self.path(session.id), json.dumps(session.to_dict(), indent=1))

    def delete(self, session_id: str) -> None:
        self.path(session_id).unlink(missing_ok=True)


class EventQueue:
    """Append-only per-session JSONL, drained by the agent."""

    def __init__(self, root: Path | None = None):
        self.root = root or paths.events_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, session_id: str, event: dict[str, Any]) -> None:
        with (self.root / f"{session_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def drain(self) -> list[tuple[str, dict[str, Any]]]:
        """Return and remove every queued event, oldest first per session."""
        out: list[tuple[str, dict[str, Any]]] = []
        for p in sorted(self.root.glob("*.jsonl")):
            sid = p.stem
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                p.unlink()
            except OSError:
                continue
            for line in lines:
                try:
                    out.append((sid, json.loads(line)))
                except ValueError:
                    continue
        return out
