"""A stand-in for the forge's `gh` in the board write-back's tests (design §4.4, TD-264): `pr create`
records a pull request against the bare origin the working directory's `origin` names, `pr merge
--squash` squashes its branch onto the base there by pushing, as GitHub would, `pr view` reads its
state, and `pr close` closes it. `FAKE_GH_FAIL=create|merge` makes that step fail; `merge-after`
merges and fails the reply, and `merge-after-moved` also changes origin's board elsewhere right after;
`FAKE_GH_VIEW_FAIL` makes `pr view` fail. State lives beside the bare origin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def main(argv: list[str]) -> int:
    origin = git("remote", "get-url", "origin").stdout.strip()
    if not origin:
        print("no origin", file=sys.stderr)
        return 1
    state_file = Path(origin) / "fake-gh.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"prs": {}}
    fail = os.environ.get("FAKE_GH_FAIL", "")
    if argv[:2] == ["pr", "create"]:
        if fail == "create":
            print("could not create the pull request", file=sys.stderr)
            return 1
        opts = dict(zip(argv[2::2], argv[3::2], strict=False))
        n = len(state["prs"]) + 1
        state["prs"][str(n)] = {
            "base": opts["--base"],
            "head": opts["--head"],
            "title": opts["--title"],
            "state": "open",
        }
        state_file.write_text(json.dumps(state))
        print(f"https://github.com/o/r/pull/{n}")
        return 0
    if argv[:2] == ["pr", "merge"]:
        pr = state["prs"][argv[2]]
        if fail == "merge":
            print("Pull request is not mergeable", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            work = str(Path(tmp) / "w")
            git("clone", "-q", "-b", pr["base"], origin, work)
            ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
            git("fetch", "-q", "origin", pr["head"], cwd=work)
            if git(*ident, "merge", "-q", "--squash", "FETCH_HEAD", cwd=work).returncode != 0:
                print("Pull request is not mergeable: the merge commit cannot be cleanly created", file=sys.stderr)
                return 1
            git(*ident, "commit", "-q", "-m", f"{pr['title']} (#{argv[2]})", cwd=work)
            if git("push", "-q", "origin", pr["base"], cwd=work).returncode != 0:
                print("push failed", file=sys.stderr)
                return 1
        pr["state"] = "merged"
        state_file.write_text(json.dumps(state))
        if fail == "merge-after-moved":  # merged, then origin's board changed elsewhere at once
            with tempfile.TemporaryDirectory() as tmp:
                work = str(Path(tmp) / "w")
                git("clone", "-q", "-b", pr["base"], origin, work)
                board = Path(work) / "docs" / "user_attention.md"
                board.write_text(board.read_text() + "\nA neighbour's edit.\n")
                ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
                git(*ident, "commit", "-q", "-am", "a neighbour", cwd=work)
                git("push", "-q", "origin", pr["base"], cwd=work)
        if fail.startswith("merge-after"):  # merged, and the reply lost: what a timeout looks like
            print("Post https://api.github.com/graphql: context deadline exceeded", file=sys.stderr)
            return 1
        return 0
    if argv[:2] == ["pr", "view"]:
        pr = state["prs"].get(argv[2])
        if pr is None or os.environ.get("FAKE_GH_VIEW_FAIL"):
            print("no pull requests found", file=sys.stderr)
            return 1
        print(pr["state"].upper())  # what `--json state --jq .state` prints
        return 0
    if argv[:2] == ["pr", "close"]:
        state["prs"][argv[2]]["state"] = "closed"
        state_file.write_text(json.dumps(state))
        return 0
    print(f"fake gh: {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
