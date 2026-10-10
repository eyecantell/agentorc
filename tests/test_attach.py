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
        # a piece sent again, behind what is written: refused the same way
        up = (await c.call("attach", id=s.id, name="r.bin", data=_b64(b"0123"), total=12))["upload"]
        await c.call("attach", id=s.id, data=_b64(b"4567"), upload=up, offset=4)
        with pytest.raises(AgentError, match="sent a piece at 4 bytes, but 8 are written"):
            await c.call("attach", id=s.id, data=_b64(b"4567"), upload=up, offset=4)
        assert not list(where.glob("*.part"))
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
        exact = await c.call("attach", id=s.id, name="exact.bin", data=_b64(b"\0" * (1 << 20)))
        assert exact["bytes"] == 1 << 20  # the bound itself fits
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
    idle, fresh = where / "a.bin.0123456789abcdef.part", where / "b.bin.fedcba9876543210.part"
    done, named = where / "c.bin", where / "notes.part"  # finished: a person may name a file `.part`
    for f in (idle, fresh, done, named):
        f.write_bytes(b"x")
    old = datetime.now(UTC).timestamp() - paths.ATTACH_PART_IDLE_S - 60
    for f in (idle, done, named):
        os.utime(f, (old, old))
    await asyncio.to_thread(agent._prune_runs, datetime.now(UTC), set())
    assert not idle.exists() and fresh.exists() and done.exists() and named.exists()


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
    """`POST /api/sessions/<id>/attach`, multipart: a file of one piece goes to `attach` as base64
    under its own name, and the answer is the path for the composer; a piece past
    `paths.ATTACH_PIECE_BYTES` is refused before the host agent is asked (TD-478)."""
    calls: list = []
    c = _client(monkeypatch, tmp_path, calls)
    r = c.post("/api/sessions/ao-x-1/attach", files={"file": ("shot.png", b"PNGDATA", "image/png")})
    assert r.status_code == 200 and r.json() == {"ok": True, "path": "/h/attachments/ao-x-1/shot.png"}
    ((method, kw),) = [x for x in calls if x[0] == "attach"]
    assert kw["id"] == "ao-x-1" and kw["name"] == "shot.png" and base64.b64decode(kw["data"]) == b"PNGDATA"
    assert "upload" not in kw and "total" not in kw
    monkeypatch.setattr(paths, "ATTACH_PIECE_BYTES", 4)
    r = c.post("/api/sessions/ao-x-1/attach", files={"file": ("big.png", b"12345", "image/png")})
    assert r.status_code == 413 and "past the" in r.json()["detail"]
    assert len([x for x in calls if x[0] == "attach"]) == 1


@pytest.mark.unit
def test_the_route_carries_a_pieces_upload_offset_total_and_cancel(tmp_path, monkeypatch):
    """TD-478 (5): the first piece carries `total` and is answered `{upload, bytes}`; a later piece
    names the `upload` and its `offset`, the last answered with the path; `cancel` with the upload
    reaches `attach` as `cancel: true` with no file, and a cancel naming no upload is refused here."""
    calls: list = []
    c = _client(monkeypatch, tmp_path, calls)
    answers = iter([{"upload": "ab" * 8, "bytes": 3}, {"path": "/h/attachments/ao-x-1/deck.pptx", "bytes": 5}])

    async def fake(method, **kw):
        calls.append((method, kw))
        return next(answers) if method == "attach" and not kw.get("cancel") else {"cancelled": True}

    from agentorc.ui import app as ui

    monkeypatch.setattr(ui.LocalClient, "call", lambda self, method, **kw: fake(method, **kw))
    r = c.post("/api/sessions/ao-x-1/attach", data={"total": "5"}, files={"file": ("deck.pptx", b"abc")})
    assert r.json() == {"ok": True, "upload": "ab" * 8, "bytes": 3}
    r = c.post(
        "/api/sessions/ao-x-1/attach", data={"upload": "ab" * 8, "offset": "3"}, files={"file": ("deck.pptx", b"de")}
    )
    assert r.json() == {"ok": True, "path": "/h/attachments/ao-x-1/deck.pptx"}
    r = c.post("/api/sessions/ao-x-1/attach", data={"upload": "cd" * 8, "cancel": "1"})
    assert r.json() == {"ok": True, "cancelled": True}
    first, second, third = (kw for m, kw in calls if m == "attach")
    assert first["total"] == 5 and "upload" not in first and base64.b64decode(first["data"]) == b"abc"
    assert second["upload"] == "ab" * 8 and second["offset"] == 3 and "total" not in second
    assert base64.b64decode(second["data"]) == b"de"
    assert third == {"id": "ao-x-1", "upload": "cd" * 8, "cancel": True}
    r = c.post("/api/sessions/ao-x-1/attach", data={"cancel": "1"})
    assert r.status_code == 400 and "names the upload" in r.json()["detail"]
    assert len([x for x in calls if x[0] == "attach"]) == 3


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


