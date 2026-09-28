"""TD-196: `ui/app.py` split into modules by page, each re-exported from the app. A guard on the one
hazard the split carries: the suite patches a few names on `agentorc.ui.app`, and a moved function
that read one of them bare would read its own module's copy, which no patch reaches — the suite
could still pass while testing the real client. So a split module reads a patched name through the
app at call time, never bare; and the app keeps re-exporting every name a split module defines."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# patched by the suite as `agentorc.ui.app.X`
PATCHED = {"LocalClient", "read_boards", "rpc", "PtySession", "repo_teams"}
# the modules moved out of app.py (TD-196), in the order they were split
SPLIT = ("common",)


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


@pytest.mark.parametrize("name", SPLIT)
def test_no_split_module_reads_a_patched_name_bare(name):
    mod = importlib.import_module(f"agentorc.ui.{name}")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            bare = {a.asname or a.name for a in node.names} & PATCHED
            assert not bare, f"ui/{name}.py imports {sorted(bare)} by name: read them as app.X at call time"
        if isinstance(node, ast.Name) and node.id in PATCHED:
            raise AssertionError(f"ui/{name}.py reads {node.id} bare: read it as app.{node.id} at call time")


@pytest.mark.parametrize("name", SPLIT)
def test_the_app_re_exports_every_name_a_split_module_defines(name):
    from agentorc.ui import app

    mod = importlib.import_module(f"agentorc.ui.{name}")
    missing = sorted(n for n in _defined(Path(mod.__file__)) if getattr(app, n, None) is not getattr(mod, n))
    assert not missing, f"agentorc.ui.app no longer re-exports {missing} from ui/{name}.py"
