#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/hydrate_worktree.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Bring a worktree's UNTRACKED dependencies into it — and take them back out
# before it is removed.
#
# THE HOLE THIS FILLS
# -------------------
# `git worktree add` gives you exactly what the repo TRACKS. Everything a
# session needs that git deliberately does not track is therefore missing from
# every worktree the cadence tells you to work in (§1):
#
#   .claude/settings.local.json   gitignored per-clone/per-machine settings. It
#                                 holds `autoMemoryDirectory`, so a worktree
#                                 session writes memory to the DEFAULT location
#                                 instead of the repo's — silently, except that
#                                 the memory guard's SessionStart hook notices
#                                 and warns. It holds the permission allowlist
#                                 too, so a worktree re-prompts for everything
#                                 the main checkout already trusts.
#
#   nested repos                  in a CONSTELLATION (cadence.md §9: one project,
#                                 several repos, cadence installed once in a
#                                 home) the siblings are separate clones with
#                                 their own remotes, gitignored inside the home.
#                                 A worktree of the home therefore contains the
#                                 cadence and none of the content. §9 already
#                                 measured this shape: 19 of 25 sessions ran in
#                                 the umbrella while the commits landed almost
#                                 entirely in two siblings. §1 says isolate into
#                                 a worktree; §9 says the work is in the
#                                 siblings; and until this script existed those
#                                 two instructions could not both be followed.
#
# So the cost of §1's isolation was paid in full and its benefit was only ever
# partial. Hydration is the missing half: the worktree is created by git, then
# completed by this.
#
# WHY A SEPARATE SCRIPT RATHER THAN A FEW LINES IN open_worktree.sh
# -----------------------------------------------------------------
# open_worktree.sh is not the only way a worktree appears. `claude --worktree`
# (cadence.md §1's own first suggestion) creates one directly through the tool,
# never touching this repo's scripts, and a hand-rolled `git worktree add` is
# always available. A hydration step that lives inside the creator can only ever
# fix the worktrees that creator made. Standalone and idempotent, this repairs
# any of them — run it inside a worktree that came from anywhere and it brings
# that worktree up to spec.
#
# CONFIGURATION — DETECTED REPOS, CONFIGURED MODES
# -------------------------------------------------
# Nested git repos are FOUND, not declared: every hydrate scans the checkout and
# names any it finds that the config does not mention, with the line to add. So
# a newly-cloned sibling announces itself instead of being silently missing from
# every worktree, and nobody has to keep a list in their head.
#
# What detection cannot do is pick the MODE, which is why the config is not
# merely a cache of the scan. worktree-vs-link is a judgement about a specific
# repo — the sibling every topic edits wants its own branch, the one with a
# 400MB install and a running dev server wants the shared clone — and nothing on
# disk tells them apart. A single auto-chosen default would either put a branch
# in every sibling for every topic or hand every topic the same HEAD, and both
# are wrong often enough to be worth one line per repo. Detection also cannot
# tell a sibling from a vendored dependency or a scratch clone somebody left in
# the tree. So: the scan proposes, the config decides.
#
# Single-repo projects need no config and get no file; with no
# docs/nested-repos.txt in the home checkout, only the settings.local.json half
# runs. A constellation writes one line per nested repo, path first, then mode:
#
#     # docs/nested-repos.txt — paths are relative to the checkout root
#     guardians-docs      worktree
#     guardians-demo      link
#     guardians-landing   skip
#
#   worktree  Give the nested repo its own worktree, on a branch of the same
#             name as the topic, nested at the same relative path. Full §1
#             isolation: two topics editing the same sibling get separate HEADs
#             and indexes. Costs a branch in that sibling per topic even when
#             untouched, and a fresh worktree has no build artifacts — an app
#             repo needs its install step re-run (and, if it serves, a port of
#             its own).
#
#   link      Symlink the home checkout's copy into the worktree. Instant, no
#             duplication, and everything already built in that clone —
#             node_modules, a venv, a running dev server — just works. Buys NO
#             isolation: every topic shares that clone's HEAD and index, which
#             is the collision §1 exists to prevent. Correct for a sibling you
#             read (or one whose build cost dwarfs the collision risk), wrong
#             for one two topics will both be committing to.
#
#   skip      Not present in worktrees. The honest setting for a sibling this
#             kind of work never touches; it keeps the branch noise and the
#             disk out of every unrelated topic.
#
# The mode is per-repo because the right answer differs per repo inside a single
# project: a docs sibling that every topic edits wants `worktree`, and the app
# sibling with a 400MB install and a dev server wants `link`.
#
# DEHYDRATION IS NOT OPTIONAL, AND HERE IS THE PROOF
# ---------------------------------------------------
# Measured (git 2.39.5), outer repo with `/nested/` gitignored, a nested
# worktree of a sibling living inside a worktree of the outer repo:
#
#   git -C <outer-worktree> status --porcelain -uall   -> EMPTY. The nested
#     worktree matches the ignore rule, so every "is this worktree clean"
#     check — including reap_worktrees.sh's — reads clean with a whole sibling
#     repo's uncommitted work sitting inside it.
#
#   git worktree remove <outer-worktree>               -> SUCCEEDS. Not a
#     refusal, not a warning: it deletes the directory tree, uncommitted
#     sibling work included, and leaves the sibling holding a `prunable`
#     registration pointing at nothing.
#
# reap_worktrees.sh runs unattended from the post-merge hook. So `worktree` mode
# without a dehydration step does not merely leak — it hands the most
# destructive script in the system a way to destroy work in a repo it was never
# looking at. That is why --dehydrate exists, why it REFUSES on anything
# unsaved, and why the reaper gates on its exit status rather than its output.
#
# The symlink case fails in the opposite, harmless direction and is worth
# knowing because it looks like a bug: gitignore patterns written
# directory-only (`/guardians-docs/`, trailing slash) do NOT match a symlink,
# because a symlink is a file. So a linked sibling shows up as `?? guardians-docs`
# forever, which makes the worktree permanently unclean and therefore
# permanently unreapable. Hydration detects that and prints the one-line fix for
# the home's .gitignore; it does not edit .gitignore itself, and it does not
# write to .git/info/exclude — measured, a linked worktree's OWN
# .git/worktrees/<name>/info/exclude is not honored at all, so the only exclude
# file that would work is the shared one, and silently reaching into shared git
# state to paper over a rule the repo can state for itself is the wrong trade.
#
# NOTHING IS MARKED; EVERYTHING IS COMPUTED
# ------------------------------------------
# --dehydrate does not read a manifest of what --hydrate created. It finds
# nested worktrees by looking for the `.git` FILE that every linked worktree
# has (a directory containing one is a worktree of some repo, and that repo's
# path is written inside it), and links by testing for a symlink. Same reasoning
# as reap_worktrees.sh's landed-ness: a computed fact cannot go stale, while a
# manifest left behind by a crashed session can — and it picks up nested
# worktrees made by hand, which a manifest never would.
#
# USAGE
#   scripts/hydrate_worktree.sh [<worktree>]              # complete it (default: cwd's)
#   scripts/hydrate_worktree.sh --dehydrate [<worktree>]  # undo, refusing on unsaved work
#   scripts/hydrate_worktree.sh --check [<worktree>]      # dry run of --dehydrate; mutates nothing
#   scripts/hydrate_worktree.sh --quiet ...               # print only problems
#
# --check is a dry run of --dehydrate specifically, not a general "is this
# hydrated?" report, because that is the question with a caller: reap_worktrees.sh
# needs to know whether a worktree can be removed without destroying anything,
# and needs to know it without mutating the worktree it is only reporting on.
#
# Idempotent in both directions: re-running --hydrate adds only what is missing
# and never replaces something already there.
#
# Exit: 0 = the worktree is in the requested state. 1 = it is not, and the
# reason is printed (unsaved work in a nested repo, a sibling that could not be
# added, a config line that does not parse). --dehydrate's exit status is the
# contract reap_worktrees.sh gates removal on, so it is 0 ONLY when nothing this
# script created is left behind.

