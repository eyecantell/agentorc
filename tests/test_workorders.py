"""Work orders (design §4.4 *Board write-back*, §6 rules 6 and 8, TD-380; built — TD-384): the
board's open decided lines, read by the repo's own reader, as `board:<key>` in the lanes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sessionorc import ledger as ledger_mod
from sessionorc import workorders
from sessionorc.models import Session

READER = Path(__file__).resolve().parent.parent / "scripts" / "nudge_user_attention.py"
FORMAT = (
    "Format: `- [ ] YYYY-MM-DD (session <first-8-of-session-uuid> on <host>, or n/a) — what's needed."
    " Context: TD-NNN / PR #N / branch. Due: YYYY-MM-DD.`"
)
DECIDED = (
    "- [ ] decide 2026-10-01 (session grinder-ao-1 on kmaster) — **Keep the nightly backup?** Two copies"
    " cost space. Context: TD-777. Due: 2026-10-02. Answers: keep | drop. Decided: keep (2026-10-03)."
)
OPEN = "- [ ] act 2026-10-01 (session n/a) — **Rotate the token.** Context: TD-778. Due: 2026-10-02."
FYI = "- [ ] fyi 2026-10-01 (session n/a) — **The mirror moved.** Due: 2026-10-02. Decided: seen (2026-10-03)."


def _repo(tmp_path: Path, *lines: str) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy2(READER, root / "scripts" / "nudge_user_attention.py")
    board = ["# User Attention Board", "", FORMAT, "", "## Needs the user", "", *lines, ""]
    (root / "docs" / "user_attention.md").write_text("\n".join(board), encoding="utf-8")
    return root


@pytest.mark.unit
def test_a_decided_line_is_a_work_order_and_an_open_or_fyi_line_is_not(tmp_path):
    got = workorders.read(_repo(tmp_path, DECIDED, OPEN, FYI), fetch=False)
    assert "error" not in got
    (o,) = got["orders"]
    assert workorders.is_work_order(o["id"]) and o["title"] == "Keep the nightly backup?"
    assert o["decided"] == {"text": "keep", "date": "2026-10-03"} and "TD-777" in o["refs"]
    e = workorders.entry(o)
    assert (e["priority"], e["for_page"], e["pickable"], e["work_order"]) == ("High", "pickable", "yes", True)


@pytest.mark.unit
def test_the_key_survives_a_snooze_a_reply_and_the_decide_and_is_the_readers_item_key(tmp_path):
    """`board:<key>` is the first eight hex of a sha256 of the line's `item_key()` — the reader's own,
    loaded from its file — so the tick's next read names the same line the same way."""
    first = workorders.read(_repo(tmp_path / "a", DECIDED), fetch=False)["orders"][0]["id"]
    moved = DECIDED.replace("Due: 2026-10-02", "Due: 2026-10-09").replace(
        ". Answers:", " — paul, 2026-10-03: keep both. Answers:"
    )
    again = workorders.read(_repo(tmp_path / "b", moved), fetch=False)["orders"][0]["id"]
    assert first == again
    other = workorders.read(_repo(tmp_path / "c", DECIDED.replace("nightly", "weekly")), fetch=False)
    assert other["orders"][0]["id"] != first


@pytest.mark.unit
def test_no_board_or_no_reader_is_no_work_orders_and_a_failed_read_says_so(tmp_path):
    assert workorders.read(tmp_path, fetch=False) == {"orders": []}
    root = _repo(tmp_path, DECIDED)
    (root / "scripts" / "nudge_user_attention.py").write_text("import sys\nsys.exit(3)\n")
    assert "error" in workorders.read(root, fetch=False)


@pytest.mark.unit
def test_a_work_order_is_in_every_free_pick_lane_whatever_its_owner_words_and_in_no_other():
    e = workorders.entry({"id": "board:0123abcd", "title": "t", "decided": {"text": "keep", "date": "2026-10-03"}})
    assert ledger_mod.lane_matches(["free-pick"], e)
    assert ledger_mod.lane_matches(["free-pick", "owner:grinder"], e)
    assert not ledger_mod.lane_matches(["design-first"], e)
    assert not ledger_mod.lane_matches(["owner:grinder"], e)
    assert not ledger_mod.lane_matches(["free-pick"], {**e, "pickable": "no"})


@pytest.mark.unit
def test_rule_eight_counts_a_work_order_as_a_gain():
    from sessionorc import work as work_mod

    m = Session(id="ao-g1", name="g1", kind="interactive", adapter="shell", dir="/tmp", lane=["free-pick"])
    m.lane_seen = {"at": "2026-10-07T00:00:00Z", "ids": ["TD-001"]}
    order = workorders.entry({"id": "board:0123abcd", "title": "t"})
    assert work_mod.gained(m, [{"id": "TD-001", "pickable": "yes", "kind": "build"}, order]) == ["board:0123abcd"]


@pytest.mark.unit
def test_the_fetch_read_falls_back_to_a_plain_one(tmp_path, monkeypatch):
    """No origin to fetch (a scratch repo): the reader's `--fetch` read is answered or not, and a
    plain read follows when it is not — the line is listed either way."""
    root = _repo(tmp_path, DECIDED)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    assert len(workorders.read(root)["orders"]) == 1


@pytest.mark.unit
def test_the_repo_reading_lists_them_on_a_full_read_and_keeps_them_between(tmp_path, monkeypatch):
    """`_read_repo` reads the board with the PRs, every `REPOS_EVERY`; a ledger-only re-read keeps
    the last orders, and a failed board read keeps them with the error beside them."""
    from sessionorc import reports
    from sessionorc.agent_tick import TickMixin

    monkeypatch.setattr(reports, "pr_reading", lambda root, now: {"open": []})
    root = _repo(tmp_path, DECIDED, OPEN)
    full = TickMixin._read_repos([str(root)], {}, {str(root)})[str(root)]
    (o,) = full["work_orders"]["orders"]
    assert o["work_order"] and o["title"] == "Keep the nightly backup?"
    again = TickMixin._read_repos([str(root)], {str(root): full}, set())[str(root)]
    assert again["work_orders"] == full["work_orders"]
    monkeypatch.setattr(workorders, "read", lambda root, fetch=True: {"error": "the reader failed"})
    failed = TickMixin._read_repos([str(root)], {str(root): full}, {str(root)})[str(root)]
    assert failed["work_orders"]["orders"] == full["work_orders"]["orders"]
    assert failed["work_orders"]["error"] == "the reader failed"
