# User Attention Board

Items that need the user to act or decide, plus in-flight work parked by a session. Sessions add entries **the moment they arise** and remove them when handled. Keep this file small — durable debt belongs in [technical_debt.md](technical_debt.md); this board is only "a human must act or decide."

All conventions — entry format and the `<host>` semantics, `Due:` dates and snoozing, the `Swept:`/`Swept-deep:` sweep stamps — live in [docs/cadence.md §3](cadence.md#3-never-strand-work). That file is SYNC (improvements reach every consumer on re-sync); this skeleton is SEED (yours, never overwritten), which is why the explanations do not live here (dev-cadence TD-17).

Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`

## Needs the user

- [x] act 2026-10-03 (session `designer-ao-1` on kmaster) — **One sentence of dev-cadence's TD-082 is yours to add, or to hand its designer: a `look` that carries `Works (default)` is closed by its session once its `Due:` has passed, with no `Decided:` written.** Today cadence §3.5 says *the board never falls to it*; agentorc's design (§4.9b *A UI change is verified by its builder*) draws such a look under Steering, and a member closes it at its date as it closes a decided *Works*. Without the sentence every look stays under Needs you until you press. TD-292 waits on TD-082. Context: TD-290 / TD-292. Due: 2026-10-07. Closed: 2026-10-03 — not needed: Paul, 2026-10-03, a look that reaches the person is mail (a steer lapses to Works by its own rule), so no sentence is asked of dev-cadence.
- [ ] decide 2026-09-23 (the anchor session on kmaster) — **On hold (Paul, 2026-09-23): the guardians team setup and the contractmatch container node.** Nothing is built toward either until Paul lifts the hold: `docs/briefs/guardians-orchestrator.md` stays unlaunchable (the repos are not on this host; the devcontainer question in design §10 is open), and TD-057's one-time node steps (hosts.yml `nodes:`, the contractmatch postCreate re-point, `~/.agentorc/nodes/contractmatch/env`, `ao host up`, the cross-link checks of PRs #211–#224) stay unrun — their text is in this file's history at the two lines folded here. When lifted, the node first (it is the guardians' substrate), then the brief. Context: TD-057 / TD-055 step 4 / design §4.4a, §10. Due: 2026-10-31. Answers: Keep the hold (default) | Lift it: the contractmatch node first, then the guardians brief. Decided: Keep the hold (2026-10-03).


## In-flight (parked by a session)

- (none)
