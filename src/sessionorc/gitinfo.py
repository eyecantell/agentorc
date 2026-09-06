"""Per-directory git status for cards and the Focus side panel (design §4.4): branch, dirty,
ahead/behind, cheap enough to refresh on a tick."""

from __future__ import annotations

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
        elif line.startswith(("1 ", "2 ")):
            xy = line.split(" ", 2)[1]
            files.append(f"{xy.replace('.', '')} {line.split(' ')[-1]}")
        elif line.startswith("? "):
            files.append(f"?? {line[2:]}")
        elif line.startswith("u "):
            files.append(f"UU {line.split(' ')[-1]}")
    return GitInfo(branch=branch, dirty=len(files), ahead=ahead, behind=behind, upstream=upstream, files=files[:20])
