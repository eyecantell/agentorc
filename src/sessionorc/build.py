"""Which commit this install was built from, and how far `main` has moved past it (design §4.4
*Version skew is survivable*, TD-062 (c)).

The live install is promoted by a person (CLAUDE.md), so a merge reaches nothing that is running
until the next promote — which is the point, and also a thing nobody could see: a wheel was a flat
`0.0.1` whatever it held. The build hook (`pdm_build.py`) now writes `sessionorc/_build.json` into
every wheel built from a git checkout; the host agent reports it on `host`, and `ao status -v` and
`ao service status` say when the checkout it came from has moved on."""

from __future__ import annotations

import json
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

# What "moved on" is measured against: what is merged, as the checkout last fetched it. The promote
# rule is *after a merge that should be live, with `main` clean and current* (CLAUDE.md).
REF = "origin/main"


def info() -> dict[str, Any]:
    """The build record, or {} when there is none — an editable install (it runs the checkout
    itself), a wheel built outside a git checkout, or a file this build cannot read."""
    try:
        raw = resources.files("sessionorc").joinpath("_build.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("commit"), str) or not data["commit"]:
        return {}
    return data


def ahead(build: dict[str, Any], ref: str = REF) -> dict[str, Any]:
    """How many commits `ref` in the build's source checkout holds that the build does not:
    `{"ref", "ahead"}`, or `{"ref", "why"}` when it cannot be said (no build record, the source is
    not on this host, the commit or the ref is not in it). Read here, on the caller's host, because
    the checkout is a directory and not something the agent holds."""
    commit = build.get("commit") if isinstance(build, dict) else None
    source = build.get("source") if isinstance(build, dict) else None
    if not commit or not isinstance(source, str):
        return {"ref": ref, "why": "the running build does not say which commit it came from"}
    if not Path(source).is_dir():
        return {"ref": ref, "why": f"its source {source} is not on this host"}
    try:
        cp = subprocess.run(
            ["git", "-C", source, "rev-list", "--count", f"{commit}..{ref}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"ref": ref, "why": f"git: {e}"}
    if cp.returncode != 0 or not cp.stdout.strip().isdigit():
        return {"ref": ref, "why": f"{commit[:12]} or {ref} is not in {source}"}
    return {"ref": ref, "ahead": int(cp.stdout.strip())}


def line(build: dict[str, Any], started_at: str = "", a: dict[str, Any] | None = None) -> str:
    """One line for a person: what is running, and whether `main` has moved past it. An agent too
    old to report a build says so, rather than nothing, since *unknown* is the case this exists
    for. `a` is an `ahead` the caller already holds (the Org's chip passes the home's promote
    reading, so the chip and the Inbox's Promote row cannot disagree); measured here when absent."""
    if not build or not build.get("commit"):
        return "host agent: build unknown — it predates build reporting, or runs from an editable install"
    commit = str(build["commit"])[:12]
    dirty = " (with uncommitted changes)" if build.get("dirty") else ""
    started = f", started {started_at}" if started_at else ""
    a = a if a is not None else ahead(build)
    if "ahead" not in a:
        tail = f" — cannot compare with {a['ref']}: {a['why']}"
    elif a["ahead"]:
        n = a["ahead"]
        tail = f" — {a['ref']} is {n} commit{'' if n == 1 else 's'} ahead of it, not live until the next promote"
    else:
        tail = f" — current with {a['ref']}"
    return f"host agent: built from {commit}{dirty} at {build.get('built_at') or '?'}{started}{tail}"


def chip(build: dict[str, Any], started_at: str = "", a: dict[str, Any] | None = None) -> dict[str, str] | None:
    """The Org top bar's **build** chip (design §4.5a, TD-132 slice 5): None when the running build
    is `main`'s head — nothing shown in the common case, as the unread chip — otherwise
    `{"text", "title", "cls"}`: the same facts as `line`, short enough for the bar, with `line`
    itself on hover. `cls` is `behind` when main is ahead of it, `unknown` when that cannot be said."""
    if not build or not build.get("commit"):
        return {"text": "build unknown", "title": line(build, started_at), "cls": "unknown"}
    a = a if a is not None else ahead(build)
    live = str(build["commit"])[:7]
    if "ahead" not in a:
        return {"text": f"live {live} · main unknown", "title": line(build, started_at, a), "cls": "unknown"}
    n = a["ahead"]
    if not n:
        return None
    return {
        "text": f"live {live} · main {n} commit{'' if n == 1 else 's'} ahead",
        "title": line(build, started_at, a),
        "cls": "behind",
    }
