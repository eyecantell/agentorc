"""TD-228 slice 1, design §4.4 *Repo facts*: pickable is derived from `**Blocked by:**`, never
written, by cadence §2.4's rule — the rule dev-cadence's `scripts/ledger.py` applies — so the home
and the repo's own tool give one answer. These tests hold the two readers equal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sessionorc import ledger

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "ledger.py"


def _script(cwd: Path, reg_dir: Path, *args: str) -> dict:
    """The synced script's `--list --json`, its roster pointed at `reg_dir` alone."""
    env = {**os.environ, "DEV_CADENCE_REG_DIR": str(reg_dir)}
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--list", "--json", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    return json.loads(cp.stdout)


def _home(text: str, archive: str | None, roots: list[str]) -> dict[str, dict]:
    return {e["id"]: e for e in ledger.entries(text, archive, ledger.Registry(roots), fallback=False)}


def _same(script: dict, home: dict[str, dict]) -> None:
    got = {e["id"]: e for e in script["entries"]}
    assert set(got) == set(home)
    for tid, s in got.items():
        h = home[tid]
        assert (h["pickable"] == "yes", h["type"], h["blocked_by"]) == (s["pickable"], s["type"], s["blocked_by"]), tid


def test_the_home_derives_what_the_script_derives_on_this_ledger(tmp_path):
    """The equality test, with the fallback off: the whole record, not only the ids. Neither reader
    has a roster, so a `<repo>#TD-NNN` item keeps its block in both."""
    text = (ROOT / "docs" / "technical_debt.md").read_text(encoding="utf-8")
    archive = (ROOT / "docs" / "technical_debt_archive.md").read_text(encoding="utf-8")
    home = _home(text, archive, [])
    _same(_script(ROOT, tmp_path / "no-roster"), home)
    assert any(e["pickable"] == "yes" for e in home.values()) and any(e["pickable"] == "no" for e in home.values())


LEDGER = """# Technical Debt

<!--
## TD-001: the template
**Blocked by:** TD-999
-->

## TD-010: nothing blocks it

**Priority:** High
**Status:** Open

## TD-011: blocked by an open entry

**Priority:** Medium
**Type:** feature
**Blocked by:** TD-010

## TD-012: blocked by an archived one

**Priority:** Medium
**Blocked by:** TD-005

## TD-013: blocked by an id in neither file

**Priority:** Low
**Blocked by:** TD-777

## TD-014: waits on the person

**Priority:** Medium
**Blocked by:** decision (Paul) — docs/user_attention.md, the 2026-09-28 item, TD-010

## TD-015: an unreadable item, and one after the decision's comma

**Priority:** Medium
**Blocked by:** TD-005, soon, decision (Paul), TD-010

## TD-016: an empty line blocks nothing

**Priority:** Low
**Type:** chore
**Blocked by:**

## TD-017: another repo's entries

**Priority:** Medium
**Blocked by:** other#TD-002, other#TD-003

## TD-018: another repo's open entry, and one not on the registry

**Priority:** Medium
**Blocked by:** other#TD-003, nowhere#TD-001

## TD-2: a short id, and a Type the script reads as debt

**Type:** feature,

## TD-019: a blocker below the header, spelt as the script does not read it

**Priority:** Low

**Blocked-by:** TD-010
**Type:** `feature`
"""

ARCHIVE = """# Archive

## TD-005: done long ago

**Resolved:** 2026-09-01
"""


