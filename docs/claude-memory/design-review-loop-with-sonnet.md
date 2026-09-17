---
name: design-review-loop-with-sonnet
description: Design sections are reviewed by Fable, changes adopted, then Sonnet rounds run until both agree it is ready for implementation
metadata:
  type: feedback
---

For a design section (e.g. design §4.10 on 2026-09-16), Paul's process is: Fable reviews and
lists ranked findings with a recommendation each; on his "adopt them", write them into the
design, TD entry and §10; then run Sonnet design-review rounds (fresh agent each round, told
what earlier rounds verified) until Sonnet says READY and Fable agrees, or Fable overrides a
Sonnet finding deliberately and records why. Record each round in §10. Then PR, Sonnet
fact-check as the cadence-review comment, cadence check, squash merge.

**Why:** Paul wants independent agreement before TD-052-scale implementation starts; §4.10 took
four Fable rounds and three Sonnet rounds to converge.
**How to apply:** when asked to review a design, deliver findings first and wait for the go;
then loop reviews to convergence rather than stopping after one. See
[[agentorc-td-grind-mechanics]] for the cadence comment shape.
