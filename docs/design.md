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

## 3. Prior art (surveyed 2026-09-04)

No surveyed tool does multi-host + hook-fed state + VS Code links + usage-cap supervision.

| Tool | Shape | Borrow | Gap vs. goals |
|---|---|---|---|
| ttyd (MIT) | websocket + xterm.js around any command | the terminal-transport shape (xterm.js over a websocket around a pty); superseded 2026-09-05 by a bridge inside the UI process, since the pty would wrap `ssh` anyway (§10) | terminal only; a second daemon per host |
| ccmanager, claude-squad | TUI session managers, tmux + worktrees, many agent CLIs | ccmanager's launch specs as adapter reference | terminal-only, scraped state, single host |
| Vibe Kanban (Apache-2.0) | web kanban, per-task terminal, 10+ agents | UI ideas for diff review | task-board model, single machine, own execution tracking |
| agent-dashboard (bjornjee) | tmux orchestrator + PWA for approvals | same idea at PoC scale | maintenance unverified |
| Anthropic Remote Control / cloud sessions | single-session sync, Claude only | — | not a fleet view, not self-hosted |

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
  Both parts are slugified to `[a-z0-9-]` (tmux treats `:`, `.` and whitespace specially) and a
  collision within the prefix gets a `-2`, `-3` suffix; the person's original name stays in the
  record. The agent handles tmux's "duplicate session" error explicitly rather than trusting
  the check.
- Every session record carries: `name` (what the person called it), `kind`
  (`interactive` | `command`), `adapter` (`claude-code`, `shell`, …), `profile` (empty for
  `shell`), `dir`, `repo` (optional), `worktree` (optional), and `adapter_id` once known (Claude
  Code's session uuid — read from the hook payload; it is what Resumable and the transcript index
  key on). Resumable shows the name first and the id under it; a session started by hand
  outside agentorc shows only the id until it is **adopted** (attach to the tmux session, give it
  a name), which is also how hand-started sessions enter the Herd.
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
| `Notification` (permission / question / idle prompt), `PermissionRequest` | `needs-you` + pending text |
| `Stop` | `idle` |
| adapter `usage()` at cap, or the tool's own limit message | `limited` + reset time |
| `SessionEnd`, or tmux session gone | `exited` |
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

Adapters without a usable hook set get a **pane classifier** (regex over the last N lines) and
`"confidence": "scraped"`. The UI shows the badge so a guessed state is never mistaken for a
reported one. No adapter may write a scraped state with `confidence: hook`.

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
    def credentials_ok(self, profile: Profile) -> bool | None
```

Prompt injection is **core**, not adapter: the composer text goes in with `tmux load-buffer`
+ `paste-buffer -p` (bracketed paste, so a multi-line brief lands as one prompt instead of
submitting line by line) followed by `Enter` — no blind `C-u`, since what is painted in the
pane may not be a readline line. **Send is disabled** while a permission or question is pending (the pane
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
   non-volatile host → `working` → `idle` / `unreachable` on a volatile host → `exited` →
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
| Focus | **Kill** | confirms, then kills the tmux session; worktree kept; state `exited` |
| Focus composer | **Attach** / drop / paste | uploads to `~/.agentorc/attachments/<session>/`, inserts the path |
| Focus composer | **Send** | `send-keys` of the composer text |
| Focus side panel | **diff / log / PRs**, run-log link, **Close** | git views; download; Close as above |
| New session | **Unattended** switch | tags the session `unattended` (policies apply); disabled without an `unattended:` block, hidden for directory sessions |
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
agentorc's fleet view is where its rules (anchor, ready to close, stranded work, ledger before
idle) get exercised unattended first. Sequence: self-hosted for developers (now) → relay →
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
itself (§9 invariant 1).

## 5. Configuration

- Hosts: `~/.agentorc/hosts.yml` on the UI host (`name`, `transport: ssh|local`, `ssh`
  target, `volatile: true|false`, `repos_registry` path, `runs_keep_days`). The UI process itself may run on a
  laptop; only the session hosts need to stay awake.
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
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands:
  - name: test        ; run: pdm run test
  - name: cluster     ; run: ./scripts/cluster-status.sh
  - name: attention   ; run: python scripts/nudge_user_attention.py --report
```

A repo without the file gets defaults: `adapter: claude-code`, worktrees under
`.claude/worktrees`, `ready_when: [tree_clean, branch_pushed, no_subagents]`, no commands, no
unattended mode. A directory session (no repo) reduces to `ready_when: [no_subagents]`.

## 6. Policies (the tdgrind supervisor, generalized)

Each runs on the host agent's tick, per repo, only for sessions whose record says
`unattended: true` (set at start by the New session switch, or flipped later by the badge
toggle on the card or Focus header; interactive sessions are exempt from gates). Mode is a
field on the session record, never re-derived from the brief or the name, and a flip takes
effect on the next tick without restarting the session. The brief file is required only when
a *policy* starts a worker; a session flipped to unattended keeps whatever it was doing.

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
   session started there from the UI. Laptop closed for an hour; session still there.
   The phone's route in (WireGuard client, or the Cloudflare tunnel) and the phone layout (Herd
   + narrow Focus) land here.
   Attachment upload over ssh (drag and drop, picker, paste) lands here, since the copy path is
   the same plumbing.
3. **tdgrind migration.** Port tdgrind's supervisor into policies (§6) driven by
   samscrape's `.agentorc.yml`; run both side by side for one window with tdgrind's cron
   disabled and the agent's policies enabled; compare `tdgrind runs` reports against
   agentorc run logs; then delete `tdgrind.sh` from samscrape (ledger a TD there for the
   swap and the cron line in `infra/kmaster/crontab`).
4. **Commands + board.** `.agentorc.yml` buttons (cmdorc where it fits), command-kind sessions
   and the Commands tab, the Due strip on the Herd and the Attention tab with Snooze/Done
   write-back, stranded-work flags.
5. **Second adapter.** Gemini CLI (hook-fed if the OSC 9 / hooks story verifies) or a scraped
   plain-shell adapter, whichever proves the contract better. Publish to PyPI, write the
   adapter-author guide.

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
- [ ] Phone answers for *questions*: the narrow Focus with a soft-key row (above) is the
      current answer; revisit after phase 2 if it is too fiddly to use one-handed.

## 11. References

- samscrape `scripts/tdgrind.sh` (supervisor being generalized), `scripts/list_sessions.py`,
  `scripts/reap_worktrees.sh`, `docs/cadence.md`
- eyecantell/dev-cadence — registry `~/.config/dev-cadence/repos.txt`, `/attention`;
  adoption and the two upstream PRs: [ADR 2026-09-06](decisions/2026-09-06-adopt-dev-cadence.md)
- eyecantell/textual-cmdorc — command specs for the buttons
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- ttyd: https://github.com/tsl0922/ttyd (fallback terminal transport, not used in phase 1)
