

## TD-505: `test_another_tools_command_is_not_read` never reaches the kill guard: it asserts `translate`'s event name, which the guard does not touch

**Priority:** Low
**Type:** debt
**Added:** 2026-10-09 (test-audit-ao-1)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `tests/test_kill_guard.py` (`test_another_tools_command_is_not_read`), `src/agentorc/adapters/claude_code/hook.py` (`refused_command`)

**Why:** The test-audit of #1395. The test builds `bash("x")` with `tool_name: "Read"` and asserts `translate(...)["event"] == "PreToolUse:Read"` — `translate` knows nothing of `refused_command`, so the test passes whatever the guard does. Probe: `refused_command`'s `or payload.get("tool_name") != "Bash"` replaced with `or False` (every tool's `command` read), `pytest -q tests/test_kill_guard.py` — **52 passed**. The name promises that a non-`Bash` tool's command is not read; nothing asserts it.

**Fix:** Call `refused_command` (or `run_hook`) with `{**bash('pkill x'), 'tool_name': 'Read'}` and assert None / no stdout; confirm by the probe above.

**Done when** the probe in the Why fails the test.

**Related:** TD-496 (#1395).

**Resolved:** 2026-10-09 (PR #1434) — `tests/test_kill_guard.py::test_another_tools_command_is_not_read` gives a `Read` input carrying `pkill -f sleep` to `refused_command` (None) and to the hook through `run_hook` (no stdout); the probe in the Why now fails it.
