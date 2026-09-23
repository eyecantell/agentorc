"""Who reads a PR before it merges (design §4.9b *The reader*, TD-093), slice 1: `review` on a role
preset, carried onto the record at create; `pr` on an `ask`; and `prs_waiting` on the view."""

from __future__ import annotations

import pytest

from agentorc import repoconfig, teams
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import normalize_review


def test_a_review_setting_is_checked_and_filled_in():
    assert normalize_review(None) is None
    assert normalize_review({"reader": "techlead"}) == {"reader": "techlead", "held": ["**"], "bound": "2h"}
    got = normalize_review({"reader": "person", "held": "src/**", "bound": "90m"})
    assert got == {"reader": "person", "held": ["src/**"], "bound": "90m"}
    for bad in (
        "techlead",  # not a mapping
        {"reader": "fable"},  # an unknown reader
        {},  # no reader at all
        {"reader": "techlead", "held": []},  # holds nothing, says nothing
        {"reader": "techlead", "bound": "two hours"},
        {"reader": "techlead", "merges": True},  # an unknown key
    ):
        with pytest.raises(ValueError, match="review"):
            normalize_review(bad)


def test_a_role_preset_carries_review_to_the_create(tmp_path):
    (tmp_path / ".agentorc.yml").write_text(
        "roles:\n  grinder:\n    review: {reader: techlead, held: [src/sessionorc/**]}\n"
    )
    cfg = repoconfig.load(tmp_path)
    role = repoconfig.resolve_role(cfg, "grinder")
    assert role.review == {"reader": "techlead", "held": ["src/sessionorc/**"], "bound": "2h"}
    assert repoconfig.resolve_role(cfg, "hunter").review is None  # a role that says nothing holds nothing
    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder:\n    review: {reader: nobody}\n")
    with pytest.raises(ValueError, match=r"grinder\.review: reader is one of"):
        repoconfig.load(tmp_path)
    # `org.yml`'s layer reaches `resolve_role` unchecked by the loader: the same check applies there
    (tmp_path / ".agentorc.yml").write_text("")
    with pytest.raises(ValueError, match=r"org roles\.grinder\.review: reader is one of"):
        repoconfig.resolve_role(repoconfig.load(tmp_path), "grinder", {"grinder": {"review": {"reader": "x"}}})
    assert repoconfig.resolve_role(
        repoconfig.load(tmp_path), "grinder", {"grinder": {"review": {"reader": "person"}}}
    ).review == {
        "reader": "person",
        "held": ["**"],
        "bound": "2h",
    }
    launch = teams.Launch(
        name="g", role="grinder", home="r", dir=tmp_path, team="t", project="p", review=dict(role.review or {})
    )
    assert launch.create_params([])["review"]["reader"] == "techlead"
    bare = teams.Launch(name="g", role="grinder", home="r", dir=tmp_path, team="t", project="p")
    assert "review" not in bare.create_params([])  # a client never sends what it has not set


async def test_review_rides_the_record_and_pr_rides_an_ask(agent, tmp_path):
    async with LocalClient() as person:

        async def mk(n: str, **kw):
            params = {"name": n, "dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], **kw}
            return (await person.call("create", **params))["id"]

        w = await mk("w", team="t", unattended=True, review={"reader": "techlead"})
        tl = await mk("tl", team="t", unattended=True)
        assert (await person.call("get", id=w))["review"] == {"reader": "techlead", "held": ["**"], "bound": "2h"}
        assert (await person.call("get", id=tl))["review"] is None
        with pytest.raises(AgentError, match="review: reader"):
            await mk("bad", review={"reader": "anyone"})
        assert (await person.call("get", id=tl))["prs_waiting"] is None
        async with LocalClient(caller=w) as wc:
            with pytest.raises(AgentError, match="only on an `ask`"):
                await wc.call("msg", to=tl, text="#12 is green", kind="note", pr=12)
            with pytest.raises(AgentError, match="positive integer"):
                await wc.call("msg", to=tl, text="#12 is green", kind="ask", pr="12")
            q = (await wc.call("msg", to=tl, text="#12: the doorbell, green, reviewed", kind="ask", pr=12))["entry"]
            await wc.call("msg", to=tl, text="rebase or merge?", kind="ask")  # a question, not a PR
        assert q["pr"] == 12
        seen = await person.call("get", id=tl)
        assert seen["asks_waiting"] == 2 and seen["prs_waiting"] == {"n": 1, "oldest": q["at"]}
        async with LocalClient(caller=tl) as tc:
            await tc.call("msg", reply_to=q["id"], kind="reply", text="merged #12")
        assert (await person.call("get", id=tl))["prs_waiting"] is None
