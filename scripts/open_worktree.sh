#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/open_worktree.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Create (if needed) a worktree for a topic and open it in its own VS Code window.
#
# WHY A WINDOW PER WORKTREE
# -------------------------
# The cadence runs one session per worktree so parallel sessions don't share a
# HEAD or index (docs/cadence.md §1). A single multi-root window can show them
# all, but switching topics then means switching three things separately — file
# tree, terminal, Source Control. One window per worktree makes the *window* the
# context: alt-tab moves all three at once and the title says which topic you
# are in. See scripts/generate_workspace.sh for the multi-root alternative, which is
# still useful as an occasional overview.
#
# ONE WINDOW PER TOPIC IS NOT THE SAME AS ONE ROOT PER WINDOW
# -----------------------------------------------------------
# That rule is about TOPICS. A constellation (cadence.md §9) adds an orthogonal
# axis: one topic spans several REPOS. hydrate_worktree.sh already builds those
# siblings inside the topic tree, so when they exist this script opens a
# per-topic multi-root workspace (generate_workspace.sh --topic) instead of the
# bare folder — still exactly one window per topic, and the workspace is named
# for the topic so the title still says which one you are in.
#
# Nesting the siblings under a single root instead is not neutral: they are
# gitignored at the worktree root by construction, and search.useIgnoreFiles
# defaults to true, so a workspace-wide search skips precisely the repos the
# topic is editing. Without siblings the generator writes nothing and this falls
# back to opening the folder, so single-repo consumers are unaffected.
#
# Measured 2026-08-13 (VS Code 1.132): opening a worktree with `code -n` gives a
# new window attached to the SAME remote/container as the window it was launched
# from — same container hash, no rebuild, no "Reopen in Container" prompt — even
# though a worktree carries its own .devcontainer/ and could plausibly have
# triggered a separate build. Cost is ~660MB per window (window + extension host
# + file watcher), so four topics is ~2.6GB. Existing windows are untouched.
#
# This is VS Code-specific by nature. Editors without a multi-window-per-folder
# model need their own equivalent; the worktree half of the flow is portable,
# the `code -n` half is not.
#
# CLEANUP IS NOT THIS SCRIPT'S JOB, AND CANNOT BE THE TOPIC SESSION'S EITHER
# --------------------------------------------------------------------------
# There is no `code --close-window`, so closing the window is manual (Ctrl+W).
# And a session cannot remove the worktree it is running in: `git worktree
# remove .` from inside succeeds but leaves the shell on a deleted directory
# (`getcwd: cannot access parent directories`), which strands the session.
# So teardown happens from the main checkout, after the PR merges:
#
#   scripts/hydrate_worktree.sh --dehydrate .claude/worktrees/<topic>
#   git worktree remove .claude/worktrees/<topic>
#   git branch -D <topic>        # squash merges never register as merged
#   scripts/generate_workspace.sh      # if you keep the multi-root overview
#
# The dehydrate step is FIRST and is not optional in a constellation. A nested
# sibling worktree is gitignored, so `git worktree remove` neither sees it nor
# refuses because of it — measured, it deletes the tree and the sibling's
# uncommitted work with it. reap_worktrees.sh does this in the right order
# already; the sequence above is for removing one by hand.
#
# USAGE
#   scripts/open_worktree.sh <topic>
#
# Reuses the worktree and branch if they already exist, so re-running it just
# reopens the window.

set -euo pipefail

TOPIC="${1:-}"
if [[ -z "$TOPIC" ]]; then
    echo "usage: $0 <topic>    (e.g. $0 rcv-deck)" >&2
    exit 2
