"""The Inbox page (design §4.5 screen 6, §4.5a **Inbox page**, §4.10): its sections, the state rows,
the board rows and their reader's argv, the promote rows, what is owed, and the rail and Find.
Moved out of `agentorc.ui.app` unchanged (TD-196) and re-exported from it, so a route, a template or
a test reads each name from the app as before. `read_boards` and `repo_teams` stay in the app: the
suite patches both there.
"""

from __future__ import annotations

import functools
import re
import sys
import time
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agentorc import review as review_mod
from sessionorc import balance as balance_mod
from sessionorc import board as board_mod
from sessionorc import cadence as cadence_mod
from sessionorc import held as held_mod
from sessionorc import hosts
from sessionorc import ledger as ledger_mod
from sessionorc import mail as mail_mod
from sessionorc import settings as settings_mod
from sessionorc import shots as shots_mod
from sessionorc import workorders as workorders_mod
from sessionorc.agent_common import WRAPUP_GRACE
from sessionorc.client import call_sync
from sessionorc.models import (
    normalize_ref,
)
from sessionorc.work import manager_of

from . import render as rendermod
from .cards import _clock, _first_line, alarm_note, alarm_to_view
from .common import _age, _countdown, _instant, _iso, _left, host_name, templates, vscode_url

# -- the Inbox page (design §4.5 screen 6, §4.5a **Inbox page**, §4.10; TD-069 step 1) -------------

PERSON_ASK_KINDS = ("ask", "conflict")  # what reads as a question to the person; `steer` has its own rules
# design §4.5 screen 6 / §4.10 *Outcomes* (TD-079 step 2): **Waiting on them** is the fourth
# section, under *Steering* and in neither number — it waits on a session, not on the person.
# §4.9b (TD-075 step 2): **Answered for you** follows it, uncounted too — what a teammate answered
# for the person from the record, each with **Overrule**.
INBOX_SECTIONS = ("needs", "steering", "waiting", "answered", "fyi", "snoozed")


def _entry_open(e: dict[str, Any]) -> bool:
    """Design §4.10 *One way of being closed*, mirrored for the dicts the RPC hands the page: an
    entry is open exactly when it is an `ask`, `steer` or `conflict` with no `closed_reason` —
    and, for entries written before 2026-09-19, one with no `closed_by` and no `expired_at`."""
    if e.get("kind") not in (*PERSON_ASK_KINDS, "steer"):
        return False
    if e.get("closed_reason"):
        return False
    return not (e.get("closed_by") or e.get("expired_at"))


def _find_text(*parts: Any) -> str:
    """What the page's free-text filter matches on, lowercased once here rather than in the
    browser: the same `data-find` the mail rows carry."""
    return " ".join(str(p).strip() for p in parts if str(p or "").strip()).lower()


# design §4.5a **Inbox row: state**: the row kinds that are a *needs you* session, and so exactly
# what the Org's needs-you count counts. Kept beside `state_kind` because the two are one rule.
NEEDS_YOU_ROWS = ("permission", "question", "needs")


def state_kind(v: dict[str, Any]) -> str:
    """Which state row this session is, or "" for one that needs nobody (design §4.5a **Inbox row:
    state**). **The one predicate the Inbox and the Org's needs-you count both read**, so every
    session the Org counts as `needs-you` has exactly one row on the Inbox and the page's hover
    text — *the session states the Org counts too* — is true (review of PR #251).

    A `needs-you` record whose `pending` is empty, is not a dict, or names a kind this build does
    not know is still a session stopped for a person: it becomes a plain **needs** row with
    **Open** and no Allow / Deny, because nothing structured came with it and a control built from
    what is not there is the thing §4.2 and TD-071 item 8 forbid."""
    state = v.get("state")
    pend = v.get("pending")
    pend = pend if isinstance(pend, dict) else {}
    if state == "needs-you":
        if pend.get("kind") == "permission" and pend.get("tool_use_id"):
            return "permission"
        return "question" if pend.get("text") or pend.get("kind") else "needs"
    if state == "stalled?":
        return "stalled"
    if state == "limited":
        return "limited"
    if state == "exited" and v.get("flag") and [name for name, ok in (v.get("ready") or []) if not ok]:
        # "exited with unpushed work" (§4.5a, TD-069): what Ready to close says, in its own words —
        # the row goes when the work is pushed or the session is forgotten.
        return "unpushed"
    return ""


def restart_mark(v: dict[str, Any]) -> tuple[str, str] | None:
    """Design §4.5a **Inbox row: restart** (§6 *Keeping a team running*, TD-103 slice 5): `(the
    mark's own time, the row's words)` for a record the tick could not restart — at a ceiling,
    held by work left, or an `early` `restart_wanted` that neither a controller nor the tick acts
    on (§4.9a) — else None. The words are fixed, from the record's fields: an early one says what
    decided it (`restart_wanted.decided`, the host agent's reading — *early: nothing reported done
    this run*, *repeats TD-229: reported done by an earlier run too*; TD-249 slice 6), and its `why`
    is the session's own sentence, shown as such and never acted on."""
    if v.get("superseded_by"):
        return None
    ceiling, held, wanted = v.get("restart_ceiling"), v.get("restart_blocked"), v.get("restart_wanted")
    if isinstance(ceiling, dict):
        n = ceiling.get("count")
        n = n if isinstance(n, int) else "?"
        if ceiling.get("why") == "fill":
            return str(ceiling.get("at") or ""), (
                f"fills exhausted · {n} in 1 h — the seats beside it were filled as often as the host agent will"
            )
        return str(ceiling.get("at") or ""), (
            f"restarts exhausted · {n} in 2 h — it exited on its own each time and the host agent restarted it "
            "up to its ceiling"
        )
    if isinstance(held, dict):
        return str(held.get("at") or ""), (
            f"restart held: {held.get('dirty')} uncommitted and {held.get('unpushed')} unpushed in its checkout "
            "— it is restarted by itself the moment they are pushed"
        )
    if isinstance(wanted, dict) and wanted.get("early") and v.get("state") in ("idle", "exited"):
        why = _first_line(wanted.get("why") or "") or "no reason given"
        decided = _first_line(wanted.get("decided") or "") or "early"  # a mark older than the field
        return str(wanted.get("at") or ""), f"restart wanted · {decided} — {why}"
    return None


def unclosed_mark(v: dict[str, Any], now: datetime) -> tuple[str, str] | None:
    """Design §4.5a **Inbox row: manager did not close** (§6 rule 9, TD-241): `(when it was told,
    the row's words)` for a live manager the tick told its team had finished (`finished_sent_at`)
    and could not close `WRAPUP_GRACE` later — never idle, `needs-you`, `limited`, or holding work
    — else None. Read from the record each time: nothing more is stored, and the row leaves when
    the manager is closed or a member at work takes the wind-down back."""
    sent = _instant(v.get("finished_sent_at"))
    if sent is None or v.get("superseded_by") or v.get("state") in ("exited", "closed"):
        return None
    if now - sent < WRAPUP_GRACE:
        return None
    return str(v["finished_sent_at"]), (
        "manager did not close — its team finished and the host agent told it so, and it has not closed; "
        "the host agent closes it only once it is idle with nothing uncommitted or unpushed"
    )


def idle_open_mark(v: dict[str, Any], views: Collection[dict[str, Any]], now: datetime) -> tuple[str, str] | None:
    """Design §4.5a **Inbox row: idle · open work** (§6 rule 3 `idle_open`, TD-259): `(the mark's
    time, the row's words)` for a supervised member of a team with **no manager** that the tick
    reads as idle with its work open — `idle_open: {at, ref}` on its record — else None. A team
    with a manager draws no row: the reading fills a manager on call, or is a standing manager's
    round. The manager is read from the team's records whatever their state (`work.manager_of`), so
    a manager on call whose seat is empty — its record closed between fills — still manages.
    The words are the record's fields and nothing off its screen: *idle 40m with TD-070 open, nudged
    14:02*."""
    mark = v.get("idle_open")
    if not isinstance(mark, dict) or v.get("state") != "idle" or not v.get("supervised") or not v.get("team"):
        return None
    if manager_of([o for o in views if o.get("team") == v["team"]]) is not None:
        return None
    idle = _age(v.get("since"), now)
    ref = str(mark.get("ref") or "").strip()
    nudged = _clock(v.get("nudged_at"))
    words = f"idle {idle}".strip() + (f" with {ref} open" if ref else " with its work open")
    return str(mark.get("at") or ""), words + (f", nudged {nudged}" if nudged else "")


def _pr_parts(prs: Collection[int], directory: Any, tail: str) -> list[dict[str, str]]:
    """A row's words as the page draws them: *PR #845 and #851 <tail>*, each `#n` a link to the pull
    request where the checkout's origin says where it is (`review.pr_url`), bare where it does not."""
    parts: list[dict[str, str]] = [{"text": "PR "}]
    for i, pr in enumerate(prs):
        if i:
            parts.append({"text": " and " if i == len(prs) - 1 else ", "})
        parts.append({"text": f"#{pr}", "url": review_mod.pr_url(str(directory) if directory else None, pr)})
    return [*parts, {"text": f" {tail}"}]


def _parts_text(parts: Collection[Mapping[str, str]]) -> str:
    return "".join(p["text"] for p in parts)