set -uo pipefail

MODE=hydrate
QUIET=0
ALL=0
TARGET=""
for a in "$@"; do
    case "$a" in
        --check)     MODE=check ;;
        --dehydrate) MODE=dehydrate ;;
        --detect)    MODE=detect ;;
        --hook)      MODE=hook; QUIET=1 ;;
        --all)       ALL=1 ;;
        --quiet)     QUIET=1 ;;
        # Same awk as reap_worktrees.sh: stop at the first non-comment line rather
        # than a fixed count, which here was printing eight lines PAST the block
        # (set -uo pipefail and the variable defaults) into the help text.
        -h|--help)   awk 'NR>1 && /^[^#]/ && NF {exit} {print}' "$0"; exit 0 ;;
        -*)          echo "unknown option: $a" >&2; exit 2 ;;
        *)  if [[ -n "$TARGET" ]]; then
                echo "error: more than one worktree given ('$TARGET', '$a')" >&2; exit 2
            fi
            TARGET="$a" ;;
    esac
done

# --- --all: every worktree of this checkout ------------------------------------
# The migration path. Hydration was added to a system whose consumers already had
# worktrees open, and per-worktree-by-hand is a step nobody is prompted to take,
# so a repo can sit half-migrated indefinitely with no signal. Re-invokes rather
# than looping internally, so one worktree failing does not abandon the rest, and
# each gets its own clearly-labelled block of output.
if [[ $ALL -eq 1 ]]; then
    [[ -n "$TARGET" ]] && { echo "error: --all takes no worktree argument" >&2; exit 2; }
    root="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)" || {
        echo "error: not in a git repo" >&2; exit 2
    }
    allrc=0
    while read -r p; do
        [[ -z "$p" ]] && continue
        [[ "$(realpath "$p" 2>/dev/null)" == "$(realpath "$root")" ]] && continue
        args=()
        [[ "$MODE" != hydrate ]] && args+=("--$MODE")
        [[ $QUIET -eq 1 ]] && args+=(--quiet)
        "$0" "${args[@]}" "$p" || allrc=1
    done < <(git -C "$root" worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')
    exit $allrc
fi

say()  { [[ $QUIET -eq 1 ]] || echo "$@"; }
warn() { echo "$@" >&2; }

# --- locate the worktree and its clone root -----------------------------------
# Both are resolved through git rather than assumed from the path, so this works
# from inside the worktree, from the main checkout with a path argument, and
# from anywhere with an absolute path.
[[ -n "$TARGET" ]] || TARGET="$PWD"
# hook mode's fail-open promise starts HERE, not at its own block 450 lines down:
# these two resolution failures sit above it, so without this the SessionStart
# hook could exit 2 on a cwd that is missing or outside a repo — exactly the
# "a guard that can fail a session start" the block below says it will never be.
hook_ok() { [[ "$MODE" == hook ]] && exit 0; }
[[ -d "$TARGET" ]] || { hook_ok; warn "error: no such directory: $TARGET"; exit 2; }
WT="$(cd "$TARGET" && git rev-parse --show-toplevel 2>/dev/null)" || {
    hook_ok; warn "error: $TARGET is not inside a git worktree"; exit 2
}
COMMON="$(cd "$WT" && cd "$(git rev-parse --git-common-dir)" && pwd)" || exit 2
CLONE_ROOT="$(dirname "$COMMON")"

IS_MAIN=0
[[ "$(realpath "$WT")" == "$(realpath "$CLONE_ROOT")" ]] && IS_MAIN=1

if [[ $IS_MAIN -eq 1 && "$MODE" != detect && "$MODE" != hook ]]; then
    # The main checkout is where the untracked things already live. Hydrating it
    # would mean linking it to itself; dehydrating it would mean deleting the
    # originals. Refusing is the only safe answer, and this is reachable by
    # accident — running the script with no argument from the main checkout.
    #
    # The read-only modes are exempt, and that exemption is the fix for a real
    # chicken-and-egg: the config is written during adoption, when no worktree
    # exists yet, so a detector that only ran inside worktrees could never fire
    # when it was actually needed.
    warn "error: $WT is the main checkout, not a worktree — nothing to hydrate, and dehydrating it would delete the originals"
    warn "       (--detect works here, and is how you write docs/nested-repos.txt in the first place)"
    exit 2
fi

CONFIG="$CLONE_ROOT/docs/nested-repos.txt"
PARSED="$(mktemp)"
trap 'rm -f "$PARSED"' EXIT
rc=0
MISSING=()   # read-only modes accumulate what hydration would add

# --- config -------------------------------------------------------------------
# Emits `path<TAB>mode` for valid lines and fails the run on any line it cannot
# parse: a typo'd mode must not silently become "skip", because a silent skip
# produces a worktree that looks hydrated and isn't.
#
# Called with a plain redirect, NEVER through a pipe or process substitution —
# those run the function in a subshell, where its `rc=1` would be discarded and
# a malformed config would exit 0. (Written that way first; the tests pin it.)
read_config() {
    local n=0 line path mode
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"     # ltrim
        line="${line%"${line##*[![:space:]]}"}"     # rtrim
        [[ -z "$line" ]] && continue
        # The MODE is the last whitespace-separated word and the PATH is
        # everything before it, rather than a two-field split. Same syntax for
        # the common case, but a directory name containing a space parses
        # correctly instead of erroring out — and this is the one file in the
        # feature a human types by hand, so it should not have a footgun that
        # only fires on machines whose paths happen to contain a space.
        mode="${line##*[[:space:]]}"
        path="${line%"$mode"}"
        path="${path%"${path##*[![:space:]]}"}"     # rtrim the separator
        if [[ -z "$path" || "$path" == "$line" ]]; then
            warn "  ERROR     $CONFIG:$n — expected '<path> <mode>', got '$line'"
            rc=1; continue
        fi
        case "$mode" in
            worktree|link|skip) ;;
            *) warn "  ERROR     $CONFIG:$n — unknown mode '$mode' (want worktree, link or skip)"
               rc=1; continue ;;
        esac
        case "$path" in
            /*|../*|*/../*|*/..|..)
                warn "  ERROR     $CONFIG:$n — path must be relative to the checkout root and must not escape it: '$path'"
                rc=1; continue ;;
        esac
        printf '%s\t%s\n' "$path" "$mode"
    done < "$CONFIG"
}

