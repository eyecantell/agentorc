#!/usr/bin/env python3
"""This repo's smoke (design §6 *The smoke*, *This repo's smoke*; TD-812): once a promote's `check` reads
the new commit live, load every page and read `ao` against the live copy, so a build that breaks on the
person's real state is put back within minutes. `.shiftlead.yml`'s `smoke:` runs it in the tree, under
the live venv's interpreter.

It presses nothing and changes nothing: GETs and reads only. It loads no Focus — opening one is the
person's *seen* (§4.2) and its attach sizes the pane (§4.6) — and never runs `ao inbox`, which marks
entries read (§4.10).

- It waits up to `WAIT_BOUND` for the UI's plain port, read from the installed unit's `ExecStart`
  (`--bind`, `--port`): loopback when `--bind` is absent or `0.0.0.0`, `DEFAULT_PORT` when the unit is
  absent or names no port.
- A page fails on a status other than 200, a body that does not end its `</html>`, a load past
  `PAGE_BOUND`, and in the browser half an uncaught script error or a `load` that never fires.
- Where `settings.yml` has `reach:`, the Org and the Queue load once more through `reach.url`: told in the
  log, failing nothing.
- `ao status --json`, `ao repo --json` and `ao promote status --json` must each exit 0 with JSON.

One log line per load (page, route, status, first byte, total, bytes). Exit 0 when all pass, else 1 with
the first failure as the last line.

The browser half needs Playwright and its Chromium headless shell, in a venv of their own that
`scripts/smoke.py --setup` makes once, at a person's hand, in `SMOKE_VENV` — never inside the live venv.
The script re-runs itself under that venv's python for the browser half (`--browser`); without it, the
HTTP half alone judges.

    "$HOME/.local/share/shiftlead-venv/bin/python" scripts/smoke.py
    "$HOME/.local/share/shiftlead-venv/bin/python" scripts/smoke.py --setup
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PAGES = (("Org", "/"), ("Inbox", "/inbox"), ("Queue", "/queue"), ("Repo", "/repo/shiftlead"), ("Settings", "/settings"))
REACH_PAGES = (("Org", "/"), ("Queue", "/queue"))  # the route the person uses: told, failing nothing
AO_READS = (("status", "--json"), ("repo", "--json"), ("promote", "status", "--json"))  # never `ao inbox`
PAGE_BOUND = 20.0  # seconds a load may take: the smoke's own, not a page budget (those are TD-805's)
WAIT_BOUND = 90.0  # seconds for the UI's port to answer: the run restarts the UI after the agent
AO_BOUND = 60.0
SMOKE_VENV = Path("~/.local/share/shiftlead-smoke").expanduser()
UNIT = Path("~/.config/systemd/user/shiftlead-ui.service").expanduser()  # service.UNIT_DIR's
DEFAULT_PORT = 8765  # service.DEFAULT_PORT
SKIPPED = "browser half skipped: run scripts/smoke.py --setup"


class Failed(Exception):
    """The first failure: the smoke's last line."""


def address(unit: Path = UNIT) -> tuple[str, int]:
    """The UI's plain address from the installed unit's `ExecStart`: `--bind` (loopback when absent or
    `0.0.0.0`) and `--port` (`DEFAULT_PORT` when the unit is absent or names none)."""
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        text = ""
    line = next((ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("ExecStart=")), "")
    try:
        argv = shlex.split(line)
    except ValueError:
        argv = line.split()

    def opt(name: str) -> str | None:
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return None

    bind = opt("--bind")
    host = "127.0.0.1" if bind in (None, "", "0.0.0.0", "::") else bind
    port = opt("--port")
    return host, int(port) if port and port.isdigit() else DEFAULT_PORT


def wait_for(host: str, port: int, bound: float = WAIT_BOUND) -> None:
    """Until the port takes a connection, up to `bound` seconds."""
    end = time.monotonic() + bound
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError as e:
            if time.monotonic() >= end:
                raise Failed(f"the UI did not answer on {host}:{port} within {bound:g} s: {e}") from None
            time.sleep(1)


