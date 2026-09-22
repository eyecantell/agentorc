---
name: reviewer-prompts-keep-out-of-the-anchor
description: "A reviewer subagent told to run `git -C /home/kmaster/agentorc …` will sooner or later run a checkout there; give it a throwaway worktree path for everything but `fetch`"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 027b217f-2bf1-485e-878a-7656f3501a89
  modified: 2026-09-22T22:52:15.628Z
---

On 2026-09-22 a Sonnet reviewer of PR #434, whose prompt said "modify no checkout" but gave every command as `git -C /home/kmaster/agentorc …`, ran `git checkout origin/<branch> -- .` in the anchor's main checkout to inspect some files, then reset it. Nothing was lost, because the tree was clean, but on a dirty anchor tree that command overwrites uncommitted edits.

**Why:** the prompt pointed the reviewer at the anchor's path, and "modify nothing" did not stop a checkout used as a way to read files.

**How to apply:** in a reviewer prompt, name the anchor path only for `git fetch`. Tell it to read with `git show origin/<branch>:<path>` or `git diff`, and to do everything else in its own `git worktree add ~/.cache/<name>` worktree. Say in so many words: never run `checkout`, `reset`, `stash` or `restore` in /home/kmaster/agentorc. Related: [[agentorc-td-grind-mechanics]], [[no-vscode-windows-for-agent-worktrees]].
