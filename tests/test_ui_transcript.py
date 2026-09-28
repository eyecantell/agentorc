"""The Transcript page (design §4.5 screen 9, §4.5a *Focus header* **Transcript** and *Transcript*;
TD-166): the `transcript` RPC's neutral entries drawn as the pane draws them, folded; *earlier
turns*; the editor button on the file; the Focus header's button. Rendered from TD-165's fixture."""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient
from test_transcript import write_fixture

from agentorc.adapters.claude_code import transcript as tr

REC = {
    "id": "ao-x-designer",
    "name": "designer-ao-1",
    "kind": "agent",
    "adapter": "claude-code",
    "adapter_id": "s1",
    "dir": "/r/wt",
    "repo": "/r",
    "team": "ao-grind",
    "role": "designer",
    "state": "exited",
    "since": "2026-09-25T16:12:00Z",
    "confidence": "hook",
    "pane": True,
    "tail": [],
    "created": "2026-09-25T13:48:00Z",
}


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as ui

    t = tr.read(write_fixture(tmp_path), subagents=tmp_path / "s1" / "subagents").to_dict()
    calls: list[tuple[str, dict]] = []
    replies: dict = {"get": REC, "transcript": t}

    class Fake:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, method, **params):
            calls.append((method, params))
            got = replies.get(method, {})
            if isinstance(got, Exception):
                raise got
            return got

    monkeypatch.setattr(ui, "LocalClient", Fake)
    monkeypatch.setattr(ui, "host_name", lambda: "kmaster")
    monkeypatch.setattr("agentorc.ui.cards.host_name", lambda: "kmaster")
    with TestClient(ui.create_app()) as c:
        yield c, calls, replies, t


@pytest.mark.unit
def test_the_page_draws_the_turns_folded_as_the_pane_does(page):
    c, calls, _, t = page
    html = c.get("/transcript/ao-x-designer").text
    assert ("transcript", {"id": "ao-x-designer", "before": None, "turns": 20}) in calls
    assert all(m != "seen" for m, _ in calls)  # a read never marks the session seen
    # the head: whose it is, and what it is
    assert "designer-ao-1" in html and "ao-grind" in html and "/r/wt" in html
    assert f"{t['path']}" in html and "on kmaster" in html
    assert 'the last <span id="tshown">2</span> turns' in html
    # the turns: the prompt with `>`, the text in full, each tool one line, results and thoughts folded
    assert re.search(
        r'<div class="tprompt"><span class="tmark mono">&gt;</span><div class="ttext">fix the tests</div>', html
    )
    assert '<div class="tsaid ttext">Looking.</div>' in html
    assert '<summary>thought · 3 lines</summary>' in html
    assert '⏺</span> Bash(<span class="targ">pdm run test</span>)' in html
    assert '<summary>result · 2 lines</summary><pre class="tbody mono">3 failed\nmore</pre>' in html
    # the subagent's turns are folded under the Agent call that started them, with their count
    agent = html[html.index("Agent(") :]
    assert "3 subagent turns" in agent.split('class="tprompt"')[0]
    assert '<div class="tcompact"><span class="meta">— compacted' in html
    # at the file's start there is nothing earlier to ask for
    assert 'data-act="transcript-earlier"' not in html
    assert "AO.transcript();" in html


@pytest.mark.unit
def test_a_session_s_words_are_text_never_a_control(page):
    """TD-071: a prompt or a result that holds markup is drawn escaped, as the words it is."""
    c, _, replies, t = page
    replies["transcript"] = {
        **t,
        "entries": [
            {"kind": "prompt", "text": '<button data-act="kill">x</button>'},
            {"kind": "tool", "name": "Bash", "call": "<a href=x>", "result": "<script>alert(1)</script>"},
        ],
    }
    html = c.get("/transcript/ao-x-designer").text
    turns = html[html.index('id="tlog"') :]
    assert "<button data-act" not in turns and "&lt;button data-act=&#34;kill&#34;&gt;" in turns
    assert "<script>alert" not in turns and "<a href=x>" not in turns


@pytest.mark.unit
def test_earlier_turns_asks_with_the_offset_and_returns_the_fragment(page):
    c, calls, replies, t = page
    replies["transcript"] = {**t, "before": 4096}
    html = c.get("/transcript/ao-x-designer").text
    assert 'data-act="transcript-earlier" data-before="4096"' in html
    replies["transcript"] = {**t, "before": None}
    part = c.get("/transcript/ao-x-designer?part=1&before=4096").text
    assert ("transcript", {"id": "ao-x-designer", "before": 4096, "turns": 20}) in calls
    assert part.lstrip().startswith('<div class="tturns" data-turns="2">') and "<html" not in part
    assert 'data-act="transcript-earlier"' not in part  # the file's start: no further button


@pytest.mark.unit
def test_the_editor_button_opens_the_file_and_none_draws_none(page, monkeypatch):
    c, _, _, t = page
    html = c.get("/transcript/ao-x-designer").text
    assert re.search(r'<a class="btn sm link editor" href="vscode://[^"]*' + re.escape(t["path"]), html)
    from agentorc.ui import uiconf

    monkeypatch.setattr(uiconf, "open_in", lambda: uiconf.OpenIn(kind="none"))
    assert 'class="btn sm link editor"' not in c.get("/transcript/ao-x-designer").text


@pytest.mark.unit
def test_another_host_s_record_draws_no_editor_button(page):
    c, _, replies, _ = page
    replies["get"] = {**REC, "host": "laptop"}
    assert 'class="btn sm link editor"' not in c.get("/transcript/ao-x-designer").text


@pytest.mark.unit
def test_a_refused_read_is_said_on_the_page(page):
    from sessionorc.client import AgentError

    c, _, replies, _ = page
    replies["transcript"] = AgentError("ao-x-designer carries no tool session id: its hook never reported one")
    html = c.get("/transcript/ao-x-designer").text
    assert 'id="terror"' in html and "its hook never reported one" in html and 'id="tlog"' not in html


@pytest.mark.unit
def test_the_focus_header_offers_transcript_only_with_a_tool_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    def focus(**rec):
        v = view({**REC, "state": "idle", **rec})
        return templates.get_template("focus.html").render(s={**v, "grants_all": []}, host="h", active="Org")

    html = focus()
    button = re.search(
        r'<a class="btn sm link" id="ftranscript" href="/transcript/ao-x-designer" target="_blank"', html
    )
    assert button, "a record with a tool session id draws Transcript, opening a new tab"
    assert re.search(r'<a class="btn sm link hidden" id="ftranscript"', focus(adapter="shell", adapter_id=None))


@pytest.mark.unit
def test_the_page_script_keeps_the_button_and_the_banner_s_words_in_step():
    """The header's button follows `adapter_id` on the delta, and the exited banner gains *or read
    its transcript* where the button is drawn (TD-166 fix 3)."""
    js = (pathlib.Path(__file__).resolve().parent.parent / "src/agentorc/ui/static/app.js").read_text()
    assert 'tr.classList.toggle("hidden", !v.adapter_id)' in js
    assert "read its transcript</a>" in js and "const read = v.adapter_id ?" in js
    assert "AO.transcript = function" in js and "?part=1&before=" in js
