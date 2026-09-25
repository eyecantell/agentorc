# How a design round runs from a cloud session

**Written 2026-09-25** after four rounds in one session (TD-129, TD-127, TD-126, TD-130, TD-100 (4)),
so the next cloud session starts without the lessons costing again. Everything below is what
Paul settled; the repo's own rules (CLAUDE.md, the designer brief) still apply.

**Before designing anything**: list the repo's open PRs for the entry's id (GitHub search) — the
designer on kmaster works a stack of design PRs that can sit open for hours. If one exists,
compare and reconcile; never design twice ([[check-open-prs-before-a-design-round]]).

**Park it first.** A cloud session cannot claim a lease (`ao progress claim` needs a session on
kmaster), so the designer would pick the same entry. Push one commit that adds a board line —
`- [ ] <date> (session <id> on claude.ai/code) — **Held: TD-NNN … is being designed in a cloud
session at Paul's ask — not by the designer.** … Due: <+3 days>.` — and a clause at the front of
the entry's Status, open the PR, and ask Paul to merge it before the design starts — **and check that it merged
before proposing anything**: the designer reads the board on `main`, so an open park PR parks
nothing ([[a-park-is-a-merged-pr]]; TD-128 was designed twice on 2026-09-25 this way). The design PR
takes the line off. The designer's brief honours a board park; a mail from Paul to the designer
covers the current run only.

**The round.** Read the entry, the design sections it names and the code it names (entries lag
reality). Propose in chat first — Paul pushes back fast and decides fast; his answers are the
design's dated decisions and go into the history verbatim. Then write: design §s in the present
tense, every control in §4.5a, glossary if a word changes, `docs/mockups/gen.py` regenerated with
the artboards rendered (headless Chromium: `/opt/pw-browsers/chromium --headless=new
--screenshot` on the `<x-dc>` body pulled out of the artboard; see the session that built
`InboxRail`), history bullets per section, the entry's Status → designed, build entries
`Owner: grinder / Kind: build / Pickable: yes` with a *Done when*, the board line off.
`PYTHONPATH=src python3 -m pytest -q tests/test_ledger.py tests/test_primer.py` (pytest via
`pip install pytest pyyaml`; pdm is absent) before every push.

**Numbers move under you.** A TD number can be taken on main while the branch is open, and a
regex renumber flips pre-existing citations too (an archived TD-145 became TD-146 once). Check
`docs/technical_debt_archive.md` as well as the open ledger, and renumber by hand.

**Review.** For a plan or a design with decisions in it, run Sonnet rounds (a fresh agent each
round, told what earlier rounds verified) until READY, recording each round in the ADR or the
entry ([[design-review-loop-with-sonnet]]); round 1 usually finds the one hole that matters.
For every PR, run a Sonnet fact-check against the repo and post it on the PR as the evidence
comment, first line `cadence-review: SHIP|FIXED|BLOCK · claude-sonnet · docs · n findings`, the
findings verbatim with what was done. The techlead reads and merges from kmaster.

**One branch.** The session pushes only its own `claude/*` branch; after a merge, restart it from
`origin/main` (`git checkout -B <branch> origin/main`). A new round on the same branch joins the
open PR — say so in the PR body, or wait for the merge.

**Paul's standing preferences from these rounds**: show options with mockups before building
(TD-129's rule); every count on a page is a count of rows on the page now; a message to him is
read cold, first paragraph the whole of it (TD-127); the reserves are what he moves weekly.
