"""TD-265 slice 1, design §4.5 *The card's anatomy* row 5 (b): a closed record says who closed it —
`closer: {by, why, at}`, `by` the person, the calling session's id, or the tick with its rule's word —
written by `rpc_close` from the envelope's caller, never from anything a session puts in its params."""

from __future__ import annotations

from conftest import park_ticks

from sessionorc.client import LocalClient


async def _shell(client: LocalClient, name: str, tmp_path, **kw) -> str:
    d = tmp_path / name
    d.mkdir()
    rec = await client.call("create", name=name, dir=str(d), adapter="shell", argv=["bash", "--norc"], **kw)
    return rec["id"]


async def test_a_persons_close_reads_person_and_carries_the_instant(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        a = await _shell(person, "a", tmp_path)
        got = await person.call("close", id=a)
    assert got["closer"] == {"by": "person", "why": None, "at": got["closed_at"]}


async def test_a_sessions_close_names_it_and_its_own_reads_itself(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        mgr = await _shell(person, "mgr", tmp_path, unattended=True)
        await person.call("set_grants", id=mgr, add=["control"])
    async with LocalClient(caller=mgr) as manager:
        w = await _shell(manager, "w", tmp_path, unattended=True)  # its creator is its controller
        assert (await manager.call("close", id=w))["closer"]["by"] == mgr
        assert (await manager.call("close", id=mgr))["closer"]["by"] == mgr  # closed itself


async def test_the_ticks_word_is_written_and_a_session_cannot_forge_it(agent, tmp_path):
    await park_ticks(agent)
    async with LocalClient() as person:
        a = await _shell(person, "a", tmp_path, unattended=True)
        b = await _shell(person, "b", tmp_path, unattended=True)
    got = await agent.rpc_close(a, closer={"by": "tick", "why": "finished"})
    assert (got["closer"]["by"], got["closer"]["why"]) == ("tick", "finished")
    # a session's caller wins over a `closer` in its params: never *closed by the tick*
    got = await agent.rpc_close(b, caller="ao-some-worker", closer={"by": "tick", "why": "seat"})
    assert (got["closer"]["by"], got["closer"]["why"]) == ("ao-some-worker", None)


async def test_a_node_writes_the_word_the_home_hands_it_over_the_link(agent, tmp_path):
    """`_route_act("close")` carries `closer` in its params; the node's `act` passes it to its own
    `rpc_close` with the home's caller — none for the tick and the person."""
    await park_ticks(agent)
    async with LocalClient() as person:
        a = await _shell(person, "a", tmp_path, unattended=True)
        b = await _shell(person, "b", tmp_path, unattended=True)
    await agent._act({"rpc": "close", "params": {"id": a, "closer": {"by": "tick", "why": "wanted"}}, "caller": None})
    assert agent.sessions[a].closer["by"] == "tick" and agent.sessions[a].closer["why"] == "wanted"
    await agent._act({"rpc": "close", "params": {"id": b}, "caller": "ao-mgr@home"})
    assert agent.sessions[b].closer["by"] == "ao-mgr@home"
