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
from sessionorc.models import MailEntry, Session


def _atomic_write(path: Path, text: str, *, mode: int | None = None) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    if mode is None:
        tmp.write_text(text, encoding="utf-8")
    else:
        # A file whose contents are evidence is created with its mode, never chmod'ed after: a
        # world-readable window, however short, is a window (design §4.8a).
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)  # an existing tmp keeps its old mode through O_CREAT
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


class PersonInboxStore:
    """The person inbox's file (design §4.10): a list of entries, written whole on every change and
    reloaded on restart. A missing or unreadable file is an empty inbox, never a crash."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.person_inbox_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[MailEntry]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [MailEntry.from_dict(d) for d in raw.get("entries", [])]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []

    def save(self, entries: list[MailEntry]) -> None:
        _atomic_write(self.path, json.dumps({"entries": [e.to_dict() for e in entries]}, indent=1))


class AttentionStore:
    """The attention trail and the state-row snoozes (design §4.10 *The Inbox is a queue*, TD-079).
    One file holding two things that belong to the **home** rather than to any record: `trail`, the
    endings of the state rows the Inbox showed, newest first; and `snoozed`, a person's *not now*
    per record and row kind. Written whole on every change; a missing or unreadable file is an
    empty trail and no snoozes, never a crash — the same rule the person inbox follows."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.attention_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            trail = [d for d in raw.get("trail", []) if isinstance(d, dict) and d.get("id")]
            snoozed = {str(k): str(v) for k, v in (raw.get("snoozed") or {}).items() if v}
            return trail, snoozed
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return [], {}

    def save(self, trail: list[dict[str, Any]], snoozed: dict[str, str]) -> None:
        _atomic_write(self.path, json.dumps({"trail": trail, "snoozed": snoozed}, indent=1))


class IdentityAlarmStore:
    """The host's **own** identity alarms (design §4.8a, TD-077 step 2): the ones about no record —
    a claim from outside every pane, an unreadable peer — which have nowhere else to live, since a
    record's alarms ride that record's file. One small `0600` file beside `sessions/`, written
    whole. A missing or unreadable file is no alarms, never a crash; the agent's log has every one
    of them either way, so nothing is lost by a file that could not be read.

    `identity_tally` is deliberately *not* here: it counts connections **since the agent started**
    (§4.8a), and a number that outlived the process would say something else."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.identity_alarms_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [a for a in raw.get("alarms", []) if isinstance(a, dict)]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []

    def save(self, alarms: list[dict[str, Any]]) -> None:
        _atomic_write(self.path, json.dumps({"alarms": alarms}, indent=1), mode=0o600)


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
