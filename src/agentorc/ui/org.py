"""The team-first Org (design §4.5 screen 1 *The Org, team-first*): a team's summary, its repo facet,
the TDs in motion, the answer blocks, the doing rows, the rollup and the compact line. Moved out of
`agentorc.ui.app` unchanged (TD-196) and re-exported from it, so a route, a template or a test reads
each name from the app as before.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentorc import teamrun
from sessionorc.models import stop_note
from sessionorc.reports import branch_ref

from . import uiconf
from .cards import DEAD
from .common import _age, _instant, _short_age
from .inbox import NEEDS_YOU_ROWS, PERSON_ASK_KINDS, _entry_open, state_kind

# -- the team-first Org (design §4.5 screen 1 *The Org, team-first*, TD-176 slice 3) ---------------

PHASES = ("add", "design", "grind", "review")
DOING_KEPT = 50  # the doing log's ring per team (§4.8), as the host agent keeps it
WINDOWS = ("day", "week", "month")
LEDGER_VIEWS = ("open", *WINDOWS)  # the Technical debt selector: the open entries, or a window
PRIORITY_BARS = (("high", "High"), ("medium", "Medium"), ("low", "Low"))
KIND_BARS = (("pickable", "pickable"), ("design-first", "design-first"), ("for-you", "for you"), ("other", "other"))


def _https(remote: str) -> str:
    """A git remote as the web page it names — `git@github.com:o/r.git` and
    `https://github.com/o/r.git` both `https://github.com/o/r` — or "" for anything else."""
    m = re.match(r"^(?:git@([^:]+):|https?://(?:[^@/]+@)?([^/]+)/)(.+?)(?:\.git)?/?$", remote.strip())
    return f"https://{m.group(1) or m.group(2)}/{m.group(3)}" if m else ""


def team_repo(members: Collection[dict[str, Any]], repos: Mapping[str, Any]) -> dict[str, Any] | None:
    """The repo a team services, as the home read it (§4.4 *Repo facts*): the registered checkout
    most of its members work in (`repo` on the record, a worktree's main checkout), or None when no
    member's repo is registered here — the facet then reads *no repo here*."""
    by_root = {str(Path(root).resolve()): r for root, r in (repos or {}).items() if isinstance(r, dict)}
    tally: dict[str, int] = {}
    for m in members:
        if m.get("repo"):
            key = str(Path(str(m["repo"])).resolve())
            if key in by_root:
                tally[key] = tally.get(key, 0) + 1
    if not tally:
        return None
    return by_root[max(sorted(tally), key=lambda k: tally[k])]


def _bars(counts: Mapping[str, Any], labels: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    """A stacked bar's segments, zeros left out: `{key, label, n, pct}` with the widths summing to 100."""
    total = sum(int(counts.get(k) or 0) for k, _ in labels)
    return [
        {"key": k, "label": label, "n": int(counts.get(k) or 0), "pct": round(100 * int(counts.get(k) or 0) / total, 1)}
        for k, label in labels
        if total and int(counts.get(k) or 0)
    ]


def _blocks(win: Mapping[str, Any] | None) -> dict[str, Any]:
    """Two sized blocks, *opened* and *closed* in a window: the counts and each one's share."""
    o, c = int((win or {}).get("opened") or 0), int((win or {}).get("closed") or 0)
    return {"opened": o, "closed": c, "opct": round(100 * o / (o + c), 1) if o + c else 50.0}


def lanes_line(team: str, lanes: Mapping[str, Any] | None, by_kind: Mapping[str, Any]) -> dict[str, Any] | None:
    """The **lanes line** under the kind bar's legend (§4.5a *team card: Repo facet*, §4.4 *In a team's
    lanes*, TD-361), from `ledger.in_lanes`' reading: what the team's lanes take, the rest of the
    pickable by owner, each member out of work with unheld work in its lane, and the two segments'
    hovers. None for a team none of whose records carries a lane: the bars stand alone."""
    if not lanes or not team:
        return None
    rest = list(lanes.get("rest") or [])
    owners = ", ".join(f"{o['owner']} {o['n']}" for o in rest)
    n_pick, n_design = len(lanes.get("pickable") or []), len(lanes.get("design_first") or [])
    waits = len(lanes.get("design_first_rest") or [])
    pick = f"{int(by_kind.get('pickable') or 0)} pickable · {n_pick} in {team}'s lanes" + (
        f" · {owners}" if owners else ""
    )
    design = (
        f"{int(by_kind.get('design-first') or 0)} design-first · {n_design} in {team}'s lanes · {waits} wait on a build"
    )
    return {
        "team": team,
        "pickable": n_pick,
        "design_first": n_design,
        "rest": rest,
        "other": sum(int(o["n"]) for o in rest),
        "out_of_work": [
            {**m, "title": f"{', '.join(m['ids'])} — in its lane, held by nobody"}
            for m in lanes.get("out_of_work") or []
        ],
        "titles": {"pickable": pick, "design-first": design},
        # a count links to its list only when the Repo page draws that list (it draws no empty one)
        "listed": {k: int(by_kind.get(k) or 0) > 0 for k in ("pickable", "design-first")},
    }


def repo_facet(
    r: Mapping[str, Any],
    now: datetime,
    waiting: dict[str, Any] | None = None,
    lanes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The team card's **Repo** facet (§4.5a *team card: Repo facet*): the repo's name and page, the
    ledger's open entries as two bars and its windows as blocks, the PRs' windows as blocks, the
    oldest open PR's age, how many wait on review, and the readings' age. A reading that failed is
    `error`, drawn *could not look*; its last numbers, when there are any, stay beside it. `lanes`,
    `lanes_line`'s, is the team's line under the legend (TD-361)."""
    led, prs = r.get("ledger") or {}, r.get("prs") or {}
    entries = led.get("entries")
    open_ = prs.get("open")
    oldest = _age(open_[0].get("created"), now) if open_ else ""
    return {
        "name": str(r.get("name") or ""),
        "url": f"/repo/{r.get('name') or ''}",
        "web": _https(str(r.get("remote") or "")),
        "ledger": {
            "n": len(entries) if isinstance(entries, list) else None,
            "error": led.get("error") or "",
            "history_error": led.get("history_error") or "",
            "priority": _bars(led.get("by_priority") or {}, PRIORITY_BARS),
            # a decided board line counts *pickable* (TD-384), never in the priorities above
            "kind": _bars(teamrun.lane_kinds(r), KIND_BARS),
            "lanes": lanes,
            "windows": {w: _blocks((led.get("windows") or {}).get(w)) for w in WINDOWS} if led.get("windows") else None,
        },
        "prs": {
            "n": len(open_) if isinstance(open_, list) else None,
            "error": prs.get("error") or "",
            "windows": {w: _blocks((prs.get("windows") or {}).get(w)) for w in WINDOWS} if prs.get("windows") else None,
            "oldest": oldest,
            "waiting": waiting,
            "truncated": bool(prs.get("truncated")),
        },
        "read": _age(r.get("at"), now),
    }


def _pr_states(r: Mapping[str, Any] | None) -> dict[int, dict[str, Any]]:
    """Every PR the reading knows, by number: its state and page."""
    prs = (r or {}).get("prs") or {}
    out: dict[int, dict[str, Any]] = {}
    for p in [*(prs.get("recent") or []), *(prs.get("open") or [])]:
        if isinstance(p, dict) and isinstance(p.get("number"), int):
            out[p["number"]] = p
    return out


MOTION_PRIORITIES = ("high", "medium", "low")  # the letters a row in motion draws, in sort order


def _holders_width(members: Collection[Mapping[str, Any]]) -> int:
    """The characters a *TDs in motion* row's holders take: the names, *, * between them, and two for
    the person glyph on a person's own session."""
    return sum(len(str(m["name"])) + (2 if m.get("mine") else 0) for m in members) + 2 * (len(members) - 1)


def motion_rows(members: Collection[dict[str, Any]], r: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """**TDs in motion** (§4.5a *team card: TDs in motion*): one row per reference a member holds as
    a `claimed` progress entry, with its **phase** derived here, never declared — *design* on an
    entry whose `Kind:` is design-first (its kind, not the page's bucket: one blocked by a decision
    sits under *for you*, TD-197, TD-228), *review* with a PR (its own `pr`, the tick's
    `review_pr`, or else an open PR whose head branch names the reference — the tick reads only the
    branch checked out, and a grinder that asked its reader has moved on), *grind* without one; a PR
    that is no longer open keeps *review*, marked *merged* / *closed*, until the member marks the
    claim done or dropped. A reference two members hold is one row naming both. Each row carries
    the entry's `priority` — *high*, *medium* or *low*, else '' (a foreign or archived reference, an
    entry with none or another word), drawn as a letter (TD-232). Rows in phase order, then priority
    with High first and an unmarked row last, then by reference."""
    # a claimed work order (`board:<key>`, TD-384) is drawn as any reference, its title the line's head
    led_entries = ((r or {}).get("ledger") or {}).get("entries") or []
    entries = {e["id"]: e for e in [*led_entries, *teamrun.work_orders(r)] if isinstance(e, dict)}
    prs, web = _pr_states(r), _https(str((r or {}).get("remote") or ""))
    # open PRs only: a merged slice's branch must not mark the next slice of the same entry *review*
    by_branch: dict[str, int] = {}
    for p in ((r or {}).get("prs") or {}).get("open") or []:
        if isinstance(p, dict) and isinstance(p.get("number"), int) and (ref := branch_ref(p.get("branch"))):
            by_branch.setdefault(ref, p["number"])  # oldest first, as the reading keeps them
    rows: dict[str, dict[str, Any]] = {}
    for m in members:
        for p in m.get("progress") or []:
            if not isinstance(p, dict) or p.get("status") != "claimed" or not p.get("ref"):
                continue
            ref = str(p["ref"])
            row = rows.setdefault(ref, {"ref": ref, "members": [], "pr": None})
            row["members"].append({"id": m["id"], "name": m.get("name") or m["id"], "mine": not m.get("unattended")})
            pr = p.get("pr") or p.get("review_pr")
            if isinstance(pr, int) and not row["pr"]:
                row["pr"] = pr
    out = []
    for ref, row in rows.items():
        e = entries.get(ref) or {}
        pr = row["pr"] = row["pr"] or by_branch.get(ref)
        known = prs.get(pr) if pr else None
        state = str((known or {}).get("state") or "")
        row["phase"] = "design" if e.get("kind") == "design-first" else "review" if pr else "grind"
        row["title"] = str(e.get("title") or "")
        prio = str(e.get("priority") or "").lower()
        row["priority"] = prio if prio in MOTION_PRIORITIES else ""
        row["pr_state"] = state if state in ("merged", "closed") else ""
        row["pr_url"] = str((known or {}).get("url") or (f"{web}/pull/{pr}" if pr and web else ""))
        out.append(row)
    rank = {p: i for i, p in enumerate(MOTION_PRIORITIES)}
    out.sort(key=lambda x: (PHASES.index(x["phase"]), rank.get(x["priority"], len(rank)), x["ref"]))
    return out


def ask_blocks(
    members: Collection[dict[str, Any]], needs: Collection[dict[str, Any]], now: datetime | None = None
) -> list[dict[str, Any]]:
    """**A member's `ask` to you** (§4.5a *team card: Answer needed / Doing*, TD-350, TD-354): every
    open ask the Inbox counts under *Needs you* — `needs`, its rows as `inbox_sections` gave them —
    whose sender is one of `members`, oldest first: the data of the team's *asked you* line. The two
    are in the reader's form (§4.4a *Every address crosses in the reader's form*): a node's record is
    `id@host` on both sides, and a bare id is this host's, so they are matched whole. Never a
    `steer`, a board row or a state row. Its text is the ask's first line, drawn as text."""
    now = now or datetime.now(UTC)
    names = {str(m["id"]): str(m.get("name") or m["id"]) for m in members}
    out = []
    for e in needs or ():
        if not isinstance(e, Mapping) or e.get("row") or e.get("kind") not in PERSON_ASK_KINDS or not _entry_open(e):
            continue
        sid = str(e.get("from") or "")
        if sid not in names:
            continue
        text = str(e.get("text") or "").strip()
        out.append(
            {
                "id": str(e.get("id") or ""),
                "member": sid,
                "name": names[sid],
                "kind": "ask",
                "text": text.splitlines()[0] if text else "",
                "at": str(e.get("at") or ""),
                "age": _short_age(e.get("at"), now),
            }
        )
    return sorted(out, key=lambda a: a["at"])


def asked_line(
    members: Collection[dict[str, Any]], needs: Collection[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any] | None:
    """**The *asked you* line** (§4.5a *team card: Answer needed / Doing*, TD-353, built by TD-354):
    the team's open asks to the person summed into one line — how many, the oldest's id (the line
    links its Inbox row) and age, and each ask's member and first line for the tooltip. None while
    no ask is open. Never a block and never counted in Answer needed: a prompt stops a session now,
    and a question in the mail waits."""
    asks = ask_blocks(members, needs, now)
    if not asks:
        return None
    first = asks[0]
    return {
        "n": len(asks),
        "id": first["id"],
        "at": first["at"],
        "age": first["age"],
        "who": [{"name": a["name"], "text": a["text"]} for a in asks],
    }


def answer_blocks(members: Collection[dict[str, Any]]) -> list[dict[str, Any]]:
    """**Answer needed** (§4.5a *team card: Answer needed / Doing*): each member waiting on a
    permission or a question in its pane, and those alone (TD-353) — the permission with Allow /
    Deny when the hook gave something to answer (`tool_use_id`), a question as its text with Focus.
    A member's open `ask` to the person is the facet's *asked you* line (`asked_line`), never here."""
    out = []
    for m in members:
        kind = state_kind(m)
        if kind not in NEEDS_YOU_ROWS:
            continue
        pend = m.get("pending") if isinstance(m.get("pending"), dict) else {}
        out.append(
            {
                "id": m["id"],
                "name": m.get("name") or m["id"],
                "kind": kind,
                "text": str(pend.get("text") or ""),
                "deadline": m.get("deadline") or "",
            }
        )
    return out


REF_WIDTH = 10  # *TDs in motion*'s reference column at most, in characters (§4.5a **Columns**, TD-252)
HOLDERS_WIDTH = 14  # …and its holders column: one ordinary name; a longer list is cut and whole on hover
DOER_WIDTH = 18  # the Doing list's doer column at most, in characters; a longer name is cut (§4.5a)


def doing_rows(
    team: str, doing: Mapping[str, Any] | None, names: Mapping[str, str], now: datetime | None = None
) -> list[dict[str, Any]]:
    """**Doing** (§4.8 *the doing log*): the team's `ao doing` calls newest first — age, doer, words.
    `age` is the short age (`_short_age`, TD-232), which the page keeps moving from `at`."""
    now = now or datetime.now(UTC)
    rows = []
    for e in reversed(list((doing or {}).get(team) or [])):
        if not isinstance(e, dict):
            continue
        at = _instant(e.get("at"))
        rows.append(
            {
                "at": str(e.get("at") or "") if at else "",
                "age": _short_age(e.get("at"), now),
                "id": str(e.get("id") or ""),
                "name": names.get(str(e.get("id") or ""), str(e.get("id") or "")),
                "text": str(e.get("text") or ""),
            }
        )
    return rows


def team_lanes(team: str, fleet: list[dict[str, Any]], r: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The team's lanes line for its repo's reading (§4.4 *In a team's lanes*), from the one reader
    `teamrun.repo_lanes` runs over the fleet — the team's records for its lanes, every live record of
    the repo for what is held; None with no reading or no lane."""
    got = teamrun.repo_lanes(teamrun.lane_entries(r), str((r or {}).get("root") or ""), fleet).get(team)
    return lanes_line(team, got, teamrun.lane_kinds(r))


# a summary's three facets, in their order: Repo, TDs in motion, Answer needed / Doing
FACETS = ("repo", "motion", "face")


def drawn_facets(summary: Mapping[str, Any]) -> list[str]:
    """The facets a team with nothing live draws (§4.5a *team card: summary*, TD-418): only those that
    hold something — a repo here, a claim in motion, a Doing row (or an answer or an ask) — so a stopped
    team reads no *no repo here* and no *nothing claimed*; none at all draws no summary."""
    holds = {
        "repo": bool(summary.get("repo")),
        "motion": bool(summary.get("motion")),
        "face": bool(summary.get("doing") or summary.get("answers") or summary.get("asked")),
    }
    return [k for k in FACETS if holds[k]]


def team_summary(
    team: str,
    members: list[dict[str, Any]],
    repos: Mapping[str, Any] | None,
    doing: Mapping[str, Any] | None,
    waiting: dict[str, Any] | None = None,
    now: datetime | None = None,
    needs: Collection[dict[str, Any]] = (),
    fleet: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A team's **summary** (§4.5a *team card: summary*): the Repo facet, TDs in motion, and Answer
    needed / Doing — the facet opening on *answer* while any member waits on one. A team with nothing
    live has one too (TD-192): its claims as the members' last records hold them, and Doing, since
    nobody can be waiting."""
    now = now or datetime.now(UTC)
    r = team_repo(members, repos or {})
    motion = motion_rows(members, r)
    answers = answer_blocks(members)
    return {
        "team": team,
        "repo": repo_facet(r, now, waiting, team_lanes(team, fleet if fleet is not None else members, r))
        if r
        else None,
        "motion": motion,
        "phases": {ph: sum(1 for x in motion if x["phase"] == ph) for ph in PHASES},
        # the reference and holders columns' widths, from the rows, so every row's grid is the same (§4.5a **Columns**)
        "ref_w": min(max((len(x["ref"]) for x in motion), default=1), REF_WIDTH),
        "who_w": min(max((_holders_width(x["members"]) for x in motion), default=1), HOLDERS_WIDTH),
        # …and the PR column's: *#811*, or *#712 merged*; 0 where no row has a PR, so the title takes the room
        "pr_w": max((len(f"#{x['pr']} {x['pr_state']}".rstrip()) for x in motion if x["pr"]), default=0),
        "answers": answers,
        # the team's open asks to the person, one line under the facet's head in either face (TD-354)
        "asked": asked_line(members, needs, now),
        "doing": (drows := doing_rows(team, doing, {m["id"]: str(m.get("name") or m["id"]) for m in members}, now)),
        # the doer column's width, set here from the names in the list so a filter moves no column (§4.5a)
        "doer_w": min(max((len(d["name"]) for d in drows), default=1), DOER_WIDTH),
        "face": "answer" if answers else "doing",
        # the facets drawn: all three on a live team and on the Repo page; `drawn_facets` narrows it
        # for a team with nothing live (TD-418)
        "drawn": list(FACETS),
        # what the toggle's memory keys on: a person's flip holds until the pending set changes (§4.5a)
        "answer_key": " ".join(sorted(f"{a['id']}:{a['kind']}" for a in answers)),
    }


# the rollup's Agents pills, in the grid's urgency order: (the filter word, the pill's class, its label)
ROLLUP_STATES = (
    ("needs-you", "needs", "needs you"),
    ("limited", "limited", "limited"),
    ("stalled", "stalled", "stalled?"),
    ("unreachable", "unreachable", "unreachable"),
    ("working", "working", "working"),
    ("waiting", "waiting", "waiting"),
    ("unseen", "idle", "unseen"),
    ("idle", "idle", "idle"),
    ("on-call", "oncall", "on call"),
    ("exited", "exited", "exited"),
    ("closed", "closed", "closed"),
)


def rollup(groups: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The Org **rollup** (§4.5a *Org: rollup*, TD-176 slice 4): sums over every live team — the
    Agents pills by state, TDs in motion by phase (each phase's link the Repo page of the team
    holding the most of it), PRs in motion per window over the teams' repos (a repo two teams share
    counted once), and Needs you's *answer needed* and *asked you*. When no team is live there is
    nothing to sum — a wound-down team's summary (TD-192) is not summed — and the rollup is the Needs
    you facet alone, `live` false, its *in the Inbox* (TD-406); with one team live, `lone`: Agents and
    Needs you, that team's own facets saying the rest (TD-418). The Inbox's count is the top bar's,
    filled in by the client."""
    live = [g for g in groups or [] if g.get("team") and g.get("summary") and g.get("live")]
    if not live:
        return {"live": False}
    members = [m for g in live for m in g["members"]]
    tally: dict[str, int] = {}
    for m in members:
        tally[str(m.get("pill_word") or m.get("state"))] = tally.get(str(m.get("pill_word") or m.get("state")), 0) + 1
    agents = [{"word": w, "cls": c, "label": label, "n": tally[w]} for w, c, label in ROLLUP_STATES if tally.get(w)]
    phases: dict[str, int] = {ph: 0 for ph in PHASES}
    lead: dict[str, str] = {}
    for ph in PHASES:
        best = max(live, key=lambda g: g["summary"]["phases"].get(ph, 0))
        phases[ph] = sum(g["summary"]["phases"].get(ph, 0) for g in live)
        if phases[ph] and best["summary"].get("repo"):
            lead[ph] = f"{best['summary']['repo']['url']}?phase={ph}"
    motion = sum(phases.values())
    repos: dict[str, dict[str, Any]] = {}
    for g in live:
        r = g["summary"].get("repo")
        if r and r["name"] not in repos:
            repos[r["name"]] = r
    wins = {w: {"opened": 0, "closed": 0} for w in WINDOWS}
    n_open, errors = 0, []
    for r in repos.values():
        p = r["prs"]
        if p["error"]:
            errors.append(f"{r['name']}: {p['error']}")
        n_open += p["n"] or 0
        for w in WINDOWS:
            for k in ("opened", "closed"):
                wins[w][k] += int(((p["windows"] or {}).get(w) or {}).get(k) or 0)
    busiest = max(repos.values(), key=lambda r: r["prs"]["n"] or 0, default=None)
    answers = [(g["team"], a) for g in live for a in g["summary"]["answers"]]
    # *asked you* (TD-354): the teams' lines summed, the oldest's age, the first such team's anchor
    asked = [(g["team"], g["summary"]["asked"]) for g in live if g["summary"].get("asked")]
    oldest = min((a for _, a in asked), key=lambda a: a["at"], default=None)
    return {
        "live": True,
        "lone": len(live) == 1,
        "agents": agents,
        "n_agents": len(members),
        "phases": [
            {"key": ph, "n": phases[ph], "pct": round(100 * phases[ph] / motion, 1), "url": lead.get(ph, "")}
            for ph in PHASES
            if phases[ph]
        ],
        "motion": motion,
        "prs": {w: _blocks(wins[w]) for w in WINDOWS},
        "prs_open": n_open,
        "prs_url": f"{busiest['url']}#prs" if busiest else "",
        "prs_errors": errors,
        "has_repo": bool(repos),
        "waiting": sum(int((g.get("prs_waiting") or {}).get("n") or 0) for g in live),
        "answer_needed": len(answers),
        "answer_team": answers[0][0] if answers else "",
        "asked": sum(a["n"] for _, a in asked),
        "asked_at": oldest["at"] if oldest else "",
        "asked_age": oldest["age"] if oldest else "",
        "asked_team": asked[0][0] if asked else "",
    }


def compact_line(v: dict[str, Any]) -> str:
    """A compact card's one line of its own (§4.5a *card: compact*): the seat's *last came*, an
    ending, a flow's mark (*sits out under build*), the role and its claim with the PR, what it says
    it is doing, or the role alone."""
    role = str(v.get("role_label") or v.get("role") or ("interactive" if not v.get("unattended") else ""))
    slot = v.get("slot") or {}
    if v.get("seat"):
        what = slot.get("caption") or slot.get("text") or "on call"
    elif v.get("state") in DEAD or v.get("out_of_work") or v.get("restart_wanted"):
        # an ending, and the last reference after it with its PR's mark (TD-193): *Grinder · exited ·
        # TD-066 → #158 merged*, so a wound-down team's cards say what each member left
        what = " · ".join(x for x in (str(slot.get("text") or v.get("state") or ""), v.get("report_ref") or "") if x)
    elif isinstance(v.get("flow_mark"), dict) and str(v["flow_mark"]["text"]).startswith("sits out"):
        # what a switch did or left on it (§4.5a, TD-309): a node's longer marks leave its claim the line
        what = str(v["flow_mark"]["text"])
    else:
        claims = [p for p in v.get("progress") or [] if isinstance(p, dict) and p.get("status") == "claimed"]
        doing = (v.get("doing") or {}).get("text") if isinstance(v.get("doing"), dict) else ""
        if claims:
            pr = claims[0].get("pr") or claims[0].get("review_pr")
            mark = (v.get("pr_marks") or {}).get(str(pr), "") if pr else ""
            what = f"{claims[0]['ref']} → #{pr}{' ' + mark if mark else ''}" if pr else str(claims[0]["ref"])
        elif doing:
            what = str(doing)
        else:
            what = ""
    return " · ".join(x for x in (role, what, *_own_stop(v)) if x)


def _own_stop(v: Mapping[str, Any]) -> tuple[str, str]:
    """A live member's stop time on its compact line (§4.5a *card: compact*, *team card: stops note*,
    TD-337): *stops <t>* only where its `run_until` reads differently from its team's — the team's is on the
    header, once — and *wrapping up* once the host agent has asked it, which is each record's own."""
    if v.get("state") in DEAD:
        return "", ""
    # compared as drawn: a stamp a second off the team's reads the same, and the header already says it
    own = stop_note({"run_until": v.get("run_until")})
    team = stop_note({"run_until": uiconf.team_until(str(v.get("team") or ""))})
    return (own if own != team else "", "wrapping up" if v.get("wrapup_sent_at") else "")
