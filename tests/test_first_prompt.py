"""The brief typed, never passed (design §4.1 *No prose in the argv*, TD-339 slice 2): a launch whose
adapter hands its prompt back as `LaunchSpec.first_prompt` lands at the composer, and the host agent
types the brief at the record's first hook-reported `idle` the way `send` does — before the doorbell
may ring — then marks the record *brief not sent* after `FIRST_PROMPT_TRIES` the composer did not
take, until a prompt goes in."""

import asyncio

import pytest
from _stubs import ComposerStub
from conftest import FAST_TICK, wait_for

from agentorc.adapters.claude_code.hook import translate
from agentorc.ui import cards
from sessionorc import adapters, agent_common, mail
from sessionorc.adapters import LaunchSpec
from sessionorc.client import LocalClient

pytestmark = pytest.mark.integration

BRIEF = "You are a grinder. Read the ledger, then pick one entry."
# the home's line, typed as keys, then the brief as the paste: one prompt (§4.1, TD-347)
TYPED = f"{mail.BRIEF_LINE} {BRIEF}"


class BriefStub(ComposerStub):
    """The composer child, with the launch's prompt handed back rather than run (§4.1)."""

    def __init__(self, swallow: int):
        super().__init__(swallow)
        self.name = f"brief{swallow}"

    def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
        spec = super().launch(profile=profile, resume=resume, prompt=None, unattended=unattended, cwd=cwd, name=name)
        return LaunchSpec(argv=spec.argv, first_prompt=prompt)


@pytest.fixture
def briefstubs(monkeypatch):
    adapters.load_all()
    for n in (0, 99):  # a composer that takes the brief, and one that swallows every Enter
        stub = BriefStub(n)
        monkeypatch.setitem(adapters._REGISTRY, stub.name, stub)


async def _submitted(agent, sid: str) -> list[str]:
    return [t.rstrip() for t in await agent.rpc_tail(sid, 30) if t.startswith("SUBMITTED ")]


async def _start(agent, c, tmp_path, name: str, adapter: str, **kw) -> str:
    (tmp_path / name).mkdir()
    sid = (await c.call("create", name=name, dir=str(tmp_path / name), adapter=adapter, unattended=True, **kw))["id"]

    async def painted() -> bool:
        return any(t.startswith(">>") for t in await agent.rpc_tail(sid, 3))

    assert await wait_for(painted, timeout=5), "the composer child never painted its prompt"
    return sid


async def test_the_brief_is_typed_at_the_first_idle_and_before_the_doorbell(agent, briefstubs, tmp_path):
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        rec = agent.sessions[w]
        assert rec.first_prompt == BRIEF and "first_prompt" not in await c.call("get", id=w), "never on the view"
        lead = (await c.call("create", name="lead", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))["id"]
        await c.call("set_controllers", id=w, add=[lead])
        async with LocalClient(caller=lead) as ld:
            await ld.call("msg", to=w, text="mail before the brief")
        await asyncio.sleep(4 * FAST_TICK)
        assert await _submitted(agent, w) == [], "nothing is typed before the first hook idle"
        await agent.rpc_hook(w, state="idle")
        assert await wait_for(lambda: _first(agent, w, "SUBMITTED " + TYPED), timeout=6), "the brief was never typed"
        assert await wait_for(lambda: _sent(rec), timeout=6), "typed, and the record never said so"
        assert rec.first_prompt is None and rec.first_prompt_sent_at and rec.first_prompt_error is None
        assert rec.sends[-1].from_ == mail.SYSTEM and rec.sends[-1].text == "(the brief)"
        # its turn starts by its own UserPromptSubmit; the doorbell rings on a later idle stretch
        await agent.rpc_hook(w, state="working", prompt=True)
        assert (await c.call("get", id=w))["state"] == "working"
        await agent.rpc_hook(w, state="idle")
        line = "SUBMITTED " + mail.unread_line(1)
        assert await wait_for(lambda: _first(agent, w, line, at=1), timeout=6), "the doorbell never rang after it"
        assert (await _submitted(agent, w))[0] == "SUBMITTED " + TYPED, "typed once"


