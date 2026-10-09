"""The Repo page (design §4.5 screen 11): the PR rows and their standing, the ledger's lists, the doing
chips, who serves the repo for what, and the grouping of a team's cards. Moved out of
`agentorc.ui.app` unchanged (TD-196) and re-exported from it, so a route, a template or a test reads
each name from the app as before.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from agentorc import org as orgmod
from agentorc import repoconfig, review, teamrun, teams
from sessionorc import mail
from sessionorc.models import (
    has_control,
    stop_note,
)

from . import uiconf
from .cards import DEAD, NO_TEAM, card_order, group_place, prs_waiting, state_counts
from .common import _age, host_name
from .inbox import work_note, work_started
from .org import compact_line, drawn_facets, team_summary

# -- Add entry (design §4.9 *Add an entry to the ledger*, §4.5a **Add entry…**, TD-219 slice 3) -------

ENTRY_PREFIX = "entry"  # the session and its worktree: `entry-<n>`, the first number free in the repo
ENTRY_TRIES = 200  # how far the first free number is looked for before the press is refused


def entry_role(org: orgmod.Org, team: str, type_: str) -> str:
    """The role **Open a session** starts for `type_`: the team's `entries:` word for it, else, for a
    feature, its current flow's design stage (§4.9c item 4), else the techlead; `plain` where no team
    services the repo (§4.9 *Where there is no seat*)."""
    t = org.teams.get(team) if team else None
    if t is None:
        return "plain"
    here = host_name()
    # §4.9c item 4: a flow's design stage. Read from this host's disk: a team on a node whose checkout
    # is not here reads no flow and opens the techlead, unless its `entries:` says otherwise
    return teams.entry_role(org, t, type_, t.host or here, here)


def entry_line(role: str, name: str, team: str) -> str:
    """The one line under **Open a session** (§4.5a): what the press will start."""
    tail = "" if team else " — no team services this repo"
    return f"an interactive {role} session, {name}, in a new worktree{tail}"


# the teams that service a checkout: `agentorc.teams`' since TD-218 slice 4, where `ao td add` reads it too
entry_teams = teams.entry_teams


def entry_hand(servicing: list[dict[str, str]], records: Mapping[str, dict[str, Any]], now: datetime) -> dict[str, str]:
    """**Hand to the techlead**'s state on the Add entry form (§4.5a): `{team, to, name, why, line}`.
    `why` is the reason the button is disabled — *no team services this repo*, *this team has no
    techlead seat*, *<seat>: the techlead seat has no checkout on its host* — else empty; `line` is
    §4.10's *When it is read* for an `ask` with no bound, the
    sentence the Message composer draws: the seat's record's own (`refill`, the agent's sentence for
    what fills a seat as an `ask` does), or a seat nobody fills where there is no record."""
    if not servicing:
        return {"team": "", "to": "", "name": "", "why": "no team services this repo", "line": ""}
    first = servicing[0]
    if not first.get("seat"):
        lead = first.get("techlead") or ""
        why = f"{lead}: the techlead seat has no checkout on its host" if lead else "this team has no techlead seat"
        return {"team": first["team"], "to": "", "name": "", "why": why, "line": ""}
    rw = (records.get(first["seat"]) or {}).get("read_when")
    line = rw.get("refill") if isinstance(rw, dict) and rw.get("refill") else ""
    line = line or mail.read_when(None, "ask", now, seat=True, lapses=False)
    return {"team": first["team"], "to": first["seat"], "name": first["name"], "why": "", "line": line}


# -- the Repo page (design §4.5 screen 11, TD-176 slice 5) -------------------------------------------

LEDGER_LISTS = (("pickable", "pickable"), ("design-first", "design-first"), ("for-you", "for you"), ("other", "other"))
LEDGER_FOLD = 4  # a list folds past this many rows, with *+n more*
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def pr_rows(
    r: Mapping[str, Any], members: Collection[dict[str, Any]], standing: Mapping[int, dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """**Open PRs** (§4.5 screen 11): every open PR, newest last, with its author — the member whose
    branch it is, else the GitHub login — its age, *draft*, and its standing with the techlead
    (`standing`, by number; nothing when no entry names it)."""
    by_branch = {str((m.get("git") or {}).get("branch") or ""): m for m in members if isinstance(m.get("git"), dict)}
    rows = []
    for p in (r.get("prs") or {}).get("open") or []:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        who = by_branch.get(str(p.get("branch") or ""))
        rows.append(
            {
                "number": p["number"],
                "title": str(p.get("title") or ""),
                "url": str(p.get("url") or ""),
                "author": {"id": who["id"], "name": who.get("name") or who["id"]} if who else None,
                "login": str(p.get("author") or ""),
                "age": _age(p.get("created"), now),
                "draft": bool(p.get("draft")),
                "standing": standing.get(p["number"]),
            }
        )
    return rows


def pr_standing(seats: list[tuple[str, list[Any], list[Any]]], now: datetime) -> dict[int, dict[str, Any]]:
    """Each PR's standing with the team's readers (§4.5 screen 11, §4.9c *What is shown*): `seats`
    one `(name, inbox, sent)` per seat of the team, read as a person's read — `review.standing`,
    the words `ao repo` prints too, aged against `now`."""
    return review.standing(seats, lambda at: _age(at, now))


def ledger_lists(
    r: Mapping[str, Any], motion: Collection[dict[str, Any]], lanes: Mapping[str, Mapping[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """**Technical debt** (§4.5 screen 11): the open entries in four lists by the page's kind, each
    row id, title, priority and owner, *held by <name>* when a member claims it and *blocked by …*
    from the entry's `blocked_by` (§4.4 *Repo facts*), and a live check *live check* or *waits for
    its build to be live* with its build's PRs (§4.9b, TD-323; the template's), sorted by priority
    then id; `fold` the rows past the fold. The board's open decided lines, the lanes' work orders
    (§4.5a *Repo page: decided lines*, TD-384), come first in *pickable*, each with its `decided`
    and, held by nobody, `pickable_by`: the teams whose lanes (`teamrun.repo_lanes`') take it, None
    where the ledger was not read and the lanes could not be (`lanes` None or the entries unread)."""
    held = {x["ref"]: ", ".join(w["name"] for w in x["members"]) for x in motion}
    entries = [e for e in ((r.get("ledger") or {}).get("entries") or []) if isinstance(e, dict)]
    known = lanes is not None and teamrun.lane_entries(r) is not None
    orders = [
        {
            **e,
            "held": held.get(e["id"], ""),
            "pickable_by": [t for t, got in (lanes or {}).items() if e["id"] in (got.get("pickable") or ())]
            if known
            else None,
        }
        for e in teamrun.work_orders(r)
    ]
    out = []
    for key, label in LEDGER_LISTS:
        rows = sorted(
            ({**e, "held": held.get(e["id"], "")} for e in entries if e.get("for_page") == key),
            key=lambda e: (PRIORITY_RANK.get(e.get("priority") or "", 9), e["id"]),
        )
        if key == "pickable":
            rows = orders + rows  # the person has answered: nothing in a ledger outranks it
        out.append({"key": key, "label": label, "rows": rows, "fold": max(0, len(rows) - LEDGER_FOLD)})
    return out


def doing_chips(rows: Collection[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Doing section's filter chips (§4.5a *Repo page: doing filters*): *all (n)* and one per
    doer in the feed, the busiest first."""
    tally: dict[str, int] = {}
    for d in rows:
        tally[d["name"]] = tally.get(d["name"], 0) + 1
    return [{"who": "", "label": "all", "n": len(rows)}] + [
        {"who": w, "label": w, "n": n} for w, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def compact_in(v: dict[str, Any], fleet: Collection[dict[str, Any]]) -> dict[str, Any]:
    """Mark `v` compact when it is a member of a team (§4.5a *card: compact*): a `team` badge, live
    or not — a team with nothing live draws its summary and compact cards too, once unfolded
    (TD-192). The full card stays on *No team*. `fleet` is kept for the callers' shape."""
    if v.get("team"):
        v["compact"], v["compact_line"] = True, compact_line(v)
    return v


def who_for_what(roles: Collection[Mapping[str, Any]], views: Collection[Mapping[str, Any]]) -> list[str]:
    """The team's **who for what** lines, drawn in its help panel (design §4.5a ***i*** mark, §4.8 *A role says when to
    message it*; TD-162, built by TD-171): one phrase per role the definition names, in its order,
    from the role's `message:` line — the definition's words, never a session's. A role one session
    holds reads *<line> → <name>*, and an empty seat *(on call)* after the name; a role several
    hold reads *<Label>: <line>*, since no one name answers for them. A role with no line is left
    out, and a team whose roles carry none has no line at all (an empty list). The line comes from
    a view of a session holding the role, which resolved it with the repo's and the org's layers;
    where no session holds it yet, from the built-in preset."""
    by_name = {str(v.get("name") or ""): v for v in views}
    out: list[str] = []
    for r in roles:
        role, names = str(r.get("role") or ""), [str(n) for n in r.get("names") or ()]
        held = [by_name[n] for n in names if n in by_name]
        line = next((str(v.get("message_line") or "") for v in held if v.get("message_line")), "")
        line = line or str((repoconfig.PRESETS.get(role) or {}).get("message") or "")
        if not line:
            continue
        if len(names) == 1:
            v = by_name.get(names[0])
            empty = bool(r.get("seat")) and (v is None or v.get("seat") or v.get("state") in ("exited", "closed"))
            out.append(f"{line} → {names[0]}{' (on call)' if empty else ''}")
        else:
            label = next(
                (str(v.get("role_label")) for v in held if v.get("role_label")), repoconfig.default_label(role)
            )
            out.append(f"{label}: {line}")
    return out


def flow_head(fv: Mapping[str, Any] | None, *, live: bool) -> dict[str, Any]:
    """A team header's flow (design §4.5a *team groups*, *team card **flow changed — Apply***; §4.9c):
    `flow`, the current one or "" for a team that lists none; `flow_strip`, its stages and the
    person; `flow_lines`, what is drawn under the header: a pick that is not one it lists, each listed
    flow it cannot follow — the current one first, with the flows it could — and a repo not read
    from here; and `flow_changed`, a live team's members whose records differ from what the flow compiles
    to, each `{name, act, line}`, with `definition_changed` when the mark reads **definition changed —
    Apply** (§4.5a, TD-399): the team runs no flow, or every difference is a seat it lacks."""
    if not fv or not fv.get("flow"):
        # a team that runs no flow is read on its seats alone (§4.9c, TD-399): one it lacks is the mark
        diffs = list((fv or {}).get("differences") or []) if live else []
        return {
            "flow": "",
            "flow_strip": "",
            "flow_lines": [],
            "flow_changed": diffs,
            "definition_changed": bool(diffs),
            "flow_picks": [],
        }
    rows = list(fv.get("flows") or [])
    now = next((r for r in rows if r.get("current")), {})
    lines = [str(fv["flow_note"])] if fv.get("flow_note") else []
    could = [r["name"] for r in rows if not r.get("current") and not r.get("cannot") and not r.get("unread")]
    if now.get("cannot"):  # the flow it runs first, with the flows it could follow
        lines.append(str(now["cannot"]) + (f" (it could follow {', '.join(could)})" if could else ""))
    lines += [str(r["cannot"]) for r in rows if r.get("cannot") and not r.get("current")]
    if unread := next((r["unread"] for r in rows if r.get("unread")), ""):
        lines.append(f"flows not read from here: {unread}")
    return {
        "flow": str(fv["flow"]),
        "flow_strip": str(now.get("strip") or ""),
        "flow_lines": lines,
        "flow_changed": list(fv.get("differences") or []) if live else [],
        "definition_changed": teamrun.definition_changed(list(fv.get("differences") or []) if live else [], fv["flow"]),
        # the **Flow** pick (§4.5a), on a team that lists more than one: each `{name, current, cannot}`
        "flow_picks": [{k: r.get(k) for k in ("name", "current", "cannot")} for r in rows] if len(rows) > 1 else [],
    }


def team_groups(
    views: list[dict[str, Any]],
    rows: Collection[dict[str, Any]] = (),
    repos: Mapping[str, Any] | None = None,
    doing: Mapping[str, Any] | None = None,
    work: Mapping[str, Any] | None = None,
    flows: Mapping[str, Any] | None = None,
    balance: Mapping[str, Any] | None = None,
    needs: Collection[dict[str, Any]] = (),
) -> list[dict[str, Any]] | None:
    """Design §4.5a Org **team groups** (§4.9, §9 invariant 9): the grid grouped by the `team` badge,
    derived from the views on every render and every delta, never stored. `rows` is the definitions
    (`teamrun.rows`). `None` when no session carries a badge and nothing is defined — the page then
    renders the flat grid, with no header anywhere. A team with nothing live keeps its group
    (2026-09-18): dead cards under a team's name are still that team's, and a definition no session
    carries is a group with no members, because its card is where Start lives.

    The badge decides the group; `controllers` decides the manager: the one member holding
    `control` that other members of the same group list as a controller. A group without one
    has no manager card and its header says so. Within a group the manager comes first, then the rest in
    urgent-first order (the same `rank`, `name` key the flat grid sorts by); the client re-sorts
    per group in Pinned mode. Down the page: the teams with something live, the sessions with no
    badge as *No team*, then the teams with nothing live.

    A manager carrying a different badge from its members — which `ao team start` never produces, but a
    hand-typed `ao new --team` can — is still found, by looking across the whole fleet rather than
    only inside the group (review of PR #117). Its card stays where its own badge puts it; the
    header names it and says so, because moving the card would contradict the badge.

    A team carries its **summary** (TD-176 slice 3, §4.5a *team card: summary*) from `repos` (the
    home's repo facts) and `doing` (its doing log), live or not — a team with nothing live shows what
    it left once unfolded (TD-192) — and its members are marked compact; its header then drops the
    state counts, which the member cards say, but for the fold.

    `work` is the home's `work_waiting` marks by team (`host`'s `work`, §6 rule 8): a wound-down
    team's header says *n entries waiting since <t>* from its mark, and a live one *started <t> for
    …* from its records' `restarts` (§4.5a team card **work waiting** note).

    `flows` is each team's `teamrun.flow_view`, read when the page was drawn and not per delta
    (§4.9c *What is shown*): the header's flow and strip, the lines under it while the definition
    cannot follow its flow, and, on a live team, *flow changed — Apply* (`flow_head`).

    `balance` is the home's balance marks by team (`host`'s `balance`, §6 *Balance*, TD-330): every
    mark, one with no `repo` too, which no checkout's reading in `repos` carries."""
    defs = {str(r["name"]): r for r in rows}
    by_team: dict[str, list[dict[str, Any]]] = {name: [] for name in defs}
    for v in views:
        by_team.setdefault(str(v.get("team") or NO_TEAM), []).append(v)
    if not any(t != NO_TEAM for t in by_team):
        return None
    groups: list[dict[str, Any]] = []
    marks = teamrun.balance_marks(
        {str(k): v for k, v in (repos or {}).items() if isinstance(v, dict)}, {"balance": balance or {}}
    )
    for team in sorted(by_team):
        members = sorted(by_team[team], key=card_order)
        manager, manager_elsewhere = None, False
        if team != NO_TEAM:
            named = {c for m in members for c in (m.get("controllers") or [])}
            # The fleet, not just this group: a manager whose own badge differs is still this group's
            # manager, and saying "managed by you" over a group that plainly has one would be a lie.
            managers = sorted(
                (v for v in views if has_control(v.get("capabilities")) and v["id"] in named),
                key=lambda v: (str(v.get("team") or "") != team, v["rank"], v["name"]),  # our own badge first
            )
            if managers:
                manager = managers[0]
                if manager in members:
                    members.remove(manager)
                    members.insert(0, manager)
                else:
                    manager_elsewhere = True
        projects = sorted({str(m.get("project")) for m in members if m.get("project")})
        row = defs.get(team) or {}
        # live, concluded, wound down and Forget all read the team's unattended sessions: a person's
        # session in it keeps nothing live (design §4.9 *A person in the team*, TD-173)
        crew = [m for m in members if not teamrun.persons(m)] if team != NO_TEAM else members
        live = sum(1 for m in crew if m.get("state") not in DEAD)
        people = [m for m in members if teamrun.persons(m) and m.get("state") not in DEAD] if team != NO_TEAM else []
        c = row.get("concluded")
        # the definition's rows read the raw records; a view that disagrees about what is live (a
        # delta between the two reads) is not drawn concluded — Wind down is the safe offer then
        concluded = c if live and isinstance(c, dict) and len(c.get("names") or ()) == live else None
        # never a person's card, live or closed: Forget all is a team act (§4.9 *A person in the team*)
        dead = [m for m in crew if not m.get("seat") and m.get("state") in DEAD] if team != NO_TEAM and not live else []
        ready = sum(1 for m in members if (m.get("slot") or {}).get("ccls") == "ready" and m.get("state") == "idle")
        waiting = prs_waiting(members) if team != NO_TEAM else None
        # `needs`: the Inbox's *Needs you* rows, whose open asks from a member are its *asked you* line (TD-354)
        summary = (
            team_summary(team, members, repos, doing, waiting, needs=needs, fleet=views) if team != NO_TEAM else None
        )
        if summary:
            for m in members:
                m["compact"], m["compact_line"] = True, compact_line(m)
            if not live:  # a team with nothing live draws only the facets that hold something (TD-418)
                summary["drawn"] = drawn_facets(summary)
        groups.append(
            {
                "team": team,
                "label": team or "No team",
                # the header names its manager only when that card is in another group (TD-095: its
                # name, state and line are on its own card, the first here); `role_label` is what it
                # is called there (§4.8 *The names*, TD-076): *Manager*, or its role's own label.
                "manager": {k: manager.get(k) for k in ("id", "name", "state", "role_label")} if manager else None,
                "manager_elsewhere": manager_elsewhere,  # its card sits under its own badge, not here
                "members": members,
                "ids": [m["id"] for m in members],
                "projects": projects or list(row.get("projects") or []),
                "needs": sum(1 for m in members if m.get("state") == "needs-you"),
                "prs_waiting": waiting,
                "summary": summary,
                "live": live,
                # the header's own facts (design §4.5 *The card's anatomy*, TD-095): where the
                # team's sessions are, once, and how many are in each state — never its manager's
                # name, state or line, which are on the manager's card, the first in the group
                "place": group_place(members),
                # …and, on any team, how many wait for a person's Close (TD-156 (b): a concluded
                # team's idle cards were folded away and read as already closed)
                "counts": state_counts(members) + ([f"{ready} ready to close"] if ready else []),
                # a definition exists, so the group's card carries Start, or Stop / Stop now (§4.5a)
                "defined": team in defs,
                "source": row.get("source"),
                "in_org": bool(row.get("in_org")),  # Members… edits org.yml's teams only (TD-172)
                # the repo whose `.agentorc.yml` defines it, for the disabled Members…'s reason (TD-229)
                "source_repo": Path(row["source"]).parent.name if row.get("source") and not row.get("in_org") else "",
                "def_manager": row.get("manager"),  # the definition's word, for a card with no sessions yet
                "def_members": row.get("members"),
                "def_techlead": row.get("techlead"),  # the seat's name (§4.9b), when the definition has one
                # *nothing running* and *nothing left to run* are different facts (§4.9a)
                "wound_down": row.get("wound_down"),
                "wound_down_age": row.get("wound_down_age"),
                "by_tick": bool(row.get("by_tick")),  # *· by the tick*: rule 9 wound it down (§6, TD-241)
                # who, how soon and why (§4.5a *wound down* note, TD-265): `teamrun.wound_down_note`'s reading
                "wound_after": row.get("after") or "",
                "closed_by": row.get("closed_by") or "",
                "manager_why": row.get("manager_why") or "",
                # §4.5a team card **work waiting** note (§6 rule 8, TD-227): display only
                "work_note": work_note((work or {}).get(team)) if not live and row.get("wound_down") else None,
                "work_started": work_started(crew) if live and team != NO_TEAM else None,
                # §4.5a team card **over its line** note (§6 *Balance*, TD-239): the home's mark, display only
                "balance_note": teamrun.balance_note(marks[team]) if live and team in marks else "",
                # §4.5a team card **stops** note (§6 *Team stop time*, TD-337): the team's own setting,
                # on a live team alone — a team with nothing live has the *starts* note's slot
                "stops_note": stop_note({"run_until": until}) if live and (until := uiconf.team_until(team)) else "",
                # live, and every live session idle and declared (§4.5a, TD-099): drawn like a
                # stopped team — sorted with them, Start alone, though it opens unfolded (TD-194) —
                # since a wind-down would only wake the manager to find nothing to wind down
                "concluded": concluded,
                # a live, defined team that is not: why there is no Start, a clause per session
                # that keeps it from the reading (§4.5a *team groups*, TD-241)
                "not_concluded": list(row.get("not_concluded") or []) if live and concluded is None else [],
                "concluded_age": row.get("concluded_age") if concluded else "",
                "stopped": not live or concluded is not None,
                # a person's live sessions in the team, named apart in Wind down's and Stop now's
                # confirm: a team act never stops an interactive session (§4.9, §9 invariant 5)
                "stays": [teamrun.stays_line(m) for m in people],
                # design §4.5a team card **Forget all** (TD-071 item 1): on a team with nothing live,
                # the Forget each card carries, on every card but those with the dirty / unpushed
                # flag — Forget drops the record that points at the worktree, and unpushed work would
                # lose its only pointer, so those are named apart and forgotten one at a time. A seat
                # with nobody in it is neither: its card never offers Forget while the definition
                # names it (`next_act`), and Forget all does not go round that
                "forget": [m for m in dead if not m.get("flag")],
                "forget_kept": [m for m in dead if m.get("flag")],
                # design §4.5a team header **✉ n** (TD-071 item 2): what the fold hides of the cards'
                # unread chips — display only, the mail stays where it is (§4.10)
                "unread": sum(int(m.get("unread") or 0) for m in members),
                # design §4.5a ***i*** mark **who for what** (§4.8, TD-171): whom to write to, by role
                "who": who_for_what((defs.get(team) or {}).get("roles") or (), views) if team != NO_TEAM else [],
                # design §4.5a *team groups*: the flow it runs and its strip, and *flow changed — Apply*
                **flow_head((flows or {}).get(team), live=bool(live) and team in defs),
            }
        )
    # what is running is read first; *No team* is never "stopped" — nothing there starts as one
    # …and among the live teams, one with a session that needs a person comes first (2026-09-18)
    groups.sort(
        key=lambda g: (2 if g["team"] and g["stopped"] else 1 if not g["team"] else 0, not g["needs"], g["team"])
    )
    return groups