# --- per-clone settings: .claude/settings.local.json --------------------------
# A SYMLINK, not a copy. The file is live per-machine state — grant a permission
# in one worktree and every other should have it, and if the memory directory
# moves, a copy keeps pointing at the old one. A copy would fix today's symptom
# and re-introduce it as drift, which is harder to see than the original bug. It
# stays gitignored either way: the entry sync.sh installs is the exact path
# `.claude/settings.local.json`, with no trailing slash, so it matches a symlink
# as readily as a file (unlike the directory-only sibling patterns above).
settings_local() {
    local src="$CLONE_ROOT/.claude/settings.local.json"
    local dst="$WT/.claude/settings.local.json"

    # detect/hook are READ-ONLY and must not touch this. Getting here through a
    # plain `!= hydrate` test would have made a SessionStart hook silently
    # DELETE the very symlink it exists to check for.
    if [[ "$MODE" == detect || "$MODE" == hook ]]; then
        if [[ ( -e "$src" || -L "$src" ) && ! -e "$dst" && ! -L "$dst" ]]; then
            MISSING+=(".claude/settings.local.json")
        fi
        return 0
    fi

    if [[ "$MODE" != hydrate ]]; then
        if [[ -L "$dst" ]]; then
            if [[ "$MODE" == check ]]; then
                say "  would unlink  .claude/settings.local.json"
            else
                rm -f "$dst"
                say "  unlinked  .claude/settings.local.json"
            fi
        elif [[ -e "$dst" ]]; then
            # Not ours to delete — someone, or a save-by-rename, put a real file
            # here. Leave it: it is gitignored per-clone state, so it costs
            # nothing, and removing it could throw away granted permissions.
            say "  kept      .claude/settings.local.json (a real file, not our symlink)"
        fi
        return 0
    fi

    if [[ ! -e "$src" && ! -L "$src" ]]; then
        say "  n/a       .claude/settings.local.json (the main checkout has none)"
        return 0
    fi
    if [[ -L "$dst" ]]; then
        say "  ok        .claude/settings.local.json -> $(readlink "$dst")"
        return 0
    fi
    if [[ -e "$dst" ]]; then
        # A regular file here means the link was replaced — editors and settings
        # writers commonly save by writing a temp file and renaming over the
        # target, which severs a symlink instead of following it. This worktree
        # then has a divergent copy and the shared one silently stops being
        # shared. Say so; do not overwrite, because the divergent copy may be
        # the newer one.
        warn "  WARN      $dst is a real file, not a link to the main checkout's — the two will drift"
        warn "            (a save-by-rename replaces a symlink instead of following it)"
        warn "            reconcile by hand, then: rm '$dst' && scripts/hydrate_worktree.sh '$WT'"
        return 1
    fi
    mkdir -p "$WT/.claude"
    ln -s "$src" "$dst"
    say "  linked    .claude/settings.local.json -> $src"
    return 0
}