fi
if [[ ! "$TOPIC" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "error: topic may contain only letters, digits, dots, underscores, dashes" >&2
    exit 2
fi

CLONE_ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
WT="$CLONE_ROOT/.claude/worktrees/$TOPIC"

# --- create the worktree, branching off *updated* origin/main (cadence §1) ---
if [[ -d "$WT" ]]; then
    echo "worktree exists: $WT"
else
    git -C "$CLONE_ROOT" fetch -q origin || echo "  warn: fetch failed; branching from the local ref"
    if git -C "$CLONE_ROOT" show-ref --verify --quiet "refs/heads/$TOPIC"; then
        echo "branch $TOPIC exists — checking it out in a new worktree"
        git -C "$CLONE_ROOT" worktree add -q "$WT" "$TOPIC"
    else
        git -C "$CLONE_ROOT" worktree add -q -b "$TOPIC" "$WT" origin/main
        echo "created worktree $WT on new branch $TOPIC (from origin/main)"
    fi
fi

# --- complete the worktree before anyone looks at it -----------------------
# `git worktree add` installs what the repo TRACKS and nothing else, so a fresh
# worktree is missing the gitignored per-clone settings (autoMemoryDirectory,
# the permission allowlist) and, in a constellation, every sibling repo — i.e.
# most of what the session is about to work on. Hydration runs BEFORE the window
# opens so the explorer never shows the half-built version. It is idempotent, so
# the reuse path above gets it too: re-running open_worktree.sh on an existing
# worktree repairs anything that has gone missing since.
if [[ -x "$CLONE_ROOT/scripts/hydrate_worktree.sh" ]]; then
    "$CLONE_ROOT/scripts/hydrate_worktree.sh" "$WT" || echo "  warn: hydration reported problems (above) — the worktree exists but is incomplete"
fi

# --- decide what the window opens on --------------------------------------
# With siblings present this is the per-topic workspace file; without them the
# generator writes nothing and prints nothing, and we open the folder as before.
# Generated BEFORE the window opens for the same reason hydration is: the
# explorer must never show the half-built version.
#
# Failure here is not fatal. A missing or broken generator costs the multi-root
# view, not the worktree, so fall back rather than abort a flow whose real work
# is already done.
OPEN_TARGET="$WT"
if [[ -x "$CLONE_ROOT/scripts/generate_workspace.sh" ]]; then
    if WS="$("$CLONE_ROOT/scripts/generate_workspace.sh" --topic "$WT" 2>/dev/null)" \
       && [[ -n "$WS" && -f "$WS" ]]; then
        OPEN_TARGET="$WS"
        echo "per-topic workspace: $WS"
    fi
fi

# Escape the regex metacharacter the topic charset allows (`.`), so a topic
# "a.b" cannot match a window titled "a-b".
TOPIC_RE="${TOPIC//./\\.}"

# --- find a VS Code window to talk to -------------------------------------
# $VSCODE_IPC_HOOK_CLI goes stale: a long-running terminal outlives its window's
# socket, and dead socket files linger in /tmp (26 of them here), so existence
# is not liveness. Probe newest-first and take the first that actually answers.
live_socket() {
    local cand
    for cand in "${VSCODE_IPC_HOOK_CLI:-}" $(ls -t /tmp/vscode-ipc-*.sock 2>/dev/null | head -4); do
        [[ -S "$cand" ]] || continue
        if VSCODE_IPC_HOOK_CLI="$cand" timeout 10 code --status >/dev/null 2>&1; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

if ! command -v code >/dev/null 2>&1; then
    echo "note: no 'code' CLI on PATH — open this manually: $OPEN_TARGET"
elif SOCK="$(live_socket)"; then
    # `code -n` always opens a NEW window, so re-running would stack duplicates
    # on the same folder. VS Code puts the folder name in the window title, so
    # check for one already open.
    #
    # Both directions of this match fail harmlessly, which is why title-scraping
    # is acceptable here: a missed match opens a redundant window (~660MB), a
    # false match tells you to alt-tab to a window that isn't there. Neither
    # touches the worktree or the branch. Status is captured once — the call
    # takes seconds.
    #
    # WHAT FOLLOWS THE TOPIC IN THE TITLE VARIES, so anchoring on one separator
    # under-matches. Observed forms of the `code --status` window line:
    #
    #   ... - topic [Dev Container: Name] - Visual Studio Code    (in a container)
    #   ... - topic - Visual Studio Code                          (plain)
    #   ... - topic (Workspace) [Dev Container: ...] - ...        (multi-root)
    #
    # The previous `-F " $TOPIC ["` matched only the first, so outside a
    # container the guard never fired and every run stacked another window.
    # Accept any of the three separators. $TOPIC_RE escapes the dots the topic
    # charset allows, which is what -F was buying before.
    STATUS="$(VSCODE_IPC_HOOK_CLI="$SOCK" timeout 15 code --status 2>/dev/null)"
    if grep -qE -- " $TOPIC_RE( \[| -| \()" <<< "$STATUS"; then
        echo "a window is already open on $TOPIC — alt-tab to it"
    else
        VSCODE_IPC_HOOK_CLI="$SOCK" code -n "$OPEN_TARGET"
        echo "opened a new VS Code window on $TOPIC"
    fi
else
    echo "note: no live VS Code window found — open this manually: $OPEN_TARGET"
fi

# Keep the multi-root overview honest even when it isn't the primary surface.
[[ -x "$CLONE_ROOT/scripts/generate_workspace.sh" ]] && "$CLONE_ROOT/scripts/generate_workspace.sh" >/dev/null

cat <<EOF

next: in the new window, open a terminal and run
    claude
It will start in $WT and hold that worktree's anchor.
EOF
