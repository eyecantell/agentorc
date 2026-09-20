"""Home and node (design §4.4a, TD-057 step 2). One host agent holds the org's session graph — the
**home** — and every other one is a **node** that keeps what must touch its own machine and dials
home. An agent whose `hosts.yml` names no `home:`, or names itself, is the home, which is phase 1
exactly: nothing in this module runs for it.

What is here is the one decision a node makes alone: what it answers while it cannot reach its
home. Until step 3 built the link that was always, and this table was a node's whole behaviour;
since step 5 what it refuses is forwarded to the home while the link is up, and refused, naming the
home, while it is down. It is a pure function, read before the gate, so it can be tested without
an agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The home's, written at the home: a node's replica is overridden by the home's copy on reconnect,
# so an edit made here would be lost, and said to have worked.
HOME_EDITS = frozenset({"set_controllers", "set_grants", "set_stop", "set_mode"})
# The mailbox lives at the home (§4.4a: mail goes to one place). Reading it is refused with the
# writes: an empty inbox would say *no mail*, and the truth is *not known from here*.
MAILBOX = frozenset({"msg", "inbox", "inbox_delete"})
# Reports are home-owned, and a claim is a lease checked against every sibling (TD-056). `doing` is
# the third channel (§4.8, TD-074) and is home-owned like the other two, so it travels the same way:
# refused while the link is down, forwarded to the home while it is up.
REPORTS = frozenset({"progress", "finding", "doing"})
# What a session may do to itself offline, and a person to any session on this host: the node is
# the single tmux writer for its host, whether or not home can be reached.
NODE_ACTS = frozenset({"send", "keys", "kill", "close", "remove", "create", "seen", "decide", "hook"})
# What a person's request forwarded from a node may do only to that node's records (design §4.4a:
# "a person's request arriving over a link (no caller) may act only on that node's records"):
# every act and every home-owned edit, a create for another host, and a delete from an inbox — and
# the two reads a node does not serve itself: an `inbox` with an `id` (mail bodies of a home lead
# would otherwise be readable from any laptop, where its `get` is not) and a `wait`, which is
# scoped to the node's host. A person may still message anyone. Checked at the home, in `_forwarded`.
# `NODE_ACTS` is in the set as a second line only: a person's act on a pane never leaves the node.
PERSON_NODE_BOUND = (HOME_EDITS | NODE_ACTS | frozenset({"inbox", "inbox_delete"})) - frozenset({"hook"})
# Never served to a call forwarded from a node, whoever makes it (step 4b.3): a checkout's files on
# any host are read by a caller at the home, for a team start there — `_forwarded` refuses it.
HOME_ONLY = frozenset({"host_files"})


def offline_refusal(method: str, caller: Any, params: Mapping[str, Any], *, host: str, home: str) -> str | None:
    """Why a node that cannot reach `home` refuses this call, or None when it serves it (design
    §4.4a, the call-by-call table). `caller` is None for a person. Reads are never listed and never
    refused. Every refusal names the home and says nothing was queued: a refusal the caller can
    see, never a delivery that is not coming. With the link up the agent forwards what this
    refuses instead of asking (`HostAgent._forward`, step 5), so the table has one answer."""
    tail = f"{home} (home) is unreachable from {host}; refused, not queued (design §4.4a)"
    if method in MAILBOX:
        return f"the mailbox is at the home: {tail}"
    if method in REPORTS:
        return f"reports are written at the home, and a claim is checked against sessions this host cannot see: {tail}"
    if method in HOME_EDITS:
        return f"{method} edits what the home owns, so it waits for the link: {tail}"
    if caller is None or method not in NODE_ACTS:
        return None  # a person at this host acts on its sessions; a read is a read
    if method == "create":
        return f"a session creates nothing while its host is offline — a create is gated on the org's graph: {tail}"
    if params.get("id") == caller:
        return None  # a session acting on itself is this node's own business
    if method in ("seen", "decide", "hook"):
        return None  # not acts on another session in the gate's sense; the hook socket is the node's
    return f"{caller} cannot {method} {params.get('id', '?')}: acting on another session is gated at the home: {tail}"
