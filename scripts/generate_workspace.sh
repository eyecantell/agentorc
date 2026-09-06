#!/usr/bin/env bash
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/generate_workspace.sh
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
# Regenerate the multi-root VS Code workspace file from `git worktree list`.
#
# WHY THIS EXISTS
# ---------------
# The cadence runs one session per worktree (docs/cadence.md §1) so parallel
# sessions don't share a HEAD or an index. That isolation costs visibility: a
# VS Code window opened on the checkout shows only the main working tree, so
# the other sessions' edits are invisible until PR time — too late to steer
# them. A multi-root workspace fixes that: each worktree becomes a top-level
# folder with its own Source Control entry.
#
# The file is GITIGNORED on purpose. Its roots are per-topic and ephemeral, so
# a tracked copy would churn and conflict across the very sessions it exists to
# show — the same shared-singleton problem docs/user_attention.md has.
#
# In a CONSTELLATION it also lists the sibling repos (docs/nested-repos.txt, the
# same config scripts/hydrate_worktree.sh reads) as roots of their own, per
# tree. Without that the generated workspace has one root where the project has
# four, and a hand-maintained workspace file listing the siblings ends up
# committed alongside it — two files answering the same question, one of which
# nothing updates when a worktree comes or goes.
#
# THE RELOAD RULE (measured 2026-08-13, VS Code 1.132)
# ----------------------------------------------------
# Open the generated file as a workspace ONCE (File > Open Workspace from
# File...). After that, just re-run this script: VS Code watches the file and
# applies folder changes live, in BOTH directions, with no reload. Measured
# across a rewrite that dropped a root and one that added it back — IPC socket
# unchanged and still responsive, window pid unchanged, terminals untouched —
# and the dropped root confirmed gone from the explorer.
#
# That is the whole mechanism. `code --add` is NOT needed and is a trap here:
# run against a window opened as a plain FOLDER it must promote the window to a
# workspace, and that promotion RELOADS — dropping every terminal, including the
# Claude sessions in them. Observed directly: title went folder -> "Untitled
# (Workspace)", the IPC socket died mid-call, the session driving the test was
# disconnected. (Against an already-multi-root window it is harmless, but it
# still can't remove a root — there is no `code --remove` — so rewriting the
# file is strictly better: one mechanism, both directions.)
#
# The one-time "open the workspace" reload is the entire reason to keep a real
# file instead of letting VS Code hold an untitled workspace in memory.
#
# THE PER-TOPIC FILE (--topic) IS A DIFFERENT ARTIFACT
# -----------------------------------------------------
# The overview above answers "show me everything". `--topic <worktree>` answers
# "show me THIS topic's repos", and scripts/open_worktree.sh opens it instead of
# the bare worktree folder.
#
# That is not a retreat from one-window-per-topic (open_worktree.sh's header
# explains why that rule is right). It is the second, orthogonal axis: the rule
# is about TOPICS, and in a constellation one topic spans several REPOS.
# hydrate_worktree.sh already knows this — it builds the siblings inside the
# topic tree. The window was the last part of the flow still assuming a topic is
# one repo, and nesting the siblings under a single root costs two things:
#
#   1. Search silently skips them. Nested siblings are gitignored at the
#      worktree root by definition (that is how the umbrella keeps them out of
#      its history), and search.useIgnoreFiles defaults to true — so a
#      workspace-wide search does NOT search the repos the topic is editing.
#      No warning; it just returns fewer hits.
#   2. Source Control leans on nested-repo detection through a gitignored
#      directory (and, for a `link` sibling, a symlink) instead of giving each
#      repo its own group with its own branch and staging.
#
# For a repo with NO nested siblings this mode writes nothing and prints
# nothing, so open_worktree.sh falls back to opening the folder exactly as
# before. Single-repo consumers see no change at all — there is no new mode to
# reason about unless docs/nested-repos.txt exists.
#
# USAGE
#   scripts/generate_workspace.sh                  # all-topics overview
#   scripts/generate_workspace.sh --topic <path>   # one worktree's repos
#
# Re-run the overview after creating or removing a worktree. Safe to run from
# inside a worktree: the output always lands at the clone root, resolved via
# git-common-dir, so there is exactly one overview file per repo.
#
# --topic prints the workspace file path on STDOUT and nothing else, so a caller
# can capture it; the human-readable summary goes to stderr. It prints nothing
# when the worktree has no siblings to show.

set -euo pipefail

