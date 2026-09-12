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

**Resolved:** 2026-09-10 (PR #41) — a global `--json` on `ao` (also accepted after any subcommand, `default=SUPPRESS` so the two never fight); every `cmd_*` goes through `emit()` (RPC result, or `{"ok": true, "id": …}` where the RPC returns nothing) and `fail()` (`{"error": …}` on stdout, same exit codes, `hint` for an unreachable agent). `ui` runs a server and prints nothing; `service install`/`uninstall` take the flag but are not exercised by the test (systemd side effects). Prose output is unchanged. README "CLI" documents it; test: `tests/test_cli.py::test_json_on_every_subcommand`.

**Related:** design §4.7; TD-019; ADR 2026-09-10.

## TD-017: Seen-state: "finished while you were away" is not the same as idle

## TD-016: `send` should confirm the prompt took: a send-and-wait RPC for policies

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/models.py` (`Session`), `src/sessionorc/agent.py`, `src/agentorc/ui/` (Herd card, Focus)

**Why:** A session that went `idle` while nobody was looking is the common phone-triage case, and
today it sorts and looks exactly like one that has been idle all day. herdr keeps `done` (idle,
not yet looked at) apart from `idle` by a server-side seen mark that explicit focus clears and
reads do not. agentorc's Focus view is the natural "seen".

**Resolved:** 2026-09-10 (PR #43) — `Session.seen_at` (persisted) set by `rpc_seen`, which the UI calls when Focus opens (`GET /focus/<id>`), after any card action, and from the open Focus page whenever its session's event arrives `unseen`. `view()` computes `unseen = idle and (no seen_at or since > seen_at)` (whole-second stamps: a tie reads as seen), renders "finished · unseen", sorts it at rank 4.5 (above `idle`, below `working`); `/events` carries the view's rank. `idle` stays `idle` in every payload. Design §4.5 sort list names the slot. Test: `tests/test_ui.py::test_unseen_idle_until_focused`.

**Related:** design §4.2, §4.5 (Herd sort); ADR 2026-09-10.

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

## TD-001: Short title of the problem

## TD-015: Screen-rule manifests per tool with `ao explain`: the scraped second source gets a shape

**Priority:** Medium
**Added:** 2026-09-10
**Status:** Resolved
**Location:** `src/sessionorc/models.py` (`Session`), `src/sessionorc/agent.py`, `src/agentorc/ui/` (Herd card, Focus)

**Why:** A session that went `idle` while nobody was looking is the common phone-triage case, and
today it sorts and looks exactly like one that has been idle all day. herdr keeps `done` (idle,
not yet looked at) apart from `idle` by a server-side seen mark that explicit focus clears and
reads do not. agentorc's Focus view is the natural "seen".

**Resolved:** 2026-09-10 (PR #43) — `Session.seen_at` (persisted) set by `rpc_seen`, which the UI calls when Focus opens (`GET /focus/<id>`), after any card action, and from the open Focus page whenever its session's event arrives `unseen`. `view()` computes `unseen = idle and (no seen_at or since > seen_at)` (whole-second stamps: a tie reads as seen), renders "finished · unseen", sorts it at rank 4.5 (above `idle`, below `working`); `/events` carries the view's rank. `idle` stays `idle` in every payload. Design §4.5 sort list names the slot. Test: `tests/test_ui.py::test_unseen_idle_until_focused`.

**Related:** design §4.2, §4.5 (Herd sort); ADR 2026-09-10.

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

## TD-001: `limited` state: wire adapter `usage()` into the agent tick

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (tick), `src/agentorc/adapters/claude_code/__init__.py` (`usage()`)

**Why:** Design §4.2 promises a `limited` state (usage cap hit, reset time shown, Switch profile / Wait). `usage()` exists and parses the OAuth usage endpoint, but nothing calls it: the agent never produces `limited`, and the top bar has no usage figure. Left out of phase 1a–1c to keep each PR reviewable. `usage()` is synchronous network I/O and must run in `asyncio.to_thread`, per profile, on a slow cadence (tdgrind polled per tick; once a minute is plenty), with a fetch failure never gating anything (§6).

**Resolved:** 2026-09-10 (PR #44) — `HostAgent._refresh_usage` (a detached task started by the tick, one at a time) asks each live agent session's adapter `usage_for(profile)` in a thread at most once per `USAGE_EVERY` (60 s), caches per profile (`rpc_usage`), streams `{"event": "usage", …}` to subscribers (also on subscribe), and applies the `limited` rule: an interactive session on a profile at 100% of a window gets `limited` with `Pending(kind="limit", text="5-hour cap · resets HH:MMZ")` (never over `needs-you`), and returns to the state it had before the cap once the window resets (a `resets_at` already past is not a cap). `ClaudeCodeAdapter.usage_for` is the name-keyed wrapper of `usage()`. The top bar carries a per-profile chip (`#usagechip`, red at a cap). Switch profile / Wait (§4.5a) are still to build. Design §4.3/§4.4 updated. Tests: `tests/test_agent.py::test_limited_from_usage_cap`, `tests/test_claude_adapter.py::test_usage_for_by_profile_name`.

**Related:** design §4.2, §4.2a, §6 usage gate (phase 3).

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

## TD-010: Adopt hand-started sessions: VS Code-terminal Claude sessions are invisible to the Herd

**Priority:** Medium
**Added:** 2026-09-06
**Status:** Resolved
**Location:** `src/sessionorc/agent.py` (`_reconcile` adoption of `ao-*` panes), `src/agentorc/adapters/claude_code/__init__.py` (`registry_entries`)

**Why:** The phase 1 success test reads "every session Paul has open on kmaster shows the right state". Today the Herd shows sessions agentorc launched plus any hand-started tmux session named `ao-*`. Paul's day-to-day sessions run in VS Code terminals with no tmux at all, so they never appear. Design §4.1 says hand-started sessions enter the Herd by being **adopted** (the Resumable tab's Adopt control; not built yet, and not assigned to a phase in §7), which assumes a tmux session to attach to; a VS Code-terminal session has none.

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

