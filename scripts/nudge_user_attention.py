#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/nudge_user_attention.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""Due-date nudges for docs/user_attention.md items, delivered to any channel.

Reads the user-attention board, finds unchecked items whose optional
``Due: YYYY-MM-DD`` marker is today or past, and pushes ONE message listing
them. On Mondays it additionally appends a full-board summary (including
items with no due date). Silent exit when
there is nothing to say. Snoozing an item = editing its Due date on the board.

Delivery channel (first match wins — most repos have no Telegram, so the
generic hook comes first):
  1. NUDGE_COMMAND env var — a shell command; the message is piped to its
     stdin. Works with anything: ``mail -s 'board' you@example.com``,
     ``ntfy publish mytopic``, a curl to a Slack/Discord webhook, etc.
  2. TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars — built-in Telegram
     sender (the channel this script was born with in samscrape).
  3. Neither set: the message prints to stdout (cron's default mail catches
     it if configured) and the run exits nonzero so the misconfiguration
     is visible.

When a message is going out anyway (due items, or the Monday summary), a
best-effort staleness line rides along if the board repo's
``docs/cadence-sync.lock`` commit no longer matches the dev-cadence upstream
HEAD (anonymous-https ``git ls-remote``, 5 s timeout, silent on any failure) —
so a consumer repo can't quietly fall behind for weeks. Staleness alone never
triggers a send. The check targets the BOARD's repo; keep the checkout this
script *runs from* current via whatever schedule updates it.

Point ``--board`` (or USER_ATTENTION_BOARD) at the LIVE working-tree copy of
the board, not a deploy-pinned checkout: the board is an operator to-do list,
not production code, and a hand-edited Due date (snooze) must take effect the
same day — not after a merge + deploy sync. Falls back to the copy next to
this script.

Secrets (NUDGE_COMMAND contents, Telegram tokens) come from the environment —
inject them in the cron line via your secrets manager (e.g. `doppler run -- ...`).

