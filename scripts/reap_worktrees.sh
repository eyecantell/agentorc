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
#   clean    no modified AND no untracked files (scratch must never be reaped)
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
# USAGE
#   scripts/reap_worktrees.sh            # report only
#   scripts/reap_worktrees.sh --reap     # remove the ones that pass every check
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

CLONE_ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)" || exit 0
cd "$CLONE_ROOT" || exit 0

HYDRATE="$CLONE_ROOT/scripts/hydrate_worktree.sh"

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
LIVE_CWDS="$(python3 - <<'PY' 2>/dev/null
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path("scripts").resolve()))
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
    from check_anchor import live_sessions
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
        base="$(git merge-base origin/main "$branch" 2>/dev/null)"
        if [[ -n "$base" ]]; then
            files=()
            mapfile -d '' -t files < <(git diff --name-only -z "$base" "$branch" 2>/dev/null)
            if [[ ${#files[@]} -eq 0 ]]; then
                landed="empty"
            elif git diff --quiet origin/main "$branch" -- "${files[@]}" 2>/dev/null; then
                landed="yes"
            fi
        fi
    fi

    # clean: untracked scratch counts, because reaping it destroys it.
    # A `git status` that FAILS (directory moved or deleted out from under the
    # worktree registration) produces empty output too, which would read as
    # clean. Distinguish on exit status and report it as its own state — inert
    # today because `landed` gates first, but a check that cannot fail loudly
    # is one refactor away from being trusted.
    if git -C "$path" rev-parse --git-dir >/dev/null 2>&1; then
        if [[ -z "$(git -C "$path" status --porcelain --untracked-files=all 2>/dev/null)" ]]; then
            clean="yes"
        else
            clean="no"
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
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')

if [[ -z "$report" ]]; then
    [[ $QUIET -eq 0 ]] && echo "no worktrees besides the main checkout"
    exit 0
fi

if [[ ${#reapable[@]} -eq 0 && $QUIET -eq 1 ]]; then
    exit 0   # hook mode: silent unless there is something to do
fi

echo "worktrees:"
printf '%s' "$report"

if [[ ${#reapable[@]} -eq 0 ]]; then
    exit 0
fi

if [[ $REAP -eq 0 ]]; then
    echo
    echo "${#reapable[@]} reapable — run scripts/reap_worktrees.sh --reap to remove"
    exit 0
fi

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

[[ -x "$CLONE_ROOT/scripts/generate_workspace.sh" ]] && "$CLONE_ROOT/scripts/generate_workspace.sh" >/dev/null
exit 0
