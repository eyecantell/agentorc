"""The agentorc web UI (design §4.5): server-rendered pages, one `/events` websocket per tab
pushing rendered cards, one `/term/<id>` websocket per open Focus terminal. Phase 1: the local
host only, from `hosts.yml`'s `local` entry; ssh transport arrives in phase 2.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agentorc import org as orgmod
from agentorc import profiles as profiles_mod
from agentorc import repoconfig, teamrun, teams
from agentorc import review as reviewmod
from agentorc.cli import stop_time as clistop
from sessionorc import hosts, naming, paths
from sessionorc.client import AgentError, AgentUnavailable, LocalClient
from sessionorc.containers import attach_argv_in
from sessionorc.models import (
    GRANTS,
    has_control,
    start_note,
    stop_note,
)

from . import help as helpmod
from . import settings_page as setmod
from . import uiconf
from .cards import (  # re-exported: routes, templates and tests read these from the app (TD-196)
    BRANCH_SHOWN,  # noqa: F401
    COUNT_ORDER,  # noqa: F401
    DEAD,  # noqa: F401
    DEFS_TTL,  # noqa: F401
    NO_TEAM,  # noqa: F401
    _clock,  # noqa: F401
    _count,  # noqa: F401
    _first_line,  # noqa: F401
    _middle,  # noqa: F401
    alarm_note,  # noqa: F401
    alarm_to_view,  # noqa: F401
    alarm_view,  # noqa: F401
    alarm_words,  # noqa: F401
    card_order,  # noqa: F401
    card_slot,  # noqa: F401
    gated_view,  # noqa: F401
    group_place,  # noqa: F401
    next_act,  # noqa: F401
    prs_waiting,  # noqa: F401
    ready_to_close,  # noqa: F401
    state_counts,  # noqa: F401
    suspended_note,  # noqa: F401
    view,  # noqa: F401
)
from .common import (  # re-exported: routes, templates and tests read these from the app (TD-196)
    GRANT_NOTES,  # noqa: F401
    HERE,  # noqa: F401
    ICON_TTL,  # noqa: F401
    METERED_KINDS,  # noqa: F401
    NEAR_CAP,  # noqa: F401
    NO_CONTROLLERS,  # noqa: F401
    NO_GRANTS,  # noqa: F401
    UNDESCRIBED_GRANT,  # noqa: F401
    USAGE_WHY,  # noqa: F401
    WRAPUP_PROMPT,  # noqa: F401
    _age,  # noqa: F401
    _aged,  # noqa: F401
    _as_session_id,  # noqa: F401
    _countdown,  # noqa: F401
    _icon_cache,  # noqa: F401
    _instant,  # noqa: F401
    _iso,  # noqa: F401
    _left,  # noqa: F401
    _look_for,  # noqa: F401
    _metered_chip,  # noqa: F401
    _money,  # noqa: F401
    _reserve_why,  # noqa: F401
    _str_list,  # noqa: F401
    _usage_hover,  # noqa: F401
    _usage_line,  # noqa: F401
    _usage_profiles,  # noqa: F401
    editor_link,  # noqa: F401
    host_name,  # noqa: F401
    identity_note,  # noqa: F401
    log,  # noqa: F401
    node_banner,  # noqa: F401
    node_org_note,  # noqa: F401
    org_here,  # noqa: F401
    page_origin,  # noqa: F401
    projects_view,  # noqa: F401
    restart_note,  # noqa: F401
    role_icons,  # noqa: F401
    role_prompts,  # noqa: F401
    rpc,  # noqa: F401
    shaped,  # noqa: F401
    start_fields,  # noqa: F401
    static_url,  # noqa: F401
    stop_fields,  # noqa: F401
    suggested_answers,  # noqa: F401
    team_reader,  # noqa: F401
    teams_for_form,  # noqa: F401
    teams_view,  # noqa: F401
    templates,  # noqa: F401
    usage_accounts,  # noqa: F401
    usage_chip,  # noqa: F401
    vscode_url,  # noqa: F401
    with_lines,  # noqa: F401
)
from .inbox import (  # re-exported: routes, templates and tests read these from the app (TD-196)
    _FIND_EDGE,  # noqa: F401
    BOARD_ACTS,  # noqa: F401
    BOARD_FILE,  # noqa: F401
    BOARD_SCRIPT,  # noqa: F401
    BOARD_TIMEOUT,  # noqa: F401
    BOARD_TTL,  # noqa: F401
    INBOX_SECTIONS,  # noqa: F401
    NEEDS_YOU_ROWS,  # noqa: F401
    OWING_CLOSES,  # noqa: F401
    OWING_KINDS,  # noqa: F401
    PERSON_ASK_KINDS,  # noqa: F401
    RAIL_KIND_NAMES,  # noqa: F401
    RAIL_KINDS,  # noqa: F401
    RAIL_NO_TEAM,  # noqa: F401
    RAIL_SECTION_NAMES,  # noqa: F401
    RAIL_SECTIONS,  # noqa: F401
    _answered_of,  # noqa: F401
    _entry_open,  # noqa: F401
    _find_text,  # noqa: F401
    _needs_key,  # noqa: F401
    _outcome_of,  # noqa: F401
    _owing,  # noqa: F401
    _same_ref,  # noqa: F401
    _trail_rows,  # noqa: F401
    board_argv,  # noqa: F401
    board_choices,  # noqa: F401
    board_rows,  # noqa: F401
    find_matches,  # noqa: F401
    find_words,  # noqa: F401
    inbox_sections,  # noqa: F401
    promote_rows,  # noqa: F401
    rail_counts,  # noqa: F401
    rail_kind,  # noqa: F401
    rail_picks,  # noqa: F401
    rail_rows,  # noqa: F401
    restart_mark,  # noqa: F401
    review_pr,  # noqa: F401
    row_find,  # noqa: F401
    state_kind,  # noqa: F401
    state_rows,  # noqa: F401
)
from .org import (  # re-exported: routes, templates and tests read these from the app (TD-196)
    DOING_KEPT,  # noqa: F401
    KIND_BARS,  # noqa: F401
    LEDGER_VIEWS,  # noqa: F401
    PHASES,  # noqa: F401
    PRIORITY_BARS,  # noqa: F401
    ROLLUP_STATES,  # noqa: F401
    WINDOWS,  # noqa: F401
    _bars,  # noqa: F401
    _blocks,  # noqa: F401
    _https,  # noqa: F401
    _pr_states,  # noqa: F401
    answer_blocks,  # noqa: F401
    compact_line,  # noqa: F401
    doing_rows,  # noqa: F401
    motion_rows,  # noqa: F401
    repo_facet,  # noqa: F401
    rollup,  # noqa: F401
    team_repo,  # noqa: F401
    team_summary,  # noqa: F401
)
from .pty_bridge import PtySession, attach_argv, pump, scroll_argv
from .repo import (  # re-exported: routes, templates and tests read these from the app (TD-196)
    LEDGER_FOLD,  # noqa: F401
    LEDGER_LISTS,  # noqa: F401
    PRIORITY_RANK,  # noqa: F401
    compact_in,  # noqa: F401
    doing_chips,  # noqa: F401
    ledger_lists,  # noqa: F401
    pr_rows,  # noqa: F401
    pr_standing,  # noqa: F401
    team_groups,  # noqa: F401
    who_for_what,  # noqa: F401
)
from .transcript import TRANSCRIPT_TURNS, transcript_editor, transcript_view

# -- the Inbox's board reading, kept here: the suite patches both names on the app (TD-196) --------


def repo_teams(org: orgmod.Org, host: str) -> dict[str, str]:
    """Each checkout on `host` → the team whose projects hold it (§4.5 screen 6: *a board item
    carries its repo, which `org.yml`'s projects map to teams*). A repo two teams share is the
    first's, in definition order: a row carries one team badge, as a message does."""
    out: dict[str, str] = {}
    for tname, t in org.teams.items():
        for pname in t.projects:
            p = org.projects.get(pname)
            for by in p.repos.values() if p else ():
                path = by.get(host)
                if path:
                    out.setdefault(str(Path(path).expanduser().resolve()), tname)
    return out


def read_boards(run: Any = subprocess.run) -> tuple[list[dict[str, Any]], str]:
    """The due board items of the repos this host knows, as Inbox rows, and a note when they could
    not be read — a reader that failed is said in words, never shown as an empty board (§4.5 *no
    silent failure path*). On a node the org is the home's (§4.4a), so a node reads none."""
    if hosts.is_node():
        return [], ""
    argv, note = board_argv(hosts.local_host().repos())
    if argv is None:
        return [], note
    try:
        done = run(argv, capture_output=True, text=True, timeout=BOARD_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], f"board items are not shown: the reader did not finish ({e})"
    if done.returncode != 0:
        why = (done.stderr or "").strip().splitlines()
        return [], f"board items are not shown: the reader exited {done.returncode}" + (f" — {why[-1]}" if why else "")
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return [], "board items are not shown: the reader's output is not JSON"
    org, _ = org_here()
    return board_rows(report, repo_teams(org, host_name())), ""


# -- app -------------------------------------------------------------------------------------------


# design §4.5a **Focus (exited / closed)** → **Resume** (TD-081 step 2; the plumbing is step 1,
# PR #282). What a one-press resume carries, and what it deliberately does not.
RESUME_CARRIES = ("name", "dir", "adapter", "profile", "repo", "role", "team", "project", "lane", "controllers")
# The first prompt **Reopen and push** writes (§4.5a *Inbox row: state*, TD-081). Fixed text, in
# the source: it is the page's sentence, never anything a session said (§4.2), and what comes of it
# returns as an outcome (§4.10 *Outcomes*).
REOPEN_AND_PUSH = (
    "Push your branch and open or update its PR, then report the outcome with "
    '`ao msg person --outcome done|blocked|dropped "<one line>" --for <the question this answers>` '
    "— or, if nothing is owed, say so on docs/user_attention.md."
)


def resume_create(rec: dict[str, Any], *, prompt: str = "") -> dict[str, Any]:
    """The `create` a one-press **Resume** makes from an exited record (design §4.5a).

    It carries the record's own `name` — which is the whole point: the name check answers
    `supersede`, the new session takes the bare name *and* the record's id, the old record is
    replaced in place and its mail stays with it (§4.10 *a resume under the same name*, built by
    TD-081 step 1). And it carries `dir`, `adapter`, `profile`, `role`, `team`, `project`, `lane`
    and `controllers`, so a live team finds its member where it was.

    **And `repo`, when the record has one** (TD-145). A name identifies one session per *scope*
    (§4.1), and the scope of a session in a worktree is its repo, not the worktree directory: the
    record's id is `ao-<repo>-<name>`. A resume that sent `dir` alone put the worktree's basename
    in the scope — and a worktree named after its session (every team member's is) made
    `ao-<name>-<name>`, collapsed to `ao-<name>`, an id nobody held — so the name check answered
    *free*, a second record appeared beside the one being resumed, and the team showed two of the
    seat (2026-09-24: `ao-techlead-ao-1` beside `ao-agentorc-techlead-ao-1`, and the seat's
    trigger dead with it, since a superseded record is never filled).

    **Never `unattended`.** The session a press starts is *attended*, whatever the record was: an
    unattended session answers its own permission prompts, and a press with no form is no place to
    grant that — least of all to a member whose manager has gone and whose `run_until` has passed.
    Also not carried: `run_until` and `wrapup_prompt` (a deadline that has passed is not one),
    the old `prompt`, and `capabilities` — the grants come from the record's **role**, through the
    same preset path the form takes, so nothing is copied and nothing is dropped."""
    lane = rec.get("lane")
    # *a `create` **on the record's host***: a record of a node's is addressed `id@host` and its
    # `host` is its own, so a resume without it would create the session on the **home** instead —
    # under the name that should have superseded the node's record (review of PR #290). Sent only
    # when it lands elsewhere, which is `Member.create_params`'s convention and §4.4's rule that a
    # client never sends a parameter it has not set.
    host = str(rec.get("host") or "")
    got: dict[str, Any] = {
        "name": str(rec.get("name") or ""),
        "dir": str(rec.get("dir") or ""),
        "adapter": str(rec.get("adapter") or "claude-code"),
        "profile": str(rec.get("profile") or ""),
        "role": str(rec.get("role") or ""),
        "team": str(rec.get("team") or ""),
        "project": str(rec.get("project") or ""),
        "lane": [str(x) for x in lane] if isinstance(lane, list) else [],
        "controllers": [str(c) for c in (rec.get("controllers") or [])],
        "resume": str(rec.get("adapter_id") or ""),
        "unattended": False,
        # the scope the name is checked in (§4.1): sent only when the record has one (§4.4 skew rule)
        **({"repo": str(rec["repo"])} if rec.get("repo") else {}),
        **({"host": host} if host and host != host_name() else {}),
    }
    if prompt:
        got["prompt"] = prompt
    # The **grants** and the **ledger** come from the directory's repo through the record's role,
    # exactly as they do when the form's Role picker is used — not copied off the old record, so a
    # grant a preset has since dropped is dropped here too, and one it has gained is gained. A
    # directory or a role that no longer resolves is not decided here: the create refuses it and
    # the press lands on the form with the agent's own words (`resume_blocked`'s docstring).
    with contextlib.suppress(KeyError, ValueError, OSError):
        cfg = repoconfig.discover(got["dir"] or os.getcwd())
        got["ledger"] = cfg.ledger
        if got["role"]:
            preset = repoconfig.resolve_role(cfg, got["role"])
            got["capabilities"] = [g for g in preset.grants if g in GRANTS]
            got["lane"] = got["lane"] or list(preset.lane)
            got["profile"] = got["profile"] or preset.profile or ""
    return got


def resume_blocked(rec: dict[str, Any]) -> str:
    """Why a one-press **Resume** cannot be silent on this record, or "" — *when it cannot be
    silent it is not a guess* (§4.5a): the press lands on the filled-in form with the reason on it.

    Only what is knowable from the record is decided here. Everything else — a directory that is
    gone, a profile or role that no longer exists, a host that is not connected, a name a live
    record has taken since — is the agent's own refusal, which the caller turns into the same
    fall-through in the agent's own words, so no rule of ours can drift from the one that runs."""
    if str(rec.get("state") or "") not in ("exited", "closed"):
        return "this session is still running — there is nothing to resume"
    if not str(rec.get("adapter_id") or ""):
        return "this record holds no tool session id, so there is no conversation to resume"
    if not str(rec.get("name") or "") or not str(rec.get("dir") or ""):
        return "this record has no name or no directory to take back"
    return ""


def resume_form_url(rec: dict[str, Any], why: str = "") -> str:
    """**Resume with changes…**: New session with every field of `resume_create` filled in, which
    is also where a press that cannot be silent lands, with its reason (§4.5a)."""
    got = resume_create(rec)
    # `capabilities` and `ledger` are the create's, not the form's — §4.5a says capabilities are
    # **not carried**, and the form derives both from the Role picker. `host` likewise: the form
    # has no host field, and phase 1 starts a session on the host the page is served from.
    q = {k: v for k, v in got.items() if k in RESUME_CARRIES and k not in ("lane", "controllers") and v}
    q["resume"] = got["resume"]
    # A worktree record lands on the form as the form says it: **Where** = new worktree, the
    # worktree's name, and the directory field holding the *repo* — which is what the form's own
    # Start sends (`repo=dir` with `worktree`), so the create checks the name in the record's scope
    # and takes its id back, exactly as the one-press Resume does (TD-145). Keyed on the record's
    # own `worktree` — the field the create sets when it made or reused `<repo>/.claude/worktrees/
    # <name>` — never on `dir != repo`: `ao new --repo` scopes a directory that is a worktree of the
    # repo's without going through that flow, and such a record resumes where it is (review of #543).
    repo = q.pop("repo", "")
    if repo and rec.get("worktree"):
        q["worktree"] = str(rec["worktree"])
        q["where"] = "worktree"
        q["dir"] = repo
    q["lane"] = ", ".join(got["lane"])
    q["controllers"] = ",".join(got["controllers"])
    # *Unattended* as the record had it — on the form, where it is next to its stop time
    q["unattended"] = "on" if rec.get("unattended") else ""
    # **The form is filled in from a record, not opened blank** — which is what lets an *empty*
    # controllers list mean *this record had none* rather than *nothing was said*. Without it the
    # repo's default would be re-ticked and a person who did not notice would Start a session with
    # controllers the record never had (review of PR #290).
    q["prefilled"] = "1"
    if why:
        q["why"] = why
    return "/new?" + urlencode({k: v for k, v in q.items() if v})


def _int_param(raw: str | None, default: int, lo: int, hi: int) -> int:
    try:
        v = int(float(raw)) if raw not in (None, "") else default
    except (TypeError, ValueError, OverflowError):  # "abc", "NaN", "inf" / "1e999"
        return default
    return max(lo, min(hi, v))


class _WsAttemptLog:
    """Log every websocket upgrade the instant it arrives, before any handler runs. uvicorn logs a
    websocket only once the app accepts or rejects it, so "no line at all" could not distinguish
    "never reached the server" from "hung before accept" (first-use finding 2026-09-06)."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "websocket":
            client = scope.get("client") or ("?", "?")
            log.info("websocket attempt from %s:%s for %s", client[0], client[1], scope.get("path"))
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    app = FastAPI(title="agentorc")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.add_middleware(_WsAttemptLog)

    async def call(method: str, **params: Any) -> Any:
        try:
            async with LocalClient() as c:
                return await c.call(method, **params)
        except AgentUnavailable as e:
            raise HTTPException(503, f"host agent unreachable: {e}") from e
        except AgentError as e:
            raise HTTPException(400, str(e)) from e

    def render_card(v: dict[str, Any]) -> str:
        return templates.get_template("card.html").render(s=v)

    settings_at = {"at": 0.0}

    @app.middleware("http")
    async def person_settings(request: Request, call_next: Any) -> Any:
        """The person's own (`person:` in the home's `settings.yml`, design §5, TD-146) read through
        the agent's `settings` read at most once in `DEFS_TTL` seconds, before a page is drawn — the
        editor button every card and Focus header draws comes from it. A failed read keeps the last."""
        now = time.monotonic()
        if now - settings_at["at"] > DEFS_TTL and not request.url.path.startswith("/static"):
            settings_at["at"] = now
            try:
                uiconf.set_read(await call("settings"))
            except Exception:  # noqa: BLE001 — a read that failed keeps the last answer; it never costs the page
                log.debug("the settings read failed; the last person: stands", exc_info=True)
        return await call_next(request)

    defs_cache: dict[str, Any] = {"at": 0.0, "org": None}

    async def defs() -> orgmod.Org:
        """The team definitions, read at most once in `DEFS_TTL` seconds: the events stream
        re-renders every header on every delta — a definition edited by hand shows within that."""
        now = time.monotonic()
        if defs_cache["org"] is None or now - defs_cache["at"] > DEFS_TTL:
            # off the loop: every open page shares it, and the read is a file per registered repo
            defs_cache.update(at=now, org=(await asyncio.to_thread(org_here))[0])
        return defs_cache["org"]

    async def team_rows(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The definitions' rows for a delta's headers."""
        return _aged(teamrun.rows(await defs(), sessions))

    async def seats_of(fleet: list[dict[str, Any]]) -> dict[str, str]:
        """The ids in `fleet` a team definition names as a seat — what `view()` draws *on call*
        while nobody is in one (TD-097). Every page that draws a pill asks, so they agree."""
        return teamrun.seat_ids(await defs(), fleet)

    async def repo_facts() -> tuple[dict[str, Any], dict[str, Any]]:
        """The home's repo facts and doing log (§4.4 *Repo facts*, TD-176), each `{}` when the agent
        cannot give it — an older agent, a node whose link is down: the facets then read *no repo
        here* and an empty feed rather than taking the page down."""
        repos: dict[str, Any] = {}
        doing: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            repos = await call("repos")
        with contextlib.suppress(Exception):
            doing = await call("doing_log")
        return repos, doing

    async def group_heads(
        known: dict[str, dict[str, Any]],
        repos: Mapping[str, Any] | None = None,
        doing: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """The team groups as the events stream ships them (design §4.5a **team groups**): per group
        its key, the member ids in order, and the header rendered by the same template the page
        uses — so the client moves cards between groups and swaps headers without composing any
        markup of its own. `None` means "flat grid", exactly as the page renders it."""
        return (await heads(known, repos, doing))["groups"]

    async def heads(
        known: dict[str, dict[str, Any]],
        repos: Mapping[str, Any] | None = None,
        doing: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """What every delta carries for the page's layout: the groups (`group_heads`) and the
        rollup's markup (§4.5a *Org: rollup*, TD-176 slice 4), both from one grouping of the fleet."""
        fleet = list(known.values())
        seats = await seats_of(fleet)
        groups = team_groups(
            [view(s, fleet, seats=seats, repos=repos) for s in fleet], await team_rows(fleet), repos, doing
        )
        ro = templates.get_template("rollup.html").render(ro=rollup(groups))
        return {"groups": render_heads(groups), "rollup": ro}

    def render_heads(groups: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if groups is None:
            return None
        head = templates.get_template("group_head.html")
        summary = templates.get_template("team_summary.html")
        return [
            {
                "team": g["team"],
                "manager": (g["manager"] or {}).get("id", ""),
                "live": g["live"],  # what the fold's default keys on: nothing live opens folded (TD-194)
                "ids": g["ids"],
                "html": head.render(g=g),
                # the summary's facets (TD-176 slice 3), swapped by the client as the header is
                "summary": summary.render(g=g) if g.get("summary") else "",
            }
            for g in groups
        ]

    identity_cache: dict[str, Any] = {"at": 0.0, "info": None}

    async def identity_info() -> dict[str, Any]:
        """This host's identity mode (design §4.8a), for the Org's one-line note. The `identity`
        RPC is a never-gated read, and it is read at most once every `DEFS_TTL` seconds — the same
        idiom as the team definitions, so a page under a delta storm never asks per render. An
        agent that is down or too old to answer leaves the note off rather than the page."""
        now = time.monotonic()
        if identity_cache["info"] is None or now - identity_cache["at"] > DEFS_TTL:
            try:
                identity_cache.update(at=now, info=await call("identity"))
            except HTTPException:
                identity_cache.update(at=now, info={})
        return identity_cache["info"] or {}

    board_cache: dict[str, Any] = {"at": None, "rows": [], "note": ""}

    async def board_items(fresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        """The Inbox's board rows and the note beside them (TD-069 step 3), read at most once every
        `BOARD_TTL` seconds and off the event loop: the reader is a subprocess over every board on
        the host, and the page and the top bar both poll every few seconds. `fresh` reads now — after
        a Snooze or Done, so the row the person answered is gone from the next refresh."""
        now = time.monotonic()
        if fresh or board_cache["at"] is None or now - board_cache["at"] > BOARD_TTL:
            rows, note = await asyncio.to_thread(read_boards)
            board_cache.update(at=now, rows=rows, note=note)
        return board_cache["rows"], board_cache["note"]

    async def person_states(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The session-state rows of the Inbox (design §4.5 screen 6, TD-069 step 2), from the
        fleet the request already read. Every host the home knows, exactly as the Org shows them —
        the same records and the same `view()` — plus this host's own identity alarms (§4.8a).

        Rendered server-side on the page's load *and* on its poll: a state that changed between
        polls is corrected by the next one, and nothing here has to ride the pushed stream to be
        no more than a few seconds behind the Org."""
        icons = await role_icons(fleet)
        seats = await seats_of(fleet)
        views = [view(s, fleet, icons=icons, seats=seats) for s in fleet]
        info = await identity_info()
        rows = state_rows(
            views,
            host_alarms=alarm_view(info.get("alarms")),
            host=str(info.get("host") or host_name()),
            identity_mode=str(info.get("mode") or ""),
        )
        # §4.5a *Inbox row: promote* (TD-132 slice 3): the home's readings; a node has none
        return rows + promote_rows((await call("host")).get("promotes"))

    async def person_view() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The mail and the state rows of one Inbox request — over **one** `list`. Both halves need
        the fleet (the mail for its senders' names, the states for the records themselves), and
        this runs on every page load and every poll: two fleet lists on that path would be two for
        no reason (review of PR #251)."""
        fleet = await call("list")
        return await person_inbox(fleet), await person_states(fleet)

    async def person_inbox(fleet: list[dict[str, Any]]) -> dict[str, Any]:
        """The person inbox as every surface here reads it (§4.10, §4.5a): the `inbox` RPC with no
        caller and no id — **a person's read, which sets no `read_at`**, because a person is not
        the session. That is what lets the Inbox page poll it every few seconds without marking
        anything read and without freeing a depth slot an unanswered question still holds; it is
        the rule the dialog this page replaces already relied on, so no `peek` was needed. Each
        sender gets the name it is known by, and `from_open` the id **Open** goes to while that
        record still exists (§4.5 screen 6: a row opens the session that needs the person)."""
        got = await call("inbox")
        names = {o.get("id"): o.get("name") or o.get("id") for o in fleet}
        records = {o.get("id"): o for o in fleet}
        # §4.5a *Inbox row: FYI* → **Put on the board** (TD-140): the form's board is the sender's
        # repo's, when this host knows that repo and it carries one; else the person picks
        boards_of = {c["root"]: c["board"] for c in board_choices()}

        def board_of(sid: Any) -> str:
            repo = str((records.get(sid) or {}).get("repo") or "")
            return boards_of.get(str(Path(repo).expanduser().resolve()), "") if repo else ""

        for t in got.get("trail") or ():
            if isinstance(t, dict):
                t["board_default"] = board_of(t.get("sid"))
        at = datetime.now(UTC)
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
            e["from_open"] = e["from"] if e["from"] in names else ""
            # the Reply dialog's line (§4.10 *When it is read*, TD-168): a reply reads as a note does
            e["reply_when"] = str(((records.get(e["from"]) or {}).get("read_when") or {}).get("note") or "")
            e["board_default"] = board_of(e["from"])
            if isinstance(e.get("pr"), int):  # §4.9b *The reader*: a held PR asked of the person (TD-093)
                sender = records.get(e["from"]) or {}
                e["pr_url"] = reviewmod.pr_url(sender.get("repo") or sender.get("dir"), e["pr"])
            _owing(e, records.get(e["from"]), at)
            e["age"] = _age(e.get("at"), at)  # the client keeps it ticking; this is what it opens on
            # §4.5 screen 6 *Layout* (TD-082): a duration is words from here, never a `…` the
            # client fills in — `left` for a `steer`'s bound, `until_words` for a snoozed entry,
            # whose row upgrades to the browser's own clock once the script runs.
            e["left"] = _left(e.get("bound"), at)
            e["until_words"] = _left(e.get("snoozed_until"), at)
            # §4.9b (TD-075 step 3): a question **passed up** is the asker's own entry, and the
            # passer — whose recommendation and suggested answers it carries — is named as a sender is
            rec = e.get("recommend")
            if e.get("passed_up") and isinstance(rec, dict) and rec.get("by"):
                e["passer_name"] = names.get(str(rec["by"]), str(rec["by"]))
            # §4.9b: an *answered for you* row names **who asked** — the session Overrule writes
            # to — by the name it is known by, and opens it while it is here
            asker = str(_answered_of(e).get("asker") or "")
            if asker:
                e["asker_name"] = names.get(asker, asker)
                # Overrule writes to the asker, so its dialog's line is the asker's (TD-168)
                e["asker_when"] = str(((records.get(asker) or {}).get("read_when") or {}).get("note") or "")
                e["asker_open"] = asker if asker in names else ""
        return got

    def inbox_html(sections: dict[str, Any], origin: str = "") -> dict[str, str]:
        """Each section's rows, rendered by the one template the page itself renders them with, so
        a poll replaces a section without the client composing any markup — the shape the events
        stream already uses for team headers. Jinja escapes every field, and a mail row's text goes
        through the closed-subset renderer (`shaped`), which is what keeps what a session wrote
        text and nothing else (TD-071 item 8, TD-138)."""
        rows = templates.get_template("inbox_rows.html")
        return {k: rows.render(rows=sections[k], section=k, origin=origin) for k in INBOX_SECTIONS}

    h = SimpleNamespace(
        call=call,
        render_card=render_card,
        defs=defs,
        team_rows=team_rows,
        seats_of=seats_of,
        group_heads=group_heads,
        heads=heads,
        repo_facts=repo_facts,
        identity_info=identity_info,
        person_states=person_states,
        person_view=person_view,
        person_inbox=person_inbox,
        board_items=board_items,
        inbox_html=inbox_html,
        settings_at=settings_at,  # the person's settings read's clock: a write here resets it (TD-174)
    )
    for register in (
        _pages_routes,
        _new_routes,
        _sessions_routes,
        _teams_routes,
        _inbox_routes,
        _help_routes,
        _settings_routes,
        _stream_routes,
    ):
        register(app, h)
    return app


def _pages_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The Org page and Focus (design §4.5)."""
    call, seats_of, identity_info, board_items = h.call, h.seats_of, h.identity_info, h.board_items
    repo_facts = h.repo_facts

    @app.get("/", response_class=HTMLResponse)
    async def org(request: Request):
        # An unreachable agent still gets a page: the banner + Retry are the recovery path
        # (design §4.5 unreachable hosts), never a bare 503.
        agent_down = False
        usage: dict[str, Any] = {}
        person_needs = person_fyi = person_overdue = 0
        info: dict[str, Any] | None = None
        entries: list[dict[str, Any]] = []
        try:
            sessions = await call("list")
            usage = await call("usage")
            with contextlib.suppress(Exception):  # an agent before TD-100 slice 1 has no `gate`: no lines
                usage = with_lines(usage, await call("gate"))
            usage = usage_accounts(usage, sessions)  # one chip per account (§4.5a, TD-122)
            info = await call("host")
            try:
                # the top bar's number: the Inbox page's **Needs you** section, and not unread mail
                # (§4.5a **Inbox page**, TD-069 steps 1–2) — mail *and* the session states, from the
                # same `inbox_sections` the page uses, so the two numbers cannot drift apart
                entries = (await call("inbox"))["entries"]
            except HTTPException as e:
                # a node whose link is down refuses the mailbox (§4.4a): the banner says why, and
                # the page is still this host's sessions
                if e.status_code == 503 or (info or {}).get("mode") != "node":
                    raise
        except HTTPException as e:
            if e.status_code != 503:
                raise
            sessions, agent_down = [], True
        icons = await role_icons(sessions)
        seats = await seats_of(sessions)
        repos, doing = ({}, {}) if agent_down else await repo_facts()
        vs = sorted((view(s, sessions, icons=icons, seats=seats, repos=repos) for s in sessions), key=card_order)
        # the needs-you badge is the same predicate the Inbox rows are (review of PR #251): a
        # record the Org counts and the Inbox did not list was the two pages disagreeing in public
        counts = {"needs-you": sum(1 for v in vs if state_kind(v) in NEEDS_YOU_ROWS)}
        counts.update({k: sum(1 for v in vs if v["state"] == k) for k in ("limited", "stalled?")})
        strip = teams_view(sessions)
        id_info = {} if agent_down else await identity_info()
        boards = [] if agent_down else (await board_items())[0]
        # §4.5a *Inbox row: promote* (TD-132 slice 3): the same rows the Inbox counts, from the
        # `host` reading this page already took, so the top bar and the Inbox cannot disagree
        promos = [] if agent_down else promote_rows((info or {}).get("promotes"))
        if entries or vs or boards or promos:
            secs = inbox_sections(
                entries,
                states=state_rows(
                    vs,
                    host_alarms=alarm_view(id_info.get("alarms")),
                    host=str(id_info.get("host") or host_name()),
                    identity_mode=str(id_info.get("mode") or ""),
                )
                + promos,
                boards=boards,
            )
            person_needs, person_fyi, person_overdue = secs["count"], secs["fyi_n"], secs["overdue_n"]
        return templates.TemplateResponse(
            request,
            "org.html",
            {
                "sessions": vs,
                "groups": (groups := team_groups(vs, strip["teams"], repos, doing)),
                "rollup": rollup(groups),
                "strip": strip,
                "counts": counts,
                "host": host_name(),
                "active": "Org",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": usage,
                "person_needs": person_needs,
                "person_overdue": person_overdue,
                "person_fyi": person_fyi,
                "node_banner": node_banner(info),
                "identity_note": identity_note(id_info),
                "restart_note": "" if agent_down else restart_note(info, id_info),
                "editor_note": uiconf.open_in().error,
                "migrate_note": uiconf.migrate_note(),
            },
        )

    @app.get("/repo/{name}", response_class=HTMLResponse)
    async def repo_page(request: Request, name: str, part: str = ""):
        """The **Repo** page (design §4.5 screen 11, TD-176 slice 5): the servicing team's three
        facets, then the lists behind the numbers — Open PRs, Technical debt, Waiting on you, Doing.
        A registered repo no team services has a page too, its Repo facet alone. `part=1` is the
        page's own refresh: the content without the chrome."""
        now = datetime.now(UTC)
        repos, doing = await repo_facts()
        r = next((v for v in repos.values() if isinstance(v, dict) and v.get("name") == name), None)
        if r is None:
            raise HTTPException(404, f"no registered repo is named {name!r} — ao repo --all lists them")
        sessions = await call("list")
        icons = await role_icons(sessions)
        seats = await seats_of(sessions)
        vs = [view(s, sessions, icons=icons, seats=seats) for s in sessions]
        groups = team_groups(vs, (), {str(r.get("root") or ""): r}, doing) or []
        serving = [g for g in groups if g.get("summary") and (g["summary"].get("repo") or {}).get("name") == name]
        org, _ = await asyncio.to_thread(org_here)
        named = repo_teams(org, host_name()).get(str(Path(str(r.get("root") or "")).resolve()), "")
        teams = list(dict.fromkeys([*(g["team"] for g in serving), *([named] if named else [])]))
        first = serving[0] if serving else None
        if first:
            summary = first["summary"]
            members = first["members"]
        elif named:
            # a team the definitions give this repo, with nothing live now (between runs, stopped):
            # its facets still stand — its members' claims, its doing log — with the repo's own
            # numbers, since no live member points the summary at the repo (review of slice 5)
            members = [v for v in vs if v.get("team") == named]
            summary = team_summary(named, members, {}, doing, prs_waiting(members), now)
            summary["repo"] = repo_facet(r, now, prs_waiting(members))
            summary["motion"] = motion_rows(members, r)
            summary["phases"] = {ph: sum(1 for x in summary["motion"] if x["phase"] == ph) for ph in PHASES}
        else:
            summary = {
                "team": "",
                "repo": repo_facet(r, now),
                "motion": [],
                "phases": {},
                "answers": [],
                "doing": [],
                "face": "doing",
                "answer_key": "",
                "noteam": True,
            }
            members = []
        standing: dict[int, dict[str, Any]] = {}
        for m in members:
            if m.get("seat") or str(m.get("role") or "") == "techlead":
                with contextlib.suppress(Exception):
                    standing.update(pr_standing((await call("inbox", id=m["id"])).get("entries") or [], now))
        rel = (r.get("ledger") or {}).get("path") or ""
        ledger_file = str(Path(str(r.get("root") or "")) / rel) if rel else ""
        boards = [
            b
            for b in (await board_items())[0]
            if str(Path(str(b.get("root") or "")).resolve()) == str(Path(str(r.get("root") or "")).resolve())
        ]
        ctx = {
            "r": r,
            "name": name,
            "g": {"team": summary["team"], "summary": summary},
            "teams": teams,
            "prs": pr_rows(r, members, standing, now),
            "lists": ledger_lists(r, summary["motion"]),
            "boards": boards,
            "doing": summary["doing"],
            "chips": doing_chips(summary["doing"]),
            "ledger_editor": editor_link(ledger_file) if ledger_file else None,
            "host": host_name(),
            "active": "",
        }
        return templates.TemplateResponse(request, "repo_part.html" if part else "repo.html", ctx)

    @app.get("/focus/{sid}", response_class=HTMLResponse)
    async def focus(request: Request, sid: str, window: str = ""):
        try:
            s = await call("seen", id=sid)  # opening Focus is the "seen" (TD-017); returns the record
        except HTTPException as e:
            if e.status_code == 503:
                return RedirectResponse("/", status_code=303)  # the Org shows the down banner
            raise
        known = True
        try:
            fleet = await call("list")
        except HTTPException:  # the record we already have still renders; membership just empties
            fleet, known = [s], False  # …and Ready to close says it does not know, rather than pass
        return templates.TemplateResponse(
            request,
            "focus.html",
            {
                "s": view(
                    s,
                    fleet,
                    fleet_known=known,
                    icons=await role_icons([s]),
                    seats=await seats_of([s]),
                    repos=(await repo_facts())[0],  # the PR's mark on the report line (TD-193)
                ),
                "host": host_name(),
                "active": "Org",
                # design §4.5a **Pop out** (TD-046): the same Focus, without the nav and the top bar
                "popped": window == "1",
                # §4.5a **Reports** (TD-150): the base a claim in review's PR number links from
                "pr_base": await asyncio.to_thread(reviewmod.repo_web, s.get("repo") or s.get("dir")),
                # §4.5a *Focus composer* **prompt chips** (TD-170): the role's saved prompts, for a
                # session whose composer is open — an interactive one — and none otherwise
                "prompts": [] if s.get("unattended") else await asyncio.to_thread(role_prompts, s),
                # §4.5a *Focus: copy on select* (TD-174): the person's, from `settings.yml`
                "copy_on_select": uiconf.copy_on_select(),
                # §4.5a *Focus side panel, Session card* **rounds** line (TD-191): display only
                "rounds": rounds_lines(s, await _rounds_tail(call, s)),
            },
        )

    @app.get("/transcript/{sid}", response_class=HTMLResponse)
    async def transcript(request: Request, sid: str, before: int | None = None, part: str = ""):
        """Screen 9, **Transcript** (design §4.5, §4.5a *Transcript*, TD-166): the record's tool
        transcript through the `transcript` RPC on the record's host — a read, gated by nobody (§9
        invariant 11), which never marks the session seen. `part=1` with `before` is *earlier
        turns*: the twenty before the first shown, as the fragment the page puts above them."""
        if part:
            try:
                t = await call("transcript", id=sid, before=before, turns=TRANSCRIPT_TURNS)
            except HTTPException as e:
                return HTMLResponse(f'<p class="note">{html.escape(str(e.detail))}</p>', status_code=e.status_code)
            return templates.TemplateResponse(request, "transcript_turns.html", {"t": transcript_view(t)})
        try:
            s = await call("get", id=sid)
        except HTTPException as e:
            if e.status_code == 503:
                return RedirectResponse("/", status_code=303)  # the Org shows the down banner
            raise
        t, error = None, ""
        try:
            t = transcript_view(await call("transcript", id=sid, before=before, turns=TRANSCRIPT_TURNS))
        except HTTPException as e:  # no tool session id, no file on its host: the page says which
            error = str(e.detail)
        v = view(s)
        here = v["host"] == host_name()
        return templates.TemplateResponse(
            request,
            "transcript.html",
            {
                "s": v,
                "t": t,
                "error": error,
                "editor": transcript_editor(t["path"], here) if t else None,
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host": host_name(),
                "active": "Org",
            },
        )


async def _rounds_tail(call: Callable[..., Any], s: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The session's last two round-log lines (design §4.8, TD-191), or None when the host agent
    cannot say (an older one, a node out of reach): the line is then left out, never *no round log*."""
    try:
        got = await call("log_tail", id=s["id"], n=2)
    except HTTPException:
        return None
    return got if isinstance(got, list) else None


def rounds_lines(s: dict[str, Any], entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The Session card's **rounds** line (design §4.5a, TD-191): the last two lines with their
    stamps, each marked *from an earlier run* when it is older than this record's start; `[]` is
    *no round log*, None is no line at all. Text a session wrote is only text (TD-071)."""
    if entries is None:
        return None
    start = str(s.get("created") or "")[:16] + "Z"  # the stamps are to the minute
    return {
        "lines": [
            {
                "at": str(e.get("at") or ""),
                "text": str(e.get("text") or ""),
                "earlier": bool(start != "Z" and str(e.get("at") or "") < start),
            }
            for e in entries
            if isinstance(e, dict)
        ]
    }


def _new_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """New session: the form, its checks, and a shell (design §4.5a *New session*)."""
    call = h.call

    @app.get("/new", response_class=HTMLResponse)
    async def new_form(
        request: Request,
        dir: str = "",
        adapter: str = "claude-code",
        resume: str = "",
        project: str = "",
        # design §4.5a **Resume with changes…** (TD-081 step 2): the same fields a one-press
        # Resume carries, filled into the form instead of used. `why` is set when a press that
        # should have been silent landed here, and the form says so rather than leaving a person
        # to wonder which of the two controls they pressed.
        name: str = "",
        profile: str = "",
        role: str = "",
        team: str = "",
        lane: str = "",
        controllers: str = "",
        unattended: str = "",
        where: str = "here",  # a worktree record's Resume with changes… (TD-145)
        worktree: str = "",
        prefilled: str = "",
        why: str = "",
    ):
        profs, default = profiles_mod.load()
        # registered repos (design §5: the dev-cadence registry, `repos_registry` in hosts.yml) first,
        # then recent directories; phase 1 reads the local host's file directly
        repos = hosts.local_host().repos()
        recent = repos + [d for d in await call("recent_dirs") if d not in repos]
        adapters = await call("adapters")
        # design §4.5a New session **Controllers** picker (§4.8): the candidates are the sessions
        # holding `control` — nothing else could act on the new session anyway.
        sessions_now = await call("list")
        control_holders = [
            {"id": o["id"], "name": o.get("name") or o["id"]}
            for o in sessions_now
            if has_control(o.get("capabilities")) and o.get("state") not in ("closed", "exited")
        ]
        # design §4.5a New session **Role** preset: the built-ins, plus what the prefilled directory's
        # repo redefines; `/api/roles` refreshes the list as the directory is typed (TD-040 step a).
        roles = _roles_for(dir)
        return templates.TemplateResponse(
            request,
            "new.html",
            {
                "host": host_name(),
                "active": "Org",
                "profiles": profs,
                "default_profile": default,
                "recent": recent,
                "adapters": adapters,
                "control_holders": control_holders,
                # design §4.5a New session **Grants** checkboxes: the grants a record may hold
                # (`sessionorc.models.GRANTS`, the same list `ao new --grant` offers), each with
                # the one-line warning the row asks for.
                "grants_all": list(GRANTS),
                # Jinja renders a missing key as empty, so a grant added to GRANTS without a note
                # would ship a silent, unexplained checkbox. Say so on the page instead (review of
                # PR #141); the test still fails first for anyone who runs them.
                "grant_notes": {g: GRANT_NOTES.get(g) or UNDESCRIBED_GRANT for g in GRANTS},
                "roles": roles["roles"],
                "default_controllers": roles.get("controllers") or [],
                # design §4.5a New session **Project** picker (§4.9): the projects defined on this
                # host, each carrying its repos' checkouts so the form can narrow the directory
                # list without another round trip. "No project" is the default and is what every
                # session was before.
                "projects": projects_view(),
                # design §4.5a New session **Team** picker (§4.9 *A person in the team*, TD-173)
                "form_teams": teams_for_form(sessions_now),
                "prefill": {
                    "dir": dir,
                    "adapter": adapter,
                    "resume": resume,
                    "project": project,
                    "name": name,
                    "profile": profile,
                    "role": role,
                    "team": team,
                    "lane": lane,
                    "controllers": [c for c in controllers.split(",") if c.strip()],
                    "unattended": unattended == "on",
                    "where": "worktree" if where == "worktree" else "here",
                    "worktree": worktree,
                    "prefilled": prefilled == "1",
                    "why": why,
                },
            },
        )

    def _roles_for(dir: str) -> dict[str, Any]:
        """The presets that resolve in `dir`'s repo, and the repo's `controllers:` default, for the
        form's pick-list and the Controllers picker's prefill (design §4.5a, §4.8 "Defaults fill
        membership at launch"). A malformed `.agentorc.yml` is reported, not raised: the form still
        renders with the built-ins, and Start says what is wrong."""
        try:
            cfg = repoconfig.discover(dir or os.getcwd())
            found = [r.to_dict() for r in repoconfig.roles(cfg)]
        except ValueError as e:
            return {"roles": [r.to_dict() for r in repoconfig.roles(repoconfig.RepoConfig())], "error": str(e)}
        return {"roles": found, "controllers": cfg.controllers, "file": str(cfg.path) if cfg.path else None}

    @app.get("/api/team_review")
    async def api_team_review(team: str = "", dir: str = ""):
        """The line under New session's **Team** picker (design §4.5a): the reader a session started
        in `team` from `dir` gets when its role has none."""
        return await asyncio.to_thread(team_reader, team, dir) if team else {"review": None, "line": ""}

    @app.get("/api/roles")
    async def api_roles(dir: str = ""):
        return _roles_for(dir)

    @app.post("/new")
    async def new_submit(
        name: str = Form(...),
        dir: str = Form(...),
        adapter: str = Form("claude-code"),
        profile: str = Form(""),
        prompt: str = Form(""),
        resume: str = Form(""),
        unattended: str = Form(""),
        where: str = Form("here"),
        worktree: str = Form(""),
        role: str = Form(""),
        lane: str = Form(""),
        project: str = Form(""),
        team: str = Form(""),
        until: str = Form(""),
        at: str = Form(""),
        controller: Annotated[list[str], Form()] = NO_CONTROLLERS,
        grant: Annotated[list[str], Form()] = NO_GRANTS,
    ):
        wt = None
        if where == "worktree":
            wt = worktree.strip() or naming.slug(name.strip() or "session")
        # The role preset (design §4.5a, §4.8) fills what the form left empty: the brief from its
        # template, the lane, its grants, and its profile unless one was picked. The Controllers
        # picker was prefilled from the repo's or preset's `controllers:` when the page loaded, so
        # what is ticked is what was meant — a person unticking the default is a decision.
        refs = [r.strip() for r in lane.split(",") if r.strip()]
        preset = brief = ledger = made_from = None
        if adapter != "shell":
            try:
                cfg = repoconfig.discover(dir.strip() or os.getcwd())
                ledger = cfg.ledger
                if role.strip():
                    preset = repoconfig.resolve_role(cfg, role.strip())
                    brief, made_from = preset.compose(refs or None)
            except (KeyError, ValueError) as e:
                raise HTTPException(400, str(e).strip('"')) from None
        # design §4.5a New session **Project** picker (§4.9 "Home and reach"): the same block
        # `ao new --project` puts in front of the brief, from the same function — each of the
        # project's repos on this host and which one is home. A one-repo project adds nothing, and
        # a name with no definition still badges the session: nothing keys on the badge.
        # design §4.5a New session **Team** picker (§4.9 *A person in the team*, TD-173): a role's own
        # `review:` wins, else the team's reader, as `ao new --team` fills it. The Controllers
        # picker was ticked with the team's live manager when the team was picked.
        review = preset.review if preset else None
        if team.strip() and adapter != "shell" and review is None:
            review = (await asyncio.to_thread(team_reader, team.strip(), dir.strip()))["review"]
        text = prompt.strip() or brief
        # what the brief was made from (design §6 rule 7, TD-217): only when the brief is the preset's
        made_from = None if prompt.strip() else made_from
        if project.strip():
            block, _note = teams.reach_block(orgmod.load(), project.strip(), dir.strip() or os.getcwd(), host_name())
            if block:
                text = block + text if text else block
                made_from = repoconfig.prefixed(made_from, block)
        s = await call(
            "create",
            name=name.strip() or "session",
            dir=dir.strip(),
            adapter=adapter,
            profile=profile or (preset.profile if preset else None) or "",
            prompt=text,
            resume=resume.strip() or None,
            unattended=unattended == "on",
            **teams.gate_prompts(unattended == "on"),  # the usage gate's two texts (§6, TD-100)
            worktree=wt,
            repo=dir.strip() if wt else None,
            # The Grants checkboxes were ticked from the preset when the page loaded and as the
            # Role changed (design §4.5a), so what is ticked is what was meant — including an
            # untick, which is a person deciding this session does not get the grant.
            capabilities=[g for g in dict.fromkeys(grant) if g in GRANTS],
            lane=refs or (list(preset.lane) if preset else []),
            role=preset.name if preset else "",
            review=review,  # who reads its PRs (design §4.9b *The reader*): the role's, else the team's
            context_bound=preset.context_bound if preset else None,  # §4.8 *A role has a context bound*
            **({"prompt_from": made_from} if made_from else {}),
            ledger=ledger,
            controllers=[c for c in controller if c.strip()],
            project=project.strip(),  # a badge, exactly as `ao new --project` sets it (§9 invariant 9)
            team=team.strip(),  # the badge and the group, as `ao new --team` sets it (§4.9)
            **stop_fields(until, unattended == "on"),
            **start_fields(at, unattended == "on"),
        )
        # a scheduled record has no terminal yet: the Org shows its card with the *starts* note (§4.5a At)
        return RedirectResponse("/" if s.get("state") == "scheduled" else f"/focus/{s['id']}", status_code=303)

    @app.post("/shell")
    async def shell(dir: str = Form(...), name: str = Form("")):  # unnamed: the agent names it (TD-030)
        s = await call("create", name=name, dir=dir, adapter="shell")
        return RedirectResponse(f"/focus/{s['id']}", status_code=303)

    @app.get("/api/occupancy")
    async def api_occupancy(dir: str = ""):
        if not dir.strip():
            return {"dir": "", "occupants": [], "git": False}
        return await call("occupancy", dir=dir.strip())

    @app.get("/api/name_check")
    async def api_name_check(dir: str = "", name: str = "", worktree: bool = False):
        """What §4.1's name rule would do (design §4.5a, TD-030): the New session form asks as you
        type, the way it already asks about directory occupancy. `worktree` puts the name in the
        repo's scope, which is where the session would actually land."""
        if not (dir.strip() and name.strip()):
            return {"id": "", "name": name, "verdict": "free", "holder": None, "message": ""}
        return await call("name_check", dir=dir.strip(), name=name.strip(), repo=dir.strip() if worktree else None)


def _sessions_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """A session's own controls and reads: resume, the card's actions, its inbox, the list."""
    call, seats_of = h.call, h.seats_of

    # -- actions (every control in design §4.5a that exists in phase 1) --------------------------

    @app.post("/api/sessions/{sid}/resume")
    async def resume_session(sid: str, request: Request):
        """design §4.5a **Focus (exited / closed)** → **Resume**, and **Inbox row: state** →
        **Reopen and push** (TD-081 step 2): *a resume option that requires no input from me*.

        One press, no form: a `create` carrying the record's own name, so the name check answers
        `supersede` and the resumed session takes the bare name and the record's id in place, with
        its mail (§4.10, built by step 1). It is a person's act from the page — **caller-less**, no
        attenuation (§4.8 create rule) — and the session it starts is **attended**, whatever the
        record was.

        **When it cannot be silent it is not a guess.** What the record itself settles is answered
        here; everything else is the agent's own refusal, and either way the answer is the same:
        the filled-in form, with the reason on it, for the person to finish by hand. The client
        follows `form`; nothing is created behind their back and nothing is guessed at."""
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        rec = await call("get", id=sid)
        # **Resume with changes…** asks for the form outright and creates nothing (§4.5a).
        if body.get("form"):
            return JSONResponse({"ok": True, "form": resume_form_url(rec)})
        if why := resume_blocked(rec):
            return JSONResponse({"ok": False, "form": resume_form_url(rec, why), "why": why})
        # *Reopen and push* is the same press with a first prompt **the page wrote** — fixed text
        # in the source, never anything a session said (§4.2, TD-071 item 8).
        prompt = REOPEN_AND_PUSH if body.get("push") else ""
        try:
            new = await call("create", **resume_create(rec, prompt=prompt))
        except HTTPException as e:
            why = str(e.detail).strip('"') or "the host agent refused the resume"
            return JSONResponse({"ok": False, "form": resume_form_url(rec, why), "why": why})
        return JSONResponse({"ok": True, "id": new["id"]})

    @app.post("/api/sessions/{sid}/{action}")
    async def action(sid: str, action: str, request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if action in ("allow", "deny"):
            s = await call("get", id=sid)
            pend = s.get("pending") or {}
            if pend.get("kind") != "permission" or not pend.get("tool_use_id"):
                raise HTTPException(409, "no pending permission (answered, timed out, or in the terminal)")
            await call("decide", id=sid, tool_use_id=pend["tool_use_id"], behavior=action, reason=body.get("reason"))
        elif action == "kill":
            await call("kill", id=sid)
        elif action == "close":
            await call("close", id=sid)
        elif action == "send":
            await call("send", id=sid, text=body.get("text", ""))
        elif action == "wrapup":
            await call("send", id=sid, text=WRAPUP_PROMPT, wrapup=True)
        elif action == "mode":
            on = bool(body.get("unattended"))
            s = await call("set_mode", id=sid, unattended=on, **teams.gate_prompts(on))  # §6's two texts
            due = _instant(s.get("run_until"))
            if s.get("unattended") and due is not None and due <= datetime.now(UTC):
                # **Hand back** (design §4.5a, TD-096): a stop time that fell due while the person held
                # the session is not a deadline any more — the resume's rule — and nothing acted on it
                # while it was interactive, so handing back must not kill it on the next tick. One
                # still ahead stays.
                await call("set_stop", id=sid, run_until=None)
        elif action == "keys":
            await call("keys", id=sid, keys=list(body.get("keys") or []))
        elif action == "shell-here":
            s = await call("get", id=sid)
            new = await call("create", name=f"{s['name']}-shell", dir=s["dir"], adapter="shell")
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)  # acted on this card too (TD-017)
            return JSONResponse({"ok": True, "id": new["id"]})
        elif action == "drop":
            # design §4.5a Focus **Reports** → Drop: the person let a claim go, and the record says
            # so rather than losing it — a declaration, so the tick's derived entry cannot undo it.
            ref = str(body.get("ref") or "").strip()
            if not ref:
                raise HTTPException(400, "drop needs the reference to drop")
            # TD-150 slice 2 (§4.5a *Reports*): not while a PR from that claim is open — the claim
            # in review is the one a person reading the panel must not let go (TD-143)
            s = await call("get", id=sid)
            pr = review_pr(s.get("progress") or [], ref)
            if pr:
                # the record cannot yet say whether that PR is still open (slice 3 brings its state),
                # so the refusal says what ends it either way
                raise HTTPException(
                    409,
                    f"{ref} is in review as PR #{pr}: a claim with a PR is not dropped — it ends when the "
                    "session reports it done or dropped, once the PR is merged or closed",
                )
            await call("progress", id=sid, ref=ref, status="dropped", why=body.get("why") or "dropped from Focus")
        elif action == "start":
            # design §4.5a **starts** note on Focus and the banner's / `more ▾`'s **Start now** (§6
            # *Start time*, TD-152): `now`, or a time as `ao at` takes it; the agent refuses a
            # record that already started, in its own words
            when = str(body.get("at") or "now").strip()
            try:
                at = "now" if when.lower() == "now" else clistop(when, "start time")
            except AgentError as e:
                raise HTTPException(400, str(e)) from None
            s = await call("set_start", id=sid, start_at=at)
            return JSONResponse({"ok": True, "start_note": start_note(s), "start_at": s.get("start_at")})
        elif action == "stop":
            # design §4.5a Focus header **stops** badge → click to edit (§6, TD-026). The stop time
            # was settable at New session and from `ao until` and nowhere else, so a person who set
            # `+8h` and wanted another hour had to reach for the CLI. An empty time clears it, which
            # is the decision to let a session run on, made out loud rather than by restarting it.
            when = str(body.get("until") or "").strip()
            try:
                run_until = clistop(when) if when else None
            except AgentError as e:
                raise HTTPException(400, str(e)) from None
            s = await call("set_stop", id=sid, run_until=run_until, wrapup_prompt=WRAPUP_PROMPT if run_until else None)
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "stop_note": stop_note(s), "run_until": s.get("run_until")})
        elif action == "grants":
            s = await call("set_grants", id=sid, add=_str_list(body, "add"), remove=_str_list(body, "remove"))
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "capabilities": s.get("capabilities") or []})
        elif action == "controllers":
            # design §4.5a Focus **controllers** chip (§4.8, TD-036). The UI calls with no `caller`,
            # so it acts as the person it is: the agent's own rules still decide what is allowed.
            # A typed *name* is resolved to an id first — the agent stores whatever it is given and
            # a name would sit in the list forever as a controller that can never act (review).
            fleet = await call("list")
            s = await call(
                "set_controllers",
                id=sid,
                add=[_as_session_id(c, fleet) for c in _str_list(body, "add")],
                remove=[_as_session_id(c, fleet, must_exist=False) for c in _str_list(body, "remove")],
            )
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "controllers": s.get("controllers") or []})
        elif action in ("message", "reply"):
            # design §4.5a **Message** (card `more ▾`, Focus header) and Focus Inbox **Reply**
            # (§4.10): mail, not a send — nothing is typed into any pane. The UI calls with no
            # `caller`, so `from` is the person and the message gate lets it through. A reply names
            # the entry it answers and no addressee: the agent addresses it to that entry's sender.
            text = str(body.get("text") or "")
            if action == "reply":
                ref = str(body.get("reply_to") or "").strip()
                if not ref:
                    raise HTTPException(400, "a reply names the entry it answers")
                got = await call("msg", text=text, kind="reply", reply_to=ref)
            else:
                kind = str(body.get("kind") or "note")
                if kind not in ("note", "ask"):
                    raise HTTPException(400, "a person's Message is a note or an ask")
                about = str(body.get("about") or "").strip() or None
                got = await call("msg", to=[sid], text=text, kind=kind, about=about)
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)
            return JSONResponse({"ok": True, "id": got["entry"]["id"], "delivered": got["delivered"]})
        elif action == "unmail":
            # design §4.5a Focus **Inbox** → delete (§4.10 lifecycle: a person's hand): this
            # session's copy only; the sender keeps its own.
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, "delete needs the entry's id")
            await call("inbox_delete", id=sid, msg=ref)
        elif action == "remove":
            await call("remove", id=sid)
        elif action == "seen":
            pass  # the mark below is the whole action (the Focus page sends it when its session goes idle)
        else:
            raise HTTPException(404, f"no action {action}")
        if action != "remove":
            with contextlib.suppress(HTTPException):
                await call("seen", id=sid)  # acting on a card counts as looking at it (TD-017)
        return JSONResponse({"ok": True})

    @app.get("/api/sessions/{sid}/inbox")
    async def api_inbox(sid: str, request: Request):
        """design §4.5a Focus side panel **Inbox** (§4.10 "A bounded body"): bodies never ride the
        pushed record, so the panel fetches them here. No caller — a person's read, which sets no
        `read_at`, because a person is not the session. Each sender gets its name beside its id,
        and its text the two halves the Inbox row draws (`shaped`, TD-138): the panel folds the
        same way, from the same renderer, and composes no markup of its own from a session's text."""
        got = await call("inbox", id=sid)
        fleet = await call("list")
        names = {o.get("id"): o.get("name") or o.get("id") for o in fleet}
        records = {o.get("id"): o for o in fleet}
        origin = page_origin(request)
        for e in got["entries"]:
            e["from_name"] = "person" if e["from"] == "person" else names.get(e["from"], e["from"])
            # the Reply dialog's line (§4.10 *When it is read*, TD-168): a reply reads as a note does
            e["reply_when"] = str(((records.get(e["from"]) or {}).get("read_when") or {}).get("note") or "")
            s = shaped(e.get("text"), origin)
            e["lead_html"], e["rest_html"] = str(s["lead"]), str(s["rest"])
        return got

    @app.get("/api/sessions")
    async def api_sessions():
        sessions = await call("list")
        icons = await role_icons(sessions)
        seats = await seats_of(sessions)
        # the readings too, so the PR's mark on a report survives Focus's Members refresh (TD-193)
        repos = (await h.repo_facts())[0]
        return [view(s, sessions, icons=icons, seats=seats, repos=repos) for s in sessions]


