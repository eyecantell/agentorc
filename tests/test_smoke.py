"""`scripts/smoke.py`, this repo's smoke (design §6 *The smoke*, *This repo's smoke*; TD-812): against a stub
server and a stub `ao`, never the live UI."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from shiftlead import service

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke.py"
PAGE = b"<!doctype html><html><body>ok</body></html>\n"


def _load():
    spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sm = _load()


@pytest.fixture
def stub():
    """A UI that answers every page, and `behave[route]` to break one: an int is its status, `cut` a body
    with no `</html>`, a float seconds of delay. `seen` is every route asked."""
    behave: dict[str, object] = {}
    seen: list[str] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            b = behave.get(self.path)
            if isinstance(b, float):
                time.sleep(b)
            body = PAGE[:20] if b == "cut" else PAGE
            self.send_response(b if isinstance(b, int) else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], behave, seen
    srv.shutdown()
    srv.server_close()


def _unit(tmp_path: Path, port: int, bind: str = "127.0.0.1") -> Path:
    u = tmp_path / "shiftlead-ui.service"
    u.write_text(service.unit_text("shiftlead-ui", bind=bind, port=port))
    return u


def _ao(tmp_path: Path, fail: str | None = None) -> str:
    """A stub `ao` that answers JSON, or exits 2 on the subcommand `fail`; it logs every argv."""
    a = tmp_path / "ao"
    a.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {tmp_path}/ao.log\n'
        f'[ "$1" = "{fail}" ] && {{ echo "boom: $1" >&2; exit 2; }}\n'
        "echo '{\"ok\": true}'\n"
    )
    a.chmod(0o755)
    return str(a)


def _run(tmp_path, port, capsys, **kw):
    kw.setdefault("unit", _unit(tmp_path, port))
    kw.setdefault("venv", tmp_path / "no-venv")
    if "ao" not in kw:
        kw["ao"] = _ao(tmp_path)
    code = sm.smoke(wait_bound=kw.pop("wait_bound", 3.0), **kw)
    lines = capsys.readouterr().out.strip().splitlines()
    return code, lines


def test_the_page_list_has_no_focus_and_the_reads_no_inbox():
    routes = [r for _, r in sm.PAGES]
    assert routes == ["/", "/inbox", "/queue", "/repo/shiftlead", "/settings"]
    assert not any(r.startswith("/focus") for r in routes)
    assert all(a[0] != "inbox" for a in sm.AO_READS)
    assert sm.PAGE_BOUND == 20.0 and sm.WAIT_BOUND == 90.0


def test_the_address_is_the_units_bind_and_port_and_the_defaults_without_one(tmp_path):
    assert sm.address(_unit(tmp_path, 8799, "100.64.0.5")) == ("100.64.0.5", 8799)
    assert sm.address(_unit(tmp_path, 8799, "0.0.0.0")) == ("127.0.0.1", 8799)
    u = tmp_path / "shiftlead-ui.service"
    u.write_text("[Service]\nExecStart=/v/bin/shiftlead-ui\n")
    assert sm.address(u) == ("127.0.0.1", service.DEFAULT_PORT)
    assert sm.address(tmp_path / "absent.service") == ("127.0.0.1", service.DEFAULT_PORT)
    assert sm.DEFAULT_PORT == service.DEFAULT_PORT and sm.UNIT == service.UNIT_DIR / "shiftlead-ui.service"


def test_a_healthy_ui_passes_with_a_line_per_load_and_the_browser_half_skipped(tmp_path, stub, capsys):
    port, _, seen = stub
    code, lines = _run(tmp_path, port, capsys)
    assert code == 0 and lines[-1] == f"smoke passed; {sm.SKIPPED}"
    assert sorted(seen) == sorted(r for _, r in sm.PAGES)
    assert any(ln.startswith("Inbox /inbox · 200 · first byte ") and ln.endswith(f"{len(PAGE)} bytes") for ln in lines)
    assert (tmp_path / "ao.log").read_text().splitlines() == ["status --json", "repo --json", "promote status --json"]


@pytest.mark.parametrize(
    ("behave", "why"),
    [
        (500, "smoke failed: Queue /queue: status 500"),
        ("cut", "smoke failed: Queue /queue: the body does not end its </html> (20 bytes)"),
        (1.5, "smoke failed: Queue /queue: no answer within 1 s"),
    ],
)
def test_each_page_failure_is_the_last_line(tmp_path, stub, capsys, behave, why):
    port, b, _ = stub
    b["/queue"] = behave
    code, lines = _run(tmp_path, port, capsys, bound=1.0)
    assert code == 1 and lines[-1].startswith(why)


def test_a_ui_that_never_answers_fails_and_an_ao_read_that_fails_names_it(tmp_path, stub, capsys):
    port, _, _ = stub
    code, lines = _run(tmp_path, sm_free_port(), capsys, wait_bound=1.0)
    assert code == 1 and lines[-1].startswith("smoke failed: the UI did not answer on 127.0.0.1:")
    code, lines = _run(tmp_path, port, capsys, ao=_ao(tmp_path, fail="repo"))
    assert code == 1 and lines[-1] == "smoke failed: ao repo --json exited 2: boom: repo"


def sm_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_reach_loads_are_told_and_a_refusal_fails_nothing(tmp_path, stub, capsys):
    port, _, _ = stub
    srv_reach = _Refusing()  # the route the person uses, refusing the Queue
    try:
        code, lines = _run(tmp_path, port, capsys, reach=srv_reach.url)
    finally:
        srv_reach.close()
    assert code == 0
    assert any(ln.startswith("reach Org / · 200") for ln in lines)
    assert "reach (fails nothing): Queue /queue: status 403" in lines
    assert lines[-1] == f"smoke passed; reach refused /queue: status 403; {sm.SKIPPED}"


class _Refusing:
    """A reach route that lets the Org in and refuses the Queue."""

    def __init__(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.path != "/queue"
                self.send_response(200 if ok else 403)
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _venv(tmp_path: Path, rows: list[dict], code: int = 0) -> Path:
    """A stand-in smoke venv whose `python` prints the browser half's JSON lines."""
    v = tmp_path / "venv"
    (v / "bin").mkdir(parents=True)
    py = v / "bin" / "python"
    out = "".join(f"echo '{json.dumps(r)}'\n" for r in rows)
    py.write_text(f'#!/bin/sh\n[ "$2" = "--browser" ] || exit 9\n{out}exit {code}\n')
    py.chmod(0o755)
    return v