def cadence_marks(v: dict[str, Any], names: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Design §4.5a **Inbox row: cadence check failed** (§6 rule 10, TD-258): one `{pr, at, parts}`
    for each `checks` entry of the record carrying `row` — a PR that failed the cadence check twice,
    or failed already merged. The words are fixed and the record's: the failed rows by the script's
    `rule` names, never its `detail`; beside *review*, *read by <seat>* when the home saw the
    reader's reply on the PR's `ask` and *recorded* when it did not; and when it was read. `names`
    gives the reader's record its name, where the page holds that record; its id is said otherwise."""
    if v.get("superseded_by") or not isinstance(v.get("checks"), list):
        return []
    out = []
    for c in cadence_mod.rows([c for c in v["checks"] if isinstance(c, dict) and isinstance(c.get("pr"), int)]):
        by = str(c.get("read_by") or "")
        note = f"read by {(names or {}).get(by) or by}" if by else "recorded"
        failed = [
            f"{r} ({note})" if r == "review" else str(r)
            for r in (c.get("failed") if isinstance(c.get("failed"), list) else [])
        ]
        tail = f"fails the cadence check: {', '.join(failed) or 'no row named'}"
        if read := _clock(c.get("at")):
            tail += f" · read {read}"
        parts = _pr_parts([c["pr"]], v.get("repo") or v.get("dir"), tail)
        out.append({"pr": c["pr"], "at": str(c.get("row") or c.get("at") or ""), "parts": parts})
    return out


def held_mark(v: dict[str, Any]) -> dict[str, Any] | None:
    """Design §4.5a **Inbox row: merged without its read** (§6 rule 11, TD-258): `{at, parts}` for a
    record whose `held_missed` holds two crossings the person has not dismissed (`held.row`), else
    None — the first is a note and the member's line. The PRs and the held paths are the entries'."""
    if v.get("superseded_by") or not isinstance(v.get("held_missed"), list):
        return None
    left = held_mod.row([c for c in v["held_missed"] if isinstance(c, dict) and isinstance(c.get("pr"), int)])
    if not left:
        return None
    reader = str((v.get("review") or {}).get("reader") or "") if isinstance(v.get("review"), dict) else ""
    chained = isinstance(v.get("review"), dict) and "chain" in v["review"]  # a flow's chain (§4.9c, TD-315)
    whose = "the person's" if reader == "person" else "its readers'" if chained else "the techlead's"
    paths = list(
        dict.fromkeys(str(p) for c in left for p in (c.get("paths") if isinstance(c.get("paths"), list) else []))
    )
    more = f" and {len(paths) - held_mod.NAMED} more" if len(paths) > held_mod.NAMED else ""
    tail = f"touched held paths and merged without {whose} read"
    if paths:
        tail += f" · {', '.join(paths[: held_mod.NAMED])}{more}"
    parts = _pr_parts([c["pr"] for c in left], v.get("repo") or v.get("dir"), tail)
    return {"at": max(str(c.get("at") or "") for c in left), "parts": parts}


def state_rows(
    views: Collection[dict[str, Any]],
    *,
    host_alarms: Collection[dict[str, Any]] = (),
    host: str = "",
    identity_mode: str = "",
) -> list[dict[str, Any]]:
    """Design §4.5 screen 6 / §4.5a **Inbox row: state** (TD-069 step 2): a session's state as a
    row of the Inbox, one per thing that needs a person. Built from the card views the Org is
    rendered from — the same `view()`, so a row's pill, `doing` line, `title`, team and role are
    the card's own and cannot drift from it — and never from anything parsed off a screen
    (TD-071 item 8).

    The kinds, in §4.5a's words: a pending **permission** (what is asked, the time left, Allow /
    Deny through the hook channel); a pending **question** and **stalled?** (the text or the host's
    note, **Open**); **limited** (the reset time); an **exited** session with unpushed work (what
    Ready to close says, **Open**). To them TD-077 step 2 adds the **identity alarm** rows: one per
    record that has alarms, and one for the host's own list — *it is either a bug of ours or a
    session misbehaving and a person should know which* (§4.8a).

    A row has no `snoozed_until` and no Snooze: a state lives on the record, and there is nowhere
    to keep a person's *not now* (the gap is written up in TD-069 step 2's entry). It leaves the
    list the moment the state does, which is the next poll."""
    rows: list[dict[str, Any]] = []
    now = datetime.now(UTC)  # one instant for the whole list, as `inbox_sections` takes one

    def base(v: dict[str, Any], row: str, text: str, *, extra: str = "") -> dict[str, Any]:
        doing = v.get("doing") or {}
        return {
            "row": row,
            "id": f"{v['id']}:{row}",  # the row's own key on the page; a record may raise two
            "sid": v["id"],
            "name": v.get("name") or v["id"],
            "team": v.get("team") or "",
            "role": v.get("role") or "",
            "role_icon": v.get("role_icon") or "",
            "role_label": v.get("role_label") or "",
            "title": v.get("title") or "",
            "doing": v.get("doing"),
            "state": v.get("state") or "",
            "state_class": v.get("state_class") or "",
            "state_label": v.get("state_label") or "",
            "scraped": bool(v.get("scraped")),
            "suspended_note": v.get("suspended_note") or "",  # §4.8a: the mark rides with the record
            "host": v.get("host") or "",
            "text": text,
            "deadline": v.get("deadline") or "" if row == "permission" else "",
            # §4.5 screen 6 *Layout* (TD-082): the countdown is rendered in words here, not left as
            # `…` for the client's first tick. The words are `fmtLeft`'s, so nothing jumps.
            "left": _countdown(v.get("deadline") if row == "permission" else None, now),
            "at": v.get("since") or "",
            "age": v.get("age") or "",
            "find": _find_text(v.get("name"), v.get("title"), doing.get("text"), text, extra),
        }

    names = {str(v.get("id")): str(v.get("name") or v.get("id")) for v in views}
    for v in sorted(views, key=lambda v: (str(v.get("since") or ""), str(v.get("id") or ""))):
        pend = v.get("pending")
        pend = pend if isinstance(pend, dict) else {}
        text = str(pend.get("text") or "")
        kind = state_kind(v)
        if kind == "permission":
            rows.append(base(v, kind, text))
        elif kind == "question":
            rows.append(base(v, kind, f"{pend.get('kind')}: {text}" if pend.get("kind") else text))
        elif kind == "needs":
            rows.append(base(v, kind, "it is waiting on a person, and nothing came with it saying what for"))
        elif kind == "stalled":
            rows.append(base(v, kind, text or v.get("host_note") or "no output for a while, and no note"))
        elif kind == "limited":
            rows.append(base(v, kind, text or "the profile is at its cap"))
        elif kind == "unpushed":
            unmet = [name for name, ok in (v.get("ready") or []) if not ok]
            rows.append(base(v, kind, f"{v['flag']} — {', '.join(unmet)}", extra=v.get("where") or ""))
        if mark := restart_mark(v):
            # its own row beside any state row, as an alarm is: a crashed record at its ceiling can
            # also be exited with unpushed work, and the two are answered differently
            rows.append(
                {
                    **base(v, "restart", mark[1]),
                    "at": mark[0] or v.get("since") or "",
                    "age": "",
                    "restartable": bool(v.get("restartable")),  # never a seat: *fills exhausted* has no Restart
                }
            )
        if mark := idle_open_mark(v, views, now):
            rows.append({**base(v, "idle_open", mark[1]), "at": mark[0] or v.get("since") or "", "age": ""})
        if mark := unclosed_mark(v, now):
            rows.append({**base(v, "unclosed", mark[1]), "at": mark[0], "age": ""})
        for c in cadence_marks(v, names):
            # one row a PR, and the PR in the row's kind: it is the snooze's key (`cadence:<pr>`)
            row = base(v, f"cadence:{c['pr']}", _parts_text(c["parts"]))
            rows.append({**row, **c, "at": c["at"] or row["at"], "age": "", "mark": "cadence"})
        if held := held_mark(v):
            row = base(v, "held", _parts_text(held["parts"]))
            rows.append({**row, "at": held["at"] or row["at"], "age": "", "mark": "held", "parts": held["parts"]})
        if alarms := v.get("alarms"):
            row = base(v, "alarm", alarm_note(alarms))
            # an alarm row is as old as its newest alarm, not as its session: what the order is
            # about is when the thing that needs a person happened
            rows.append(
                {
                    **row,
                    "at": max((a["last"] or a["at"]) for a in alarms) or row["at"],
                    "age": "",
                    "alarms": alarms,
                    # §4.8a *An alarm's answers* (TD-077 b): who **Log TD** would hand these to — the
                    # home's own answer (`alarm_to`, the record's first live controller from the
                    # control graph), never worked out here, so drawn-or-not and the RPC's
                    # refused-or-not cannot disagree.
                    "alarm_to": alarm_to_view(v.get("alarm_to")),
                    "mode": identity_mode,
                    "find": _find_text(row["find"], *(a["words"] for a in alarms)),
                }
            )
    if host_alarms:
        rows.append(
            {
                "row": "alarm_host",
                "id": "host:alarm",
                "sid": "",
                "name": host or host_name(),
                "team": "",
                "role": "",
                "role_icon": "",
                "role_label": "",
                "title": "",
                "doing": None,
                "state": "",
                "state_class": "",
                "state_label": "",
                "scraped": False,
                "host": host,
                "text": alarm_note(list(host_alarms)),
                "deadline": "",
                "at": max((a["last"] or a["at"]) for a in host_alarms),
                "age": "",
                "find": _find_text(host, "identity alarm", *(a["words"] for a in host_alarms)),
                "alarms": list(host_alarms),
                "mode": identity_mode,
            }
        )
    return rows


# design §4.5 screen 6 / §4.4 (TD-069 step 3): **board items** in the Inbox. The board is each
# repo's `docs/user_attention.md`, in dev-cadence's format, and its reader is dev-cadence's own
# `nudge_user_attention.py --report --json` — never a second parser here (§4.4: the items, with the
# board line each sits on, come from that report). It is run over the boards of the repos this host
# knows, with the copy `board_reader` picks (TD-278: synced copies differ while a repo lags), at most
# once every `BOARD_TTL` seconds, since the page and the top bar poll every few.
BOARD_FILE = Path("docs") / "user_attention.md"
BOARD_SCRIPT = Path("scripts") / "nudge_user_attention.py"
# dev-cadence's own source of the reader, in its registered checkout (TD-278): what every synced copy
# is made from, so preferred over any copy; else the copy whose repo synced last (`cadence-sync.lock`)
BOARD_SOURCE = Path("files") / "scripts" / "nudge_user_attention.py"
SYNC_LOCK = Path("docs") / "cadence-sync.lock"
# the fields of a board item the Inbox draws that a reader older than them does not give: a copy
# that lacks one is named on the page rather than drawn as if the board had nothing there (TD-278)
BOARD_FIELDS = {"answers": "dev-cadence TD-036"}
BOARD_TTL = 60.0
# §4.5a's answers to a board row, the `board_edit` RPC's actions: Snooze, Done, and **Decide** — one
# of the item's own `Answers:`, or *Go with it* for the one marked default (§4.4 *Decide*, TD-255)
BOARD_ACTS = ("snooze", "done", "decide")
# the kinds a live look is written as: `look` since dev-cadence TD-082, `watch` before it (TD-292)
LOOK_KINDS = ("look", "watch")
WORKS = "Works"  # a live look's first answer, cadence §3.5's word (the second is `board.NOT_RIGHT`'s form)
BOARD_TIMEOUT = 20.0
# §4.5 screen 6 *Boards are read against origin* (TD-221): the read passes the reader's own `--fetch`,
# which fetches each repo's origin serially (30 s a repo, no aggregate bound once `--due-only` went,
# TD-220), so a fetching read has a bound of its own; stopped or failed, a plain read follows at once
BOARD_FETCH_TIMEOUT = 45.0

# §4.5 screen 6 *Boards are read against origin*, §4.5a **origin note** (TD-221 slice 3): which case the
# reader's fetch found, read from the fixed phrases of its `fetch_note` — the reader gives the cases no
# field of their own (one is asked of dev-cadence) — in order, the first that opens the note winning,
# so the parenthesis after *board DIFFERS from* is read before the opening alone. A case of None draws
# no note. `tests/test_ui_board.py` fails on a phrase in the reader that this table does not know.
ORIGIN_PHRASES: tuple[tuple[str, str, str | None], ...] = (
    ("fetch skipped", "", "unreached"),
    ("fetched; no board at", "", None),
    ("fetched; board matches", "", None),
    ("fetched; local clone is behind", "", "behind"),
    ("fetched; board DIFFERS from", "(no common history", "both"),
    ("fetched; board DIFFERS from", "(local edits not pushed)", "local"),
    ("fetched; board DIFFERS from", "(both sides changed)", "both"),
)
# the home's pull readings by repo name (`host`'s `pulls`, §6 *Pull*), kept fresh by the app as
# `work_marks` keeps its marks: what the *behind* note's tail says
PULLS: dict[str, Any] = {}


def pull_tail(reading: Mapping[str, Any] | None) -> str:
    """The *behind* origin note's tail (§4.5 screen 6, TD-263): the pull's standing for the repo, in
    the design's words — "" when no pass has reached it, or its last pass left it current or pulled
    (the board read is then older than the checkout, and the next read says so)."""
    r = reading or {}
    outcome = r.get("outcome")
    if outcome == "waiting":
        who = str(r.get("occupant") or "")
        return f" — the host agent pulls it once {who} is idle" if who else " — the host agent pulls it once it is idle"
    if outcome == "refused":
        return f" — it could not be pulled: {r.get('why') or 'no reason given'}"
    if outcome == "off":
        return " — pulling is off for this repo"
    return ""


def origin_case(source: str, fetch_note: str) -> str | None:
    """The case of one board's read against origin (§4.5 screen 6): `behind`, `local`, `both`,
    `unreached`, or None — matches origin, no board there, a plain read, or a phrase this table does
    not know. `source` is non-empty only when the board was read from origin, which is *behind*."""
    if source:
        return "behind"
    for opening, paren, case in ORIGIN_PHRASES:
        if fetch_note.startswith(opening) and (not paren or paren in fetch_note):
            return case
    return None


def origin_note(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """§4.5a **origin note**: the line above a repo's first board row, in the design's words, as
    `{text, warn}` — `warn` the warning colour, for the one case where rows are hidden — or None.
    The reader's sentence is never drawn but for a skipped fetch's reason, which carries no count."""
    source, fetch_note = str(row.get("source") or ""), str(row.get("fetch_note") or "")
    case = origin_case(source, fetch_note)
    if case == "behind":
        tail = pull_tail(PULLS.get(Path(str(row.get("root") or "")).name)) if row.get("root") else ""
        return {"text": f"read from {source or 'origin'}: this checkout has not pulled it yet{tail}", "warn": False}
    if case == "local":
        return {"text": "board edits made here are not on origin", "warn": False}
    if case == "both":
        return {
            "text": "this checkout's board and origin's have both changed: showing the checkout's, "
            "and what origin added is not shown — pull",
            "warn": True,
        }
    if case == "unreached":
        why = fetch_note.removeprefix("fetch skipped").strip().removeprefix("(").removesuffix(")")
        return {
            "text": f"origin could not be reached ({why.strip() or 'no reason given'}): "
            "showing the checkout's board as of its last pull",
            "warn": False,
        }
    return None


def origin_firsts(rows: Collection[Mapping[str, Any]]) -> set[str]:
    """The ids of the rows that carry their repo's **origin note** in one list as drawn: the first
    board row of each board whose read has a note (§4.5a: *one line above a repo's first board row*)."""
    seen: set[str] = set()
    out: set[str] = set()
    for r in rows:
        if r.get("row") != "board" or not r.get("board") or r["board"] in seen:
            continue
        seen.add(r["board"])
        if origin_note(r):
            out.add(str(r.get("id")))
    return out


templates.env.globals.update(origin_note=origin_note, origin_firsts=origin_firsts)


def _synced(root: Path) -> str:
    """When a repo last took dev-cadence's files, `synced:` off its `cadence-sync.lock` (an ISO
    instant, compared as text), or "" when it has none or it cannot be read."""
    try:
        text = (root / SYNC_LOCK).read_text()
    except OSError:
        return ""
    return next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("synced:")), "")


