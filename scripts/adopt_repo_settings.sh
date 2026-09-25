#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/adopt_repo_settings.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Check (default) or apply the GitHub repo settings the cadence assumes (TD-034).
#
#   adopt_repo_settings.sh [--check|--apply] [--no-auto-delete] [owner/repo]
#
# The cadence mandates squash merges (cadence.md §4) and recommends GitHub's
# "Automatically delete head branches" (§4, "Give the rule teeth"). Both live
# only in prose, and prose is not re-read: measured 2026-08-27, auto-delete was
# off in 2 of 4 repos on one machine's roster, and one repo still allowed
# merge-commit and rebase merges — 83 merged heads accumulated on a remote
# before anyone noticed. This script makes the claim checkable.
#
#   --check   (default) print each setting, current vs cadence; exit 1 on drift,
#             0 when clean, 2 when gh cannot answer (not authenticated, offline,
#             not a GitHub repo). Mutates nothing.
#   --apply   PATCH the drifted settings, then re-read and confirm. Idempotent:
#             a clean repo is left untouched (no API write at all).
#
#   --no-auto-delete   leave "Automatically delete head branches" out of both the check
#             and the apply (a repo that stacks PRs, below).
#
# The repo defaults to the current checkout's `origin` remote, parsed from its URL — never
# `gh repo view`, which prefers an `upstream` remote and honours `gh repo set-default`, so
# in a fork clone it named the PARENT, and --apply would have patched a repo that is not
# this one (TD-058). A setting gh reads back as null (a non-admin cannot see it) is
# "cannot verify", exit 2 — never drift.
#
# WHY THIS IS NOT A sync.sh STEP
# A sync tool mutating a remote's settings is the wrong shape, and auto-delete
# is a per-repo decision: it also deletes the branch a *stacked* PR is based
# on, and GitHub then retargets the stacked PR onto the default branch (§4).
# A repo that stacks PRs may want auto-delete off — run --check, read, decide.
set -u

MODE=check
REPO=""
NO_AUTO_DELETE=0
for a in "$@"; do
    case "$a" in
        --check) MODE=check ;;
        --apply) MODE=apply ;;
        --no-auto-delete) NO_AUTO_DELETE=1 ;;
        -h|--help) awk 'NR>3 && /^[^#]/ {exit} NR>3 {sub(/^# ?/, ""); print}' "$0"; exit 0 ;;
        -*) echo "unknown option: $a" >&2; exit 2 ;;
        *) REPO="$a" ;;
    esac
done

if ! command -v gh >/dev/null 2>&1; then
    echo "cannot verify: gh not installed" >&2; exit 2
fi
if [ -z "$REPO" ]; then
    url="$(git remote get-url origin 2>/dev/null)" || url=""
    url="${url%/}"
    REPO="$(printf '%s\n' "$url" | sed -nE 's#^(https?://([^@/]+@)?|ssh://git@|git@)github\.com[:/]([^/]+/[^/]+)$#\3#p' | sed -E 's#\.git$##')"
    if [ -z "$REPO" ]; then
        # never echo a credential an https origin may carry (user:token@)
        shown="$(printf '%s' "$url" | sed -E 's#//[^@/]+@#//#')"
        echo "cannot verify: no owner/repo given and this checkout's origin is not a GitHub repo (${shown:-no origin})" >&2; exit 2
    fi
    echo "(repo from this checkout's origin: $REPO)"
fi

# setting=cadence value, in the order the report prints them
WANT=(
    "allow_squash_merge=true"
    "allow_merge_commit=false"
    "allow_rebase_merge=false"
)
[ "$NO_AUTO_DELETE" = 1 ] || WANT+=("delete_branch_on_merge=true")

read_settings() {  # -> one "name=value" line per setting, or non-zero
    gh api "repos/$REPO" --jq '
        "allow_squash_merge=\(.allow_squash_merge)",
        "allow_merge_commit=\(.allow_merge_commit)",
        "allow_rebase_merge=\(.allow_rebase_merge)",
        "delete_branch_on_merge=\(.delete_branch_on_merge)"' 2>/dev/null
}

compare() {  # prints the table from $1 (the read_settings output); sets DRIFT (array of -F args)
    DRIFT=()
    for w in "${WANT[@]}"; do
        name="${w%%=*}"; want="${w#*=}"
        have="$(printf '%s\n' "$1" | sed -n "s/^$name=//p")"
        if [ "$have" = "$want" ]; then mark="ok"
        else mark="DRIFT"; DRIFT+=("-F" "$name=$want"); fi
        printf '  %-6s %-24s %-6s (cadence: %s)\n' "$mark" "$name" "${have:-?}" "$want"
    done
}

current="$(read_settings)"
if [ -z "$current" ]; then
    echo "cannot verify: gh api repos/$REPO failed (not authenticated, offline, or not a GitHub repo)" >&2; exit 2
fi
unreadable="$(printf '%s\n' "$current" | sed -n 's/=null$//p' | tr '\n' ' ')"
if [ -n "$unreadable" ]; then
    echo "cannot verify: gh read ${unreadable% } as null on $REPO — a non-admin cannot see these; ask an admin to run this" >&2; exit 2
fi
echo "$REPO — merge settings vs cadence.md §4:"
compare "$current"

if [ "${#DRIFT[@]}" -eq 0 ]; then
    echo "clean: nothing to change"; exit 0
fi
if [ "$MODE" = check ]; then
    echo "drift: re-run with --apply to set the cadence values (auto-delete has a stacked-PR caveat, §4)"
    exit 1
fi

echo "applying: gh api -X PATCH repos/$REPO ${DRIFT[*]}"
if ! gh api -X PATCH "repos/$REPO" "${DRIFT[@]}" >/dev/null; then
    echo "apply failed: gh api PATCH repos/$REPO returned non-zero (need admin on the repo?)" >&2; exit 2
fi
after="$(read_settings)"
echo "after:"
compare "$after"
if [ "${#DRIFT[@]}" -eq 0 ]; then echo "applied"; exit 0; fi
echo "apply did not stick: settings still drift after PATCH" >&2; exit 1
