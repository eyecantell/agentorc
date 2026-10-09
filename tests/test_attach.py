"""The Focus composer's **Attach** / drop / paste (design §4.5a *Focus composer*, §4.4 *Attachment
drop*, TD-002): the host agent's `attach` writes the person's file under `attachments/<session>/`
and answers its path; the page's route hands it the upload; the composer draws the button; and the
JavaScript that names a pasted image and inserts the path runs as itself under node."""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import shutil
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient

from sessionorc import paths
from sessionorc.client import AgentError, LocalClient

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.mark.integration
async def test_attach_writes_the_file_under_the_sessions_attachments_and_answers_its_path(agent, tmp_path):
    """A pasted screenshot lands as a path Claude Code can read (TD-002's *Done when*): the bytes as
    sent, `0600`, under `attachments/<session>/`, by a name a prompt needs no quoting for; a second
    file of that name is `-2`, never over the first; past the bound, not base64, or no such
    session, refused."""
    from sessionorc.models import Session

    s = Session(id="ao-att-1", name="att", kind="interactive", adapter="shell", dir=str(tmp_path))
    agent.sessions[s.id] = s
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    async with LocalClient() as c:
        got = await c.call("attach", id=s.id, name="Screen Shot 1.png", data=_b64(png))
        again = await c.call("attach", id=s.id, name="Screen Shot 1.png", data=_b64(b"second"))
        first, second = pathlib.Path(got["path"]), pathlib.Path(again["path"])
        assert first == paths.attachments_dir() / "ao-att-1" / "Screen_Shot_1.png" and got["bytes"] == len(png)
        assert first.read_bytes() == png and stat.S_IMODE(first.stat().st_mode) == 0o600
        assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700  # a person's screenshots, listable by nobody else
        assert second.name == "Screen_Shot_1-2.png" and second.read_bytes() == b"second"
        traversal = await c.call("attach", id=s.id, name="../../settings.yml", data=_b64(b"x"))
        assert pathlib.Path(traversal["path"]).parent == first.parent  # its last component only
        (paths.home() / "settings.yml").write_text("person: {attach: {max: 1M}}\n")
        with pytest.raises(AgentError, match="big.bin is 2 MiB, past the 1 MiB a file may be"):
            await c.call("attach", id=s.id, name="big.bin", data=_b64(b"\0" * ((1 << 20) + 1)))
        assert not (first.parent / "big.bin").exists()
        with pytest.raises(AgentError, match="did not arrive as base64"):
            await c.call("attach", id=s.id, name="x.txt", data="not base64!")
        with pytest.raises(AgentError, match="no session ao-nobody"):
            await c.call("attach", id="ao-nobody", name="x.txt", data=_b64(b"x"))


