"""The promote's readings and its intent files (design §6 *Promote*, §5 `promote:`, TD-132 slice 1).

A repo's live copy is made from `main` by a person's press or the home's policy, never by a
session. This module is the part that runs in a thread and returns plain data: the repo's
`promote:` block (the one key of `.agentorc.yml` the host agent reads, by key alone), the three
readings — **live** from `check`, **main** after the home's own `git fetch origin main`, **checks**
from `gh` — the three preconditions as one function, and the run itself, detached, with its intent
file written before it starts. The agent's `PromoteMixin` decides when; nothing here keeps state
beyond the files under `~/.agentorc/promotes/<repo>/`.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sessionorc import paths

PROMOTE_EVERY = 300.0  # seconds between full readings: the reports' cadence (§6)
PROMOTE_WATCH = 15.0  # seconds between `check` reads while a run is in flight
PROMOTE_SETTLE = 600.0  # main must stand still this long before `auto` promotes (§6)
PROMOTE_BOUND = 1200.0  # a run past this is killed and failed (§6)
CHECK_TIMEOUT = 60.0
GIT_TIMEOUT = 60.0
GH_TIMEOUT = 30.0
KEYS = ("run", "check")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# a check run's conclusions that are not a pass; `neutral` and `skipped` are
# this agent's own runs by pid: held so `subprocess` never reaps one behind `alive`'s back (a
# dropped Popen is reaped by the next `subprocess.run` and its status lost), polled for `exit`
_procs: dict[int, subprocess.Popen[bytes]] = {}
_exits: dict[int, int] = {}
FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


def block(root: str | Path) -> dict[str, str] | None:
    """The checkout's `promote: {run, check}`, or None when its `.agentorc.yml` has no such key.
    Raises ValueError, saying why, when the file or the block cannot be used — the caller logs it
    and skips the repo; it is never fatal to the tick. The rest of the file is the clients'."""
    f = Path(root) / ".agentorc.yml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"{f} cannot be read: {type(e).__name__}") from None
    if not isinstance(doc, dict) or doc.get("promote") is None:
        return None
    value = doc["promote"]
    if not isinstance(value, dict):
        raise ValueError(f"{f}: promote must be a mapping (run, check)")
    out = {k: value.get(k).strip() for k in KEYS if isinstance(value.get(k), str) and value.get(k).strip()}
    missing = [k for k in KEYS if k not in out]
    if missing:
        raise ValueError(f"{f}: promote needs {' and '.join(missing)} (design §5)")
    return out