# --- nested repos -------------------------------------------------------------

# The topic a nested worktree should be branched on: the parent worktree's own
# branch, so one name spans the project. A detached parent has no branch to
# borrow, so fall back to the directory name — which is what open_worktree.sh
# named it after in the first place.
TOPIC="$(git -C "$WT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
[[ -z "$TOPIC" || "$TOPIC" == HEAD ]] && TOPIC="$(basename "$WT")"

# Where a nested worktree should branch FROM, and — read as a branch name — the
# one branch dehydration must never delete. A constellation's siblings are
# independent clones and nothing guarantees they all call it `main`, so ask the
# remote rather than assume.
nested_base() {
    local repo="$1" ref
    ref="$(git -C "$repo" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)"
    if [[ -n "$ref" ]] && git -C "$repo" rev-parse --verify -q "$ref" >/dev/null; then
        echo "$ref"; return 0
    fi
    if git -C "$repo" rev-parse --verify -q origin/main >/dev/null; then
        echo "origin/main"; return 0
    fi
    return 1
}

# A nested worktree is a directory whose `.git` is a FILE (`gitdir: ...`), which
# is how git marks every linked worktree. Symlinks are not followed (no -L), so
# a `link`ed sibling is never descended into — which would otherwise walk back
# into the main checkout and start reporting other topics' worktrees.
#
# No -mindepth, for the reason spelled out on detect_nested: -mindepth suppresses
# -prune along with every other predicate, so `-mindepth 2` left a node_modules
# sitting directly under $WT unpruned and walked the whole tree. This function
# had that bug after detect_nested was fixed for it — the same mistake twice in
# one file, caught by review rather than by the fix. The parent worktree's own
# `.git` file is excluded by pruning its exact path instead of by depth.
#
# The node_modules prune is not only about speed here. A package that is itself
# checked out as a worktree of some unrelated repo has a `.git` FILE, and this
# list feeds `git worktree remove` — so an unpruned walk could delete a worktree
# belonging to a project that has nothing to do with this one.
find_nested_worktrees() {
    find "$WT" -maxdepth 4 \
        \( -name node_modules -o -path "$WT/.git" -o -name .git -type d \) -prune -o \
        -name .git -type f -print 2>/dev/null | sed 's:/\.git$::' | sort
}

