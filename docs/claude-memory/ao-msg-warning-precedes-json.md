---
name: ao-msg-warning-precedes-json
description: An ao msg reply's warning line prints before its JSON, so a jq parse error is not a failed send; strip the line, never resend on the error
metadata:
  type: feedback
---

`ao msg … --json 2>&1 | jq` fails with *parse error: Invalid literal* when the reply carries a warning (a first paragraph past ~60 words, for one): the warning prints as a plain line ahead of the JSON, and the send has already gone out.

**Why:** on 2026-10-08 the designer read the parse error as a failed send and sent the note again, so Paul got the same note twice.

**How to apply:** the warning goes to stderr (`cli.py` `cmd_msg`, after the send), so keep stderr out of the pipe — drop `2>&1`, or `2>/dev/null` — and jq reads clean JSON; with the streams merged, `sed -n '/^{/,$p' | jq`; on a parse error check the raw reply's `entry.id` before any resend. Related: [[designer-run-lessons-2026-10-05]] (the warning is read past, not around), [[ao-msg-options-before-recipients]].
