# Tests

`pdm run test` runs everything; `pdm run test-fast` runs the `unit` tier only. CI
(`.github/workflows/ci.yml`) runs lint and the full suite on every push and pull request.

## Rules

1. **Never the user's tmux server or `~/.agentorc`.** Every fixture that touches tmux uses a
   private server (`-L ao-test-<uuid>`) and a temp `AGENTORC_HOME`. `agentorc-agent serve` is
   never spawned by a test: it builds `Tmux()` with no socket name, which is the real server.
   The subprocess agent is `tests/_agent_child.py`.
   **Two pytest runs at once must not touch each other** (TD-025): the stale-server sweep kills
   leaked `ao-test-*` servers, and it skips any whose owning process is still alive
   (`/tmp/ao-test-owners-<uid>/<socket>`, written by `private_socket_name`). It used to kill them
   all, which is how a review running the suite beside a worker's run destroyed that run's tmux
   server mid-test — a harness bug that looked like a timing flake for two weeks.
2. **Every wait is bounded.** Use `wait_for`, `wait_for_sync`, or `wait_state` from
   `conftest.py`. A bare `sleep` is never synchronisation.
3. **Tests clean up their own sessions.** Kill what you create; the fixture kills the server,
   but an exited pane left behind is re-adopted on the next tick and confuses the next assertion.
4. **Pick the right agent fixture.** `agent` (in-process, `async def` tests via `LocalClient`)
   or `subprocess_agent` (separate process, plain `def` tests). Anything that calls
   `call_sync`, `cli.main`, or the sync FastAPI `TestClient` uses `asyncio.run` and hangs
   against the in-process fixture, because a sync test body never pumps that fixture's loop.
5. **`subprocess_agent` is module-scoped.** It sets `AGENTORC_HOME` and `AGENTORC_TMUX_SOCKET`
   in the test process as well as the child, because the UI under `TestClient` runs in the test
   process and reads them from `os.environ` at call time. A child process that must reach the
   same agent (`agentorc-hook`, `agentorc-agent rpc`) inherits the environment; do not pass a
   curated `env=` without those two variables.
6. **Never wipe the adapter registry.** Add a stub with
   `monkeypatch.setitem(adapters._REGISTRY, ...)` after `adapters.load_all()` (the `hookstub`
   fixture does this), or, for a registry test, `monkeypatch.setattr(adapters, "_REGISTRY", {})`
   so the real one comes back at teardown.
7. **Markers.** `unit`: no tmux, sockets, or subprocesses. `integration`: everything else. Mark
   at module level with `pytestmark`; split a file rather than leave it unmarked.

## Real-environment dependencies

- The anchor rule (design §9 invariant 2) reads the default Claude Code profile's session
  registry under `~/.claude` to see sessions started outside agentorc. Tests create sessions in
  temp directories, which no real session occupies, so this is harmless but not isolated.
- `test_gitinfo` sets its own git identity per repo; no global git config is needed.
- `TestClient` keeps a portal thread alive, so `ptyprocess`'s `forkpty()` in the `/term` tests
  runs in a multi-threaded process and Python warns it may deadlock the child. The child only
  execs `tmux attach`, so the exposure is accepted and the warning is filtered in
  `pyproject.toml` (TD-007, archived 2026-09-10). If a `/term` test ever hangs in the child,
  the alternative is a pty helper subprocess.

## After a killed run

Ctrl-C or a CI timeout skips fixture teardown and can leave `ao-test-*` tmux servers and
`_agent_child.py` orphans behind. They never touch the real server. The session-scoped
`_sweep_stale_test_servers` fixture kills stale servers at the start of the next run; by hand:

```sh
for s in /tmp/tmux-$(id -u)/ao-test-*; do tmux -S "$s" kill-server; done
pkill -f tests/_agent_child.py
```
