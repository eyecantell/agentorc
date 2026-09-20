"""Per-directory git status for cards and the Focus side panel (design §4.4): branch, dirty,
ahead/behind, cheap enough to refresh on a tick."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


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
    branch, upstream, ahead, behind, files = "?", None, 0, 0, []
    for line in cp.stdout.splitlines():
        if line.startswith("# branch.head "):
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
    )


def _git(directory: Path | str, *args: str, timeout: float) -> str | None:
    """One read-only git command, or None when it could not be run or failed. The host agent never
    fetches for any of this: it reads the refs it has (design §4.2)."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=timeout
        )
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
