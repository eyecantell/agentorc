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
SPLIT = ("common", "cards", "inbox")


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


def _bare_reads(tree: ast.Module) -> list[str]:
    """Reads of a patched name that would dodge the patch. A **call** of one (`rpc(...)`) is always
    one, whatever else the function assigns; a plain read is exempt only where the name is a local of
    that function or of one enclosing it (`rpc = str(...)` in an alarm's words is a string, not the
    RPC). Order in the body is not tracked, which is why the exemption stops short of calls."""
    out: list[str] = []

    def visit(node: ast.AST, local: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                own = {a.arg for a in ast.walk(child.args) if isinstance(a, ast.arg)}
                own |= {n.id for n in ast.walk(child) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                visit(child, local | own)
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in PATCHED:
                out.append(child.func.id)
            elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) and child.id in PATCHED - local:
                out.append(child.id)
            visit(child, local)

    visit(tree, frozenset())
    return out


@pytest.mark.parametrize("name", SPLIT)
def test_no_split_module_reads_a_patched_name_bare(name):
    mod = importlib.import_module(f"agentorc.ui.{name}")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            bare = {a.asname or a.name for a in node.names} & PATCHED
            assert not bare, f"ui/{name}.py imports {sorted(bare)} by name: read them as app.X at call time"
    bare = _bare_reads(tree)
    assert not bare, f"ui/{name}.py reads {sorted(set(bare))} bare: read them as app.X at call time"


def test_the_guard_sees_a_bare_read_and_passes_a_local():
    assert set(_bare_reads(ast.parse("def f():\n    return rpc('x')\n"))) == {"rpc"}
    assert _bare_reads(ast.parse("def f(a):\n    rpc = str(a)\n    return rpc\n")) == []
    # a local in one branch does not hide a call in another, and a closure sees its outer local
    assert set(_bare_reads(ast.parse("def f(a):\n    if a:\n        return rpc('x')\n    rpc = 1\n"))) == {"rpc"}
    assert _bare_reads(ast.parse("def f(a):\n    rpc = str(a)\n    return lambda: rpc\n")) == []
    assert set(_bare_reads(ast.parse("x = [rpc]\n"))) == {"rpc"}


@pytest.mark.parametrize("name", SPLIT)
def test_the_app_re_exports_every_name_a_split_module_defines(name):
    from agentorc.ui import app

    mod = importlib.import_module(f"agentorc.ui.{name}")
    missing = sorted(n for n in _defined(Path(mod.__file__)) if getattr(app, n, None) is not getattr(mod, n))
    assert not missing, f"agentorc.ui.app no longer re-exports {missing} from ui/{name}.py"
