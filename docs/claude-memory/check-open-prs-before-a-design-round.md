# Check open PRs before a design round

**2026-09-25 (Paul).** Before designing a ledger entry — in a cloud session or anywhere — list the repo's
open PRs for its id (`gh pr list --search TD-NNN`, or the GitHub search): the designer on kmaster works
a stack of design PRs that can sit open for hours, and a parked entry on the board says nothing about a
PR that already exists. On 2026-09-24 a cloud session designed TD-127 and TD-126 while the designer's
#532 and #531 for the same entries were open and reviewed; the designs mostly agreed, but one merged
over the other and the rest had to be reconciled by hand. When a PR exists, compare against it first
and write the round as a reconciliation — the differences and what was taken from it — not a second
design. (Related: *Check in-flight work before a TD step*, which says the same of build steps.)

**2026-10-04 (the designer, own miss).** It applies to the designer's own new run too: a run that ended
with `restart` leaves its design PRs open on a steer, and `main`'s ledger still reads *Open* for those
entries. A fresh run claimed TD-092 and wrote half a second design before `gh pr list` showed #1035.
So the first read of a run includes `gh pr list --state open` and each PR's last comment (the handoff),
**before** `ao progress claim`; `ao status -v`'s *waiting on you: TD-NNN until …* on the run's own
record names the same entries.
