"""A role's context bound (design §4.8 *A role has a context bound*, §6 rule 5, TD-190 step 3):
`context: {bound}` on a preset, layered, `none` removing it; on the record at start as `review` is;
the card's reading red past it, and `bound 200k` in `ao status -v`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import park_ticks, wait_for

from agentorc import repoconfig, teams
from sessionorc.agent import CONTEXT_AGAIN
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import ProgressEntry, context_over, normalize_context


def test_a_context_setting_is_checked_and_read_as_tokens():
    assert normalize_context(None) is None and normalize_context("none") is None
    assert normalize_context({"bound": "200k"}) == 200_000
    assert normalize_context({"bound": "1M"}) == normalize_context({"bound": "1m"}) == 1_000_000
    assert normalize_context({"bound": "1.5M"}) == 1_500_000
    assert normalize_context({"bound": 150_000}) == 150_000  # idempotent over the record's own shape
    assert normalize_context({"bound": "none"}) is None
    for bad in ("200k", {"bound": "lots"}, {"bound": 0}, {"bound": True}, {"bound": "200k", "warn": "150k"}):
        with pytest.raises(ValueError, match="context"):
            normalize_context(bad)


def test_over_is_past_the_bound_and_never_without_one():
    s = {"context": {"tokens": 231_000, "window": 1_000_000}, "context_bound": 200_000}
    assert context_over(s)
    assert not context_over({**s, "context_bound": 250_000})
    assert not context_over({**s, "context_bound": 231_000})  # at the bound is not past it
    assert not context_over({**s, "context_bound": None}) and not context_over({"context_bound": 200_000})


def test_the_worker_presets_carry_200k_and_a_layer_can_change_or_remove_it(tmp_path):
    cfg = repoconfig.load(tmp_path)
    got = {n: repoconfig.resolve_role(cfg, n).context_bound for n in repoconfig.PRESETS}
    assert got == {
        "grinder": 200_000, "hunter": 200_000, "auditor": 200_000,
        "manager": None, "techlead": None, "plain": None,
    }  # fmt: skip
    (tmp_path / ".agentorc.yml").write_text(
        "roles:\n  grinder:\n    context: {bound: 300k}\n  hunter:\n    context: none\n"
    )
    cfg = repoconfig.load(tmp_path)
    assert repoconfig.resolve_role(cfg, "grinder").context_bound == 300_000
    assert repoconfig.resolve_role(cfg, "hunter").context_bound is None
    assert repoconfig.resolve_role(cfg, "grinder").to_dict()["context_bound"] == 300_000
    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder:\n    context: {bound: lots}\n")
    with pytest.raises(ValueError, match=r"grinder\.context: bound is a token count"):
        repoconfig.load(tmp_path)
    # `org.yml`'s layer reaches `resolve_role` unchecked by the loader: the same check applies there
    (tmp_path / ".agentorc.yml").write_text("")
    cfg = repoconfig.load(tmp_path)
    with pytest.raises(ValueError, match=r"org roles\.manager\.context"):
        repoconfig.resolve_role(cfg, "manager", {"manager": {"context": {"bound": "x"}}})
    assert repoconfig.resolve_role(cfg, "manager", {"manager": {"context": {"bound": "400k"}}}).context_bound == 400_000


def test_a_team_launch_sends_the_bound_and_a_bare_one_sends_nothing(tmp_path):
    launch = teams.Launch(
        name="g", role="grinder", home="r", dir=tmp_path, team="t", project="p", context_bound=200_000
    )
    assert launch.create_params([])["context_bound"] == 200_000
    bare = teams.Launch(name="g", role="grinder", home="r", dir=tmp_path, team="t", project="p")
    assert "context_bound" not in bare.create_params([])


async def test_the_bound_rides_the_record_and_status_v_says_it(agent, tmp_path, capsys):
    from agentorc import cli

    async with LocalClient() as person:

        async def mk(n: str, **kw):
            params = {"name": n, "dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], **kw}
            return (await person.call("create", **params))["id"]

        w = await mk("w", unattended=True, context_bound=200_000)
        plain = await mk("p", unattended=True)
        assert (await person.call("get", id=w))["context_bound"] == 200_000
        assert (await person.call("get", id=plain))["context_bound"] is None
        with pytest.raises(AgentError, match="context: bound"):
            await mk("bad", context_bound=-5)
    agent.sessions[w].context = {"tokens": 231_203, "at": "2026-09-27T20:00:00Z", "window": 1_000_000}
    agent.sessions[plain].context = {"tokens": 231_203, "at": "2026-09-27T20:00:00Z", "window": 1_000_000}
    assert await asyncio.to_thread(cli.main, ["status", "-v"]) == 0
    out = capsys.readouterr().out
    assert "context: 231k of 1M, bound 200k (over)" in out
    assert out.count("context: 231k of 1M\n") == 1  # the one without a bound: the reading alone


def test_the_card_draws_the_reading_red_past_the_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    s = {
        "id": "ao-r-w", "name": "w", "kind": "interactive", "adapter": "claude-code", "dir": str(tmp_path),
        "state": "idle", "since": "2026-09-11T16:00:00Z", "confidence": "hook", "tail": [], "model": "claude-opus-5-5",
        "context": {"tokens": 231_203, "at": "2026-09-27T20:00:00Z", "window": 1_000_000},
    }  # fmt: skip
    card = templates.get_template("card.html")
    under = view({**s, "context_bound": 300_000})
    assert not under["context_over"] and under["context_bound"] == "300k"
    html = card.render(s=under)
    assert 'class="context"' in html and "context 231k of 1M, bound 300k —" in html
    over = view({**s, "context_bound": 200_000})
    html = card.render(s=over)
    assert 'class="context over"' in html and "bound 200k — over it" in html
    assert not view(s)["context_over"] and view(s)["context_bound"] == ""


# ── step 4: rule 5's line and the reply clause (design §6 rule 5, §4.10 *Busy for hours*) ──────────


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


OVER = {"tokens": 231_203, "at": "2026-09-27T20:00:00Z", "window": 1_000_000}


async def _member(agent, person, tmp_path, name="w", **kw) -> str:
    (tmp_path / name).mkdir()
    params = {
        "name": name, "dir": str(tmp_path / name), "adapter": "composer0", "unattended": True,
        "supervised": True, "prompt": "the brief", "context_bound": 200_000,
    }  # fmt: skip
    sid = (await person.call("create", **{**params, **kw}))["id"]
    assert await wait_for(lambda: _painted(agent, sid), timeout=5), "the composer child never painted its prompt"
    return sid


async def _painted(agent, sid: str) -> bool:
    return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


@pytest.mark.integration
async def test_an_idle_member_over_its_bound_is_told_and_told_again_after_twenty_minutes(
    agent, composerstubs, tmp_path
):
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        sid = await _member(agent, person, tmp_path)
        rec = agent.sessions[sid]
        await agent.rpc_hook(sid, state="idle")
        await agent._keep_running(now)
        assert await _submitted(agent, sid) == [] and rec.context_sent_at is None, "no reading, nothing said"
        rec.context = dict(OVER)
        rec.progress = [ProgressEntry(ref="TD-001", status="claimed")]
        await agent._keep_running(now)
        assert await _submitted(agent, sid) == [], "a claim in progress is never interrupted"
        rec.progress = [ProgressEntry(ref="TD-001", status="done", pr=5)]
        await agent._keep_running(now)
        lines = await _submitted(agent, sid)
        assert len(lines) == 1 and "context 231k, over your 200k bound — take nothing new" in lines[0]
        assert 'ao progress restart --why "context bound"' in lines[0]
        assert rec.context_sent_at and rec.sends[-1].from_ == "system"
        await agent._keep_running(now + timedelta(minutes=10))
        assert len(await _submitted(agent, sid)) == 1, "not again inside twenty minutes"
        rec.context_sent_at = _iso(now - CONTEXT_AGAIN - timedelta(minutes=1))
        await agent._keep_running(now)
        assert len(await _submitted(agent, sid)) == 2, "still idle and over: again"
        await person.call("kill", id=sid)


@pytest.mark.integration
async def test_what_the_context_line_leaves_alone(agent, composerstubs, tmp_path):
    """Under the bound, no bound, working, declared, a seat, a wrap-up, unsupervised: nothing typed."""
    await park_ticks(agent)
    now = datetime.now(UTC)
    async with LocalClient() as person:
        cases = {
            "under": {"context_bound": 300_000},
            "nobound": {"context_bound": None},
            "working": {"state": "working"},
            "declared": {"out_of_work": {"at": _iso(now), "why": "nothing open"}},
            "restart": {"restart_wanted": {"at": _iso(now), "why": "context bound"}},
            "seat": {"seat": {"trigger": "asks"}},
            "wrapping": {"wrapup_at": _iso(now)},
            "unsupervised": {"supervised": False},
        }
        ids = {}
        for n, fields in cases.items():
            sid = await _member(agent, person, tmp_path, name=n)
            await agent.rpc_hook(sid, state="idle")
            agent.sessions[sid].context = dict(OVER)
            for k, v in fields.items():
                setattr(agent.sessions[sid], k, v)
            ids[n] = sid
        await agent._keep_running(now)
        for n, sid in ids.items():
            assert await _submitted(agent, sid) == [] and agent.sessions[sid].context_sent_at is None, n
            await person.call("kill", id=sid)


async def test_every_reply_to_a_member_over_its_bound_carries_the_clause(agent, tmp_path):
    from sessionorc import client as clientmod

    async with LocalClient() as person:
        params = {"dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], "unattended": True}
        w = (await person.call("create", name="w", context_bound=200_000, **params))["id"]
    async with LocalClient(caller=w) as wc:
        await wc.call("get", id=w)
        assert not (clientmod.last_mail or {}).get("context"), "no reading yet"
        agent.sessions[w].context = dict(OVER)
        await wc.call("get", id=w)
        assert clientmod.last_mail["context"] == "context 231k over the 200k bound"
        agent.sessions[w].context_bound = 300_000
        await wc.call("get", id=w)
        assert not (clientmod.last_mail or {}).get("context")
    clientmod.last_mail = None


def test_the_clause_rides_the_unread_line_or_stands_alone(capsys):
    import argparse

    from agentorc import cli
    from sessionorc import client as clientmod

    args = argparse.Namespace(json=False)
    over = "context 231k over the 200k bound"
    clientmod.last_mail = {"unread": 0, "wake_budget_spent": False, "context": over}
    cli.unread_line(args)
    assert capsys.readouterr().out == f"[agentorc] ({over}) — finish the entry in hand, then declare\n"
    clientmod.last_mail = {"unread": 2, "wake_budget_spent": False, "context": over}
    cli.unread_line(args)
    assert capsys.readouterr().out == f"[agentorc] you have 2 unread messages — run ao inbox ({over})\n"
    clientmod.last_mail = None
