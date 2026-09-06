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
