#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/check_claude_memory.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# check_claude_memory.sh — guard against silently losing Claude Code auto-memory.
#
# Failure modes this catches:
#   1. autoMemoryDirectory misconfigured / not active on this machine or session
#      → Claude silently falls back to ~/.claude/projects/<encoded-cwd>/memory/
#        and writes memory THERE (outside the repo, never committed → lost).
#   2. New memory written to the repo dir but not yet committed/pushed (not backed up).
#   3. core.hooksPath not set in this clone → the pre-push main guard is silently
#      OFF (both are per-clone/per-machine settings that a fresh machine lacks).
#   4. The memory directory isn't version-controlled at all (autoMemoryDirectory
#      points outside any git repo) → memory is machine-local and dies with it.
#
# The directory checked is whatever `autoMemoryDirectory` names (project-local
# settings first, then user settings), falling back to docs/claude-memory when
# unset — checking a hardcoded path would let this guard report OK about a
# directory Claude Code never writes to.
#
# PARITY (cadence.md §7): the stranded-work skill's memory check resolves the
# SAME setting in the SAME order, for the same reason. Change them together —
# when this resolution landed here and not there, the sweep scanned a
# nonexistent docs/claude-memory and reported 0 memories clean while 34 sat in
# the directory the setting names.
#
# Usage:
#   scripts/check_claude_memory.sh          # standalone: prints OK or warnings
#   scripts/check_claude_memory.sh --hook   # SessionStart hook: stdout → session context
#
# As a SessionStart hook its stdout is injected into the session (exit 0, non-blocking),
# so Claude sees the warning at the top of the next session. It is silent on success in
# --hook mode to avoid adding noise/tokens every session.
#
# BUDGET (TD-10): the three SessionStart hooks together target < 1s on the
# common path (measured 2026-08-12, 3-repo roster: this guard 0.16s + anchor
# 0.04s + due-line 0.10s). Each declares a hard-stop `timeout` in the settings
# template — this guard 20s, anchor 20s, due-line 10s — sized for pathological
# filesystems (stale NFS, a spun-down disk), not for growth. A new check must
# fit the same envelope: short-circuit before subprocess work on the common
# path (see check #6's ORDER comment) and bound any git/network calls it adds
# in the AGGREGATE per run (see check #6's GIT_BUDGET and check_anchor.py's
# OCCUPANCY_GIT_BUDGET) — a declared timeout only the worst case ever hits is
# a backstop, not a budget.
set -uo pipefail