# Unsaved = anything a removal would destroy that is not recoverable elsewhere:
# a dirty tree (tracked or untracked), or committed work whose CONTENT is not
# on the sibling default branch. Both are checked because they strand
# differently — the first is gone forever, the second survives on a branch
# nobody will think to look for.
#
# The second test is content-scoped, not ancestry, for the reason
# reap_worktrees.sh spends its longest comment on: §4 squash merges sever
# ancestry, so `rev-list --count base..branch` counts every landed branch as
# unmerged FOREVER. Written with rev-list first, and the fixture caught it
# immediately — a nested sibling whose work had been squash-merged still read as
# unsaved, which would have made the parent worktree permanently unreapable.
# Compare only the files the branch itself touched (plain `diff base branch`
# reports the base's unrelated advances as differences, so a landed branch reads
# as unlanded the moment anything else merges).
#
# The file list is NUL-separated into an array and passed quoted, and the
# non-empty guard is load-bearing: `git diff --quiet` with a pathspec matching
# nothing exits 0, so an unguarded empty list would silently report "saved"
# without comparing anything. That exact bug reached production in the reaper;
# it is not going to be reintroduced here, in the function guarding the same
# deletion.
nested_unsaved() {
    local w="$1" out base mb files
    out="$(git -C "$w" status --porcelain --untracked-files=all 2>/dev/null)"
    if [[ -n "$out" ]]; then
        echo "uncommitted changes ($(grep -c '' <<< "$out") paths)"; return 0
    fi
    # Everything below asks about HEAD, never about a branch NAME, and that is a
    # correction rather than a style choice. The first version gated this whole
    # section on `[[ -n "$br" && "$br" != HEAD ]]`, so a nested worktree on a
    # DETACHED head skipped the content comparison entirely and was reported
    # safe. Adversarial review reproduced the consequence through the real
    # unattended path: a detached nested worktree with a clean tree and one
    # unpushed commit read `nested=ok`, `--reap` removed it, and the commit
    # survived only as an unreachable object awaiting gc — silently, with no
    # diagnostic at any stage. Detached is not a rare state here either: this
    # script advertises that it repairs worktrees made by hand, and
    # `git worktree add --detach` is how those are often made.
    if ! base="$(nested_base "$w")"; then
        # Cannot compare, so cannot claim it is saved. "I don't know" routes to
        # "keep", the same way reap's __UNKNOWN__ registry does.
        echo "cannot tell what it holds (no origin/HEAD and no origin/main to compare against)"
        return 0
    fi
    mb="$(git -C "$w" merge-base "$base" HEAD 2>/dev/null)"
    if [[ -z "$mb" ]]; then
        echo "cannot tell what it holds (no merge base with $base)"; return 0
    fi
    files=()
    mapfile -d '' -t files < <(git -C "$w" diff --name-only -z "$mb" HEAD 2>/dev/null)
    # No files touched: a worktree opened and never written in. Nothing to lose.
    [[ ${#files[@]} -eq 0 ]] && return 1
    if ! git -C "$w" diff --quiet "$base" HEAD -- "${files[@]}" 2>/dev/null; then
        echo "${#files[@]} file(s) whose content is not on $base"; return 0
    fi
    return 1
}

dehydrate_nested() {
    local any=0 w src br base reason
    while read -r w; do
        [[ -z "$w" ]] && continue
        any=1
        if reason="$(nested_unsaved "$w")"; then
            warn "  REFUSE    ${w#"$WT"/} — $reason"
            warn "            commit and push it, or remove it by hand once you are sure"
            rc=1
            continue
        fi
        if [[ "$MODE" == check ]]; then
            say "  would remove  ${w#"$WT"/}"
            continue
        fi
        # Remove through the OWNING repo so the registration goes with the
        # directory. Deleting the directory alone is what leaves the `prunable`
        # entries described in the header.
        src="$(cd "$w" && cd "$(git rev-parse --git-common-dir)" && pwd)"
        src="$(dirname "$src")"
        br="$(git -C "$w" rev-parse --abbrev-ref HEAD 2>/dev/null)"
        base="$(nested_base "$src" || true)"
        if git -C "$src" worktree remove "$w" >/dev/null 2>&1; then
            say "  removed   ${w#"$WT"/} (worktree of $(basename "$src"))"
            # Squash merges sever ancestry, so -d refuses on branches that did
            # land; nested_unsaved() above already proved this branch holds
            # nothing that is not upstream, which is what makes -D safe. Never
            # the sibling's default branch, though — a nested worktree left on
            # `main` is not a topic branch, and -D would delete the repo's main.
            if [[ -n "$br" && "$br" != HEAD && "$br" != "${base#origin/}" ]]; then
                git -C "$src" branch -D "$br" >/dev/null 2>&1 && say "  deleted   branch $br in $(basename "$src")"
            fi
        else
            warn "  FAILED    could not remove nested worktree $w"
            rc=1
        fi
    done < <(find_nested_worktrees)

    # Links: only ever a symlink, so `rm` touches the link and never the target.
    # Driven off the config rather than a scan, because an arbitrary symlink in
    # a worktree is somebody else's, not ours.
    local path mode
    while IFS=$'\t' read -r path mode; do
        [[ "$mode" == link ]] || continue
        [[ -L "$WT/$path" ]] || continue
        any=1
        if [[ "$MODE" == check ]]; then
            say "  would unlink  $path"
        else
            rm -f "$WT/$path"
            say "  unlinked  $path"
        fi
    done < "$PARSED"

    [[ $any -eq 0 ]] && say "  none      nothing nested left in this worktree"
    return 0
}

hydrate_nested() {
    local path mode src dst base
    while IFS=$'\t' read -r path mode; do
        src="$CLONE_ROOT/$path"
        dst="$WT/$path"

        case "$mode" in
        skip)
            say "  skip      $path"
            continue ;;

        link)
            if [[ -L "$dst" ]]; then
                say "  ok        $path -> $(readlink "$dst")"
            elif [[ -e "$dst" ]]; then
                warn "  WARN      $dst already exists and is not a link — left alone"
                rc=1; continue
            elif [[ ! -d "$src" ]]; then
                # Constellations routinely list siblings that do not exist yet
                # (a repo created the week its code starts). Note it and move
                # on; this is not a failure of the config.
                say "  absent    $path — configured 'link' but not cloned here yet"
                continue
            else
                mkdir -p "$(dirname "$dst")"
                # Relative, so the link survives the whole tree being moved or
                # reachable at a second absolute path — which, per cadence.md's
                # containers appendix, is the normal case here.
                ln -s "$(realpath --relative-to="$(dirname "$dst")" "$src")" "$dst"
                say "  linked    $path -> $(readlink "$dst")"
            fi
            ;;

        worktree)
            if [[ -e "$dst" ]]; then
                say "  ok        $path (already present)"
            elif [[ ! -e "$src/.git" ]]; then
                if [[ -d "$src" ]]; then
                    warn "  WARN      $path is configured 'worktree' but $src is not a git repo — skipped"
                    rc=1
                else
                    say "  absent    $path — configured 'worktree' but not cloned here yet"
                fi
                continue
            else
                git -C "$src" fetch -q origin 2>/dev/null || warn "  note      fetch failed in $path; branching from the local ref"
                if git -C "$src" show-ref --verify --quiet "refs/heads/$TOPIC"; then
                    if git -C "$src" worktree add -q "$dst" "$TOPIC" 2>/dev/null; then
                        say "  worktree  $path on existing branch $TOPIC"
                    else
                        # The usual cause is that another worktree of this
                        # sibling already holds that branch — git allows a
                        # branch in exactly one worktree at a time.
                        warn "  WARN      could not add $path on branch $TOPIC (already checked out in another worktree?)"
                        rc=1; continue
                    fi
                elif base="$(nested_base "$src")"; then
                    if git -C "$src" worktree add -q -b "$TOPIC" "$dst" "$base" 2>/dev/null; then
                        say "  worktree  $path on new branch $TOPIC (from $base)"
                    else
                        warn "  WARN      could not add $path on a new branch $TOPIC"
                        rc=1; continue
                    fi
                else
                    warn "  WARN      $path has no origin/HEAD and no origin/main to branch from — skipped"
                    rc=1; continue
                fi
            fi
            ;;
        esac

        # Whatever we just put there has to be invisible to the home repo, or it
        # becomes permanent `git status` noise and — because reap_worktrees.sh
        # counts untracked files — makes this worktree unreapable forever. The
        # usual cause is a directory-only pattern (trailing slash) meeting a
        # symlink, which is a file. Report it with the fix; the .gitignore
        # belongs to the home repo, not to this script.
        if [[ -e "$dst" || -L "$dst" ]] && ! git -C "$WT" check-ignore -q "$path" 2>/dev/null; then
            warn "  WARN      '$path' is not gitignored here, so it shows as untracked in every worktree"
            warn "            (a directory-only pattern with a trailing slash does not match a symlink)"
            warn "            add to $CLONE_ROOT/.gitignore:  /$path"
            rc=1
        fi
    done < "$PARSED"
}

