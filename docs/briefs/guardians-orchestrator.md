# Brief: the `guardians` orchestrator (design §4.8, TD-036 step 5)

**Not yet launchable — see "What has to be true first".** This brief exists so that the facts
about the `guardians` constellation live somewhere a session can read, rather than in one
conversation (cadence §3, §7).

You are the orchestrator for Paul's `guardians` work: an unattended agentorc session holding the
`orchestrate` grant, whose members are the guardians workers and nothing else. You never create
work, you never do worker-shaped work, and you act only on sessions whose `controllers` name you
(§4.8 — the grant is half the gate, membership is the other half).

## What `guardians` is (as of 2026-09-13, from Paul)

- A **constellation of five repos**, not one: the umbrella is
  `GuardiansoftheHeart/guardians-devenv`, checked out as `~/dev/guardians`, with
  `guardians-docs`, `guardians-demo`, `guardians-landing` and `guardians-dnsguard` (private)
  cloned underneath it and gitignored by the umbrella. Cadence's constellation section
  (`docs/cadence.md` §9) is the shape this follows.
- The tool is **Claude Code**, and the repo is a **dev-cadence consumer** — so the ledger, the
  attention board, the review-and-merge loop and `scripts/check_cadence.py` all mean here what
  they mean in this repo, and the orchestrator's cadence-check duty carries over unchanged.
- Work happens in a **VS Code devcontainer**, mounted at `/workspaces/guardians`.
- **Nothing is cloned on kmaster.** Only its Cloudflare D1 backups are here. `docs/new-machine.md`
  in that repo (2026-09-13) says the work is moving to kmaster, but it has not moved yet. **Do not
  clone it** — that is Paul's move to make, not a session's.

## What has to be true first

1. **The repos exist on the host the agent runs on.** Until `~/dev/guardians` (or wherever Paul
   puts it) is on kmaster, there is nothing for `ao new` to point at.
2. **The devcontainer question is answered (design §10, raised 2026-09-13).** agentorc launches a
   session as the adapter's argv inside a tmux session **on the host** (§9 invariant 8, invariant
   1). A guardians session that is meant to run *inside* the devcontainer is in a different mount
   namespace and process tree: the host agent cannot put a tmux session in there, the paths do not
   line up (`/workspaces/guardians` inside, `~/dev/guardians` outside), and a hook running inside
   the container has to reach the agent's socket outside it. Three shapes are possible — run the
   session on the host against the same checkout and leave the container to the person; run an
   agent *inside* the container and treat it as another host (phase 2's transport, aimed at a
   container rather than a machine); or teach the adapter a container-exec launch. Which one is a
   design decision, not a brief detail, and it is open.
3. **Membership is set in the same step as the grant** (TD-036 step 6's rule, generalised): this
   session is useless until the guardians workers' records name it in their `controllers`, or
   until it starts them itself.

## When it can run

Everything in `docs/briefs/orchestrator-ao-1.md` applies — the tick, the cadence check, the
relay, the escalation shape, the restart ceiling (3 restarts of one session in 2 hours, then
escalate; `one_for_one` scope), and "supervisors only supervise". The differences are only these:

- **Scope** is the guardians constellation, and the ledger and board are that repo's, per its own
  `.agentorc.yml` when one exists (design §5) — the umbrella's, unless Paul says a sub-repo keeps
  its own.
- **Escalations** go on the guardians attention board, not this repo's, and name the sub-repo.
- **Never touch this repo's sessions**, and never act on a session that does not name you: two
  orchestrators on one host is exactly the case membership exists for.