def board_reader(roots: Collection[Path]) -> Path | None:
    """Which copy of dev-cadence's board reader the Inbox runs (§4.5 screen 6, TD-278): picked by
    what it can do, never by the registry's order — synced copies are the same only while every
    repo is synced at once, which they are not (samscrape's was two weeks behind and drew no row's
    answers). dev-cadence's own source when its checkout is registered; else the copy whose repo
    synced last, the registry's order deciding a tie; None when no repo carries one."""
    for r in roots:
        if (r / BOARD_SOURCE).is_file():
            return r / BOARD_SOURCE
    copies = [r for r in roots if (r / BOARD_SCRIPT).is_file()]
    return max(copies, key=_synced) / BOARD_SCRIPT if copies else None


def reader_lacks(report: Any, script: str = "") -> str:
    """The page's note when the reader run gives no field the Inbox draws (TD-278): its items carry
    no `answers`, say, so no row would draw its answers and nothing would say why. "" when every
    field is there, or there is no item to tell by."""
    items = [
        it
        for b in (report.get("boards") or [] if isinstance(report, dict) else [])
        if isinstance(b, dict)
        for it in b.get("items") or []
        if isinstance(it, dict)
    ]
    if not items:
        return ""
    gone = [f for f in BOARD_FIELDS if not any(f in it for it in items)]
    if not gone:
        return ""
    whose = f"the reader at {script}" if script else "the board reader"
    since = ", ".join(f"`{f}` ({BOARD_FIELDS[f]})" for f in gone)
    return f"board rows are drawn without {since}: {whose} predates it — sync dev-cadence's files into that repo"


def board_argv(roots: Collection[str | Path], *, fetch: bool = False, only: str = "") -> tuple[list[str] | None, str]:
    """The command that reads every open item of these repos' boards — not `--due-only` since TD-220
    (§4.5 screen 6 *The board's horizon*): the page sorts what is due from what comes up — or None
    and a note saying why nothing is read. No board anywhere is not a fault and has no note; boards with no reader do,
    since the Inbox would otherwise look clear when it is not (§4.5 *no silent failure path*).

    `fetch` passes the reader's own `--fetch` (§4.5 screen 6 *Boards are read against origin*,
    TD-221): it fetches each repo's origin and reads a board that is merely behind from
    `origin/<default>` — the fetch is the reader's, never a second one of ours. `only` reads that one
    board alone, as the read after a press does."""
    rs = [Path(r).expanduser() for r in roots]
    boards = [str(r / BOARD_FILE) for r in rs if (r / BOARD_FILE).is_file()]
    if only:
        boards = [b for b in boards if b == only] or ([only] if Path(only).is_file() else [])
    if not boards:
        return None, ""
    script = board_reader(rs)
    if script is None:
        return None, f"board items are not shown: no repo here carries {BOARD_SCRIPT}, dev-cadence's reader"
    argv = [sys.executable, str(script), "--report", "--json"] + (["--fetch"] if fetch else [])
    for b in boards:
        argv += ["--board", b]
    return argv, ""


def unreached(report: Any) -> bool:
    """Whether a fetching read did not reach origin: stopped or failed (no report), or each board it
    read says *fetch skipped* — the reader's own bound on its fetch (§4.5 screen 6)."""
    boards = report.get("boards") if isinstance(report, dict) else None
    if not isinstance(boards, list):
        return True
    return bool(boards) and all(
        isinstance(b, dict) and origin_case(str(b.get("source") or ""), str(b.get("fetch_note") or "")) == "unreached"
        for b in boards
    )


def written_back_argv(board: str) -> list[str] | None:
    """The plain read of one board as the write-back left it (§4.5 screen 6 (3), TD-264): the board in
    the host agent's own tree (`board.tree_dir`), which a press resets to origin's head once its PR
    has merged — read in place of the checkout's when the fetching read after that press is stopped,
    so the row is drawn as the press left it and not as the checkout held it before. None when there
    is no such tree here, or its board is the checkout's word for word (the plain read is then the same)."""
    path = Path(board)
    if path.parts[-len(BOARD_FILE.parts) :] != BOARD_FILE.parts:
        return None
    root = Path(*path.parts[: -len(BOARD_FILE.parts)])
    tree = board_mod.tree_dir(root) / BOARD_FILE
    try:
        if not tree.is_file() or tree.read_bytes() == path.read_bytes():
            return None
    except OSError:
        return None
    script = board_reader([root])
    if script is None:
        return None
    return [sys.executable, str(script), "--report", "--json", "--board", str(tree)]


def as_written_back(report: dict[str, Any], board: str) -> dict[str, Any]:
    """`report`, read from the write-back's tree, named as the checkout's board it stands for: its
    board, root and label the checkout's, and read from origin — the tree is origin's head after the
    press — so the row carries the *behind* note until the checkout pulls (§4.5a **origin note**)."""
    root = Path(*Path(board).parts[: -len(BOARD_FILE.parts)])
    for b in report.get("boards") or ():
        if isinstance(b, dict):
            b.update(board=board, root=str(root), label=root.name, source="origin", fetch_note=None)
    return report


def _same_ref(a: Any, b: Any) -> bool:
    """One reference, as the host agent stores it (`normalize_ref`: `td-27` is `TD-027`)."""
    try:
        return normalize_ref(str(a or "")) == normalize_ref(str(b or ""))
    except ValueError:  # an empty reference names nothing
        return False


def review_pr(progress: Collection[dict[str, Any]], ref: str) -> int | None:
    """The PR a claim on `ref` is in review as (design §4.5a *Focus side panel → Reports*, TD-150),
    or None: a declared `claimed` entry's own `pr`, else the `review_pr` its record carries for its
    branch — the rule `AO.reportGroups` draws the panel by, so the panel and the refusal agree. No
    derived entry shares a declared one's reference (§9 invariant 10), so there is no third."""
    for p in progress:
        if not isinstance(p, dict) or not _same_ref(p.get("ref"), ref) or p.get("status") != "claimed":
            continue
        if (p.get("source") or "declared") != "declared":
            continue
        got = p.get("review_pr") or p.get("pr")
        if isinstance(got, int) or str(got or "").isdigit():
            return int(got)
    return None


def board_choices(roots: Collection[str | Path] | None = None) -> list[dict[str, str]]:
    """The boards *Put on the board* may write to (design §4.5a *Inbox row: FYI*, TD-140): each
    checkout this host's repos registry names that carries a board, as `{label, root, board}` with
    both paths resolved — the paths `board_edit` resolves against the same registry. On a node the
    org is the home's (§4.4a), so a node offers none."""
    if roots is None:
        if hosts.is_node():
            return []
        roots = hosts.local_host().repos()
    out: list[dict[str, str]] = []
    for r in roots:
        root = Path(r).expanduser().resolve()
        if (root / BOARD_FILE).is_file():
            out.append({"label": root.name, "root": str(root), "board": str(root / BOARD_FILE)})
    return out


def promote_rows(promotes: Mapping[str, Any] | None, now: datetime | None = None) -> list[dict[str, Any]]:
    """design §4.5a **Inbox row: promote** (§6 *Promote*, TD-132 slice 3): one row per repo in the
    home's `promotes` reading, drawn only while live is not main's head or a promote failed — and
    every part from that structured reading, never text a session wrote. Under `auto: false` a repo
    main is ahead of is *Needs you*, counted (the press is what stands between merged and live); a
    run **in flight** is FYI (`fyi`), uncounted, its Promote disabled; a **failure** is *Needs you*
    whatever `auto` says. Under `auto: true` nothing but a failure: the normal flow is the note.
    Aged from when main moved (`moved`, its head's committer time). Keyed `promote:<repo>` in the
    attention store, so Snooze is by time alone and more merges do not wake a snoozed row.

    **After a rollback** (§6 *A rollback*, TD-226 slice 3) the row is drawn while the home's `held`
    stands, whatever `auto` says, under *Needs you*: *live `<sha7>`, rolled back from `<sha7>` <t>
    ago · main … · auto on · held* — *rolled back*, never *behind*, since a person put live there;
    its Dismiss ends the hold when no failure stands."""
    at = now or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for repo, r in sorted((promotes or {}).items()):
        if not isinstance(r, dict):
            continue
        failed = r.get("failed") if isinstance(r.get("failed"), dict) else None
        flight = r.get("inflight") if isinstance(r.get("inflight"), dict) else None
        held = r.get("held") if isinstance(r.get("held"), dict) else None
        behind = bool(r.get("main")) and r.get("live") != r.get("main")
        if not failed and not held and (r.get("auto") or not (flight or behind)):
            continue
        live = str(r.get("live") or "")[:7]
        main = str(r.get("main") or "")[:7]
        ahead = r.get("ahead")
        checks = str(r.get("checks") or "unknown")
        text = f"{repo} · live {live or 'unknown'}"
        if held:
            frm, since = str(held.get("from") or "")[:7], _age(held.get("at"), at)
            text += f", rolled back from {frm or 'unknown'}" + (f" {since} ago" if since else "")
        text += f" · main {main or 'unknown'}"
        if isinstance(ahead, int) and ahead:
            text += f", {ahead} commit{'' if ahead == 1 else 's'} ahead"
        text += f" · checks {checks}"
        if held:
            text += f" · auto {'on' if r.get('auto') else 'off'} · held"
        when = str(
            (failed or {}).get("at") or (flight or {}).get("at") or (held or {}).get("at") or r.get("moved") or ""
        )
        out.append(
            {
                "row": "promote",
                "sid": f"promote:{repo}",
                "id": f"promote:{repo}",
                "name": repo,
                "repo": repo,
                "team": "",
                "at": when,
                "age": _age(when, at),
                "text": text,
                "live_why": r.get("live_why") if not live else None,
                "checks_why": r.get("checks_why"),
                "unmet": r.get("unmet"),
                "failed": failed,
                "held": held,
                "inflight": flight,
                "fyi": bool(flight) and not failed,
                "find": " ".join(x for x in (repo, "promote", text, (failed or {}).get("why") or "") if x),
            }
        )
    return out


WORK_IDS = 5  # §4.5a *Inbox row: team start*: five ids at most, then *and n more*


def work_ids(mark: Mapping[str, Any]) -> list[str]:
    """The ids of one `work_waiting` mark, each once, in its members' order (§6 rule 8)."""
    members = mark.get("members") if isinstance(mark.get("members"), Mapping) else {}
    return list(dict.fromkeys(str(i) for ids in members.values() if isinstance(ids, list) for i in ids))


def work_questions(mark: Mapping[str, Any]) -> list[dict[str, str]]:
    """A mark's question ends (§6 rule 8 *A question's end is work*, TD-274), each `{name, kind, ref,
    how, words}`: *designer-ao-1's steer about TD-222 lapsed to its default*, *… ask about TD-222 was
    answered*. Only a `steer` lapses, so a mark that names no kind reads a lapse as one and an
    answer as a *question*. Every part is the home's reading, never text a session wrote."""
    got = mark.get("questions") if isinstance(mark.get("questions"), list) else []
    out = []
    for q in got:
        if not isinstance(q, Mapping) or not q.get("ref"):
            continue
        how = str(q.get("how") or "")
        kind = str(q.get("kind") or ("steer" if how == "lapsed" else "question"))
        end = "lapsed to its default" if how == "lapsed" else "was answered"
        name, ref = str(q.get("name") or "a member"), str(q["ref"])
        out.append({"name": name, "kind": kind, "ref": ref, "how": how, "words": f"{name}'s {kind} about {ref} {end}"})
    return out


def work_held(held: Any, now: datetime) -> str:
    """Why rule 8's start was held back, in the row's words (§4.5a *Inbox row: team start*), from the
    mark's `held: {why, …}` — or "" for no hold, which is `on_work: ask`'s row."""
    if not isinstance(held, Mapping):
        return ""
    why = held.get("why")
    if why == "usage":
        left = _left(str(held.get("resets") or ""), now)  # "" with no reset on the hold, or one already past
        return f"{held.get('profile') or 'its profile'} is over its line" + (f", resets in {left}" if left else "")
    if why == "until":
        return "its stop time has passed"
    if why == "day":
        n = held.get("count")
        return f"started {n} time{'' if n == 1 else 's'} today"
    if why == "early":
        ago = _age(held.get("started"), now)
        return f"started {ago + ' ago' if ago else 'just now'} and wound down again"
    if why == "link":
        got = held.get("hosts")  # every down host; an older home names one, as `host`
        down = [str(h) for h in got if h] if isinstance(got, list) else []
        if len(down) > 1:
            return f"{', '.join(down[:-1])} and {down[-1]} are unreachable"
        return f"{(down[0] if down else held.get('host')) or 'a host'} is unreachable"
    if why == "nothing":
        return "no record to start"
    if why == "balance":
        lines = balance_mod.crossed_words(dict(held))
        return "its repo is over its line" + (f": {lines}" if lines else "")
    return str(why or "held")


