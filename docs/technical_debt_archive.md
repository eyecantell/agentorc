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

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_handle_conn`, `_last_pushed`)

**Why:** `_last_pushed` is one dict for all subscribers; a new `subscribe` clears it so the newcomer gets a full snapshot, which also re-sends every session to every other connected tab. Harmless at a handful of tabs, wasteful at many; found in the PR #4 review.

**Resolved:** 2026-09-10 (PR #37) — `HostAgent._subscribers` is now `writer → {session id: last payload sent}`; `_push_changes` serialises each session once and diffs per subscriber, `subscribe` registers an empty map so only the newcomer gets the full snapshot, and `_forget` scrubs the id from every map and queues one `gone` that the next `_push_changes` (the caller's or a tick's) announces once. Test: `tests/test_agent.py::test_second_subscriber_gets_a_snapshot_without_disturbing_the_first`.

## TD-012: Resuming a conversation that is still live elsewhere is not refused

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`rpc_create`, `_supersede`)

**Why:** `create(resume=<id>)` supersedes an *exited* record with that adapter id, but nothing stops a resume of a conversation whose session is still running (in another directory, or hand-started): two tmux sessions would then drive one Claude Code conversation. The anchor rule compares directories, not conversations. Raised in the PR #17 review.

**Resolved:** 2026-09-10 (PR #38) — `rpc_create` refuses a `resume` whose conversation is still live: `HostAgent.conversation_holders(adapter_id)` lists live records of ours with that adapter id plus live sessions outside agentorc (`adapters.external_sessions()`) with that tool id, and the create errors `conversation … is still live in …; kill it first, or Switch to it`. A per-conversation lock (`_dir_locks["conversation:<id>"]`, taken with the directory lock) spans the check and the record insert, so two concurrent resumes of one id into different directories start at most one. An exited record is still superseded. Test: `tests/test_agent_paths.py::test_resume_of_a_live_conversation_is_refused`.

## TD-013: External-session check reads the default profile's registry only

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`, `external_sessions`)

**Why:** The anchor rule's view of Claude Code sessions started outside agentorc comes from `~/.claude/sessions/`, i.e. the default profile's config dir. A second profile with its own `CLAUDE_CONFIG_DIR` keeps its registry elsewhere, so a hand-started session under that account is invisible to occupancy and to the create-time refusal. No second profile exists yet; noted in the PR #20 review.

**Resolved:** 2026-09-10 (PR #39) — `ClaudeCodeAdapter.external_sessions()` reads the registry of every profile declared in `profiles.yml` whose adapter is `claude-code`, once per distinct config dir (the implicit `default` profile still reads `~/.claude`). Test: `tests/test_claude_adapter.py::test_external_sessions_reads_every_profiles_registry` (two config dirs, a duplicate, another tool's profile).

## TD-007: test_ui mutates `os.environ` for a module-scoped agent

**Priority:** Low
**Added:** 2026-09-06
**Status:** Resolved
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

**Resolved:** 2026-09-10 (PR #41) — a global `--json` on `ao` (also accepted after any subcommand, `default=SUPPRESS` so the two never fight); every `cmd_*` goes through `emit()` (RPC result, or `{"ok": true, "id": …}` where the RPC returns nothing) and `fail()` (`{"error": …}` on stdout, same exit codes, `hint` for an unreachable agent). `ui` runs a server and prints nothing; `service install`/`uninstall` take the flag but are not exercised by the test (systemd side effects). Prose output is unchanged. README "CLI" documents it; test: `tests/test_cli.py::test_json_on_every_subcommand`.

**Related:** design §4.7; TD-019; ADR 2026-09-10.

## TD-017: Seen-state: "finished while you were away" is not the same as idle

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/models.py` (`Session`), `src/sessionorc/agent.py`, `src/agentorc/ui/` (Team card, Focus)

**Why:** A session that went `idle` while nobody was looking is the common phone-triage case, and
today it sorts and looks exactly like one that has been idle all day. herdr keeps `done` (idle,
not yet looked at) apart from `idle` by a server-side seen mark that explicit focus clears and
reads do not. agentorc's Focus view is the natural "seen".

**Resolved:** 2026-09-10 (PR #43) — `Session.seen_at` (persisted) set by `rpc_seen`, which the UI calls when Focus opens (`GET /focus/<id>`), after any card action, and from the open Focus page whenever its session's event arrives `unseen`. `view()` computes `unseen = idle and (no seen_at or since > seen_at)` (whole-second stamps: a tie reads as seen), renders "finished · unseen", sorts it at rank 4.5 (above `idle`, below `working`); `/events` carries the view's rank. `idle` stays `idle` in every payload. Design §4.5 sort list names the slot. Test: `tests/test_ui.py::test_unseen_idle_until_focused`.

**Related:** design §4.2, §4.5 (Team sort); ADR 2026-09-10.

## TD-016: `send` should confirm the prompt took: a send-and-wait RPC for policies

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`rpc_send`), `src/agentorc/cli.py` (`send`)

**Why:** `send` already refuses while a permission or question is pending (the right half of the
rule). It then writes the text and Enter and returns, so a policy that nudges an unattended
worker cannot tell whether the prompt was taken, swallowed by a dialog that appeared in between,
or typed into a pane whose agent had just exited. herdr's `agent prompt --wait` names the two
failure modes worth copying: nothing starts working within a few seconds (`agent_prompt_stalled`),
and the caller's timeout passes before a settled state. Phase 3's supervisor (§6: wrap-up prompt,
then kill) needs exactly this to know the wrap-up request landed.

**Resolved:** 2026-09-10 (PR #42) — `rpc_send(id, text, wait=False, timeout=None)`: with `wait` it returns the record once the session has started on *this* prompt (a transition off `idle`; a busy session first has its current turn end — a stop on anything but `idle` is returned as is, prompt still queued — then the next turn must start) and then reached one of `SETTLED` (`idle`, `needs-you`, `exited`, `closed`, `limited`, `stalled?`); errors `prompt-stalled` after `SEND_STALL_SECONDS` (5 s, or the remaining `timeout` if shorter) of nothing, `timeout` after `timeout` seconds in total, `removed` if the record goes away. `ao send --wait [--timeout N]` prints the settled state. Nothing is ever re-sent. Tests: `tests/test_agent.py::test_send_wait_three_outcomes`, `tests/test_cli.py::test_send_wait`. Phase 3's wrap-up policy is the intended caller (design §6).

**Related:** design §4.2 (the never-re-send rule), §4.4, §6; ADR 2026-09-10.

## TD-014: herdr spike: can it be the `sessionorc` substrate under phase 2? (design §10)

**Priority:** High
**Added:** 2026-09-10
**Status:** Done 2026-09-10 — spike run against herdr 0.9.0 on kmaster in a scratch config; the pass criterion failed (Claude Code state is screen-scraped, the status event carries no kind or text, no `limited`); decided (a) independent in [ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md); §10 item checked, §3 row corrected.
**Location:** design §3 (herdr row), §10 "Build on, or beside, herdr?"; `src/sessionorc/` (the layer a substrate would sit under)

**Why:** herdr (https://herdr.dev, Apache-2.0, Herdr, Inc., $6M seed 2026-09-09) already ships the
substrate half of this design — persistent panes, multi-host over ssh, restart recovery, hook-fed
state for six of its 17 integrated CLIs (not Claude Code, which it screen-scrapes — the spike's finding), a worktree API, an event-subscription socket API — and
Herdr Cloud is about to ship the `relay` transport of §4.5b. It does not do the half §1 came
from: states finer than `blocked`, unattended supervision, the anchor rule, Ready to close,
per-repo commands, phone triage. Core contribution is closed (no unsolicited PRs); plugins and
the socket API are the open surface. Phase 2 (ssh transport) is the first thing herdr would
replace, so the decision has to come before phase 2 is built, and it should be decided by a
measurement, not by argument. Analysis in session 019chcZM (2026-09-10); facts in design §3.

**Resolved:** 2026-09-10 (PRs #26, #27; ledgered here 2026-09-10) — the spike ran against herdr 0.9.0 in a scratch config and failed the pass criterion (Claude Code state is screen-scraped, the status event carries no kind or text, no `limited`); decided (a) independent in [ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md); design §3 row corrected and the §10 item checked in PR #26; the lessons became TD-015..TD-019 and design §4.1/§4.2/§4.7/§9 edits in PR #28.

**Related:** design §3, §4.5b, §4.5c, §7 phase 2, §10; PRs #23, #24; TD-004 (ssh transport, the work this decides).

## TD-015: Screen-rule manifests per tool with `ao explain`: the scraped second source gets a shape

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/adapters.py` (`classify`), `src/agentorc/adapters/claude_code/__init__.py`, `src/sessionorc/agent.py` (tick)

**Why:** Design §4.2 allows a pane classifier as a labelled fallback, and today the only scraped
verdicts are the shell/command adapters' foreground-process check and the agent's liveness
cross-check; the Claude Code adapter's `classify` returns nothing. The herdr spike ([ADR 2026-09-10](decisions/2026-09-10-herdr-spike.md))
showed what the fallback is for: its screen detector caught the trust dialog, which no Claude Code
hook reports, and its `agent explain` printed the rule that fired, the region it matched and the
fallback reason when nothing did. Three things agentorc wants rest on the same mechanism: the
trust dialog and any future dialog no hook covers, `limited` from the tool's own limit message
before TD-001's usage polling exists, and a `stalled?` that can say *why* it is unsure.

**Resolved:** 2026-09-10 (PR #47) — `sessionorc.screen` (`Rule`, `Manifest`, `Match`): one versioned TOML manifest per tool, rules with `any`/`all`/`not` regexes over the last `region` lines, a priority, and an optional pending (`$line` = the matched line); the highest-priority match wins with its evidence. Claude Code ships `adapters/claude_code/screen_rules.toml` (trust dialog → `needs-you`; the spike's three usage-limit screens → `limited`) and `explain(tail)`. The agent applies a hook-fed adapter's verdict as `scraped` only when no hook has reported within `STALL_AFTER` (`_last_hook`; a session no hook has reported on yet takes it at once), never over a fresh hook state. `rpc_explain` and `ao explain <id>` / `ao explain --file <screen> [-a adapter]` print the screen, the rule, the evidence and whether it applies. Fixtures under `tests/fixtures/screens/`; tests `tests/test_screen.py`, `tests/test_agent_paths.py::test_screen_rule_is_a_labelled_fallback_that_a_fresh_hook_outranks`, `tests/test_cli.py::test_explain_file_and_session`. Not done here: a per-adapter stall window and a `stalled?` rule with a reason (the manifest can carry one when a screen for it exists).

**Related:** design §4.2, §9 invariant 4; TD-001 (`limited` from the usage endpoint); ADR 2026-09-10.

## TD-001: `limited` state: wire adapter `usage()` into the agent tick

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (tick), `src/agentorc/adapters/claude_code/__init__.py` (`usage()`)

**Why:** Design §4.2 promises a `limited` state (usage cap hit, reset time shown, Switch profile / Wait). `usage()` exists and parses the OAuth usage endpoint, but nothing calls it: the agent never produces `limited`, and the top bar has no usage figure. Left out of phase 1a–1c to keep each PR reviewable. `usage()` is synchronous network I/O and must run in `asyncio.to_thread`, per profile, on a slow cadence (tdgrind polled per tick; once a minute is plenty), with a fetch failure never gating anything (§6).

**Resolved:** 2026-09-10 (PR #44) — `HostAgent._refresh_usage` (a detached task started by the tick, one at a time) asks each live agent session's adapter `usage_for(profile)` in a thread at most once per `USAGE_EVERY` (60 s), caches per profile (`rpc_usage`), streams `{"event": "usage", …}` to subscribers (also on subscribe), and applies the `limited` rule: an interactive session on a profile at 100% of a window gets `limited` with `Pending(kind="limit", text="5-hour cap · resets HH:MMZ")` (never over `needs-you`), and returns to the state it had before the cap once the window resets (a `resets_at` already past is not a cap). `ClaudeCodeAdapter.usage_for` is the name-keyed wrapper of `usage()`. The top bar carries a per-profile chip (`#usagechip`, red at a cap). Switch profile / Wait (§4.5a) are still to build. Design §4.3/§4.4 updated. Tests: `tests/test_agent.py::test_limited_from_usage_cap`, `tests/test_claude_adapter.py::test_usage_for_by_profile_name`.

**Related:** design §4.2, §4.2a, §6 usage gate (phase 3).

## TD-022: Focus terminal cannot scroll back: wheel/PageUp show nothing above the live screen

**Priority:** High
**Added:** 2026-09-09 (first-use finding, Paul)
**Status:** Resolved
**Location:** `src/sessionorc/tmux.py` (`attach_argv`), `src/agentorc/ui/pty_bridge.py` (`scroll_argv`, `pump`),
`src/agentorc/ui/app.py` (`/term` scroll callback), `src/agentorc/ui/static/app.js` (`AO.focus`)

**Why:** The Focus terminal is xterm.js around `tmux attach`, and scrolling is the one thing that shape
does not give for free. tmux owns the pane: when a line leaves the top of the screen it goes into
*tmux's* history (`history-limit` 50000), and tmux repaints the client in place, so xterm.js's own
5000-line scrollback held nothing useful — the wheel and Shift+PageUp scrolled an empty or stale buffer,
and the user saw exactly one screen of a Claude Code conversation. `mouse` was off on the user's tmux
server (default), so the wheel was not forwarded to tmux either. The result on first use: a reply longer
than the pane could not be read in the UI at all. That broke design §2 requirement 2 ("full conversation
in an embedded terminal") — the terminal is the *primary* read surface, not a peek — and it made the
phone layout (TD-003) pointless before it started, since a phone pane is shorter still.

**Resolved:** 2026-09-10 (PR #33). `sessionorc.tmux.attach_argv` (shared by the pty bridge and `ao focus`)
chains `mouse on` on the session after `tmux attach` (a session option, at attach so adopted and
pre-existing sessions get it; attach first because a tmux chain stops at the first failure); xterm.js
runs with `scrollback: 0` so the wheel only ever reaches tmux; Shift+PageUp / Shift+PageDown send a
`scroll` bridge message that the UI turns into `copy-mode -e -u` / `page-down` against the session.
Lasting content: design §4.6 ("Scrollback is tmux's"), the `attach_argv` and `scroll_argv` docstrings,
and `tests/test_ui.py::test_terminal_scrollback_reaches_tmux`. Not verified on the narrow layout (TD-003
is still open); the Copy button's hint documents Shift+drag for selection under mouse tracking.

**Related:** design §2 req. 2, §4.6, §4.5a; TD-003 (phone layout); TD-002 (composer); TD-010 (b) (`ao focus`).

## TD-024: `agentorc-agent serve` logs a pending-task traceback on every SIGTERM stop

**Priority:** Low
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`main`, the `serve` branch)

**Why:** `main` installs `loop.stop` as the SIGINT/SIGTERM handler and runs `agent.serve()` with `run_until_complete`. `loop.stop` halts the loop with the `serve` coroutine still pending, so the process ends with `ERROR asyncio: Task was destroyed but it is pending!` and an `Exception ignored … RuntimeError: Event loop is closed` traceback in the journal. Seen 2026-09-10 in `journalctl --user -u agentorc-agent` when `ao service install` restarted the unit after the promote (pid 681793). Harmless — sessions are re-adopted on the next tick, nothing is lost — but every restart writes an ERROR line that looks like a crash, and `serve`'s `finally` (cancel the ticker, unlink the socket file) is skipped.

**Resolved:** 2026-09-10 (PR #51). `sessionorc.agent.serve_until_signal` runs `serve()` as a task and installs `task.cancel` as the SIGINT/SIGTERM handler; `main` runs it under `asyncio.run`, which also closes async generators and the loop. The test child (`tests/_agent_child.py`) now calls the same helper, and `tests/test_agent_restart.py::test_sigterm_stops_serve_cleanly` asserts exit 0, socket unlinked, no traceback on stderr. Live check on the user unit pending (board).

**Related:** TD-020 (agent restart), the restart tests from PR #30.

## TD-023: `exited` is overloaded: a killed session and a natural exit look the same, but only one still has a pane

**Priority:** Low
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`rpc_kill`, `_observe`), `src/agentorc/cli.py` (`cmd_focus`)

**Why:** `rpc_kill` destroys the tmux session and sets `exited`; a process that ends on its own also reads `exited`, but `remain-on-exit` keeps its dead pane (exit code, last screen) until `remove`. Nothing on the record says which, so a client cannot tell whether Focus / `ao focus` will find a pane: the CLI learned to run `tmux attach` as a child and report a non-zero exit instead (PR #46 review), and the UI's `/term` bridge finds out the same way. Found in the PR #46 review.

**Resolved:** 2026-09-10 (PR #52). The record carries `pane: bool` (`sessionorc.models.Session`): the tick sets it true whenever a pane (live or dead) is observed and false when the snapshot has none past the create grace; `kill` and `close` set it false at once. `ao focus` and the UI's `/term` refuse a `pane: false` record with agentorc's own line before any tmux call; the card shows Details instead of Focus and the Focus banner says the pane is gone. Lasting content: design §4.2 (state table), §4.5a (Kill, Details rows); tests in `test_agent.py::test_shell_lifecycle` (natural exit keeps its pane, kill does not), `test_cli.py::test_focus_and_attach_print_the_attach_argv_under_json`, `test_ui.py::test_closed_session_terminal_is_final_and_occupancy_endpoint`.

**Related:** TD-010 (b), PR #46.

## TD-010: Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Team

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption of `ao-*` panes), `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`)

**Why:** The phase 1 success test reads "every session Paul has open on kmaster shows the right state". Today the Team shows sessions agentorc launched plus any hand-started tmux session named `ao-*`. Paul's day-to-day sessions run in VS Code terminals with no tmux at all, so they never appear. Design §4.1 says hand-started sessions enter the Team by being **adopted** (the Resumable tab's Adopt control; not built yet, and not assigned to a phase in §7), which assumes a tmux session to attach to; a VS Code-terminal session has none.

**Resolved:** 2026-09-10 — (b) PR #46 (`ao new --attach`, `ao focus`); (a) PR #56: `HostAgent._reconcile_external` builds a read-only card (`Session.external`, id `ext-<tool id>`, `pane: false`, state from the registry status, `scraped`) for every live session `adapters.external_sessions()` reports that is not one of ours by tool id or directory; never stored, gone with the process; acting RPCs refuse with "started outside agentorc", `seen` works. `config_dir()` now honours `CLAUDE_CONFIG_DIR` for a profile without one (as Claude Code does), which is also how the test fixtures keep this machine's live registry out of the suite. Not built, by design: Allow/Deny on such a card (no hook channel without agentorc's hooks layer). Lasting content: design §4.1 (the read-only card bullet), §4.5a (card row); `tests/test_agent.py::test_registry_only_sessions_get_read_only_cards`, `tests/test_ui.py::test_registry_only_card_renders_read_only`. Live check on kmaster pending (board).

**Related:** design §4.1 adoption, §4.3 registry cross-check, phase 1 success test.

## TD-027: `send`'s Enter is swallowed after the bracketed paste: four workers sat all afternoon with an unsubmitted wrap-up prompt

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/tmux.py` (`send_prompt`: `paste` then `send_enter`), `src/sessionorc/agent.py` (`rpc_send`)

**Why:** `send_prompt` pastes the text with `paste-buffer -p` and sends `Enter` in the very next tmux command. The likely mechanism (not yet measured): Claude Code is still processing the bracketed paste when the Enter arrives, and consumes it with the paste instead of submitting; the text lands in the composer and stays there. Seen 2026-09-10 at 17:10 on every unattended session on kmaster: `ao-samscrape-tdgrind-1/2/3` each show `❯ run window is closing — wrap up and exit` (one of them the shorter `run window is closing`) unsubmitted since around noon, and `ao-agentorc-tdgrind-ao-1-2` shows `❯ /exit` unsubmitted since 10:24. All four had already finished their work, so nothing was lost this time, but the same swallow on a real prompt means a policy's instruction never runs while the card reads `idle`. samscrape's `tdgrind.sh` `send_text` hit the same thing in August and settled on `C-u`, `send-keys -l <text>`, `C-m` with no bracketed paste; agentorc chose the paste for multi-line briefs (design §4.3). `ao send --wait` (TD-016) would have reported `prompt-stalled` here, but nothing retries, and the sender did not use `--wait`.

**Resolved:** 2026-09-10 (PR #60) — **Measured first, and the four screens were not what they looked like.** Read-only `capture-pane -e` on the three samscrape composers shows their `run window is closing …` text painted in **faint** (SGR 2): it is Claude Code's suggested-next-prompt ghost text (the session's own last prompt is a common suggestion), not an unsubmitted prompt — no transcript entry follows, and nothing had sent it: those workers run under agentorc with no supervisor, TD-026 gap (1). The fourth (`ao-agentorc-agentorc-tests-2`) is a dead pane whose last frame shows `/exit` in slash-command colour: it exited normally on 2026-09-09 22:05. A real swallow does exist, and a throwaway session on a private socket reproduced it: with idle waits, 50/50 pastes submitted (Enter and `C-m` alike, even paste and Enter in one tmux command), but a burst of five into a **fresh** session submitted one and concatenated the other four — an Enter that arrives before the tool has read the paste is dropped, which is what a fresh, loaded, or slow-painting session does. Fix: `HostAgent._submit` pastes, waits up to 1 s for the text to paint in the composer, presses Enter, and requires the composer to empty within 1.5 s; one `C-m` retry, then `prompt-stuck` (every `send`, not only `--wait`; the text is never re-pasted). The read goes through a new optional adapter method `composer(tail_raw)` (design §4.3) over `capture-pane -e`, with `sessionorc.screen.painted_text` dropping faint runs so ghost text is never "stuck". Claude Code implements it (last `❯` row); `shell` does not (a running command's echo is not a composer) and keeps the blind paste + Enter. Validated through `_submit` against the real tool: the failing burst 5/5, then 50/50 serial. Tests: `tests/_composer_child.py` (a pane that swallows N Enters per paste) behind `ComposerStub` — `test_send_confirms_the_submit` covers 0 (one submit, faint echo ignored), 1 (`C-m` lands, no duplicate), 2 (`prompt-stuck`, text left in place); `test_composer_reads_painted_text_only`; `test_painted_text_drops_faint_runs`. Design §4.2 (`send` confirms), §4.3 (`composer`), §4.5a Send row.

**Related:** TD-016 (`send --wait`), TD-015 (screen rules), design §4.3, samscrape `scripts/tdgrind.sh` `send_text`.

## TD-019: Ship a skill file for `ao` (`ao --skill`)

**Priority:** Low
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/agentorc/cli.py`, a new `src/agentorc/skill.md`

**Why:** `herdr --skill` prints the instructions a coding agent needs to drive it safely: check
you are inside a managed session, parse ids from JSON, which commands mutate, what not to do
(never answer another agent's dialog). An agent running inside an agentorc session has the same
needs — `AGENTORC_SESSION` is set, `ao` is on `PATH` — and the adapter-author guide in phase 5
is the moment to write it down once the CLI is stable.

**Resolved:** 2026-09-10 (PR #61) — pulled forward from phase 5 because an orchestrator session driving `ao` is next. `ao --skill` prints `src/agentorc/skill.md` (an argparse action, so it works without a subcommand): front matter, the first-three-steps ritual (`AGENTORC_SESSION` is you, `ao status --json`, `--json` on every call), the state table from design §4.2 with what to do in each, the mutating commands with their errors, the TD-027 lesson (verify every send: `send --wait --json`, act on `prompt-stalled` / `prompt-stuck`, never re-send on a guess), the Never list from §9 invariants 1/2/5/6 and the unattended-worker brief rules (no tmux, no other session's dialog, no anchor, no `~/.claude` / `~/.agentorc` / units), and a worked loop. 84 lines. Installing it into a repo is `ao --skill > .claude/skills/ao/SKILL.md`; the New-session install offer stays phase 5 (design §7). Test: `tests/test_cli.py::test_skill_prints_the_rules`. Design §4.7, §7 phase 5, README "CLI".

**Related:** design §4.7, §7 phase 5; TD-018; ADR 2026-09-10.

## TD-031: Show the model in use on the card and in `ao status` (when the adapter can tell)

**Priority:** Low
**Added:** 2026-09-11
**Status:** Resolved 2026-09-11 (PR #72)
**Location:** `src/agentorc/adapters/claude_code/` (hook payload, transcript locator), `src/sessionorc/agent.py` (session record, tick), `src/agentorc/ui/templates/card.html` (`profile_line`), `src/agentorc/cli.py` (`status`)

**Why:** Paul asked for it on 2026-09-11: with grinders on one model and his own sessions on another, the card's profile line (`claude-code · <account or profile>`, plus the profile's *declared* model when one is set) does not say which model a session is actually running, and a `/model` switch mid-session changes it without any card noticing. The profile's declared `model` (§4.2a) is an intent, not an observation. What the adapter can observe for Claude Code: every `type: assistant` entry in the transcript carries `message.model` (`claude-fable-5-1` in this session's transcript; `<synthetic>` for system entries), so the last one is the model in use as of the last turn. Read the entry's own field, not a raw grep: `"model":"sonnet"` also appears inside `tool_input` of Agent calls that *request* a subagent model, which is not the session's model; whether the hook payload carries a model field is unverified (the hook reads none today).

**Resolved:** 2026-09-11 (PR #72). The hook payload does carry one, which this entry left unverified: `model` on SessionStart (documented as not always present), and `to_model` on `PostModelSwitch`, which is how a `/model` mid-session reports itself — both now read by the adapter's `translate`, and `PostModelSwitch` added to `HOOK_EVENTS` so it fires at all. There is **no** `$CLAUDE_MODEL` environment variable, and `$ANTHROPIC_MODEL` does not follow a `/model`, so neither is used. The transcript remains the cross-check and the fallback for a session that started before the hook carried one: `ClaudeCodeAdapter.model_in_use` reads the tail (256 KiB) of the transcript for the last top-level `assistant` entry's own `message.model`, skipping `isSidechain` entries (a subagent's turn) and `<synthetic>`, on a 30 s tick cadence. An unknown profile name reads nothing rather than falling back to another account's config dir. Shown as the profile line's third part on the card and in Focus — `claude-code · paul · fable-5-1`, and `opus-5 (profile)` when only the profile's declared model is known — and as a `model:` line under `ao status -v` (the entry said "a column"; a line matches how `-v` already prints `grants:` and `report:`). `-v` shows an observation or nothing — the `(profile)` fallback is the card's, where the line always names three things. The name is shortened by the adapter that owns the naming, through `sessionorc.adapters.short_model`, so core still holds no tool-specific names. The lasting content is design §4.2a.

**Related:** design §4.2a (profiles: tool · account · model), §4.5 card profile line, TD-001 (usage per profile).

## TD-030: One name, one session: refuse a live holder, supersede an exited one, drop hidden `-2` suffixes

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved 2026-09-11 (PRs #75, #77)
**Location:** `src/sessionorc/naming.py` (`session_id`), `src/sessionorc/agent.py` (`rpc_create`, `_supersede`, `occupants`), `src/agentorc/cli.py` (`new`, `shell`), `src/agentorc/ui/` (New session form, `/api/occupancy`)

**Why:** `naming.session_id` appends `-2`, `-3` on any id collision — live or dead — and the record keeps the person's original name, so on 2026-09-10 the Team showed `aotest` beside `aotest-2` and two cards named `tdgrind-ao-1` (one exited, one working). Paul: "it is a little confusing to have multiple sessions of the same name". The name is the handle `ao focus`, `ao send` and the filter take, so ambiguity there is a defect. Design §4.1 now says a name identifies one session per scope: a live holder refuses, an exited or closed holder is superseded (as `_supersede` already does for a resumed conversation), and a suffix is only for a tmux id with no record and is then shown.

**Resolved:** 2026-09-11 (steps 1-3 in PR #75, steps 4-5 in PR #77). All five steps shipped as the entry specified them, with three decisions worth keeping:

- **One place decides the rule.** `_name_verdict` answers "what would this name do" and both callers use it: `_name_holder` (which `rpc_create` calls, raising for a live holder) and `rpc_name_check` (which the New session form polls as you type, like the directory occupancy check). The refusal text, the "replaces the closed `<name>` — run log kept" note and the switch-to hint are composed there, so the form and `ao new` cannot drift.
- **The name is taken only once the launch has succeeded**, and the superseded record is *replaced in place* rather than forgotten — the card becomes the new session instead of going and coming back, and a launch that fails leaves the old record standing (found in review; the first version forgot the record first and lost its run log link if the launch then failed).
- **The suffix path is effectively retired.** At create the agent decides on tmux's own answer for an id nobody has a record of (live pane → refuse, dead pane → kill and reuse) rather than on whether the tick has adopted it, so `-2` is now reached only through tmux's own `duplicate session` verdict — and is then shown in the record's name.

Also landed: `previous_run` on the record; the refusal carries `holder`, `holder_state` and a `hint` as error data (`RpcError(**data)` → the envelope's `error_data` → `AgentError.data` → `ao new --json`); the name check is taken under a lock on the **scope**, because a scope spans a repo's worktrees; the Team's Shell button and `ao shell` both send no name, so the agent names them (`shell`, `shell-2`); and every CLI subcommand resolves a bare name here, with a live session winning over an exited one and ambiguity an error rather than a guess. The lasting content is design §4.1, §4.5a's name-field row, and the `ao` skill's note on names.

**Related:** design §4.1, §4.5a (New session name field), §4.7, §9 invariant 12, §10 (2026-09-10); PR #17 (resume supersedes), TD-023 (`pane` flag), TD-029 (the Close that prompted the report).

## TD-034: Derived report entries land on every record sharing a directory, including an exited predecessor

**Priority:** Low
**Added:** 2026-09-11
**Status:** Resolved 2026-09-12 (PR #86)
**Location:** `src/sessionorc/agent.py` (`_derive_reports_inner`), `src/sessionorc/reports.py` (`derive`)

**Why:** The Team showed `TD-030 done #77 (derived)` on **`ao-agentorc-tdgrind-ao-1-2`** — run 4's *exited* record — when run 5 (`…-1-3`) did that work. Both records carry the same `dir` (the `tdgrind-ao-1` worktree, reused run after run), and the tick derives from the *directory's current branch and PRs*, so every record pointing at that directory is credited with whatever is checked out there now. The declared entries on run 5's own record are correct and unaffected (§9 invariant 10 only protects against overwriting, not against a derived entry appearing where nothing was declared). It is a display lie of a specific kind: an exited worker looks as though it finished work it never saw, which is exactly the question the report channels exist to answer. It is *not* enough to skip exited records — TD-032's whole point is that a merged PR must still land on the record of the worker that has since exited, and TD-028 step 3 re-checks a `pending` PR by number for that reason.

**Resolved:** 2026-09-12 (PR #86) — attribute by *occupancy in time*, not by directory alone — derive the current branch's entries only for the record that currently holds the directory (the live one, or the most recently created when none is live), and keep the `pending`-by-PR-number re-check for everyone, since that is attributed by a PR the record already claimed rather than by what is checked out now. `reports.holds_directory` answers "who holds this directory now" over the whole record set and `_derive_reports_inner` passes the branch only for a holder; every record still gets its `pending`-by-PR re-check, and a record with pending entries and no branch is now derived for too, which it was not before. The lasting content is design §4.8 (the fourth honesty rule) and the two tests: `holds_directory` in `tests/test_reports.py`, and `test_derived_entries_go_to_the_record_that_holds_the_directory` in `tests/test_agent.py` — two records in one directory, one exited and one live, with a `tdNNN-*` branch checked out — only the live one gains the derived claim; the exited one keeps a claim it made earlier and still gains its `done` when that PR merges.

**Related:** design §4.8 (derived source), §9 invariant 10; TD-028 step 3 (PR #70, which introduced this), TD-032 (why skipping exited records is the wrong fix), TD-030 (one name, one session — which stops *new* same-name pairs, but these two predate it).

## TD-025: `tests/test_cli.py` flakes: a shell session's `idle` can take longer than the 6 s wait

**Priority:** Low
**Added:** 2026-09-10
**Status:** Resolved 2026-09-12 (PR #91)
**Location:** `tests/test_cli.py` (`wait_state`, `test_keys_reach_the_pane`, `test_shell_send_tail_status_kill_close`), `tests/conftest.py` (`wait_state`, `pane_line`), `tests/_agent_child.py`

**Why:** Two distinct flakes have been seen in this module. (1) PR #34's CI run asserted `│` in `ao status -v` before the tick had refreshed the record's tail — fixed in PR #42 by waiting for the tail first. (2) 2026-09-10, one failure in 65 local runs of the module (a Sonnet review was running the suite concurrently, three samscrape workers were on the box): `test_keys_reach_the_pane` — `ao-…-keys never reached idle: working []` after 6 s. A 150-iteration probe of the same create → idle path against a child agent on an idle box measured no start over 1 s, so it is load-sensitive, not deterministic. The shell adapter says `idle` only when `pane_current_command` is a shell name; `ao shell` starts the person's login shell (tmux `default-shell`), whose `~/.bashrc` on kmaster runs `lesspipe`, `dircolors`, bash-completion and a sourced secrets file — under load those foreground commands can plausibly hold `working` past the wait, and the empty tail fits (bashrc prints nothing). The other reading, the pane never appearing in the tick's snapshot, would show as `pane=none` in the new diagnostic. Related but separate: PR #52's CI (3.13 job only) saw tmux report a dead pane without its exit status for over 6 s; that assertion was dropped rather than waited on.

**Resolved:** 2026-09-12 (PR #91) — **the cause was the test suite's own stale-server sweep, not the shell, the load, or the agent.** The diagnostic this entry added did its job: every reproduction printed `never reached idle: working [] pane=none (not in list-panes)`, and one printed what the next call got — `tmux send-keys …: error connecting to /tmp/tmux-1000/ao-test-837a0473 (No such file or directory)`. The socket file was *gone mid-test*.

`_sweep_stale_test_servers` (session-scoped, autouse) globs `/tmp/tmux-<uid>/ao-test-*` at the start of every run and kills every one it can connect to — including the private server a **concurrent** pytest process is in the middle of using. Confirmed deliberately: start `tests/test_cli.py`, start `tests/test_agent.py` six seconds later, and the first run fails with exactly this signature (it did, first try; the same two runs pass every time after the fix). That is the flake's whole shape — a "load-sensitive" failure that only ever appeared when *another suite was running beside this one*: flake (2) of this entry was seen while a Sonnet review ran the suite, and both of TD-033's were one run of mine beside one of a reviewer's.

The fix keeps the sweep (killed runs really do leak servers) and gives it ownership: `private_socket_name` records the creating pid in `/tmp/ao-test-owners-<uid>/<socket>`, the sweep skips any live server whose owner process is still alive, and an unowned or orphaned one is killed and its bookkeeping removed as before — `test_the_stale_server_sweep_leaves_a_concurrent_runs_server_alone` in `tests/test_tmux.py` holds both halves. Evidence for the whole family: nine runs of `test_cli.py` + `test_ui.py`, three at a time, all green with only this change and none of TD-033's test-side waits (before it, three failures in nine).

Neither hypothesis in the Why was right, and both are worth keeping as a warning: the empty tail and the frozen `working` fit "a slow shell start-up" perfectly, and the box being loaded made a harness bug look like a timing one for two weeks.

**Related:** PR #34, PR #42 (fix 1), PR #52 (exit-status lag), `tests/README.md` real-environment notes.

## TD-033: Two load-sensitive flakes in the test suite, each seen once in a full run

**Priority:** Low
**Added:** 2026-09-11
**Status:** Resolved 2026-09-12 (PR #89)
**Location:** `tests/test_cli.py::test_explain_file_and_session`, `tests/test_ui.py::test_terminal_scrollback_reaches_tmux`, `src/sessionorc/agent.py` (`rpc_explain`, the screen verdict)

**Why:** Two different tests failed one full-suite run each while passing on their own and on the next run. Both read a live pane and assert on what it shows, so the likely race is the usual one for this suite (TD-025's shape): the assertion runs before the pane has painted what it looks for. Neither failing assertion's output was captured, which is exactly why this is a ledger entry and not a longer sleep — a sleep would hide the timing rather than wait for the thing being asserted, and there is no evidence yet about which read is early.

**Resolved:** 2026-09-12 (PRs #91, #89) — **the cause was the test suite's own stale-server sweep** (TD-025, PR #91): it killed every live `ao-test-*` tmux server at session start, including the one a concurrently running pytest process was using. That fits both occurrences exactly — this entry recorded one as "my run" and one as "the reviewer's", which is to say *two suites running at once*, and the reviewer's sweep killed my server (or mine theirs). Nine runs of the two modules, three at a time, are green with TD-025's fix alone and none of the changes below.

So the read-once diagnosis in the Fix above was wrong about these two failures, and worth keeping as a caution: every symptom fitted it — an empty screen, a state that never advanced — because a server that has been killed out from under a run looks exactly like a pane that has not painted yet.

The three test-side waits went in anyway (PR #89), as cheap insurance rather than as the fix, through one helper — `wait_screen` in `tests/conftest.py`, `wait_state` for a screen: re-read until what is being asserted on is there, bounded, with tmux's own pane line on a timeout.

- `test_explain_file_and_session` waits for the `screen:` section it asserts on. `idle` means the shell is the foreground process, which is true before its prompt has painted, so an empty screen there is a real (if rare) possibility on a slow box.
- `test_unseen_idle_until_focused` (a third test, not in this entry) waits for the sent command to reach the pane before waiting for the `working` it produces — `sleep 2` is only `working` for two seconds.
- `test_terminal_scrollback_reaches_tmux`'s two read-once assertions (`mouse on`, and the pane not being in copy mode) now wait, like the two `wait_for(mode() == …)` calls already beside them.

`tests/README.md` rule 2 carries the rule and the "run the suspect modules three at a time" recipe — which is how the harness bug surfaced in the first place.

**Related:** TD-025 (the same class and, as it turned out, the same cause; `wait_screen` came from this entry and is there for it), TD-015 (screen rules), design §4.2.

## TD-044: Unit tests read this machine's real `~/.agentorc`, so the suite broke the day the fleet got an `org.yml`
**Priority:** Medium
**Added:** 2026-09-13
**Status:** Resolved
**Location:** `tests/conftest.py` (the agent fixtures' `AGENTORC_HOME`), `src/sessionorc/paths.py` (`home`)

**Why:** `pdm run test` failed on kmaster with `test_cli_roles.py::test_ao_roles_lists_built_ins_and_the_repo_overrides_marking_the_source`: it asserts `orchestrator  [built-in]` and got `orchestrator  [built-in + org]`. Nothing was wrong with `ao roles` — the test was reading **Paul's own `~/.agentorc/org.yml`**, written the same day when the fleet became a team (TD-040). Only the *agent* fixtures set `AGENTORC_HOME`; a unit test that calls `agentorc.cli` directly sets nothing, and `paths.home()` falls back to `~/.agentorc`, so every unit test on this machine was reading live fleet state. CI never saw it (no `org.yml` there) and it would have gone on breaking every worker's suite silently — the suite is the only way a worker exercises the agent, so a test that depends on the operator's machine is worse than a failing one. CLAUDE.md's promise is "never the user's server"; this is the same promise for `AGENTORC_HOME`.

**Resolved:** 2026-09-13 (PR #123) — a session-scoped autouse fixture (`_never_this_machines_home`) points `AGENTORC_HOME` and `CLAUDE_CONFIG_DIR` at temp directories and clears `AGENTORC_SESSION` for the whole run. Session scope is deliberate: it is set up before the module-scoped `subprocess_agent` and the function-scoped `agent`, which then override it with their own temp home, so the tests that need a real agent home are untouched.

**Related:** TD-040 (the `org.yml` that exposed it), TD-025/TD-033 (the other "the suite must not depend on the machine" fixes), CLAUDE.md "Run the thing".

## TD-043: Concurrent `ao send` calls raced on one server-wide tmux paste buffer
**Priority:** Medium
**Added:** 2026-09-13
**Status:** Resolved
**Location:** `src/sessionorc/tmux.py` (`paste`)

**Why:** Reported by `orchestrator-ao-1` on 2026-09-11 (board item, now closed). `TmuxSession.paste()` always used the fixed buffer name `ao-paste` and pasted with `-d` (delete after paste). tmux buffers are per *server*, not per session, so the name was shared by every concurrent caller: four `ao send --wait` calls issued in parallel at 2026-09-12 00:21Z left one succeeding and three failing `TmuxError: tmux paste-buffer -p -d -b ao-paste -t =<session>:: no buffer ao-paste`, because the first paste had already deleted the buffer. That failure is the *benign* interleaving — the orchestrator verified afterwards that all three composers were empty, so nothing was mis-delivered. The one that matters was never excluded by the code: B's `load-buffer` landing between A's `load-buffer` and A's `paste-buffer` makes A's session receive B's prompt, silently and with no error anywhere. The orchestrator's workaround was to serialize its sends, which is a bound on one caller and not on the fleet.

**Resolved:** 2026-09-13 (PR #122) — the buffer name is `ao-paste-<pid>-<counter>`, unique per call, and a failed paste deletes its own buffer (`-d` already covers the success path). The test starts four sessions, pastes into all four from four threads, and asserts no error, that each session received only its own text, and that the server holds no leftover buffers; against the old code it fails with exactly the reported `no buffer ao-paste`.

**Related:** design §4.3 (the paste channel), TD-027 (the Enter after the paste), the board item of 2026-09-11 this closes.


## TD-045: A derived claim with no PR number was immortal — nothing in the system could retire one

**Priority:** Medium
**Added:** 2026-09-13
**Status:** Resolved
**Location:** `src/sessionorc/reports.py` (`derive`), `src/sessionorc/agent.py` (`_derive_reports_inner`), `src/sessionorc/models.py` (`ProgressEntry.branch`, `Session.retire_branch_claims`)

**Why:** Reported by `orchestrator-ao-1` on 2026-09-12 (board item, now closed), observed live: `ao-samscrape-tdgrind-1` carried `TD-395 claimed pr=null source=derived` from 16:33:38Z onwards while demonstrably working TD-396 — TD-395 was `tdgrind-3`'s. Three pieces of the derived source met: the checked-out branch yields `ProgressEntry(ref, "claimed", pr=None)`; the `pending` re-check that lets a merge land after the session moves on takes `(ref, pr)` **pairs** and its caller filters on `if e.pr`, so a PR-less entry never reaches it; and the upsert has no delete branch anywhere. Invariant 10 is not an escape hatch either — a derived entry is replaced only when the session *declares* the same reference, and unprompted declarations are rare (of five workers on the day, one declared on its own). So a `tdNNN-*` branch created and abandoned before its PR existed — which is what a grinder does the moment it finds a neighbour already holds that TD, i.e. correct behaviour — left a `claimed` entry that nothing could ever retire. Beyond a wrong card: the orchestrator brief's rule, and §6's idle-with-open-work policy when it lands, fire on a session `idle` with a lane item not `done`/`dropped`, so a permanent false `claimed` means a worker that has finished everything reads as having open work and gets nudged forever — the mirror image of the TD-026 gap the role exists to close, arriving as noise instead of silence.

**Resolved:** 2026-09-13 (PR #126) — the second of the two shapes the report proposed, chosen because the first (retire whatever the current derivation no longer supports) would have thrown away a real claim in the window between "the tick saw the branch" and "the PR was opened", which is five minutes wide and which this very session walked through. A derived claim now records the `branch` it came from. Once the session is no longer on that branch the tick hands it to `derive` as `left`, which looks it up one last time by branch name: a PR from that branch makes the claim real (merged makes it `done`), and no PR at all returns the reference in a third `retire` list that `Session.retire_branch_claims` deletes. That last look is its own `gh` query for that one branch (`_prs_for_head`), which reports *could not ask* distinctly from *no PR*: `_prs`'s "every failure is an empty list" is right for a source that only fills things in and fatal for one that deletes — an outage, a lapsed token or an offline laptop would otherwise have retired every branch-only claim on every session at once (found by the PR #126 review, which also named the second half: a PR older than the single page `_prs` fetches, a delay for the fill-in half and a deletion for this one). Asking by name closes both. The delete is narrow by construction — a `done` entry is a fact about the past, an entry with a PR is still watched by the by-number re-check, a declaration is untouchable (§9 invariant 10) — and it runs only for the record that still holds its directory (TD-034). A claim with no branch recorded is retired: those are exactly the entries written before the field existed, which are the immortal ones already on the records.

**Not fixed, deliberately:** the *attribution* in the reported case. `tdgrind-1` did have that branch checked out when the claim was derived, and if its PR later exists the claim is kept on both records. TD-034 settled attribution as occupancy in time; this entry is about immortality, and a claim that resolves to `done` at the merge is no longer immortal.

**Related:** design §4.8 (derived source, the `pending` re-check, the retirement), §9 invariant 10; TD-034 (attribution by occupancy), TD-032 (the idle-with-open-work nudge that fires on a stale claim), TD-028 step 3 (the derived source).
## TD-037: The mockups are a week stale: three controls that landed are undrawn, and the New session screen draws two controls that do not exist

**Priority:** Low
**Added:** 2026-09-12
**Status:** Resolved
**Location:** `docs/mockups/gen.py` (`row()` / `team_desktop()` card renderer, `focus()`, `new_session()`)

**Why:** `gen.py` was last touched 2026-09-05 and the UI has moved twice since. Undrawn but shipped: the card's **report line**, the Focus header **grants** chip, and the Focus **Reports** side panel (all landed 2026-09-12, `src/agentorc/ui/templates/card.html`, `focus.html`; §4.5a). Drawn but nonexistent: `new_session()` renders a four-way **Where** radio group with an *existing worktree* picker and a separate Fresh/Resume pair, while the shipped form (`src/agentorc/ui/templates/new.html`) has the two-option this-directory/new-worktree control and a plain Resume field — the picker was superseded on 2026-09-06 (§10, noted 2026-09-12) and §4.5a never carried it. That second half is the one that matters: mockups are what a person reads to learn what the product does, so a screen showing a control that does not exist teaches a false UI, and §4.5a's rule ("a control not in this table does not exist") cannot defend itself against a picture. The membership controls (§4.5a, TD-036) are correctly absent — they are proposed, not built — and should stay absent until they ship.

**Resolved:** 2026-09-13 (PR #127) — regenerated from `gen.py`, which now carries the team layer as data (`EXTRA`, `TEAMS`) rather than as markup: the **Org** rename through the tab strip, the titles, the artboard names and the prose; **team groups** with a header per team (name, lead and its state, project, needs-you count) with the lead's card first and *No team* last; the **Teams** strip with Start / Stop / Stop now per definition and its source; the card's **team** and **role** badges, its **under** chip and its **report line** (dashed when derived); Focus's **grants** and **controllers** chips, its **Reports** panel with **Drop**, and the **Members** list (labelled as the orchestrator-only panel it is); and `new_session()` redrawn field for field from `new.html` — Project picker, Role, Lane, Profile, Resume, the two-option Where with its worktree name, and the Controllers picker. The **Grants** checkboxes stay undrawn: they are the one §4.5a row still unbuilt (TD-028 step 5), and TD-036's membership controls that had not shipped are now drawn because they did.

**Not done here:** no rendered check. `shot.sh` needs a Chromium, and the session that did this pass had none — the artboards were verified by reading the generated HTML (tag balance, group order, the counts in the headers). Anyone with a browser should run `docs/mockups/shot.sh` once and re-seed the canvas.

**Related:** design §4.5a, §10 (the superseded existing-worktree picker); TD-036 (whose controls must *not* be drawn yet); `docs/mockups/README.md`.

## TD-048: The ledger's two files broke their own rules and nothing read them: a reused id, four lost bodies, two entries archived under someone else's text

**Priority:** Medium
**Added:** 2026-09-14
**Status:** Resolved
**Location:** `docs/technical_debt.md`, `docs/technical_debt_archive.md`, `tests/test_ledger.py`

**Why:** `technical_debt.md` states two rules about itself — ids are "assigned in order and never reused", and the summary table "lists exactly the entries that have a body in this file" — and nothing checked either, so both were broken for days without a symptom. Three separate failures had accumulated:

1. **A reused id.** TD-043 was assigned on 2026-09-13 to a tmux paste-buffer race (PR #122), resolved and archived the same day; hours later another session, reading the open file alone, assigned TD-043 again to a vocabulary entry. For a day the number meant two things, and the code, the tests and the board all pointed at the archived one.
2. **Four entries with no body.** Archiving TD-012 (PR #38) inserted its heading directly after TD-009's *heading* rather than after TD-009's body; PRs #39 and #40 stacked TD-013 and TD-007 on the same spot. The result was four headings in a row followed by four bodies under the last of them, and the metadata lines of three entries were dropped entirely.
3. **Two entries archived under someone else's text, and their real bodies left dangling.** TD-016 (PR #42) and TD-015 (PR #47) were each archived with TD-017's seen-state body pasted beneath their titles, so the archive described `send --wait` and the screen-rule manifests as a phone-triage seen mark — wrong in a way that reads as plausible, which is the dangerous kind. TD-017's body appeared twice; TD-017's own heading had none. Their **real** bodies were in the file the whole time, as two unheaded `Location`/`Why`/`Resolved` blocks dangling below TD-001, where reading top to bottom makes them look like a continuation of the entry above.

Every one of these is what a concurrent fleet does to a shared text file: the merges were clean, the conflicts were resolved by keeping both sides, and no reader ever compared the two files.

**Resolved:** 2026-09-14 (PR #138) — the duplicate TD-043 renumbered to TD-047 and moved into id order; the four stacked entries given back their own bodies and their `Priority`/`Added`/`Status` lines, recovered from `git show 872d6a8`, `59ce382^`, `272084e^` and `1a4fd31^`; TD-015's and TD-016's own bodies restored from the orphaned blocks under TD-001, which are the authentic text with their real PR numbers and test names — so no sentence in this repair is invented, and the orphans are gone rather than duplicated. `tests/test_ledger.py` enforces what the prose claims, in seven checks: no id used twice across both files, none open and archived at once, the summary table and the entries matching exactly, the open file in id order, every entry having a body of its own, **no two entries sharing a `Why` paragraph** (the wrong-body bug is structurally perfect otherwise — that duplicate prose is its only signal), and **no entry carrying a second `Location` line** (an unheaded entry dangling below it, which is how the two real bodies hid for five days).

**Related:** TD-047 (the renumbered entry); PRs #38, #39, #40, #42, #47 (where the damage was introduced), #122 (the first TD-043); cadence §2 (the archive rule).

## TD-047: The design has no word for "a message into a session that is already working"

**Priority:** Low
**Added:** 2026-09-13
**Status:** Resolved — vocabulary and wording only, no behaviour change. **Filed 2026-09-13 as TD-043 and renumbered to TD-047 on 2026-09-14**: TD-043 was already taken by a tmux paste-buffer race fixed the same day (PR #122, now archived), so the number pointed at two different problems for a day. Nothing outside this file referenced the vocabulary entry; every `TD-043` in the code, the tests and the board means the paste-buffer fix. See TD-048

**Location:** design §4.3 (prompt injection and the Send rule), §4.5 (the Focus composer), §4.5a (the control table), `src/agentorc/` wherever the composer's Send is labelled

**Why:** §4.3 gets the behaviour right and never names it. Send is disabled while a permission or question is pending (and, for scraped adapters, while a foreground process runs), and otherwise enabled — including while the agent is working, because a hook-fed tool like Claude Code queues input typed at it. So one button does two different things depending on the session's state: it starts a new piece of work, or it redirects work already in flight. The person cannot tell which from the button, and the design cannot say which in a sentence without a paragraph. OpenAI's Agents API (surveyed 2026-09-13, [ADR](decisions/2026-09-13-openai-agents-api.md)) names exactly this split: a session is durable, a **turn** is one cycle of work, a message to an idle session starts a turn and a message during an active turn **steers** it. That is the missing word, and it costs nothing to adopt — agentorc's states already carry the information the distinction needs (`working` vs `idle`/`needs-you`).

**Resolved:** 2026-09-14 (PR #142) — *turn* and *steer* adopted as design vocabulary. §4.3 says it in one sentence beside the Send rule ("to an `idle` session it starts a turn; to a `working` one it steers the turn in flight"), naming the Agents API as the source and saying why the distinction belongs in the words rather than in a second control: both are the same paste and the same Enter, and only the person's intent differs. §4.5's composer description and §4.5a's **Send** row carry it. The Focus composer reads **→ Steer** with "steers the turn in flight — this session is working, and Claude Code queues what you type" while the session is `working`, and **→ Send** with "starts a new turn" otherwise; the disabled cases (a pending permission, a question, an external session) keep the hints they had, `stalled?` steers (a `working` session that stopped producing output is a turn in flight, §4.2) and `limited` says the cap holds what you send rather than claiming a turn starts — both from the review, both cases where the first draft's hint contradicted §4.2 — and `exited`, `closed` and `unreachable` are newly excluded — the composer sat enabled and silent there before, which was useless rather than wrong, but "starts a new turn" at a killed session would be a lie, so the states that have no turn are taken out ahead of the branch that names one. Test: `tests/test_ui.py::test_the_composer_says_whether_send_starts_a_turn_or_steers_one`. Not driven in a browser — no session in this run has one.

## TD-040: Team and project definitions: where they live, `ao team start`, a role's `profile`, the Org page's grouped view, a project-aware New session

**Priority:** Medium
**Added:** 2026-09-13
**Status:** Resolved 2026-09-13 (every step, (a)–(e)) — vocabulary in [ADR 2026-09-13](decisions/2026-09-13-org-teams-projects.md); **design written 2026-09-13 as §4.9** (with rows in §4.5, §4.5a, §4.7, §4.8, §5, §9 invariant 9; the devcontainer question in §10 settled as a second host). Paul authorised stopping every running agent and the whole system on 2026-09-13, to restart once teams exist — so the TD-036 migration (its step 6) is superseded by `ao team start`, and step 4's `.agentorc.yml` loader is built here as the first code step. Code steps, each its own PR: (a) `agentorc/repoconfig.py`, the `.agentorc.yml` reader (§5: `roles`, `controllers`, `ledger`, `teams`, `worktrees`, `unattended` presence) with `ao new` reading `controllers:` and printing the no-controller line (closes TD-036 step 4) and `ao roles` (TD-028 step 5's loader half); (b) `agentorc/org.py`, the `org.yml` reader (projects, teams, roles) and `team`/`project` on the record — **landed 2026-09-13 (PR #115)**: the loader with every §4.9 default and validation, `merge_repo_teams` for a repo's own `teams:`, the two badges through `create`, `status --json`, `ao status -v` and `ao new --team --project`; (c) `ao team start|stop|status|list`, `ao new --project`, the role `profile`; (d) the Org page: rename, team groups, Teams strip, Project picker, team badge — **first half landed 2026-09-13**: the Team → Org rename (route `/` unchanged), the server-derived team groups with their headers on the page and on every `/events` delta, and the card's clickable `team` badge; the **Teams strip** and the New session **Project picker** wait on step (c), since Start / Stop are `ao team start|stop` and the picker needs `ao new --project`; (e) mockups regenerated (TD-037) once (d) ships **Step (a) landed 2026-09-13 (PR #116)**: `agentorc/repoconfig.py` (`RepoConfig`, `load`, `discover`, `PRESETS`, `resolve_role` with a `roles_overlay` hook for step (b)'s org layer — `org.yml` is not read yet), the three package brief templates under `agentorc/briefs/`, `ao new --role` and `ao roles`, the `controllers:` default (preset's, else repo's, names resolved in the session's directory), `role` and `ledger` on the record (the tick reads `ledger` for the derived source; `sessionorc` still never reads the file), the New session Role and Lane fields with the Controllers prefill. Left for later steps: `teams:` is parsed and passed through only; `ready_when`, `commands`, `worktrees`, `anchor` and `unattended` are parsed but nothing consumes them yet **Step (c) landed 2026-09-13 (PR #118)**: `agentorc/teams.py` (`plan`, `Launch`, the Project block, the one `WRAPUP_PROMPT` the card's Wrap up now imports from there) and `ao team start|stop|status|list` over the existing `name_check`, `create`, `send` and `kill` RPCs — no new RPC; the pre-flight refuses the whole start when any checkout is missing, any role, profile or brief fails to resolve, or any name has a live holder (§4.1), and an exited or closed holder is superseded, so a start after a night's exit is the restart; the lead is created first and each member with `controllers: [lead id]`, its role, lane, brief, profile and a worktree of its home repo; a `person` lead starts no session and members get an empty list; `ao new --project` adds the same reach block and the badge; the `profile` precedence chain (built-ins < `org.yml` `roles:` < the repo's `.agentorc.yml` < a member's `profile` < `--profile`) is wired through `resolve_role`'s `roles_overlay`, which `ao new` and `ao roles` read too. Left after (c): a `{team: …}` nested member is refused by name, not started; a repo on another host is a note in the Project block, not a start (phase 2); `ao team stop` waits on each member's state or a `--timeout` window (300 s default) because "wrapped up" is not a state the record carries; the team lead starts with no controller by deliberate choice; and step (d), the Org page (rename, team groups, Teams strip, Project picker), plus step (e)'s mockups, are untouched Follow-up 2026-09-13 (PR #119, while writing the first real `org.yml`): a **lead may name its own `brief`, `lane`, `grants` and `unattended`** like a member — an orchestrator's brief is the one a repo keeps its own copy of, and a `brief:` on a lead was previously read by nobody — and **a key nobody reads is now an error naming it** in a team, lead or member block, since silence about a typo is how that brief disappeared. **The fleet runs as a team since 2026-09-13**: `~/.agentorc/org.yml` defines `ao-grind` (orchestrator-ao-1 over tdgrind-ao-1, the repo's own briefs) and `samscrape-grind` (defined, not started — samscrape has no briefs of its own yet, board item 2026-09-13). The seven hand-started workers were killed and their records forgotten; `ao team start ao-grind` brought the two back with `controllers` set at create, which is what step 6 of TD-036 would have done by hand. **Step (d) complete 2026-09-13 (PR #124)**: the second half — the Org page's **Teams** strip (every definition from `org.yml` and from every registered repo's own `teams:`, with its source, projects, lead, member count and live count; **Start** on a stopped team, **Stop** and **Stop now** on a live one; collapsed to a line when nothing is defined) and New session's **Project** picker (a select of this host's projects, the repo list narrowed to that project's checkouts, the `project` badge, and the same Project block `ao new --project` puts in front of the brief). The shared sequence moved to `agentorc/teamrun.py` — `start`, `stop_members`, `stop_lead`, `rows` over an injected blocking `call` — which `ao team start|stop|list` now runs too, so the strip and the CLI cannot drift; the UI runs it on a worker thread, and the wrap-up wait (minutes) runs behind the response, the page naming the lead that follows while the `/events` deltas show the rest. A refused start creates nothing and comes back as the agent's own message in a toast. **Step (e) landed 2026-09-13 (PR #127)**, which closes TD-037 and with it TD-040: the mockups are regenerated on the Org page, the team groups, the Teams strip and the Project picker.
**Location:** design §4.5 (the home page), §4.5a (controls), §4.8 (role presets), §5 (configuration), §10 (the 2026-09-13 entry); later `src/agentorc/cli.py`, `src/agentorc/ui/`, the profiles and repo config loaders

**Why:** the ADR fixes the nouns — Org, Team, Project, Role, Agent — and what each is over the records that exist. It does not say where a team or project definition is stored, how a team is started as one action, or what the Org page shows when teams exist. Without those a "team" is a word on a page and the person still launches four sessions by hand and sets each one's controllers. Guardians is the forcing case: five repos, one lead, sessions that today run inside a devcontainer while ao launches tmux on the host.

**Resolved:** 2026-09-13 — every step, (a)–(e); the per-step record is in the Status line above. In short: `agentorc/repoconfig.py` and `agentorc/org.py` read `.agentorc.yml` and `org.yml`; `agentorc/teams.py` and `agentorc/teamrun.py` turn a definition into the sequence of creates that starts it, and `ao team start|stop|status|list` and the Org page's **Teams** strip run that same code so the two cannot drift; `ao new` gained `--role`, `--project`, `--team` and the profile precedence chain; the Org page gained the Team → Org rename, server-derived team groups, the team badge and the New session **Project** picker. Step (e), the mockups, landed as TD-037 (PR #127), which closed this. The fleet has run as a team since 2026-09-13: `~/.agentorc/org.yml` defines `ao-grind`, and `ao team start ao-grind` is the restart as well as the start (see TD-042 for what that demands of a brief). PRs #115, #116, #118, #119, #124, #127.

**Related:** [ADR 2026-09-13](decisions/2026-09-13-org-teams-projects.md); TD-036 (membership edge), TD-039 (two leads on one member), TD-037 (mockups need the Org page), design §4.5c (why the vocabulary is the workplace's and the claim is not).

## TD-041: §9 invariant 5 is convention, not a gate: nothing stops a controller acting on an interactive session

**Priority:** Medium
**Added:** 2026-09-13
**Status:** Resolved 2026-09-13, PR #114 — found by the independent review of TD-036 step 6, while fact-checking a migration doc that told a person the invariant was enforced
**Location:** `src/sessionorc/agent.py` (`_gate`, `rpc_set_controllers`), `src/agentorc/ui/app.py` (the controllers action), design §4.8 and §9 invariant 5

**Why:** §4.8 said "§9 invariant 5 still binds a granted session: interactive sessions are out of reach whoever the caller is". The code does not do that. `_gate` checks the `orchestrate` grant and membership and never looks at the target's `kind`; `rpc_set_controllers` will add an interactive session to a `controllers` list without complaint, and a `send`, `kill` or `close` from that controller then lands. Invariant 5 as written constrains *policies* (§6) — "never paused, killed, or nudged by a policy" — and the policy engine does not exist yet, so the sentence was true of a thing that is not built while reading as a guarantee about the thing that is. What kept it safe so far is that no interactive session has ever been in a `controllers` list, and the orchestrator briefs say not to; that is a convention, and the migration doc nearly shipped telling a person it was a boundary. The person's own anchor session is the obvious casualty: it sits in the main checkout of every repo, and an orchestrator that acquired it would be nudging the human's own session.

**Resolved:** 2026-09-13 (PR #114) — refused in the gate rather than in a brief: an acting RPC whose target is `kind: interactive` is refused whatever the caller's grant and membership, with a message naming invariant 5, and a person (no caller) is unaffected. `rpc_set_controllers` refuses one step earlier, so an interactive session cannot be put in a `controllers` list at all and the UI chip and `ao control` inherit the rule. §9 invariant 5 reworded to cover both policies and acting RPCs; §4.8's promissory sentence deleted.

**Related:** design §4.8, §9 invariant 5, §6; TD-036 (the membership this rides on); TD-039 (controller conflict).

## TD-051: Two finished entries sat in the open ledger, so "open work" counted two items that were done

**Priority:** Low
**Added:** 2026-09-14
**Status:** Resolved
**Location:** `docs/technical_debt.md`, `docs/technical_debt_archive.md`, `tests/test_ledger.py`

**Why:** `technical_debt.md` opens by saying "This file holds open work only" and cadence §2 says a resolved entry moves to the archive with its summary row deleted. TD-040 and TD-041 were both finished on 2026-09-13 and both still sat in the open file on 2026-09-14, their summary rows reading `Done 2026-09-13` — 21 rows of "open work" of which two were not. That is small and it is exactly the kind of error that compounds: a reader counting what is left gets the wrong number, and a session free-picking the next item can spend its first minutes discovering that the work merged a day ago. This session very nearly did on TD-029, whose remaining step is a browser check.

The reason it happened is worth recording: `TD-048`'s tests checked that the summary table and the entries match each other, which they did — both entries were consistently present in both places. Nothing checked them against the file's own opening sentence.

**Resolved:** 2026-09-14 (PR #143) — TD-040 and TD-041 moved to the archive with their summary rows deleted and their `Fix:` sections replaced by a `Resolved:` line recording what actually shipped (cadence §2); the open file is 19 rows and every one of them is open. `tests/test_ledger.py::test_the_open_file_holds_open_work_only` fails on any entry in the open file whose Status begins `Done` or `Resolved` — a *partly* done entry stays, which is what the Status field is for.

**Related:** cadence §2; TD-048 (the other ledger rules, and the tests this joins); TD-040, TD-041 (the two moved).

## TD-054: The card stop-note test fails for every run after 21:00 local

**Priority:** Low
**Added:** 2026-09-14

**Status:** Resolved

**Location:** `tests/test_ui.py::test_a_card_says_when_the_session_stops_and_only_then`, against `sessionorc.models.stop_note` (`models.py:157`)

**Why:** the test builds a stop time of `now + 3h` and asserts the card contains `f"stops {when:%H:%M}"`. `stop_note` prefixes the weekday once the stop is **not today** (`models.py:175`, deliberately — *stops Mon 06:00*), so from 21:00 local onwards the rendered text is `stops Tue 00:08` and the bare `stops 00:08` is not in it. The behaviour is correct and the assertion is the thing that is wrong; it simply never runs late enough in a working day to be noticed. It is a wall-clock dependency of the same family as TD-033 and TD-025, and it fails a clean checkout, so it costs a session the ability to tell its own breakage from the suite's.

**Fix:** either freeze the clock for this test, or pick an offset that cannot cross midnight from any start time (a stop earlier the same day, or assert against `stop_note`'s own output rather than a re-derived format string). Done when the test passes at 23:59 as reliably as at 09:00 — check by running it under a faked local time at both.

**Related:** TD-033 and TD-025 (the other load- and clock-sensitive flakes); design §6 / §4.5a (the **stops** note, TD-026), which is correct as built.

**Resolved:** 2026-09-16 (PR #158) — the test asserts `stops ` and the local `HH:MM` separately, as `tests/test_cli.py`'s version already did, and a second stop one day out pins the weekday prefix (`stops Thu 21:57`). Found blocking CI on PR #157 at 21:57 UTC (CI runs on UTC). Verified: the old assertion fails under `TZ=UTC` at 21:57; the new test passes under UTC, Etc/GMT+2, Etc/GMT-2 (23:57), Etc/GMT+10, Etc/GMT-11 (08:57) and America/Denver.

## TD-065: The `chip` class the controllers chips use is not defined in any stylesheet

**Priority:** Low
**Added:** 2026-09-17 (session `tdgrind-ao-1`, found while building TD-053 step 6 — looking for the class a new chip should use)

**Status:** Open — the defect is established by reading the files; **how wrong it looks is not**, because this session has no browser. That is the same bar TD-038 sets for its own remaining parts.

**Location:** `src/agentorc/ui/templates/card.html` (the *under `<controller>`* chips), `src/agentorc/ui/templates/focus.html` (the Focus header's controllers chips), `src/agentorc/ui/static/app.js` (which re-renders the same chips on a live delta and repeats the class), `src/agentorc/ui/static/app.css`

**Why:** both templates render the membership chips as `class="chip"` (`card.html`, the `s.under` loop; `focus.html`, the `fcontrollers` block), and `app.css` defines no `.chip` rule at all — its only four matches for the word are `#hostchip`, `#usagechip`, a comment, and a media query on `#hostchip`. The class does nothing. What those elements get instead is whatever their tag carries: an `<a>` on the card, a `<button>` in the Focus header — so the two halves of one control are drawn differently from each other *and* differently from every other chip on the page, all of which use `.badge` (`app.css`, the `.badge` rule: a bordered, rounded, 10px monospace pill, which is what the grants chip, the unread chip and the team badges are). `chip scraped`, for a controller whose session is gone, is worse than the same problem: `.scraped` *is* in `app.css`, but only as `.pill.scraped` and `.meta.scraped` — compound rules that need `.pill` or `.meta` as well, and the chip carries neither. So both halves of that class list are inert on this element, and the one visual cue that says *this controller's session is gone* — the cue the chip's own `title` promises — is not drawn at all.

It matters more than a Low priority suggests in one narrow way: design §4.5a's rows for the card *under* chip and the Focus controllers chip are the surface for **who may act on a session** (§4.8, TD-036), and a control that reads as bare text does not look like a control. Nothing is broken — the click handlers key on `data-act`, never on the class (`app.js`), so removing or restyling the class cannot break the behaviour.

**Fix:** either give `.chip` a rule, or — the cheaper and more consistent answer — use `.badge` on both, as every other chip on the page does, keeping `chip` only where a test or the JS needs a hook (neither does today; `app.js` selects by `data-act` and by id). Whichever is chosen, **three** places change together, not two: `card.html`, `focus.html`, and the line in `app.js` that rebuilds the Focus chips on a live delta — the card and the Focus header are two halves of one §4.5a row, and the page rewrites the second of them itself, so a fix that misses the JS looks right until the first state change. **Done when** the controllers chips are drawn as chips in both places, and a stylesheet rule exists for whatever class they name.

**Related:** design §4.5a (the card *under* chip and the Focus controllers chip), §4.8 / TD-036 (what the control is for), TD-038 (the terminal's look — the same class of "it works and reads badly", and the same reason this entry stops at reading the files: judging it wants a browser), TD-053 step 6 (the out-of-work chip, which used `.badge` for exactly this reason).

**Resolved:** 2026-09-20 (PR #259) — `badge controller` in all three places (`card.html`, `focus.html`, and the `app.js` line that rebuilds the Focus chips on a live delta), with `.badge.controller` and `.badge.controller.scraped` in `app.css`: the same bordered pill every other chip on the page is, and for a controller whose session is gone, dim plus the dashed `--scraped` outline `.pill.scraped` uses — which is design §4.5a's *shown dim, not dropped*, so the design needed no change. `tests/test_ui.py::test_every_class_the_controllers_chips_name_is_one_the_stylesheet_draws` holds the three sources and the stylesheet together, so a fourth place that grows a chip cannot name a class nothing draws. Still not judged in a browser — the bar the entry set for itself — but nothing here is behavioural: the click handlers key on `data-act`.
## TD-066: A `list` reply outgrew the client's line limit and every `ao` on the machine failed

**Priority:** High
**Added:** 2026-09-17 (anchor session, watching the samscrape team)

**Status:** Open — the immediate cause is fixed (PR #209: the client and the stdio bridge open their streams with the same 8 MiB limit the link uses). **The growth is bounded since 2026-09-18 (TD-052 step 6, PR #214):** read mail is kept 12 hours, a mailbox refuses past 100 unread, a thread's tally leaves with its last entry, and `list`, the stream and `wait` no longer carry `threads` or `wakes` (a `get` of one record still does). Left: a reply larger than the client can read should be a logged refusal at the agent rather than a silent failure at every client.

**Location:** `src/sessionorc/client.py` (`LINE_LIMIT`), `src/sessionorc/link.py` (`FRAME_LIMIT`), `src/sessionorc/mail.py` (the bounds that are `None` until TD-052 step 6: `MAIL_RETENTION`, `MAILBOX_DEPTH`, `THREAD_BOUND`), `src/sessionorc/models.py` (`Session.view()`)

**Why:** at about 20:00 MDT every `ao` command answered `ValueError: Separator is found, but chunk is longer than limit` and the Org page answered 500. One reply is one line, and asyncio's default line limit is 64 KiB; the `list` of six records had grown past it. The records themselves were 106–249 KB each: after seven hours of a four-session team with no retention, the lead's `outbox` held 173 KB and each grinder's `inbox` 77–107 KB. `view()` drops `inbox` and `outbox`, but what it keeps — `sends`, `threads`, `wakes`, `tail`, the reports — was enough across six records. The agent's server had been opened with an 8 MiB limit for the link (TD-057 step 3a); the client had not, so the agent wrote a reply nobody could read. Every session on the machine lost `ao` at once — the leads' rounds, the grinders' claims, the UI — for as long as it took a person to notice.

**Fix:** (1) done: `LINE_LIMIT` on the client's two connections, with a test that reads a `list` past 64 KiB. (2) TD-052 step 6 is now urgent rather than pending measurement: `MAIL_RETENTION` and `MAILBOX_DEPTH` bound what a record carries; until they are set a long-running team's records grow without limit, and a 249 KB record is rewritten to disk on every change. (3) `view()` should carry only what a card needs — `threads` and `wakes` are the host agent's bookkeeping and could leave the view, or be summarised — so a `list` grows with the number of sessions, not with their history.

**Done when** a team can run for days without any record or reply growing past a bound the design names, and a reply larger than the client can read is a logged refusal at the agent, never a silent failure at every client.

**Related:** TD-052 (step 5's measurement and step 6's numbers — this is the first measurement: what seven hours of a four-session team weighs), TD-057 step 3a (where the agent's limit was raised), TD-062 (the same shape: the agent newer than what talks to it).

**Resolved:** 2026-09-20 (PR #258) — the last half: the agent refuses its own oversize reply instead of writing a line no client can frame. A reply past `FRAME_LIMIT` is that request's **error**, against its own id and logged with the method (`_reply_line`, both reply paths); a session **view** past it is dropped from the subscription stream — one card, logged once, returning as soon as it fits — rather than ending every tab's stream; and `link.Mux._write` refuses its own oversize frame (a reply becomes that request's error and the link lives; a request raises `FrameTooLarge`, a `LinkError`) rather than leaving the reading end to end the link over a frame it cannot name. The rule is in design §4.4, the link's half in §4.4a *Frames*. The earlier halves: the client's 8 MiB `LINE_LIMIT` (PR #209) and the mail bounds that stop a record growing at all (TD-052 step 6, PR #214). Growth over a multi-day team run is now observation, not build — a record that still outgrows a bound is a new entry, and it would be a logged refusal rather than a machine-wide outage.

## TD-068: A brief over about 16 KB cannot start a session — tmux refuses the command line

**Priority:** Medium
**Added:** 2026-09-18 (the anchor's worktree session, launching TD-057 step 4b's worker)
**Status:** Open
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`launch`: the prompt is appended to `argv`), `src/sessionorc/tmux.py` (`new_session`), `src/agentorc/teams.py` (`_brief` → `Launch.prompt`), `docs/briefs/`

**Why:** `ao new --prompt "$(cat docs/briefs/td057-step4b.md)"` answered `TmuxError: tmux new-session ao-agentorc-td057-step4b: command too long`. The brief is 16,495 bytes; the largest that has ever launched is `orchestrator-ao-1.md` at 14,230. The adapter hands the whole prompt to the tool as one argv element and tmux caps the command it will take, so the limit is somewhere between the two and nothing says so until the launch fails — after `create` has already made the worktree (it stayed, with no record; the second launch reused it). `ao team start` has the same path: a role's brief plus a Project block is the prompt, so a team whose lead brief grows past the limit stops starting, all of it (§4.9: never half a team), with tmux's words rather than agentorc's.

**Worked around** for that launch with a short prompt telling the worker to read the brief from its own worktree, which works because a team session's worktree is cut from the repo that holds the brief — and is not a fix: a `brief:` override may live outside the checkout, and the Project block is composed, not a file.

**Fix:** pass a long prompt by file, not by argv. Options, cheapest first: (1) the adapter writes the prompt under the session's run directory and launches the tool with a one-line prompt naming the file (what the workaround did by hand), above a threshold well under the limit; (2) start the tool with no prompt and deliver the brief through `_submit` once the composer is up (the paste path `ao send` already uses, which has no such limit); either way (3) `create` refuses a prompt it cannot deliver **before** it makes a worktree, naming the size and the limit. Measure tmux's actual limit first (it is a build constant, not a setting) and pin it in a test.

**Done when** a 32 KB brief starts a session through `ao new --prompt` and through `ao team start`, and a prompt no path can deliver is refused by agentorc, in its own words, before anything is created.

**Related:** TD-042 (briefs that name one run), TD-067 (the operator's guide should say how long a brief may be), design §4.3 (adapters own the tool's launch), §4.9 (`ao team start` is all or nothing).

**Resolved:** 2026-09-20 — in two parts. **The delivery (PR #256, the anchor's):** past `LONG_COMMAND` (8 KB of the whole tmux command line, the environment counted in) `Tmux._fit` writes the argv to a launch script under the home, mode `0700`, that `exec`s it — so the pane's first process is still the command itself and the kernel's far larger limit is the one that applies; `tests/test_tmux.py::test_a_command_too_long_for_tmux_is_launched_through_a_script` carries a ~40 KB brief through it with its quotes and newlines intact. **The refusal (PR #272, a worker):** what no path can deliver is one argument past `ARG_LIMIT` (120 KB, under the kernel's `MAX_ARG_STRLEN`), and `create` now says so **before it makes a worktree** — the 2026-09-18 failure left one behind with no record pointing at it, which is the half of *done when* the delivery alone did not cover. Option (2) of the entry, delivering the brief through `_submit` once the composer is up, was not needed and is not built.

## TD-073: Usage is shaped like Claude Code's two windows — a second tool's quota has nowhere to go

**Priority:** Medium
**Added:** 2026-09-18 (the anchor session; Paul asked whether Claude, Codex, Grok and on-prem agents side by side break the top bar's usage display)
**Status:** Resolved
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`Usage`: `five_hour_pct`, `weekly_pct`, `five_hour_resets`, `weekly_resets`), `src/sessionorc/agent.py` (`_cap`, which loops over the literal keys `five_hour` and `weekly`), `src/sessionorc/adapters.py` (the `Adapter` protocol's `usage_for` docstring, which spells out the same five field names as the contract), `src/agentorc/ui/templates/base.html` and `src/agentorc/ui/static/app.js` (`onUsage`), which print those two fields by name

**Why:** design §4.3 puts tool-specific names inside the adapter, and usage is where that leaks. The adapter's `usage()` is per **profile** — an account of a tool — which is the right key, and a profile whose adapter reports nothing simply has no chip, so a shell, an on-prem model with no quota, or a tool with no usage endpoint costs nothing. But the *shape* of what is reported is Claude Code's: a 5-hour window and a weekly one. The core's cap check reads exactly those two keys, and the top bar prints exactly those two numbers. A tool with a daily window, a monthly credit balance, a token budget, or three windows cannot be represented, and its adapter would have to lie in Claude's field names to get a `limited` state at all. The chip also grows by one span per profile, unbounded, in a top bar with no room for six.

**Fix (as built):** the adapter reports a list, the core and UI iterate it: `windows: [{label: "5h", pct: 19, resets: <iso>}, {label: "wk", pct: 49, resets: <iso>}]`, labels chosen by the adapter. `_cap` becomes *any window at or over 100 whose reset has not passed*; the chip prints each window's label and number; a budget that is not a percentage is the adapter's to convert or to leave out. The Claude Code adapter keeps reading the same endpoint and maps its two windows into the list; the stored `_usage` and the `usage` event change shape together (one release with both, as the `orchestrate` → `control` rename did). For the top bar: the chip shows the **worst** window of each profile and the rest on hover, and collapses to the profiles at or near a cap when there are more than fits.

**Several providers in the top bar (Paul, 2026-09-19: should the chip rotate — *Claude window 5% week 18%*, then *OpenAI window 3% week 10%* — showing one when only one is in use?).** Agreed: **only profiles a live session is running under are shown**, so one tool in use is one chip, and each chip names its profile and carries its tool's mark. The anchor's recommendation against the rotation itself: a display that rotates hides a number at the moment it is looked at, and the one that matters — a profile near its cap — may be the one off screen. Instead: chips side by side while they fit; past that, the worst profile's chip and *+n* opening the rest; **a profile at or near a cap is always shown**. **Decided by Paul 2026-09-19: side by side, no rotation.**

**Done when** a test adapter reporting one daily window drives `limited` and shows in the chip without any Claude field name appearing outside `adapters/claude_code/`, and §4.3 and §6 describe the list.

**Related:** TD-001 (the usage chip), design §4.3 (adapters own tool-specific names), §6 (usage gate), §4.5a (the **usage** chip row), TD-071 item 8 (the label fix that prompted the question).

**Resolved:** 2026-09-20 (PR #279) — built as the **Fix** above says. The adapter reports
`Usage(windows=[Window(label, pct, resets)], fetched)` and the labels are its own; the Claude Code
adapter maps the same endpoint into `5h` and `wk`, so nothing changed for the one tool that reports
usage today. `_cap` (`src/sessionorc/agent.py`) iterates the list — any window at or over 100% whose
reset has not passed — and names no window of any tool; `src/sessionorc/adapters.py`'s protocol
docstring says the list shape. The chip (`base.html`, `app.js`, `app.css`) prints each profile's
**worst** window with the rest on hover, side by side while they fit and collapsing to `+n` past
that, a profile at or near a cap (80%) never the one collapsed — measured against the chip's
`max-width` rather than a hard-coded count. The core also prunes a profile no live session runs
under and sends `usage: null` to take its chip off the bar (*only profiles a live session is
running under are shown*). Design: §4.3, §4.4, §4.5a's **usage** row, §6's usage gate, whose config
example is now one percentage with an optional per-label override. Tests:
`test_limited_from_one_daily_window` (the *done when* — a stub adapter whose whole quota is one
window labelled `day`) and `test_usage_chip_prints_each_profiles_worst_window`.

**Not built here:** the usage **gate** itself is still design-only — nothing in `src/` reads
`usage_gate` — so §6's shape is a contract waiting for its policy, not a regression of this entry.
The `+n` collapse is browser-measured and was not exercised against a live top bar from an
unattended session: **merged, live check pending** on `docs/user_attention.md`.

## TD-082: The Inbox page needs a second design round

**Priority:** Medium
**Added:** 2026-09-20 (the anchor session; Paul, from the page at full width — his screenshot, and the anchor's own of the live page)
**Status:** Resolved — **designed 2026-09-20 (#280), built 2026-09-20 (#281)**: Paul picked mockup A (one centred column) over B (a grid); the design is §4.5 screen 6 *Layout*, the §4.5a row *Inbox: section heading, the **i** mark*, and the artboard `docs/mockups/Inbox.dc.html` (`gen.py` `inbox()`), which also draws TD-079's *Waiting on them*, the trail and the FYI count and TD-081's *Reopen and push* so the page step has one picture. Finding 4's three: the repeated name is settled here (printed once), the bare *Details* is TD-081's (*Reopen and push*, **Resume**, **Open**), and the `…` countdown is settled here (durations are server-rendered in words; the script only keeps them moving). Build it with TD-079 step 2 (same templates), by `tdgrind-ao-2`. Earlier: Paul's direction; design first (§4.5 screen 6, mockups), and **after** the builds already decided (TD-079's steps) unless one of these is in their way. Paul: *the design review work can wait if it makes more sense to have our ao team catch up on the designs already decided.*
**Location:** `src/agentorc/ui/templates/inbox.html` (the section heads and their blurbs, the footer paragraph), `inbox_row.html`, `src/agentorc/ui/static/app.css` (`.inboxpage .mailrow .body`: `max-width: 110ch`), `docs/mockups/gen.py`

**Why:** Paul's findings, each confirmed on a 1920-wide screenshot of the live page:
1. **The blurbs are always on screen.** Each section opens with a paragraph saying what it is (*Needs you*'s runs to two lines at full width) and the page ends with another. They are reference, read once: they belong behind a mouseover or a clickable info mark on the section's title, not above every row for ever.
2. **The word-wrap is wrong at full width.** A message's text wraps at a fixed measure — well under half of a 1900 px row — while the blurb above it and the controls below it run the full width, so the row reads as a narrow column in an empty box. Either the whole row shares one readable maximum, or the page itself is a centred column.
3. **Sections blend into their messages.** A section is a bordered box and its rows are unboxed text inside it; with one row it is hard to say where the head ends and the message starts. Paul: *the messages could probably be cards themselves, to be easily selected* — a row as a card (its own surface, a hover and a focus state), sections as plain headings above a list of cards.
4. **Seen on the same screenshot, for the same round:** a session named `push` shows as `push  push  [plain]  [No team]` (its name, then the tool's own title — a separate value that here is the same word; the row should not print a title that only repeats the name); the state row's one control is a bare *Details* at the far right (TD-081 gives it real answers); the steering row's countdown is filled in by script after load and is `…` until then.

**Done when** a design round (mockups first, then §4.5 screen 6 and the §4.5a rows it touches) has settled the four, and the page is built to it.

**Related:** TD-069 (the Inbox), TD-079 (the queue — its page step and this one touch the same templates: build that first, or fold this into it), TD-081 (the state row's answers), TD-071 (mockups), TD-076 (friendlier titles on the same rows).

**Resolved:** 2026-09-20 (PR #281) — the page is built to the round. **Finding 1:** each section's
paragraph is now the **i** mark's (§4.5a) — a `<button>` with `aria-expanded` / `aria-controls`,
labelled *About <section>*, whose `title` is the same text as the paragraph it describes through
`aria-describedby`; the paragraph is in the page always and merely `hidden`, opens in place under
the heading when pressed, and which are open is remembered in the browser. The page's closing
paragraph is gone into the blurbs it repeated, and inside FYI's `<summary>` the press does not also
fold the section. **Finding 2:** `.inboxpage` is one centred column at 1100 px and `.body`'s
`max-width: 110ch` is gone — the column is the measure. **Finding 3:** a section is a heading (no
`.card`), a row is a card with a hover state, a focus ring and `tabindex="0"` so it is a tab stop;
what says *what a row is* — kind mark, role badge, state pill — is flat and unbordered, and the
team badge keeps its border because it is a button. **Finding 4:** a state row prints the session's
name once (the title is dropped when it only repeats it), and every duration is rendered server
side by `_left` / `_countdown` in the exact words `fmtLeft` in `app.js` uses — the `…` is gone from
the steer countdown, the snoozed row and an alarm's instants, and nothing jumps on the first tick;
the two client formatters became that one function. Tests: four in `tests/test_ui_inbox.py`
(`…is_one_centred_column…`, `…i_mark_that_a_screen_reader_can_hear`, `…words_from_the_server…`,
`…prints_the_session_name_once`).

**Not built here, and said so in §4.5 screen 6:** the FYI **new** mark, which is compared against
**the FYI count** — that count is TD-079's (§4.5a *the FYI count*), so the mark lands with it. The
unpushed row's bare *Details* is TD-081's. The page step's other half, TD-079 step 2's row controls
in these same templates, follows this PR (agreed with `tdgrind-ao-1`, 2026-09-20: the page is one
worker's, the host agent the other's). **Live check pending** on `docs/user_attention.md`: how the
column reads at 1920 is what this round was about, and no unattended session may open the live UI.

## TD-074: A card's preview is the tool's chrome — the session should say what it is doing

**Priority:** Medium
**Added:** 2026-09-19 (the anchor session; Paul, during the look-and-feel pass — TD-071 item 8)
**Status:** Resolved — **approved by Paul 2026-09-19** with one change (below); the design landed the same day (§4.8 `doing`, §4.3 `title()`, §4.5a the **doing** and **title** rows, the role `icon:`), the build is next. **Steps 1–2 built 2026-09-19** (PR for branch `td074-ao-doing`): the `doing: {text, at}` field beside `out_of_work`, the ungated `doing` RPC only the session itself may write, `ao doing "<line>" | --clear`, the line in `ao status -v`, and the card's slot, the team header's lead line and the Focus header. **Steps 3–4 built 2026-09-19** (PR for branch `td074-title-icons`): the adapter's optional `title()`, `#{pane_title}` read with the pane list the tick already takes, the observed `title` field beside `tail` (out of the wake digest), the title beside the session's name on the card and in the Focus header and in `ao status -v` — matched by the filter, with no rename of agentorc's own — and `icon:` on `Role` with its fixed set, the built-ins (`lead: flag`, `grinder: wrench`, `hunter: search`) and the badge's picture. **Step 5 built 2026-09-19** (#239, below); **step 6, the mockups, 2026-09-20**. Steps: (1) `ao doing` — the RPC, the record field, the CLI, `ao status`; (2) the card's slot, the team header's lead line, the Focus header; (3) `title()` on the Claude Code adapter, `#{pane_title}` read with the pane list, the filter; (4) `icon:` on `Role`, the fixed set, the built-ins; (5) the briefs — this repo's and the package templates — say when to say it; (6) the mockups. Originally: design first: a new report channel is §4.8, the card's slot is §4.5 screen 1 and §4.5a, a role's icon is §4.8 *Role presets* and §5.
**Location:** `src/agentorc/ui/templates/card.html` (the `sc-slot`: `s.tail[-3:]` while working, `last: {{ s.tail[-1] }}` when idle), `src/sessionorc/agent.py` (`rpc_progress` and the report channels), `src/agentorc/cli.py` (`ao progress`), `src/agentorc/adapters/claude_code/__init__.py` (`composer()` already tells the tool's input line from its output), `src/agentorc/repoconfig.py` (`Role`), the briefs

**Why:** the card's slot shows the last three lines of the pane while a session works and the last line when it is idle. For a shell or a command run that is the right thing. For Claude Code it is the bottom of a TUI: on 2026-09-18 an idle worker's card read *last: ▸▸ bypass permissions on (shift+tab…* and another *last: new task? /clear to save 392.6k tokens* — the tool's chrome, never the work. The record already knows **which item** a worker holds (`ao progress claim`, the card's report line, *TD-431 → #923 · 10/11*), and nothing says **what it is doing about it**.

**Proposed, in the order the slot would prefer them:**
1. **What needs a person** — unchanged: the pending permission or question (hook channel, §4.2).
2. **What the session says it is doing** — a third report channel beside `progress` and `findings` (§4.8): `ao doing "<one line>"`, ungated like the others, bounded (one line, a couple of hundred characters, the last value only — not a log), stamped, shown with its age (*says · 11m ago*) so a stale line reads as stale. The briefs tell a worker to say it when it claims and when what it is doing changes; a lead says its round in the same channel, and **the team card's header shows the lead's line** — which is the lead reporting on the team without the lead being asked to narrate each member.
3. **The tool's own title, labelled as such** — Claude Code sets its terminal title to a short summary of the conversation, and tmux holds it (`#{pane_title}`; seen 2026-09-19 on a live pane as *✳ Error Checker*). An adapter method (`title()`), so the core names no tool (§4.3); rendered like every derived value, dashed and labelled (*the tool's own title · it has not said*). It is text a model wrote: shown, never acted on, never given a button (TD-071 item 8).
4. **The pane's tail** — for adapters where the tail is the work (`shell`, command runs), and as the last resort elsewhere, with the tool's chrome trimmed by the adapter that knows it.
Not proposed: **the lead describing each member** (second-hand, a token cost every round, stale between rounds), and **the member's latest mail as its status** (mail is addressed to someone and is about whatever it is about; a claim note is not a status).

**Layout per role: no; a role icon: yes, small.** A role is a label and *nothing keys on it* (§4.8 *Role presets*, §9 invariant 9) — a card layout chosen by role would be the first thing that does. The card already draws whichever channels are non-empty, which is what makes a lead's card differ from a grinder's and a hunter's without a rule saying so: a grinder fills `progress`, a hunter `findings`, a lead its round. If a role later needs to say which channel leads its card, that is an ordering hint in the preset, not a layout. An **icon** is only a label's picture: `icon:` on the role preset (`repoconfig.Role`, overridable per repo like every other key), chosen from a fixed set the UI ships (no free-form SVG from a config file), drawn small and monochrome inside the role badge — the state tile stays the one coloured thing on a card.

**Paul's change, 2026-09-19: the tool's title is a name, not a fallback.** *I set "Error Checker" so I would know the general purpose of that session* — the title is often the person's own, given with the tool's rename. So it is shown **always**, beside the session name, and the slot's order is: what needs a person, the `doing` line, the tail. It stays settable where it was set — in the tool; agentorc keeps no second name. Shells and command runs keep the tail. Approved as proposed: `ao doing`, no layout per role, the role icon. Also decided the same day, its own small change: the team card's **Stop** becomes **Wind down** beside **Stop now** (§4.5a).

**Step 5, 2026-09-19:** the package templates (`grinder`, `hunter`, `lead`), this repo's `tdgrind-ao-1` and `orchestrator-ao-1`, and `skill.md` say when to say it — a worker when it claims and when what it is doing changes, a lead its round — and to skip the command on an install that predates it. samscrape's team runs on the package templates, so it follows at the next promote. **contractmatch keeps its own briefs**: the same two paragraphs are that repo's sessions' to add, not this one's.

**Done when** a Claude Code worker's card shows what it said it is doing, with its age, and falls back to the tail; the tool's title is shown beside the name whenever there is one; the team header shows the lead's line; a role may carry an icon; the briefs say when to say it; and §4.8, §4.5, §4.5a, §4.3 and §5 describe all of it.

**Related:** design §4.8 (report channels, role presets), §4.5a (the card **report line**), §4.3 (adapter contract), TD-028 (the report channels), TD-071 item 8 (the look-and-feel pass and *no control parses agent output*), TD-069 (the Inbox, which would show the same line on a row).

**Resolved:** 2026-09-20 (PR #288 — step 6, the mockups) — `docs/mockups/gen.py` and its artboards now show what the page shows: the **doing** line with its age in the card's slot (before the tail, after what needs a person; a stalled card carries its screen-rule note above it), the **tool's own title** beside the session name on the card and in the Focus header, and the **role icon** inside the role badge, its paths copied from `src/agentorc/ui/icons.py` so the mockup cannot draw a picture the page does not have. Three defects the refresh turned up and fixed with it: `FocusOrc.dc.html` printed `{ICON["term"]}`, `{term}` and three more placeholders **literally** (doubled braces in an f-string that has no second pass); the card's `.sc-slot` was `height: 54px` where the page has `min-height`, so a wrapped doing line was clipped and a stalled note stacked on top of it; and the artboards still said `orchestrator` and `orchestrate` where TD-055 renamed them `lead` and `control` (session *names* are untouched — those are Paul's). The lasting content is design §4.8 (`doing`, role presets and their icons), §4.3 (`title()`), §4.5a (the **doing**, **title** and **report line** rows) and `docs/mockups/README.md`.

## TD-056: Reference leases — a claim is an advisory, timed reservation checked at claim, not a note to siblings

**Priority:** Medium
**Added:** 2026-09-16 (raised by Paul, adopting a lesson from mcp_agent_mail)

**Status:** Resolved — **designed and built 2026-09-17** (design §4.8 "A claim is a lease"; `rpc_progress`'s `force`, `_lease_holder`, `LEASE_TTL` = 12 h; `ao progress claim --force`, which sends `force` only when given — the first build sent it on every claim and so broke `ao progress claim` against the still-running pre-TD-056 agent until the fix the next day; `test_a_claim_is_a_lease_on_its_reference` claims one reference from two sessions in one moment and gets one grant and one refusal naming the holder). Decisions this made: a lease is held only by a *declared* claim on a *live* record (not exited/closed), younger than the TTL; derived claims neither hold nor are checked; a re-claim renews; references only — path globs are not built and wait for a case. **Remaining:** once the running host agent has been restarted onto this build, remove the at-claim `note` from the grinder briefs (below) — until then it is the only protection the live team has (asked of Paul on docs/user_attention.md, the 2026-09-17 restart line). Was: adopted as a direction in ADR `docs/decisions/2026-09-16-agent-messaging-prior-art.md` (lesson 3). The at-claim note it replaces now exists, as its stopgap, in the grinder briefs (`src/agentorc/briefs/grinder.md`, `docs/briefs/tdgrind-ao-1.md`; TD-052 step 4)

**Location:** design §4.8 (the `progress` channel and claims), §4.10 (TD-052 step 4's at-claim note, which this replaces); `src/sessionorc/agent.py` (the `progress` RPC); `src/agentorc/briefs/grinder.md`

**Why:** two workers on one team can pick the same reference. Today a claim is a `progress` entry on the claimer's own record, and TD-052 step 4 asks the grinder's brief to also `note` its siblings at claim — a rule a brief must remember, delivered as mail the sibling must read before it claims. mcp_agent_mail solves the same problem structurally: an advisory reservation on a path with a TTL, and a conflict returned to whoever reserves second. Paul, 2026-09-16: *"take lessons learned and improve on ideas from the other systems but continue to build our own."*

**Fix:** design first, in §4.8: a claim (`ao progress <ref> claimed`) checks every live record on the host for an unexpired claim on the same reference and, finding one, returns the holder rather than silently succeeding — advisory, so `--force` proceeds and says so; a claim carries a TTL renewed by the claimer's later `progress` on that reference and released by `done`, `dropped` or the record ending; what a *reference* is (a `TD-NNN`, a path glob, a PR) and how path globs overlap. Then build it on the `progress` RPC, and drop TD-052 step 4's at-claim note.

**Done when** two workers claiming one `TD-NNN` within a second of each other get one grant and one conflict naming the holder, with no brief text involved.

**Resolved:** 2026-09-20 (PR #291) — the last step, which waited on the running host agent enforcing leases. It does: with `tdgrind-ao-1` holding `TD-078`, `tdgrind-ao-2`'s `ao progress claim TD-078` answered, on the installed build and from a second live session — *TD-078 is claimed by ao-agentorc-tdgrind-ao-1 since 2026-09-20T20:43:08Z (a lease, design §4.8): pick another reference, or claim it anyway with `--force`*. It refused, named the holder, named when, cited the rule and offered the override. So the at-claim `note` to siblings (TD-052 step 4) is out of `src/agentorc/briefs/grinder.md` and `docs/briefs/tdgrind-ao-1.md`, replaced by what the lease actually does; design §4.8 records the change and the evidence. The lasting content is design §4.8 *A claim is a lease*, `rpc_progress`'s `force` and `_lease_holder`, and `tests/test_agent.py::test_a_claim_is_a_lease_on_its_reference`. **Not built, and still waiting for a case that needs them:** path reservations and how two globs overlap.

**Related:** ADR 2026-09-16 (messaging prior art); TD-052 step 4; TD-028 (the report channels); design §4.8, §4.10.

## TD-085: The Focus header outgrew its row — it does not wrap, so the name and the newest chips squeeze each other

**Priority:** Low
**Added:** 2026-09-20 (`tdgrind-ao-1`, from the TD-074 step 6 mockup redraw)
**Status:** Resolved
**Location:** `src/agentorc/ui/templates/focus.html` (`#fhead`, a `row gap`), `src/agentorc/ui/static/app.css` (`.row` — `flex-wrap` is only on `.wrap`)

**Why:** `#fhead` is one flex row with no wrap, and 2026-09-19 and -20 added three things to it: the tool's **title** (TD-074), the **out of work** chip (TD-053 step 6) and the **doing** line (TD-074), beside what was already there — back link, `host / repo / name`, state, the unattended toggle, the stop badge, team, role, grants, controllers, and on a `needs-you` session Allow / Deny with the tool and its command. Redrawing the Focus artboard at 1440 with all of it showed the failure shape: the session's path broke over four lines, the title was squeezed to nothing and the doing line never appeared. Without wrap a flex row shrinks its shrinkable children rather than moving anything to a second line, and the session's name is the most shrinkable thing there. **The artboard is not proof** — it is a copy of the same row at the same width, not the page — so the first step is a browser at 1440 and at a laptop's 1280.

**Fix:** the row wraps (`flex-wrap: wrap` on `#fhead`, which is what the mockup's Focus artboard now does and what the lead's artboard always did), or the header is split — the identity line above, the chips and controls below. The second is a design question for §4.5a, since it decides what a person sees first on a narrow window; the first is a line of CSS that stops the name disappearing. Whatever is chosen, the **name and its state must never be the things that shrink**.

**Done when** the Focus header at 1440 and at 1280 shows the session's name whole, its state, and every chip it carries, on a `needs-you` session with a title, a stop time, a team, a role, grants, a controller and a doing line.

**Related:** TD-074 (what was added), TD-053 step 6 (the out-of-work chip), TD-003 (the phone layout, which has the same row and less of it), design §4.5a (*Focus header*).

**Resolved:** 2026-09-20 (PR #292) — the first of the entry's two answers, which is the one that is
not a design question: `#fhead` wraps (`flex-wrap: wrap` with a `row-gap`, which is what the Focus
artboard draws), and the rule the entry ends on is made explicit in the CSS — **the name and its
state never shrink** (`flex: 0 0 auto` on `.title` and `#fstate`), while the two long derived
strings give way instead (`flex: 0 1 auto` with an ellipsis on the tool's title and the `doing`
line, both of which keep the whole of it on hover). A test pins all three rules and asserts that
every one of the things that crowded the row is still in it.

**Found because the line never fitted:** the Focus header's `doing` line read *rebasing #269 ·
says · 10m ago* — a stray dot, from a template that nobody had been able to read. It now matches
the Inbox row's `doing_line`, which writes *<text> · says 10m ago* on one line. (The **card** is a
third shape and deliberately so: it puts the text in its own block and *says · 10m ago* on the line
under it, because a card's slot is a column. Only the two one-line forms had to agree.) Fixed in
the same PR, with the shape pinned by the same test.

**Left to a person, on `docs/user_attention.md`:** the browser at 1440 and at 1280 that this entry
rightly calls the first step. No unattended session may open the live UI, and the artboard is not
proof. **The other answer is still open and is the anchor's:** splitting the header into an
identity line and a control line is a §4.5a decision about what a person sees first on a narrow
window, and the wrap does not foreclose it.

## TD-084: On a node, `ao status` says its home is unreachable without asking

**Priority:** Low
**Added:** 2026-09-20 (the anchor session; seen while sampling a node's calls for TD-077)
**Status:** Resolved
**Location:** `src/agentorc/cli.py` (`cmd_status`, the `hosts.is_node()` branch), design §4.4a (*on a node out of reach of its home*)

**Why:** `ao status` inside the contractmatch container printed *offline — contractmatch is a node of kmaster, which is unreachable: this host's sessions only; no mail, no org* while the home's journal showed the link **up** and the node freshly re-provisioned. `cmd_status` prints that line whenever the host is a node; nothing checks the link. What is true on a linked node is narrower — *this listing is this host's sessions only* — and the word *unreachable* sends a reader looking for an outage that is not there (it sent the anchor to the journal).

**Done when** the line says *unreachable* only when the node's agent reports its link down, and otherwise says what the listing is (this host's sessions; the org is at the home), with a test for each.

**Related:** TD-057 (nodes), TD-077 (where it was seen).

**Resolved:** 2026-09-20 (PR #294) — `cmd_status`'s node branch is `_node_status_line()`, which
asks. The `host` RPC has carried `home_reachable` since TD-057, so nothing new is reported and no
change was needed in `sessionorc`. **Three answers, one per state the agent can be in**, and the
third is the point: a `host` call that *fails* says the link could not be read and that whether the
home is in reach **is not known** — claiming an outage on a failed read is the very mistake this
entry is about. What is always true on a node is said in all three: *this listing is this host's
sessions only — the org and your mail are at the home*. The stronger sentence is kept word for word
for the state that earns it. The line stays on stderr, so `--json` is the records and nothing else.
Tests: `tests/test_cli.py::test_a_nodes_status_line_says_unreachable_only_when_it_is` (the three
states, and `--json` unpolluted) and `::test_the_host_line_is_drawn_only_on_a_node` — the home says
nothing, because its listing is the whole org's.

## TD-079: The Inbox is a queue — nothing leaves without an answer, and an answer is followed to its outcome

**Priority:** High
**Added:** 2026-09-20 (the anchor session; Paul, after a day with the Inbox page)
**Status:** Resolved — **step 2 (the page) built 2026-09-20** by `tdgrind-ao-2` (PR below): the fourth section *Waiting on them*, the counted rows for a `blocked` outcome and for a debt whose asker exited without reporting, a `done`/`dropped` outcome under the question it closes (its reporting `note` listed there rather than beside it), the trail in FYI, the top bar's second number with FYI opening itself and the *new* mark, **Dismiss** / **Dismiss all**, and the state-row Snooze on `stalled?` and unpushed work that step 1b's store made possible. **Step 3 (the briefs) built 2026-09-20** by `tdgrind-ao-1`: the worker templates (`grinder`, `hunter`), this repo's `tdgrind-ao-1.md` and `ao --skill` say that an answered question owes an outcome and name the command that settles it, that `--thread` is how to ask again rather than a second question out of nowhere, and — in the words the mistake was made in — that *"tell me if you want less"* is a `steer` and not a `note`; the lead templates (`lead`, `orchestrator-ao-1`) gain a round step that chases a member's debt and forbids reporting one **for** a member. One line of code went with it, because the instruction would otherwise have been unreadable: `ao status -v` now prints an `owed:` line beside the other mail marks, which is where a manager sees what it is chasing. **With that, every step of TD-079 is built.** Earlier: **the five parts accepted by Paul 2026-09-20** (*sounds good on the inbox rules*); **the design landed the same day** (§4.10 *The Inbox is a queue* and *Outcomes*, §4.5 screen 6, the §4.5a rows **Waiting on them** and **the FYI count, Dismiss all**, §4.7). **Step 1 is landing in pieces (worker `tdgrind-ao-1`, 2026-09-20): 1a — outcomes and the debt (this PR): `--outcome … --for`, `--thread`, `outcome` on the question, the debt's bound of ten, the line on every `ao` reply (and its node hint), `ao progress none` refused, the Ready to close row, `asker_gone` on a closed or forgotten asker. **1b (PR #269): the attention trail — `attention.json`, `t-` ids, the newest 100 for the retention window, coalesced by `{sid, kind, how}` and keyed by the record's **address** so a node's row trails too, the five-second floor with a person's own press exempt, `how` from the act that ended the row (allowed/denied/acknowledged by you, resumed — from `superseded_by`, so a node's resume says so too — forgotten, else *resolved*) — the state-row snooze (`attention_snooze`, per record and row kind, TD-069's open gap) and `inbox_dismiss` (a list of ids, mail and trail alike, an open question refused by name, a missing id skipped). `models.attention_kind` is a parity pair with the page's `state_kind`. The person's **Dismiss** ending an outcome debt — the one thing that needed both halves in one tree — is in 1b too, since 1a merged first: the entry is settled `dismissed` and the asker is told by a `system` note. **Step 1 is then complete.** Build steps: (1) the host agent — `--outcome … --for` / `--thread`, `outcome` on the question, the debt (not pruned, its own bound of ten, the line on every `ao` reply, `ao progress none` refused, the Ready to close row, `asker_gone`), `blocked` to *Needs you*, the attention trail (coalesced, five-second floor) and the state-row snooze beside it, `inbox_dismiss`; (2) the page — *Waiting on them*, the exited-without-reporting row, the trail in FYI, the second number, FYI opening itself, *Dismiss all*; (3) the briefs — report the outcome of every answered question before you exit, and the manager chases its members' debts.
**Location:** `src/agentorc/ui/app.py` (`inbox_sections`, `state_rows`), `src/agentorc/ui/templates/inbox*.html`, `src/agentorc/ui/static/app.js` (the FYI fold), `src/sessionorc/agent.py` (`_msg`, the person inbox), `src/sessionorc/models.py` (`MailEntry`), the briefs

**Why:** Paul, 2026-09-20: *when I read an inbox message it disappeared. Our inbox concept is maybe more like a queue — reading an item should never change it (or make it disappear); every item should require an answer of some sort (snooze and dismiss are answers). Also, if I give an answer, how do I know the work was completed — or do we just assume it gets done because we have a manager? Does work completed based on answers show up in FYI (it could show up in questions again if more direction is needed)?*

What had happened, as far as the anchor could establish: nothing was deleted — `person_inbox.json` still held all five of its entries, unread. The row that vanished was almost certainly a **state row** (*`orchestrator-ao-1` exited with unpushed work*): a state row is a live view of a record, so when Paul opened and resumed that session the state changed and the row left with no trace. And looking found something worse: **the five entries are `note`s from samscrape sessions of 2026-09-17 and -18, sitting in FYI — folded by default and counted nowhere — so they had very likely never been seen**, among them *the shared Python venv on kmaster is broken for every Claude…* and a report of an agentorc tool defect. A section that is closed and uncounted is a place for mail to be lost in.

**Proposed (each part Paul's to accept or change):**
1. **Nothing leaves without an answer.** Reading, opening, focusing or following a row's link never changes it. The answers are the row's controls — Reply, a suggested answer, *Go with it*, Snooze, Dismiss, Acknowledge, Allow / Deny — and nothing else removes a row a person has not answered.
2. **What resolves itself leaves a trail.** A state row whose state went away by some other road — the session was resumed, the permission was answered in the terminal, the work was pushed — moves to FYI as *resolved: <how>* and stays for the retention window, instead of vanishing. That needs the home to remember that a row was shown (a small list of resolved attention items), since a state row is otherwise derived and leaves no entry behind.
3. **FYI is counted, quietly, and opens itself when it has something new.** A second, smaller number beside the main one (*Inbox 1 · 5*); the section unfolds when it holds an entry the person has not yet had on screen; a `note` leaves only by **Dismiss** (or *Dismiss all*, with a confirm). The main number stays *what needs you*.
4. **An answer is followed to its outcome.** Once a person has answered a question, the asker **owes an outcome on the same thread**: `ao msg person --outcome done|blocked|dropped "<one line>" --for <ask id>` (the proposal said `--about`; the build made it `--for`, because `--about` is free text nobody checks) (a `note` that names the thread and says how it ended, with the PR when there is one) — or a new `ask` on the thread if more direction is needed, which returns to *Needs you* with the thread's history above it. The Inbox lists **answered, no outcome yet** with its age, and flags **the asker exited without reporting one**; the manager's brief gains chasing those for its members. Assuming it gets done because there is a manager is not enough: the manager sees its members' states, not whether the person's answer was acted on. Outcomes land in FYI under the question they close.
5. **The briefs** say: report the outcome of every answered question before you exit; an outcome is one line and a reference, not a narrative.

**Found by Paul on the page, 2026-09-20 — and its cause, found by the fact-check of the PR that recorded it:** *when an FYI is dismissed, it currently closes the FYI list — it should leave the list open.* `app.js`'s one click handler closes **the nearest `<details>`** around whatever `[data-act]` button was pressed — written to fold a row's *more ▾* menu after a choice — and an FYI row's **Dismiss** sits in no menu, so the nearest `<details>` is the FYI section itself (and, for **Unsnooze**, the snoozed list). The fix is to close only a `details.more`; it is a line, and goes in on its own. (The anchor had guessed at a page reload on the events socket reconnecting; that socket is not even opened on `/inbox`.) **And a lesson from the same hour:** the grinder put *I plan to build all three parts of TD-072 as one PR — tell me if you want less* to the person as a **`note`** — a `steer` in everything but its kind — so it sat uncounted in FYI while it started work. Step 3's briefs should say it in those words: *"tell me if you want less" is a `steer`*.

**Done when** the design says all of it, a row never leaves the Inbox except by a person's answer or by resolving with a trail, FYI cannot hide unseen mail, and a person can see for every answer they gave whether it was carried out.

**Fixed 2026-09-20 (#263), one line of this entry:** dismissing an FYI entry no longer closes the FYI list — the click handler folded the nearest `<details>` of any kind; it folds a `details.more` menu and nothing else.

**Resolved:** 2026-09-20 (step 3, PR #295) — every step is built: step 1a and 1b in the host agent (#267, #269), step 2 on the page (#287), step 3 the briefs (#295), and the FYI-dismiss line in #263. The lasting content is design §4.10 *The Inbox is a queue* and *Outcomes*, §4.5 screen 6, the §4.5a rows *Inbox row: state*, *Waiting on them* and *the FYI count, Dismiss all*, §4.7, and the role templates under `src/agentorc/briefs/`. **Merged, live check pending** on `docs/user_attention.md`: the page's four sections and the second number want a browser, `ao status -v`'s `owed:` line wants the promoted build, and the briefs take effect at the next `ao team start`. **Not built, by design, and waiting on TD-075:** an outcome that goes through a techlead before the person.

**Related:** TD-069 (the Inbox; its steps 3–4 and the state-row snooze are unaffected), TD-070 (suggested answers — an answer index is what an outcome refers back to), TD-072 (mail triage by sessions — the same idea from the other side), TD-075 (a go-between would owe outcomes too), design §4.10 (*What a person is asked*), §9 invariant 13 (mail is not durable: an outcome that must outlive the record is still a board line).

## TD-081: Resuming a session should take its own name back — and the unpushed-work row should offer *Reopen and push*

**Priority:** Medium
**Added:** 2026-09-20 (the anchor session; Paul: *I should not have to rename the session to reopen it — it should just pull its previous name*; and *let's add options on the message like "reopen session and push"*)
**Status:** Resolved
**Location:** `src/agentorc/ui/static/app.js` (the Details banner: *Resume this conversation* links to `/new?…&resume=<adapter id>` and carries the directory and the adapter but **not the name**), `src/agentorc/ui/app.py` (`/new`'s prefill), `src/sessionorc/agent.py` (`name_check`, `_supersede`), `src/agentorc/ui/templates/inbox_row.html` (the *unpushed* row: **Details** only)

**Why:** to resume `orchestrator-ao-1` on 2026-09-20 Paul had to **type a name**: the banner's link carries the directory and the adapter and no name, the form's name box arrives empty, and what he typed (`push`) became the new session — `ao-orchestrator-ao-1-push` — beside the exited record it was a resume *of*. Nothing refused the old name: the name check already answers `supersede` for a name an exited record holds, and a create under it replaces that record in place (`_name_verdict`, `_take_name`). The page simply never offers it. **Fix:** *Resume* prefills the old record's name, so the ordinary path is the superseding one — the resumed session takes the bare name, the old record is closed and its mail moves (§4.10 *Resume carries mail forward*) — and the form says that is what will happen. **And the row:** *exited with unpushed work* offers only **Details** today. Paul's *Reopen session and push*: one control that resumes the session under its own name with a fixed, page-written first prompt — *push your branch and open or update its PR; then report the outcome* — never text a session wrote; it is a person's act (a `create` with `resume`), and its result comes back as an outcome (TD-079).

**Paul, later on 2026-09-20 — two controls, not a prefilled form:** *when a session needs to be resumed, I should have a resume option that requires no input from me, and a "resume with changes" (or similar) that goes to the normal resume screen where name etc. can be changed if desired.* So: **Resume** is one press — same name, directory, adapter, profile, role and team as the record it resumes, no form — and **Resume with changes…** opens the New session form with all of those filled in. The design round settles what the one-press path does when it cannot be silent (the name is held by a *live* record; the directory is gone; the profile no longer exists): it falls through to the form with the reason shown, never a guess.

**Found by the design review, 2026-09-20 — build this first:** a create that takes the old name *and* resumes the old conversation replaces the record under the same id with an **empty** mailbox (`_take_name`), and `_supersede` — the only caller of `_move_mail` — looks for a *different*, `exited` record and finds none. So the fix Paul asked for would have silently dropped the resumed session's mail; §4.10 *a resume under the same name* is the rule, and it has a test before any button exists.

**Done when** resuming from the page needs no typing, the resumed session has its old name, *Resume with changes…* reaches the filled-in form, and the unpushed-work row can be answered from the row.

**Related:** design §4.5a (*Focus (exited / closed)*: the exited banner), TD-079 (outcomes; every row needs an answer it can be given), TD-080 (why this particular row was a false alarm), design §4.10 (*Resume carries mail forward*), §9 invariant 12 (names).

**Resolved:** 2026-09-20 (PRs #282, #290) — step 1 (#282) keeps a resumed session's mail when it takes its own name back (`_supersede` moves the entries from the record the name rule replaced in place; `tests/test_mail.py::test_a_resume_under_the_same_name_keeps_its_mail`); step 2 (#290) is the page's one-press **Resume**, **Resume with changes…** and the unpushed row's **Reopen and push**. The lasting content is design §4.5a (*Focus (exited / closed)*, *Inbox row: state*, *Resumable*) and §4.10 *Resume carries mail forward*. Whether it reads right in a browser is the live walk on docs/user_attention.md (item 4 of `tdgrind-ao-2`'s UI pass).

## TD-086: A promote ends every lead's `ao wait`, and the CLI then tells a session to start a host agent

**Priority:** Medium
**Added:** 2026-09-20 (the anchor session, from `orchestrator-ao-1`'s board item of the same day, which asked for the entry — a lead creates no work)
**Status:** Resolved
**Location:** `src/sessionorc/client.py` (`AgentUnavailable("host agent closed the connection")`, raised when a call's reply line is empty), `src/agentorc/cli.py` (the one `fail(..., 3, hint="start it with: agentorc-agent serve")`), `cmd_wait`; `src/agentorc/skill.md` (*exit 3: the host agent is down — stop, do not start one*)

**Why:** A promote restarts `agentorc-agent` (TD-062), and the anchor promoted eight times on 2026-09-20. Each restart closed the socket under every blocked `ao wait`; the lead's round saw `error: host agent closed the connection` and the hint *start it with: agentorc-agent serve* — three times that evening (20:34, 21:01, 21:05 UTC, each within seconds of a promote; the agent was `active` again at once, `NRestarts=0`). Two defects in one line. **The wait is lost**: a lead's wake channel is gone until its next round, by a routine act of the anchor's, and the more the team merges the more often. **The hint is wrong for a session and wrong in fact**: the agent was restarting, not down, and the `ao` skill forbids a session to start one — the CLI tells it to do the one thing its Never list rules out. The briefs' fallback (the `loop` skill) is what kept the cost to the tail of a round.

**Done when** (1) a connection that closes **mid-call** is told apart from an agent that cannot be reached at all: `ao wait` reconnects and re-subscribes for a bounded time (the unit is back in seconds) and returns what it would have — a lead does not see a promote; (2) where a call cannot be retried, the message says *the host agent restarted — run it again*, and the *start it with…* hint is printed only when no agent answers and **never to a session** (`AGENTORC_SESSION` set, or a session channel): a session is told to stop, as its skill says; (3) a test restarts a private agent under a blocked `wait` and sees it return on the next event. Design first if (1) changes what `wait` promises (§4.10 *wake budget*: a reconnect must not count as a wake).

**Related:** TD-062 (why promotes restart the unit), TD-058 (the agent stops in a second with a `wait` blocked — the other half of the same restart), TD-052 (`wait` as an RPC).

**Resolved:** 2026-09-20 (PRs #300, #303, #309) — (1) `client.wait_rpc` remakes a `wait` whose connection was made and then dropped, within a 30 s grace and on the caller's remaining timeout, and a reconnect decides no wake (#303; the remade count fixed to count connections, #309); (2) exit 3 asks again with one bounded `ping` and says *the host agent restarted under this command — run it again* when one answers, and the *start it with…* hint is printed only when nothing answers and the caller is not a session (#300); (3) `tests/test_agent_restart.py::test_a_wait_rides_out_a_restart_and_returns_the_change` restarts a private agent under a blocked wait. The lasting content is design §4.8 *Waking a lead* and `src/agentorc/skill.md`. The one half of (2) no CLI change can build — a session whose `AGENTORC_SESSION` is unset still gets the person's hint when no agent answers — is TD-089.

## TD-090: A `/compact` leaves a healthy session reading `stalled?`

**Priority:** Medium
**Added:** 2026-09-20 (`tdgrind-ao-1`, from the attention board item of 2026-09-14, which said no ledger entry held it)
**Status:** Resolved
**Location:** `src/agentorc/adapters/claude_code/hook.py` (`translate`, `STATE_EVENTS`), design §4.2 (the Claude Code state table)

**Why:** `translate()` mapped every `SessionStart` to `working`. Claude Code ends a compaction by firing `SessionStart` again with `source: "compact"`, and a manual `/compact` fires no `Stop` after it — so a session that was `idle` read `working`, and the liveness cross-check turned it `stalled?` at `STALL_AFTER` while it sat healthy at an empty prompt (seen live on `ao-agentorc-tdgrind-ao-1`, 2026-09-14). Auto-compaction is routine for long-running unattended workers, so every one of them would eventually read `stalled?`, and a doorbell (§4.10) is never rung at a `stalled?` session.

**Resolved:** 2026-09-20 (PR #325) — `SESSION_START_NOT_A_START = {"compact"}`: that `SessionStart` reports its session id and model and no state, so the session stays what it was — `idle` after a manual `/compact`, `working` through an auto-compaction mid-turn. Design §4.2's table carries the row. `tests/test_claude_adapter.py::test_a_compaction_is_not_a_start`. Live check pending on docs/user_attention.md (the board item it came from).

## TD-059: The rename search found neighbours building the same thing — read them

**Priority:** Medium
**Added:** 2026-09-16 (Paul, during the rename discussion held in samscrape session 746e1973 on kmaster)

**Status:** Resolved

**Location:** no code. The output is an ADR under `docs/decisions/` beside the existing `2026-09-12-orchestrator-membership-prior-art.md`, plus whatever TD entries the survey produces.

**Why:** the name `agentorc` turned out to be in use, and checking replacement names against PyPI, npm, crates.io and GitHub kept landing on projects in this exact niche — which is a finding about the field, not only about names. Two unrelated people took `agentboss` for agent-session tooling within three months of each other. None of these has been read for what it *does*, and at least one has solved problems that are open entries in this ledger. The projects, with how each was found:

- **`tallu-wonder/agentboss`** (Go, MIT, created 2026-07-30, pushed 2026-09-10, 0 stars) — *"One screen for every coding-agent session you have running — a persistent tmux-backed desk for Claude Code and Codex."* The closest neighbour: one tmux session per agent session, status from Claude Code hooks, everything needed to revive a session kept on disk. From its README alone it has things this project lacks or has open: **Codex as a second first-class agent** (status from transcript events plus the `notify` hook; the conversation id adopted by folder + start time because Codex reveals it only at turn end), a five-state status vocabulary (`working` / `needs you` / `finished since you looked` / `idle` / `stopped`) — *finished since you looked* is a state this project does not have, and bears on TD-032 and TD-049 — desktop notifications that jump to the session, per-session model / context / estimated cost, context that drops on a compaction record (**the 2026-09-14 board item: a `/compact` leaves a healthy session reading `stalled?`**), colored groups with an Archived shelf, `opt+W` to start a session in a fresh worktree, and fork-the-conversation (`claude --resume --fork-session`). What it does **not** appear to have is anything above the single person at one terminal: no lead/worker/director, no grants or controllers, no mail between sessions, no unattended teams, no multi-host, no browser UI. That gap is this project's claim to exist, and the survey should confirm it from the code rather than from a README's silence.
- **npm `agentboss`** (`2026hackathon/AgentBoss`, v0.1.4, 2026-06) — *"AI Agent collaboration analytics — become your AI agent's boss, not its babysitter."* Analytics over agent sessions, not control of them. Worth a look for what it measures: this project records progress and findings (TD-028) but reports nothing about them over time.
- **the project holding `agentorc`**, and **`agentdirector`** — Paul found both taken; neither has been identified in this ledger. Step one of the survey is to name them (registry, URL, what they are), since a project that chose the same name probably chose it for the same reason.
- Not in scope: `18682402476-svg/AgentBoss` (a Sui-blockchain agent arena), `ntnusky/shiftleader` (a dormant Puppet management dashboard — relevant only as the nearest name to `shiftlead`), the npm `directr` directive processor.

**Fix:** read each in-scope project's code, not its README — the README above is a claim about agentboss, and this entry's bullets restate it unverified. For each, record in one ADR: (a) what it does that this project does not, and whether that is wanted — each wanted item becomes its own TD entry, or a line on an existing one (TD-032, TD-049, the `/compact` stall item); (b) how it solved a problem this project also has, where the mechanism differs — status detection, session revival, the conversation-id handshake, compaction; (c) what this project does that it does not, stated as the sentence the README's first screen should say once the rename lands. Start from design §3, which already surveys ttyd, ccmanager, claude-squad, Vibe Kanban, agent-dashboard, herdr (measured, ADR 2026-09-10) and OpenAI's Agents API — none of the projects above is in it, so this entry adds to that table and does not redo it. Then widen the search past the names already tripped over: search GitHub for the *description* (tmux + Claude Code + sessions, agent orchestration, multi-agent desk), because the names found so far were found by accident and a survey scoped to them proves only that scope. State the search terms and the date in the ADR.

**Done when** the ADR exists, names every project read and the commit each was read at, and each wanted capability has a TD number; and the rename's README positioning has a sentence drawn from (c).

**Related:** TD-060 (the rename itself; [ADR 2026-09-16](decisions/2026-09-16-rename.md)), design §3 (the existing prior-art table), `docs/decisions/2026-09-10-herdr-spike.md`, TD-055 (the glossary rename: `lead` is already the decided word for the coordinating session, which is part of why `shiftlead` fits), TD-032, TD-049, TD-028, `docs/decisions/2026-09-12-orchestrator-membership-prior-art.md` (the earlier prior-art pass, scoped to membership).

**Resolved:** 2026-09-20 (PR below) — [ADR 2026-09-20](decisions/2026-09-20-session-desk-neighbours.md): the name holders identified (`agentorc` on npm is a hosted workflow-automation service, the GitHub repos of that name are unrelated; `agentdirector` is effectively `gabemahoney/agent-director`, which is in the niche; npm `agentboss` is after-the-fact transcript analytics), `tallu-wonder/agentboss` and `agent-director` read from their code, the search widened by description with its terms and date, and four more in-niche projects read. Wanted capabilities: TD-091 (a context gauge) and TD-092 (a notification when the page is closed); a line on TD-072 (a Stop hook that refuses to stop with unread mail); the `/compact` bug a neighbour had hit too was ours and is TD-090. Design §3 gained one row. The README sentence from (c) is in the ADR, with a pointer on TD-060, and lands with the rename.

## TD-087: The usage chip is empty because the usage endpoint rate-limits us, and the adapter says nothing

**Priority:** Medium
**Added:** 2026-09-20 (the anchor session; the chip never drew on the live page after TD-073's promote)
**Status:** Resolved — **items (1), (2), (3)'s first clause, (4) and (5) built 2026-09-20 (PR #307, `tdgrind-ao-1`)**: `usage_for` answers with a **reason** (`ok`, `rate_limited` with the endpoint's `Retry-After` when it sends a number, `no_credentials`, `no_profile`, `error`) instead of one silence, raised as `UsageRefused` from `usage()`; the host agent logs a change of reason **once**, not a line per poll; a 429 — and only a 429 — backs the poll off to its `Retry-After` or doubles to an hour, and any other answer returns to the cadence; the **last good reading is kept** with the reason beside it and **persists across a restart** (`usage.json`, `store.UsageStore`), so a promote no longer forgets it and polls at once; and `USAGE_EVERY` is five minutes rather than one, since the shortest window the endpoint reports is five hours — **and what that costs is written down** (§4.2): `limited` is read from the same poll, so a session that hits its cap shows it up to five minutes later rather than up to one, which the screen's half of the same rule covers within a tick and which is in any case five minutes added to a cap lasting hours. Tests: each reason from the adapter, a 429 with and without a readable `Retry-After` against a date form and a 500 and a network error, the backoff and its ceiling and its reset, the reading kept through a refusal, and the reading reloaded by a second agent. **The chip's words, 2026-09-20 (PR #333, `grinder-ao-2`)**: a refused poll's held reading is drawn dimmed with *· stale*, its hover saying when it was read and why the poll since failed; nothing ever held is *`<profile>` no reading*; the §4.5a usage row says so.
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`usage`, `usage_for`: every failure is `return None`), `src/sessionorc/agent.py` (the tick's usage poll, `USAGE_EVERY = 60.0`, `_usage` and `_usage_checked` in memory), `src/agentorc/ui/static/app.js` (`fitUsage`)

**Why:** After #279 the top bar showed no usage chip through three promotes. Read 2026-09-20 22:50 UTC with the `grind` profile's own credentials (present, unexpired): the OAuth usage endpoint answers **HTTP 429, `rate_limit_error`**. `usage()` catches every exception and returns `None`, `usage_for` passes it on, and the tick ignores anything that is not a dict — so *rate-limited*, *no credentials*, *no network* and *no profile* are one silence, in the journal and on the page. Three things keep it that way: the poll is **every 60 s per profile with no backoff**, so a 429 is answered with another request a minute later; `_usage` lives in memory, so **each promote forgets the last good reading and polls at once** (eight promotes that day); and anything else polling the same account's endpoint (tdgrind's own poller, the tool) spends the same allowance. The cost is more than a missing chip: `_cap` reads the same dict, so **a session that hits its cap is not marked `limited`** while the endpoint refuses us.

**Done when** (1) the adapter tells the core *why* there is no reading — a small structured result (`ok`, `rate_limited` with `Retry-After` when given, `no_credentials`, `error`), never prose — and the host agent logs a change of reason once; (2) a 429 backs the poll off (honour `Retry-After`, else double up to a ceiling) and a success resets it; (3) the last good reading survives a restart with its `fetched` time, and the chip shows it as stale rather than vanishing (a design line in §4.5a's usage row first: a stale chip is a new state of a mark); (4) `USAGE_EVERY` is reconsidered — five minutes is plenty for a five-hour window; (5) tests for each reason and for the backoff.

**Open PR, handed over 2026-09-20 (`tdgrind-ao-1`, wrapped up for the team's restart):** **#307** (the host-agent half, branch `td087-usage-says-why`) — the reason word, backoff on a 429 alone, the last reading held in `usage.json`, a five-minute cadence; and, answering the anchor's read of 00:51Z in its last commit, the first poll after a restart is **seeded from the held reading's `fetched`** (due at `fetched + USAGE_EVERY`, never sooner; a reading with no readable time is polled at once), held by `tests/test_agent_restart.py::test_a_restart_keeps_the_polls_allowance_too`. Rebased on main 2026-09-20; suite and lint green. `src/sessionorc`, so the anchor merges it. The chip's words are `tdgrind-ao-2`'s half and wait on it — **undone at wrap-up 2026-09-20, waiting on PR #307**: a stale reading drawn from the last good `windows`/`fetched` with the `reason` beside it, and the §4.5a usage-row line for a stale mark, in one PR.

**Related:** TD-073 (the windows and the chip), TD-001 (the poll), TD-062 (promotes restart the agent).

**Resolved:** 2026-09-20 (PR #307, the host agent; PR #333, the chip) — design §4.2 *Usage* (the reason word, the backoff, the held reading) and §4.5a **usage** chip (*a held reading goes stale, not out*); `usage_chip` in `src/agentorc/ui/app.py` and `AO.usageChip` in `app.js`, held to the same cases by `tests/test_ui_org.py::test_the_usage_chip_rule_is_the_same_in_the_page_and_in_app_js`.

## TD-089: A session whose `AGENTORC_SESSION` is unset is told to start a host agent when none answers

**Priority:** Low
**Added:** 2026-09-20 (`tdgrind-ao-1`, the half of TD-086 item (2) that entry could not build)
**Status:** Resolved
**Location:** `src/agentorc/cli.py` (the exit-3 path and its *start it with: agentorc-agent serve* hint), design §4.8a (the session channel), `src/agentorc/skill.md`

**Why:** TD-086 item (2) asked that the hint be printed **never to a session**, recognised by *`AGENTORC_SESSION` set, or a session channel*. The first signal is built (#300). The second is the host agent's own classification (§4.8a, by peer credentials and process ancestry), and it is unavailable in the one branch that prints the hint, because that branch is reached only when nothing is answering to be asked. So an `ao` run by a session with the variable unset — a reparented background job, a hook — still reads *start it with: agentorc-agent serve*, the one thing a session's skill forbids.

**Fix:** give the CLI a signal that does not need the agent — decided in §4.8a (below).

**Done when** an `ao` run from inside an agentorc session with `AGENTORC_SESSION` unset, against no host agent, is told to stop rather than to start one.

**Related:** TD-086 (archive), design §4.8a, TD-077 (the same classification).
**Resolved:** 2026-09-20 (PR #338, `grinder-ao-1`) — The signal is the **ancestors' start-up environment**. The launch sets `AGENTORC_SESSION` on the pane's first process, so on exit 3 the CLI walks its own `ppid` chain (64 hops) and reads each ancestor's `/proc/<pid>/environ` (`agentorc.cli._session_by_ancestry`). A hit means the session is told to stop. It chooses the sentence only and is never an identity. It lives in design §4.8a *With no host agent to ask*. A detached job that also lost its chain still reads as a person; the skill's Never list covers that case.

## TD-049: An orchestrator only learns what its members did on its own timer

**Priority:** Medium
**Added:** 2026-09-14 (raised by Paul)

**Status:** Resolved — **steps (1)–(4) landed 2026-09-14 (PR #145)**: design §4.8 gained a **Waking a lead** section (written before the code) and its orchestrator row now ends each tick in `ao wait` rather than a sleep; `ao wait [--timeout N] [--scope controlled|all]` blocks over the existing `subscribe` and needs no new RPC and no change to the agent's push path. The four decisions, made as the entry argued them: scope is the authority rule (the sessions whose `controllers` name the caller); the wake vocabulary is `state`, `exit_code`, the pending *question* (never a permission's countdown), `progress` (ref/status/pr), `findings` and `controllers`, with `last_output`, `tail`, `since`, `seen_at` and `git` deliberately excluded as tick-churn; it returns the changed records, so the tick's first `ao status` is free; and the missed-while-busy case is answered by a per-caller cursor under `~/.agentorc/waits/` compared against a **complete** snapshot — an ordinary `list`, not `subscribe`'s opening burst, which has no end marker and would have to be judged complete by timing it — so an event that fired mid-turn is still there on the next wait. A cursor that exists and cannot be read means *unknown* and wakes on everything in scope, never on nothing. A first wait records where it is and wakes on nothing. `src/agentorc/skill.md` carries it too, inside the 120-line budget that file keeps because it is context every session pays for. Recorded from the re-review and not acted on, because it costs one cycle rather than a wake: `_gone` is one global queue, so if a session is forgotten in the window between a waiter's `list` and its `subscribe` registration, and another connection's push drains that queue first, this waiter sees the disappearance on its *next* wait rather than this one. **Left: steps (5) and (6)** — the fallback interval and the short list of things a lead must still do on it regardless of events, and the `loop` / `ScheduleWakeup` instruction in every lead brief. **Since TD-052 step 3 (PR #171) `ao wait` is a thin call to the host agent's `wait` RPC**, with the same scope, vocabulary and cursor; steps 5–6 are unaffected. Both change how the live lead behaves on its next restart, so they want Paul to see the mechanism work first (board item). §4.5a is untouched: `ao wait` is not a UI control, and the Org page shows nothing about a lead's wake state. No design task remains before the rest of the code. **Filed as TD-047 in PR #140 and renumbered to TD-049 before merge**: the same number was taken on `main` while the PR was open, by the vocabulary entry above (itself renumbered from TD-043 the same day, see TD-048). The PR title and its commits say TD-047; nothing else references it. **Extended 2026-09-14 by design §4.10**: `ao wait` returns on new mail as well as on a member's state change (TD-052 step 3), since the wake and the mailbox are one mechanism — the cursor this entry built is what makes a message that arrived while the lead was mid-turn still there on its next wait, and mail needs no second answer to that question

**Location:** `src/sessionorc/agent.py` (`subscribe`, the per-subscriber last-sent map, `_push_changes`), `src/sessionorc/client.py`, `src/agentorc/cli.py` (a new blocking command), `docs/briefs/orchestrator-ao-1.md` (the tick), design §4.8 (the `orchestrator` policy row: *"read `ao --json status` on a cadence"*), §4.6, §4.9

**Why:** everything a lead knows, it learns by asking. Its brief is a tick every 10 minutes over `ao status --json` (`loop`, dynamic — and a quiet lead self-paces longer: on 2026-09-14 orchestrator-ao-1 was running a 30-minute heartbeat). So the lead is up to a tick stale on every event that matters: a worker marking `done` with a PR waits a tick for its cadence check, a worker that exited waits a tick for its restart, a `needs-you` permission waits a tick for its answer — and a quiet fleet pays for a poll that finds nothing, every tick, on the same usage budget as the work. Paul (2026-09-14): *"we would be better served adding a way for the workers to report changes to the orchestrators when work is finished ... we would keep the timer as a backup, but the orchestrators would be more responsive."*

The substrate is already there and unused by sessions: the agent's `subscribe` turns a connection into a stream of `{"event": "session", ...}` / `{"event": "gone", ...}` lines, with a per-subscriber map of what each has been sent (§4.6, TD-009). That is what the Org page's `/events` consumes. Nothing gives a **session** — a CLI process, not a browser — a way to consume it.

**Fix (design first, then code), the pieces that need deciding:**

1. **The lead blocks instead of sleeping.** `ao wait [--timeout N]`: a blocking command over the existing `subscribe`, returning as soon as something in the caller's scope changes, or empty at the timeout. A lead's tick then *ends* with `ao wait --timeout 600` in place of a `ScheduleWakeup` — an event returns in a second, a quiet window returns at the timeout, and that timeout **is** the backup timer. Nothing is sent into the lead's pane, so there is no interrupt semantics to invent and no keystrokes landing mid-turn.
2. **Scope: what wakes a lead.** The natural default is exactly what it may act on — sessions whose `controllers` name it (§4.8, TD-036) — so the wake and the authority share one rule. Decide the event vocabulary: state transitions (`→ idle`, `→ exited`, `→ needs-you`, `→ limited`), `progress` changes (a claim, and a `done` with a PR, which is what triggers the cadence check), a new `finding`. Explicitly **not** `last_output` moving, which is the false-liveness signal the board already carries.
3. **What it returns.** Decide whether `ao wait` hands back the changed records — so the tick's first `ao status` is free — or only *something changed, go look*. The former is one round trip and one prompt's worth of context; the latter cannot go stale between the two calls.
4. **A lead is not always waiting.** Mid-turn — running a cadence check, writing a board line — it is not blocked on anything, and events that arrive then must still be there when it comes back. So either the subscription is per-caller and resumable, or `ao wait` takes a **cursor** and returns immediately when anything changed after it. Decide which, and where the cursor lives across a tick; a lead that misses the one event it existed for is worse than a poll.
5. **The timer stays, and its interval can go up.** If events carry the urgent cases, the fallback poll is for what no record delta can see: a PR merged from a worker's branch, a new `docs/cadence-changes.md` entry, an agent restart that dropped the subscription, and the wrap-up window. Decide the fallback interval and, more importantly, the short list of things the lead must still do on it *regardless* of events.
6. **What it changes in the brief and the design.** §4.8's orchestrator row says "read `ao --json status` on a cadence"; if a lead waits, that sentence and the `loop` / `ScheduleWakeup` instruction in every lead brief change with it. §4.5a gains nothing unless the Org page shows a lead's wake state.
7. **Why not the literal reading — a worker sending its lead.** Recorded so it is not re-proposed: an acting RPC is gated on the **target's** `controllers` (§4.8, TD-036), so a worker acting on its lead needs its own id in the **lead's** list — and a lead deliberately starts with an empty one (§4.9), so the edge would have to be opened in the direction the design just closed; and `ao send` is send-keys into a pane, which for a lead mid-turn is an interruption, not a message. The worker already *declares* what matters through `ao progress` and `ao finding`. The agent — the one process that sees every record and already computes deltas — is the right thing to turn a declaration into a wake.

**A limit worth recording up front:** this makes a lead responsive to things that *happen*, and silence is not an event. The case that prompted the conversation was a worker sitting at an empty prompt after a manual `/compact` — it emitted nothing, so no wake would have fired, and only the fallback poll's stale-state rule catches it. Events shorten the tail on activity; they do not replace the timer's job of noticing absence. That is the strongest argument for keeping both, and for deciding (5) carefully rather than treating the poll as vestigial.

**Done when:** a worker marking `done --pr N` has its cadence check start within seconds rather than within a tick; a worker that exits is restarted within seconds; a quiet fleet wakes its lead only at the fallback interval; and a lead that was busy when an event fired still sees it on its next wait.

**Related:** design §4.8 (the orchestrator policy row, its cadence and its `progress` per tick), §4.6 (`subscribe`, the delta stream, one connection per subscriber), §4.2 (the states a wake would name), §4.9 (a lead's `controllers` are empty by design — the reason for (7)); TD-026 (scheduling inside the tick: a lead's schedule and its wakes are the same mechanism and should be designed together); TD-036 (the `controllers` edge the scope in (2) reuses).
**Resolved:** 2026-09-20 (archived by `grinder-ao-1`, from the repo as it stands). No code was left. Step (5) is design §4.8 *Waking a manager*, paragraph **The timer stays**. It lists what the fallback is for: a PR merged from a worker's branch, a new `docs/cadence-changes.md` entry, a subscription dropped by an agent restart, and a session gone quiet when it should not have. The interval is the manager brief's `ao wait --timeout 540`: the timeout *is* the fallback poll, one mechanism. Step (6) landed with TD-052 (PR #194, 2026-09-17, *a lead ends every round in ao wait*). The design's policy row, `src/agentorc/briefs/manager.md` and `docs/briefs/manager-ao-1.md` all end the round in `ao wait`, and the `loop` / `ScheduleWakeup` instruction survives only as the fallback when `ao wait` itself fails. The live manager has run that way since, which is the look the entry wanted Paul to have. One brief was never changed: `docs/briefs/director.md`, which says it is not yet launchable. That gap is now a line in TD-036.

## TD-094: `test_the_doorbell_rings_only_where_it_may` fails now and then on CI

**Priority:** Low
**Added:** 2026-09-21 (the anchor session)
**Status:** Resolved — was: seen three times on 2026-09-21, each on Python 3.13: on `main` at the merge of #346 and again at the merge of #353 (neither re-run), and on PR #348's branch after a merge of `main`, where the anchor re-ran the failed job and it passed — so that run now reads green in the history. It fails at `tests/test_doorbell.py:127`, `assert await wait_for(lambda: _has(agent, w, "SUBMITTED " + mail.unread_line(2)), timeout=6)` — a six-second wait for the second ring after the wrap-up flag clears. Not investigated: whether six seconds is simply too short on a loaded runner (the ring rides the tick and a quiet period) or the ring can be lost. The doorbell itself (PR #343, `src/sessionorc`) was merged by its author before the anchor read it and before its review comment was posted, so **the anchor's read of #343 is owed as well** — the same sitting as this flake.
**Location:** `tests/test_doorbell.py`, the doorbell pass in `src/sessionorc/agent.py` (TD-052 step 7)

**Why:** a test that fails one run in some number makes every red CI a question, and this one guards a thing that types into a session's pane.

**Done when** the cause is known and either the wait is made to follow the mechanism (as TD-088 did for the trail tests, by driving the tick) or the lost ring is fixed; and #343 has had its read.

**Related:** TD-052 (step 7), TD-078, TD-088 (the last two timing flakes and how they were fixed), TD-093.

**Resolved:** 2026-09-21 (PR #357, grinder-ao-1) — the ring was **lost**, not slow. `send` cleared `wrapup_at` before it typed. A tick in the gap before its paste showed read an empty composer, decided a ring (watermark advanced, wake charged) and pasted into the middle of the send, so the ring's line never showed as its own submission and no later stretch rang for mail the watermark had passed. Fixed with one typist per pane: `_submit` holds a per-session lock from paste to confirmed submit, and a ring skips a locked pane and holds the lock from reading the composer to its own submit (`src/sessionorc/agent.py`, `_typing`; design §4.10 *Idle: the doorbell*). `test_a_ring_never_types_into_the_middle_of_a_send` holds a send's paste back for six ticks and fails without the fix. The anchor's read of #343 was posted 2026-09-21 14:22 UTC.

## TD-088: A row that ends because its session exited is trailed as *resolved*, and two trail tests race the tick for it

**Priority:** Low
**Added:** 2026-09-20 (`tdgrind-ao-1`, from a CI failure the anchor saw on the docs-only PR #294, `test (3.12)` on `a825242`)
**Status:** Resolved
**Location:** `tests/test_attention.py` (`test_a_name_taken_back_does_not_hand_the_new_session_the_old_rows`, `test_a_name_taken_back_by_a_resume_says_resumed`), `src/sessionorc/agent.py` (`_note_attention`, `_trail_append`), design §4.10 *The Inbox is a queue* rule 2

**Why:** CI read `how='resolved'` where the test expects `'forgotten'`. It is not a timing artefact of the assertion — it is the tick doing its job. The `agent` fixture runs a live tick loop at 0.3 s; between the test's `kill` and its `create` a tick sees the record `exited`, whose `attention_kind` is `""`, so **the row ends there** and `_trail_append` writes it with no word to hand: `_attention_how` is empty, `superseded_by` is unset, and the fallback is *resolved*. `_take_name`'s `_attention_gone` then finds the slot already popped and says nothing. Reproduced on demand by putting one tick's worth of sleep in that window: the entry comes out `('…-w', 'question', 'resolved', 'which one?')` against the expected `'forgotten'`, which is the CI line exactly.

**Two halves.** The **test** half is this entry's family — a test that raced the loop instead of driving it — and is fixed: both tests **stop** the loop rather than slow it (`conftest.park_ticks`, which cancels the ticker and awaits it, so an in-flight tick is finished or cancelled before the test goes on) and take every tick they want by hand, so the name rule, not the clock, decides what the trail says. The first fix only lengthened the tick interval and slept past one period, which the review of PR #297 rightly called *a narrower window, not a closed one* — a real tick does tmux reads and `git` subprocesses, and nothing bounds those by two tick periods on a loaded runner. `HostAgent.serve` now keeps its ticker on the agent (`_ticker`) so a test can cancel it; the one line exists for that. The **word** half is real and is left open: the row a person was looking at ended *because the session exited*, and the home knows that — but the vocabulary §4.10 rule 2 builds has no word for it (*allowed / denied / acknowledged by you*, *resumed*, *forgotten*, else *resolved*), so it falls to *resolved*, which the design defines as **when the home cannot tell**. On the live system a person whose question a worker died holding reads *resolved*, as if it had sorted itself out.

**Fix (the open half):** decide the word in §4.10 rule 2 — *the session exited* is the obvious candidate, beside the three the design already lists as unbuilt (*answered in the terminal*, *pushed*, *the limit reset*) — then derive it in `_note_attention`, where the record's new state is in hand, rather than in `_trail_append`'s fallback. Worth deciding with those three rather than alone: they are one list, and each is a case where the home can tell and does not say.

**Done when** the trail says why a row ended for every ending the home can name, and *resolved* means only what the design says it means.

**Related:** TD-079 (the trail), TD-078 (the same family of test: a wait bounded on something other than the thing waited for), design §4.10 rule 2.

**Resolved:** 2026-09-22 (`grinder-ao-1`) — the test half by PR #297; the word half by design §4.10 rule 2, which now lists *the session exited* and *the session was closed*, derived in `_note_attention` from the record's own state (`agent.py`, `_ended_by`) and ranked below every word an act wrote; `tests/test_attention.py::test_a_row_its_session_ended_says_so`. The three other unbuilt words (*answered in the terminal*, *pushed*, *the limit reset*) are still the design's and not this entry's.

## TD-102: A held peer message is a menu the adapter does not see, and the briefs do not forbid the channel

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session, from a live observation on the samscrape-grind team's first run)
**Status:** Resolved 2026-09-22 — **folded into TD-064**, which already held the classifier half from 2026-09-17 and now carries the policy (refuse on an unattended launch, Paul's word) and the briefs half; nothing was built under this number. Two halves as written that afternoon. **(a) The screen rule.** Claude Code delivers a message from another session (its `SendMessage` peer channel) straight into the conversation only when both sessions run the same permission-mode class; otherwise it draws a *Held message from another session* panel with a two-item menu — *Deny — drop it and tell the sender it was declined* / *Deliver this message to Claude* — and waits. On 2026-09-22 at 13:39 MDT Paul's interactive samscrape session sent one to `manager-sam-1` (unattended, bypass mode), and `ao explain` read the manager as `idle (hook)`, *no screen rule matched*, with the menu on its screen: no hook fires for a menu, the composer was blocked, and the manager's rounds waited behind it until the anchor pressed Deliver (`ao keys … Down Enter`). Add a rule to `src/agentorc/adapters/claude_code/screen_rules.toml` beside `trust-dialog`: `any` the panel's heading, `all` the two options (the heading alone could be prose), `needs-you` with `pending: {kind: question, text: "held message from another session (deliver or deny in the terminal)"}` — a manager's brief already escalates a `question` rather than answering it, and §9 invariant 6 says the core never types a menu choice into a pane, so a person decides; the Focus header then says so instead of *idle*. **(b) The channel.** `ao --skill` (`src/agentorc/skill.md`) and the built-in briefs (`src/agentorc/briefs/*.md`) say nothing about Claude Code's own peer tools, and samscrape's first grinder briefs told members to *coordinate via ListAgents/SendMessage* — claims and mail have their own channels (`ao progress claim`, a lease that refuses a held reference; `ao msg`, read at the receiver's next round and marked by who sent it), and a peer message is held for a person whenever the modes differ, which for a person's session and an unattended one they always do. One sentence in the skill under the report channels, and in each built-in brief's rules: *never message another session through the tool's own peer channel; `ao msg` is the channel* — agentorc's own briefs got the sentence 2026-09-22 (this entry's PR), samscrape's were told the same day.
**Location:** `src/agentorc/adapters/claude_code/screen_rules.toml` (a), `src/agentorc/skill.md` and `src/agentorc/briefs/` (b); design §4.2 (screen rules), §9 invariant 6

**Why:** a modal that agentorc reads as *idle* is the TD-032 shape again — the resting state of a session waiting on a person, shown for a session that is not resting — and a manager blocked behind one runs no rounds, so nothing on its team is watched until a person happens to look.

**Related:** TD-032 (the Remote-Control stand-down, the same *idle under a modal* shape), TD-041 (interactive sessions out of reach), §4.10 (mail).

**Resolved:** 2026-09-22 (PR #430, folded into TD-064) — nothing was built under this number; the screen rule and the briefs' sentence were built under TD-064 by PR #431.

## TD-107: Three compatibility tables for a tool that has never been released

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session; the design review)
**Resolved:** 2026-09-22 (PR #440, PR #442) — design §4.8 (*Grants*, *Role names*) and §4.9 carry the lasting content.
**Status:** Resolved 2026-09-22 (grinder-ao-1). Step 1, PR #440: `ROLE_ALIASES`, `RESERVED_ROLES` and the `lead:` team key gone. Step 2: `GRANT_ALIASES`, `canonical_grants`, the `renamed_grants` rewrite on load and over the link, and the `orchestrate` choice and warnings in the CLI, `roles:` and `org.yml` gone; `orchestrate` is an unknown grant everywhere. The lasting content is design §4.8 (*Grants*, *Role names*) and §4.9.
**Was:** Partly done. **Step 1 (2026-09-22, grinder-ao-1):** `ROLE_ALIASES`, `RESERVED_ROLES` and the `lead:` team key deleted from `repoconfig.py` and `org.py`, the §4.8 *Role names* tables and §4.9's `lead` sentence from the design; `orchestrator` and `lead` are unknown roles, `lead:` a stray key. **Step 2, left:** `GRANT_ALIASES` in `src/sessionorc/models.py`, the `renamed_grants` rewrite on load and over a link (`agent.py` `_renamed_grants`), `canonical_grants`/`has_control`'s alias reading (`mail.py`, `ui/app.py`), the `orchestrate` choice and warnings in `cli.py`, `repoconfig.py` and `org.py` (`deprecated_grant`), and design §4.8's `control` sentence — a `src/sessionorc` PR, so the anchor merges it. Was: the renamed-roles table (`ROLE_ALIASES`: `orchestrator → manager`, `lead → manager`), the retired-words table (design only, no code yet), the reserved-words table (`RESERVED_ROLES`, empty), `GRANT_ALIASES` (`orchestrate → control`) and the `lead:` team key still read as `manager:` (`org.py`, `deprecated_team_key`) — each with its one-line-per-process warning. "For one release" has no meaning yet: there has been no release and one user. Fix: delete the aliases and the tables, let an unknown role be an unknown role and an unknown key an error, and drop the paragraphs from §4.8 and §4.9. A record badged `orchestrator` or `lead` keeps its badge as text, since nothing keys on it. The project rename (TD-060) will want the same clean cut, and should not inherit these.
**Location:** `src/agentorc/repoconfig.py` (`ROLE_ALIASES`, `RESERVED_ROLES`; the retired-words table of §4.8 is design only and has no code), `src/sessionorc/models.py` (`GRANT_ALIASES`), `src/agentorc/org.py` (`lead:`), design §4.8 *The names*, §4.9

**Why:** compatibility code is a promise to a user base that does not exist yet, and each table is a rule the briefs and the fact-checks have to know.

**Related:** TD-055, TD-076 (the renames), TD-060 (the project rename).

## TD-104: A seat's trigger is timed and counted by the manager, against the design's own rule

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session; the design review)
**Resolved:** 2026-09-22 (PR #459, TD-103 slice 3) — design §6 *Keeping a team running* rule 3 and `src/sessionorc/agent.py` (`_seat_pass`, `_count_seats`) carry the lasting content.
**Status:** Was: **Folded into TD-103 (design 2026-09-22): §6 *Keeping a team running*, rule 3** — `ao team start` writes `seat: {trigger}` on the seat's record, the tick computes `seat_due: {at, by}` (from `asks_waiting`, the derived reports tick's `gh` count of PRs merged to the repo's default branch since the record was created, or the clock), the card draws the count from it, and the fill is the tick's; the build is TD-103 slice (3). Kept open only until that slice lands.
**Location:** design §4.9b (*Seats with a trigger*), §4.9a (*Inside the ceiling*), `src/sessionorc/agent.py` (the tick), `src/agentorc/briefs/manager.md`

**Why:** the design contradicts itself on where a clock lives, and the cheaper answer is also the one it already argued for.

**Related:** TD-098 (seats), TD-103 (policies on the tick), TD-075 (the techlead seat).

## TD-076: `lead` becomes `manager`, the go-between is `techlead`, and the bare word `lead` is retired

**Priority:** Medium
**Added:** 2026-09-19 (the anchor session; decided by Paul the same day, from TD-075)
**Resolved:** 2026-09-22 — design §4.8 (*The names*, *Role names*) and the glossary carry the lasting content; step (5), a retired-words table after the alias release, was superseded by TD-107, which removed the aliases with no release and decided there is no such table.
**Status:** Resolved 2026-09-22 (grinder-ao-2, archiving): every step built — (1)–(4b) as below, the *Waking a manager* sweep finished by PR #438, and (5) superseded by TD-107.
**Was:** Partly done — **designed 2026-09-20 (the anchor)**: the glossary (*manager*, *techlead* reserved, *lead* retired, *director* kept) and design §4.8 *The names* — the role and its two aliases, the team key `manager:` with `lead:` read and both-present refused, the refusal that stays after the alias release, `techlead` reserved until TD-075, the display `label:`, and session names as **new sessions, not renamed ones**. Build order: (1) **the sweep — done 2026-09-20 (the anchor)** — of `docs/design.md`'s running text (*lead* → *manager*; dated history keeps its word) — the anchor, one PR, timed between the team's design PRs since it touches some two hundred lines; (2) the role, the aliases (**`orchestrator` repointed to `manager`, not left pointing at `lead`** — the table is one lookup, never a chain, and a record badged `orchestrator` on a running session must not resolve to a word this same step retires), the team key, the reserved word, `--json` saying `manager` and the design's preset table and config examples with it — `repoconfig.py`, `org.py`, `teamrun.py`, `teams.py`, `cli.py`, `briefs/lead.md` → `manager.md`, `team_skill.md`, `skill.md` — **done 2026-09-20 (PR #334, `grinder-ao-1`)**: the preset, both aliases straight to `manager`, the `manager:` key with `lead:` read and both refused, `techlead` refused by name in `--role` and in either `roles:`, `ao team` and its `--json` saying `manager`, the model (`ManagerDef`, `TeamDef.manager`), the briefs and skills, and the design's preset table and config examples. **Kept on purpose**: the default session name `<team>-lead` (§4.9) — changing it renames the manager of a running team that relies on the default, so it is the anchor's to change with a restart, or to keep; the page's own words and its `/api/teams/*` response keys, which are (3)'s; and internal identifiers (`Plan.lead`, `Launch.lead`, `Stopping.lead`), which nothing outside the code reads. It fixed one bug on the way: `/api/teams/<t>/start` answered `lead: <id>`, and the page's shared start/stop handler ran `watchStop` on it, so it toasted *<team>: <id> stopped* five seconds after every Start; (3) `label:` and the pages (role badge, team header, Inbox rows, `def_lead`) — tdgrind-ao-2, after (2) — **done 2026-09-20 (PR #337, `grinder-ao-2`)**: `label:` in both `roles:` layers with the built-ins' *Manager* / *Grinder* / *Hunter* and the name-raised default (an old badge read through the renamed-roles table), `ao roles` printing it, the badge / team header / Inbox row showing it, and the page's own *lead* words and `/api/teams` keys saying *manager*; **(4a) done 2026-09-20 on Paul's word (*restart under the new names; you can act as director for now*)**: the repo's briefs are `manager-ao-1.md`, `grinder-ao-1.md` and `grinder-ao-2.md`, the names inside them changed and the manager's and the first grinder's say once (the second grinder reads the first's in full) that *lead* in `ao`'s output means the manager until step 2; the team was stopped under its old names and started under the new, with `org.yml` still on the `lead:` key, which is what the code reads — **(4b) done 2026-09-20 (the anchor), after steps 2 (PR #334) and 3 (PR #337) were promoted**: `org.yml` says `manager:` and `role: manager` for all three teams and `manager:` under `roles:` — the two one-line warnings `ao team status` printed are gone — with the running team untouched, as designed (the old file is `org.yml.bak-2026-09-20-lead-key`; the contractmatch and samscrape definitions keep their own session names and briefs, which are those repos' to change). What is left of this entry is step (5), the retired-words table, after the alias release — **and one small sweep nobody was given**: step 1 renamed the design's heading *Waking a lead* to *Waking a manager*, and the comments and docstrings that cite it by its old title still do (`src/agentorc/cli.py`, `src/sessionorc/client.py`, `paths.py`, `waits.py`, `models.py`, `agent.py`, `docs/briefs/manager-ao-1.md`; the landed TD-052 one-shot briefs are history and stay) — a free-pick for a grinder, the `src/sessionorc` half merged by the anchor. **The `src/agentorc` half is done 2026-09-20 (`grinder-ao-2`)**: `cli.py`'s two comments and `tests/test_cli.py`'s section heading say *Waking a manager* (and *round*, *team*, where they said *tick*, *fleet*); what is left is `src/sessionorc/` (`client.py`, `paths.py`, `waits.py`, `models.py`, `agent.py`) and `docs/briefs/manager-ao-1.md`, both the anchor's. **The `src/sessionorc` half is done 2026-09-21 (`grinder-ao-1`, PR #353)**: the five files cite *Waking a manager*, and `client.py`'s and `models.py`'s comments say *manager* where they said *lead*; `docs/briefs/manager-ao-1.md` was the last, done 2026-09-22 (PR #438, `grinder-ao-1`), so the sweep is complete. As first written: (4b), the key and `role: manager` in `org.yml`, waits for step 2 and needs no restart (the key is read at start and at status). The anchor directs the manager with the person's hand: it is not a session, so it is nobody's controller. (4), as first written: the repo's own briefs renamed (`docs/briefs/manager-ao-1.md`, `grinder-ao-N.md`) and `~/.agentorc/org.yml` on the new key and names — the anchor, **with the team stopped and on Paul's word**, then `ao team start`; the old records are his to forget. (5) after the alias release: `lead` and `orchestrator` leave the renamed-roles table for a **retired-words** table that refuses them by name, for good (design §4.8 *The names*) — unscheduled, and not TD-055's *delete the aliases*. (2) and (3) are safe to land under a running team — the aliases are what make them so; (4) is the only step that needs the restart.
**Location:** `docs/glossary.md` (*lead*, *director*), `src/agentorc/repoconfig.py` (`BUILTIN_ROLES`, `ROLE_ALIASES`), `src/agentorc/org.py` (the team definition's `lead:` key, `lead: person`), `src/agentorc/teamrun.py`, `src/agentorc/cli.py` (`ao team`), `src/agentorc/briefs/lead.md`, `docs/briefs/`, `src/agentorc/ui/` (the team header's lead pill, `def_lead`), `~/.agentorc/org.yml`, design §4.8 and §4.9

**Why:** what today's lead does is manage its members' lifecycle — start, nudge, wrap up — and Paul wants the word *lead* for the technical go-between of TD-075. The anchor's objection was to **re-assigning** a live word, not to either meaning: `lead` is a role name, the team definition's structural key, a field `ao team` prints, the glossary's *director > lead > worker*, and the `role` badge on every record started since 2026-09-17 (#188). A rename leaves old data readable through an alias, as `orchestrator → lead` does today; a re-assignment would make a record badged `lead`, and a team's `lead:` key, silently mean the other session.

**Decided:** the lifecycle role is **`manager`** — the role, the brief, the team key (`manager:`, with `manager: person` for a team a person runs); `lead` and `orchestrator` stay as **aliases that resolve to `manager` and warn**, for one release, as TD-055 did. The go-between is **`techlead`**, shown as *Tech lead*. **Bare `lead` is never given a new meaning.** Whether *director* keeps its name (its members become managers) is for the glossary round.

**Decide it with the project's name (2026-09-20).** The rename ADR (`docs/decisions/2026-09-16-rename.md`, merged 2026-09-20 as #173; TD-060) leads with **`shiftlead`**, and part of its case is the vocabulary of 2026-09-16: *the session which coordinates a team's workers is the **lead*** — *a shift lead does not do the work: they assign it, check in* — *`director > lead > worker` reads naturally under it*. Under this entry that session is the **manager**, bare `lead` is retired, and the only lead-like role (`techlead`) does a different job — so the project would carry *lead* in its name while no role is a plain lead. Four of the ADR's five tests (free on the registries, spellable, searchable, not *agent* + an authority word) are untouched; the fifth — *fits once explained* — is the one this weakens, since the explanation offered is *the metaphor is the job*. Neither rename starts without the other in view: either `shiftlead` stands as the product's purpose rather than a role, or the project's name pulls the role vocabulary back toward `lead`. Paul's to decide.

**Confirmed by Paul, 2026-09-20, together with the project's name (TD-060): `shiftlead` for the project, `manager` and `techlead` for the roles.** Not a free-pick item: it changes the glossary, §4.8, §4.9, a structural key of every team definition and the live `org.yml`, so it is **the anchor's, design first**, and a team that is running when it lands has to be restarted onto the new names.

**Friendlier titles (Paul, 2026-09-20): *let's make our agent titles more user friendly, like "Manager" and "Grinder" where appropriate.*** Part of this rename's design: a role preset gains a **display label** (`label:` — *Manager*, *Tech lead*, *Grinder*, *Hunter*; default: the role's name, capitalised), which is what the role badge, the team header and the Inbox rows show in place of the bare key; and **session names in the team definitions** stop encoding history — `orchestrator-ao-1` becomes `manager-ao-1`, `tdgrind-ao-1` becomes `grinder-ao-1` — so a card reads *Manager · ao-grind* and not *orchestrator-ao-1 lead*. The tool's own title (§4.5a **title**) follows the session name, so it changes with it. Nothing keys on a label (§9 invariant 9).

**Done when** the glossary, §4.8 and §4.9 say `manager` and `techlead`; `org.yml`, the built-in roles, the briefs and the pages use them; a definition or record that still says `lead` or `orchestrator` loads, resolves to `manager` and says so once; and nothing in the repo uses bare `lead` for the go-between.

**Related:** TD-075 (the go-between), TD-055 (the last rename and its alias), design §9 invariant 9 (nothing keys on a role — which is why this is cheap in code and costly only in words).

## TD-113: `ao team --skill` sends a second repo to samscrape's files

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session; from the dev-cadence session standing up `dc-grind` cold from the recipe, and samscrape's the same day)
**Status:** Resolved 2026-09-23 — was: Partly done — **(0) and (a) built 2026-09-22 (grinder-ao-2)**: the manager preset leaves the crash restart to the tick and boards `restart_ceiling`; its wanted restart is `ao new --supervised` until rule 2 is on the tick; `{manager}` is filled by `ao team start` with the id the manager takes (the start says so should it come up under another, `none` for a person or a hand start), and the built-in grinder's `done` line uses it. **(b) and (8) written 2026-09-22 (grinder-ao-2)** in `team_skill.md`: the list-shaped lane and its order, which file a team belongs in, `.agentorc.yml`'s keys (held to `repoconfig._apply` by a test), the primer's four checks in words, ignoring `.claude/worktrees/`, and the placeholders a start fills — all but (b)'s first sentence, *what the built-in briefs assume and when a repo needs its own*, which TD-114 changes to *what a supplement holds*. That sentence and (c), the skeleton, are written with TD-114 step (1) (PR #463, *A repo's brief* in the recipe); (9) waits on TD-103 slice (3). Was: Open. Two repos in one day followed `ao team --skill` and had to learn the same things from `~/samscrape/docs/briefs/` instead of the recipe: **(1)** the built-in briefs (`src/agentorc/briefs/*.md`) assume this repo's layout — `scripts/check_cadence.py`, `pdm run test`, `docs/design.md`, the never-list — so a repo with another gate, layout or standing rules needs its own briefs (samscrape's README records that lesson: a team *defined and deliberately never started* for that reason); **(2)** step 3 shows `lane: free-pick` only, while a named lane is a YAML list rendered by `Role.brief_text` as `TD-041, TD-054, …` and worked in order; **(3)** no skeleton says what a brief must contain (first reads, where it works, the gate, the lane loop, asking, standing rules, the ending declarations) — writing one is a 2,000-word port instead of a fill-in; **(4)** the primer's *held to its pointers by a test where the repo can* names `tests/test_primer.py` and not the four checks it makes (every design section, invariant, path and ledger id the primer names exists), so a repo without Python tests writes its own blind; **(5)** the org.yml examples do not say which keys `.agentorc.yml` takes (`roles`, `controllers`, `ready_when`, `commands`, `teams` — never `projects`; `repoconfig._apply`) or give a criterion for team-in-repo against team-in-org beyond *travels with the checkout*; **(6)** there is no `{manager}` placeholder (`repoconfig.py` fills `{lane}`, `{techlead}`, `{context}`), so every grinder brief carries a paragraph telling the member to read `under:` off its record — the launcher knows the manager's id when it fills a member's brief (`teams.plan`, the manager's `create_params` come first), so filling `{manager}` removes the paragraph. **(7)** the crash-restart line the same feedback raised is answered by TD-103 slice (2) — the tick restarts from the launch record — and **the built-in manager preset's crash-restart paragraph is now stale for the same reason**: `src/agentorc/briefs/manager.md` still sends a manager down `ao new … --prompt "$(cat brief)"`, which ships `{lane}` and `{techlead}` literally; dev-cadence's manager brief had the same defect and replaced it the same night (its PR #96: *the host agent restarts; watch `restart_ceiling` and board it*). **(8)** `ao team start` in a repo that does not gitignore `.claude/worktrees/` leaves embedded repos that `git add -A` in the main checkout stages as gitlinks (dev-cadence, 2026-09-22, live): the recipe should say so, or `ensure_worktree` could check the ignore and warn. **(9)** the built-in manager preset's seat-fill rule (`ao new --keep-mail`, same name, directory, worktree, profile and brief) cannot fill `{context}`: `ao new` has no context flag and `cli.py` line 368 calls `role.brief_text(lane)` with neither `techlead=` nor `context=`, so a hand-refilled techlead seat reads *none* for its primer and `techlead.md` then treats the team as having no primer, which is false for any team that defines one (dev-cadence's fact-check of its PR #96, 2026-09-22). Only `ao team start` fills it. The fix is TD-103 slice (3): the seat fill on the tick replays the launch record, whose `prompt` is the brief as filled, so no client fills anything; until then a manager's hand fill is wrong for any team with a primer.
**Location:** `src/agentorc/team_skill.md` (steps 3–4, the primer paragraph), `src/agentorc/repoconfig.py` (`MANAGER_PLACEHOLDER` beside `TECHLEAD_PLACEHOLDER`, `brief_text(manager=…)`), `src/agentorc/teams.py` (`plan` passes the manager's id), `src/agentorc/briefs/grinder.md`, `docs/briefs/README.md`

**Why:** the recipe's *done when* is a fresh session standing a team up without reading design.md (TD-067); two sessions did, and both went to another repo's files for the half the recipe leaves out.

**Resolved:** 2026-09-23 (grinder-ao-1): every part landed. (0) and (a), the manager preset's crash restart left to the tick and `{manager}`, in #458. (b) and (8), the recipe's list lane, `.agentorc.yml` keys, the primer's checks and the `.claude/worktrees/` ignore, in #460. (b)'s first sentence and (c), *A repo's brief* with its skeleton, in `src/agentorc/team_skill.md` by TD-114 step (1), #463. (9) is gone rather than fixed: the tick fills a seat by replaying its launch record, whose prompt is the brief as filled (TD-103 slice (3), #459, live since the 2026-09-23 promote). The built-in manager preset no longer tells a manager to fill by hand (TD-103 slice (5), #471). The lasting content is `src/agentorc/team_skill.md` and design §6 *Keeping a team running*.

**Related:** TD-067 (the operator's guide), TD-103 (the crash restart on the tick), TD-075 (the primer), TD-109 (the briefs README).

## TD-032: An unattended worker that stood down (Remote Control takeover) sat `idle` for 20 h with its PR unmerged and nothing noticed

**Priority:** Medium
**Added:** 2026-09-11
**Status:** Resolved 2026-09-23 — was: Partly done — (a) and (c) merged 2026-09-13 (PR #125): the stand-down screen rule, the note on a `stalled?` card, and the design paragraph. (b), the idle-with-open-work policy, is the part that would have caught the 20 hours, and it waits on §6 (phase 3); until then it is the orchestrator brief's first rule, which is where it lives today.
**Location:** design §6 (stall policy, phase 3), `src/sessionorc/agent.py` (tick), `src/agentorc/adapters/claude_code/screen_rules.toml` (TD-015 manifest), `src/agentorc/ui/templates/card.html`

**Why:** Run 4 of `tdgrind-ao-1` (`ao-agentorc-tdgrind-ao-1-2`, unattended) opened PR #63 at 20:36 on 2026-09-10 and at 20:40 printed "Waiting for CI on the updated head of PR #63 before merging", then Claude Code printed "Remote Control disconnected — another connection took over this session … this device is standing down (code 4090)" and the pane's footer later showed `/rc failed`. From then until 16:47 on 2026-09-11 the session sat `idle` on the Team — 20 hours, CI long green, PR unmerged, run unfinished — and nothing flagged it: `idle` is the normal state for a session waiting on a person (§4.2), so an unattended session that has *stopped driving itself* is indistinguishable from one resting between turns. A single `ao send --wait` (in short: CI is green, merge it, exit) woke it; it merged #63 and exited within 30 s, so the process was fine and only its loop had ended. Two gaps: (1) no policy for an unattended session idle past a threshold with its lane unfinished (§6 has a *stall* rule for `working` with no output, not for `idle` with open work — an orchestrator session, §4.8, would have caught it on its first tick); (2) the stand-down banner is a screen state the classifier does not know (TD-015 manifest): a Claude Code pane that says "standing down" is not `idle`, it is `detached`, and the card should say so.

**Fix:** (a) ✅ a TD-015 rule (`remote-control-standdown`) for the Remote Control stand-down / `/rc failed` screen → `stalled?` with the note "stood down: another device took over this session", so the card reads differently from a resting worker. `stalled?` rather than a new `detached` state: the states are design §4.2's list and a new one is Paul's call, while `stalled?` already means *this session is not making progress and here is why* — §4.2 promised "a `stalled?` that can say why" and nothing had yet said one. The rule wants **both** of the banner's markers — the close code and the `/rc` failure. Three reviews of PR #125 broke every looser form in turn: `any` and `all` may match *different* lines, so a pair of loose phrases fires on a screen that merely mentions both; two phrases joined by `.*` fire on one line of prose *about* a stand-down, which is what a review comment or an unwrapped paragraph of this repo's docs looks like; and each marker alone is ordinary text elsewhere — a boot script reports a failed rc file, any circuit breaker stands down with a numeric code. The footer marker is anchored to the end of its line, which is where a pane's footer puts it and where prose about the rule never does: without that, this entry, the rule's own comment and its tests each tripped the rule they describe, so any pane showing one of them read as a stood-down worker — and a test now walks those files in the same 20-line windows the tick uses. The pair is what the one observed incident showed. The cost is a stand-down that shows only one of them, which is missed — the right way round for a scraped rule, and the cheap half to loosen once someone captures a real pane, since this rule is written from a sentence quoted in this entry and not from a screen anyone saved. A pane displaying the banner *verbatim* still reads as the banner, as a pane displaying the usage-limit message does: reading the pane is what a screen rule is, and what bounds it is that a scraped verdict never outranks a fresh hook, so a session reading a file is reporting hooks. Priority 70, below `rate-limited-429`'s 80, because a pane that is also rate limited has the more actionable answer; (b) in §6, an **idle-with-open-work** rule for `unattended` sessions: idle past `idle_after` (default 15 min) with a `progress` entry still `claimed` or a PR open from the session's branch → flag `stalled?` and nudge once with a fixed prompt through `send --wait`, then wrap up — the same escalation as the working-stall rule; until §6 lands, this is the orchestrator brief's first rule; (c) ✅ recorded in design §4.2 ("Takeovers happen to worker panes"). Done when a worker that stands down is flagged within `idle_after` and the nudge lands without a person.

**Resolved:** 2026-09-23 (grinder-ao-1). (a) and (c) landed in #125. (b) is design §6 *Keeping a team running* rule 4, *the idle nudge* (TD-103 slice (4), #461, live since the 2026-09-23 promote). The design settled it differently from the sketch above: it covers every *supervised* session, which is every one `ao team start` makes, and not every `unattended` one. It reads open work from the record's lane and declared claims, not from an open PR. It waits twenty minutes, sends one fixed line, and then shows *idle · open work* on the card rather than `stalled?`. It never wraps a session up by itself; what a session still idle after the nudge needs is its manager's or the person's judgement. The 20-hour case, a team worker idle with a claim, is caught on the tick. A hand-started unattended session with no `--supervised` is not: that is TD-026's ground, sessions that nothing keeps running.

**Related:** design §4.2 (idle is not an alert), §6 stall, §4.8 orchestrator, TD-015, TD-026 (no stopper for hand-started unattended sessions), TD-028 step (1) (PR #63, the run this happened to).

## TD-117: Deny with a reason: the permission row's Deny carries no words for the session to read

**Priority:** Low
**Added:** 2026-09-23 (the anchor session; design §10's question since 2026-09-06, decided by Paul 2026-09-23)
**Status:** Resolved 2026-09-23 — was: Open — decided 2026-09-23: **yes to Deny with a reason; no, for now, to "allow for this session".** The hook decision already carries a `reason` (`rpc_decide(behavior, reason)`, and the hook script prints it back to Claude Code); what is missing is the words: an optional one-line *why* beside Deny on the card, the Focus header and the Inbox's permission row; `ao deny <id> [reason]` already carries one.
**Location:** design §4.5a (the permission row, the card's Deny), §4.2, §10 (the question, marked decided); `src/agentorc/ui/templates/card.html`, `inbox_row.html`, `focus.html`, `static/app.js`, `src/agentorc/cli.py` (`ao deny`), `src/sessionorc/agent.py` (`rpc_decide` already takes `reason`)

**Why:** a bare refusal makes an unattended session try the same call another way; one sentence steers its next attempt, and the hook already carries it for free.

**Resolved:** 2026-09-23 (PR #486, grinder-ao-1). An optional *why?* box (`input.denywhy`) sits beside Deny on the card, the Focus header and the Inbox's permission row; `AO.denyBody` sends a filled one as the decision's `reason`, and `AO.denyWhys` / `AO.restoreDenyWhys` keep typed text through redraws. Design §4.5a (card, Focus, Inbox rows) and §10 carry it; test `test_deny_carries_an_optional_reason_from_every_place_it_is_offered`, with `test_ui.py`'s permission round trip for the hook half. "Allow for this session" stays *no, not now* in §10.

**Related:** TD-008 (the permission questions), TD-116 (who may decide), design §10.

## TD-008: Deny reason input and "allow for this session" (design §10 open questions)

**Priority:** Low
**Added:** 2026-09-06
**Owner:** grinder
**Kind:** build
**Pickable:** no — both halves decided 2026-09-23; the build is TD-117, which archives this entry
**Status:** Resolved 2026-09-23 — was: Open — **decided by Paul 2026-09-23: Deny with a reason, yes (TD-117 builds it); "allow for this session", no, not now.** **Design review 2026-09-22:** the deny-with-reason half endorsed as cheap and useful for unattended permission loops; "allow for this session" still not recommended.
**Location:** `src/agentorc/ui/templates/card.html`, `focus.html`; design §10

**Why:** The hook decision already carries a `reason` (the API and CLI accept one), but the UI's Deny button sends none. "Allow for this session" is not built. Both are open questions in design §10 for Paul to decide (board item).

**Resolved:** 2026-09-23 (PR #486, grinder-ao-1, as TD-117). Both halves decided by Paul 2026-09-23 and marked so in design §10. Deny with a reason is built — the optional *why?* box beside Deny on the card, the Focus header and the Inbox's permission row (design §4.5a); "allow for this session" is *no, not now*, so nothing is built for it and §4.5a lists no such control.

## TD-116: `decide` is ungated: any session may answer another session's permission prompt

**Priority:** Medium
**Added:** 2026-09-23 (the anchor session; the board's 2026-09-12 question from `tdgrind-ao-1`, decided by Paul 2026-09-23)
**Status:** Resolved 2026-09-23 — was: Open — decided 2026-09-23: **`decide` needs the `control` grant and membership, exactly as `send` does.** It was left ungated at TD-028 step (1) because design §4.8 listed it so and the UI answers as a person with no caller; a worker auto-approving another worker's tool call is the same class of act as typing at it.
**Location:** `src/sessionorc/agent.py` (`_gate`, `rpc_decide`, `NODE_ACTS`), design §4.8 (the acting RPCs and what needs the grant), §9 invariant 11, `tests/test_agent.py`

**Why:** the gate exists so that only a session's controllers act on it; a permission answer is an act on it.

**Resolved:** 2026-09-23 (PR #490, grinder-ao-1; merged by the anchor). `decide` is in `mail.ACTING_RPCS`, so `act_gate` refuses a session without `control` or outside the target's `controllers`; `modes.offline_refusal` refuses a session's `decide` on another while the link is down. `rpc_decide` takes `caller`: only a person's answer refills the wake budget, and the trail says *by <name>* for a controller (`_answered_by`). Design §4.8 (the grant's list, §4.10's table), §4.4a's node table, §4.10 *Time and a person restore it* and §9 invariant 11 carry it; tests `test_a_session_answers_a_permission_only_as_a_controller`, `test_orchestrate_grant_gates_acting_rpcs`, `test_modes`. A session's `decide` on **itself** still passes, as its `send` does: TD-119.

**Related:** TD-028 (where it was left ungated), TD-036 (membership), design §4.8, §9 invariant 11.

## TD-115: A session's own exit hook can be judged outside and refused, and the refusal is applied anyway through the events queue

**Priority:** Medium
**Added:** 2026-09-22 (the anchor session; the identity alarm on the person's Inbox the same night, which Paul asked about)
**Owner:** anchor
**Kind:** live-check
**Pickable:** no — resolved
**Status:** Resolved — live check passed 2026-09-23. **(1) and (2) built 2026-09-22 (`grinder-ao-1`, PR #470; it touches `src/sessionorc`, so the anchor merges):** design §4.8a *A hook just after its pane ended* — `identity.PANE_GONE_GRACE` (ten seconds), `identity.classify_gone` (the gone pane's session id, then its terminal, no walk), the agent's `_id_gone` kept by `_id_note_panes` and asked by `_identify` for a `hook` that matched no live pane, for the record it names only; `hook.py` raises `Refused` on an error reply, prints it to stderr and never queues it (§4.2's sentence). The queue's hole is a sentence under *What this does not stop*, as the fix said. Tests: `test_a_gone_pane_is_matched_by_session_id_then_terminal_never_by_a_walk`, `test_a_hook_just_after_its_pane_ended_is_its_sessions_for_the_grace`, `test_a_refused_hook_is_never_queued`. **Left, after the promote:** (3) — dismiss the two alarms from the Inbox and watch the next tick-closed seat raise none. Was: Open — **found 2026-09-22.** The host's alarm list (`ao identity`, the Inbox's identity row) read *outside claimed `ao-agentorc-techlead-ao-1` on hook ×2*, at 03:33:22Z and 03:55:29Z on 2026-09-23. Each came at the end of a techlead seat's run: the run log ends with its summary and `/exit` in the composer (03:33:09Z, 03:55:26Z), the alarm follows within twenty seconds, and the record reads `exited (hook)` two seconds after the alarm. The seat ran three times that hour; the second run's end (about 03:35:29Z) raised nothing. A probe the same night — a throwaway session ended once by `ao close` and once by a typed `/exit`, idle both times — raised nothing either. So it is a race at a Claude Code exit: a hook of the ending process (its `Stop` or its `SessionEnd`) can connect after its pane is gone, and then `identity.classify` has nothing to match — the ancestry walk meets no pane pid, and the peer's session id and tty are a pane the list no longer holds — and answers *outside*; `judge` refuses every hook from outside (*a hook runs under a pane, always*) and records the alarm on the host's own list, since it frames no record. The second half is worse: `hook.py` (`main`) treats the refusal — an `error` in the reply, raised as `RuntimeError` by `call_agent` — exactly as an agent that is down, and appends the event to `events/<session>.jsonl`; `_reconcile` drains that queue straight into `_apply_event`, which judges nothing. That is why the record went `exited (hook)` two seconds after the refusal, and `~/.agentorc/events/` was last written at 03:55Z, the second alarm's minute. It also means `enforce` does not hold on the events path at all: a line written to `~/.agentorc/events/<id>.jsonl` by any process of this user is applied as that session's hook. §4.8a calls identity tamper-evidence rather than a wall (TD-077), and the events queue is a door in it with no log. **Why now:** TD-103 slice (3) (PR #459) closes an idle seat from the tick and slice (4) (PR #461, built and waiting for the anchor's merge at this writing; TD-103's status line is updated by that PR) closes an idle member before a wanted restart; every such close ends a Claude Code process, so once both are live the Inbox gains an identity alarm on some fraction of the host agent's own closes — noise that buries a real alarm.
**Location:** `src/sessionorc/identity.py` (`classify`, `judge` — the hook rule), `src/sessionorc/agent.py` (`_id_channel`, `_id_note_panes`, `_identify`; `_reconcile`'s drain), `src/agentorc/adapters/claude_code/hook.py` (`main`, the fallback to `EventQueue`), `src/sessionorc/store.py` (`EventQueue`), design §4.8a, §4.5a **Inbox row: identity alarm**

**Why:** an alarm that fires on the host agent's own routine act teaches a person to dismiss alarms; and a refusal that is then applied through a side door is a check that records a refusal it did not make.

**Fix, in order — the design first (§4.8a, a small change: a hook is its session's for a short grace after its pane ends):** (1) `_id_channel` keeps each record's last pane (its session id and tty) for a grace after it leaves the list (`PANE_GONE_GRACE`; ten seconds is plenty, the alarm followed the pane by less than three), and a `hook` naming a record whose pane is inside that grace is judged *session* by its session id or tty against the gone pane, the walk unchanged; a test that a `SessionEnd` from a pane that closed reads as its session, and one that a hook naming a record whose pane left a minute ago is still *outside*. (2) `hook.py`: an `error` reply is a refusal, never an outage — log it to stderr and return, and never queue it; the queue stays what it is for, an agent that was down. (3) Once (1) is live, dismiss the two alarms and note it here; the events queue's remaining hole (a file any local process can write) is a sentence under §4.8a's *What this does not stop, so nobody reads it as more*, not a build.

**Resolved:** 2026-09-23 (the anchor). (1) and (2) built in PR #470 (`grinder-ao-1`), promoted the same morning; (3) the live check: two techlead seats closed by the tick after the promote — `techlead-dc-1` at 06:17:40Z and `techlead-ao-1` at 13:57:46Z, each `exited (hook)` — raised no alarm, and the host's list held only the two of 03:33Z and 03:55Z from before the fix; the anchor acknowledged those at about 16:50 MDT (`identity_ack`, the Inbox row's Dismiss, run as the person), and `ao identity` reads *no identity alarms*.

**Related:** TD-077 (identity), TD-106 (identity finished as built), TD-103 (the tick's closes), TD-113 (9) (the seat filled by hand that these runs were: no `supervised`, no `seat`), TD-111 (`ao doctor`).

## TD-046: A session cannot be popped out into its own browser window, so switching between agents needs the mouse

**Priority:** Medium
**Added:** 2026-09-13 (raised by Paul)

**Status:** Resolved 2026-09-23 — was: Designed 2026-09-23 (the anchor) — **build it as design §4.5 screen 2 *Pop out* and the §4.5a rows **Pop out**, **Focus** (its *Focus window* reading) and **title** say; the six pieces below are decided there:** (1) Pop out in the card's *more ▾* and the Focus header, Focus itself a plain link so middle-click stays the browser's; (2) `/focus/{sid}?window=1`, the whole Focus minus nav and top bar, panels included; (3) `window.open` named `ao-focus-<id>`, size and position per session in the browser, default fits 100 columns; (4) the title `<name> · <state>`, `▲ ` while it needs you, from the feed — on every Focus, tab or window; (5) no ceiling, a tab's cost each, the window never closes itself; (6) the card reads *Focus window* and raises it in the browser that opened it, other browsers open Focus as ever. One PR for the page (`src/agentorc/ui/`: `card.html`, `focus.html`, `base.html`, `app.js`, `app.py`); no `src/sessionorc` change. Was: **the design round is next, the anchor's (Paul, 2026-09-23: *go with your recommendations*):** one OS window per Focus, the six pieces below decided in §4.5a and §4.5, then a grinder builds. Was: design task first: not to be coded before the control is in design §4.5a and the behaviour in §4.5

**Location:** `src/agentorc/ui/templates/card.html` (the Focus button), `focus.html`, `base.html` (the chrome a popped-out window should not carry), `src/agentorc/ui/static/app.js` (the events websocket, one per tab today), `src/agentorc/ui/app.py` (`/focus/{sid}`), design §4.5 screen 2 and §4.5a

**Why:** every Focus opens in the tab you were in, so moving between two working agents is Org → click a card → Focus → back → click the other. Paul (2026-09-13): *"having the ability to alt-tab between agent sessions is quite useful (instead of having to involve the mouse to click)."* That is the real requirement — **the operating system's window switcher, not a widget inside the page**. One OS window per agent makes the fleet behave like the terminals it replaces: alt-tab is muscle memory, the windows can be tiled or sent to separate monitors, and the terminal keeps its own scrollback and keyboard focus instead of being torn down and rebuilt on every navigation. It also fixes something the current shape cannot: a pane you are watching disappears the moment you look at another one, so there is no way to keep two agents visible at once on one screen.

**Fix (design first, then code), the pieces that need deciding:**

1. **The control.** A **pop out** button on the card's `more ▾` menu and in the Focus header, plus the obvious shortcut of middle-click or ctrl-click on Focus behaving as it already does on a link. §4.5a gains the row; a control not in that table does not exist.
2. **A chromeless route.** `/focus/{sid}?window=1` (or a `/pane/{sid}`) rendering the Focus screen without the nav, so the window is the session and nothing else. Decide whether the side panels come with it.
3. **The window itself.** `window.open` with a **name keyed on the session id**, so pressing pop out twice focuses the window that exists rather than opening a second one. Decide the default size and whether position is remembered per session (localStorage, like Pinned order).
4. **The window title is the point.** It is what alt-tab shows, so it must be the session's name first and short — `tdgrind-ao-1 · working` rather than `agentorc — Focus`. It has to track the state deltas the page already receives, and say when the session needs you, since a window switcher is the only place a background window can speak.
5. **Cost of many windows.** Each tab opens its own `/events` websocket and its own terminal pty (§4.6). Decide the ceiling, what happens when it is reached, and whether a popped-out window that loses its session (killed, closed, forgotten) closes itself or shows the exited banner — today `/focus` of a dead record still renders Details.
6. **The opener's grid.** A card whose session is popped out should say so and offer *focus that window* rather than a second one, or the person ends up with two views of one pane and no way to tell them apart.

Done when two agents can be open in two OS windows at once, alt-tab moves between them, each window's title names its session and its state, and popping out the same session twice raises the first window instead of opening another.

**Related:** design §4.5 screen 2 (Focus), §4.5a, §4.5 (its screens intro: one pty per open terminal), §4.6 (transport and terminal mechanics), §4.5b (reachability: a popped-out window is the same origin, so the tunnel or private network carries it unchanged); TD-029 (a closed session's terminal reconnecting), TD-038 (the terminal's look).

**Resolved:** 2026-09-23 (PR #500, `grinder-ao-2`) — built as §4.5 screen 2 *Pop out* and the §4.5a rows say: `/focus/{sid}?window=1` renders Focus without the top bar and the Org link; **Pop out** in the card's *more ▾* and the Focus header opens `window.open("", "ao-focus-<id>")`, so a second press raises the window without a reload; size and position per session in `localStorage`; every Focus titled `<name> · <state>` with `▲ ` while it needs you (`AO.focusTitle`); a `BroadcastChannel` between the browser's tabs makes the card read **Focus window**; a forgotten record's window says so and stays. `tests/test_ui_popout.py`. The *done when* (two windows, alt-tab, the title) is Paul's live look after the next promote, on `docs/user_attention.md`.

## TD-119: A session may answer its own permission prompt: the gate passes every acting RPC a session makes on itself, `decide` included

**Priority:** Medium
**Added:** 2026-09-23 (`grinder-ao-1`, found building TD-116, PR #490)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-23 — was: Decided 2026-09-23 (the anchor): **refuse.** A permission prompt exists so that someone other than the session approves the call; a session answering its own is the prompt defeated, the same class as `set_grants` on oneself, which the gate already carves out. Build the *Fix* below as written: `decide` joins the self-exclusion tuple in `act_gate` and `offline_refusal`'s self branch, the refusal reads *a session does not answer its own permission prompt (design §4.8)*, a test beside `test_a_session_answers_a_permission_only_as_a_controller`, and §4.8's self-case sentence says it — design first, in the same PR. `src/sessionorc`, so the anchor merges it (TD-093). Was: Open — needs a decision: refuse `decide` on the caller's own record, or leave it as `send` to oneself is
**Location:** `src/sessionorc/mail.py` (`act_gate`: `if method not in ("create", "set_grants", "set_controllers") and params.get("id") == caller: return None`), `src/sessionorc/modes.py` (`offline_refusal`, the same self rule), design §4.8 (the gate), §9 invariant 11

**Why:** TD-116 made `decide` an acting RPC "exactly as `send` does", and `act_gate` passes any acting RPC a session makes on **itself**: that is right for `send`, `set_mode` or `close` (a session may type into, or close, its own pane). But a permission prompt exists so that someone other than the session approves the call: a session whose main turn is blocked on the hook can still run `ao allow $AGENTORC_SESSION` from a background task or a subagent and approve its own tool call. The gate already carves out the two RPCs that edit authority (`set_grants`, `set_controllers`) for the same reason; a permission answer is arguably a third. Not changed in PR #490 because the decision said "exactly as `send`".

**Resolved:** 2026-09-23 (PR #498, grinder-ao-1; `src/sessionorc`, so merged by the anchor). `mail.act_gate` refuses `decide` on the caller's own record before the self-pass, whatever the caller holds, and `modes.offline_refusal` refuses it in its self branch, both with `mail.self_decide_refusal`'s line; design §4.8 *Grants* states the self case. Tests: `test_a_session_never_answers_its_own_permission` (`tests/test_agent.py`), the self-case assertion in `tests/test_modes.py`.

**Related:** TD-116 (the gate on `decide`), TD-117 (Deny with a reason), design §4.8, §9 invariant 11.

## TD-123: Three dead tabs on the top bar — Resumable, Commands, Attention — disabled placeholders since phase 1

**Priority:** Low
**Added:** 2026-09-23 (Paul's question; the anchor session)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-23 — was: Decided by Paul 2026-09-23 (*yes, remove the three tabs*): **remove all three from the bar; one page PR, design first** — `base.html` loses the three spans, §4.5's screen list says where each went (Attention: struck, the Inbox's board rows; Commands: the Org filter's *show command runs*, the specs unbuilt; Resumable: kept as an unbuilt phase-4 screen, a tab when it exists), §7's phase 4 keeps the two, the dated fact to the history. Was: **recommendation (the anchor, 2026-09-23): remove all three from the bar now.** They are `<span class="tab off" title="phase 4">` in `base.html` — not links, pressable by nothing — and each one's job has moved or never started: **Attention** is the Inbox's board rows since TD-069 step 3 (2026-09-23), so the screen is struck from §4.5 in favour of the Inbox; **Commands**' visible half is the Org filter's *show command runs*, and its command specs (§4.5 screen 5, cmdorc-shaped) were never built; **Resumable** (§4.5 screen 4, conversations agentorc did not start, with Adopt) is the one still worth building — it stays in §4.5 as an unbuilt phase-4 screen, reached from New session's *resume* when it comes, and gets a tab when it exists. A tab that does nothing teaches the person that the bar lies. If Paul agrees: one page PR removes the three spans and §4.5's screen list says where each went (design first, the dated fact to the history).
**Location:** `src/agentorc/ui/templates/base.html` (the three `tab off` spans), design §4.5 screens 4 and 5, §7 (phase 4)

**Why:** three of the bar's five tabs are dead; on a phone they take the width the live ones need.

**Resolved:** 2026-09-23 (PR #507, `grinder-ao-2`) — `base.html`'s top bar draws only built pages; design §4.5 screens 4 and 5 say *not built, a tab when it exists*, screen 7 (Attention) is struck in favour of the Inbox's board rows and `/attention`, §4.5a's *Due strip / Attention* rows are *Due strip / Inbox board row* or *Due strip*, §7 phase 4 restated, the Attention mockup removed; `tests/test_ui_org.py` asserts no dead tab renders.

**Related:** TD-069 (the Inbox), TD-081 (Resume on a card — what Resumable's *running one* case became), design §7.

## TD-121: A card's more ▾ menu is clipped by the card: the fixed-height card hides overflow and the menu is positioned inside it

**Priority:** Medium
**Added:** 2026-09-23 (Paul, from the Org page; the anchor session)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-23
**Location:** `src/agentorc/ui/static/app.css` (`.sc { overflow: hidden }` from TD-095's one-height card; `details.more .menu { position: absolute }`), `src/agentorc/ui/templates/card.html` (`details.more`)

**Why:** pressing **more ▾** on a card at the bottom of its row shows a sliver of the menu's border and nothing else — the card clips it, so the mode toggle, Pop out and the rest are unreachable there. A control that cannot be reached is worse than one that is absent (design §4.5a).

**Resolved:** 2026-09-23 (PR #506, `grinder-ao-1`) — `details.more .menu` is `position: fixed`, placed by `AO.placeMenu` from its summary's rect when it opens (right-aligned under it, flipped above when the space below is short, kept 8px inside the viewport) and re-placed on scroll and resize; `.sc` keeps its `overflow: hidden`. The one rule covers the cards, the Inbox rows' *Snooze* menus and the Focus header's menu; a delta that redraws a card reopens the menu that was open on it. Test: `tests/test_ui_menu.py`.

**Related:** TD-095 (the card's one height), TD-046 (Pop out lives in this menu).

## TD-145: A one-press Resume of a session in a worktree creates a second record beside the one being resumed

**Priority:** High
**Added:** 2026-09-24 (Paul, from the Org page: two techleads on the ao team; the anchor session)
**Owner:** anchor
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-24
**Location:** `src/agentorc/ui/app.py` (`resume_create`, `resume_form_url`, the `/new` form's prefill), `src/agentorc/ui/templates/new.html` (the Where radio), `sessionorc/naming.py` (`base_id`: unchanged — the rule it applies is §4.1's)

**Why:** a name identifies one session per scope (§4.1, §9 invariant 12), and the scope of a session in a worktree is its repo: the record's id is `ao-<repo>-<name>`. The one-press Resume (TD-081) sent the record's `dir` and not its `repo`, so the agent scoped the name by the worktree's basename — and every team member's worktree is named after the member, so `ao-techlead-ao-1-techlead-ao-1` collapsed to `ao-techlead-ao-1`, an id nobody held. The name check answered *free*, a second record was created, and `_supersede` (matching on the tool's session id) then closed the first with `superseded_by` pointing at the second. Seen 2026-09-24 19:42 MDT on `ao-grind`: the exited `ao-agentorc-techlead-ao-1` was resumed from Focus, `ao-techlead-ao-1` appeared beside it, the team's status listed two techleads, and the seat's `asks` trigger went dead — `_seat_pass` skips a superseded record, and the second record carried no `seat` (Resume does not carry one, by design). The mode flips that followed acted on the second record and changed nothing here; popping the tab into its own window is the browser's and touched nothing.

**Resolved:** 2026-09-24 (PR #543, the anchor session) — `resume_create` carries `repo` when the record has one, so the create checks the name in the record's scope and takes its id back; `resume_form_url` lands a worktree record on the form as Where = new worktree, the worktree's name and the repo in the directory field, which is what the form's own Start sends; the `/new` form prefills `where` and `worktree`. Design §4.5a *Focus (exited / closed)* says both. The live team was put right by hand: the seat filled from its launch record (`create` with `keep_mail` and `supervised`, the tick's own replay), the stray record forgotten.

**Related:** TD-081 (the one-press Resume), §4.1 (the scope rule), §6 rule 3 (the seat fill that a superseded record blocks).

## TD-138: Build the message shape on the Inbox — the markdown renderer, the *details* fold, the backstop

**Priority:** Medium
**Added:** 2026-09-24 (TD-127's design, a cloud session with Paul)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25. Was: open — designed, nothing built. Design: §4.10 *How a message to a person is written*, §4.5a *Inbox row: details* and the four mail rows, §4.5 screen 6 (*no scroll box*), mockups `Inbox.dc.html` and `InboxMessage.dc.html`.

**Location:** `src/agentorc/ui/render.py` (new: the closed-subset renderer — paragraphs, `*em*` / `**strong**`, inline code, fenced code blocks, `-` and `1.` lists, `[text](url)` — emitting escaped HTML only, with the link rule: absolute `http(s)`, not the request's own origin, `target=_blank rel=noopener`, the host in a `<small>` after the text; anything else is its characters), `src/agentorc/ui/app.py` (`fold(text) -> (lead, rest)`: up to the first blank line, else the backstop at the last sentence end before `FOLD_CHARS` = 300; both halves rendered once per row in `inbox_sections`' output as `lead_html` / `rest_html`; the *answered for you* quotation from the question's lead), `src/agentorc/ui/templates/inbox_row.html` (the `body` macro: the lead, then `<details><summary>details</summary>` holding the rest when there is one; every mail macro through it; controls stay outside), `src/agentorc/ui/static/app.js` (the set of row ids whose *details* is open, re-applied after each poll, since the poll replaces rows; nothing stored; and `AO.mailEntry`, the Focus Inbox panel's entry, drawn through the same lead and fold — taken from the designer's PR #532, 2026-09-25), `app.css` (the summary as a quiet unbordered line; `.body` loses `white-space: pre-wrap`, paragraphs are elements now), `tests/test_screen.py` or a new `tests/test_render.py`.

**Why:** a person reading the Inbox reads the verdict first or not at all; today the row is one run of text and a two-hundred-word reply hides *merged* in its first word and *one gap* in the middle.

**Resolved:** 2026-09-25 (PR #557, `grinder-ao-2`) — `agentorc.ui.render` (the closed subset and `fold`), the `shaped` template global every mail row's text goes through, the *details* fold kept open across the poll (`app.js` `foldsOpen`, `AO.reopenFolds`), the Focus panel's halves from `/api/sessions/<id>/inbox`. Tests: `tests/test_render.py`, the TD-138 rows in `tests/test_ui_inbox.py`. Design §4.5a *Inbox row: details* carries the lasting content; the live look at a real `--source` reply is the anchor's after the promote.

**Done when** (1) techlead-ao-1's #517-style reply (a verdict line, a blank line, a list) reads as its verdict with *details* closed, and open shows the list rendered; (2) the six refusals above and the one allowed link behave as listed, in tests and in a browser; (3) an old one-paragraph entry from before the rule folds at a sentence and answers folded; (4) the page's find (TD-135, if landed) still matches words inside a closed *details*; (5) `pdm run test` and `pdm run lint` pass; (6) TD-127 is marked built for the page half; (7) the Focus Inbox panel's entries fold the same way.

**Related:** TD-127 (the design), TD-139 (the senders' half), TD-136 (the message page renders the same row open), TD-135 (the find over the whole text), TD-071 item 8 (nothing pressable from text — the link rule is its one exception).

## TD-155: A resumed session that starts no turn reads `working` until it stalls: `SessionStart` with source `resume` lands at the composer and fires no `Stop`

**Priority:** Medium
**Added:** 2026-09-25 (seen on the designer's record after Paul's one-press Resume)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25

**Location:** `src/agentorc/adapters/claude_code/hook.py` (`STATE_EVENTS` maps every `SessionStart` to `working`; `SESSION_START_NOT_A_START` exempts `compact` only, TD-090), `src/sessionorc/agent.py` (`STALL_AFTER`, `_bell_blocked` — *not hook-confirmed idle*), design §4.2.

**Why:** `claude --resume <id>` prints the old conversation and waits at the composer. Its `SessionStart` (source `resume`) is reported as `working` with confidence `hook`, and no turn follows, so no `Stop` ever reports `idle`. Seen 2026-09-25 14:06Z: the designer resumed by Paul reads `working (hook)` with an empty composer (`ao explain`: *no screen rule matched*), and at `STALL_AFTER` (20 minutes) it will read `stalled?` — an alert on a session that is simply idle. Two things follow from the wrong state: the doorbell never rings it (it rings hook-confirmed idle only), so mail for a resumed session waits until someone types; and a manager's `wait` sees a member `working` that is doing nothing. A fresh `SessionStart` (source `startup`) is a different case — `ao new` types the prompt at once, so `working` is right there — and `clear` is already handled as a continuation.

**Resolved:** 2026-09-25 (PR #556, `grinder-ao-1`) — `SESSION_START_AT_THE_COMPOSER = {"resume"}` in `hook.py` reports `idle` at confidence hook; a prompt given with the resume reports `working` through its own `UserPromptSubmit`. Held by `test_a_resume_lands_at_the_composer`; design §4.2's state table carries the row. The live check (a one-press Resume reads `idle (hook)` within a tick) is on `docs/user_attention.md`.

**Done when** a one-press Resume with no prompt shows `idle` within a tick, never `stalled?`, and the doorbell rings it for mail that lands after the resume.

**Related:** TD-090 (the `compact` exemption, the same shape), TD-081 / TD-145 (Resume), TD-153 (the doorbell needs a hook-confirmed idle), §4.2.

## TD-139: The shape asked of the senders — the presets' paragraph, the designer brief, the composer placeholder, `ao msg`'s warning

**Priority:** Medium
**Added:** 2026-09-24 (TD-127's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25 — the three presets, the warning and the placeholder in PR #561, the designer's brief in PR #563.

**Location:** `src/agentorc/briefs/techlead.md`, `manager.md`, `grinder.md` (the paragraph, in the same words, beside each brief's *ao msg person* line; the techlead's beside *An `ask`: only when the answer is already written down*, with *a `--source` reply's first line is its verdict*), `docs/briefs/designer-ao-1.md` (the same paragraph under *How much Paul sees* — `docs/briefs/**` is held, so the techlead reads this PR, §4.9b; split it from the rest if that wait is long), `src/agentorc/cli.py` (`ao msg`: when the addressee is `person`, or the message is a `--source` reply, and the first paragraph is over `FIRST_PARA_WORDS` = 60 words or the text has no blank line and is over `FOLD_CHARS` = 300 characters, print *the person reads the first paragraph: n words — say what it is about, what you decided or ask, what they must do* to stderr and send anyway), `src/agentorc/ui/templates/base.html` (the Message / Reply dialog's `#mailtext` placeholder: *first what you want, then why — …*), `tests/test_cli.py`, `tests/test_primer.py` (the pointers, if the briefs' headings move).

**Why:** the row can only fold what the sender shaped; the rule lives with the writers, and a warning at send is the one moment the writer can still fix it.

**Resolved:** 2026-09-25 (PRs #561 and #563, `grinder-ao-2`) — the paragraph in `techlead.md`, `manager.md`, `grinder.md` and `docs/briefs/designer-ao-1.md`; `ao msg`'s `shape_warning` on `render.paragraph_break` (fence- and CRLF-aware, the Inbox row's own blank line); the Message / Reply dialog's placeholder. Tests in `tests/test_cli.py`. Design §4.10 *How a message to a person is written* carries the lasting content.

**Done when** (1) each of the four briefs carries the paragraph in those words; (2) `ao msg person` with a 90-word first paragraph prints the warning and the mail arrives; a shaped message prints nothing; a `--source` reply is checked the same way; (3) the Message / Reply dialog's placeholder reads the line; (4) the designer-brief change waited for the techlead's read or was split out; (5) `pdm run test` passes; (6) TD-127 is marked built for the senders' half.

**Related:** TD-127 (the design), TD-138 (the page half), TD-125 (the wind-down report, the same rule applied once), TD-114 (the briefs' template — the paragraph goes where the mechanics live, not in a repo's supplement).

## TD-135: Build the Inbox rail — sections, teams and kinds as toggles, the URL, and the find box

**Priority:** Medium
**Added:** 2026-09-24 (TD-129's design, a cloud session with Paul)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25. Was: open — designed, nothing built. Design: §4.5 screen 6 *The rail* and *Find*, §4.5a *Inbox page: the rail*, *Inbox page: find*, the **keys** row (`/`), glossary *rail*, mockups `Inbox.dc.html` and `InboxRail.dc.html`.

**Location:** `src/agentorc/ui/templates/inbox.html` (the rail beside `.inboxcol`; the `#ifilter` box moves into it), `inbox_row.html` (the team badge's press; `data-find` on every row kind; `data-team` and `data-kind` for the filter), `src/agentorc/ui/app.py` (`inbox_sections` gains each row's coarse `kind` and the counts per section, team and kind under the current picks; the route reads the query and the poll echoes it), `src/agentorc/ui/static/app.js` (`AO.inbox`: the toggles, the URL, the browser's memory, the find), `app.css`, `tests/`.

**Why:** the page's one filter is a typed syntax and team and kind are not visible controls; Paul's way of working the Inbox is one team's *Needs you* rows, then the next team's — a press per team, with a count that says where to go next.

**Resolved:** 2026-09-25 (PR #567, `grinder-ao-2`) — `inbox_rail.html`, `rail_counts` / `rail_picks` / `rail_kind` / `row_find` in `app.py`, `AO.railCounts` and the toggles, URL and find in `app.js`, held to one answer by `test_the_script_counts_the_rail_as_the_server_does`. The design row §4.5a *Inbox page: the rail* carries the lasting content, and design-history §4.5a the four calls made in the build (counts twice, a team line's *Needs you* count, push on a press and replace on typing, Dismiss all on the rows on screen). The live look is on the board line for TD-144/TD-138's promote.

**Done when** (1) with two teams' mail in the inbox, pressing one team shows that team's rows in every section and the number beside the team reads what it needs from the person; pressing a second team adds its rows; pressing *FYI* alone then shows both teams' FYI and nothing else; **All** shows everything; (2) the URL after those presses, opened in a fresh tab, is the same page, and a bare `/inbox` in the first browser remembers the last picks; (3) the top bar's number does not change under any pick, every count reads *n of all* while one is on, an unpicked team reads *0 of n* while another team is picked, and no count ever exceeds the rows on the page; (4) typing two words from a body in the other order, `#517`, and `517,` each find the row, *jeff* finds *jeffrey*, a team pressed and a word typed shows only that team's matching rows and the team counts change with the word, a word from a folded FYI entry unfolds FYI, and the count reads *n of all*; (5) `pdm run test` covers `inbox_sections`' counts under picks and the `kind` of each row kind; (6) TD-129 (2) and (3) are marked built; (7) TD-137's `Pickable` is flipped to yes in the same PR, since nothing else flips it. (7) TD-137's Pickable flipped to yes in the same PR

**Related:** TD-129 (the design), TD-136 (the page, reached from this filtered list), TD-137 (narrow), TD-069 (the filter this replaces), TD-124 (`/`, `Esc`), TD-131 (the size at which the counts move into a store).

## TD-137: Build the Inbox's narrow layout — the rail as a chip row and a filter sheet, the page as the page

**Priority:** Low
**Added:** 2026-09-24 (TD-129's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25. Was: open — designed, nothing built. Design: §4.5 screen 6 *Narrow*, §4.5a *Inbox page: the rail* (its last sentence), mockup `InboxPhone.dc.html`.

**Location:** `src/agentorc/ui/templates/inbox.html` (the chip row — the rail's Teams list, picked first — and the sheet, drawn from the same counts as the rail), `app.css` (the 720 px breakpoint Focus already uses), `app.js` (the sheet's open and close; the toggle code is TD-135's).

**Why:** the pages are used from a phone (§4.5 *Phone layout*), and a 200 px rail beside a 390 px column is neither a rail nor a column.

**Resolved:** 2026-09-25 (PR #575, `grinder-ao-2`) — the chip row and the `<dialog>` sheet in `inbox.html`, the sheet taking the rail's own node while open (`app.js`, `AO.inbox`), the team chips from `AO.railCounts`, the 720 px rules in `app.css`; `test_below_720_the_rail_is_a_chip_row_and_a_sheet_holding_the_same_toggles`. *A mail row's text opens its page* is the page's own (TD-136). Design §4.5 screen 6 *Narrow* carries the lasting content.

**Done when** at 390 px wide the Inbox shows the chip row, a team chip filters as the rail's line does, the sheet opens and its toggles work, the URL is the desktop's for the same picks, and a mail row's text opens its page; back at 1100 px the rail is drawn with the picks kept.

**Related:** TD-129, TD-135 (the toggles this re-renders), TD-136 (the page), §4.5 *Phone layout*.

## TD-134: A test drives two controllers to contradict one worker, and the grinder preset says what a worker does with a contradiction

**Priority:** Low
**Added:** 2026-09-24 (the designer; TD-039's design round, PR #527)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25. Was: Open — designed, nothing built. Design: §4.10 *A conflict, worked* and *A bounded exchange, counted by thread* (the escalation is an `ask` to the person, not a board line).

**Location:** `tests/test_mail.py` (beside `test_sends_are_recorded_with_who_typed_and_a_conflict_cites_them` and `test_a_conflicts_answers_reach_every_controllers_copy`), `src/agentorc/briefs/grinder.md` (one line under the mail rules), nothing in `src/agentorc/cli.py`: the escalation names the conflict's id in the text of a plain `ask` to the person, and the asker writes the ruling as a `reply` on the conflict's thread (`--thread` takes only the caller's own question to the person and is not the road).

**Why:** TD-039's *done when* — a test that drives two controllers to contradict one worker and ends in a recorded resolution or a question to the person, never a stalled worker — has no test, and the grinder preset does not say what a worker does when two `send`s contradict each other. The mechanics exist (the `conflict` kind, `sends`, the first reply closing every copy, `bound_hit`); the path through them is untested end to end.

**Resolved:** 2026-09-25 (PR #569, `grinder-ao-1`) — `tests/test_mail.py`'s `test_two_controllers_contradict_a_worker_and_the_first_reply_is_the_ruling` and `test_a_conflict_nobody_answers_goes_to_the_person_and_is_never_a_stalled_worker`; the grinder preset's *Two controllers telling you opposite things* rule, held by `test_the_grinder_preset_says_what_a_worker_does_with_a_contradiction`. Step (2) as written — the worker writes the person's ruling *as a `reply` on the conflict's thread* — is not possible (a worker holds no copy of its own conflict); §4.10 *A conflict, worked* now says one `note` to both controllers, and *ends its turn in `ao wait`* became *ends its turn, to be rung by the reply* (TD-153).

**Done when** the two tests pass on the suite, the grinder preset carries the line and its test, and TD-039 is archived with a pointer at §4.10 *A conflict, worked*.

**Related:** TD-039 (the design), TD-052 (the mail this rides on), TD-032 (the stalled worker this must not reproduce), TD-125 (the brief-test pattern).

## TD-039: Two controllers of one session can contradict each other and nothing lets them talk: design the conflict report, the controller-to-controller exchange, and the escalation

**Priority:** Medium
**Added:** 2026-09-13
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25. Was: Open — **the conflict-specific half designed 2026-09-24 (the designer, PR #527):** §4.10 *A conflict, worked* — the worker stops on the contested step and waits in `ao wait`, never guessing; the first `reply` from either controller is the ruling and the worker is never the arbiter; a resolved conflict is a thread, not a `finding` (what it reveals is the manager's ledger entry); nobody writes a board line — the escalation on a `bound_hit` or an expired bound is an `ask` to the person carrying the thread, which also changes the bounded-exchange rule's *the refused sender writes the board line itself* (steered to Paul as a default, bound one night). The worker asks the person only when its bound expires with no reply at all. Left to build: the test and the grinder preset's one line — TD-134. Was: design task, raised by Paul 2026-09-13 with the TD-036 go. **Its general half was answered 2026-09-14 by design §4.10** (TD-052): the gap was not a conflict feature but a missing concept — sessions could act on each other and never message each other — so the conflict report is a `conflict` message to both controllers, the exchange is `reply` traffic in one thread, the escalation is §4.10's exchange bound, and "not double-nudging" stops being a matter for briefs, since a controller's message about a session is copied to that session's other controllers. What stays here: the conflict-specific judgement — what a worker does *while* it waits, whether a resolved conflict becomes a `finding`, and who writes the board line. Not to be coded before TD-052 step 6 sets the bounds
**Location:** design §4.8 (membership, report channels), §10 (the 2026-09-13 question); later `src/sessionorc/agent.py` (`_gate`, a new report kind), `src/agentorc/cli.py`, the orchestrator brief

**Why:** TD-036 deliberately allows several controllers per session with no privileged member, and says keeping them from double-nudging "is a matter for their briefs". That is fine for nudges and useless for contradictions: a ui orc says "ship the chip now", a backend orc says "wait for the RPC", and the worker has no move but to pick one or stall. Paul's rule is the one a team would use — the worker puts it to both leads, they settle it between themselves, and a person hears about it only if they cannot. Nothing in agentorc supports that today. Upward, a worker has `ao progress` and `ao finding`, which declare claims on references and are read by whoever looks at the card, not delivered to a controller. Sideways, an orchestrator may `ao send` to another only because the `orchestrate` grant is not yet narrowed by membership; once TD-036's gate lands, two peers over a shared worker control neither each other nor anything but their own members, so even that accidental path closes. There is no conflict object, no delivery, no bound, and no escalation.

**Resolved:** 2026-09-25 (PR #569, with TD-134) — the design is §4.10 *A conflict, worked* and *A bounded exchange, counted by thread*; the end-to-end tests its *done when* asked for are TD-134's.

**Related:** design §4.8, §9 invariant 11, §10 (2026-09-12 and 2026-09-13 entries); TD-036 (the gate this extends), TD-028 (the report channels this adds a kind to), TD-032 (a stalled worker nobody noticed — the failure this must not reproduce).

## TD-169: A hook event queued while the host agent was slow is applied at the next tick after the events that followed it, so a stale state can overwrite a fresh one

**Priority:** Low
**Added:** 2026-09-25 (the Sonnet review of PR #556, TD-155; `grinder-ao-1`)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25

**Location:** `src/agentorc/adapters/claude_code/hook.py` (`main`: any exception but `Refused` appends the event to `events/<session>.jsonl`), `src/sessionorc/agent.py` (`_reconcile` drains the queue and `_apply_event` sets the state unconditionally).

**Why:** each hook is its own process with a 3 s call. If one call times out (the host agent busy, not down) and the next one succeeds, the second is applied at once and the first is applied at the next tick, **after** it — so the record ends on the older state. A queued `Stop` (`idle`) behind a live `UserPromptSubmit` (`working`) is the old case; TD-155 (PR #556) added one more, a queued `SessionStart` from a resume (`idle`) behind the argv prompt's `UserPromptSubmit`. Either way a working session reads `idle` until its next hook (usually seconds), and in that window the doorbell may ring it. Rare and self-healing, which is why it is Low; but a queued event is a record of the past and is applied as if it were the present.

**Resolved:** 2026-09-25 (PR #573, `grinder-ao-1`) — `hook.py` stamps a queued event `at`; `_apply_event(queued=True)` skips a queued state stamped before the last live hook (`_live_hook_at`), applying its adapter id, model and subagent delta. Held by `test_a_queued_event_older_than_a_live_one_does_not_overwrite_its_state`; design §4.2 carries the rule.

**Related:** TD-115 (the queue is for an agent that is down, never for a refusal), TD-155 (the resume case), §4.2.

## TD-153: Sessions poll for mail in foreground loops instead of going idle to be rung: the doorbell is built, and nothing tells a session it will be woken

**Priority:** High
**Added:** 2026-09-25 (raised by Paul, watching the designer loop on its inbox)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25

**Location:** `src/agentorc/skill.md` (the *Mail* section — the doorbell is named there as a thing that happens, never as the reason to end a turn), `docs/briefs/designer-ao-1.md` (*"never wait for input, never end a turn to ask a question"*, which a session reads as *never be idle*; `grinder-ao-1.md` and `manager-ao-1.md` carry no such phrase, and say nothing about waiting either), design §4.8 (the role presets the briefs are cut from) and §4.10 (*How a Claude Code session is told it has mail*), `src/agentorc/cli.py` (`ao wait`'s and `ao inbox --unread`'s reply when there is nothing), `src/sessionorc/agent.py` (`_ring_doorbells`, `_bell_blocked` — the mechanism, already right).

**Why:** the mechanism that makes waiting free exists and was never used. The doorbell (§4.10, TD-052 step 7) rings a hook-confirmed `idle` session when unread mail lands, within its wake budget; a `steer`'s bound running out mails its sender a `system` note that wakes it **uncharged** (`_lapse_or_expire`); `ao wait` blocked in the host agent returns on mail. So a session waiting on a reply has nothing to do but end its turn. The designer did the opposite for a whole run: from 2026-09-24 20:28Z to 2026-09-25 13:56Z its transcript holds **110** foreground calls of `for i in 1..9; do ao wait --timeout 60; ao inbox --unread; done` (nine minutes each, the Bash tool's ten-minute ceiling), plus background copies (three were still running when the run ended). Each return is a turn on the strongest model with the full context re-read, to learn *unread=0*; and because the session was `working` throughout, the doorbell — which rings only a hook-confirmed idle — never rang once (zero `[agentorc] you have N unread` lines in the transcript). The loop is the one thing that defeats the doorbell, and the brief pushed it into the loop: *never wait for input* was written against asking a person in the pane, and reads as *never be idle*. Nothing the session could read said *end the turn; you will be rung*.

**Resolved:** 2026-09-25 (PR #560, `grinder-ao-1`) — design §4.10 *Waiting on mail is ending the turn*; `ao --skill`'s *Mail* bullet; the five role presets and `docs/briefs/designer-ao-1.md` say *mail it, then end the turn* (the repo supplements inherit it from the templates, TD-114, so `grinder-ao-1.md` and `manager-ao-1.md` are unchanged); `ao wait` with nothing and `ao inbox --unread` with nothing end with `cli.END_THE_TURN` for a session. Held by `test_a_poll_that_finds_nothing_tells_a_session_to_end_its_turn` and `test_the_skill_and_the_presets_say_waiting_on_mail_is_ending_the_turn`. The *done when* — the designer's next run shows no loop — is a live check on `docs/user_attention.md`.

**Done when** a session that has sent a `steer` or an `ask` and has nothing else to do ends its turn, the record reads `idle`, and the reply rings it; the designer's next run shows no `ao wait`/`ao inbox` loop in its transcript; and a grinder asked in review why it is idle can point at the skill sentence.

**Related:** TD-052 (step 7, the doorbell; step 3, `wait` in the host agent), §4.10 (*How a Claude Code session is told it has mail*, the wake budget), TD-120 (the designer role and its brief), TD-072 / TD-141 (mail before wind-down: the other place a session is told what mail does to its turn).

## TD-136: Build the Inbox message page — `/inbox/<id>`, the thread, the row's controls at the foot

**Priority:** Medium
**Added:** 2026-09-24 (TD-129's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25 — slice 1 (PR #576, grinder-ao-1: the `thread` read, `ao inbox --thread`), slice 2 (PR #579, grinder-ao-2: the page). Design: §4.5 screen 6 *The message page*, §4.5a *Inbox message page*, the **keys** row (`Enter`, `Esc`, `j` / `k` on the page), mockup `InboxMessage.dc.html`.

**Location:** `src/sessionorc/agent.py` (a new person-only read, `thread {id}`: every entry whose `root` is the named entry's root, gathered across the person inbox and every record's `inbox` and `outbox`, one per id, oldest first, marking nothing — `src/sessionorc/**`, so the techlead reads that slice, §4.9b), `src/agentorc/cli.py` (`ao inbox --thread <id>`, the same read printed), `src/agentorc/ui/app.py` (the `/inbox/<id>` route: the entry from the `inbox` read by id, the thread from `thread`, the *gone* and refusal cases), a new template `inbox_entry.html` reusing `inbox_row.html`'s macros for the head, the thread rows and the controls, `inbox_row.html` (a mail row's text becomes the link to its page; `data-page` for the key), `src/agentorc/ui/static/app.js` (`Enter` on a mail row; `Esc` on the page; `j` / `k` across the list's order; the return to the ringed row), `tests/`.

**Why:** a long entry — a techlead's reading, a passed-up question with its history — is read in a scrolled box a few lines high, and the thread it belongs to is shown nowhere.

**Resolved:** 2026-09-25 (PR #576, `grinder-ao-1`; PR #579, `grinder-ao-2`) — `rpc_thread` and `ao inbox --thread`; the `/inbox/<id>` route and `inbox_entry.html`, the row's *whole entry ›* link and `data-page`, the keys (`Enter` the page, `o` Open, `Esc` / `j` / `k` / `r` / `s` / `x` on the page), the refresh after an answer. Left for later, each named in design-history §4.5a: a trail row's *re* as a link, and *pruned <t> ago* on a gone entry. Design §4.5 screen 6 *The message page* carries the lasting content.

**Done when** (1) a 2,000-character `ask` with two replies (one the person's) and an outcome reads whole on its page, the answer controls directly under it, the thread under those, oldest first with the person's reply in it, and **Reply** sends the mail the row's Reply sends and returns to the list at that row, ringed; `ao inbox --thread` prints the same entries and a session calling `thread` is refused; (2) `Enter` on a ringed mail row opens its page, `Esc` returns to the list with the filters as they were, and `j` on the page opens the next entry of the filtered list; (3) the page of a pruned id and of another host's id read their words, never a blank or a 500; (4) a permission row and a board row are unchanged; (5) `pdm run test` covers the `thread` read (root across mailboxes, one per id, the person's reply included, `pruned`) and the two failure texts; (6) TD-129 (1) is marked built.

**Related:** TD-129, TD-135 (the filtered list this returns to), TD-127 (the entry's shape, applied here when it lands), TD-124 (keys), TD-079 (outcomes, the thread), TD-125 (retention: the *gone* case).

## TD-129: The Inbox has no page for one message, and its filters are one typed box: a row is read where it is listed, and team and kind are not visible controls

**Priority:** Medium
**Added:** 2026-09-24 (Paul: *make a note to add a focused view (page) for a single inbox message*; and *we seem to have lost our filters on the inbox — they were by team and type on the left at one point*; the anchor session)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-135 (the rail and find), TD-136 (the message page), TD-137 (narrow)

**Status:** Resolved 2026-09-25 — all three built: (1) the message page (TD-136, PRs #576 and #579), (2) the filters and (3) the find (TD-135, PR #567), and the narrow layout (TD-137, PR #575); the live look is on the board line. Was: (2) the filters and (3) the find **built 2026-09-25** (TD-135, PR #567); (1) the message page built 2026-09-25 (TD-136, PRs #576 and #579); the narrow layout built 2026-09-25 (TD-137, PR #575). **Designed 2026-09-24** in a cloud session with Paul (branch `claude/kind-cerf-dg50d4`; Paul parked the entry on the board that afternoon so the designer would leave it, PR #537, and this PR takes the line off). Design: §4.5 screen 6 *The rail*, *Find*, *The message page*, *Narrow*; §4.5a *Inbox page: the rail*, *Inbox page: find*, *Inbox message page*, the **keys** row; glossary *rail*; mockups `Inbox.dc.html` (now with the rail), `InboxRail.dc.html`, `InboxMessage.dc.html`, `InboxPhone.dc.html`. **What Paul chose:** option (a), the rail, over the tree he first drew (*All → team → that team's sections*) — the sections, teams and kinds as three groups of toggles, OR'd within a group and AND'd across, every count following the picks, since his way of working the page is one team's *Needs you* rows and then the next team's; the sections filter too (*FYI items from the Guardians team* is two presses). **Option (b), the preview pane, is not designed** — a defensible default: at any width the entry opens as its page, and the pane, if ever wanted, is that page drawn beside the list; one word from Paul makes it a round. (3), the find box, is designed as the rule below says. Nothing built. **As asked 2026-09-24:** Two things. **(1) A message page.** A row is read and answered in the list (§4.5 screen 6, TD-082: one centred column, a row is a card); a long entry — a techlead reading, a passed-up question with its history, a thread — is clipped at `12em` and scrolled inside the row, and the thread it belongs to is not shown. A page for one entry, `/inbox/<id>`, reached from the row (its text, or a key on the ringed row, TD-124) and from a link in the trail: the whole entry in TD-127's shape (first paragraph, then *details* open), its thread — the question it answers, the replies on it, the outcome — its structured fields (`about`, `pr` as a link, `source`, `answered`), and the row's own controls again at the foot, so it is answered there too; Esc or **Back** returns to the list at the same row. **(2) The filters.** What the Inbox has today is what TD-069 built on 2026-09-19: one typed box, `team:name` (or `team:` for entries without one) and free text over sender, text and `about`, plus a team badge on each row that sets the team filter when clicked. Nothing was removed — the *filters on the left* Paul remembers are the Org page's host / repo / profile row and the mockup's *team: all* button, never built as controls on the Inbox — and there has never been a **kind** filter. **Paul, 2026-09-24 12:45 MDT: show the options before building** — this is a third-tier item for the designer (an `ask` with the options as answers, each with a mockup from `docs/mockups/gen.py`): **(a)** a left rail of filters with counts, the shape every mail client has — teams, kinds, sections, each a count that is also the press; **(b)** a preview pane — the list narrow, the ringed row's entry open beside it, which is TD-129's page without leaving the list; **(c)** the mobile layout for whichever is chosen: the rail becomes a sheet or a chip row, the pane becomes the page, one column under the width the pages are used at on a phone. Each option is reflected in the URL so a filtered Inbox is a link, remembered per browser, cleared by one press. **(3) The free-text box (Paul: *fix our filter box to be able to search on text*).** Measured on the live page 2026-09-24 12:50 MDT (re-measured by the fact-check at 12:55, counting only the text a reader sees): 82 of 83 rows carry every visible word in `data-find` — a mail row's is sender, text and `about`; a state row's its name, title, `doing` and the explanation it shows; a trail row's its name, kind, `how` and text; all lowercased once in `app.py` — so a word from a body does match, and the one row that does not is the identity-alarm row, whose `find` is the host and the alarm words. What fails is the match itself: one exact lowercase substring (`includes`), so two words in the other order, a `#` before a number or a comma between them find nothing; a match inside a folded section (FYI, snoozed) stays folded; and nothing says how many matched beyond the section counts. The fix, whichever shape wins: every word must match, in any order, against the row's whole visible text (every row kind), a match unfolds its section, and the count reads *n of all*.

**Location:** design §4.5 screen 6 (*Layout*), §4.5a (**Inbox page: the count, sections, team filter**; the rows), TD-124's keys rows (a key to open the ringed row's page), `src/agentorc/ui/templates/inbox.html` (`#ifilter`), `inbox_row.html`, `src/agentorc/ui/app.py` (the inbox route), `app.js` (the filter), `docs/mockups/gen.py` (screen 6)

**Why:** a person reading a long entry reads it in a scrolled box a few lines high, and a person looking for one team's questions types a filter syntax; both are the page's job.

**Related:** TD-127 (the entry's shape), TD-124 (keys), TD-082 (one column, a row is a card), TD-069 (the Inbox), TD-070 (suggested answers).

**Resolved:** 2026-09-25 (PRs #567, #575, #576, #579) — the rail, the find, the narrow layout and the message page; design §4.5 screen 6 *The rail*, *Find*, *Narrow* and *The message page* carry the lasting content.

## TD-141: Build mail before wind-down — the refusal on unread, the *mail read* row, an unread note ages out with its run

**Priority:** Medium
**Added:** 2026-09-24 (the designer; TD-072's design round, PR #529)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25. Was: Open — designed; **a build exists**: branch `td072-mail-before-winddown` (worker `tdgrind-ao-1`, 2026-09-20, suite and lint green there then; 116 lines over `src/sessionorc/agent.py`, `src/agentorc/ui/app.py`, `tests/test_mail.py`, `tests/test_ui_org.py`, two briefs and the design). Rebase it onto `main`, drop its design edits (the design now says it), and finish what is below. Design: §4.9a *The declaration is refused while the session has unread mail*, §4.2 Ready to close *mail read*, §4.10 lifecycle stages 1 and 3.

**Location:** `src/sessionorc/agent.py` (`rpc_progress` → the `none` and `restart` paths; the mail sweep's `_keep` with `dead_since`), `src/agentorc/ui/app.py` (`ready_to_close`: the *mail read* row beside *outcomes reported*), `src/agentorc/briefs/grinder.md` and `manager.md` (read the inbox before a claim and before the declaration — the grinder preset says neither today; the manager's says *first*), `tests/test_mail.py`, `tests/test_cli.py` (the briefs' words), `docs/briefs/` only if a repo supplement restates it (it should not: cadence §7, cite by section).

**Why:** on 2026-09-18 the Org showed exited sessions holding nineteen unread entries; nothing makes a worker read its inbox before it winds down, and an unread entry never ages out. The design (TD-072) says what changes; the parked branch built most of it before TD-079 landed the sibling refusal, and the two now sit side by side.

**Resolved:** 2026-09-25 (PR #577, `grinder-ao-1`) — `rpc_progress` refuses `none` and `restart` while unread (after the owed check); `_keep(dead_since=)` ages an unread `note`/`reply` out from an exit; Ready to close's *mail read* row; the grinder preset's second inbox read. Held by `test_a_session_cannot_wind_down_or_restart_with_unread_mail`, `test_an_unread_note_ages_out_with_the_run_it_was_sent_to`, `test_a_resume_inside_the_window_carries_an_unread_note`, the row's and the preset's tests. The parked branch `td072-mail-before-winddown` is deleted.

**Done when** a worker that runs `ao progress none` with unread mail is refused and told the count, reads, and is then accepted; an `exited` card shows no unread `note`s twelve hours after its exit; a resume within the window carries the note; Ready to close shows *mail read*; the grinder preset's line has its test; the parked branch is deleted; TD-072 is archived.

**Related:** TD-072 (the design), TD-079 (the sibling refusal and row), TD-066 (the crash that hid that night's mail), TD-069 (the page this keeps clean), TD-059 (the Stop-hook road, not taken).

## TD-072: Sessions exit with unread mail — nothing makes a worker read its inbox before it winds down

**Priority:** Medium
**Added:** 2026-09-18 (the anchor session; Paul's review of the Org page — *"our agents should go through them and decide they don't care about them instead of just leaving them pending"*)
**Owner:** grinder
**Kind:** build
**Pickable:** no — resolved
**Status:** Resolved 2026-09-25. Was: Open — **designed 2026-09-24 (the designer, PR #529), beside TD-079 as built (the person's 2026-09-20 condition):** §4.9a *The declaration is refused while the session has unread mail* (`none` and `restart`, naming the count and the command, beside the *owed outcome* refusal TD-079 built), §4.2 Ready to close *mail read* (and the *outcomes reported* row written in), §4.10 lifecycle: an unread `note` or `reply` on an `exited` or `closed` record ages out on the retention window from the exit, open questions untouched. The parked branch is taken as written; the Stop-hook road (TD-059) rejected — a session that ignores a note would be un-endable, and wakes are bounded by design. Steered to Paul as a default. Build: TD-141. Was: three parts proposed to Paul 2026-09-18, not yet approved one by one. Design first: §4.10 (mail lifecycle) and §4.9a (wind-down) change before code does. **Do not build it alone: it overlaps TD-079** (the Inbox as a queue — `ao progress none` refused while something is owed, and a Ready to close row), and the two must be designed together (person, 2026-09-20). **A build of all three parts is parked, unmerged, on branch `td072-mail-before-winddown`** (worker `tdgrind-ao-1`, 2026-09-20, claim dropped the same day; suite and lint green there): `ao progress none` refused while unread, naming the count; a *mail read* row on Ready to close; an unread `note` on an exited record ageing out on the retention window running from the exit — *not* dropped at the exit, because resume carries mail forward inside that window, which is the one place the build reads this entry differently; the two briefs; and the design edits to §4.9a, §4.2 and §4.10. Take from it or discard it in the design round — the refusal and the Ready to close row are exactly where it collides with TD-079.
**Location:** `docs/briefs/tdgrind-ao-1.md` and the samscrape team's briefs (when a worker reads mail), `src/sessionorc/agent.py` (`rpc_progress` → `_out_of_work`, and the mail sweep), the Ready to close checks (§4.2; the check names live in `src/agentorc/repoconfig.py`, `ready_when`, and the phase-1 subset that is evaluated is `ready_to_close()` in `src/agentorc/ui/app.py`), design §4.10 and §4.9a

**Why:** on 2026-09-18 the Org page showed exited sessions still holding unread mail — `tdgrind-1` 19 entries, `tdgrind-3` 2 — almost all `note`s from siblings (*claimed: TD-289…*) and from the lead. Part of that night's count was a defect, since fixed: any reply longer than the client's 64 KiB line limit failed (TD-066, recorded there as the `list` reply; fixed by PR #209), and three sessions reported to the person inbox between 00:36Z and 01:47Z that bare `ao inbox` died the same way on one long message — *Separator is found, but chunk is longer than limit* — while `ao inbox --unread --json` still worked. A worker told *19 unread* on every `ao` reply had a default read command that crashed. But the mechanism allows it on any night. Reading is what marks mail read (`ao inbox`, design §4.10); every `ao` reply carries the unread count (`cli.py`, `unread_line`); and nothing else happens. The lead's brief says to read the inbox at the top of every round (`docs/briefs/orchestrator-ao-1.md`, step 1); the grinder's brief says to read it before claiming a reference (`docs/briefs/tdgrind-ao-1.md`, *Read your inbox before claiming*) and never again — not before it declares itself out of work, which is the moment the 19 were left behind. An unread entry never ages out (§4.10), so a claim note that was stale within the hour sits on a dead record until a person forgets the session — and it is noise on the page that TD-069 is about to make the place a person works from.

**Resolved:** 2026-09-25 (PR #577, with TD-141) — the design is §4.9a *The declaration is refused while the session has unread mail*, §4.2 *Mail read*, §4.10 lifecycle stages 1 and 3; the build is TD-141's.

**Done when** a grinder that tries to wind down with unread mail is refused and told why, Ready to close shows the row, an exited session's card shows no unread `note`s, the briefs say when to read, and §4.10 / §4.9a / §4.2 say all of it.

**A third road, from the neighbours survey (TD-059, 2026-09-20):** `multi-agent-shogun`'s Claude Code Stop hook refuses to end a turn while the agent has unread mail and feeds the mail back in — the refusal at the moment the turn ends rather than at `ao progress none`. It would live in the Claude Code adapter (§4.3), and it covers a session that never declares; weigh it in the design round beside part 2.

**Related:** design §4.10 (lifecycle, *an unread entry never ages out*), §4.9a (`out_of_work`), §4.2 (Ready to close), TD-052 (mail), TD-053 (wind-down), TD-066 (the crash that hid that night's mail), TD-069 (the page this keeps clean), TD-071 item 2.

## TD-069: One place to work from — the Inbox as the list of everything that needs a person

**Priority:** Medium
**Added:** 2026-09-18 (the anchor session; Paul's review of the Org page). Widened the same day: from *the dialog is too narrow* to *the one place to work from*.
**Owner:** grinder
**Kind:** build
**Pickable:** no — step 4 designed; its build is TD-140, and this entry archives with it
**Status:** Resolved 2026-09-25 — see *Resolved* below. Was: Open — **step 4 designed 2026-09-24 (the designer, PR #528):** §4.5a *Inbox row: FYI · Put on the board* — on FYI rows only (a `note`, a closed question, a trail row), never on an open `ask` or `steer`, whose *later* is Snooze, since deleting an open question declines it (§4.10) and parking is not declining; a small form (the sender's repo's board or a pick, the text editable, Due as Snooze offers) → the write-back's one **add** (§4.4: the only line this system adds to a board, at a person's press) → the entry deleted. The narrowing from *a mail row* is steered to Paul. Build: TD-140. Was: **step 0 (the mail rules) is built, 2026-09-19**: the `steer` kind with its required `default` and *lapsed*, an `ask` to the person with no bound that never expires and is asked alone, `closed_reason` on every close path (`replied` | `declined` | `asker_gone` | `lapsed` | `go_with_it` | `expired`), `system` notes with their wake rules, the person's `inbox_snooze` / `inbox_pause` / `inbox_resume` / *Go with it*, deleting an open question as declining it, depths that count what is unanswered, the `team` stamp, `ao msg --kind steer --default`, and the briefs' needed / steering / FYI rule. **Step 1 (the page, mail only) is built 2026-09-19**: `/inbox`, full width, the three sections in their order, the snoozed list with **Unsnooze**, every §4.5a row control (Reply · Delete · Snooze; Reply · Go with it · Pause / Resume; Dismiss) over thin `/api/person/<action>` routes that call each RPC caller-less, the team and free-text filter remembered in the browser, and **the count**: one server-side split (`inbox_sections` in `src/agentorc/ui/app.py`) that the page and the top bar's poll both read, so *what is waiting on a person* means the same in both places. The `personbox` dialog is retired with it. **Step 2 (states as rows) is built 2026-09-19**: `state_rows` in `src/agentorc/ui/app.py` turns the card views the Org is rendered from into rows — a pending permission (Allow / Deny, the card's own `data-act` and the hook channel, nothing parsed), a question, `stalled?`, `limited`, an exited session with unpushed work (what Ready to close says), and TD-077's identity alarms — joined into **Needs you** by the same `inbox_sections`, ordered *what is on the tool's clock first, then oldest first across states and mail together*; one row renderer per kind in `inbox_row.html`; the team and free-text filters cover them; the page and the poll render them from one fresh `list` — **one** per request, mail and states sharing it (review of PR #251) — and a permission answered here leaves at once (answering a second time says *already answered* rather than failing). The rows and the Org's needs-you badge read **one predicate** (`state_kind`), so a `needs-you` record with an empty, malformed or unknown `pending` is a plain *needs you* row rather than a session the Org counts and the Inbox does not list. **Two gaps this step found, neither improvised over:** (a) **no Snooze on a state row.** §4.5a allows one on `stalled?` and unpushed work, but a state has no entry to carry `snoozed_until` — mail's lives on the mail entry — and the design names no place to keep it. Proposed wording, for the anchor to confirm: *a person may set a state aside with **Snooze** on `stalled?` and on unpushed work; it is kept on the record as `attention_snoozed_until`, home-owned like `controllers`, set and cleared by a person-only RPC (`attention_snooze`) refused to every session as `inbox_delete` is, and it hides the row and uncounts it until that time — never a permission, a question or `limited`, which are on the tool's clock.* (b) **`limited` has no Switch profile… / Wait**: §4.5a gives the row the card's controls, and neither is built on the card either, so the row says what the cap is doing and offers **Open** until they are. **Steps 3–4 remain** (board items, *Put on the board*), so this entry stays Open. **Step 3 is built across two PRs, 2026-09-23, both for the anchor:** the write-back, `board_edit` on the host agent (grinder-ao-1, PR #474), and the rows (grinder-ao-2, PR #472): due board items as counted *Needs you* rows on the page, the poll and the top bar, read through dev-cadence's `nudge_user_attention.py --report --due-only --json` over the host roster's boards, cached 60 s and read again after an answer, a reader failure said in a note, with **Snooze ▾** (+1 day · +1 week · a date, counted from today) and **Done** calling `board_edit` caller-less. **Merge #474 first**: #472's controls call its RPC, and #472 is rebased after it to say so in §4.4's *no page offers it yet*. The techlead held the rows back until the write-back existed (2026-09-23): a counted row nobody can clear from the page breaks §4.10's *a row leaves only by an answer*. Direction agreed with Paul 2026-09-18 (work from one spot, filter it by team, a row opens the session that needs you; the Org page keeps its own needs-you marks for a person who prefers the grid). Board items appear in the Inbox, not mail on the board, and the three questions the proposal ended on are answered below (2026-09-18); the rest of the shape is the proposal the design round starts from. **The design landed 2026-09-19** (§4.10 *What a person is asked*, §4.5 screen 6, the §4.5a **Inbox** rows): an `ask` to the person never expires, `steer` is a new kind with a default and a bound, snooze on person-inbox entries, and the page in three sections. The build is next, in the steps below.
**Location:** `src/agentorc/ui/app.py` (`inbox_sections`, `/inbox`, `/api/person/inbox`, `/api/person/{action}`), `src/agentorc/ui/templates/inbox.html`, `inbox_rows.html` and `inbox_row.html` (one row renderer per kind), `base.html` (the top bar's **Inbox** link; the `personbox` dialog it replaced, retired step 1), `src/agentorc/ui/static/app.js` (`AO.inbox`, the row controls); design §4.5 screen 6 (**Inbox**, step 1 built) and screen 7 (**Attention**) and the **Due** strip, both unbuilt

**Why:** three things ask for a person today, in three places. (1) **A session's state** — a permission prompt, a question in the terminal, a usage limit, a stall: on the session's card, found by scanning the grid. (2) **Mail to the person** (`ao msg person`): in a dialog off the top bar, a fixed-width column at `max-height: 60vh`, where sessions write paragraphs — on 2026-09-18 its five entries were two defect reports, a wind-down notice and a venv failure. (3) **Board items** (`docs/user_attention.md`, per repo, dated): printed into a session's context by a hook, and nowhere in the UI — 39 were due across this machine that day, most of them weeks stale. The Urgent first sort was the first answer to (1) and team cards made it moot (dropped 2026-09-18, PR with this entry); nothing answers all three.

**Proposed shape.** An **Inbox** page at `/inbox`, full width, the top bar's count on it. It is a **view over the three sources, and none is copied into another**: a state lives on the session's record and leaves the list the moment it is answered; mail lives in the host agent's person inbox (not durable, §9 invariant 13); a board item lives in its repo's git history. One row per thing, each with the controls of its kind, in place:
- *permission* — what is asked, the countdown, **Allow / Deny** (the card's own controls, §4.5a);
- *question*, *stalled?* — the text, **Open** (Focus on that session: the link Paul asked for);
- *limited* — the reset time, **Switch profile / Wait**;
- *ask* — the whole text, its suggested answers (TD-070), **Reply**, **Delete**; *note* — the text, **Dismiss**;
- *board item, overdue or due today* — the text, its repo, **Snooze / Done** (the agent write-back §4.4 already specifies), **Open** on the session that wrote it when it is still here.
Order: what is on a clock first (a permission's countdown, an `ask`'s bound), then states, then due board items, then notes. **A team filter** narrows all of it: a state and a message carry their session's `team` badge; a board item carries its repo, which `org.yml`'s projects map to the teams that work it.

**Board items in the Inbox, not mail on the board** (the anchor's recommendation). The board is a file in git, written by PR, dated, and survives this machine; mail is chatter a session sends without a commit and the host agent may lose. Showing due board items in the Inbox costs a read; putting mail on the board would mean a commit per message and a board nobody can keep small. The door between them is one way and explicit: **Put on the board** on a mail row writes the entry (with a `Due:`) through the same write-back, and deletes the mail — the rule sessions already follow (*anything with a date on it belongs on the board*), given to the person too. This makes the Inbox what §4.5's unbuilt **Due** strip was going to be, so the strip is struck rather than built; the **Attention** tab stays the *whole* board — undated and future items included — which the Inbox deliberately is not.

**Decided with Paul, 2026-09-18** (the three questions the proposal ended on):
- **One count and one view.** A due board item counts toward the top bar's number like a state or a message — no second, quieter count. What a person does not want to handle now they **snooze**, and a snoozed row leaves the list and the count until its time. For a board item that is the board's own snooze (edit the `Due:` date, §4.4's write-back); for mail it is new — a `snoozed_until` on the person-inbox entry, which §4.10 must gain; a permission prompt is on the tool's clock and cannot be snoozed; whether a question or a stall can is for the design round.
- **The stale backlog is Paul's to clear, not the page's to hide.** 39 items were due on 2026-09-18 because the boards have not been manageable, which is what this page is for; he works through them or snoozes them once it exists. No bulk-snooze control is planned for that reason alone — add one only if the first pass shows it is needed.
- **Exited with unpushed work is a row.** Only a person resolves it, and today it is a small flag on a dead card (`orchestrator-ao-1`, *305 unpushed*, 2026-09-18). The row says what Ready to close says (§4.2) and opens the session's details; it goes when the work is pushed or the session is forgotten.
- **Board items get suggested answers too** — see TD-070, which now covers both; the board's half is a change to dev-cadence's entry format, not to this repo.

**What belongs in it, and the countdown (raised by Paul 2026-09-19; the split below was decided by Paul the same day: needed, steering — his word — and FYI. Design first: §4.10's sentence on an `ask` to the person, then the kinds, then the page).** Today an `ask` to the person carries the same bound as any other (24 h by default, `mail.ASK_BOUND`) and expires *read or not* (§4.10), so the person is shown a countdown and the asker then moves on. Paul: *either user input is legitimately needed or it isn't* — and the two cases want opposite rules, which one `ask` with a timer blurs. The split: (1) **Needed** — the session cannot or must not go on without the answer. It **does not expire**: it stays until answered, snoozed or deleted, the asker goes on with other work or declares itself out of work, and an answer that arrives after it exited is the next session's to pick up (the board's `Decided:` path, TD-070, when it must outlive the record). (2) **Steering** (Paul's word) — a preference the session can proceed without: *I will do X unless you say otherwise*. It carries **its default and its timer**, shown as such — a section of the Inbox of its own, below what is needed and **outside the count** — and at the bound the session does what it said; nothing is marked expired, because nothing failed. The suggested answers of TD-070 are its buttons, the default marked. (3) **FYI** — a `note` to the person: never counted; whether it is shown at all, or only behind a filter, is open. The count is then *what is needed*, which is what makes one number worth looking at. Design first: §4.10's *an `ask` to the person expires read or not, like any other* is the sentence this changes, and the briefs have to say which kind to send — a worker that marks every preference *needed* brings the old inbox back.

**Left over from step 2, 2026-09-20:** ~~`/api/person/inbox` answers a bare 503 when the host agent is down~~ **done 2026-09-20 (PR #271, a worker):** the poll answers 200 with `{agent_down, why, needs: null}` — `null` is *not known*, which is not *nothing is waiting* — the page's banner is always drawn and hidden until the poll turns it on, and the rows on screen are left alone rather than blanked or silently frozen (design §4.5 *there is no silent failure path*). A state row's Snooze gets its home from TD-079 step 1b — `attention_snooze`, per record and row kind, in the home's own attention store — which **merged as PR #269** — and the page half of it (the Snooze on `stalled?` and unpushed work, and the snoozed state row's Unsnooze) is built with TD-079 step 2, so this leftover is **done 2026-09-20**.

**Done when** the top bar's Inbox opens a full-width page listing states, mail and due board items with their controls in place, a team filter narrows it, a row opens the session it is about, a 2,000-character message reads without a scroll box, and §4.5 / §4.5a describe it. Steps, each its own PR: (0) **built 2026-09-19** — the mail rules — no bound on an `ask` to the person, the `steer` kind with `default` and *lapsed*, *declined by the person*, `snoozed_until`, `ao msg --kind steer --default`, and the briefs' needed / steering rule; (1) **built 2026-09-19** — the page with mail only, in its three sections with the count and the team filter (retires the dialog); (2) states as rows; (3) board items and the write-back; (4) *Put on the board*.

**Related:** design §4.10 (the person inbox), §4.5 screens 1, 6 and 7, §4.4 (board write-back), §4.5a (the top bar **Inbox** row, the **Due** strip rows), TD-052 (mail), TD-070, cadence §3 (the board).

**Resolved:** 2026-09-25 (TD-140 slice 2, PR #580; slice 1 PR #578) — step 4, the last, built by TD-140; steps 0–3 as its status says.

## TD-140: Build *Put on the board* — the write-back's one add and the FYI row's form

**Priority:** Low
**Added:** 2026-09-24 (the designer; TD-069 step 4's design, PR #528)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25 — see *Resolved* below. Was: Slice 1 built 2026-09-25 (PR #578, grinder-ao-1: `board.add`, `board_edit` with `action: add`, the entry dismissed after the commit); slice 2, the Inbox button and form, remains. Design: §4.5a *Inbox row: FYI · Put on the board*, §4.4 *Board write-back* (the one add).

**Location:** `src/sessionorc/board.py` (`write_back` gains `action: add` — a new line at the top of the open items, in the board's format, committed as `agentorc: board <item head> (from <entry id>)`; refused on the same conditions as an edit — a checkout off its default branch or dirty — touching nothing), `src/sessionorc/agent.py` (`rpc_board_edit` takes `add` with `text`, `due`, `context`, `entry`; the person's alone; then `inbox_delete` of the entry in the same call so a failed commit deletes nothing), `src/agentorc/ui/app.py` and `inbox_row.html` (the button on FYI rows only, the form: board pick defaulting to the sender's repo, text, Due +1 day · +1 week · a date), the Inbox mockup already carries the button (`docs/mockups/gen.py`), `tests/`.

**Why:** the rule sessions follow — *anything with a date on it belongs on the board* — has no press for the person: a note that says *check this Friday* can only be dismissed or left in FYI, and the board is edited by hand. TD-069 designed the door on 2026-09-18 and built the write-back's edits (step 3); the add is the last piece of the Inbox as the one place to work from.

**Fix:** two slices; the first touches `src/sessionorc/**`, so the techlead reads it (§4.9b). (1) `board.write_back(..., action="add")` and the RPC: the line is `- [ ] <today> (session <sender's name> on <host>, or n/a) — <text>. Context: <about or none>. Due: <date>.`, inserted above the first open item (after the `Format:` line and any header the reader skips); the commit message names the entry's id; the entry is deleted only after the commit lands; refused with the checkout's reason otherwise. (2) The page: the control on FYI rows only (`note`, closed questions, the trail), the form, the row leaving on success, the refusal drawn in place; the new board item appears as a counted row when due through the existing reader (step 3), nothing new polled.

**Done when** a `note` in FYI can be put on its repo's board with a Due date in three presses, the board's commit names the entry, the entry is gone from FYI, the item is a counted *Needs you* row on its due date with Snooze and Done working on it, an open `ask` row shows no such button, and TD-069 is archived.

**Related:** TD-069 (the design; steps 0–3 built), TD-123 (the board's whole view stays dev-cadence's report), cadence §4 (a session's own board lines go by PR, as before).

**Resolved:** 2026-09-25 (TD-140 slice 2, PR #580; slice 1 PR #578) — the Inbox's *Put on the board*: the button on FYI and trail rows, the form (`board_add.html`), `/api/person/board` with `action: add`; slice 1 the write-back's add. Design §4.5a *Inbox row: FYI*, §4.4.

## TD-168: Build *when it is read*: `mail.read_when`, the pair on the record's view, the composer's sentence that changes with the kind, `read_when` on every `ao msg` reply

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-158's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25 — see *Resolved* below. Was: Open — nothing built. Design §4.10 *When it is read: the sentence the sender sees*, §4.5a **Message** (the last sentences), §4.7 *Mail* (the reply's last line); mockup `Message.dc.html`.

**Location:** `src/sessionorc/mail.py` (`wake_budget_spent` is beside where `read_when` goes; the seat's `asks_waiting`), `src/sessionorc/agent.py` (`rpc_msg`'s reply dict — `wake_budget_spent`, `unreachable` already there; `_view` / the pushed record), `src/agentorc/ui/app.py` (`view`: `seat`, `seat_when`, `scraped`, the wrap-up and gate notes — the same facts the sentence reads), `src/agentorc/ui/templates/base.html` (the `#mailbox` dialog: a line under `#mailkindrow`), `src/agentorc/ui/static/app.js` (`AO.compose`: takes the record's `read_when` pair and swaps the line on the kind's `change`), `src/agentorc/cli.py` (`cmd_msg`: print `read_when` after `advice`), `src/agentorc/skill.md` (one clause: the reply says when it is read).

**Why:** TD-158's *Why*: nothing on the screen said the seat comes on a question.

**Fix:**
1. **`read_when(record, kind, now) -> str`** in `sessionorc.mail`: the table's cases in its order, the page's words for states, the seat's two sentences by kind (`reply` as `note`), the bound clause on an `ask`, the budget clause only when the sender is a session (a parameter, `person: bool`). `src/sessionorc/**` is a held path: the techlead reads this PR.
2. **On the record's view**: `read_when: {ask, note}` computed at `_view` time for every record the UI receives — two short strings, no request at open; a seat with nobody in it (the placeholder card) gets its pair from the seat's definition.
3. **The composer**: the sentence under the kind selector, from the pair; swapped on the kind's `change`; the Reply dialog shows the `note` one. Text only.
4. **`rpc_msg`** returns `read_when: {id: sentence}` per addressee, computed after delivery with `person=(sender == PERSON)`; **`ao msg`** prints each after the `advice` line, `read_when` under `--json`.
5. **Tests:** `read_when` on a record per case (twelve cases, both kinds where they differ, the session's budget clause); the composer's swap under node as `test_ui_keys.py` runs `app.js`; the CLI's printed line on a fixture reply.

**Done when** TD-158's *Done when*: a person opening Message on an on-call seat reads, before typing, that a note will not fill it and an ask will, and switching the kind changes the line; and `ao msg` to an exited member ends with *read when it is resumed, or started again under this name*.

**Related:** TD-158 (the design), TD-153 (what a session is told about being woken), TD-157 / TD-167 (the *i* marks: the same idea at the button), TD-152 (the `scheduled` sentence), §4.9b (the seat's trigger), §4.10 (the doorbell's order, the budget's refill).

**Resolved:** 2026-09-25 (PR #583, grinder-ao-1) — `mail.read_when`, `read_when` on `msg`'s reply and the `{ask, note}` pair on the record's view, the composer's line (`#mailwhen`, `AO.whenLine`), `ao msg`'s last lines; `tests/test_read_when.py`. The `scheduled` row waits for TD-152's state.

## TD-143: The Focus Reports panel reads as a to-do list: a `claimed` row with its PR open looks like an unstarted claim, and Drop beside it lets a person let go of work in review

**Priority:** Medium
**Added:** 2026-09-25 (Paul, on designer-ao-1's Focus page: *I was confused by it and hit "Drop" on 126 and 127 thinking they were todo, but apparently they were done (already had a PR) — lets add a TD to make this more clear*; a cloud session)
**Owner:** grinder
**Kind:** build
**Pickable:** no — designed; the build is TD-150, and this entry archives with it

**Status:** Resolved 2026-09-25 — see *Resolved* below. Was: Open — **designed 2026-09-25 (the designer, PR #546), the recommendation (a)–(f) confirmed as written:** §4.5a *Focus side panel → Reports* (grouped by state, the PR beside a claim in review from the entry, the derived entry or the branch's PR; Drop behind *more ▾* on an in-progress row only, its confirm naming the consequence; the *i* mark; the trail row names the PR) and §4.10 (the `system` note on a person's drop). **Undo** on the trail row: **none** (Paul, 2026-09-25 — the session claims again on its own, told by the note). Build: TD-150. Was: **asked 2026-09-25.** What happened: the designer's Reports panel listed `TD-069 claimed 5h 51m Drop`, `TD-072 claimed`, `TD-035 claimed`, `TD-126 claimed`, `TD-127 claimed`, each with a **Drop** button, above `TD-120 done → #525` and two more; TD-126 and TD-127 each had an open PR (#531, #532) waiting in a stack, which the panel did not say — a declared claim carries no `pr` until `done --pr`, and the derived channel's PR for the same reference is not drawn beside the declared entry. Paul read the claimed rows as a to-do list and pressed Drop on two; the confirm (*Drop TD-127? It is recorded as dropped by you.*) says what is recorded, not what it means: the lease ends, the record reads *dropped — dropped from Focus*, the designer's inbox is not told, and its PRs stay open with no claim pointing at them. The panel's one explanation (*What the session declared, plus what the agent derived from its branch and PRs (dashed). Drop records that a claim was let go.*) is a note under the list, read after the press. Three faults: **(1)** a claim in review is drawn like a claim not started; **(2)** Drop is a first-class button on every claimed row, though letting go of another session's work is rare and consequential — the rest of the pages put such acts behind a confirm that says the consequence, or behind `more ▾`; **(3)** nothing says who a drop affects (the lease another session may now take; the session, which is not told) or how it is undone (the session claims again; a person cannot).

**The recommendation for the round:** (a) a claimed row shows its PR when one exists — the entry's own `pr`, else the derived entry on the same reference, else a PR from the record's `tdNNN-*` branch (`sessionorc.reports` already asks `gh` for it) — as *claimed · in review #532*, and the panel groups its rows *in progress*, *in review*, *done*, *dropped*, so the states read at a glance; (b) Drop leaves the row's face — behind the panel's `more ▾`, never a primary button — and is not offered while a PR from that claim is open; (c) its confirm names the consequence: *let go of TD-127's claim: the lease ends and another session may take it; the branch and PR #532 stay; only designer-ao-1 can claim it again*; (d) a person's drop is told to the session by a `system` note, as every act on its work is (§4.10), so a worker learns its lease is gone before it pushes into a reference somebody else has taken; (e) the explanation becomes the panel's *i* mark at its heading (§4.5 screen 6's pattern); (f) the trail row names the PR too. **Open for Paul:** an **Undo** on the trail row (the person re-declares the claim on the session's behalf — a declaration by the person, as Drop is; §9 invariant 14 says only the session writes its own word, so this is a new exception) or none, since the session can claim again and (d) tells it to.

**Location:** design §4.5a *Focus side panel → Reports*, §4.8 (report channels; the lease), §4.10 (`system` notes), `src/agentorc/ui/static/app.js` (the Reports panel, `data-act="drop"`, its confirm), `src/agentorc/ui/templates/focus.html` (the note under the list), `src/agentorc/ui/app.py` (the drop route, *dropped from Focus*), `src/sessionorc/reports.py` (the derived PR beside a declared claim), `src/sessionorc/agent.py` (`rpc_progress`: the note to the session on a person's drop).

**Why:** one confused minute let go of two design PRs' claims; the Reports panel is the one place a person sees a session's work, and it must say what state each piece is in and what a press does to it before the press.

**Related:** TD-056 (a claim is a lease), TD-028 (declared and derived), TD-045 (a derived claim's PR), TD-126 and TD-127 (the two claims dropped), TD-124 (keys: none on this panel yet).

**Resolved:** 2026-09-25 — designed by PR #546; built by TD-150 (PRs #586, #590 and PR #591).

## TD-150: Build the Reports panel by state — the PR beside a claim in review, Drop behind more with its consequence, the note to the session

**Priority:** Medium
**Added:** 2026-09-25 (the designer; TD-143's design round, PR #546)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-25 — see *Resolved* below. Was: Partly done — **slice 1 built 2026-09-25 (grinder-ao-1, PR #586)**: the groups (`AO.reportGroups`), *claimed · in review #n* from the entry's own `pr`, the heading's *i* mark, Drop under the panel's `more ▾` on declared in-progress claims with the consequence confirm. **Found on review (PR #586):** a declared claim's PR has only two sources — its own `pr`, and its branch's — because §9 invariant 10's upsert never lets a derived entry share a declared one's reference; and a session that claimed and then opened its PR without `--pr` reads *in progress* until the branch source lands, which makes that source the one that matters. Remaining: the branch's PR on the record as `review_pr` (from `sessionorc.reports`, a held path; `AO.reportGroups` already reads it), slice 3 (the `system` note). **Slice 2 built 2026-09-25 (PR #590)**: the drop route refuses a declared claim with a PR (`review_pr`, the panel's rule), 409 in words — merged or open alike, since the record cannot tell them apart until the branch's PR state lands with the branch source; *the trail row names the PR* is moot, since no drop now has one. Design: §4.5a *Focus side panel → Reports*, §4.10 (the `system` note on a person's drop).

**Location:** `src/agentorc/ui/static/app.js` (the Reports panel: the four groups, *claimed · in review #n* with the PR as a link from the entry's `pr`, the derived entry on the same reference, or the branch's PR; Drop moved under the panel's `more ▾`, drawn only on an in-progress row, `data-confirm` naming the consequence with the reference, the PR and the session's name), `src/agentorc/ui/templates/focus.html` (the note under the list becomes the heading's **i** mark), `src/agentorc/ui/app.py` (the drop route: refuse while a PR from that claim is open, saying which; the trail row names the PR), `src/sessionorc/reports.py` (the PR for a record's `tdNNN-*` branch exposed beside the declared entry — it already asks `gh`), `src/sessionorc/agent.py` (`rpc_progress`: a person's `dropped` sends the session the `system` note, waking it as a person's act does), `tests/test_ui.py`, `tests/test_agent.py`.

**Why:** on 2026-09-24 Paul read the designer's claimed rows as a to-do list and pressed Drop on two claims whose PRs were open in a stack; the panel said neither that they were in review nor what Drop would do, and the session was not told (TD-143).

**Fix:** three slices; the last touches `src/sessionorc/**`, so the techlead reads it (§4.9b). (1) The panel: groups, the PR beside a claim in review from the three sources in that order, the *i* mark, Drop under `more ▾` on in-progress rows only, the consequence confirm; a test that a claimed entry with a derived PR on the same reference is drawn *in review* with the number. (2) The route: refuse a drop while a PR from that claim is open (the same three sources), the trail row with the PR. (3) The note: `rpc_progress` with a person caller and `status: dropped` files the `system` note to the session (§4.10's words), one per drop; a test that the session's inbox holds it and its wake fires.

**Done when** a session with a claimed reference and an open PR on its `tdNNN-*` branch shows *claimed · in review #n* under *in review* with no Drop on its face; Drop on an in-progress row sits under `more ▾`, its confirm names the lease, the branch and who can claim again, and the session's inbox holds the note after the press; the panel's heading carries the *i* mark and the note under the list is gone; TD-143 is archived.

**Related:** TD-143 (the design), TD-056 (a claim is a lease), TD-028 (declared and derived), TD-045 (a derived claim's PR), TD-124 (keys: none on this panel yet).

**Resolved:** 2026-09-25 — PR #586 (the panel), PR #590 (the refusal), PR #591 (the `system` note and the branch's PR as `review_pr`), grinder-ao-1. Found on the way: a declared claim's PR has two sources, not three (§9 invariant 10); a declared claim's own `pr` is refused merged or open alike, since only `review_pr` can tell.

## TD-176: Build the team-first Org and the Repo page — repo facts and the doing log on the host agent, the rollup, the team card's summary and compact cards, `/repo/<name>`, `ao repo`

**Priority:** Medium
**Added:** 2026-09-25 (designed with Paul in a cloud session, after TD-156's Focus review: *a quick view of what is outstanding, what is in progress, and what is waiting on me, per repo a team services — and whether the team is balanced*)
**Owner:** grinder
**Kind:** build
**Pickable:** yes

**Status:** Resolved

**Paul's direction after the design (2026-09-25, on the rendered shapes):** the **team-first** shape — the team card carries the summary (the repo's numbers, *on now* in full, *needs you* with its answer, the reader's queue, the manager's word) and a member's card shrinks to name, state and buttons; interactive sessions may sit on a team (TD-160) and are marked as the person's on the compact card; the Org's Due section is gone (the Inbox's). A design pass on both screens is on a Design canvas, https://claude.ai/artifact/EYzJc7aHysXsCyaNMoz3xj (private to Paul), rendered as `docs/mockups/reviews/2026-09-25-org-team-first-design.png` and `2026-09-25-repo-page-design.png`; the design text for the team-first Org (a compact card in §4.5 *The card's anatomy*, the summary block in place of the strip's line, the §4.5a rows) is the next round, on Paul's word on the canvas.

**Paul's second round on the canvas (2026-09-26), drawn on the same canvas and rendered as `docs/mockups/reviews/2026-09-26-org-team-first-design.png` and `2026-09-26-repo-page-design.png`:** the Repo facet carries **charts** — open entries by priority and by kind as stacked bars (pies were asked and weighed; a stacked bar is the part-to-whole form the dataviz method gives, a pie the anti-pattern for close values), an *open | closed* toggle on the entries, PRs opened and closed in the window as two sized blocks with the numbers inside, and a **window picker** (day · week · month) on the facet; the second facet is **TDs in motion** — every reference a member claims, as *adding* (a design-first claim), *grinding* (a claim without a PR) or *reviewing* (a claim with an open PR); the third is a **live Doing feed** of the team's `ao doing` calls (time, doer, words), which **swaps out for Needs you** while a permission or question waits, with a toggle between them; the team header's state chips are dropped (the member cards say it); and an **Org rollup** strip under the title — the needs-you / stalled / limited pills, TDs in motion by phase, PRs opened and closed in the window, open PRs with the reader's wait, and *for you* — with no live feed at that level. **What the build needs beyond §4.4 *Repo facts* as designed:** closed-in-window entries from the ledger's git history (an entry whose section left the file), PR opened / closed dates per window from `gh pr list --state all`, a bounded **doing log** on the host agent (the record keeps only the latest `doing`, §4.8; the feed needs the last n calls per team), the manager's brief asking for `ao doing` per round step, and the "for you" kind (`Owner: paul` or `Kind: decision`). The design text for all of this is the next round, on Paul's word on the canvas.

**Reviewed by Sonnet again on the team-first shape, three rounds (2026-09-26), READY at round 3, every item taken:** the six-row rule made the full card's, the ring's keys on a compact card answering through the facet, the artboard's Other list and PR order (round 1; its "branch reverts shipped work" was a two-dot diff against a moved main, answered by merging main in); `state:` in the filter box, *review* as an open PR with the merged case said, the pickers' memory, a repo no team services, the readings in the offline table (round 2); the strip's name gone from `ao repo`, §10 and this entry (round 3).

**Reviewed by Sonnet, three rounds in the cloud session (2026-09-25), READY at every round, each round's items taken:** a repo two teams share is drawn on one card (the `repo_teams` convention), the PR row's reader standing names its source and its blank case, *draft* beside the standing (round 1); this entry's Location naming `merged_prs`'s failure semantics rather than `_prs`'s and the `/events` arm, the artboard's Open ledger button and a missing PR (round 2); a fixture age (round 3). Nothing overruled.

**Location:** `src/sessionorc/agent.py` (the tick: a `_read_repos` beside `_count_seats`, the `repos` RPC, the `repos` event), a new `src/sessionorc/ledger.py`, `src/sessionorc/reports.py` (`_prs` has the `gh pr list` call shape, but swallows a failure into `[]`; the open-PR read follows `merged_prs`, which answers `None` when `gh` could not be asked — the *never zero* rule), `src/agentorc/ui/app.py` (`team_groups` — the summary's data per group and the rollup's sums; a `/repo/{name}` route; the board rows filtered to a repo, from `board_items`), `src/agentorc/ui/templates/group_head.html` / `org.html` / `card.html` (the summary's facets, the rollup, the compact card), a new `templates/repo.html`, `static/app.js` (the `repos` and `doing` events re-rendering the facets), the `/events` handler in `app.py`, which forwards only `session`, `gone` and `usage` today and needs a `repos` arm, `src/agentorc/cli.py` (`ao repo`).

**Why:** nothing on the Org says what a repo holds — the team header counts sessions, the reader's queue and the needs-you mark, and the Inbox is the person's queue, not the repo's state. Paul wants to see, per repo, the open PRs (and whether they are piling up ahead of the reader), the pickable and design-first ledger entries (what a grinder could take next), the board items due (what waits on him), and what each member is on, in one glance from the Org, with the lists a click away — and, once those numbers are visible, a rule a manager can apply to them (TD-177).

**Fix — the slices, each its own PR:**
1. **Repo facts on the host agent** (§4.4): `sessionorc.ledger` (the entry-header reader — id, title, priority, owner, kind, pickable, with `tests/test_ledger.py`'s regex moved in so the test reads the reader; the four kinds; *opened* / *closed* per window from the file's git history); the PR read (`gh pr list --state all --json …` per remote, in a thread, every `USAGE_EVERY`; the open list and the per-window *opened* / *closed* counts; an outage keeping the last reading with `error` and `failed_at`, `merged_prs`'s semantics, never `_prs`'s `[]`); `repos.json`; the `repos` RPC and event (a new arm in the `/events` handler, which forwards only `session`, `gone` and `usage` today). Done when `ao repo --json` prints every reading for every registered checkout and a `gh` outage reads as *could not look*, never zero.
2. **The doing log** (§4.8): the host agent appends every `doing` call to a per-team ring of fifty, `doing.jsonl`, the `doing` RPC and event; `ao repo` prints the window's calls. Done when the calls of one team appear in order with time and caller and the fifty-first drops the first.
3. **The team card's summary and the compact card** (§4.5a *team card: summary*, *Repo facet*, *TDs in motion*, *Answer needed / Doing*, *card: compact*): `team_groups` gains the repo readings, the phases (from the members' `progress` and the ledger reading) and the pending answers; `group_head.html` loses its chips and gains the three facets; `card.html` gains its compact form for a member of a live team; `app.js` re-renders the facets on the `repos` and `doing` events, keeps the selectors and the toggle per browser, and flips the toggle to *answer* on a new permission. The bars are plain HTML (flex segments with a 2 px surface gap, the counts inside where they fit, a legend under), no chart library. Done when Paul's Org shows a team as the artboard draws it and every number is a link.
4. **The Org rollup** (§4.5a *Org: rollup*): the four facets under the title from the groups' sums; the Agents pills filter the page; the window picker shared with the teams' or its own, remembered per browser. Done when the counts match the teams' below at every delta.
5. **The Repo page** (§4.5 screen 11): `/repo/{name}`, the team's facets by the same partial, the four lists, the doing filters, the *i* marks, narrow layout; `RepoPage.dc.html` regenerated from the built page's shape. Done when every facet link lands on its list and Snooze / Done / Reply write the board as the Inbox does.
6. **`ao repo`** (§4.7) and **the manager's brief**: `ao repo [name] [--all] [--json]`; the manager brief's *A round* gains one `ao doing` per step (§4.8 *the doing log*). Done when a manager's round shows in a team's Doing feed.

**Related:** §4.5 screen 1 *The Org, team-first* and screen 11, §4.4 *Repo facts*, §4.7, §4.8 *the doing log*, §4.9b (the techlead's queue), §4.5 screen 6 (the board reader the Inbox runs; the rail's URL the *for you* link uses — TD-135), TD-159 (whether the ledger reader belongs to dev-cadence), TD-177 (the balance rule), TD-146–148 (the Settings page's Repos cards, a sibling of this page), TD-156 (the Focus review this followed).

**Resolved:** 2026-09-26 — built in six slices by an interactive session with Paul: PR #595 (repo facts), PR #596 (the doing log), PR #597 (the team card's summary and the compact card), PR #598 (the rollup), PR #599 (the Repo page), PR #600 (`ao repo`'s standing, holdings and board, the manager's brief). Each slice had a Sonnet review, every finding taken. Beyond the design: the PR read is two reads and the windows roll (§4.4 history); the doing log's read is `doing_log`, apart from the `doing` write. Not built: the Inbox's overdue count beside the rollup's *in the Inbox*.

## TD-167: Build the help: `help.py` as the one table, the *i* marks on the team card, the Focus header and the exited banner, the `title` tooltips from it, the Help page, the overlay's link, and the test that holds it to §4.5a

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-157's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-26. Was: Open — nothing built. Design §4.5 screen 10 *Help*, §4.5a the ***i*** mark row, the *Help page* row, the **?** overlay row's last line, and *The help text* under the table; mockups `Help.dc.html`, and the marks on `Main.dc.html` and `Focus.dc.html`.

**Location:** a new `src/agentorc/ui/help.py` (the table: `(where, control) → paragraph`, plus the groups per mark and the screens' order), `src/agentorc/ui/templates/group_head.html` (Start, Wind down, Stop now, Forget all, the fold: their `title=` and the mark), `card.html` (Forget, Close session, Message…, Switch profile…: `title=`), `focus.html` (Wrap up, Kill, Message…, Close: `title=`; the header's mark; the exited banner's mark beside Forget), the Inbox's mark macro (the same `<button>` shape and script — reuse it), a new `help.html` at `/help` in `src/agentorc/ui/app.py`, `src/agentorc/ui/static/app.js` (the `?` overlay's last line; the marks' open / close remembered per browser as the Inbox's are), a new `tests/test_help.py`.

**Why:** TD-157's *Why*: a person meets Forget and Start on a card whose state they are trying to change, with no way to learn the difference but the design document.

**Resolved:** 2026-09-26 (PR #604, `grinder-ao-2`) — `src/agentorc/ui/help.py`, `help_mark.html`, `help.html` at `/help`, the titles in `group_head.html`, `card.html` and `focus.html`, the marks' delegated press and memory in `app.js`, the **?** overlay's last line; `tests/test_help.py` holds `help.py` to design §4.5a *The help text*, which carries the lasting content.

**Done when** TD-157's *Done when*: a person at a concluded team's card can learn, without leaving the page, that Forget drops the record and keeps the worktree, and that Start closes the concluded sessions and runs the team again from its definition — and the sentence they read is §4.5a's, held so by the test.

**Related:** TD-157 (the design), TD-148 (the Settings page's marks, the nearest built shape), TD-162 (which session to message: its own line per role, on the Message composer), TD-124 (the `?` overlay), TD-095 (the card's quiet foot: no mark on it), TD-071 (fixed text in the source, never a session's).

## TD-157: What does this button do? An *i* mark or a help page for every control on Org and Focus, from §4.5a — Forget, Start, Wind down and the fold first

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul, at a concluded team's card: *questions arise with what "forget" and "start" really mean — when and why would I want to do those?*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-167

**Status:** Resolved 2026-09-26. Was: Designed 2026-09-25 (the designer): design §4.5 screen 10 *Help*, §4.5a the ***i*** mark row (team card header, Focus header, exited banner), the *Help page* row, the **?** overlay's last line, and *The help text* — the list under the table, twelve paragraphs in the shape *what it does · when you would press it · what it does not do*, the one source the marks, the `title` tooltips and the page draw, held equal to `help.py` by a test. Settled: (a) the marks per control group with the page behind them, and the paragraph's first sentence as every control's `title`; the Org card carries no mark (its foot is quiet, TD-095); (b) the source is a list in §4.5a, not a fourth column — the rows are a specification, the paragraphs are for the person; (c) the first set is Start, Wind down, Stop now, the fold, Forget, Forget all, Kill, Close, Wrap up, Resume, Message…, Switch profile…; (d) the Inbox's mark shape carries the keyboard; (e) mockups `Help.dc.html`, the marks on Main and Focus; (f) a named member's Forget stays — Forget drops a run's record, never the seat, and Start fills the seat again. The build is TD-167; this entry archives with it. The design is PR #562, stacked on #558; the surface (marks with a page behind them, against a page alone) and (f) went to Paul as one steer (m-15dfabd2b4dc), and #562 merges after #558 at the steer's bound (12 h from 2026-09-25 19:10 MDT) unless he says otherwise — the next designer run merges both if this one has ended. **What was:** nothing designed. What exists: §4.5a is the table of every control (a control not in it does not exist, CLAUDE.md), and the UI carries `title=` tooltips on some buttons (`group_head.html`'s fold and Start, the unattended badge) and *i* marks on two pages — the Inbox's section headings (§4.5a *Inbox: section heading, the i mark*: the paragraph that says what the section is and counts) and the Settings page's values (*Settings page: read-only values and the i mark*: where each comes from and when it is re-read). Nothing tells a person, at the button, what **Forget** or **Start** does to the thing it is on or when they would want it.

**Location:** design §4.5a (the table, whose third column is the source), §4.5 (*The card's anatomy*, the foot), `src/agentorc/ui/templates/card.html`, `group_head.html`, `focus.html` (the buttons and their `title=`), `src/agentorc/ui/static/app.js` (confirms), `docs/mockups/gen.py`.

**Why:** the buttons are named for what they do to a record — Forget, Start, Wind down, Stop now, Kill, Close, Resume, New session here — and a person meets them at the moment they matter, on a card whose state they are trying to change, with no way to learn the difference between two of them but the design document. The two Paul asked about are the two whose consequence is least visible: **Forget** removes the record — the card and its history under Resumable — and keeps the worktree and the pane's run log readable until then, which is why a card carrying the dirty / unpushed flag is named apart in *Forget all* (§4.5a); a person wants it when a finished session's card is clutter and its work is merged or gone, and never as a way to stop anything (it refuses a live record). **Start** on a concluded team is not a message: it is `ao team start` — every check first, then the concluded sessions closed under the wrap-up's safety check, then the manager and members created from the definition with their briefs (§4.9a); a person wants it to run the team again, and mails a session (**Message**) when they want a running one to do something. That distinction is exactly what a button label cannot carry and what an *i* mark or a confirm's first line can.

**What the design round has to settle:** (a) **the surface** — an *i* mark per control group (the card's foot, the team card's header, the Focus header) opening the same paragraph shape the Inbox's marks use: *what it does · when you would press it · what it does not do*; or a **Help** page reachable from the top bar that lists every control by screen; or the confirm's first line carrying the *what* (Start's already does: *It first closes designer-ao-1 …*); likely the marks with the page behind them; (b) **the source** — the text is written once, in §4.5a's third column or a fourth *help* column, and the page and the marks are generated or checked against it by a test, so the table stays the one place a control is defined; (c) **the first set** — Forget, Forget all, Start, Wind down, Stop now, Kill, Close, Resume, Wrap up, Message, the fold, Switch profile; (d) the keyboard: an *i* mark is a tab stop and its text is its `aria-describedby`; (e) the mockups; (f) **whether a member the definition names should offer Forget at all** (Paul, 2026-09-25, at the wound-down ao-grind: *if a team is defined to have those roles, should they be forget-able?*) — today an on-call seat offers no Forget while the definition names it (§4.5a *team card: Forget all*) and a member does; Forget drops the record and its mail and nothing spins back up, since Start recreates from the definition, so the round says whether a named member's Forget stays, goes, or is folded into Start and Forget all.

**Done when** a person at a concluded team's card can learn, without leaving the page, that Forget drops the record and keeps the worktree, and that Start closes the concluded sessions and runs the team again from its definition, and the sentence they read is §4.5a's.

**Related:** §4.5a (the table; *Inbox: section heading, the i mark*; *Settings page: read-only values and the i mark*), TD-148 (the Settings page's *i* marks, the nearest built shape), TD-156 (the end-of-session review this sits beside — the same team card), TD-095 (the card's foot), §4.9a (what Start and Wind down do to a team).

**Resolved:** 2026-09-26 — built by TD-167 (PR #604); design §4.5 screen 10 *Help* and §4.5a *The help text* carry the lasting content.

## TD-178: The Org rollup's overdue count beside *in the Inbox*

**Priority:** Low
**Added:** 2026-09-26
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-26. Was: open
**Location:** `src/agentorc/ui/templates/rollup.html` (the Needs you facet), `src/agentorc/ui/static/app.js` (where `[data-inbox-needs]` is copied from the top bar), `src/agentorc/ui/app.py` (the board reader's due items)

**Why:** design §4.5 screen 1 has the rollup's **Needs you** facet show *in the Inbox* "with the overdue count"; TD-176 slice 4 (#598) drew the Inbox count, copied in from the top bar, and not the overdue count, and §4.5a's **Org: rollup** row says so. Left out of TD-176's close (#600) and found at the session's close.

**Resolved:** 2026-09-26 (PR #606, `grinder-ao-2`) — `overdue_n` from `inbox_sections`, the rollup's *m overdue* in `rollup.html`, kept live from the Inbox poll in `app.js`; `test_the_rollup_counts_the_board_items_past_their_date_beside_in_the_inbox`. Design §4.5a *Org: rollup* carries the lasting content.

**Related:** TD-176 (archived), design §4.5 screen 1 *The Org, team-first*, §4.5a **Org: rollup**.

## TD-171: Build when to message whom: `message:` on the preset with the built-in defaults, the composer's first line, the Message control's `title`, the team header's who-for-what line, `ao roles`

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-162's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-26. Was: Open — nothing built. Design §4.8 *A role says when to message it*, §4.5a **Message** (the composer's first line, the `title`) and *team groups* (**who for what**); mockups `Main.dc.html`, `Message.dc.html`.

**Location:** `src/agentorc/repoconfig.py` (`ROLE_KEYS` gains `message`; the built-in presets' defaults beside their `label:`; the length check as `label:`'s), `src/agentorc/cli.py` (`ao roles` prints it), `src/agentorc/ui/app.py` (the record's view carries `message_line` resolved from its role; `team_groups` builds the header's line from the definition's roles and the sessions holding them), `src/agentorc/ui/templates/base.html` (`#mailbox`: a line under `#mailtitle`), `card.html` / `focus.html` (the Message control's `title`), `group_head.html` (the second line), `src/agentorc/ui/static/app.js` (`AO.compose` takes the line).

**Why:** TD-162's *Why*: six Message buttons and no reason to pick one; the wrong pick costs a wake and a round of passing up.

**Resolved:** 2026-09-26 (PR #605, `grinder-ao-2`) — `message:` in `repoconfig` (the built-ins' defaults, the check), `ao roles`, the view's `message_line`, `teamrun.role_holders` and `who_for_what` for the team header, the composer's first line (`AO.roleLine`) and the Message controls' `title`. The header draws the sentences, not the design's former example phrases (design-history §4.8). Design §4.8 *A role says when to message it* carries the lasting content.

**Done when** TD-162's *Done when*: a person hovering Message on the techlead's card reads what it is for, and the composer repeats the line above the text box; and the ao-grind header reads *questions → manager-ao-1 · PRs and the architecture → techlead-ao-1 · a grinder about its own card*.

**Related:** TD-162 (the design), TD-158 / TD-168 (the when-read line beneath it), TD-157 / TD-167 (the `title` as the mark), TD-160 / TD-169 (the person in the team), TD-097 (the seat card's Message…).

## TD-162: When to message whom: a `message:` line per role, read on the Message control's *i* mark and in the composer, so a person knows the manager from the techlead from a grinder

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: *add info icons to each team member's message button to say when you would want to message them*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-171

**Status:** Resolved 2026-09-26. Was: Designed 2026-09-25 (the designer): design §4.8 *A role says when to message it* (`message:`, one sentence, built-in defaults for the five presets — manager, techlead, grinder, hunter, auditor — and a role's own line in its `roles:` entry for a `designer`; the definition's line, never the session's), §4.5a **Message** (the composer's first line, the control's `title`) and *team groups* (the **who for what** line), mockups `Main.dc.html` (the header line) and `Message.dc.html` (the composer's line). Settled: (a) the field on the preset; (b) the composer's first line and the control's `title` — no mark on every card's Message (the Org card is quiet, TD-095; the team header answers the choice before a card is picked); (c) the team-level line, generated; (d) no, a session may not rewrite it. The design is PR #570; the no-mark-on-the-card choice was to go to Paul as a steer, but the person inbox refused it as full (Paul is away, §4.10), so the choice is on the board (the designer's line of 2026-09-25, merged as PR #571) with a Due date, and #570 merges after 2026-09-26 08:15 MDT unless he says otherwise — the next designer run merges it if this one has ended. The build is TD-171; this entry archives with it. **What was:** nothing designed. Paul suggested the profiles as the home for the line; a profile is tool · account · model (§4.2a) and says nothing about a job, so the role preset (§4.8) is the candidate — the round confirms.

**Location:** design §4.5a **Message** (the composer), §4.8 *Role names* and the presets, §4.9 (manager, techlead, members: who does what), §4.9b (an `ask` fills the seat); TD-157 (the *i* mark per control), TD-158 (what the composer says about when the message is read); `src/agentorc/ui/templates/base.html` (`#mailbox`), `src/agentorc/ui/static/app.js` (`AO.compose`).

**Why:** every card's Message opens the same composer, and the design's answer to *who do I message* is spread over §4.9: the manager for what the team works on, the techlead for a PR or the architecture, a grinder only about its own card. A person at the Org sees six Message buttons and no reason to pick one over another; the wrong pick costs a wake budget and a round of passing up. The role knows its own job — the brief says it — so one sentence per role, *message me when …*, drawn where the choice is made, lets the person choose before typing. TD-158 says *when* the message will be read; this says *whether this is the one to send it to*.

**What the design round has to settle:** (a) **the field** — `message:` on the role preset (built-in presets carry a default: manager, techlead, grinder, hunter, auditor, designer), overridable in org and repo `roles:`, one sentence; (b) **the surfaces** — the *i* mark on the card's Message and in `more ▾` (TD-157's mechanism), and the line at the top of the composer under the addressee's name, beside TD-158's when-read line; (c) **a team-level pointer** — on the team header, *questions to the manager; PRs to the techlead*, generated from the same fields; (d) whether a session may rewrite its own line (the entry's view: no, it is the definition's, in the spirit of §9 invariant 9 — a preset sets defaults at start and is a badge afterwards; the round decides); (e) the mockup.

**Done when** a person hovering the Message *i* on the techlead's card reads what it is for, and the composer repeats the line above the text box.

**Related:** §4.5a **Message**, §4.8, §4.9, §4.9b, TD-157, TD-158, TD-160 (the person in the team who needs this most).

**Resolved:** 2026-09-26 — built by TD-171 (PR #605); design §4.8 *A role says when to message it* carries the lasting content.

## TD-170: Build prompt chips: `prompts:` on the preset through the layers and `/api/roles`, the chips beside Send (a press sends, Shift+press fills) and beside the Opening prompt, `ao roles`

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-161's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-26. Was: Open — nothing built. Design §4.8 *A role has saved prompts*, §5 `roles:`, §4.5a *Focus composer* **prompt chips** and *New session* **prompt chips**, §4.5 *Phone layout*; mockups `Focus.dc.html`, `NewSession.dc.html`.

**Location:** `src/agentorc/repoconfig.py` (`ROLE_KEYS` gains `prompts`; the shape check — a list of `{label, text}`, label one line ≤ 24 characters, text non-empty; `resolve_role`'s layering, the key replaced whole), `src/agentorc/cli.py` (`ao roles`: the labels per role), `src/agentorc/ui/app.py` (`/api/roles` carries `prompts`; the Focus route puts the record's role's list on the page), `src/agentorc/ui/templates/focus.html` (the chips in the composer's button row, before Attach), `new.html` (a row before Opening prompt), `src/agentorc/ui/static/app.js` (a chip press → the Send path with the text; Shift → fill and focus; the role pick rebuilds New session's chips), `app.css` (the wrapping row in narrow mode).

**Why:** TD-161's *Why*: the browser and the phone have no keyboard worth typing a prompt into; the prompts a person repeats are a list in a file.

**Resolved:** 2026-09-26 (PR #607, `grinder-ao-2`) — `prompts` in `repoconfig` (`_prompts`, whole-list layering), `ao roles`, `/api/roles`, `role_prompts` and the Focus chips beside Send, New session's chips filling the Opening prompt. Design §4.8 *A role has saved prompts* carries the lasting content.

**Done when** TD-161's *Done when*: Paul, on his own team session's Focus from a phone, presses one chip and the session starts the turn with that text, and the chip's text is readable in `org.yml` or `.agentorc.yml` and nowhere else.

**Related:** TD-161 (the design), TD-160 / TD-169 (the person's session the chips are for), TD-027 (send's confirmation), TD-096 (the composer closed on an unattended session), TD-003 (the phone's narrow Focus), TD-157 / TD-167 (the chip's `title` as its mark).

## TD-161: Saved prompts as chips beside Send: fixed text from the role preset's `prompts:`, typed by a press, for the jobs a person starts by hand today

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: *lets talk about adding on-demand buttons for interactive sessions and what that might look like*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-170

**Status:** Resolved 2026-09-26. Was: Designed 2026-09-25 (the designer): design §4.8 *A role has saved prompts* (`prompts:` — `{label, text}`, layered as every key is, replaced whole per layer, verbatim text, no substitution), §5 (the `roles:` example's `plain: {prompts: …}`), §4.5a *Focus composer* **prompt chips** (a press is a Send of the text, reading as Send reads; Shift+press fills the composer) and *New session* **prompt chips** (a press fills the Opening prompt), §4.5 *Phone layout* (the chips' row above the composer); mockups `Focus.dc.html` and `NewSession.dc.html`. Settled: (a) the text lives on the preset, in `org.yml` or the repo's `.agentorc.yml`, nowhere else; (b) chips beside Send, interactive sessions only, a press sends; (c) the same list fills the opening prompt on New session; (d) a chip is a Send and nothing more. The design is PR #568; the press-sends-against-press-fills choice went to Paul as a steer, and #568 merges at its bound (12 h from 2026-09-25 19:55 MDT) unless he says otherwise — the next designer run merges it if this one has ended. The build is TD-170; this entry archives with it. **What was:** nothing designed.

**Location:** design §4.5a *Focus composer* (**Send**, **Attach**), §4.5a *Inbox row: state* (*Reopen and push* — the precedent: a first prompt the page wrote, fixed text in the source, never anything a session said), §4.8 role presets, §5 (`roles:` in `.agentorc.yml` and `org.yml`), §4.5 screen 5 (Commands — the button that runs a script, phase 4, TD-123); `src/agentorc/ui/templates/focus.html` (the composer), `src/agentorc/ui/static/app.js`.

**Why:** the design has two on-demand buttons: a Commands button runs a script as a `kind: command` session, and a role preset starts a session shaped for a job. Neither presses a prompt into a running interactive session. What a person types by hand into their own session, again and again — *review PR n*, *sweep stranded work*, `/cadence`, *what is waiting on me* — is a saved prompt, and the rule for one already exists in *Reopen and push*: fixed text from the source, typed by the page, never something a session said. Claude Code's own slash commands cover the case at the keyboard; the button's worth is the browser and the phone, where a person steers a session without a keyboard to type into.

**What the design round has to settle:** (a) **where the text lives** — a `prompts:` list on the role preset (org and repo `roles:`, layered as presets are), each `{label, text}`, so a grinder's chips differ from a plain session's; (b) **the surface** — chips beside Send on the Focus composer, drawn only on an interactive session (an unattended session's composer is closed, TD-096), each press filling the composer or sending outright, and whether the chip reads **Steer** while a turn is in flight as Send does; (c) **on New session** — the same list offered as the first prompt, beside the brief the preset fills; (d) **bounds** — a chip is a Send and nothing more: no grant, no schedule, no state on the record; (e) the §4.5a rows and the mockup.

**Done when** Paul, on his own team session's Focus from a phone, presses one chip and the session starts the turn with that text, and the chip's text is readable in `org.yml` or `.agentorc.yml` and nowhere else.

**Related:** §4.5a *Focus composer*, *Inbox row: state* (Reopen and push), §4.8, TD-123 (Commands, the other button), TD-160 (the session the chips are for), TD-157 (an *i* mark per chip is the same mechanism).

**Resolved:** 2026-09-26 — built by TD-170 (PR #607); design §4.8 *A role has saved prompts* carries the lasting content.

## TD-172: Build Members… on the team card: the one-line text edit of `org.yml` with re-parse and restore, the live create of one member and the wind-down of one, the dialog, the exited banner's line and `ao team --skill`'s *one member back*

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-163's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved 2026-09-26. Was: Open — nothing built. Design §4.9 *Add or remove a member from the team card*, §4.9a *One member back, today*, §4.5a *team card: Members…* and *Members dialog*, ADR 2026-09-25 (the addendum); mockups `Members.dc.html`, `Main.dc.html`.

**Location:** `src/agentorc/org.py` (`load`; a new `edit_members(path, team, add=…|remove=…) -> str` that edits the text, re-parses, restores on failure — the one writer of `org.yml`), `src/agentorc/teams.py` (`plan`; a `plan_member(org, team, name, host)` that returns the one spec), `src/agentorc/ui/app.py` (`/api/teams/<t>/members` GET and POST; the create of one member as the start route creates one; the wind-down of one as Wrap up does; `team_groups`: the button's presence, the repo-defined note), `src/agentorc/ui/templates/group_head.html` (**Members…**), `base.html` (the dialog), `src/agentorc/ui/static/app.js`, `focus.html` / `app.js` (the exited banner's line: *to bring it back into the team unattended: Resume with changes… and tick Unattended*), `src/agentorc/team_skill.md` (§6 *Stop it*: one member back).

**Why:** TD-163's *Why*: a third grinder or a second designer is a hand edit and a restart today, with nothing on the page saying the definition and the run differ.

**Resolved:** 2026-09-26 (PR #608, `grinder-ao-2`) — `org.edit_members` (with `_set_count`, the field and never text that looks like it), `teamrun.members_view` / `add_member` / `remove_member`, `/api/teams/<t>/members`, the card's **Members…** and its dialog, the exited banner's line, `ao team --skill` §6; tests in `test_org.py`, `test_cli_teams.py`, `test_ui_org.py`. Design §4.9 *Add or remove a member from the team card* carries the lasting content.

**Done when** TD-163's *Done when*: Paul adds a grinder to ao-grind from its card while it runs, the member appears under the manager without a restart, and `org.yml` shows the member with the comment block above it intact.

**Related:** TD-163 (the design), ADR 2026-09-25 §5, TD-146–148 (the Settings page keeps to settings), TD-157 / TD-167 (Forget on a defined member), TD-160 / TD-169 (a person as a member), TD-081 (Resume in place), §4.9a (Start on a concluded team).

## TD-163: Add or remove a member from the team card: a control that edits the team's definition, beside the Settings page's Teams section or apart from it

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: *a button on the team card to add/remove a member, e.g. a grinder, that would change the team definition*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-172

**Status:** Resolved 2026-09-26. Was: Designed 2026-09-25 (the designer): design §4.9 *Add or remove a member from the team card* (the four points), §4.9a *One member back, today* (f), §4.5a *team card: Members…* and the *Members dialog* rows, the settings audit ADR's dated addendum before its open decisions, mockup `Members.dc.html` and the headers of `Main.dc.html`. Settled: (a) `org.yml`'s `members` edited as text in place — a `count:` bumped or one line added or removed, comments kept, re-parsed, refused when not one line; a repo-defined team is a PR's; (b) at once on a live team — one member's create under the manager, one member's wind-down — and the definition only on a stopped one; (c) **Members…** on the team card, not the Settings page; (d) the record stays until Forget; (e) the mockup; (f) written in §4.9a, and the build puts it on the exited banner and in `ao team --skill`. The design is PR #572; the card-against-Settings-page choice is on the board for Paul (the designer's line of 2026-09-25, extended here — the person inbox is full), and #572 merges after 2026-09-26 08:30 MDT unless he says otherwise — the next designer run merges it if this one has ended. The build is TD-172; this entry archives with it. **What was:** nothing designed. It crosses a line drawn today: the settings audit (ADR 2026-09-25 §5) says *a definition says what a team is and belongs in its file, by hand or by PR; a setting is a number a person turns* — and its §2 calls the org file *a team editor in waiting*. This is the first control that edits a definition from the page, and the round has to say so in the ADR's terms.

**Location:** ADR `docs/decisions/2026-09-25-settings-audit.md` §5 and its open decisions, design §4.5 screen 8 (Settings: **Teams** — schedule, until, reserve; no members), §4.5a *Org: team card* (Start / Wind down / Stop now, Forget all), §4.9 (a team definition: manager, techlead, members with role, name, lane, brief), §5 (`org.yml` `teams:`; a repo's `.agentorc.yml` `teams:`), §4.4a (the org file is read per call, clients only, never the agent); `~/.agentorc/org.yml`; TD-146–148 (the settings file, replica and page).

**Why:** growing or shrinking a running team is the one change a person makes by watching it — a third grinder contends on the ledger, a second designer is needed for a review week — and today it is a hand edit of `org.yml`, then `ao team start` to pick it up, with nothing on the page that says the definition and the run now differ. A member is a definition, not a setting, so the Settings page as designed does not hold it; but the act is a person's, one press, on the team's own card, which is where every other team control lives.

**What the design round has to settle:** (a) **what is edited** — `org.yml`'s `teams.<team>.members` (add: role, name, lane, brief from the role's defaults; remove: by name), written by the client that serves the page as `ao` would, with the file's comments preserved or the edit refused when they cannot be; a repo-defined team (`.agentorc.yml`) is a PR's and the control says so; (b) **when it takes effect** — on a running team, add creates the member at once under the manager (the same create `ao team start` does for one member) and remove is a Wind down of that one member; on a stopped team, the definition only; (c) **the surface** — **Members…** on the team card's header beside Start, a dialog listing the definition's members with add and remove, the same shape as the Focus **Members** view; or the Settings **Teams** section gaining a members table — the ADR's line argues for the card; (d) **what a removed member's record does** — it stays a card until Forget, as any exited member's does; (e) the mockup and the §4.5a rows; (f) **the docs on bringing one member back today** — Paul asked 2026-09-25 whether an `ask` to the exited designer or manager would start it without the grinders. It does not: mail to an exited record is delivered and waits (§4.9b), and only a *seat* is filled on an ask; a member comes back by a person's **Resume with changes…** on its own card with *Unattended* ticked (Resume alone starts it attended, §4.5a), which supersedes the record in place and keeps its mail, or by the team's Start, which brings the whole definition. Nothing on the page or in §4.9 says this beside the team's Start, and the round writes it where the person looks — the team card's *i* mark (TD-157) or the exited member's banner — and the `README`'s *Run it*.

**Done when** Paul adds a grinder to ao-grind from its card while it runs, the member appears under the manager without a restart, and `org.yml` shows the member with the comment block above it intact.

**Related:** ADR 2026-09-25 §5 (definition versus setting), TD-146–148 (the Settings page), §4.5a *Org: team card*, §4.9, TD-157 (Forget on a defined member — the same question from the other side), TD-160 (a person as a member).

**Resolved:** 2026-09-26 — built by TD-172 (PR #608); design §4.9 and the §4.5a *Members* rows carry the lasting content.

## TD-179: Two temp-dir test fixtures sit in the machine's repos roster, so the live host agent reads repo facts for them

**Priority:** Low
**Added:** 2026-09-26 (found at the promote of #600: `~/.agentorc/repos.json` named them beside the real repos)
**Owner:** anchor
**Kind:** build
**Status:** Resolved 2026-09-26.
**Location:** `~/.config/dev-cadence/repos.txt` (the `repos_registry` default, `src/sessionorc/hosts.py`); `Agent._refresh_repos` in `src/sessionorc/agent.py`

**Why:** the host's repos registry is dev-cadence's machine roster, and it ends with `/tmp/tmp.4dWahA9hY9/fixture2/consumer` and `/tmp/tmp.4dWahA9hY9/fixture3/consumer`, written 2026-09-23 00:29 (the file's mtime). The temp dir holds a copy of dev-cadence (`c/`) beside the two fixtures, each a `consumer` checkout with a `src.git` origin: a sync run under a temp root that wrote to the real roster because `DEV_CADENCE_REG_DIR` / `XDG_CONFIG_HOME` were not pointed at the temp dir. No checked-in dev-cadence test names `fixture2`, so it was likely an ad-hoc run by a session that night. Since TD-176 slice 1 (#595) the host agent reads PRs and the ledger for every roster line, so it now runs `gh` in those fixtures every five minutes, keeps their readings in `repos.json`, and `_board_root` accepts their boards as boards of a known repo. Once `/tmp` is cleaned, the registry still lists them as checkouts that fail every read.

**Related:** TD-176 (the repo facts), design §4.4 *Repo facts*; `docs/claude-memory/scratch-worktree-tests-import-main-checkout.md` (the same pattern: test state reaching the live machine).

**Resolved:** 2026-09-26 (PR #612 recorded the cleanup): Paul removed the two fixture lines from the roster and the host agent dropped them from `repos.json`. The leak went to dev-cadence as its TD-068 (dev-cadence PR #161): `sync.sh` refuses to add a consumer under a temp dir to a roster outside one, and the readers skip a roster path that no longer exists. That covers step (3) from the source side, so agentorc's reader keeps its current behaviour.

## TD-186: A restart's own race trips the restart ceiling, and the ceiling mark never lifts

**Priority:** High
**Added:** 2026-09-26 (found when Paul asked why ao-grind read *running* with every member idle)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_crash_restart`, `_wanted_restart`, `RESTART_CEILING` / `RESTART_WINDOW`, `_replay`)

**Why:** grinder-ao-1's record carries three restarts in four seconds: `{2026-09-25T20:14:19Z, wanted}`, then `{20:14:21Z, crash, error: "…/worktrees/grinder-ao-1 already has agent session grinder-ao-1 (claude-code, outside agentorc, busy); anchor rule"}` and the same again at 20:14:23Z. So the wanted restart worked: the new run is the record created at 20:14:19Z, and it went on to merge #580–#592. But on the next two ticks the crash rule read the record as crashed, and its replay was refused because the fresh run it had just started already held the worktree. Two failed replays count toward the ceiling by design, so `restart_ceiling {at: 20:14:25Z, count: 3}` was written into a healthy session. That mark is never cleared (`_wanted_restart` returns on any `s.restart_ceiling`, and §4.5a says the tick never lifts it). When the member declared `restart_wanted` at 23:05Z with its work pushed, the tick skipped it. It sat for a day, grinder-ao-2 went out of work behind it (the remaining grinder entries are in grinder-ao-1's files), and the team read *running* with all members idle.

**Resolved:** 2026-09-26 (PR #628) — an exit event carrying another run's tool id is ignored (`_apply_event`), rule 1 waits `RESTART_SETTLE` after a restart that succeeded (`_just_restarted`), and a clean `restart_wanted` runs past a ceiling whose window has emptied (`_window_full`); design §6 rules 1–2 and the §4.5a *Inbox row: restart* say so; tests in `tests/test_wanted_and_nudge.py` and `tests/test_crash_restart.py`.

**Related:** TD-103 (the restart rules, slice 5 the Inbox row), TD-083 (`restart_wanted`), TD-115 (the exit-hook grace), TD-187, TD-188; design §6 *Keeping a team running*, §4.5a *Inbox row: restart*, §9 invariant 2 (the anchor rule).

## TD-183: Clicking the team card collapses or expands it

**Priority:** Medium
**Added:** 2026-09-26 (Paul: *have the team cards themselves be clickable to expand or collapse them*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-194
**Status:** Designed 2026-09-26 (the designer): design §4.5 screen 1 (*Any team folds, and its header is the press*), §4.5a *team card: fold* (the new row), *team groups*, *Org: keys*, *team header ✉ n* (the fold's help text changes with the build); mockup `OrgTeamFirst.dc.html` (a live team folded, the fold button on the live headers), rendered `docs/mockups/reviews/2026-09-26-td183-folded-live-team.png`. Settled: the header row is the press, its controls, links and marks excepted; the *n sessions* button stays as the keyboard's control; any team folds; a folded live row keeps the counts and every mark; the choice is per team, per browser, and stands across state changes; `f` and a header stop in the ring. **One choice is Paul's to turn**: a needs-you member rings the folded row and does not open it (the alternative: the row opens itself) — sent as a steer (`m-db0c6cb316ac`), which lapsed at its bound on 2026-09-27 with no word against, so the default stands. The build is TD-194; this entry archives with it.
**Location:** `src/agentorc/ui/static/app.js` (`syncTeams`, the fold keyed `fold:<team>`), `src/agentorc/ui/templates/group_head.html`

**Why:** the only fold today is the *n sessions* button (§4.5a *the fold*), and `syncTeams` folds only a team with nothing live: *a live team never folds*. A page with several running teams can't be tidied, and the fold is a small button when the whole header is the natural target.

**Resolved:** 2026-09-27 (PR #641, TD-194's build) — the design is design §4.5 screen 1 *Any team folds, and its header is the press* and §4.5a *team card: fold*.

**Related:** TD-181 (what an unfolded team shows), design §4.5 screen 1, §4.5a **team groups**, *the fold*, *Org: keys*.

## TD-194: Build the team card's fold: a click on the header folds any team, the folded live row with its counts and marks, the ring's header stop and `f`

**Priority:** Medium
**Added:** 2026-09-26 (the designer, from TD-183's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Open — nothing built. Design §4.5 screen 1 (*Any team folds, and its header is the press*), §4.5a *team card: fold*, *Org: keys*, *team header ✉ n*; mockup `OrgTeamFirst.dc.html`.
**Location:** `src/agentorc/ui/static/app.js` (`syncTeams`: `folded` loses its `!+sec.dataset.live` clause and takes the default from the team's state when nothing is stored; the header's click handler, which ignores a press that lands on a button, a link, an input or a mark, or that ends a text selection; the keys table), `src/agentorc/ui/templates/group_head.html` (the fold button on every team with sessions, `aria-expanded` / `aria-controls`; the counts and the ✉ mark on any folded team), `src/agentorc/ui/static/app.css` (the pointer, the hover tint, the amber ring on a folded row), `src/agentorc/ui/help.py` (the fold's text)

**Why:** TD-183's *Why*: a page with several running teams cannot be tidied, and the fold is a small button where the whole header is the natural target.

**Resolved:** 2026-09-27 (PR #641) — design §4.5a *team card: fold* now reads as built; `AO.teamFolded`, the header's press and the `f` key are in `src/agentorc/ui/static/app.js`, the header's markup in `group_head.html`, and `tests/test_ui_keys.py` holds the default and the stored choice. The live look is on `docs/user_attention.md`.

**Done when** TD-183's *Done when*: a click on a team's header folds and unfolds it, live or not, and the choice survives a reload.

**Related:** TD-183 (the design), TD-192 (what an unfolded wound-down team shows), TD-156 (f) (where the button sits), TD-124 (the keys).

## TD-181: An unfolded team with nothing live shows its member cards but no summary

**Priority:** Medium
**Added:** 2026-09-26 (Paul, at the dc-grind card after its wind-down: *the unfolded card should show the same info as the running team — some items will become stale and that's ok*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-192
**Status:** Designed 2026-09-26 (the designer): design §4.5 screen 1 (*Unfolded, it is a running team's card*), §4.5a *team card: summary*, *card: compact*, *team groups* (the fold's help text changes with the build, TD-192 step 4). Settled: the same three facets and compact members as a running team; the third facet opens on Doing; nothing is marked stale, the header's *stopped* / *wound down <t> ago* dates it; folded, the one row is unchanged; the rollup still sums live teams only. Obvious from Paul's words and the entry's *Fix*, so landed with a note to him. The build is TD-192; this entry archives with it. **What was:** Paul set the shape; the design rows and the build remained.
**Location:** `src/agentorc/ui/app.py` (the groups builder: `summary = team_summary(…) if team != NO_TEAM and live else None`), `src/agentorc/ui/templates/team_summary.html`, `org.html`

**Why:** the team summary (TD-176 slice 3) is built only for a team with a live member. When dc-grind wound down on 2026-09-26 all three sessions exited. The card folded to one row as §4.5 screen 1 says, and pressing *3 sessions* unfolded the member cards with no summary: no TD or PR bars, no TDs in motion, no Doing. samscrape-grind, concluded but with idle members, kept its summary right below. The repo facts don't depend on anyone being live, and a wound-down team is when the person most wants to see what it left behind. Because the summary wasn't there, a merged PR on the grinder's report line read as outstanding (TD-182).

**Resolved:** 2026-09-27 (PR #644, TD-192's build) — the design is design §4.5 screen 1 *Unfolded, it is a running team's card* and §4.5a *team card: summary*, *card: compact*.

**Related:** TD-176 (archived), TD-182, TD-183, design §4.5 screen 1, §4.5a **team card: summary**, **team groups**, *the fold*.

## TD-192: Build the wound-down team's summary: an unfolded team with nothing live draws the three facets and compact member cards

**Priority:** Medium
**Added:** 2026-09-26 (the designer, from TD-181's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Open — nothing built. Design §4.5 screen 1 (*Unfolded, it is a running team's card*), §4.5a *team card: summary*, *card: compact*, *team groups*.
**Location:** `src/agentorc/ui/app.py` (the groups builder: `summary = team_summary(…) if team != NO_TEAM and live else None`, and the `compact` flags set under it), `src/agentorc/ui/templates/org.html` and `team_summary.html` (the summary inside the fold), `src/agentorc/ui/static/app.js` (`syncTeams`: the fold hides the summary with the cards), `src/agentorc/ui/help.py` (the fold's text)

**Why:** TD-181's *Why*: a wound-down team is when the person most wants to see what it left, and the summary is built only while a member is live.

**Resolved:** 2026-09-27 (PR #644) — design §4.5a *team card: summary* and *card: compact* now read as built; `team_groups` and `compact_in` in `src/agentorc/ui/app.py`, the rollup's `live` filter, and `tests/test_ui_team_summary.py` (`test_a_wound_down_team_shows_what_it_left`). The live look is on `docs/user_attention.md`.

**Done when** TD-181's *Done when*: an unfolded wound-down team shows the three facets, and a test covers a team whose members have all exited.

**Related:** TD-181 (the design), TD-193 (the mark its cards carry), TD-183 (the fold's target), TD-176 (archived: the summary).

## TD-164: Terminal selection in Focus: plain drag selects in the browser and Shift+drag still does, the wheel scrolls tmux through the bridge, and copy-on-select is the one toggle

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: *is shift-drag necessary for copy/paste in the tmux terminals or should we change it to auto-copy selected text? Ideally it would work smoothly like in vscode terminals where a selection can be made, then grown/shrunk with a shift-click*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-174

**Status:** Designed 2026-09-25 (the designer): design §4.6 *Scrollback is tmux's* (**The mouse is the browser's**) and *A read-only attach* (the carve-out goes), §4.5a *Focus: Copy / Paste* and the **copy on select** toggle row, §5 `person.terminal.copy_on_select`, §4.5 screen 8 **You**; mockup `Focus.dc.html` (the toggle). Settled: (a) no `mouse on` on the attach, no tracking, a plain drag selects; the wheel is a scroll message with a line count the bridge turns into `copy-mode -e; send-keys -X -N n scroll-up`; (b) Shift+drag still selects and Shift+click grows — xterm.js's own selection service (`shiftKey → _handleIncrementalClick`), checked in the vendored source rather than a live pane; (c) copy on select is the one toggle, **on by default**, Ctrl+C-with-selection and Copy unchanged; (d) a person's setting, `person.terminal.copy_on_select` through `set_settings` — yours everywhere, as the terminal's face, not per browser; (e) a program asking for the mouse itself gets none, and there is no per-session escape; (f) the rows, the paragraphs, the texts named for the build. The design is PR #574, stacked on #572; the default-on choice is on the board for Paul (the designer's line of 2026-09-25, extended here — the person inbox is full), and #574 merges after 2026-09-26 08:45 MDT unless he says otherwise — the next designer run merges it if this one has ended. The build is TD-174; this entry archives with it. **What was:** nothing designed. Today the Focus terminal is a terminal emulator's: the attach sets `mouse on` on the tmux session so the wheel reaches tmux's own history (§4.6 *Scrollback is tmux's*, TD-022), and because tmux then asks for mouse tracking, a plain drag goes to tmux — a copy-mode selection into tmux's buffer, not the clipboard — and only Shift+drag selects in the browser, then **Copy**, Ctrl+C with a selection or Ctrl+Shift+C (§4.5a *Focus: Copy / Paste*; `focus.html`'s Copy tooltip and the `select text in the terminal first (Shift+drag: plain drag goes to tmux)` toast in `app.js` say so). The rule is what a tmux user expects of a real terminal, and it is the wrong rule for a browser pane that is mostly read.

**Why:** the pane is where a person reads a session's output and lifts a line out of it — an error, a path, a PR number — and today the first drag they try does the wrong thing silently: tmux takes it, the clipboard stays as it was, and the toast is the only teacher. A browser can offer both gestures at once, so this need not be a setting over which drag selects; the one real choice is whether a selection copies itself.

**What the design round has to settle:** (a) **plain drag selects in the browser** — xterm.js stops forwarding the mouse to tmux; the wheel, the one thing tracking bought, goes down the bridge path Shift+PageUp already uses (`copy-mode -e -u` / `page-down` against the session, §4.6) as a scroll message per tick, with the latency and the batching that implies, and tmux's own mouse copy mode goes; whether `mouse on` is still set at all, since nothing then reads it; (b) **Shift+drag keeps working** — with tracking off, Shift is nothing to xterm.js, so the habit costs nothing and needs no setting; Shift+click to grow or shrink a selection is native to xterm.js's selection service and may already work while Shift is held — the round checks in a live pane before it designs around it; (c) **copy on select** — VS Code's `terminal.integrated.copyOnSelection`, off there by default, since a selection made to read clobbers the clipboard; on a pane that is mostly read the round decides the default and makes it the one toggle, with Ctrl+C-with-a-selection and **Copy** unchanged either way; it needs the secure context Copy needs; (d) **where the toggle lives** — a person's browser preference like the team fold (*remembered per team in the browser*, §4.5a *team groups*), not the org's, and the settings audit (ADR 2026-09-25) is where that is argued; (e) **what is lost** — a full-screen program that asks for mouse tracking itself (htop, a mouse-enabled `less`) no longer gets the mouse; Claude Code does not ask for it; the round says whether a per-session escape exists or it is simply so; (f) the §4.5a row, the §4.6 paragraph, the tooltip and toast texts, the read-only Focus (TD-096: Copy works, Paste is inert — unchanged).

**Done when** Paul drags in a Focus pane without Shift and the selection is the browser's, Shift+click grows it, the wheel still scrolls tmux's history, and the copy-on-select choice is on the page and survives a reload.

**Resolved:** 2026-09-27 (PR #646, TD-174's build) — the design is design §4.6 *The mouse is the browser's* and §4.5a *Focus: copy on select*.

**Related:** §4.5a *Focus: Copy / Paste*, §4.6 *Scrollback is tmux's* (TD-022), TD-096 (the read-only attach), TD-157 (an *i* mark for the control), ADR 2026-09-25 (where a person's preference lives).

## TD-174: Build the mouse as the browser's: no `mouse on` on the attach, the wheel as a scroll message with a line count through the bridge, the wheel-only carve-out removed, the copy-on-select toggle and `person.terminal.copy_on_select`, the tooltip and toast texts

**Priority:** Medium
**Added:** 2026-09-25 (the designer, from TD-164's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Open — nothing built. Design §4.6 *Scrollback is tmux's* (**The mouse is the browser's**), *A read-only attach*, §4.5a *Focus: Copy / Paste* and **copy on select**, §5 `person:`, §4.5 screen 8 **You**; mockup `Focus.dc.html`.

**Location:** `src/sessionorc/tmux.py` (the attach argv ~line 46: the chained `set-option mouse on` goes), `src/agentorc/ui/pty_bridge.py` (the scroll argv ~line 77: takes `lines`, emits `copy-mode -e -t target ; send-keys -X -N n scroll-up|scroll-down`; `WHEEL_ONLY` and its `read_only` carve-out removed), `src/agentorc/ui/static/app.js` (a `wheel` listener on `#term` batching notches per animation frame into `{scroll, lines}`; the toggle's `change` → `set_settings`; `term.onSelectionChange` copying when on and the selection has ended; the toast *select text in the terminal first (Shift+drag: plain drag goes to tmux)* reworded), `src/agentorc/ui/templates/focus.html` (the toggle beside `#tcopy`, its `title`; `#tcopy`'s tooltip reworded), `src/agentorc/ui/app.py` and `src/sessionorc/agent.py` (`settings` / `set_settings`: the `person.terminal.copy_on_select` key, default true, replicated as `person.terminal` is), the Settings page's **You** section (TD-148 builds the page; this adds the row).

**Why:** TD-164's *Why*: the first drag a person tries does the wrong thing silently.

**Resolved:** 2026-09-27 (PR #646) — design §4.6 *The mouse is the browser's* and *A read-only attach*, and §4.5a *Focus: copy on select*, now read as built. The attach argv is in `src/sessionorc/tmux.py`, `scroll_argv` and the pump in `src/agentorc/ui/pty_bridge.py`, the wheel, `AO.wheelStep` and copy on select in `app.js`, and `POST /api/settings/person` in `app.py`. The tests are in `tests/test_ui.py` (`test_bridge_argv_shapes`, `test_terminal_scrollback_reaches_tmux`, `test_focus_watches_an_unattended_session`, `test_copy_on_select_is_the_persons_and_on_by_default`). Three things only a live pane can show, a plain drag selecting, Shift+click growing the selection and htop getting no mouse, are on `docs/user_attention.md`. The Settings page's You row is TD-148's.

**Done when** TD-164's *Done when*: Paul drags in a Focus pane without Shift and the selection is the browser's, Shift+click grows it, the wheel still scrolls tmux's history, and the copy-on-select choice is on the page and survives a reload.

**Related:** TD-164 (the design), TD-022 (scrollback is tmux's), TD-096 (the read-only attach), TD-146–148 (the settings file and page), TD-157 / TD-167 (the toggle's `title` as its mark), goal 12 (a pane you type into).

## TD-182: A card's report line keeps a merged PR as `TD-066 → #158` with no mark

**Priority:** Low
**Added:** 2026-09-26 (found at the dc-grind wind-down: Paul read the grinder's card as *they have a PR outstanding*; #158 had merged at 17:00 UTC)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; the build is TD-193
**Status:** Designed 2026-09-26 (the designer): design §4.5a *card: report line* (**the PR's mark**), *card: compact* (an ended member keeps its last reference after the ending), §4.5 screen 1, §4.7 (`ao status -v`); mockup `OrgTeamFirst.dc.html` (a compact line with the mark). Settled: the word after the number, *merged* or *closed*, from the repo readings; unmarked when the readings do not hold the PR; `ao status -v` shows it too, by one `repos` read per call; the record and `--json` are unchanged; the Focus Reports panel marks its PR links the same way. The build is TD-193; this entry archives with it.
**Location:** `src/sessionorc/models.py` (`report_line`), the card's report line in `src/agentorc/ui/templates/card.html`; the PR states are in the repo facts (`repos.json`, TD-176 slice 1)

**Why:** §4.5a's **card: report line** row draws `TD-027 → PR #59 · 1/2 done` from the record alone. TDs in motion marks a PR *merged* / *closed* once it is no longer open (§4.5a, TD-176). The member card doesn't, so an exited grinder's last report reads as work in flight. The same line is in `ao status -v` and the Members list.

**Resolved:** 2026-09-27 (PR #648, TD-193's build). The design is design §4.5a card **report line** (**the PR's mark**) and §4.5 screen 1 *A PR that is no longer open says so*.

**Related:** TD-181 (the summary would have shown it), TD-176, TD-095 (the card's anatomy), design §4.5a **card: report line**, **TDs in motion**.

## TD-193: Build the PR's mark on the report line: `TD-066 → #158 merged` on the card, the compact line, the Members list, `ao status -v` and the Reports panel

**Priority:** Low
**Added:** 2026-09-26 (the designer, from TD-182's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Open — nothing built. Design §4.5a *card: report line* (**the PR's mark**), *card: compact*, §4.7 (`ao status -v`). Touches `src/sessionorc/**`, so the techlead reads its PR (§4.9b).
**Location:** `src/sessionorc/models.py` (`report_line`: a second argument, the readings' PRs by number, default none), `src/agentorc/ui/app.py` (`_pr_states` is the lookup TDs in motion already uses; the card's `report`, `compact_line`, the Members rows), `src/agentorc/cli.py` (`ao status -v`: one `repos` read per call, its failure swallowed), `src/agentorc/ui/static/app.js` (`renderReports`: the word after the PR link, from a structured field the page passes, never from text)

**Why:** TD-182's *Why*: an exited grinder's last report reads as work in flight.

**Resolved:** 2026-09-27 (PR #648). Design §4.5a card **report line** (**the PR's mark**), *card: compact* and §4.7 `ao status -v` now read as built. `report_ref`, `report_line(session, prs)` and `pr_marks` are in `src/sessionorc/models.py`. `view(…, repos=)`, `pr_marks` on the view and `compact_line` are in `src/agentorc/ui/app.py`, `renderReports` is in `app.js`, and the `repos` read is in `cmd_status`. The tests are `tests/test_models.py`, `tests/test_ui_team_summary.py` and `tests/test_cli.py` (`test_status_v_marks_a_pr_that_is_no_longer_open_and_never_guesses`).

**Done when** TD-182's *Done when*: the dc-grind grinder's card would read `TD-066 → #158 merged`, and the tests above pass.

**Related:** TD-182 (the design), TD-192, TD-176 (the readings), TD-095 (the card's anatomy).

## TD-202: *Choose by priority* is one clause in the grinder template, with no order, no tool and no check

**Priority:** Low
**Added:** 2026-09-27 (Paul: *when a grinder chooses a TD, are they working them by priority (critical/high) first? Seems like they should be*)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/briefs/grinder.md` (*Lane*, line 12 at filing: *a `free-pick` lane means scan the ledger and choose by priority*), design §4.8 (the `free-pick` lane); `scripts/ledger.py` is dev-cadence's (SYNCED)

**Why:** the answer to Paul's question is yes, in one clause: the template says *choose by priority*. That is all it says. It doesn't give the order (High, then Medium, then Low; the ledger has no Critical), the tie-break, or what counts as a reason to pass a higher entry over (a sibling's lease, the brief's exclusions, a `Blocked by:`). It names no tool, and nothing checks the pick. dev-cadence's `scripts/ledger.py --pickable` sorts by priority, then Summary-table order, and drops what a `Blocked by:` holds. But it does not read the Owner, Kind and Pickable lines (TD-118), so on its own it lists designer and anchor entries a grinder may not take, and the template never mentions it. A grinder's claim note doesn't give the entry's priority, so the page can't show whether a High was passed over.

**Resolved:** 2026-09-27 (PR #662). (1) The template spells out the order: High, then Medium, then Low, ties in the ledger's order, and the reasons that justify passing a higher entry over. (2) The tool is `ao repo`, not `ledger.py`: it already lists only `Pickable: yes` entries (the header reading, TD-118) beside what live members hold, and now does so High first, each with its priority. `ledger.py --pickable` needs no dev-cadence change for this. (3) The claim's first `ao doing` line names the priority, and the reason when a higher entry was passed over; TD-203 shows it. Design §4.8 *Choosing in a free-pick lane*. A running grinder keeps its brief, so this reaches each at its next start. The *done when* is met by the template test and the `ao repo` order test.

**Fix:** (1) spell out the clause in the template: High, then Medium, then Low, ties in Summary-table order, and say why when passing over a higher one. (2) Name the tool: either `ledger.py --pickable` gains `--owner` / `--kind` filters on the header lines (a dev-cadence TD, through its anchor), or the template gives the one-line filter to run. (3) Put the entry's priority in the claim note, which TD-203 would then show. Add the sentence to design §4.8's `free-pick` in the same PR. A running grinder keeps the brief it started with, so the change reaches each grinder at its next start. Done when a grinder's claim note names the entry's priority and a test on the template (or the tool) shows High sorted first.

**Related:** TD-118 (the header lines), TD-203 (priority on TDs in motion, on the parked branch at filing), dev-cadence's TD-064 (`ledger.py --pickable`; not this ledger's TD-064), design §4.8.

## TD-205: Scrolling the Doing list jumps back to the top

**Priority:** Medium
**Added:** 2026-09-27 (Paul: *there is an issue with scrolling in the doing list (on the teams card) — it seems to redraw while scrolling, popping back up to the top*)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/agentorc/ui/static/app.js` (the group delta: `tpl.innerHTML = g.summary.trim(); … sum.replaceWith(fresh); AO.restoreDenyWhys(fresh, kept)`, around line 908 at filing), `src/agentorc/ui/templates/team_summary.html`

**Why:** the team summary is re-sent with the groups on every delta and on the `repos` and `doing` events, and the client swaps it whole (`team_summary.html`'s header says so). The swap carries over the Deny *why?* boxes a person was typing (`denyWhys`), but nothing else. The Doing list is a scrolled box, so every delta (any member's state change, any `ao doing`) snaps it back to the top. On a busy team that is every few seconds.

**Resolved:** 2026-09-27 (PR #676). The feed carries `data-keep-scroll="doing"` in `team_summary.html`; `AO.scrolls` / `AO.restoreScrolls` in `app.js` read it at `syncGroups`' swap and put it back in `syncSummaries` once the face is shown. Design §4.5a *Answer needed / Doing*; `tests/test_ui_team_summary.py` (`test_the_doing_feed_keeps_its_scroll_across_the_summary_swap`). Merged, live check pending.

**Fix (as filed):** carry each scrolled facet's `scrollTop` across the swap as `denyWhys` does, keyed by the facet (`.facet.fface` for Doing, `.facet.fmotion` for TDs in motion), and keep the person's position unless it was at the top, where new rows should show. Better still, re-render only the facet whose data changed. Test in the page's JS tests if they cover the swap, or a note in the PR on how it was checked by hand. Done when a person can scroll the Doing list while the team works and stay where they scrolled.

**Related:** TD-206 (the same list), TD-176 slice 3 (the summary and its swap).

## TD-197: TDs in motion misreads phases — design-first reads *grind*, and a PR waiting for its reader reads *grind*

**Priority:** Medium
**Added:** 2026-09-26 (Paul, at the Org page: *it shows 3 TDs in motion, when only one grinder is running … designer items are being listed as GRIND instead of DESIGN*)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/agentorc/ui/app.py` (`motion_rows`: `row["phase"] = "design" if e.get("for_page") == "design-first" else …`), `src/sessionorc/ledger.py` (`kind_of`), `src/sessionorc/reports.py` and `src/sessionorc/agent.py` (the derive tick: `review_pr` from the checked-out branch)

**Why:** the count was right. At the screenshot, three claims were open: grinder-ao-1's TD-146 (in hand) and TD-186 (PR #628, waiting for the techlead's read), and designer-ao-1's TD-175 (#603, waiting for its 03:00 MDT merge bound). There is no clean-up delay; a claim leaves the section when its member marks it done or dropped. The phases were wrong, for two reasons:
1. **Design-first reads *grind*.** `motion_rows` keys the phase on the ledger reading's `for_page`, the page's four-way bucket. `kind_of` puts every `Pickable: yes` entry in *pickable* before it looks at `Kind`. So a design-first entry the designer may pick is never *design*. On 2026-09-26, TD-175, TD-180 and TD-183 each read `kind: design-first`, `for_page: pickable` in `repos.json`; all three have since been designed or parked, so none does today, but the next pickable design-first entry will. It is *grind* without a PR and *review* with one (TD-183 with #623 read *review*). Design §4.5 screen 1: *a claim on a design-first entry is design*.
2. **A parked PR reads *grind*.** A declared claim gets its PR as `review_pr` only from the derive tick's reading of the branch the session has checked out (`reports.derive`, TD-150). A grinder that opens its PR, asks its reader and moves to the next entry before the next derive pass (`DERIVE_EVERY`) leaves a claim whose PR the tick never saw. grinder-ao-1's TD-186 claim read *grind* on the page while #628 waited for the techlead. The same holds for any held PR, which is the common case for `src/sessionorc/**`.

**Resolved:** 2026-09-27 (PR #677). `motion_rows` in `src/agentorc/ui/app.py` keys *design* on the entry's `kind`, and a claim with no PR takes an open PR whose head branch names its reference (`reports.branch_ref`); open PRs only, so a merged slice never marks the next slice *review*. Design §4.5 screen 1 and §4.5a *team card: TDs in motion*; `tests/test_ui_team_summary.py` (`test_tds_in_motion_read_the_entrys_kind_and_find_a_claims_pr_by_its_branch`). The `ao progress claim --pr N` alternative was not needed.

**Fix (as filed):** (1) key the phase on `e.get("kind") == "design-first"`, not `for_page`, with a test on a pickable design-first entry. (2) Match open PRs to declared claims by head branch (`branch_ref(headRefName)`) across the repo's PR reading, not only the checked-out branch, so a claim's PR is found wherever its branch sits. TD-176's repo facts already hold the open PRs with `headRefName`. Or have `ao progress claim --pr N` recorded when the grinder asks its reader, and say so in the grinder brief. Tests: a claim whose PR was opened on a branch the session has since left reads *review*. Done when the screenshot's three rows would read *design* (TD-175), *review* (TD-186) and *grind* (TD-146).

**Related:** TD-176 (TDs in motion, the repo facts), TD-150 (`review_pr`), TD-198 (the same `kind_of` precedence on the kind bar); design §4.5 screen 1, §4.5a *team card: TDs in motion*.

## TD-200: Small Org card misreadings — *last came*, a clipped flag, a closed record reading EXITED

**Priority:** Low
**Added:** 2026-09-26 (found at the Org page with TD-197)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/agentorc/ui/app.py` (the slot's caption: `came = "last came" …; caption = came + f" · {d['age']} ago"` with `d["age"] = _age(s.get("since"))`; `d["flag"]`), `src/agentorc/ui/templates/card.html` (the compact card's row 2), `src/agentorc/ui/static/app.css` (`.flag`, `white-space: nowrap`)

**Why:** three small things the Org page said wrongly on 2026-09-26:
1. **A seat's *last came* measures when it left.** The caption uses the record's `since`, the time of its last state change, which for an exited seat is `closed_at`. techlead-ao-1, filled at 03:32Z and gone at 03:36Z, read *last came · 0s ago* on the page just after it left. It should use when the seat came (`created`), or say *left*.
2. **The compact card clips its flag into a different number.** manager-ao-1 has 47 unpushed commits (its round log, TD-175). Its compact card showed *⚠ 4*, because the flag text *47 unpushed* was cut to fit and still reads as a count. The compact card needs a short form that stays true (*⚠ 47*, with the full text on hover), or no flag.
3. **A record closed by `ao close` reads EXITED.** grinder-ao-2, closed by the anchor at 02:19:54Z, has `closed_at` set and `state: exited`. Its card reads EXITED and offers Forget. Check what the design wants a closed unattended member to read (§4.5a *Ready to close*, the Close row) before changing anything; this may be right.

**Resolved:** 2026-09-27 (PR #678 for (1)–(2), PR #680 for (3)). (1) `view()`'s `came_age` from the record's `created`; (2) `flag_short` on the compact card, the full words as `title`, `.sc .r2 .flag` unshrinking — design §4.5a *card: compact*, `tests/test_ui_org.py` (`test_a_seats_last_came_is_its_fill_and_a_compact_flag_keeps_its_count`). (3) the design wanted `closed`; the killed run's `SessionEnd` landed after `ao close` and `_apply_event` set `exited`. It now ignores every hook on a closed record — design §4.2's **Close** row, `tests/test_agent.py` (`test_a_closed_record_stays_closed_when_its_run_says_it_ended`).

**Fix (as filed):** (1) caption from `created` (the fill) for a seat, with a test; (2) a compact flag form, with a test that a two-digit count survives; (3) per the design check. Done when all three read true on the Org page.

**Related:** TD-197 (found together), TD-175 (the unpushed round log), TD-176 slice 3 (the compact card), TD-097 (seats).

## TD-147: Build the settings replica — the `settings` link frame, the node's copy, `set_settings` forwarded and refused offline

**Priority:** Medium
**Added:** 2026-09-25 (TD-100 (4)'s design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved

**Resolved:** 2026-09-27 (PR #682). The `settings` link notification, home → node, after each `set_settings` write and once per dial after the snapshot (`agent_remote._push_settings`); the node's replica (`agent_link._take_settings`); `set_settings` in `modes.HOME_EDITS`, so it is forwarded while linked and refused offline; design §4.4a *Settings, replicated*, the offline table's row, §5 *Nodes*; `tests/test_link.py` (`test_the_homes_settings_reach_a_node_on_each_write_and_on_the_next_dial`). The page's *set at <home>* is TD-148's, as the page is.

**Location:** `src/sessionorc/agent.py` (the `settings` notification to every linked node after each `set_settings` write and to a node on `hello`, beside the `intent` push; the node's handler writing its own `settings.yml`; `set_settings` forwarded from a node — `modes.HOME_EDITS` gains it, so `modes.offline_refusal` refuses it offline in the home-owned edits' words; the node's `gate` and `settings` reads answering from the replica), `src/sessionorc/link.py` (nothing new: a method with no `id` is a notification already), `src/sessionorc/modes.py`, `tests/test_link.py`, `tests/test_modes.py`.

**Why:** the usage gate, a team's stop time and a schedule run on the node that holds the session, so a setting turned at the home must reach the node's tick, offline included; today each host read its own file and a reserve set on the home never reached a node's sessions.

**Fix (as filed):** one slice in `src/sessionorc/**` (the techlead reads it). The frame and its two triggers; the node's write; the row change in `modes.py`; the node's reads from the replica; a test that a reserve set on the home while a node is linked pauses the node's session on its next tick, that one set while the node is offline reaches it on the next dial, and that `ao gate` at an offline node is refused in the same words `ao control` is. The replica also routes the two team stop time writes that run wherever `create` and `set_settings` are served — the create's stamp (`_team_stamp`) and the restamp on a moved or cleared instant (`_restamp_team`) — which read a node's own `settings.yml` until then (the techlead's read of #630).

**Done when** the three tests pass, `ao gate` at a linked node writes the home's file and the node's replica agrees within one tick, and the Settings page on a node (TD-148) shows *set at <home>* beside each editable value.

**Related:** TD-100, TD-146, TD-057 (the link), TD-148.

## TD-196: `ui/app.py` is one 4,413-line module: split it into modules

**Priority:** Medium
**Added:** 2026-09-26 (Paul, while TD-108's split was planned: *do we want one for app.py as well?*)
**Owner:** grinder
**Kind:** build
**Status:** Resolved

**Resolved:** 2026-09-28 (grinder-ao-1, on Paul's word via the anchor; PRs #700, #704, #705 and #706). `ui/app.py`'s sections moved verbatim, each re-exported from the app so every route, template global and `from agentorc.ui.app import X` reads as before: the shared helpers → `ui/common.py` (#700); the card and view model with the identity alarms → `ui/cards.py`, `_iso` to `common` (#704); the Inbox page and the rail → `ui/inbox.py` (#705); the team-first Org → `ui/org.py` and the Repo page → `ui/repo.py` (#706). `app.py` (4,946 lines at the pick, 2,316 after) is the assembly: `create_app`, its route groups, the resume helpers, and `read_boards` and `repo_teams`, kept there because the suite patches both and one calls the other. No split module imports the app; `tests/test_ui_split.py` pins the re-exports and refuses a split module that calls or reads a patched name (`LocalClient`, `read_boards`, `rpc`, `PtySession`, `repo_teams`) bare. The Fix's *each route group beside its views* was not done: the 2026-09-27 plan kept the routes in the assembly, and the page split alone ends the shared-file contention the lanes needed. The lanes are redrawn with TD-108 step 2 (the anchor's).

**Was:** In progress — **picked 2026-09-28 on Paul's word (via the anchor): grinder-ao-1 alone on `ui/app.py` until this merges. Slice a (the shared helpers → `ui/common.py`, the guard `tests/test_ui_split.py`): PR #700, merged. Slice b (the card and view model, the identity alarms → `ui/cards.py`; `_iso` to `ui/common.py`, which the cards read): PR #704, merged. Slice c (the Inbox page and the rail → `ui/inbox.py`; `read_boards` and `repo_teams` stay in the app, the suite patches both there): PR #705.** Planned 2026-09-27 (grinder-ao-1, before its restart): move app.py's own sections by page into modules re-exported from `agentorc.ui.app`, as TD-108 step 1 did — the top helpers (usage chip, node and identity notes, 60–492) → `ui/common.py`; the view model and identity alarms (`view`, `card_slot`, 493–1316) → `ui/cards.py`; the team-first Org (1317–1607) → `ui/org.py`; the Repo page (1608–1857) → `ui/repo.py`; the Inbox page and the rail (1858–2614) → `ui/inbox.py`; `create_app`, the route groups and the resume helpers stay. The tests patch five names on the module — `LocalClient` (10), `read_boards` (6), `rpc`, `PtySession`, `repo_teams` — so a moved function that calls one reads it as `app.X` at call time (import `agentorc.ui.app` lazily inside it), and a guard like `tests/test_agent_split.py` pins that and the re-exports
**Location:** `src/agentorc/ui/app.py` (4,413 lines: about 2,760 of helpers and view builders — `view`, `team_summary`, `compact_line`, `prs_waiting`, the rollup and the Repo page's readers — then `create_app` and its seven route groups `_pages_routes` … `_stream_routes`), `tests/test_ui_*.py`

**Why:** TD-108's first half (2026-09-22) split `create_app` by page, but within the one module, so that the tests' `monkeypatch` of `LocalClient`, `rpc` and `PtySession` on `agentorc.ui.app` kept reaching every route. The module is still the second most-changed file in the repo (127 commits in 30 days, level with `agent.py`), every page change passes through it, and grinder-ao-2's brief calls it *shared* with its sibling. The same reasons as TD-108 apply: parallel lanes, and less context for a grinder reading one page's code.

**Fix (as filed):** move the view builders into modules by page (`ui/org.py`, `ui/repo.py`, `ui/inbox.py`, `ui/focus.py` or similar) and each route group beside its views, keeping `agentorc.ui.app` as the assembly (`create_app`). Keep the tests' patch points working: the patched names (`LocalClient`, `rpc`, `PtySession`, …) are looked up through `app` at call time, or the tests move to the new paths in the same PR. No behaviour change; the full suite passes unchanged in count. Done in small PRs as TD-108's step 1 is, and then the lanes are redrawn along with TD-108's step 2.

**Related:** TD-108 (the host agent's split, and the page split within this module), TD-188 (context per run).

## TD-215: Build the orphaned question, the home's half

**Priority:** High
**Added:** 2026-09-28 (the designer, from TD-213's design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved

**Resolved:** 2026-09-28 (PR #712). `orphaned` and `adopted_at` on `MailEntry`, `reference_of` in `models.py`, `_asker_gone(how=)` and `_adopt_orphans` in `agent_attention.py`, the orphaned `steer`'s cleared bound and the adopted lapse note in `_lapse_or_expire`, the refusal `orphaned_refusal` until TD-216; design §4.10 *A question about a reference outlives its asker*; tests in `tests/test_mail.py` (`test_a_question_about_a_reference_outlives_its_asker` and the three after it).

**Location:** `src/sessionorc/agent_attention.py` (`_asker_gone`, called from `rpc_close`, `_forget`, `_cancel_start` and the tick), `src/sessionorc/models.py` (`MailEntry`: `orphaned`), `src/sessionorc/agent_inbox.py` (`_lapse_or_expire` and the lapse sweep), `src/sessionorc/agent.py` (`rpc_create`'s supersede in place), `src/sessionorc/agent_mail.py` (the person's reply path, `_person_holds`); design §4.10 *A question about a reference outlives its asker*. Held path: the techlead reads the PR.

**Why:** TD-213's *Why*: a wind-down closed two members and took a `steer` and an `ask` the ledger still waited on out of the Inbox before Paul saw them.

**Fix (as filed):**
1. **`orphaned` on the entry**: `{at, how, ref, name, repo, host, team}`, persisted with the person inbox, in the `inbox` RPC's view of an entry. `how` is `closed`, `forgotten` or `cancelled`.
2. **`_asker_gone` orphans instead of closing** an open `ask` or `steer` from the record whose `about` names a reference — it has one of the two shapes `normalize_ref` canonicalises, a ledger id or a PR number (`normalize_ref` itself passes any other text through, so the test is the shape, not the call), and the canonical form is kept on the stamp as `ref`, which is what TD-216 matches a lease on; `about` itself is free text and is not rewritten: the stamp is written from the record, `paused_at` is cleared, and nothing closes. An entry with no `about`, or one of any other shape (a session's id, a board line, prose), closes `asker_gone` as today. An entry already orphaned (a close, then the forget a day later) keeps its first stamp. The outcome half of `_asker_gone` is unchanged, and so is the `superseded_by` return.
3. **The bound**: the lapse sweep, on an orphaned `steer` whose bound has run out, clears `bound` and leaves the entry open; nothing is told. An entry with `orphaned` and no `bound` is skipped by the sweep as an `ask` to the person is.
4. **Adoption**: a create that puts a live record under the id an orphaned entry's `from` names clears `orphaned` on each such entry, whether or not it resumed the conversation. A `steer` adopted with its bound still ahead lapses as any does, and its `system` note reads *steer m-… about <about> lapsed: the default was "<default>"* when the entry was ever orphaned (keep a mark for it, such as `adopted_at`).
5. **Until TD-216**, a person's reply or *Go with it* naming an orphaned entry is refused with a sentence that says its asker is gone and the answer's road is not built, so nothing is sent to a record that is not there; Delete declines it as on any open question.
6. **Tests** (`tests/test_mail.py`): the wind-down case — two members each with an open question to the person that names a reference, `team stop --close` closes both, and both questions are still open in the person inbox with `orphaned.how == "closed"`; a question with no `about`, and one whose `about` is prose, closes `asker_gone`; a forget after a close keeps the stamp; an orphaned `steer` past its bound is open with no bound and no `lapsed`; a paused one loses its pause; a create under the same name clears the stamp and a reply then lands in the new record's inbox; an adopted `steer` lapses with the note naming the default; the existing resume tests (`test_a_resumed_askers_questions…`) pass unchanged. `test_asker_gone_closes_the_persons_questions_on_close_and_forget_but_not_on_exit` changes to cover both kinds of question.

**Done when** a member closed with an open question to the person that names a reference leaves that question open in the person inbox, the wind-down test passes, and design §4.10's *not built — TD-215* is corrected in the same PR.

**Related:** TD-213 (the design), TD-216 (the answer and the row), TD-069 (the needed rule and `asker_gone`), TD-081 (the resume under the same name).

## TD-149: Settings housekeeping the audit found — dead `.agentorc.yml` keys, `promote:` refused, backups, the org `roles:` overlay unvalidated, start-only host fields, `AGENTORC_TICK`, bind and port

**Priority:** Low
**Added:** 2026-09-25 (the settings audit, ADR `docs/decisions/2026-09-25-settings-audit.md`)
**Status:** Resolved 2026-09-28 — **(1) done 2026-09-28 (grinder-ao-2, PR #701)**: `adapter`, `worktrees`, `anchor` refused naming where each is decided (`repoconfig.RETIRED`); `unattended`, `ready_when`, `commands` accepted and marked *read by nothing yet* in §5 (the default of a steer to the techlead that re-asked m-43f5e3922686, closed unseen by the wind-down — TD-213). **(2), (4) and (6) done 2026-09-25 (grinder-ao-1, PR #592)**: `promote:` accepted and checked (`repoconfig._promote`: `run` and `check` required, `auto` refused as `settings.yml`'s); the org `roles:` overlay checked by `_role_block` (the live `org.yml`'s six presets pass unchanged, `review` gaining its default `bound: 2h` as the record already shows); `AGENTORC_TICK` named in §5 as a test knob. **(7) and (8) done 2026-09-26 (grinder-ao-1)**: the UI's bind and port defaults are `agentorc.service.DEFAULT_BIND`/`DEFAULT_PORT`, read by `ao ui`, `agentorc-ui` and `ao service install`; `AGENTORC_PROFILE` left the launch environment; §5's `hosts.yml` field list names what `sessionorc.hosts` reads, `transport`/`ssh` said to arrive with TD-004. **(5) done 2026-09-26 (grinder-ao-1)**: the Org page says *restart pending*, naming each of `local.name`, `home:`, `local.identity` that moved under the running agent (`ui.app.restart_note`). **(3) done 2026-09-26 with TD-146 slice 1** (`settings.yml` in `BACKUP_MEMBERS`). Eight findings, each small, none design: (1) `.agentorc.yml`'s `adapter`, `worktrees`, `anchor`, `unattended`, `ready_when` and `commands` are parsed and read by nothing — implement or remove each, and say which in §5; (2) `promote:` is refused as an unknown key by `repoconfig._apply`, so writing the designed block breaks `ao new` in that repo — accept it now, ahead of TD-132; (3) `BACKUP_MEMBERS` lacks `settings.yml` (TD-146 adds it; remove this item when it lands); (4) the org-level `roles:` overlay skips `_role_block`'s validation — validate it the same way; (5) a hand edit of `hosts.yml`'s `local.name`, `home:` or `local.identity` leaves the agent (start-only) and the UI (per request) disagreeing until a restart — the UI should read the agent's snapshot for those three, or say *restart pending*; (6) `AGENTORC_TICK` is an override §5 does not name — document it as a test knob in §5 or remove it; (7) bind and port live in `ao ui`, `agentorc-ui` and the unit — one source; (8) `AGENTORC_PROFILE` is exported to every pane and read by nothing, and `hosts.yml`'s `transport`/`ssh` are in §5 and not the code — reconcile each way.

**Location:** `src/agentorc/repoconfig.py`, `src/sessionorc/agent.py`, `src/agentorc/org.py`, `src/sessionorc/hosts.py`, `src/agentorc/ui/app.py`, `src/agentorc/service.py`, design §5.

**Why:** a settings page shows what the files hold, and a dead key or a silently unvalidated one is a lie the page would draw.

**Done when** each of the eight is done or struck with a reason, §5 names only keys the code reads, and `pdm run test` covers (2) and (4).

**Related:** TD-100, TD-132, TD-004, TD-060.

**Resolved:** 2026-09-28 (PR #592 for (2), (4), (6); PR #701 for (1); (3) with TD-146 slice 1; (5), (7), (8) by grinder-ao-1 2026-09-26). The lasting content is design §5 (the `.agentorc.yml` example and its *Read by nothing yet* paragraph, the `hosts.yml` field list, `AGENTORC_TICK`), `repoconfig.RETIRED`, and `tests/test_repoconfig.py`.

## TD-213: Closing a member closes the questions it put to the person, even when the ledger still waits on the answer

**Priority:** High
**Added:** 2026-09-28 (Paul: *I do not see a steering item from ao-grind in the inbox*)
**Owner:** designer
**Kind:** design-first
**Status:** Resolved

**Resolved:** 2026-09-28 — built by TD-215 (PR #712) and TD-216 (PRs #717, #724): a question to the person that names a reference outlives its asker, and its answer goes on the board and to whoever holds the reference (design §4.10 *A question about a reference outlives its asker*).

**Was:** Designed 2026-09-28 (the designer, PR #699; the steer to Paul is `m-bd30b5cf92af`, bound 2026-09-28 21:11 MDT): design §4.10 *A question about a reference outlives its asker* (and the `asker_gone` clause of *What a person is asked*, *Pause*), §4.4 *Board write-back* (the second add), §4.5a *Inbox row: orphaned question* and the Inbox's count, §4.9a (the wind-down writes no board line for it); the glossary's *orphaned question*. Settled: a question to the person whose envelope carries `about` is **orphaned**, not closed, when its asker is closed, forgotten or cancelled, and one without a reference closes `asker_gone` as before (its FYI row, *closed · the asker is gone*, is already built and is the trail the entry asked for); an orphaned `steer` runs to its bound and then waits on the person, counted, instead of lapsing; the asker's name coming back (a team's Start, a restart, a Resume) adopts the question; the answer to an orphaned one is written on the repo's board at the person's press and mailed `handed` to whoever holds a lease on the reference, as a board reply is (TD-126). Of the entry's options: (a) is taken for the answer and not for the question — the host agent writes no board line unpressed (§4.4), and a brief's rule covers one road to a close out of several; (b) would leave a live holder that refuses the team's next Start; (c) is the lease, not the team (§9 invariant 9). Two choices steered to Paul: the board as where the answer goes, and an orphaned `steer` becoming counted at its bound. This entry archives with TD-216.
**Location:** `src/sessionorc/agent_attention.py` (`_asker_gone`: closing or forgetting a record closes the open `ask`s and `steer`s it put to the person, `closed_reason: asker_gone`), design §4.10 *What a person is asked*, §4.9a (the wind-down), `src/agentorc/briefs/manager.md` (*Out of work*)

**Why:** an `ask` to the person never expires, so the design ends it with its asker: closing or forgetting the record closes its open questions, or a forgotten worker's questions would stand forever. The team wind-down closes every finished member. On 2026-09-28 at 06:56Z, ao-grind's wind-down closed grinder-ao-1 and designer-ao-1, and with them grinder-ao-1's steer `m-43f5e3922686` (TD-149 (1): six `.agentorc.yml` keys) and the designer's ask `m-d20bbf79bdbe` (TD-180: who drafts the entry). Both left the person's Inbox before Paul had seen them. The work still waits on them. grinder-ao-1's out-of-work note, written three minutes before its close, says *TD-149 (1) waits on a steer to Paul*, and TD-180's header said *waits on Paul's answer*. The question is gone and the dependency stays, so an entry can wait forever on an answer nobody can give.

**Fix:** design what a question outlives. Options: (a) a question tied to a ledger entry (its `about` or `cites` names a TD) survives its asker's close and moves to the board as a line (*Decide: …*, the steer's default beside it), written by the host agent or by the manager's wind-down step (the brief already boards a passed-up question nobody answered; extend it to the members' own open questions); (b) the wind-down refuses to close a member with open questions to the person and leaves it idle for the person; (c) the question stays open under the team rather than the record, answered to whoever next holds the entry. Also: `asker_gone` on a question the person had not yet seen should at least leave a trail row saying what was withdrawn. Done when a member closed with an open question to the person leaves that question somewhere the person will see it, and a test covers the wind-down case.

**Related:** TD-180, TD-149 (the two questions that went), TD-187 / TD-195 (the same wind-down closed a designer whose lane had work), §4.10, §4.9a.

## TD-216: Build the orphaned question, the answer and the row

**Priority:** High
**Added:** 2026-09-28 (the designer, from TD-213's design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved

**Resolved:** 2026-09-28 (PRs #717, #724; grinder-ao-1). Slice 1 (#717): the board's second add (`board.add(…, answer=)`), `_answer_orphan` in `agent_inbox.py` — the line, then a `handed` note to each lease holder, then the close — for Reply, a suggested answer and *Go with it*; `orphaned.repo` falls back to the record's directory. Slice 2 (#724): the row — `sessionorc.mail.orphan_standing` (and `LEASE_TTL` beside it), the section rule `_orphan_held` in `ui/inbox.py`, the `orphaned` macro in `inbox_row.html`, the refusal on the row and the toast from the home's `note` in `app.js`, `ao inbox`'s standing and `ao msg --reply-to`'s result in `cli.py`; design §4.4, §4.5a *Inbox row: orphaned question*, §4.10; tests in `tests/test_board.py` and `tests/test_ui_orphaned.py`. The *Done when*'s live half — Paul answering one from the Inbox — is on docs/user_attention.md.

**Was:** Partly done — **slice 1 (items 1, 2 and 5's road) built 2026-09-28 (grinder-ao-1, PR #717)**: Reply, a suggested answer and *Go with it* on an orphaned question write the board's second add and a `handed` note to each lease holder, and close the entry; `orphaned.repo` falls back to the record's directory, and a board outside the home's registry (an asker on a node) is refused. **Next: slice 2**, items 3 and 4 — the Inbox row with its standing, the count, `ao inbox`'s standing (`src/agentorc/**`).
**Location:** `src/sessionorc/agent_inbox.py` (`_board_add_one`, `rpc_board_edit`; the person's reply and `inbox_go_with_it` on an orphaned entry), `src/sessionorc/board.py` (`write_back`'s add), `src/agentorc/ui/app.py` (`inbox_sections`, the mail row's view), `src/agentorc/ui/templates/inbox_row.html`, `src/agentorc/ui/static/app.js`; design §4.10 *A question about a reference outlives its asker*, §4.4 *Board write-back*, §4.5a *Inbox row: orphaned question*. Held path: the techlead reads the PR.

**Why:** TD-213's *Why*. After TD-215 the question stands; this entry gives the person's answer somewhere to go.

**Fix:**
1. **The answer's road**: a person's reply, suggested answer or *Go with it* naming an entry that carries `orphaned` writes the board line of design §4.10 through the write-back's add on `orphaned.repo`'s board (the host whose checkout it is serves it, as for a board row), committed as `agentorc: answer <item head> (from <entry id>)`; then a `note` from the person, `about` the reference, marked `handed`, to every live record with an unexpired declared lease on it; then the entry closes `replied` or `go_with_it`. A refused write refuses the press and changes nothing. One press per entry at a time, as `_board_adding` does for *Put on the board*. The reply's result carries `board`, `sent` and the sentence the toast draws.
2. **The handed note owes an outcome** as a handed board reply does (design §4.10 *Outcomes*); with no holder nothing is owed.
3. **The row** (§4.5a): the standing line from `orphaned` and the records' leases, drawn as the board row's standing is; an orphaned `steer` with a bound under *Steering* with *then it waits on you*, without one under *Needs you* and counted; no Pause, no Open; Snooze once the clock is gone; the refusal drawn on the row. The message page (§4.5 screen 6) draws the same line.
4. **`ao inbox`** run by a person prints the standing beside an orphaned entry.
5. **Tests**: an answer with no holder writes one line and mails nothing; with a holder it writes the line and lands one `handed` note that owes an outcome; *Go with it* writes *go with the default: …*; a dirty checkout refuses and the entry stays open; a second press while the first is in flight is refused; the count includes an orphaned `steer` only once its bound is cleared; the row's controls in each case.

**Done when** Paul answers an orphaned question from the Inbox and the answer is on the repo's board and in the inbox of the session that holds the entry, the tests above pass, and design §4.4, §4.5a and §4.10 lose their *not built — TD-216*. TD-213 archives with this entry.

**Related:** TD-213 (the design), TD-215 (the home's half), TD-126 / TD-142 (the board reply this follows; its mail half computes the same lease holders), TD-140 (*Put on the board*, the first add).

## TD-224: The Org page jumps to the top when scrolled past a certain point

**Priority:** High
**Added:** 2026-09-28 (Paul: *the screen redraws (seen if scrolling, get popped up to top)*; later: *it was there before the promote and is still there now … it seems to be more position based than time based (scrolling past a certain point vertically seems to trigger it) and it happens consistently*)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/static/app.js` (`syncGroups`: `sum.replaceWith(fresh)`, `box.appendChild(sec)` for every section on every delta; `syncSummaries`: the person's selector state applied after the swap, `AO.restoreScrolls`; `AO.scrolls` reads `scrollTop`; the reload on reconnect, line 748 at filing), `src/agentorc/ui/static/app.css`, `src/agentorc/ui/templates/team_summary.html`

**Why:** the Org page throws the reader back to the top once they scroll past some vertical position, every time, and it was there before the 2026-09-28 promote. TD-205 (#676) fixed the Doing list's own scroll box; this is the page. A person can't read the lower teams or the No-team cards. Being position-based, not time-based, suggests layout, not the update clock.

**Suspects, to confirm or rule out:**
1. **A forced layout mid-swap.** The template renders every selector variant (`.lv`, `.wv`, `.fv`) with the server's defaults visible and the rest `hidden`. `syncSummaries` applies the person's own choices (the team's Technical debt selector, the window, the answer/doing face) only after `syncGroups` has swapped every team's summary in. Inside that loop, `AO.scrolls` reads `scrollTop` on the next team's scrolled box, which forces a layout while the previous team's fresh summary is still in the server's default state. Where the person's choice differs from the default, that summary is briefly a different height. This alone would shift the page by that difference, not throw it to the top, so it is the weaker suspect.
2. **Scroll anchoring on a removed node (the likelier).** The browser anchors to a node near the viewport. If that node sits inside a summary that `replaceWith` removes, or a section that `appendChild` detaches and re-inserts, the anchor is lost on every delta and the fallback can be the top.
3. **A reload.** `ws.onmessage` reloads the page after a reconnect. It would be position-independent, so it is the least likely here, but it explains a jump at a promote.

**Fix:** reproduce first (a headless browser: scroll to a depth below the first team's summary, drive a delta, read `scrollY` before and after; say the depth at which it fires), then: apply the person's selector state and restored scrolls to `fresh` **before** it is inserted; move a section only when it is out of order; read every summary's scroll positions in one pass before any swap; consider `overflow-anchor: none` on the swapped regions if anchoring is the cause. Keep a person's scroll across a reconnect reload (`history.scrollRestoration` or a saved `scrollY`). Done when scrolling to the bottom of a busy Org page stays put through a minute of deltas, with a test of the swap order if the page's JS tests can hold one.

**Resolved:** 2026-09-28 (PR #732; grinder-ao-1). Reproduced in headless Firefox against the live Org page: scrolled to any depth from 300 px, the first delta that swapped the groups threw the page to 0 (or near it), every time; with `overflow-anchor: none` injected it held, so the cause was **scroll anchoring** (suspect 2), set off by suspect 1's forced layout. `syncGroups` re-appended every section on every delta and read each summary's `scrollTop` inside the loop, so a layout ran with the sections half re-ordered and the browser moved the page to follow its anchor. Now every summary's scrolls are read before anything moves; a section (`syncGroups`) or a card (`layout`) moves only when it is out of the server's order (`AO.placeAt`); and a fresh summary shows the person's faces (`showSummary`) before it is inserted. The same rig with the fix held at every depth through fifteen swaps each. A reconnect's `location.reload()` already keeps the position (the browser's own scroll restoration, measured). Tests: `tests/test_ui_org_scroll.py` (the placement under node, the swap's order from the source). Live check pending on the board.

**Related:** TD-205 (the Doing list's own scroll, fixed), TD-176 slice 3 (the summary and its swap), TD-194 (the team fold).

## TD-207: The Inbox shows board items only once they are due, and says so nowhere

**Priority:** Medium
**Added:** 2026-09-27 (Paul: *"the Inbox only lists board items once they're due" — we need to add a setting for this so it is obvious to the user*)
**Owner:** designer
**Kind:** design-first
**Status:** Resolved
**Location:** design §4.5 screen 6 (*Board items*: `--due-only`), §5 `person:` in `settings.yml` (TD-146), §4.5a (the Inbox's board rows and the Settings page's **You**), `src/agentorc/ui/inbox.py` (`board_argv`: `--report --due-only --json`)

**Why:** grinder-dc-1 finished its run on 2026-09-27 telling Paul it had left him two items. They were two `act` lines on dev-cadence's board, each `Due: 2026-10-04`. Paul looked in the Inbox, found nothing, and asked where the two messages were. The Inbox reads boards with `--due-only`, so an item a week out doesn't exist there until its day, and nothing on the page says the Inbox has a horizon or that items wait beyond it. (TD-208 hid these two a second way.) A session that writes a board line reasonably calls it *left for you*; the person reasonably looks in the one place the system sends them.

**Resolved:** 2026-09-29 (built by TD-220, PR #728 … #739). The design is §4.5 screen 6 *The board's horizon*; see TD-220's **Resolved:**.

**Related:** TD-208 (the same rows, read from a stale checkout), TD-069 step 3 (board rows in the Inbox), TD-126 (Reply on a board row), TD-146 (`settings.yml`'s `person:`), dev-cadence's TD-039 (`act` and the other item kinds).

## TD-220: Build the board's horizon

**Priority:** Medium
**Added:** 2026-09-28 (the designer, from TD-207's design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/inbox.py` (`board_argv`: `--report --due-only --json`; the rows built from its items), `src/agentorc/ui/templates/` (the Inbox's board rows, `repo.html`'s *Waiting on you*, the Settings page's **You**), `src/agentorc/ui/static/app.js` (**show**, the fold open for the page view), `src/agentorc/ui/app.py` (`repo_teams`), `src/sessionorc/settings.py` (`parse_person`: `open_in`, `terminal`, now `inbox`), `src/agentorc/briefs/grinder.md`; design §4.5 screen 6 *The board's horizon*, §4.5a, §5. Held path: `src/sessionorc/settings.py` waits for the techlead's read.

**Why:** TD-207's *Why*: a board item due next week does not exist in the Inbox until its day, and the page does not say so.

**Resolved:** 2026-09-29 (PR #728, #731, #733, #737, #739; grinder-ao-2). The read without `--due-only` sorts each board item by its date against the reader's `today` (`board_due_now`, `board_horizon`); `person.inbox.board_show` (`next:<n>`, `due`, `<n>d`, `all`; default `next:10`) is read and validated by `sessionorc/settings.py` and picked on the Settings page's **You**; the Inbox and the Repo page draw *Board, coming up*, the *not shown* fold and the line (`board_horizon.html`, `board_view`, `horizon_of`), counted nowhere; the grinder preset says *on the board, due <date>*; a board Snooze counts from the later of today and the item's date. The lasting account is design §4.5 screen 6 *The board's horizon*, §4.5a and §5; tests in `tests/test_ui_board.py`. The live look at slices 3–4 is on `docs/user_attention.md`.

**Related:** TD-207 (the design), TD-208 (the same rows, read from a checkout behind origin), TD-069 step 3 (board rows), TD-146 (`settings.yml`'s `person:`), TD-148 (the Settings page).

## TD-204: `send --wait` reads the tool's own start of *this* prompt as a previous turn, when its hook lands while the paste is being confirmed, and reports `prompt-stalled` for a prompt that ran

**Priority:** Medium
**Added:** 2026-09-27 (grinder-ao-1, reading `rpc_send` while fixing TD-078's test race, PR #672)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved — found by reading, not reproduced live. **Paul, 2026-09-28: yes, a TD; build the recommended fix** (the baseline taken before typing), answering the board item of 2026-09-12.
**Location:** `src/sessionorc/agent.py` `rpc_send` (the busy branch and the `rev` baselines, read **after** `_submit` returns), `_type` (paste, Enter, then polling until the composer is empty)

**Why:** `rpc_send` reads the record's state and `rev` only after `_submit` — the paste, the Enter and `_type`'s check that the prompt left the composer — has returned. Claude Code fires `UserPromptSubmit` the moment Enter lands, so under load the hook can turn the record `working` while `_type` is still polling the composer. `rpc_send` then sees `working`, takes the **busy** branch (*the tool queues the text; wait for the current turn to end*), waits for that turn — which is this prompt's own — to settle, finds `rev` moved by less than three, and waits for a *further* turn to start within `SEND_STALL_SECONDS`: none comes, and it raises `prompt-stalled` for a prompt that ran. That is the board item's symptom exactly (2026-09-12: two in eight sends, a 27 s answer on screen, the record's `progress` proving the prompt ran), and it explains why a long context was not the cause. The test in `test_send_wait_three_outcomes` could not see it: its stand-in hooks were fired by the test after the paste (TD-078).

**Resolved:** 2026-09-29 (PR #740; grinder-ao-1). `_submit` returns the record's state and `rev` read under the typing lock just before the paste, and `rpc_send` decides busy and *started* from them, so a turn that began, or began and ended, during the typing is this prompt's. `tests/test_agent.py::test_send_wait_hook_lands_while_typing` fires the hooks from inside a slowed `_type` after its Enter; on the old code it fails with `prompt-stalled`. Design §4.2 (`send` confirms the prompt took) says where the baseline is read. Not reproduced live: the board item of 2026-09-12 is the live evidence.

**Related:** TD-078 (the test's race, fixed in #672), TD-016 / TD-027 (the wait and the composer check, archived), the board item of 2026-09-12 (*`ao send --wait` reported `prompt-stalled` for a prompt that in fact ran*).

## TD-203: TDs in motion shows no priority

**Priority:** Low
**Added:** 2026-09-27 (Paul: *add priority to "TDs in Motion" list on the teams card*)
**Owner:** designer
**Kind:** design-first
**Status:** Resolved
**Location:** design §4.5a *team card: TDs in motion* (row 2291 at filing), `src/agentorc/ui/org.py` (`motion_rows`: the ledger reading's `priority` is at hand beside `title`), `src/agentorc/ui/templates/team_summary.html`

**Why:** a row reads *phase · reference · title · holder · PR*. Whether the team has its High entries in hand or is grinding Lows is not visible without opening the ledger. The ledger reading already carries each entry's `priority` (`sessionorc/ledger.py`), so the data is there.

**Resolved:** 2026-09-29 (built by TD-232 slice 1, PR #741). The design is §4.5a *team card: TDs in motion* (**Priority**); see TD-232's **Resolved:**.

**Related:** TD-197 (the same rows' phases), TD-202 (grinders picking by priority), TD-176 (archived: TDs in motion).

## TD-206: The Doing list's times are clock times cut short, and its fields run together

**Priority:** Low
**Added:** 2026-09-27 (Paul: *format times on the doing list to be relative/fuzzy (just now, 1h, 2d, etc) and put the data in columns*)
**Owner:** designer
**Kind:** design-first
**Status:** Resolved
**Location:** design §4.5a *team card: Answer needed / Doing* (*time · doer · words*), §4.5 screen 11 (the Repo page's Doing section), `src/agentorc/ui/templates/team_summary.html`, `repo.html`, `src/agentorc/ui/static/app.js`

**Why:** on the 2026-09-26 screenshot the Doing facet read *21:… techlead-ao… answering grinder-ao-1's held PR #628…*. The time is a clock time cut to fit (*21:…*), and the doer's name is cut too (*techlead-ao…*). With no columns, the time, the doer and the words run together and the eye can't scan down the list.

**Resolved:** 2026-09-29 (built by TD-232 slice 2, PR #742). The design is §4.5a *team card: Answer needed / Doing* (**Ages and columns**) and §4.5 screen 11; see TD-232's **Resolved:**.

**Related:** TD-205 (the same list's scrolling), TD-176 slice 2 (the doing log), §4.8 *the doing log*.

## TD-232: Build the team card's priority letter and the Doing list's ages and columns

**Priority:** Low
**Type:** feature
**Added:** 2026-09-28 (the designer, from TD-203's and TD-206's designs)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/org.py` (`motion_rows`, `doing_rows`), `src/agentorc/ui/templates/team_summary.html` and `repo_part.html`, `src/agentorc/ui/static/app.js` and `app.css`, `src/agentorc/ui/common.py` (a short age beside `_age`, which reads *2h 5m*), `tests/test_ui_*`; design §4.5a *team card: TDs in motion*, *Answer needed / Doing*, §4.5 screen 11.

**Why:** TD-203's and TD-206's: a row in motion does not say how much it matters, and the Doing list cannot be scanned.

**Resolved:** 2026-09-29 (PR #741, #742; grinder-ao-2). A row in *TDs in motion* carries its entry's priority as the bar's chip, **H** / **M** / **L**, in a fixed `.mprio` slot empty for an unmarked row, rows sorting by phase, priority, reference (`motion_rows`, `MOTION_PRIORITIES`); the Doing list reads *age · doer · words* in columns on the card and the Repo page — `_short_age` and `fmtShortAge` in the same words, re-drawn once a minute from `data-doing-at` with the reader's clock as the tooltip, the doer's width `doer_w` (at most 18). The lasting account is design §4.5a and §4.5 screens 1 and 11; tests in `tests/test_ui_team_summary.py`. The live look is on `docs/user_attention.md`.

**Related:** TD-203, TD-206 (the designs), TD-205 (the same list's scrolling), TD-197 (the phases), TD-176.

## TD-062: A merged RPC change breaks `ao` on the live system until the host agent restarts

**Priority:** Medium
**Added:** 2026-09-17 (anchor session, watching the first night both teams ran with mail)
**Owner:** grinder
**Kind:** build

**Status:** Resolved — **the still-open half designed 2026-09-24 (the designer, TD-120 step 2):** `ao service upgrade` is `ao promote` (design §4.7) over the repo's `promote:` block (§5) and the home's *Promote* policy (§6), which refuses nothing for a live team — sessions survive the restart, as they do today — and instead refuses on a dirty or off-main checkout, a promote in flight or a failure standing; TD-132 builds it, and this entry archives when it lands. Was: the one instance seen is fixed: `tdgrind-ao-1` hit it, and PR #184 (merged 2026-09-17 00:03) makes `ao progress` send `force` only when asked; the host agent was restarted at 00:08 for other reasons. The class of failure is untouched. **Second occurrence the same night:** PR #189 (TD-055 step 3, merged 00:40) renamed the grant to `control`; the running agent validated against `orchestrate` only, so a team start or a grant from the new client would have been refused. The window was one minute only because the anchor session was watching and restarted both services at 00:41. The PR itself allowed for the opposite skew (`has_control` reads either name), which is the care fix (a) asks of every change — and still not enough without a restart. **(d) decided and done 2026-09-17 (Paul):** the live venv is now a non-editable install (it had been editable, against the README's own advice — which also meant any branch checked out in the main checkout was live at once, templates without even a restart); the anchor session promotes after a merge that should be live, by the two commands now in `CLAUDE.md`, which replace client and agent together, so the skew this entry describes cannot arise from a merge. **(a) and (b) done 2026-09-17 (PR #197, session `tdgrind-ao-1`):** both are properties of the envelope now, not of any one command, so no new parameter needs its own dance again. (a) `LocalClient.call` drops a parameter whose value is `None` — every optional RPC parameter means the same absent as `None` — so a call that does not use a new feature never mentions it and cannot be refused for it; `ao progress` says *unset* by passing `force=None` and its hand-rolled skip is gone, as is `ao msg`'s own filter. (b) `_dispatch` removes the parameters the method does not take, before the gate so a refusal still says why it refused, runs the call without them, and names them in the reply's `ignored: [...]`; `ao` prints one stderr line, accumulated across every call the command made — the review of PR #197 caught it clearing itself on the last call, which is exactly wrong for `ao team start` and `ao control … add <many>`, where the call that skews is the first — and worded as *what happened, then why*, because a caller bug against an agent of the same age looks identical from the client; the agent logs the same thing with the method name, which is what tells the two apart. A method taking `**kwargs` (`hook`) keeps everything. Tests: `tests/test_rpc_skew.py` (six, including the falsy-but-set case — `wait=False`, `--lines 0` — which is *set* and still sent). **Fix (c) merged 2026-09-21 (`grinder-ao-1`, PR #402; live check on the board):** a wheel says which commit it came from — `pdm_build.py`, pdm-backend's local hook, writes `sessionorc/_build.json` (commit, dirty, source directory, built at) into every wheel built from a git checkout; the host agent reads it at start and reports it on `host` as `built_from` with `started_at`; `ao status -v` and `ao service status` print one line saying how many commits `origin/main` in that checkout, as last fetched, holds that the running build does not, or why that cannot be said (design §4.4 *What is running says which commit it is*). It reaches the live system at the next promote, and the first promote after it is the first build that carries a record — until then the line reads *build unknown*. Not done: the page (the top bar could carry the same line) and `ao team start` saying it — the line is on the two commands a person reads for the system's health, not on every call, since a worker cannot promote. **Still open on 2026-09-17** (the Resolved line below answers it): an `ao service upgrade` that does the promotion in one command and refuses while a team is live unless forced; and team `brief:` files, which are still read from the checkout at start and so still follow whatever branch is checked out there. Note the two halves only protect an agent *older* than the client from this release on: the running agent predates (b), so the first promotion is still a hard cut.

**Location:** `src/agentorc/cli.py` (every `cmd_*` that passes an optional parameter whether it is set or not — `cmd_progress` did so with `force` until PR #184, and still does with `why` and `pr`), `src/sessionorc/agent.py` (`_dispatch` passes params straight to the method, so an unknown one is a `TypeError`), the live install (`~/.local/share/agentorc-venv`, editable over `/home/kmaster/agentorc`).

**Why:** the live services run from an editable install of the main checkout. A worker's PR that merges and is pulled changes the `ao` client for every session at once, while the host agent keeps the code it started with. PR #180 (TD-056, merged 2026-09-16 23:32) added `force` to the `progress` RPC; from then until PR #184 merged at 00:03 every `ao progress claim|done|none` from every worker on both teams failed with *unexpected keyword argument 'force'*. No card showed progress, no lease was taken, and samscrape's `tdgrind-2` could not record its out-of-work declaration — its lead would have read its exit as a crash. The workers coped by mailing claims and results, which is how this was seen. The teams merge RPC changes as their ordinary work, so this recurs on every such merge.

**Done when** a merge that adds an RPC parameter leaves every existing `ao` call working against the running agent, and a person is told the agent is older than the code.

**Resolved:** 2026-09-29 (PR #743; grinder-ao-1, TD-132 slice 5). (a) and (b) since PR #197 keep an older agent answering a newer client; (d) since 2026-09-17 makes a merge live only by a promote, now TD-132's press or policy (§6 *Promote*); (c) tells the person, in four places from one function (`sessionorc.build`): `ao status -v`, `ao service status`, the end of `ao team start`, and the Org top bar's **build** chip, drawn only while the running build is not main's head. Live look on the board with TD-132's.

**Related:** TD-058 (the restart this forces was a 90 s hang until #176 loaded), TD-052 (mail carried the claims while progress was down), design §4.4 (the RPC envelope, and the skew rule (a) and (b) are written into).

## TD-234: A failed Snooze or Delete on an orphaned question's row reads *not written*

**Priority:** Low
**Added:** 2026-09-28 (the techlead's read of #724, noted not a finding; filed by grinder-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/static/app.js` (the row action handler's `catch`: ``rowerr.textContent = `not written: ${e.message}` ``), `src/agentorc/ui/templates/inbox_row.html` (`.rowerr`, the orphaned row)

**Why:** design §4.5a *Inbox row: orphaned question* draws a **refused write** on the row as *not written: …*, since the answer's road ends in a board line. The handler draws it for any failed press on a row that carries a `.rowerr`. So a failed Snooze (`snooze`, offered once no clock runs) or Delete (`unmail`) reads as if a board line had been refused, when nothing was being written.

**Resolved:** 2026-09-29 (PR #744; grinder-ao-2). The row's error is drawn only for `reply`, `answer` and `gowithit`, the presses that write the board line; any other failed press keeps its toast alone. `tests/test_ui_orphaned.py` `test_only_a_failed_write_is_drawn_as_not_written`.

**Related:** TD-216 (the row), #724.

## TD-235: Rule 2's wanted restart undoes a person's Close

**Priority:** Medium
**Added:** 2026-09-29 (grinder-ao-1, from the techlead's read of #748)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_wanted_restart`: `closed_by_tick`)

**Why:** `_wanted_restart` treats a `closed` record whose last `restarts` entry has `why: wanted` as the tick's own close followed by a failed replay, and replays it. A successful wanted restart leaves that same entry on the new record. So a member that was restarted once by rule 2, declared `restart_wanted` again, and was then closed by a person is started again on the next tick. The closed path also skips the idle and git tests. Design §6 rule 2 says a kill or a Close is never undone. Rule 7's `_brief_restart` had the same hole; #748 fixed it there by requiring the entry's `error`.

**Fix:** retry a closed record only when its last entry is a `wanted` entry that carries `error` (both failure paths write one: `close: …` and the replay's). Test: restarted once by rule 2, declares again, is closed by a person, and is not restarted. Consider one helper shared with `_brief_restart`.

**Resolved:** 2026-09-29 (PR #749; grinder-ao-1). `_closed_by_tick(s, why)` in `agent_tick.py` serves rules 2 and 7: a `closed` record is the tick's to retry only when its last entry is that rule's and carries `error`. Design §6 rule 2 says so. Test: `test_a_person_s_close_after_a_wanted_restart_is_never_undone` in `tests/test_wanted_and_nudge.py`.

**Related:** TD-217 (rule 7, same fix in #748), TD-186 (the restart rules' races), design §6 rule 2.

## TD-236: A person's Close after a failed restart is undone by the tick

**Priority:** Low
**Added:** 2026-09-29 (the techlead's read of #749; filed by grinder-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_closed_by_tick`)

**Why:** after TD-235, a `closed` record counts as the tick's own failed restart when its last `restarts` entry is `wanted` (or `brief`) and carries `error`. A member whose restart failed without being closed carries that same entry. That is an exited one whose replay failed, or an idle one whose close failed before `rpc_close` marked it. If a person then Closes it, the next tick read the record as its own and started it again. `rpc_close` records no closer, so the entry alone cannot tell the two apart. Design §6 rule 2 says a Close is never undone.

**Resolved:** 2026-09-29 (PR #750; grinder-ao-1). `_closed_by_tick` also requires the entry's `at` to be at or after the record's `closed_at`. The tick's own close is stamped before its failure is written; a person's Close comes after the entry. Design §6 rule 2 says so. Test: `test_a_person_s_close_after_a_failed_wanted_restart_is_never_undone` in `tests/test_wanted_and_nudge.py`.

**Related:** TD-235 (the same rule's first hole, #749), design §6 rules 2 and 7.

## TD-237: The record does not say who closed it, so a person's Close can still read as the tick's failed restart

**Priority:** Low
**Added:** 2026-09-29 (the techlead's read of #750; filed by grinder-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_closed_by_tick`, the tick's closes in `_wanted_restart` and `_brief_restart`), `src/sessionorc/agent.py` (`rpc_close`), `src/sessionorc/models.py` (a closer on the record, and whether the home or the node owns it)

**Why:** after TD-235 and TD-236, `_closed_by_tick` reads a `closed` record as the tick's own failed restart when its last `restarts` entry is that rule's, carries `error`, and was written at or after `closed_at`. Two cases still undo a person's Close (design §6 rule 2):
1. `now_iso()` is whole seconds and the comparison is `>=`, as it has to be, since the tick's close and its failure usually share a second. So a person's Close in the same second as the failed entry reads as the tick's.
2. For a node's member, `closed_at` is the node's clock and the entry is the home's. If the node's clock runs behind, a person's Close there can read as earlier than an entry the home wrote before it. Rule 2 acts on node members; rule 7 does not yet.

Both go away only when the record says who closed it.

**Fix:** the tick's own close records that it was the tick's, for example a `closed_by` field that the tick's close sets and any other close clears. `_closed_by_tick` then reads that field in place of the clock comparison. For a node's member, decide whether the home owns the field on its mirror (so an older node needs no new RPC parameter, per the skew rules in design §4.4) or the node owns it. A record with no closer reads as a person's Close. Design §6 rule 2 is changed first.

**Resolved:** 2026-09-29 (PR #752; grinder-ao-1). `closed_for` is a new home-owned field on the record. The tick writes it after its own close for a restart, and any other close clears it: `rpc_close`, and `_route_act` for a close routed to a node. `_closed_by_tick` reads the mark in place of the clocks. Design §6 rule 2 and §4.4's home-owned list say so. Tests: `test_the_ticks_own_failed_close_is_marked_and_a_person_s_close_in_the_same_second_clears_it` in `tests/test_wanted_and_nudge.py`, `test_a_close_routed_to_a_node_clears_the_ticks_mark_at_the_home` in `tests/test_link.py`.

**Related:** TD-235 (#749), TD-236 (#750), design §6 rules 2 and 7, §4.4 (the RPC skew rules).

## TD-238: A person's Close made at a node leaves the tick's mark on the home's copy, and the tick starts it again

**Priority:** Low
**Added:** 2026-09-29 (the techlead's read of #752; filed by grinder-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_mark_closed`, `_closed_by_tick`), `src/sessionorc/models.py` (`closed_for`)

**Why:** TD-237's `closed_for` is cleared by any close the home runs. A person's Close made at the node itself never leaves the node (`modes.py`: *a person's act on a pane never leaves the node*), so it cleared nothing. Take a node's member the tick closed and failed to replay, which a person then closes again at the node: it kept the home's mark, and the tick started it again. Before #752, TD-236's `closed_at` test caught that case. Also, rule 2 said a failed close "is tried again by the tick", but a close routed to a node whose verdict never came back leaves no mark, so that restart strands.

**Resolved:** 2026-09-29 (PR #753; grinder-ao-1). The mark is `{why, closed_at}`, naming the `closed_at` the tick's own close wrote. For a node's member that is the node's stamp, taken from the close's reply, which `_route_act` applies before it returns. A Close at the node writes a new `closed_at`, and `_closed_by_tick` requires the two to be equal: a comparison for equality, not an order between two hosts' clocks. The unknown-verdict routed close is left as it was, since it fails safe, and design §6 rule 2 now says so. Residual: a person's re-Close at the node within the same whole second as the tick's close writes an equal stamp. Test: `test_a_persons_close_at_the_node_no_longer_matches_the_ticks_mark` in `tests/test_link.py`.

**Related:** TD-237 (#752), TD-236 (#750), TD-235 (#749), design §6 rule 2.

## TD-208: The Inbox reads each board from the local working tree, so a checkout behind origin hides items

**Priority:** Medium
**Added:** 2026-09-27 (Paul: *yes look into the second part*, after TD-207's two items could not be found)
**Owner:** designer
**Kind:** design-first
**Status:** Resolved
**Location:** `src/agentorc/ui/inbox.py` (`board_argv`: the boards are `<root>/docs/user_attention.md` of each registry root, read with `--report --json` — `--due-only` went with TD-220 — and no `--fetch`), design §4.5 screen 6 (*Board items*), §4.4 (the board write-back), dev-cadence's `nudge_user_attention.py` (`--fetch`, TD-030 there)

**Why:** every board line a session writes lands on origin by a merged PR. The registry's roots are the main checkouts, which move only when someone pulls. The Inbox reads the file in each checkout's working tree, so an item merged on origin is invisible until that checkout is pulled. Measured 2026-09-27 after a `git fetch` of each: dev-cadence 10 commits behind (its board lacked grinder-dc-1's two `act` items), agentorc 4 behind, samscrape 1. Three of the six boards differed from origin. The SessionStart hook already solves this for itself: dev-cadence's reader takes `--fetch` and reads a merely-behind clone's board from `origin/<default>`, bounded by `ATTENTION_DUE_FETCH_BUDGET` (8 s) with `--due-only`. The Inbox doesn't pass it.

**Fix:** design where the fetch happens and what the write-back does then: (a) pass `--fetch` on the Inbox's read (at most once a minute already; the 8 s budget bounds it), or (b) the host agent's repo tick (TD-176, which already reads the remote every five minutes) fetches, and the Inbox reads with `--fetch`'s origin fallback; (c) what Snooze / Done / Reply do on an item that exists only on origin, since the write-back commits to the local default branch (§4.4), so it must pull first or refuse and say so; (d) a checkout that is ahead or diverged is read from the working tree, as the reader already does, with a note. Then the build, with a test on a clone that is one commit behind. Done when a board line merged on origin shows in the Inbox within the read interval without a pull.


**Resolved:** 2026-09-29 (designed PR #715; built as TD-221, PRs #771 and #774). Design §4.5 screen 6 *Boards are read against origin*; whether a write-back pushes and whether the host agent may pull stays TD-222.

**Related:** TD-207 (the horizon), TD-069 (board rows, the write-back), TD-176 (the repo tick), dev-cadence's TD-030 (`--fetch`).

## TD-221: Build the board read against origin

**Priority:** Medium
**Added:** 2026-09-28 (the designer, from TD-208's design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/inbox.py` (`board_argv`, `BOARD_TTL`, `BOARD_TIMEOUT`), `src/agentorc/ui/app.py` (`read_boards`, `board_items`, `board_fetch`, and the read of one board after Snooze, Done, Reply and Put on the board), the Inbox's and the Repo page's templates; design §4.5 screen 6 *Boards are read against origin*, §4.5a **origin note**. No held path: the host agent's write-back (`src/sessionorc/board.py`) is not changed.

**Why:** TD-208's *Why*: a board line merged on origin is invisible in the Inbox until the main checkout is pulled.

**Fix, in slices a PR each:**
1. **The read**: `board_argv` adds `--fetch`; the fetching read is bounded by `BOARD_FETCH_TIMEOUT` (45 s) and, stopped or failed, followed at once by a plain read, the reading marked *origin could not be reached* with the reason; the reading keeps each board's `source` and `fetch_note`.
2. **One read at a time**: a request that finds the reading stale starts a read only if none runs and is answered from the last reading; the read after a press is a plain read of that one board laid over the last reading.
3. **The note and the rows**: the case is read from `source` (non-null only when the board was read from origin) and the fixed phrases of `fetch_note` — its opening, *fetch skipped*, *fetched; no board at*, *fetched; board matches*, *fetched; local clone is behind*, and after *fetched; board DIFFERS from* the parenthesis, *(no common history*, *(local edits not pushed)*, *(both sides changed)* — in one table with a test that fails on a phrase it does not know, and a field for the case is asked of dev-cadence; one line above a repo's first board row on the Inbox and the Repo page, in the design's words — behind, local edits, two-sided (the warning colour), origin not reached — and none when the board matches or origin has none; on a board whose `source` is origin, Snooze, Done and Reply disabled with the design's reason, Open board kept.
4. **Tests**: on a clone one commit behind, a board line that is only on origin shows within one read, its note says it was read from origin and its Snooze is disabled; after a pull the same row is pressable; a two-sided clone shows the local rows and the warning note; a remote that does not answer leaves the local rows and the *not reached* note, inside the bound; two requests on a stale reading start one read; a press does not wait on a fetch.

**Done when** a board line merged on origin shows in the Inbox within the read interval without a pull, and a remote that is down never empties the board rows; design §4.5, §4.4 and §4.5a lose their *not built* for this entry, and TD-208 archives with this one.


**Resolved:** 2026-09-29 (PR #771, slices 1–2; PR #774, slices 3–4; grinder-ao-2). The Inbox's board read passes the reader's `--fetch`, bounded at `BOARD_FETCH_TIMEOUT` with a plain read behind it, one read at a time; the origin note (`ORIGIN_PHRASES`, `origin_note`, `origin_firsts` in `src/agentorc/ui/inbox.py`) above a repo's first board row in each list, and a board read from origin read-only on the page and at the route. Design §4.5 screen 6 *Boards are read against origin* and §4.5a **origin note** carry the lasting content; the field for the case is asked of dev-cadence on the board.

**Related:** TD-208 (the design), TD-220 (the horizon: the same read, without `--due-only` and so without the reader's 8 s budget), TD-222 (the push and the pull), TD-069 (the write-back), dev-cadence's TD-030 (`--fetch`).

## TD-242: `test_a_metered_accounts_spend_is_summed_noted_and_gated` fails near local midnight

**Priority:** Low
**Added:** 2026-09-29 (grinder-ao-1, seen in its own suite runs late in the evening; filed at the techlead's read of #784)
**Status:** Resolved
**Location:** `tests/test_spend.py` (`test_a_metered_accounts_spend_is_summed_noted_and_gated`)

**Why:** the test failed on `main` when the suite ran within an hour of local midnight, and passed otherwise; a flaky test in the gate costs every PR a rerun. Reproduced at 23:30 MDT on 2026-09-29, at `test_spend.py:199`: after the pause at the day's amount the test ran the gate again at `now + timedelta(hours=1)` (*a restart of the home*), and a day window is the home's local day (§4.2a), so that instant was past the window's reset and the pause was lifted.

**Resolved:** 2026-09-29 (PR #787, grinder-ao-1) — the second gate runs a second later, not an hour: the restart needs a later instant, not a later day. The test passes at 23:31 local, where it had failed a minute before.

## TD-243: `test_derived_entries_go_to_the_record_that_holds_the_directory` flakes on a live tick

**Priority:** Low
**Added:** 2026-09-30 (grinder-ao-1, CI on PR #803, 3.13 runner)
**Status:** Resolved
**Location:** `tests/test_agent.py` (`test_derived_entries_go_to_the_record_that_holds_the_directory`), `tests/conftest.py` (`park_ticks`, `derived`).

**Why:** the test creates `run-1` in a repo already on `td077-cap`, claims TD-070 for it, kills it, starts `run-2`, and asserts the exited `run-1` holds only TD-070. The `agent` fixture's tick loop runs at `FAST_TICK`, so a derive can run while `run-1` is still live and credit it with the branch's TD-077 (`assert ['TD-070', 'TD-077'] == ['TD-070']`, run 2026-09-30 on #803, a PR that changed no code). The behaviour is right; the test raced its own clock.

**Fix:** `await park_ticks(agent)` at the start, so every tick is the test's own (`derived` drives them by hand), as TD-078 and TD-088 did for their tests.

**Related:** TD-063 (the CI flakes), TD-078, TD-088 (`park_ticks`), TD-034 (what the test holds).

**Resolved:** 2026-09-30 (PR #805, grinder-ao-1) — the test parks the fixture's tick loop and awaits a derive the loop had already started, so every derive it asserts on is one `derived` drove; five runs in a row pass.

## TD-218: Build Add entry, the home's half

**Priority:** Medium
**Type:** feature
**Added:** 2026-09-28 (the designer, from TD-180's design)
**Owner:** grinder
**Kind:** build
**Status:** Resolved. **Slice 1 built 2026-09-29 (grinder-ao-1, PR #760):** `MailEntry.entry` and `handed_entry`, `asks_waiting` counting a handed entry while it owes and not while the seat's own question on its thread is open, `read_when`'s `refills` (the `msg` sentence, the view's `refill`, the person inbox's `on_handed` for the Reply dialog). **Slice 2 built 2026-09-29 (grinder-ao-1, PR #764):** `rpc_entry_add` (person-only; the caller hands `teams: [{team, seat}]`; no bound; `handed` and `entry` marked), `ENTRY_TYPES`, `read_when`'s `lapses`, `entry_add` in `modes.HOME_EDITS`. **Slice 3 built 2026-09-29 (grinder-ao-1, PR #765):** `--thread <handed id>` puts the question on the entry's thread and settles nothing (it had written `asked_again`, from the review of #760); `inbox_dismiss` naming a handed entry held in a session's inbox ends the debt and tells the holder by a `system` note (the techlead's note on #760); the person's `inbox` read lists handed entries still owed, or `blocked`, as `handed` with `holder`, `holder_name` and `holder_state`; the seat's nudge names owed entries apart with the `--outcome` line. **The one copy is the seat's (the techlead's read of #764 asked for a person-inbox copy):** a person copy would be an open `ask` from the person in the person inbox, which counts toward its depths, is never pruned while open and is filed under *Needs you*, so slice 3 lists the seat's copy in the person's read instead; `_sweep_mail`'s expiry of mail in a `closed` record's inbox leaves a handed entry owing (`owes` reads `handed` and `outcome` alone). **Slice 4 built 2026-09-30 (grinder-ao-1, PR #813):** `ao td add` (`cli.cmd_td_add`), handing the main checkout's path; `entry_teams` moved to `agentorc.teams`. The row under *Waiting on them* was TD-219's (built 2026-09-29).
**Location:** `src/sessionorc/mail.py` (the envelope, `read_when`), `src/sessionorc/models.py` (`asks_waiting`), `src/sessionorc/agent_inbox.py` (`entry_add`, beside `rpc_board_edit`), `src/sessionorc/agent_mail.py` (`_owing_question` and `_question_to_take_up`: what `--for` and `--thread` accept), `src/sessionorc/modes.py` (a home-owned write: the table that `suspend` and `identity_log` were once missing from), `src/agentorc/cli.py` (`ao td add`); design §4.10 *An entry handed to a seat*, §4.9b (`asks_waiting`), §4.7 *Entries*. Held path: `src/sessionorc/**` waits for the techlead's read.

**Why:** TD-180's *Why*. The form of TD-219 needs a message the seat is filled by, that is closed by its outcome, and that the seat can ask the person about and be filled again by the answer; none of the three exists.

**Fix, in slices a PR each:**
1. **`asks_waiting` and a handed entry**: a handed entry addressed to the record counts while it owes its outcome, read or not, and not while an `ask` of the record's own to the person with the entry as its `root` is open. It is written with no bound and never lapses. `read_when` gains the row for a person's answer on such a thread to a seat on call, and the Reply composer draws it.
2. **`entry_add {repo, type, text}`**: person-only, served by the home; resolves the repo in the registry, its first servicing team and that team's techlead seat from the definitions handed to it by the caller as `board_reply`'s `refs` are (the host agent does not read `org.yml`); writes an `ask` from the person carrying `entry: {repo, type}` and `handed`. Refused in words: no such repo, no team services it, the team defines no techlead seat, an unknown type, empty text, a session as the caller.
3. **Closed by its outcome**: `--outcome … --for <id>` and `--thread <id>` accept a handed entry addressed to the caller; a `--thread` question leaves the entry open and its debt standing; the entry closes when the outcome lands or the person dismisses it; it is listed under *Waiting on them* while the definition names the seat, filled or on call.
4. **`ao td add [--repo] [--type] ["<words>"]`**: the argument or standard input; prints the id and the *when it is read* sentence; refused from inside a session.
5. **Tests**: a seat idle with a handed entry and its own question open has no `seat_due` and is closed; the person's reply sets `seat_due` and the tick fills it; a seat that exited with the entry owing and no question open is filled; a handed entry does not lapse at an `ask`'s default bound; `entry_add` from a session is refused; a handed entry is not closed by a reply and is closed by `--outcome done`; `--thread` on it draws the person's words above the question; a node forwards `entry_add` to the home.

**Done when** `ao td add "…"` from a person's terminal fills the techlead seat with a message carrying `entry`, a question the seat asks is answered by a reply that fills the seat again, and `--outcome done --for <id>` closes the row; design §4.10, §4.9b and §4.7 lose their *not built* for this entry.

**Related:** TD-180 (the design), TD-219 (the form), TD-142 (the `handed` note a board reply sends), TD-103 (the tick's seat rule), TD-168 (`read_when`), TD-213 and TD-215 (a question that outlives its asker: a seat's question on an entry's thread names no reference until the entry has its number).

**Resolved:** 2026-09-30 (PR #760, #764, #765, #813; grinder-ao-1) — design §4.10 *An entry handed to a seat*, §4.9 *Add an entry to the ledger* and §4.7 *Entries* carry what was built; `tests/test_handed_entry.py` and the `td_add` tests in `tests/test_cli_teams.py` hold it.

## TD-240: Whether a team has finished is the manager's judgement from memory, not a reading of its members' records

**Priority:** Medium
**Added:** 2026-09-29 (Paul: *I wanted to start it, saw there was no start choice, so hit "wind down"… now the team seems to be idle with no start button*; then: *seems like we should be able to check member states mechanically*)
**Owner:** designer
**Kind:** design-first
**Pickable:** no — designed; built as TD-241
**Status:** Designed 2026-09-29 (the designer, PR #775; the steer to Paul is `m-131e40cf9385`, bound 2026-09-30 09:02 MDT): design §6 rule 9 *Finished is the home's reading*, §4.9a, §4.5a *team groups* (*concluded* from the same reading, the *not concluded* line), §4.9 `ao team status --json`. Of the Fix's three: (a) and (c) together, since the page and the tick must agree, and (b) as the brief's word. Closes with TD-241.
**Location:** `src/agentorc/briefs/manager.md` (*Out of work*: the manager decides every member is finished and winds the team down), design §4.9a (the wind-down, *finished means declared*), §4.5a **team groups** (*concluded*: every live session idle and declared, the manager included), the tick (`agent_tick.py`)

**Why:** dc-grind sat live and idle for most of 2026-09-29. grinder-dc-1 declared `out_of_work` at 04:19Z, took TD-072 later in the day, and declared again at 23:54Z (dev-cadence: 0 pickable, three entries on Paul's decisions), but manager-dc-1 never wound the team down. Its rounds read *continued quiet, grinder still interactive under Paul*, a belief two days old: Paul had made the grinder interactive on 2026-09-27, and the anchor restarted it unattended the same evening (`ao new`, 22:06 MDT), as its record's `unattended: true` said throughout. Because the manager never declared, the page's *concluded* test (every live session idle **and** declared, the manager among them) never held. So the card offered **Wind down**, not **Start**, and when Paul wanted to start the team he could only wind it down. Every fact the manager needed was on the records: each member's `unattended`, `state`, `out_of_work`. The manager read its memory instead. TD-199 is the same shape: a running member keeps what it knew at its start.

**Fix:** design the team's *finished* as a reading the home makes, not a judgement a session keeps. Options: (a) the tick, which already derives *concluded* for the page, winds a team down itself when every member is finished and its seats are idle (a policy beside TD-214's rule 8, which restarts it when work arrives); (b) the manager's round reads `ao team status --json` (the members' `unattended`, `state`, `out_of_work`) every round and is told never to rely on an earlier round's reading; (c) the page's *concluded* stops requiring the manager's own declaration when every member has declared, and offers **Start** (which closes the concluded sessions first) then. The round may take more than one. Also: the team card's controls should say why Start is absent (*live: manager-dc-1 has not declared*). Done when a team whose members have all declared out of work reads concluded, or winds down, within a tick or a round, and a test covers a manager that has not declared.

**Related:** TD-199 (a running member keeps its start brief), TD-214 (rule 8, a wound-down team gaining work), TD-213 (archived: the wind-down's closes took the person's questions), TD-053 (wind-down), §4.9a, §4.5a **team groups**.

**Resolved:** 2026-09-30 (PR #775, the design; built as TD-241 — PR #816, #821, #828, #831, #837) — design §6 rule 9 *Finished is the home's reading* and §4.9a carry it; what the build left open is TD-256.

## TD-241: Build finished as the home's reading — rule 9, the page's concluded, the manager's brief

**Priority:** High
**Type:** feature
**Added:** 2026-09-29 (the designer, from TD-240's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Done — **slice 1 built 2026-09-30 (grinder-ao-1, PR #816):** `work.finished(records, seats)` and `work.manager_of`, over the home's `Session`s or a client's views; `teamrun.concluded` returns the reading where it holds, so the page and `_close_concluded` take an idle manager that never declared; `tests/test_finished.py` holds slice 5's cases for the reading (the `why` clauses, a working seat, an interactive member, a dead member with `restart_wanted`, `restart` true). The answer is given for every team with an unattended session live and **holds when `why` is empty**; a blocking dead member has a clause too. **For slice 2:** `rows` does not carry `why` yet (it calls `concluded`; the *not concluded:* line reads `finished(mine, seat_names(t, mine))["why"]`), and `group_head.html`'s title on *concluded* still says every session *has declared its run over*, which the manager need not have. **Slice 3 built 2026-09-30 (grinder-ao-1, PR #821):** `_finished_pass` / `_finished_team` in `agent_tick.py`, run on the home's tick after the keep-running pass — `_finished_first` in memory, `FINISHED_SETTLE` (`agent_common`), `_finished_close` (idle, git fields known and clean, a node's over its link), the manager's one line and `finished_sent_at` (`models`, home-owned), its close `WRAPUP_GRACE` later with `closed_for: {why: finished}`, `_finished_tell`'s note to the person, and `work.wound_down` passing over a manager with that mark (`work.closed_finished`); slice 5's tick cases are `tests/test_finished_tick.py`. **For slice 2, from slice 3:** the Inbox row *manager did not close* is a live record whose `finished_sent_at` is older than `WRAPUP_GRACE` (nothing else is stored), and the card's *· by the tick* is a manager record for which `work.closed_finished` is true. Not tested against a pane or a link: the line's typing is `_policy_send`'s, and a node's close is `_route_act`'s. **Known gap, from the review of #821:** where no manager is live to carry `finished_sent_at`, the note to the person is written only on a tick whose own close ends the last member, so a person's Close of a member left open with work ends the team with no note — a mark on the team's `host` entry would cover it, if it is ever seen. **Slice 2 built 2026-09-30 (grinder-ao-1, PR #828), but for what reads slice 3's fields:** `teamrun.rows` asks the reading once and carries `not_concluded` (its `why`), `ui/repo.py` `team_groups` passes it for a live team that is not concluded, and `group_head.html` draws *not concluded: …* under the counts, above *who for what*, with the new help entry *not concluded* (`ui/help.py`, §4.5a's list) as its hover; *concluded*'s hover says every member declared, not every session; `ao team status --json` carries `unattended`, `seat`, `out_of_work`, `restart_wanted` per row and `finished` on the reply (`tests/test_cli_teams.py`, `tests/test_ui_teams.py`). **The rest of slice 2 built 2026-09-30 (grinder-ao-1, PR #831):** the Inbox row *manager did not close* (`ui/inbox.py` `unclosed_mark`, raised in `state_rows`; §4.5a's new row, **Open** alone — a steer to the techlead with that default, m-8e0282638dba) and the card's *· by the tick* (`teamrun.rows` `by_tick`); tests in `tests/test_ui_inbox.py` and `tests/test_ui_teams.py`. **Known gap, from the review of #831 (`sessionorc`, the writer's):** a person's Resume of a manager rule 9 closed keeps `finished_sent_at` and `closed_for: finished` on the record — only a member at work removes the first and only a Close rewrites the second — so the resumed manager raises *manager did not close* at once (and the tick closes it again once idle and clean), and if it then exits its team still reads *· by the tick*; the cure is to drop both marks where a resume brings the record back. **Slice 4 built 2026-09-30 (grinder-ao-1, PR #837):** `briefs/manager.md` *A round* step 1 reads every member's state from `ao team status <your team> --json` each round and carries none over, and *Out of work* says the team's finished is the home's reading, that the round may still end the team first, and what the tick's line means; a test in `tests/test_cli.py` binds the brief's quotation of the line to `agent_tick.py`. The live half of *Done when* is on the board.
**Location:** `src/sessionorc/` (a `finished(records)` reading in `work.py`, beside `wound_down` and `team_wound_down`, which TD-227 put there; `agent_tick.py` `_keep_running`, a team pass beside `_team_stop_times`; `agent_common.py` `FINISHED_SETTLE`; the close and `closed_for` of rule 2 in `_wanted_restart`; `agent_mail.py` `_system_note`), `src/agentorc/teamrun.py` (`concluded`, `wound_down`, `rows`, `_close_concluded`, `stop_members`, `stop_lead`), `src/agentorc/ui/repo.py` (`team_groups`: `concluded`, `stopped`), `ui/templates/group_head.html`, `src/agentorc/cli.py` (`cmd_team_status`), `src/agentorc/briefs/manager.md` (*A round*, *Out of work*), `tests/test_cli_teams.py` (or a new `tests/test_finished.py`) and the tick's tests

**Why:** TD-240's *Why*: dc-grind sat live and idle for a day because its manager's memory said a member was interactive, and the page asked for that manager's own declaration before it would offer Start.

**Fix, in slices a PR each:**
1. **The reading** (`sessionorc`): `finished` takes the team's badged records and nothing else — a seat is a record with `seat`, the manager the record the others list in `controllers` that holds `control`, a person's `unattended: false` — and answers `{at, restart, names, why: [...]}` or none — every unattended live non-seat non-manager record finished (`out_of_work`; a dead one with `out_of_work` counts, a crashed one or a dead one with `restart_wanted` blocks, other dead ones are passed over), `restart` true when any counted member carries `restart_wanted`, seats idle or gone, the manager idle when live; interactive records ignored; `why` one clause per unattended live session that keeps it from holding (*grinder-dc-1 working*, *grinder-dc-2 idle, not declared*). The clients' `teamrun.concluded` calls it, so the page and `ao team start`'s `_close_concluded` change with it: the idle manager is among what Start closes.
2. **The page and the terminal**: `group_head.html` draws the *not concluded:* line from `why` on a live, defined team that is not concluded; `cmd_team_status --json` adds `unattended`, `seat`, `out_of_work`, `restart_wanted` per row and `finished` on the reply; help text for the line goes into §4.5a's help list and `ui/help.py` together (bound by `tests/test_help.py`; the wording is this slice's).
3. **Rule 9** (`sessionorc`): a team pass on the home's tick keeps, in memory, when the reading first held per team, dropped when it stops holding; past `FINISHED_SETTLE` (ten minutes), and only with `restart` false, it closes each finished member under `_unsafe_to_close`'s check (the tick's own form of it), sends a home manager the fixed line once (`finished_sent_at`; an idle composer only, as rule 4; a node's manager gets no line), and `WRAPUP_GRACE` after `finished_sent_at` closes the manager once `idle` and clean with `closed_for: {why: finished, closed_at}`, or leaves it and draws the Inbox row *manager did not close*; a node's member is closed as rule 2 closes one; seats are left to rule 3. Then one `system` note to the person — the PRs from the members' `progress` entries reported `done` with a `pr` since the earliest `created_at` among the team's records that are neither superseded nor forgotten, and each member's `out_of_work.why` — unless a `note` from the manager reached the person inbox after the reading first held. `work.wound_down` and `work.team_wound_down` (the card's reading and rule 8's, since TD-227) skip a manager whose `closed_for` says `finished`, and the card's *wound down* gains *· by the tick* from the mark.
4. **The brief**: *A round* says the members' states are read from `ao team status --json` (or `ao status --json`) every round and never carried from an earlier one — an `interactive` member is one whose record says `unattended: false` now; *Out of work* says the team's finished is the home's reading, that the round may still end the team first, and what the tick's line means when it arrives.
5. **Tests**: two members declared and a manager idle without declaring reads finished, and `concluded` on the page agrees; a member `restart_wanted` reads *restart*; a working seat blocks it; an interactive member counts for nothing; a member that claims again inside the settle stops the wind-down; the tick closes the members and sends the manager one line, then closes it after the grace with the mark, and the team then reads *wound down · by the tick*; a member with unpushed work stays open and is named; a member `exited` with `restart_wanted` blocks the reading; a team with `restart` true is not wound down; the announcement is written when the manager wrote none and not when it did; the `why` clauses name the right sessions.

**Done when:** on a scratch home, a team whose two grinders declared `none` while its manager sits idle reads *concluded* on the card with Start as its one control within a tick, and, left alone, is wound down by the tick within `FINISHED_SETTLE` + `WRAPUP_GRACE` with one note in the person's inbox and *wound down · by the tick* on the card.

**Related:** TD-240 (the design), TD-199 and TD-217 (a member's stale memory of its brief, the same shape), TD-214 / TD-227 (rule 8 starts a wound-down team again), TD-237 and TD-238 (`closed_for`), TD-125 (the announcement), TD-053 (the wind-down).

**Resolved:** 2026-09-30 (PR #816, #821, #828, #831, #837; grinder-ao-1) — design §6 rule 9, §4.9a, §4.5a *team groups* and *Inbox row: manager did not close*, and §4.9 `ao team status --json` carry what was built; `tests/test_finished.py`, `tests/test_finished_tick.py`, `tests/test_ui_teams.py`, `tests/test_ui_inbox.py`, `tests/test_cli_teams.py` and the brief's test in `tests/test_cli.py` hold it. The live half of *Done when* is on `docs/user_attention.md` (the 2026-09-30 line, *the home winds a finished team down*); the two gaps the reviews of #821 and #831 named are TD-256.

## TD-252: *TDs in motion* in columns, and the team header's *who for what* line dropped

**Priority:** Medium
**Type:** debt
**Added:** 2026-09-30 (Paul, from the Org page: *put the "TDS IN MOTION" card data into columns. We can also drop the boilerplate "the team's work.." blurb — it does not really add good info*. TD-249 and TD-250 are the designer's, in PRs #822 and #825; TD-251 a grinder's.)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Built 2026-09-30 (grinder-ao-2, PR #838)
**Location:** `src/agentorc/ui/templates/team_summary.html` (the `fmotion` facet's `mrow`), `templates/group_head.html` (the `whofor` line), `src/agentorc/ui/static/app.css`, `src/agentorc/ui/repo.py` (`team_groups`: `who`), `src/agentorc/ui/help.py` and design §4.5a's help list (bound by `tests/test_help.py`), design §4.5a *team card: TDs in motion* and *team groups* (**who for what**), `docs/mockups/gen.py`

**Why:** two things on the team card. (1) A *TDs in motion* row is a flex line — phase, priority letter, reference, title, holders, PR and its state — in which only the priority slot is a fixed width (TD-232) and the title takes what is left; the phase, the reference, the holders and the PR are each as wide as their text. So down a list the reference starts where the phase word ends, the holder and the PR wherever that row's own widths put them, and a row with no PR ends short. The *Doing* facet beside it was given fixed-width columns by TD-232 and reads at a glance; this one does not. (2) Under every team's header sits the *who for what* line (TD-162, built by TD-171): *the team's work: what it picks, its pace, a member that is stuck or should stop → manager-ao-1 · a PR on a held path … → techlead-ao-1 (on call) · Grinder: its own card only …*. It is each role's `message:` line joined, two lines of the same words on every team, read once and then only scrolled past; it takes the header's height on a page whose job is to show state.

**Resolved:** 2026-09-30 (PR #838; grinder-ao-2) — design §4.5a *team card: TDs in motion* (**Columns**) and the ***i*** mark row (**who for what**) carry what was built; `tests/test_ui_team_summary.py` (the six cells, the widths) and `tests/test_ui.py` (no line on the header, the panel's list) hold it. The holders column is one ordinary name wide (14 characters), a longer list cut and whole on hover. Screenshots: `docs/mockups/reviews/2026-09-30-td252-*.png`; the live look is on the board.

**Related:** TD-232 (the Doing list's columns and the priority letter), TD-176 (the team card's facets), TD-162 and TD-171 (*who for what*), TD-167 (the help panel).

## TD-257: `test_usage_unknown_is_said_once_a_day_while_a_session_works` fails across 00:00 UTC

**Priority:** Low
**Added:** 2026-09-30 (grinder-ao-1, from manager-ao-1's board line of that day, which Paul answered *take it*)
**Status:** Resolved
**Location:** `tests/test_usage_projection.py` (`test_usage_unknown_is_said_once_a_day_while_a_session_works`)

**Why:** the CI job on PR #807 failed at 23:59:47Z with `assert (2 == 1)` at `tests/test_usage_projection.py:163` and passed on a re-run. The test took `datetime.now(UTC)` and ran the gate again at that instant plus five minutes, and the *usage unknown* note is keyed on the UTC date (`agent_tick.py`, `_unknown_noted`), so a run in the five minutes before midnight UTC said it twice; any PR's CI could fail there. The same class as TD-242.

**Resolved:** 2026-09-30 (PR #842, grinder-ao-1) — the test's instant is noon UTC of the day it runs, so its minutes cross no date; forcing 23:58 before the change fails the same assertion. The test also runs the gate a day later and reads a second note, which holds *once a day* from both sides.

**Related:** TD-242 (`test_spend`, local midnight), TD-233 (the projection and the note).

## TD-253: A restarted grinder's brief names its techlead and its manager as `none` while both exist

**Priority:** Medium
**Type:** debt
**Added:** 2026-09-30 (grinder-ao-2, met in its own brief on the run created 2026-10-01T02:23Z)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved — the lead held: both creates composed the brief with no team in it. As it was read while open: a lead on the cause, not proven (Paul's note, at the end of this line); **a reading from the records (grinder-ao-1, 2026-09-30, `ao --json status`):** grinder-ao-2 is the only record of the team whose replays fill the brief again — its record carries `brief.sources`, and its `restarts` begin at 2026-10-01T00:58Z — while grinder-ao-1, techlead-ao-1 and manager-ao-1, started by the team's start of 2026-09-29, replay `prompt: stored` and name both ids. So grinder-ao-2's launch record was written by a later, different create, and two creates hand `prompt_from` with no team in it: `ao new --role … --team … --brief …` (`cli.py` `cmd_new`: `role.compose(lane, supplement=supplement)`, no `techlead=` and no `manager=`) and the page's New session form (`ui/app.py`, the same call), so their `{techlead}` and `{manager}` slots are stored as `none` from the first run on. `teams.plan`, and Members… → Add through it, pass both. Not confirmed: which create made that record (the launch record is under `~/.agentorc`). If it was one of the two, the fix is theirs: a create that carries `--team` for a defined team fills both ids from `teams.seat_id` and `teams.manager_id` **Which create it was, from Paul (a note of 2026-09-30, `m-4a91de69d248`; a lead, he says, not confirmed):** the record was made by hand on 2026-09-30 at about 23:55Z — the anchor closed the idle record, whose early `restart_wanted` nobody acted on, and ran `ao new -d ~/agentorc -w grinder-ao-2 --unattended --supervised --role grinder --brief docs/briefs/grinder-ao-2.md --lane free-pick --team ao-grind --project agentorc -p grind grinder-ao-2`. That is the first of the two creates above, so the fix is `cmd_new`'s (and the New session form's, `ui/app.py`, whose `preset.compose(…)` passes no team ids either): fill `{techlead}` and `{manager}` from the team's definition when `--team` names a defined team, with a test that such a create stores both ids in `prompt_from.slots`. Every replay of this record reuses the stored `none` until the record is made again by a create that fills them — the team's start, or the person's restart of TD-246 / TD-250. designer-ao-1 was not started this way.
**Location:** `src/sessionorc/brief.py` (`fill`: each slot of `prompt_from` by plain replacement) and `src/sessionorc/agent_tick.py` (`_refill_prompt`, rule 7's replay), `src/agentorc/repoconfig.py` (`Role.compose`: the `{techlead}` and `{manager}` slots, `none` where the value is empty), `src/agentorc/teams.py` (`seat_id`, `manager_id`, `plan`), `src/agentorc/teamrun.py` (`add_member`, the other caller of `plan`)

**Why:** grinder-ao-2's run of 2026-09-30 evening (its third `restarts` entry, `why: wanted`; its record's `brief.sources` are the installed `grinder.md` and `docs/briefs/grinder-ao-2.md`, so the prompt was filled again from `prompt_from`) read *Your team's techlead is `none`*, *`ao msg --kind ask --pr <n> none`* and *`ao msg none "done: …"`*. Its record lists `ao-agentorc-manager-ao-1` in `controllers` and the team has the seat `techlead-ao-1`. Followed word for word, a held PR's ask and every `done` line go to nobody: `ao msg none "…"` answers *no session named none here*. This run found the manager from `ao status --json`; a held PR would have needed the same guess for the seat. Ruled out: `teams.plan` over this repo's own `.agentorc.yml` fills both ids for every member (run in a scratch home, 2026-09-30), so a start from the definition as it stands on `main` is not the cause. Not looked at: what the first launch's `prompt_from.slots` held (the launch record is under `~/.agentorc`, which a grinder does not read), and whether the live copy's build, the org file's own `ao-grind`, or a Members… add made it (written before Paul's note in *Status*, which names a hand-run `ao new`).

**Resolved:** 2026-09-30 (PR #843; grinder-ao-2) — `teams.brief_ids` gives `ao new --team` (`cli._team_slots`) and the New session form (`ui.common.team_brief_ids`) the seat's id, the manager's id and the seat's primer, as `teams.plan` fills them, so both are stored in `prompt_from.slots`; design §4.9 *A person in the team* carries the rule, and `tests/test_cli_teams.py` and `tests/test_ui_teams.py` hold it (the slots equal a team start's; `none` with no seat, an undefined team or no team). The launch record under `~/.agentorc` was not read: Paul's note named the hand-run `ao new`, and the code showed that create passing no team. A record made before the promote keeps its stored `none` until it is made again — on the board.

**Related:** TD-217 (rule 7's replay), TD-113 (the `{manager}` slot), TD-229 (the team's definition moving to the repo), TD-251 (the same brief's `--thread` form).

## TD-256: Rule 9's marks outlive a resumed manager, and a person's Close of the last member ends a person-led team with no note

**Priority:** Low
**Type:** debt
**Added:** 2026-09-30 (grinder-ao-1, from the reviews of PR #821 and PR #831; carried out of TD-241 when it was archived)
**Status:** Resolved
**Location:** `src/sessionorc/agent_tick.py` (`_finished_team`, `_finished_tell`), `src/sessionorc/work.py` (`closed_finished`, `wound_down`), `src/sessionorc/models.py` (`finished_sent_at`, `closed_for`), wherever a resume brings a record back, `src/agentorc/ui/inbox.py` (`unclosed_mark`), `src/agentorc/teamrun.py` (`rows`, `by_tick`), `tests/test_finished_tick.py`

**Why:** two gaps in rule 9 (design §6) as built by TD-241. (1) **A person's Resume of a manager the tick closed keeps `finished_sent_at` and `closed_for: {why: finished}` on the record**: only a member at work removes the first and only a Close rewrites the second. The resumed manager therefore raises the Inbox row *manager did not close* at once, the tick closes it again as soon as it is idle and clean, and if it exits instead its team still reads *wound down · by the tick*. (2) **Where no manager is live to carry `finished_sent_at`, the note to the person is written only on a tick whose own close ends the last member**: a person-led team with one member left open holding work, which the person then closes by hand, ends with no note, though §6 rule 9 says a team that dissolves is never quiet.

**Resolved:** 2026-09-30 (PR #844, grinder-ao-1) — (2) where no manager is live to carry `finished_sent_at` and a member is left open with work past the settle, the home keeps that the note is owed per team in memory (`_finished_owed`, beside the settle's clock) and writes it on the tick that finds the last member gone, whoever closed it; a member at work again drops it (design §6 rule 9; `tests/test_finished_tick.py`). In memory rather than a stored field, so a home restart between the settle and the person's Close loses that one note. (1) was not a gap: a resume's record is built anew by `rpc_create`, under the name or another, and a node's superseding record is taken whole (`_take_supersession`), so neither mark reaches the resumed run — held by `test_a_resumed_manager_carries_neither_of_rule_9s_marks`.

**Related:** TD-241 and TD-240 (archived: rule 9), TD-237 and TD-238 (`closed_for`), TD-125 (the announcement).

## TD-244: Groom the agentorc attention board onto the Inbox's features

**Priority:** High
**Type:** debt
**Added:** 2026-09-30 (Paul: *set up a session to groom the inbox — we want to use the new features, e.g. recommended options, blocked by*)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved. As it was built: **the pass built 2026-09-30 (grinder-ao-2):** each of the 58 open items took one of the Fix's five: 3 closed as done already (one of them by its raiser, the manager, in #807 while the pass was open), 5 questions carry `Answers:` (four with a default; the TD-190 window question has none, a session cannot know it), 5 are `act`, 45 are `watch`; none was 14 days overdue, so none was ledgered, and none was a duplicate. No `Due:` was moved. **The archive built 2026-09-30 (grinder-ao-2, PR #811):** the 70 closed lines are in `docs/user_attention_archive.md`; its title names no TD, since §3.4 keeps that PR to the two files and the cadence check's ledger row asks a named TD for a ledger file. A line the manager added during the pass (the midnight test flake, #809) took `decide` and its `Answers:` with this update. **Left:** *Done when*'s 15: the board holds 56 open, because 45 are live looks the Fix says stay open — put to Paul as a steer (`m-4147dedf2d50`: leave them open, the default; fold them into one walk item per page; or move each into its TD as `Kind: live-check`). On the default the count stays above 15 and this entry closes by Paul's word or as the looks are done; on either other answer, whoever holds this entry does it. The live copy is built from #755, so the looks for PRs up to it can be done now. **Paul answered the steer 2026-09-30 (his reply `m-4b4ee331818d`, the second answer):** *Fold the live looks into one walk item per page and close the originals.* **The fold built 2026-09-30 (grinder-ao-2, PR #855):** seven `watch` lines at the top of *Needs the user* in the shape of the anchor's *One browser walk* line — the Org, Focus, the Inbox, the Repo page, Settings, the Transcript page and a terminal (no look started on Help, so it has none) — each look a clause with its TD and PR, `Due:` the earliest still-future date among those folded; 53 original live-look lines closed with `board_edit.py done --no-commit` as *folded into the <page> walk item of 2026-09-30*, the anchor's own walk line of 2026-09-23 among them, its clauses carried into the Org, Focus and Inbox items; the anchor's *300k default* relay line closed as done (TD-249's Fix 4, PR #852). The live copy was built from #837, so four looks are marked *after the promote* (TD-252, TD-253, TD-239 slice 4, and TD-249 slices 1–4, whose line grinder-ao-1 added in #854 while this PR was open). The board holds 12 open lines: the seven, two `act` and three `decide`, each `decide` with its `Answers:`; none is overdue 14 days. **The archive built 2026-09-30 (grinder-ao-2, PR #856):** the 62 closed lines are in `docs/user_attention_archive.md`, and the walk items say so. The steer `m-4147dedf2d50` owed no outcome by then: the host agent had settled it as `asker_gone` when the run that asked ended.
**Location:** `docs/user_attention.md` (this repo's board only), `docs/technical_debt.md` (the entries the groom ledgers), `docs/user_attention_archive.md` (§3.4's archive PR), `scripts/board_edit.py`, `scripts/ledger.py`

**Why:** on 2026-09-30 the board holds 58 open items (`BOARD_SIZE_WARN` is 15) and the SessionStart line warns of it on every start. Not one carries cadence §3.5's `Answers:`, so the Inbox draws no answer buttons and no recommended answer on any of them, and Paul must read each item whole to find what he is asked. Several are overdue 14 days or more, which cadence §3.3 says are ledgered and closed; others are *merged, live look pending* for work since promoted, or name PRs that merged or closed. The ledger was groomed onto `**Blocked by:**` on 2026-09-28 (PR #693); the board never was.

**Resolved:** 2026-09-30 (PRs #808 and #811, the pass and its archive; #855 and #856, the fold and its archive; grinder-ao-2) — every *Done when* holds: the board has 12 open items (seven walk items, one per page a look is made on, two `act`, three `decide` each with `Answers:` and at most one default), none overdue 14 days, none ledgered so none to list, each PR's body carrying one table row per item, and the closed lines archived. What lasts is the board itself and its archive; the rules the groom followed are cadence §3.3–§3.5. A new live look is still written as its own line (TD-255 slice 4 gives each the *Works* / *Not right…* pair).

**Done when:** the board has 15 or fewer open items; every open `decide` item carries `Answers:` with at most one `(default)`; none is overdue 14 days or more; every ledgered item's entry is listed by `python3 scripts/ledger.py --pickable` as blocked on `decision (Paul)` or its TD, or is `Owner: paul`; the PR body carries one table row per item the board held at the start — the line's first words, what was done (closed / answers / ledgered TD-NNN / kind and due / duplicate / answers unclear), and the evidence or the default — so Paul can check the groom from the PR alone; and, once 10 or more lines are closed, §3.4's archive PR has moved them.

**Related:** cadence §3.3–§3.5 (`Answers:` and the default: dev-cadence#TD-036, dev-cadence#TD-066), cadence §2.4 (`Blocked by:`: dev-cadence#TD-064), TD-223 and TD-228 (pickable derived), TD-218 and TD-219 (the Inbox's handed entries), PR #693 (the ledger's groom).

## TD-251: The grinder's brief says to ask the reader again with `--thread`, which the host agent refuses

**Priority:** Low
**Type:** debt
**Added:** 2026-09-30 (grinder-ao-1, met on PR #821's second ask)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/agentorc/briefs/grinder.md` (the lane paragraph: *findings, fix and ask again on the same thread (`--thread <its id>`)*), `tests/test_cli.py` or wherever the preset's words are held; `src/sessionorc/agent_mail.py` (`rpc_msg`: *--thread follows up a question put to the person: name `person` as the addressee*; a reply's root is `replied.root`); design §4.9b *The reader* (*the author fixes and re-asks on the same thread*)

**Why:** on 2026-09-30 the techlead answered the ask on PR #821 with findings. The brief's form, `ao msg --kind ask --pr 821 --thread <the ask's id> <techlead> "…"`, was refused: `--thread` takes a question to the person, and names nobody else. What put the second ask on the first one's thread was `ao msg --kind ask --pr 821 --reply-to <the findings' id> "…"`, whose root is the findings' root. A grinder that takes the refusal at its word asks the person, which closes the question at the reader and puts a held PR in front of Paul that the reader would have merged.

**Resolved:** 2026-10-01 (PR #860, grinder-ao-1) — the grinder template's clause names the form that works, *ask again as a reply to them* (`ao msg --kind ask --pr <n> --reply-to <the findings' id>`), and keeps `--thread` for the person after the bound; design §4.9b *The reader* says how the re-ask stays on the thread; `tests/test_review.py` holds the refusal of `--thread` toward a reader, the second ask's root and the seat's one `prs_waiting` entry.

**Done when:** a grinder following the brief word for word re-asks its reader without a refusal, and the seat's queue shows one entry for the PR.

**Related:** TD-093 (the reader), TD-075 (asks and threads), TD-241 (PR #821, where it was met).

## TD-260: A link socket exists at the umask's mode until the chmod after the bind, and the test reads it in that window

**Priority:** Low
**Type:** debt
**Added:** 2026-09-30 (grinder-ao-1, from a CI failure on PR #852)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (the per-node link listeners: `asyncio.start_unix_server(...)` then `os.chmod(lsock, 0o600)`; the same pair for `agent.sock`), `tests/test_link.py` (`test_a_container_node_dials_the_homes_socket_with_no_ssh_and_survives_its_restart`)

**Why:** `start_unix_server` binds the socket and is awaited before the `chmod`, so the file exists for a moment at whatever the umask gives (0755 on the CI runner). The test waits for `sock.exists` and at once asserts mode 0600; on PR #852's run 36818633814 (Python 3.13) it read `49645 & 0o777 == 0o755` and failed, and passed on a rerun with no change. A link socket's directory is 0700 before the bind, so nothing else can reach it in that window (`agent.sock`'s directory is only made, never chmod'ed, so its window is as open as the home directory is): the harm is a test that fails a run now and then and costs every PR a rerun, and a socket whose mode is not what the design says for an instant. A second hang in the same week — a docs-only PR's run 36815754454 timed out on 3.12 in a test whose name the rerun's log replaced — is not this one and is not ledgered beyond this line. A third, on this entry's own docs-only PR #854 (run 36819635729, 3.12): `tests/test_mail.py::test_the_debt_has_a_bound_of_its_own_and_is_never_pruned` raised `KeyError: 'm-…'` at line 2201 inside its `pytest.raises(AgentError, match="you owe 2 outcomes")` block — a message id looked up before it was there. Whoever takes this entry reproduces that one in a loop first and files it apart if it is real.

**Resolved:** 2026-10-01 (PR #865, grinder-ao-1) — `agent._bound` makes and binds each socket by hand under a umask of `0177`, with no `await` inside, and `start_unix_server` takes it as `sock=`; the `chmod` after the bind is gone for `agent.sock` and the link sockets alike. `tests/test_link.py` holds the mode and the umask's return; the container-node test passed 100 runs in a row. The third failure named above was real and a test's own: `test_the_debt_has_a_bound_of_its_own_and_is_never_pruned` left `MAIL_RETENTION` at zero, every tick sweeps mail, and a tick between its last `close` and the read after it pruned the settled question — the test now restores the retention first. Not reproduced in 40 runs; read from the code.

**Related:** TD-057 (step 3c, the link sockets).

## TD-261: `test_send_wait_three_outcomes` hangs to the timeout on CI now and then

**Priority:** Low
**Type:** debt
**Added:** 2026-10-01 (grinder-ao-1, from a CI failure on PR #865)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved
**Location:** `tests/test_agent.py` (`test_send_wait_three_outcomes`), `src/sessionorc/agent.py` (`rpc_send` with `wait`)

**Why:** on PR #865's run 36827137106 the Python 3.13 job hit pytest's 120 s timeout in this test, the seventh of the run; 3.12 passed the same commit and the rerun passed. The dump shows the main thread in the event loop's `select` and all four `asyncio_n` worker threads idle, so the test was awaiting something that never came — a `send --wait` whose turn never started or never settled, or the stub pane's output never arriving — and not a blocked thread. It passed 25 runs in a row on kmaster (3.13). It is likely the hang TD-260's entry mentions and could not name (run 36815754454, 3.12). The harm is a rerun now and then, and a `--wait` path that may be able to wait for ever where its own bounds (`prompt-stalled`, `timeout`) should end it.

**Resolved:** 2026-10-01 (PR #866, grinder-ao-1) — the cause is named: the test's last case forgets a record whose pane is alive, the tick (0.3 s in the fixture) adopts that pane as a shell under the same id, and when the adoption fell inside one 0.1 s poll `_wait_state` never saw the id empty and a `send --wait` with no timeout read the adopted record for ever. `rpc_send`'s wait now holds the record it typed into (`_wait_state`, `_raise_not_settled`), and an id that is another record's ends it as `removed`; design §4.2's `removed` clause says so. `tests/test_agent.py` `test_send_wait_ends_when_its_id_is_taken_by_another_record` holds it, failing on the old code. The earlier 3.12 hang (run 36815754454) left no dump, so that it was this is likely and not shown.

**Related:** TD-260 (the other two CI races of that week), TD-016 (`send --wait`).

## TD-250: Build the person's restart — the `restart` RPC, Restart on the row and the card, `ao restart`

**Priority:** Medium
**Type:** feature
**Added:** 2026-09-30 (the designer, from TD-246's design)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved — **slice 1 built 2026-10-01 (grinder-ao-1, PR #862):** `agent_wake.rpc_restart` and `_restart_check` (every refusal made before anything is touched), `restart` in `modes.HOME_EDITS`, `gitinfo.work_left` as the one clean-and-pushed test (the tick's wanted restart and `teamrun._unsafe_to_close` call it; a `git` with no `unpushed` count now reads unknown at a team's stop too), `agent_common._counted` leaving a `person` entry out; the create carries `keep_mail`, since a replay at the same id does not keep mail (design §6 rule 2 reworded); a record that is not supervised is refused as one with no launch record; a failed create leaves the record closed with its marks. Slice 4's RPC cases are in `tests/test_person_restart.py`. A node's member is written as rule 1's replay is and has no test of its own. **Slice 2 built 2026-10-01 (grinder-ao-2, PR #871; live look pending, on the board):** `POST /api/sessions/<id>/restart` over the RPC, its refusal the toast; **Restart** beside Resume on the Inbox restart row and in the card's *more ▾* (`cards.py` `restartable`: supervised, idle/exited/closed, not a seat, not superseded; `restart_marked` decides the confirm); the help entry `restart` in `ui/help.py` and §4.5a's list. Slice 4's page cases are in `tests/test_ui_inbox.py` and `tests/test_ui.py`. **Slice 3 built 2026-10-01 (grinder-ao-2, PR #873):** `ao restart` and the words, with slice 4's CLI case. **For slice 2's words, from the techlead's read of #862 (merged 2026-10-01):** `rpc_close` runs `_asker_gone`, so an idle member's own open questions to the person end with the close before its mail moves, while an exited member's survive the restart — a sentence for §6 rule 2's *the mail kept* when the row's toast is written.
**Location:** `src/sessionorc/agent.py` or `agent_wake.py` (a `rpc_restart`, person-only by the `caller` check `rpc_set_settings` makes, in `modes.HOME_EDITS`), `src/sessionorc/agent_tick.py` (`_replay` and `_refill_prompt`, the inline clean-and-pushed test of `_wanted_restart` — moved into `sessionorc` as one function that `agentorc.teamrun`'s `_unsafe_to_close` then calls, so the two cannot drift), `src/agentorc/team_skill.md` and `ui/static/app.js` (the *member back* notes that name Resume with changes…), `src/sessionorc/models.py` (`restarts`, the marks), `src/agentorc/ui/templates/inbox_row.html` (the `restart` row), `src/agentorc/ui/inbox.py` (`restart_mark`), the card's more menu (`src/agentorc/ui/templates/` and `static/app.js`), `src/agentorc/ui/app.py` (the API route), `src/agentorc/ui/help.py`, `src/agentorc/cli.py` (`ao restart`), `src/agentorc/briefs/manager.md` (the board line), `tests/`

**Why:** TD-246's *Why*: the Inbox's restart row says *yours now* and offers only Resume, which brings the member back attended; the anchor restarted grinder-ao-2 by hand with nine flags read off the old record.

**Resolved:** 2026-10-01 (PR #862 slice 1, grinder-ao-1; PR #871 slice 2 and PR #873 slice 3, grinder-ao-2) — the `restart` RPC (`agent_wake.rpc_restart`, `_restart_check`), **Restart** on the Inbox restart row and the card's *more ▾* (`cards.py` `restartable`, `POST /api/sessions/<id>/restart`), and `ao restart <session>` (`cli.cmd_restart`, printed with `cli.status_line`); the words in `briefs/manager.md`, `team_skill.md` and the exited member's banner. The lasting content is design §6 rule 2 *A person's restart*, §4.7 and §4.5a; `tests/test_person_restart.py`, `tests/test_ui_inbox.py`, `tests/test_ui.py` and `tests/test_cli.py` (`test_ao_restart_…`) hold it. The Note's question is answered in #862: the person's restart passes `keep_mail`. The live look is one board line (Restart on the page and `ao restart`, after the next promote).

**Note (the designer, 2026-09-30, from TD-247's research):** `_replay` passes neither `keep_mail` nor `resume`, and `rpc_create` moves mail only for those, so a replay by rules 1, 2 or 7 keeps no mail today; the design's *the mail kept, as a replay at the same id keeps it* (§6 rule 2 *A person's restart*) is not what the code does. The build either passes `keep_mail` on the person's restart (a person may always keep a record's mail, §4.9b) or corrects the sentence; say which in the PR.

**Related:** TD-246 (the design), TD-245 / TD-249 (why the row appears), TD-103 slice 5 (the row), TD-083, TD-186, TD-172 (Members…), TD-152 (`ao at`, the other person's act on a record's clock).

## TD-248: A written `Pickable: no` with no blocker hides an entry from every lane, and nothing refuses it — turn on dev-cadence's `ledger.py --check`

**Priority:** Medium
**Type:** debt
**Added:** 2026-09-30 (Paul: *add a TD to dev-cadence to create a validate flag for our td list … then we can add a hook here to check that we are not making invalid changes*; dev-cadence TD-073 is that entry, PR eyecantell/dev-cadence#185)
**Owner:** grinder
**Kind:** build
**Pickable:** yes
**Status:** Resolved — **step 2 built 2026-10-01 (grinder-ao-1, PR #885):** the preamble's `Fields:` line declares the `Owner` and `Kind` words, `scripts/ledger.py` reads them (`--fields` says *preamble* for both, and a value outside them is a ⚠ flag), and `tests/test_ledger.py` takes `OWNERS` and `KINDS` from the line through the script's own `declared()`, a second test holding the line readable and the preamble's paragraph to the same words. **Step 1 came with the sync of 2026-10-01 (PR #889)**, as *Resolved* below says. Step 3 is TD-228's.
**Location:** `tests/test_ledger.py` (`HEADER`, `OWNERS`, `KINDS`, `test_every_open_entry_carries_the_header_in_order_with_known_values`), `docs/technical_debt.md` (the preamble: a `Fields:` line), `scripts/ledger.py` and `scripts/check_cadence.py` (SYNCED: they arrive by sync, never edited here)

**Why:** on 2026-09-30 the anchor wrote TD-245 and TD-246 as `**Pickable:** no — design-first: …` with no `Blocked by:`. `sessionorc.ledger` reads a written *no* as blocked while TD-228's migration lasts, so neither entry matched the `design-first` lane, rule 6 told the designer nothing, and the entries waited until Paul asked why the designer was idle (PR #820 corrected the two lines). What is checked today: `tests/test_ledger.py` holds every open entry to an `Owner` and a `Kind` from its own two sets and to a Pickable line that reads `yes` or `no — <reason>`, in CI. What is not: a *no* against `Blocked by:` — a *no* whose reason is no blocker passes, which is the case above. `scripts/ledger.py` printed the disagreement as one ℹ line among 84 and exits 0; and this ledger declares no `Fields:` line (cadence §2.12), so the synced script validates none of the repo's own fields and the words live in the test alone.

**Resolved:** 2026-10-01 (PR #885 step 2; the sync PR #889 step 1; archived by PR #893) — step 1 came with the sync from dev-cadence `b90272e`: `scripts/check_cadence.py`'s `ledger` row runs `ledger.py --check --since origin/<default>` on every PR that touches the ledger (`ledger_edit_check`), so nothing here had to turn it on. Checked 2026-10-01 on main: a header edit that writes `Pickable: no` on an entry with no blocker is an error naming the entry (PR #890's first draft, seven entries, refused until each carried its `Blocked by:`), a `Kind:` outside the preamble's `Fields:` words is one too, and the ledger as it stands reads *0 error(s)*, its older lines counted as standing. Step 2 is the `Fields:` line and `tests/test_ledger.py` reading it. Step 3 is TD-228's slices 3 and 4, whose Fix already says the test's `HEADER` loses the line.

**Done when:** a PR that writes a new `Pickable: no` on an entry with no blocker, or a `Kind:` or `Owner:` outside the declared words, fails its check with the entry named; and the ledger as it stands passes.

**Related:** dev-cadence TD-073 (the flag), TD-223 and TD-228 (pickable derived, the migration), TD-118 (the three header lines), TD-198, TD-245 and TD-246 (the two entries), PR #820.

## TD-127: Inbox entries to the person are written for another agent: dense, dry, the decision buried under the reading; the row draws the whole text as one block

**Priority:** Medium
**Added:** 2026-09-24 (Paul, reading the Inbox: *they look like they were written for another agent to read — very dense, dry, text. Should we make them markdown for formatting and give instructions to make the asks more clear? … I do not need all the gritty details, just the basic context, and the decision. Maybe we add a details option on the message to show the original and have the sending agent humanize it?*; the anchor session)
**Owner:** designer
**Kind:** design-first
**Status:** **Designed 2026-09-24** in a cloud session with Paul (branch `claude/kind-cerf-dg50d4`; the designer had claimed it and dropped it on Paul's word; the board line that held it comes off in this PR). Design: §4.10 *How a message to a person is written*, §4.5a *Inbox row: details* and the mail rows, *Focus composer: placeholder*, §4.5 screen 6, §4.7 `ao msg`; mockup `Inbox.dc.html` (the ask row folded, an *Answered for you* row open) and `InboxMessage.dc.html`. The anchor's four points were confirmed, with two changes: the renderer is the UI's own closed-subset renderer, not a vendored library behind a sanitiser; and a link is drawn only when absolute, `http(s)` and off-origin, with its host shown after its text. Added: the fold's backstop at 300 characters and `ao msg`'s warning at 60 words. **Both halves built 2026-09-25**: the page (TD-138, PR #557: the renderer, the fold, the Focus panel) and the senders (TD-139, PRs #561 and #563: the four briefs' paragraph, `ao msg`'s warning, the dialog's placeholder). What is left is the live read on the board line. **As asked 2026-09-24:** The example was techlead-ao-1's reply on a held PR, filed to the person as *answered for you* (§4.9b): two hundred words beginning *merged #517: read against §4.2a (…)* — the sections the diff was measured against, the gate, a gap for a follow-up, a date discrepancy — one block, in which the only things the person needs (*merged*, *one gap left, not a blocker*) are the first word and a clause in the middle. It was written for its asker and for the record, and the person's copy is the same text. Two faults: **the text has no shape the row can use** (no first line that is the answer, no break between the answer and the reading), and **the row draws it as one run of text** (§4.5a: *the whole text as text*), so nothing folds.

**The anchor's recommendation, for the round to confirm or refine:** (1) **A shape rule, not a second text**: anything a session sends to the person — an `ask`, a `steer`, a `note`, a reply filed as *answered for you* — is written to be read cold, and its **first paragraph is the whole of what the person needs**: what it is about, what was decided or is being asked, and what (if anything) they need to do — in one to three plain sentences; a blank line; then the reading, the evidence, the sections. The presets (`techlead.md`, `manager.md`, `grinder.md`, the designer's brief) say so in the same words; a `--source` reply's first line is its verdict (*merged #517* / *findings on #517: two*). No summarising model in the path: the sender knows what it meant, and a second text is a second thing to get wrong. (2) **The row draws the first paragraph and folds the rest** under a *details* disclosure (a `<details>`, keyboard-reachable, remembered open per row while the page lives), so the original is one press away — Paul's *details option*, from the text's own shape rather than a separate field. (3) **Markdown, rendered safely**: paragraphs, emphasis, inline code, lists and links, from a sanitising renderer that emits no raw HTML, no images and no scripts, links opening in a new tab and drawn as links — never as a button; §4.5a's rule stands, *nothing on the page is built from text a session wrote except as text*, and a link is text. (4) **The shape is asked of humans too**: the composer's placeholder for a message to a session says the same thing the other way.

**Location:** design §4.5a (Inbox rows: `ask`, `steer`, `note`, *answered for you*; *the whole text as text*), §4.10 (mail kinds, what a reply carries), §4.5a (*nothing on the page is built from text a session wrote except as text*), `src/agentorc/briefs/techlead.md` (*An `ask`: only when the answer is already written down*), `manager.md`, `grinder.md`, `docs/briefs/designer-ao-1.md`, `src/agentorc/ui/templates/inbox_row.html`, `src/agentorc/ui/static/app.js` (a renderer, or a small one vendored under `static/`), `app.css`

**Why:** the Inbox's one job is to let a person act on what waits on them in a press; text written for the record makes the person do the reading a session already did.

**Resolved:** 2026-10-01 (PR #898, TD-228's migration; the builds are PRs #557, #561 and #563) — designed, and built as TD-138 (the renderer and the fold) and TD-139 (the presets, the placeholder, the warning), both archived 2026-09-25; the entry stayed open under a written `Pickable: no` that named them. The lasting content is design §4.10 *How a message to a person is written* and §4.5a *Inbox row: details*.

**Related:** TD-125 (the wind-down report as a two-line note — the same shape rule, applied once), TD-069 (the Inbox page), TD-070 (suggested answers — the press), TD-126 (the board reply), TD-093 (*answered for you* on a held PR).

## TD-158: The Message composer says when the message will be read: on call, exited, budget spent, a person's session — and a `note` to an on-call seat is not refused

**Priority:** Medium
**Added:** 2026-09-25 (raised by Paul: a note to the on-call techlead, expecting it to wake)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-09-25 (the designer): design §4.10 *When it is read: the sentence the sender sees* (the table of twelve cases in the doorbell's precedence, the one function `mail.read_when(record, kind, now)`), §4.5a **Message** (the sentence under the kind selector, changing with the kind, from the `read_when` pair on the record's view), §4.7 (`ao msg`'s reply ends with it; `read_when` under `--json`), mockup `Message.dc.html`. Settled: (a) the line as the table; (b) it changes with the kind — the seat's is the only case that differs, and the bound is added on an ask; (c) the CLI prints the same sentence per addressee, computed in the same function; *wake budget spent* is a session's sentence only, since a person's message refills the budget (§4.10 *Time and a person restore it*; `rpc_msg` refills on `sender == PERSON`); (d) the mockup. A note to a seat stays deliverable. Obvious from the entry and §4.10, so landed with a note to Paul. The build is TD-168; this entry archives with it. **What was:** nothing designed. Done the same day, beside it: the composer opens on `ask` (§4.5a **Message**, the history line of 2026-09-25), so the case that prompted this — a question typed as a note — is no longer the default.

**Location:** `src/agentorc/ui/templates/base.html` (the `#mailbox` dialog), `src/agentorc/ui/static/app.js` (`AO.compose`, which today knows only the addressee's name), `src/agentorc/ui/app.py` (`view`: `state`, `confidence`, `seat`, `seat_due`, `mail.wake_budget_spent`, `unattended` — everything the line needs is already on the card), design §4.5a **Message**, §4.9b *Seats with a trigger*, §4.10 (the doorbell, the wake budget, invariant 5).

**Why:** the composer looks the same whoever it is addressed to, and what happens next differs by the addressee's state: an `idle` session is rung within a tick; a `working` one reads it when its turn ends; an on-call seat is filled by an `ask` and not by a `note` (the trigger counts questions, `asks_waiting`); an `exited` member's mail waits for its restart or resume (`keep_mail`); a session whose wake budget is spent takes the mail without waking; a person's session is never woken by mail (invariant 5). Paul sent *Lets check PR 545 and merge it when its ready* as a note to the exited techlead and waited for it to come. Nothing on the screen said the seat comes on a question. **Refusing the note is the wrong fix**: a note to a seat is a legitimate FYI, kept and read at the next fill — the same reason `ao msg` delivers to an exited record — and a refusal would turn information into a question just to get it delivered, which §4.10 tells sessions not to do.

**What the design round has to settle:** (a) **the line** — one sentence under the kind selector, from the addressee's state, in the composer's own words: *on call — an ask fills this seat now; a note waits for its next question*; *exited — read when it is resumed or restarted*; *working — read when its turn ends*; *idle — rung within a minute*; *wake budget spent — lands, read when it next turns*; *a person's session — lands, never wakes it*; (b) **whether the line changes with the kind** as the person switches ask ↔ note (it should, for the seat); (c) **the same line on the CLI** — `ao msg`'s reply already carries a verdict; it could carry this sentence; (d) the mockup.

**Done when** a person opening Message on an on-call seat reads, before typing, that a note will not fill it and an ask will, and switching the kind changes the line.

**Resolved:** 2026-10-01 (PR #898, TD-228's migration; the build is PR #583) — designed 2026-09-25 and built as TD-168, archived the same day; the Status says *this entry archives with it*. The lasting content is design §4.10 *When it is read: the sentence the sender sees* and `mail.read_when`.

**Related:** §4.5a **Message** (the row this extends), §4.9b (the seat's trigger), §4.10 (delivery, the doorbell, the budget, invariant 5), TD-157 (the *i* marks — this line is the same idea at the moment of sending), TD-153 (what a session is told about being woken; this is what a person is told).

## TD-246: A person cannot restart a member unattended — Resume brings it back attended and there is no `ao restart`

**Priority:** Medium
**Type:** feature
**Added:** 2026-09-30 (the anchor, restarting grinder-ao-2 for Paul)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-09-30 (the designer, PR #825, stacked on #822; the steer to Paul is `m-b2288fc944f2`, bound 2026-10-01 07:29 MDT): design §6 rule 2 *A person's restart*, §4.5a **Inbox row: restart** (Restart beside Resume) and the card's **more ▾**, §4.7 `ao restart`. The Fix as written, with the round's two decisions: the press clears `restarts` as a Resume does and never counts toward the ceiling, and a manager's board line names the control instead of asking in words. Closes with TD-250.
**Blocked by:** TD-250
**Location:** design §4.5a (**Inbox row: restart**, the card's ⋯), §4.9 (the CLI), §6 rule 2; `src/agentorc/ui/templates/inbox_row.html` (the `restart` row), `src/sessionorc/agent_tick.py` (`_wanted_restart`, `_replay`), `src/agentorc/cli.py`

**Why:** the Inbox's restart row reads *the host agent will not restart it — yours now*, and offers Open, Resume, Snooze and Dismiss. Resume brings the session back **attended**, which takes a supervised member out of its team's run. `ao` has no `restart`, and `ao team start` refuses while the names are held. On 2026-09-30 the anchor did it by hand: `ao close` on the idle, clean record, then `ao new -d ~/agentorc -w grinder-ao-2 --unattended --supervised --role grinder --brief docs/briefs/grinder-ao-2.md --lane free-pick --team ao-grind --project agentorc -p grind grinder-ao-2` — nine flags read off the old record, any one of which, wrong, starts a different session (the record's `restarts` list and its stored prompt are lost either way). The manager's board line asked Paul to *say restart it*, and nothing reads that sentence.

**Resolved:** 2026-10-01 (PR #898, TD-228's migration; the design is PR #825, the builds PRs #862, #871 and #873) — designed 2026-09-30 and built as TD-250, archived 2026-10-01; the Status says *Closes with TD-250*. The lasting content is design §6 rule 2 *A person's restart*, §4.5a **Inbox row: restart** and §4.7 `ao restart`.

**Done when:** on a scratch home, a supervised member idle with an `early` `restart_wanted` is back `working`, unattended, under its manager and on its stored prompt after one press or one `ao restart`, with `restarts` carrying `why: person`; a dirty checkout is refused by name; a test covers both.

**Related:** TD-245 (why the row appears), TD-103 slice 5 (the row), TD-083, TD-186, TD-172 (Members…).

## TD-211: The briefs read a hand-written `**Pickable:**` line while dev-cadence's `ledger.py --pickable` derives the same answer from `**Blocked by:**`: two answers that can disagree

**Priority:** Medium
**Added:** 2026-09-28 (the session that groomed the ledger onto cadence §2.4 *Blocked by* and §2.11 *Type* after the dev-cadence 6cbb35f sync, PR #693)
**Owner:** anchor
**Kind:** decision
**Status:** Open — proposal, **answered 2026-09-28 by TD-223's design** (Paul's question there; design §4.4 *Repo facts*): the line retires and pickable is derived; TD-228 builds it and migrates the entries, and this entry closes with it. **Paul, 2026-10-01:** *lets use the derived pickable - the grinders can tell us if they run into issues* (TD-228 slice 3 is open for a grinder). The groom added `**Blocked by:**` and `**Type:** feature` beside the existing header lines and changed none of them.
**Blocked by:** TD-228
**Location:** `docs/technical_debt.md` (the preamble's three-header paragraph, every entry's `**Pickable:**`), `src/agentorc/briefs/grinder.md` (*Out of work*: filter on Owner/Kind/Pickable), `docs/briefs/designer-ao-1.md`, `docs/briefs/grinder-ao-2.md`, `src/sessionorc/ledger.py` and its readers (the Repo page's kind bar, TD-198; lane news, TD-195), `tests/test_ledger.py` (`HEADER`), `scripts/ledger.py` (SYNCED)

**Why:** cadence §2.4 (synced 2026-09-28) says *Pickable is derived, never written*: an entry is pickable when it has no `**Blocked by:**`, or every entry it names is archived and it names no decision, and `scripts/ledger.py --pickable` prints the pick order (debt before features within a Priority, §2.11). agentorc's own `**Pickable:** yes | no — <why>` line (TD-118) predates that and is what the live team's briefs filter on. The two now answer the same question in two places and disagree already: after the groom `--pickable` lists 80 entries (this one among them) while 13 carry `Pickable: yes`, because a hand-written *no* also says *live-check*, *Paul's*, *the anchor's* or *built, the live look is left* — reasons that are an Owner or a Kind, not a blocker. And a blocker archived tomorrow makes its dependents pickable to `ledger.py` at once, while their hand-written line still says *no — after TD-N* until someone edits it.

**Resolved:** 2026-10-01 (PR #898, TD-228 slice 3) — the line is gone from every open entry, the preamble, the `Fields:` line and `tests/test_ledger.py`'s `HEADER`; `test_no_entry_writes_a_pickable_line` holds it gone; the briefs (`src/agentorc/briefs/grinder.md`, `entry.md`, `docs/briefs/designer-ao-1.md`) pick from `ao repo` or `scripts/ledger.py --list --pickable yes --owner … --kind …` and name no written line. Paul's word of 2026-10-01 is in the Status.

**Related:** TD-118 (the three header lines), TD-198 (the kind bar's pickable bucket), TD-195 (lane news reads the header fields), dev-cadence TD-064 (the Blocked by field), cadence §2.4, §2.11.

## TD-270: Hover texts that hold more than one thing are one run-on paragraph

**Priority:** Low
**Type:** debt
**Added:** 2026-10-01 (the anchor, from Paul's walk of the Org: on the usage chip, *the mouseover text is one big paragraph which is hard to read — it should get formatted*; on a seat's Message…, *the mouseover message should be formatted better*)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** the usage chip's hover — `src/agentorc/ui/common.py` (`usage_chip`: the title joined with `. ` and ` · `, the profiles with `; `), `src/agentorc/ui/static/app.js` (`usageProfiles`; `el.title = c.title`), `base.html` (TD-122, TD-233, TD-151); the card's **Message…** `title` — `card.html`, the role's `message_line` (`cards.py`) and the help's first sentence joined with ` — ` (`help.first_sentence`, registered as `help_title` in `common.py`); and any other `title` built by joining sentences

**Why:** both hovers are right in what they say and hard to read: the chip's lists several windows, their ages and each profile in one wrapped block, and Message… on a seat's card runs the role's *who for what* line and the control's help sentence together as *for … — …*. A `title` shows line breaks, so a list can be a list.

**Resolved:** 2026-10-02 (PR #909) — the usage chip's hover (`common.usage_chip`, `AO.usageChip`), Message…'s title (`card.html`, `focus.html`) and the rollup's PR-errors hover put one item per line; design §4.5a's usage chip and Message rows say so, and `tests/test_ui_org.py`, `tests/test_ui.py`, `tests/test_ui_team_summary.py` hold the titles.

**Done when:** the usage chip's hover and a seat's Message… hover read as short lines, and the PR lists every title it changed.

**Related:** TD-122, TD-233, TD-151 (the chip), TD-162 / TD-171 (the role's line) and TD-097 (the seat's Message…), TD-167 (the *i* panels, where longer help already lives).

## TD-267: Build the closed card: Forget first in its foot, a menu of what applies, and the hover that says when the record goes

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-01 (the designer, TD-266's build)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/agentorc/ui/cards.py` (`next_act`; the `closed` branch of the slot: `full`), `src/agentorc/ui/templates/card.html` (the foot's `forget` branch; the `nopane` variable; the *more ⋯* menu), `src/agentorc/ui/static/app.js` (the exited / closed banner — Resume, New session here, Forget — is the script's, about lines 2745–2763; `focus.html` holds only the empty `#fexited`), `src/agentorc/ui/help.py` (the `forget` entry's *where* already reads *a card's foot*), `docs/mockups/gen.py` (the closed card's foot: already Forget · Details, regenerated with the design), `tests/test_ui_org.py`

**Why:** design §4.5 *The card's anatomy* row 6 and §4.5a **Forget** / **Details** / **more ▾** (TD-266): a closed session in no team sat on the Org with Details as its lead and a menu of Wrap up, Kill, Close, Switch to unattended, Open shell here, Pop out and Copy tmux command — every one acting on a process or a pane that was gone — and no Forget; Paul looked for the way to remove it and did not find it. The `remove` RPC accepts a `closed` record already (`rpc_remove`: `exited` or `closed`); `ao forget` and the Details page's banner reach it; the card did not.

**Resolved:** 2026-10-02 (PR #912) — built as designed: `cards.next_act` leads a closed card with Forget, `card.html`'s `nopane` menu draws Message…, Restart and Forget (not on a seat), and `cards.closed_keep` gives the hover's and the Details banner's *forgotten by itself a day after the close* from `CLOSED_KEEP`. The lasting content is design §4.5 row 6 and §4.5a **more ▾** / **Details** / **Forget**; `tests/test_ui_org.py` holds it.

**Done when:** a closed session in no team is removed from its card on the Org with one press of Forget, its *more ⋯* holds nothing that acts on a gone process, and its hover says the record goes by itself a day after the close; the tests above pass.

**Related:** TD-266 (the design), TD-095 (the card's anatomy, the foot's rule), TD-262 / TD-265 (the closer's words in the same slot — the hover's tail follows them), TD-156 (Forget all on a team's header), TD-250 (Restart in the menu), TD-077 (Forget refused on a suspended record).

## TD-272: Focus's *Ready to close* card has a button labelled **Close** that reads as closing the card

**Priority:** Low
**Type:** debt
**Added:** 2026-10-02 (the anchor, from Paul's walk of Focus: *the "close" button looks like it is for closing the "ready to close" card, maybe relabel it "close session"?*)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/agentorc/ui/templates/focus.html` (the side panel's *Ready to close* card: its summary's button `#closebtn`, labelled **Close**; the header's own is already **Close session**), design §4.5a's Focus rows for **Close session** (TD-156), `src/agentorc/ui/help.py` if the label is bound there

**Why:** the button sits on the card's summary line, at its right, where a panel's own close or fold control usually is; it ends the session. The header's button for the same act already reads **Close session**.

**Resolved:** 2026-10-02 (PR #913) — `focus.html`'s `#closebtn` reads **Close session** with `help_title('close')`; design §4.5a's Focus side panel row names it so; `tests/test_ui_org.py` holds the label.

**Done when:** both buttons for the act carry the same words.

**Related:** TD-156 (the end of a session on Focus), TD-095 (*Close session* on a card).

## TD-273: A drag in the Focus terminal copies on release and says nothing

**Priority:** Low
**Type:** debt
**Added:** 2026-10-02 (the anchor, from Paul's walk of Focus, TD-174's live look: *we should probably give some sort of indicator when text is copied*)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/agentorc/ui/static/app.js` (the terminal's selection handling: the copy on release catches its failure and is otherwise silent — *silent, as the copy is*; `copySel`, the menu's Copy, toasts *copied*), design §4.5a's Focus terminal row (TD-174)

**Why:** a plain drag selects and the release copies, which works; nothing says it happened, so the first time a person cannot tell a copy from a selection, and a blocked clipboard is silent too.

**Resolved:** 2026-10-02 (PR #915) — the copy on release goes through `copySel`, Copy's own path, which toasts *copied* or *clipboard blocked*; design §4.5a's **copy on select** row says so; `tests/test_ui.py::test_a_copy_on_release_says_so_with_copys_own_toast` holds it.

**Done when:** a drag-and-release in the terminal shows that the text was copied, and a refused copy says so.

**Related:** TD-174 (selection and copy in the terminal), TD-046 (Pop out, the same terminal).

## TD-275: A test fails when `tests/test_ui.py` runs before `tests/test_ui_org.py` alone: `uiconf._read` is a module cache a test leaves set

**Priority:** Low
**Type:** debt
**Added:** 2026-10-02 (grinder-ao-2, met while building TD-270)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/agentorc/ui/uiconf.py` (`_read`, the person's settings as last read, module-level), `tests/test_ui.py` (whichever test leaves `_read["person"]` holding an `open_in` that draws no editor link), `tests/test_ui_org.py` (`test_members_is_on_an_org_defined_team_and_a_note_on_a_repo_defined_one`: asserts **Open file**, drawn only through `editor_link`)

**Why:** `pytest tests/test_ui.py tests/test_ui_org.py` fails that one test on main as on a branch; alone, or in `pdm run test`'s order, it passes. A test whose result depends on which file ran first is one a future reorder or a `-k` run turns red for nothing, and the next session spends its time proving the failure is not its own (TD-270's did).

**Resolved:** 2026-10-02 (PR #916) — the culprit was `test_ui.py::test_the_page_reads_person_through_the_agents_settings_read`, which wrote `open_in: none` and a `ui.yml` into the module agent's home; it now takes them back, and `tests/conftest.py`'s autouse `_no_persons_settings_carried_over` starts every test with nothing read.

## TD-263: Build the pull: the home's tick fast-forwards every registered main checkout when git allows and the anchor session is idle

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-01 (the designer, TD-222's build; decided by Paul 2026-10-01)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/sessionorc/promote.py` (`survey`: the pass over the registry's roots, its `read_main` fetch), `src/sessionorc/agent.py` (the home's promote tick, `promotes` on `host`), `src/sessionorc/board.py` (`ready`'s busy list, to share), `src/sessionorc/settings.py` (`parse_repo`: `pull`), `src/agentorc/ui/` (the Inbox's origin note; the Settings page's Repos card), design §6 *Pull*, §4.5 screen 6, §4.5a *origin note* and *Settings page: Repos*, §5

**Why:** design §6 *Pull* (TD-222): on 2026-10-01 the anchor's `main` stood nineteen commits behind origin with every board row read from origin disabled, and the promote's precondition (1) fails on a checkout no one has pulled. Paul: *lets have it do when git allows and the anchor session is idle*.

**Resolved:** 2026-10-02 (PR #917, PR #918) — the pass, its reading and the switch in `src/sessionorc/promote.py` (`pull`, `pulls`), `HostAgent.pull_occupant` and `repos.<repo>.pull` (#917); the origin note's tail (`inbox.pull_tail`) and the Settings page's Repos card (`settings_page.pull_reading`) (#918). Design §6 *Pull* carries the rule; `tests/test_pull.py`, `tests/test_ui_board.py` and `tests/test_ui_settings.py` hold it. A live look after the next promote is a `watch` line on `docs/user_attention.md` (PR #920).

**Done when:** on a registered checkout left on `main` and behind origin with no session in its root, the home's next full pass fast-forwards it and the Repos card reads *last pulled … · n commits*; with a `working` session in the root the card reads *waiting: <name> is mid-turn* and the checkout does not move; a checkout with its own commit is left alone and says so; and the tests above pass.

**Related:** TD-222 (the design), TD-264 (the write-back on origin's head, which leaves the checkout only ever behind), TD-208 and TD-221 (the origin note), TD-132 (the promote's pass this joins).

## TD-285: A container test fails on CI now and then: the reach it reads says `root`

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-02 (the anchor, at the promote of 7cca6af: main's CI failed on this test alone, and passed on a rerun of the failed job)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `tests/test_containers.py` (`test_the_home_derives_a_container_nodes_reach_when_it_dials_in`, the `container_home` and `agent` fixtures, `Fake`), `src/sessionorc/agent*.py` (`_note_reach`, and the tick that may call it too)

**Why:** `assert reach["container"] == "abc123def456" and reach["user"] == "developer"` failed with `'root' == 'developer'` on main's run of 7cca6af (#930, which touched no container code), and the same test failed on two other recent runs; reruns pass. A red main stops the promote policy (*not now: checks on main are failed*) and a hand promote presses through it, so a flake costs either a stall or a promote over a red check. `root` is the code's fallback, not the `Fake`'s answer (its `user` is `developer`): `src/sessionorc/containers.py` takes the user from the node's devcontainer definition's `remoteUser`, else `root`, and `root` too when the read raises (`_remote_user`). The likely cause, unverified: that read sometimes finds no `remoteUser` or fails — the `agent` fixture's own tick racing the test's `_note_reach`, or the definition read before the fixture has written it.

**Fix:** reproduce under load (`pytest -p no:randomly --count 50` or a loop), confirm the race, and make the test own the reach (the definition written, and the tick kept out, before `_note_reach`), rather than retrying the assertion; if the fallback itself is the fault, the code says so.

**Done when:** the test passes fifty runs in a row locally and is not seen failing on CI for a week.

**Related:** TD-057 (the home and node split, where the reach comes from), TD-132 (the promote's check on main).

**Resolved:** 2026-10-02 (PR #941) — reproduced: `_remote_user` reads the node's own generated definition (`nodes/cm/.devcontainer/devcontainer.json`), which the `agent` fixture's supervisor writes during its provision; read before that write landed, it raised and the user fell back to `root`. Deleting the file before the read gives the CI assertion exactly, and a 1 s delay on the write fails the old test and passes the new. The test now waits until the supervisor has stood down and the definition exists. No product change: a node that dials in has been provisioned.

## TD-289: A seat test fails on CI now and then: a second ask expires beside the one expected

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-02 (the anchor, at PR #937's CI: attempt 2 failed on this test alone, attempt 1 and 3 on TD-285's; it also failed run 37090936027 on a branch)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `tests/test_seat_closed.py` (`test_a_question_to_a_closed_seat_survives_a_refused_fill_and_is_there_at_the_fill`, the assertion `mail["expired"] == [lost["id"]]`), the home's expiry of asks to closed records

**Why:** `assert mail["expired"] == [lost["id"]], "a closed record that is no seat expires its ask, as ever"` failed with a second id in the list (`['m-07d2f728a44c', 'm-4fe2fb1334fa']`): an ask the test expects to survive, the seat's, expired too, or a tick ran an expiry the test did not plan. It passes locally and on reruns, so it is timing: a red check stops the promote policy and makes every PR's author rerun CI.

**Fix:** reproduce under a slowed tick or a loop, find which ask the second id is and what expired it, and make the test own the timing (or fix the expiry if a seat's ask can expire). As TD-285 was (#941): show the cause before the fix.

**Done when:** the cause is shown, and the test passes fifty runs in a row.

**Related:** TD-285 (the container test's race, the same shape), TD-097 (a seat), TD-259 (the manager on call).

**Resolved:** 2026-10-02 (PR #943) — reproduced: the test's `short` ask carries `bound=1`, a second of wall clock from its send, and the first sweep's `now` was read only after two closes; on a slow runner they outlast the second, so that sweep expired `short` beside `lost` (a 1.2 s sleep before `now` fails it exactly as CI did, the extra id being `short`'s). The test now takes `now` from the short ask's own `at`; with the sleep left in it passes, and without it fifty runs in a row. No product change: a seat's ask did not expire wrongly.

## TD-294: Build the New session form's reads on the picked host

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-03 (the designer, TD-293's build)
**Owner:** grinder
**Kind:** build
**Status:** Built — slices 1 (#957), 2 (#958) and 3 (#959).
**Location:** `src/sessionorc/agent_link.py` (the node's link methods beside `stat`, `repos`, `files`), `src/sessionorc/agent_remote.py` (`rpc_host_dir`, `rpc_host_repos`, `rpc_host_files`: the two new reads beside them), `src/agentorc/ui/app.py` (`new_form`, `_roles_for`, `new_submit`, `/api/dir_check`, `/api/worktrees`, `/api/occupancy`, `/api/roles`, `/api/team_review`), `src/agentorc/ui/static/app.js` (`AO.newSession`: `away()`), `src/agentorc/teamrun.py` (how a team start reads a node's config and briefs: the model); design §4.4a *The New session form on another host*, §4.5a New session **the form** (*Another host*)

**Why:** design §4.4a *The New session form on another host* (TD-293): a pick of another host is right today only where both hosts hold the same repos at the same paths; elsewhere the form offers repos the node lacks and starts a session whose brief and ledger are this host's.

**Resolved:** 2026-10-03 (PR #959) — slice 1 the reads (#957), slice 2 the page (#958), slice 3 Start (#959): the role, its brief, the ledger and the team's reader resolved from the picked host's files. Lasting content: design §4.4a *The New session form on another host*, §4.5a New session **the form** (*Another host*); `tests/test_ui_new_host.py`, `tests/test_link.py`.

**Done when:** with a node holding a repo this host does not, picking the node lists that repo, its worktrees and its roles, an occupied directory there is said before Start, and a role started there carries the node's brief and ledger.

**Related:** TD-293 (the design), TD-284 (the form), TD-057 (the link), TD-229 (`host_repos`).

## TD-266: A closed session's card does not say how it is removed — Forget is behind Details, and its menu offers what no longer applies

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-01 (the anchor, for Paul, with a screenshot of the Org: *there is a closed session in the No team section that I do not see how to remove — is this an oversight?*)
**Owner:** designer
**Kind:** design-first
**Blocked by:** TD-267
**Status:** **Designed 2026-10-01** (the designer, PR #903; the placement steered to Paul as `m-1918b64cb2ce`; Paul, 2026-10-02, through the anchor: *go with the default*). Design: §4.5 *The card's anatomy* row 6 (`closed` → **Forget**, Details second, as `exited`; *the menu draws what applies to the state*: on a closed card or one whose pane is gone, Message… · Restart · Forget, the seven that act on a process or a pane left out, not dimmed; the slot's hover and the Details banner say a closed record is forgotten by itself a day after the close; Resume stays on the banner), §4.5a (a **Forget** row for the card's foot, the **Details** row, the **more ▾** row's closed-card shape), the Org mockup's closed card (`gen.py`: Forget · Details), design-history §4.5 and §4.5a. Settled: (1) Forget is the foot's lead, not a menu item alone — the same button an exited card leads with, with no confirm (it stops nothing, and the record goes by itself in a day); (2) the inapplicable controls are left out, since dimmed is for a control that applies and is refused, and the pill already says the process is gone; (3) the hover carries the day, no new note on the card; Resume is not added to the card. The build is TD-267.
**Location:** `src/agentorc/ui/cards.py` (`next_act`: `forget` for `exited` only; `closed` or a gone pane → `details`), `src/agentorc/ui/templates/card.html` (the foot; the *more ⋯* menu, drawn the same for every state but `scheduled`), design §4.5 *The card's anatomy* row 6 and §4.5a's **more ▾** row and the Details row (where a closed record's Forget is), `src/sessionorc/agent_tick.py` (the reap after `CLOSED_KEEP`, one day)

**Why:** `error_examine`, a person's own interactive session in samscrape, was closed and sat under *No team* reading *closed by you*. Its foot is **Details** and VS Code. Its *more ⋯* menu is Message…, Switch to unattended, Wrap up, Kill, Close, Open shell here, Pop out and Copy tmux command: (and Restart where the record is restartable): three that act on a process that is gone, one that copies a command for a tmux session that no longer exists, and no **Forget**. An `exited` card leads with Forget; a closed one keeps it on the Details page's banner (§4.5a's Details row: *Resume / New session here / Forget*), two presses away with nothing on the card pointing there — Paul looked for it and did not find it — beside `ao forget <id>` and the tick's reap a day after the close (`CLOSED_KEEP`), which the card does not mention either. A team's closed members have **Forget all** on the team's header; a closed session in no team has nothing.

**Resolved:** 2026-10-03 (PR #903 the design, PR #912 the build) — designed 2026-10-01 and built by TD-267 (archived): Forget leads a closed card's foot, the menu draws what applies to the state, the hover and the Details banner say when the record goes. The lasting content is design §4.5 *The card's anatomy* row 6 and §4.5a **more ▾** / **Details**.

**Done when:** a person removes a closed session from its card on the Org, and a closed card's menu holds no control that does nothing.

**Related:** TD-095 (the card's anatomy), TD-156 (the end of a session — Paul's UI review, which covers Focus after Wrap up), TD-262 / TD-265 (who closed it, the same slot), TD-097 (a seat's card).

## TD-293: New session on another host reads this host's disk for its repos, roles and a role's resolution, and checks nothing of the node's

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-03 (grinder-ao-2, TD-284 slice 6; the review of #949)
**Owner:** designer
**Kind:** design-first
**Blocked by:** TD-294
**Status:** **Designed 2026-10-03** (the designer, PR #954; obvious from §4.4a *Teams across hosts* — a team start on another host already reads the registry, a directory and the repo's files there — so landed with a note to Paul). Design: §4.4a *The New session form on another host*, §4.5a New session **the form** (*Another host*). Of the Fix's two ways, the node returns the files and the home parses them: `host_files` and `repoconfig.load_text`, the team start's loader, so nothing new crosses the link for roles; the two new reads are `host_occupancy` and `host_worktrees`, and `stat` gains `root`. **Next:** designed; the build is TD-294.
**Location:** `src/agentorc/ui/static/app.js` (`AO.newSession`: `away()` in `dirCheck`, `loadWorktrees`, `check`), `src/agentorc/ui/app.py` (`new_form`: `form_repos(hosts.local_host().repos())`, `_roles_for`; `new_submit`: `repoconfig.discover`, `resolve_role`, `team_reader`; `/api/dir_check`, `/api/worktrees`, `/api/occupancy`, `/api/roles`, `/api/team_review`), `src/sessionorc/agent_common.py` (`NODE_READS`), `src/sessionorc/agent_link.py` (`_read`), `src/sessionorc/agent_remote.py` (`_act_host`, `_route_read`), design §4.5a New session **the form** (*Another host*), §4.4a

**Why:** the Host pick (TD-284 slice 3) sends the create and the name check to the picked host, but the rest comes from this host or is not drawn: the Repo list is this host's registry, and the Role list and the role a Start resolves come from this disk's `.agentorc.yml` — its brief, its ledger and the team's reader with it; *another directory…*'s check, the occupancy and the Where chips are skipped (`app.js`'s `away()`), so the person learns of a missing directory or an occupied checkout only from the node's refusal at Start. A pick of another host is right only where both hosts hold the same repos at the same paths; elsewhere the form offers repos the node lacks and starts a session whose brief and ledger are this host's.

**Resolved:** 2026-10-03 (PR #954 the design, PR #957, #958 and #959 the build) — designed 2026-10-03 and built by TD-294 (archived): the New session form reads the picked host for every reading about a place, and Start resolves the role from that host's files. The lasting content is design §4.4a *The New session form on another host* and §4.5a New session **the form** (*Another host*).

**Done when:** with a node holding a repo this host does not, picking the node lists that repo, its worktrees and its roles, and a role started there carries the node's brief and ledger.

**Related:** TD-284 (the form), TD-057 (the link and routed reads), TD-145 (a worktree record's Resume with changes…).

## TD-295: The context reading's window is guessed from a model table; read the one Claude Code reports

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-03 (the anchor, on Paul's question whether the open check *does Focus's 231k of 1M match /context?* could run by itself: it can be made unnecessary)
**Owner:** grinder
**Kind:** build
**Status:** Built — PR #967.
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`CONTEXT_WINDOWS`, whose comment says *not verified: whether Claude Code runs any of these at a smaller window by default*; `ClaudeCode.context()`, which reads tokens from the transcript and the window from that table; `usage_report()`, which reads the status line's stdin), `src/agentorc/adapters/claude_code/hook.py` (`statusline()`), design §4.3 `context`, TD-190

**Why:** the card's and Focus's context reading (*231k of 1M*) takes its tokens from the transcript's last turn and its window from `CONTEXT_WINDOWS`, a table of model-id prefixes that gives 1M to every current model but Haiku. Claude Code's documentation mostly agrees: *Model configuration* gives Fable 5 and 5.1, Sonnet 5 and later, and Opus 4.7 and later a native 1M window, and Opus 4.6 and Sonnet 4.6 reach 1M only through the `[1m]` model suffix. So the table's two 4.6 rows overstate the window unless that suffix is chosen, and `message.model` does not show the suffix. *Customize your status line* describes the window field as *200000 by default, or 1000000 for models with extended context*. Rather than keep a table in step with the tool, read the truth from the status line JSON: `context_window.context_window_size`, `context_window.used_percentage`, `context_window.current_usage` and `exceeds_200k_tokens`. agentorc already runs a status line on every launch (`agentorc-hook --statusline`) and reads `rate_limits` from the same payload, so the real window is in hand and unused. The board's decide line (*Focus matches /context | Focus does not match /context*) was asking a person to catch the guess.

**Fix:** `usage_report()` (or a sibling) also keeps `context_window.context_window_size` and the tokens it reports, written where `context()` can read it for that session; `context()` prefers that window, and its tokens when fresher than the transcript's, and falls back to the table only when no status line has reported. The bound rule (§6 rule 5) then reads the window the tool runs, not a guess. Fixtures from the documented payload; a test that a reported 200k beats the table's 1M.

**Resolved:** 2026-10-03 (PR #967) — `agentorc-hook --statusline` keeps the window and tokens Claude Code reports per tool session; `context()` takes that window, and the tokens when later than the turn, the model table only as the fallback (its 4.6 rows now 200k). Tests pin a reported 200k over the table's 1M. The board's *Focus matches /context* line was closed into this entry by #966. The lasting content is design §4.3 `context`.

**Done when:** a session's reading names the window its status line reported, a test pins it, and the board's *Focus matches /context* line is closed as answered by this entry.

**Related:** TD-190 (the context bound), TD-122 / TD-233 (the status line's usage reading, the same payload).

## TD-291: Build the builder's half of a UI change's verification

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-03 (the designer, TD-290's build)
**Owner:** grinder
**Kind:** build
**Status:** Slice 1 built — PR #970 (`scripts/look_home.py`: the scratch home, its fixtures and its refusals; `tests/test_look_home.py`). Slice 2 built — PR #972 (the grinder preset's ban; this repo's grinder brief's paragraph *A change to a page is verified by you*; the designer's mockup shots). Slice 3 built — PR #973 (the techlead preset's *A screenshot ask*; the primer's pointer). Slice 4 — PR #975 (TD-292 slice 1), the first page change through it: its UI check made on `scripts/look_home.py` with Playwright, its screenshot committed, no board line.
**Location:** `scripts/look_home.py` (new, agentorc's own: the scratch home), `tests/conftest.py` (how the suite isolates a host agent: a temp `AGENTORC_HOME` and a `Tmux` on a private socket handed to `HostAgent`), `src/agentorc/briefs/grinder.md` and `src/agentorc/briefs/techlead.md`, `docs/briefs/grinder-ao-*.md`, `docs/briefs/designer-ao-1.md`, `docs/briefs/techlead-context.md`; design §4.9b *A UI change is verified by its builder*

**Why:** design §4.9b (TD-290; Paul, 2026-10-03: *I would expect a grinder who makes ui changes to verify them itself before sending to a reviewer, and only send to a reviewer if needed*). A grinder may not run `ao ui` today, so every merged change to a page reaches Paul unchecked.

**Fix:** build it as written, in slices, each its own PR. (1) **The scratch home**: `scripts/look_home.py` stands up a host agent and a UI from the worktree it is run in, on an `AGENTORC_HOME` of its own under a short path (a unix socket's path limit), a tmux server of its own and a free port — started in-process with a `Tmux` on a private socket handed to `HostAgent`, as `tests/conftest.py`'s fixture does, since `agentorc-agent serve` builds a bare `Tmux()` on the default server — with fixture sessions under the `shell` adapter and a fixture board; it prints the URL, tears everything down on exit and on a signal, and refuses to run against `~/.agentorc` or the default tmux server; a test starts it, loads the Org page and stops it. (2) **The builder's briefs** (touches `docs/briefs/**`: the reader's read): the grinder preset and this repo's grinder briefs change their ban on `agentorc-agent serve` and `ao ui` (`src/agentorc/briefs/grinder.md`: *no `agentorc-agent serve`, `ao ui`, `ao service`*; the repo briefs' *Never run `agentorc-agent serve`, `ao ui` or `ao service`*) to *never run the host agent or `ao ui` against the live home; `scripts/look_home.py` is the one way* — `ao service` and the units stay banned outright, and say the rest of the rule — a PR that changes anything under `src/agentorc/ui/` carries a **UI check** in its body (one line per check: what the change says, what was read) and its screenshots as `docs/mockups/reviews/<date>-td<n>-<what>.png`, cropped, at most four; what a browser cannot settle is an `ask` to `{techlead}` naming the screenshots and the design section, before the merge; what only the live copy shows is a `watch` line the next member makes read-only once `ao promote status` shows the commit live; and the three cases of what is left (no line; a `look` with `Works (default)` naming the techlead's reply and a `Due:` no sooner than the next day; a `look` with the bare pair), written as a `watch` with the pair until TD-292's last slice says the kind is here; where Playwright is on this machine (`~/ao-shots/pwlib`) is this repo's brief's to say. The designer's brief gains the same rule for a mockup it shows. (3) **The techlead's half** (touches `docs/briefs/**`): the techlead preset and the primer say how a screenshot `ask` is answered — read the images against §4.5, §4.5a and the mockup; *matches* or *does not match: <what>* with `--source`, or *not written* with a lean; never passed up as mail (§4.9b's second carve-out from *What it may answer*, and §4.8's preset row). (4) **The first one**: the next merged change to a page goes through it, and what the builder could not do from its brief is fixed there.

**Resolved:** 2026-10-03 (PRs #970, #972, #973, #975) — the scratch home, the builder's and the designer's briefs, the techlead's *A screenshot ask*, and the first page PR carrying a UI check with no board line. The lasting content is design §4.9b *A UI change is verified by its builder*, the grinder preset's ban, this repo's grinder brief and the techlead preset.

**Done when:** a grinder's PR that changes a page carries a UI check made on a scratch home with its screenshots, and no board line is written for a change whose checks all held.

**Related:** TD-290 (the design), TD-292 (the Inbox's half), TD-255 (a live look's answers), cadence §3.5.

## TD-298: A scratch `AGENTORC_HOME` still reads this machine's repo registry and default tmux server

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-03 (the anchor: a look agent's scratch host agent fast-forwarded `/home/kmaster/agentorc`)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/sessionorc/hosts.py` (`repos_registry`'s default), `src/sessionorc/agent*.py` (`serve`, the bare `Tmux()`), `src/sessionorc/tmux.py`

**Why:** on 2026-10-03 a look agent started `agentorc-agent serve` with `AGENTORC_HOME=~/.cache/aolkE`, a private `AGENTORC_TMUX_SOCKET` and an empty `CLAUDE_CONFIG_DIR`. Its registry still filled with the four real checkouts, since `repos_registry` defaults outside the home, and four seconds after it started (20:33:00 MDT) its pull policy fast-forwarded the real agentorc checkout `05634ce7 → 6cf8e701` — which the live agent was holding *until agentorc-18 is idle*; the scratch agent could not see that session with its empty Claude config. The move was an ff-only merge on a clean checkout and harmed nothing, but a scratch home must not act on real repos. `AGENTORC_TMUX_SOCKET` is also not read by `serve`: its `Tmux()` takes the default server, so only `TMUX_TMPDIR` kept tmux apart. Three other look agents isolated themselves by hand (their own `hosts.yml` with `repos_registry`, a `HostAgent(tmux=Tmux(socket_name=…))`), as TD-291's `scripts/look_home.py` (#970) does.

**Resolved:** 2026-10-03 (PR #974) — `serve` drives the tmux server `AGENTORC_TMUX_SOCKET` names, and an unset `repos_registry` reads `repos.txt` under any home but `~/.agentorc` (`hosts.default_repos_registry`). Lasting content: design §4.4 (*Its own tmux server and its own repos*), §5 (`repos_registry`); `tests/test_hosts.py`.

**Done when:** a host agent on a non-default home with no registry of its own lists no repo and touches no tmux server but its own.

**Related:** TD-291 (`look_home.py`, which isolates by hand), TD-263 (the pull), TD-296.

## TD-301: A container node's sessions fire no hooks: `agentorc-hook` is not on the node agent's PATH

**Priority:** High
**Type:** debt
**Added:** 2026-10-03 (the anchor, at cm-grind's first start on the contractmatch node, TD-299)
**Owner:** anchor
**Kind:** build
**Status:** Built — this PR; live once promoted and the node rebuilt.
**Location:** `src/agentorc/adapters/claude_code/__init__.py` (`write_hooks_file`, `hook_command`)

**Why:** every hook in the three cm-grind sessions on the node failed with `/bin/sh: 1: agentorc-hook: not found`. The node's host agent runs from `/agentorc/venv` with `/agentorc/venv/bin` off its PATH, so `shutil.which("agentorc-hook")` found nothing and the layer wrote the bare name. The agent's log said so (*agentorc-hook not on PATH … hooks for profile grind may never fire*). With no hooks, a node session's state is scraped only: no hook-confirmed idle, no status line, no context or usage reading.

**Fix:** `hook_command()` takes `agentorc-hook` from PATH, else the one beside `sys.executable` (a venv's console scripts sit next to its python), and the bare name only when neither exists, with the warning as before.

**Resolved:** 2026-10-03 — `hook_command()` with its fallback, tested in `test_the_hook_command_falls_back_to_the_one_beside_the_interpreter`; the node takes it at the next promote and `ao host rebuild contractmatch`.

## TD-268: Members… → Add member writes a duplicate name: nothing refuses it, nothing starts, and Remove would wind down the live member

**Priority:** High
**Type:** debt
**Added:** 2026-10-01 (the anchor, from Paul's walk of the Org: TD-172's live look, *this does not appear to be working correctly … note name similarity and not shown on team card*)
**Owner:** grinder
**Kind:** build
**Status:** Built — PR #907 (the free-name default, the refusal, `ao org check`'s lack, Remove of a duplicate keeping the session); merged, live look pending on `docs/user_attention.md`. The duplicate line in the home's `org.yml` is still the person's to take out.
**Location:** `src/agentorc/ui/static/app.js` (`openMembers`: `fill` sets the name box to the **existing** member's name for the picked role), `src/agentorc/org.py` (`edit_members`: appends `- {role, name}` with no check against the team's names), `src/agentorc/teamrun.py` (`add_member`: `new` is the names not there before, so a duplicate makes it empty and nothing is created, with no error; `remove_member`: winds down the live session holding the removed entry's name), `tests/` (the members tests)

**Why:** Paul pressed **Members…** on dc-grind while it ran and **Add member** with the form as it opened: role `grinder`, name `grinder-dc-1` — the name of the grinder already there, which is what the form prefills. `org.yml` gained `- {role: grinder, name: grinder-dc-1}` (the home's commit `d3ac743`, *org: dc-grind added grinder-dc-1 (grinder)*), no session started, no card appeared, and the toast said nothing was wrong. The dialog then listed two rows, *grinder · grinder-dc-1 · free-pick · 1* and *grinder · grinder-dc-1 · — · 1*, each showing the one live session as its holder and each with **Remove** — and Remove on either sends that live grinder the wrap-up, since the removed entry's name is its name. The team's definition now names one session twice, which the next Start has to make something of.

**Fix:** (1) **The form offers the next free name**: for the picked role, the team's pattern with the first number no member holds (`grinder-dc-2`), never an existing name. (2) **A duplicate is refused before anything is written**: `edit_members` (or `add_member` ahead of it) refuses a name the team's definition already holds, in the page's words — *dc-grind already has grinder-dc-1* — and the file and the home's history are untouched. (3) **A definition that already holds a duplicate** is said by `ao org check` (a new check: it has none for a member's name today) and on the Members dialog, and Remove on a duplicated entry removes the line without winding down a session another entry still names. (4) Tests: the prefill, the refusal leaving the file byte-identical, Remove on a duplicate. The duplicate line in this home's `org.yml` is Paul's or the anchor's to take out by hand (a revert of `d3ac743`); the anchor's attempt on 2026-10-01 was refused by its permissions.

**Done when:** Add member with the form as it opens on a running team starts a new, differently named member whose card appears, and a typed duplicate is refused with the file unchanged.

**Related:** TD-172 (Members…, whose live look this was), TD-229 (a repo-defined team's Members… is disabled), TD-210 (the home's definition history).

**Resolved:** 2026-10-03 — built; its look (board line of 2026-10-02) closed 2026-10-03: not checkable as written, since every team is repo-defined since TD-229's switch (Members… disabled, TD-210); the Add member check on an org.yml-defined scratch team is in TD-297.

## TD-278: The Inbox reads the boards with a stale copy of the reader, so no board row draws its answers

**Priority:** High
**Type:** debt
**Added:** 2026-10-02 (the anchor, from Paul's walk of the Inbox, TD-255's live look: *It looks like the Answers are not being parsed correctly (do we need a more strict format?)* — the row ended `Answers: Works | Not right: <what>.` and drew Reply, Snooze and Done only)
**Owner:** grinder
**Kind:** build
**Status:** Built — PR #929 (`board_reader` picks dev-cadence's source, else the copy synced last; `reader_lacks` names a copy without `answers` in the board note); merged, live look pending on `docs/user_attention.md`.
**Location:** `src/agentorc/ui/inbox.py` (`BOARD_SCRIPT` and the read: *the script from the first of them that carries it (a SYNCED file: every copy is the same)*), `~/.agentorc/repos.json` (the order: dev-cadence, samscrape, contractmatch, pneuma-ops, agentorc, …), design §4.4 / §4.5 screen 6 (the board's reader is dev-cadence's own)

**Why:** the format is not the fault: this repo's reader (`scripts/nudge_user_attention.py --report --json`) gives that line `"answers": ["Works", "Not right: <what>"]`. The page does not run this repo's copy. It runs the first registered repo's that has one: dev-cadence keeps its scripts under `files/`, so the first is samscrape's, last synced 2026-09-17, before dev-cadence TD-036 (2026-09-22, its #110) gave the reader `Answers:`. That copy emits no `answers`, so every row on every board draws without them, and nothing says the copy is old. *Every copy is the same* holds only while every consumer is synced at once, which it is not.

**Fix:** pick the reader by what it can do, not by repo order: prefer dev-cadence's source (`files/scripts/nudge_user_attention.py` in its registered checkout), else the newest copy among the registered repos (or this repo's own); and say on the page when the copy read lacks a field the page draws. Separately, samscrape's synced files are two weeks behind: `sync-all.sh` for it is dev-cadence's rollout, not this entry's.

**Done when:** with samscrape's copy left as it is, the Inbox draws TD-255's answers on this repo's board rows, and a test pins the choice of copy.

**Related:** TD-255 (the answers, built), dev-cadence TD-036 (the reader's `Answers:`), dev-cadence TD-074 (the live look's pair), TD-069 (board items on the Inbox).

**Resolved:** 2026-10-03 — built; its look closed 2026-10-03 as Works (every live look line draws Works / Not right…, no `answers` note).

## TD-274: Build the waiting member: the finished reading, the lapse to the default, a question's end as rule 8 work, and the words

**Priority:** High
**Type:** feature
**Added:** 2026-10-02 (the designer, TD-271's build)
**Owner:** grinder
**Kind:** build
**Status:** Slice 1 (PR #924, 2026-10-02): the home's reading. `work.waiting_of` reads the person inbox; `work.finished(…, waiting=)` reads a waiting member as not finished, with the clause; rule 9 passes it and its manager's half takes the wind-down back. Slice 3 (PR #927, 2026-10-02): the lapse — an orphaned `steer` closes `lapsed` at its bound with the note in the closed asker's mailbox; an orphan's answer leaves its `handed` note there too; a note landing on an ended record runs its retention from its arrival. Fix (1)'s client half (PR #930, 2026-10-02): the home's `host` reading carries `waiting`; the page's *concluded*, a Start's close of a concluded team, `ao team list` and `ao team status --json` (with `waiting` by name) read it; the *not concluded* help clause in `help.py` and §4.5a. (2) The words (PR #936, 2026-10-02): the slot's ending, the Focus header chip and `ao status -v`'s declaration line say *waiting on you: <ref> until <time>*, the question's first paragraph on hover. (4)'s client half (PR #940, 2026-10-02): the Inbox team start row's question clause and the card note's count, from `work_waiting.questions`. Slice 4 (PR #932, 2026-10-02): rule 8's home half — `_question_end` writes `work_waiting.questions` (with each question's `kind`) at the lapse and the answer for a wound-down team; rule 8's replays keep every record's mail, and a team's start asks `keep_mail` for a member whose name a closed record holds where the one starting may hand that mailbox on. Built: merged, live look pending on `docs/user_attention.md` — the *Done when* is the check.
**Location:** `src/sessionorc/work.py` (`finished`: its callers hand it the person inbox's open entries, or a sender → questions map; `wound_down` untouched), `src/sessionorc/agent_inbox.py` (`_lapse_or_expire`: the orphaned branch; `_answer_orphan`: the holders; `_adopt_orphans`), `src/sessionorc/agent_tick.py` (rule 8's `work_waiting` pass and `_work_marks`; `_finished_pass`; rule 8's start and `ao team start`'s create: `keep_mail` for every superseded record), `src/agentorc/ui/cards.py` and `templates/card.html`, `focus.html` (the slot's ending, the header chip), `src/agentorc/ui/inbox.py` (`_orphan_held` kept; the orphaned row's time text; the team start row's clause; the work waiting tooltip), `ao status -v` and `ao team status --json` (`waiting`), `src/agentorc/ui/help.py` (*not concluded*), design §4.5a's help list (bound to help.py — the wording change lands here, with the code)

**Why:** design §4.9a *Waiting is read, never declared*, §4.10 *An orphaned `steer` lapses to its default*, §6 rule 8 *A question's end is work*, rule 9, §4.5a **waiting** mark (TD-271): on 2026-10-02 the designer declared `none` with three steered design PRs open, the team wound down, the bounds passed with nobody to take the defaults, the Inbox read *0 steers*, and Paul's answers reached nobody for eighteen hours. Every fact needed was on the records and in the person inbox.

**Fix:** build the design as written, in slices, each its own PR. (1) **The reading**: `finished` reads a live member with an open `ask` or `steer` from it in the person inbox whose `about` names a reference (`normalize_ref`'s two shapes) as waiting — a `why` clause *<name> waiting on the person: <ref>, until <bound>* (the sooner bound of several, *and n more*) — so rule 9's wind-down and the page's *concluded* hold back; `ao team status --json` carries `waiting: {<name>: [{id, ref, bound}]}`; the help entry *not concluded* gains the clause *one waiting on your answer to its question*, in `help.py` and §4.5a's list together. (2) **The words**: the card slot's ending and the Focus header chip as the §4.5a row says, `ao status -v` on the declaration's line; the hover is the question's first paragraph. (3) **The lapse**: `_lapse_or_expire`'s orphaned branch closes the entry `lapsed`, writes the adopted form's note into the closed asker's mailbox, and hands rule 8 the question's end; `_orphan_held` stays for entries the older rule cleared; `_answer_orphan` writes the `handed` note to the closed asker's record beside the live holders'. (4) **Rule 8**: `work_waiting.questions` written at the lapse and at the answer for a wound-down team, no settle, a standing mark gaining the question and keeping its `at`; the Inbox row's clause and the card note's count; every create that supersedes a closed record under its name (`ao team start`, rule 8's start) keeps the record's mail as a Restart does. Tests: a member with an open steer about TD-n keeps `finished` from holding and one about prose does not; an orphaned steer at its bound closes `lapsed`, notes the closed record, and writes `work_waiting.questions` on a wound-down team and nothing on a stopped one; a Start keeps the note; the slot's words and the Steering row's time text under each `on_work`.

**Done when:** a designer that opens a design PR, steers and has nothing else to pick keeps its team live until the bound and is rung by the lapse; one closed before the bound has its team asked to start, or started, at the bound or at the answer, and its successor finds the note in its inbox; the card, Focus and the Inbox say what waits and until when.

**Related:** TD-271 (the design), TD-213 / TD-216 (the orphaned question), TD-214 / TD-227 (rule 8), TD-240 / TD-241 (rule 9), TD-246 / TD-250 (`keep_mail` on a Restart), TD-187 / TD-195 (rule 6), TD-262 / TD-266 (the same slot).

**Resolved:** 2026-10-03 — built; its look closed 2026-10-03 as Works (card, Focus header and `ao status -v` read *waiting on you*); the weekday the Focus header drops is TD-296 (5), and the wind-down-first part waits in TD-297.

## TD-255: Build a board row's answers — the decide write-back, the buttons, Go with it, a live look's pair

**Priority:** High
**Type:** feature
**Added:** 2026-09-30 (the designer, from TD-254's design)
**Owner:** paul
**Kind:** live-check
**Status:** Partly done — **slice 1 built 2026-10-01 (grinder-ao-2, PR #861):** `decide` in `board.ACTIONS`; `edit_line` writes `Decided: <text> (<date>)` at the line's end and refuses a line already decided or an answer the reader would not read back (`DECIDED_RE`, the reader's own); `message` is `agentorc: decide <head>: <answer> (session <name>)`; `rpc_board_edit` takes `answer` and `answers` and refuses any answer that is not one of them word for word, or `Not right:` with words where the pair is among them (`_board_answer`). A reply is written ahead of the line's `Answers:` and `Decided:` fields (`_fields_start`): before this a reply on a line with `Answers:` was read by the reader as part of the last answer. Tests in `tests/test_board.py`, two of them reading the line back with `scripts/nudge_user_attention.py` itself. **Slice 2 built 2026-10-01 (grinder-ao-2, PR #863, merged; live look pending, on the board):** the board row carries the reader's `answers`, `default`, `decided`, `kind` and a `body` without the field tails (`board_rows`, `board_body`); `inbox_row.html`'s `board()` draws the answer buttons, the default's mark, **Go with it** in the foot after Reply and *decided: <text> · <date>* in their place, disabled on a row read from origin; `decide` joins `BOARD_ACTS` and the route hands `answer` and `answers` to `board_edit`; `g` and `1`–`4` in `AO.KEYS` (`AO.keyAnswers`, the digits yielding only on a row with answer buttons — an `ask`'s too); help entries `board-answers` and `board-go-with-it` in §4.5a's list and `ui/help.py`. Tests in `tests/test_ui_board.py` (one end to end through dev-cadence's reader and the real write-back) and `tests/test_ui_keys.py`. Slices 3–4 open and **waiting on dev-cadence TD-074** (checked 2026-10-01: neither `docs/cadence.md` nor the synced scripts carry the pair's words, `Answers: Works | Not right: <what>.`, so there is nothing to draw by its words and nothing to regroom to) — **dev-cadence TD-074 is resolved (seen by the anchor 2026-10-01: its PRs #186, #187 and #193 merged, the last at 13:30Z, and dev-cadence's `files/docs/cadence.md` §3.5 carries the pair); what slices 3–4 wait on now is the sync PR that brings it here**, **synced 2026-10-01 by the anchor from dev-cadence `b90272e`: `docs/cadence.md` §3.5 and `scripts/nudge_user_attention.py` now carry the pair's words, so slices 3–4 wait on nothing**. **Slice 3 built 2026-10-01 (grinder-ao-2, PR #896; live look pending, on the board):** `inbox.live_look` — a `watch` whose two answers are *Works* and the form *Not right: <what>* — rides the row as `pair` with the item's `head` and the checkout's `name`; `inbox_row.html` draws **Works** and **Not right…** by their words, a default on *Works* as *Go with it: Works* (which `g` presses, with none in the foot); `app.js`'s `board_notright` opens the composer with *Not right:* begun (`AO.compose`'s `text`), sends one `decide`, then `/api/entry/hand` with type `debt` — the designed `bug` is no word of `entry_add`'s; §4.5a now says `debt` — and *<head> — Not right: <what>*, a refusal toasted; `_board_answer` takes an offered answer word for word before the form (`board.NOT_RIGHT_FORM`; the review of #863); `briefs/manager.md` step 3 has the round read `decided` and tell the holder of a *Works*. Tests in `tests/test_ui_board.py` and `tests/test_board.py`. **Open from slice 3:** a team whose manager is on call has no round, so nothing tells the holder of a decided *Works* there — the raiser reads its own line at its next start (cadence §3.5); and the pair has no help entry (each button's `title` says what it does). **Slice 4 done 2026-10-01 (grinder-ao-2):** the board pass (PR #897) and the briefs' live-look line (PR #899); slice 5's tests are written with each slice. **Every slice is built. Left:** Paul's live look after a promote (the board). **Next:** Paul's live look after a promote, on the board; every slice a grinder can build is built.
**Location:** `src/sessionorc/board.py` (`ACTIONS`, `write_back`, `_write_back`: the `decide` action calling `scripts/board_edit.py decide --answer`), `src/sessionorc/agent_inbox.py` (`rpc_board_edit`), `src/agentorc/ui/inbox.py` (`BOARD_ACTS`, the board row view: `answers`, `default`, `decided`, `kind` from the reader), `src/agentorc/ui/templates/inbox_row.html` (the `board()` macro; the `answers(e)` macro as the model), `src/agentorc/ui/static/app.js` (the keys `g`, `1`–`4`; the Not right… composer), `src/agentorc/ui/help.py` and §4.5a's help list (bound by `tests/test_help.py`), `src/agentorc/ui/app.py` (the API route), `src/agentorc/briefs/*.md` and `docs/briefs/grinder-ao-*.md` (the live-look line; `docs/briefs/**` is a held path), `tests/`

**Why:** TD-254's *Why*: the groom's `Answers:` and defaults are invisible on the Inbox and nothing on the page records a decision; 49 live looks read as a wall with nothing to press.

**Fix, in slices a PR each:**
1. **The write-back** (`sessionorc`): `decide` joins `ACTIONS` in `board.py` as a branch of `edit_line` and `message` making the edit `board_edit.py decide` makes — `Decided: <text> (<date>)` at the line's end, the message `agentorc: decide <head>: <answer> (session <name>)` — under `ready(root)`, exact text, refused on a line already carrying `Decided:`; `rpc_board_edit` takes `answer` and `answers` (the reader's list, handed by the page as `refs` are) and refuses an answer that is neither one of them word for word nor `Not right: …` when the pair is among them. The reply edit learns to write ahead of a `Decided:` tail. Tests: a decide writes the field and the message; a moved line, a dirty board, a decided line and an answer not on the list are refused; a reply after a decide leaves `decided` readable; a decide after a reply follows its tail.
2. **The buttons**: the board row draws the item's `answers` as buttons in order, in the order written, the `default`'s marked, and a bare **Go with it** in the foot after Reply with the steer's confirm naming the default; a press calls `board_edit {action: decide, answer}` and the row re-reads *decided: <text> · <date>* (from the reader's `decided`) in place of the buttons; rows read from origin draw them disabled; the help entries for **answers** and **Go with it** go into §4.5a's help list and `ui/help.py` together (their wording is this slice's), and `g` / `1`–`4` on the ringed row, the digits yielding to the row only when it has answer buttons; the body stops printing the `Answers:` and `Decided:` tails.
3. **A live look's pair** — once dev-cadence TD-074 has landed the words (`Blocked by` is not written here because slices 1–2 do not wait): a `watch` item whose answers are the pair draws **Works** and **Not right…**; *Works* decides at once; *Not right…* opens the Reply composer with *Not right:* begun and decides with the text, one write; a decided *Not right* hands an entry to the repo's techlead by a second call from the page, `entry_add` with type `bug` and the repo's teams, after the decide committed, its first line the item's head and the text — a refusal is toasted and the decision stands; a decided *Works* is left for the raising team's round — the manager preset's round reads `decided` on its repo's board rows and tells the member that holds the item's `Context:` ref to close the line and archive the entry (`ao msg --kind note`), once per item.
4. **This repo's live looks and the briefs**: one pass over `docs/user_attention.md` giving every *merged, live look pending* item the pair (a grinder's PR; the words TD-074 settled), and the package briefs' and this repo's supplements' live-look line written with the pair (`docs/briefs/**` is held: that PR waits for the techlead's read). **The board pass done 2026-10-01 (grinder-ao-2, PR #897):** the ten open `watch` lines end with the pair, no default; TD-255's own look gets it in #896. **The briefs' line written 2026-10-01 (grinder-ao-2):** `docs/briefs/grinder-ao-1.md`, which its sibling's brief follows, says a live look is a `watch` line ending with the pair, no default without evidence, and what each answer orders; the package's templates carry no live-look line (cadence §3.5 is the rule for every repo), so none was added there. Slice 4 is done.
5. **Tests**: a row with three answers and a default draws three answer buttons in the order written, the default marked, and Go with it in the foot; a press writes `Decided:` and the row re-reads; a `watch` with the pair draws two buttons and *Not right…* writes the typed text; a decided row from *Board, coming up* moves to *Needs you* and is counted; a decided item's body prints no `Answers:` tail; `1` on a ringed board row presses the first answer as written and `g` Go with it.

**Done when:** on a scratch home, a board item's answers are buttons on its Inbox row, one with a default is decided by one press, a live look by one of two, and the board line carries `Decided:` after each; the help entries are in §4.5a's list; this repo's live looks carry the pair.

**Related:** TD-254 (the design), dev-cadence TD-074 (the guidance and the pair), TD-244 (the groom), TD-142 (Reply), TD-218 / TD-219 (an entry handed to the techlead), TD-124 (the keys), TD-140 (Put on the board, the first add), TD-036 (`board_edit.py`).

**Resolved:** 2026-10-03 — built; its look closed 2026-10-03: the pair, `decided:` and `1` to the Org work; the other answers' order, Go with it and the keys wait in TD-297.

## TD-254: A board row draws no answers at all — the answer buttons, *Go with it* as a steer has, and a live look's two answers

**Priority:** High
**Type:** feature
**Added:** 2026-09-30 (Paul, on the Inbox after TD-244's groom: *I see many agentorc items in the inbox that do not have recommended answers*, then: *let's add "go with it" similar to the steering entries. We should probably build some guidance on this to keep it consistent*)
**Owner:** designer
**Kind:** design-first
**Status:** Designed 2026-09-30 (the designer, PR #836; the steer to Paul is `m-68df9eaccb90`, bound 2026-10-01 09:12 MDT): design §4.4 *Board write-back* (**Decide**), §4.5a **Due strip / Inbox board row: answers, Go with it** and **Works / Not right…**, the Inbox **keys** row. The Fix's five points as written; the round offered the default's button first carrying its text; Paul chose a bare *Go with it* in the steer row's place with the default marked among the answers, and *Not right…* decides with the typed text in one write. Closes with TD-255. **Next:** designed; the build is TD-255.
**Blocked by:** TD-255
**Location:** design §4.5a (**Inbox row: `steer`** for the shape; the board rows' controls: Snooze, Done, Reply, Open board — no Decide row exists), §4.5 screen 6, §4.10; `src/agentorc/ui/templates/inbox_row.html` (the `board()` macro, which never calls `answers(e)`), `src/agentorc/ui/inbox.py` (`BOARD_ACTS = ("snooze", "done")`), the board RPC (no `decide` action), `ui/help.py` and §4.5a's help list (bound by `tests/test_help.py`); `scripts/board_edit.py` (SYNCED: `decide`); `docs/briefs/grinder-ao-*.md` and the package briefs, where they say how a live look is written

**Why:** after the groom the board holds 60 open items: 6 `decide`, each with `Answers:` and five with a `(default)`, 49 `watch` that are nearly all *merged, live look pending*, and 5 `act`. On the Inbox a steer shows the sender's one line and **Go with it**, and an `ask` its suggested answers as buttons. **A board row draws none of it**: its controls are Open board, Reply, Snooze and Done, the template never draws an item's `Answers:`, and neither the page nor the board RPC has a `decide` action, so the answers and the recommendation the groom wrote are invisible on the Inbox and the only way to record a `Decided:` is `board_edit.py decide` by hand. That is why Paul saw *many items that do not have recommended answers* the day after a groom whose point was to add them. A live look has the same four controls, so *I looked and it works* is Done with nothing recorded and *it does not* is prose in a Reply; 49 rows read as a wall with nothing to press. The guidance for what a session writes is the cadence's and is dev-cadence TD-074 (the fixed pair `Answers: Works | Not right: <what>.` on a live look, when a default is allowed); this entry is the Inbox's half.

**Fix — a design round, then a build entry:**
1. **The answer buttons on a board row** — the Decide control cadence §4.5 already allows a tool and this page never built: an item's `Answers:` drawn as the row's buttons, as an `ask`'s suggested answers are (display from the board's own fields, never a control built from prose), each press `board_edit.py decide --answer "<its text>"` through a `decide` action on the board RPC beside `snooze` and `done`, with the refusals `board_edit.py` already gives (the line moved, a board read from origin). The row then reads *decided: <text>* and stays until its session closes it.
2. ***Go with it* on a row that carries a `(default)`**: one press, in the steer's words and place, that records the default — the same `decide`, the person's action as every Decide is, never something the board or a session falls to. Say how it sits beside the answer buttons (the default's button first and marked, or *Go with it* in its place), and that a row with no default has no such button.
3. **A live look's pair as two buttons** once boards carry it: *Works* decides at once; *Not right…* opens the Reply composer and decides with the text. Say what follows each: *Works* is the raising team's order to close the line and archive the entry (rule 4's owed-outcome shape, or a board note to the manager), *Not right* a TD handed to the techlead as Add entry does (TD-218).
4. **This repo's existing live looks** get the pair in one pass by a grinder, as TD-244 did the kinds, once TD-074 has settled the words; and the briefs' *merged, live look pending* line is written with the pair from then on.
5. **Keys and help**: a key for *Go with it* on the ringed row beside `x` and `r`; the help sentences for both controls.

**Done when:** the design's §4.5a rows say all of it and a build entry carries the slices; on a scratch home a board item's answers are buttons on its Inbox row, one with a default is decided by one press, a live look by one of two, and the board line carries `Decided:` after each.

**Related:** dev-cadence TD-074 (the guidance and the pair), TD-244 (the groom), TD-142 (Reply on a board row), TD-218 and TD-219 (an entry handed to the techlead), TD-124 (the pages' keys); the steer's *Go with it*: §4.5a **Inbox row: `steer`**, §4.10.

**Resolved:** 2026-10-03 — designed (PR #836) and built as TD-255, archived 2026-10-03.

## TD-271: A member waiting on a steer's bound is closed with its team, and nothing brings it back at the bound or at the answer

**Priority:** High
**Type:** debt
**Added:** 2026-10-02 (the anchor, from Paul's walk of Focus, on the designer's closed page: *it seems odd that TD-262 would be "claimed" but the worker would have wound down/closed — do we need a "waiting" state for workers when they are waiting on a steer? Also, my inbox shows zero steers — is this waiting on a reviewer/other?*)
**Owner:** designer
**Kind:** design-first
**Blocked by:** TD-274
**Status:** **Designed 2026-10-02** (the designer, PR #910; the shape steered to Paul as `m-3eb438d357a9`; Paul, 2026-10-02: *go with your default*). Design: §4.9a *Waiting is read, never declared* (a live member with an open `ask` or `steer` to the person about a reference is waiting: read by `finished` from the person inbox each tick, never declared, never a state; not finished whatever it declared, so the team stays live to the bound or the answer and the lapse or the reply rings it where it sits; `none` is not refused), §4.10 *An orphaned `steer` lapses to its default* (TD-213's cleared bound reversed: the entry closes `lapsed`, the note goes into the closed asker's mailbox, nothing becomes the person's question), *The name coming back adopts it* (a superseding create keeps the closed record's mail) and *Where the person's answer goes* (the closed asker gets the `handed` note too), §6 rule 8 *A question's end is work* (the lapse, or the answer, on a wound-down team writes `work_waiting.questions` with no settle; `on_work` as for ids; the start keeps the mail) and rule 9 (a waiting member blocks the reading), §4.5 row 5 (b) and §4.5a's **waiting** mark row, the orphaned question row's Steering text (*then its default stands and ao-grind is asked to start*), the team start row and the work waiting note; glossary *waiting*, *finished*; mockups `gen.py` (tdgrind-4's slot, the Inbox's orphaned steer). Settled: (1) waiting is a reading, not a state or a fourth declaration; (2) the default stands at the bound whoever is there, since doing nothing was offered as the answer; (3) a question's end is rule 8's event, with the row and the start it already has; (4) a stopped team is left alone, and a merely exited asker's steer is not orphaned. The build is TD-274.
**Location:** design §4.9a *Out of work*, §6 rule 9 (*a team's finished is the home's reading*), rule 8 (`on_work`), §4.10 *An orphaned `steer` lapses to its default* (before this round, *does not lapse*) and *The name coming back adopts it*, §4.5 screen 6 (*Steering*, *Needs you*); `src/agentorc/ui/inbox.py` (`_orphan_held`), `src/sessionorc/agent_tick.py` (rules 8 and 9), the designer's brief (*it merges at the bound*)

**Why:** on 2026-10-02 at about 04:00Z designer-ao-1 had three design PRs open and green — #892 (TD-222), #895 (TD-262, stacked on it) and #903 (TD-266). Two waited on a steer to Paul whose default *merges at the bound*, that morning (#892 and #903); #895, its steer already answered, waited on #892. It said so (*waiting on three steer bounds*), declared out of work, and the team wound down with every member finished: by rule 9's reading a member with nothing to pick is done, whatever it waits on. Eighteen hours later the three PRs are still open. Nothing fails, and nothing moves:
- **The bound passes with nobody to take the default.** The two steers were orphaned at the close; at each bound the home cleared it rather than take the default (§4.10, `_lapse_or_expire`), so they wait on Paul as questions — though each had told him *nothing to do if the default is right*.
- **The page reads as if nothing waits.** The Inbox's *Steering* line said 0 of 0 — right by its rule, since an orphaned steer is a *Needs you* row — and Paul read it as no steers; the designer's Focus says *closed · out of work* over three claims, *TD-262 claimed* among them, with no word that it waits or on what.
- **An answer wakes nobody.** Paul's replies to the first two steers were written to the board, since no live session held the references; the team's lanes gained nothing, so rule 8 has nothing to start it for. The designer's next run found them only because the anchor started the team by hand.

**Fix:** design first. Settle: (1) **whether waiting on a person is a state** — a member that holds an open steer or ask with work that follows the answer is *waiting*, not *out of work*: said on its card and Focus with what it waits on and until when, and read by rule 9 so a team with a waiting member is not finished, or is finished and says what it left waiting; (2) **what happens at the bound when the sender is closed** — the home brings the member back (a restart from its launch record, as rule 2 makes one) to take the default, in place of clearing the bound and turning a *nothing to do* into a question; (3) **what an answer does** — a reply to an orphaned steer starts its asker, or its team, as a lane's new work does under rule 8, within the same bounds; (4) **what the Inbox says** — a steer whose sender is closed and whose default will be taken at a time reads so, and one that has become the person's question says why it did.

**Done when:** a designer that opens a design PR, steers and has nothing else to pick is back at the bound or at the answer without a person starting its team, and until then its card and the Inbox say what it waits on.

**Related:** TD-213 / TD-216 (the orphaned question), TD-214 / TD-227 (rule 8, the start on work), TD-240 / TD-241 (rule 9, finished), TD-187 (a member out of work is never woken when its lane gains entries), TD-262 (who closed a session — the same card slot), memory `steer-replies-land-on-the-board`.

**Resolved:** 2026-10-03 — designed (PR #910) and built as TD-274, archived 2026-10-03.

## TD-296: What the live looks of 2026-10-03 found off

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-03 (the anchor, from five look agents on Paul's word: *lets pass the looks back to the grinders or do them here*)
**Owner:** grinder
**Kind:** build
**Status:** Built. #14 fixed — PR #979 (a pressed answer's index is written on the question it closes, so *Waiting on them* says the answer; `ao inbox` prints a question's picked answer in the answer's words). #13 not reproduced: the live records carry `review_pr` on a declared claim with an open PR (designer-ao-1's TD-290 → #977, grinder-ao-2's TD-292 → #978, 2026-10-03); the look's shot was taken at 20:25 MDT, the minute #947 merged (02:25:47Z), and a merge clears `review_pr` by design (TD-150) while the declared claim stands until its session says `done`. #1, #2, #17 fixed — PR #985 (the chip prints `:g`, `.btn[hidden]`, the pull reading in its note's column). #4 fixed — PR #986 (`AO.quoteText`: the composer's quote without the inline marks). #5 not reproduced: the look's own `focus_sam1.png` reads *until Sun 17:17* in the Focus header, and the card, Focus and `ao status -v` all draw it with `ending.waiting_words`, which adds the day once it is not today. #7, #10 fixed — PR #988 (`ao team list` says *wound down <t> ago*; a seat's *on call* pill is never `scraped`). #3, #8 fixed — PR #989 (`.field > label`; `/api/dir_check` says `git` and Where hides the worktree choice outside a checkout; `applyRole` keeps a picked team's manager ticked). #16 fixed — PR #991 (the debt row's id and tags `flex: none`; the narrow wrap skips a Doing row). #15 fixed — PR #994 (rule 9's `system` note stamps its team, so the rail files it under that team). #11 fixed — PR #995 (the read-only line sits over the terminal, not in xterm's normal buffer, which tmux's alternate buffer hid; the toast waits for `onKey`). #6 fixed — PR #1002 (screen rules `first-run-theme` and `first-run-login`: Claude Code's first-run screens read `needs-you`, a question answered in the terminal). #9 fixed — PR #999 (the top bar wraps and the usage chip keeps its width, its own row below 720 px; Settings' number boxes at 4.5em). #12: the design's sentence changed — PR #1008 (the techlead's ruling: a program that asks for the mouse gets it; Shift+drag selects off a Mac; a read-only attach drops the reports).
**Location:** `src/agentorc/ui/` (templates, `static/app.css`, `static/*.js`), `src/sessionorc/` where named; screenshots on kmaster under `~/ao-shots/looks-A/` … `~/ao-shots/looks-E/` (outside the repo: read them there, copy the ones a PR needs into `docs/mockups/reviews/`)

**Why:** the board's 25 live looks were made on 2026-10-03 by a second session against the live copy `8de3e27` (read-only) and isolated scratch homes (presses). Most work; their lines are closed in `docs/user_attention_archive.md` with a verdict each. These are what did not, numbered as the closed lines cite them.

**Resolved:** 2026-10-04 (PRs #979, #985, #986, #988, #989, #991, #994, #995, #999, #1002, #1008) — each of the seventeen fixed, not reproduced (#5, #13) or its design sentence changed (#12); the lasting content is in design §4.2, §4.5, §4.5a and §4.6 and the tests each PR names.

**Done when:** each of the seventeen is fixed or its design sentence changed, each checked on a scratch home with a screenshot in its PR.

**Related:** TD-297 (what was not seen), TD-298 (the scratch home that leaked), TD-291 (the builder's check), TD-244 (the walks).

## TD-311: `look_home.py`'s teardown leaves its `/tmp/aolook-*` home behind when a claude-code session ran in it

**Priority:** Low
**Type:** debt
**Added:** 2026-10-04 (grinder-ao-2, TD-283 slice 2's UI check)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `scripts/look_home.py` (`main`'s `finally`: `tmux kill-server`, then one `shutil.rmtree(home)`)

**Why:** a UI check that presses Add entry → Open a session starts a real `claude` in the scratch home's tmux server. The teardown kills the server and removes the home once, but the dying tool's hooks still fire after that (its `SessionEnd`/`Stop` run the hook script, which appends to `<home>/events/<id>.jsonl`). That recreates `<home>/events/` after the `rmtree`. Twice on 2026-10-04, `/tmp/aolook-*` was left holding one events file. The script's docstring promises that only a SIGKILL leaves anything behind.

**Resolved:** 2026-10-04 (PR #1017) — `remove_home` in `scripts/look_home.py`, pinned by `tests/test_look_home.py`.

**Done when:** a look home torn down after an Open a session press leaves no `/tmp/aolook-*`.

**Related:** TD-291 (the scratch home), TD-283 (the press that found it).

## TD-305: Build the answered board row — a decided or replied board item waits under *Waiting on them*

**Priority:** High
**Type:** feature
**Added:** 2026-10-04 (the designer, TD-303's build)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/inbox.py` (`board_rows`, `board_due_now`, `board_horizon`, `inbox_sections`, the rail's counts), `src/agentorc/ui/app.py` and the board row's partial (the row under *Waiting on them*, its controls), `src/agentorc/ui/help.py` and design §4.5a's help list (the **answers** and **Go with it** texts), `docs/mockups/gen.py` (`INBOX_BLURB`)

**Why:** TD-303's design: a board item the person has decided or replied to waits on a session, and the Inbox still draws it under *Needs you*, counted.

**Resolved:** 2026-10-04 (PR #1018) — `board_answered`, `board_waits` and `board_waiting_on` in `src/agentorc/ui/inbox.py`, the board row's *Waiting on them* branch in `inbox_row.html`, pinned by `tests/test_ui_board.py`; design §4.5 screen 6 *A board item the person answered waits on them*.

**Done when:** on a scratch home (`scripts/look_home.py`) with a board that holds a decided item, a replied one and an undecided due one: the first two are under *Waiting on them* and in neither the top bar's number nor the Org's *m overdue*, the third is under *Needs you*; pressing an answer on the third moves it at the press; a decided item whose date is three days back is under *Needs you*, one two days back is not, counted, saying nobody has acted; tests cover the three placements, the stale return, a reader without `waiting_on`, and the counts. A page change, so the builder looks at it and sends the look (§4.9b).

**Related:** TD-303 (the design), TD-142 (the standing on a *Needs you* board row, slice 2), TD-304 (the detail block on the same row), TD-255 (answers), TD-079 (*Waiting on them*).

## TD-303: An answered Needs-you row stays in Needs you

**Priority:** High
**Type:** debt
**Added:** 2026-10-04 (Paul: *Currently a "Needs you" item seems to stay in "needs you" once answered - it should move out to "waiting on them"/other so it does not appear to be waiting on me*; the anchor session)
**Owner:** designer
**Kind:** design-first
**Status:** **Designed 2026-10-04** (the designer, PR #1006): design §4.5 screen 6 *A board item the person answered waits on them*, §4.5a **Inbox section: Waiting on them** and the **answers** and **Reply** rows, §4.4 *Decide*; mockup `Inbox.dc.html`, shot as `docs/mockups/reviews/2026-10-04-td303-answered-board-row.png`. The reader already says whom a decided item waits on (`waiting_on: session`, dev-cadence TD-036) and the Inbox did not read it, so the row's place follows from the reader's fields and nothing is stored at the home. Two choices are steered to Paul as `m-3a00d3ac1c03`, which lapsed at its bound (2026-10-04 12:31 MDT), so both stand: a Reply moves the row as a `Decided:` does, and the row comes back to *Needs you* after three days with nothing done (`BOARD_WAIT_DAYS`). No mail row needed the cure. The build is TD-305.
**Location:** design §4.5 screen 6 (the Inbox's sections), §4.5a **answers** / **Go with it** / **Reply** (Inbox board row) and the help text that says the decided line *stays on the board, due*, §4.4 *Board write-back*; `src/agentorc/ui/inbox.py` (`inbox_sections`, `board_due_now`); cadence §3.5 (*a decided entry is not done: it stays on the board, and stays due, until a session acts on it*)

**Why:** a board row answered from the Inbox (a pressed answer or Go with it writes `Decided:`; a Reply writes `— Paul, <date>: …`) stays under **Needs you**, counted in the top bar, because the design keeps the line *due* as its session's work order and the Inbox counts every due board row as the person's. Seen 2026-10-04: samscrape's backup item reads *decided: approve R2 · Oct 3 — not done: it stays on the board as its session's work order* at the head of Needs you (64). Once the person has answered, the item waits on a session, which is what *Waiting on them* says for mail.

**Resolved:** 2026-10-04 (PR #1006 the design, PR #1018 the build, TD-305) — design §4.5 screen 6 *A board item the person answered waits on them*; `board_answered` / `board_waits` in `src/agentorc/ui/inbox.py`, pinned by `tests/test_ui_board.py`.

**Done when:** an answered board row leaves Needs you and the top-bar count at the press, and reads under its new section as waiting on its session.

**Related:** TD-254/TD-255 (answers on a board row), TD-142 (Reply), TD-079 (*Waiting on them*), TD-290 (looks as mail), TD-297 (who closes a decided look).

## TD-306: A scraped `needs-you` stays after its screen is gone, until the next hook

**Priority:** Low
**Type:** debt
**Added:** 2026-10-04 (grinder-ao-2, the review of PR #1002)
**Owner:** grinder
**Kind:** build
**Status:** Built — PR #1013 (`_screen_held`, `_screen_gone` and `_hook_state` in the tick; design §4.2 *A rule's verdict lasts as long as its screen*). Nothing waits on a live look: the test in `tests/test_agent_paths.py` is the check.
**Location:** `src/sessionorc/agent_tick.py` (`_observe`: a screen verdict is applied only on a match; the `stalled?` cross-check reads `working` alone)

**Why:** a hook-fed session whose last hook is older than `STALL_AFTER` takes a screen rule's verdict at once (design §4.2), and nothing takes it back when the screen stops matching: the record keeps the scraped `needs-you` until the tool's next hook. For the first-run screens and the trust dialog that hook comes (`SessionStart` once onboarding ends), but a person who opens `/theme` or `/login` in a session idle for twenty minutes and dismisses it leaves the card reading *needs-you* until the next turn — the same for any screen rule's state on a quiet session.

**Resolved:** 2026-10-04 (PR #1013) — `_screen_held`, `_screen_gone` and `_hook_state` in `src/sessionorc/agent_tick.py`; design §4.2 *A rule's verdict lasts as long as its screen*; pinned by `tests/test_agent_paths.py`.

**Related:** TD-015 (screen rules), TD-296 #6 (the first-run rules), TD-283 (the at-composer `startup`).

## TD-312: Build the detail block on an Inbox board row

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-04 (the designer, TD-304's build)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/render.py` (`_block`: lists), `src/agentorc/ui/inbox.py` (`board_rows`, `_find_text`), `src/agentorc/ui/templates/inbox_row.html` (the board row's fold), `src/agentorc/ui/static/app.js` (`foldsOpen`, `reopenFolds`), `tests/test_render.py` and the Inbox tests

**Why:** TD-304's design: a board item's detail block is what the person decides from, and the Inbox row draws the head line alone.

**Resolved:** 2026-10-04 (PR #1021) — `render._list` (lists nest by indent), `board_detail` in `src/agentorc/ui/inbox.py`, the board row's fold in `inbox_row.html`, `foldsOpen` / `foldsShut` in `app.js`; pinned by `tests/test_render.py` and `tests/test_ui_board.py`.

**Done when:** on a scratch home (`scripts/look_home.py`) with a board synced past dev-cadence TD-084: a `decide` item with a block shows Context, Question, Caveats and Recommended under its head with nested bullets drawn as nested lists, the fold open; an item with a block and no answers has it closed; an item without a block, and a report whose items carry no `detail` key, draw as today; a block holding `<script>` or `Answers:` draws them as text; tests cover the renderer's nesting and the row. A page change, so the builder looks at it and sends the look (§4.9b).

**Related:** TD-304 (the design), TD-305 (the answered row, the same partial), TD-279 (the row's text and fold), dev-cadence TD-084.

## TD-304: The Inbox shows a board item's head line only — not the detail block the person decides from

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-04 (Paul: *add a td for agentorc*, after dev-cadence TD-084 gave board items a plain-English detail block; the dev-cadence session that built it)
**Owner:** designer
**Kind:** design-first
**Status:** **Designed 2026-10-04** (the designer, PR #1009): design §4.5a **Inbox board row: detail block**, §4.4 *Board write-back* (**add** writes a head line alone), §4.10 (the subset's lists nest); mockup `Inbox.dc.html`, shot as `docs/mockups/reviews/2026-10-04-td304-detail-block.png`. The block is drawn in the row's *details* fold, open on a row with answers to press and closed otherwise; nothing is lifted out of it; **Put on the board** asks for no block. The fold's default is steered to Paul as `m-d4d4dcb3c6a3`, which lapsed at its bound (2026-10-04 12:39 MDT), so it stands. The build is TD-312.
**Location:** design §4.5 screen 6 (the Inbox's board rows), §4.5a (answers / Go with it / Reply on a board row), §4.4 *Board write-back* (**add**, *Put on the board*); `src/agentorc/ui/inbox.py`, `src/agentorc/ui/app.py` (the board row), `src/sessionorc/board.py` (`add`, `item_line`); dev-cadence cadence.md §3.3 *An item explains itself*

**Why:** since dev-cadence TD-084 (PR #210, 2026-10-04), a board item has a short head line carrying every field and, under it, indented sub-bullets in plain English: **Context:** (a few sentences, nested bullets where clearer), **Question:**, **Caveats:**, **Recommended:** / **Otherwise:**. A `decide` always has one. They are what the person makes the decision from. `nudge_user_attention.py --report --json` carries them as each item's `detail` (a list of lines, common indent removed), but the Inbox row draws `text` (with `line` and `due_tag`) and never `detail`. So the row shows the headline and the answer buttons, without the context, caveats or the reason for the default. The person presses an answer they cannot see the case for, or has to open the board file. Nothing breaks: the write-back edits only the head line, where every field still lives, and **add** inserts above the first item, so never inside a block. It is a missing view, not a fault. The Inbox reads `detail` only once a repo has taken the sync carrying TD-084, and before that the key is absent.

**Resolved:** 2026-10-04 (PR #1009 the design, PR #1021 the build, TD-312) — design §4.5a **Inbox board row: detail block**; `board_detail` and `render._list`, pinned by `tests/test_ui_board.py` and `tests/test_render.py`.

**Done when:** a board row in the Inbox shows its item's detail block, on a repo synced past dev-cadence TD-084, and a row without one reads as it does today.

**Related:** dev-cadence TD-084 (the block), TD-254/TD-255 (answers on a board row), TD-142 (Reply), TD-140 (Put on the board), TD-303 (answered rows).