def load(base: str, page: str, route: str, bound: float = PAGE_BOUND) -> tuple[str, str | None]:
    """One GET: `(the log line, why it failed or None)`."""
    t0 = time.monotonic()
    first, body, status, why = None, b"", None, None
    try:
        with urllib.request.urlopen(base + route, timeout=bound) as r:
            first, status = time.monotonic() - t0, r.status
            body = r.read()
    except urllib.error.HTTPError as e:
        first, status = time.monotonic() - t0, e.code
        body = e.read() or b""
    except (OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        if isinstance(reason, TimeoutError):  # the socket's own bound, which is the page's
            why = f"{page} {route}: no answer within {bound:g} s"
        else:
            why = f"{page} {route}: {reason}"
    total = time.monotonic() - t0
    if why is None and status != 200:
        why = f"{page} {route}: status {status}"
    elif why is None and not body.rstrip().lower().endswith(b"</html>"):
        why = f"{page} {route}: the body does not end its </html> ({len(body)} bytes)"
    elif why is None and total > bound:
        why = f"{page} {route}: took {total:.1f} s, past {bound:g} s"
    fb = f"{first:.2f} s" if first is not None else "-"
    line = f"{page} {route} · {status or '-'} · first byte {fb} · total {total:.2f} s · {len(body)} bytes"
    return line, why


def reach_url(settings: Path | None = None) -> str | None:
    """`reach.url` from `settings.yml`, or None: absent, malformed, or the file unreadable."""
    try:
        from sessionorc import settings as settings_mod

        reach = settings_mod.reach(settings_mod.load(settings))
    except Exception:  # the reach loads fail nothing, and so does not reading them
        return None
    return (reach or {}).get("url") or None


def ao_reads(ao: str) -> list[str]:
    """Each read must exit 0 with JSON; raises Failed on the first that does not. Returns its log lines."""
    out = []
    for args in AO_READS:
        cmd = f"ao {' '.join(args)}"
        try:
            p = subprocess.run([ao, *args], capture_output=True, text=True, timeout=AO_BOUND, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Failed(f"{cmd}: {e}") from None
        if p.returncode != 0:
            last = next((ln for ln in reversed((p.stderr or p.stdout).splitlines()) if ln.strip()), "")
            raise Failed(f"{cmd} exited {p.returncode}: {last}")
        try:
            json.loads(p.stdout)
        except ValueError:
            raise Failed(f"{cmd}: its output is not JSON") from None
        out.append(f"{cmd} · exit 0 · {len(p.stdout)} bytes of JSON")
    return out


def browser(python: Path, base: str, bound: float = PAGE_BOUND) -> tuple[list[str], str | None]:
    """The browser half, run as this script under `python` (`--browser`): `(log lines, why or None)`."""
    try:
        p = subprocess.run(
            [str(python), str(Path(__file__).resolve()), "--browser", base],
            capture_output=True, text=True, timeout=bound * len(PAGES) + 60, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"the browser half did not finish: {e}"
    lines, why = [], None
    for ln in p.stdout.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        lines.append(f"browser {r.get('page')} {r.get('route')} · {'ok' if not r.get('error') else r['error']}")
        if r.get("error") and why is None:
            why = f"{r.get('page')} {r.get('route')}: {r['error']}"
    if why is None and p.returncode != 0:
        why = f"the browser half exited {p.returncode}: " + (p.stderr.strip().splitlines() or ["no output"])[-1]
    return lines, why


def browse(base: str, bound: float = PAGE_BOUND) -> int:
    """`--browser`, under the smoke venv: each page loaded in headless Chromium; one JSON line per page,
    its `error` an uncaught script error or a `load` that never fired."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            for page, route in PAGES:
                pg = b.new_page()
                errors: list[str] = []
                pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                error = None
                try:
                    pg.goto(base + route, wait_until="load", timeout=bound * 1000)
                except PlaywrightError as e:
                    error = f"load never fired within {bound:g} s: {str(e).splitlines()[0]}"
                if errors and error is None:
                    error = f"script error: {errors[0].splitlines()[0]}"
                print(json.dumps({"page": page, "route": route, "error": error}), flush=True)
                pg.close()
        finally:
            b.close()
    return 0


def setup(venv: Path = SMOKE_VENV) -> int:
    """`--setup`, at a person's hand: the smoke venv from the live venv's base interpreter, with Playwright
    and its Chromium headless shell."""
    base = getattr(sys, "_base_executable", None) or sys.executable
    steps = [
        [base, "-m", "venv", str(venv)],
        [str(venv / "bin" / "pip"), "install", "-q", "playwright"],
        [str(venv / "bin" / "python"), "-m", "playwright", "install", "--only-shell", "chromium"],
    ]
    for cmd in steps:
        print("+", " ".join(cmd), flush=True)
        if subprocess.run(cmd).returncode != 0:
            print(f"setup failed at: {' '.join(cmd)}", flush=True)
            return 1
    print(f"the smoke's browser half is set up in {venv}", flush=True)
    return 0


def smoke(
    *,
    unit: Path = UNIT,
    venv: Path = SMOKE_VENV,
    ao: str | None = None,
    reach: str | None = None,
    wait_bound: float = WAIT_BOUND,
    bound: float = PAGE_BOUND,
) -> int:
    """The smoke: prints its log lines, and its verdict as the last line. 0 when everything passed."""
    ao = ao or str(Path(sys.executable).with_name("ao"))
    told: list[str] = []
    try:
        host, port = address(unit)
        base = f"http://{host}:{port}"
        wait_for(host, port, wait_bound)
        print(f"the UI answers on {host}:{port}", flush=True)
        for page, route in PAGES:
            line, why = load(base, page, route, bound)
            print(line, flush=True)
            if why:
                raise Failed(why)
        for page, route in REACH_PAGES if reach else ():
            line, why = load(reach.rstrip("/"), page, route, bound)
            print(f"reach {line}", flush=True)
            if why:
                told.append(f"reach refused {route}: {why.split(': ', 1)[-1]}")
                print(f"reach (fails nothing): {why}", flush=True)
        for line in ao_reads(ao):
            print(line, flush=True)
        python = venv / "bin" / "python"
        if python.exists():
            lines, why = browser(python, base, bound)
            for line in lines:
                print(line, flush=True)
            if why:
                raise Failed(why)
        else:
            told.append(SKIPPED)
            print(SKIPPED, flush=True)
    except Failed as e:
        print(f"smoke failed: {e}", flush=True)
        return 1
    print("smoke passed" + "".join(f"; {t}" for t in told), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup", action="store_true", help=f"make the browser half's venv in {SMOKE_VENV}")
    ap.add_argument("--browser", metavar="URL", help=argparse.SUPPRESS)  # the child's own mode
    args = ap.parse_args(argv)
    if args.setup:
        return setup()
    if args.browser:
        return browse(args.browser)
    return smoke(reach=reach_url())


if __name__ == "__main__":
    sys.exit(main())
