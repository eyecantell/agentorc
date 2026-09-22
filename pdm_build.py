"""pdm-backend's local build hook (design §4.4 *Version skew is survivable*, TD-062 (c)).

A wheel says which commit it was built from: `sessionorc/_build.json` carries the commit, whether
the tree had changes on top of it, the directory it was built from and when. The live install is
promoted from the main checkout (CLAUDE.md), and before this a wheel was a flat `0.0.1` whatever
it held, so nothing could say that `main` had moved past what was running. Only a wheel carries
it: an editable install runs the checkout itself, and a build outside a git checkout (from an
sdist) writes nothing, which `sessionorc.build.info()` reads as *unknown*."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime


def _git(root, *args: str) -> str:
    cp = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def pdm_build_update_files(context, files) -> None:
    if context.target != "wheel":
        return
    try:  # a build record is never worth a failed build: any git trouble writes none
        commit = _git(context.root, "rev-parse", "HEAD")
        dirty = bool(commit and _git(context.root, "status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return
    if not commit:
        return
    info = {
        "commit": commit,
        "dirty": dirty,
        "source": str(context.root.resolve()),
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # the build directory's contents are packed at the wheel's root, so the path under it is the
    # path in the wheel
    out = context.ensure_build_dir() / "sessionorc" / "_build.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(info, indent=1) + "\n")