def _teams_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """Teams: the list, Start, and Stop / Wind down (design §4.9, §4.9a)."""
    call = h.call

    # -- the Teams strip (design §4.5a Org **Teams** strip, §4.9) --------------------------------
    # Start and Stop are `agentorc.teamrun`'s, the sequence `ao team start|stop` runs: every
    # pre-flight check before any create, and the wrap-up order on the way down. The runner blocks
    # (it waits on states), so it goes to a worker thread and the event loop stays free.

    background: set[asyncio.Task[Any]] = set()  # strong refs: a bare create_task may be collected
    stopping_leads: set[str] = set()  # teams whose lead-stop is already in flight: one per team
    stop_errors: dict[str, str] = {}  # a background lead-stop that failed, until the strip reports it

    def _lead_stopped(team: str, lead: str) -> Any:
        """What becomes of the background half of a stop. A task whose exception nobody retrieves is
        a silent failure path, and §4.5 "Errors" says there is none here — so the outcome is logged
        either way, and a failure is kept for the strip to report on its next refresh, which is the
        only channel a request that has already returned still has (review of PR #124)."""

        def done(task: asyncio.Task[Any]) -> None:
            background.discard(task)
            stopping_leads.discard(team)
            if task.cancelled():
                log.warning("team %s: stopping its lead %s was cancelled", team, lead)
                stop_errors[team] = f"stopping {lead} was cancelled"
                return
            err = task.exception()
            if err is None:
                log.info("team %s: lead %s stopped", team, lead)
                stop_errors.pop(team, None)
                return
            log.error("team %s: stopping its lead %s failed: %s", team, lead, err)
            stop_errors[team] = f"{lead} did not stop: {str(err).strip(chr(34))}"

        return done

    def _team_http(e: Exception) -> HTTPException:
        """One mapping for every way a start or a stop can fail, so the strip reports the agent's
        own message the way every other control does — a toast (design §4.5 "Errors")."""
        if isinstance(e, AgentUnavailable):
            return HTTPException(503, f"host agent unreachable: {e}")
        if isinstance(e, teamrun.PartialStart):
            # The created sessions are running on the brief, so a stale-brief warning belongs on
            # this path most of all (review of PR #139). `detail` is what the toast renders.
            warnings = " · ".join([*e.plan.warnings, *e.plan.notes])
            return HTTPException(409, f"{e}{' — ' + warnings if warnings else ''}")
        return HTTPException(400, str(e).strip('"'))

    @app.get("/api/teams")
    async def api_teams():
        v = teams_view(await call("list"))
        for row in v.get("teams", []):
            if row["name"] in stopping_leads:
                row["stopping"] = True
            failed = stop_errors.pop(row["name"], None)  # reported once, then forgotten
            if failed:
                row["error"] = failed
        return v

    @app.post("/api/teams/{name}/start")
    async def api_team_start(name: str):
        if hosts.is_node():
            raise HTTPException(409, node_org_note())  # the strip's note, as the toast (§4.4a)
        org, _notes = org_here()
        try:
            _plan, result = await asyncio.to_thread(teamrun.start, rpc, org, name, host_name())
        except (teams.TeamError, ValueError, AgentError, AgentUnavailable) as e:
            # A failed pre-flight check created nothing (§4.9): the toast is the whole outcome.
            raise _team_http(e) from None
        return JSONResponse({"ok": True, **result})

    # design §4.9 *Add or remove a member from the team card*, §4.5a *team card: Members…* and the
    # *Members dialog* (TD-163, built by TD-172): the one control that edits a definition from the
    # page — `org.yml`, as text, through the UI process, never the host agent (§4.4a: the org file
    # is the clients'). A repo-defined team is read here and edited by PR.
    @app.get("/api/teams/{name}/members")
    async def api_team_members(name: str):
        if hosts.is_node():
            raise HTTPException(409, node_org_note())
        org, _notes = org_here()
        try:
            v = teamrun.members_view(org, name, await call("list"))
        except (teams.TeamError, ValueError) as e:
            raise _team_http(e) from None
        v["roles"] = [r.name for r in repoconfig.roles(repoconfig.RepoConfig(), org.roles)]
        return v

    @app.post("/api/teams/{name}/members")
    async def api_team_members_edit(name: str, request: Request):
        body = await request.json()
        if hosts.is_node():
            raise HTTPException(409, node_org_note())
        org, _notes = org_here()
        try:
            view_ = teamrun.members_view(org, name, await call("list"))
            if not view_["editable"] or org.path is None:
                raise teams.TeamError(f"team {name} is {view_['note'] or 'not in org.yml'}")
            if body.get("action") == "add":
                lane = [x.strip() for x in str(body.get("lane") or "").split(",") if x.strip()]
                got = await asyncio.to_thread(
                    teamrun.add_member, rpc, org.path, name, host_name(),
                    role=str(body.get("role") or ""), member=str(body.get("name") or "").strip(), lane=lane,
                )  # fmt: skip
            elif body.get("action") == "remove":
                got = await asyncio.to_thread(
                    teamrun.remove_member,
                    rpc,
                    org.path,
                    name,
                    index=int(body.get("index")),
                    role=str(body.get("role") or ""),
                )
            else:
                raise HTTPException(400, "action is add or remove")
        except (teams.TeamError, ValueError, TypeError, AgentError, AgentUnavailable) as e:
            raise _team_http(e) from None
        return JSONResponse({"ok": True, **got})

    @app.post("/api/teams/{name}/stop")
    async def api_team_stop(name: str, request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        now = bool(body.get("now"))
        if hosts.is_node():
            raise HTTPException(409, node_org_note())
        org, _notes = org_here()
        try:
            st = await asyncio.to_thread(teamrun.stop_members, rpc, org, name, now=now)
        except (teams.TeamError, ValueError, AgentError, AgentUnavailable) as e:
            raise _team_http(e) from None
        pending = None
        if st.lead is not None and name in stopping_leads:
            # Two presses, or a slow first stop: the lead is already being stopped and a second task
            # would send it a second wrap-up or kill, whose failure the first would swallow.
            return JSONResponse(
                {
                    "ok": True,
                    "team": name,
                    "now": now,
                    "sessions": st.acted,
                    "manager": None,
                    "text": f"{name}: already stopping — its manager follows when the members settle",
                }
            )
        if st.lead is not None:
            # §4.9's order is members, then the lead once they settle — up to the wrap-up window,
            # which is minutes. The page must not hold a request open that long, so the second half
            # runs behind the response and the state deltas on /events show it happening.
            pending = st.lead.get("name") or st.lead["id"]
            stopping_leads.add(name)
            task = asyncio.create_task(asyncio.to_thread(teamrun.stop_lead, rpc, st))
            background.add(task)
            task.add_done_callback(_lead_stopped(name, pending))
        sent = sum(1 for e in st.acted if e["role"] != "person")  # a person's session is left alone (§4.9)
        msg = f"{name}: {'killed' if now else 'wrap-up sent to'} {sent} session{'' if sent == 1 else 's'}"
        if pending:
            msg += f" — {pending} follows when they settle"
        for e in st.acted:
            if e["role"] == "person":
                msg += f"; {e['action']}"
        return JSONResponse(
            {"ok": True, "team": name, "now": now, "sessions": st.acted, "manager": pending, "text": msg}
        )


def _help_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The Help page (design §4.5 screen 10, §4.5a *Help page*; TD-157, built by TD-167)."""

    @app.get("/help", response_class=HTMLResponse)
    async def help_page(request: Request):
        """Every control that has a paragraph in §4.5a *The help text*, by screen, each under a
        heading whose id is the control's, so a mark's *every control → Help* lands on its group.
        Display only — nothing on it is a control — and fixed text from `help.py`, which
        `tests/test_help.py` holds equal to the design's list."""
        screens = [(sid, title, [helpmod.BY_KEY[k] for k in keys]) for sid, title, keys in helpmod.SCREENS]
        return templates.TemplateResponse(
            request,
            "help.html",
            {
                "screens": screens,
                "host": host_name(),
                "active": "",
                "usage": {},
                "volatile": hosts.local_host().volatile,
            },
        )


def _settings_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The Settings page (design §4.5 screen 8, §4.5a *Settings page*; TD-148): the page and its
    writes. Every write goes through the host agent's `set_settings` — a person's own, refused to a
    session — and every read of `settings.yml` through its `settings` read; this process reads only
    the files the clients read (`profiles.yml`, `hosts.yml`, the org, each repo's `.agentorc.yml`)."""
    call = h.call

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_view(request: Request):
        agent_down, got, usage, info, why = False, {}, {}, {}, ""
        try:
            got = await call("settings")
            usage = await call("usage")
            info = await call("host")
        except HTTPException as e:
            if e.status_code != 503:
                why = str(e.detail)  # an agent that refuses the read: its words, the files still drawn
            else:
                agent_down = True
        notes: list[str] = [why] if why else []
        try:
            profiles, default = profiles_mod.load()
        except ValueError as e:
            profiles, default = {}, ""
            notes.append(str(e))
        org, org_notes = org_here()
        notes += org_notes
        local = hosts.local_host()
        node = (info or {}).get("mode") == "node"
        uiconf.set_read(got or None)
        term = setmod.you(got.get("person"))
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "usage_groups": setmod.usage_cards(profiles, got.get("usage_gate"), usage),
                "profiles_file": str(profiles_mod.profiles_file()),
                "teams": setmod.team_cards(org.teams, got.get("teams")),
                "repos": setmod.repo_cards(local.repos(), got.get("repos")),
                "you": term,
                "browser_keys": setmod.BROWSER_KEYS,
                "host_card": setmod.host_card(
                    setmod.local_entry(hosts.hosts_file()), local, hosts.home_name(), hosts.nodes()
                ),
                "profile_cards": setmod.profile_cards(profiles, default),
                "org_cards": setmod.org_cards(org.teams),
                "files": {
                    "settings": str(got.get("file") or ""),
                    "profiles": str(profiles_mod.profiles_file()),
                    "hosts": str(hosts.hosts_file()),
                    "org": str(org.path or orgmod.org_file()),
                },
                "reads": setmod.FILES,
                "set_at": str((info or {}).get("home") or hosts.home_name()) if node else "",
                "notes": notes,
                "migrate": [str(m) for m in got.get("migrate") or []],
                "editor_note": uiconf.open_in().error,
                "host": host_name(),
                "active": "Settings",
                "agent_down": agent_down,
                "volatile": local.volatile,
                "usage": {},
            },
        )

    async def body_of(request: Request) -> dict[str, Any]:
        got = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(got, dict):
            raise HTTPException(400, "send a JSON object")
        return got

    def answer(got: Any) -> JSONResponse:
        h.settings_at["at"] = 0.0  # the next page reads the person's settings again (TD-174)
        return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})

    @app.post("/api/settings/usage")
    async def settings_usage(request: Request):
        """§4.5a *Settings page: Usage* → **Save** on a profile card: `{profile, reserves: {label:
        text}}`, each text in `ao gate`'s forms (`30`, `10/day`, empty to clear; on a metered card an
        amount, `$5` or `20M tok`) — refused in place with the same words when it is not one — then
        `set_settings {profile, reserves}`, whose refusal names the billing when the kind is wrong."""
        body = await body_of(request)
        raw = body.get("reserves")
        if not isinstance(raw, dict) or not raw:
            raise HTTPException(400, "usage: send {profile, reserves: {label: reserve}}")
        try:
            reserves = {str(k): setmod.parse_reserve_text(v) for k, v in raw.items()}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return answer(await call("set_settings", profile=str(body.get("profile") or ""), reserves=reserves))

    @app.post("/api/settings/teams")
    async def settings_teams(request: Request):
        """§4.5a *Settings page: Teams* → **Save** / **Clear**: `{team, until?, reserve?}` — `until`
        in the CLI's forms (`06:00`, `+8h`, ISO) read in this host's clock and handed over as an
        instant, `null` to clear; `reserve` a whole percent, `0` or empty clearing it. A team the
        org does not define is refused, naming the defined ones, as `ao team until` refuses it."""
        body = await body_of(request)
        team = str(body.get("team") or "")
        defined = org_here()[0].teams
        if team not in defined:
            raise HTTPException(
                400, f"no team {team!r}: the org defines {', '.join(sorted(defined)) or 'none'} (design §4.9)"
            )
        change: dict[str, Any] = {}
        if "until" in body:
            when = str(body.get("until") or "").strip()
            try:
                change["until"] = clistop(when) if when else None
            except AgentError as e:
                raise HTTPException(400, str(e)) from None
        if "reserve" in body:
            r = str(body.get("reserve") if body.get("reserve") is not None else "").strip()
            if r and not r.isdigit():
                raise HTTPException(400, f"reserve priority is a whole percent, not {r!r}")
            change["reserve"] = int(r) if r and int(r) else None  # 0 clears it, as `ao team reserve 0` does
        if not change:
            raise HTTPException(400, "teams: send until or reserve")
        return answer(await call("set_settings", teams={team: change}))

    @app.post("/api/settings/repos")
    async def settings_repos(request: Request):
        """§4.5a *Settings page: Repos* → the promote's **auto** switch: `{repo, auto: bool}`, written
        to `repos.<repo>.promote.auto` through `set_settings`."""
        body = await body_of(request)
        repo, auto = str(body.get("repo") or ""), body.get("auto")
        if not repo or not isinstance(auto, bool):
            raise HTTPException(400, "repos: send {repo, auto: true|false}")
        return answer(await call("set_settings", repos={repo: {"promote": {"auto": auto}}}))

    @app.post("/api/settings/you")
    async def settings_you(request: Request):
        """§4.5a *Settings page: You* → **Save**: `{open_in?, terminal?: {size?, face?, copy_on_select?}}` into
        `person:` through `set_settings`, which validates each and refuses a session. `open_in` is
        `vscode`, `none` or `{label, url}` — a template the UI would refuse (§5: its scheme) is
        refused here in the same words, before it is written; a `null` clears a key."""
        body = await body_of(request)
        change: dict[str, Any] = {}
        if "open_in" in body:
            o = body["open_in"]
            if o is not None:
                got = uiconf.parse_open_in(o)
                if got.error:
                    raise HTTPException(400, got.error)
            change["open_in"] = o
        term = body.get("terminal")
        if term is not None:
            if not isinstance(term, dict) or not set(term) <= {"size", "face", "copy_on_select"}:
                raise HTTPException(400, "you: terminal takes size, face and copy_on_select")
            change["terminal"] = term
        if not change:
            raise HTTPException(400, "you: send open_in or terminal")
        return answer(await call("set_settings", person=change))


