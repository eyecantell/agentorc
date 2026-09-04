# sessionherd

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

## CLI

The package installs `sessionherd` and a `herd` alias (`herd status`, `herd new`, ...).
Laravel Herd also installs a `herd` command on macOS/Windows; if that is you, drop the alias
(see docs/design.md §4.6) and use `sessionherd`.

## License

MIT — see [LICENSE](LICENSE).