# Nested repos actually on disk in the main checkout, at any of the first two
# levels. Detection is what makes the config small and honest: it finds the
# candidates, so nobody has to remember to add a newly-cloned sibling, and an
# unconfigured one is REPORTED rather than silently left out of every worktree.
#
# Detection deliberately does NOT pick the mode. worktree-vs-link is a real
# judgement about a specific repo — the sibling every topic edits wants its own
# branch, the one with a 400MB install and a dev server wants the shared clone —
# and nothing on disk distinguishes them. Guessing one for all would either put
# a branch in every sibling for every topic or hand every topic the same HEAD;
# both are wrong often enough to be worth one config line per repo.
#
# It also cannot tell a sibling from a vendored dependency, a scratch clone
# somebody left in the tree, or a package that ships its own .git — which is the
# other half of why detection proposes and the config decides.
# No -mindepth here, deliberately: -mindepth suppresses predicate evaluation on
# shallower entries, which includes -prune. With `-mindepth 2` a top-level
# node_modules is never pruned and the scan walks the whole dependency tree —
# written that way first, and the test caught it reporting node_modules/somedep
# as a candidate sibling. The checkout's own .git is excluded by pruning
# $COMMON instead, and .claude/worktrees is pruned so the worktrees themselves
# are not mistaken for nested repos.
detect_nested() {
    find "$CLONE_ROOT" -maxdepth 3 \
        \( -name node_modules -o -path "$COMMON" -o -path "$CLONE_ROOT/.claude/worktrees" \) -prune -o \
        -name .git -print 2>/dev/null |
        sed -e 's:/\.git$::' -e "s:^$CLONE_ROOT/*::" | grep -v '^$' | sort
}