async def _first(agent, sid: str, line: str, at: int = 0) -> bool:
    got = await _submitted(agent, sid)
    return len(got) > at and got[at] == line


async def test_a_composer_that_never_takes_the_brief_marks_the_record_until_a_prompt_goes_in(
    agent, briefstubs, tmp_path, monkeypatch
):
    monkeypatch.setattr(agent_common, "SUBMIT_SECONDS", 0.3)
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief99", prompt=BRIEF)
        await agent.rpc_hook(w, state="idle")
        rec = agent.sessions[w]
        assert await wait_for(lambda: _marked(rec), timeout=20), "never marked"
        assert rec.first_prompt_tries == agent_common.FIRST_PROMPT_TRIES and "prompt-stuck" in rec.first_prompt_error
        assert rec.first_prompt_sent_at is None and await _submitted(agent, w) == []
        view = await c.call("get", id=w)
        slot = cards.view(view)["slot"]  # §4.5a **brief not sent**: the card's slot, amber
        assert slot["text"].startswith("brief not sent · prompt-stuck") and slot["kind"] == "needs"
        await asyncio.sleep(4 * FAST_TICK)
        assert rec.first_prompt_tries == agent_common.FIRST_PROMPT_TRIES, "given up on: nothing more is typed"
        # a person's send goes in: its UserPromptSubmit clears the mark
        await agent.rpc_hook(w, state="working", prompt=True)
        assert rec.first_prompt_error is None and rec.first_prompt is None and rec.first_prompt_sent_at


async def test_a_paste_refused_after_the_line_pastes_the_brief_after_it_on_the_next_try(
    agent, briefstubs, tmp_path, monkeypatch
):
    """The line is typed as keys and the brief pasted after it (§4.1, TD-347): a paste tmux refuses
    leaves the line alone in the composer, and the next try pastes the brief after it — an Enter there
    would submit the line with no brief and mark the brief sent (review of PR #1158). It is known from
    the step that failed, never read back, since the composer may show the line cut over two rows."""
    paste, refused = agent.tmux.paste, []

    def once(name: str, text: str) -> None:
        if not refused:
            refused.append(text)
            raise RuntimeError("tmux refused the paste")
        paste(name, text)

    monkeypatch.setattr(agent.tmux, "paste", once)
    # the real tool paints the 111-character line over two rows of an 80-column pane, and the composer
    # read returns the first alone: the recovery cannot read the line back (review of PR #1158)
    stub = adapters.get("brief0")
    whole = stub.composer
    monkeypatch.setattr(stub, "composer", lambda tail: None if (t := whole(tail)) is None else t[:78])
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        await agent.rpc_hook(w, state="idle")
        rec = agent.sessions[w]
        assert await wait_for(lambda: _sent(rec), timeout=10), "the brief was never sent"
        assert refused == [BRIEF] and rec.first_prompt_tries == 1
        assert await _submitted(agent, w) == ["SUBMITTED " + TYPED], "the line and the brief, as one prompt"


async def _sent(rec) -> bool:
    return bool(rec.first_prompt_sent_at)


async def _marked(rec) -> bool:
    return bool(rec.first_prompt_error)


async def test_a_launch_with_no_brief_types_and_marks_nothing(agent, briefstubs, tmp_path):
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0")
        await agent.rpc_hook(w, state="idle")
        await asyncio.sleep(4 * FAST_TICK)
        rec = agent.sessions[w]
        assert await _submitted(agent, w) == [] and rec.first_prompt is None and rec.first_prompt_error is None


def test_a_prompt_submit_says_a_prompt_went_in():
    got = translate({"hook_event_name": "UserPromptSubmit", "session_id": "u1"})
    assert got == {"adapter_id": "u1", "state": "working", "pending": None, "prompt": True}
    assert "prompt" not in translate({"hook_event_name": "Stop", "session_id": "u1"})


# -- a brief whose first hook is lost (design §4.1, TD-348) ------------------------------------------------