def _inbox_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The person's Inbox: the page, its payload and its controls (design §4.10, §4.5a)."""
    call, person_view, inbox_html, board_items = h.call, h.person_view, h.inbox_html, h.board_items

    @app.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request):
        """design §4.5 screen 6 / §4.5a **Inbox page** (TD-069 steps 1 and 2): full width, the
        person inbox and the sessions' states in three sections, and the count that means *what is
        waiting on a person*. From step 3 the due board items join **Needs you** too."""
        agent_down = False
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            got, states, agent_down = {"entries": []}, [], True  # the banner + Retry, never a bare 503
        # with the host agent down nothing is claimed as waiting — the Org's top bar and the poll say
        # the same — so the board is left unread rather than counted on this page alone (review of #472)
        boards, board_note = ([], "") if agent_down else await board_items()
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
            boards=boards,
        )
        picks = rail_picks(request.query_params)
        return templates.TemplateResponse(
            request,
            "inbox.html",
            {
                "sections": sections,
                "picks": picks,
                "rail": rail_counts(rail_rows(sections), picks),
                "origin": page_origin(request),
                "board_note": board_note,
                "board_choices": board_choices(),
                "person_needs": sections["count"],
                "person_fyi": sections["fyi_n"],
                "host": host_name(),
                "active": "Inbox",
                "agent_down": agent_down,
                "volatile": hosts.local_host().volatile,
                "usage": {},
            },
        )

    @app.get("/inbox/{mid}", response_class=HTMLResponse)
    async def inbox_entry_page(request: Request, mid: str):
        """design §4.5 screen 6 *The message page*, §4.5a **Inbox message page** (TD-129, built by
        TD-136): one mail entry whole — its row, drawn by the row's own macro in the section the
        list has it in, so its controls, RPCs and confirms are the list's, with *details* open —
        then its thread from the person-only `thread` read, oldest first, none of it a control.
        **Back** returns to the list the page was opened from (`back`, the list's query) at this
        row. An entry the person inbox no longer holds reads *gone*; a refusal (another host's id,
        §4.4a) reads in the host agent's words. Reading marks nothing (§4.10)."""
        back = str(request.query_params.get("back") or "")
        back = back if back.startswith("?") else ""
        ctx: dict[str, Any] = {
            "host": host_name(),
            "active": "Inbox",
            "usage": {},
            "volatile": hosts.local_host().volatile,
            "mid": mid,
            "back_url": f"/inbox{back}#{mid}",
            "origin": page_origin(request),
            "fold_open": True,
            "board_choices": board_choices(),
            "entry": None,
            "section": "",
            "thread": [],
            "pruned": False,
            "gone": "",
        }
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            ctx["gone"] = f"{host_name()}: host agent unreachable — no mail can be read until it is back"
            return templates.TemplateResponse(request, "inbox_entry.html", ctx)
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
        )
        for sec in INBOX_SECTIONS:
            e = next((x for x in sections[sec] if x.get("id") == mid and not x.get("row")), None)
            if e is not None:
                ctx["entry"], ctx["section"] = e, sec
                break
        if ctx["entry"] is None:
            ctx["gone"] = f"gone: {mid} is no longer in the person inbox — pruned by retention, or dismissed"
            if "@" in mid:  # another host's entry (§4.4a): the host agent says why in its own words
                try:
                    await call("thread", msg=mid)
                except HTTPException as err:
                    ctx["gone"] = str(err.detail)
            return templates.TemplateResponse(request, "inbox_entry.html", ctx)
        try:
            th = await call("thread", msg=mid)
        except HTTPException as err:
            # an older host agent has no `thread`: the entry still reads whole, the thread says why
            ctx["thread_error"] = str(err.detail)
            th = {"entries": [], "pruned": False}
        # a thread reaches senders the person inbox never heard from (a techlead's reply to its asker)
        names = {o.get("id"): o.get("name") or o.get("id") for o in await call("list")}
        ctx["thread"] = [
            {
                **t,
                "from_name": "person" if t.get("from") == "person" else names.get(t.get("from")) or t.get("from"),
                "age": _age(t.get("at"), datetime.now(UTC)),
            }
            for t in th.get("entries") or ()
            if t.get("id") != mid
        ]
        ctx["pruned"] = bool(th.get("pruned"))
        return templates.TemplateResponse(request, "inbox_entry.html", ctx)

    @app.get("/api/person/inbox")
    async def api_person_inbox(request: Request):
        """design §4.5a Org top bar **Inbox** and the **Inbox page** (§4.10): the entries, which
        section each is in, their rendered rows, and `needs` — the count, computed in the one place
        (`inbox_sections`) the page renders from, so the top bar's number and the page cannot
        disagree. Both poll this: the pushed stream carries session records only, and the person
        inbox belongs to none. The read marks nothing (`person_inbox`, above).

        From TD-069 step 2 the **state rows** ride this poll too, rendered from a fresh `list`
        (`person_states`): the Org's pushed stream is per-record and this page is per-person, so
        the simplest correct thing is one snapshot per poll — a row whose state changed in between
        is corrected by the next one, and a permission answered here leaves at once because the
        press refreshes."""
        try:
            got, states = await person_view()
        except HTTPException as e:
            if e.status_code != 503:
                raise
            # The page's own load already answers a host agent that is down with a banner; its
            # **poll** answered a bare 503, which the client could only drop on the floor — the
            # rows would sit there looking current (design §4.5 *there is no silent failure path*,
            # TD-069's leftover). So the poll says it in a shape the page can render: no entries,
            # no counts claimed, and the reason in words.
            return {
                "entries": [],
                "sections": {k: [] for k in INBOX_SECTIONS},
                "needs": None,  # not zero: nothing is *known* to be waiting, which is not *nothing is*
                "fyi_n": None,  # the same rule for the second number: not known is not zero
                "overdue_n": None,  # …and the rollup's overdue count (TD-178)
                "snoozed_n": 0,
                "answered_marks": None,  # not known either: the team headers keep what they showed
                "html": {},
                "unread": None,
                "agent_down": True,
                "why": str(e.detail),
            }
        boards, board_note = await board_items()
        sections = inbox_sections(
            got["entries"],
            states=states,
            trail=got.get("trail") or (),
            attention_snoozed=got.get("attention_snoozed"),
            boards=boards,
        )
        got["agent_down"] = False
        got["board_note"] = board_note
        got["sections"] = {k: [e["id"] for e in sections[k]] for k in INBOX_SECTIONS}
        got["needs"] = sections["count"]
        # §4.10 *The Inbox is a queue*: FYI's own quiet number, **never added to the first** — the
        # first is *what needs you*. The page opens the section by itself when this is higher than
        # the browser last saw, which is what stops a folded FYI hiding mail nobody counted.
        got["fyi_n"] = sections["fyi_n"]
        got["overdue_n"] = sections["overdue_n"]  # the Org rollup's *m overdue* (TD-178)
        got["snoozed_n"] = len(sections["snoozed"])
        # §4.5a **team header** → *answered for you* count (§4.9b, TD-075): each row's team and
        # time, and nothing it says — the browser counts those newer than it last opened the group
        # (that memory is the browser's, as FYI's *new* mark is), so the home keeps no read state
        got["answered_marks"] = [{"team": e.get("team") or "", "at": e.get("at") or ""} for e in sections["answered"]]
        got["html"] = inbox_html(sections, page_origin(request))
        # the rail's *Teams* lines (§4.5 screen 6 *The rail*): a team appears or goes with its rows,
        # so the poll brings the group's markup as it brings the rows'; the script presses the lines
        # the URL picks and recounts every line from the rows on the page
        rail = rail_counts(rail_rows(sections), rail_picks({}))
        got["html"]["rail_teams"] = str(
            templates.get_template("inbox_rail.html").module.teams_group(rail, rail_picks({}))  # type: ignore[attr-defined]
        )
        return got

    # design §4.5a **Inbox row** controls (§4.10): each is a person's own act on their own inbox,
    # so each calls its RPC **caller-less** — a person is not a session, and every one of these is
    # refused to every session by the agent. The UI adds no rule of its own: a refusal comes back
    # as the toast every other RPC error on the page does.
    PERSON_ACTS = {"pause": "inbox_pause", "resume": "inbox_resume", "gowithit": "inbox_go_with_it"}

    @app.post("/api/settings/person")
    async def api_settings_person(request: Request):
        """A person's own settings from a page (design §5 `person:`, §4.4a *Settings, replicated*):
        today the Focus pane's **copy on select** toggle (§4.5a, TD-174), `{terminal: {copy_on_select}}`
        through `set_settings`, which validates it and refuses a session. The page's next settings
        read takes it up, so the choice survives a reload."""
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        term = body.get("terminal") if isinstance(body, dict) else None
        if (
            not isinstance(term, dict)
            or set(term) != {"copy_on_select"}
            or not isinstance(term["copy_on_select"], bool)
        ):
            raise HTTPException(400, "person: send {terminal: {copy_on_select: true|false}}")
        got = await call("set_settings", person={"terminal": term})
        h.settings_at["at"] = 0.0  # read it again before the next page is drawn
        return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})

    @app.post("/api/person/{action}")
    async def api_person_action(action: str, request: Request):
        """design §4.5a Org top bar **person inbox** → Reply and delete (§4.10): a person's reply
        lands in the sender's inbox (the agent addresses it to the entry's sender and closes its
        `ask`); delete removes the entry from the person inbox only — the sender keeps its copy.
        From 2026-09-19 (TD-069 step 1) the Inbox page's own controls join it: **Snooze** and
        **Unsnooze** (`inbox_snooze`, with and without an `until`), **Pause** / **Resume**, and
        **Go with it**."""
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if action in PERSON_ACTS or action == "snooze":
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, f"{action} needs the entry's id")
            if action == "snooze":
                # no `until` is the clear — *Unsnooze* on the page's snoozed list (§4.10 *Snooze*)
                until = str(body.get("until") or "").strip() or None
                got = await call("inbox_snooze", msg=ref, until=until)
            else:
                got = await call(PERSON_ACTS[action], msg=ref)
            return JSONResponse({"ok": True, **got})
        if action == "board":
            # design §4.5a **Due strip / Inbox board row** → **Snooze ▾** and **Done** on a board row
            # (§4.4 *Board write-back*, TD-069 step 3): the host agent edits the one line and commits
            # it in the repo's main checkout. The row hands back what the reader gave it — the board,
            # the line and its text — and the agent refuses the edit when that line has moved on.
            what = str(body.get("action") or "")
            if what == "reply":
                # §4.5a *Due strip / Inbox board row* → **Reply** (§4.4, TD-142): the person's words
                # appended to the item's own line by `board_reply`, which re-checks the line as an
                # edit does; `refs` are the reader's when it carries them (TD-142 slice 2)
                board, text = str(body.get("board") or ""), str(body.get("text") or "")
                reply = str(body.get("reply") or "").strip()
                try:
                    line = int(body.get("line"))
                except (TypeError, ValueError):
                    raise HTTPException(400, "a board reply names the item's line") from None
                if not board or not text or not reply:
                    raise HTTPException(400, "a board reply names the board, the item's text and the reply")
                refs = [str(r) for r in body.get("refs") or () if str(r).strip()]
                got = await call("board_reply", board=board, line=line, text=text, reply=reply, refs=refs)
                await board_items(fresh=True)
                return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})
            if what == "add":
                # §4.5a *Inbox row: FYI* → **Put on the board** (TD-140): the form's board, text and
                # Due, and the entry it comes from; the agent writes the one line, commits it, and
                # only then dismisses the entry — its refusal is the form's, drawn in place
                ref, board, text = (str(body.get(k) or "").strip() for k in ("msg", "board", "text"))
                due = str(body.get("due") or "").strip()
                if not ref or not board or not text or not due:
                    raise HTTPException(400, "Put on the board names the entry, the board, the text and a Due date")
                got = await call("board_edit", board=board, action="add", text=text, due=due, entry=ref)
                await board_items(fresh=True)
                return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})
            if what not in BOARD_ACTS:
                raise HTTPException(400, f"a board row's act is {' or '.join(BOARD_ACTS)}, not {what!r}")
            try:
                line = int(body.get("line"))
            except (TypeError, ValueError):
                raise HTTPException(400, "a board row's act names the item's line") from None
            board, text = str(body.get("board") or ""), str(body.get("text") or "")
            if not board or not text:
                raise HTTPException(400, "a board row's act names the board and the item's text")
            due = str(body.get("due") or "").strip() or None
            if what == "snooze" and not due:
                raise HTTPException(400, "a snooze names the new date, YYYY-MM-DD")
            got = await call("board_edit", board=board, line=line, text=text, action=what, due=due)
            await board_items(fresh=True)
            return JSONResponse({"ok": True, **(got if isinstance(got, dict) else {})})
        if action == "dismiss":
            # design §4.10 *The Inbox is a queue* (TD-079 step 2): **Dismiss** and **Dismiss all**.
            # A **list of ids**, because *Dismiss all* dismisses the entries **this browser has on
            # screen** — mail that arrived after the page was drawn is exactly what must not go
            # unseen — and the ids are the browser's, mail (`m-`) and trail (`t-`) alike. The agent
            # refuses an id that is still an open question, naming it; that comes back as the toast.
            raw = body.get("msg")
            ids = [str(x).strip() for x in (raw if isinstance(raw, list) else [raw]) if str(x or "").strip()]
            if not ids:
                raise HTTPException(400, "dismiss names the entries to dismiss, by id")
            got = await call("inbox_dismiss", msg=ids)
            return JSONResponse({"ok": True, **got})
        if action == "attention_snooze":
            # design §4.5a **Inbox row: state** / §4.10: a **state row's** snooze — `stalled?` and
            # unpushed work, the two that are not on the tool's clock. It is keyed on the record
            # **and the row kind**, because a session's permission and its stalled row are two rows
            # and snoozing one is not snoozing the other; no `until` is the clear (*Unsnooze*).
            sid, kind = str(body.get("id") or "").strip(), str(body.get("kind") or "").strip()
            if not sid or not kind:
                raise HTTPException(400, "a state row's snooze names the session and the row kind")
            got = await call("attention_snooze", id=sid, kind=kind, until=str(body.get("until") or "").strip() or None)
            return JSONResponse({"ok": True, **got})
        if action in ("promote", "clear_promote"):
            # design §4.5a **Inbox row: promote** (§6 *Promote*, TD-132 slice 3): **Promote** presses
            # the `promote` RPC with main's head, **Dismiss** on a failure row `clear_promote`. Both
            # are the person's own and the agent refuses them to a session; a refusal — the tree, a
            # run in flight, a failure standing — comes back in its words and is drawn in place.
            repo = str(body.get("repo") or "").strip()
            if not repo:
                raise HTTPException(400, f"{action} names the repo")
            got = await call(action, repo=repo)
            return JSONResponse({"ok": True, **got})
        if action == "suspend":
            # design §4.8a *An alarm's answers* (TD-077 a2): **Suspend** — a person's own act, and
            # refused to every session by the agent for the reason `identity_ack` is, turned around:
            # a session that could suspend could stop its rival. Caller-less, like every control on
            # this page; `why` is left to the agent, which composes it from the alarm's own words.
            who = str(body.get("id") or "").strip()
            if not who:
                raise HTTPException(400, "suspend names the session to suspend")
            got = await call("suspend", id=who)
            return JSONResponse({"ok": True, **got})
        if action == "identity_log":
            # design §4.5a **Inbox row: identity alarm** (§4.8a *An alarm's answers*, TD-077 b):
            # **Log TD** hands the record's alarms to the session that answers for it, as mail from
            # the person that owes an outcome, and clears the list. The agent picks the controller
            # and composes the words; the page names nobody. A controller that went between the
            # draw and the press is refused by the agent in words, which is the toast.
            who = str(body.get("id") or "").strip()
            if not who:
                raise HTTPException(400, "Log TD names the session whose alarms it hands on")
            got = await call("identity_log", id=who)
            return JSONResponse({"ok": True, **got})
        if action == "identity_ack":
            # design §4.5a **Inbox row: identity alarm** (§4.8a, TD-077 step 2): a person has seen
            # the alarms and decided what they were, so the list is cleared and the row leaves.
            # Caller-less like every other control here — the agent refuses it to every session,
            # and the log keeps every alarm, so acknowledging loses nothing.
            got = await call("identity_ack", id=str(body.get("id") or "").strip() or None)
            return JSONResponse({"ok": True, **got})
        if action == "reply":
            ref = str(body.get("reply_to") or "").strip()
            if not ref:
                raise HTTPException(400, "a reply names the entry it answers")
            # design §4.5a **Inbox row: suggested answers** (§4.10, TD-070): a press sends **the
            # index**, never the label. The text is looked up here, from the entry the home holds,
            # so a tampered DOM cannot make the person "say" something else under a given index —
            # and the RPC re-checks that the two agree, because this route is not the only caller.
            answer = body.get("answer")
            text = str(body.get("text") or "")
            if answer is not None:
                if isinstance(answer, bool) or not isinstance(answer, int):
                    raise HTTPException(400, "a suggested answer is picked by its index")
                held = next((e for e in (await call("inbox"))["entries"] if e.get("id") == ref), None)
                offered = suggested_answers(held or {})
                if not 0 <= answer < len(offered):
                    raise HTTPException(400, "that is not one of the suggested answers")
                text = offered[answer]
            got = await call("msg", text=text, kind="reply", reply_to=ref, answer=answer)
            return JSONResponse({"ok": True, "id": got["entry"]["id"], "delivered": got["delivered"]})
        if action == "unmail":
            ref = str(body.get("msg") or "").strip()
            if not ref:
                raise HTTPException(400, "delete needs the entry's id")
            got = await call("inbox_delete", msg=ref)
            # design §4.10 *Deleting is declining*: on an open `ask` or `steer` the entry is not
            # stripped — it closes as `declined`, the asker is told, and it stays for retention.
            return JSONResponse({"ok": True, "unread": got["unread"], "declined": bool(got.get("declined"))})
        raise HTTPException(404, f"no action {action}")