say "== hydrate ($MODE) $WT"

settings_local || rc=1

[[ -f "$CONFIG" ]] && read_config > "$PARSED"

# Unconfigured nested repos: named, with the line to add. Not an error and not a
# guess — a repo with no mode is absent from worktrees, which is a defensible
# default but a terrible silent one, because the symptom (a sibling missing from
# a worktree) looks exactly like the bug this whole script exists to fix.
unconfigured=""
if [[ "$MODE" == hydrate || "$MODE" == detect ]]; then
    while read -r found; do
        [[ -z "$found" ]] && continue
        cut -f1 "$PARSED" | grep -qxF "$found" || unconfigured+="  $found"$'\n'
    done < <(detect_nested)
    if [[ -n "$unconfigured" ]]; then
        warn "  NOTE      nested git repo(s) here with no line in $CONFIG, so absent from every worktree:"
        while read -r u; do
            [[ -n "$u" ]] && warn "              ${u# } — add:  ${u# }  worktree|link|skip"
        done <<< "$unconfigured"
    fi
fi

if [[ "$MODE" == detect ]]; then
    # Pure report, and the only mode that runs in the main checkout — which is
    # where it is needed, because the config is written during adoption, before
    # any worktree exists. The unconfigured NOTE above has already printed.
    n=0
    while read -r found; do
        [[ -z "$found" ]] && continue
        n=$((n + 1))
        mode="$(awk -F'\t' -v p="$found" '$1==p{print $2}' "$PARSED")"
        [[ -z "$mode" ]] && mode="(unconfigured)"
        say "  $(printf '%-40s %s' "$found" "$mode")"
    done < <(detect_nested)
    [[ $n -eq 0 ]] && say "  none      no nested git repos found under $CLONE_ROOT"
    exit 0
