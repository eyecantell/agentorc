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


def launch_dir() -> Path:
    """Launch scripts for commands too long for tmux's own command line (`Tmux.new_session`)."""
    return home() / "launch"


def remote_dir(host: str) -> Path:
    """Where the home keeps another host's records (design §4.4a "A node's records at the home"):
    apart from its own `sessions/`, one directory per node, so two hosts may hold one id."""
    return home() / "remote" / host


def person_inbox_file() -> Path:
    """The org's person inbox (design §4.10 "A session reaches a person"): one per org, held by the
    home host agent and belonging to no session record, so it is its own file beside `sessions/`."""
    return home() / "person_inbox.json"


def identity_alarms_file() -> Path:
    """The host agent's **own** identity alarms (design §4.8a, TD-077 step 2): the ones about no
    record, which no record's file can hold. Mode `0600`, beside `person_inbox.json`."""
    return home() / "identity_alarms.json"


def attention_file() -> Path:
    """The home's **attention trail** and the state rows' snoozes (design §4.10 *The Inbox is a
    queue*, TD-079): a state row is a view of a record and leaves no entry behind, so what became
    of it — and a person's *not now* on it — have nowhere else to live. Beside
    `person_inbox.json`, one file, written whole."""
    return home() / "attention.json"


def usage_file() -> Path:
    """The last good usage reading per profile (TD-087). It survives a restart on purpose: the
    readings lived in memory, so each promote forgot them and polled at once — eight promotes in
    one day, against an endpoint that answers 429."""
    return home() / "usage.json"


def repos_file() -> Path:
    """The home's repo facts (design §4.4 *Repo facts*, TD-176): the last reading of each registered
    checkout's PRs and ledger, kept across a restart so the page has numbers before the first read,
    and an outage keeps saying *could not look* with the last reading beside it."""
    return home() / "repos.json"


def socket_path() -> Path:
    return home() / "agent.sock"


def links_dir() -> Path:
    """The home's per-node link sockets (design §4.4a "A container node", TD-057 step 3c): one
    directory per `nodes:` entry, `links/<name>/link.sock`. A container node gets the *directory*
    bind-mounted, never the file — the home unlinks and re-binds its sockets on every start, and a
    mounted file would keep the dead inode."""
    return home() / "links"


def link_socket(name: str) -> Path:
    return links_dir() / name / "link.sock"


def waits_dir() -> Path:
    """One cursor file per waiter (`ao wait`, design §4.8 "Waking a manager", TD-049): what that
    caller had already seen when it last looked, so an event that arrives while it is busy is
    still there when it comes back."""
    return home() / "waits"


def backups_dir() -> Path:
    """The home's nightly tarballs of its store (design §4.4a "When the home is lost", TD-057 step
    4b.3): `store-<date>.tar.gz`, the newest seven kept."""
    return home() / "backups"


def recent_dirs_file() -> Path:
    return home() / "recent_dirs"


def ensure_layout() -> None:
    for d in (sessions_dir(), events_dir(), runs_dir(), attachments_dir(), waits_dir()):
        d.mkdir(parents=True, exist_ok=True)
