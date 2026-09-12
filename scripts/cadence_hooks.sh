#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/cadence_hooks.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
#
# The one SessionStart entry point for dev-cadence's hooks (cadence §3, 2026-09-11).
#
#     cadence_hooks.sh --session-start
#
# WHY ONE RUNNER
# A consumer's .claude/settings.json is a SEED file (it also holds the repo's own
# permissions), so every hook that used to be its own settings line cost one hand-edited
# PR per consumer and silently missed every worktree whose branch predated the edit. Now
# settings.json carries ONE stable line that calls this script, and a hook is added HERE —
# in a synced file — never as a new settings line again. agentorc's launch layer carries
# the same line for the sessions it starts (design §4.2), so an ao session in an old
# worktree still runs the current set.
#
# CONTRACT
#   - children run in the order below; each is skipped unless present AND executable, so
#     the line is harmless in a directory that is not a consumer or only partly synced;
#   - Claude Code hands the hook its JSON payload on stdin ONCE; the runner reads it up
#     front (when stdin is not a terminal) and pipes the same bytes to every child, so a
#     child that reads stdin (check_anchor.py does) gets the payload wherever it sits in
#     the order — no child ever reads the runner's stdin directly;
#   - each child is bounded by `timeout ${CADENCE_HOOK_CHILD_TIMEOUT:-25}` seconds so one
#     slow child cannot starve the rest (cadence_changes.py documents a 25 s worst case);
#     without coreutils `timeout` the child runs unbounded and stderr says so once;
#     the settings line's own timeout must exceed children × child timeout (5 × 25 = 125;
#     the seeded line uses 150);
#   - stdout of every child passes through (SessionStart output is context for the model);
#   - the runner itself always exits 0: these are detectors, never a gate (cadence §7).
#
# The seeded line (byte-identical in dev-cadence files/.claude/settings.json and in
# agentorc's CADENCE_HOOK_LINE — a parity pair):
#   f="$CLAUDE_PROJECT_DIR/scripts/cadence_hooks.sh"; if [ -x "$f" ]; then "$f" --session-start; fi
set -u

root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
    root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
child_timeout="${CADENCE_HOOK_CHILD_TIMEOUT:-25}"

have_timeout=1
if ! command -v timeout >/dev/null 2>&1; then
    have_timeout=0
    echo "cadence_hooks.sh: coreutils timeout not found; children run unbounded" >&2
fi

# The hook payload, read once. A terminal stdin (a person running this by hand) is not read;
# a pipe that never closes is given up after 2 s rather than hanging the session start.
payload=""
if [ ! -t 0 ]; then
    if [ "$have_timeout" = 1 ]; then
        payload="$(timeout 2 cat 2>/dev/null)"
    else
        payload="$(cat)"
    fi
fi

run_child() {
    # $1 = path relative to the repo root; the rest = its arguments
    local rel="$1"; shift
    local f="$root/$rel"
    [ -x "$f" ] || return 0
    if [ "$have_timeout" = 1 ]; then
        printf '%s' "$payload" | timeout "$child_timeout" "$f" "$@"
    else
        printf '%s' "$payload" | "$f" "$@"
    fi
    return 0
}

case "${1:-}" in
    --session-start)
        run_child scripts/check_claude_memory.sh --hook
        run_child scripts/check_anchor.py --hook
        run_child scripts/hydrate_worktree.sh --hook
        run_child scripts/nudge_user_attention.py --report --due-only --fetch
        run_child scripts/cadence_changes.py --hook
        ;;
    --list)
        # the set, one per line, for tests and for a person checking what runs
        printf '%s\n' \
            "scripts/check_claude_memory.sh --hook" \
            "scripts/check_anchor.py --hook" \
            "scripts/hydrate_worktree.sh --hook" \
            "scripts/nudge_user_attention.py --report --due-only --fetch" \
            "scripts/cadence_changes.py --hook"
        ;;
    *)
        echo "usage: cadence_hooks.sh --session-start | --list" >&2
        ;;
esac
exit 0
