# ADR 2026-09-12: prior art for orchestrator membership (`controllers` on the target)

Status: research log (2026-09-12). Not a decision on its own — it is the evidence behind the
§10 question *"One orchestrator or many; how is membership expressed?"*, and the source of the
ADOPT/ADAPT items the design change took. The design change is written separately (§4.8, §4.5a,
§9 invariant 11, §10) and its implementation is ledgered as TD-036.

## Context

Today `orchestrate` is one bit on the *caller* and the gate (`_gate` in
`src/sessionorc/agent.py`) never looks at the target: any session holding the grant may act on
every session on the host (design §4.8, §9 invariant 11). That was enough while there was one
orchestrator. Several are now in sight — a `guardians` orchestrator, a ui orc and a backend orc
inside one large repo, a read-only cross-repo status orc, and an orchestrator-of-orchestrators
that restarts the others and is the one place Paul talks to. Every one of them would reach every
session.

Per-repo boundaries were rejected: the boundary must be one the person sets, not one the tool
infers. The design under consideration puts a `controllers: [session ids]` list on each session
record, and passes an acting RPC from session A onto session B only if A holds `orchestrate`
**and** A's id is in B's `controllers`. Paul asked, before that is written into the design:
*are there existing systems with these sort of parent-child relationships that we can take
lessons from?*

## What was examined

A Sonnet research subagent (prompt: `~/.agentorc-briefs/orc-membership-research-prompt.md`,
web search + primary documentation, read-only) covered eight families: Erlang/OTP supervision
trees; Kubernetes `ownerReferences` and controller-runtime; systemd unit relationships;
process supervisors (supervisord, s6/runit/daemontools, Circus, PM2); capability-based security
(attenuation, revocation, the confused deputy); multi-agent LLM frameworks (Anthropic's
orchestrator-workers, AutoGen, CrewAI, and field reports on tmux-based Claude Code fleets);
Unix reparenting and tmux; and ACL-vs-capability-list placement. Lessons below are the
subagent's, kept with their URLs; the ADOPT/ADAPT/REJECT ranking is the one this repo is acting
on.

## Lessons

### 1. Erlang/OTP supervision trees

- Restart strategies are explicit and per-supervisor, not inferred: `one_for_one` restarts only
  the failed child, `one_for_all` every sibling, `rest_for_one` the failed child plus everything
  started after it. An orc-of-orcs needs the same explicit choice per group; the design today
  says only "restarts exited ones".
  <https://www.erlang.org/doc/apps/stdlib/supervisor.html>
- A restart-intensity ceiling (`intensity`/`period`, default 1 restart in 5 s) stops crash loops
  by giving up and escalating. Applies directly and is missing: nothing in the design bounds
  orchestrator-driven restarts.
  <https://www.erlang.org/doc/apps/stdlib/supervisor.html>
- "Supervisors only supervise, workers only work" — a bug in application code can never break
  its own recovery path. Applies as a brief-level rule: an orchestrator does not take on
  worker-shaped coding work.
  <https://learnyousomeerlang.com/supervisors>
- Child specs are declared statically, which keeps the supervision topology auditable. Reinforces
  an explicit `controllers` list over any inferred boundary.

### 2. Kubernetes `ownerReferences` and controllers

- An object may carry several owner references but at most one with `controller: true` — the
  single managing controller, and the only one with `blockOwnerDeletion` semantics. This is the
  closest precedent to "several controllers per session", and it says: several owners, *one*
  authoritative. agentorc's list is deliberately flat (any member may act, none privileged), so
  that is a choice worth stating rather than leaving implicit.
  <https://v1-34.docs.kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents>
- Deleting an owner without its dependents orphans them, and controllers then race to adopt the
  orphans. The design does not say what happens to a `controllers` entry when that controller
  exits: prune it (control silently lost) or leave it (a dangling id treated as valid)?
- controller-runtime distinguishes `Owns()` (lifecycle-linked) from `Watches()` (loose,
  input-only). "Create adds the creator" is `Owns`-shaped; a controller added later is
  `Watches`-shaped. Worth recording which entry created the session even if both share one gate.
  <https://github.com/kubernetes-sigs/controller-runtime/blob/main/pkg/builder/controller.go>
- Cross-namespace owner references are disallowed. Applies weakly: grant + membership already
  stops a session reaching into a boundary nobody granted it.

### 3. systemd

