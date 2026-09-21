"""The techlead's primer (design §4.9b, TD-075 step 1b) is an index: a techlead reads it cold and
follows its pointers, so a pointer that leads nowhere misdirects every answer after it. This holds
the section numbers, the paths and the ledger ids it names to ones that exist — it cannot hold the
prose to the truth, which is why the primer is never cited as a source."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIMER = REPO / "docs" / "briefs" / "techlead-context.md"


def _text() -> str:
    return PRIMER.read_text(encoding="utf-8")


def test_every_design_section_the_primer_names_exists():
    design = (REPO / "docs" / "design.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^#{2,3} (\d+(?:\.\d+[a-z]?)?)[. ]", design, flags=re.M))
    named = set(re.findall(r"§(\d+(?:\.\d+[a-z]?)?)", _text()))
    assert named, "the primer names no section at all"
    assert not named - headings, f"sections the design does not have: {sorted(named - headings)}"


def test_every_invariant_the_primer_names_exists():
    design = (REPO / "docs" / "design.md").read_text(encoding="utf-8")
    body = design[design.index("## 9. Invariants") : design.index("## 10. ")]
    have = set(re.findall(r"^(\d+)\. ", body, flags=re.M))
    named = set(re.findall(r"invariant (\d+)", _text()))
    assert named and not named - have, f"invariants the design does not have: {sorted(named - have)}"


def test_every_path_the_primer_names_exists():
    paths = {
        p
        for p in re.findall(r"`((?:docs|src|scripts|tests|\.claude)/[^`<>* ]+)`", _text())
        if "<" not in p and "NNN" not in p
    }
    assert paths
    missing = sorted(p for p in paths if not (REPO / p).exists())
    assert not missing, f"paths that do not exist: {missing}"


def test_every_ledger_entry_the_primer_names_exists():
    ledger = (REPO / "docs" / "technical_debt.md").read_text(encoding="utf-8")
    archive = (REPO / "docs" / "technical_debt_archive.md").read_text(encoding="utf-8")
    have = set(re.findall(r"^## (TD-\d+)", ledger + "\n" + archive, flags=re.M))
    named = set(re.findall(r"\bTD-\d+\b", _text()))
    assert named and not named - have, f"ledger entries that do not exist: {sorted(named - have)}"