@pytest.mark.integration
async def test_attach_in_pieces_links_the_whole_file_into_place(agent, tmp_path):
    """§4.4 *Attachment drop* in pieces (TD-478): a five-piece upload lands whole, named by the rule at
    the moment it is linked (a file of that name written meanwhile makes it `-2`), its `.part` gone; a
    piece at the wrong offset is refused and leaves no `.part`; a first piece whose `total` is past
    `person.attach.max` is refused with nothing written; a cancel removes the `.part`; an upload is
    its session's alone."""
    from sessionorc.models import Session

    s = Session(id="ao-att-2", name="att", kind="interactive", adapter="shell", dir=str(tmp_path))
    other = Session(id="ao-att-3", name="att3", kind="interactive", adapter="shell", dir=str(tmp_path))
    agent.sessions[s.id] = s
    agent.sessions[other.id] = other
    where = paths.attachments_dir() / s.id
    body = bytes(range(256)) * 20  # 5120 bytes, sent as five pieces of 1024
    pieces = [body[i : i + 1024] for i in range(0, len(body), 1024)]
    async with LocalClient() as c:
        got = await c.call("attach", id=s.id, name="My Deck.pptx", data=_b64(pieces[0]), total=len(body))
        up = got["upload"]
        assert got == {"upload": up, "bytes": 1024}
        (part,) = where.glob("*.part")
        assert part.name == f"My_Deck.pptx.{up}.part" and stat.S_IMODE(part.stat().st_mode) == 0o600
        with pytest.raises(AgentError, match=f"no upload {up} for ao-att-3"):
            await c.call("attach", id=other.id, data=_b64(pieces[1]), upload=up, offset=1024)
        for i, piece in enumerate(pieces[1:4], start=1):
            got = await c.call("attach", id=s.id, data=_b64(piece), upload=up, offset=i * 1024)
            assert got == {"upload": up, "bytes": (i + 1) * 1024}
        assert not list(where.glob("My_Deck*.pptx"))  # nothing a prompt can name until it is whole
        (where / "My_Deck.pptx").write_bytes(b"sent meanwhile")
        got = await c.call("attach", id=s.id, data=_b64(pieces[4]), upload=up, offset=4096)
        assert got == {"path": str(where / "My_Deck-2.pptx"), "bytes": len(body)}
        assert (where / "My_Deck-2.pptx").read_bytes() == body and (
            where / "My_Deck.pptx"
        ).read_bytes() == b"sent meanwhile"
        assert not list(where.glob("*.part"))
        with pytest.raises(AgentError, match=f"no upload {up}"):  # finished: the id names nothing now
            await c.call("attach", id=s.id, data=_b64(b"x"), upload=up, offset=len(body))

        # out of order: refused, and the .part goes with it
        up = (await c.call("attach", id=s.id, name="b.bin", data=_b64(b"0123"), total=12))["upload"]
        with pytest.raises(AgentError, match="sent a piece at 8 bytes, but 4 are written: the upload is dropped"):
            await c.call("attach", id=s.id, data=_b64(b"89ab"), upload=up, offset=8)
        assert not list(where.glob("*.part"))
        with pytest.raises(AgentError, match=f"no upload {up}"):
            await c.call("attach", id=s.id, data=_b64(b"4567"), upload=up, offset=4)
        # past the total: refused the same way
        up = (await c.call("attach", id=s.id, name="c.bin", data=_b64(b"0123"), total=6))["upload"]
        with pytest.raises(AgentError, match="sent 8 bytes of a 6-byte file"):
            await c.call("attach", id=s.id, data=_b64(b"4567"), upload=up, offset=4)
        assert not list(where.glob("*.part"))

        # cancel: the .part deleted, the id spent
        up = (await c.call("attach", id=s.id, name="d.bin", data=_b64(b"0123"), total=8))["upload"]
        assert await c.call("attach", id=s.id, upload=up, cancel=True) == {"upload": up, "cancelled": True}
        assert not list(where.glob("*.part"))
        with pytest.raises(AgentError, match="cancel names the upload"):
            await c.call("attach", id=s.id, cancel=True)

        # past the bound on the first piece: nothing written, no upload minted
        (paths.home() / "settings.yml").write_text("person: {attach: {max: 1M}}\n")
        with pytest.raises(AgentError, match="deck.pptx is 612 MiB, past the 1 MiB a file may be"):
            await c.call("attach", id=s.id, name="deck.pptx", data=_b64(b"0123"), total=612 << 20)
        assert not list(where.glob("deck*")) and not agent._uploads
        with pytest.raises(AgentError, match="total is the file's whole size"):
            await c.call("attach", id=s.id, name="e.bin", data=_b64(b"0123"), total=2)


@pytest.mark.integration
async def test_the_sweep_takes_an_hour_idle_part_whatever_runs_keep_days(agent, tmp_path):
    """§4.6 *Run-log retention*, §4.4 *Attachment drop* (TD-478): a `.part` nothing has written to for
    an hour goes on the hourly sweep even with `runs_keep_days: 0`, which keeps everything else; a
    fresh `.part` and a finished attachment stay."""
    import os
    from datetime import UTC, datetime

    (paths.home() / "hosts.yml").write_text("local:\n  runs_keep_days: 0\n")
    where = paths.attachments_dir() / "ao-att-4"
    where.mkdir(parents=True)
    idle, fresh, done = where / "a.bin.1.part", where / "b.bin.2.part", where / "c.bin"
    for f in (idle, fresh, done):
        f.write_bytes(b"x")
    old = datetime.now(UTC).timestamp() - paths.ATTACH_PART_IDLE_S - 60
    for f in (idle, done):
        os.utime(f, (old, old))
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), set())
    assert not idle.exists() and fresh.exists() and done.exists()


@pytest.mark.unit
def test_attachment_names_need_no_quoting_in_a_prompt():
    assert paths.attachment_name("Screenshot 2026-10-07 at 5.12.png") == "Screenshot_2026-10-07_at_5.12.png"
    assert paths.attachment_name("../../etc/passwd") == "passwd"
    assert paths.attachment_name("C:\\Users\\p\\my file.txt") == "my_file.txt"
    assert paths.attachment_name(".bashrc") == "bashrc"
    assert paths.attachment_name("..") == paths.attachment_name("") == "attachment"
    long = paths.attachment_name("a" * 150 + ".png")
    assert len(long) == 100 and long.endswith(".png")


