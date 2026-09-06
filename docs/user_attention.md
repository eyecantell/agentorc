# User Attention Board

Items that need the user to act or decide, plus in-flight work parked by a session. Sessions add entries **the moment they arise** and remove them when handled. Keep this file small — durable debt belongs in [technical_debt.md](technical_debt.md); this board is only "a human must act or decide."

All conventions — entry format and the `<host>` semantics, `Due:` dates and snoozing, the `Swept:`/`Swept-deep:` sweep stamps — live in [docs/cadence.md §3](cadence.md#3-never-strand-work). That file is SYNC (improvements reach every consumer on re-sync); this skeleton is SEED (yours, never overwritten), which is why the explanations do not live here (dev-cadence TD-17).

Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`

## Needs the user

- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Review and merge the two dev-cadence PRs agentorc depends on: #82 (`--report --json` + `BoardItem.line`) and #83 (cadence §4 carve-out for tool-made Snooze/Done edits). Until #82 lands, the Due strip / Attention tab (phase 4) has no data path. Context: docs/decisions/2026-09-06-adopt-dev-cadence.md. Due: 2026-09-13.
- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Decide the GitHub settings the ADR proposes and did not apply: squash-only merges, delete merged heads, a 0-approval PR ruleset on main (commands in the ADR). Context: docs/decisions/2026-09-06-adopt-dev-cadence.md. Due: 2026-09-13.
- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Decide design §10's two permission-dialog questions: Deny with a reason (cheap, the hook already carries one) and "allow for this session" (a third smaller button, never the default). Context: TD-008, design §10. Due: 2026-09-20.
- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Use phase 1 for real: install Tailscale on kmaster (design says the UI binds to localhost until then; `ssh -L 8765:127.0.0.1:8765 kmaster` reaches it now), run the agent + UI, point a session at samscrape, and note what the flows get wrong. Context: README "Run it", design §7 phase 1 success test. Due: 2026-09-13.

## In-flight (parked by a session)

- (none)