PIECES_PROBE = (
    PRELUDE
    + """
const fileOf = (text) => ({ size: text.length, slice: (a, b) => text.slice(a, b) });
function host(opts) {
  const seen = [];
  let had = 0;
  const post = async (fields) => {
    seen.push(Object.assign({}, fields));
    if (fields.cancel) return { ok: true, cancelled: true };
    if (opts.refuseAt !== undefined && seen.length === opts.refuseAt) throw new Error("upload 12 was cancelled");
    if (!fields.upload && fields.file.length === fields.total) return { ok: true, path: "/a/" + fields.name };
    if (fields.upload && fields.offset !== had) throw new Error("bad offset");
    had += fields.file.length;
    if (had === opts.total) return { ok: true, path: "/a/" + fields.name };
    return { ok: true, upload: "u1", bytes: had };
  };
  return { seen, post };
}
async function run() {
  const out = {};
  {
    const h = host({ total: 10 }), pct = [];
    const path = await AO.uploadPieces(fileOf("0123456789"), { name: "deck.pptx", piece: 4, post: h.post,
      progress: (n, last) => pct.push([n, last]), cancelled: () => false });
    out.whole = { path, pct, seen: h.seen };
  }
  {
    const h = host({ total: 3 }), pct = [];
    const path = await AO.uploadPieces(fileOf("abc"), { name: "s.png", piece: 4, post: h.post,
      progress: (n) => pct.push(n), cancelled: () => true });
    out.one = { path, pct, seen: h.seen };
  }
  {
    const h = host({ total: 0 });
    const path = await AO.uploadPieces(fileOf(""), { name: "empty.txt", piece: 4, post: h.post,
      progress: () => {}, cancelled: () => true });
    out.empty = { path, seen: h.seen };
  }
  {
    const h = host({ total: 10 });
    let asked = 0;
    const path = await AO.uploadPieces(fileOf("0123456789"), { name: "d.pdf", piece: 4, post: h.post,
      progress: () => {}, cancelled: () => ++asked > 0 });
    out.cancelled = { path, seen: h.seen };
  }
  {
    const h = host({ total: 10, refuseAt: 2 });
    let err = "";
    try { await AO.uploadPieces(fileOf("0123456789"), { name: "d.pdf", piece: 4, post: h.post,
      progress: () => {}, cancelled: () => false }); } catch (e) { err = e.message; }
    out.refused = { err, seen: h.seen };
  }
  console.log(JSON.stringify(out));
}
run().catch((e) => { console.error(e); process.exit(1); });
"""
)


@pytest.mark.unit
def test_the_page_sends_a_file_in_pieces_and_cancels_what_is_half_up(tmp_path):
    """TD-478 (6), under node: `AO.uploadPieces` slices a file by the piece size and sends it in
    order — the first piece with `total`, each later one with the `upload` and its `offset` — with
    the percent after each middle piece and the path from the last; a file of one piece is one call
    and is never asked to cancel; the ✕ before a later piece sends `{upload, cancel}` and answers no
    path; a refused piece cancels what is up and throws the host agent's words; an empty file is one call."""
    got = _node(tmp_path, PIECES_PROBE)
    whole = got["whole"]
    assert whole["path"] == "/a/deck.pptx" and whole["pct"] == [[40, False], [80, True]]
    assert [(x.get("upload"), x.get("offset"), x.get("total"), x["file"]) for x in whole["seen"]] == [
        (None, None, 10, "0123"),
        ("u1", 4, None, "4567"),
        ("u1", 8, None, "89"),
    ]
    assert got["one"] == {"path": "/a/s.png", "pct": [], "seen": [{"total": 3, "name": "s.png", "file": "abc"}]}
    assert got["empty"] == {"path": "/a/empty.txt", "seen": [{"total": 0, "name": "empty.txt", "file": ""}]}
    cancelled = got["cancelled"]
    assert cancelled["path"] is None
    assert [x.get("cancel") or x["file"] for x in cancelled["seen"]] == ["0123", "1"]
    assert cancelled["seen"][1] == {"upload": "u1", "cancel": "1"}
    refused = got["refused"]
    assert refused["err"] == "upload 12 was cancelled"
    assert refused["seen"][-1] == {"upload": "u1", "cancel": "1"} and len(refused["seen"]) == 3