- `PartOf=` propagates in one direction only: stopping or restarting the parent reaches the
  child, stopping the child does not reach the parent. "Orchestrator exits → its workers stop"
  is one clean option the design should pick between explicitly.
  <https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html>
- `BindsTo=` stops the bound unit even when the target dies unexpectedly. That distinction —
  asked-to-close versus crashed — matters here: a *crashed* orchestrator arguably should not
  take its workers down with it.
- `StopWhenUnneeded=` garbage-collects a unit once nothing depends on it. A candidate policy for
  a session whose `controllers` list empties, and one to reject consciously: explicit user
  control argues for leaving the session running and surfacing it.
- `StartLimitBurst=`/`StartLimitIntervalSec=` gate restarts the way OTP's intensity/period does,
  with `FailureAction=` after. The ceiling, invented twice independently.

### 4. Process supervisors (supervisord, s6/runit/daemontools, Circus, PM2)

- s6/runit: daemons must always be spawned by the supervision tree, never by hand — the
  supervisor being the sole creation path is what makes its bookkeeping trustworthy. agentorc
  already has this as §9 invariant 1 (only the host agent creates `ao-*` sessions), and it is
  what would make a `controllers` list trustworthy.
  <https://skarnet.org/software/s6/overview.html>
- Circus's "flapping" detection stops restarting after too many attempts in a window — a third
  independent lineage converging on the same guard.
  <https://circus.readthedocs.io/en/latest/for-ops/configuration/>
- PM2's ecosystem file is a flat, user-edited array with no ownership hierarchy; grouping is by
  naming convention. The cautionary contrast: people lose track of what belongs to what.
  <https://pm2.keymetrics.io/docs/usage/application-declaration/>
- None of them supports N controllers per unit. The ui-orc-plus-backend-orc case has no
  battle-tested pattern to copy.

### 5. Capability-based security

- "Authority should only shrink along a delegation chain" is the core anti-confused-deputy
  principle, and "the child's grants ⊆ the creator's grants" is a textbook instance of it. The
  create rule is on established ground.
  <https://blog.acolyer.org/2016/02/16/capability-myths-demolished/>
- Capabilities are cheap to delegate and expensive to revoke. The design dodges that by anchoring
  control on the *target* (ACL-shaped): revoking A's control of B is an edit to B's list, not a
  hunt through everything A was handed.
- Macaroons attach caveats that only ever narrow and cannot be stripped — a ready mechanism if
  `orchestrate` is ever split into finer verbs (restart vs. close vs. edit-controllers).
  <https://theory.stanford.edu/~ataly/Papers/macaroons.pdf>
- The confused deputy arises when one program juggles authority from several sources — exactly
  the ui-orc + backend-orc case. Unaddressed: two controllers issuing conflicting RPCs on one
  session at the same time (one restarts while the other is mid-prompt).

### 6. Multi-agent LLM frameworks

- Anthropic's orchestrator-workers pattern is single-level delegation, with no manager-of-managers
  and no access control between orchestrators. The orc-of-orcs is past what the reference pattern
  covers, so there is nothing to copy there.
  <https://www.anthropic.com/engineering/building-effective-agents>
- AutoGen's `GroupChatManager` is itself an LLM call choosing the next speaker, and nested chats
  give arbitrary depth with no membership model at all. The explicit list plus a deterministic
  gate is a deliberate rejection of emergent coordination — auditability over flexibility.
  <https://microsoft.github.io/autogen/stable//user-guide/core-user-guide/design-patterns/group-chat.html>
- CrewAI's hierarchical process has one manager per crew; no documented multi-manager-per-worker
  case.
- Field reports on tmux-based Claude Code orchestration name the failure modes: coordination
  overhead dominates past roughly 3–5 agents, static specs go stale as agents reinterpret them
  mid-run, and review debt multiplies under bad supervision. The phase plan has no fan-out
  ceiling.
  <https://shipyard.build/blog/claude-code-multi-agent/>

### 7. Unix reparenting and tmux

- Orphans reparent to the nearest living subreaper ancestor (`PR_SET_CHILD_SUBREAPER`), not
  necessarily to init. Maps onto: an exited orchestrator's workers are adopted by the orc-of-orcs
  rather than left with a dead controller id or stopped.
  <https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html>
- Reparenting is automatic and unconditional. The caution: decide whether adoption here is
  automatic (simple, but it silently changes who may act) or goes through the same explicit
  `set_controllers` edit (matches "no inferred boundaries", but leaves orphans ungoverned until
  someone claims them).
- tmux has no "this session may control that session" concept. Nothing to borrow.

