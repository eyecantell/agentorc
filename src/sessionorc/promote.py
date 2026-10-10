"""The promote's readings and its intent files (design §6 *Promote*, §5 `promote:`, TD-132 slice 1).

A repo's live copy is made from `main` by a person's press or the home's policy, never by a
session. This module is the part that runs in a thread and returns plain data: the repo's
`promote:` block (the one key of `.agentorc.yml` the host agent reads, by key alone), the three
readings — **live** from `check`, **main** after the home's own `git fetch origin main`, **checks**
from `gh` — the three preconditions as one function, and the run itself, detached, with its intent
file written before it starts. The agent's `PromoteMixin` decides when; nothing here keeps state
beyond the files under `~/.agentorc/promotes/<repo>/`.

A rollback (§6 *A rollback*, TD-226) is the same run started in a detached worktree of the checkout at
an older commit of main, `~/.agentorc/promotes/<repo>/tree`, so the person's checkout is never moved;
one that concludes writes `held.json`, and the policy starts nothing for the repo while it stands.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sessionorc import build, paths

PROMOTE_EVERY = 300.0  # seconds between full readings: the reports' cadence (§6)
PROMOTE_WATCH = 15.0  # seconds between `check` reads while a run is in flight
PROMOTE_SETTLE = 600.0  # main must stand still this long before `auto` promotes (§6)
PROMOTE_BOUND = 1200.0  # a run past this is killed and failed (§6)
CHECK_TIMEOUT = 60.0
GIT_TIMEOUT = 60.0
GH_TIMEOUT = 30.0
KEYS = ("run", "check")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")  # a rollback's commit: its hex in full or a prefix of seven or more
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
        # git's first `fatal:`/`error:` line says what went wrong; its last may be advice (*… and the
        # repository exists.*, TD-322), so it is kept only when no line carries either prefix
        lines = [ln.strip() for ln in (cp.stderr or "").splitlines() if ln.strip()] or ["failed"]
        why = next((ln for ln in lines if ln.startswith(("fatal:", "error:"))), lines[-1])
        return None, f"git {args[0]}: {why[:200]}"
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


def read_main(root: str | Path, fetch_why: str | None = None) -> dict[str, Any]:
    """After the home's own `git fetch origin main` (a policy that acts is not hostage to whoever
    last fetched): `main` (origin/main's head), `moved` (its committer time — when main last moved,
    for the settle and the row's age; a squash merge on GitHub stamps it), and `tree`, None when
    the checkout is on main's head with a clean tree (precondition 1) or the text of why not. A
    failed fetch is `fetch_why` beside the last-fetched main, never a missing one. Reads only:
    no checkout, no reset. `fetch_why` is the pull's own fetch of main this pass (§6 *Pull*: one
    fetch per repo per pass), `""` when it went through; None fetches here."""
    out: dict[str, Any] = {}
    why = fetch_why if fetch_why is not None else _git(root, "fetch", "-q", "origin", "main")[1]
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


def read_since(root: str | Path, live: str | None, main: str | None) -> dict[str, Any]:
    """The build chip's two readings (§6 *Promote*, §4.5a; TD-539): `live_at`, live's committer
    time, and `pending`, the commits of main past live as `{sha, subject}`, newest first, the first
    `build.PENDING_SHOWN` with `pending_more` the count of the rest — empty when live is main's
    head. A failed read, or an unknown live or main, leaves a field absent."""
    out: dict[str, Any] = {}
    at, _ = build.commit_at(str(root), live)
    if at:
        out["live_at"] = at
    got = build.pending(str(root), live, main)
    if got is not None:
        out["pending"], out["pending_more"] = got
    return out


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


def unmet(reading: dict[str, Any], press: bool = False, kind: str = "promote") -> tuple[str, str] | None:
    """The first of §6's three preconditions the reading does not meet, as `(name, text)`, or None.
    (1) `tree` — the checkout on main's head with a clean tree; (2) `checks` — green, which a
    person's press goes through (`press=True` skips it; the reply says what the checks read);
    (3) `inflight` / `failed` — nothing in flight and no failure standing for the repo. Then the
    rollback's `held`, which stops the policy and never a press (§6 *The hold*). A `rollback` is
    asked neither (1), since the checkout's tree is not what it installs, nor `failed`, since a
    failed promote is the first reason to go back (§6 *What stands in its way*)."""
    if not reading.get("main"):
        return "main", f"main cannot be read: {reading.get('main_why') or 'unknown'}"
    if kind == "promote":
        if "tree" not in reading:
            return "tree", "the checkout has not been read yet"
        if reading["tree"]:
            return "tree", str(reading["tree"])
    if not press and reading.get("checks") != "green":
        why = reading.get("checks_why")
        return "checks", f"checks on main are {reading.get('checks') or 'unknown'}" + (f": {why}" if why else "")
    if reading.get("inflight"):
        f = reading["inflight"]
        what = "rollback" if f.get("kind") == "rollback" else "promote"
        return "inflight", f"a {what} of {str(f.get('sha', ''))[:7]} is in flight"
    if kind == "promote" and reading.get("failed"):
        return "failed", f"the promote of {str(reading['failed'].get('sha', ''))[:7]} failed and is not cleared"
    if not press and (h := reading.get("held")):
        return "held", (
            f"live was rolled back to {str(h.get('sha', ''))[:7]} from {str(h.get('from') or 'unknown')[:7]}: "
            "nothing is promoted by itself until the person promotes or clears it"
        )
    return None


def resolve(root: str | Path, commit: str) -> tuple[str | None, str]:
    """A rollback's commit, resolved in the checkout after the home's fetch (§6 *Which commit*):
    `(full sha, "")`, or `(None, why)` when it is not hex (a branch, a tag or `HEAD~1` would be read
    in the person's checkout, not on main), names no commit or more than one, or is not an ancestor
    of `origin/main` (a branch is never promoted). Whether it is live already is the caller's."""
    x = (commit or "").strip().lower()
    if not HEX_RE.match(x):
        return None, (
            f"{commit!r} is not a commit's hex (seven to forty of 0-9a-f): a branch, a tag or HEAD~n would be "
            "read in the checkout, not on main"
        )
    sha, _ = _git(root, "rev-parse", "--verify", "--quiet", f"{x}^{{commit}}")
    if not sha:
        objs, _ = _git(root, "rev-parse", f"--disambiguate={x}")
        commits = [o for o in (objs or "").split() if _git(root, "cat-file", "-t", o)[0] == "commit"]
        if len(commits) > 1:
            return None, f"{x} names {len(commits)} commits: give more of it"
        return None, f"{x} names no commit in {root}"
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "origin/main"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git merge-base: {type(e).__name__}"
    if cp.returncode == 1:
        return None, f"{sha[:7]} is not on main: a branch is never promoted (a hotfix is a merge, then a press)"
    if cp.returncode != 0:
        return None, "git merge-base: " + ((cp.stderr or "").strip().splitlines() or ["failed"])[-1][:200]
    return sha, ""


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


def last(repo: str) -> dict[str, Any] | None:
    """`last.json` `{sha, from, at, by}`: the promote that last concluded, `from` what `check` read
    before it — where `--back` goes (§6 *Which commit*)."""
    return _read(repo_dir(repo) / "last.json")


def held(repo: str) -> dict[str, Any] | None:
    """`held.json` `{sha, from, main, at}`: a rollback concluded, and the policy waits for the person."""
    return _read(repo_dir(repo) / "held.json")


def drop_tree(root: str | Path, repo: str) -> None:
    """Remove the worktree an earlier run left (§6 *How it reaches `run`*): it stays until the next
    promote or rollback of the repo starts, since what was installed from it may name it."""
    t = repo_dir(repo) / "tree"
    if t.exists():
        _git(root, "worktree", "remove", "--force", str(t))
        if t.exists():  # not the checkout's worktree any more, or never was: the directory alone
            shutil.rmtree(t, ignore_errors=True)
    _git(root, "worktree", "prune")


def make_tree(root: str | Path, repo: str, sha: str) -> Path:
    """A detached worktree of the checkout at `sha`: the one write a rollback makes to a repo — an
    entry in its worktree list, touching no branch, no index and no file of the person's tree.
    Raises ValueError, saying why, when git cannot make it."""
    t = repo_dir(repo) / "tree"
    t.parent.mkdir(parents=True, exist_ok=True)
    _, why = _git(root, "worktree", "add", "-q", "--detach", str(t), sha, timeout=CHECK_TIMEOUT * 5)
    if why:
        raise ValueError(f"the rollback's worktree cannot be made: {why}")
    return t


def clear(repo: str, which: str) -> None:
    with suppress(FileNotFoundError):
        (repo_dir(repo) / f"{which}.json").unlink()


def start(
    root: str | Path,
    repo: str,
    sha: str,
    run: str,
    by: str,
    n: int | None,
    kind: str = "promote",
    frm: str | None = None,
) -> dict[str, Any]:
    """Start `run` detached from the agent's process group (for this repo the run restarts the agent
    that started it), output to `<sha>.log`: in the checkout for a promote, in a detached worktree
    at `sha` for a `rollback` (§6 *How it reaches `run`*), a tree an earlier run left removed first
    either way. `run` is handed `AGENTORC_PROMOTE_SHA`, `_FROM` (`frm`, what is live; empty when
    unread) and `_ROOT` (the checkout). The intent file is written **before** the start and gains
    the pid after, so an agent that dies in between still finds it; `n` is how many commits it
    makes live, for the note. Returns the intent. Raises ValueError when the worktree cannot be made."""
    d = repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    drop_tree(root, repo)
    tree = make_tree(root, repo, sha) if kind == "rollback" else None
    log_path = d / f"{sha}.log"
    intent: dict[str, Any] = {"sha": sha, "at": datetime.now(UTC).isoformat(), "pid": None, "log": str(log_path)}
    intent |= {"by": by, "ahead": n, "kind": kind, "from": frm, "tree": str(tree) if tree else None}
    _write(d / "inflight.json", intent)
    env = {**os.environ, "AGENTORC_PROMOTE_SHA": sha, "AGENTORC_PROMOTE_FROM": frm or ""}
    env["AGENTORC_PROMOTE_ROOT"] = str(root)
    with open(log_path, "ab") as out:
        p = subprocess.Popen(
            run, shell=True, cwd=str(tree or root), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env,
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


def note_text(repo: str, intent: dict[str, Any], behind: int | None = None) -> str:
    """The `system` note a concluded run files: *promoted `<repo>` `<sha7>` — n commits*, or for a
    rollback *rolled back `<repo>` to `<sha7>` from `<sha7>` — main is n commits ahead* (§6)."""
    if intent.get("kind") == "rollback":
        frm = str(intent.get("from") or "")[:7] or "an unread live"
        text = f"rolled back `{repo}` to `{str(intent.get('sha') or '')[:7]}` from `{frm}`"
        return text + (
            f" — main is {behind} commit{'' if behind == 1 else 's'} ahead" if isinstance(behind, int) else ""
        )
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
        _concluded(repo, intent, r.get("main"), now)
        clear(repo, "inflight")
        behind = ahead(r["root"], intent.get("sha"), r.get("main")) if intent.get("kind") == "rollback" else None
        return None, note_text(repo, intent, behind)
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


def _concluded(repo: str, intent: dict[str, Any], main: str | None, now: datetime) -> None:
    """What a run that reached its commit leaves: `last.json` for every promote; a rollback writes
    the hold, and a second one under a hold keeps its `from` — what the person first went back from
    is what the row goes on naming; a plain promote ends the hold (§6 *The hold*)."""
    d = repo_dir(repo)
    at = now.isoformat()
    _write(d / "last.json", {"sha": intent.get("sha"), "from": intent.get("from"), "at": at, "by": intent.get("by")})
    if intent.get("kind") == "rollback":
        frm = (held(repo) or {}).get("from") or intent.get("from")
        _write(d / "held.json", {"sha": intent.get("sha"), "from": frm, "main": main, "at": at})
    else:
        clear(repo, "held")


def survey(
    roots: list[str],
    prev: dict[str, dict[str, Any]],
    full: bool,
    auto: dict[str, bool],
    now: datetime,
    fetched: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
    """One pass, in a thread: every registered checkout whose `.agentorc.yml` carries `promote:`,
    keyed by the repo's name — `(readings, notes, bad)`. `full` takes all three readings (the
    reports' cadence); otherwise only `check`, and only for a repo with a run in flight. A run in
    flight is concluded, and under `auto: true` one is started when live ≠ main, the three
    preconditions hold and main has settled. `notes` are the *promoted …* lines for the person
    inbox; `bad` names each checkout whose block could not be used, with why. `fetched` holds the
    pull's fetch of main for each root it fetched this pass (`pulls`), which `read_main` reuses."""
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
            main = read_main(root, (fetched or {}).get(str(root)))
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
            for k in ("live_at", "pending", "pending_more"):
                r.pop(k, None)
            r.update(read_since(root, r.get("live"), r.get("main")))
        if r["inflight"]:
            fl, note = _conclude(repo, r, now)
            if note:  # a promote or rollback that succeeds clears a failure standing, as Dismiss does (§6)
                notes.append(note)
                clear(repo, "failed")
                r["failed"] = None
            elif fl:
                r["failed"] = fl
            r["inflight"] = inflight(repo)
        r["held"] = held(repo)
        r["auto"] = bool(auto.get(repo))
        behind = bool(r.get("main")) and r.get("live") != r["main"]
        # `auto` acts only on a live it has read (§6, Paul 2026-09-28): a `check` that is not
        # answering is *unknown*, not behind — even with a last good reading kept beside its why —
        # and the row still offers the person's press
        read = bool(r.get("live")) and not r.get("live_why")
        if r["auto"] and behind and read and unmet(r) is None and settled(r.get("moved"), now):
            r["inflight"] = start(root, repo, r["main"], b["run"], "auto", r.get("ahead"), frm=r.get("live"))
        u = unmet(r)
        r["unmet"] = {"name": u[0], "text": u[1]} if u else None
        if r["failed"]:
            r["failed"] = {**r["failed"], "tail": tail(r["failed"].get("log"))}
        readings[repo] = r
    return readings, notes, bad


# ── the pull: the main checkout follows origin (design §6 *Pull*, TD-263) ──────────────────────
PULL_OUTCOMES = ("current", "pulled", "waiting", "refused", "off")


def _git_why(root: str | Path, *args: str) -> tuple[bool, str]:
    """`(True, "")` or `(False, why)` — git's own first line, its `error:`/`fatal:` prefix dropped: a
    refused fast-forward's last line is *Aborting*, its first says what would be overwritten."""
    try:
        cp = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git {args[-1]}: {type(e).__name__}"
    if cp.returncode == 0:
        return True, ""
    lines = [ln.strip() for ln in (cp.stderr or cp.stdout or "").splitlines() if ln.strip()]
    first = re.sub(r"^(error|fatal|hint): ", "", lines[0]) if lines else "git failed"
    return False, first[:200]


def pull(root: str | Path, on: bool, occupant: str | None, now: datetime) -> tuple[dict[str, Any], str | None]:
    """One registered checkout's pull, in a thread: `(reading, fetch_why)`. The reading is `{at,
    outcome, why, from, to, commits, occupant}`, `outcome` one of `PULL_OUTCOMES`; `fetch_why` is
    this pass's fetch of `main` for the promote to reuse — `""` when it went through, None when no
    fetch of `main` was made here. `occupant` is the caller's read of the root (§6 *Pull* (2)):
    None when every session there is at rest, a name when one is mid-turn, `""` when one's state
    cannot be read. Only ever `git fetch` and `git merge --ff-only`: no checkout, reset, rebase or
    stash, and never a push."""
    r: dict[str, Any] = {k: None for k in ("why", "from", "to", "commits", "occupant")}
    r["at"] = now.isoformat()
    if not on:
        return {**r, "outcome": "off"}, None

    def refused(why: str) -> dict[str, Any]:
        return {**r, "outcome": "refused", "why": why}

    from sessionorc import board  # the checkout's default branch and git's busy markers, shared

    default = board.default_branch(Path(root))
    _, fetch_why = _git(root, "fetch", "-q", "origin", default)
    main_fetch = fetch_why if default == "main" else None
    if fetch_why:
        return refused(fetch_why), main_fetch
    head, why = _git(root, "rev-parse", "HEAD")
    to, why2 = _git(root, "rev-parse", f"origin/{default}")
    if head is None or to is None:
        return refused(why or why2), main_fetch
    branch, _ = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    if branch != default:
        return refused(f"on {branch or 'a detached HEAD'}"), main_fetch
    r["from"], r["to"] = head, to
    if head == to:
        return {**r, "outcome": "current"}, main_fetch
    try:
        under_way = board.busy(Path(root))
    except board.Refused as e:
        return refused(str(e)), main_fetch
    if under_way:
        return refused(f"a git operation under way ({', '.join(under_way)})"), main_fetch
    if inflight(Path(root).name):
        return refused("a promote in flight"), main_fetch
    mine, _ = _git(root, "rev-list", "--count", f"origin/{default}..HEAD")
    if mine and mine != "0":
        n = int(mine)
        return refused(
            f"{n} commit{'' if n == 1 else 's'} of its own, not on origin — the person's to push"
        ), main_fetch
    if occupant is not None:
        return {
            **r,
            "outcome": "waiting",
            "occupant": occupant or None,
            "why": "unreadable" if not occupant else None,
        }, main_fetch
    n, _ = _git(root, "rev-list", "--count", f"HEAD..origin/{default}")
    # `merge.autoStash` off whatever the person's config says: the pull never stashes (§6 *Pull*)
    ok, why = _git_why(root, "-c", "merge.autoStash=false", "merge", "--ff-only", "-q", f"origin/{default}")
    if not ok:
        return refused(why), main_fetch
    return {**r, "outcome": "pulled", "commits": int(n) if n and n.isdigit() else None}, main_fetch


def pulls(
    roots: list[str], on: dict[str, bool], occupants: dict[str, str | None], now: datetime
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The pull's pass over every registered checkout, `promote:` block or not, in a thread:
    `(readings by repo name, fetched)` — `fetched` maps each root whose `main` this pass fetched to
    that fetch's why (`""` when it went through), for `survey` to reuse. `on` is
    `repos.<repo>.pull` (absent is true); `occupants` is the caller's read of each root."""
    readings: dict[str, dict[str, Any]] = {}
    fetched: dict[str, str] = {}
    for root in roots:
        repo = Path(root).name
        try:
            reading, main_fetch = pull(root, on.get(repo, True), occupants.get(str(root)), now)
        except Exception as e:  # noqa: BLE001 — one checkout never stops the pass over the others
            reading, main_fetch = (
                {"at": now.isoformat(), "outcome": "refused", "why": f"{type(e).__name__}: {e}"[:200]},
                None,
            )
        readings[repo] = reading
        if main_fetch is not None:
            fetched[str(root)] = main_fetch
    return readings, fetched
