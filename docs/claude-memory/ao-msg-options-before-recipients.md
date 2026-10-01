---
name: ao-msg-options-before-recipients
description: "`ao msg person --kind note \"text\"` fails with a usage error — options go before the recipients, and a `| tail` hides the failure"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 3adf6465-2942-42da-a263-485d7ccef4c6
  modified: 2026-10-01T17:41:59.929Z
---

`ao msg` takes `to… "text"` as one run of positionals, so an option placed after the first recipient (`ao msg person --kind note "…"`) splits it and argparse exits 2 with `unrecognized arguments: <the text>`. Put the options first: `ao msg --kind note person "…"`, and `ao --json` before the subcommand.

**Why:** On 2026-10-01 the manager's wind-down note to Paul was "sent" with `| tail -3`; the tail showed only the end of the echoed text, which looked like a delivered message. It had not been sent. Found by looking for it in the person inbox; the resend with the options first returned `delivered: ['person']`.

**How to apply:** after any `ao msg` whose result matters, read the response (`ao --json msg …` → `delivered`) instead of piping it through `tail`/`head`. Related: [[designer-run-lessons-2026-09-28]] (an `ao msg` warning is not a refusal).
