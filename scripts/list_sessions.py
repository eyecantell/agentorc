#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/list_sessions.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""Index of Claude Code sessions for one or more repos — richer than /resume's blurbs.

Scans the JSONL transcripts under ~/.claude/projects/ for each given repo's
project dir AND its worktree variants, and prints one block per session: id,
start, last activity, git branch, how it STARTED (first real user message), how
it ENDED (last assistant text), the latest compaction RECAP and session slug
when present (more relevant when a session starts/ends with a slash command or
harness noise), an ACTIVE-NOW flag from the live registry (~/.claude/sessions/),
the directory the session ran from (the repo checkout or its worktree — this is
what distinguishes repos in a combined index), and the session's open items on
that repo's docs/user_attention.md (flagged, with the item text listed in the
session's details).

The index is generated on demand from the transcripts — never maintained by
hand — so it is always current and covers crashed/disconnected sessions too.

Usage:
    list_sessions.py [--repo PATH]... [--roster] [--mentioning PATH]... [--limit N] [--days N]
                     [--chars N] [--format text|md]

--repo is repeatable: pass each repo that runs sessions on this machine to get
one combined, recency-sorted index (e.g. --repo ~/samscrape --repo ~/contractmatch).
--roster adds every repo on this machine's roster (cadence.md §9).

--mentioning PATH (repeatable, TD-28) keeps only the sessions whose transcript
names that repo's path (absolute, or ~/-relative) — work on repo A routinely
happens from a session running in repo B, and its transcript lives under B.
`--roster --mentioning <this repo>` lists the candidates from every repo on the
machine. A path in a transcript is a heuristic: it surfaces candidates for a
person or the sweeping session to judge; it decides nothing.

--format text (default) prints terminal-friendly blocks; --format md renders a
markdown overview table plus per-session detail sections (what the
generate_session_index.sh cron wrapper writes to docs/session_index.md).

Resume a listed session with:  claude --resume <session-uuid>
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def munge(path: Path) -> str:
    """Path -> Claude Code's project-dir name.

    Claude Code replaces every non-alphanumeric character with '-', not just
    '/' and '.'. Spelling out only those two silently loses any repo whose path
    contains '_' (or anything else): /workspaces/work_history lives in
    ~/.claude/projects/-workspaces-work-history, and the two-replace version
    looked for -workspaces-work_history, found nothing, and reported "no
    project dirs" — indistinguishable from a repo that genuinely has no
    sessions. The character class is a superset of the old behavior ('-' maps
    to itself), so it changes nothing for paths that already worked.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path))


def short_path(p: Path) -> str:
    """Home-relative display form (~/repo, ~/repo/.claude/worktrees/x)."""
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.startswith(home + "/") else s


def project_dirs(repo: Path) -> list[Path]:
    """The repo's own project dir plus worktree-variant dirs."""
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return []
    dirs = []
    exact = base / munge(repo)
    if exact.is_dir():
        dirs.append(exact)
    # worktrees live under <repo>/.claude/worktrees/<name> → munged prefix match
    wt_prefix = munge(repo) + "--claude-worktrees-"
    dirs += [d for d in base.iterdir() if d.is_dir() and d.name.startswith(wt_prefix)]
    return dirs


# Board items name the session that parked them — `(session <first-8-of-uuid> on <host>)`,
# the Format line of docs/user_attention.md. A parity pair with that line (cadence.md §7
# Parity pairs; tests/test_parity.sh runs the Format line's example through this).
SESSION_RE = re.compile(r"session (\w{8})")

RECAP_BOILERPLATE = re.compile(
    r"^.{0,40}(This session is being continued from a previous conversation[^:]*:|"
    r"The conversation is summarized below:?)\s*", re.DOTALL)


