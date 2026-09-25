#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/reap_worktrees.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Report (and optionally remove) worktrees whose work has landed on origin/main.
#
# WHY NOT A CRON
# --------------
# A worktree records an ABSOLUTE path in .git/worktrees/<name>/gitdir — the one
# in force where it was created. When the same repo is reachable at two paths
# (the common case: a container sees /workspaces/<repo> while the host sees
# ~/dev/<repo>), every worktree created on one side looks, from the other, like
# its directory is gone. That is exactly `git worktree prune`'s cleanup
# condition, so a scheduler running on the far side silently de-registers live
# worktrees. Run worktree maintenance from the same namespace that created
# them — which, when sessions run in a container, means a session or a git hook
# rather than a host cron. See cadence.md's containers appendix.
#
# The trigger is scripts/git-hooks/post-merge, which fires on `git pull --ff-only`
# (verified) — i.e. exactly when the anchor pulls after merging a PR, which is
# the moment a worktree becomes reapable. No scheduler, no marker file. A
# worktree never needs to declare that it is temporary: cadence §1 says all of
# them are, so the flag would be true for every one and carry no information.
# Landed-ness is computed instead, which is why it cannot go stale the way a
# marker left by a crashed session would.
#
# WHAT COUNTS AS REAPABLE — all five, or it is left alone
#   landed   the branch's own files are identical to origin/main
#   clean    no modified, no untracked AND no ignored files (scratch must never
#            be reaped), bar a short allowlist of derived paths — see IGNORED
#   idle     no live Claude session has its cwd inside the worktree
#   unlocked Claude Code locks worktrees it created; a lock means someone owns it
#   nested   nothing inside it belonging to ANOTHER repo holds unsaved work
#
# THE `nested` CHECK, AND WHY `clean` COULD NOT COVER IT
# ------------------------------------------------------
# In a constellation (cadence.md §9) a worktree can contain worktrees of the
# sibling repos, put there by scripts/hydrate_worktree.sh. Those siblings are
# separate repos, and every check above is blind to them:
#
#   - `clean` asks THIS repo for its status, and the nested directories are
#     gitignored here, so a sibling with a week of uncommitted work reads
#     clean=yes.
#   - `git worktree remove` does not refuse either. Measured on git 2.39.5: it
#     succeeds, deletes the nested tree along with the parent, and leaves the
#     sibling holding a `prunable` registration. No warning, no exit code.
#
# So the single most destructive path in this repo ran, unattended from the
# post-merge hook, straight through the one thing that would have stopped it.
# The `nested` column closes that: `hydrate_worktree.sh --check` reports whether
# every nested worktree is committed and pushed, and the reap loop runs
# `--dehydrate` — which removes them through their OWN repos, so registrations
# go with the directories — and proceeds only if that exits 0.
#
# Consumers synced before hydrate_worktree.sh existed have no such script; there
# the column reads `n/a` and nothing changes, which is correct, because without
# that script nothing creates nested worktrees in the first place.
#
# A lock outlives the session that took it — a crashed or SIGKILLed creator
# leaves one behind, and nothing clears it, so that worktree is unreapable
# forever and reports unlocked=no with a live-looking reason. Check the reason
# before overriding, then release it by hand:
#
#   git worktree list --porcelain | grep -A2 '^worktree .*<topic>'   # see the reason
#   git worktree unlock <path>
#
# The reason names the session and pid that took it; if that pid is gone (see
# cadence.md §1 on live-but-unattended sessions), the lock is a corpse.
#
# IGNORED FILES ARE SCRATCH TOO (TD-054)
# --------------------------------------
# `git status --untracked-files=all` never lists IGNORED paths, and `git
# worktree remove` deletes them without --force. Reproduced on git 2.48.1: a
# worktree whose only unsaved file is scratch/notes.txt under an ignored
# scratch/ printed no status lines, was removed, and the file was gone. The
# scratch a session is most likely to leave — notes in an ignored dir, build
# output, a downloaded fixture — is ignored by construction, and so are two
# worse things: a nested FULL clone (a .git directory, which hydrate's scan
# prunes, so `nested` passes it) with its unpushed commits, and a worktree's
# real .claude/settings.local.json, which hydrate refuses to touch because the
# divergent copy may be the newer one.
#
# So `clean` also reads `git status --ignored --untracked-files=all` (the
# traditional mode lists ignored files one by one, and a nested repo as one
# `dir/` entry) and every ignored entry blocks, except:
#   - anything under a __pycache__/ directory (derived, regenerated on import);
#   - a symlink (removing it never touches its target — hydrate's links);
#   - a nested WORKTREE (`dir/.git` is a file) when hydrate_worktree.sh is
#     present, at most nested_depth() deep (3, or the deepest configured path) and not
#     under node_modules: exactly what hydrate's
#     --check/--dehydrate see, so the `nested` column already judges it (a
#     parity pair with find_nested_worktrees's -maxdepth 4; test case 20 pins
#     both sides of the boundary).
# A nested .git DIRECTORY always blocks. The report prints the blocking paths
# under the row, so a person can delete them deliberately. The ignored scan
# walks ignored trees (.venv, node_modules), so it runs only for a worktree
# that has already passed landed and the tracked/untracked check — any other
# row is kept regardless, and its clean column says tracked+untracked only.
#
# THE LANDED TEST IS SCOPED, AND HAS TO BE
# ----------------------------------------
# Squash merges (§4) sever ancestry, so `git log origin/main..<branch>` reports
# every merged branch as unmerged forever. Content comparison is the answer —
# but plain `git diff origin/main <branch>` is also wrong, because it reports
# main's *other* advances as differences, so a landed branch reads as unlanded
# the moment anything else merges. Scope the diff to the files the branch itself
# touched, which is what cadence §1's `git diff origin/main HEAD -- <your files>`
# means. Demonstrated:
#
#   git diff --stat main topic          -> shows g, an unrelated file main gained
#   git diff --stat main topic -- f     -> empty; topic's own file did land
#
# If main later edits those same files differently the test says "not landed"
# and the worktree survives. That is the correct direction to be wrong in.
#
# BRANCHES WITHOUT A WORKTREE (TD-032) get their own report section and gates —
# see ORPHAN BRANCHES below the worktree loop.
#
# USAGE
#   scripts/reap_worktrees.sh            # report only
#   scripts/reap_worktrees.sh --reap     # remove the ones that pass every check (worktrees and orphan branches)
#   scripts/reap_worktrees.sh --quiet    # print nothing when there is nothing to say

