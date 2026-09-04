# sessionherd

*Say it "s-herd" or "essherd" — the package and command stay `sessionherd`.*

A self-hosted web dashboard that herds interactive AI coding-agent sessions (Claude Code first;
Gemini CLI, Codex CLI, and on-prem harnesses via adapters) running in tmux across one or more
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

Design is settled enough to build. In order:

1. **Evaluate, then install [dev-cadence](https://github.com/eyecantell/dev-cadence)** using the
   evaluate-first prompt in its README (both directions; file upstream findings as PRs there).
2. **Write `CLAUDE.md`** for this repo: Python 3.12, `pdm`, ruff at 120, the one-anchor rule,
   and the design doc as the source of truth.
3. **Phase 1 of [`docs/design.md`](docs/design.md) §7**: host agent + Claude Code adapter +
   Herd and Focus pages, kmaster only. The success test is written there.
4. Open questions still listed in §10 are implementation-level (ttyd vs a Python pty bridge,
   one package or two, pinned-layout storage) — decide them in the build session and mark them.

Mockups: https://claude.ai/code/artifact/0e14af3a-5e5a-4d9c-88b2-74205c394c04 (sources and
regeneration notes in [`docs/mockups/`](docs/mockups/)). The CSS block at the top of
`docs/mockups/gen.py` is the seed for the app's stylesheet tokens; the dark artboard is a token
swap of the light one, which is how the real dark mode should work too.

## CLI

The package installs `sessionherd` and a `herd` alias (`herd status`, `herd new`, ...).
Laravel Herd also installs a `herd` command on macOS/Windows; if that is you, drop the alias
(see docs/design.md §4.6) and use `sessionherd`.

## License

MIT — see [LICENSE](LICENSE).
