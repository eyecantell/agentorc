"""The person-only gate (design §4.8 *A person's own act is a named RPC, and the gate is one list*,
TD-317): `mail.PERSON_ONLY` and the RPCs that begin with `agent_common.person_only` are held to each
other in both directions, so a new person's act is one name added and cannot be forgotten."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import pytest

from sessionorc import mail
from sessionorc.agent import HostAgent
from sessionorc.agent_common import RpcError

# what each RPC's first statement may call to make the check: the check, or one of the two
# helpers that make it and keep what else each does (the entry lookup, *runs at the home*)
CHECKS = {"agent_common.person_only", "self._person_entry", "self._promote_person"}


def _first_call(fn) -> str | None:
    """The dotted name the RPC's first statement (past its docstring) calls, or None."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    st = body[0] if body else None
    value = st.value if isinstance(st, (ast.Expr, ast.Assign)) else None
    if isinstance(value, ast.Await):
        value = value.value
    return ast.unparse(value.func) if isinstance(value, ast.Call) else None


def _rpcs() -> dict[str, object]:
    return {
        name[len("rpc_") :]: fn
        for name, fn in inspect.getmembers(HostAgent, inspect.isfunction)
        if name.startswith("rpc_")
    }


def test_every_name_in_the_list_is_an_rpc_that_refuses_a_session_before_reading_its_parameters():
    """Every name is an `rpc_` method, and called by a session with every other parameter left
    `None` it is refused in the one shape — so nothing it reads is reached first. The agent is
    never constructed: a check that touched `self` before refusing would fail here too."""
    rpcs = _rpcs()
    assert len(mail.PERSON_ONLY) == 29
    assert set(rpcs) >= mail.PERSON_ONLY, sorted(mail.PERSON_ONLY - set(rpcs))
    bare = HostAgent.__new__(HostAgent)
    for name in sorted(mail.PERSON_ONLY):
        fn = rpcs[name]
        params = [p for p in inspect.signature(fn).parameters if p not in ("self", "caller")]
        with pytest.raises(RpcError) as e:
            call = getattr(bare, f"rpc_{name}")(**dict.fromkeys(params), caller="ao-repo-worker")
            if inspect.iscoroutine(call):
                asyncio.run(call)
        msg = str(e.value)
        assert msg.startswith("ao-repo-worker cannot "), (name, msg)
        assert ": a person's own act, refused to every session (design §" in msg, (name, msg)


def test_every_rpc_that_makes_the_check_is_in_the_list():
    """Read from the source: an RPC whose first statement is the check, or one of its two helpers,
    is in `PERSON_ONLY` — and every name in the list begins that way."""
    gated = {name for name, fn in _rpcs().items() if _first_call(fn) in CHECKS}
    assert gated == mail.PERSON_ONLY, {
        "checked, not listed": gated - mail.PERSON_ONLY,
        "listed, not checked": mail.PERSON_ONLY - gated,
    }


def test_the_lists_neighbours_keep_their_own_rules():
    """§4.8 *What is not in the list*: an RPC a person or a grant holder may call, the acting RPCs,
    and the RPCs with one person's branch keep their own checks."""
    assert not mail.PERSON_ONLY & {"host_files", "inbox", "msg", "decide", "wait"}
    assert not mail.PERSON_ONLY & mail.ACTING_RPCS


def test_a_person_passes_the_check():
    from sessionorc.agent_common import person_only

    assert person_only(None, "restart a session", "§6 rule 2") is None
    with pytest.raises(RpcError, match=r"^0 cannot restart a session: a person's own act"):
        person_only(0, "restart a session", "§6 rule 2")  # any caller present is a session (§4.8a)
