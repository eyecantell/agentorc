# sessionherd — design

Status: **draft for review** (2026-09-04). Nothing is built. This document is the requirements and
architecture agreed in the 2026-09-04 design session; each open question at the end is a decision
that changes what gets built.

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
   usage or token cap, waiting on a reset) / `stalled?` / `idle` / `exited` / `done`, plus
   last-activity age and the pending question or reset time when there is one.
2. **Read and drive a session in place**: full conversation in an embedded terminal, type
   prompts, answer menus, attach files.
3. **Lifecycle from the UI**: start a new session (fresh or resumed), close one out, kill one.
4. **Repo awareness**: git status per checkout/worktree, one-anchor-per-checkout enforcement,
   dirty-or-unpushed flags on idle/exited sessions.
5. **Configurable buttons**: per-repo commands (cmdorc-style specs) that run as sessions in the
   same list.
6. **Jump out**: "open in VS Code" for the session's directory on its host.
7. **Survive the laptop closing**: sessions live on the host (kmaster today, a VPS next), never on
   the client.
8. **Unattended supervision**: run windows, usage caps, wrap-up-then-kill, credential-lapse
   detection — `tdgrind` generalized per repo.
9. **Framework, not a Claude tool**: the core knows sessions, hosts, repos, and adapters. Claude
   Code is the first adapter; Gemini CLI, Codex CLI, and on-prem harnesses are later adapters.
   Share with other devs once it proves useful.

10. **Phone triage**: the Herd view works on a phone over Tailscale — state, pending question, one-tap
    answers — so a blocked session can be unblocked from anywhere. The embedded terminal is a
    desktop feature.
11. **Done, not just exited**: a per-repo checklist (PR merged, branch pushed, tree clean, no
    subagents or background tasks running, ledger/attention board updated) decides whether a
    finished session is `done`; an exit that fails it is shown as `exited, not done` with the
    failing items.

Non-goals (for now): multi-user access control, a kanban/task-board model of work (see §11 prior
art), replacing Claude Code's own `/resume`, mobile-first UI.

## 3. Prior art (surveyed 2026-09-04)

No surveyed tool does multi-host + hook-fed state + VS Code links + usage-cap supervision.

| Tool | Shape | Borrow | Gap vs. goals |
|---|---|---|---|
| ttyd (MIT) | websocket + xterm.js around any command | **the terminal transport** — embed per host, never write our own pty bridge | terminal only |
| ccmanager, claude-squad | TUI session managers, tmux + worktrees, many agent CLIs | ccmanager's launch specs as adapter reference | terminal-only, scraped state, single host |
| Vibe Kanban (Apache-2.0) | web kanban, per-task terminal, 10+ agents | UI ideas for diff review | task-board model, single machine, own execution tracking |
| agent-dashboard (bjornjee) | tmux orchestrator + PWA for approvals | same idea at PoC scale | maintenance unverified |
| Anthropic Remote Control / cloud sessions | single-session sync, Claude only | — | not a fleet view, not self-hosted |

## 4. Architecture

```
laptop browser ──https──▶ sessionherd UI (one process, runs on a host you choose)
                              │  ssh transport (no public ports on hosts beyond ssh)
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        host agent        host agent       host agent
        (kmaster)         (vps)            (...)
          │  ├─ tmux server (systemd user unit, linger on)
          │  ├─ ttyd (localhost only; UI proxies the websocket)
          │  ├─ state dir  ~/.sessionherd/sessions/<id>.json  ◀── adapter hooks write here
          │  ├─ run logs   ~/.sessionherd/runs/<session>.log  ◀── tmux pipe-pane, continuous
          │  └─ policies   (run window, usage gate, reap worktrees, anchor rule)
          └─ repos from ~/.config/dev-cadence/repos.txt (+ ~/.sessionherd/hosts.yml)
```

### 4.1 Session substrate: tmux, one session per conversation

- Session name `sh-<repo>-<name>` (prefix lets the agent enumerate its own sessions).
- Created **only** by the host agent (one writer per shared resource — see §9). The UI, the CLI,
  and the cron reconcile all call the agent.
