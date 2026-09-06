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

## Start here (handoff from the 2026-09-04 design session)

Design is settled enough to build. Steps 1 and 2 landed 2026-09-06; the rest in order:

1. ~~Evaluate, then install dev-cadence~~ — done, both directions:
   [ADR 2026-09-06](docs/decisions/2026-09-06-adopt-dev-cadence.md); upstream PRs
   dev-cadence #82 (`--report --json`) and #83 (board-edit carve-out) await the maintainer.
2. ~~Write `CLAUDE.md`~~ — done; it is the map, `docs/design.md` is the source of truth.
3. **Phase 1 of [`docs/design.md`](docs/design.md) §7**: host agent + Claude Code adapter +
   Herd and Focus pages, kmaster only. The success test is written there. Build against a
   scratch directory and a throwaway profile first (CLAUDE.md says why).
4. §10 is fully decided as of 2026-09-05: UI on whichever host installs `agentorc[ui]`, pty
   bridge (no ttyd), one distribution with a `[ui]` extra, pinned layout in `localStorage`.
   Tailscale on the UI host is a phase 1 prerequisite; until then the UI binds to localhost and
   is reached over `ssh -L`. Two small permission-dialog questions remain open there.

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

## CLI

The package installs `agentorc` and an `ao` alias (`ao status`, `ao new`, `ao shell`, ...).

## License

MIT — see [LICENSE](LICENSE).
