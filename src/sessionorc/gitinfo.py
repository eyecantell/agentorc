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

    def to_dict(self) -> dict:
        return asdict(self)


class WorktreeError(RuntimeError):
    pass


WORKTREES_DIR = ".claude/worktrees"  # design §5 default; Claude Code's own `--worktree` uses the same place


def ensure_worktree(repo: Path, name: str, timeout: float = 60.0) -> Path:
    """`<repo>/.claude/worktrees/<name>` on branch <name>, created from origin's default branch
    (after a fetch) or from HEAD when there is no origin. Reused when it already exists. Runs the
    repo's own `scripts/hydrate_worktree.sh` when present (dev-cadence repos), so the worktree
    gets the untracked pieces git leaves out."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise WorktreeError(f"worktree name {name!r}: letters, digits, . _ - only")
    # The MAIN checkout, even when called from inside a worktree: --show-toplevel would answer
    # with the worktree and nest the new one under it (cadence §9 path-resolution; review 2026-09-06).
    common = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if common.returncode != 0:
        raise WorktreeError(f"{repo} is not a git repository")
    repo = Path(common.stdout.strip()).parent
    path = repo / WORKTREES_DIR / name
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
    return GitInfo(branch=branch, dirty=len(files), ahead=ahead, behind=behind, upstream=upstream, files=files[:20])