set -uo pipefail

REAP=0
QUIET=0
for a in "$@"; do
    case "$a" in
        --reap) REAP=1 ;;
        --quiet) QUIET=1 ;;
        # Print the leading comment block, however long it grows: awk stops at the
        # first non-comment line instead of a hardcoded count. The count it replaces
        # was already two short of the USAGE section, so -h never printed the usage
        # it exists to print, and the provenance header above would have made any
        # fixed number wrong again.
        -h|--help) awk 'NR>1 && /^[^#]/ && NF {exit} {print}' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TOP="$(git rev-parse --show-toplevel 2>/dev/null)" && TOP="$(cd "$TOP" && pwd -P)"
CLONE_ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)" || exit 0
cd "$CLONE_ROOT" || exit 0

# Sibling scripts come from the MAIN checkout, never from the worktree this copy
# happens to run in: hydrate's --check/--dehydrate gates the destructive path,
# and a worktree mid-edit on it (or on an old branch) must not swap in its own
# logic. So: $CLONE_ROOT/scripts/<name> first — exactly what a consumer always
# used — then the main checkout's copy at THIS script's repo-relative directory
# (files/scripts/ in dev-cadence itself, where scripts/ does not exist and a
# hardcoded path left the reaper blind to hydrate and the registry, TD-038),
# and only then the copy beside this one.
REL=""
case "$HERE/" in "${TOP:-/nonexistent}"/*) REL="${HERE#"$TOP"}"; REL="${REL#/}" ;; esac
sibling() {
    local c
    for c in "$CLONE_ROOT/scripts/$1" ${REL:+"$CLONE_ROOT/$REL/$1"} "$HERE/$1"; do
        [[ -e "$c" ]] && { printf '%s\n' "$c"; return; }
    done
    printf '%s\n' "$CLONE_ROOT/scripts/$1"
}
HYDRATE="$(sibling hydrate_worktree.sh)"
ANCHOR_DIR="$(dirname "$(sibling check_anchor.py)")"

# Default branch — cadence.md §9 "Default-branch rule". Parity: this function is copied
# verbatim into pre-push, reap_worktrees.sh, open_worktree.sh and hydrate_worktree.sh,
# and default_branch() in the Python scripts follows the same rule; tests/test_default_branch.sh
# runs every copy against the same fixtures. Prints a branch NAME, never empty.
default_branch() {
    local repo="$1" b
    b="$(git -C "$repo" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)"
    for b in "${b#origin/}" "$(git -C "$repo" config init.defaultBranch 2>/dev/null)" main master; do
        if [[ -n "$b" ]] && git -C "$repo" rev-parse --verify -q "refs/remotes/origin/$b" >/dev/null; then
            echo "$b"; return 0
        fi
    done
    echo main
}
BASE="origin/$(default_branch .)"

# Only fetch when a human is driving. In hook mode we have just pulled, so
# origin/main is already current and a network call would slow every pull.
[[ $QUIET -eq 0 ]] && git fetch -q origin 2>/dev/null

# Which worktree paths does Claude Code hold a lock on? Upstream's TD-26: the
# session that CREATED a worktree locks it, and the lock reason names it.
LOCKED="$(git worktree list --porcelain | awk '
    /^worktree /  { p = substr($0, 10) }
    /^locked/     { print p }
')"

# Live-session cwds, reusing the anchor guard rather than re-deriving liveness —
# it corroborates each registry pid against /proc procStart, which is the subtle
# part.
#
# EVERY failure here must produce the __UNKNOWN__ sentinel, because the fallback
# for "I don't know who's live" has to be "don't delete anything". The two
# halves that guarantee it:
#   - the `except Exception` inside, for anything the python raises;
#   - the `|| LIVE_CWDS=__UNKNOWN__` outside, for the interpreter not running at
#     all. Without it the substitution yields an empty string with stderr
#     swallowed, no sentinel appears, and every worktree reads idle=yes —
#     including ones with a live session in them.
# A `command -v python3` pre-check used to sit here too; it was removed as
# genuinely redundant — mutation testing showed the `||` already covers the
# missing-interpreter case, and an unreachable branch no test can pin is a
# liability rather than defence in depth.
LIVE_CWDS="$(ANCHOR_DIR="$ANCHOR_DIR" python3 - <<'PY' 2>/dev/null
import os, sys, pathlib
sys.path.insert(0, os.environ["ANCHOR_DIR"])
try:
    # An UNREADABLE or MISSING registry is not the same fact as an EMPTY one,
    # and live_sessions() returns [] for all three. Empty means "nobody is
    # working"; the other two mean "this machine cannot tell you". Collapsing
    # them retires the only liveness signal the reaper has.
    #
    # os.listdir, not is_dir(): is_dir() only needs the PARENT searchable, so
    # it returns True for a directory that cannot actually be read — and
    # pathlib's glob() swallows the PermissionError and yields nothing, so
    # live_sessions() reports an empty registry with no exception raised.
    # Verified: chmod 000 on a populated sessions dir gives is_dir()=True and
    # glob()=[]. That combination reaped a live worktree in review. listdir
    # raises on both missing and unreadable, and the handler below turns any
    # OSError into the sentinel.
    os.listdir(pathlib.Path.home() / ".claude" / "sessions")
    from check_anchor import live_sessions, procfs_available
    # No procfs: liveness is undeterminable, so idleness is too (TD-043).
    if not procfs_available():
        raise RuntimeError("no procfs")
    for s in live_sessions():
        cwd = s.get("cwd")
        if cwd:
            print(cwd)
except Exception:
    print("__UNKNOWN__")
PY
)" || LIVE_CWDS="__UNKNOWN__"

if [[ "$LIVE_CWDS" == *__UNKNOWN__* ]]; then
    echo "warn: could not read the live-session registry — not reaping anything" >&2
    REAP=0
fi

# Print the ignored paths in worktree $1 that removing it would destroy (see
# IGNORED FILES above), one per line; nothing means none. Exit non-zero when
# git itself fails, which the caller treats as not clean.
# Nested-worktree scan depth (TD-058) — a §7 parity pair: hydrate_worktree.sh's
# find_nested_worktrees and reap_worktrees.sh's ignored_blockers() must agree on how deep
# a nested worktree can sit, or one sibling is invisible to hydrate yet exempt in the
# reaper. The deepest path configured in docs/nested-repos.txt, never less than 3. This
# function is copied verbatim into both scripts; tests/test_reap_worktrees.sh pins that.
nested_depth() {
    local d
    # the path is every word but the last (the mode), as hydrate's read_config() reads it,
    # so a directory name with a space counts at its real depth
    d="$(awk '{sub(/#.*/, "")} NF >= 2 {p = $0; sub(/[ \t]+[^ \t]+[ \t]*$/, "", p)
              sub(/^[ \t]+/, "", p); sub(/^\.\//, "", p); sub(/\/+$/, "", p)
              n = split(p, a, "/"); if (n > m) m = n}
              END {print (m > 3 ? m : 3)}' "$1" 2>/dev/null)"
    echo "${d:-3}"
}
NESTED_DEPTH="$(nested_depth "$CLONE_ROOT/docs/nested-repos.txt")"