def _first_text(content) -> str:
    """Plain text from a message content field (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return ""


def _is_real_user_text(text: str) -> bool:
    if not text.strip():
        return False
    # skip harness noise: slash-command wrappers, hook output, interrupts
    return not re.match(r"\s*(<command-|<local-command|<system-remind|<task-notification|\[SYSTEM NOTIFICATION|\[Request interrupted)", text)


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def scan_session(path: Path, chars: int) -> dict | None:
    """Single pass over the transcript; substring-gate lines before JSON-parsing."""
    info = {
        "id": path.stem,
        "file": path,
        "last": None,
        "start": None,
        "branch": None,
        "slug": None,
        "first_user": "",
        "last_assistant": "",
        "summary": "",
        "recap": "",
    }
    try:
        info["last"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None  # gone between the glob and now (a session exiting): not an error
    last_assistant_line = None
    last_recap_line = None
    head_budget = 200  # head fields live in the first lines; don't full-parse huge slug-less files
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                obj = None
                if head_budget > 0 and (info["start"] is None or info["branch"] is None or info["slug"] is None):
                    head_budget -= 1
                    obj = _try_json(line)
                    if obj:
                        info["start"] = info["start"] or obj.get("timestamp")
                        info["branch"] = info["branch"] or obj.get("gitBranch")
                        info["slug"] = info["slug"] or obj.get("slug")
                if '"type":"summary"' in line:
                    obj = obj or _try_json(line)
                    if obj and obj.get("type") == "summary":
                        info["summary"] = obj.get("summary", info["summary"])
                if '"isCompactSummary":true' in line:
                    last_recap_line = line
                if not info["first_user"] and '"type":"user"' in line:
                    obj = obj or _try_json(line)
                    if obj and obj.get("type") == "user" and not obj.get("isCompactSummary"):
                        text = _first_text(obj.get("message", {}).get("content"))
                        if _is_real_user_text(text):
                            info["first_user"] = _clip(text, chars)
                if '"type":"assistant"' in line and '"type":"text"' in line:
                    last_assistant_line = line
                if info["slug"] is None and '"slug":"' in line:
                    obj = obj or _try_json(line)
                    if obj:
                        info["slug"] = obj.get("slug")
    except OSError:
        return None

    for raw, key, extract in (
        (last_assistant_line, "last_assistant", lambda o: _first_text(o.get("message", {}).get("content"))),
        (last_recap_line, "recap", lambda o: _first_text(o.get("message", {}).get("content"))),
    ):
        if raw:
            obj = _try_json(raw)
            if obj:
                text = extract(obj)
                if key == "recap":
                    text = RECAP_BOILERPLATE.sub("", text)
                if text.strip():
                    info[key] = _clip(text, chars)

    if not (info["first_user"] or info["last_assistant"] or info["recap"]):
        return None  # empty/degenerate session file
    return info


def _try_json(line: str):
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def active_sessions() -> dict[str, str]:
    """sessionId -> cwd for currently-running sessions (live registry).

    Liveness is check_anchor.live_sessions()'s, not a second test of its own: bare
    /proc/<pid> existence manufactures false liveness under a bind-mounted
    ~/.claude (a reused low pid), and the index then said "already running" for a
    dead session nobody resumed (TD-043). The naive test below is only the
    fallback for an install without check_anchor.py beside this script.
    """
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from check_anchor import live_sessions
    except ImportError:
        live_sessions = None
    if live_sessions is not None:
        return {s["sessionId"]: s.get("cwd", "") for s in live_sessions()}
    reg = Path.home() / ".claude" / "sessions"
    live = {}
    if not reg.is_dir():
        return live
    for f in reg.glob("*.json"):
        try:
            obj = _try_json(f.read_text(encoding="utf-8", errors="replace") or "")
        except OSError:
            continue  # the session exited and removed its entry since the glob
        if not obj or "sessionId" not in obj:
            continue
        pid = obj.get("pid")
        if pid and not Path(f"/proc/{pid}").exists():
            continue  # stale registry entry
        live[obj["sessionId"]] = obj.get("cwd", "")
    return live


def board_items(repo: Path) -> dict[str, list[str]]:
    """Open items on docs/user_attention.md keyed by the 8-char session id they cite."""
    board = repo / "docs" / "user_attention.md"
    if not board.is_file():
        return {}
    items: dict[str, list[str]] = {}
    try:
        board_text = board.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in board_text.splitlines():
        if not re.match(r"\s*-\s*\[ \]", line):
            continue  # only unchecked (still-open) board entries
        text = re.sub(r"^\s*-\s*\[ \]\s*", "", " ".join(line.split()))
        for sid in SESSION_RE.findall(line):
            items.setdefault(sid, []).append(text)
    return items


def _title(s: dict) -> str:
    return s["id"][:8] + (f" — {s['slug']}" if s["slug"] else "")


def _anchor(title: str) -> str:
    """GitHub/VS Code-style anchor slug for a heading (sufficient for our titles)."""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def _session_flags(s: dict, live: dict, parked: dict) -> list[str]:
    """Status flags shared by both renderers (location is rendered separately)."""
    flags = []
    if s["id"] in live:
        flags.append("ACTIVE NOW")
    if s["id"][:8] in parked:
        flags.append("PARKED ITEMS ON BOARD")
    if s["branch"] and s["branch"] not in ("main", ""):
        flags.append(f"branch:{s['branch']}")
    return flags


def _resume(s: dict, live: dict) -> str:
    return "already running — switch to it" if s["id"] in live else f"claude --resume {s['id']}"


def _times(s: dict) -> tuple[str, str]:
    return (s["start"] or "?")[:16].replace("T", " "), s["last"].strftime("%Y-%m-%d %H:%M")


def print_text(sessions: list[dict], live: dict, parked: dict) -> None:
    for s in sessions:
        started, last = _times(s)
        flags = _session_flags(s, live, parked)
        branch = [f for f in flags if f.startswith("branch:")]
        flags = [f for f in flags if not f.startswith("branch:")] + [s["where"]] + branch
        flag_str = f"  [{', '.join(flags)}]"
        slug = f" ({s['slug']})" if s["slug"] else ""
        print(f"● {s['id']}{slug}{flag_str}")
        print(f"  {started} → {last} UTC")
        if s["summary"]:
            print(f"  summary: {s['summary']}")
        if s["recap"]:
            print(f"  recap:   {s['recap']}")
        if s["first_user"]:
            print(f"  started: {s['first_user']}")
        if s["last_assistant"]:
            print(f"  ended:   {s['last_assistant']}")
        for item in parked.get(s["id"][:8], []):
            print(f"  parked:  {_clip(item, 200)}")
        print(f"  resume:  {_resume(s, live)}")
        print()
    print(f"{len(sessions)} session(s) shown. Flags: ACTIVE NOW (running), PARKED ITEMS ON BOARD (on user_attention.md), ~/… (directory the session ran from — repo checkout or worktree; may since have been removed).")


def _cell(text: str) -> str:
    """Make a string safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ") or "—"


