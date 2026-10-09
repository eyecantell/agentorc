# ADR 2026-10-08: Keep the one-hour prompt cache, restart a lapsed member rather than ring it, and trial a 200k context bound

**Status:** proposed
**Date:** 2026-10-08
**Related:** TD-378 (this research), TD-151 (metered profiles), design §6 rule 5 (the context bound), §6 rule 6 (lane news), §4.2a (`prices:`)

## Context

Paul, 2026-10-07: *do some (or have sonnet do some) research on how cached input works for claude
and see if we could leverage it in ao to save token costs.* The anchor read Anthropic's docs (a
Sonnet reader, sources below). It then measured eight days of agentorc's own sessions from Claude
Code's transcripts, read only.

**How the cache works, as documented.**
- The cached prefix is tools, then system, then messages. A write costs 1.25× base input for the
  five-minute lifetime and 2× for the one-hour lifetime. A read costs 0.05× on Opus 5.5 and
  Sonnet 5.5, and 0.025× on Fable 5.1.
- A hit refreshes the lifetime for free.
- Claude Code caches by itself. On a subscription the main conversation gets the **one-hour**
  lifetime, and subagents, compaction and titles get five minutes.
- On an API key, a cloud provider, or a subscription drawing on usage credits, every request gets
  five minutes.
- `CLAUDE_CODE_PROMPT_CACHE_TTL` (or the `promptCacheTtl` setting, v2.1.242 or later) chooses the
  main conversation's lifetime. `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` chooses the rest.
  `ENABLE_PROMPT_CACHING_1H` and `FORCE_PROMPT_CACHING_5M` set both, and `DISABLE_PROMPT_CACHING*`
  turns caching off.
- These break the prefix: a model switch, fast mode turned on, a Claude Code upgrade, `/compact`
  and `/clear` (each rebuilds the conversation layer), and a tool list loaded upfront that changes.
- These keep it: a CLAUDE.md edit mid-session (it does not apply until the next start or
  `/clear`), an effort change on the 5.5 models and Fable, permission modes, skills, and file edits.
- A `--resume` re-sends the whole conversation. Inside the lifetime it is read from cache, and
  after the lifetime it is written again in full.
- Sessions in one directory share the system prompt's cache.
- How a subscription's usage limits weigh a cached read against fresh input is **not documented**.
  The Pro help page says cached content "counts less" and gives no factor.

Sources: platform.claude.com/docs/en/build-with-claude/prompt-caching,
code.claude.com/docs/en/prompt-caching, code.claude.com/docs/en/env-vars, code.claude.com/docs/en/costs,
code.claude.com/docs/en/hooks, support.claude.com/en/articles/8324991.

**What agentorc's sessions spent, 2026-10-01 to 10-08.**
- The data is the main conversation of every transcript under `~/.claude/projects/`, subagents left
  out and each request counted once. Opus 5.5 alone is shown: 19,534 requests over 144 sessions,
  79% of them grinders.
- Weights are the API's: a read 0.05×, a one-hour write 2×, and output assumed 5× base input.
- The other models (Fable, Sonnet) show the same shape.

| what | share of the weighted cost |
|---|---|
| cache reads | 57.8% |
| cache writes, one-hour (no five-minute write was made) | 25.8% |
| output | 16.4% |
| fresh, uncached input | ~0% |

Of the writes:
- **16.8%** is each turn's new content, written once and read afterwards. That cost is unavoidable.
- **6.0%** is a whole context written again after the cache lapsed: a gap over an hour, 44 times.
  Of those, 23 were grinders rung by the doorbell after a median 5.4 h idle, re-writing a median
  191k tokens each. Most of the rest were the person's own sessions picked up again.
- **2.8%** is the first request of each of the 144 sessions. Its median context is 50k, of which
  27k was already cached from a sibling session's system prompt.

The one-hour lifetime is already earning its price. 813 requests (4%) came 5–60 minutes after the
previous one: waits on CI, on mail, on a subagent. Each was a hit. At five minutes each would have
written its whole context again, about 60% more than the week cost in total.

**Context size is what the reads follow.** The mean request reads 190k tokens, and context grows
about 1.5k tokens a request from a start near 50k. Requests over 200k carry **35%** of the whole
weighted cost, and those over 300k carry 11%. 300k is the bound every built-in role has today
(§6 rule 5). A request is above 300k when it ran past the bound mid-entry, or when it is a seat,
which rule 5 does not bound.

## Options

1. **Do nothing.** The 1h lifetime is already the subscription default, and nothing agentorc does
   breaks the prefix often: no model switch mid-run, and a promote restarts no session.
2. **Pin the one-hour lifetime for a metered profile.** A profile on an API key gets five minutes
   by default. Setting `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` in its layer's env costs 0.75× more on
   every write and saves a full re-write at each 5–60-minute gap. On this week's traffic that
   trade is lopsided in the 1h lifetime's favour, as above. Cost: one env key on the metered
   profile's layer (TD-151's build carries the profile).
3. **Restart rather than ring a member whose cache has lapsed.** The 23 grinder re-writes followed
   §4.10's doorbell: mail landed for a member idle for hours. When the doorbell is about to ring a
   member that has been idle longer than the lifetime (an hour) and holds a context over about
   100k, the host agent restarts it on its brief instead. The new run reads the mail, since its
   brief reads the inbox first. The restart writes about
   23k new tokens rather than about 190k at 2×. Saving: roughly 2–2.5% of the week's cost at this
   week's rate. It grows with the number of overnight rings. Cost: the member loses its
   conversation. The restart takes rule 5's precondition (§6) by analogy: the member is idle,
   holds no claim in progress, and has pushed its work. Without it, the doorbell rings as
   today.
4. **Lower the context bound from 300k to 200k, as a trial.** Over a restart cycle the mean context
   falls from about 175k to about 125k, so reads fall by about 29% a request. Against that, each
   cycle is about two-fifths shorter (150k of growth rather than 250k), and every restart pays its first write and a re-orientation:
   reading the ledger, the files and the design again. Nobody can net these on paper: the
   re-orientation's size is the unknown. Cost: one number in the repo's `.agentorc.yml` (§4.8
   *The bound has two layers*), and a week's comparison of usage per merged PR.
5. **Order briefs and primers so the shared part comes first.** Cold starts are 2.8%, and half of
   their prefix is already cached. Too small to pursue.
6. **Batch rings.** Rings inside the hour are already hits, so batching saves nothing unless it
   crosses the hour. Not pursued.

## Decision

Proposed, for Paul. Take options 2 and 3, and run option 4 as a one-week trial on ao-grind's
grinders, compared against the week before by usage-window percentage per merged PR. Option 1 is
the stance for everything else. On a subscription, the cached reads' weight against the weekly
window is not published. The trial's reading of the window itself is therefore the measure that
counts, and these API-price shares only rank the levers.

## Consequences

- Option 2 is a build entry: the metered profile's layer sets the main conversation's lifetime,
  and design §4.2a says so.
- Option 3 is a design-first entry: §4.10's doorbell gains a branch that restarts a lapsed
  member, with the precondition above, and the restart's `why` names the cause.
- Option 4 changes `.agentorc.yml`'s bound for the trial. The anchor reads the windows after a
  week, and the bound stays or goes back.
- A member that is restarted more often loses more of its conversation. That is acceptable only
  because rule 5 already restarts between entries, never mid-entry.

## Revisit triggers

- Anthropic publishes how a subscription's limits weigh cached reads.
- Claude Code changes its default lifetimes.
- A team moves to a metered profile.
- A week in which lapse re-writes pass 10% of the weighted cost.