HOOK_MODE=0
[ "${1:-}" = "--hook" ] && HOOK_MODE=1

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# WHERE memory actually goes is decided by `autoMemoryDirectory`, so read that
# setting rather than assuming the default layout. Assuming it inverts the whole
# guard: point the setting anywhere else (a shared memory dir for a family of
# repos, docs/memory, a path outside the repo) and every check below silently
# validates a directory Claude never writes to — check #1 warns forever about a
# correctly-configured repo, while checks #2/#3 report clean about memory that is
# in fact unwatched. A guard against silent memory loss must not have a silent
# failure mode of its own.
#
# Precedence follows Claude Code's own settings order, project-local first.
#
# BINDING (TD-21): where python3 exists, the value is bound by a real JSON
# parse — one spawn (~15ms, measured) per candidate file that textually
# mentions the key, the same spawn the validity gate already paid, so
# unconfigured repos still pay nothing. The parse is what makes binding
# CORRECT: it takes the actual top-level key (never a lookalike nested in an
# unrelated block), resolves duplicate top-level keys to the last occurrence
# exactly as Claude Code's own JSON.parse does, decodes escaped characters the
# sed extraction had to reject, skips an unloadable file and walks on to the
# next in precedence (Claude Code's loader does the same), and refuses a file
# whose only mention is a decoy/null/non-string — loudly, because from the
# outside that file looks configured.
#
# Where python3 is ABSENT, the grep/sed textual fallback below still runs, and
# every way it can be wrong stays LOUD rather than guessed at (a wrong
# directory reported as OK is the exact failure this guard exists to prevent):
#   - the key/string pair appearing more than once — `head -1` binds to the
#     first TEXTUAL match, which need not be the top-level one, so the count is
#     reported. Counted on the key+string pattern, not the bare key: a nested
#     `"autoMemoryDirectory": null` never competes for `head -1`, so counting
#     bare keys raised a false alarm on an unambiguous file;
#   - an escaped quote inside the value — `[^"]*` stops at the backslash and
#     yields a truncated path, so a trailing backslash is unparsable, not used.
DEFAULT_MEM_DIR="$REPO_ROOT/docs/claude-memory"
mem_setting=""; mem_setting_src=""; mem_setting_unparsed=""; mem_setting_ambiguous=""
mem_setting_badjson=""; mem_setting_decoy=""
for f in "$REPO_ROOT/.claude/settings.local.json" "$REPO_ROOT/.claude/settings.json" "$HOME/.claude/settings.json"; do
  [ -f "$f" ] || continue
  keyhits=$(grep -o '"autoMemoryDirectory"[[:space:]]*:[[:space:]]*"[^"]*"' "$f" 2>/dev/null | wc -l | tr -d ' ')
  grep -q '"autoMemoryDirectory"' "$f" 2>/dev/null || continue
  v=$(grep -o '"autoMemoryDirectory"[[:space:]]*:[[:space:]]*"[^"]*"' "$f" 2>/dev/null | head -1 | sed 's/.*:[[:space:]]*"//; s/"$//')
  # Truncated at an escaped quote: unusable, and must not be silently adopted.
  case "$v" in *\\) v="" ;; esac
  if command -v python3 >/dev/null 2>&1; then
    # TD-21: bind the top-level key with a real parse, in the same spawn the
    # validity gate already paid — textual matching cannot tell a top-level key
    # from a nested lookalike, and minified JSON defeats positional heuristics.
    # JSON-decoding the value also makes escaped characters correct, where the
    # sed extraction had to reject them (one degenerate exception: a value
    # ending in a literal newline loses it to command substitution — not a
    # real path shape). Duplicate top-level keys resolve to
    # the LAST occurrence, matching Claude Code's own JSON.parse. Exit codes:
    # 0 = bound (decoded value on stdout); 2 = not loadable JSON (Claude Code
    # skips the file, so the walk continues); 3 = loads fine but carries no
    # usable top-level string value (nested lookalike, null, empty, or non-string) —
    # Claude Code ignores it, so the walk continues, loudly, because from the
    # outside the file looks configured.
    pyv=$(python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as fh:
        d = json.load(fh)
except Exception:
    sys.exit(2)
v = d.get("autoMemoryDirectory") if isinstance(d, dict) else None
if isinstance(v, str) and v:
    sys.stdout.write(v)
    sys.exit(0)
sys.exit(3)' "$f" 2>/dev/null)
    case $? in
      0) mem_setting="$pyv"; mem_setting_src="$f"; break ;;
      2) [ -z "$mem_setting_badjson" ] && mem_setting_badjson="$f"; continue ;;
      *) [ -z "$mem_setting_decoy" ] && mem_setting_decoy="$f"; continue ;;
    esac
  fi
  # Interpreter-absent fallback: the previous textual behavior, every warning
  # kept — binding here is best-effort and the ambiguity warning says so.
  if [ -n "$v" ]; then
    mem_setting="$v"; mem_setting_src="$f"
    [ "$keyhits" -gt 1 ] && mem_setting_ambiguous="$keyhits"
    break
  fi
  [ -z "$mem_setting_unparsed" ] && mem_setting_unparsed="$f"
done
case "$mem_setting" in
  "~")   mem_setting="$HOME" ;;
  "~/"*) mem_setting="$HOME/${mem_setting#\~/}" ;;
esac
MEM_DIR="${mem_setting:-$DEFAULT_MEM_DIR}"
# Claude Code munges EVERY non-alphanumeric to '-' in project-dir names (not
# just '/'), same rule as list_sessions.py's munge() — keep them in step. A
# narrower substitution makes check #2 probe a nonexistent fallback dir for
# any repo path containing '.', '_', etc., silently disabling stray detection.
ENCODED=$(printf '%s' "$REPO_ROOT" | sed 's/[^a-zA-Z0-9]/-/g')
FALLBACK="$HOME/.claude/projects/${ENCODED}/memory"

warns=()