**Why:** `naming.session_id` appends `-2`, `-3` on any id collision — live or dead — and the record keeps the person's original name, so on 2026-09-10 the Herd showed `aotest` beside `aotest-2` and two cards named `tdgrind-ao-1` (one exited, one working). Paul: "it is a little confusing to have multiple sessions of the same name". The name is the handle `ao focus`, `ao send` and the filter take, so ambiguity there is a defect. Design §4.1 now says a name identifies one session per scope: a live holder refuses, an exited or closed holder is superseded (as `_supersede` already does for a resumed conversation), and a suffix is only for a tmux id with no record and is then shown.

**Resolved:** 2026-09-11 (steps 1-3 in PR #75, steps 4-5 in PR #77). All five steps shipped as the entry specified them, with three decisions worth keeping:

- **One place decides the rule.** `_name_verdict` answers "what would this name do" and both callers use it: `_name_holder` (which `rpc_create` calls, raising for a live holder) and `rpc_name_check` (which the New session form polls as you type, like the directory occupancy check). The refusal text, the "replaces the closed `<name>` — run log kept" note and the switch-to hint are composed there, so the form and `ao new` cannot drift.
- **The name is taken only once the launch has succeeded**, and the superseded record is *replaced in place* rather than forgotten — the card becomes the new session instead of going and coming back, and a launch that fails leaves the old record standing (found in review; the first version forgot the record first and lost its run log link if the launch then failed).
- **The suffix path is effectively retired.** At create the agent decides on tmux's own answer for an id nobody has a record of (live pane → refuse, dead pane → kill and reuse) rather than on whether the tick has adopted it, so `-2` is now reached only through tmux's own `duplicate session` verdict — and is then shown in the record's name.

Also landed: `previous_run` on the record; the refusal carries `holder`, `holder_state` and a `hint` as error data (`RpcError(**data)` → the envelope's `error_data` → `AgentError.data` → `ao new --json`); the name check is taken under a lock on the **scope**, because a scope spans a repo's worktrees; the Herd's Shell button and `ao shell` both send no name, so the agent names them (`shell`, `shell-2`); and every CLI subcommand resolves a bare name here, with a live session winning over an exited one and ambiguity an error rather than a guess. The lasting content is design §4.1, §4.5a's name-field row, and the `ao` skill's note on names.

**Related:** design §4.1, §4.5a (New session name field), §4.7, §9 invariant 12, §10 (2026-09-10); PR #17 (resume supersedes), TD-023 (`pane` flag), TD-029 (the Close that prompted the report).
