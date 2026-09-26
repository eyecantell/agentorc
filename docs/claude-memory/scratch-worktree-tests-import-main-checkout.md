---
name: scratch-worktree-tests-import-main-checkout
description: "pytest run inside a scratch worktree imports src from the main checkout's editable install, so pull main first or a rebased branch fails on names main does not have yet"
metadata:
  node_type: memory
  type: feedback
  originSessionId: ef3cf8b9-4b29-480d-83aa-a4ba9df15f1c
  modified: 2026-09-26T18:54:49.508Z
---

Running `.venv/bin/python -m pytest` from a scratch worktree (a rebase of someone's PR under the scratchpad) imports `agentorc`/`sessionorc` from the **main checkout's** `src/`, not the worktree's: the venv is an editable install of `/home/kmaster/agentorc`. A test that reads a field the branch added (2026-09-22: `KeyError: 'supervised'`) is not a branch failure — it is the main checkout being behind.

**Why:** two hours of merges had moved main while the checkout sat at an older commit; the branch's tests were right and the import path was stale.

**How to apply:** run the branch's tests with its own `src` first on the path — `PYTHONPATH=$PWD/src /home/kmaster/agentorc/.venv/bin/python -m pytest -q -p no:cacheprovider` from the worktree (2026-09-26, TD-176: the only way when the branch adds a module main lacks, `ImportError: cannot import name 'ledger'`; the subprocess test agent inherits it). Don't `pdm run` in a worktree without a venv: pdm creates one there with no pytest. Read a KeyError/ImportError on a name the branch adds as the import path, not a defect. Related: [[pdm-run-fmt-sweeps-other-sessions-files]].