def _client(monkeypatch, tmp_path, calls):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    from agentorc.ui import app as ui

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call(self, method, **kw):
            calls.append((method, kw))
            if method == "attach":
                return {"path": f"/h/attachments/{kw['id']}/{kw['name']}", "bytes": len(base64.b64decode(kw["data"]))}
            return {}

    monkeypatch.setattr(ui, "LocalClient", Fake)
    return TestClient(ui.create_app())


@pytest.mark.unit
def test_the_route_hands_the_upload_to_the_host_agent_and_answers_the_path(tmp_path, monkeypatch):
    """`POST /api/sessions/<id>/attach`, multipart: the file goes to `attach` as base64 under its own
    name, and the answer is the path for the composer; one past the bound is refused before the host
    agent is asked."""
    calls: list = []
    c = _client(monkeypatch, tmp_path, calls)
    r = c.post("/api/sessions/ao-x-1/attach", files={"file": ("shot.png", b"PNGDATA", "image/png")})
    assert r.status_code == 200 and r.json() == {"ok": True, "path": "/h/attachments/ao-x-1/shot.png"}
    ((method, kw),) = [x for x in calls if x[0] == "attach"]
    assert kw["id"] == "ao-x-1" and kw["name"] == "shot.png" and base64.b64decode(kw["data"]) == b"PNGDATA"
    monkeypatch.setattr(paths, "ATTACH_BYTES_MAX", 4)
    r = c.post("/api/sessions/ao-x-1/attach", files={"file": ("big.png", b"12345", "image/png")})
    assert r.status_code == 413 and "past the" in r.json()["detail"]
    assert len([x for x in calls if x[0] == "attach"]) == 1


@pytest.mark.unit
def test_the_composer_draws_attach_beside_send(tmp_path, monkeypatch):
    """§4.5a *Focus composer*: **Attach** in the composer's row, with the picker it opens; the
    composer, and so Attach, is closed on an unattended session."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {"id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "idle", "since": "2026-10-07T16:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
            "created": "2026-10-07T15:00:00Z"}  # fmt: skip
    html = templates.get_template("focus.html").render(
        s={**view(base), "grants_all": [], "ready": []}, host="h", active="Org", popped=False
    )
    composer = html[html.index('id="composer"') :]
    composer = composer[: composer.index('id="send"')]
    assert 'id="attach"' in composer and ">Attach</button>" in composer
    assert '<input type="file" id="attachfile" multiple hidden>' in composer


PRELUDE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], contains: () => false });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: () => el() };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
"""

PROBE = (
    PRELUDE
    + """
const now = new Date(2026, 9, 7, 17, 4, 9);
const box = (value, a, b) => ({ value, selectionStart: a, selectionEnd: b === undefined ? a : b });
const at = (b, t) => { AO.insertAtCaret(b, t); return [b.value, b.selectionStart]; };
console.log(JSON.stringify({
  named: AO.attachName({ name: "spec.pdf", type: "application/pdf" }, now),
  pasted: AO.attachName({ name: "image.png", type: "image/png" }, now),
  unnamed: AO.attachName({ name: "", type: "image/jpeg" }, now),
  empty: at(box("", 0), "/a/x.png"),
  after_word: at(box("look at", 7), "/a/x.png"),
  mid: at(box("see  please", 4), "/a/x.png"),
  over_selection: at(box("see THIS please", 4, 8), "/a/x.png"),
}));
"""
)


