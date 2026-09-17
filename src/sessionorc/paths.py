"""Where the host agent keeps its state. One root, overridable for tests and throwaway installs."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("AGENTORC_HOME", "~/.agentorc")).expanduser()


def sessions_dir() -> Path:
    return home() / "sessions"


def events_dir() -> Path:
    return home() / "events"


def runs_dir() -> Path:
    return home() / "runs"


def attachments_dir() -> Path:
    return home() / "attachments"


def remote_dir(host: str) -> Path:
    """Where the home keeps another host's records (design §4.4a "A node's records at the home"):
    apart from its own `sessions/`, one directory per node, so two hosts may hold one id."""
    return home() / "remote" / host


def person_inbox_file() -> Path:
    """The org's person inbox (design §4.10 "A session reaches a person"): one per org, held by the
    home host agent and belonging to no session record, so it is its own file beside `sessions/`."""
    return home() / "person_inbox.json"


def socket_path() -> Path:
    return home() / "agent.sock"


def waits_dir() -> Path:
    """One cursor file per waiter (`ao wait`, design §4.8 "Waking a lead", TD-049): what that
    caller had already seen when it last looked, so an event that arrives while it is busy is
    still there when it comes back."""
    return home() / "waits"


def recent_dirs_file() -> Path:
    return home() / "recent_dirs"


def ensure_layout() -> None:
    for d in (sessions_dir(), events_dir(), runs_dir(), attachments_dir(), waits_dir()):
        d.mkdir(parents=True, exist_ok=True)
