

## TD-461: The Inbox rail's find count keeps its line while empty (TD-427) and no design line says so

**Priority:** Low
**Type:** debt
**Added:** 2026-10-09 (docs-audit-ao-1, auditing #1305–#1317)
**Owner:** grinder
**Kind:** build
**Status:** Resolved
**Location:** design §4.5 screen 6 *The rail* (`docs/design/4.5-ui.md`, the paragraph at ~L840 on how counts are written), §4.5a's **Inbox page: the rail** row, `docs/design-history.md` under §4.5; `src/agentorc/ui/static/app.css` (`.rail #findn`)

**Why:**
- #1308 (TD-427) added `.rail #findn { min-height: 1lh; }`, so the find count holds a line of its own while empty and the first typed word moves no toggle. It is a behaviour of the rail, and TD-427's own Fix says "Design §4.5 screen 6 *The rail* names the count; if its place changes, the design says so in the same PR".
- The PR touched no design file. `grep -n "findn\|keeps its line\|1lh" docs/design/4.5-ui.md docs/design/4.5a-controls.md` finds nothing; §4.5 names only the count's text (*n of all*, L843 and L879). TD-423, the same kind of change for **Clear filters**, wrote "always in place … disabled while nothing is picked or typed" into §4.5 and §4.5a (#1307).
- `docs/design-history.md` has no TD-427 line either.

**Fix:** one sentence in §4.5 screen 6 *The rail* and in §4.5a's rail row, in the present tense: the find count holds its line while empty, so typing moves no toggle. The dated fact (TD-427, 2026-10-08) goes to `docs/design-history.md` under §4.5. **Done when** both design files say it and the history line exists.

**Related:** TD-427 (archived), TD-423 (the model for the wording), TD-455.

**Resolved:** 2026-10-09 (PR #1421) — §4.5 screen 6 *Find* and §4.5a's **Inbox page: find** row (the row that names the count) say the count holds its line while empty, so the first word typed moves no toggle; `docs/design-history.md` carries the TD-427 line under §4.5 and under §4.5a, each beside TD-423's.
