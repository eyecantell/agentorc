#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/install_git_hooks.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
#
# install_git_hooks.sh [REPO] — put this repo's git hooks in force in EVERY worktree of
# the clone, whatever branch each is on (TD-055, cadence.md §4 "Give the rule teeth").
#
# Why not core.hooksPath: a relative core.hooksPath resolves against each worktree's own
# working tree, so a worktree on a branch without scripts/git-hooks/ (pre-adoption, an old
# --detach) pushed HEAD:main with no guard at all, and a branch that edits a hook ran its
# edit. An absolute path breaks silently where a clone is seen at two paths (container and
# host, cadence.md appendix). So instead:
#
#   - one small SHIM per shipped hook goes into the clone's shared hooks directory,
#     $(git rev-parse --git-common-dir)/hooks/ — git runs hooks from there in every
#     linked worktree when core.hooksPath is unset;
#   - the shim finds the MAIN checkout at run time (the common dir's parent) and execs
#     that checkout's copy of the hook, so every worktree runs the same, current hook
#     and no path is baked in;
#   - a clone-local core.hooksPath naming our own hooks directory is then unset, since it
#     would override the shims.
#
# Never clobbers: an existing hook in the shared directory that is not our shim is kept
# (and our relative core.hooksPath is then left set too, so nothing that ran before stops
# running); a core.hooksPath pointing anywhere else is someone's own hooks setup and is
# left alone. Each such case is a WARN naming the fix. Idempotent; run by sync.sh on every
# sync, and by hand on a clone that predates it. Exit 0 installed or already fine (WARNs
# may print), 2 cannot install here (not a git clone, a bare repo, a separate git dir,
# no hooks beside this script).
set -u

REPO="${1:-.}"
MARK="dev-cadence hook shim (TD-055)"

common="$(cd "$REPO" 2>/dev/null && d="$(git rev-parse --git-common-dir 2>/dev/null)" && cd "$d" && pwd -P)" || {
    echo "  WARN  $REPO is not a git clone — hooks not installed" >&2; exit 2; }
if [ "${common##*/}" != ".git" ]; then
    # Bare repo, submodule (.git/modules/x) or --separate-git-dir: there is no main
    # checkout at the common dir's parent for a shim to run the hooks from.
    echo "  WARN  $common is not a checkout's .git directory — hooks not installed; set core.hooksPath by hand" >&2
    exit 2
fi
root="${common%/*}"

# Which hooks, and where they live relative to the checkout: beside this script
# (scripts/git-hooks in a consumer, files/scripts/git-hooks in dev-cadence itself).
# Follow a symlinked invocation to the real script first: from the link's directory the
# hooks glob below matches nothing, and the run would install nothing and exit 0.
self="${BASH_SOURCE[0]}"
while [ -L "$self" ]; do
    link="$(readlink "$self")"
    case "$link" in /*) self="$link" ;; *) self="$(dirname "$self")/$link" ;; esac
done
here="$(cd "$(dirname "$self")" && pwd -P)"
top="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" && top="$(cd "$top" && pwd -P)" || top=""
rel="scripts/git-hooks"
if [ -n "$top" ]; then
    case "$here/" in
        "$top"/*) rel="${here#"$top"}"; rel="${rel#/}"; rel="${rel:+$rel/}git-hooks" ;;
    esac
fi

conflict=0
found=0
mkdir -p "$common/hooks"
for src in "$here"/git-hooks/*; do
    { [ -f "$src" ] && [ -x "$src" ]; } || continue
    name="${src##*/}"
    case "$name" in *.sample) continue ;; esac
    found=$((found+1))
    dst="$common/hooks/$name"
    if [ -e "$dst" ] && ! grep -qF "$MARK" "$dst" 2>/dev/null; then
        echo "  WARN  $dst is a hook of this clone's own — kept, so the cadence $name hook does not run from there."
        echo "        Fix: fold $root/$rel/$name into it by hand, or move it aside and rerun $0"
        conflict=1
        continue
    fi
    shim="#!/bin/sh
# $MARK — written by ${rel%/git-hooks}/install_git_hooks.sh; rerun that, never edit.
# Runs the MAIN checkout's copy of this hook, whatever branch this worktree is on.
d=\$(git rev-parse --git-common-dir 2>/dev/null) && d=\$(cd \"\$d\" && pwd -P) || d=
hook=\"\${d%/*}/$rel/\${0##*/}\"
[ -n \"\$d\" ] && [ -x \"\$hook\" ] && exec \"\$hook\" \"\$@\"
echo \"git hook shim: \$hook is missing or not executable — the \${0##*/} hook did not run (TD-055)\" >&2
exit 0
"
    if [ "$(cat "$dst" 2>/dev/null)" != "${shim%$'\n'}" ]; then
        printf '%s' "$shim" > "$dst" && chmod +x "$dst"
        echo "  hook  $name: shim in $common/hooks -> $root/$rel/$name"
    else
        echo "  hook  $name: shim already in place"
    fi
done

if [ "$found" -eq 0 ]; then
    # Never a silent success: no hooks found means nothing was installed.
    echo "  WARN  no hooks found in $here/git-hooks — nothing installed; the pre-push main guard is OFF" >&2
    exit 2
fi

local_hp="$(git -C "$root" config --local core.hooksPath 2>/dev/null || true)"
if [ -n "$local_hp" ]; then
    case "$local_hp" in
        "$rel"|"$rel/"|"$root/$rel"|"$root/$rel/")
            if [ "$conflict" -eq 0 ]; then
                git -C "$root" config --local --unset core.hooksPath
                echo "  conf  core.hooksPath ($local_hp) unset — the shims in $common/hooks run the hooks in every worktree"
            else
                echo "  WARN  core.hooksPath ($local_hp) left set: a hook of this clone's own is in $common/hooks (above), and unsetting would switch it on in place of the cadence hook."
            fi ;;
        *)
            echo "  WARN  core.hooksPath is '$local_hp' — this clone's own hooks setup, left alone, so the shims in $common/hooks do not run."
            echo "        Do NOT just unset it: git honors ONE hooks directory. Fix: copy $root/$rel/pre-push (and post-merge) into it" ;;
    esac
fi
eff="$(git -C "$root" config core.hooksPath 2>/dev/null || true)"
if [ -n "$eff" ] && [ -z "$(git -C "$root" config --local core.hooksPath 2>/dev/null || true)" ]; then
    echo "  WARN  core.hooksPath is set outside this clone ('$eff', global or system config) — git uses that directory, not the shims."
fi
exit 0
