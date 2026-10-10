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
from datetime import datetime
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


PENDING_SHOWN = 10  # commits of main past live listed on the chip's hover; the rest are a count (§4.5a)


def _git(source: str, *args: str) -> str | None:
    try:
        cp = subprocess.run(["git", "-C", source, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def commit_at(source: Any, commit: Any) -> tuple[str, str]:
    """`(committer time ISO, subject)` of `commit` in the checkout `source`, `("", "")` when it
    cannot be read — the chip's *live <time>* and the hover's first line (§4.5a, TD-539)."""
    if not commit or not isinstance(source, str) or not Path(source).is_dir():
        return "", ""
    out = _git(source, "log", "-1", "--format=%cI%x09%s", str(commit), "--")
    if not out or "\t" not in out:
        return "", ""
    at, subject = out.strip("\n").split("\t", 1)
    return at, subject


def pending(source: Any, live: Any, main: Any) -> tuple[list[dict[str, str]], int] | None:
    """The commits of `main` past `live`, newest first: `([{sha, subject}], more)` — the first
    `PENDING_SHOWN` and how many more — or None when it cannot be read (§6 *Promote*, TD-539)."""
    if not live or not main or not isinstance(source, str) or not Path(source).is_dir():
        return None
    out = _git(source, "log", "--format=%H%x09%s", f"{live}..{main}", "--")
    if out is None:
        return None
    rows = [ln.split("\t", 1) for ln in out.splitlines() if "\t" in ln]
    return [{"sha": s, "subject": t} for s, t in rows[:PENDING_SHOWN]], max(0, len(rows) - PENDING_SHOWN)


def stamp(at: str, now: datetime | None = None) -> str:
    """*10-10 15:41*: an ISO time in this process's zone, the year in front only when it is not
    this one — the server's print of the chip's time, which the page reprints in the browser's."""
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now().astimezone()
    return t.strftime("%m-%d %H:%M" if t.year == now.year else "%Y-%m-%d %H:%M")


def chip(build: dict[str, Any], started_at: str = "", a: dict[str, Any] | None = None, **more: Any) -> dict[str, str]:
    """The top bar's **build** chip (design §4.5a, TD-539), always drawn: `{"text", "title", "cls",
    "at", "rest"}`. *live <MM-DD HH:MM>* — the live commit's committer time, `at` the ISO the page
    reprints in the browser's zone, `rest` what follows it — then *· promoting…* (`inflight`),
    *· held* (`held`), *· main unknown* (`a` cannot count), *· main +n* (`a`'s `ahead`). `cls` is
    `inflight`, `held`, `unknown`, `behind` or `live`; *build unknown* without a record. On hover
    the live commit's sha and subject, `line`'s sentence, then *not live yet:* and the pending
    commits. `more` carries the home's reading where it holds this build (`live_at`, `pending`,
    `pending_more`, `inflight`, `held`); without one (`a` None) main is measured here, against this
    checkout's `REF`, and the hover says so. `page` is the page's own build, named where it differs,
    and `nodes` `(name, build)` for each linked node marked stale."""
    if not build or not build.get("commit"):
        return {"text": "build unknown", "title": line(build, started_at), "cls": "unknown", "at": "", "rest": ""}
    commit, source = str(build["commit"]), build.get("source")
    here = a is None
    a = a if a is not None else ahead(build)
    at, subject = commit_at(source, commit)
    at = str(more.get("live_at") or at)
    rows, extra = more.get("pending"), int(more.get("pending_more") or 0)
    if here and a.get("ahead"):
        rows, extra = pending(source, commit, REF) or (None, 0)
    if more.get("inflight"):
        rest, cls = " · promoting…", "inflight"
    elif more.get("held"):
        rest, cls = " · held", "held"
    elif "ahead" not in a:
        rest, cls = " · main unknown", "unknown"
    elif a["ahead"]:
        rest, cls = f" · main +{a['ahead']}", "behind"
    else:
        rest, cls = "", "live"
    when = stamp(at) if at else ""
    title = [f"{commit[:7]} {subject}".rstrip(), line(build, started_at, a)]
    if rows:
        title += ["", "not live yet:"] + [f"{r.get('sha', '')[:7]} {r.get('subject', '')}" for r in rows]
        if extra:
            title.append(f"… and {extra} more")
    if here:
        title += ["", f"measured from this checkout's {REF}: the home's promote reading does not hold it"]
    page = more.get("page") or {}
    if page.get("commit") and page["commit"] != commit:
        p_at, _ = commit_at(page.get("source"), page["commit"])
        title += ["", f"this page: {str(page['commit'])[:7]} {stamp(p_at) if p_at else page.get('built_at') or '?'}"]
    for name, b in more.get("nodes") or ():
        title.append(f"{name}: {str(b or 'unknown')[:7]}, stale")
    return {
        "text": f"live {when or commit[:7]}{rest}",
        "title": "\n".join(title),
        "cls": cls,
        "at": at if when else "",
        "rest": rest,
    }
