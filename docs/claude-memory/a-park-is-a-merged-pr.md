# A park is a merged PR, not an open one

**2026-09-25 (Paul).** A cloud session parks a ledger entry on the board by opening a PR with the
board line and a Status clause ([[cloud-design-round]]). **The park exists when that PR is merged,
not when it is opened**: the designer on kmaster reads the board on `main`, and an unmerged park is
invisible to it. On 2026-09-25 a cloud session opened #545 to park TD-128 at 05:02Z and went straight
into the proposal; Paul had not merged it; the designer picked TD-128 at 06:29Z (#547, merged 13:42Z),
and the cloud session's proposal was written against an entry that was already designed. Two rules:

1. **Do not start the round until the park PR reads merged** (`pull_request_read` → `merged: true`;
   `gh pr view` on kmaster). If Paul has not merged it, say so and wait — the proposal can be drafted,
   but nothing in it is a decision until the entry is held.
2. **Re-search PRs for the id in every state, not just open, and again at the start of the round**
   (`repo:… TD-NNN`, all states, sorted by updated): the designer's PR can be opened and merged
   inside one cloud session's turn, so a check at session start proves nothing an hour later. A
   merged PR for the id means the round is a reconciliation against `main`
   ([[check-open-prs-before-a-design-round]]), never a second design.