def _git(root: str | Path, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[str | None, str]:
    """`(stdout stripped, "")` or `(None, why)`."""
    try:
        cp = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git {args[0]}: {type(e).__name__}"
    if cp.returncode != 0:
        return None, f"git {args[0]}: " + ((cp.stderr or "").strip().splitlines() or ["failed"])[-1][:200]
    return cp.stdout.strip(), ""


def read_live(root: str | Path, check: str) -> tuple[str | None, str]:
    """`check` run in the checkout: one full commit on stdout and exit 0 → `(sha, "")`; anything
    else → `(None, why)`, the reason its stderr gives (§5: *live: unknown — <reason>*)."""
    try:
        cp = subprocess.run(
            check, shell=True, cwd=str(root), capture_output=True, text=True, timeout=CHECK_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"check could not run: {type(e).__name__}"
    if cp.returncode != 0:
        return None, ((cp.stderr or "").strip().splitlines() or [f"check exited {cp.returncode}"])[-1][:200]
    sha = (cp.stdout or "").strip().lower()
    if not SHA_RE.match(sha):
        return None, "check did not print one full commit"
    return sha, ""


def read_main(root: str | Path) -> dict[str, Any]:
    """After the home's own `git fetch origin main` (a policy that acts is not hostage to whoever
    last fetched): `main` (origin/main's head), `moved` (its committer time — when main last moved,
    for the settle and the row's age; a squash merge on GitHub stamps it), and `tree`, None when
    the checkout is on main's head with a clean tree (precondition 1) or the text of why not. A
    failed fetch is `fetch_why` beside the last-fetched main, never a missing one. Reads only:
    no checkout, no reset."""
    out: dict[str, Any] = {}
    _, why = _git(root, "fetch", "-q", "origin", "main")
    if why:
        out["fetch_why"] = why
    line, why = _git(root, "log", "-1", "--format=%H %cI", "origin/main")
    if line is None or " " not in line:
        return {**out, "main": None, "main_why": why or "origin/main has no commit"}
    out["main"], out["moved"] = line.split(" ", 1)
    head, why = _git(root, "rev-parse", "HEAD")
    status, why2 = _git(root, "status", "--porcelain")
    if head is None or status is None:
        out["tree"] = f"the checkout cannot be read: {why or why2}"
    elif head != out["main"]:
        branch, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        out["tree"] = f"the checkout is on {branch or head[:7]} at {head[:7]}, not main's head {out['main'][:7]}"
    elif status:
        n = len(status.splitlines())
        out["tree"] = f"the checkout has {n} uncommitted change{'' if n == 1 else 's'}"
    else:
        out["tree"] = None
    return out


def ahead(root: str | Path, live: str | None, main: str | None) -> int | None:
    """How many commits main holds that live does not; None when it cannot be said."""
    if not live or not main:
        return None
    n, _ = _git(root, "rev-list", "--count", f"{live}..{main}")
    return int(n) if n and n.isdigit() else None


def read_checks(root: str | Path, sha: str) -> tuple[str, str]:
    """The CI verdict on `sha`: `green` when every check run has concluded and none failed,
    `pending`, `failed`, or `unknown` with why (no `gh`, no remote, a rate limit, no check runs at
    all — a commit CI has not looked at is not a green one)."""
    try:
        cp = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100"],
            capture_output=True, text=True, timeout=GH_TIMEOUT, cwd=str(root),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return "unknown", f"gh could not be asked: {type(e).__name__}"
    if cp.returncode != 0:
        return "unknown", ((cp.stderr or "").strip().splitlines() or ["gh api failed"])[-1][:200]
    try:
        runs = json.loads(cp.stdout or "{}").get("check_runs")
    except (json.JSONDecodeError, AttributeError):
        return "unknown", "gh api printed something that is not the check runs"
    if not isinstance(runs, list):
        return "unknown", "gh api printed something that is not the check runs"
    if not runs:
        return "unknown", f"no check runs on {sha[:7]}"
    runs = [r for r in runs if isinstance(r, dict)]
    if any(r.get("status") == "completed" and r.get("conclusion") in FAILED for r in runs):
        return "failed", ""
    if any(r.get("status") != "completed" for r in runs):
        return "pending", ""
    return "green", ""


def unmet(reading: dict[str, Any], press: bool = False) -> tuple[str, str] | None:
    """The first of §6's three preconditions the reading does not meet, as `(name, text)`, or None.
    (1) `tree` — the checkout on main's head with a clean tree; (2) `checks` — green, which a
    person's press goes through (`press=True` skips it; the reply says what the checks read);
    (3) `inflight` / `failed` — nothing in flight and no failure standing for the repo."""
    if not reading.get("main"):
        return "main", f"main cannot be read: {reading.get('main_why') or 'unknown'}"
    if "tree" not in reading:
        return "tree", "the checkout has not been read yet"
    if reading["tree"]:
        return "tree", str(reading["tree"])
    if not press and reading.get("checks") != "green":
        why = reading.get("checks_why")
        return "checks", f"checks on main are {reading.get('checks') or 'unknown'}" + (f": {why}" if why else "")
    if reading.get("inflight"):
        return "inflight", f"a promote of {str(reading['inflight'].get('sha', ''))[:7]} is in flight"
    if reading.get("failed"):
        return "failed", f"the promote of {str(reading['failed'].get('sha', ''))[:7]} failed and is not cleared"
    return None


# -- the intent files (§6: `~/.agentorc/promotes/<repo>/`) ---------------------------------------


def repo_dir(repo: str) -> Path:
    return paths.home() / "promotes" / repo


def _read(f: Path) -> dict[str, Any] | None:
    try:
        v = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _write(f: Path, value: dict[str, Any]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1), encoding="utf-8")
    tmp.replace(f)


def inflight(repo: str) -> dict[str, Any] | None:
    return _read(repo_dir(repo) / "inflight.json")


def failed(repo: str) -> dict[str, Any] | None:
    return _read(repo_dir(repo) / "failed.json")


def clear(repo: str, which: str) -> None:
    with suppress(FileNotFoundError):
        (repo_dir(repo) / f"{which}.json").unlink()


def start(root: str | Path, repo: str, sha: str, run: str, by: str, n: int | None) -> dict[str, Any]:
    """Start `run` in the checkout, detached from the agent's process group (for this repo the run
    restarts the agent that started it), output to `<sha>.log`. The intent file is written
    **before** the start and gains the pid after, so an agent that dies in between still finds it;
    `n` is how many commits it makes live, for the note. Returns the intent."""
    d = repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    log_path = d / f"{sha}.log"
    intent: dict[str, Any] = {"sha": sha, "at": datetime.now(UTC).isoformat(), "pid": None, "log": str(log_path)}
    intent |= {"by": by, "ahead": n}
    _write(d / "inflight.json", intent)
    with open(log_path, "ab") as out:
        p = subprocess.Popen(
            run, shell=True, cwd=str(root), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )  # fmt: skip
    intent["pid"], intent["started"] = p.pid, proc_start(p.pid)
    _procs[p.pid] = p
    _write(d / "inflight.json", intent)
    return intent


def proc_start(pid: int) -> int | None:
    """The process's start time in clock ticks since boot (`/proc/<pid>/stat` field 22), which with
    the pid names one process: a pid read back from a file after a restart of the home may since
    have been taken by another. None where it cannot be read."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def alive(pid: Any, started: Any = None) -> bool:
    """Whether the run's process is still there. This agent's own child is polled, which reaps it,
    so an exited child reads gone and leaves its exit status; another agent's is signalled, and is
    the run's only while its start time is still `started` (when the intent recorded one)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid not in _procs and isinstance(started, int) and proc_start(pid) not in (started, None):
        return False  # the number was taken again by another process
    if pid in _procs:
        code = _procs[pid].poll()
        if code is None:
            return True
        _exits[pid] = code
        del _procs[pid]
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill(pid: Any, started: Any = None) -> None:
    """The run's whole process group, as the stop time kills: it was started in a session of its own.
    Only while `alive` still reads it as the run, so a reused pid's group is never killed."""
    if alive(pid, started):
        with suppress(OSError):
            os.killpg(pid, signal.SIGKILL)
        p = _procs.pop(pid, None)
        if p is not None:  # this agent's own: reaped, so a killed run leaves no zombie
            with suppress(subprocess.TimeoutExpired):
                _exits[pid] = p.wait(timeout=5)


def fail(repo: str, intent: dict[str, Any], why: str) -> dict[str, Any]:
    """Move the intent to `failed.json` `{sha, at, log, exit, why}`: nothing further is promoted for
    the repo until the person clears it."""
    out = {"sha": intent.get("sha"), "at": datetime.now(UTC).isoformat(), "log": intent.get("log")}
    out |= {"exit": _exits.pop(intent.get("pid"), None), "why": why, "by": intent.get("by")}  # None: not ours
    _write(repo_dir(repo) / "failed.json", out)
    clear(repo, "inflight")
    return out


def tail(log_path: Any, lines: int = 20) -> list[str]:
    """The log's last lines, for the failure's row."""
    try:
        text = Path(str(log_path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


# -- one pass over the registry (the home's tick, detached) ---------------------------------------


def settled(moved: Any, now: datetime) -> bool:
    """Main has stood still for `PROMOTE_SETTLE`: a burst of merges is one promote."""
    try:
        t = datetime.fromisoformat(str(moved))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return (now - t).total_seconds() >= PROMOTE_SETTLE


def note_text(repo: str, intent: dict[str, Any]) -> str:
    n = intent.get("ahead")
    count = f" — {n} commit{'' if n == 1 else 's'}" if isinstance(n, int) else ""
    return f"promoted `{repo}` `{str(intent.get('sha') or '')[:7]}`{count}"


def _conclude(repo: str, r: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """The run's outcome, read from `check` and never from its exit code (for this repo the agent
    judging it need not be the one that started it): `(failed or None, note or None)`, clearing
    the intent file whichever way it ended. Still running and inside the bound: `(None, None)`."""
    intent = r["inflight"]
    if r.get("live") == intent.get("sha") and not r.get("live_why"):
        alive(intent.get("pid"))  # reaped if it was this agent's child: a done run leaves no zombie
        _exits.pop(intent.get("pid"), None)
        clear(repo, "inflight")
        return None, note_text(repo, intent)
    if not alive(intent.get("pid"), intent.get("started")):
        live = r.get("live_why") and f"live unknown — {r['live_why']}" or f"live is {str(r.get('live'))[:7]}"
        return fail(repo, intent, f"the run ended and {live}, not {str(intent.get('sha'))[:7]}"), None
    try:
        at = datetime.fromisoformat(str(intent.get("at")))
    except ValueError:
        at = now
    if (now - at).total_seconds() > PROMOTE_BOUND:
        kill(intent.get("pid"), intent.get("started"))
        return fail(repo, intent, f"still running after {PROMOTE_BOUND / 60:g} minutes: killed"), None
    return None, None


def survey(
    roots: list[str], prev: dict[str, dict[str, Any]], full: bool, auto: dict[str, bool], now: datetime
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
    """One pass, in a thread: every registered checkout whose `.agentorc.yml` carries `promote:`,
    keyed by the repo's name — `(readings, notes, bad)`. `full` takes all three readings (the
    reports' cadence); otherwise only `check`, and only for a repo with a run in flight. A run in
    flight is concluded, and under `auto: true` one is started when live ≠ main, the three
    preconditions hold and main has settled. `notes` are the *promoted …* lines for the person
    inbox; `bad` names each checkout whose block could not be used, with why."""
    readings: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    bad: dict[str, str] = {}
    stamp = now.isoformat()
    for root in roots:
        repo = Path(root).name
        try:
            b = block(root)
        except ValueError as e:
            bad[root] = str(e)
            continue
        if b is None:
            continue
        old = prev.get(repo) or {}
        r: dict[str, Any] = {k: v for k, v in old.items() if k not in ("inflight", "failed", "unmet")}
        r["root"] = str(root)
        r["inflight"], r["failed"] = inflight(repo), failed(repo)
        first = not old
        if full or first:
            main = read_main(root)
            for k in ("fetch_why", "main_why", "moved"):
                r.pop(k, None)
            r.update(main)
            if r.get("main"):
                r["checks"], why = read_checks(root, r["main"])
                r["checks_why"] = why or None
            r["at"] = stamp
        if full or first or r["inflight"]:
            sha, why = read_live(root, b["check"])
            if sha:
                r["live"], r["live_why"] = sha, None
            else:
                r.setdefault("live", None)
                r["live_why"] = why
            r["ahead"] = ahead(root, r.get("live"), r.get("main"))
        if r["inflight"]:
            fl, note = _conclude(repo, r, now)
            if note:  # a promote that succeeds clears a failure standing, as Dismiss does (§6)
                notes.append(note)
                clear(repo, "failed")
                r["failed"] = None
            elif fl:
                r["failed"] = fl
            r["inflight"] = inflight(repo)
        r["auto"] = bool(auto.get(repo))
        behind = bool(r.get("main")) and r.get("live") != r["main"]
        if r["auto"] and behind and unmet(r) is None and settled(r.get("moved"), now):
            r["inflight"] = start(root, repo, r["main"], b["run"], "auto", r.get("ahead"))
        u = unmet(r)
        r["unmet"] = {"name": u[0], "text": u[1]} if u else None
        if r["failed"]:
            r["failed"] = {**r["failed"], "tail": tail(r["failed"].get("log"))}
        readings[repo] = r
    return readings, notes, bad
