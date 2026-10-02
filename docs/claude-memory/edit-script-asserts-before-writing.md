---
name: edit-script-asserts-before-writing
description: a Python edit script that asserts per replacement and writes as it goes leaves partial edits when one string mismatches; a following `git commit -a` in the same command sweeps them in
metadata:
  type: feedback
---

On 2026-10-01 a design edit script replaced strings file by file with an assert on each; the fourth assert failed, the first file's edits were already written, and the `&& git commit -a` chained after it committed a half-applied change (then amended).

**Why:** a half-applied design edit pushed under one message misleads the fact-checker and the history.

**How to apply:** collect every (file, old, new) first, check all counts, then write; and never chain `git commit` after an edit script in one command — run the script, inspect `git diff --stat`, then commit. See [[pdm-run-fmt-sweeps-other-sessions-files]].
