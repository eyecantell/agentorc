# agentorc

*Was `sessionherd` for the first day of design; renamed 2026-09-04 to sit beside
[cmdorc](https://github.com/eyecantell/textual-cmdorc).*

A self-hosted web dashboard that orchestrates interactive AI coding-agent sessions (Claude Code first;
Gemini CLI, Codex CLI, and on-prem harnesses via adapters) — and plain shells — running in tmux across one or more
hosts. One view of every session and whether it is **working**, **needs you**, **idle**, or
**exited**; read and drive any of them in an embedded terminal; start, resume, and close them;
see git status per checkout; press configured command buttons; jump to VS Code; and supervise
unattended workers with run windows and usage caps. Sessions live on the host, so closing the
laptop changes nothing.

**Status: design only.** Read [`docs/design.md`](docs/design.md). Nothing is built yet.

Grew out of [samscrape](https://github.com/eyecantell/samscrape)'s `tdgrind` worker supervisor
and the [dev-cadence](https://github.com/eyecantell/dev-cadence) working-cadence system, whose
repo registry it depends on.

## Start here (handoff, updated 2026-09-06 evening)

**Phase 1 is built** (PRs #1–#4, all Sonnet-reviewed): host agent, Claude Code adapter, Herd
and Focus pages, New session, CLI. Run it per "Run it" below. What to do next, in order:

1. **Use it** against a real repo and note what the flows get wrong (board item, due 2026-09-13).
   Until Tailscale is on kmaster, reach the UI with `ssh -L 8765:127.0.0.1:8765 kmaster`.
2. **Decisions waiting on Paul**: see [`docs/user_attention.md`](docs/user_attention.md)
   (dev-cadence PRs #82/#83, GitHub merge settings, the two permission-dialog questions).
3. **Deferred work** is in [`docs/technical_debt.md`](docs/technical_debt.md) (TD-001 `limited`
   state, TD-002 attachments, TD-003 phone, TD-004 `hosts.yml`, TD-010 adopting hand-started
   sessions), then **phase 2** of [`docs/design.md`](docs/design.md) §7: second host, ssh
   transport, agent install script.
4. Known gap in the phase 1 success test: only sessions agentorc launched (or hand-started
   `ao-*` tmux sessions) appear in the Herd. A Claude Code session opened in a VS Code terminal
   is not a tmux session and is invisible until Adopt (TD-010) exists.

Mockups: https://claude.ai/code/artifact/0e14af3a-5e5a-4d9c-88b2-74205c394c04 (sources and
regeneration notes in [`docs/mockups/`](docs/mockups/)). The CSS block at the top of
`docs/mockups/gen.py` is the seed for the app's stylesheet tokens; the dark artboard is a token
swap of the light one, which is how the real dark mode should work too.

## Layout

One repo, one distribution (`agentorc`, with an `[ui]` extra for the host that serves the web
UI), two import packages: `sessionorc` (tmux, hosts, ssh, the pty terminal bridge, run logs, the
shell adapter) and `agentorc` (hook adapters, profiles, policies, Ready to close, the board) on
top of it. `sessionorc` never imports `agentorc`; it becomes its own repo only when a second
consumer appears (docs/design.md §10).

## Requirements

Python 3.12+, tmux 3.2+ (`new-session -e`, bracketed `paste-buffer -p`; kmaster has 3.5a), and
on each session host a user systemd session with `loginctl enable-linger` so the agent and its
tmux server survive logout and reboot.

## Run it (phase 1, one host)

```bash
pdm install -G ui                      # or: pipx install 'agentorc[ui]' once published
pdm run agentorc-agent serve           # the host agent: tmux, sessions, hooks, run logs
pdm run ao ui                          # the web UI on http://127.0.0.1:8765 (never the LAN; use ssh -L or Tailscale)
pdm run ao new td-302 -d ~/samscrape   # or the New session form; `ao shell` for a plain shell
```

State lives under `~/.agentorc/` (`AGENTORC_HOME` overrides it — every session the agent creates
carries it, so hooks inside find the right agent). Profiles: `~/.agentorc/profiles.yml`
(design §4.2a). `AGENTORC_HOST_NAME` sets the name the UI shows; `AGENTORC_VSCODE_HOST` is the
ssh alias VS Code links use (must be in your own `~/.ssh/config`); `AGENTORC_LOCAL_HOST=1` makes
those links `vscode://file/…` instead.

## CLI

The package installs `agentorc` and an `ao` alias (`ao status`, `ao new`, `ao shell`, ...).

## License

MIT — see [LICENSE](LICENSE).
