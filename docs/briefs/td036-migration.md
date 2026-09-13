# TD-036 step 6: attaching orchestrator-ao-1 to the sessions it already supervises

**Run this in the same sitting as the agent upgrade, not after it.** Until it is done, the
orchestrator holds the `orchestrate` grant and can act on nothing: every nudge it makes is
refused with `not in its controllers`, and its own log is the only place that says why.

## Why it is needed

Membership (design §4.8, TD-036 steps 1–3) puts a `controllers` list on each *target* and makes
an acting RPC need both the grant and a place in that list. An empty list means nobody may act —
the explicit default. Records written before the change have no list at all, which reads as
empty. A person's `ao new` sets no controller either, because there is no caller to add (only a
session that creates another is added automatically). So every session now running was launched
into a world where the grant was the whole gate, and none of them names the orchestrator.

## The fleet this applies to (read 2026-09-13, `ao status --json`)

| session | state | grant | controllers |
|---|---|---|---|
| `ao-agentorc-orchestrator-ao-1` | idle | `orchestrate` | — |
| `ao-agentorc-tdgrind-ao-1` | idle | — | — |
| `ao-samscrape-tdgrind-1` | idle | — | — |
| `ao-samscrape-tdgrind-2` | idle | — | — |
| `ao-samscrape-tdgrind-3` | idle | — | — |
| `ao-samscrape-td377-enumeration` | idle | — | — |

Confirm the list before running anything — sessions come and go, and the ids carry suffixes when a
name has been reused (§4.1).

## Procedure

1. **Upgrade the installed agent.** The venv at `~/.local/share/agentorc-venv` is the 2026-09-12
   build: `ao control` does not exist in it (`invalid choice: 'control'`) and its records have no
   `controllers` field. Reinstall it from `origin/main` at or past `0ff5ea2` (step 3).
2. **Restart `agentorc-agent`.** Records are reloaded from disk; a record with no `controllers`
   key loads with an empty list, so nothing is lost and nothing is migrated on disk — the field
   simply starts existing. The tmux sessions and their processes survive an agent restart
   (`tests/test_agent_restart.py`), so the workers keep running through this.
3. **Attach, in one command per repo:**

   ```sh
   ao control ao-agentorc-orchestrator-ao-1 add ao-agentorc-tdgrind-ao-1
   ao control ao-agentorc-orchestrator-ao-1 add ao-samscrape-tdgrind-1 ao-samscrape-tdgrind-2 \
                                                ao-samscrape-tdgrind-3 ao-samscrape-td377-enumeration
   ```

   **Full ids on purpose, so the commands work from any directory.** A bare name resolves only
   against sessions whose directory or repo contains the cwd, so `ao control orchestrator-ao-1 …`
   run from `~/samscrape` — the natural place to be, since four of the five targets live there —
   fails with `no session named orchestrator-ao-1 here`. A refusal names the session it refused and
   leaves the rest attached; the exit code is 1 if anything was refused.
4. **Verify both directions before walking away:**

   ```sh
   ao status -v      # each worker shows `under: ao-agentorc-orchestrator-ao-1`
                     # the orchestrator shows `members:` with all five
   ```

   The orchestrator's Focus page shows the same thing as its **Members** list.
5. **Prove it end to end** with one real act, rather than trusting the display: from the
   orchestrator, `ao send <a worker> --wait "orchestrator: membership check, no action needed"`.
   Before step 3 this is refused; after it, it lands.

## What *not* to do

- Do not attach the anchor session, any interactive session, or this repo's other worktrees.
  **Nothing in the code stops you** — this is the one item here that is convention rather than a
  gate, and it is worth knowing before you lean on it. `_gate` checks the grant and membership and
  never looks at the target's `kind`; `set_controllers` will happily add an interactive session to
  a list, and a `send` from its controller will then land. §9 invariant 5 constrains *policies*
  (§6), and the policy engine does not exist yet, so today the only thing keeping an orchestrator
  off an interactive session is its brief. TD-041 is filed to make the invariant real; until it
  lands, attach only the unattended workers above.
- Do not attach a session to two orchestrators today. It is allowed by design and is the reason
  the list is flat, but what two controllers do when they disagree is an open question (TD-039),
  and the first time it happens should not be unattended overnight.
- Do not grant `orchestrate` to anything else while doing this. The grant is still the one
  revocable kill switch; membership narrows it, it does not replace it.