- tmux server runs under a user systemd unit with `loginctl enable-linger`, so a reboot restarts
  it rather than a cron tick noticing later.
- `history-limit` raised at creation; `pipe-pane` streams output to
  `~/.sessionherd/runs/<session>-<created>.log` continuously (replaces tdgrind's per-tick
  snapshot; a reboot loses nothing that reached the pipe).
- Directory is the repo checkout or a worktree; the agent records which and refuses a second
  session on a main checkout (anchor rule, §9).

### 4.2 State feed: hooks first, scraping as a labelled fallback

The three states the person cares about are already emitted by the tools that have hooks. An
adapter installs a small hook script that writes
`~/.sessionherd/sessions/<session-id>.json`:

```json
{"session_id": "...", "tool": "claude-code", "tmux": "sh-samscrape-tdgrind-1",
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
| `exited` + the repo's `done_when` checklist passes | `done` |

`limited` is distinct from `needs-you` because nothing the person does unblocks it, and from
`stalled?` because it is explained. The card shows the reset time and offers **Switch
profile** (below) or **Wait**. For Claude Code the reset time comes from the usage endpoint
tdgrind already polls; the pane's limit message is the scraped fallback.

`done_when` is evaluated by the host agent when a session goes `idle` or `exited`:
`git status --porcelain` empty, branch pushed, `gh pr view --json state` merged (when the branch
has a PR), no live subagents (Claude Code: `SubagentStop` balances `SubagentStart`; other
adapters: nothing running under the pane), and the ledger/attention board touched since the
session started (dev-cadence repos). Each item is a named check in `.sessionherd.yml` so other
repos can pick their own subset. The Focus view shows the checklist live; the Herd view shows
failing items on an `exited` row. This is the stranded-work audit with teeth.

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
equivalents. Profiles are declared once per host in `~/.sessionherd/profiles.yml`.

### 4.3 Adapter contract

One package per tool under `sessionherd/adapters/<tool>/`. Core never imports tool-specific
names outside the adapter.

```python
class Adapter(Protocol):
    name: str                         # "claude-code"
    def launch_cmd(self, *, profile: Profile, resume: str | None, prompt_file: Path | None, unattended: bool) -> list[str]
    def install_hooks(self, repo: Path) -> None      # writes/merges the tool's hook config
    def state_source(self) -> Literal["hook", "scraped"]
    def classify_pane(self, tail: str) -> State | None   # only for scraped adapters
    def transcript_path(self, session_id: str, cwd: Path) -> Path | None
    def quirks(self) -> Quirks                      # first-run dialogs, settings pre-seed
    def usage(self, profile: Profile) -> Usage | None     # quota + reset time, per account
    def credentials_ok(self, profile: Profile) -> bool | None
