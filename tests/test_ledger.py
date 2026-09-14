"""The ledger's own rules, checked against the two files that state them.

docs/technical_debt.md says IDs are "assigned in order and never reused", and that its summary
table "lists exactly the entries that have a body in this file". Both were broken on 2026-09-13:
TD-043 was assigned twice — once for a tmux paste-buffer race that was fixed and archived, and
once, hours later and by a different session, for a vocabulary entry — because nothing reads the
two files together. A concurrent fleet re-reads a resolved entry's number all the time; these
tests are the check the prose asked for.
"""

import pathlib
import re
from collections import Counter

DOCS = pathlib.Path(__file__).parents[1] / "docs"
OPEN = DOCS / "technical_debt.md"
ARCHIVE = DOCS / "technical_debt_archive.md"

HEADING = re.compile(r"^## (TD-\d{3}):", re.M)
ROW = re.compile(r"^\| (TD-\d{3}) \|", re.M)


def text(path):
    # The entry template lives in an HTML comment and is headed TD-001, which is a real id.
    return re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.S)


def headings(path):
    return HEADING.findall(text(path))


def entries(path):
    """(id, body) for each entry, the body running to the next heading."""
    t = text(path)
    return list(zip(HEADING.findall(t), re.split(r"^## TD-\d{3}:", t, flags=re.M)[1:], strict=True))


def test_no_id_is_used_twice_anywhere_in_the_ledger():
    both = headings(OPEN) + headings(ARCHIVE)
    repeated = sorted(id_ for id_, n in Counter(both).items() if n > 1)
    assert not repeated, f"TD ids are never reused, but these have two entries: {repeated}"


def test_an_open_entry_and_an_archived_one_never_share_a_number():
    # The dangerous half of a reuse: the archive's copy is what a merged PR, a test docstring and
    # a board line point at, so a new entry taking that number silently redirects all of them.
    clash = sorted(set(headings(OPEN)) & set(headings(ARCHIVE)))
    assert not clash, f"resolved and open entries share a number: {clash}"


def test_the_summary_table_lists_exactly_the_entries_with_a_body():
    table = OPEN.read_text().split("<!-- Entry template:")[0]
    rows, bodies = set(ROW.findall(table)), set(headings(OPEN))
    assert not rows - bodies, f"summary rows with no entry below them: {sorted(rows - bodies)}"
    assert not bodies - rows, f"entries missing a summary row: {sorted(bodies - rows)}"


def test_ids_are_assigned_in_order_within_each_file():
    for path in (OPEN, ARCHIVE):
        ids = headings(path)
        assert ids, f"{path.name} has no entries"
        # The archive is append-ordered by resolution, not by id; only the open file is sorted.
        if path is OPEN:
            assert ids == sorted(ids), f"{path.name} entries are out of id order: {ids}"


def test_every_entry_has_a_body_and_not_someone_elses():
    """The failure that hid the reuse: an archived entry can lose its body entirely.

    Four entries (TD-007, TD-009, TD-012, TD-013) had their four bodies stacked under the last
    heading, and TD-015 and TD-016 were archived with TD-017's body pasted under their titles —
    so the archive claimed `send --wait` was a seen-state feature. A heading with no fields of
    its own is the shape both bugs take.
    """
    for path in (OPEN, ARCHIVE):
        for id_, body in entries(path):
            assert "**Priority:**" in body, f"{path.name} {id_} has a heading but no entry under it"
            assert "**Why:**" in body, f"{path.name} {id_} has no **Why:**"
        for id_, body in entries(ARCHIVE):
            assert "**Resolved:**" in body, f"an archived entry records how it was resolved: {id_}"


def test_no_two_entries_share_a_body():
    """Corruption #3 in isolation: a well-formed body pasted under the wrong title.

    TD-015 and TD-016 were each archived with TD-017's seen-state body beneath them. Every entry
    was then structurally perfect — a Priority, a Why, a Resolved — and the archive still said
    `send --wait` was a phone-triage seen mark. Nothing about the *shape* of such an entry is
    wrong, so the signal is that two ids carry the same prose.
    """
    for path in (OPEN, ARCHIVE):
        whys: dict[str, str] = {}
        for id_, body in entries(path):
            why = body[body.index("**Why:**") :].split("\n\n")[0].strip() if "**Why:**" in body else ""
            if why and why in whys.values():
                other = next(k for k, v in whys.items() if v == why)
                raise AssertionError(
                    f"{path.name}: {id_} and {other} have the same **Why:** — one is under the wrong title"
                )
            whys[id_] = why


def test_no_entry_body_dangles_below_the_entry_it_belongs_to():
    """The other half of the stacking bug, and what a bad repair leaves behind: a `Location`/`Why`/
    `Resolved` block with no heading over it, adopted by whichever entry happens to sit above it.

    Two of these had been hanging off TD-001 since 2026-09-09 — they were TD-015's and TD-016's
    real bodies — and reading the file top to bottom does not reveal them, because they look like
    a continuation of the entry above.
    """
    for path in (OPEN, ARCHIVE):
        for id_, body in entries(path):
            # Anchored to the line start: an entry may legitimately name these fields in its prose.
            for fld in ("Location", "Why"):
                n = len(re.findall(rf"^\*\*{fld}:\*\*", body, re.M))
                assert n <= 1, f"{path.name}: {id_} has {n} {fld} lines — an unheaded entry is dangling below it"