### 8. ACL vs. capability-list placement

- Lampson's access matrix: ACLs (a column per object) make "who controls Y" and revocation
  trivial; capability lists (a row per subject) make "what does X control" trivial and revocation
  costly. `controllers` is ACL-shaped and matches the queries the UI actually asks.
  <https://www.sciencedirect.com/topics/computer-science/access-control-matrix>
- The confused deputy is associated with ACL systems that rely on ambient authority. The gate
  combines an ACL check (A ∈ B.controllers) with a capability check (A holds `orchestrate`),
  which is the right pair — provided the handler never trusts a claimed caller id or a cached
  grant, and re-reads both on every call.
- Exposing "who controls Y" on the card is a genuine strength of the ACL placement, not just
  parity with the orchestrator's member view.

## What to leverage

| # | Verdict | Takeaway | Why |
|---|---|---|---|
| 1 | **ADOPT** | A restart-intensity ceiling: N restarts per period, then stop and escalate to the attention board | OTP, systemd and Circus converged on it independently; highest value, lowest risk, and absent today |
| 2 | **ADOPT** | `one_for_one` as the default restart scope, stated per orchestrator rather than assumed | "restarts exited ones" is silent about siblings; OTP makes the choice explicit |
| 3 | **ADOPT** | Subreaper-style adoption: an exited orchestrator's workers are adopted by the orc-of-orcs, not left pointing at a dead id | the alternative is a `controllers` entry that can never act again and never be noticed |
| 4 | **ADOPT** | "Supervisors only supervise" — an orchestrator never takes on worker-shaped work | keeps the recovery path independent of the code being worked on; a brief rule, not a gate |
| 5 | **ADOPT** | Child's grants ⊆ creator's grants is the established attenuation pattern — cite it | the create rule is not novel, and saying so is what keeps it from being weakened later |
| 6 | **ADOPT** | Two independent gates — membership *and* grant — re-read live on every RPC; never infer authority from context | matches the existing gate's "checked against the record as it is then"; nothing may cache a grant |
| 7 | **ADAPT** | `PartOf=`/`BindsTo=`: do not blanket-propagate an orchestrator's exit to its workers; distinguish asked-to-close from crashed, and document one policy | a crashed orchestrator killing a worker mid-PR is the worst reading of the rule |
| 8 | **ADAPT** | Owner vs. watcher: keep one flat `controllers` ACL for authorization, but record which entry created the session | adoption logic later needs "who is responsible", and a second gate is not worth it |
| 9 | **ADAPT** | Macaroon-style caveats if `orchestrate` is ever split into finer verbs (restart / close / edit-controllers) | a ready design to reach for, not work to do now |
| 10 | **ADAPT** | PM2/Circus flat naming as the anti-pattern the explicit list avoids — name it in the design | it is the fate of "just name them consistently", which is the obvious cheaper alternative |
| 11 | **REJECT** | Inferred or emergent coordination (AutoGen speaker selection, CrewAI's implicit manager) | the whole point of the change is that the person sets the boundary |
| 12 | **REJECT** | `StopWhenUnneeded=`-style auto-stop of a session whose `controllers` list empties | explicit user control argues for leaving it running and surfacing it; an empty list means *nobody may act*, not *nobody wants it* |

## Risks not addressed

Carried into the §10 entry and, where they need code, into TD-036:

- **No restart-rate limiting** anywhere in the design (takeaway 1).
- **No policy for a worker whose last controller exits** — the static reading ("empty = nobody
  may act") and the dynamic one ("orchestrators restart exited sessions") disagree.
- **No concurrency or ordering rule** for two controllers acting on one session at once — the
  confused-deputy case the multi-controller design deliberately creates.
- **No fan-out ceiling** or degradation behaviour for an orc-of-orcs as its membership grows;
  field reports put the knee at 3–5.
- **No distinction between a clean orchestrator exit and a crash** for whatever propagation is
  chosen.
- **Revocation depends on every RPC re-reading live state** — confirm nothing caches a grant or
  a membership list (the current gate does re-read; a member view that is derived must not become
  a cache).

## Method note

The research was a subagent's, and cadence §8 applies: a subagent's summary is evidence, not a
source. The lessons above were kept in the subagent's words with their URLs so a reader checks
the primary document rather than this file; the PR's fact-check review verified the URLs resolve
and the claims match what they say. Where a lesson contradicts the design rather than extending
it, the contradiction is recorded in the design's §10 entry, not resolved silently here.
