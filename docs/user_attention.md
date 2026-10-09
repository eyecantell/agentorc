# User Attention Board

Items that need the user to act or decide, plus in-flight work parked by a session. Sessions add entries **the moment they arise** and remove them when handled. Keep this file small — durable debt belongs in [technical_debt.md](technical_debt.md); this board is only "a human must act or decide."

All conventions — entry format and the `<host>` semantics, `Due:` dates and snoozing, the `Swept:`/`Swept-deep:` sweep stamps — live in [docs/cadence.md §3](cadence.md#3-never-strand-work). That file is SYNC (improvements reach every consumer on re-sync); this skeleton is SEED (yours, never overwritten), which is why the explanations do not live here (dev-cadence TD-17).

Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed. Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`

## Needs the user

- [x] act 2026-10-03 (session 3ebe2207 on kmaster) — **Give the contractmatch container node what a worker needs, so cm-grind can run inside it (TD-299 step 3).** In `~/.agentorc/nodes/contractmatch/env` (`chmod 600`): a fine-grained GitHub token scoped to contractmatch (`GH_TOKEN=…`), `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`, and a Doppler service token for contractmatch; then one `claude` login inside the node per profile its roles name (`docker exec -u developer -it <container> env CLAUDE_CONFIG_DIR=/agentorc/profiles/<profile> claude`, the container id from `ao host status contractmatch`). The anchor checks the image's tools first and says on TD-299 if anything else is missing. The same steps for guardians follow once its node is up. Context: TD-299 / TD-057. Due: 2026-10-10. Closed: 2026-10-04 — done: Paul, 2026-10-04 (TD-299 (3) and (4), 2026-10-03).
- [x] act 2026-10-03 (session `designer-ao-1` on kmaster) — **One sentence of dev-cadence's TD-082 is yours to add, or to hand its designer: a `look` that carries `Works (default)` is closed by its session once its `Due:` has passed, with no `Decided:` written.** Today cadence §3.5 says *the board never falls to it*; agentorc's design (§4.9b *A UI change is verified by its builder*) draws such a look under Steering, and a member closes it at its date as it closes a decided *Works*. Without the sentence every look stays under Needs you until you press. TD-292 waits on TD-082. Context: TD-290 / TD-292. Due: 2026-10-07. Closed: 2026-10-03 — not needed: Paul, 2026-10-03, a look that reaches the person is mail (a steer lapses to Works by its own rule), so no sentence is asked of dev-cadence.
- [x] watch 2026-10-04 (session `grinder-ao-1` on kmaster) — **Merged, live look pending: `ao host rebuild <node>` no longer ends a node's live sessions without being told to (TD-316).** The next time a container node is rebuilt with a team running inside it (contractmatch's cm-grind, TD-299), run it bare first: it should refuse, name the sessions by team, and leave `ao host status <node>`'s container id unchanged; then `--wind-down` should wrap the team up, close what settled clean and pushed, and rebuild. `pdm run test` covers it against a faked container only. Context: TD-316. Due: 2026-10-07. Answers: Works | Not right: <what>. Decided: Works (2026-10-05). Closed: 2026-10-05 — Paul decided Works; TD-316 archived.
- [x] fyi 2026-10-06 (session `grinder-ao-2` on kmaster) — **The design is `docs/design/` since #1172: one file per section, `docs/design.md` the index.** A design branch opened before it rebases its `design.md` edits into the section files (`scripts/split_design.py` documents the cut; `grep -rn` the directory to find a section). Context: TD-351 / PR #1172. Due: 2026-10-07.
- [ ] decide 2026-10-08 (session 705e0395 on kmaster) — Which prompt-cache levers should agentorc take? Context: TD-378, docs/decisions/2026-10-08-prompt-cache.md. Due: 2026-10-15. Answers: Pin 1h, restart lapsed, trial 200k (default) | Pin 1h and restart lapsed only | None for now. Decided: Pin 1h, restart lapsed, trial 200k (2026-10-08).
  - **Context:** You asked on 2026-10-07 whether Claude's prompt cache could save tokens. Eight days of agentorc's sessions show most of the cost is re-reading long contexts from the cache:
    - reads are 58% of the cost, writes 26%, output 16%
    - requests over 200k tokens of context carry 35% of it
    - a cache that lapsed overnight and was written again is 6%, mostly grinders woken by mail after hours idle
    - Claude Code already keeps the cache for an hour on your subscription, and that is the biggest saving there is
  - **Question:** Which of the three changes should be built or tried?
  - **Caveats:** How your subscription's weekly limit counts cached reads is not published, so the percentages rank the changes and do not promise a saving. A lower bound restarts grinders more often: each restart costs a re-read of the ledger and the files, and nobody knows how much until it is tried.
  - **Recommended:** Pin 1h, restart lapsed, trial 200k — the first two are small and safe, and the trial measures the third on your weekly window. **Otherwise:** the first two only, if you would rather not change how often grinders restart; none, if the weekly window is not tight.

## In-flight (parked by a session)

- (none)
