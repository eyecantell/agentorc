# Technical Debt Archive

Resolved entries moved from [technical_debt.md](technical_debt.md), appended in resolution order (the archive doubles as a chronological account of what has been paid down). Each keeps its original TD number forever (numbers are never reused), keeps its original **Why**, and replaces **Fix** with `**Resolved:** YYYY-MM-DD (PR #n)` plus a pointer to wherever the lasting content now lives.

## TD-011: VS Code link path is not URL-escaped (spaces break the URI)

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/agentorc/ui/app.py` (`vscode_url`)

**Why:** The session directory is interpolated raw into `vscode://vscode-remote/ssh-remote+<host><path>?windowId=_blank`. A directory with a space or `?` produces a malformed URI and the link silently does nothing. Pre-existing; noted by the PR #10 review.

**Resolved:** 2026-09-10 (PR #35) — `vscode_url` percent-encodes the path (`urllib.parse.quote(directory, safe="/")`) in both URI forms; `tests/test_ui.py::test_vscode_url_opens_a_new_window` covers a space and a `?`.

## TD-020: `_removed_at` compares wall-clock stamps; a clock step re-adopts a just-removed pane

**Priority:** Low
**Added:** 2026-09-10 (review note on PR #22, 2026-09-07; ledgered late)
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_reconcile`, `rpc_remove`)

**Why:** `rpc_remove` stamps `_removed_at[id] = datetime.now(UTC)` and `_reconcile` skips
re-adopting a pane whose stamp is newer than the tick's `snapshot_at`, forgetting stamps after a
minute. All three are wall-clock; an NTP step or a suspend/resume between them can make a stamp
look older than the snapshot (the dead pane comes back as a nameless shell for a tick) or keep
it alive past the minute. Harmless in practice, which is why it is Low, but the guard exists
because the same re-adoption bit us on first use (2026-09-06).

**Resolved:** 2026-09-10 (PR #36) — the guard no longer compares clocks at all: `HostAgent._removed` maps a name to the removed pane's tmux `created` epoch plus a `time.monotonic()` stamp used only for expiry (`REMOVED_GUARD_SECONDS`); `_is_removed_pane` decides by pane identity. `snapshot_at` stays a `datetime` because its only remaining comparison (`CREATE_GRACE`) is against the persisted `created` field. Test: `tests/test_agent_paths.py::test_pane_snapshot_older_than_a_remove_does_not_readopt` (includes a one-hour wall-clock step).

**Related:** PR #22 review; TD-021.

## TD-021: Immediate id reuse after `remove` can mis-adopt the new pane for one tick

**Priority:** Low
**Added:** 2026-09-10 (review note on PR #22, 2026-09-07; ledgered late)
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption loop), `src/sessionorc/naming.py`

**Why:** The adoption guard is keyed by tmux session *name*. If a session is removed and a new
one with the same name is created by hand before the next tick, the new pane is skipped for
that tick (the stamp says "just removed") and then adopted as a nameless shell on the one after;
if the new one is created through the agent, the record exists and the guard never applies.
One tick of wrong state, only for hand-created reuse of a just-removed name.

**Resolved:** 2026-09-10 (PR #36) — `_is_removed_pane(name, pane)` skips a pane only when `pane.created <= ` the removed pane's creation time, so a hand-made session reusing the name is adopted on the first tick that sees it. Residual: `created` is whole seconds, so a pane that was created, exited, observed, removed and had its name reused all inside one second (or whose pane was already gone at remove, `created` None) stays skipped until the guard expires (`REMOVED_GUARD_SECONDS`, 60 s). Covered by the same test as TD-020, including the equal-second case.

**Related:** PR #22 review; TD-020; TD-010 (adoption of hand-started sessions).

## TD-009: `subscribe` resets the shared push cache: every new tab re-pushes everything to every tab

## TD-012: Resuming a conversation that is still live elsewhere is not refused

## TD-013: External-session check reads the default profile's registry only

## TD-007: test_ui mutates `os.environ` for a module-scoped agent

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_handle_conn`, `_last_pushed`)

**Why:** `_last_pushed` is one dict for all subscribers; a new `subscribe` clears it so the newcomer gets a full snapshot, which also re-sends every session to every other connected tab. Harmless at a handful of tabs, wasteful at many; found in the PR #4 review.

**Resolved:** 2026-09-10 (PR #37) — `HostAgent._subscribers` is now `writer → {session id: last payload sent}`; `_push_changes` serialises each session once and diffs per subscriber, `subscribe` registers an empty map so only the newcomer gets the full snapshot, and `_forget` scrubs the id from every map and queues one `gone` that the next `_push_changes` (the caller's or a tick's) announces once. Test: `tests/test_agent.py::test_second_subscriber_gets_a_snapshot_without_disturbing_the_first`.

**Location:** `src/sessionorc/agent.py` (`rpc_create`, `_supersede`)

**Why:** `create(resume=<id>)` supersedes an *exited* record with that adapter id, but nothing stops a resume of a conversation whose session is still running (in another directory, or hand-started): two tmux sessions would then drive one Claude Code conversation. The anchor rule compares directories, not conversations. Raised in the PR #17 review.

**Resolved:** 2026-09-10 (PR #38) — `rpc_create` refuses a `resume` whose conversation is still live: `HostAgent.conversation_holders(adapter_id)` lists live records of ours with that adapter id plus live sessions outside agentorc (`adapters.external_sessions()`) with that tool id, and the create errors `conversation … is still live in …; kill it first, or Switch to it`. A per-conversation lock (`_dir_locks["conversation:<id>"]`, taken with the directory lock) spans the check and the record insert, so two concurrent resumes of one id into different directories start at most one. An exited record is still superseded. Test: `tests/test_agent_paths.py::test_resume_of_a_live_conversation_is_refused`.

**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`, `external_sessions`)

**Why:** The anchor rule's view of Claude Code sessions started outside agentorc comes from `~/.claude/sessions/`, i.e. the default profile's config dir. A second profile with its own `CLAUDE_CONFIG_DIR` keeps its registry elsewhere, so a hand-started session under that account is invisible to occupancy and to the create-time refusal. No second profile exists yet; noted in the PR #20 review.

**Resolved:** 2026-09-10 (PR #39) — `ClaudeCodeAdapter.external_sessions()` reads the registry of every profile declared in `profiles.yml` whose adapter is `claude-code`, once per distinct config dir (the implicit `default` profile still reads `~/.claude`). Test: `tests/test_claude_adapter.py::test_external_sessions_reads_every_profiles_registry` (two config dirs, a duplicate, another tool's profile).

**Location:** `tests/test_ui.py` (`client` fixture over `subprocess_agent` in `tests/conftest.py`)

**Why:** The UI tests need one agent shared across a sync `TestClient`. The env/tick leak was fixed in PR #4 review (module-scoped `MonkeyPatch`, undone at teardown). The agent thread went away in the test-suite consolidation (2026-09-07): the module's agent is now a separate process (`tests/_agent_child.py` on the private tmux socket; `agentorc-agent serve` cannot take one). What remains: starlette's `TestClient` keeps an anyio portal thread alive for the whole `with` block, so `ptyprocess` still calls `forkpty()` in a multi-threaded process and Python still warns it may deadlock the child — the rare-flake exposure is smaller, not gone.

**Resolved:** 2026-09-10 (PR #40) — accepted: the warning is filtered in `pyproject.toml` (`[tool.pytest.ini_options] filterwarnings`, comment points here) and the residual exposure is written up in `tests/README.md` "Real-environment dependencies" with the pty-helper alternative should a `/term` test ever hang.

## TD-018: `ao --json` on every subcommand

**Priority:** Low
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/agentorc/cli.py`

**Why:** `status --json` exists; `new`, `shell`, `send`, `kill`, `close`, `allow`, `deny`, `tail`
print prose, so a script or an agent driving `ao` has to parse "ao-x-y  attach: tmux attach …".
herdr's CLI is JSON-first (most commands print the API response with the ids the next call
needs, and its skill file tells agents to parse ids rather than predict them), which is what made
the spike's automation a matter of `jq`; agentorc's own sessions are the obvious next driver of
`ao`.

**Resolved:** 2026-09-10 (PR #41) — a global `--json` on `ao` (also accepted after any subcommand, `default=SUPPRESS` so the two never fight); every `cmd_*` goes through `emit()` (RPC result, or `{"ok": true, "id": …}` where the RPC returns nothing) and `fail()` (`{"error": …}` on stdout, same exit codes, `hint` for an unreachable agent). `ui` runs a server and prints nothing. README "CLI" documents it; test: `tests/test_cli.py::test_json_on_every_subcommand`.

**Related:** design §4.7; TD-019; ADR 2026-09-10.
