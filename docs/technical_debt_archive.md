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
