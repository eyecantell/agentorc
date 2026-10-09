

## TD-476: PR #1342's tests do not pin the facet rule's `answers`/`asked` arm, or the Repo page drawing all three facets

**Priority:** Medium
**Type:** debt
**Added:** 2026-10-09 (test-audit-ao-1, auditing the tests of the last 10 merged PRs: PR #1342)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** `src/agentorc/ui/org.py` `drawn_facets` (the `face` key), `src/agentorc/ui/app.py` (`summary = {**first["summary"], "drawn": list(FACETS)}`); `tests/test_ui_team_summary.py`, `tests/test_ui_org.py`

**Why:** Mutation probes over `tests/test_ui.py tests/test_ui_org.py tests/test_ui_team_summary.py tests/test_ui_teams.py tests/test_ui_work_row.py` (195 pass at baseline): (a) `"face": bool(summary.get("doing"))` — dropping `or summary.get("answers") or summary.get("asked")` — still 195 passed, though the docstring says a stopped team draws the facet for "a Doing row (or an answer or an ask)"; (b) replacing the Repo page's `{**first["summary"], "drawn": list(FACETS)}` with `first["summary"]` still 195 passed, though the comment says "the Repo page draws all three facets, whatever a stopped team's card leaves out". The other changes of the PR (`lone`, `"repo"`, `if not live`, `"0m"`, the `drawn` gate in the render) each fail a test.

**Resolved:** 2026-10-09 (PR #1374) — `tests/test_ui_team_summary.py::test_an_answer_or_an_ask_alone_draws_the_face_facet` and `tests/test_ui_repo_page.py::test_the_repo_page_draws_all_three_facets_for_a_stopped_team_that_holds_nothing`; mutations (a) and (b) each fail one test.

**Related:** PR #1342.