Report mode (``--report``, plan 2026-08-10 P2): print a machine-wide attention
report to stdout and exit — no delivery channel is ever invoked, regardless of
NUDGE_COMMAND/TELEGRAM_* being set. Boards come from repeated ``--board`` flags,
or (with none) from every entry in this machine's roster
(``${XDG_CONFIG_HOME:-~/.config}/dev-cadence/repos.txt`` — written by dev-cadence
sync.sh; path-resolution spec: cadence.md §Machine scope), falling back to the
usual single-board resolution when no roster exists. Offline and fast by
default; ``--fetch`` (opt-in) additionally fetches each repo's origin serially —
hang-proofed (GIT_TERMINAL_PROMPT=0, BatchMode ssh, 30 s per repo, failures
degrade to a per-repo "fetch skipped" note) — and compares the local board
with the origin default branch's copy, plus the per-repo cadence staleness
line. When the local clone is merely BEHIND (local board untouched since the
merge-base, origin's has moved — the service-account append case, TD-030) the
row reads origin's board and says so; local-only or two-sided differences keep
reading the local file and are flagged. ``--due-only --fetch`` is the same
under a hard aggregate budget (``ATTENTION_DUE_FETCH_BUDGET``, default 8 s)
so a SessionStart hook can opt in without ever waiting long on the network.
Nudge (delivery) mode is unchanged and takes at most one ``--board``.

Usage:
    nudge_user_attention.py [--board PATH] [--dry-run] [--force-weekly]
    nudge_user_attention.py --report [--board PATH ...] [--fetch]
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("nudge_user_attention")

def _default_boards() -> list[Path]:
    boards = []
    env = os.environ.get("USER_ATTENTION_BOARD")
    if env:
        boards.append(Path(env))
    boards.append(Path(__file__).resolve().parent.parent / "docs" / "user_attention.md")
    return boards

ITEM_RE = re.compile(r"^\s*-\s*\[ \]\s+(?P<text>.+)$")
DUE_RE = re.compile(r"\bDue:\s*(?P<due>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
MAX_ITEM_CHARS = 200


@dataclass
class BoardItem:
    text: str
    due: date | None

    def overdue_days(self, today: date) -> int | None:
        """Days at-or-past due (0 = due today); None if no due date or not yet due."""
        if self.due is None or self.due > today:
            return None
        return (today - self.due).days


def parse_board(content: str, *, warn: bool = True) -> list[BoardItem]:
    """Extract unchecked items and their optional Due: dates from board markdown."""
    items = []
    for line in content.splitlines():
        m = ITEM_RE.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        due = None
        dm = DUE_RE.search(text)
        if dm:
            try:
                due = datetime.strptime(dm.group("due"), "%Y-%m-%d").date()  # noqa: DTZ007  # local civil dates by design (TD-15: skew tolerated at the sweep comparisons)
            except ValueError:
                if warn:
                    logger.warning("Unparseable Due date in item, treating as undated: %s", text[:80])
        items.append(BoardItem(text=text, due=due))
    return items


def _clip(text: str) -> str:
    return text if len(text) <= MAX_ITEM_CHARS else text[: MAX_ITEM_CHARS - 1] + "…"


def due_tag(item: BoardItem, today: date) -> str:
    """4-way due tag: 'Nd overdue' / 'due today' / 'due YYYY-MM-DD' / 'no due date'.

    overdue_days() alone can't produce this split (None for both future-dated
    and undated items); the item.due disambiguation extracted here is the same
    one build_message's weekly marker uses. Shared by nudge and report modes.
    """
    days = item.overdue_days(today)
    if days is not None:
        return "due today" if days == 0 else f"{days}d overdue"
    if item.due is not None:
        return f"due {item.due.isoformat()}"
    return "no due date"


def _report_sort_key(item: BoardItem, today: date) -> tuple[int, int]:
    """Overdue first (most overdue leading), then dated soonest-first, then undated."""
    days = item.overdue_days(today)
    if days is not None:
        return (0, -days)
    if item.due is not None:
        return (1, item.due.toordinal())
    return (2, 0)


def build_message(items: list[BoardItem], today: date, weekly: bool) -> str | None:
    """The single Telegram message for this run, or None if nothing to send."""
    due_items = [(i, i.overdue_days(today)) for i in items]
    due_items = [(i, d) for i, d in due_items if d is not None]

    lines: list[str] = []
    if due_items:
        lines.append(f"📌 user_attention.md — {len(due_items)} item(s) due:")
        for item, days in sorted(due_items, key=lambda p: -p[1]):
            lines.append(f"• ({due_tag(item, today)}) {_clip(item.text)}")

    if weekly and items:
        lines.append("")
        lines.append(f"🗓 Weekly board review — {len(items)} open item(s):")
        for item in items:
            marker = f" [due {item.due.isoformat()}]" if item.due else " [no due date]"
            lines.append(f"• {_clip(item.text)}{marker}")
        # (The old "run /stranded-work" Monday reminder is retired — TD-7/TD-12:
        # the Swept:/Swept-deep: staleness warnings are its pull-side successor.)

    if not lines:
        return None
    lines.append("")
    lines.append("Snooze: edit the item's Due date in docs/user_attention.md.")
    return "\n".join(lines)


def send_command(cmd: str, text: str) -> bool:
    """Pipe the message to an arbitrary shell command's stdin (NUDGE_COMMAND)."""
    try:
        r = subprocess.run(cmd, shell=True, input=text.encode(), timeout=120, check=False)
        if r.returncode != 0:
            logger.error("NUDGE_COMMAND exited %d", r.returncode)
        return r.returncode == 0
    except Exception:
        logger.exception("NUDGE_COMMAND failed")
        return False


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    """Best-effort Telegram push; mirrors cluster_health_check.send_telegram_alert."""
    try:
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
        ).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = resp.status == 200
        if not ok:
            logger.error("Telegram send failed: HTTP %s", resp.status)
        return ok
    except Exception:
        logger.exception("Telegram send failed")
        return False


def _normalize_source_url(source: str) -> str | None:
    """Anonymous-https form of the lock's source (legacy slug, https, or ssh).

    Always anonymous https regardless of the URL's own auth form: an ssh or
    credentialed URL would hang or fail in a cron with no agent/TTY, and the
    check must never use (or expose) credentials.
    """
    source = source.strip()
    if not source:
        return None
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):  # legacy slug owner/repo
        return f"https://github.com/{source}"
    m = re.fullmatch(r"(?:ssh://)?git@([\w.-]+)(?::\d+)?[:/](.+?)(?:\.git)?/?", source)
    if m:  # ssh port (if any) dropped — the https host serves the web UI/API
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"https?://(?:[^@/]+@)?([\w.-]+(?::\d+)?)/(.+?)(?:\.git)?/?", source)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return None