def print_md(sessions: list[dict], live: dict, parked: dict) -> None:
    if not sessions:
        print("_No sessions found._")
        return
    print("| Session | Slug | Start (UTC) | Last active (UTC) | Where | Status |")
    print("|---|---|---|---|---|---|")
    for s in sessions:
        started, last = _times(s)
        flags = _session_flags(s, live, parked)
        status = _cell(", ".join(flags)) if flags else ""
        link = f"[`{s['id'][:8]}`](#{_anchor(_title(s))})"
        print(f"| {link} | {_cell(s['slug'] or '')} | {started} | {last} | {_cell(s['where'])} | {status} |")
    print()
    print("Status: **ACTIVE NOW** = running right now · **PARKED ITEMS ON BOARD** = has open items on"
          " that repo's `docs/user_attention.md` (listed under the session's details below) · Where ="
          " directory the session ran from (repo checkout or worktree; a removed worktree's path still"
          " identifies the repo). Session ids link to the details.")
    print()
    for s in sessions:
        started, last = _times(s)
        flags = _session_flags(s, live, parked)
        print(f"### {_title(s)}")
        print()
        meta = f"{started} → {last} UTC · {s['where']}"
        if flags:
            meta += " · " + " · ".join(f"**{f}**" for f in flags)
        print(meta)
        print()
        for label, key in (("summary", "summary"), ("recap", "recap"),
                           ("started", "first_user"), ("ended", "last_assistant")):
            if s[key]:
                print(f"- **{label}:** {s[key]}")
        for item in parked.get(s["id"][:8], []):
            print(f"- **parked (board item, needs action):** {item}")
        print(f"- **resume:** `{_resume(s, live)}`" if s["id"] not in live
              else "- **resume:** already running — switch to it")
        print()


def default_repo() -> str:
    """No --repo: the MAIN checkout of the repo the cwd is in (TD-053). "." from a worktree
    indexed that worktree's dir only, and from a subdirectory found no project dirs at all —
    and §1 puts nearly every session in a worktree."""
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return str(Path(r.stdout.strip()).parent)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "."