PROGRESS_PROBE = (
    PRELUDE
    + """
const tick = () => new Promise((r) => setImmediate(r));
const stub = (extra) => { const on = {}; const cls = new Set(extra && extra.hidden ? ["hidden"] : []);
  return Object.assign({ on,
  classList: { contains: (c) => cls.has(c), add: (c) => cls.add(c), remove: (c) => cls.delete(c),
    toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)) },
  addEventListener: (k, f) => { on[k] = f; }, dispatchEvent: () => {}, focus: () => {}, click: () => {} }, extra); };
async function run() {
  const button = stub({ lastChild: { textContent: "Attach" }, disabled: false });
  const cancel = stub({ hidden: true });
  const compose = stub({ value: "", selectionStart: 0, selectionEnd: 0 });
  let gate, seen = {};
  const upload = async (f, ctl) => {
    if (f.name === "two.bin") {
      ctl.progress(60, true); seen.last_shown = !cancel.classList.contains("hidden"); return "/a/two.bin";
    }
    ctl.progress(37);
    seen.label = button.lastChild.textContent; seen.shown = !cancel.classList.contains("hidden");
    await new Promise((r) => { gate = r; });
    seen.cancelled = ctl.cancelled();
    return ctl.cancelled() ? null : "/a/" + f.name;
  };
  const attach = AO.wireAttach({ button, input: stub({ files: [], value: "" }), composer: stub(), compose,
    targets: [], upload, fail: () => {}, cancel });
  const done = attach([{ name: "deck.pptx" }]);
  await tick(); await tick();
  cancel.on.click();
  seen.hidden_on_press = cancel.classList.contains("hidden");
  gate(); await done;
  seen.cancel_value = compose.value;
  await attach([{ name: "two.bin" }]);
  seen.value = compose.value; seen.after = !cancel.classList.contains("hidden");
  seen.label_after = button.lastChild.textContent;
  console.log(JSON.stringify(seen));
}
run().catch((e) => { console.error(e); process.exit(1); });
"""
)


@pytest.mark.unit
def test_the_composer_shows_an_uploads_percent_and_its_cancel(tmp_path):
    """TD-478 (6), §4.5a *Attach*: past a file's first piece the label reads *Attaching <name> · n%*
    and the ✕ is shown; its press marks the upload cancelled and hides it, and a cancelled upload
    inserts no path; the label is *Attach* again afterwards. Once only the last piece is left the ✕ is
    hidden: that piece links the file into place, past cancelling (the review of #1413)."""
    got = _node(tmp_path, PROGRESS_PROBE)
    assert got == {
        "label": "Attaching deck.pptx · 37%", "shown": True, "hidden_on_press": True, "cancelled": True,
        "cancel_value": "", "value": "/a/two.bin ", "after": False, "label_after": "Attach", "last_shown": False,
    }  # fmt: skip


@pytest.mark.unit
def test_the_focus_page_serves_the_piece_size_and_the_cancel(tmp_path, monkeypatch):
    """TD-478: the Attach button carries the host agent's `paths.ATTACH_PIECE_BYTES`, so the page
    slices by the constant the host agent reads, and the ✕ is drawn hidden beside it."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    from agentorc.ui.app import templates, view

    base = {"id": "ao-x-9", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": str(tmp_path),
            "state": "idle", "since": "2026-10-07T16:00:00Z", "confidence": "hook", "pane": True, "tail": ["…"],
            "created": "2026-10-07T15:00:00Z"}  # fmt: skip
    html = templates.get_template("focus.html").render(
        s={**view(base), "grants_all": [], "ready": []}, host="h", active="Org", popped=False
    )
    assert f'id="attach" data-piece="{paths.ATTACH_PIECE_BYTES}"' in html
    assert 'class="btn sm hidden" id="attachcancel"' in html


@pytest.mark.integration
async def test_a_link_that_fails_leaves_no_part(agent, tmp_path, monkeypatch):
    """TD-478: the last piece's link into place failing spends the upload and deletes its `.part`,
    rather than leaving it for the sweep with nothing able to name it."""
    import os

    from sessionorc.models import Session

    s = Session(id="ao-att-5", name="att", kind="interactive", adapter="shell", dir=str(tmp_path))
    agent.sessions[s.id] = s
    where = paths.attachments_dir() / s.id

    def refuse(src, dst):
        raise PermissionError("no links here")

    async with LocalClient() as c:
        up = (await c.call("attach", id=s.id, name="f.bin", data=_b64(b"0123"), total=8))["upload"]
        monkeypatch.setattr(os, "link", refuse)
        with pytest.raises(AgentError):
            await c.call("attach", id=s.id, data=_b64(b"4567"), upload=up, offset=4)
        assert not list(where.iterdir()) and not agent._uploads


CLIP_PROBE = (
    PRELUDE
    + """
