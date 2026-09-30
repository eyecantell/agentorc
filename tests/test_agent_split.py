"""TD-108 step 1: the host agent split into mixin modules over one state object. A guard on the one
hazard the split carries: the constants the tests patch are read through `agent_common` at call
time, never imported by name — an imported copy would not see a test's patch, and the suite could
still pass while testing the default."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import sessionorc

pytestmark = pytest.mark.unit

# patched by the suite as `sessionorc.agent_common.X`
PATCHED = {
    "TICK_SECONDS", "USAGE_FRESH", "SUBMIT_SECONDS", "CREATE_GRACE", "WRAPUP_GRACE", "SEND_STALL_SECONDS",
    "REPORT_WRITE", "CLOSED_KEEP", "TRAIL_KEEP", "ID_RECHECK", "RESTART_SETTLE",
}  # fmt: skip


def test_no_agent_module_imports_a_patched_constant_by_name():
    here = Path(sessionorc.__file__).parent
    for f in sorted(here.glob("agent*.py")):
        if f.name == "agent_common.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sessionorc.agent_common":
                bare = {a.name for a in node.names} & PATCHED
                if f.name == "agent.py":
                    continue  # its re-export block: a copy nothing in the agent reads (they read agent_common.X)
                assert not bare, f"{f.name} imports {sorted(bare)} by name: read them as agent_common.X"
            if isinstance(node, ast.Name) and node.id in PATCHED:
                raise AssertionError(f"{f.name} reads {node.id} bare: read it as agent_common.{node.id}")


def test_the_agent_re_exports_every_name_agent_common_defines():
    """`from sessionorc.agent import X` keeps working for every name the split moved (TD-108): the
    re-export block is the contract, whatever `agent.py` itself still reads after a section moves."""
    from sessionorc import agent, agent_common

    tree = ast.parse(Path(agent_common.__file__).read_text(encoding="utf-8"))
    defined = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined |= {x.id for t in node.targets for x in ast.walk(t) if isinstance(x, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    missing = sorted(n for n in defined if getattr(agent, n, None) is not getattr(agent_common, n))
    assert not missing, f"sessionorc.agent no longer re-exports {missing}"
