"""The Focus composer's **Attach** / drop / paste (design §4.5a *Focus composer*, §4.4 *Attachment
drop*, TD-002): the host agent's `attach` writes the person's file under `attachments/<session>/`
and answers its path; the page's route hands it the upload; the composer draws the button; and the
JavaScript that names a pasted image and inserts the path runs as itself under node."""

from __future__ import annotations

import base64
import json
import pathlib
import shutil
import stat
import subprocess
import tempfile

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
        assert second.name == "Screen_Shot_1-2.png" and second.read_bytes() == b"second"
        traversal = await c.call("attach", id=s.id, name="../../settings.yml", data=_b64(b"x"))
        assert pathlib.Path(traversal["path"]).parent == first.parent  # its last component only
        with pytest.raises(AgentError, match="past the 4 MiB"):
            await c.call("attach", id=s.id, name="big.bin", data=_b64(b"\0" * (paths.ATTACH_BYTES_MAX + 1)))
        with pytest.raises(AgentError, match="did not arrive as base64"):
            await c.call("attach", id=s.id, name="x.txt", data="not base64!")
        with pytest.raises(AgentError, match="no session ao-nobody"):
            await c.call("attach", id="ao-nobody", name="x.txt", data=_b64(b"x"))


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


PROBE = """
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


@pytest.mark.unit
def test_the_composers_attach_rules_run_as_themselves():
    """The JavaScript half, under node: a pasted image, which the browser always calls `image.png`,
    is named for the moment; any other file keeps its name; the path goes in at the caret, over a
    selection, a space either side where the neighbour is not one, the caret after it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the rule is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "attach_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["named"] == "spec.pdf"
    assert got["pasted"] == "paste-20261007-170409.png" and got["unnamed"] == "paste-20261007-170409.jpeg"
    assert got["empty"] == ["/a/x.png ", 9]
    assert got["after_word"] == ["look at /a/x.png ", 17]
    assert got["mid"] == ["see /a/x.png please", 12]
    assert got["over_selection"] == ["see /a/x.png please", 12]
