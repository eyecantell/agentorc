---
name: ao
description: Rules for an agent driving agentorc's `ao` CLI from inside a session — states, the JSON-first commands, what mutates, what never to do.
---

# Driving `ao` from inside a session

You are inside an agentorc session when `AGENTORC_SESSION` is set (it is your own session id)
and `ao` is on `PATH`. The host agent owns tmux; `ao` is a thin client of it (design §4.7).
Print this text again with `ao --skill`.

## First, every time

1. `echo "$AGENTORC_SESSION"` — your own id. Never `send` to it, `kill` it, or `close` it.
2. `ao status --json` — every session on this host. Parse ids and states from the JSON, never
   from prose, a card, or a screen.
3. Put `--json` on every call: it prints only the RPC result, or `{"error": …}` with exit 1
   (exit 3: the host agent is down — stop, do not start one).

## States (design §4.2)

| state | means | you |
|---|---|---|
| `working` | a turn is running | wait; a `send` queues behind the turn |
| `idle` | at the composer, waiting for a prompt | send the next prompt |
| `needs-you` | `pending.kind` is `permission` or `question`, `pending.text` says what | permission: `ao allow <id> [reason]` / `ao deny <id> reason` — only for a session you were asked to supervise. question or menu: a human answers in the terminal; never type a choice |
| `limited` | usage cap; the record carries the reset time | wait for the reset; never retry into it |
| `stalled?` | working with no output past the adapter's stall window | `ao tail` / `ao explain`; report, do not nudge |
| `exited` / `closed` | the process ended / the person closed it | `ao new … --resume <adapter_id>` if it is yours to resume |
| `unreachable` | the host is not answering | wait |

`confidence: "scraped"` means the state was read from the screen, not reported by a hook:
advisory. `ao explain <id> --json` shows the screen, the rule that fired, and the evidence.

## Commands

Read-only: `ao status [-v]`, `ao tail <id> -n N`, `ao explain <id>`.
Mutating — each one is a decision, so check the state first:

- `ao new <name> -d <dir> [--worktree <name>] [--prompt …] [--unattended] [--resume <id>]` —
  one agent session per directory (§9 invariant 2); beside an existing session use `--worktree`.
- `ao send <id> [text] [--wait [--timeout S]]`, or `ao send <id> --wait < brief.md` for a
  multi-line prompt (bracketed paste: one prompt). Refused while a permission or question is
  pending. Every send confirms the text left the composer and errors `prompt-stuck` if it did
  not (TD-027). `--wait` returns the record once *this* prompt's turn has settled; errors
  `prompt-stalled` (nothing started), `timeout`, `removed`.
- `ao allow|deny <id> [reason]` — the pending permission, through the hook channel.
- `ao mode <id> unattended|interactive`; `ao kill <id>` (worktree kept); `ao close <id>`.
- Acting on a session other than your own (`send`, `keys`, `kill`, `close`, `mode`, `new`) needs
  the `orchestrate` grant on your record (`ao status --json` → `capabilities`); without it the
  agent answers "needs the orchestrate grant". You cannot grant yourself: a person does it with
  `ao grant <id> orchestrate` (design §4.8).
- `ao keys <id> Key…` — raw keys. Not for dialogs, menus, or another agent's composer.
- `ao focus <id>` attaches a terminal: for people, not for you.

## Verify every send (the TD-027 lesson)

A prompt that is typed is not a prompt that ran. Four unattended workers once sat all afternoon
behind what looked like an unsubmitted prompt. So: one prompt, one `ao send <id> --wait --json`,
then act on the result — `idle`: next step; `needs-you`: see the table; `prompt-stalled` or
`prompt-stuck`: `ao tail <id>`, understand, then decide. Never re-send on a guess: the text is
still in the composer, and a second paste appends to it (measured while fixing TD-027: five
pastes became one concatenated prompt). Without `--wait`, poll
`ao status --json` for the state to change before concluding anything.

## Never

- Never run `tmux` against an `ao-*` session yourself (§9 invariant 1): `ao` is the only writer.
- Never answer another session's question, menu, or trust dialog (§9 invariant 6). Permissions
  only through `ao allow`/`ao deny`, only when supervising that session is your job.
- Never `send`, `kill`, or `close` a session you did not start unless your brief names it; the
  session in a repo's main checkout is the person's anchor — leave it alone.
- Never nudge, pause, or kill an interactive session (§9 invariant 5), including "are you done?".
- Never `send` into `needs-you` (refused while a permission or question is pending), `limited`
  (nothing stops you, and the prompt fails or queues behind the cap), or `unreachable` (exit 3).
- Never edit `~/.claude/settings.json`, `~/.claude.json`, or anything under `~/.agentorc`; never
  start, stop, or restart `agentorc-agent` / `agentorc-ui`. The agent's state is not yours.
- Never leave a session you started without a record: its work pushed, its ledger touched, then
  `ao close` once it is `idle` and its `ready_when` checks pass.

## Shape of a worked loop

```sh
ID=$(ao new worker -d "$REPO" --worktree worker --json | jq -r .id)
ao send "$ID" --wait --timeout 1800 --json < brief.md | jq -r .state   # idle | needs-you | …
ao status --json | jq '.[] | select(.state == "needs-you") | {id, pending}'
ao close "$ID" --json
```
