"""Told on Telegram when nobody is looking (design §4.10, TD-092's design, TD-319).

The home sends one Telegram message for a row that newly stops a session or a team. This module holds
what such a message says — one line from **structured fields alone** (a name, a team, a kind, a
reference: never a pending text, a question's words or a `doing` line, which a session wrote and which
would leave the machine for a third party's servers and a phone's lock screen) and the row's address
under the person's `link` — and the sender itself, run as a child of its own:

    doppler run --project <p> --config <c> -- <python> -m sessionorc.notify

with the message on stdin. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exist in that child's
environment for one request and nowhere else. It exits 0, or 1 with a one-line reason on stderr that
is the HTTP status or the exception's class — never the request's URL, which holds the token.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

HOLD = timedelta(seconds=60)  # NOTIFY_HOLD: a row is told once it has stood this long and is still there
# A row whose hold ended longer ago than this is not told: it was standing before the switch was turned
# on, or while the home was down for longer than a restart takes — *newly* stops (§4.10), never a backlog
LATE = timedelta(minutes=5)
BURST = 6  # NOTIFY_BURST: messages in BURST_WINDOW; the next reads MORE, and then nothing until it clears
BURST_WINDOW = timedelta(minutes=10)
WATCHED = timedelta(minutes=2)  # NOTIFY_WATCHED: a hold ending this soon after a visible page's read is not told
CHILD_SECONDS = 30.0  # the child's whole life, doppler's start included
PREFIX = "agentorc"
MORE = f"{PREFIX} · and more need you — open the Inbox"
STATE_KINDS = ("permission", "question", "needs")  # the state rows told (§4.10 *What is told*)
API = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_SECONDS = 20.0


def _who(name: str, team: str) -> str:
    return f"{name} ({team})" if team else name


def state_line(name: str, team: str, kind: str) -> str:
    """A session in `needs-you`: *agentorc · grinder-ao-1 (ao-grind) needs you: permission*."""
    what = "needs you" if kind == "needs" else f"needs you: {kind}"
    return f"{PREFIX} · {_who(name, team)} {what}"


def ask_line(name: str, team: str, ref: str) -> str:
    """An open `ask` in the person inbox: *agentorc · grinder-ao-1 (ao-grind) asks you a question · TD-229*."""
    return f"{PREFIX} · {_who(name, team)} asks you a question" + (f" · {ref}" if ref else "")


def blocked_line(name: str, team: str, ref: str) -> str:
    """An outcome reported `blocked`: *agentorc · grinder-ao-1 (ao-grind) reports blocked · TD-142*."""
    return f"{PREFIX} · {_who(name, team)} reports blocked" + (f" · {ref}" if ref else "")


def restart_line(name: str, team: str) -> str:
    """A record the tick could not restart: *agentorc · ao-grind: grinder-ao-2 was not restarted*."""
    return f"{PREFIX} · {team + ': ' if team else ''}{name} was not restarted"


def work_line(team: str, n: int, finished: list[str] | None = None) -> str:
    """A wound-down team whose lanes gained work: *agentorc · cm-grind wound down and has work: 3 entries*;
    for a team that runs on, its finished members (§6 rule 8, TD-466): *… ao-grind's grinder-ao-1 finished
    and has work: 1 entry*."""
    who = f"'s {', '.join(finished)} finished" if finished else " wound down"
    return f"{PREFIX} · {team}{who} and has work: {n} entr{'y' if n == 1 else 'ies'}"


def test_line(home: str) -> str:
    """**Send a test**: *agentorc · a test from kmaster*."""
    return f"{PREFIX} · a test from {home}"


def alarm_line(name: str, team: str) -> str:
    """An identity alarm on a record (§4.8a): *agentorc · identity alarm on grinder-ao-1 (ao-grind)*."""
    return f"{PREFIX} · identity alarm on {_who(name, team)}"


def row_link(link: str, key: str) -> str:
    """The Inbox with that row scrolled to and marked: `<link>/inbox?row=<key>`."""
    return f"{link}/inbox?row={urllib.parse.quote(key, safe='')}" if link else ""


def mail_link(link: str, mid: str) -> str:
    """The message page for a question (§4.5 screen 6 *The message page*): `<link>/inbox/<id>`."""
    return f"{link}/inbox/{urllib.parse.quote(mid, safe='')}" if link else ""


def message(line: str, link: str) -> str:
    return f"{line}\n{link}" if link else line


def argv(secrets: str) -> list[str]:
    """The child's command: Doppler puts the two values in its environment, and nowhere else."""
    project, config = secrets.split("/", 1)
    return ["doppler", "run", "--project", project, "--config", config, "--", sys.executable, "-m", "sessionorc.notify"]


_TOKENISH = re.compile(r"bot[0-9]+:[A-Za-z0-9_-]+")


def reason(text: str) -> str:
    """A failed child's stderr as the one line the home keeps: its last non-empty line, a cap, and
    anything shaped like a bot token taken out — Doppler's own words pass, a token never does."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return _TOKENISH.sub("bot…", lines[-1])[:200] if lines else ""


def send(text: str, token: str, chat: str) -> str | None:
    """One `sendMessage`; None when Telegram took it, else the reason — never the URL."""
    body = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        API.format(token=token), data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_SECONDS) as r:  # noqa: S310 — a fixed https address
            return None if 200 <= r.status < 300 else f"telegram answered HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"telegram answered HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — the class alone: its message may carry the URL
        return f"the send failed: {type(e).__name__}"


def main() -> int:
    text = sys.stdin.read().strip()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set in that Doppler config", file=sys.stderr)
        return 1
    if not text:
        print("nothing to send", file=sys.stderr)
        return 1
    why = send(text, token, chat)
    if why:
        print(why, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
