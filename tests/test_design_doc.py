"""The design's files and its index agree (TD-346, built by TD-351): every file under
`docs/design/` begins with the heading of the section it is named for, and the index links every
file once — so `section()` and `whole()` in `tests/design_doc.py` read the whole design."""

from __future__ import annotations

import re

import pytest
from design_doc import DIR, INDEX, files, section, whole

pytestmark = pytest.mark.unit


def test_every_file_begins_with_the_heading_it_is_named_for():
    for path in DIR.glob("*.md"):
        num = path.name.split("-", 1)[0]
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        assert re.match(rf"^#{{2,3}} {re.escape(num)}[. ]", first), f"{path.name} begins {first!r}"


def test_the_index_links_every_file_once():
    linked = [p.name for p in files()]
    assert len(linked) == len(set(linked)), f"linked twice: {sorted({n for n in linked if linked.count(n) > 1})}"
    assert sorted(linked) == sorted(p.name for p in DIR.glob("*.md"))
    assert not re.search(r"^##", INDEX.read_text(encoding="utf-8"), re.M), "the index holds a section heading"


def test_section_and_whole_read_the_files():
    assert section("4.5a").startswith("### 4.5a Controls")
    assert section("9").startswith("## 9. Invariants")
    assert whole().startswith("## 1. Problem") and section("4.9c") in whole()