```

Prompt injection and menu answering are **core**, not adapter: `tmux send-keys` (`C-u`, literal
text, `C-m`) and arrow/Enter for menus. The embedded terminal makes this the same as typing.

Adapter status at design time (verify before building each):

| Tool | State signal | Adapter type |
|---|---|---|
| Claude Code | full hook set incl. `Notification`, `Stop`, `PermissionRequest`; transcripts in `~/.claude/projects/`; live registry `~/.claude/sessions/`; usage via the OAuth usage endpoint (tdgrind `usage`) | hook-fed — **phase 1** |
| Gemini CLI | hooks since v0.26 + OSC 9 "action required / complete" notifications | hook-fed (verify) |
| Codex CLI | experimental hooks (Pre/PostToolUse); a "waiting" event unconfirmed | scraped until verified |
| Aider / plain shell / cmdorc command | none | scraped: running vs exited by pane + exit marker |

### 4.4 Host agent

Python, one process per host, started by the same systemd user unit. Responsibilities:

- Enumerate sessions (tmux + state dir), merge, serve JSON over a local Unix socket.
- Create / kill / nudge / resume sessions (the only writer).
- Per-repo `git status --porcelain=v2 --branch` for every checkout and worktree the registry
  lists, cached with a short TTL.
- Policies (§6), run on a tick from the same process — no cron, no fd-9 lock inheritance.
- Start ttyd bound to localhost for the UI to proxy.
- Attachment drop: accept an uploaded file into `~/.sessionherd/attachments/<session>/`, return
  the path for the UI to insert into the prompt (Claude Code takes file paths in prompts).

### 4.5 UI

Single web process (FastAPI + websockets; plain server-rendered pages with a small amount of JS
and xterm.js — no SPA build step, so other devs can run it with one command). Talks to each host
over ssh: JSON RPC over `ssh host sessionherd-agent rpc` and the ttyd websocket over an ssh-
forwarded port. Adding a host is `sessionherd host add vps user@vps` + installing the agent there.

Screens:

1. **Herd** (home): a **card grid** (decision 2026-09-04, over a table — keeps each session's
   facts grouped and shows a live tail). Each card: host/repo, name, age, state pill, profile
   line, where (checkout or worktree → branch), dirty/unpushed flag, then either the pending
   question with Allow / Deny / Answer, the reset time with Switch profile / Wait, or the
   last output lines; buttons Focus / VS Code / git / more. A scraped state shows as a dashed
   pill outline; there is no separate source column (it follows from the tool). Two sort
   modes, remembered per browser: **attention** (`needs-you` → `limited` → `stalled?` →
   `working` → `idle` → `exited` → `done`) and **pinned** (cards stay where the person
   dragged them, needs-you cards are highlighted and counted in the top bar — for people who
   run a standard set of sessions). A second tab lists **resumable** inactive sessions from
   each adapter's transcript locator (Claude: `list_sessions.py`-style index) with a Resume
   button.
2. **Focus**: embedded terminal (full conversation, keyboard passes through, so permissions and
   menus are answered exactly as in VS Code), prompt box with attachment upload, git status side
   panel, run-log download, "open in VS Code" link
   (`vscode://vscode-remote/ssh-remote+<host>/<path>` — handled by the browser on the laptop,
   which is why this is a web UI and not a TUI).
3. **New session**: pick host → repo → adapter → checkout or new worktree (main refused if it
   already has a session) → fresh or resume → optional brief file → unattended toggle.
4. **Commands**: per-repo buttons from `.sessionherd.yml` (cmdorc command specs where cmdorc
   fits); each press starts a `sh-<repo>-cmd-<name>` session so it shows in the herd with
   running/exited state and a log.
5. **Attention**: the dev-cadence `/attention` report as a panel — "sessions waiting on me" and
   "board items due" are one question from the person's side.

Security: the UI can type into a shell as you, so it is root-equivalent. **Decision
2026-09-04: Tailscale only** — the UI binds to the tailnet address, the phone joins the tailnet,
no public port and no password to manage. Host ssh keys are the only credential the UI holds.

Phone layout: the Herd view collapses to cards sorted `needs-you` first with Allow / Deny on a
pending permission and a Focus button; the terminal, git panel, and New-session form stay
desktop-width. Mockups (2026-09-04): https://claude.ai/code/artifact/0e14af3a-5e5a-4d9c-88b2-74205c394c04

### 4.6 CLI

Package and canonical command: `sessionherd`. The package also registers `herd` as an alias
(`herd status`, `herd new`, `herd focus <name>`, `herd off --now`) because that is what gets
typed day to day. Laravel Herd installs a `herd` command on macOS/Windows; the README documents
the collision and the alias is a separate console-script entry so an affected dev can drop it
without losing anything. The CLI is a thin client of the host agent RPC — it never touches tmux
itself (§9 invariant 1).

## 5. Configuration

- Hosts: `~/.sessionherd/hosts.yml` on the UI host (`name`, `ssh`, `repos_registry` path).
- Repos: the dev-cadence registry (`~/.config/dev-cadence/repos.txt`) on each host — not
  duplicated. A repo without dev-cadence can still be listed there.