const now = new Date(2026, 9, 7, 17, 4, 9);
const tick = () => new Promise((r) => setImmediate(r));
const blob = (body, type) => new Blob([body], { type });
const item = (parts) => ({ types: Object.keys(parts), getType: async (t) => blob(parts[t], t) });
async function paste(clip) {
  const got = { text: [], file: [], toast: [] };
  await AO.clipPaste(clip, { now, text: (t) => got.text.push(t), toast: (m) => got.toast.push(m),
    file: (f) => got.file.push({ name: f.name, type: f.type, size: f.size }) });
  return got;
}
const stub = (extra) => { const on = {}; return Object.assign({ on, classList: { contains: () => false },
  addEventListener: (k, f) => { on[k] = f; }, dispatchEvent: () => {}, focus: () => {}, click: () => {} }, extra); };
async function run() {
  const out = {};
  out.shot = await paste({ read: async () => [item({ "image/png": "PNGDATA" })], readText: async () => "" });
  out.text = await paste({ read: async () => [item({ "text/plain": "hello", "text/html": "<b>hello</b>" })] });
  out.html_image = await paste({ read: async () => [item({ "text/html": "<img>", "image/png": "P" })] });
  out.empty = await paste({ read: async () => [], readText: async () => "" });
  out.no_read = await paste({ readText: async () => "" });
  out.no_read_text = await paste({ readText: async () => "typed" });
  out.refused = await paste({ read: async () => { throw new Error("denied"); }, readText: async () => "plain" });
  out.blocked = await paste({ readText: async () => { throw new Error("denied"); } });
  out.none = await paste(undefined);
  // the terminal's road: the composer closed, the path to `put`, nothing at the caret
  const uploaded = [], put = [];
  const button = stub({ lastChild: { textContent: "Attach" }, disabled: false });
  const composer = stub({ classList: { contains: (c) => c === "hidden" } });
  const compose = stub({ value: "draft", selectionStart: 5, selectionEnd: 5 });
  const attach = AO.wireAttach({ button, input: stub({ files: [] }), composer, compose, targets: [], fail: () => {},
    upload: async (f) => { uploaded.push(f.name); await tick(); return "/a/" + f.name; } });
  await attach([{ name: "paste-1.png" }], (p) => put.push(p));
  await attach([{ name: "shut.png" }]);
  out.road = { uploaded, put, value: compose.value, label: button.lastChild.textContent, disabled: button.disabled };
  console.log(JSON.stringify(out));
}
run().catch((e) => { console.error(e); process.exit(1); });
"""
)


@pytest.mark.unit
def test_a_screenshot_pasted_on_the_terminal_takes_the_attach_road(tmp_path):
    """§4.5a *Copy / Paste* (TD-472, TD-479), under node: a clipboard holding a file and no text is
    handed to the attach road as a File named for the moment, an image before other types; one
    carrying `text/plain` is the text's and never a file; a browser without `read()` pastes text
    alone and says so when there is none, one that refuses `read()` falls back to `readText()`, and a
    clipboard it cannot read is a toast. The terminal's road attaches with the composer closed and
    hands each path to the caller, never to the composer's caret."""
    got = _node(tmp_path, CLIP_PROBE)
    shot = {"name": "paste-20261007-170409.png", "type": "image/png", "size": 7}
    assert got["shot"] == {"text": [], "file": [shot], "toast": []}
    assert got["text"] == {"text": ["hello"], "file": [], "toast": []}
    assert got["html_image"]["file"] == [{**shot, "size": 1}] and got["html_image"]["text"] == []
    assert got["empty"] == {"text": [], "file": [], "toast": []}
    assert got["no_read"] == {"text": [], "file": [], "toast": ["this browser pastes text only"]}
    assert got["no_read_text"] == {"text": ["typed"], "file": [], "toast": []}
    assert got["refused"] == {"text": ["plain"], "file": [], "toast": []}
    blocked = {"text": [], "file": [], "toast": ["clipboard blocked (needs https or localhost)"]}
    assert got["blocked"] == blocked and got["none"] == blocked
    assert got["road"] == {
        "uploaded": ["paste-1.png"], "put": ["/a/paste-1.png"], "value": "draft", "label": "Attach", "disabled": False,
    }  # fmt: skip


@pytest.mark.unit
def test_the_terminals_paste_reads_nothing_on_a_read_only_focus():
    """§4.5a *Copy / Paste*: Paste is inert on a read-only Focus — the toast comes before the clipboard
    is read — and every paste, text or file, goes through `AO.clipPaste` to the terminal."""
    js = (UI / "static" / "app.js").read_text()
    body = js[js.index("const pasteClip = () => {") :]
    body = body[: body.index("\n    };\n")]
    ro = body.index('if (readOnly) { AO.toast("watching: paste is off — Take over to type"); return; }')
    assert ro < body.index("AO.clipPaste(navigator.clipboard") and "readText" not in body
    assert "attachFiles([f], (path) => { if (live()) term.paste(path); })" in body
    assert "attachFiles = AO.wireAttach({" in js
