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