- Per repo: `.sessionherd.yml` (checked in):

```yaml
adapter: claude-code
worktrees: .claude/worktrees         # where new-session worktrees go
anchor: main-checkout-single         # refuse a 2nd session on the main checkout
unattended:
  workers: 3
  brief: ~/.tdgrind/{name}-prompt.md
  window: {weekday: "20:00-06:00", weekend: all}
  usage_gate: {five_hour_pct: 70, weekly_pct: 70}
  wrapup_minutes: 15
  creds_min_hours: 0.25
done_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands:
  - name: test        ; run: pdm run test
  - name: cluster     ; run: ./scripts/cluster-status.sh
  - name: attention   ; run: python scripts/nudge_user_attention.py --report
```

## 6. Policies (the tdgrind supervisor, generalized)

Each runs on the host agent's tick, per repo, only for sessions tagged `unattended`
(interactive sessions are exempt from gates):

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
   locator, usage, creds) + herd page + focus page with ttyd + new-session flow + VS Code link.
   Success test: every session Paul has open on kmaster shows the right state within 5 s of a
   change, and a permission prompt can be answered from the browser.
2. **Second host.** `hosts.yml`, ssh transport, agent install script, the VPS added and a
   session started there from the UI. Laptop closed for an hour; session still there.
3. **tdgrind migration.** Port tdgrind's supervisor into policies (§6) driven by
   samscrape's `.sessionherd.yml`; run both side by side for one window with tdgrind's cron
   disabled and the agent's policies enabled; compare `tdgrind runs` reports against
   sessionherd run logs; then delete `tdgrind.sh` from samscrape (ledger a TD there for the
   swap and the cron line in `infra/kmaster/crontab`).
4. **Commands + attention panel.** `.sessionherd.yml` buttons (cmdorc where it fits), the
   dev-cadence attention report as a panel, stranded-work flags.
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

1. Only the host agent creates, kills, or sends keys to a `sh-*` tmux session.
2. A main checkout has at most one session; worktrees have at most one each.
3. Every session has a run log from its first byte.
4. A state shown as `hook` came from a hook; `scraped` is visible in the UI.
5. Interactive sessions are never paused, killed, or nudged by a policy.

## 10. Open questions

- [ ] Where does the UI process run: kmaster (simplest, LAN) or the VPS (reachable anywhere via
      Tailscale)? Affects nothing in the design, only the install order.
- [x] Password vs Tailscale-only for the UI in phase 1 → Tailscale only (2026-09-04).
- [x] Herd view: table vs card grid → card grid (2026-09-04), with attention/pinned sort modes.
- [ ] Pinned layout: stored per browser (localStorage) or per person on the UI host? Per host
      survives a new browser; per browser needs no identity. Recommendation: per browser first.
- [ ] "Done when" in the Focus side panel only, or also a column in the Herd table.
- [ ] Name: `sessionherd` (current) vs `essherd`. Rename is cheap until code exists.
- [ ] Does the PoC embed ttyd or use xterm.js + a Python pty bridge in the UI process? ttyd is
      less code; a Python bridge is one fewer binary for other devs to install. Recommendation:
      ttyd for the PoC, revisit at phase 5.
- [ ] `.sessionherd.yml` vs. a section in dev-cadence's per-repo config. Recommendation: its
      own file; dev-cadence stays the cadence system, sessionherd depends on its registry only.
- [ ] Repo layout for the eventual PyPI package: `sessionherd` (UI + CLI) and
      `sessionherd-agent` (host side, minimal deps) as one package with extras, or two.

## 11. References

- samscrape `scripts/tdgrind.sh` (supervisor being generalized), `scripts/list_sessions.py`,
  `scripts/reap_worktrees.sh`, `docs/cadence.md`
- eyecantell/dev-cadence — registry `~/.config/dev-cadence/repos.txt`, `/attention`
- eyecantell/textual-cmdorc — command specs for the buttons
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- ttyd: https://github.com/tsl0922/ttyd
