---
name: read-the-claims-reply
description: "`ao progress claim` is a lease that can be refused — never send its output to /dev/null; check the claim is on your record before the first step"
metadata:
  type: feedback
---

Never discard `ao progress claim <ref>`'s reply (`>/dev/null`, `| head` of a different field). On
2026-10-05 grinder-ao-1 ran `ao progress claim TD-152 --json >/dev/null`, the claim was refused
because grinder-ao-2 already held TD-152, and the whole scratch-home press was done twice; the
sibling's PR (#1072) merged while the duplicate was still being written up.

**Why:** a claim is a lease (design §4.8): the refusal naming the holder is the only thing that
keeps two grinders off one entry, and it only works if it is read.

**How to apply:** run the claim without redirection and read it; or check
`ao status --json | jq '.[]|select(.name=="<you>")|.progress'` shows the ref `claimed` before the
first step. Related: [[check-in-flight-before-a-td-step]].
