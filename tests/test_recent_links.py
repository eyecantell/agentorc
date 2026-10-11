"""The Session card's recent links (design §4.2 *The record's `links`*, §4.3 `links`, §4.5 item 4, §4.5a
**recent links**, TD-543, built by TD-544): the adapter reads the URLs the session printed from its
transcript by a byte cursor, the tick keeps them on the record — newest first, a URL once, the newest
`RECENT_LINKS` — and the card draws each as a link that opens in a new tab."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

from agentorc.adapters.claude_code import ClaudeCodeAdapter, munge
from agentorc.adapters.claude_code import transcript as transcript_mod
from sessionorc import adapters
from sessionorc.adapters import Link
from sessionorc.client import LocalClient
from sessionorc.models import NODE_OWNED, RECENT_LINKS, Session

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"


def _text(at: str, *texts: str) -> dict:
    return {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "text", "text": t} for t in texts]}}


def _result(at: str, content) -> dict:
    return {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "content": content}]}}


def _write(path: pathlib.Path, entries: list[dict], mode: str = "w") -> None:
    with path.open(mode) as f:
        f.write("".join(json.dumps(e) + "\n" for e in entries))


@pytest.mark.unit
def test_the_url_rule_ends_at_whitespace_a_bracket_or_a_quote_and_drops_trailing_punctuation():
    got = transcript_mod.urls_in(
        "Opened https://github.com/o/r/pull/12. See (https://docs.x.org/a/b), [CI](https://ci.x/run/9) "
        '"https://q.example/x?y=1" `https://tick.example/z` <https://angle.example/p>; and https://w.org/A_(b) '
        "http://plain.example/path: **https://bold.example/b** is it https://q.example/end?"
    )
    assert got == [
        "https://github.com/o/r/pull/12",
        "https://docs.x.org/a/b",
        "https://ci.x/run/9",
        "https://q.example/x?y=1",
        "https://tick.example/z",
        "https://angle.example/p",
        "https://w.org/A_(b",  # a trailing `)` is punctuation, as the entry's rule says
        "http://plain.example/path",
        "https://bold.example/b",  # Markdown's bold off its end
        "https://q.example/end",  # a question's mark is the sentence's
    ]
    # a loopback host is this machine's, never a link worth keeping; a bare scheme is no URL (a bracketed
    # IPv6 host ends at its bracket, so `[::1]` leaves one)
    assert (
        transcript_mod.urls_in(
            "http://localhost:8080/x http://127.0.0.1/y http://[::1]:9/z http://0.0.0.0:5000/ "
            "http://app.localhost/ http://127.8.0.1/ https:// ftp://x.org/"
        )
        == []
    )


@pytest.mark.unit
def test_the_read_takes_text_and_tool_results_and_skips_prompts_thoughts_calls_and_subagents(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(
        p,
        [
            {"type": "user", "timestamp": "T0", "message": {"content": "look at https://prompt.example/a"}},
            {
                "type": "user",
                "timestamp": "T0",
                "message": {"content": [{"type": "text", "text": "https://p2.example"}]},
            },
            _text("T1", "PR: https://github.com/o/r/pull/7"),
            {
                "type": "assistant",
                "timestamp": "T2",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "https://thought.example"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "curl https://call.example"}},
                    ]
                },
            },
            _result("T3", "run: https://ci.example/run/1"),
            _result("T4", [{"type": "text", "text": "doc https://docs.example/x"}]),
            {**_text("T5", "https://sub.example/x"), "isSidechain": True},
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"content": [{"type": "text", "text": "https://s.x"}]},
            },
        ],
    )
    found, cursor = transcript_mod.links(p)
    assert found == [
        Link("https://github.com/o/r/pull/7", "T1"),
        Link("https://ci.example/run/1", "T3"),
        Link("https://docs.example/x", "T4"),
    ]
    assert cursor == p.stat().st_size


@pytest.mark.unit
def test_the_cursor_advances_past_whole_lines_and_starts_again_past_the_files_end(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, [_text("T1", "https://a.example")])
    found, cursor = transcript_mod.links(p)
    assert [k.url for k in found] == ["https://a.example"]
    # nothing new past the cursor: nothing found, the cursor where it was
    assert transcript_mod.links(p, cursor) == ([], cursor)
    # a line still being written is left for the next read
    with p.open("a") as f:
        f.write(json.dumps(_text("T2", "https://b.example")) + "\n" + '{"type": "assistant", "mess')
    found, after = transcript_mod.links(p, cursor)
    assert [k.url for k in found] == ["https://b.example"] and after < p.stat().st_size
    # the tool rewrote the file shorter (a compaction): a cursor past the end reads it from 0
    _write(p, [_text("T3", "https://c.example")])
    assert after > p.stat().st_size
    found, _ = transcript_mod.links(p, after)
    assert [k.url for k in found] == ["https://c.example"]


@pytest.mark.unit
def test_the_adapter_reads_by_profile_and_answers_none_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "profiles.yml").write_text(
        f"default: t\nprofiles:\n  t: {{account: t, model: opus, config_dir: {tmp_path / 'cc'}}}\n"
    )
    ad = ClaudeCodeAdapter()
    assert ad.links("abc", tmp_path / "repo", "t") is None  # no transcript: not an error
    d = tmp_path / "cc" / "projects" / munge(tmp_path / "repo")
    d.mkdir(parents=True)
    _write(d / "abc.jsonl", [_text("T1", "https://a.example/x")])
    found, cursor = ad.links("abc", tmp_path / "repo", "t")
    assert found == [Link("https://a.example/x", "T1")] and cursor > 0
    assert ad.links("abc", tmp_path / "repo", "t", cursor=cursor) == ([], cursor)
    assert ad.links("abc", tmp_path / "repo", "nope") is None  # an unknown profile, never another account's
    # the contract: the shell adapter has no transcript, so no read
    assert getattr(adapters.ShellAdapter(), "links", None) is None


@pytest.mark.unit
def test_links_is_the_nodes_and_new_records_start_empty():
    assert "links" in NODE_OWNED
    assert Session(id="ao-x", name="x", kind="interactive", dir="/d", adapter="shell").links == []


@pytest.mark.integration
async def test_the_tick_merges_newest_first_a_url_once_and_keeps_twenty(agent, tmp_path, monkeypatch):
    """The tick reads each live record with a tool session id from its cursor, once per LINKS_EVERY,
    attended too, and merges: a URL once at its newest time, newest first, `RECENT_LINKS` kept; a new
    tool session starts its cursor at 0; a failed read leaves the record's list alone."""
    batches: list[tuple[list[Link], int]] = []
    asked: list[tuple[str, int]] = []

    def links(session_id, cwd, profile="", *, cursor=0):
        asked.append((session_id, cursor))
        return batches.pop(0) if batches else ([], cursor)

    monkeypatch.setattr(adapters.get("shell"), "links", links, raising=False)
    async with LocalClient() as person:
        sid = (await person.call("create", name="sh", dir=str(tmp_path), adapter="shell", argv=["bash", "--norc"]))[
            "id"
        ]
        agent._links_checked.clear()
        await agent.tick()
        assert asked == []  # no tool session id yet: nothing to read
        await person.call("hook", session=sid, adapter_id="conv-1")
        batches.append(
            (
                [Link("https://a.example", "2026-10-10T20:00:00Z"), Link("https://b.example", "2026-10-10T20:01:00Z")],
                100,
            )
        )
        agent._links_checked.clear()
        await agent.tick()
        got = (await person.call("get", id=sid))["links"]
        assert got == [
            {"url": "https://b.example", "at": "2026-10-10T20:01:00Z"},
            {"url": "https://a.example", "at": "2026-10-10T20:00:00Z"},
        ]
        await agent.tick()
        assert asked == [("conv-1", 0)]  # once per LINKS_EVERY, not every tick
        # printed again later: once, at the top, from the cursor the last read returned
        batches.append(([Link("https://a.example", "2026-10-10T20:05:00Z")], 160))
        agent._links_checked.clear()
        await agent.tick()
        assert asked[-1] == ("conv-1", 100)
        assert [k["url"] for k in (await person.call("get", id=sid))["links"]] == [
            "https://a.example",
            "https://b.example",
        ]
        # a new tool session reads its own transcript from 0
        await person.call("hook", session=sid, adapter_id="conv-2")
        many = [Link(f"https://x.example/{i}", f"2026-10-10T21:{i:02d}:00Z") for i in range(RECENT_LINKS + 5)]
        batches.append((many, 900))
        agent._links_checked.clear()
        await agent.tick()
        assert asked[-1] == ("conv-2", 0)
        got = (await person.call("list"))[0]["links"]  # on the pushed view too
        assert len(got) == RECENT_LINKS and got[0]["url"] == f"https://x.example/{RECENT_LINKS + 4}"
        # a read that fails leaves the list as it was
        monkeypatch.setattr(adapters.get("shell"), "links", lambda *a, **kw: None, raising=False)
        agent._links_checked.clear()
        await agent.tick()
        assert (await person.call("get", id=sid))["links"] == got
        await person.call("kill", id=sid)
        await person.call("remove", id=sid)
        assert sid not in agent._links_checked and sid not in agent._links_cursor  # no key outlives the record


PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const v = { links: [
  { url: "https://github.com/eyecantell/samscrape/pull/811", at: "2026-10-10T20:05:09Z" },
  { url: "https://github.com/o/r/issues/3#issuecomment-1", at: "2026-10-10T20:04:00Z" },
  { url: "https://github.com/eyecantell/samscrape/actions/runs/38071122904/job/114281004412", at: null },
  { url: "https://docs.pytest.org/en/stable/", at: "2026-10-10T19:48:00Z" },
  { url: "javascript:alert(1)" },
] };
const box = () => { const cls = new Set(["hidden"]); return { innerHTML: "", cls,
  classList: { toggle: (c, f) => (f ? cls.add(c) : cls.delete(c)) } }; };
const row = box(), dt = box();
AO.paintLinks(v, row, dt);
const painted = { html: row.innerHTML, rowHidden: row.cls.has("hidden"), dtHidden: dt.cls.has("hidden") };
AO.paintLinks({ links: [] }, row, dt);
const emptied = { html: row.innerHTML, rowHidden: row.cls.has("hidden"), dtHidden: dt.cls.has("hidden") };
console.log(JSON.stringify({ painted, emptied, html: AO.recentLinks(v), none: AO.recentLinks({}) }));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the Session card's row is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "links_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_row_draws_each_url_short_with_the_full_one_and_its_time_on_hover_opening_a_new_tab():
    got = _probe()
    rows = re.findall(r"<div>(.*?)</div>", got["html"])
    assert len(rows) == 4  # only http and https are links
    texts = [re.sub(r"<[^>]+>", "", r) for r in rows]
    assert texts == [
        "eyecantell/samscrape#811",  # a pull request as owner/repo#n
        "o/r#3",  # an issue too, a fragment after its number or not
        "github.com/eyecantell/samscrap…/job/114281004412",  # the middle elided past forty-eight
        "docs.pytest.org/en/stable/",  # the scheme dropped
    ]
    assert all(len(t) <= 48 for t in texts)
    assert rows[0] == (
        '<a href="https://github.com/eyecantell/samscrape/pull/811" target="_blank" rel="noopener"'
        ' title="https://github.com/eyecantell/samscrape/pull/811 — printed 2026-10-10 20:05Z">'
        "eyecantell/samscrape#811</a>"
    )
    assert 'title="https://github.com/eyecantell/samscrape/actions/runs/38071122904/job/114281004412">' in rows[2]
    assert got["none"] == ""


@pytest.mark.unit
def test_the_paint_shows_the_row_with_a_link_and_hides_it_with_none():
    got = _probe()
    assert not got["painted"]["rowHidden"] and not got["painted"]["dtHidden"]
    assert got["painted"]["html"].count('rel="noopener"') == 4
    assert got["emptied"] == {"html": "", "rowHidden": True, "dtHidden": True}


@pytest.mark.unit
def test_focus_paints_the_links_row_from_each_view():
    js = (UI / "static" / "app.js").read_text()
    focus = js[js.index("AO.focus = function") :]
    render = focus[focus.index("    function render(v) {") :]
    render = render[: render.index("\n    }\n")]
    assert '      AO.paintLinks(v, $("#flinks"), $("#flinksdt"));' in render
    html = (UI / "templates" / "focus.html").read_text()
    assert '<dt id="flinksdt" class="hidden">recent links</dt>' in html and 'id="flinks"' in html
