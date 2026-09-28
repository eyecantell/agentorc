"""The Inbox page (design §4.5 screen 6, §4.5a **Inbox page**, §4.10): its sections, the state rows,
the board rows and their reader's argv, the promote rows, what is owed, and the rail and Find.
Moved out of `agentorc.ui.app` unchanged (TD-196) and re-exported from it, so a route, a template or
a test reads each name from the app as before. `read_boards` and `repo_teams` stay in the app: the
suite patches both there.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sessionorc import hosts
from sessionorc.models import (
    normalize_ref,
)

from .cards import _first_line, alarm_note, alarm_to_view
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
    on (§4.9a) — else None. The words are fixed, from the record's fields; the `why` of an early
    one is the session's own sentence and is shown as such, never acted on."""
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
        return str(wanted.get("at") or ""), f"restart wanted · early — asked inside its first half hour: {why}"
    return None


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
            rows.append({**base(v, "restart", mark[1]), "at": mark[0] or v.get("since") or "", "age": ""})
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
# knows, with the script from the first of them that carries it (a SYNCED file: every copy is the
# same), at most once every `BOARD_TTL` seconds, since the page and the top bar poll every few.
BOARD_FILE = Path("docs") / "user_attention.md"
BOARD_SCRIPT = Path("scripts") / "nudge_user_attention.py"
BOARD_TTL = 60.0
BOARD_ACTS = ("snooze", "done")  # §4.5a's two answers to a board row, the `board_edit` RPC's actions
BOARD_TIMEOUT = 20.0


def board_argv(roots: Collection[str | Path]) -> tuple[list[str] | None, str]:
    """The command that reads the due items of these repos' boards, or None and a note saying why
    nothing is read. No board anywhere is not a fault and has no note; boards with no reader do,
    since the Inbox would otherwise look clear when it is not (§4.5 *no silent failure path*)."""
    rs = [Path(r).expanduser() for r in roots]
    boards = [str(r / BOARD_FILE) for r in rs if (r / BOARD_FILE).is_file()]
    if not boards:
        return None, ""
    script = next((r / BOARD_SCRIPT for r in rs if (r / BOARD_SCRIPT).is_file()), None)
    if script is None:
        return None, f"board items are not shown: no repo here carries {BOARD_SCRIPT}, dev-cadence's reader"
    argv = [sys.executable, str(script), "--report", "--due-only", "--json"]
    for b in boards:
        argv += ["--board", b]
    return argv, ""


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
    attention store, so Snooze is by time alone and more merges do not wake a snoozed row."""
    at = now or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for repo, r in sorted((promotes or {}).items()):
        if not isinstance(r, dict):
            continue
        failed = r.get("failed") if isinstance(r.get("failed"), dict) else None
        flight = r.get("inflight") if isinstance(r.get("inflight"), dict) else None
        behind = bool(r.get("main")) and r.get("live") != r.get("main")
        if not failed and (r.get("auto") or not (flight or behind)):
            continue
        live = str(r.get("live") or "")[:7]
        main = str(r.get("main") or "")[:7]
        ahead = r.get("ahead")
        checks = str(r.get("checks") or "unknown")
        text = f"{repo} · live {live or 'unknown'} · main {main or 'unknown'}"
        if isinstance(ahead, int) and ahead:
            text += f", {ahead} commit{'' if ahead == 1 else 's'} ahead"
        text += f" · checks {checks}"
        when = str((failed or {}).get("at") or (flight or {}).get("at") or r.get("moved") or "")
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
                "inflight": flight,
                "fyi": bool(flight) and not failed,
                "find": " ".join(x for x in (repo, "promote", text, (failed or {}).get("why") or "") if x),
            }
        )
    return out


def board_rows(report: Any, teams: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """design §4.5a **Due strip / Inbox board row** rows, as the Inbox draws them (TD-069 step 3): one
    per item the report says is due today or overdue — its repo, its due words, the whole text, and
    the board at that line in the editor. `at` is the due date, so *oldest first* in **Needs you**
    puts the longest overdue first among the states and the mail. The text is the board's, shown as
    text: nothing here is a control built from it (TD-071 item 8)."""
    rows: list[dict[str, Any]] = []
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
                    "due": str(it.get("due") or ""),
                    "due_tag": tag,
                    "at": str(it.get("due") or ""),
                    "editor": url,
                    "find": _find_text(label, text, tag, "board"),
                }
            )
    return rows


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


def inbox_sections(
    entries: Collection[dict[str, Any]],
    *,
    now: datetime | None = None,
    states: Collection[dict[str, Any]] = (),
    trail: Collection[dict[str, Any]] = (),
    attention_snoozed: dict[str, Any] | None = None,
    boards: Collection[dict[str, Any]] = (),
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
      no count (§4.10 *Snooze*); soonest first, so the page can say *n snoozed — show* and a
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
        if snoozed and snoozed > at:
            out["snoozed"].append(e)
        elif _entry_open(e) and (e.get("kind") in PERSON_ASK_KINDS or e.get("paused_at")):
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
    out["fyi"].extend(_trail_rows(trail or (), at))
    out["needs"].extend(boards)
    out["needs"].sort(key=_needs_key)
    out["steering"].sort(key=lambda e: (not e.get("bound"), str(e.get("bound") or "")))
    out["waiting"].sort(key=lambda e: str(e.get("closed_at") or e.get("at") or ""))
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


def rail_rows(sections: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every row on the page that the rail counts, as the four things a pick reads: its section,
    team (`none` for no team), coarse kind and find text. The snoozed list is in no count."""
    return [
        {
            "section": sec,
            "team": str(e.get("team") or "") or RAIL_NO_TEAM,
            "kind": rail_kind(e),
            "find": row_find(e, sec),
        }
        for sec in RAIL_SECTIONS
        for e in sections.get(sec) or ()
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
            if g != skip and want and r[key[g]] not in want:
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
templates.env.globals["rail_rows"] = rail_rows
templates.env.globals["rail_counts"] = rail_counts