def staleness_line(board: Path) -> str | None:
    """One extra report line if the board repo's cadence sync lags upstream.

    Reads docs/cadence-sync.lock next to the board (so the check targets the
    BOARD's repo even when this script runs from another repo's checkout) and
    compares its recorded commit against the upstream remote HEAD. Best-effort
    by design (plan P2 / TD-2): any parse, git, or network failure returns None
    — the nudge's core job must never be blocked by this check. Rides along
    only when a message is already being sent (incl. Mondays), so staleness
    alone never triggers a send.
    """
    try:
        lock = board.parent / "cadence-sync.lock"
        if not lock.is_file():
            return None
        source = commit = None
        for line in lock.read_text(encoding="utf-8").splitlines():
            if line.startswith("source:"):
                source = line.split(":", 1)[1].strip()
            elif line.startswith("commit:"):
                commit = line.split(":", 1)[1].strip().removesuffix("+dirty")
        if not source or not commit:
            return None
        url = _normalize_source_url(source)
        if url is None:
            return None
        r = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            check=False,
            capture_output=True,
            timeout=5,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if r.returncode != 0 or not r.stdout:
            return None
        head = r.stdout.split()[0].decode("ascii", errors="replace")
        if head.startswith(commit):
            return None
        return (
            f"🔄 cadence sync behind (lock {commit[:8]} → upstream {head[:8]}): "
            f"pull the dev-cadence clone and re-run sync.sh against this repo."
        )
    except Exception:
        logger.debug("Staleness check skipped", exc_info=True)
        return None


def deliver(text: str) -> bool:
    """Send via the first configured channel (see module docstring)."""
    cmd = os.environ.get("NUDGE_COMMAND")
    if cmd:
        return send_command(cmd, text)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return send_telegram(text, token, chat_id)
    logger.error(
        "No delivery channel configured — set NUDGE_COMMAND (message piped to its "
        "stdin) or TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID. Printing to stdout instead."
    )
    print(text)
    return False


def resolve_board(explicit: str | None) -> Path | None:
    candidates = [Path(explicit)] if explicit else []
    candidates += _default_boards()
    for path in candidates:
        if path.is_file():
            return path
    logger.error("No user_attention.md found (tried: %s)", ", ".join(str(p) for p in candidates))
    return None


# --- report mode (plan 2026-08-10 P2) ----------------------------------------


# TD-12 sweep staleness: /stranded-work stamps `Swept: YYYY-MM-DD (host, quick|deep)`
# on the board as its final step; deep runs also stamp `Swept-deep: YYYY-MM-DD`
# (TD-13), which quick runs never touch — so the last transcript-scan date
# survives later quick sweeps instead of being overwritten out of existence.
# The report warns past SWEEP_STALE_DAYS (or when the header is absent) and,
# separately, when no deep sweep is on record or the last one is past
# SWEEP_DEEP_STALE_DAYS (a `Swept:` stamp whose own mode is deep counts as deep
# recency, so pre-TD-13 stamps never false-warn "never"). "Weekly-ish quick,
# warned past 10 days; monthly-ish deep, warned past 30" — documented with a
# parity note in cadence.md §3; change writer, reader, and doc together.
SWEEP_STALE_DAYS = 10
SWEEP_DEEP_STALE_DAYS = 30
SWEEP_RE = re.compile(r"^Swept:\s*(?P<date>\d{4}-\d{2}-\d{2})\s*(?:\((?P<meta>[^)]*)\))?", re.MULTILINE)
# `^Swept:` cannot match a `Swept-deep:` line (the colon is anchored), so the
# two headers never shadow each other.
# On duplicate `Swept-deep:` lines (a hand-edit or merge leftover — the skill
# replaces in place, so normal use never makes one) the FIRST match binds:
# fail-safe, it can only over-warn relative to a fresher line below it.
SWEEP_DEEP_RE = re.compile(r"^Swept-deep:\s*(?P<date>\d{4}-\d{2}-\d{2})\s*(?:\((?P<meta>[^)]*)\))?", re.MULTILINE)
# The mode marker the skill calls load-bearing. Matched as its own comma-separated
# token in the parenthesised meta — the skill's format is `(<host>, quick|deep)` —
# so `(kmaster, deep)` and bare `(deep)` both read. In a multi-token meta the first
# token is the host and is never read as a mode, so `(deep-box)` and even a host
# literally named `deep` in `(deep, <mode>)` can't satisfy the check by accident.
SWEEP_MODES = ("quick", "deep")


def sweep_mode(meta: str | None) -> str | None:
    """'quick' | 'deep' from a stamp's meta, or None when it claims neither."""
    if not meta:
        return None
    tokens = [tok.strip().lower() for tok in meta.split(",")]
    # the mode is a standalone token; the first token is the host and is never a mode
    for tok in tokens[1:] if len(tokens) > 1 else tokens:
        if tok in SWEEP_MODES:
            return tok
    return None