def test_the_browser_half_runs_under_the_smoke_venv_and_a_script_error_fails(tmp_path, stub, capsys):
    port, _, _ = stub
    ok = [{"page": p, "route": r, "error": None} for p, r in sm.PAGES]
    code, lines = _run(tmp_path, port, capsys, venv=_venv(tmp_path / "a", ok))
    assert code == 0 and lines[-1] == "smoke passed" and "browser Queue /queue · ok" in lines
    bad = [*ok[:2], {"page": "Queue", "route": "/queue", "error": "script error: TypeError: x is undefined"}]
    code, lines = _run(tmp_path, port, capsys, venv=_venv(tmp_path / "b", bad))
    assert code == 1 and lines[-1] == "smoke failed: Queue /queue: script error: TypeError: x is undefined"
    code, lines = _run(tmp_path, port, capsys, venv=_venv(tmp_path / "c", ok[:1], code=3))
    assert code == 1 and lines[-1].startswith("smoke failed: the browser half exited 3")


def test_the_cli_takes_setup_and_the_shiftlead_yml_names_the_smoke():
    import yaml

    block = yaml.safe_load((SCRIPT.parent.parent / ".shiftlead.yml").read_text())["promote"]
    assert "scripts/smoke.py" in block["smoke"] and "shiftlead-venv/bin/python" in block["smoke"]
    with pytest.raises(SystemExit):
        sm.main(["--help"])
