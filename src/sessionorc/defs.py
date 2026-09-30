"""The home's definition files have a history (design §4.9 *What is left at the home has a history*,
TD-210, TD-229 slice 5): at the home, `~/.agentorc` is a git work tree that tracks `org.yml`,
`profiles.yml` and `settings.yml` and ignores the rest — sessions, runs and mail are state, and
`hosts.yml` is this machine's own. The home's host agent is the one committer; it reads none of the
three for meaning, only whether each parses as YAML. Nothing here adds a remote or pushes."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import yaml

from sessionorc import paths

log = logging.getLogger(__name__)

TRACKED = ("org.yml", "profiles.yml", "settings.yml")
IGNORE = "# the home's definitions (design §4.9): these are tracked, the rest is state\n*\n!.gitignore\n" + "".join(
    f"!{f}\n" for f in TRACKED
)
# the committer's own name, and nothing of the person's git setup that could stop or sign a commit
_GIT = (
    "git",
    "-c", "user.name=agentorc",
    "-c", "user.email=agentorc@localhost",
    "-c", "core.hooksPath=/dev/null",
    "-c", "commit.gpgsign=false",
)  # fmt: skip


def _git(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*_GIT, "-C", str(home), *args], capture_output=True, text=True, timeout=30)


def is_tree(home: Path | None = None) -> bool:
    """Whether the home is a work tree of its own (its `.git` here, not a repo it happens to sit in)."""
    return ((home or paths.home()) / ".git").exists()


def init(home: Path | None = None) -> bool:
    """Make the home a work tree that tracks the three files (`ao service install`, at the home and
    never on a node), and commit what it holds as the first entry. False when it already is one."""
    home = home or paths.home()
    if is_tree(home):
        return False
    home.mkdir(parents=True, exist_ok=True)
    cp = _git(home, "init", "-q")
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "git init failed")
    ignore = home / ".gitignore"
    if not ignore.exists():
        ignore.write_text(IGNORE, encoding="utf-8")
    commit("the home's definitions, first tracked", home=home, files=(".gitignore", *TRACKED))
    return True


def _parses(p: Path) -> bool:
    try:
        yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False
    return True


def commit(message: str, *, home: Path | None = None, files: tuple[str, ...] = TRACKED) -> bool:
    """Commit `files` as they stand with `message`, and True when a commit was made. A file that does
    not parse as YAML is left out until it does; one removed is committed as removed; a home that is
    no work tree commits nothing. A git that fails is logged, never raised: the write it follows has
    already happened and stands."""
    home = home or paths.home()
    if not is_tree(home):
        return False
    try:
        tracked = set(_git(home, "ls-files", "-z").stdout.split("\0"))
        take = []
        for f in files:
            p = home / f
            if p.exists():
                if f.endswith(".yml") and not _parses(p):
                    log.info("%s does not parse as YAML: left uncommitted until it does", p)
                    continue
                take.append(f)
            elif f in tracked:
                take.append(f)  # removed: the removal is the change
        if not take:
            return False
        cp = _git(home, "add", "-A", "--", *take)
        if cp.returncode != 0:
            log.warning("the home's definitions: git add failed: %s", cp.stderr.strip())
            return False
        if _git(home, "diff", "--cached", "--quiet", "--", *take).returncode == 0:
            return False  # nothing changed
        cp = _git(home, "commit", "-q", "-m", message, "--", *take)
        if cp.returncode != 0:
            log.warning("the home's definitions: git commit failed: %s", cp.stderr.strip() or cp.stdout.strip())
            return False
    except (OSError, subprocess.SubprocessError):
        log.exception("the home's definitions: committing %r failed", message)
        return False
    log.info("the home's definitions committed: %s", message)
    return True


def changes(before: object, after: object, prefix: str = "") -> list[str]:
    """What a settings write changed, as a commit message reads it: `usage_gate.grind.week 30 → 20`,
    a key added or removed with `none` on its missing side."""
    if before is None and isinstance(after, dict):
        before = {}  # a mapping added or removed is told leaf by leaf
    if after is None and isinstance(before, dict):
        after = {}
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        for k in sorted(set(before) | set(after), key=str):
            out += changes(before.get(k), after.get(k), f"{prefix}.{k}" if prefix else str(k))
        return out
    if before == after:
        return []
    return [f"{prefix} {_word(before)} → {_word(after)}"]


def _word(v: object) -> str:
    if v is None:
        return "none"
    return yaml.safe_dump(v, default_flow_style=True, width=10_000).strip().removesuffix("...").strip()


def settings_message(before: object, after: object, cap: int = 200) -> str:
    """`settings: <the changes>`, cut to `cap` characters."""
    text = "; ".join(changes(before, after)) or "rewritten, unchanged"
    return "settings: " + (text if len(text) <= cap else text[: cap - 1] + "…")
