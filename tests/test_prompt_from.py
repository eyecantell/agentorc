"""TD-217 slice 1, design §6 *Keeping a team running* rule 7: the client that composes a brief hands
`create`, beside the prompt, what it was made from — `prompt_from: {base, slots}`, and `prefix` for
the Project block — and the launch record keeps it. Filling `base` from those files and texts, the
way a replay will (slice 2), gives back the very prompt; nothing changes behaviour yet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import park_ticks
from test_cli_teams import creates, org_doc, world, write_org  # noqa: F401 — `world` is a fixture

from agentorc import cli, repoconfig
from sessionorc import paths
from sessionorc.client import LocalClient


def fill(made_from: dict) -> str:
    """What a replay does with a `prompt_from`: plain replacement of each slot, in order, from its
    file (stripped, `none` when empty) or its text, then the prefix in front."""
    text = Path(made_from["base"]).read_text(encoding="utf-8")
    for slot, spec in made_from["slots"].items():
        value = spec["text"] if "text" in spec else (Path(spec["file"]).read_text(encoding="utf-8").strip() or "none")
        text = text.replace(slot, value)
    return made_from.get("prefix", "") + text


@pytest.mark.unit
def test_compose_says_what_the_brief_was_made_from_and_filling_it_gives_the_text(tmp_path):
    (tmp_path / "briefs").mkdir()
    sup = tmp_path / "briefs" / "g.md"
    sup.write_text("grind {lane}, ask {techlead}\n")
    (tmp_path / ".agentorc.yml").write_text("roles: {grinder: {brief: briefs/g.md}, solo: {brief: briefs/g.md}}\n")
    solo = tmp_path / ".agentorc" / "roles" / "solo"  # a role directory (§4.9c, TD-313)
    solo.mkdir(parents=True)
    (solo / "role.yml").write_text("")
    (solo / "template.md").write_text("solo {lane}: {repo}\n")
    cfg = repoconfig.load(tmp_path)
    g = repoconfig.resolve_role(cfg, "grinder")
    text, made = g.compose(["TD-001"], techlead="tl-1")
    assert text == g.brief_text(["TD-001"], techlead="tl-1")
    assert made["base"].endswith("grinder.md") and Path(made["base"]).is_file()  # the installed template
    assert made["slots"]["{repo}"] == {"file": str(sup)}  # absolute, so a replay reads it wherever it runs
    assert made["slots"]["{lane}"] == {"text": "TD-001"} and made["slots"]["{techlead}"] == {"text": "tl-1"}
    assert list(made["slots"])[0] == "{repo}"  # filled first, so the supplement's own slots are filled too
    assert fill(made) == text and "grind TD-001, ask tl-1" in text
    sup.write_text("grind harder {lane}\n")  # the file as it is then, not as it was
    assert "grind harder TD-001" in fill(made)
    # no supplement: the slot is the text the template's `none` word is
    text, made = repoconfig.resolve_role(repoconfig.load(tmp_path / "briefs"), "hunter").compose()
    assert made["slots"]["{repo}"] == {"text": "none"} and fill(made) == text
    # a role directory: its template.md is the base, the repo's brief in its `{repo}` slot
    text, made = repoconfig.resolve_role(cfg, "solo").compose(["x"])
    assert made["base"] == str(solo / "template.md") and made["slots"]["{repo}"] == {"file": str(sup)}
    assert fill(made) == text == "solo x: grind harder x\n"
    assert repoconfig.resolve_role(cfg, "plain").compose() == (None, None)
    assert repoconfig.prefixed(made, "") is made and repoconfig.prefixed(None, "B") is None
    assert repoconfig.prefixed(made, "B")["prefix"] == "B"


@pytest.mark.unit
def test_a_team_start_hands_each_member_its_prompt_from(world):  # noqa: F811
    tmp_path, state = world
    docs = tmp_path / "agentorc" / "docs"
    docs.mkdir()
    (docs / "b.md").write_text("mine: {lane}, ask {techlead}\n")
    doc = org_doc(tmp_path, two_repos=True)  # a reach to describe: the Project block goes in front
    doc["teams"]["ao-grind"]["members"][0]["brief"] = "docs/b.md"
    write_org(tmp_path, doc)
    assert cli.main(["team", "start", "ao-grind"]) == 0
    made = creates(state)
    assert all(m.get("prompt_from") for m in made)
    for m in made:
        assert fill(m["prompt_from"]) == m["prompt"]
    member = made[1]["prompt_from"]
    assert member["slots"]["{repo}"] == {"file": str(docs / "b.md")} and member["prefix"].startswith("## Project")


@pytest.mark.unit
def test_ao_new_sends_prompt_from_with_a_role_and_never_with_a_typed_prompt(world):  # noqa: F811
    tmp_path, state = world
    write_org(tmp_path, org_doc(tmp_path, two_repos=True))
    assert cli.main(["new", "solo", "--project", "ao", "--role", "hunter"]) == 0
    p = creates(state)[0]
    assert fill(p["prompt_from"]) == p["prompt"] and p["prompt_from"]["prefix"].startswith("## Project: ao")
    state["calls"].clear()
    assert cli.main(["new", "typed", "--role", "hunter", "--prompt", "just this"]) == 0
    assert "prompt_from" not in creates(state)[0]  # a whole prompt fills nothing: a replay replays it
    state["calls"].clear()
    assert cli.main(["new", "bare"]) == 0
    assert "prompt_from" not in creates(state)[0]  # no role, no brief: the client sends what it has not set


@pytest.mark.integration
async def test_the_launch_record_keeps_prompt_from_and_a_replay_hands_it_back(agent, tmp_path):
    await park_ticks(agent)
    made_from = {"base": "/x/grinder.md", "slots": {"{lane}": {"text": "TD-1"}}, "prefix": "P"}
    async with LocalClient() as person:
        s = await person.call(
            "create",
            name="w",
            dir=str(tmp_path),
            adapter="shell",
            argv=["bash", "--norc", "--noprofile"],
            unattended=True,
            supervised=True,
            prompt="the brief",
            prompt_from=made_from,
        )
        kept = json.loads((paths.launch_dir() / f"{s['id']}.json").read_text())
        assert kept["prompt_from"] == made_from and kept["prompt"] == "the brief"
        assert agent._read_launch(s["id"])["prompt_from"] == made_from
        assert "prompt_from" not in agent.sessions[s["id"]].view()  # the launch record's alone
        await person.call("kill", id=s["id"])


@pytest.mark.unit
def test_the_lane_slot_leaves_an_owner_word_out():
    """TD-227 slice 1: `owner:<word>` narrows what the home tells a member of; the brief names the lane."""
    g = repoconfig.resolve_role(repoconfig.RepoConfig(), "grinder")
    _, made = g.compose(["free-pick", "owner:grinder"])
    assert made["slots"]["{lane}"] == {"text": "free-pick"}