MODE=overview
TOPIC_PATH=""
case "${1:-}" in
    "")       ;;
    --topic)  MODE=topic
              TOPIC_PATH="${2:-}"
              if [[ -z "$TOPIC_PATH" ]]; then
                  echo "usage: $0 --topic <worktree-path>" >&2
                  exit 2
              fi
              if [[ ! -d "$TOPIC_PATH" ]]; then
                  echo "error: not a directory: $TOPIC_PATH" >&2
                  exit 2
              fi
              TOPIC_PATH="$(cd "$TOPIC_PATH" && pwd)"
              if [[ $# -gt 2 ]]; then
                  echo "error: unexpected argument after --topic <path>: ${3}" >&2
                  exit 2
              fi
              ;;
    *)        echo "usage: $0 [--topic <worktree-path>]" >&2; exit 2 ;;
esac

CLONE_ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
REPO_NAME="$(basename "$CLONE_ROOT")"
OUT="$CLONE_ROOT/$REPO_NAME.code-workspace"

if [[ "$MODE" == topic ]]; then
    TOPIC="$(basename "$TOPIC_PATH")"
    OUT="$CLONE_ROOT/$TOPIC.code-workspace"
    # A topic named after the repo would otherwise overwrite the overview file
    # with a one-topic view -- silently, and the overview would keep being
    # rewritten by every other caller, so the two would fight.
    #
    # The separator is `+` because the topic charset (open_worktree.sh) is
    # [A-Za-z0-9._-], which excludes it. Anything drawn FROM that charset only
    # moves the collision: a `.topic` suffix collides with a topic actually
    # named "<repo>.topic", whose ordinary filename is the same string. Caught
    # in review after the first attempt did exactly that; case 5b pins it.
    [[ "$TOPIC" == "$REPO_NAME" ]] && OUT="$CLONE_ROOT/$TOPIC+topic.code-workspace"
fi

git worktree list --porcelain | python3 -c '
import json, os, sys

clone_root, repo_name, mode, topic_path = sys.argv[1:5]

paths = [line.split(" ", 1)[1].strip()
         for line in sys.stdin if line.startswith("worktree ")]

main = [p for p in paths if os.path.realpath(p) == os.path.realpath(clone_root)]
extra = sorted((p for p in paths if p not in main), key=os.path.basename)


# In a CONSTELLATION (cadence.md §9) the sibling repos are separate clones
# gitignored inside the checkout, so `git worktree list` never mentions them and
# a workspace built from it alone shows the cadence and none of the content —
# one root where the project has four. Each sibling needs its OWN root or it
# gets no Source Control entry, which is the whole reason this file exists.
#
# Read from the same docs/nested-repos.txt that hydrate_worktree.sh uses, so a
# root appears here exactly when that sibling is actually present in that tree.
# `skip` siblings are deliberately absent from a worktree, so they are absent
# here too; listing a root that resolves to nothing just puts a phantom folder
# in the explorer.
def nested(root):
    cfg = os.path.join(clone_root, "docs", "nested-repos.txt")
    out = []
    try:
        with open(cfg, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out          # no config, or unreadable: single-repo layout
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) != 2 or fields[1] == "skip":
            continue
        p = os.path.join(root, fields[0])
        if os.path.isdir(p):
            out.append(p)
        # A configured sibling that is not on disk is silently omitted rather
        # than reported: this script is a view builder, and hydrate_worktree.sh
        # is where a missing sibling is diagnosed with the context to fix it.
    return out


if mode == "topic":
    # One topic, all its repos. No worktree list involved: a worktree contains
    # no worktrees of its own, so the only extra roots are the siblings.
    sibs = nested(topic_path)
    if not sibs:
        # Nothing a multi-root window would add. Write no file and print no
        # path, so the caller opens the plain folder exactly as it always has.
        sys.exit(0)
    folders = [{"name": os.path.basename(topic_path), "path": topic_path}]
    folders += [{"name": os.path.basename(p), "path": p} for p in sibs]
    # These patterns filter INSIDE each root: they hide the sibling
    # subdirectory copies under the topic root without hiding the sibling roots.
    exclude = {os.path.basename(p): True for p in sibs}
    print(json.dumps({"folders": folders,
                      "settings": {"files.exclude": exclude}}, indent=2))
    sys.exit(0)

folders = [{"name": f"{repo_name} (main)", "path": clone_root}]
folders += [{"name": os.path.basename(p), "path": p} for p in nested(clone_root)]
for p in extra:
    folders.append({"name": os.path.basename(p), "path": p})
    # Qualified, because in a constellation every worktree contributes a root
    # with the SAME basename: three bare "guardians-docs" entries in one
    # explorer are indistinguishable, and picking the wrong one means editing
    # the branch of a different topic.
    #
    # (No apostrophes anywhere in this python block: it is a single-quoted
    # `python3 -c` argument, so one ends the string and the rest of the script
    # becomes shell. `bash -n` catches it; nothing else does.)
    folders += [{"name": f"{os.path.basename(q)} ({os.path.basename(p)})", "path": q}
                for q in nested(p)]

exclude = {
    # The worktrees are also nested inside the main root; hiding them there
    # stops every one showing up twice in the explorer.
    "**/.claude/worktrees": True,
}
# Same duplication, one level down: a sibling repo is a root of its own AND a
# subdirectory of the root that contains it. These patterns filter INSIDE each
# root, so they hide the subdirectory copies without hiding the sibling roots
# themselves.
for entry in {os.path.basename(p) for p in nested(clone_root)}:
    exclude[entry] = True

print(json.dumps({"folders": folders, "settings": {"files.exclude": exclude}}, indent=2))
' "$CLONE_ROOT" "$REPO_NAME" "$MODE" "$TOPIC_PATH" > "$OUT.tmp"

# An empty temp file means the python block chose to write nothing: --topic on a
# worktree with no siblings. Leave any previous file alone and say nothing on
# stdout, so the caller falls back to opening the folder.
if [[ ! -s "$OUT.tmp" ]]; then
    rm -f "$OUT.tmp"
    exit 0
fi

# Atomic: a reader (VS Code watches this file) never sees a half-written one.
mv "$OUT.tmp" "$OUT"

# --topic is machine-readable: the path on stdout, the summary on stderr.
if [[ "$MODE" == topic ]]; then
    echo "$OUT"
    exec >&2
fi

echo "wrote $OUT"
python3 -c '
import json, sys
for f in json.load(open(sys.argv[1]))["folders"]:
    print("  %-28s %s" % (f["name"], f["path"]))
' "$OUT"