def _stream_routes(app: FastAPI, h: SimpleNamespace) -> None:
    """The two websockets: the events stream and the terminal."""
    call, render_card, seats_of, heads = h.call, h.render_card, h.seats_of, h.heads
    repo_facts = h.repo_facts

    # -- live state ------------------------------------------------------------------------------

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()

        async def gone() -> None:
            # The page never sends on this socket, so the next message is its disconnect. Without
            # this watch the handler noticed a closed tab only when the next event's send failed —
            # and uvicorn's shutdown waits on the handler, which is the UI's 40 s stop (TD-058).
            while (await ws.receive()).get("type") != "websocket.disconnect":
                pass

        async def stream(c: LocalClient) -> None:
            # A delta carries one record, but membership is read across records (design §4.8):
            # a card's *under* chip names its controllers, which live on other records. So the
            # loop keeps the fleet it has already been told about — seeded once, then updated
            # by the very deltas it is rendering — rather than re-listing per event.
            known: dict[str, dict[str, Any]] = {}
            with contextlib.suppress(Exception):
                known = {o["id"]: o for o in await call("list")}
            # the repo facts and the doing log (TD-176): seeded once, then kept by their own events,
            # so a session delta redraws the summaries without asking the agent again
            repos, doing = await repo_facts()
            acct_of: dict[str, str] = {}  # profile → the account chip it is drawn on (TD-122)
            with contextlib.suppress(Exception):
                for name, acc in usage_accounts(await call("usage")).items():
                    for p in acc["profiles"]:
                        acct_of[p["name"]] = name
            async for ev in c.subscribe():
                if ev.get("event") == "session":
                    s = ev["session"]
                    known[s["id"]] = s
                    v = view(
                        s,
                        list(known.values()),
                        icons=await role_icons([s]),
                        seats=await seats_of([s]),
                        repos=repos,
                    )
                    compact_in(v, known.values())
                    # `groups` rides on every delta (design §4.5a **team groups**): a badge or a
                    # `controllers` change on one record can move a card, change a lead, or turn
                    # grouping on or off for the whole page, and only the server sees the fleet.
                    await ws.send_text(
                        json.dumps(
                            {
                                "event": "session",
                                "id": s["id"],
                                "state": s["state"],
                                "rank": v["rank"],  # the view's: unseen idle sorts above idle
                                "html": render_card(v),
                                "session": v,
                                **await heads(known, repos, doing),
                            }
                        )
                    )
                elif ev.get("event") in ("repos", "doing"):
                    # A checkout's repo facts changed (design §4.4 *Repo facts*, TD-176), or a team
                    # member said what it is doing (§4.8 *the doing log*): kept here, passed through
                    # as the agent sent it (`repo: null` for a checkout the registry dropped), and
                    # the groups re-sent, so the team cards' summaries redraw (TD-176 slice 3).
                    if ev["event"] == "repos":
                        if ev.get("repo") is None:
                            repos.pop(str(ev.get("root")), None)
                        else:
                            repos[str(ev.get("root"))] = ev["repo"]
                    elif isinstance(ev.get("entry"), dict):
                        ring = doing.setdefault(str(ev.get("team")), [])
                        ring.append(ev["entry"])
                        del ring[:-DOING_KEPT]
                    await ws.send_text(json.dumps(ev))
                    await ws.send_text(json.dumps({"event": "groups", **await heads(known, repos, doing)}))
                elif ev.get("event") in ("gone", "usage"):
                    if ev.get("event") == "gone":
                        # only a `gone` names a session; a `usage` event carries a profile, and
                        # popping on it would one day evict a live session by coincidence
                        went = str(ev.get("id") or "")
                        known.pop(went, None)
                        await ws.send_text(json.dumps({**ev, **await heads(known, repos, doing)}))
                        # A card's *under* chip names another record, so the session that went
                        # is not the only card now out of date: every card listing it as a
                        # controller has to be redrawn, or it keeps naming and linking to a
                        # session that is gone until the page is reloaded (review 2026-09-13).
                        for other in list(known.values()):
                            if went in (other.get("controllers") or []):
                                ov = view(
                                    other,
                                    list(known.values()),
                                    icons=await role_icons([other]),
                                    seats=await seats_of([other]),
                                    repos=repos,
                                )
                                compact_in(ov, known.values())
                                await ws.send_text(
                                    json.dumps(
                                        {
                                            "event": "session",
                                            "id": other["id"],
                                            "state": other["state"],
                                            "rank": ov["rank"],
                                            "html": render_card(ov),
                                            "session": ov,
                                        }
                                    )
                                )
                        continue
                    # The agent pushes a profile's reading; the page draws **one chip per account**
                    # (§4.5a **usage**, TD-122), with the lowest line among the account's profiles and
                    # each profile's sessions on hover — so the account is regrouped here from the
                    # whole reading, the gate's lines and the fleet this loop already keeps. A profile
                    # that went (`usage: null`) redraws the account it was on, or takes its chip off.
                    prof = str(ev.get("profile"))
                    was = acct_of.pop(prof, prof)
                    readings: dict[str, Any] = {}
                    with contextlib.suppress(Exception):
                        readings = await call("usage")
                    with contextlib.suppress(Exception):
                        readings = with_lines(readings, await call("gate"))
                    grouped = usage_accounts(readings, list(known.values()))
                    for name, acc in grouped.items():
                        for p in acc["profiles"]:
                            acct_of[p["name"]] = name
                    now = acct_of.get(prof)
                    for name in {was, now} - {None}:
                        await ws.send_text(json.dumps({"event": "usage", "account": name, "usage": grouped.get(name)}))

        try:
            async with LocalClient() as c:
                tasks = {asyncio.ensure_future(stream(c)), asyncio.ensure_future(gone())}
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                for t in done:
                    t.result()  # the stream's own failure is handled below, as it always was
        except (WebSocketDisconnect, AgentUnavailable, ConnectionError):
            pass
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps({"event": "error", "text": "events stream failed; reconnecting"}))
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    # -- terminal --------------------------------------------------------------------------------

    @app.websocket("/term/{sid}")
    async def term(ws: WebSocket, sid: str):
        # Size comes from the query, leniently: a bad or missing value falls back to a default
        # instead of a 403 before accept, which a browser can only report as an opaque 1006
        # (first-use finding 2026-09-06). The client re-sends its real size on open anyway.
        cols = _int_param(ws.query_params.get("cols"), 120, 10, 500)
        rows = _int_param(ws.query_params.get("rows"), 32, 2, 200)
        await ws.accept()
        try:
            s = await call("get", id=sid)
        except HTTPException as e:
            await ws.send_bytes(f"\r\n[agentorc] {e.detail}\r\n".encode())
            await ws.close(code=4404)  # final: the client must not retry
            return
        sid = sid.removesuffix(f"@{host_name()}")  # a self-addressed id is this host's tmux session
        sock = os.environ.get("AGENTORC_TMUX_SOCKET")
        inside: list[str] = []  # the `docker exec` prefix every tmux command takes for a container node's session
        argv = attach_argv(sid, socket_name=sock)
        if s.get("host") and s["host"] != host_name():
            # Another host's session (design §4.4a "Reach"): a container node on this machine is
            # reached by `docker exec` into it, from what the home derived when it dialed in; a
            # machine node's pane has nothing here that reaches it yet.
            reach = (s.get("host_link") or {}).get("reach") or {}
            if not reach.get("container"):
                await ws.send_bytes(
                    f"\r\n[agentorc] runs on {s['host']}: no terminal reaches it from here "
                    "(a container node's reach comes with its link; a machine node's waits for the terminal "
                    "over the link, TD-057 *Later*).\r\n".encode()
                )
                await ws.close(code=4404)
                return
            sid, sock = naming.split_address(sid)[0], None  # the bare id, on the user's default server inside
            argv = attach_argv_in(reach["container"], reach.get("user") or "root", sid)
            inside = argv[: argv.index("tmux")]
        if s.get("state") == "closed" or not s.get("pane", True):  # no pane to attach (TD-023)
            await ws.send_bytes(b"\r\n[agentorc] this session's pane is gone (see the banner).\r\n")
            await ws.close(code=4404)
            return
        try:
            pty = PtySession(argv, cols=cols, rows=rows)
        except Exception as e:  # noqa: BLE001 — no silent failure path (design §4.5)
            await ws.send_bytes(f"\r\n[agentorc] could not attach a terminal: {type(e).__name__}: {e}\r\n".encode())
            await ws.close()
            return

        # design §4.6 *A read-only attach* (TD-096): the mode is read once, here, and a read-only attach
        # tells the page so in its first frame — a text frame, which is the page's to read and not
        # pane output, so it neither counts as a painted screen nor resets the page's backoff. An
        # attach that sends none takes keys. A mode change re-attaches.
        read_only = bool(s.get("unattended"))
        if read_only:
            await ws.send_text(json.dumps({"read_only": True}))
        produced = False

        async def send(data: bytes) -> None:
            nonlocal produced
            produced = True
            await ws.send_bytes(data)

        async def recv() -> Any:
            msg = await ws.receive()  # a disconnect arrives as a message, never as an exception here
            if msg.get("type") == "websocket.disconnect":
                return None
            if msg.get("bytes") is not None:
                return msg["bytes"]
            text = msg.get("text") or ""
            if text.startswith("{"):
                with contextlib.suppress(ValueError):
                    return json.loads(text)
            return text

        reapers: set[asyncio.Future[int]] = set()

        async def scroll(direction: str, lines: int | None = None) -> None:
            # A tmux command against the session, not keys into the pane: there is no escape
            # sequence that enters copy mode (TD-022). The wheel's `lines` scroll lines, their absence
            # a page (TD-174). A bad direction or count is the client's bug; ignore.
            try:
                argv = [*[a for a in inside if a != "-it"], *scroll_argv(sid, direction, lines, socket_name=sock)]
            except ValueError:
                return
            devnull = asyncio.subprocess.DEVNULL
            proc = await asyncio.create_subprocess_exec(*argv, stdout=devnull, stderr=devnull)
            # Reap in the background: waiting here would hold the key pump behind a slow tmux.
            reapers.add(asyncio.ensure_future(proc.wait()))

        try:
            await pump(pty, send, recv, scroll, read_only=read_only)
        finally:
            for f in reapers:
                f.cancel()
            # `pump` has already closed the pty, so the child is reaped and its status is final:
            # a `tmux attach` that exited on its own carries its code, and one the teardown
            # signalled carries None, which is the retryable case (TD-029).
            status = pty.exit_status()
            with contextlib.suppress(Exception):
                if status not in (0, None) or not produced:
                    # A dead attach is final (TD-029): `tmux attach` exits non-zero when its session
                    # is gone, and an attach that never painted a screen attached to nothing. Either
                    # way the record is behind — retrying twice a second would print tmux's "can't
                    # find session" forever, which is what Paul saw on 2026-09-10.
                    await ws.send_bytes(
                        b"\r\n[agentorc] this session's pane is gone (the attach ended immediately).\r\n"
                    )
                    await ws.close(code=4404)
                else:
                    await ws.close()


app = create_app()


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    from agentorc.service import DEFAULT_BIND, DEFAULT_PORT

    ap = argparse.ArgumentParser(prog="agentorc-ui")
    ap.add_argument("--bind", default=DEFAULT_BIND, help="address to listen on (design §4.5: never the LAN)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    # lifespan="off": the app has no startup/shutdown handlers, and with lifespan on, Ctrl+C makes
    # uvicorn log a CancelledError traceback from starlette's lifespan task (seen 2026-09-06).
    uvicorn.run(
        "agentorc.ui.app:app", host=args.bind, port=args.port, log_level="info", ws_ping_interval=20, lifespan="off"
    )
    return 0


async def _wait_agent() -> bool:
    for _ in range(20):
        if paths.socket_path().exists():
            return True
        await asyncio.sleep(0.25)
    return False
