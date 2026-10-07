"""The design, read the way the doc-bound tests read it (TD-346, built by TD-351).

The design is the files under `docs/design/`, one per section and one per subsection of §4, and
`docs/design.md` is their index. A test that binds to the design's words reads the one section it
binds to with `section("4.5a")`, or every section in index order with `whole()`; the order is the
index's, so a file the index does not link is not read, and a test below says no such file exists."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).parents[1]
INDEX = ROOT / "docs" / "design.md"
DIR = ROOT / "docs" / "design"


def files() -> list[pathlib.Path]:
    """The section files, in the index's order (`- **§4.5a** [Controls](design/4.5a-controls.md) — …`)."""
    names = re.findall(r"^- \*\*§[\w.]+\*\* \[.+?\]\(design/([^)]+\.md)\)", INDEX.read_text(encoding="utf-8"), re.M)
    return [DIR / n for n in names]


def section(num: str) -> str:
    """The text of the file holding §`num` (`"4.5a"`, `"9"`), its heading line first."""
    (path,) = [p for p in files() if p.name.split("-", 1)[0] == num]
    return path.read_text(encoding="utf-8")


def whole() -> str:
    """Every section file in index order, joined: the design as one text, without the index."""
    return "".join(p.read_text(encoding="utf-8") for p in files())
