---
name: design-help-text-is-bound-to-help-py
description: A docs-only design PR must not reword §4.5a's help list — tests/test_help.py holds it word for word against help.py; run the doc-bound tests before pushing
metadata:
  type: feedback
---

The help texts listed in `docs/design.md` §4.5a (the *i* marks' list: Start, Wind down, the fold, Forget, …) are compared word for word with `src/agentorc/ui/help.py` by `tests/test_help.py`. A design PR that rewords one fails CI (PR #621, 2026-09-26).

**Why:** the list in the design is the source the code copies, and the test keeps them from drifting — so the wording can only change in the PR that changes `help.py`.

**How to apply:** in a design round leave the help list alone and write the new wording into the build entry's *Fix* as a step. Before pushing any docs PR run the tests that read the docs, not only `test_primer.py`: `grep -l -E 'design\.md|technical_debt|mockups' tests/*.py`, then pytest on those with `PYTHONPATH=$PWD/src` (see [[scratch-worktree-tests-import-main-checkout]]).