fi

if [[ "$MODE" == hook ]]; then
    # SessionStart. Exits 0 unconditionally: a guard that can fail a session
    # start is worse than the problem it reports, and this one is advisory in
    # every case. Says nothing when there is nothing to say.
    if [[ $IS_MAIN -eq 0 && -f "$CONFIG" ]]; then
        while IFS=$'\t' read -r path mode; do
            [[ "$mode" == skip ]] && continue
            [[ -e "$WT/$path" || -L "$WT/$path" ]] && continue
            [[ -e "$CLONE_ROOT/$path" ]] || continue   # not cloned here at all
            MISSING+=("$path")
        done < "$PARSED"
    fi
    if [[ ${#MISSING[@]} -gt 0 ]]; then
        echo "⚠ This worktree is missing ${#MISSING[@]} thing(s) git does not track: ${MISSING[*]}"
        echo "  Run: $0 '$WT'"
        echo "  (cadence.md §1 — a worktree carries only what the repo tracks; the siblings and"
        echo "   .claude/settings.local.json have to be brought in.)"
    fi
    exit 0
fi

if [[ "$MODE" == hydrate ]]; then
    if [[ -f "$CONFIG" ]]; then
        hydrate_nested
    elif [[ -n "$unconfigured" ]]; then
        # Detection found repos but there is no config at all — a constellation
        # that has not written one yet. The NOTE above already listed them with
        # the lines to add, so do not also claim this is a single-repo layout.
        say "  none      no $CONFIG yet — see the nested repos listed above"
    else
        # Not an error and not a warning: the overwhelming majority of repos are
        # one repo, and for them there is nothing nested to bring in.
        say "  n/a       no $CONFIG — single-repo layout, nothing nested to bring in"
    fi
else
    # Dehydration runs WITHOUT regard to the config, and must. Its scan is what
    # reap_worktrees.sh gates a deletion on, and a nested worktree put there by
    # hand — or left behind by a config entry that has since been edited away —
    # is exactly as destroyable as one this script created. Gating the scan on
    # the config would make the safety check disappear in the cases that need it
    # most. Only the link half reads the config, because a symlink this script
    # did not create is somebody else's.
    dehydrate_nested
fi

exit $rc