def sweep_note(content: str, today: date) -> str | None:
    """Staleness warning for the board's Swept: stamp, or None when fresh."""
    m = SWEEP_RE.search(content)
    if m is None:
        return "⚠ never swept (no Swept: header) — run /stranded-work in this repo (cadence.md §3)"
    try:
        swept_on = datetime.strptime(m.group("date"), "%Y-%m-%d").date()  # noqa: DTZ007  # local civil dates by design (TD-15)
    except ValueError:
        return "⚠ unparseable Swept: header — fix the date or re-run /stranded-work"
    age = (today - swept_on).days
    if age < -1:
        # future-dated stamp (typo'd year, badly wrong clock) would otherwise read
        # as fresh forever — the exact silent-health illusion the stamp exists to
        # kill. One day of tolerance: a host in a timezone ahead of the reader can
        # legitimately stamp "tomorrow" for up to ~21 hours (TD-15).
        return f"⚠ Swept: date is in the future ({m.group('date')}) — fix the stamp"
    if age > SWEEP_STALE_DAYS:
        meta = f", {m.group('meta')}" if m.group("meta") else ""
        return f"⚠ last sweep {age}d ago ({m.group('date')}{meta}) — run /stranded-work in this repo"
    # Checked AFTER staleness on purpose: a stale stamp's fix (re-run the sweep)
    # also fixes a missing marker, so the more urgent line is the one to show.
    # A date alone is not a coverage claim — without this, `Swept: <today>` with no
    # mode read as full health, which is the illusion TD-12 exists to remove.
    if sweep_mode(m.group("meta")) is None:
        return (f"⚠ Swept: {m.group('date')} claims no quick|deep mode — re-run "
                "/stranded-work so the stamp records the coverage it actually had")
    # TD-13: deep coverage has its own slot and threshold. One `Swept:` slot let
    # a quick sweep overwrite the record of the last deep one, so a
    # permanently-quick repo read as fully healthy while the transcript scan —
    # the thing that recovers closed-session work — was deferred forever with
    # no signal. Checked only after the main stamp reads healthy: the more
    # urgent warning wins, and re-running the sweep refreshes both.
    deep_on = None
    dm = SWEEP_DEEP_RE.search(content)
    if dm is not None:
        try:
            deep_on = datetime.strptime(dm.group("date"), "%Y-%m-%d").date()  # noqa: DTZ007  # local civil dates by design (TD-15)
        except ValueError:
            return "⚠ unparseable Swept-deep: header — fix the date or re-run /stranded-work deep"
        deep_age = (today - deep_on).days
        if deep_age < -1:
            return f"⚠ Swept-deep: date is in the future ({dm.group('date')}) — fix the stamp"
    # A Swept: stamp whose own mode is deep is a deep sweep on record — the
    # second header only exists to PRESERVE that recency, so its absence next
    # to a fresh deep-mode stamp (a pre-TD-13 writer) must not warn "never".
    if sweep_mode(m.group("meta")) == "deep" and (deep_on is None or swept_on > deep_on):
        deep_on = swept_on
    if deep_on is None:
        return ("⚠ no deep sweep on record (quick sweeps only) — the transcript scan that "
                "recovers closed-session work has not run; run /stranded-work deep (cadence.md §3)")
    if (today - deep_on).days > SWEEP_DEEP_STALE_DAYS:
        return (f"⚠ last deep sweep {(today - deep_on).days}d ago — quick sweeps since keep the "
                "board fresh but do not scan transcripts; run /stranded-work deep")
    return None


def registry_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "dev-cadence" / "repos.txt"


def read_registry() -> list[Path] | None:
    """Canonicalized, deduped roster entries; None when no registry file exists.

    Read-time canonicalization + dedupe is the defense-in-depth half of the
    registry contract (writers canonicalize too) — spec: cadence.md §Machine
    scope. Entries are returned even if the path is gone; callers degrade
    per-entry with a note rather than dropping rows silently.
    """
    reg = registry_path()
    if not reg.is_file():
        return None
    entries: list[Path] = []
    seen: set[str] = set()
    for raw in reg.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        p = Path(line).resolve()
        if str(p) not in seen:
            seen.add(str(p))
            entries.append(p)
    return entries


FETCH_TIMEOUT = 30.0  # s per repo fetch (plan P2.5 hang-proofing)
# --due-only --fetch (TD-030): the SessionStart hook line has a 10 s harness
# timeout in already-seeded consumers, so the aggregate fetch budget must land
# under it with room for python startup and the per-repo git show/merge-base
# calls. Exhaustion degrades to "fetch skipped" per remaining repo, never to a
# hung session start. Env override exists for the test suite.
DUE_FETCH_BUDGET = float(os.environ.get("ATTENTION_DUE_FETCH_BUDGET", "8"))


@dataclass
class FetchResult:
    """Outcome of one repo's origin comparison (TD-030).

    note     — the human line for the full report.
    content  — when set, the board text to READ instead of the local file:
               origin's copy, used only when the local board carries nothing
               origin lacks (see _fetch_board). None = read the local file.
    source   — the ref the content came from (e.g. "origin/main"), for labels.
    """
    note: str
    content: str | None = None
    source: str | None = None