# 0) Could the setting be read at all? A present-but-unparsable value means this
#    guard is checking the DEFAULT path while Claude writes somewhere else — the
#    one outcome worse than no guard, so it is loud rather than silent.
if [ -n "$mem_setting_badjson" ]; then
  # Which directory is really in play depends on whether any loadable file
  # further down the precedence order supplied a value. Both branches are loud;
  # only one of them is true, so it must not be hardcoded.
  if [ -n "$mem_setting_src" ]; then
    warns+=("$mem_setting_badjson is not valid JSON, so Claude Code cannot load it and its autoMemoryDirectory is ignored — the lower-precedence $mem_setting_src supplies the value in effect, so this check is watching $MEM_DIR. Fix the JSON syntax; until then the higher-precedence setting silently does nothing.")
  else
    warns+=("$mem_setting_badjson is not valid JSON, so Claude Code cannot load it — autoMemoryDirectory is NOT active and memory is going to the default fallback ($FALLBACK), however configured the file looks. Fix the JSON syntax; this check is meanwhile watching $DEFAULT_MEM_DIR.")
  fi
fi
if [ -n "$mem_setting_unparsed" ]; then
  # Say where the value actually came from. Naming the default is wrong (and sends
  # people chasing a phantom) when a LOWER-precedence file supplied a usable value.
  if [ -n "$mem_setting_src" ]; then
    landed="the lower-precedence $mem_setting_src supplied one instead, so this check is using $MEM_DIR"
  else
    landed="this check is falling back to $DEFAULT_MEM_DIR and may be watching the wrong directory"
  fi
  warns+=("autoMemoryDirectory is set in $mem_setting_unparsed but its value could not be parsed (empty, non-string, or containing an escaped quote) — $landed. Claude Code may resolve this differently, so verify the setting by hand.")
fi
# Ambiguity is loud rather than resolved — reachable only on the interpreter-
# absent fallback path (with python3 the parse binds the top-level key and
# ambiguity does not arise): without a JSON parse there is no way to tell a
# top-level key from one nested in an unrelated block, and the first textual
# match is not necessarily the one Claude Code honors.
if [ -n "$mem_setting_ambiguous" ]; then
  warns+=("\"autoMemoryDirectory\" appears $mem_setting_ambiguous times in $mem_setting_src — this check cannot tell which is the top-level setting and is using the first ('$mem_setting'). If Claude Code is honoring a different one, every memory check below is watching the wrong directory. Remove the duplicate or the nested lookalike.")
fi
# A file that mentions the key but, parsed, carries no usable top-level string
# value — a nested lookalike, null, empty, or a non-string. Claude Code ignores it and
# so did the walk; loud because from the outside the file looks configured.
if [ -n "$mem_setting_decoy" ]; then
  if [ -n "$mem_setting_src" ]; then
    landed="the lower-precedence $mem_setting_src supplies the value in effect, so this check is watching $MEM_DIR"
  else
    landed="no file supplies one, so this check is falling back to $DEFAULT_MEM_DIR"
  fi
  warns+=("$mem_setting_decoy mentions \"autoMemoryDirectory\" but carries no usable top-level string value (nested lookalike, null, empty, or non-string) — Claude Code ignores it; $landed. Remove the lookalike or set the real key.")