def _repo(path: Path, ledger_text: str, archive: str | None) -> Path:
    (path / "docs").mkdir(parents=True)
    (path / "docs" / "technical_debt.md").write_text(ledger_text, encoding="utf-8")
    if archive is not None:
        (path / "docs" / "technical_debt_archive.md").write_text(archive, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_the_rule_and_another_repos_entries_through_one_registry(tmp_path):
    """Two scratch repos and one scratch registry that both readers are pointed at: `other`'s
    TD-002 is archived there, its TD-003 open; `nowhere` is not listed."""
    here = _repo(tmp_path / "here", LEDGER, ARCHIVE)
    other = _repo(
        tmp_path / "other",
        "# Technical Debt\n\n## TD-003: open there\n\n**Priority:** Low\n",
        "# Archive\n\n## TD-002: archived there\n",
    )
    reg = tmp_path / "reg"
    reg.mkdir()
    (reg / "repos.txt").write_text(f"{here}\n{other}\n", encoding="utf-8")
    home = _home(LEDGER, ARCHIVE, [str(here), str(other)])
    _same(_script(here, reg), home)
    assert {t for t, e in home.items() if e["pickable"] == "yes"} == {"TD-010", "TD-012", "TD-016", "TD-2", "TD-019"}
    assert home["TD-2"]["type"] == home["TD-019"]["type"] == "debt", "the Type's first word, as the script splits it"
    assert home["TD-011"]["blocked_by"] == ["TD-010"] and home["TD-013"]["blocked_by"] == ["TD-777"]
    assert home["TD-014"]["blocked_by"] == ["decision (Paul)"], "what follows the decision is its pointer"
    assert home["TD-015"]["blocked_by"] == ["decision (Paul)", "'after the decision: TD-010'", "'soon'"]
    assert home["TD-017"]["blocked_by"] == ["other#TD-003"], "archived there lifts it; open there blocks"
    assert home["TD-018"]["blocked_by"] == ["other#TD-003", "nowhere#TD-001"]
    assert (home["TD-011"]["type"], home["TD-016"]["type"]) == ("feature", "debt"), "an unknown Type reads as debt"
    assert home["TD-014"]["for_page"] == "for-you", "blocked by a decision is the person's"


def test_a_name_two_checkouts_share_keeps_the_block(tmp_path):
    a = _repo(tmp_path / "a" / "other", "# L\n", "# A\n\n## TD-002: archived\n")
    b = _repo(tmp_path / "b" / "other", "# L\n", "# A\n\n## TD-002: archived\n")
    assert ledger.Registry([str(a)])("other#TD-002") == "archived"
    assert ledger.Registry([str(a), str(b)])("other#TD-002") == "unresolved"
    assert ledger.Registry([str(a)])("other#TD-009") == "unresolved", "an id in neither file"


def test_a_written_no_still_blocks_while_the_line_lasts():
    text = "## TD-020: marked\n\n**Priority:** Low\n**Pickable:** no — the live look is left\n"
    assert ledger.entries(text)[0]["pickable"] == "no"
    assert ledger.entries(text, fallback=False)[0]["pickable"] == "yes"
    yes = "## TD-021: blocked\n\n**Blocked by:** TD-020\n**Pickable:** yes\n\n## TD-020: open\n"
    assert ledger.entries(yes)[0]["pickable"] == "no", "a written yes adds nothing"


def test_a_ledger_with_no_header_lines_gives_free_pick_every_unblocked_entry():
    got = ledger.entries(LEDGER, ARCHIVE)
    matched = {e["id"] for e in got if ledger.lane_matches(["free-pick"], e)}
    assert matched == {e["id"] for e in got if not e["blocked_by"]} == {"TD-010", "TD-012", "TD-016", "TD-2", "TD-019"}
    assert not any(ledger.lane_matches(["design-first"], e) for e in got)


@pytest.mark.parametrize(
    ("kind", "owner", "lane", "matches"),
    [
        ("", "", ["free-pick"], True),
        ("build", "grinder", ["free-pick", "owner:grinder"], True),
        ("live-check", "", ["free-pick"], False),
        ("evaluation", "", ["free-pick"], False),
        ("design-first", "designer", ["design-first", "owner:designer"], True),
        ("design-first", "", ["free-pick"], False),
        ("build", "anchor", ["free-pick", "owner:grinder"], False),
    ],
)
def test_lane_words_read_the_derived_field_with_build_for_no_kind(kind, owner, lane, matches):
    head = "".join(f"**{k}:** {v}\n" for k, v in (("Kind", kind), ("Owner", owner)) if v)
    e = ledger.entries(f"## TD-030: x\n\n{head}")[0]
    assert ledger.lane_matches(lane, e) is matches


def test_the_page_kinds_in_order():
    def kind(block: str) -> str:
        return ledger.entries(f"## TD-040: x\n\n{block}\n\n## TD-041: open\n")[0]["for_page"]

    assert kind("**Owner:** paul") == "for-you"
    assert kind("**Kind:** design-first\n**Blocked by:** decision (Paul)") == "for-you"
    assert kind("**Kind:** design-first\n**Blocked by:** TD-041") == "design-first"
    assert kind("**Kind:** build") == "pickable" and kind("") == "pickable"
    assert kind("**Kind:** build\n**Blocked by:** TD-041") == "other"
    assert kind("**Kind:** live-check") == "other"


def test_the_archive_is_read_beside_the_ledger(tmp_path):
    from datetime import UTC, datetime

    _repo(tmp_path / "r", LEDGER, ARCHIVE)
    got = {e["id"]: e for e in ledger.reading(tmp_path / "r", datetime.now(UTC), with_history=False)["entries"]}
    assert got["TD-012"]["pickable"] == "yes", "its blocker is archived beside it"
    assert ledger.archive_path("docs/technical_debt.md") == "docs/technical_debt_archive.md"
    assert ledger.archive_path("notes/ledger.md") == "notes/ledger_archive.md"