def work_rows(
    work: Mapping[str, Any] | None,
    wound: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    records: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """design §4.5a **Inbox row: team start** (§6 rule 8, TD-227 slice 3): one row per wound-down
    team whose lanes gained work, from the home's `work_waiting` (`work` on the `host` read) —
    *`<team>` · wound down <t> · its lanes gained n entries: TD-213, TD-214, TD-223*, five ids at
    most and *and n more*, each a link to the Repo page's entry made with the mark's `repo`. Every
    part is a structured reading, never text a session wrote. `wound` is `{team: instant}`, the
    card's own *wound down* reading; a team it does not name is drawn without the time. Under
    *Needs you*, counted, aged from the mark's `at`; under `on_work: start` the home keeps the mark
    only while a bound holds the start back, and the row says which (`held`). Keyed `work:<team>`
    in the attention store, so Snooze is by time alone.

    **A team that runs on** (§6 rule 8 *A member that finished while its team runs on*, TD-466): a
    team `wound` does not name, whose mark names members, reads *`<team>` · grinder-ao-1 finished <t>
    · its lane gained …* — each named member by its record's name and the instant it closed, from
    `records` (the page's own list) — and is `running`, so its **Start** posts `work_start`."""
    at = now or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for team, mark in sorted((work or {}).items()):
        if not isinstance(mark, Mapping):
            continue
        # a question's end is its own clause, before the ids where both stand (TD-274)
        asked = work_questions(mark)
        refs = {q["ref"] for q in asked}
        ids = [i for i in work_ids(mark) if i not in refs]
        if not ids and not asked:
            continue
        down = (wound or {}).get(team)
        repo = Path(str(mark.get("repo") or "")).name
        held = work_held(mark.get("held"), at)
        n = len(ids)
        finished = work_finished(str(team), mark, records) if wound is not None and not down else []
        lanes = ("its lane" if len(finished) == 1 else "their lanes") if finished else "its lanes"
        said = f"{lanes} gained {n} entr{'y' if n == 1 else 'ies'}: {', '.join(ids[:WORK_IDS])}" if ids else ""
        if n > WORK_IDS:
            said += f" and {n - WORK_IDS} more"
        said = "; ".join([*(q["words"] for q in asked), *([said] if said else [])])
        who = ", ".join(f"{f['name']} finished" + (f" {f['at']}" if f["at"] else "") for f in finished) or "wound down"
        out.append(
            {
                "row": "work",
                "sid": f"work:{team}",
                "id": f"work:{team}",
                "name": str(team),
                "team": str(team),
                "at": str(mark.get("at") or ""),
                "age": _age(mark.get("at"), at),
                "wound_down": str(down) if down else "",
                "repo": repo,
                "ids": ids[:WORK_IDS],
                "more": max(0, n - WORK_IDS),
                "n": n,
                "questions": asked,
                "held": held,
                "running": bool(finished),
                "finished": finished,
                "lanes": lanes,
                "text": f"{team} · {who} · {said}" + (f" · not started: {held}" if held else ""),
                "find": " ".join(x for x in (str(team), "team start", who, said, held) if x),
            }
        )
    return out


def work_finished(
    team: str, mark: Mapping[str, Any], records: Iterable[Mapping[str, Any]]
) -> list[dict[str, str]]:
    """The members a running team's mark names (§6 rule 8 *A member that finished while its team runs
    on*, TD-466), each `{name, at}` in the mark's order: the mark keys them by name, and the instant is
    the latest `closed_at` among the team's records under that name (a superseded record and its
    successor share one); a member the page holds no closed record of is drawn with no time."""
    members = mark.get("members") if isinstance(mark.get("members"), Mapping) else {}
    closed: dict[str, str] = {}
    for r in records:
        if isinstance(r, Mapping) and r.get("team") == team and r.get("closed_at"):
            name, at = str(r.get("name") or ""), str(r["closed_at"])
            closed[name] = max(closed.get(name, ""), at)
    return [{"name": str(m), "at": closed.get(str(m), "")} for m in members]


def work_note(mark: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """design §4.5a team card **work waiting** note (§6 rule 8): *n entries waiting since <t>* beside
    *wound down <t>*, its tooltip the ids by member — `{n, at, age, title}`, or None for no mark."""
    if not isinstance(mark, Mapping) or not (ids := work_ids(mark)):
        return None
    members = mark.get("members") if isinstance(mark.get("members"), Mapping) else {}
    by = "; ".join(f"{m}: {', '.join(str(i) for i in got)}" for m, got in members.items() if isinstance(got, list))
    # a question's end is counted as one of the n and named in the tooltip (TD-271, TD-274)
    by = "; ".join([by, *(q["words"] for q in work_questions(mark))]) if by else by
    return {
        "n": len(ids),
        "at": str(mark.get("at") or ""),
        "age": _age(mark.get("at"), now or datetime.now(UTC)),
        "title": by,
    }


def work_started(members: Collection[Mapping[str, Any]], now: datetime | None = None) -> dict[str, Any] | None:
    """design §4.5a team card **work waiting** note on a live team: *started <t> for TD-213 and 2
    more*, from the newest `restarts` entry `why: work` on the team's live records (rule 8's start
    writes one on every record it replays) — `{at, age, first, more}`, or None. A start whose
    replays partly failed adds `n`, `of` and `failed`: every entry of one start carries its `start`
    instant and `of`, the records it set out to replay, and a failed replay leaves its entry, with
    `error`, on the ended record — *· 2 of 3 records started — manager-ao-1 did not*."""
    marks = [
        e
        for m in members
        if m.get("state") not in ("exited", "closed")
        for e in (m.get("restarts") or [])
        if isinstance(e, Mapping) and e.get("why") == "work" and e.get("at") and not e.get("error")
    ]
    if not marks:
        return None
    last = max(marks, key=lambda e: str(e["at"]))
    ids = [str(i) for i in last.get("ids") or []]
    out: dict[str, Any] = {
        "at": str(last["at"]),
        "age": _age(last["at"], now or datetime.now(UTC)),
        "first": ids[0] if ids else "",
        "more": max(0, len(ids) - 1),
    }
    start, of = last.get("start"), last.get("of")
    if start and isinstance(of, int):  # a home before the count writes neither, and the note says nothing of it
        same = [
            (m, e)
            for m in members
            for e in (m.get("restarts") or [])
            if isinstance(e, Mapping) and e.get("why") == "work" and e.get("start") == start
        ]
        # a failed replay's entry goes with the record's history onto whatever starts it later (a
        # crash restart, a person's Start), so a name that is live now did come up, whatever it carries
        up = {str(m.get("name") or m.get("id") or "") for m in members if m.get("state") not in ("exited", "closed")}
        names = (str(m.get("name") or m.get("id") or "") for m, e in same if e.get("error"))
        failed = [n for n in dict.fromkeys(names) if n not in up]
        if failed:
            out.update(n=of - len(failed), of=of, failed=failed)
    return out


def board_due_now(it: Mapping[str, Any]) -> bool:
    """Whether a reader's item is **due now** (§4.5 screen 6 *The board's horizon*): what the
    `--due-only` read surfaced, and so a counted *Needs you* row under every mode — a decided item
    whatever its date, one whose date could not be read (`due_error`), one due today or earlier; an
    undecided `fyi` item never (the reader's own `surfaces_at_start`, read here from its fields)."""
    if it.get("kind") == "fyi" and not it.get("decided"):
        return False
    return bool(it.get("decided")) or bool(it.get("due_error")) or it.get("overdue_days") is not None


def board_body(text: str, it: Mapping[str, Any]) -> str:
    """A board row's body (§4.5a *answers*, TD-255): the item's text without its `Answers:` and
    `Decided:` tails where the reader read them as fields — those are drawn as the row's buttons and
    its *decided* line, so the body does not print them twice. The reader's fields decide, never the
    prose: an item the reader gave no `answers` and no `decided` keeps its whole text."""
    fields = (it.get("answers") and board_mod.ANSWERS_RE, it.get("decided") and board_mod.DECIDED_RE)
    due = board_mod.DUE_RE.search(text)
    off = due.end() if due else 0  # the reader looks for the fields after the item's `Due:`
    starts = [m.start() for m in (f.search(text[off:]) for f in fields if f) if m]
    return text[: off + min(starts)].rstrip() if starts else text


# the reader's own `Context:` field (`CONTEXT_RE` in dev-cadence's `nudge_user_attention.py`)
# a bold run ending in a colon after a sentence's end — *… (TD-095). **Cards:** a report …* — is the
# head of a part of a long line, and opens a paragraph under *details*
SUBHEAD_RE = re.compile(r"(?<=[.;!?)])\s+(?=\*\*[^*\n]{1,80}?:\*\*)")
# — at a sentence's start only, so *the Context: of this* in prose and a bold **Context:** stay text
CONTEXT_RE = re.compile(r"(?:^|(?<=[.;!?)]\s))Context:\s*(?P<ctx>.*?)(?=\s+(?:Due|Answers|Decided|Closed):|$)")
# a reply tail's mark, as the reader writes it: *— Paul, 2026-10-02:*
REPLY_MARK_RE = re.compile(r"(?:^|\s)—\s+[^,—]+,\s*\d{4}-\d{2}-\d{2}:")


def board_text(body: str, it: Mapping[str, Any]) -> dict[str, Any]:
    """A board row's text as the row draws it (§4.5a *Inbox board row: text*, TD-279), from its
    `board_body`: `lead`, up to the end of its bold head and the sentence after it (`fold_head`),
    and `rest`, under *details*, ending with the line's `Context:` as a paragraph of its own; the
    `Due:` is the row's due words and goes, and the person's replies are `replies`, drawn under the
    text — from the reader's field, so a reader that gives none leaves them in the text. Display
    only: nothing here is a control."""
    replies = [
        {"by": str(r.get("by") or ""), "date": str(r.get("date") or ""), "text": str(r.get("text") or "")}
        for r in it.get("replies") or ()
        if isinstance(r, dict) and str(r.get("text") or "").strip()
    ]
    text, tail = body, ""
    due = board_mod.DUE_RE.search(body)
    if due:
        text, tail = body[: due.start()].rstrip(), body[due.end() :].lstrip(" .")
        if replies and tail.startswith("—") and len(REPLY_MARK_RE.findall(tail)) == len(replies):
            tail = ""  # the reader's replies, drawn from its field — every one of them, or none
        elif tail:
            replies = []  # what follows the `Due:` is not just the reader's replies: the text keeps it all, once
    if tail:
        text = f"{text} {tail}"
    ctx = CONTEXT_RE.search(text)
    context = ctx.group("ctx").rstrip(" .") if ctx else ""
    if ctx:
        text = (text[: ctx.start()] + text[ctx.end() :]).rstrip()
    lead, rest = rendermod.fold_head(text)
    rest = SUBHEAD_RE.sub("\n\n", rest)  # a walk's **Part:** heads each open a paragraph
    if context:
        rest = f"{rest}\n\nContext: {context}" if rest else f"Context: {context}"
    for r in replies:
        d = _civil(r["date"])
        r["date"] = f"{d:%b} {d.day}" if d else r["date"]
    return {"lead": lead, "rest": rest, "replies": replies}


def live_look(it: Mapping[str, Any]) -> bool:
    """Whether a board item's answers are cadence's fixed pair for a live look (§3.5, design §4.5a
    **Works** / **Not right…**): a `look` (cadence §3.3's kind for it, dev-cadence TD-082) or a
    `watch` written before that kind (TD-292), whose two answers are *Works* and the form *Not
    right: <what>*. Anything else — a third answer, another kind, a complete *Not right: wrong
    repo* — is ordinary answer buttons."""
    answers = [" ".join(str(a).split()) for a in it.get("answers") or ()]
    return (
        str(it.get("kind") or "") in LOOK_KINDS
        and len(answers) == 2
        and answers[0] == WORKS
        and bool(board_mod.NOT_RIGHT_FORM.fullmatch(answers[1]))
    )


# §4.5a **Inbox row: a look** (§4.10 *A look*, TD-292 slice 3): the one directory a look's screenshots
# are served from, as origin's default branch holds it, and the names that may be asked of it —
# the shape and the count the host agent checks a look's `shots` against (`mail`, TD-292 slice 2).
# The reading itself is `sessionorc.shots`, the one the host agent makes for another host (TD-300).
SHOT_DIR = shots_mod.SHOT_DIR
SHOT_NAME = mail_mod.SHOT_NAME
SHOTS_MAX = mail_mod.SHOTS_MAX
SHOT_TTL = 60.0  # seconds a screenshot's presence on origin is kept: a row is redrawn on every poll
_shot_seen: dict[tuple[str, str], tuple[float, bool]] = {}
SHOT_WAIT = 2.0  # seconds a row waits for another host's answer: a hung node holds the Inbox no longer
_shot_host_down: dict[str, float] = {}  # a host that did not answer, by when: not asked again for `SHOT_TTL`
shot_root = shots_mod.root_of
shot_bytes = shots_mod.read


def _shot_on_origin(root: Path, name: str) -> bool:
    key, now = (str(root), name), time.monotonic()
    at, seen = _shot_seen.get(key, (0.0, False))
    if now - at > SHOT_TTL:
        seen = shots_mod.exists(root, name)
        _shot_seen[key] = (now, seen)
    return seen


def _shot_on_host(host: str, repo: str, name: str) -> bool:
    """The same test for a repo another host registers (TD-300): `host_shot` with `head`, asked
    through the home and kept `SHOT_TTL` as this host's is, waiting `SHOT_WAIT` and no longer. A host
    that cannot be asked, or does not answer in time, reads as not holding it — the row draws the
    name alone — and is not asked again, for any name, for `SHOT_TTL`."""
    key, now = (f"{host}:{repo}", name), time.monotonic()
    at, seen = _shot_seen.get(key, (0.0, False))
    if now - at > SHOT_TTL:
        if now - _shot_host_down.get(host, -SHOT_TTL - 1.0) <= SHOT_TTL:
            return False
        try:
            got = call_sync("host_shot", host=host, repo=repo, name=name, head=True, _timeout=SHOT_WAIT)
            seen = bool((got or {}).get("exists"))
        except Exception:  # noqa: BLE001 — whatever the transport raised, the image is not drawn
            _shot_host_down[host] = now
            seen = False
        _shot_seen[key] = (now, seen)
    return seen


def look_shots(shots: Any, repo: str, host: str = "") -> list[dict[str, str]]:
    """A look's screenshots as its row draws them (§4.5a **Inbox row: a look**): each of the
    envelope's `shots`, in the order sent, at most four — `name`, the file's name, and `url`, the
    image's address on this page when origin holds the file in the sender's repo (`repo`, a
    registered checkout's name), else "" and the row draws the name alone. The repo is read where
    the sender runs: this host's registry, or — `host`, the sender's record's, when it is another —
    that host's through the home (TD-300). A path outside `docs/mockups/reviews/` has no address,
    whatever its name."""
    if not isinstance(shots, list):
        return []
    there = host if host and host != host_name() else ""
    root = None if there else shot_root(repo)
    out: list[dict[str, str]] = []
    for raw in shots[:SHOTS_MAX]:
        if not isinstance(raw, str) or not raw.strip():
            continue
        p = Path(raw.strip().removeprefix("./"))
        url = ""
        if repo and p.parent == SHOT_DIR and SHOT_NAME.fullmatch(p.name):
            if there and _shot_on_host(there, repo, p.name):
                url = f"/repo/{quote(repo)}/shot/{quote(p.name)}?host={quote(there)}"
            elif root is not None and _shot_on_origin(root, p.name):
                url = f"/repo/{quote(repo)}/shot/{quote(p.name)}"
        out.append({"name": p.name, "url": url})
    return out


def look_pair(e: Mapping[str, Any]) -> bool:
    """Whether a mail row draws its answers as a live look's pair, **Works** / **Not right…**
    (§4.5a **Inbox row: a look**): an `ask` whose envelope carries `shots` and whose suggested
    answers are exactly *Works* and the form *Not right: <what>*. A look is known by its `shots`,
    never by its words; a `steer` look keeps its kind's row, and any other answers are ordinary."""
    shots = e.get("shots")
    answers = [" ".join(str(a).split()) for a in e.get("answers") or ()]
    return (
        e.get("kind") == "ask"
        and isinstance(shots, list)
        and any(isinstance(s, str) and s.strip() for s in shots)
        and len(answers) == 2
        and answers[0] == WORKS
        and bool(board_mod.NOT_RIGHT_FORM.fullmatch(answers[1]))
    )


def look_review(e: dict[str, Any], seat: str, seat_name: str, handed: Mapping[str, Mapping[str, Any]]) -> None:
    """§4.5a **Send to reviewer** (§4.10 *A look*, TD-292 slice 4b), on a look that is an open `ask`
    (it carries `shots`): with a reviewer already — `snoozed_for` names the `handed` `ask` that
    carries it — the row reads *with <seat> since <time>* from that entry in `handed` (by id, the
    holder's own copy the person's read lists), *a reviewer* where the read no longer lists it;
    otherwise the button is drawn when the sender's team has a techlead seat, `seat` being the id it
    takes as the page reads it from `org.yml` (the host agent reads none) and `seat_name` its name.
    Any other entry is left as it is."""
    if not e.get("shots") or e.get("kind") != "ask" or not _entry_open(e):
        return
    hid = str(e.get("snoozed_for") or "")
    if hid:
        h = handed.get(hid) or {}
        e["with_seat"] = str(h.get("holder_name") or h.get("holder") or "a reviewer")
        e["with_at"] = str(h.get("at") or "")
        e["with_since"] = _clock(h.get("at"))
        return
    if seat:
        e["review_seat"] = seat
        e["review_name"] = seat_name or seat


def board_head(text: str) -> str:
    """A board item's head, as the write-back's commit message names it: its first bold run, else
    the line, clipped."""
    h = board_mod.HEAD_RE.search(text)
    words = " ".join((h.group("head") if h else text).split())
    return words if len(words) <= board_mod.HEAD_MAX else words[: board_mod.HEAD_MAX - 1].rstrip() + "…"


def _decided(it: Mapping[str, Any]) -> dict[str, str] | None:
    """The reader's `decided` as the row prints it — *decided: <text> · <date>* — or None."""
    got = it.get("decided")
    if not isinstance(got, dict) or not got.get("text"):
        return None
    d = _civil(got.get("date"))
    return {"text": str(got["text"]), "date": f"{d:%b} {d.day}" if d else str(got.get("date") or "")}


def _ahead_words(due: str, today: str, tag: str = "") -> str:
    """A not-yet-due item's due words, *due in 6 d · Oct 4*; an undated one's, *no due date*. An item
    that is not due now yet dated today or earlier — an undecided `fyi`, which the reader never
    surfaces as due — takes the reader's own words for it (*3d overdue*), never *due in -3 d*."""
    d, t = _civil(due), _civil(today)
    if d is None:
        return "no due date"
    if t is None:
        return f"due {d:%b} {d.day}"
    if d <= t:
        return tag or f"due {d:%b} {d.day}"
    return f"due in {(d - t).days} d · {d:%b} {d.day}"


def _civil(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v or ""))
    except ValueError:
        return None


# §4.5 screen 6 *A board item the person answered waits on them* (TD-303, built by TD-305): an
# answered row waits under *Waiting on them* until this many civil days from the answer's date, and
# then comes back to where it would be drawn unanswered, saying nobody has acted
BOARD_WAIT_DAYS = 3


def board_answered(it: Mapping[str, Any], today: str) -> dict[str, Any]:
    """Whom a reader's item waits on (§4.5 screen 6 *A board item the person answered waits on
    them*), from the reader's fields and never the prose: `answered` is `decided` when the reader
    says `waiting_on: session` (a reader that gives no `waiting_on`: its `decided`), else `replied`
    when an undecided item carries `replies`, else empty; `answered_at` the answer's date — the
    `decided` date, the last reply's — and `stale` whether `BOARD_WAIT_DAYS` or more civil days lie
    between it and the reader's `today`, with `stale_line` the words the row then says. A date that
    does not parse reads as not stale."""
    decided = it.get("waiting_on") == "session" if "waiting_on" in it else bool(it.get("decided"))
    replies = [r for r in it.get("replies") or () if isinstance(r, dict) and str(r.get("text") or "").strip()]
    if decided:
        got = it.get("decided")
        how, at = "decided", str(got.get("date") or "") if isinstance(got, dict) else ""
    elif replies:
        how, at = "replied", str(replies[-1].get("date") or "")
    else:
        return {"answered": "", "answered_at": "", "stale": False, "stale_line": ""}
    d, t = _civil(at), _civil(today)
    days = (t - d).days if d and t else None
    stale = days is not None and days >= BOARD_WAIT_DAYS
    line = f"{how} {d:%b} {d.day} — no session has acted in {days} d" if stale and d else ""
    return {"answered": how, "answered_at": at, "stale": stale, "stale_line": line}


def board_waiting_on(
    r: Mapping[str, Any], records: Collection[Mapping[str, Any]], now: datetime, starting: Collection[str] = ()
) -> str:
    """The *waiting on …* words of an answered board row (§4.5 screen 6, the standing of §4.5a as
    words): a live record with an unexpired declared lease on one of the item's `refs` or on the
    line's own work order (*waiting on grinder-ao-2 — holds TD-122*, *… — holds board:1a2b3c4d*),
    else a live record the item's `session` names, else — a decided line being a work order in the
    repo's lanes (§4.4 *Board write-back*, TD-384) — *pickable by <team>* naming the teams whose
    `free-pick` lanes take it, with each one's wound-down state when none of them is live (*· the
    start row is in Needs you* for a team in `starting`, the teams a *team start* row names, else
    *· <team> wound down — starts on `on_work`*), else the next session to read the repo's board.
    Read from the fleet the page already holds, as `orphan_standing` reads it for mail; display only."""
    order = _board_order(r)
    holder, ref = _board_holder(r, records, now, order)
    if holder:
        return f"waiting on {holder} — holds {ref}"
    named = _board_named(r, records)
    if named:
        return f"waiting on {named}"
    if order:
        teams = _order_teams(r, order, records)
        if teams:
            words = f"pickable by {', '.join(teams)}"
            if not any(teams.values()):
                for t in teams:
                    words += (
                        " · the start row is in Needs you"
                        if t in starting
                        else f" · {t} wound down — starts on `on_work`"
                    )
            return words
    return f"waiting on the next session to read {r.get('repo') or 'its repo'}'s board"


def _board_order(r: Mapping[str, Any]) -> str:
    """A board row's work order, `board:<key>` by its repo's own reader (TD-384), or empty: only a
    decided line that is not `fyi` is one."""
    if not r.get("decided") or r.get("kind") == "fyi" or not r.get("root"):
        return ""
    return workorders_mod.ref(str(r["root"]), str(r.get("text") or ""))


def _order_teams(r: Mapping[str, Any], order: str, records: Collection[Mapping[str, Any]]) -> dict[str, bool]:
    """The teams whose `free-pick` lanes take a work order (§6 rule 6, `lane_matches`), from their
    records in the board's repo, live or ended, never one resumed as another — each `{team: live}`."""
    here = _resolved(str(r.get("root") or ""))
    if not here:
        return {}
    entry = {"id": order, "work_order": True, "pickable": "yes"}
    out: dict[str, bool] = {}
    for rec in records:
        if not isinstance(rec, Mapping) or not rec.get("team") or not rec.get("repo") or rec.get("superseded_by"):
            continue
        if _resolved(str(rec["repo"])) != here or not ledger_mod.lane_matches(list(rec.get("lane") or ()), entry):
            continue
        team = str(rec["team"])
        out[team] = out.get(team, False) or rec.get("state") not in mail_mod._NOT_LIVE
    return dict(sorted(out.items()))


@functools.lru_cache(maxsize=256)
def _resolved(path: str) -> str:
    """A path resolved once per process — a decided row asks it of every record on each poll — or
    empty for one that will not resolve (a NUL, a loop)."""
    try:
        return str(Path(path).resolve()) if path else ""
    except (OSError, ValueError, RuntimeError):
        return ""


def _board_holder(
    r: Mapping[str, Any], records: Collection[Mapping[str, Any]], now: datetime, order: str = ""
) -> tuple[str, str]:
    """The first live record with an unexpired declared lease on one of a board row's `refs`, or on
    its work order `order`, as `(name, ref)` — the session `board_reply` hands its note to — or
    `("", "")`."""
    for ref in [*(r.get("refs") or ()), *([order] if order else [])]:
        held = mail_mod.lease_holders(str(ref), records, now)
        if held:
            return str(held[0].get("name") or held[0].get("id") or ""), str(ref)
    return "", ""


def _board_named(r: Mapping[str, Any], records: Collection[Mapping[str, Any]]) -> str:
    """The name of a live record the board row's `session` names (its name, its id, or its tool's
    session id by the board's eight-character prefix), or empty."""
    sess = str(r.get("session") or "")
    if not sess:
        return ""
    for rec in records:
        if not isinstance(rec, Mapping) or rec.get("state") in mail_mod._NOT_LIVE:
            continue
        if sess in (rec.get("name"), rec.get("id")) or (
            len(sess) >= 8 and str(rec.get("adapter_id") or "").startswith(sess)
        ):
            return str(rec.get("name") or rec.get("id") or "")
    return ""


def board_standing(
    r: Mapping[str, Any], records: Collection[Mapping[str, Any]], now: datetime
) -> dict[str, str] | None:
    """A board row's **standing** (design §4.5a *Inbox board row: standing*, TD-142): where a Reply
    will go, said before the press — *still on TD-122 — grinder-ao-2 holds it* while a live record
    holds an unexpired declared lease on one of the item's `refs` (Reply mails it), else *moved on*
    while a live record carries the item's `session` name, else *gone*. `word` is `still on`,
    `moved on` or `gone`; `holder` and `ref` name the lease. None for an item the reader gave no
    `session` and no `refs`: nothing is known, so nothing is drawn. Read from the records the page
    already holds; display only — the home reads the leases again at the press."""
    if not r.get("session") and not r.get("refs"):
        return None
    holder, ref = _board_holder(r, records, now)
    if holder:
        return {"word": "still on", "holder": holder, "ref": ref, "text": f"still on {ref} — {holder} holds it"}
    word = "moved on" if _board_named(r, records) else "gone"
    return {"word": word, "holder": "", "ref": "", "text": word}


def board_standings(
    rows: Collection[dict[str, Any]], records: Collection[Mapping[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """`rows` as copies carrying their `standing` (`board_standing`) — copies, because the board
    rows are the page's cache, read again by the next request."""
    at = now or datetime.now(UTC)
    return [{**r, "standing": board_standing(r, records, at)} if r.get("row") == "board" else r for r in rows]


def with_standings(
    hz: Mapping[str, Any], records: Collection[Mapping[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """A board horizon (`board_horizon`, `horizon_of`) whose drawn rows — due, coming up and the
    *not shown* fold — carry their `standing`; the answered rows under *Waiting on them* say
    *waiting on …* instead and are left as they are."""
    at = now or datetime.now(UTC)
    return {**hz, **{k: board_standings(hz.get(k) or (), records, at) for k in ("due", "ahead", "hidden")}}


def board_detail(it: Mapping[str, Any]) -> str:
    """An item's **detail block** (§4.5a *Inbox board row: detail block*, TD-312): the reader's
    `detail` lines joined as one text, drawn through the closed subset under the row's *details* —
    only when the key is a non-empty list of strings; anything else draws the row as it was. Data,
    never a field: nothing in it is read here."""
    got = it.get("detail")
    if not isinstance(got, list) or not got or not all(isinstance(x, str) for x in got):
        return ""
    return "\n".join(got).strip("\n")


def board_rows(report: Any, teams: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """design §4.5a **Due strip / Inbox board row** rows, as the Inbox draws them (TD-069 step 3): one
    per open item of the report — its repo, its due words, the whole text, and the board at that
    line in the editor — with `due_now` saying which are due (`board_due_now`) and `ahead` the due
    words of one that is not (TD-220). `at` is the due date, so *oldest first* in **Needs you**
    puts the longest overdue first among the states and the mail. The text is the board's, shown as
    text: nothing here is a control built from it (TD-071 item 8)."""
    rows: list[dict[str, Any]] = []
    today = str(report.get("today") or "") if isinstance(report, dict) else ""
    boards = report.get("boards") if isinstance(report, dict) else None
    for b in boards if isinstance(boards, list) else ():
        if not isinstance(b, dict):
            continue
        root = str(b.get("root") or "")
        board = str(b.get("board") or "")
        label = str(b.get("label") or Path(root).name)
        team = (teams or {}).get(str(Path(root).resolve()), "") if root else ""
        for it in b.get("items") or ():
            if not isinstance(it, dict) or not it.get("text"):
                continue
            line, text, tag = it.get("line"), str(it["text"]), str(it.get("due_tag") or "")
            detail = board_detail(it)
            url = vscode_url(board) if board else ""
            if url and isinstance(line, int):
                head, _, query = url.partition("?")
                url = f"{head}:{line}" + (f"?{query}" if query else "")
            rows.append(
                {
                    "row": "board",
                    "id": f"board:{root}:{line}",
                    "repo": label,
                    "root": root,
                    "board": board,
                    "line": line,
                    "team": team,
                    "text": text,
                    # §4.5a **answers** / **Go with it** (§4.4 *Decide*, TD-255): the reader's own
                    # fields, never read out of the prose here — the item's answers in the order
                    # written, the one marked default, what was decided, and the body without them
                    "body": board_body(text, it),
                    # §4.5a *Inbox board row: text* (TD-279): the body folded after its head, the
                    # `Context:` under *details*, the replies from the reader's field
                    **board_text(board_body(text, it), it),
                    "detail": detail,  # §4.5a *Inbox board row: detail block* (TD-312)
                    "answers": [str(a) for a in it.get("answers") or () if str(a).strip()],
                    "default": str(it.get("default") or ""),
                    "decided": _decided(it),
                    "kind": str(it.get("kind") or ""),
                    # §4.5a **Works** / **Not right…** (TD-255 slice 3): a live look's pair, known
                    # by its words; `head` and `name` are what the *Not right* hand-off sends
                    "pair": live_look(it),
                    "head": board_head(text),
                    "name": Path(root).name if root else "",
                    "due": str(it.get("due") or ""),
                    "due_tag": tag,
                    "at": str(it.get("due") or ""),
                    "due_now": board_due_now(it),
                    "today": today,  # the reader's, which `board_horizon` measures days from
                    # §4.5 screen 6 *A board item the person answered waits on them* (TD-305): whom
                    # it waits on, and the reader's `session` and `refs` for the *waiting on* words
                    **board_answered(it, today),
                    "session": str(it.get("session") or ""),
                    "refs": [str(x) for x in it.get("refs") or () if str(x).strip()],
                    "due_error": bool(it.get("due_error")),
                    "ahead": "" if board_due_now(it) else _ahead_words(str(it.get("due") or ""), today, tag),
                    "editor": url,
                    # §4.5 screen 6 *Boards are read against origin* (TD-221): whether the board was
                    # read from origin (the reader's `source`, non-null only then) and the reader's
                    # sentence for what its fetch found, kept for the note above the repo's rows
                    "source": str(b.get("source") or ""),
                    "fetch_note": str(b.get("fetch_note") or ""),
                    "find": _find_text(label, text, tag, "board", detail),
                }
            )
    return rows


def board_horizon(rows: Collection[dict[str, Any]], mode: str | None = None, today: str = "") -> dict[str, Any]:
    """The board's horizon (design §4.5 screen 6, §5 `person.inbox.board_show`; TD-220): the board rows
    sorted into `due` — due now, a counted *Needs you* row under every mode — `ahead`, what the mode
    draws before it is due (*Board, coming up*), and `hidden`, what it does not (the *not shown*
    fold); soonest first, an undated item after the dated ones. `mode` is the setting as read,
    `next:10` when it is unset or does not parse; `today` is the reader's (each row carries it),
    for the days a `<n>d` reaches. A row with no `due_now` is due: a row from before TD-220 was the `--due-only` read's.
    An answered row that is not stale (`board_waits`, TD-305) is in none of the three but `waiting`,
    drawn under *Waiting on them* and taking no place.

    - `next:<n>`: per team, the n soonest open items of its boards, the due ones among them, so
      what is due takes places first; a due row whose date could not be read takes none. A board
      no team works is its repo's own group. The team is the row's badge (`repo_teams`).
    - `due`: nothing ahead. `<n>d`: what falls due within n days of `today`; an undated item is
      hidden. `all`: every open item.

    `next_due` is the soonest date among the hidden, for the line (*the next due Oct 12*)."""
    try:
        mode = settings_mod.parse_board_show(mode)
    except ValueError:
        mode = settings_mod.BOARD_SHOW_DEFAULT
    waiting = [r for r in rows if board_waits(r)]
    rows = [r for r in rows if not board_waits(r)]
    due = [r for r in rows if r.get("due_now", True)]
    rest = sorted(
        (r for r in rows if not r.get("due_now", True)),
        key=lambda r: (not r.get("due"), str(r.get("due") or ""), str(r.get("repo") or ""), r.get("line") or 0),
    )
    ahead: list[dict[str, Any]] = []
    if mode == "all":
        ahead = rest
    elif mode.endswith("d"):
        t = _civil(today or next((r.get("today") for r in rows if r.get("today")), "")) or date.today()
        until = (t + timedelta(days=int(mode[:-1]))).isoformat()
        ahead = [r for r in rest if r.get("due") and str(r["due"]) <= until]
    elif mode.startswith("next:"):
        n = int(mode.split(":", 1)[1])

        def group(r: Mapping[str, Any]) -> str:
            return f"team:{r['team']}" if r.get("team") else f"repo:{r.get('root') or r.get('repo') or ''}"

        places: dict[str, int] = {}
        for r in due:
            if not r.get("due_error"):
                places[group(r)] = places.get(group(r), 0) + 1
        for r in rest:
            g = group(r)
            if places.get(g, 0) < n:
                ahead.append(r)
                places[g] = places.get(g, 0) + 1
    shown = {id(r) for r in ahead}
    hidden = [r for r in rest if id(r) not in shown]
    now = str(today or next((r.get("today") for r in rows if r.get("today")), "")) or date.today().isoformat()
    return {
        "mode": mode,
        "due": due,
        "ahead": ahead,
        "hidden": hidden,
        "waiting": waiting,
        "next_due": _next_due(hidden, now),
        "today": now,
    }


def board_waits(r: Mapping[str, Any]) -> bool:
    """Whether a board row is drawn under *Waiting on them* (§4.5 screen 6, TD-305): answered, and
    not yet `BOARD_WAIT_DAYS` from the answer — in no count and out of the board's horizon."""
    return bool(r.get("answered")) and not r.get("stale")


def _next_due(hidden: Collection[Mapping[str, Any]], today: str) -> str:
    """The line's *the next due*: the soonest date among the hidden that is ahead of today — a hidden
    past-dated `fyi` is not what comes next."""
    dated = [str(r["due"]) for r in hidden if r.get("due") and str(r["due"]) > today]
    return min(dated) if dated else ""


def horizon_of(h: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    """The Repo page's *Waiting on you* (§4.5 screen 11): the Inbox's horizon filtered to one repo —
    the same rows the Inbox draws, each list cut to this checkout, and the line's next date from what
    is left hidden. The mode's places are the Inbox's, so a row is coming up here exactly when it is
    there."""
    want = str(Path(root).resolve())

    def mine(rs: Collection[Mapping[str, Any]]) -> list[Any]:
        return [r for r in rs if r.get("root") and str(Path(str(r["root"])).resolve()) == want]

    hidden = mine(h.get("hidden") or ())
    out = {**h, "due": mine(h.get("due") or ()), "ahead": mine(h.get("ahead") or ()), "hidden": hidden,
           "waiting": mine(h.get("waiting") or ()),
           "next_due": _next_due(hidden, str(h.get("today") or ""))}  # fmt: skip
    if h.get("line") is not None:  # the line says this repo's hidden count and next date, not the Inbox's
        out["line"] = board_line(out)
    return out


def board_line(h: Mapping[str, Any]) -> dict[str, Any]:
    """The line that closes the board rows (§4.5 screen 6 *The board's horizon*, §4.5a): the page
    saying its mode — *showing the next 10 board items per team* — then what it hides, *14 not shown,
    the next due Oct 12*, or *nothing hidden*. `n` is the count hidden: with none there is no fold
    and no **show**."""
    mode = str(h.get("mode") or settings_mod.BOARD_SHOW_DEFAULT)
    if mode == "all":
        says = "showing every board item"
    elif mode == "due":
        says = "showing board items that are due"
    elif mode == "7d":
        says = "showing board items due this week"
    elif mode.endswith("d"):
        says = f"showing board items due within {mode[:-1]} days"
    else:
        n = int(mode.split(":", 1)[1]) if mode.startswith("next:") else 10
        says = f"showing the next {n} board item{'' if n == 1 else 's'} per team"
    hidden = len(h.get("hidden") or ())
    nd = _civil(h.get("next_due"))
    if not hidden:
        rest = "nothing hidden"
    else:
        rest = f"{hidden} not shown" + (f", the next due {nd:%b} {nd.day}" if nd else "")
    return {"says": says, "rest": rest, "n": hidden}


def _needs_key(item: dict[str, Any]) -> tuple[int, str]:
    """The **Needs you** order (design §4.5 screen 6): *what is on the tool's clock first (a
    permission's countdown), then oldest first* — across states and mail together, which is why one
    key reads both."""
    if item.get("row") == "permission" and item.get("deadline"):
        return (0, str(item["deadline"]))
    return (1, str(item.get("at") or ""))


# design §4.10 *Outcomes*: the same predicate as `MailEntry.owes`, over the dict the RPC hands the
# page. It is duplicated rather than imported because the page reads entries, not models — and it
# is the *one* rule that decides which section a settled question is in, so it says so out loud.
OWING_CLOSES = ("replied", "go_with_it")
# …and **every** kind that can be one, which is not `PERSON_ASK_KINDS`: that pair is about which
# *open* entry is a question in *Needs you*, where a `steer` has its own branch. A debt is the
# model's `ASK_KINDS`, `steer` included — *a `go_with_it` close owes one too* (§4.10 *Outcomes*),
# and `go_with_it` is a `steer`'s own close reason. The agent enforces the debt on that set, so a
# narrower one here would leave a `steer`'s debt sitting in FYI, uncounted, while `ao progress
# none` was still refusing its sender (review of PR #287).
OWING_KINDS = ("ask", "steer", "conflict")


def _owing(e: dict[str, Any], record: dict[str, Any] | None, now: datetime) -> None:
    """Stamp a question the person answered with what *Waiting on them* and *Needs you* need
    (§4.10 *Outcomes*, §4.5a): whether it still owes an outcome, how long it has owed it, what the
    person answered as far as the inbox still holds it, and whether its asker has **exited without
    reporting** — which is what moves the row into the counted section, because then only a person
    or the asker's manager can find out what happened.

    An asker whose record is **gone** is not this row: the home settles that as `asker_gone` and
    the question stops owing. `exited` is the live record that will not report by itself."""
    outcome = _outcome_of(e)
    e["owes"] = bool(
        "person" in (e.get("to") or [])
        and e.get("kind") in OWING_KINDS
        and e.get("closed_reason") in OWING_CLOSES
        and not outcome
    )
    e["owed_age"] = _age(e.get("closed_at"), now) if e["owes"] else ""
    # what the person answered, as far as the person inbox still holds it: a pressed suggested
    # answer is in the entry itself (§4.10 *Suggested answers*), and a typed reply is not — the
    # reply went to the asker's inbox, not to this one — so the row says which of the two it was
    # rather than inventing words the person did not write
    answers = e.get("answers") if isinstance(e.get("answers"), list) else []
    idx = e.get("answer")
    e["answer_given"] = str(answers[idx]) if isinstance(idx, int) and 0 <= idx < len(answers) else ""
    e["answer_how"] = "you let it go with its default" if e.get("closed_reason") == "go_with_it" else "you answered"
    e["asker_state"] = str((record or {}).get("state") or "")
    # the record here is the raw one off `list`, not a `view()` — so its `doing` is `{text, at}`
    # and the row wants `{text, age}`, as the card and the Focus header draw it (§4.8, TD-074)
    doing = (record or {}).get("doing")
    doing = doing if isinstance(doing, dict) else {}
    e["asker_doing"] = (
        {"text": str(doing.get("text") or ""), "age": _age(doing.get("at"), now)} if doing.get("text") else None
    )
    e["asker_gone_quiet"] = bool(e["owes"] and e["asker_state"] in ("exited", "closed"))
    if outcome:
        e["outcome_age"] = _age(outcome.get("at"), now)


def handed_rows(
    handed: Collection[dict[str, Any]], records: Mapping[str, dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """The entries the person handed a seat (§4.10 *An entry handed to a seat*, TD-219): the
    `inbox` read lists them as `handed` beside the person inbox, each the holder's own copy with
    `holder`, `holder_name` and `holder_state`, while it owes its outcome or came back `blocked`.
    Each becomes a row keyed `handed_row`, never `row` — it is mail, so its page (`/inbox/<id>`) and
    **Dismiss** reach it by its id as any entry's do. It waits on the seat, not on the holder: a seat
    that exited half way is filled again, so an exited holder is not the counted *asker gone quiet*
    of an answered question; only `blocked` is counted (`inbox_sections`)."""
    out = []
    for e in handed:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        holder = str(e.get("holder") or "")
        rec = records.get(holder) or {}
        doing = rec.get("doing") if isinstance(rec.get("doing"), dict) else {}
        entry = e.get("entry") if isinstance(e.get("entry"), dict) else {}
        out.append(
            {
                **e,
                "handed_row": True,
                "entry": entry,
                "team": e.get("team") or rec.get("team") or "",
                "from_name": "you",
                "holder_name": str(e.get("holder_name") or rec.get("name") or holder),
                "holder_state": str(e.get("holder_state") or rec.get("state") or ""),
                "holder_open": holder if holder in records else "",
                "holder_doing": (
                    {"text": str(doing.get("text") or ""), "age": _age(doing.get("at"), now)}
                    if doing.get("text")
                    else None
                ),
                "age": _age(e.get("at"), now),
                "outcome_age": _age(_outcome_of(e).get("at"), now) if _outcome_of(e) else "",
            }
        )
    return out


def _outcome_of(e: dict[str, Any]) -> dict[str, Any]:
    """An entry's `outcome` (§4.10 *Outcomes*) as a dict, or `{}` — read as a shape, never trusted:
    an entry from another build may carry anything there and it must cost a row a line, not the
    page (the `_age` rule)."""
    o = e.get("outcome")
    return o if isinstance(o, dict) else {}


def _answered_of(e: dict[str, Any]) -> dict[str, Any]:
    """An entry's `answered` (§4.9b, TD-075 step 2) as a dict, or `{}` — the structured field the
    home writes on an *answered for you* FYI, `{question, asker, answerer, source}`. The row and its
    **Overrule** key on it and on nothing else: never on who sent the entry, never on a role (§9
    invariant 9). Read as a shape, as `_outcome_of` is."""
    a = e.get("answered")
    return a if isinstance(a, dict) and a else {}


def _trail_rows(trail: Collection[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """The trail as FYI rows (§4.10 *The Inbox is a queue*, TD-079): what a state row's **ending**
    left behind, so a row that resolved by some other road does not simply vanish. The home writes
    these, and they are not mail — the page gives them `at` and `age` so they sort and read beside
    the entries, and a `row` of `trail` so one renderer draws them."""
    out = []
    for t in trail:
        if not isinstance(t, dict) or not t.get("id"):
            continue
        at = str(t.get("last") or t.get("resolved_at") or "")
        # *up for* is how long the row **stood**, which is its ending minus its start — not its
        # age now, which is a different number and the one a reader would misread it as
        ended = _instant(at) or now
        out.append({**t, "row": "trail", "at": at, "age": _age(at, now), "for_words": _age(t.get("since"), ended)})
    return out


def _orphan_held(e: dict[str, Any]) -> bool:
    """An orphaned `steer` whose bound the home cleared (design §4.10 *An orphaned `steer` does not
    lapse*): nobody is left to take its default, so from its bound it waits on the person — under
    *Needs you* and counted, as a paused one is. With its clock still running it is *Steering*."""
    return e.get("kind") == "steer" and bool(e.get("orphaned")) and not e.get("bound")


def inbox_sections(
    entries: Collection[dict[str, Any]],
    *,
    now: datetime | None = None,
    states: Collection[dict[str, Any]] = (),
    trail: Collection[dict[str, Any]] = (),
    attention_snoozed: dict[str, Any] | None = None,
    boards: Collection[dict[str, Any]] = (),
    handed: Collection[dict[str, Any]] = (),
    fleet: Collection[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Design §4.5 screen 6: the person inbox split into the page's three sections, plus what is
    snoozed — and, from TD-069 step 2, the **session states** (`states`, from `state_rows`) joined
    into **Needs you** here rather than anywhere else. From step 3 the due **board items**
    (`boards`, from `board_rows`) join it the same way, counted: a due board item is waiting on a
    person by definition. They carry no snooze of the Inbox's own: a board row's Snooze is §4.4's
    write-back, which moves its `Due:` date on the board itself.

    - **Needs you** — the state rows, open `ask`s to the person (an open `conflict` too: it cannot be addressed to
      the person, §4.10, but one written before that gate would still be a question nobody else
      can answer) and **paused** `steer`s; oldest first.
    - **Steering** — open `steer`s whose clock is running; soonest bound first, and a `steer`
      that somehow carries no bound last.
    - **FYI** — everything else: `note`s, `system` notes, replies, and every closed entry until
      retention prunes it (`MAIL_RETENTION`, 12 h); newest first, because the useful end of a list
      nobody must act on is the recent end.
    - **Snoozed** — an entry whose `snoozed_until` is still ahead is in none of the three and in
      no count (§4.10 *Snooze*); soonest first, so the page can draw the **Snoozed (n)** fold and a
      snooze is never a way to lose mail.

    `count` is the **Needs you** section's length, which is the top bar's number (§4.5a): what is
    waiting on a person, never unread mail. One computation, used by the page and by the poll.
    An unreadable `snoozed_until` reads as *not snoozed*: mail is never hidden by a bad field.

    - **Waiting on them** (§4.10 *Outcomes*, TD-079 step 2) — answered questions that owe an
      outcome and whose asker is still live: it waits on a session, not on the person, so it is in
      neither number. A debt whose asker **exited without reporting** is in *Needs you* instead,
      counted — only a person, or the asker's manager, can find out what happened — and so is a
      **`blocked`** outcome, which is not a dead end but work stopped on something only a person
      can move. A `done` or `dropped` outcome is FYI, shown under the question it closes.
      **An entry the person handed a seat** (`handed`, from `handed_rows`; §4.10 *An entry handed
      to a seat*, TD-219) waits here from the press until its outcome, whatever its holder's state
      — a seat that exited is filled again — and is in *Needs you*, counted, once it came back
      `blocked`. A `done` or `dropped` one is no longer listed by the read, so it is in neither.
      **A board item the person answered** (§4.5 screen 6, TD-305) — decided, or replied to — waits
      here, uncounted, whatever its `Due:`, until `BOARD_WAIT_DAYS` from the answer (`board_waits`);
      then it is where it would be unanswered, saying so. Its *waiting on …* words are read from
      `fleet`, the records the request already holds.

    **A state row's snooze** (§4.10 *The Inbox is a queue*, TD-079 step 1b) lives in the home's own
    attention store, per record and row kind, because a state has no mail entry to carry one:
    `attention_snoozed` is that store as the RPC hands it over, and a row whose time is still
    ahead is in no section and no count, exactly as a snoozed entry is.

    The **trail** (§4.10) is FYI too: what a state row's ending left behind, so a row that resolved
    by some other road — the session was resumed, the permission was answered in the terminal —
    does not simply vanish.

    - **Answered for you** (§4.9b, TD-075 step 2) — the FYI the home files when a teammate answers
      a question from the record (a reply carrying `source`): it carries `answered`, and is here
      instead of in FYI, uncounted, newest first, so what was decided in the person's name is
      never mixed in with the notes."""
    at = now or datetime.now(UTC)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in INBOX_SECTIONS}
    snoozed_rows = attention_snoozed or {}
    for r in states:
        if r.get("fyi"):
            out["fyi"].append(r)  # a promote in flight (§4.5a *Inbox row: promote*): FYI, uncounted
            continue
        raw = snoozed_rows.get(f"{r.get('sid') or ''}|{r.get('row') or ''}")
        if isinstance(raw, str) and raw.startswith("dismissed:"):
            # the restart row's **Dismiss** (§4.5a, TD-103): that one mark's row is gone from every
            # section; a new mark carries a new `at` and raises a new row
            if raw == f"dismissed:{r.get('at') or ''}":
                continue
            raw = None
        until = _iso(raw)
        if until and until > at:
            out["snoozed"].append({**r, "snoozed_until": str(raw), "until_words": _left(str(raw), at)})
        else:
            out["needs"].append(r)
    # The outcome `note` that settles a question is an ordinary entry in its own right, and is
    # **listed under its question** rather than beside it (§4.10 *Outcomes*) — so it is taken out
    # of the FYI list here and hung on the question's own row by `id`.
    settles = {str(_outcome_of(e)["by"]): e["id"] for e in entries if _outcome_of(e).get("by")}
    for e in entries:
        snoozed = _iso(e.get("snoozed_until"))
        outcome = _outcome_of(e)
        question = e.get("kind") in (*PERSON_ASK_KINDS, "steer")
        if (snoozed and snoozed > at and (_entry_open(e) or not question)) or (e.get("snoozed_for") and _entry_open(e)):
            # a look with a reviewer (`snoozed_for`, §4.5a **Send to reviewer**) is set aside until
            # that debt closes, which clears the field: listed with the snoozed, in no count. A
            # question closed while snoozed is not: a press that answers or closes it ends the
            # snooze with it, and it is listed by its close (§4.10 *Snooze*, TD-373)
            out["snoozed"].append(e)
        elif _entry_open(e) and (e.get("kind") in PERSON_ASK_KINDS or e.get("paused_at") or _orphan_held(e)):
            out["needs"].append(e)
        elif _entry_open(e) and e.get("kind") == "steer":
            out["steering"].append(e)
        elif outcome and outcome.get("state") == "blocked":
            out["needs"].append(e)  # counted: work stopped on something only a person can move
        elif e.get("owes"):
            # the asker exited without reporting: only a person or its manager can find out what
            # happened, so that one is counted; the rest wait on a live session and are not
            (out["needs"] if e.get("asker_gone_quiet") else out["waiting"]).append(e)
        elif _answered_of(e):
            out["answered"].append(e)  # §4.9b: uncounted, and apart from FYI's notes
        elif e["id"] in settles:
            continue  # the reporting note: drawn under the question it closes, not beside it
        else:
            out["fyi"].append(e)
    for e in handed:
        out["needs" if _outcome_of(e).get("state") == "blocked" else "waiting"].append(e)
    out["fyi"].extend(_trail_rows(trail or (), at))
    # the teams a *team start* row names (§6 rule 8): a decided line pickable by one says so (TD-384)
    starting = {str(x.get("team") or "") for x in states if x.get("row") == "work"}
    for r in boards:
        if board_waits(r):
            # a copy: the board rows are the page's cache, read again by the next request
            out["waiting"].append({**r, "waiting_on": board_waiting_on(r, fleet, at, starting)})
        else:
            out["needs"].append({**r, "standing": board_standing(r, fleet, at)})
    out["needs"].sort(key=_needs_key)
    out["steering"].sort(key=lambda e: (not e.get("bound"), str(e.get("bound") or "")))
    out["waiting"].sort(key=lambda e: str(e.get("closed_at") or e.get("answered_at") or e.get("at") or ""))
    out["answered"].sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    out["fyi"].sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    out["snoozed"].sort(key=lambda e: str(e.get("snoozed_until") or ""))
    # the Org rollup's *m overdue* beside *in the Inbox* (§4.5 screen 1, §4.5a *Org: rollup*, TD-178):
    # the Needs you board items past their `Due:` date — a civil date, so read in this host's calendar
    today = at.astimezone().date().isoformat()
    overdue = sum(1 for e in out["needs"] if e.get("row") == "board" and e.get("due") and str(e["due"]) < today)
    return {**out, "count": len(out["needs"]), "fyi_n": len(out["fyi"]), "overdue_n": overdue}


# -- the rail (design §4.5 screen 6 *The rail* and *Find*, §4.5a **Inbox page: the rail**; TD-129,
# built by TD-135) ----------------------------------------------------------------------------------

# *Urgency*: the page's own sections in the page's order. The snoozed list is not one of them: it
# is in no section and no count (§4.10 *Snooze*).
RAIL_SECTIONS = ("needs", "steering", "waiting", "answered", "fyi")
RAIL_SECTION_NAMES = {
    "needs": "Needs you",
    "steering": "Steering",
    "waiting": "Waiting on them",
    "answered": "Answered for you",
    "fyi": "FYI",
}
# *Kinds*: a row's coarse kind, one per line (§4.5 screen 6 *The rail*)
RAIL_KINDS = ("questions", "steering", "states", "board", "notes", "trail")
RAIL_KIND_NAMES = {
    "questions": "questions",
    "steering": "steering",
    "states": "session states",
    "board": "board items",
    "notes": "notes",
    "trail": "trail",
}
# the board's rows coming up and the fold's (TD-220) are drawn under *Needs you* and picked with it,
# but are in none of its numbers: a section pick reads them as the section they sit in
RAIL_UNDER = {"coming": "needs", "unshown": "needs"}
RAIL_NO_TEAM = "none"  # a row that carries no team, in the URL and on the rail's *no team* line
_FIND_EDGE = ",.;:!?()[]{}\"'“”‘’<>"


def rail_kind(e: Mapping[str, Any]) -> str:
    """A row's coarse kind (§4.5 screen 6 *The rail*): *questions* (an `ask`, a `conflict`, a
    passed-up question), *steering* (a `steer`), *session states* (every state row, alarms
    included), *board items*, *notes* (a `note`, a `system` note, a reply, answered-for-you, an
    outcome), *trail*."""
    row = e.get("row")
    if row == "board":
        return "board"
    if row == "trail":
        return "trail"
    if row:
        return "states"
    if e.get("kind") in PERSON_ASK_KINDS or e.get("passed_up"):
        return "questions"
    if e.get("kind") == "steer":
        return "steering"
    return "notes"


def row_find(e: Mapping[str, Any], section: str = "") -> str:
    """The row's whole visible text, lowercased once, which the find matches every word against
    (§4.5 screen 6 *Find*): a mail row's sender, team, kind, `about`, PR and text; the rows the
    home builds carry their own (`find` on a state, alarm or board row); a trail row its name,
    kind, `how` and text. `data-find` on every row kind is this."""
    row = e.get("row")
    if row == "trail":
        return _find_text(e.get("name"), e.get("team"), e.get("kind"), "trail", e.get("how"), e.get("text"))
    if row:  # the home's own text for the row, and its team and pill, which the row draws too
        return _find_text(e.get("find"), e.get("team"), e.get("state_label"))
    answered = e.get("answered") if section == "answered" and isinstance(e.get("answered"), dict) else {}
    pr = e.get("pr")
    return _find_text(
        e.get("from_name") or e.get("from"),
        e.get("team"),
        e.get("kind"),
        e.get("about"),
        f"#{pr}" if isinstance(pr, int) and not isinstance(pr, bool) else "",
        e.get("text"),
        e.get("asker_name") if answered else "",
        answered.get("question") if answered else "",
        answered.get("source") if answered else "",
    )


templates.env.globals["rail_kind"] = rail_kind
templates.env.globals["row_find"] = row_find
templates.env.globals["rail_sections"] = RAIL_SECTIONS
templates.env.globals["rail_section_names"] = RAIL_SECTION_NAMES
templates.env.globals["rail_kinds"] = RAIL_KINDS
templates.env.globals["rail_kind_names"] = RAIL_KIND_NAMES


def find_words(find: str) -> list[str]:
    """The find's words (§4.5 screen 6 *Find*): lowercased, and a word's edge punctuation dropped,
    so *517,* finds what *517* does; a `#` is kept, and a bare number finds *#517* as a substring
    of it anyway."""
    return [w for w in (x.strip(_FIND_EDGE) for x in str(find or "").lower().split()) if w]


def find_matches(text: str, words: Collection[str]) -> bool:
    """Every word must match, in any order, as a substring of the row's text."""
    return all(w in text for w in words)


def rail_picks(query: Mapping[str, Any]) -> dict[str, Any]:
    """The picks a page's URL carries (§4.5 screen 6 *The rail*): `team`, `sec` and `kind` as comma
    lists — `none` is *no team* — and `find`. An unknown section or kind is dropped, never an error:
    a link survives a renamed line by showing a little more."""

    def lst(key: str) -> list[str]:
        return [x.strip() for x in str(query.get(key) or "").split(",") if x.strip()]

    return {
        "team": lst("team"),
        "sec": [x for x in lst("sec") if x in RAIL_SECTIONS],
        "kind": [x for x in lst("kind") if x in RAIL_KINDS],
        "find": str(query.get("find") or "").strip(),
    }


def rail_rows(sections: Mapping[str, Any], ahead: Collection[Mapping[str, Any]] = ()) -> list[dict[str, str]]:
    """Every row on the page that the rail counts, as the four things a pick reads: its section,
    team (`none` for no team), coarse kind and find text. The snoozed list is in no count. `ahead` is
    the board's rows coming up (TD-220), in section `coming`: counted by the rail's *board items* and
    in no section's number; the fold's rows (`unshown`) join only once it is opened, in the browser."""
    return [
        {
            "section": sec,
            "team": str(e.get("team") or "") or RAIL_NO_TEAM,
            "kind": rail_kind(e),
            "find": row_find(e, sec),
        }
        for sec, rows in [*((s, sections.get(s) or ()) for s in RAIL_SECTIONS), ("coming", ahead)]
        for e in rows
    ]


def rail_counts(rows: Collection[Mapping[str, str]], picks: Mapping[str, Any]) -> dict[str, Any]:
    """The rail's counts and the section headings' (§4.5 screen 6 *The rail*): **every count is a
    count of rows on the page now**. Within a group the picks are OR'd, across groups AND'd, and a
    group with nothing picked means all of it; the find is a fourth group. For a line: a picked line
    reads its share of the page; an unpicked line in a group with a pick reads 0; an unpicked line
    in a group without one reads its share under the other groups' picks. `all` is the line's count
    with no filter at all. A **team** line counts that team's *Needs you* rows — *which team needs
    me* (TD-135's steer to the techlead, 2026-09-25). `app.js`'s `AO.railCounts` is this function
    again, over the rows in the DOM, and a test holds the two to one answer."""
    words = find_words(picks.get("find") or "")
    pick = {g: set(picks.get(g) or ()) for g in ("team", "sec", "kind")}
    key = {"team": "team", "sec": "section", "kind": "kind"}

    def passes(r: Mapping[str, str], skip: str = "") -> bool:
        for g, want in pick.items():
            v = RAIL_UNDER.get(r[key[g]], r[key[g]]) if g == "sec" else r[key[g]]
            if g != skip and want and v not in want:
                return False
        return find_matches(r["find"], words)

    teams = sorted({r["team"] for r in rows if r["team"] != RAIL_NO_TEAM})
    if any(r["team"] == RAIL_NO_TEAM for r in rows):
        teams.append(RAIL_NO_TEAM)
    # a picked team no row carries still gets its line, so the pick can be undone
    teams += [t for t in picks.get("team") or () if t not in teams]

    def line(group: str, value: str, among: Collection[Mapping[str, str]]) -> dict[str, int]:
        mine = [r for r in among if r[key[group]] == value]
        shown = 0 if pick[group] and value not in pick[group] else sum(1 for r in mine if passes(r, group))
        return {"shown": shown, "all": len(mine)}

    needs = [r for r in rows if r["section"] == "needs"]
    return {
        "filtered": bool(pick["team"] or pick["sec"] or pick["kind"] or words),
        "sections": {s: line("sec", s, rows) for s in RAIL_SECTIONS},
        "teams": {t: line("team", t, needs) for t in teams},
        "team_order": teams,
        "kinds": {k: line("kind", k, rows) for k in RAIL_KINDS},
        "heads": {
            s: {
                "shown": sum(1 for r in rows if r["section"] == s and passes(r)),
                "all": sum(1 for r in rows if r["section"] == s),
            }
            for s in RAIL_SECTIONS
        },
    }


# the page computes its rail from its sections when a caller did not (a test rendering the template
# directly, as the rows are shaped by `shaped` and `suggested_answers` whoever renders them)
templates.env.globals["rail_picks"] = rail_picks
templates.env.globals["look_pair"] = look_pair
templates.env.globals["rail_rows"] = rail_rows
templates.env.globals["rail_counts"] = rail_counts


def ledger_for_you(
    repos: Mapping[str, Any], fleet: Collection[dict[str, Any]], named: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The Inbox's **For you in the ledger (n)** fold (design §4.5 screen 6 *The ledger's entries
    that wait on you*, §4.5a, TD-368): the entries §4.4 *Repo facts* sorts *for you* in the home's
    repo facts (`repos`, keyed by checkout, in the registry's order), one group per repo with any,
    each in the Repo page's order (priority, then id). A row is `{id, title, pri, why, held, team,
    url}`: *yours* for `Owner: paul`, *your decision* for a `decision (paul)` item; *held by* the
    names of the live records of that repo claiming it; the team the rail's *Teams* picks filter by
    — the definitions' team for the repo (`named`, by resolved checkout), else the team most of its
    live records carry, else none; the link the Repo page opened on the id. `n` is every row,
    counted in no number of the page's."""
    from .repo import PRIORITY_RANK  # repo imports this module: read at call time

    live = [s for s in fleet if s.get("state") not in ("exited", "closed") and s.get("repo")]
    groups: list[dict[str, Any]] = []
    for root, r in (repos or {}).items():
        if not isinstance(r, dict):
            continue
        entries = [
            e
            for e in ((r.get("ledger") or {}).get("entries") or [])
            if isinstance(e, dict) and e.get("for_page") == "for-you"
        ]
        if not entries:
            continue
        here = Path(str(r.get("root") or root)).resolve()
        mine = [s for s in live if Path(str(s["repo"])).resolve() == here]
        tally: dict[str, int] = {}
        for s in mine:
            if s.get("team"):
                tally[str(s["team"])] = tally.get(str(s["team"]), 0) + 1
        team = (named or {}).get(str(here)) or (max(sorted(tally), key=lambda t: tally[t]) if tally else "")
        name = str(r.get("name") or here.name)
        rows = []
        for e in sorted(entries, key=lambda e: (PRIORITY_RANK.get(e.get("priority") or "", 9), e["id"])):
            held = [
                str(s.get("name") or s.get("id"))
                for s in mine
                if any(
                    isinstance(p, dict) and p.get("ref") == e["id"] and p.get("status") == "claimed"
                    for p in s.get("progress") or []
                )
            ]
            owner = str(e.get("owner") or "").lower() == "paul"
            rows.append(
                {
                    "id": e["id"],
                    "title": e.get("title") or "",
                    "pri": (str(e.get("priority") or "")[:1] or "?").upper(),
                    "why": "yours" if owner else "your decision",
                    "held": ", ".join(held),
                    "team": team,
                    "url": f"/repo/{quote(name)}#{quote(e['id'])}",
                }
            )
        groups.append({"repo": name, "rows": rows})
    return {"n": sum(len(g["rows"]) for g in groups), "groups": groups}
