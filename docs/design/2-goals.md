## 2. Goals

1. **One view** of every session across hosts and repos with a trustworthy state:
   `working` / `needs-you` (waiting on a prompt, permission, or question) / `limited` (hit a
   usage or token cap, waiting on a reset) / `stalled?` / `idle` / `exited` / `closed` /
   `unreachable`, plus last-activity age and the pending question or reset time when there is one.
2. **Read and drive a session in place**: full conversation in an embedded terminal, type
   prompts, answer menus in the terminal, attach files from the laptop (drag and drop, a picker,
   or a pasted screenshot) — effortless, because it is how briefs and specs reach a session.
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
10. **Phone triage**: the Org view works on a phone over a private network or an authenticated
    tunnel (§4.5) — state, pending question, one-tap answers — so a blocked session can be
    unblocked from anywhere. The embedded terminal is a desktop feature.
11. **Ready to close, decided by the person**: a per-repo checklist (PR merged, branch pushed,
    tree clean, no subagents or background tasks running, ledger/attention board updated) says
    when a session is *ready* to close; only the person closes it (**Close** kills the session,
    reaps the worktree, and moves the card to `closed`). An exit that fails the checklist is
    shown as `exited` with the failing items. The tool never declares work done.
12. **Dark mode**: CSS tokens, `prefers-color-scheme` default plus a manual toggle. The terminal
    panes are dark regardless, so light chrome is the jarring case at night. The Focus pane
    carries VS Code's Dark Modern terminal palette (the sixteen ANSI colours, foreground, cursor
    and selection) as literals, not tokens, because the pane must not follow the page; the same
    output is the same colour in Focus as in the editor's terminal beside it. Its face
    is bundled (JetBrains Mono, OFL) with ligatures off, because it is a pane you type into — the
    person may set the face and size (§5 `person.terminal`, the Settings page), ligatures off and
    `monospace` the fallback whatever they choose; it
    draws through xterm.js's WebGL renderer where the browser has WebGL, the DOM renderer
    otherwise.
13. **Local and volatile hosts**: the person's own laptop is a host too (transport `local`, no
    ssh). A host marked `volatile: true` sleeps with the lid; its sessions show `unreachable`
    (not `stalled?`) when the host agent stops answering, its VS Code links use the local
    `vscode://file/<path>` form, and unattended policies refuse to start workers there unless
    overridden.
14. **A session is a tmux session, with or without a repo, with or without an agent.** A plain
    shell on `vpnmaster` or `host1` (proxmox) is a first-class card: it has a directory, a run
    log, a state, and Focus, just no hooks and no worktrees. A repo is optional; an adapter is
    just what decides where state comes from.

Non-goals (for now): multi-user access control, a kanban/task-board model of work (see §3 prior
art), replacing Claude Code's own `/resume`, mobile-first UI. A hosted service is **not** a
non-goal: it is the `relay` transport in §4.5b, kept compatible from phase 1 and scheduled after
phase 5.

