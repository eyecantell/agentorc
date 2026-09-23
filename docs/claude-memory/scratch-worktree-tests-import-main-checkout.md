---
name: scratch-worktree-tests-import-main-checkout
description: pytest run inside a scratch worktree imports src from the main checkout's editable install, so pull main first or a rebased branch fails on names main does not have yet
metadata:
  type: feedback
---

Running `.venv/bin/python -m pytest` from a scratch worktree (a rebase of someone's PR under the scratchpad) imports `agentorc`/`sessionorc` from the **main checkout's** `src/`, not the worktree's: the venv is an editable install of `/home/kmaster/agentorc`. A test that reads a field the branch added (2026-09-22: `KeyError: 'supervised'`) is not a branch failure — it is the main checkout being behind.

**Why:** two hours of merges had moved main while the checkout sat at an older commit; the branch's tests were right and the import path was stale.

**How to apply:** `git pull --ff-only` in the main checkout before running any branch's tests from a scratch worktree, and read a KeyError/ImportError on a name the branch adds as that, not as a defect. Related: [[pdm-run-fmt-sweeps-other-sessions-files]].