def _fetch_board(root: Path, board: Path, deadline: float | None = None) -> FetchResult:
    """Opt-in origin comparison for one repo; NEVER hangs, never raises.

    Mandatory hang-proofing (plan P2.5): GIT_TERMINAL_PROMPT=0 + BatchMode ssh
    with a 5 s connect timeout, FETCH_TIMEOUT subprocess cap on the fetch and
    10 s on each local call — ALL of them further clipped to the remaining
    aggregate ``deadline`` when one is given — and every failure path degrades
    to a 'fetch skipped' note. A board that is missing locally is fine: it
    reads as "" and the compare decides whether origin's copy is the answer.

    Three-way compare (TD-030): local working-tree board vs the board blob at
    origin/<default> vs the blob at their merge-base. Branch-independent —
    whatever branch or detached state the repo is on.
      local == origin            → matches
      local == base, origin ≠    → origin moved and local is untouched: read
                                   ORIGIN's board (content set), say so. This is
                                   the service-account append case — the line
                                   exists only on origin until someone pulls.
      origin == base, local ≠    → local edits not pushed: read local, say so.
      all three differ           → diverged: read local, flag it (no safe
                                   automatic answer).
    Reading origin's blob never mutates the clone beyond the fetch itself — a
    report must not fast-forward repos it is only inspecting.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5",
    }

    def git(args: list[str], cap: float) -> subprocess.CompletedProcess:
        # Every call — the network fetch AND the local show/merge-base calls —
        # is clipped to the remaining aggregate deadline, so a wedged .git
        # cannot push --due-only past its budget any more than a dead remote can.
        timeout = cap
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0.05:
                raise subprocess.TimeoutExpired(args, 0)
        return subprocess.run(["git", "-C", str(root), *args],
                              check=False, capture_output=True, timeout=timeout, env=env)

    def blob(ref: str, rel: str) -> str | None:
        r = git(["show", f"{ref}:{rel}"], 10)
        return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None

    try:
        r = git(["fetch", "-q", "--prune", "origin"], FETCH_TIMEOUT)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
            return FetchResult(f"fetch skipped ({err[-1] if err else 'git fetch failed'})")
        d = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], 10)
        dflt = d.stdout.decode().strip().removeprefix("origin/") if d.returncode == 0 and d.stdout.strip() else "main"
        ref = f"origin/{dflt}"
        rel = (board.relative_to(root) if board.is_relative_to(root) else Path("docs/user_attention.md")).as_posix()
        origin = blob(ref, rel)
        if origin is None:
            return FetchResult(f"fetched; no board at {ref}")
        local = board.read_text(encoding="utf-8", errors="replace") if board.is_file() else ""
        if origin == local:
            return FetchResult(f"fetched; board matches {ref}")
        mb = git(["merge-base", "HEAD", ref], 10)
        if mb.returncode != 0:
            return FetchResult(f"fetched; board DIFFERS from {ref} (no common history to compare) — pull/push; showing local")
        base = blob(mb.stdout.decode().strip(), rel) or ""
        if local == base:
            return FetchResult(
                f"fetched; local clone is behind — showing {ref}'s board (pull to catch up)",
                content=origin, source=ref)
        if origin == base:
            return FetchResult(f"fetched; board DIFFERS from {ref} (local edits not pushed) — push for the cross-machine view")
        return FetchResult(f"fetched; board DIFFERS from {ref} (both sides changed) — pull/push; showing local")
    except subprocess.TimeoutExpired:
        if deadline is not None and time.monotonic() >= deadline - 0.05:
            return FetchResult("fetch skipped (budget exhausted)")
        return FetchResult("fetch skipped (timeout)")
    except Exception as e:  # noqa: BLE001 — a dead report row beats a dead report
        return FetchResult(f"fetch skipped ({e})")


# --- Remote board tier (TD-6, plan 2026-08-13) -------------------------------
# Budget convention mirrors TD-10's GIT_TIMEOUT/GIT_BUDGET in
# check_claude_memory.sh: a per-call cap and an aggregate per-run cap, both
# named constants. Unlike the SessionStart hook (where exhaustion degrades to
# silence), --remote is a user-invoked report, so exhaustion prints an
# incomplete-note — never a stuck report, never a fake all-clear. The env
# overrides exist for the test suite only.
GH_TIMEOUT = float(os.environ.get("ATTENTION_GH_TIMEOUT", "10"))    # s per gh call
GH_BUDGET = float(os.environ.get("ATTENTION_GH_BUDGET", "60"))     # s aggregate
GH_LIST_LIMIT = int(os.environ.get("ATTENTION_GH_LIST_LIMIT", "500"))


def _remote_filter_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "dev-cadence" / "remote_repos.txt"


def _read_remote_filter() -> tuple[list[str], list[str]]:
    """(owners, owner/repo entries) from the optional machine-local filter.

    Missing file OR a file with no entries both mean "no filter" (D1 as
    amended 2026-08-13): full discovery, user + orgs. Same comment/blank
    conventions as repos.txt.
    """
    f = _remote_filter_path()
    if not f.is_file():
        return [], []
    owners: list[str] = []
    repos: list[str] = []
    for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        (repos if "/" in line else owners).append(line)
    return owners, repos


def _gh(args: list[str], spent: dict) -> tuple[int, str, str]:
    """One budgeted gh call. Returns (rc, stdout, stderr); rc -1 = timeout,
    rc -2 = budget already exhausted (call not attempted), rc -3 = gh missing.
    Books elapsed time into spent["t"] in a finally so timeouts count too."""
    remaining = GH_BUDGET - spent["t"]
    if remaining <= 0:
        spent["hit"] = True
        return -2, "", "remote-tier budget exhausted"
    cap = min(GH_TIMEOUT, remaining)   # the budget may truncate below GH_TIMEOUT
    t0 = time.monotonic()
    try:
        r = subprocess.run(["gh", *args], check=False, capture_output=True,
                           text=True, timeout=cap)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -3, "", "gh not installed"
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {cap:g}s"
    finally:
        spent["t"] += time.monotonic() - t0


def _local_origin(root: Path) -> str | None:
    """Normalized (anonymous-https, lowercased) origin URL of a local repo."""
    try:
        r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                           check=False, capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 — a missing origin just means no dedup match
        return None
    if r.returncode != 0:
        return None
    url = _normalize_source_url(r.stdout.strip())
    return url.lower() if url else None


def remote_tier(local_roots: list[Path], today: date) -> list[str]:
    """Render the remote-only tier (TD-6): discover boards on GitHub that no
    local row covers. Returns printable lines; every failure mode degrades to
    a ⚠ note — a per-repo failure must never ride the same silent path as a
    genuine 404, and discovery failure must never render as "no remote boards".
    """
    lines: list[str] = ["-- Remote tier (GitHub, pushed state only) --"]
    spent: dict = {"t": 0.0, "hit": False}

    owners, direct = _read_remote_filter()
    if not owners and not direct:
        rc, out, err = _gh(["api", "user", "--jq", ".login"], spent)
        if rc != 0:
            reason = "gh not installed" if rc == -3 else (err.strip().splitlines() or ["gh api user failed"])[-1]
            lines.append(f"⚠ remote tier unavailable ({reason})")
            return lines
        owners = [out.strip()]
        rc, out, err = _gh(["api", "user/orgs", "--jq", ".[].login"], spent)
        if rc == 0:
            owners += [o for o in out.split() if o]
        else:
            reason = (err.strip().splitlines() or ["org listing failed"])[-1]
            lines.append(f"⚠ org discovery failed ({reason}) — org repos not covered")

    candidates: list[str] = list(direct)
    for owner in owners:
        rc, out, err = _gh(["repo", "list", owner, "--json", "nameWithOwner,isArchived",
                            "--limit", str(GH_LIST_LIMIT)], spent)
        if rc != 0:
            reason = "budget exhausted" if rc == -2 else (err.strip().splitlines() or ["repo list failed"])[-1]
            lines.append(f"⚠ repo list failed for {owner} ({reason}) — that owner not covered")
            continue
        try:
            listed = json.loads(out)
        except ValueError:
            lines.append(f"⚠ repo list unparseable for {owner} — that owner not covered")
            continue
        if len(listed) >= GH_LIST_LIMIT:
            lines.append(f"⚠ repo list truncated at {GH_LIST_LIMIT} for {owner} — remote coverage incomplete")
        candidates += [r["nameWithOwner"] for r in listed if not r.get("isArchived")]

    local = {u for u in (_local_origin(r) for r in local_roots) if u}
    seen: set[str] = set()
    probes = [c for c in candidates
              if not (c in seen or seen.add(c))                      # order-preserving dedup
              and f"https://github.com/{c}".lower() not in local]    # local rows win (fresher)

    boards: list[tuple[str, str]] = []
    unprobeable: list[tuple[str, str]] = []
    probed = 0
    for name in sorted(probes):
        rc, out, err = _gh(["api", f"repos/{name}/contents/docs/user_attention.md",
                            "-H", "Accept: application/vnd.github.raw"], spent)
        if rc == -2:
            break
        probed += 1
        if rc == 0:
            boards.append((name, out))
        elif "HTTP 404" in err:
            continue                                # no board — silent by design
        else:
            reason = "gh not installed" if rc == -3 else (err.strip().splitlines() or ["probe failed"])[-1]
            unprobeable.append((name, reason))

    if spent["hit"]:
        lines.append(f"⚠ remote tier incomplete (budget exhausted after {probed} of {len(probes)} repos)")
    if unprobeable:
        n, (first, why) = len(unprobeable), unprobeable[0]
        lines.append(f"⚠ {n} repo(s) unprobeable (first: {first}: {why})")

    lines[0] = (f"-- Remote tier (GitHub, pushed state only) — {len(boards)} remote-only "
                f"board(s) across {probed} repo(s) probed --")
    for name, content in boards:
        lines.append("")
        lines.append(f"== {name} (remote-only, pushed state)")
        items = parse_board(content, warn=False)
        if items:
            for item in sorted(items, key=lambda i: _report_sort_key(i, today)):
                lines.append(f"  • ({due_tag(item, today)}) {_clip(item.text)}")
        else:
            lines.append("  (no open items)")
    return lines


def report(boards_cli: list[str], fetch: bool, due_only: bool = False, remote: bool = False) -> int:
    """Machine-wide attention report to stdout. Report mode never delivers:
    deliver()/send_*() are unreachable from here regardless of environment.

    due_only (TD-7): the SessionStart wake-up line — only due/overdue items,
    one compact line each, and NOTHING when nothing is due, so a clean roster
    adds zero context noise. Row-level problems (missing boards, bad roster
    paths) are deliberately silent here; the full report is where they show."""
    today = date.today()  # noqa: DTZ011  # local civil dates by design (TD-15: one-day skew tolerated at the sweep comparisons)
    # (label, board_path or None, repo_root or None, note)
    rows: list[tuple[str, Path | None, Path | None, str | None]] = []
    # Board paths that are absent on disk, by row index — still fetchable
    # (TD-030): a clone that predates the board's first commit, or a locally
    # deleted board, has origin's copy as the only readable one.
    missing: dict[int, Path] = {}
    if boards_cli:
        for b in boards_cli:
            board = Path(b).resolve()
            root = board.parent.parent if board.parent.name == "docs" else board.parent
            if not board.is_file():
                missing[len(rows)] = board
            rows.append((root.name, board if board.is_file() else None, root,
                         None if board.is_file() else f"missing board file ({board})"))
    else:
        entries = read_registry()
        if entries is None:
            if due_only and not any(p.is_file() for p in _default_boards()):
                # SessionStart hook path (TD-7): a repo with no board yet has
                # nothing due — exit 0 with NO output and no ERROR log, per the
                # hooks-never-break-a-session rule (cadence.md §1).
                return 0
            board = resolve_board(None)
            if board is None:
                return 1
            root = board.resolve().parent.parent
            rows.append((root.name, board, root, "no machine roster — showing this checkout's board only"))
        else:
            for root in entries:
                if not root.is_dir():
                    rows.append((root.name, None, None, f"roster path missing ({root})"))
                    continue
                if not (root / ".git").exists():
                    rows.append((root.name, None, None, f"roster path is not a git repo ({root})"))
                    continue
                board = root / "docs" / "user_attention.md"
                if not board.is_file():
                    missing[len(rows)] = board
                rows.append((root.name, board if board.is_file() else None, root,
                             None if board.is_file() else "no board (docs/user_attention.md not found)"))

    # TD-030: with --fetch, each row's board may be read from origin/<default>
    # when the local clone is merely behind. Full report: no aggregate budget
    # (serial, FETCH_TIMEOUT per repo, as before). --due-only: hard aggregate
    # budget so the SessionStart hook never waits on the network for long.
    deadline = time.monotonic() + DUE_FETCH_BUDGET if (fetch and due_only) else None
    fetched: dict[int, FetchResult] = {}
    if fetch:
        for i, (label, board, root, note) in enumerate(rows):
            bpath = board if board is not None else missing.get(i)
            if bpath is None or root is None:
                continue
            fr = _fetch_board(root, bpath, deadline)
            fetched[i] = fr
            if board is None and fr.content is not None:
                # Missing locally, present on origin: origin's copy IS the row.
                rows[i] = (label, bpath, root, None)

    def read_board(i: int, board: Path) -> str:
        fr = fetched.get(i)
        if fr is not None and fr.content is not None:
            return fr.content
        return board.read_text(encoding="utf-8", errors="replace")

    if due_only:
        due_lines: list[str] = []
        for i, (label, board, _root, _note) in enumerate(rows):
            if board is None:
                continue
            try:
                content = read_board(i, board)
            except OSError:
                continue
            fr = fetched.get(i)
            tag = f" [{fr.source} — local clone behind, pull]" if fr is not None and fr.source else ""
            for item in parse_board(content, warn=False):
                if item.overdue_days(today) is not None:
                    due_lines.append(f"  • {label}{tag}: ({due_tag(item, today)}) {_clip(item.text)}")
        if due_lines:
            print(f"⚠ {len(due_lines)} attention item(s) due across this machine's repos — "
                  "run /attention for the full report (cadence.md §3):")
            for ln in due_lines:
                print(ln)
        return 0

    sections: list[str] = []
    total_items = 0
    total_due = 0
    n_boards = 0
    for i, (label, board, root, note) in enumerate(rows):
        fr = fetched.get(i)
        head = f"== {label}" + (f" ({board})" if board else "")
        if fr is not None and fr.source:
            head += f" — showing {fr.source}"
        body: list[str] = []
        if board is not None:
            try:
                # errors="replace": one mis-encoded board must degrade to mojibake in
                # its own row, never crash the whole multi-repo report (review finding)
                content = read_board(i, board)
            except OSError as e:
                body.append(f"  ⚠ unreadable board: {e}")
                content = None
            if content is not None:
                n_boards += 1
                items = parse_board(content, warn=False)
                total_items += len(items)
                total_due += sum(1 for i in items if i.overdue_days(today) is not None)
                if items:
                    for item in sorted(items, key=lambda i: _report_sort_key(i, today)):
                        body.append(f"  • ({due_tag(item, today)}) {_clip(item.text)}")
                else:
                    body.append("  (no open items)")
                # Deliberately OUTSIDE the open-items gate (TD-14, decided
                # 2026-08-12): the sweep covers PRs, worktrees, unpushed
                # commits, and memories — an empty board proves nothing about
                # those, so staleness warns unconditionally.
                sweep = sweep_note(content, today)
                if sweep:
                    body.append(f"  {sweep}")
        if note:
            body.append(f"  note: {note}")
        if fr is not None:
            body.append(f"  {fr.note}")
            stale = staleness_line(board)
            if stale:
                body.append(f"  {stale}")
        sections.append("\n".join([head, *body]))

    print(f"Attention report — {n_boards} board(s), {total_items} open item(s), {total_due} due/overdue")
    for s in sections:
        print()
        print(s)
    if remote:
        # Dedup compares against the rows actually rendered above, however
        # they were sourced (registry or explicit --board) — plan D-amendment.
        print()
        for ln in remote_tier([r for _, _, r, _ in rows if r is not None], today):
            print(ln)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", action="append",
                    help="Path to user_attention.md (default: $USER_ATTENTION_BOARD, then this checkout). "
                         "Repeatable in --report mode; nudge mode takes at most one.")
    ap.add_argument("--dry-run", action="store_true", help="Print the message instead of sending")
    ap.add_argument("--force-weekly", action="store_true", help="Include the weekly summary regardless of weekday")
    ap.add_argument("--report", action="store_true",
                    help="Print a machine-wide attention report to stdout (no delivery) and exit. "
                         "--dry-run/--force-weekly are nudge-mode flags and are ignored here (a report "
                         "is already a dry run and always shows the full board).")
    ap.add_argument("--fetch", action="store_true",
                    help="With --report: also fetch each repo's origin (serial, hang-proofed) and compare boards; "
                         "a clone that is merely behind has its board read from origin/<default> (TD-030). "
                         "With --due-only: bounded by ATTENTION_DUE_FETCH_BUDGET (default 8 s) in aggregate.")
    ap.add_argument("--remote", action="store_true",
                    help="With --report: additionally discover boards on GitHub (gh CLI) that no "
                         "local row covers — the cross-machine tier (TD-6). Implies --fetch so the "
                         "freshness story stays coherent. Pushed state only.")
    ap.add_argument("--due-only", action="store_true",
                    help="With --report: print only due/overdue items, compactly; SILENT when none "
                         "are due (TD-7: the SessionStart wake-up line — offline, no delivery)")
    args = ap.parse_args()

    if args.fetch and not args.report:
        ap.error("--fetch requires --report")
    if args.remote and not args.report:
        ap.error("--remote requires --report")
    if args.due_only and not args.report:
        ap.error("--due-only requires --report")
    if args.report:
        return report(args.board or [], args.fetch or args.remote, args.due_only, args.remote)
    if args.board and len(args.board) > 1:
        ap.error("nudge mode takes at most one --board (use --report for a multi-board view)")

    board = resolve_board(args.board[0] if args.board else None)
    if board is None:
        return 1
    today = date.today()  # noqa: DTZ011  # local civil dates by design (TD-15: one-day skew tolerated at the sweep comparisons)
    weekly = args.force_weekly or today.weekday() == 0  # Monday
    items = parse_board(board.read_text(encoding="utf-8"))
    message = build_message(items, today, weekly)

    if message is None:
        logger.info("Board clean (%d open item(s), none due, not weekly) — no nudge.", len(items))
        return 0
    stale = staleness_line(board)
    if stale:
        message += "\n\n" + stale
    if args.dry_run:
        print(message)
        return 0
    sent = deliver(message)
    logger.info("Nudge %s (%d open item(s), board: %s)", "sent" if sent else "NOT sent", len(items), board)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
