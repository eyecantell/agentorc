# agentorc — design

Status: **settled for build** (2026-09-04 design; the last open questions closed 2026-09-05). Nothing
is built. This document is the requirements and architecture agreed in the 2026-09-04 design
session; each open question at the end is a decision that changes what gets built. The project was
called `sessionherd` for most of that day; see §10 for the rename.

## 1. Problem

One person runs many interactive AI coding-agent sessions (today: Claude Code) across several
repos and hosts. Some are unattended workers (samscrape's `tdgrind` supervisor: three Claude
Code workers in tmux, in their own git worktrees, run on a night/weekend window and gated by
subscription usage). Some are the person's own conversations, opened in VS Code windows.

Today, knowing which session is working, which is blocked on a question, and which has quietly
died means cycling through VS Code windows and tmux panes by hand. Lessons from `tdgrind`
(samscrape `scripts/tdgrind.sh`, TD-274) and the 2026-08-07 stranded-work audit:

- A reboot silently killed the tmux server; a cron tick noticed 10 minutes later.
- OAuth lapses stalled workers twice until a human looked.
- Worker state was detected by grepping the pane (`Yes, I accept`, `[tdgrind] claude exited`,
  a 401 regex). Every one breaks when the CLI changes its wording.
- A worker's end-of-run report existed only in tmux scrollback; snapshots every tick lose up to
  a tick.
- Work that lived only in a session's chat was lost when the session closed.

## 2. Goals

1. **One view** of every session across hosts and repos with a trustworthy state:
   `working` / `needs-you` (waiting on a prompt, permission, or question) / `limited` (hit a
   usage or token cap, waiting on a reset) / `stalled?` / `idle` / `exited` / `closed` /
   `unreachable`, plus
   last-activity age and the pending question or reset time when there is one.
2. **Read and drive a session in place**: full conversation in an embedded terminal, type
   prompts, answer menus in the terminal, attach files from the laptop (drag and drop, a
   picker, or a pasted screenshot — this must be effortless, it is how briefs and specs reach a
   session).
3. **Lifecycle from the UI**: start a new session (fresh or resumed), close one out, kill one.
4. **Repo awareness**: git status per checkout/worktree, one-anchor-per-checkout enforcement,
   dirty-or-unpushed flags on idle/exited sessions.
5. **Configurable buttons**: per-repo commands (cmdorc-style specs) that run as sessions of
   kind `command` — same substrate, own tab (§4.5).
6. **Jump out**: "open in VS Code" for the session's directory on its host.
7. **Survive the laptop closing**: sessions live on the host (kmaster today, a VPS next), never on
   the client.
8. **Unattended supervision**: run windows, usage caps, wrap-up-then-kill, credential-lapse
   detection — `tdgrind` generalized per repo.
9. **Framework, not a Claude tool**: the core knows sessions, hosts, repos, and adapters. Claude
   Code is the first adapter; Gemini CLI, Codex CLI, and on-prem harnesses are later adapters.
   Share with other devs once it proves useful.

10. **Phone triage**: the Herd view works on a phone over a private network or an authenticated tunnel (§4.5) — state, pending question, one-tap
    answers — so a blocked session can be unblocked from anywhere. The embedded terminal is a
    desktop feature.
11. **Ready to close, decided by the person**: a per-repo checklist (PR merged, branch pushed,
    tree clean, no subagents or background tasks running, ledger/attention board updated) says
    when a session is *ready* to close; only the person closes it (**Close** kills the session,
    reaps the worktree, and moves the card to `closed`). An exit that fails the checklist is
    shown as `exited` with the failing items. The tool never declares work done.
12. **Dark mode**: CSS tokens, `prefers-color-scheme` default plus a manual toggle. The
    terminal panes are dark regardless, so light chrome is the jarring case at night.
13. **Local and volatile hosts**: the person's own laptop is a host too (transport `local`,
    no ssh). A host marked `volatile: true` sleeps with the lid; its sessions show
    `unreachable` (not `stalled?`) when the agent stops answering, its VS Code links use the
    local `vscode://file/<path>` form, and unattended policies refuse to start workers there
    unless overridden.
14. **A session is a tmux session, with or without a repo, with or without an agent.** A plain
    shell on `vpnmaster` or `host1` (proxmox) is a first-class card: it has a directory, a run
    log, a state, and Focus, just no hooks and no worktrees. A repo is optional; an adapter is
    just what decides where state comes from.

Non-goals (for now): multi-user access control, a kanban/task-board model of work (see §11 prior
art), replacing Claude Code's own `/resume`, mobile-first UI. A hosted service is **not** a
non-goal any more: it is the `relay` transport in §4.5b, kept compatible from phase 1 and
scheduled after phase 5.

## 3. Prior art (surveyed 2026-09-04; herdr added 2026-09-09, measured 2026-09-10)

No surveyed tool does multi-host + hook-fed state + VS Code links + usage-cap supervision. Since
herdr (below) multi-host on its own is no longer a differentiator; the combination still is.