async def test_a_launch_whose_hooks_never_speak_gets_its_brief_on_a_scraped_idle(
    agent, briefstubs, tmp_path, monkeypatch
):
    """No hook since the launch and `FIRST_PROMPT_HOOK_WAIT` passed: one send on a scraped `idle`
    whose composer reads empty, marked `scraped` on the record's `sends`."""
    monkeypatch.setattr(agent_common, "FIRST_PROMPT_HOOK_WAIT", 0.5)
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        rec = agent.sessions[w]
        rec.set_state("idle", confidence="scraped")  # what the tick reads off a launch no hook reached
        assert await wait_for(lambda: _first(agent, w, "SUBMITTED " + TYPED), timeout=8), "never sent on the scrape"
        assert await wait_for(lambda: _sent(rec), timeout=6)
        assert rec.first_prompt is None and rec.first_prompt_error is None and rec.first_prompt_tries == 0
        assert rec.sends[-1].text == "(the brief)" and rec.sends[-1].scraped is True
        assert (await c.call("get", id=w))["sends"][-1]["scraped"] is True


async def test_a_scraped_idle_waits_for_the_hook_wait(agent, briefstubs, tmp_path):
    """Before `FIRST_PROMPT_HOOK_WAIT`, a scraped `idle` sends nothing: the hook is the ordinary road."""
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        agent.sessions[w].set_state("idle", confidence="scraped")
        await asyncio.sleep(4 * FAST_TICK)
        assert await _submitted(agent, w) == [] and agent.sessions[w].first_prompt == BRIEF


async def test_a_composer_that_never_reads_empty_is_marked_at_the_bound(agent, briefstubs, tmp_path, monkeypatch):
    """Someone's words in the composer: the scraped send waits, uncounted, and at `FIRST_PROMPT_BOUND`
    the record reads *brief not sent · no hook since launch* — on the card, and cleared by a prompt."""
    monkeypatch.setattr(agent_common, "FIRST_PROMPT_HOOK_WAIT", 0.2)
    monkeypatch.setattr(agent_common, "FIRST_PROMPT_BOUND", 2.0)
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        agent.tmux.send_literal(w, "half a thought")
        rec = agent.sessions[w]
        rec.set_state("idle", confidence="scraped")
        assert await wait_for(lambda: _marked(rec), timeout=10), "never marked at the bound"
        assert rec.first_prompt_error == agent_common.FIRST_PROMPT_NO_HOOK and rec.first_prompt_tries == 0
        assert await _submitted(agent, w) == [] and rec.first_prompt == BRIEF
        slot = cards.view(await c.call("get", id=w))["slot"]
        assert slot["text"] == "brief not sent · no hook since launch" and "no hook reached" in slot["full"]
        # a hook arriving later clears nothing by itself; a prompt that goes in does
        await agent.rpc_hook(w, state="idle")
        await asyncio.sleep(3 * FAST_TICK)
        assert rec.first_prompt_error == agent_common.FIRST_PROMPT_NO_HOOK and await _submitted(agent, w) == []
        await agent.rpc_hook(w, state="working", prompt=True)
        assert rec.first_prompt_error is None


async def test_a_hook_before_the_bound_keeps_the_ordinary_road(agent, briefstubs, tmp_path, monkeypatch):
    """A record a hook has reached is never marked *no hook since launch*, however long its brief waits,
    and its hook `idle` sends as before — no `scraped` on the send."""
    monkeypatch.setattr(agent_common, "FIRST_PROMPT_HOOK_WAIT", 0.2)
    monkeypatch.setattr(agent_common, "FIRST_PROMPT_BOUND", 0.5)
    async with LocalClient() as c:
        w = await _start(agent, c, tmp_path, "w", "brief0", prompt=BRIEF)
        await agent.rpc_hook(w, state="working")
        await asyncio.sleep(1.0)
        rec = agent.sessions[w]
        assert rec.first_prompt_error is None and await _submitted(agent, w) == []
        await agent.rpc_hook(w, state="idle")
        assert await wait_for(lambda: _sent(rec), timeout=6)
        assert rec.sends[-1].scraped is False