def _node(tmp_path, probe):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    script = tmp_path / "attach_probe.js"
    script.write_text(probe)
    out = subprocess.run([node, str(script), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.unit
def test_the_composers_attach_rules_run_as_themselves(tmp_path):
    """The JavaScript half, under node: a pasted image, which the browser always calls `image.png`,
    is named for the moment; any other file keeps its name; the path goes in at the caret, over a
    selection, a space either side where the neighbour is not one, the caret after it."""
    got = _node(tmp_path, PROBE)
    assert got["named"] == "spec.pdf"
    assert got["pasted"] == "paste-20261007-170409.png" and got["unnamed"] == "paste-20261007-170409.jpeg"
    assert got["empty"] == ["/a/x.png ", 9]
    assert got["after_word"] == ["look at /a/x.png ", 17]
    assert got["mid"] == ["see /a/x.png please", 12]
    assert got["over_selection"] == ["see /a/x.png please", 12]


WIRE_PROBE = (
    PRELUDE
    + """
const tick = () => new Promise((r) => setImmediate(r));
const stub = (extra) => { const on = {}; return Object.assign({ on, classList: { contains: () => false },
  addEventListener: (k, f) => { on[k] = f; }, dispatchEvent: () => {}, focus: () => {}, click: () => {} }, extra); };
const ev = (kind, files, types) => { const e = { prevented: false, preventDefault() { this.prevented = true; } };
  e[kind] = { files, types: types || (files.length ? ["Files"] : []) }; return e; };
const file = (name) => ({ name, type: "image/png" });
async function run() {
  let hidden = false, live = 0, most = 0;
  const uploaded = [], failed = [];
  const button = stub({ lastChild: { textContent: "Attach" }, disabled: false });
  const input = stub({ files: [], value: "x" });
  const composer = stub({ classList: { contains: (c) => c === "hidden" && hidden } });
  const compose = stub({ value: "", selectionStart: 0, selectionEnd: 0 });
  const term = stub();
  const upload = async (f) => {
    live++; most = Math.max(most, live); await tick(); await tick(); live--;
    if (f.name === "bad.png") throw new Error("refused");
    uploaded.push(f.name); return "/a/" + f.name;
  };
  const attach = AO.wireAttach({ button, input, composer, compose, targets: [term, compose], upload,
    fail: (m) => failed.push(m) });
  const out = {};
  const over = ev("dataTransfer", [], ["Files"]); term.on.dragover(over); out.dragover_open = over.prevented;
  // the refused file first in its drop (TD-376): the rest of that same drop is still sent
  const drop = ev("dataTransfer", [file("bad.png"), file("one.png")]); term.on.drop(drop);
  const drop2 = ev("dataTransfer", [file("two.png")]); compose.on.drop(drop2);
  await tick();
  out.during = { disabled: button.disabled, label: button.lastChild.textContent };
  await attach([]);
  out.drop = { prevented: drop.prevented && drop2.prevented, uploaded: [...uploaded], most, failed: [...failed],
    value: compose.value, disabled: button.disabled, label: button.lastChild.textContent };
  const text = ev("clipboardData", [file("image.png")], ["Files", "text/plain"]); compose.on.paste(text);
  const image = ev("clipboardData", [file("shot.png")], ["Files"]); compose.on.paste(image);
  await attach([]);
  out.paste = { text_prevented: text.prevented, image_prevented: image.prevented, uploaded: [...uploaded] };
  hidden = true;
  const shutOver = ev("dataTransfer", [], ["Files"]); term.on.dragover(shutOver);
  const shutDrop = ev("dataTransfer", [file("late.png")]); term.on.drop(shutDrop);
  const shutPaste = ev("clipboardData", [file("late.png")], ["Files"]); compose.on.paste(shutPaste);
  input.files = [file("late.png")]; input.on.change();
  await attach([]); await tick();
  out.shut = { prevented: shutOver.prevented || shutDrop.prevented || shutPaste.prevented,
    uploaded: [...uploaded], input_value: input.value };
  console.log(JSON.stringify(out));
}
run().catch((e) => { console.error(e); process.exit(1); });
"""
)


@pytest.mark.unit
def test_the_composers_drop_and_paste_handlers_run_as_themselves(tmp_path):
    """TD-370: the handlers `AO.wireAttach` hangs on the composer, driven under node against stubs —
    a drop on the terminal or the composer attaches its files one upload at a time, a failed one
    reported and the next file of the same drop still sent (TD-376: the refused one first), each path
    at the caret; a paste carrying `text/plain` is the
    text's; with the composer closed (an unattended session) no dragover, drop, paste or pick
    attaches anything."""
    got = _node(tmp_path, WIRE_PROBE)
    assert got["dragover_open"] is True
    assert got["during"] == {"disabled": True, "label": "Attaching bad.png…"}
    assert got["drop"] == {
        "prevented": True, "uploaded": ["one.png", "two.png"], "most": 1, "failed": ["Attach failed: refused"],
        "value": "/a/one.png /a/two.png ", "disabled": False, "label": "Attach",
    }  # fmt: skip
    assert got["paste"] == {
        "text_prevented": False,
        "image_prevented": True,
        "uploaded": ["one.png", "two.png", "shot.png"],
    }
    assert got["shut"] == {"prevented": False, "uploaded": ["one.png", "two.png", "shot.png"], "input_value": ""}