| Tool | Shape | Borrow | Gap vs. goals |
|---|---|---|---|
| ttyd (MIT) | websocket + xterm.js around any command | the terminal-transport shape (xterm.js over a websocket around a pty); superseded 2026-09-05 by a bridge inside the UI process, since the pty would wrap `ssh` anyway (§10) | terminal only; a second daemon per host |
| ccmanager, claude-squad | TUI session managers, tmux + worktrees, many agent CLIs | ccmanager's launch specs as adapter reference | terminal-only, scraped state, single host |
| Vibe Kanban (Apache-2.0) | web kanban, per-task terminal, 10+ agents | UI ideas for diff review | task-board model, single machine, own execution tracking |
| agent-dashboard (bjornjee) | tmux orchestrator + PWA for approvals | same idea at PoC scale | maintenance unverified |
| Anthropic Remote Control / cloud sessions | single-session sync, Claude only | — | not a fleet view, not self-hosted |
| herdr (Apache-2.0, https://herdr.dev) — surveyed 2026-09-09, corrected 2026-09-10, measured 2026-09-10 ([ADR](decisions/2026-09-10-herdr-spike.md)); not in the 2026-09-04 survey | "the runtime coding agents run on": a Rust daemon per machine keeping agent sessions alive in persistent panes, one layout across local and ssh-added machines, restored after a restart; single binary (macOS, Linux, Windows); 21 agent CLIs; 17 *integrations*, of which six (Pi, OMP, Kimi, OpenCode, Kilo, MastraCode) push `idle`/`working`/`blocked` from hooks and the rest — Claude Code, Codex, Copilot, Cursor among them — only report a session id for restore, their state coming from screen-matching manifests; socket API with `events.subscribe`, `agent.*`, `worktree.*`, `plugin.*`; Claude rate-limit and context bars; ~1k community plugins found by a GitHub topic, no review; 36.5k stars, ~770k installs. Herdr, Inc.: $6M seed (Bessemer, YC) announced 2026-09-09; "Herdr Cloud" (no-ssh machines) next; releases 0.5.1 (2026-04) → 0.9.0 (2026-09). **Does not accept unsolicited pull requests** — an allow-list of approved contributors, bugs fixed by the maintainers' own agent, features via Discussions | the closest tool to agentorc found so far; the worktree API shape; a screen-rule fallback for prompts no hook reports (its detector catches the trust dialog); measured as a substrate 2026-09-10 and not taken (§10, ADR) | states are working / blocked / idle / done / unknown — one `blocked`, and the `pane.agent_status_changed` event carries only the state (the `--message` of a report is stored nowhere), so a permission, a question and the trust dialog look alike to anything above it and a usage-limit screen reads as `idle`; an outside source cannot take a Claude pane's state from the screen detector; a server restart ends every pane process (restore = layout + `claude --resume`); panes start through the person's interactive shell (rc files move the cwd); no run log, no exit code, no state for plain shells; the socket API is per machine (multi-host is the TUI over ssh); no `limited` with a reset time, no `stalled?`/`unreachable`; no run windows, usage gates, wrap-up-then-kill or credential-lapse detection found; no anchor rule, Ready to close, per-repo command buttons, VS Code links or first-party phone UI (the TUI over ssh is the mobile story; community mobile apps exist); runtime only, no notion of when work is done |

## 4. Architecture

```
laptop browser ──https──▶ agentorc UI (one process on any host with `agentorc[ui]`; a pty per open
                              │  terminal: `ssh -tt host tmux attach` ↔ xterm.js websocket)
                              │  ssh transport (no public ports on hosts beyond ssh)
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        host agent        host agent       host agent
        (kmaster)         (vps)            (host1, vpnmaster, laptop …)
          │  ├─ tmux server (systemd user unit, linger on)
          │  ├─ state dir  ~/.agentorc/sessions/<id>.json  ◀── adapter hooks write here
          │  ├─ run logs   ~/.agentorc/runs/<session>.log  ◀── tmux pipe-pane, continuous
          │  └─ policies   (run window, usage gate, reap worktrees, anchor rule)
          └─ repos from ~/.config/dev-cadence/repos.txt (+ ~/.agentorc/hosts.yml)
```

### 4.1 Session substrate: tmux, one session per conversation

- Session name `ao-<repo-or-dir>-<name>` (prefix lets the agent enumerate its own sessions).
  Both parts are slugified to `[a-z0-9-]` (tmux treats `:`, `.` and whitespace specially).
  **A name identifies one session within its scope** (the repo, or the directory for a
  repo-less session; decision 2026-09-10, §10, §9 invariant 12): it is what a person types
  into `ao focus`, `ao send` and the Herd filter, so two cards called `aotest` is a defect, not
  a namespace. Today `ao focus` and `ao send` take the full id (`ao-agentorc-tests-aotest`);
  with the rule below a bare name resolves to the one live session of that name in the
  current repo or directory, and the full id keeps working everywhere (TD-030). The rules, in
  the order the agent applies them at create:
  - the name is held by a **live** record (any state but `exited` / `closed`) → refused:
    "`aotest` is running — switch to it, or pick another name". The New session form learns
    this as you type, like the directory occupancy check (§4.5a), and offers **Switch to**.
  - the name is held by an **exited or closed** record → the new session **supersedes** it: it
    takes the id, the old record is forgotten, its run log is kept and linked from the new
    record as *previous run* (a new field). This extends the supersede that Resume has done
    since PR #17 — which closes the exited record and keeps it a day — to a fresh start under
    the same name, and goes one step further by forgetting rather than keeping, because the
    name now belongs to the new session. No `-2` card appears.
  - the id is taken in tmux by a session the agent has **no record of** (hand-made, or a stale
    pane the tick has not adopted yet) → the agent adopts it first if it is ours, else appends
    `-2`, `-3` and — unlike before — shows the suffixed name on the record, so what the Herd
    says is what tmux has. The agent handles tmux's "duplicate session" error explicitly rather
    than trusting the check.
  - `shell` sessions are named by the agent when the person gives no name (`shell`,
    `shell-2`, …) and follow the same rule under that generated name; registry-only cards
    (`ext-*`, below) are outside it, their ids come from the tool.
- Every session record carries: `name` (what the person called it), `kind`
  (`interactive` | `command`), `adapter` (`claude-code`, `shell`, …), `profile` (empty for
  `shell`), `dir`, `repo` (optional), `worktree` (optional), `adapter_id` once known (Claude
  Code's session uuid — read from the hook payload; it is what Resumable and the transcript index
  key on), `capabilities` (grants, §4.8 — empty for most sessions), `lane`, `progress` and
  `findings` (§4.8's report channels: what the session was handed and what it says it did),
  `role` (the preset it was started from, a badge and nothing more), and `unattended` with its
  schedule (§6). Grants, report channels, mode and schedule are independent fields: a grant
  says what a session may do to others, the channels say what it did, `unattended` says whether
  policies act on it, the schedule says when. Any can be set without the others. Resumable shows the name first and the id under it; a session started by hand
  outside agentorc shows only the id until it is **adopted** (attach to the tmux session, give it
  a name), which is also how hand-started sessions enter the Herd.
- A live session the adapter can see that has **no tmux at all** (`claude` in a VS Code
  terminal; Claude Code's registry `~/.claude/sessions/<pid>.json`) is a **read-only card**
  (landed 2026-09-10, TD-010 a): id `ext-<tool id>`, name and directory from the registry, state
  from its status (`busy` → `working`, `idle` → `idle`, `shell` → `working`), always `scraped`,
  no pane and no controls — the card offers Details, the badge reads *registry*, and every
  acting RPC (kill, close, send, mode) refuses with "started outside agentorc". Never stored:
  rebuilt on every tick and gone when the process is. Not doubled: a registry entry whose tool id
  one of our records carries, or whose directory one of our live agent sessions holds (our own
  pane before its first hook), is skipped. Allow/Deny on such a card would need the person to
  launch with agentorc's hooks layer, which is not wired (the hook needs an `AGENTORC_SESSION`).
- A **plain shell is an adapter** (`shell`, scraped: `working` while a foreground process runs,
  `idle` at the prompt — a shell waiting for you is the normal state, not an alert — `exited`
  when the pane is gone). Ad-hoc shells are ordinary
  `interactive` cards; the profile line reads `shell`. Predefined command buttons (§4.5) start
  `kind: command` sessions, which are hidden from the Herd unless the "show command runs"
  filter is on and never rank in the urgency sort.
- Created **only** by the host agent (one writer per shared resource — see §9). The UI, the CLI,
  and the cron reconcile all call the agent.
- The **host agent** runs under a user systemd unit with `loginctl enable-linger`, so a reboot
  restarts it rather than a cron tick noticing later. tmux is not systemd-owned (it daemonises
  away from whatever spawns it): the agent starts the server idempotently on its own startup and
  before every create, with `exit-empty off` so the server survives its last session closing
  (see §4.6). Default tmux socket, so hand-started sessions and "Copy tmux command" just work.
  tmux, not the agent, **owns the processes**: a host-agent restart, upgrade or crash reconciles
  against live panes and loses no session, where a runtime that holds the ptys itself must kill
  every session to restart and re-launch the tool with `--resume` once a client attaches (measured on herdr,
  [ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md)). This is a reason, not an accident.
- The adapter's **argv runs directly** in the tmux session (`new-session -c <dir> -- <argv>`),
  never through the person's interactive shell: rc files change directories, set aliases and
  print banners, and any of those moves or breaks a launch (the same ADR saw a `.bashrc` `cd`
  relocate every pane). The `shell` adapter is the one place the person's shell is the point.
- `history-limit` raised at creation; `pipe-pane` streams output to
  `~/.agentorc/runs/<session>-<created>.log` continuously (replaces tdgrind's per-tick
  snapshot; a reboot loses nothing that reached the pipe).
- Directory is the repo checkout, a worktree, or — with no repo — any directory the person
  names (recent directories remembered per host). The anchor rule (§9) is about directories, not
  checkouts: one *agent* session per directory; a repo's worktrees are just extra directories.
  Shells and `kind: command` runs are exempt — the rule is scoped to `kind: interactive` sessions
  with a non-`shell` adapter — because the person is not what the rule protects against, and a
  shell or a test run next to an agent in the same directory is the common case.

### 4.2 State feed: hooks first, scraping as a labelled fallback

The three states the person cares about are already emitted by the tools that have hooks. An
adapter installs a small hook script that writes
`~/.agentorc/sessions/<session-id>.json`. Hooks reach a session **per launch, as a settings
layer** (Claude Code: `claude --settings ~/.agentorc/claude-hooks/<profile>.json`, generated at
launch), never by editing the person's own `settings.json` and never per repo — sessions run in
plain directories too, hand-started sessions stay untouched, and repos carry their own hooks
(dev-cadence's SessionStart guards) that keep running alongside. Verified 2026-09-06 that a
`--settings` file's hooks fire. The hook script (`agentorc-hook`) knows which agentorc session
it belongs to from `AGENTORC_SESSION`, and which agent to talk to from `AGENTORC_HOME`; the host
agent sets both on the tmux session at creation — explicitly, because the tmux server may predate
the agent and carry another environment (decision 2026-09-06,
[ADR](decisions/2026-09-06-adopt-dev-cadence.md)). Claude Code's own session uuid is chosen by
agentorc at launch (`--session-id`), so `adapter_id` is known from birth; a resume passes
`--resume <id>` instead. **First-run quirk**: the "trust this folder?" dialog is reported by no
hook, so the adapter marks the directory trusted in the tool's `.claude.json` before launch.
The hook payload → state mapping:

```json
{"session_id": "...", "name": "tdgrind-1", "kind": "interactive", "tool": "claude-code",
 "tmux": "ao-samscrape-tdgrind-1",
 "cwd": "/home/kmaster/samscrape/.claude/worktrees/tdgrind-1",
 "state": "needs-you", "since": "2026-09-04T15:02:11Z",
 "pending": {"kind": "permission", "text": "Bash: git push origin td-301"},
 "confidence": "hook"}
```

State transitions (Claude Code adapter):

| Hook event | State |
|---|---|
| `SessionStart`, `UserPromptSubmit`, `PreToolUse` | `working` |
| `Notification` (permission / question), `PermissionRequest`, `PreToolUse` of `AskUserQuestion` | `needs-you` + pending text |
| `Notification` `idle_prompt` (idle for a minute) | ignored — an idle session waiting for you is `idle`, not an alert (first-use finding 2026-09-06) |
| `Stop` | `idle` |
| adapter `usage()` at cap, or the tool's own limit message | `limited` + reset time |
| `SessionEnd`, or tmux session gone | `exited` — the record's `pane` says whether a dead pane is still there to read (natural exit: yes; killed, or the tmux server restarted: no) |
| person clicks **Close** (kill + reap worktree) | `closed` — card kept a day, then history under Resumable |
| host agent unreachable (a property of the **host**; every card on it flips at once) | `unreachable` — card greyed, last known state kept visible |

`unreachable` is shown at the host level first: the host chip in the top bar goes hollow and one
banner row in the Herd says "laptop unreachable since 14:02 · 2 sessions". Where it sorts depends
on whether it is expected: a `volatile` host asleep sorts with `idle` (grey); a non-volatile host
that stops answering sorts right after `stalled?` (red). No new colour.

**Permissions are answered through the hook, not through keystrokes.** Claude Code's
`PermissionRequest` hook may return the decision itself. The adapter's hook script asks the host
agent and blocks; the UI's **Allow** / **Deny** (card, phone) answer the agent. *Measured
2026-09-06 (Claude Code 2.1.263):* the terminal dialog is **not** held back — it appears a few
seconds into the hook's wait, with a `permission_prompt` notification — but the hook's answer
still resolves it while the hook is blocking, so both channels work at once and the agent keeps
the buttons up (it ignores that notification while its waiter is live). If nobody answers before
the hook timeout the terminal dialog is the only channel left and the card's buttons collapse to
**Focus**, because the decision now lives in the terminal. The timeout is per profile (`permission_wait` in
`profiles.yml`), default 10 minutes for interactive sessions — long enough to reach a phone.
Unattended workers pre-authorise their tool set in the repo's tool settings (the allowlist tdgrind
already ships) so they rarely reach the hook at all; when one does, the `needs-you` state with its
age is the alert, and no extra policy is needed. Questions and multi-option menus always show pending text
plus **Focus** — never buttons — since the hook does not carry the option list and typing "1"
into a pane on the assumption that a dialog is still up is a race we refuse to run.

`limited` is distinct from `needs-you` because nothing the person does unblocks it, and from
`stalled?` because it is explained. The card shows the reset time and offers **Switch
profile** (below) or **Wait**. For Claude Code the reset time comes from the usage endpoint
tdgrind already polls; the pane's limit message is the scraped fallback.

`ready_when` (the **Ready to close** checklist) is evaluated by the host agent when a session
goes `idle` or `exited`:
`git status --porcelain` empty, branch pushed (a branch with no upstream is *not* pushed — that is
exactly the stranded work the check exists for), `gh pr view --json state` merged (when the branch
has a PR), no live subagents (Claude Code: `SubagentStop` balances `SubagentStart`; other
adapters: nothing running under the pane), and the ledger/attention board touched since the
session started (dev-cadence repos). Each item is a named check in `.agentorc.yml` so other
repos can pick their own subset. The Focus view shows the checklist live with a **Close** button
that enables when it passes; an idle card that passes shows "ready to close ✓" and a one-click
Close; an `exited` card shows the failing items. Closing is always the person's act: the
checklist is a readiness signal, never a verdict. This is the stranded-work audit with teeth.

Adapters without a usable hook set get a **pane classifier** and `"confidence": "scraped"`. The
UI shows the badge so a guessed state is never mistaken for a reported one. No adapter may write
a scraped state with `confidence: hook`. The classifier's intended shape (TD-015, after herdr's
detector caught the trust dialog that no hook reports): one versioned rule manifest per tool,
evaluated over the bottom of the pane, with `ao explain <session>` printing the rule that fired
and the evidence, and `ao explain --file` for fixtures. The same rules give `limited` from the
tool's own limit message and a `stalled?` that can say why. Scraped never outranks a fresh hook
state — fresh meaning a hook reported within the stall window; a session no hook has reported
on yet (the trust dialog appears before any hook fires) takes the classifier's verdict at once.

**Unseen idle.** An `idle` session nobody has looked at since it finished (`since > seen_at`;
Focus sets `seen_at`) renders "finished · unseen" and sorts above plain `idle` — the morning
triage case. Not a state: `idle` stays `idle` in every payload (TD-017).

**`send` confirms the prompt took.** `send` already refuses while a permission or question is
pending. Every `send` to an adapter that can read its tool's composer (§4.3 `composer`) waits
for the pasted text to paint before pressing Enter, then requires the composer to empty; if it
does not, Enter is pressed once more as `C-m`, and if the text is still there the call fails
with `prompt-stuck` — the text left where the person can see it, never re-pasted (TD-027:
measured 2026-09-10, an Enter that reaches Claude Code before it has read the paste is
dropped, so a burst of sends into a fresh or slow session lost every Enter but one; the
paint-wait removes the race). The Claude Code adapter's composer read counts painted text only: the tool paints
its suggested next prompt — often the session's own last prompt — in faint text, which is not
an unsubmitted prompt. With `wait` it also returns only after the session has started on *this* prompt and
settled again (`idle`, `needs-you`, `exited`, `closed`, `limited`, `stalled?`): a busy session queues the text, so the wait
first lets the current turn end — a stop on a question or an exit is returned as is, prompt
still queued — then requires the next turn to start. It fails with `prompt-stalled` when
nothing starts within a few seconds of the moment it could, `timeout` after the caller's limit,
and `removed` if the record goes away — so a policy's wrap-up request (§6) is known to have
landed, and no text is ever re-sent on a guess (TD-016).

Liveness cross-check: the agent also watches the pipe-pane log's mtime; a `working` state with no
output for longer than the adapter's `stall_after` is shown as `stalled?`, which is how a
credential lapse surfaces without a 401 regex.

### 4.2a Profiles: tool · account · model

People run more than one account of one tool, and more than one tool. A **profile** is
`(adapter, account, model)`, e.g. `claude-code · paul (max) · opus` and
`claude-code · grind (pro) · sonnet`. Every session carries one; the card shows it as a line.
Commands, policies, and the usage gate key on the profile, so two accounts of one tool are
gated and reported separately, and a `limited` session can be re-launched under another
profile. For Claude Code the adapter maps an account to its own config directory
(`CLAUDE_CONFIG_DIR`) and a model to the `--model` flag; other adapters map their own
equivalents. Profiles are declared once per host in `~/.agentorc/profiles.yml`.

### 4.3 Adapter contract

One package per tool under `agentorc/adapters/<tool>/`. Core never imports tool-specific
names outside the adapter. The `shell` adapter is the degenerate case and ships in phase 1
(it is how repo-less hosts get cards at all).

```python
class Adapter(Protocol):
    name: str                         # "claude-code"
    def launch_cmd(self, *, profile: Profile, resume: str | None, prompt_file: Path | None, unattended: bool) -> list[str]
    def launch(...) -> LaunchSpec                     # argv + env + adapter_id; writes the per-profile hooks layer
    def state_source(self) -> Literal["hook", "scraped"]
    def classify_pane(self, tail: str) -> State | None   # only for scraped adapters
    def transcript_path(self, session_id: str, cwd: Path) -> Path | None
    def quirks(self) -> Quirks                      # first-run dialogs, settings pre-seed
    def usage(self, profile: Profile) -> Usage | None     # quota + reset time, per account
    def usage_for(self, profile: str) -> dict | None      # the same by profile name, for the core (it cannot build a Profile)
    def composer(self, tail_raw: list[str]) -> str | None  # optional: the text painted in the tool's input line ("" empty,
                                                           # None when no composer is on screen); lets `send` confirm a submit (TD-027)
    def credentials_ok(self, profile: Profile) -> bool | None
```

Prompt injection is **core**, not adapter: the composer text goes in with `tmux load-buffer`
+ `paste-buffer -p` (bracketed paste, so a multi-line brief lands as one prompt instead of
submitting line by line) followed by `Enter` — no blind `C-u`, since what is painted in the
pane may not be a readline line. The Enter waits for the paste to paint and is confirmed by the
composer emptying, with one `C-m` retry, when the adapter implements `composer` (§4.2, TD-027);
adapters without it get the blind paste + Enter. **Send is disabled** while a permission or question is pending (the pane
owns a dialog) and, for scraped adapters, while a foreground process runs; otherwise it is
enabled — Claude Code queues input typed while it works. Menus and questions are answered *in* the terminal (keys pass
through); permissions go through the hook decision channel (§4.2). The core never types a menu
choice into a pane.

Adapter status at design time (verify before building each):

| Tool | State signal | Adapter type |
|---|---|---|
| Claude Code | full hook set incl. `Notification`, `Stop`, `PermissionRequest`; transcripts in `~/.claude/projects/`; live registry `~/.claude/sessions/`; usage via the OAuth usage endpoint (tdgrind `usage`) | hook-fed — **phase 1** |
| Gemini CLI | hooks since v0.26 + OSC 9 "action required / complete" notifications | hook-fed (verify) |
| Codex CLI | experimental hooks (Pre/PostToolUse); a "waiting" event unconfirmed | scraped until verified |
| `shell` (ad-hoc shell, Aider, a cmdorc command run) | none | scraped: foreground process vs prompt vs pane gone, exit code from the marker — **phase 1** |

### 4.4 Host agent

Python, one process per host, started by the same systemd user unit. Responsibilities:

- Enumerate sessions (tmux + state dir), merge, serve JSON over a local Unix socket.
- Create / kill / nudge / resume sessions (the only writer).
- Per-repo `git status --porcelain=v2 --branch` for every checkout and worktree the registry
  lists, cached with a short TTL.
- Policies (§6), run on a tick from the same process — no cron, no fd-9 lock inheritance.
- Usage: each live agent session's profile is asked its adapter's `usage_for` once a minute in
  a thread (never per tick); the last answer is cached, served by `usage`, streamed as a `usage`
  event for the top bar's per-profile figure, and drives the `limited` rule of §4.2 (an
  interactive session on a profile at 100% of a window shows `limited` with the reset time,
  `working` again once the window resets). A fetch failure keeps the last answer (TD-001).
- Attachment drop: accept an uploaded file (the UI copies it over ssh) into
  `~/.agentorc/attachments/<session>/`, return the path for the UI to insert into the composer
  (Claude Code takes file paths in prompts). Drag and drop onto the terminal or composer, a file
  picker, and clipboard paste (screenshots) on desktop; the share sheet on the phone.
- Permission decisions: the `PermissionRequest` hook script asks the agent over the socket and
  blocks until the UI answers or the hook times out (§4.2).
- Board write-back: **Snooze** (edit the `Due:` date) and **Done** (check the item off) on a
  dev-cadence `user_attention.md` item are one-line edits the agent makes and commits with a
  fixed message naming the session (`agentorc: snooze <item> to <date> (session <name>)`), so the
  main checkout never sits dirty and the history is auditable. The agent is the only writer to
  those files from this system; it never pushes. This is a bounded carve-out from cadence §4's
  branch → PR rule, proposed upstream as dev-cadence PR #83. The items themselves, with the
  board line each sits on, come from `nudge_user_attention.py --report --json` (dev-cadence
  PR #82); the Due strip, the Attention tab, and the edits all key on that line number.

### 4.5 UI

Single web process (FastAPI + websockets; plain server-rendered pages with a small amount of JS
and xterm.js — no SPA build step, so other devs can run it with one command). Talks to each host
over ssh: JSON RPC over `ssh host agentorc-agent rpc`, and one pty per open terminal running
`ssh -tt host tmux attach -t <session>` (`tmux attach` directly for `transport: local`), bridged
to xterm.js over a websocket; resize is a `TIOCSWINSZ` on that pty, which ssh forwards. No
terminal daemon on the hosts. Adding a host is `agentorc host add vps user@vps` + installing the
agent there.

Screens:

1. **Herd** (home): a **card grid** (decision 2026-09-04, over a table — keeps each session's
   facts grouped and shows a live tail). Each card: host/repo (or host/directory), name, age,
   state pill, profile line (`shell` for a shell), where (checkout, worktree → branch, or
   directory), dirty/unpushed flag, then either the pending permission with Allow / Deny (hook
   channel, §4.2), a pending question with Focus, the reset time with Switch profile / Wait, or
   the last output lines; buttons Focus / VS Code / more. The **more** menu holds Wrap up, Kill
   (confirms), Close (enabled only when Ready to close passes; a card that passes also shows it
   inline, see §4.2), Open shell here, Copy tmux command. A scraped state shows as a dashed pill outline. Two sort modes, remembered per
   browser: **Urgent first** (`needs-you` → `limited` → `stalled?` → `unreachable` on a
   non-volatile host → `working` → unseen `idle` (§4.2) → `idle` / `unreachable` on a volatile host → `exited` →
   `closed`) and **Pinned** (cards stay where the person dragged them, needs-you cards are
   highlighted and counted in the top bar). A **Due** strip above the grid lists the
   dev-cadence board items that are overdue or due today, each with Snooze and Done (agent
   write-back, §4.4); collapsed to a count when empty. Unreachable hosts get one banner row.
   Command-kind sessions are hidden unless "show command runs" is on. Two shortcuts next to
   **New session**: **Shell** (host + directory, nothing else) — and on Focus, **Open shell
   here** (a shell in the same directory as the session being viewed).
2. **Focus**: embedded terminal (full conversation, keyboard passes through, so menus and
   questions are answered exactly as in VS Code — there are no answer buttons under the
   terminal; a pending permission shows Allow / Deny in the Focus header, same hook channel as
   the card, because the hook holds the dialog back from the terminal until it times out), a
   **composer** (multi-line prompt box; Send delivers to the pane; the reason it
   exists beside the terminal is pastes, composing while the session is busy, and phone typing)
   with **Attach**, git status side panel, Ready-to-close panel, run-log download, Wrap up
   (sends the same wrap-up prompt the policy uses — one code path), Kill, "open in VS Code"
   (`vscode://vscode-remote/ssh-remote+<host>/<path>` — handled by the browser on the laptop,
   which is why this is a web UI and not a TUI).
3. **New session**: pick host → repo *or* directory → adapter → checkout, new worktree, or an
   existing worktree (only `exited`/`closed` ones are offered; an in-use one is greyed with
   "in use — resume from the Herd"; main refused if it already has a session) → fresh or
   resume → optional brief file → **Unattended** switch (off by default; disabled with "no `unattended:` block in
   `.agentorc.yml`" for repos without one; hidden for directory sessions). The same mode can be
   flipped later from the card or Focus header (§4.5a).
4. **Resumable**: inactive sessions from each adapter's transcript locator (Claude:
   `list_sessions.py`-style index over `~/.claude/projects`), grouped by host/repo, name first
   and adapter id under it, with Resume (prefills New session) or Switch to (a running one), and
   Adopt for a hand-started session. Closed sessions are filed here after their day on the Herd.
5. **Commands**: per-repo buttons from `.agentorc.yml` (cmdorc command specs where cmdorc fits);
   each press starts an `ao-<repo>-cmd-<name>` session of kind `command` with running/exited
   state, exit code, and a log; a recent-runs list; Focus on a run opens its terminal. The
   attention report's refresh *is* the repo's `attention` command — there is no second way to
   run a script.
6. **Attention**: the full dev-cadence board, every repo, undated items included, with the
   stale-sweep warning the report prints; clicking an item focuses the session that left it
   (via `adapter_id`); Snooze and Done as on the Due strip. No sessions column — the Herd is the
   sessions view.

Security: the UI can type into a shell as you, so it is root-equivalent. **Decision
2026-09-04, generalised 2026-09-06: never a bare public port.** The UI is reached over a private
network or through an authenticated tunnel, and holds no credential beyond the host ssh keys.
The concrete options, any of which satisfies the rule (§4.5b has the reasoning):

- a private network the laptop and phone already join — the existing WireGuard on `vpnmaster`
  is enough; Tailscale is the same thing with less setup, not a requirement;
- an authenticated tunnel — Cloudflare Tunnel + Access from `127.0.0.1:8765` (samscrape already
  runs `cloudflared`), which gives a hostname reachable from anywhere with a login in front and
  carries the websockets;
- the LAN address at home only.

Default until one of those is set up: the UI binds to `127.0.0.1` and the laptop reaches it
through `ssh -L 8765:127.0.0.1:8765 kmaster`.

Phone layout (lands in phase 2 with the phone's route in — WireGuard or the tunnel; until then the UI is reachable only over
`ssh -L`): the Herd view collapses to cards sorted `needs-you` first with Allow / Deny on a
pending permission (hook channel) and a Focus button, the Due strip on top. Focus gets a
**narrow mode** below 720px: header, pending text, the terminal full-width with a soft-key row
(`↑ ↓ ← → Enter Esc Tab 1–9`) so menus and questions are still answered *through the
terminal*, the composer under it, side panel collapsed. The git panel and the New-session form
stay desktop-width.

Browser mechanics (2026-09-06 review):

- **Live state** comes over one `/events` websocket per browser tab, pushing per-session
  deltas (state, pending, age, tail, ready-to-close) and host reachability; the page patches
  the DOM by session id. Pages are server-rendered on load and never fully re-rendered after.
  Reconnect with backoff; on reconnect the page reloads its snapshot once.
- **Pinned layout**: the client owns card order and position (localStorage, by session id);
  pushed data only patches card content. New or adopted cards are inserted at the top in
  Pinned mode; a card whose session drops out of the Herd is removed and its slot forgotten.
- **Permission countdown**: the delta carries the deadline once; the browser counts down
  locally. The timeout transition (buttons collapse to Focus) arrives as an ordinary state
  delta, never from the local clock reaching zero.
- **Tail hygiene**: the host agent strips ANSI and control bytes and caps the tail (lines and
  width) before it enters a session record; templates autoescape. Pane output is untrusted
  text everywhere it is shown outside xterm.js.
- **Keyboard focus** on Focus: clicking the terminal or the composer gives it focus; after
  **Send**, focus returns to the terminal so a follow-up menu can be answered at once; `Esc`
  in the composer moves focus to the terminal; `Tab` inside the terminal passes through to the
  pane. A pending permission or question shows a hint on the composer ("answer in the terminal
  above") instead of accepting Send.
- **Errors**: every RPC-triggered control reports failure the same way — a toast on the Herd,
  an inline banner in the Focus header — with the agent's error text and a Retry where one
  makes sense. There is no silent failure path.
- **VS Code links** need the `hosts.yml` `ssh` target to be an alias in the *person's own*
  `~/.ssh/config` (that is what Remote-SSH resolves); agentorc's multiplexing config is
  separate and never edited by hand. `vscode_host` in `hosts.yml` overrides the alias if the
  two differ. Mockups (2026-09-04): https://claude.ai/code/artifact/0e14af3a-5e5a-4d9c-88b2-74205c394c04

### 4.5a Controls

Every button in the mockups, what it does, and who executes it (UI → host agent RPC unless
noted). If a control is not in this table it does not exist.

| Where | Control | Does |
|---|---|---|
| top bar | **New session** | opens the New session form |
| top bar | **Shell** | starts a `shell` session: host + directory, nothing else asked |
| Herd | **Urgent first / Pinned** | sort mode, remembered per browser |
| Herd | host / repo / profile filters, **show command runs** | filters; the last one reveals `kind: command` sessions |
| Herd banner | **Retry** | asks the agent on an unreachable host again now instead of on the next tick |
| card | **Allow / Deny** | answers a pending permission through the hook channel; shown with the time left |
| card | **Switch profile…** | re-launches a `limited` session under another profile (resume id carried over) |
| card | **Wait** | dismisses the limited slot until the reset time |
| card | **Close session** (inline, only when Ready to close passes) | kill + reap worktree → `closed` |
| card | **Focus** | opens the Focus screen |
| card | **VS Code** | `vscode://` link for the session's directory on its host (browser-handled) |
| card | **more ▾** | Wrap up · Kill (confirms) · Close (as above) · Open shell here · Copy tmux command |
| card / Focus header | **unattended / interactive** badge | a toggle: click flips the session's mode in its record (agent RPC); policies pick the change up on their next tick. Cards show the badge only when unattended; Focus always shows it. Flipping to interactive is how a person takes over a worker; flipping to unattended hands a session to the run window and usage gate, and needs the repo's `unattended:` block |
| Due strip / Attention | **Snooze ▾** | +1 day · +1 week · pick a date → agent edits the item's `Due:` and commits |
| Due strip / Attention | **Done** | agent checks the item off and commits |
| Due strip / Attention | item text | expands the row: full text, context links, and *open board in VS Code* at that line; no separate Open button |
| Due strip / Attention | session link / **Focus session** | opens the session that left the item (by adapter id); a closed one opens in Resumable |
| Due strip | **full board →** / **▾** | jumps to the Attention tab / collapses the strip to its count |
| Focus | **Allow / Deny** | same hook channel as the card |
| Focus | **Open shell here** | a `shell` session in this session's directory |
| Focus | **Wrap up** | sends the wrap-up prompt (same one the policy uses) |
| Focus | **Kill** | confirms, then kills the tmux session; worktree kept; state `exited` with `pane: false` — unlike a natural exit, whose dead pane is kept, a kill destroys it, so the card offers Details and Focus / `ao focus` refuse without calling tmux (TD-023) |
| Focus (exited / closed) | **Resume this conversation** / **New session here** / **Forget** | the exited banner: New session prefilled with the directory and, for Resume, the tool's session id; Forget removes the record (the pane and its run log stay readable until then) |
| Focus | **Copy / Paste** | terminal clipboard: Copy takes the terminal selection (also Ctrl+Shift+C, or Ctrl+C with a selection — no interrupt is sent then); Paste sends the clipboard through the terminal (also Ctrl+V — Claude Code would otherwise read a raw ^V as an image paste — Ctrl+Shift+V, Shift+Insert, right-click). Needs a secure context: https or localhost |
| Focus composer | **Attach** / drop / paste | uploads to `~/.agentorc/attachments/<session>/`, inserts the path |
| Focus composer | **Send** | pastes the composer text and presses Enter, confirmed by the tool's composer emptying (one `C-m` retry, then `prompt-stuck`; §4.2, TD-027) |
| Focus side panel | **diff / log / PRs**, run-log link, **Close** | git views; download; Close as above |
| New session | **Unattended** switch | tags the session `unattended` (policies apply); disabled without an `unattended:` block, hidden for directory sessions |
| New session | **Role** preset + **Lane** field | `plain` (default) or a preset from §4.8 (built-in `grinder`, `hunter`, `orchestrator`, or one the repo's `.agentorc.yml` defines). A preset fills the brief from its template, the lane's default, and the grants it carries; each can be edited before Start. Lane is the ordered list of references (`TD-027, TD-019`) or `free-pick`. Independent of the Unattended switch and of any schedule |
| New session | **Grants** checkboxes | the `capabilities` the session gets (§4.8; today only `orchestrate`). Unchecked by default for every preset but `orchestrator`; shown with a one-line warning of what the grant allows |
| card | **report line** | shown only when a channel is non-empty: progress `TD-027 → PR #59 · 1/2 done`, findings `3 filed`, an orchestrator's `last tick 20:10 · 2 wrapped up`; an entry the agent derived (not declared) is dashed, like a scraped state. Any session can have one — a plain interactive session that files a TD gets `1 filed` |
| Focus side panel | **Reports** | the full `progress` and `findings` lists: each reference with its status, PR or priority, time, and declared / derived; **Drop** on a claimed progress item (agent RPC, recorded as dropped by the person) |
| Focus header | **grants** chip | lists the session's `capabilities`; click to revoke or grant (agent RPC; takes effect on the next call the session makes) |
| New session | **Where**: this directory / new worktree | for a git repo, the agent creates `<repo>/.claude/worktrees/<name>` on branch `<name>` from origin's default branch (reused if it exists; the repo's `hydrate_worktree.sh` runs when present) and the session runs there — landed 2026-09-06 after a session was started in the main checkout beside its anchor |
| New session | name field → holder | as you type, the form asks the agent who holds that name in the chosen repo or directory (§4.1): a live holder disables Start and shows **Switch to**; an exited or closed holder shows "replaces the exited `aotest` — run log kept" and Start proceeds; free names show nothing |
| New session | directory field → occupancy | as you type, the form asks the agent who holds the agent slot for that directory — agentorc's own live agent sessions *and* live sessions the adapters can see outside agentorc (Claude Code's registry) — and, when it is taken, disables "this directory" and selects a new worktree (landed 2026-09-06; the create RPC refuses the same way) |
| card (closed, or exited with `pane: false`) | **Details** | the Focus page without a terminal (the pane is gone); the banner offers Resume / New session here / Forget |
| card (registry-only, badge *registry*) | **Details** | the Focus page without a terminal or composer (§4.1: a session started outside agentorc with no tmux); VS Code link only — no mode toggle, no ⋯ menu |
| New session | **Start session / Cancel** | agent creates the session / discards the form |
| Resumable | **Resume** | New session prefilled (host, repo, directory, worktree, Start = Resume) |
| Resumable | **Switch to** | the running card in the Herd |
| Resumable | **Adopt…** | attach to a hand-started tmux session and name it |
| Commands | **Run / Stop** | start a `kind: command` session / kill it |
| Commands | **log**, **Focus** | the run log; the run's terminal |
| Commands | **edit yml** | opens `.agentorc.yml` in VS Code |
| Focus header | **VS Code** | same `vscode://` link as the card |
| Herd top bar | **filter…** text box | matches name, repo, directory, branch; client-side |
| Resumable | **search transcripts…**, Recent / Closed / With board items, date range | filters over the transcript index — *phase 4 polish; phases 1–3 ship the plain list* |
| Commands | host / repo filters | client-side filters — *phase 4* |
| Attention | repo filter, overdue · today · this week · undated | client-side filters — *phase 4* |

### 4.5b Reachability, and the shape of a hosted service

Why this is not "install Tailscale": for one person the private network is fine, but the
question that decides the long-term shape is *how would someone who has never opened a port
use this?* The answer is the one Tailscale and `cloudflared` themselves use — **the host agent
dials out; nothing on the host listens.** Three transports, one agent:

| transport | who runs the UI | how the agent is reached | who it is for |
|---|---|---|---|
| `local` | you, on the same host | Unix socket | phase 1, one machine |
| `ssh` | you, on a host you choose | `ssh host agentorc-agent rpc` (§4.6) | phases 2+, several hosts you own |
| `relay` | a service (yours or a hosted one) | the agent opens an outbound connection to the relay and keeps it up; the relay authenticates the person and proxies the UI, `/events`, and the terminal websocket over it | non-technical users; the hosted product |

The `relay` transport is the hosted service: `pipx install agentorc && agentorc join <token>`
on a laptop or a server, log in on a web page, done — no port forward, no VPN client, no ssh
keys. It keeps every invariant in §9: the agent is still the only writer, sessions still live
on the host, the relay sees only what the UI sees today. What changes is where the UI process
runs and who is trusted to run it, which is a product decision, not an architecture one.

Consequences for what gets built now: the agent's RPC stays a plain JSON-lines stream over any
byte pipe (already true — `agentorc-agent rpc` is a stdio bridge); the terminal bridge, which
today spawns `tmux attach` locally under `/term/<id>` (phase 1), **must move onto that same pipe
in phase 2** rather than gain a port of its own; and nothing in the UI may assume it can reach a
host by address. `relay` itself is not scheduled; it is a phase after 5, and the first
hosted version can be a single small VPS running the relay and the UI for a handful of people.

### 4.5c Product direction (2026-09-06, a paragraph, not a plan)

Two products share this architecture and differ only in who owns the host: **bring your own
machine** (the `relay` transport above: hosted UI, the person's sessions stay on the person's
host) and **we host your workspace** (a managed host we provision with the agent preinstalled,
reached the same way). Lead with the first. The person's Claude Max login, their repos, their
tools, and the cost of what their sessions do stay with them; we hold nothing but what the UI
shows. A managed host is an add-on for someone with no machine, built when someone asks for it,
not before. A hosted "run Claude Code for you" is the one thing every model supplier already
sells with their own login and price, so that is not where the value is.

What no supplier offers, and what this design is for: **one neutral view across tools** (Claude
Code, Gemini CLI, Codex, plain shells), on machines you own, with policies and a working cadence
that never strands work. Neutrality is the moat and it only holds while agentorc stays a layer
over the tools rather than a hosted copy of one.

The later step, and the strongest one, is **automated context management for people who are
not developers**: every session's work lands as a commit, a branch, a ledger line, and a board
item without the person knowing what a branch is; they see what changed, what is waiting on
them, and what would otherwise have been lost. dev-cadence is that system for developers, and
agentorc's fleet view is where its rules (the anchor rule, stranded-work sweeps, ledger before
idle) and agentorc's own Ready to close get exercised unattended first. Sequence: self-hosted for developers (now) → relay →
managed host on demand → cadence-as-a-product. Nothing here changes what phase 2 builds; it
says why the terminal must ride the agent's pipe and why the adapter contract stays neutral.

### 4.6 Transport and terminal mechanics (2026-09-05 review)

Decisions taken from a review of the `sessionorc` layer before build:

- **One long-lived ssh per host, JSON lines over it.** The UI keeps `ssh host agentorc-agent
  serve` open and speaks newline-delimited JSON requests/responses on its stdin/stdout (the
  same protocol the CLI speaks to the Unix socket locally). No per-call ssh handshake, so a
  Herd refresh across hosts is one round trip, and no argument ever reaches a remote shell —
  ssh's argv-joining is never used for data. The connection is re-opened with backoff when it
  drops. Terminal attaches (`ssh -tt host tmux attach -t <name>`) are separate ssh processes
  and reuse the same master via `ControlMaster auto` / `ControlPersist` in a config file the
  UI writes and passes with `-F`, so a person's own ssh config is untouched.
- **`unreachable` is diagnosed, not assumed.** The transport distinguishes *ssh failed* (host
  down or asleep) from *ssh ok, agent RPC failed* (agent crashed, stale socket). Both grey the
  cards; the banner says which ("laptop unreachable" vs "agent down on host1"), and **Retry**
  on the second case also tries `systemctl --user restart agentorc-agent` over ssh.
- **Creation is serialised per directory** inside the host agent (one `asyncio.Lock` per
  resolved path), which is what makes the anchor rule (§9 invariant 2) a guarantee rather than
  a check that two clicks can race past.
- **Attach behaviour with another client present.** tmux's default `window-size latest` means a
  browser Focus and a VS Code `tmux attach` on the same session re-size each other's view as
  each is used. Phase 1 accepts this and tests it (it is what a hand-typed second `tmux attach`
  does today); `window-size manual` plus a fixed `default-size` is the fallback if the reflow
  upsets Claude Code's TUI.
- **Reconnect contract.** The pty lives in the UI process. If that process or the websocket
  drops, the browser reconnects with backoff and the fresh `tmux attach` redraws the current
  screen; nothing is replayed from the run log. `send-keys` is an agent RPC independent of any
  attached pty, so a Send never depends on a Focus being open. The terminal shows tmux's
  scrollback (`history-limit`) only; the run log is a download, never a terminal source.
- **Scrollback is tmux's, reached through tmux (TD-022, 2026-09-09).** tmux repaints the client
  in place and keeps the history itself, so xterm.js runs with no local buffer. The attach sets
  `mouse on` on the session (a session option, never the person's global one): the wheel reaches
  tmux, which enters copy mode over its history and leaves it on scrolling back to the live
  screen. Shift+PageUp / Shift+PageDown do the same by a bridge message the UI turns into
  `copy-mode -e -u` / `page-down` against the session (there is no escape sequence for copy
  mode). Mouse tracking means plain drag goes to tmux; Shift+drag selects in the browser.
- **Run-log retention.** A session's log is bounded by its lifetime; retention is by age:
  logs of `exited`/`closed` sessions are deleted after `runs_keep_days` (default 30) on the
  agent's tick. Live logs are never truncated, so invariant 3 holds while the session exists.
- **pty bridge implementation.** `ptyprocess` (or `pexpect`'s pty layer) for the child pty, so
  controlling-tty, `SIGWINCH`, and teardown are handled by a maintained library; the UI adds
  only the asyncio read loop and the websocket framing. The "about 150 lines" in §10 assumes
  this.

### 4.7 CLI

Package and canonical command: `agentorc`. The package also registers `ao` as an alias
(`ao status`, `ao new`, `ao shell`, `ao focus <name>`, `ao off --now`) because that is what gets
typed day to day; it is a separate console-script entry so anyone with a colliding `ao` can
drop it without losing anything. The CLI is a thin client of the host agent RPC — it never touches tmux
itself (§9 invariant 1), with one read-only exception: `ao focus <id>` and `ao new --attach` /
`ao shell --attach` run `tmux attach` on the session, the terminal's Focus screen, which
creates, kills and types nothing (under `--json` the attach argv is printed instead). That is
how a session started from any terminal gets a first-class card: `ao new --attach` where you
would have typed `claude` (TD-010 b). Every subcommand takes `--json` and prints the RPC result with the ids
the next call needs (TD-018); `ao explain <id>` prints a session's screen, the rule that fires
on it and whether it applies, and `ao explain --file` classifies a saved screen (TD-015); `ao --skill` prints the rules an agent driving `ao` from inside a
session must follow (TD-019 — planned for phase 5, pulled forward and landed 2026-09-10 because an orchestrator session driving `ao` came first; `ao --skill > .claude/skills/ao/SKILL.md` installs it in a repo, the New-session install offer is still phase 5). Both follow herdr's JSON-first CLI and skill file, which made the spike's
automation a matter of `jq` ([ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md)).
`ao new <name>` applies §4.1's name rule and says so: a live holder is refused with
"`aotest` is running — `ao focus ao-agentorc-tests-aotest`, or pick another name" (exit 1, the
holder's id under `--json`); an exited or closed holder is superseded and the reply names the
previous run's log. Once the rule holds, every subcommand that takes an id also takes a bare
name and resolves it within the current repo or directory (TD-030), so the hint can say
`ao focus aotest`.
Sessions report through the channels in §4.8: `ao progress claim TD-027`, `ao progress done
TD-027 --pr 59`, `ao progress drop TD-027 --why "..."`, and `ao finding TD-029 --priority low`
(each a small RPC on the calling session's own record — `--id` for another's, since the channels
are ungated; `ao status -v` prints the same report line the card will, and `--json` the entries). Presets are picked at start, `ao new
--role grinder --lane TD-027,TD-019` (`--lane` landed with step 2, the rest with step 5;
`--lane free-pick` for scan-and-choose; `ao roles` lists
what the repo and the package define; `--grant orchestrate` adds a grant a preset lacks, and
works without a preset today). `ao grant <id> orchestrate` / `ao revoke <id> orchestrate` edit a
running session's grants (the `set_grants` RPC; `ao status -v` and `--json` show
`capabilities`). The CLI reads the calling session from `AGENTORC_SESSION`, the variable the
hook already uses (§4.2), and sends it as the request envelope's `caller` with every RPC
(landed 2026-09-10, TD-028 step 1): that is how a report lands on the right record and how the
agent tells a worker acting on another session from a person typing in a terminal (§4.8).

### 4.8 Capabilities, report channels, and role presets (2026-09-10)

Four unattended workers ran on kmaster the first day the Herd showed more than one, and the
Herd could not say which TDs any of them held, had finished, or had filed on the side; nor
could it stop one worker from `ao kill`-ing another. Both gaps are about what a session *does
to agentorc*, not what it is called, so the first-class concepts are **capabilities** — verbs
the agent can see — and **roles** are only presets over them. Considered and rejected: a
`role` field the agent keys on (a grinder that files a TD while grinding, as run 2 did with
TD-025, is misdescribed by any single label; and a display keyed on a label shows what a
session was called rather than what it did).

Two kinds of capability, deliberately different:

**Report channels** — ungated, any session may write them, the Herd renders whichever are
non-empty. Two channels cover every worker seen so far and the person's own sessions too:

- `progress`: references the session set out to resolve. Entries
  `{ref, status: claimed | done | dropped, pr, why, at, source}` — `why` carries
  `ao progress drop`'s reason and is empty otherwise. The `lane` on the record is the
  ordered list of references (or `free-pick`) the session was handed, so the card can say
  *1 of 2* without parsing the brief. One reference is one entry: a report upserts by `ref`, in
  the order the references arrived, and a reference is canonical (`td-27` and `TD-027` are one
  entry; a bare number is a PR, `#59`). The two RPCs are `progress` and `finding`, ungated like
  the channels themselves — an entry invariant 10 refuses is not an error, the reply carries the
  record as it stands and names the `refused` entry (landed 2026-09-11, TD-028 step 2).
- `findings`: references the session filed. Entries `{ref, priority, at, source}`.

Each entry has a **source**, on the same rule as state (§4.2): **declared** — the session said
so through `ao progress` / `ao finding`; the skill file (`ao --skill`, TD-019) is to tell every
session to declare a claim before its first edit and the result before moving on (TD-028 step
2); **derived** —
the tick reads the session's worktree branch (`tdNNN-*` → claimed), the PRs from that branch and
their merge state (merged → done), and ledger rows that appeared on main from that branch
(→ finding), and fills in what the session forgot, always marked `derived` (landed 2026-09-11,
TD-028 step 3, `sessionorc.reports`). Three things make that honest rather than magical: only the
`tdNNN-*` branch shape is read (anything looser turns `release-2` into a ledger reference); the
merge state comes from `gh`, the only thing that knows a squash merge happened, and a PR already
derived as claimed is re-checked by number, so a merge lands even after the session has moved on
to its next branch; and a merged PR's ledger rows are read from its squash-merge commit on
`origin/<default>`, found by the `(#N)` in its subject — the one link that survives GitHub
deleting the merged head. No `gh`, no network, no origin, a clone that has not fetched since the
merge, a claim on a PR older than the one page `gh` is asked for: fewer entries, never an error
and never a guess, on a five-minute cadence detached from the tick, so nothing waits on it; **scraped** — a
`TD-NNN` on the screen, a TD-015 rule, fallback only. A declared entry is never overwritten by a
derived one (§9 invariant 10); a derived entry is replaced the moment the session declares the
same reference. A reference is a ledger id (`TD-NNN`), an attention-board line, or a PR number;
the repo's `.agentorc.yml` names where its ledger lives (§5). Lanes are references, not prose:
"refactor the UI module" is not a lane until it has an entry a card can link to.

**Grants** — gated, recorded in `capabilities` on the session, checked by the agent on every
acting RPC. One exists today:

- `orchestrate`: the session may act on *other* sessions — `send`, `keys`, wrap-up, `kill`,
  `close`, `mode`, `new`, `remove`, and `set_grants` (gated on every target, so a session cannot
  grant itself). Without it, an acting RPC whose caller is a session and
  whose target is a different session is refused with "needs the orchestrate grant"; reads
  (`status`, `tail`, `explain`) are never gated. The caller is known from the session id the CLI
  sends (§4.7); an RPC with no caller is a person at a terminal or the UI, and is allowed as
  today; a caller id the agent has no record of is a session too, holding no grant. The agent
  checks the gate before the method runs, against the record as it is then, so a grant or a
  revoke takes effect on the session's next call (landed 2026-09-10, TD-028 step 1). This is a
  guard against a confused worker, not a security boundary — the socket is
  local and the id is an environment variable — and it closes the gap where any worker could
  kill its neighbour. §9 invariant 5 still binds a granted session: interactive sessions are
  out of reach whoever the caller is.

Being scheduled is **not** a capability and a grant carries no schedule: everything time-shaped
stays on the `unattended` side (§6, TD-026) and applies to a session whatever it holds.

**Role presets.** A role is a name for New session and `ao new` that resolves to a brief
template, a default lane shape, and default grants; the record keeps the name as `role` for the
badge and nothing keys on it (§9 invariant 9). Three ship with the package; a repo may redefine
any of them or add its own (§5):

| Preset | Brief template says | Lane | Grants | Typically writes |
|---|---|---|---|---|
| `grinder` | resolve each lane item to a merged PR: verify, fix, test, independent review, merge, archive the entry; never free-pick when given a list; never touch another session's worktree | references or `free-pick` | none | `progress`, and `findings` for what it meets on the way |
| `hunter` | look for problems and file them with evidence — probes, measurements, logs — and never fix them (a hunter has no reason to under-report what it would otherwise have to fix) | an area (`tests`, `ui`, a path) or `free` | none | `findings` |
| `orchestrator` | read `ao --json status` on a cadence; wrap up unattended sessions past their stop, resend a stalled prompt with `--wait`, restart a worker whose tool exited, forget exited records, escalate to the attention board when a person is needed; never create work | the host, or a list of sessions | `orchestrate` | `progress` per tick: sessions acted on and what was done |
| `plain` | — (no template) | — | none | whatever it declares |

The first orchestrator is a **session, not code**: its brief is the samscrape supervisor's
rules written for an agent driving `ao`, and it runs for a few evenings before any rule becomes
a §6 policy. Rules that prove mechanical (wrap up at the stop time, retry a stalled send) move
into the tick; those that needed judgement (stuck or thinking? interrupt now?) stay in the
brief. The grant is what makes this safe to try: the orchestrator's power is a field the person
can see on the Focus header and revoke, not a promise in its prompt.

## 5. Configuration

- Hosts: `~/.agentorc/hosts.yml` on the UI host (`name`, `transport: ssh|local`, `ssh`
  target, `volatile: true|false`, `repos_registry` path, `runs_keep_days`). The UI process itself may run on a
  laptop; only the session hosts need to stay awake. The parser is `sessionorc.hosts`, shared by
  the UI and the host agent: in phase 1 both run on the one machine and read the same `local`
  entry (`name`, `vscode_host`, `local`, `volatile`, `repos_registry`, `runs_keep_days` — landed
  2026-09-10, TD-004; the env-var overrides are gone). A field the *agent* acts on
  (`runs_keep_days`) is read on the session host from its own file's `local` entry, so a phase 2
  session host carries its own copy; the ssh entries remain phase 2.
- Repos: the dev-cadence registry (`~/.config/dev-cadence/repos.txt`) on each host — not
  duplicated. A repo without dev-cadence can still be listed there. Directories that are not
  repos are not registered anywhere: New session takes a path, and the agent remembers recent
  ones per host in `~/.agentorc/recent_dirs`.
- Per repo: `.agentorc.yml` (checked in):

```yaml
adapter: claude-code
worktrees: .claude/worktrees         # where new-session worktrees go
anchor: main-checkout-single         # refuse a 2nd agent session on the main checkout (shells exempt)
unattended:
  workers: 3
  brief: ~/.tdgrind/{name}-prompt.md
  window: {weekday: "20:00-06:00", weekend: all}
  usage_gate: {five_hour_pct: 70, weekly_pct: 70}
  wrapup_minutes: 15
  creds_min_hours: 0.25
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick}
  hunter: {brief: docs/briefs/hunter.md}
  orchestrator: {brief: docs/briefs/orchestrator.md, grants: [orchestrate]}
ledger: docs/technical_debt.md        # what a TD-NNN reference resolves to
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands:
  - name: test        ; run: pdm run test
  - name: cluster     ; run: ./scripts/cluster-status.sh
  - name: attention   ; run: python scripts/nudge_user_attention.py --report
```

A repo without the file gets defaults: `adapter: claude-code`, worktrees under
`.claude/worktrees`, `ready_when: [tree_clean, branch_pushed, no_subagents]`, no commands, no
unattended mode, the three built-in presets with the package's brief templates, and
`docs/technical_debt.md` as the ledger. The `unattended:` block is where every time-shaped
setting lives (window, gate, stop times — TD-026 extends it); a `roles:` preset never carries
a schedule, and a grant never carries one either. A directory session (no repo) reduces to `ready_when: [no_subagents]`.

## 6. Policies (the tdgrind supervisor, generalized)

Each runs on the host agent's tick, per repo, only for sessions whose record says
`unattended: true` (set at start by the New session switch, or flipped later by the badge
toggle on the card or Focus header; interactive sessions are exempt from gates). Mode is a
field on the session record, never re-derived from the brief or the name, and a flip takes
effect on the next tick without restarting the session. The brief file is required only when
a *policy* starts a worker; a session flipped to unattended keeps whatever it was doing.
Policies key on `unattended` and the session's schedule, never on its role preset or its
grants (§4.8, §9 invariant 9): an orchestrator session left running past the window is wrapped
up like any worker, and a plain session with a stop time is stopped like any worker. A policy
that starts a worker names the preset and lane it starts it with (`workers: [{role: grinder,
lane: free-pick}, …]` replaces the bare `workers: 3` once §4.8 lands); the schedule stays on
the block. A policy is agent code and needs no grant; a session doing the same work does.

- **Run window**: start missing workers inside the window; wrap-up-then-kill outside.
- **Usage gate** (per profile): pause unattended sessions on a profile above its 5-hour /
  weekly thresholds; resume when usage drops; a fetch failure never pauses. Interactive
  sessions on a capped profile are shown `limited`, never paused.
- **Credential lapse**: adapter `credentials_ok()` false → don't start; running workers get a
  nudge when fresh credentials land (tdgrind's `.nudged` marker).
- **Stall**: `working` with no output past `stall_after` → flag `stalled?`, nudge once, then
  wrap up.
- **Exit reap**: a worker whose tool exited sits on a sleep; reap it and keep the run log.
- **Worktree reap**: run `reap_worktrees.sh` (or its generalized form) between lifecycles.
- **Stranded-work flag**: any session going `idle`/`exited` with a dirty tree or unpushed
  commits is flagged in the herd — the stranded-work audit, continuous.
- **PAUSE** flag and `on`/`off`/`off --now` semantics kept as agent RPCs.

## 7. Phases

1. **PoC, kmaster, Claude Code only.** Host agent + Claude Code adapter (hooks, transcript
   locator, usage, creds) + herd page with the `/events` websocket + focus page with the pty
   bridge + new-session flow + VS Code link. Desktop only; no tab filters.
   Includes the `shell` adapter, the Shell button, and the hook-channel permission answer.
   Success test: every session Paul has open on kmaster shows the right state within 5 s of a
   change, and a permission prompt can be answered from the browser.
2. **Second host.** `hosts.yml`, ssh transport, agent install script, the VPS added and a
   session started there from the UI. (Confirmed as the plan 2026-09-10: herdr does not replace
   this step — [ADR](decisions/2026-09-10-herdr-spike.md).) Laptop closed for an hour; session still there.
   The phone's route in (WireGuard client, or the Cloudflare tunnel) and the phone layout (Herd
   + narrow Focus) land here.
   Attachment upload over ssh (drag and drop, picker, paste) lands here, since the copy path is
   the same plumbing.
3. **tdgrind migration.** Port tdgrind's supervisor into policies (§6) driven by
   samscrape's `.agentorc.yml`; run both side by side for one window with tdgrind's cron
   disabled and the agent's policies enabled; compare `tdgrind runs` reports against
   agentorc run logs; then delete `tdgrind.sh` from samscrape (ledger a TD there for the
   swap and the cron line in `infra/kmaster/crontab`).
   **Capabilities, report channels and presets** (§4.8) land at the start of this phase,
   because the migration is the first time several workers run at once and the Herd has to say
   what each is doing and keep them off each other: the caller check and the `orchestrate`
   grant first, then `progress` / `findings` with `ao progress` / `ao finding`, the derived
   source on the tick, the card's report line and the Focus Reports panel, and last the
   presets — with an orchestrator run as a session for a few evenings before its mechanical
   rules become policies here.
4. **Commands + board.** `.agentorc.yml` buttons (cmdorc where it fits), command-kind sessions
   and the Commands tab, the Due strip on the Herd and the Attention tab with Snooze/Done
   write-back, stranded-work flags.
5. **Second adapter.** Gemini CLI (hook-fed if the OSC 9 / hooks story verifies) or a scraped
   plain-shell adapter, whichever proves the contract better. Publish to PyPI, write the
   adapter-author guide. The `ao --skill` text (TD-019) was pulled forward and landed in
   phase 1 (2026-09-10); what remains here is offering to install it from the New session flow.

## 8. Lessons carried in (dev-cadence + tdgrind)

- **One anchor per checkout** — enforced at creation, not warned about later.
- **Ledger before idle** — the stranded-work flag makes the audit continuous.
- **One writer per shared resource** — only the host agent touches tmux.
- **State from the tool, not from the screen** — scraped state is labelled, never silent.
- **A record that exists only in scrollback is not a record** — pipe-pane from the first byte.
- **A reboot must not need a human** — systemd + linger, not a cron that notices.
- **Verify a real completion, not a catalog** — `credentials_ok()` and `usage()` are live
  calls, and the UI shows when they last succeeded.

## 9. Invariants

1. Only the host agent creates, kills, or sends keys to an `ao-*` tmux session.
2. A directory has at most one agent session (`kind: interactive`, adapter other than `shell`;
   main checkout, worktree, or plain directory). Shells and command runs are exempt.
3. Every session has a run log from its first byte.
4. A state shown as `hook` came from a hook; `scraped` is visible in the UI.
5. Interactive sessions are never paused, killed, or nudged by a policy.
6. The core never types a menu choice into a pane; permissions are answered through the hook,
   everything else in the terminal.
7. The agent's edits to a repo's board file are always committed, never left in the tree.
8. A session's process is launched as the adapter's argv, never through the person's
   interactive shell (§4.1); tmux, not the agent, holds the process.
9. Nothing keys on a session's role: policies key on `unattended` and the schedule, acting
   RPCs key on grants, displays key on the report channels (§4.8). A preset sets defaults at
   start and is a badge afterwards.
10. A report entry the session declared is never overwritten by one the agent derived; a
    derived entry is shown as such, like a scraped state.
11. A session acts on another session only through the agent and only with the `orchestrate`
    grant on its record; reads are never gated, and a person at a terminal or the UI is not a
    session.
12. Within a scope (repo, or directory), a name identifies at most one session record: a live
    holder refuses a second, an exited or closed holder is superseded by it (§4.1). Suffixes
    exist only for tmux-level accidents and are then shown, never hidden.

## 10. Open questions

- [x] Reachability (2026-09-06): "Tailscale only" generalised to **never a bare public port** —
      a private network (the existing WireGuard, or Tailscale) or an authenticated tunnel
      (Cloudflare Tunnel + Access, already in use for samscrape); no VPS needed for that. For
      non-technical users and a hosted service the answer is an agent-initiated **relay**
      transport (§4.5b): the host dials out, the service does login and proxies. Kept
      compatible now, scheduled later.
- [x] Where does the UI process run → **wherever `agentorc[ui]` is installed; nothing in the
      design assumes a particular host** (2026-09-05). The UI host is configured, not fixed:
      `hosts.yml` lives on it, and it may or may not also be a session host. For Paul it is
      kmaster (no VPS exists yet; kmaster already lingers and holds every session); another
      person points it at whatever server they have. The VPS, if one appears, is a session
      host in phase 2. A hosted service is the `relay` transport (§4.5b), after phase 5.
- [x] Password vs Tailscale-only for the UI in phase 1 → Tailscale only (2026-09-04); superseded
      2026-09-06 by the reachability decision above (any private network or authenticated tunnel).
- [x] Herd view: table vs card grid → card grid (2026-09-04), with urgent-first/pinned sort modes
      (the sort was called "attention" until it collided with the Attention tab).
- [x] Pinned layout → **per browser, `localStorage`** (2026-09-05), keyed by session id so a
      resumed session keeps its slot and a closed one drops out. Same store as the sort mode
      and the dark-mode toggle. Moves to the UI host only if a second person or a second
      browser ever makes it hurt.
- [x] "Done when" → renamed **Ready to close** (2026-09-04): Focus side panel with a Close
      button; a card shows "ready to close ✓" or the failing items. `done` state → `closed`,
      reached only by the person's Close.
- [x] Name → `sessionherd` (2026-09-04 morning) → **`agentorc`** (2026-09-04 evening), to sit
      beside cmdorc. Free on PyPI; one empty GitHub repo of that name, no dashboards among the
      neighbours. CLI alias `ao`.
- [x] ttyd vs a Python pty bridge → **pty bridge in the UI process** (2026-09-05), reversing
      the earlier lean. The deciding fact: the UI reaches hosts over ssh anyway, so the terminal
      is a pty around `ssh -tt host tmux attach -t <name>` opened by the UI process and bridged to
      xterm.js with `asyncio` + `os.openpty` (about 150 lines). ttyd would need a daemon per
      host, a port forward per host, and a websocket proxy on top — three moving parts to save
      those lines. The host agent stays the only per-host process. Resize is `TIOCSWINSZ` on the
      local pty; ssh carries it to tmux. ttyd remains the fallback if the bridge proves flaky
      on slow links.
- [x] `.agentorc.yml` vs. a section in dev-cadence's per-repo config → its own file
      (2026-09-04). dev-cadence stays the cadence system; agentorc reads its registry and, via
      the host agent only, edits and commits board items (Snooze / Done).
- [x] Repo layout → **one repo, two packages** (2026-09-04): `sessionorc` (below the adapter
      contract: tmux, ssh, hosts, the pty bridge, run logs, scraped running/exited, the `shell` adapter)
      and `agentorc` (above it: hook adapters, profiles, usage gates, unattended policies, Ready
      to close, the board). `sessionorc` imports nothing from `agentorc`. Split into two repos
      only when a second consumer appears — cmdorc wanting tmux + multi-host + logs is the
      trigger. **Packaging (2026-09-05): one distribution, `agentorc`, with an extra.** The base
      install is what every host needs — the host agent, the hook scripts, the CLI (`agentorc`,
      `ao`) — with light dependencies (`pyyaml`, `typer`). `pip install agentorc[ui]` adds
      FastAPI, uvicorn, websockets for the one host that runs the UI. One version number crosses
      the RPC boundary, so agent and UI never skew; the agent install script on a new host is
      `pipx install agentorc`. `sessionorc` and `agentorc` are still two import packages inside
      that one distribution. `requires-python >= 3.12` (kmaster runs 3.13).
- [x] Attention sort vs Attention tab (2026-09-04): sort renamed Urgent first; overdue/due-today
      board items on a Herd strip with Snooze/Done; the tab keeps the full board and loses its
      sessions column.
- [x] Board write-back (2026-09-04): both Snooze and Done, by the host agent, committed with a
      fixed message naming the session.
- [x] Repo-less sessions (2026-09-04): allowed; anchor on the directory, one agent session per
      directory, shells and command runs exempt; worktrees are a repo feature.
- [x] Shell vs agent (2026-09-04): a shell is an adapter, not a separate concept. Ad-hoc shells
      are Herd cards (Shell button, Open shell here); predefined command runs are `kind:
      command` and live on the Commands tab, hidden from the Herd by default.
- [x] `unreachable` (2026-09-04): host-level chip + banner, greyed cards keep last state; sorts
      with idle on a volatile host, after stalled? otherwise.
- [x] Answer buttons under the Focus terminal (2026-09-04): removed — the terminal owns menus
      and questions; Allow/Deny on cards and phone go through the `PermissionRequest` hook
      decision; questions get Focus only. Composer + Attach stay.
- [x] Session identity (2026-09-04): name + adapter id from birth; hand-started sessions show
      the id until adopted.
- [x] Existing-worktree picker (2026-09-04): only exited/closed worktrees offered; in-use ones
      greyed with "resume from the Herd".
- [ ] **Deny with a reason?** The hook decision can carry a message Claude reads. A one-line
      "why" next to Deny (optional field, card and Focus) would steer the next attempt better
      than a bare refusal. Cost: one input box; the phone gets it too.
- [ ] **"Allow for this session"?** The hook can also update the session's permission rules so
      the same tool does not ask again. Tempting for `git push` loops, but it is how a permission
      prompt stops being an alert; if added, it must be a third, smaller button and never the
      default.
- [x] **Build on, or beside, herdr?** (2026-09-09; decided 2026-09-10, (a) independent —
      [ADR](decisions/2026-09-10-herdr-spike.md).) The TD-014 spike measured herdr 0.9.0 as a
      `sessionorc` substrate: Claude Code's state under herdr is screen-scraped (its integration
      reports only a session id), the status event carries no kind and no text, a usage-limit
      screen reads as `idle`, an outside source cannot own a Claude pane's state, a server restart
      kills every pane process, and the socket API is per machine. So `sessionorc` stays on tmux,
      phase 2 builds the ssh transport as designed, and herdr is prior art (§3) with two things to
      borrow later: a screen-rule fallback for prompts no hook reports, and the worktree API shape.
- [x] **Are worker types (grinder, hunter, orchestrator) roles the agent keys on, or
  capabilities?** (2026-09-10) → **capabilities, with roles as presets (§4.8)**. Raised the
  day the Herd first showed four workers and could not say which TDs any of them held; the
  first draft made `role` a field with a progress record behind it, and the review asked
  whether the verbs (report what was found or finished, act on other sessions) were the
  real thing. They are: a grinder also files findings, a label keyed on intent misdescribes
  it, and "may act on other sessions" is a grant a person should see and revoke, not a
  promise in a brief. So: two ungated report channels any session writes, one gated grant the
  agent checks, presets that only fill the New session form. **Schedule is orthogonal to all
  of it** — time-shaped settings stay on the `unattended` side (§6, TD-026), so any session
  can be scheduled and no preset or grant exempts one (§9 invariant 9).
- [x] **Should a name identify one session?** (2026-09-10) → **yes, per scope (§4.1, §9
  invariant 12)**. Raised when the Herd showed `aotest` beside `aotest-2` and `tdgrind-ao-1`
  twice (one exited, one working): the `-2` suffix kept tmux happy while the record kept the
  original name, so the person saw two cards with one name and could not tell which one `ao focus` meant (it
  takes the full id today, which is the other half of the same problem). Decided: a live holder refuses a second session under the name (offer
  Switch to); an exited or closed holder is superseded, the way Resume already superseded its
  exited record (PR #17); a suffix survives only for a tmux id the agent has no record of, and
  is then shown. What this costs: two workers cannot share a name across worktrees any more —
  the right price, since the name is the handle every command takes.
- [ ] Phone answers for *questions*: the narrow Focus with a soft-key row (above) is the
      current answer; revisit after phase 2 if it is too fiddly to use one-handed.

## 11. References

- samscrape `scripts/tdgrind.sh` (supervisor being generalized), `scripts/list_sessions.py`,
  `scripts/reap_worktrees.sh`, `docs/cadence.md`
- eyecantell/dev-cadence — registry `~/.config/dev-cadence/repos.txt`, `/attention`;
  adoption and the two upstream PRs: [ADR 2026-09-06](decisions/2026-09-06-adopt-dev-cadence.md)
- eyecantell/textual-cmdorc — command specs for the buttons
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- herdr: https://herdr.dev, https://github.com/herdrdev/herdr (prior art, §3; decided §10, [ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md))
- ttyd: https://github.com/tsl0922/ttyd (fallback terminal transport, not used in phase 1)
