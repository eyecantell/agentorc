# User Attention Board

Items that need the user to act or decide, plus in-flight work parked by a session. Sessions add entries **the moment they arise** and remove them when handled. Keep this file small — durable debt belongs in [technical_debt.md](technical_debt.md); this board is only "a human must act or decide."

All conventions — entry format and the `<host>` semantics, `Due:` dates and snoozing, the `Swept:`/`Swept-deep:` sweep stamps — live in [docs/cadence.md §3](cadence.md#3-never-strand-work). That file is SYNC (improvements reach every consumer on re-sync); this skeleton is SEED (yours, never overwritten), which is why the explanations do not live here (dev-cadence TD-17).

Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`

## Needs the user

- [ ] act 2026-10-03 (session 3ebe2207 on kmaster) — **Give the contractmatch container node what a worker needs, so cm-grind can run inside it (TD-299 step 3).** In `~/.agentorc/nodes/contractmatch/env` (`chmod 600`): a fine-grained GitHub token scoped to contractmatch (`GH_TOKEN=…`), `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`, and a Doppler service token for contractmatch; then one `claude` login inside the node per profile its roles name (`docker exec -u developer -it <container> env CLAUDE_CONFIG_DIR=/agentorc/profiles/<profile> claude`, the container id from `ao host status contractmatch`). The anchor checks the image's tools first and says on TD-299 if anything else is missing. The same steps for guardians follow once its node is up. Context: TD-299 / TD-057. Due: 2026-10-10.
- [ ] act 2026-10-03 (session `designer-ao-1` on kmaster) — **One sentence of dev-cadence's TD-082 is yours to add, or to hand its designer: a `look` that carries `Works (default)` is closed by its session once its `Due:` has passed, with no `Decided:` written.** Today cadence §3.5 says *the board never falls to it*; agentorc's design (§4.9b *A UI change is verified by its builder*) draws such a look under Steering, and a member closes it at its date as it closes a decided *Works*. Without the sentence every look stays under Needs you until you press. TD-292 waits on TD-082. Context: TD-290 / TD-292. Due: 2026-10-07.


## In-flight (parked by a session)

- (none)
