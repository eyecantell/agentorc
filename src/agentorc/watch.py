"""`agentorc-watch`: the watch's one run (design §4.10 *When the home itself is down: the watch*, TD-497).

Everything that tells the person runs in the home's tick, so a home that is down tells nothing — on
2026-10-09 the host agent, the UI and every session were gone for 4h20m. The watch stands outside the
user manager: `agentorc-watch.timer`, a system timer, runs this every `WATCH_EVERY` as the person
(`ao service install --system` installs it, `agentorc.service` writes its text). A run:

- **starts the user manager when it is stopped** — not here: the unit's `ExecStartPre=+systemctl
  start user@<uid>.service`, root's by its `+`. This run only learns whether that start did anything,
  from the manager's `ActiveEnterTimestampMonotonic` against the start of this activation of the watch
  service (`InactiveExitTimestampMonotonic`): a manager that became active after the watch began was
  started by it;
- **asks the home's socket** one never-gated read (`ping`) bounded at `ASK_SECONDS`, and keeps
  `silent_since` and what it told in `~/.agentorc/watch.json`;
- **tells the person** through the Telegram child (`sessionorc.notify`, the switch read from
  `settings.yml` directly, since no home answers): once when it started the manager, once when the
  home has been silent `WATCH_SILENCE` with the manager running, and once when it answers again after
  either. A failed send is a line in `~/.agentorc/watch.log` and is not retried; with `on: false`
  nothing is sent.

`step` is the judgement, pure over a fake clock, socket and `systemctl`; `main` wires the real ones.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

WATCH_EVERY = timedelta(minutes=5)  # the timer's period (`OnUnitActiveSec`, agentorc.service)
WATCH_SILENCE = timedelta(minutes=10)  # two periods of silence, the manager running, before the person is told
ASK_SECONDS = 10.0  # the one read's bound
# a manager that became active this long before the run, or less, and after the watch began, was the
# watch's own start: a bound, so a run by hand long after the last timer run never claims a start
STARTED_WITHIN = 120.0
SERVICE = "agentorc-watch.service"
PREFIX = "agentorc"


def manager_line(host: str, at: datetime) -> str:
    return f"{PREFIX} · {host}'s user manager was stopped; started again at {_hhmm(at)}, sessions kept running"


def silent_line(host: str, since: datetime) -> str:
    return f"{PREFIX} · the host agent on {host} has not answered since {_hhmm(since)}"


def back_line(host: str, silent: timedelta) -> str:
    return f"{PREFIX} · the host agent on {host} answers again (silent {_span(silent)})"


def _hhmm(at: datetime) -> str:
    return at.astimezone().strftime("%H:%M")


def _span(d: timedelta) -> str:
    """*4h20m*, *15 min*."""
    mins = max(int(d.total_seconds() // 60), 0)
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{mins} min"


def _when(iso: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def step(
    state: dict[str, Any], *, now: datetime, answered: bool, started: bool, host: str, on: bool = True
) -> tuple[dict[str, Any], list[str]]:
    """One run's judgement: the state `watch.json` keeps next — `silent_since`, `manager_started_at`,
    `told` (which of `manager` and `silent` went for this outage) — and the lines to tell, at most one
    of each kind an outage and one on its recovery. With the switch off (`on`) nothing is told and
    nothing is marked told, so a switch turned on mid-outage tells the outage before its recovery."""
    silent_since = _when(state.get("silent_since"))
    started_at = _when(state.get("manager_started_at"))
    told = [t for t in state.get("told") or [] if t in ("manager", "silent")]
    lines = []
    outage = bool(told)  # a line went out in an earlier run: its recovery is told
    if started:
        started_at = now
        silent_since = silent_since or now
        if on and "manager" not in told:
            lines.append(manager_line(host, now))
            told.append("manager")
    if answered:
        if outage:  # a manager started and answering in the one run says so in its own line alone
            lines.append(back_line(host, now - (silent_since or started_at or now)))
        return {"silent_since": None, "manager_started_at": None, "told": [], "last_run": now.isoformat()}, lines
    silent_since = silent_since or now
    if on and now - silent_since >= WATCH_SILENCE and "silent" not in told:
        lines.append(silent_line(host, silent_since))
        told.append("silent")
    return {
        "silent_since": silent_since.isoformat(),
        "manager_started_at": started_at.isoformat() if started_at else None,
        "told": told,
        "last_run": now.isoformat(),
    }, lines


# -- the real world: systemctl, the socket, the child, the files --------------------------------------

Run = Callable[[list[str]], str]


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _mono(run: Run, unit: str, prop: str) -> int | None:
    out = run(["systemctl", "show", unit, "-p", prop, "--value"]).strip()
    return int(out) if out.isdigit() and int(out) > 0 else None


def manager_started(run: Run = _run, uid: int | None = None, mono_now: float | None = None) -> bool:
    """Whether this run's `ExecStartPre` started the user manager: it became active after this
    activation of the watch service began, and within `STARTED_WITHIN` of now (both in systemd's
    monotonic microseconds)."""
    uid = os.getuid() if uid is None else uid
    entered = _mono(run, f"user@{uid}.service", "ActiveEnterTimestampMonotonic")
    began = _mono(run, SERVICE, "InactiveExitTimestampMonotonic")
    if entered is None or began is None:
        return False
    now_us = (time.monotonic() if mono_now is None else mono_now) * 1_000_000
    return entered >= began and now_us - entered <= STARTED_WITHIN * 1_000_000


def ask() -> bool:
    """One never-gated read of the home's socket, bounded: whether the host agent answers."""
    from sessionorc import client

    try:
        client.call_sync("ping", _timeout=ASK_SECONDS)
    except Exception:  # noqa: BLE001 — down, wedged or refusing: each is *not answering*
        return False
    return True


def tell(line: str, secrets: str, log: Callable[[str], None]) -> None:
    """The Telegram child with the line on its stdin; a failure is a log line, not retried."""
    from sessionorc import notify

    try:
        cp = subprocess.run(
            notify.argv(secrets), input=line, capture_output=True, text=True, timeout=notify.CHILD_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"send failed: {type(e).__name__}")
        return
    if cp.returncode != 0:
        log(f"send failed: {notify.reason(cp.stderr) or f'exit {cp.returncode}'}")
    else:
        log(f"told: {line}")


def _load(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def run_once(
    *,
    home: Path,
    now: datetime,
    answered: bool,
    started: bool,
    host: str,
    secrets: str | None,
    send: Callable[[str, str, Callable[[str], None]], None] = tell,
) -> list[str]:
    """One run against the files under `home`: the state read and written back, the lines sent when
    the switch is on (`secrets` set), each a line in the log; returns the lines judged due."""
    path = home / "watch.json"
    state, lines = step(_load(path), now=now, answered=answered, started=started, host=host, on=bool(secrets))
    home.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    tmp.replace(path)

    def log(text: str) -> None:
        with (home / "watch.log").open("a") as f:
            f.write(f"{now.isoformat(timespec='seconds')} {text}\n")

    for line in lines:
        send(line, secrets or "", log)
    return lines


def main() -> int:
    from sessionorc import hosts, paths, settings

    started = manager_started()
    answered = ask()
    tg = settings.telegram(settings.load())
    run_once(
        home=paths.home(),
        now=datetime.now(UTC),
        answered=answered,
        started=started,
        host=hosts.local_host().name,
        secrets=tg["secrets"] if tg else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
