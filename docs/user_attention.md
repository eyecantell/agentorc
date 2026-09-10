# User Attention Board

Items that need the user to act or decide, plus in-flight work parked by a session. Sessions add entries **the moment they arise** and remove them when handled. Keep this file small — durable debt belongs in [technical_debt.md](technical_debt.md); this board is only "a human must act or decide."

All conventions — entry format and the `<host>` semantics, `Due:` dates and snoozing, the `Swept:`/`Swept-deep:` sweep stamps — live in [docs/cadence.md §3](cadence.md#3-never-strand-work). That file is SYNC (improvements reach every consumer on re-sync); this skeleton is SEED (yours, never overwritten), which is why the explanations do not live here (dev-cadence TD-17).

Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`

## Needs the user

- [ ] 2026-09-10 (session tdgrind-ao-1 on kmaster) — **Merged overnight, live check pending on the running agent/UI (restart both to pick them up):** `limited` from the usage endpoint + the top-bar usage chip (TD-001, #44); "finished · unseen" idle cards (TD-017, #43); `ao send --wait` (TD-016, #42); screen rules — a fresh session's trust dialog should show `needs-you (scraped)` within a tick, `ao explain <id>` shows why (TD-015, #47); `ao new --attach` / `ao focus <id>` (TD-010 b, #46); `ao --json` everywhere (TD-018, #41); TD-009/011/012/013/020/021 fixes and TD-007 accepted. The one-line thing to watch: does anything in the Herd look wrong after the restart? Context: docs/technical_debt_archive.md entries dated 2026-09-10. Due: 2026-09-13.
- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Decide design §10's two permission-dialog questions: Deny with a reason (cheap, the hook already carries one) and "allow for this session" (a third smaller button, never the default). Context: TD-008, design §10. Due: 2026-09-20.
- [ ] 2026-09-06 (session 6d1b7b88 on kmaster) — Use phase 1 for real: reach the UI over `ssh -L 8765:127.0.0.1:8765 kmaster`, WireGuard, or a Cloudflare tunnel (design §4.5), run the agent + UI, point a session at samscrape, and note what the flows get wrong. Context: README "Run it", design §7 phase 1 success test. Due: 2026-09-13.


## In-flight (parked by a session)

- (none)
