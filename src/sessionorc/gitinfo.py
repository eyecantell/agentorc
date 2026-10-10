"""Per-directory git status for cards and the Focus side panel (design §4.4): branch, dirty,
ahead/behind, cheap enough to refresh on a tick."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class GitInfo:
    branch: str
    dirty: int  # changed + untracked paths
    ahead: int
    behind: int
    upstream: str | None
    files: list[str]  # up to 20 porcelain lines ("M path", "?? path")
    # **One measure of pushed** (design §4.2, 2026-09-20, TD-080): *does this work exist only on
    # this machine*, never *is it merged*. Computed once here and read by the card's flag, Ready to
    # close, the Inbox's unpushed row and `ao team stop --close`, which had three tests between
    # them. `pushed_against` says what it was measured against, so a page can show it.
    unpushed: int = 0
    pushed_against: str = ""
    # the commit HEAD is at, so a detached HEAD can be named by it (design §4.5 *The card's
    # anatomy*, row 3, TD-095: *detached at <short sha>*); "" in a repo with no commit yet
    oid: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class WorktreeError(RuntimeError):
    pass


WORKTREES_DIR = ".claude/worktrees"  # design §5 default; Claude Code's own `--worktree` uses the same place


def worktree_path(repo: Path, name: str) -> Path:
    """Where `ensure_worktree` puts (or finds) the worktree `name` of `repo`: under the MAIN
    checkout's `.claude/worktrees/`, even when `repo` is a worktree or a subdirectory of it —
    `--show-toplevel` would answer with the worktree and nest the new one under it (cadence §9
    path-resolution; review 2026-09-06). Read-only: the one resolution every check of that path
    shares (review of PR #215)."""
    common = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if common.returncode != 0:
        raise WorktreeError(f"{repo} is not a git repository")
    return Path(common.stdout.strip()).parent / WORKTREES_DIR / name


def toplevel(directory: Path | str, timeout: float = 5.0) -> str:
    """The top level of the checkout `directory` is in — a worktree's own, as git says — or "" outside
    one: the `stat` link method's `root` (design §4.4a *The New session form on another host*)."""
    return (_git(directory, "rev-parse", "--show-toplevel", timeout=timeout) or "").strip()


def ensure_worktree(repo: Path, name: str, timeout: float = 60.0) -> Path:
    """`<repo>/.claude/worktrees/<name>` on branch <name>, created from origin's default branch
    (after a fetch) or from HEAD when there is no origin. Reused when it already exists. Runs the
    repo's own `scripts/hydrate_worktree.sh` when present (dev-cadence repos), so the worktree
    gets the untracked pieces git leaves out."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise WorktreeError(f"worktree name {name!r}: letters, digits, . _ - only")
    path = worktree_path(repo, name)
    repo = path.parent.parent.parent
    if path.is_dir():
        if subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
            raise WorktreeError(f"{path} exists but is not a worktree")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], capture_output=True, timeout=timeout)
    base = "origin/HEAD"
    if (
        subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", base], capture_output=True).returncode
        != 0
    ):
        base = "HEAD"
    has_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", f"refs/heads/{name}"], capture_output=True
    )
    args = ["git", "-C", str(repo), "worktree", "add", "-q"]
    args += [str(path), name] if has_branch.returncode == 0 else ["-b", name, str(path), base]
    cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if cp.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {cp.stderr.strip() or cp.stdout.strip()}")
    hydrate = repo / "scripts" / "hydrate_worktree.sh"
    if hydrate.is_file() and os.access(hydrate, os.X_OK):
        hp = subprocess.run([str(hydrate), str(path)], capture_output=True, text=True, timeout=timeout)
        if hp.returncode != 0:
            # The worktree exists and is usable; say what hydration left out rather than hide it.
            raise WorktreeError(
                f"worktree created at {path}, but hydrate_worktree.sh failed: {(hp.stderr or hp.stdout).strip()[-400:]}"
            )
    return path


def git_info(directory: Path | str, timeout: float = 5.0) -> GitInfo | None:
    try:
        cp = subprocess.run(
            ["git", "-C", str(directory), "status", "--porcelain=v2", "--branch", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    branch, upstream, ahead, behind, files, oid = "?", None, 0, 0, [], ""
    for line in cp.stdout.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
            oid = "" if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            ahead, behind = int(parts[2].lstrip("+")), int(parts[3].lstrip("-"))
        elif line.startswith("1 "):
            # 1 XY sub mH mI mW hH hI path   — path may contain spaces (v2 without -z is unquoted)
            parts = line.split(" ", 8)
            files.append(f"{parts[1].replace('.', '')} {parts[8]}")
        elif line.startswith("2 "):
            # 2 XY sub mH mI mW hH hI Xscore path<TAB>origPath
            parts = line.split(" ", 9)
            files.append(f"{parts[1].replace('.', '')} {parts[9].split(chr(9))[0]}")
        elif line.startswith("? "):
            files.append(f"?? {line[2:]}")
        elif line.startswith("u "):
            # u XY sub m1 m2 m3 mW h1 h2 h3 path
            files.append(f"UU {line.split(' ', 10)[10]}")
    unpushed, against = _unpushed(directory, branch, upstream, ahead, timeout)
    return GitInfo(
        branch=branch,
        dirty=len(files),
        ahead=ahead,
        behind=behind,
        upstream=upstream,
        files=files[:20],
        unpushed=unpushed,
        pushed_against=against,
        oid=oid,
    )


def changed_files(directory: Path | str, base_ref: str | None, limit: int, timeout: float = 5.0) -> list[dict] | None:
    """The paths the work in `directory` has changed against its base, `{path, at, sha}` newest first, a
    path once, the newest `limit` (design §4.2 *The record's `files`*, TD-538): `git diff --name-only`
    from `merge-base HEAD <base_ref>` and the untracked paths — the tree alone where `base_ref` is None
    (no origin) or shares no merge base with `HEAD` — each path absolute. One the porcelain holds is the
    file's own: its modification time and no `sha` (a deleted one the newest commit's time, else the
    read's); any other is the newest of the branch's commits that touched it, from one `git log
    --name-only`. None when a read fails, so the record keeps what it had. Computed on each read and never kept; nothing is fetched."""
    top = toplevel(directory, timeout=timeout)
    if not top:
        return None
    status = _git(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=timeout)
    if status is None:
        return None
    dirty: list[str] = []
    fields = status.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        dirty.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1  # a rename's or copy's source follows its path, on either side: no longer in the tree
    paths = dict.fromkeys(dirty)
    commits: dict[str, tuple[str, int]] = {}  # path → the newest commit since the base that touched it
    # no merge base (an unborn HEAD, unrelated histories) reads as no origin: the tree alone
    base = (_git(top, "merge-base", "HEAD", base_ref, timeout=timeout) or "").strip() if base_ref else ""
    if base:
        diff = _git(top, "diff", "--name-only", "-z", base, timeout=timeout)
        log_out = _git(top, "log", "--name-only", "-z", "--format=%x01%H %ct", f"{base}..HEAD", timeout=timeout)
        if diff is None or log_out is None:
            return None
        paths.update(dict.fromkeys(p for p in diff.split("\0") if p))
        for chunk in log_out.split("\x01")[1:]:  # `<sha> <time>\0\n<path>\0…`, newest first as git gives them
            head, _, names = chunk.partition("\0")
            sha, _, ct = head.partition(" ")
            for name in names.removeprefix("\n").split("\0"):
                if name and name not in commits and ct.isdigit():
                    commits[name] = (sha, int(ct))
    now = datetime.now(UTC).timestamp()
    dirty_set = set(dirty)
    out: list[tuple[float, dict]] = []
    for rel in paths:
        path = os.path.join(top, rel)
        if rel in dirty_set:
            try:
                at = os.stat(path).st_mtime
            except OSError:  # deleted: the commit's time, or the read's
                at = commits[rel][1] if rel in commits else now
            out.append((at, {"path": path, "at": _iso(at)}))
        elif rel in commits:
            sha, ct = commits[rel]
            out.append((ct, {"path": path, "at": _iso(ct), "sha": sha}))
    out.sort(key=lambda t: -t[0])
    return [f for _, f in out[:limit]]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


UNKNOWN = "git state unknown"


def work_left(git: Any) -> str | None:
    """Why a checkout is not *clean and pushed*, from a record's `git` fields, or None when it is
    (design §6 rule 2, §4.2 *One measure*). **Unknown is work left**: no fields yet, or a count
    that is not a number, reads `UNKNOWN` — known, not merely absent. The one test the tick's
    wanted restart, a person's Restart and `ao team stop --close` share (TD-250), so the three
    cannot drift; `unpushed` is the host agent's one measure (TD-080) and nothing is run here."""
    if not isinstance(git, dict) or not all(
        isinstance(git.get(k), int) and not isinstance(git.get(k), bool) for k in ("dirty", "unpushed")
    ):
        return UNKNOWN
    if git["dirty"]:
        return f"{git['dirty']} uncommitted"
    if git["unpushed"]:
        return f"{git['unpushed']} unpushed (vs {git.get('pushed_against') or 'its remote'})"
    return None


def _git(directory: Path | str, *args: str, timeout: float) -> str | None:
    """One read-only git command, or None when it could not be run or failed. The host agent never
    fetches for any of this: it reads the refs it has (design §4.2)."""
    try:
        cp = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout if cp.returncode == 0 else None


def _unpushed(directory: Path | str, branch: str, upstream: str | None, ahead: int, timeout: float) -> tuple[int, str]:
    """How many commits exist **only on this machine**, and what that was measured against (design
    §4.2 *one measure*, TD-080). The first rule that applies answers:

    1. the branch has its own remote-tracking ref, `origin/<branch>` — count against it, whatever
       the upstream is, because *ahead of `origin/main`* is *unmerged* and that is a different
       question (a launch branch tracking `origin/main` and pushed to its own ref read *308
       unpushed* for ever, TD-080);
    2. no such ref but an upstream — the porcelain's *ahead*, as before;
    3. neither — a detached `HEAD`, or a branch never pushed — the commits on no remote-tracking
       branch at all, which is 0 exactly when `HEAD` is contained in one (a worker that merged and
       sits on a detached `origin/main`) and otherwise **not pushed**, which is the stranded work
       the check exists for.

    A read that cannot be made leaves the count at the porcelain's, never at a confident 0."""
    if branch and branch != "(detached)":
        ref = f"refs/remotes/origin/{branch}"
        if _git(directory, "rev-parse", "--verify", "-q", ref, timeout=timeout) is not None:
            out = _git(directory, "rev-list", "--count", f"origin/{branch}..HEAD", timeout=timeout)
            if out is not None and out.strip().isdigit():
                return int(out.strip()), f"origin/{branch}"
    if upstream:
        return ahead, upstream
    out = _git(directory, "rev-list", "--count", "HEAD", "--not", "--remotes", timeout=timeout)
    if out is not None and out.strip().isdigit():
        return int(out.strip()), "remote branches"
    return ahead, ""
