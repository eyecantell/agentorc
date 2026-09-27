"""A role's context bound (design §4.8 *A role has a context bound*, §6 rule 5, TD-190 step 3):
`context: {bound}` on a preset, layered, `none` removing it; on the record at start as `review` is;
the card's reading red past it, and `bound 200k` in `ao status -v`."""

from __future__ import annotations

import asyncio

import pytest

from agentorc import repoconfig, teams
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import context_over, normalize_context


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