def roster_repos() -> list[Path]:
    """The machine roster's repos (cadence.md §9 path spec: canonicalized, deduped at
    read time; a missing roster is an empty list, a gone path is skipped)."""
    # the machine-scope dir as nudge_user_attention.machine_dir() resolves it (TD-029)
    reg = (Path(os.environ["DEV_CADENCE_REG_DIR"]) if os.environ.get("DEV_CADENCE_REG_DIR")
           else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dev-cadence") / "repos.txt"
    try:
        lines = reg.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[Path] = []
    for line in lines:
        line = line.split("#", 1)[0].strip()   # comments as nudge's read_registry() takes them
        if not line:
            continue
        p = Path(line).resolve()
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def mention_needles(spec: str) -> list[bytes]:
    """The spellings a transcript may use for a repo: the path as given and resolved
    (a symlinked checkout), each also ~/-relative, and — when the path is inside a git
    worktree — its main checkout's root, which is how other sessions usually name it."""
    given = Path(spec).expanduser().absolute()
    paths = [given, given.resolve()]
    try:
        r = subprocess.run(["git", "-C", str(given), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5, check=False)
        if r.returncode == 0 and r.stdout.strip():
            paths.append(Path(r.stdout.strip()).parent)
    except (OSError, subprocess.TimeoutExpired):
        pass
    out: list[bytes] = []
    for p in paths:
        for form in (str(p), short_path(p)):
            b = form.encode()
            if (form == str(p) or form.startswith("~/")) and b not in out:
                out.append(b)
    return out


def mentions(path: Path, needles: list[bytes]) -> bool:
    """Does the transcript name any needle AS A PATH — not as the prefix of a longer name
    (/a/repo must not match /a/repo2 or /a/repo.old, but "…in /a/repo." is a mention)?
    Read in chunks; transcripts are large."""
    pats = [re.compile(re.escape(n) + rb"(?![A-Za-z0-9_\-]|\.[A-Za-z0-9_\-])") for n in needles]
    keep = max(len(n) for n in needles)
    tail = b""
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                buf = tail + chunk
                if any(p.search(buf) for p in pats):
                    return True
                tail = buf[-keep:]
    except OSError:
        return False
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", dest="repos", metavar="PATH",
                    help="Repo path; repeatable — pass each repo running sessions on this machine "
                         "for one combined index (default: cwd)")
    ap.add_argument("--roster", action="store_true",
                    help="Also scan every repo on this machine's roster (cadence.md §9)")
    ap.add_argument("--mentioning", action="append", default=[], metavar="PATH",
                    help="Keep only sessions whose transcript names this repo's path; repeatable (TD-28)")
    ap.add_argument("--limit", type=int, default=20, help="Max sessions to show (default 20)")
    ap.add_argument("--days", type=int, default=0, help="Only sessions active in the last N days (0 = all)")
    ap.add_argument("--chars", type=int, default=160, help="Blurb length (default 160)")
    ap.add_argument("--format", choices=("text", "md"), default="text",
                    help="Output format: text for terminals (default), md for a markdown index file")
    args = ap.parse_args()

    repos = [Path(r).resolve() for r in (args.repos or ([] if args.roster else [default_repo()]))]
    if args.roster:
        repos += [r for r in roster_repos() if r not in repos]
        if not repos:
            print("No repos: the machine roster is empty or missing, and no --repo was given.")
            return 1
    needles: list[bytes] = []
    for m in args.mentioning:
        needles += [n for n in mention_needles(m) if n not in needles]
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.days) if args.days else None

    sessions = []
    scanned = 0  # transcript files read, for the TD-050 canary below
    parked: dict[str, list[str]] = {}
    missing = []
    marker = "--claude-worktrees-"
    for repo in repos:
        dirs = project_dirs(repo)
        if not dirs:
            missing.append(repo)
            continue
        for d in dirs:
            wt_name = d.name.split(marker, 1)[1] if marker in d.name else None
            # NB wt_name comes from the munged project-dir name, so dots in a worktree
            # name display as dashes — display-only, and the path still names the repo.
            run_dir = repo / ".claude" / "worktrees" / wt_name if wt_name else repo
            for f in d.glob("*.jsonl"):
                # --days by mtime BEFORE reading anything: across a roster, reading every
                # transcript in full only to drop most of them is the expensive part
                try:
                    if cutoff and datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < cutoff:
                        continue
                except OSError:
                    continue  # gone since the glob (a session exiting) — TD-053's race, here too
                if needles and not mentions(f, needles):
                    continue
                scanned += 1
                s = scan_session(f, args.chars)
                if s:
                    s["where"] = short_path(run_dir)
                    sessions.append(s)
        for sid, items in board_items(repo).items():
            parked.setdefault(sid, []).extend(items)
    if len(missing) == len(repos):
        print(f"No Claude Code project dirs found under ~/.claude/projects/ for: "
              f"{', '.join(str(r) for r in repos)}")
        return 1
    for repo in missing:
        print(f"note: no Claude Code project dirs found for {repo} under ~/.claude/projects/ — skipped.",
              file=sys.stderr)

    if scanned and not sessions:
        # TD-050 canary: transcripts are an undocumented Claude Code format (verified
        # against 2.1.280); files present and none readable is a format change more often
        # than an empty history — say so rather than print an empty index
        print(f"note: {scanned} transcript file(s) found and none had a readable user or assistant "
              "message — they hold only commands, or Claude Code's transcript format has changed "
              "(verified against 2.1.280).", file=sys.stderr)
    sessions.sort(key=lambda s: s["last"], reverse=True)
    if cutoff:
        sessions = [s for s in sessions if s["last"] >= cutoff]
    sessions = sessions[: args.limit]

    live = active_sessions()
    if args.format == "md":
        print_md(sessions, live, parked)
    else:
        print_text(sessions, live, parked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