fi
# A relative value is invalid to Claude Code (cadence.md adoption checklist step 2:
# absolute or ~/-prefixed), so it is silently ignored there — and would send memory
# to the fallback while looking configured here.
case "$MEM_DIR" in
  /*) ;;
  *)  warns+=("autoMemoryDirectory in ${mem_setting_src:-?} is a relative path ('$mem_setting') — Claude Code requires an absolute or ~/-prefixed value, so the setting is being ignored and memory is going to the default fallback.")
      # Claude Code ignores it, so the guard must too — probing the relative
      # string would add a second, misleading "index missing" warning about a
      # path nothing uses. Check #2 is what catches the resulting strays.
      MEM_DIR="$DEFAULT_MEM_DIR"; mem_setting_src="" ;;
esac

# 1) Memory index present? (absence ⇒ setting likely misconfigured)
if [ ! -f "$MEM_DIR/MEMORY.md" ]; then
  warns+=("Memory index missing: $MEM_DIR/MEMORY.md${mem_setting_src:+ (autoMemoryDirectory from $mem_setting_src)} — autoMemoryDirectory may be misconfigured, so memory could be writing to the default location instead.")
fi

# 2) Stray memories in the default fallback (written when the setting wasn't active)?
if [ -d "$FALLBACK" ]; then
  stray=$(find "$FALLBACK" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$stray" -gt 0 ]; then
    warns+=("$stray memory file(s) in the default fallback $FALLBACK are NOT in the repo. autoMemoryDirectory likely isn't active here (restart the session, or set it in .claude/settings.local.json). Migrate: mv \"$FALLBACK\"/*.md \"$MEM_DIR\"/ && commit.")
  fi
fi

# 3) Uncommitted memories (on disk but not backed up until committed & pushed)?
#    Ask the repo that OWNS the memory dir, which is not necessarily this session's
#    repo — `git -C "$REPO_ROOT" status -- <path outside it>` fails with "outside
#    repository", and the error was being swallowed into dirty=0, i.e. reported as
#    clean. Whether memory is committed is a property of its own repo.
MEM_REPO=""
[ -d "$MEM_DIR" ] && MEM_REPO=$(git -C "$MEM_DIR" rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$MEM_REPO" ]; then
  dirty=$(git -C "$MEM_REPO" status --porcelain -- "$MEM_DIR" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$dirty" -gt 0 ]; then
    where="$MEM_DIR"
    [ "$MEM_REPO" != "$REPO_ROOT" ] && where="$MEM_DIR (in $MEM_REPO, a different repo than this session's)"
    warns+=("$dirty uncommitted change(s) in $where — commit & push to back up new memories.")
  fi
elif [ -d "$MEM_DIR" ]; then
  warns+=("$MEM_DIR is not inside a git repo — memory written there is never committed or pushed, so it is lost with the machine (cadence.md §7: memories are git-tracked like any other doc).")
fi

# 4) Pre-push main guard enabled in this clone? (per-machine — a fresh clone lacks it)
# core.hooksPath is clone-wide, so from a worktree it may legitimately hold the main
# checkout's absolute path — accept any configured path that actually contains the
# pre-push hook instead of string-matching against $REPO_ROOT.
if [ -f "$REPO_ROOT/scripts/git-hooks/pre-push" ]; then
  hookspath=$(git -C "$REPO_ROOT" config core.hooksPath 2>/dev/null || true)
  if [ -z "$hookspath" ]; then
    warns+=("core.hooksPath is not set in this clone — the pre-push main guard is OFF on this machine. Fix: git config core.hooksPath scripts/git-hooks")
  else
    case "$hookspath" in
      /*) resolved="$hookspath" ;;
      *)  resolved="$REPO_ROOT/$hookspath" ;;
    esac
    if [ ! -f "$resolved/pre-push" ]; then
      # A configured-but-pre-push-less hooksPath means the repo has its OWN hooks
      # there. Never tell the user to repoint it — git honors one hooks directory,
      # so that trades the main guard for whatever lint/stamp hooks they already
      # run, and the loss is silent. Copying the hook in keeps both.
      warns+=("core.hooksPath ($hookspath) has no pre-push hook — the pre-push main guard is OFF on this machine. git honors only ONE hooks directory, so do NOT repoint core.hooksPath — any hooks already in $hookspath would be silently disabled. Fix: mkdir -p \"$resolved\" && cp \"$REPO_ROOT/scripts/git-hooks/pre-push\" \"$resolved/\"")
    fi
  fi
fi

# 5) RETIRED 2026-08-12 (TD-7): Nudge-claim verification. Push nudges were
#    retired in favor of the pull channels — the SessionStart due-items line
#    (nudge_user_attention.py --report --due-only, wired in the settings
#    template) and the /attention skill — so boards no longer carry a
#    delivery claim to verify. Numbering kept so checks #0-#6 stay stable in
#    docs and old messages. BOARD stays assigned — check #6 reads it, and under
#    `set -u` an unbound reference is a hard crash of the whole hook (found in
#    review when deleting #5's body took the assignment with it).
BOARD="$REPO_ROOT/docs/user_attention.md"

# 6) Machine roster coverage (plan 2026-08-10 P1): does this machine's registry
#    (~/.config/dev-cadence/repos.txt) know about this repo's board? Soft warn.
#    ORDER IS A PERFORMANCE INVARIANT — the conditions short-circuit as written:
#    registry existence, then open board items, then coverage — so a clean board
#    or an unregistered machine (fresh laptop, CI, ephemeral container) never
#    pays the git/realpath subprocess work at session start. Do not reorder.
#    Coverage = canonical main-checkout root listed, OR origin URL matching a
#    listed repo's origin (a deploy mirror or bind-mounted devcontainer checkout
#    is covered *somewhere*, which is all this check exists to establish).
#    Degradation is silence, never a false warning. Path-resolution spec:
#    cadence.md §Machine scope (parity: sync.sh, sync-all.sh, --report).
REGISTRY="${XDG_CONFIG_HOME:-$HOME/.config}/dev-cadence/repos.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
if [ -f "$REGISTRY" ] \
   && [ -f "$BOARD" ] && grep -Eq '^[[:space:]]*-[[:space:]]*\[ \]' "$BOARD" \
   && git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  roster_warn=$(python3 - "$REPO_ROOT" "$REGISTRY" "$SCRIPT_DIR" <<'PY' 2>/dev/null
import os, subprocess, sys, time

repo, reg, script_dir = sys.argv[1], sys.argv[2], sys.argv[3]

# TD-10: per-call timeouts alone don't bound this check — the origin scan runs
# one git per roster entry, so a long roster of slow paths pays timeout × N,
# serially, at session start. Bound the aggregate; past it, every remaining
# lookup is undeterminable, and an undeterminable coverage check exits silent
# (the stated degradation direction), never warns on a repo it couldn't check.
GIT_TIMEOUT = 3       # seconds per call
GIT_BUDGET = 6.0      # seconds across all git calls in one run
_git_spent = 0.0
_budget_hit = False

def canon(p):
    return os.path.realpath(p)

def run_git(path, *args):
    global _git_spent, _budget_hit
    if _git_spent >= GIT_BUDGET:
        _budget_hit = True
        return None
    t0 = time.monotonic()
    try:
        r = subprocess.run(["git", "-C", path, *args],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        _git_spent += time.monotonic() - t0
    return r.stdout.strip() if r.returncode == 0 else None

def main_root(path):
    common = run_git(path, "rev-parse", "--git-common-dir")
    if not common:
        return None
    if not os.path.isabs(common):
        common = os.path.join(path, common)
    return canon(os.path.dirname(canon(common)))

try:
    # _normalize_source_url lives in nudge_user_attention.py, installed next to
    # this guard (SYNC set travels together) — import it, never copy it.
    norm = None
    if script_dir:
        sys.path.insert(0, script_dir)
        try:
            from nudge_user_attention import _normalize_source_url as norm
        except Exception:
            norm = None

    mine = main_root(repo)
    if mine is None:
        sys.exit(0)
    entries, seen = [], set()
    with open(reg, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            c = canon(line)  # read-time canonicalize + dedupe (spec: cadence.md §Machine scope)
            if c not in seen:
                seen.add(c)
                entries.append(c)
    if mine in entries:
        sys.exit(0)
    if norm is not None:
        my_origin = run_git(repo, "remote", "get-url", "origin")
        my_norm = norm(my_origin) if my_origin else None
        if my_norm:
            for e in entries:
                if not os.path.isdir(e):
                    continue
                o = run_git(e, "remote", "get-url", "origin")
                if o and norm(o) == my_norm:
                    sys.exit(0)  # same-origin second checkout — covered elsewhere
    if _budget_hit:
        sys.exit(0)  # ran out of budget before coverage could be disproven — silence
    print(f"this machine's roster ({reg}) doesn't list this repo ({mine}) — "
          "machine-scope views (/attention, nudge --report) won't see this board. "
          "Fix: run dev-cadence sync.sh against this repo on this machine, or add "
          "the main-checkout path to the roster by hand.")
except Exception:
    pass  # degrade to silence, never a false warning
PY
  ) || roster_warn=""
  [ -n "$roster_warn" ] && warns+=("$roster_warn")
fi

if [ "${#warns[@]}" -eq 0 ]; then
  if [ "$HOOK_MODE" -eq 0 ]; then
    n=$(find "$MEM_DIR" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
    echo "OK: $n memory files tracked in $MEM_DIR${mem_setting_src:+ (autoMemoryDirectory from $mem_setting_src)}; no strays in fallback; clean working tree; per-clone guards enabled."
  fi
  exit 0
fi

echo "⚠️  Claude auto-memory check found issue(s):"
for w in "${warns[@]}"; do echo "  • $w"; done
exit 0
