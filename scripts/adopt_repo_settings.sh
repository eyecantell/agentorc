#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/adopt_repo_settings.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Check (default) or apply the GitHub repo settings the cadence assumes (TD-034).
#
#   adopt_repo_settings.sh [--check|--apply] [owner/repo]
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
# The repo defaults to the one `gh` resolves from the current checkout's origin.
#
# WHY THIS IS NOT A sync.sh STEP
# A sync tool mutating a remote's settings is the wrong shape, and auto-delete
# is a per-repo decision: it also deletes the branch a *stacked* PR is based
# on, and GitHub then retargets the stacked PR onto the default branch (§4).
# A repo that stacks PRs may want auto-delete off — run --check, read, decide.
set -u

MODE=check
REPO=""
for a in "$@"; do
    case "$a" in
        --check) MODE=check ;;
        --apply) MODE=apply ;;
        -h|--help) sed -n '4,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $a" >&2; exit 2 ;;
        *) REPO="$a" ;;
    esac
done

if ! command -v gh >/dev/null 2>&1; then
    echo "cannot verify: gh not installed" >&2; exit 2
fi
if [ -z "$REPO" ]; then
    REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)" || true
    if [ -z "$REPO" ]; then
        echo "cannot verify: no owner/repo given and gh cannot resolve one from this checkout" >&2; exit 2
    fi
fi

# setting=cadence value, in the order the report prints them
WANT=(
    "allow_squash_merge=true"
    "allow_merge_commit=false"
    "allow_rebase_merge=false"
    "delete_branch_on_merge=true"
)

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