ignored_blockers() {
    local wt="$1" entry p rel depth out
    # A file, not $(...): command substitution drops the NULs -z separates on.
    out="$(mktemp)" || return 1
    if ! git -C "$wt" status --porcelain -z --ignored --untracked-files=all >"$out" 2>/dev/null; then
        rm -f "$out"; return 1
    fi
    while IFS= read -r -d '' entry; do
        [[ "$entry" == '!! '* ]] || continue
        p="${entry#!! }"
        rel="${p%/}"
        case "/$rel/" in */__pycache__/*) continue ;; esac
        [[ -L "$wt/$rel" ]] && continue
        if [[ "$p" == */ && -f "$wt/$rel/.git" && -x "$HYDRATE" ]]; then
            depth="${rel//[!\/]/}"
            case "/$rel/" in */node_modules/*) ;; *)
                [[ ${#depth} -le $(( NESTED_DEPTH - 1 )) ]] && continue ;;
            esac
        fi
        # One line per path in the report, even for a name holding a newline.
        printf '%s\n' "${p//$'\n'/\\n}"
    done < "$out"
    rm -f "$out"
    return 0
}

# landed_of <branch>: yes | empty | no — the scoped landed test (header: THE LANDED
# TEST IS SCOPED), shared by the worktree loop and the orphan-branch pass so there is
# ONE implementation of it and its traps (comments in the worktree loop below).
landed_of() {
    local branch="$1" base files
    base="$(git merge-base "$BASE" "$branch" 2>/dev/null)"
    [[ -z "$base" ]] && { echo no; return; }
    files=()
    mapfile -d '' -t files < <(git diff --name-only -z "$base" "$branch" 2>/dev/null)
    if [[ ${#files[@]} -eq 0 ]]; then
        echo empty
    elif git diff --quiet "$BASE" "$branch" -- "${files[@]}" 2>/dev/null; then
        echo yes
    else
        echo no
    fi
}

reapable=()
reapable_branch=()
report=""

while read -r path; do
    [[ -z "$path" ]] && continue
    [[ "$(realpath "$path" 2>/dev/null)" == "$(realpath "$CLONE_ROOT")" ]] && continue

    branch="$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"

    # landed: compare only the files this branch touched (see header).
    #
    # A branch that touches NOTHING is reported "empty", never "landed" — and
    # that distinction is load-bearing. A worktree created moments ago has no
    # commits yet, so its diff from the merge-base is empty; calling that
    # "landed" would mark every freshly-opened topic reapable before its
    # session has written a line. (Caught exactly that way, against a live
    # worktree, which is why this is spelled out rather than terse.) Empty
    # means nothing to prove landed, so it is kept.
    #
    # The file list is NUL-separated into an array and passed quoted. An earlier
    # version interpolated it unquoted, which bash word-split on spaces: the
    # fragments reached `git diff` as pathspecs matching nothing, and
    # `--quiet` with a pathspec that matches nothing exits 0 — silently, no
    # error — so the branch was reported LANDED without its content ever being
    # compared. Reproduced end to end: a branch whose only work was in a file
    # absent from origin/main was reaped and its commit left unreachable. Found
    # by adversarial review, in a repo that already tracked a dozen filenames
    # containing spaces — a live landmine, not a theoretical one. Any repo with
    # spaces anywhere in a tracked path has it.
    landed="no"
    if [[ "$branch" != "HEAD" && "$branch" != "?" ]]; then
        landed="$(landed_of "$branch")"
    fi

    # clean: untracked scratch counts, because reaping it destroys it.
    # A `git status` that FAILS (directory moved or deleted out from under the
    # worktree registration) produces empty output too, which would read as
    # clean. Distinguish on exit status and report it as its own state — inert
    # today because `landed` gates first, but a check that cannot fail loudly
    # is one refactor away from being trusted.
    blockers=""
    if git -C "$path" rev-parse --git-dir >/dev/null 2>&1; then
        if [[ -z "$(git -C "$path" status --porcelain --untracked-files=all 2>/dev/null)" ]]; then
            clean="yes"
        else
            clean="no"
        fi
        # Ignored scratch (header: IGNORED FILES), only where it can decide the
        # verdict. A failing scan reads as not clean, never as nothing found.
        if [[ "$clean" == "yes" && "$landed" == "yes" ]]; then
            if ! blockers="$(ignored_blockers "$path")"; then
                clean="no"; blockers="(git status --ignored failed)"
            elif [[ -n "$blockers" ]]; then
                clean="no"
            fi
        fi
    else
        clean="gone"
    fi

    # When the registry could not be read, idleness is UNKNOWN, not yes — a
    # field that reads confident over a dead signal is the same shape as the
    # clean=yes lie the `gone` state exists to prevent. Reaping is already
    # disabled in that state; `?` also fails the reapable gate below.
    if [[ "$LIVE_CWDS" == *__UNKNOWN__* ]]; then
        idle="?"
    else
        idle="yes"
        while read -r c; do
            [[ -z "$c" ]] && continue
            case "$(realpath "$c" 2>/dev/null)/" in
                "$(realpath "$path" 2>/dev/null)"/*) idle="no" ;;
            esac
        done <<< "$LIVE_CWDS"
    fi

    unlocked="yes"
    grep -qxF "$path" <<< "$LOCKED" && unlocked="no"

    # nested: does this worktree contain another repo's worktree holding work
    # that removing it would destroy? See the header — neither `clean` nor `git
    # worktree remove` can see one. `--check` mutates nothing and exits non-zero
    # when anything nested is unsaved, so an absent or failing script reads as
    # "no" and keeps the worktree, never as "yes".
    if [[ ! -x "$HYDRATE" ]]; then
        nested="n/a"
    elif "$HYDRATE" --check --quiet "$path" >/dev/null 2>&1; then
        nested="ok"
    else
        nested="no"
    fi

    if [[ "$landed" == "yes" && "$clean" == "yes" && "$idle" == "yes" && "$unlocked" == "yes" \
          && ( "$nested" == "ok" || "$nested" == "n/a" ) ]]; then
        verdict="REAPABLE"
        # Two parallel arrays rather than "$path|$branch" packing: a literal `|`
        # in a manually-created worktree path or branch name would mis-split on
        # ${entry##*|}, and this is the destructive path. open_worktree.sh's
        # topic validation prevents it for anything this tooling creates, but
        # the reaper also runs against worktrees it did not create.
        reapable+=("$path")
        reapable_branch+=("$branch")
    else
        verdict="keep"
    fi

    report+=$(printf '  %-34s %-22s landed=%-3s clean=%-3s idle=%-3s unlocked=%-3s nested=%-3s  %s\n' \
        "$(basename "$path")" "$branch" "$landed" "$clean" "$idle" "$unlocked" "$nested" "$verdict")
    report+=$'\n'
    if [[ -n "$blockers" ]]; then
        n=0
        while IFS= read -r b; do
            n=$((n+1))
            [[ $n -le 5 ]] && report+="      ignored, would be destroyed: $b"$'\n'
        done <<< "$blockers"
        [[ $n -gt 5 ]] && report+="      … and $((n-5)) more (git -C $path status --ignored)"$'\n'
    fi
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')

# --- ORPHAN BRANCHES (TD-032) -------------------------------------------------
# A branch left behind when its worktree moved on (a worker's `checkout -b` per PR,
# then `checkout --detach` at its next run) has no worktree, so the loop above never
# sees it. Branch refs are ONE namespace across every worktree and workers reuse
# names, so the gate is never "its remote head is gone": a reused name can carry new
# unpushed commits by now. A branch is deletable only when ALL hold:
#   - not the default branch, and not `main` (with the anchor parked elsewhere, the
#     default has no worktree and reads as contained — it must never be a candidate);
#   - not checked out in ANY worktree, and not the branch a rebase or a bisect in any
#     worktree started from (both detach HEAD, so `worktree list` stops naming it; the
#     name is in rebase-merge/ or rebase-apply/head-name, or BISECT_START);
#   - contained: every commit already on origin/<default> (`rev-list BASE..b` is 0) —
#     stricter than the worktree loop's `empty` (no net file change), because commits
#     that net to nothing are still history someone made — OR landed=yes by the same
#     scoped predicate the worktree loop uses (landed_of).
# `--reap` deletes with `git update-ref -d <ref> <sha seen here>`: a compare-and-delete,
# so a branch that gained a commit between this report and the delete survives.
DEFAULT_BRANCH="${BASE#origin/}"
held="$(git worktree list --porcelain | awk '/^branch refs\/heads\//{print substr($0,19)}')"
while read -r wt; do
    [[ -z "$wt" ]] && continue
    gd="$(git -C "$wt" rev-parse --absolute-git-dir 2>/dev/null)" || continue
    for f in "$gd/rebase-merge/head-name" "$gd/rebase-apply/head-name" "$gd/BISECT_START"; do
        [[ -r "$f" ]] && held+=$'\n'"$(sed 's#^refs/heads/##' "$f")"
    done
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')

orphans=()
orphan_sha=()
orphan_report=""
while IFS=' ' read -r ob osha; do
    [[ -z "$ob" ]] && continue
    [[ "$ob" == "$DEFAULT_BRANCH" || "$ob" == "main" ]] && continue
    grep -qxF -- "$ob" <<< "$held" && continue
    if [[ "$(git rev-list --count "$BASE..$osha" 2>/dev/null)" == "0" ]]; then
        state="contained"
    else
        state="$(landed_of "$osha")"
        [[ "$state" == "empty" ]] && state="net-empty"   # commits, no net change: kept
    fi
    if [[ "$state" == "contained" || "$state" == "yes" ]]; then
        verdict="DELETABLE"; orphans+=("$ob"); orphan_sha+=("$osha")
    else
        verdict="keep"
    fi
    orphan_report+=$(printf '  %-58s landed=%-9s %s' "$ob" "$state" "$verdict")
    orphan_report+=$'\n'
done < <(git for-each-ref --format='%(refname:short) %(objectname)' refs/heads/)

if [[ -z "$report" && -z "$orphan_report" ]]; then
    [[ $QUIET -eq 0 ]] && echo "no worktrees besides the main checkout, and no branches without one"
    exit 0
fi

if [[ ${#reapable[@]} -eq 0 && ${#orphans[@]} -eq 0 && $QUIET -eq 1 ]]; then
    exit 0   # hook mode: silent unless there is something to do
fi

[[ -n "$report" ]] && { echo "worktrees:"; printf '%s' "$report"; }
[[ -n "$orphan_report" ]] && { echo "branches without a worktree:"; printf '%s' "$orphan_report"; }

if [[ ${#reapable[@]} -eq 0 && ${#orphans[@]} -eq 0 ]]; then
    exit 0
fi

if [[ $REAP -eq 0 ]]; then
    echo
    echo "${#reapable[@]} reapable worktree(s), ${#orphans[@]} deletable branch(es) — run scripts/reap_worktrees.sh --reap to remove"
    exit 0
fi

for i in "${!orphans[@]}"; do
    ob="${orphans[$i]}"
    # Re-check at the destructive step: a worktree may have checked it out since.
    if git worktree list --porcelain | grep -qxF "branch refs/heads/$ob"; then
        echo "branch $ob is checked out now — left alone" >&2
        continue
    fi
    if git update-ref -d "refs/heads/$ob" "${orphan_sha[$i]}" 2>/dev/null; then
        echo "deleted branch $ob (was ${orphan_sha[$i]:0:7})"
    else
        echo "branch $ob moved since the report — left alone" >&2
    fi
done

for i in "${!reapable[@]}"; do
    path="${reapable[$i]}"
    branch="${reapable_branch[$i]}"
    echo
    # Take the nested siblings out FIRST, through their own repos. The verdict
    # above already said they hold nothing unsaved, but it said so at report
    # time and this is the destructive step, so the exit status is re-checked
    # rather than trusted: between the two, a session could have written a file.
    # A refusal here skips the parent entirely — leaving a worktree behind costs
    # disk, and removing it over a live nested sibling costs that sibling's work
    # (see the header's `nested` note).
    if [[ -x "$HYDRATE" ]] && ! "$HYDRATE" --dehydrate "$path"; then
        echo "could not dehydrate $path — left alone" >&2
        continue
    fi
    if git worktree remove "$path" 2>&1; then
        echo "removed worktree $path"
        # The per-topic workspace file (generate_workspace.sh --topic) lives at
        # the clone root, not inside the worktree, so removing the tree does not
        # take it with it. Left behind it is a workspace whose every root is a
        # dead path -- and it is gitignored, so nothing else would ever report
        # it. Both spellings, because a topic named after the repo gets the
        # disambiguated one. That suffix uses `+`, which is outside the topic
        # charset, so neither name can belong to a DIFFERENT live topic -- a
        # `.topic` suffix could, making this rm delete another topic's file.
        rm -f "$CLONE_ROOT/$(basename "$path").code-workspace" \
              "$CLONE_ROOT/$(basename "$path")+topic.code-workspace"
        # Squash merges never register as merged, so -d would refuse; the landed
        # check above is what makes -D safe here.
        if [[ "$branch" != "HEAD" && "$branch" != "?" ]]; then
            git branch -D "$branch" >/dev/null 2>&1 && echo "deleted branch $branch"
        fi
    else
        echo "could not remove $path — left alone" >&2
    fi
done

GENWS="$(sibling generate_workspace.sh)"
[[ -x "$GENWS" ]] && "$GENWS" >/dev/null
exit 0
