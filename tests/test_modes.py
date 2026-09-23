"""Home and node (design §4.4a, TD-057 step 2): the `home:` line, what a node answers while it cannot
reach its home, and the replica's merge by field owner."""

import asyncio
import contextlib

import pytest
from conftest import FAST_TICK, kill_private_server, private_socket_name, wait_for

from sessionorc import hosts, modes, paths
from sessionorc.agent import HostAgent
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import HOME_OWNED, NODE_OWNED, NotTheSameSession, Session, apply_home, apply_node
from sessionorc.tmux import Tmux

# -- hosts.yml ------------------------------------------------------------------------------------


@pytest.mark.unit
def test_no_home_line_or_one_naming_this_host_is_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    assert hosts.home_name() == hosts.local_host().name and not hosts.is_node()  # no file at all
    f = tmp_path / "hosts.yml"
    f.write_text("local:\n  name: kmaster\n")
    assert hosts.home_name() == "kmaster" and not hosts.is_node()
    f.write_text("home: kmaster\nlocal:\n  name: kmaster\n")
    assert not hosts.is_node()  # naming itself
    f.write_text("home: kmaster\nlocal:\n  name: laptop\n")
    assert hosts.home_name() == "kmaster" and hosts.is_node()


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad", ["home: true\n", "home: [kmaster]\n", "home: {name: kmaster}\n", "home: '  '\n", "home: 3\n"]
)
def test_a_malformed_home_is_no_home_never_a_node_by_accident(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text(bad + "local:\n  name: laptop\n")
    assert hosts.home_name() == "laptop" and not hosts.is_node()


# -- the call-by-call table -------------------------------------------------------------------------


def refusal(method, caller=None, **params):
    return modes.offline_refusal(method, caller, params, host="laptop", home="kmaster")


@pytest.mark.unit
def test_reads_are_served_to_anyone():
    for m in (
        "list",
        "get",
        "tail",
        "explain",
        "occupancy",
        "name_check",
        "recent_dirs",
        "usage",
        "adapters",
        "ping",
        "wait",
    ):
        assert refusal(m) is None and refusal(m, "ao-x-w") is None, m


@pytest.mark.unit
def test_a_person_acts_on_this_hosts_sessions_and_creates():
    for m in ("send", "keys", "kill", "close", "remove", "create", "seen", "decide", "hook"):
        assert refusal(m, id="ao-x-w") is None, m


@pytest.mark.unit
def test_a_session_acts_on_itself_and_on_nobody_else_and_creates_nothing():
    assert refusal("close", "ao-x-w", id="ao-x-w") is None
    assert refusal("send", "ao-x-w", id="ao-x-w", text="hi") is None
    r = refusal("send", "ao-x-lead", id="ao-x-w", text="hi")
    assert r and "ao-x-lead cannot send ao-x-w" in r and "kmaster (home) is unreachable from laptop" in r
    assert "refused, not queued" in r
    assert "creates nothing" in refusal("create", "ao-x-lead", name="w2")
    assert refusal("hook", "ao-x-w", id="ao-x-w") is None  # the hook socket is the node's
    # answering another session's permission is an act gated at the home, like a send (TD-116)
    # ...and a session never answers its own prompt, link or no link (TD-119)
    r = refusal("decide", "ao-x-w", id="ao-x-w", tool_use_id="t", behavior="allow")
    assert r and "does not answer its own permission prompt" in r
    r = refusal("decide", "ao-x-lead", id="ao-x-w", tool_use_id="t", behavior="allow")
    assert r and "ao-x-lead cannot decide ao-x-w" in r and "gated at the home" in r


@pytest.mark.unit
def test_home_owned_edits_the_mailbox_and_reports_are_refused_to_a_person_too():
    # `suspend` is here with them (§4.8a, TD-077 a2): the mark is a home-owned field, so a node
    # that served it would kill the session and write a mark the home's next copy wipes
    # and `identity_log` (review of PR #318): its mail, its debt and its trail are the home's
    for m in ("set_controllers", "set_grants", "set_stop", "set_mode", "suspend", "identity_log"):
        assert "waits for the link" in refusal(m, id="ao-x-w"), m
    for m in ("msg", "inbox", "inbox_delete"):
        assert "the mailbox is at the home" in refusal(m), m
        assert "the mailbox is at the home" in refusal(m, "ao-x-w"), m
    for m in ("progress", "finding", "doing"):  # `doing` is the third channel (§4.8, TD-074)
        assert "reports are written at the home" in refusal(m, "ao-x-w", id="ao-x-w"), m


# -- the replica's merge ------------------------------------------------------------------------------


def rec(**kw):
    return Session(id="ao-x-w", name="w", kind="agent", adapter="claude-code", dir="/tmp/x", host="laptop", **kw)


@pytest.mark.unit
def test_merges_go_by_owner_never_by_last_write():
    """§4.4a's own example: the link was down, the node wrapped the worker up and it exited, and a
    person at the home extended `run_until`. Both stand."""
    node = rec(state="exited", exit_code=0, wrapup_sent_at="2026-09-17T20:00:00Z", run_until="2026-09-17T20:00:00Z")
    home = rec(state="working", run_until="2026-09-18T06:00:00Z", controllers=["ao-x-lead@kmaster"], team="grind")
    apply_home(node, home.to_dict())
    assert node.state == "exited" and node.exit_code == 0 and node.wrapup_sent_at  # the node's, untouched
    assert (
        node.run_until == "2026-09-18T06:00:00Z" and node.controllers == ["ao-x-lead@kmaster"] and node.team == "grind"
    )
    apply_node(home, node.to_dict())
    assert home.state == "exited" and home.exit_code == 0
    assert home.run_until == "2026-09-18T06:00:00Z"  # the home's, untouched by the report


@pytest.mark.unit
def test_a_copy_overlays_only_its_owners_fields_parsed_and_omitted_fields_stand():
    node = rec(state="idle", team="old")
    mail = {"id": "m-1", "from": "person", "to": ["ao-x-w"], "at": "t", "kind": "note", "text": "hi"}
    sneaky = {"state": "exited", "team": "new", "inbox": [mail]}
    apply_home(node, sneaky)
    assert node.state == "idle" and node.team == "new"  # a home copy cannot write what the node owns
    assert node.inbox[0].from_ == "person"  # parsed into the record's own types, not left a dict
    apply_home(node, {})
    assert node.team == "new"  # omitted: left alone
    assert not (HOME_OWNED & NODE_OWNED)


@pytest.mark.unit
def test_what_a_person_did_on_an_offline_node_survives_the_homes_copy():
    """Review of PR #198: `sends`, `seen_at` and `wake_refilled_at` are home-owned and still written
    on a node offline — a person typed there, looked there. They only grow, so both directions merge
    them; an overlay would erase the offline half and say nothing."""
    from sessionorc import mail
    from sessionorc.models import SENDS_KEPT, SendEntry

    assert SENDS_KEPT == mail.SENDS_KEEP  # a parity pair: models cannot import mail
    node = rec(seen_at="2026-09-17T21:00:00Z", wake_refilled_at="2026-09-17T21:00:00.000000+00:00")
    node.sends = [SendEntry(id="s-node", from_="person", at="2026-09-17T21:00:00Z", text="typed on the laptop")]
    home = rec(seen_at="2026-09-17T20:00:00Z")
    home.sends = [SendEntry(id="s-home", from_="ao-x-lead", at="2026-09-17T20:30:00Z", text="typed from the home")]
    apply_home(node, home.to_dict())
    assert [e.id for e in node.sends] == ["s-home", "s-node"]  # both, in time order
    assert node.seen_at == "2026-09-17T21:00:00Z" and node.wake_refilled_at  # the later look stands
    apply_node(home, node.to_dict())
    assert [e.id for e in home.sends] == ["s-home", "s-node"] and home.seen_at == "2026-09-17T21:00:00Z"
    apply_node(home, node.to_dict())
    assert len(home.sends) == 2  # idempotent: a replayed snapshot adds nothing


@pytest.mark.unit
def test_a_copy_that_disagrees_on_identity_is_another_session():
    with pytest.raises(NotTheSameSession, match="`host`"):
        apply_home(rec(), {**rec().to_dict(), "host": "kmaster"})
    with pytest.raises(NotTheSameSession, match="`dir`"):
        apply_node(rec(), {"dir": "/tmp/elsewhere", "state": "idle"})
    # …but what the session's own host sets after it exists travels with the node's report
    r = apply_node(rec(), {"adapter_id": "tool-uuid", "name": "renamed", "state": "idle"})
    assert (r.adapter_id, r.name, r.state) == ("tool-uuid", "renamed", "idle")
    assert apply_home(rec(), {"adapter_id": "from-home"}).adapter_id is None  # never from the home's copy


# -- a node, end to end ---------------------------------------------------------------------------------


@pytest.fixture
async def node(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # `link.command`: never the default `ssh kmaster`, which on Paul's machine is the live system
    (home / "hosts.yml").write_text("home: kmaster\nlocal:\n  name: laptop\nlink: {command: ['false']}\n")
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("AGENTORC_SESSION", raising=False)
    monkeypatch.setattr("sessionorc.agent.TICK_SECONDS", FAST_TICK)
    sock_name = private_socket_name()
    monkeypatch.setenv("AGENTORC_TMUX_SOCKET", sock_name)
    tmux = Tmux(socket_name=sock_name)
    a = HostAgent(tmux=tmux)
    task = asyncio.create_task(a.serve(paths.socket_path()))
    assert await wait_for(lambda: paths.socket_path().exists(), timeout=5.0, step=0.05)
    yield a
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    kill_private_server(tmux)


async def test_a_node_serves_a_person_and_refuses_what_needs_the_home(node, hookstub, tmp_path):
    assert node.mode == "node" and node.home == "kmaster" and not node.home_reachable()
    async with LocalClient() as c:
        me = await c.call("host")
        assert (me["host"], me["home"], me["mode"], me["home_reachable"]) == ("laptop", "kmaster", "node", False)
        assert me["link"]["up"] is False  # this fixture's link command is `false`
        s = await c.call("create", name="w", dir=str(tmp_path), adapter=hookstub.name)  # a person's offline create
        assert s["host"] == "laptop" and [x["id"] for x in await c.call("list")] == [s["id"]]
        with pytest.raises(AgentError, match="the mailbox is at the home: kmaster"):
            await c.call("msg", to=[s["id"]], text="hi")
        with pytest.raises(AgentError, match="waits for the link"):
            await c.call("set_controllers", id=s["id"], controllers=["ao-x-lead"])
        await c.call("kill", id=s["id"])  # and a person still stops it
    async with LocalClient(caller=s["id"]) as c:
        with pytest.raises(AgentError, match="reports are written at the home"):
            await c.call("progress", id=s["id"], ref="TD-1", status="claimed")
        with pytest.raises(AgentError, match="creates nothing"):
            await c.call("create", name="w2", dir=str(tmp_path), adapter=hookstub.name)


async def test_the_home_never_consults_the_table(agent):
    assert agent.mode == "home" and agent.home == agent.host and agent.home_reachable()
    async with LocalClient() as c:
        assert (await c.call("host"))["mode"] == "home"
        assert (await c.call("inbox"))["unread"] == 0  # the person inbox answers, as in phase 1
