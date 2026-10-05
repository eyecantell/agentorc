"""Who reads a PR before it merges (design §4.9b *The reader*, TD-093), slice 1: `review` on a role
preset, carried onto the record at create; `pr` on an `ask`; and `prs_waiting` on the view."""

from __future__ import annotations

import pytest

from agentorc import repoconfig, teams
from sessionorc.client import AgentError, LocalClient
from sessionorc.models import normalize_review, review_links


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


def test_a_chain_of_review_stages_is_checked_and_reads_as_links():
    """§4.9c *A review stage any seat may hold* (TD-315 slice 1): the record carries `{chain: [{stage,
    reader, held}], bound}`; the older shape is a chain of one."""
    chain = {"chain": [{"stage": "ui-review", "reader": "ui-reader", "held": "src/agentorc/ui/**"},
                       {"stage": "review", "reader": "techlead", "held": ["src/sessionorc/**"]}]}  # fmt: skip
    got = normalize_review(chain)
    assert got == {
        "chain": [
            {"stage": "ui-review", "reader": "ui-reader", "held": ["src/agentorc/ui/**"]},
            {"stage": "review", "reader": "techlead", "held": ["src/sessionorc/**"]},
        ],
        "bound": "2h",
    }
    assert normalize_review(got) == got, "idempotent, as the loader and the host agent each pass it"
    assert review_links(got) == got["chain"]
    assert review_links({"reader": "techlead", "held": ["**"], "bound": "2h"}) == [
        {"stage": "review", "reader": "techlead", "held": ["**"]}
    ]
    assert review_links(None) == [] and review_links({}) == []
    # a role's `review:` in a file never writes one: the chain is the compile's (review of #1120)
    with pytest.raises(ValueError, match="written by a flow's compile"):
        normalize_review(chain, chain=False)
    for bad in (
        {"chain": []},  # no link
        {"chain": "techlead"},
        {"chain": [{"stage": "review"}]},  # no reader
        {"chain": [{"reader": "techlead"}]},  # no stage
        {"chain": [{"stage": "a", "reader": "x"}, {"stage": "a", "reader": "y"}]},  # one stage twice
        {"chain": [{"stage": "a", "reader": "x", "held": []}]},
        {"chain": [{"stage": "a", "reader": "x", "merges": True}]},
        {"chain": [{"stage": "a", "reader": "x"}], "reader": "techlead"},  # the two shapes mixed
        {"chain": [{"stage": "a", "reader": "x"}], "bound": "soon"},
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
    # a chain is a flow's compile's, never a file's (TD-315, review of #1120)
    (tmp_path / ".agentorc.yml").write_text("roles:\n  grinder:\n    review: {chain: [{stage: r, reader: x}]}\n")
    with pytest.raises(ValueError, match=r"grinder\.review: a role's review takes reader"):
        repoconfig.load(tmp_path)
    # `org.yml`'s layer reaches `resolve_role` unchecked by the loader: the same check applies there
    (tmp_path / ".agentorc.yml").write_text("")
    org_chain = {"grinder": {"review": {"chain": [{"stage": "r", "reader": "x"}]}}}
    with pytest.raises(ValueError, match=r"org roles\.grinder\.review: a role's review takes reader"):
        repoconfig.resolve_role(repoconfig.load(tmp_path), "grinder", org_chain)
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
        assert seen["asks_waiting"] == 2 and seen["prs_waiting"] == {
            "n": 1,
            "oldest": q["at"],
            "asks": [{"from": w, "pr": 12}],
        }
        # each ask's sender and PR, in the order they came, and never the text (TD-333): what a switch
        # of flow leaves with this reader (§4.9c)
        w2 = await mk("w2", team="t", unattended=True, review={"reader": "techlead"})
        async with LocalClient(caller=w2) as w2c:
            q2 = (await w2c.call("msg", to=tl, text="#13: green", kind="ask", pr=13))["entry"]
        seen = await person.call("get", id=tl)
        assert seen["prs_waiting"] == {
            "n": 2,
            "oldest": q["at"],
            "asks": [{"from": w, "pr": 12}, {"from": w2, "pr": 13}],
        }
        async with LocalClient(caller=tl) as tc:
            # a reader's reply to a PR's ask says what it came to (§4.9c, TD-315): refused without a verdict
            with pytest.raises(AgentError, match="--verdict pass | merged | findings"):
                await tc.call("msg", reply_to=q["id"], kind="reply", text="merged #12")
            with pytest.raises(AgentError, match="a verdict is one of"):
                await tc.call("msg", reply_to=q["id"], kind="reply", text="merged #12", verdict="lgtm")
            got = (await tc.call("msg", reply_to=q["id"], kind="reply", text="merged #12", verdict="merged"))["entry"]
            assert got["verdict"] == "merged"
            await tc.call("msg", reply_to=q2["id"], kind="reply", text="merged #13", verdict="merged")
        assert (await person.call("get", id=tl))["prs_waiting"] is None


@pytest.mark.integration
async def test_a_second_ask_is_a_reply_to_the_readers_findings_and_the_queue_holds_one(agent, tmp_path):
    """TD-251, design §4.9b *The reader*: findings come back on the thread, and the author asks again
    as a **reply to them** — `--thread` names a question put to the person and is refused toward a
    reader. The second ask's root is the first's, and the seat's queue shows the PR once."""
    async with LocalClient() as person:

        async def mk(n: str, **kw):
            params = {"name": n, "dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], **kw}
            return (await person.call("create", **params))["id"]

        w = await mk("w", team="t", unattended=True, review={"reader": "techlead"})
        tl = await mk("tl", team="t", unattended=True)
        async with LocalClient(caller=w) as wc, LocalClient(caller=tl) as tc:
            first = (await wc.call("msg", to=tl, text="#12: green, reviewed", kind="ask", pr=12))["entry"]
            found = (
                await tc.call(
                    "msg", reply_to=first["id"], kind="reply", text="the mark is read twice", verdict="findings"
                )
            )["entry"]
            assert (await person.call("get", id=tl))["prs_waiting"] is None, "answered: the PR is the author's"
            with pytest.raises(AgentError, match="name `person` as the addressee"):
                await wc.call("msg", to=tl, text="#12: fixed", kind="ask", pr=12, thread=first["id"])
            again = (await wc.call("msg", reply_to=found["id"], text="#12: fixed", kind="ask", pr=12))["entry"]
            assert again["root"] == first["id"] and again["pr"] == 12 and again["to"] == [tl]
            assert (await person.call("get", id=tl))["prs_waiting"] == {
                "n": 1,
                "oldest": again["at"],
                "asks": [{"from": w, "pr": 12}],
            }
            await tc.call("msg", reply_to=again["id"], kind="reply", text="merged #12", verdict="merged")
        assert (await person.call("get", id=tl))["prs_waiting"] is None
        for sid in (w, tl):
            await person.call("kill", id=sid)


@pytest.mark.integration
async def test_a_verdict_rides_only_on_a_reply_to_a_prs_ask_and_the_person_needs_none(agent, tmp_path):
    """§4.9c *The reader's answer carries a verdict* (TD-315 slice 1): `--verdict` on any other message
    is refused, and the person's word on a PR's ask — past its bound it is theirs — needs none."""
    async with LocalClient() as person:

        async def mk(n: str, **kw):
            params = {"name": n, "dir": str(tmp_path), "adapter": "shell", "argv": ["bash", "--norc"], **kw}
            return (await person.call("create", **params))["id"]

        w = await mk("w", team="t", unattended=True, review={"reader": "techlead"})
        tl = await mk("tl", team="t", unattended=True)
        async with LocalClient(caller=w) as wc, LocalClient(caller=tl) as tc:
            with pytest.raises(AgentError, match="a verdict rides only on a reply"):
                await wc.call("msg", to=tl, text="#12 is green", kind="ask", pr=12, verdict="pass")
            plain = (await wc.call("msg", to=tl, text="rebase or merge?", kind="ask"))["entry"]
            with pytest.raises(AgentError, match="a verdict rides only on a reply"):
                await tc.call("msg", reply_to=plain["id"], kind="reply", text="rebase", verdict="pass")
            got = (await tc.call("msg", reply_to=plain["id"], kind="reply", text="rebase"))["entry"]
            assert got["verdict"] is None, "a question that is no PR's is answered as before"
            asked = await wc.call("msg", to="person", text="#14: the reader did not answer", kind="ask", pr=14)
        said = (await person.call("msg", reply_to=asked["entry"]["id"], kind="reply", text="merge it"))["entry"]
        assert said["verdict"] is None, "the person's word ends it: a word, not a verdict"
        for sid in (w, tl):
            await person.call("kill", id=sid)
