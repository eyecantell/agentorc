"""`agentorc` / `ao`: a thin client of the host agent (design §4.7). Never touches tmux itself."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any

from agentorc import org as orgmod
from agentorc import repoconfig, teamrun, teams
from sessionorc import hosts, naming
from sessionorc.adapters import short_model
from sessionorc.client import AgentError, AgentUnavailable
from sessionorc.client import call_sync as _call_sync
from sessionorc.models import GRANTS, STATE_RANK, report_line, stop_note
from sessionorc.tmux import attach_argv


def _age(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "?"
    secs = int((datetime.now(UTC) - dt).total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def call_sync(method: str, **params: Any) -> Any:
    """Every RPC carries the calling session's id from `AGENTORC_SESSION` (design §4.7, §4.8;
    TD-028): that is how the agent tells a worker acting on another session from a person at a
    terminal. Unset outside a session, so nothing changes for a person."""
    return _call_sync(method, caller=os.environ.get("AGENTORC_SESSION") or None, **params)


def resolve(ident: str, here: pathlib.Path | None = None) -> str:
    """Design §4.1 (TD-030 step 5): every subcommand that takes an id also takes a bare **name**,
    resolved to the one session of that name here — this directory, or the repo it belongs to.
    A full `ao-…` id always means itself, so nothing that worked before changes. A live session
    wins over an exited one of the same name; anything else is an error, never a guess. `here`
    is the directory the name is read from (default: the cwd; `ao new -d` names another)."""
    if ident.startswith(naming.PREFIX):
        return ident
    here = (here or pathlib.Path.cwd()).resolve()
    named = [s for s in call_sync("list") if s.get("name") == ident and _is_here(s, here)]
    live = [s for s in named if s["state"] not in ("exited", "closed")] or named
    if len(live) == 1:
        return str(live[0]["id"])
    if not live:
        raise AgentError(f"no session named {ident} here — pass the full id, or `ao status` to see them")
    raise AgentError(f"{ident} is ambiguous here — {', '.join(sorted(s['id'] for s in live))}")


def _is_here(s: dict[str, Any], here: pathlib.Path) -> bool:
    """The session's scope covers the current directory: it runs here, under here, or in a repo
    this directory belongs to (which is how a name typed in the checkout finds its worktree)."""
    for key in ("dir", "repo"):
        if not s.get(key):
            continue
        p = pathlib.Path(str(s[key]))
        if p == p.parent:
            continue  # a session rooted at `/` is an ancestor of everything: not a scope
        if here == p or p in here.parents or here in p.parents:
            return True
    return False


def emit(args: argparse.Namespace, result: Any, prose: Callable[[], None]) -> int:
    """`--json` (TD-018, design §4.7): print the RPC result — the ids the next call needs — and
    nothing else on stdout; otherwise the human line(s)."""
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        prose()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sessions = call_sync("list")
    if args.json:
        print(json.dumps(sessions, indent=1))
        return 0
    if not sessions:
        print("no sessions")
        return 0
    sessions.sort(key=lambda s: (STATE_RANK.get(s["state"], 9), s["name"]))
    w = max(len(s["id"]) for s in sessions)
    for s in sessions:
        conf = "" if s["confidence"] == "hook" else " ~"
        pend = f"  ← {s['pending']['kind']}: {s['pending']['text']}" if s.get("pending") else ""
        mode = " [unattended]" if s.get("unattended") else ""
        print(f"{s['id']:<{w}}  {s['state']:<10}{conf:<3} {_age(s['since']):>4}  {s['adapter']}{mode}{pend}")
        if args.verbose:
            if s.get("capabilities"):
                print(f"{'':<{w}}      grants: {', '.join(s['capabilities'])}")
            if s.get("team"):
                print(f"{'':<{w}}      team:   {s['team']}")
            if s.get("project"):
                print(f"{'':<{w}}      project: {s['project']}")
            # Both directions of membership (design §4.8): what may act on this session, and — for
            # an orchestrator — what it may act on. The second is derived from the records here,
            # never stored, which is the same rule the Focus member list follows.
            if s.get("controllers"):
                print(f"{'':<{w}}      under:  {', '.join(s['controllers'])}")
            if members := [o["id"] for o in sessions if s["id"] in (o.get("controllers") or [])]:
                print(f"{'':<{w}}      members: {', '.join(members)}")
            if note := stop_note(s):
                print(f"{'':<{w}}      {note}")
            if model := short_model(s.get("adapter") or "", s.get("model")):
                print(f"{'':<{w}}      model:  {model}")
            if line := report_line(s):
                print(f"{'':<{w}}      report: {line}")
            if s.get("findings"):
                print(f"{'':<{w}}      filed:  {', '.join(_finding(f) for f in s['findings'])}")
            for line in (s.get("tail") or [])[-3:]:
                print(f"{'':<{w}}      │ {line}")
    return 0


def _attach(args: argparse.Namespace, sid: str, result: Any | None = None) -> int:
    """`ao focus` / `--attach`: run `tmux attach` on the session (the terminal equivalent of the
    Focus screen; TD-010 b) as a child, so a pane that went away since the record was read (a
    tick behind; `cmd_focus` refuses a known-gone pane before getting here) gets a clear line
    rather than tmux's. Under `--json` nothing is run: the argv is printed for the caller."""
    argv = attach_argv(sid, socket_name=os.environ.get("AGENTORC_TMUX_SOCKET"))
    if args.json:
        print(json.dumps({**(result or {"id": sid}), "attach": argv}, indent=1))
        return 0
    sys.stdout.flush()
    rc = subprocess.call(argv)
    if rc != 0:
        return fail(
            args, f"could not attach to {sid} (tmux exit {rc}): the pane is gone — killed, or the server restarted", 1
        )
    return 0


def cmd_focus(args: argparse.Namespace) -> int:
    s = call_sync("get", id=args.id)  # a clear error for an unknown id, not tmux's
    if s["state"] == "closed":
        return fail(args, f"{args.id} is closed; its pane is gone", 1)
    if not s.get("pane", True):  # killed, or the tmux server restarted (TD-023); a natural exit keeps its pane
        return fail(args, f"{args.id} has exited and its pane is gone (killed, or the tmux server restarted)", 1)
    return _attach(args, args.id, s)


def _project_block(project: str | None, cfg: repoconfig.RepoConfig, directory: pathlib.Path) -> str:
    """`ao new --project <name>`: the same reach block a team member gets, composed by
    `teams.reach_block` — the New session form's **Project** picker calls the very same function.
    An undefined name is a note on stderr and a badge, not a refusal (design §4.9)."""
    block, note = teams.reach_block(orgmod.load(), project or "", cfg.root or directory, hosts.local_host().name)
    if note:
        print(note, file=sys.stderr)
    return block


def _launch_defaults(args: argparse.Namespace) -> dict[str, Any]:
    """What the repo's `.agentorc.yml` and the `--role` preset fill in for `ao new` (design §4.8,
    §5): the brief from the role's template with `{lane}` filled, its lane, its grants (plus any
    `--grant`), its profile unless `--profile` says otherwise, and — when `--controller` is not
    given — the preset's `controllers:`, else the repo's, resolved from names to session ids here.
    The flags always win; the role's name and the repo's `ledger:` ride along on the record.
    `--project` adds §4.9's Project block to the brief, as a team start does for its members."""
    directory = pathlib.Path(args.dir or os.getcwd())
    try:
        if args.adapter == "shell":
            # a shell asks for nothing (§4.5a): no preset, no membership or ledger from the repo's
            # file — but the flags typed beside it (`--grant`, `--controller`) still mean themselves
            cfg, role = repoconfig.RepoConfig(), repoconfig.Role(name="")
        else:
            cfg = repoconfig.discover(pathlib.Path(args.repo) if args.repo else directory)
            # `org.yml`'s `roles:` is the overlay between the built-ins and the repo's file — the
            # profile precedence of §4.9, wired through `resolve_role`'s existing hook.
            overlay = orgmod.load().roles if args.adapter != "shell" else {}
            role = (
                repoconfig.resolve_role(cfg, args.role, overlay)
                if getattr(args, "role", None)
                else repoconfig.Role(name="")
            )
        lane = args.lane or list(role.lane)
        prompt = args.prompt or role.brief_text(lane)
        if block := _project_block(getattr(args, "project", None), cfg, directory):
            prompt = block + prompt if prompt else block
    except (KeyError, ValueError) as e:
        raise AgentError(str(e).strip('"')) from None
    controllers = list(args.controller or [])
    source = "--controller"
    if not args.controller:
        # `controllers_set` rather than a truth test: a preset that says `controllers: []` means
        # *nobody may act on this*, deliberately, and must not fall through to the repo's default.
        if role.controllers_set:
            controllers, source = role.controllers, f"role {role.name}"
        else:
            controllers, source = cfg.controllers, "repo"
    ids = []
    for c in controllers:
        try:
            ids.append(resolve(c, directory))
        except AgentError:
            if source == "--controller":
                raise  # the person typed it: an unknown session is theirs to hear about
            # A configured default naming a session that is not running is dropped, not an error
            # (design §4.8): a stale `controllers:` must not block every start in the repo. One line
            # per name; with none left, the no-controller line below says so as it always has.
            where = (cfg.path or pathlib.Path(repoconfig.FILE)).name
            print(f"controllers: `{c}` from {where} ({source}) is not running — skipped", file=sys.stderr)
    return {
        "profile": args.profile or role.profile or "",
        "prompt": prompt,
        "capabilities": list(dict.fromkeys([*role.grants, *(args.grant or [])])),
        "controllers": ids,
        "lane": lane,
        "role": role.name,
        "ledger": cfg.ledger if cfg.root else None,  # None for a shell: there is no repo file behind it
    }


def stop_time(when: str) -> str:
    """`--until` in the shapes a person types, as an absolute UTC instant (design §6, TD-026).

    `06:00` is the next 06:00 *here* — the host's local time, because that is the clock the person
    saying "stop at six" is reading; `+8h` / `+90m` / `+45s` is from now; anything else must be an
    ISO time, and one without a zone is read as local for the same reason. The agent only ever sees
    the instant: "next 06:00" is a question about the caller's clock, not the record's.
    """
    text = (when or "").strip()
    if not text:
        raise AgentError("--until: no time given")
    now = datetime.now().astimezone()
    if m := re.fullmatch(r"\+(\d+)\s*([smhd])", text, re.IGNORECASE):
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m[2].lower()]
        return _utc(now + timedelta(**{unit: int(m[1])}))
    if m := re.fullmatch(r"(\d{1,2}):(\d{2})", text):
        hour, minute = int(m[1]), int(m[2])
        if hour > 23 or minute > 59:
            raise AgentError(f"--until: not a time of day: {text}")
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return _utc(at if at > now else at + timedelta(days=1))  # today if it is still ahead
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentError(f"--until: not a time: {text} (try 06:00, +8h, or an ISO time)") from exc
    return _utc(parsed if parsed.tzinfo else parsed.astimezone())


def _utc(when: datetime) -> str:
    return when.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stop(args: argparse.Namespace) -> dict[str, str]:
    """`--until` and the words the agent will use when the time comes (design §6, TD-026).

    The wrap-up wording travels with the record because `sessionorc` must not know what a brief or a
    role is — the same reason `ledger` does. It is the one `ao team stop` sends, so a worker that
    runs out of time and a worker its lead wraps up are asked the same thing in the same words.
    """
    until = getattr(args, "until", None)
    if not until:
        return {}
    if not getattr(args, "unattended", False):
        # A stop time is a policy, and §4.2 says policies never touch an interactive session. Silently
        # storing one that nothing will ever act on is the failure this entry is about, inverted.
        raise AgentError("--until applies to unattended sessions: add --unattended, or leave it off")
    return {"run_until": stop_time(until), "wrapup_prompt": teams.WRAPUP_PROMPT}


def cmd_new(args: argparse.Namespace) -> int:
    defaults = _launch_defaults(args)
    s = call_sync(
        "create",
        name=args.name,
        dir=args.dir or os.getcwd(),
        adapter=args.adapter,
        repo=args.repo or (args.dir or os.getcwd() if args.worktree else None),
        worktree=args.worktree,
        unattended=args.unattended,
        resume=args.resume,
        **defaults,
        team=getattr(args, "team", None) or "",  # badges (design §4.9): plain strings, unvalidated
        project=getattr(args, "project", None) or "",
        **_stop(args),
    )
    if not s.get("controllers") and not args.json:
        # Design §4.8: an empty list is the explicit default, not an error — but an unattended
        # worker nobody may act on is rarely what was meant, so `ao new` says so once, here,
        # rather than leaving it to be discovered when a nudge is refused.
        print(f"{s['id']} starts with no controller: nobody may act on it (ao control <orc> add {s['name']})")
    if s.get("previous_run") and not args.json:
        # the same note the New session form shows before Start (design §4.1, TD-030)
        print(f"replaces the earlier {s['name']} — run log kept: {s['previous_run']}")
    if getattr(args, "attach", False):
        if not args.json:
            print(f"{s['id']}  ({s['adapter']}, {s['dir']})")
        return _attach(args, s["id"], s)
    return emit(args, s, lambda: print(f"{s['id']}  ({s['adapter']}, {s['dir']})\nattach: tmux attach -t {s['id']}"))


def cmd_shell(args: argparse.Namespace) -> int:
    args.adapter, args.profile, args.repo, args.unattended, args.resume, args.prompt = (
        "shell",
        "",
        None,
        False,
        None,
        None,
    )
    args.worktree = None
    args.grant, args.lane, args.controller, args.role = [], [], [], None
    args.name = args.name or ""  # the agent names it (`shell`, `shell-2`): one name, one session
    return cmd_new(args)


def cmd_roles(args: argparse.Namespace) -> int:
    """`ao roles` (design §4.7, §4.8): every preset that resolves in this repo — the package's
    built-ins and what the repo's `.agentorc.yml` redefines or adds — with where each came from."""
    try:
        cfg = repoconfig.discover(pathlib.Path(args.dir or os.getcwd()))
        found = repoconfig.roles(cfg, orgmod.load().roles)  # built-in < org.yml < the repo (§4.9)
    except ValueError as e:
        return fail(args, str(e), 1)
    result = {"file": str(cfg.path) if cfg.path else None, "controllers": cfg.controllers, "roles": []}
    result["roles"] = [r.to_dict() for r in found]

    def prose() -> None:
        print(f"roles from {cfg.path}" if cfg.path else f"roles: built-in only (no {repoconfig.FILE} under {cfg.root})")
        w = max(len(r.name) for r in found)
        for r in found:
            bits = [
                f"lane: {', '.join(r.lane) or '-'}",
                f"grants: {', '.join(r.grants) or 'none'}",
                f"profile: {r.profile or 'default'}",
                f"controllers: {', '.join(r.controllers or cfg.controllers) or 'nobody'}",
                f"brief: {r.brief or '-'}",
            ]
            print(f"{r.name:<{w}}  [{r.source}]  " + "  ".join(bits))

    return emit(args, result, prose)


# ── ao team (design §4.9) ─────────────────────────────────────────────────────────────────────


def _org_here(directory: pathlib.Path) -> orgmod.Org:
    """`~/.agentorc/org.yml`, plus a repo's own `teams:` when the command is run inside one — the
    org file wins a name collision (design §4.9). Read on every use and cached nowhere."""
    o = orgmod.load()
    cfg = repoconfig.discover(directory)
    if cfg.teams and cfg.root:
        o = orgmod.merge_repo_teams(o, cfg.root, cfg.teams)
    return o


def cmd_team_start(args: argparse.Namespace) -> int:
    """`ao team start <name>` (design §4.9): the sequence itself lives in `agentorc.teamrun`, which
    the Org page's **Teams** strip runs too, so the two cannot drift — every check before any
    create, the lead first, then each member with `controllers: [lead id]`. A live name holder
    refuses the whole start, so there is never half a team; an exited or closed holder is
    superseded, which makes this the restart too. What is left here is the terminal's half: the
    messages and the exit code."""
    directory = pathlib.Path(args.dir or os.getcwd())
    try:
        org = _org_here(directory)
    except ValueError as e:
        return fail(args, str(e), 1)
    try:
        p, result = teamrun.start(call_sync, org, args.name, hosts.local_host().name, profile=args.profile)
    except teamrun.NamesHeld as e:
        return fail(args, str(e), 1, holders=e.holders)
    except teamrun.PartialStart as e:
        for rec in e.created:
            print(_team_line(rec, args.name, e.plan), file=sys.stderr)
        return fail(args, f"{e} (above)", 1)
    except (teams.TeamError, ValueError) as e:
        return fail(args, str(e), 1)

    def prose() -> None:
        for rec in result["sessions"]:
            print(_team_line(rec, args.name, p))
        for name in result["out_of_reach"]:
            print(
                f"{name} is interactive, so {p.lead.name if p.lead else 'the lead'} cannot act on it "
                "(design §9 invariant 5): its controllers are recorded and take effect if you flip it "
                "to unattended",
                file=sys.stderr,
            )

    return emit(args, result, prose)


def _team_line(rec: dict[str, Any], team: str, p: teams.Plan) -> str:
    launch = next((x for x in p.launches if x.name == rec.get("name")), None)
    what = "lead" if launch and launch.lead else "member"
    role = f" {launch.role}" if launch and launch.role else ""
    return f"{rec['id']}  {what}{role}  {rec.get('dir', '')}"


def cmd_team_stop(args: argparse.Namespace) -> int:
    """`ao team stop <name>` (design §4.9): the wrap-up prompt — the one the card's **Wrap up**
    sends, `agentorc.teams.WRAPUP_PROMPT` — to each member, wait for each to go idle or the window
    to pass, then the lead. `--now` kills instead of asking. Both halves are `agentorc.teamrun`'s,
    shared with the Org page's strip; here they run in a row, because a terminal may wait."""
    directory = pathlib.Path(args.dir or os.getcwd())
    try:
        org = _org_here(directory)
        st = teamrun.stop_members(call_sync, org, args.name, now=args.now)
    except (teams.TeamError, ValueError) as e:
        return fail(args, str(e), 1)
    acted = teamrun.stop_lead(call_sync, st, timeout=args.timeout).acted
    result = {"team": args.name, "now": bool(args.now), "sessions": acted}

    def prose() -> None:
        for e in acted:
            state = f"  ({e['state']})" if e.get("state") and e["state"] != "?" else ""
            print(f"{e['id']}  {e['role']}: {e['action']}{state}")
        waiting = [e["id"] for e in acted if e.get("state") not in (*teamrun.SETTLED, "killed", None)]
        if waiting:
            print(f"still working when the {args.timeout:g}s window passed: {', '.join(waiting)}")

    return emit(args, result, prose)


def cmd_team_status(args: argparse.Namespace) -> int:
    """`ao team status <name>` (design §4.9): the lead's Members view for a terminal — each session
    carrying the badge with its state, lane and report line, the lead first, and every name the
    definition expects that is not running said to be so."""
    directory = pathlib.Path(args.dir or os.getcwd())
    org, expected = orgmod.Org(), []
    try:
        org = _org_here(directory)
        # the names the definition would start; a definition that cannot start (a checkout gone,
        # say) is not an error here — status reads what is running, and says what is not
        expected = [x.name for x in teams.plan(org, args.name, hosts.local_host().name).launches]
    except (teams.TeamError, ValueError):
        pass
    found = teamrun.badged(args.name, call_sync("list"))
    lead, members = teamrun.split(args.name, found, org)
    rows = [
        {**{k: s.get(k) for k in ("id", "name", "state", "lane", "role")}, "report": report_line(s), "running": True}
        for s in ([lead] if lead else []) + members
    ]
    rows += [
        {"id": None, "name": n, "state": "not started", "lane": [], "role": "", "report": "", "running": False}
        for n in expected
        if n not in {s.get("name") for s in found}
    ]
    if not rows:
        return fail(args, f"team {args.name}: no session carries the badge and no definition names one", 1)

    def prose() -> None:
        w = max(len(str(r["id"] or r["name"])) for r in rows)
        for r in rows:
            lane = f"  lane: {', '.join(r['lane'] or []) or '-'}"
            report = f"  report: {r['report']}" if r["report"] else ""
            print(f"{str(r['id'] or r['name']):<{w}}  {r['state']:<11}{lane}{report}")

    return emit(args, {"team": args.name, "sessions": rows}, prose)


def cmd_team_list(args: argparse.Namespace) -> int:
    """`ao team list` (design §4.9): every definition, its source file, and whether it is live — a
    team is live when any session carrying its badge is live. There is no team record: a team that
    is stopped is only its definition."""
    directory = pathlib.Path(args.dir or os.getcwd())
    try:
        org = _org_here(directory)
    except ValueError as e:
        return fail(args, str(e), 1)
    rows = teamrun.rows(org, call_sync("list"))

    def prose() -> None:
        if not rows:
            print(f"no team defined in {org.path}" + (f" or {directory}/{repoconfig.FILE}" if directory else ""))
            return
        w = max(len(r["name"]) for r in rows)
        for r in rows:
            live = f"{r['live']} live" if r["live"] else "stopped"
            print(
                f"{r['name']:<{w}}  {live:<8}  lead: {r['lead']}  members: {r['members']}  "
                f"projects: {', '.join(r['projects'])}  [{r['source']}]"
            )

    return emit(args, {"teams": rows}, prose)


def cmd_kill(args: argparse.Namespace) -> int:
    s = call_sync("kill", id=args.id)
    return emit(args, s, lambda: print(f"killed {args.id}"))


def cmd_close(args: argparse.Namespace) -> int:
    s = call_sync("close", id=args.id)
    return emit(args, s, lambda: print(f"closed {args.id}"))


def cmd_forget(args: argparse.Namespace) -> int:
    """The card's Forget (design §4.5a): drop an exited or closed record; the agent refuses a live one."""
    call_sync("remove", id=args.id)  # returns nothing: the record is gone, so no record to emit (unlike kill/close)
    return emit(args, {"id": args.id, "removed": True}, lambda: print(f"forgot {args.id}"))


def cmd_send(args: argparse.Namespace) -> int:
    text = " ".join(args.text) if args.text else sys.stdin.read()
    s = call_sync("send", id=args.id, text=text, wait=args.wait, timeout=args.timeout)
    if s:  # --wait: the settled record
        pend = f"  ← {s['pending']['kind']}: {s['pending']['text']}" if s.get("pending") else ""
        return emit(args, s, lambda: print(f"{s['id']}: {s['state']}{pend}"))
    return emit(args, {"ok": True, "id": args.id}, lambda: None)  # without --wait the RPC returns nothing


def cmd_keys(args: argparse.Namespace) -> int:
    """Raw tmux key names into the pane (Down, Enter, Escape, C-c, 1 …) — for dialogs the
    terminal owns when no browser is open. Not a menu-answering API: design §9 invariant 6."""
    call_sync("keys", id=args.id, keys=args.keys)
    return emit(args, {"ok": True, "id": args.id}, lambda: None)


def _print_explanation(x: dict[str, Any]) -> None:
    head = (
        f"{x['id']}  {x['state']} ({x['confidence']})"
        if x.get("id")
        else f"{x.get('file', 'fixture')}  ({x['adapter']} rules)"
    )
    if x.get("pending"):
        head += f"  ← {x['pending']['kind']}: {x['pending']['text']}"
    print(head)
    m = x.get("match")
    if m:
        pend = f"  ← {m['pending']['kind']}: {m['pending']['text']}" if m.get("pending") else ""
        print(f"rule: {m['rule']} → {m['state']}{pend}")
        for ln in m["evidence"]:
            print(f"  evidence │ {ln}")
    print(f"why: {x['reason']}")
    if x.get("tail"):
        print("screen:")
        for ln in x["tail"][-12:]:
            print(f"  │ {ln}")


def cmd_explain(args: argparse.Namespace) -> int:
    """`ao explain <id>`: the screen, the rule that fires on it and whether it applies (design §4.2,
    TD-015). `--file` classifies a saved screen with an adapter's rules — no agent needed."""
    if args.file:
        from sessionorc import adapters

        try:
            ad = adapters.get(args.adapter)
        except KeyError as e:
            return fail(args, str(e).strip('"'), 1)
        explain = getattr(ad, "explain", None)
        if explain is None:
            return fail(args, f"{args.adapter} has no screen rules", 1)
        try:
            tail = pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return fail(args, f"cannot read {args.file}: {e.strerror or e}", 1)
        m = explain(tail)
        x = {
            "file": args.file,
            "adapter": args.adapter,
            "match": m.to_dict() if m else None,
            "reason": f"rule {m.rule} matched" if m else "no screen rule matched",
            "tail": tail,
        }
        return emit(args, x, lambda: _print_explanation(x))
    if not args.id:
        return fail(args, "explain needs a session id, or --file", 2)
    x = call_sync("explain", id=args.id, lines=args.lines)
    return emit(args, x, lambda: _print_explanation(x))


def cmd_tail(args: argparse.Namespace) -> int:
    lines = call_sync("tail", id=args.id, lines=args.lines)
    return emit(args, lines, lambda: print("\n".join(lines)) if lines else None)


def cmd_mode(args: argparse.Namespace) -> int:
    s = call_sync("set_mode", id=args.id, unattended=args.mode == "unattended")
    return emit(args, s, lambda: print(f"{s['id']}: {'unattended' if s['unattended'] else 'interactive'}"))


def cmd_grants(args: argparse.Namespace) -> int:
    """`ao grant <id> orchestrate` / `ao revoke <id> orchestrate` (design §4.8): edit the record's
    `capabilities`; the agent applies it on the session's next call."""
    edit = {"add": args.grants} if args.cmd == "grant" else {"remove": args.grants}
    s = call_sync("set_grants", id=args.id, **edit)
    return emit(args, s, lambda: print(f"{s['id']}: grants {', '.join(s['capabilities']) or 'none'}"))


def cmd_until(args: argparse.Namespace) -> int:
    """`ao until <session> <when>` / `ao until <session> --clear` (design §6, TD-026): set or clear
    when an unattended session stops. Acting, so it is gated like `kill` — a stop time is a kill
    with a delay on it."""
    when = None if args.clear else stop_time(args.when or "")
    s = call_sync("set_stop", id=resolve(args.id), run_until=when, wrapup_prompt=teams.WRAPUP_PROMPT)
    return emit(args, s, lambda: print(f"{s['id']}: {stop_note(s) or 'no stop time'}"))


def cmd_control(args: argparse.Namespace) -> int:
    """`ao control <orc> add|remove <session>…` (design §4.8, TD-036): edit membership from the
    orchestrator's side, which is how a person thinks about it — *this orc controls these
    sessions* — while the list itself lives on each target. One `set_controllers` call per target,
    so a refusal names the session it refused and the rest still stand."""
    orc = resolve(args.orc)
    edit = "add" if args.action == "add" else "remove"
    done, refused = [], []
    for ident in args.sessions:
        try:
            # Every rule about *who may control what* lives in the agent (`rpc_set_controllers`,
            # `_gate`): a session may not control itself, may not edit its own list, needs the
            # grant and membership. The CLI re-implements none of them — a copy here would be the
            # one that goes stale the day the rule changes — it just reports what came back.
            done.append(call_sync("set_controllers", id=resolve(ident), **{edit: [orc]}))
        except AgentUnavailable:
            raise  # not a per-target refusal: `main` gives the agent-is-down message and exit 3
        except AgentError as e:
            refused.append({"session": ident, "error": str(e)})
    if args.json:
        # a list, not a dict keyed by name: the same name twice is two attempts and two answers
        print(json.dumps({"controller": orc, "action": edit, "sessions": done, "refused": refused}, indent=1))
    else:
        for s_ in done:
            under = ", ".join(s_["controllers"]) or "nobody"
            print(f"{s_['id']}: under {under}")
        for r in refused:
            print(f"{r['session']}: {r['error']}", file=sys.stderr)
    return 1 if refused else 0


def _finding(f: dict[str, Any]) -> str:
    pri = f" ({f['priority']})" if f.get("priority") else ""
    return f["ref"] + ("~" if f.get("source") != "declared" else "") + pri


def _own_session(args: argparse.Namespace) -> str | None:
    """Which record a report lands on: `--id` for another session's, otherwise this session's own
    (`AGENTORC_SESSION`). A person at a terminal with neither gets told, not guessed at."""
    sid = getattr(args, "id", None) or os.environ.get("AGENTORC_SESSION")
    if not sid:
        fail(args, "no session: run this inside an agentorc session, or pass --id <session>", 2)
    return sid


def cmd_progress(args: argparse.Namespace) -> int:
    """`ao progress claim|done|drop <ref>` (design §4.8): declare a lane item claimed before the
    first edit and its result before moving on. Ungated, and lands on this session's own record."""
    sid = _own_session(args)
    if sid is None:
        return 2
    status = {"claim": "claimed", "done": "done", "drop": "dropped"}[args.action]
    s = call_sync("progress", id=sid, ref=args.ref, status=status, pr=args.pr, why=args.why)
    return emit(args, s, lambda: print(f"{s['id']}: {report_line(s) or args.ref}"))


def cmd_finding(args: argparse.Namespace) -> int:
    """`ao finding <ref> [--priority …]` (design §4.8): a reference this session filed on the side."""
    sid = _own_session(args)
    if sid is None:
        return 2
    s = call_sync("finding", id=sid, ref=args.ref, priority=args.priority)
    return emit(args, s, lambda: print(f"{s['id']}: filed {', '.join(_finding(f) for f in s['findings'])}"))


def cmd_decide(args: argparse.Namespace) -> int:
    s = call_sync("get", id=args.id)
    pend = s.get("pending") or {}
    if pend.get("kind") != "permission" or not pend.get("tool_use_id"):
        msg = f"{args.id} has no pending permission"
        return fail(args, msg, 1, prose=msg)  # no "error:" prefix: the line main printed before --json
    call_sync("decide", id=args.id, tool_use_id=pend["tool_use_id"], behavior=args.behavior, reason=args.reason)
    result = {"ok": True, "id": args.id, "behavior": args.behavior, "tool_use_id": pend["tool_use_id"]}
    return emit(args, result, lambda: print(f"{args.behavior}: {pend['text']}"))


def cmd_service(args: argparse.Namespace) -> int:
    from agentorc import service

    if args.action == "install":
        written = service.install(bind=args.bind, port=args.port, start=not args.no_start)
        status = service.status()
        return emit(
            args,
            {"written": written, "status": status},
            lambda: print(
                "wrote " + ", ".join(written) + "\n" + status + "\n"
                "units run under your user; `loginctl enable-linger` keeps them (and tmux) alive after logout"
            ),
        )
    if args.action == "uninstall":
        service.uninstall()
        return emit(args, {"ok": True}, lambda: print("units disabled and removed (tmux sessions untouched)"))
    status = service.status()
    return emit(args, {"status": status}, lambda: print(status))


def fail(args: argparse.Namespace, message: str, code: int, prose: str | None = None, **extra: Any) -> int:
    """An error in the same shape as a success: `{"error": …, **extra}` on stdout under `--json`;
    otherwise `prose` (default `error: <message>` plus any `hint` line) on stderr. Same exit code."""
    if args.json:
        print(json.dumps({"error": message, **extra}))
    else:
        line = prose if prose is not None else f"error: {message}"
        print(line + (f"\n{extra['hint']}" if extra.get("hint") else ""), file=sys.stderr)
    return code


def cmd_ui(args: argparse.Namespace) -> int:
    from agentorc.ui.app import main as ui_main

    return ui_main(["--bind", args.bind, "--port", str(args.port)])


def skill_text() -> str:
    """The `ao` skill (design §4.7, TD-019): front matter + rules, shipped with the package so it can
    be printed anywhere `ao` runs — `ao --skill > .claude/skills/ao/SKILL.md` installs it in a repo."""
    return resources.files("agentorc").joinpath("skill.md").read_text(encoding="utf-8")


class _SkillAction(argparse.Action):
    def __init__(self, option_strings, dest, **kw):  # noqa: ANN001 — argparse's Action signature
        super().__init__(option_strings, dest, nargs=0, **kw)

    def __call__(self, parser, namespace, values, option_string=None):  # noqa: ANN001
        print(skill_text(), end="")
        parser.exit(0)


def _refs(value: str) -> list[str]:
    """`--lane TD-027,TD-019` → the list; the agent canonicalises each reference (design §4.8)."""
    return [r.strip() for r in value.split(",") if r.strip()]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ao", description="agentorc — sessions in tmux, one view")
    ap.add_argument("--json", action="store_true", help="print the RPC result as JSON (every subcommand; TD-018)")
    ap.add_argument(
        "--skill",
        action=_SkillAction,
        help="print the rules an agent driving ao from inside a session must follow (Markdown; TD-019)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, **kw: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kw)
        # SUPPRESS: a subparser default would otherwise overwrite the global flag's value
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the RPC result as JSON")
        return p

    p = add("status", help="list sessions on this host")
    p.add_argument("-v", "--verbose", action="store_true", help="show the last output lines")
    p.set_defaults(fn=cmd_status)

    p = add("new", help="start a session")
    p.add_argument("name")
    p.add_argument("-d", "--dir", help="directory (default: cwd)")
    p.add_argument("-a", "--adapter", default="claude-code")
    p.add_argument("-p", "--profile", help="tool · account · model profile name")
    p.add_argument("--repo", help="repo root when dir is a worktree")
    p.add_argument(
        "-w",
        "--worktree",
        help="run in <repo>/.claude/worktrees/NAME on branch NAME (created if missing); dir is the repo",
    )
    p.add_argument("--unattended", action="store_true")
    p.add_argument("--resume", help="the tool's session id to resume")
    p.add_argument("--prompt", help="opening prompt")
    p.add_argument(
        "--grant",
        action="append",
        choices=GRANTS,
        help="a grant the session starts with (design §4.8); `orchestrate` lets it act on other sessions",
    )
    p.add_argument(
        "--controller",
        action="append",
        metavar="SESSION",
        help="a session that may act on this one (design §4.8; repeatable). None means nobody may",
    )
    p.add_argument(
        "--lane",
        type=_refs,
        default=[],
        help="the references this session was handed, comma-separated (`TD-027,TD-019`) or `free-pick` (design §4.8)",
    )
    p.add_argument(
        "--role",
        help="a preset (design §4.8): fills the brief, lane, grants, profile and controllers; `ao roles` lists them",
    )
    p.add_argument("--team", help="the team this session is started under (design §4.9): a badge, nothing keys on it")
    p.add_argument(
        "--until",
        metavar="WHEN",
        help="when this unattended session stops (design §6, TD-026): 06:00 (the next one, local), "
        "+8h, or an ISO time. At it the session is asked to wrap up and is killed once it settles "
        "or ten minutes later — so a worker started by hand has a stopper without anyone remembering",
    )
    p.add_argument(
        "--project",
        help="the project it is started under (design §4.9): the badge, and the Project block in front of the brief "
        "naming each of the project's repos on this host when there is more than one",
    )
    p.add_argument("--attach", action="store_true", help="then attach this terminal to it (tmux attach)")
    p.set_defaults(fn=cmd_new)

    p = add("roles", help="list the role presets this repo resolves: built-in and from .agentorc.yml (design §4.8)")
    p.add_argument("-d", "--dir", help="the repo or directory to read (default: cwd)")
    p.set_defaults(fn=cmd_roles)

    p = add("team", help="start, stop, inspect and list the team definitions of §4.9")
    tsub = p.add_subparsers(dest="action", required=True)

    def add_team(name: str, **kw: Any) -> argparse.ArgumentParser:
        q = tsub.add_parser(name, **kw)
        q.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the RPC result as JSON")
        q.add_argument("-d", "--dir", help="where a repo's own `teams:` is read from (default: cwd)")
        return q

    q = add_team("start", help="launch a team: every check first, then the lead, then its members")
    q.add_argument("name")
    q.add_argument("-p", "--profile", help="a profile for every session in it, over the role's and the member's")
    q.set_defaults(fn=cmd_team_start)

    q = add_team("stop", help="wrap the members up, then the lead (--now kills instead of asking)")
    q.add_argument("name")
    q.add_argument("--now", action="store_true", help="kill each session instead of sending the wrap-up prompt")
    q.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for the members (default: 300)")
    q.set_defaults(fn=cmd_team_stop)

    q = add_team("status", help="each member of a live team with its state, lane and report line")
    q.add_argument("name")
    q.set_defaults(fn=cmd_team_status)

    q = add_team("list", help="every definition, its source file and whether it is live")
    q.set_defaults(fn=cmd_team_list)

    p = add("shell", help="start a plain shell session here")
    p.add_argument("name", nargs="?")
    p.add_argument("-d", "--dir")
    p.add_argument("--attach", action="store_true", help="then attach this terminal to it (tmux attach)")
    p.set_defaults(fn=cmd_shell)

    p = add("focus", help="attach this terminal to a session (the Focus screen, in tmux)")
    p.add_argument("id")
    p.set_defaults(fn=cmd_focus)

    for name, fn, help_ in (
        ("kill", cmd_kill, "kill a session (worktree kept)"),
        ("close", cmd_close, "close a session"),
        ("forget", cmd_forget, "drop an exited or closed record (the card's Forget)"),
    ):
        p = add(name, help=help_)
        p.add_argument("id")
        p.set_defaults(fn=fn)

    p = add("send", help="send a prompt (args or stdin)")
    p.add_argument("id")
    p.add_argument("text", nargs="*")
    p.add_argument(
        "--wait",
        action="store_true",
        help="return once the session has started on the prompt and settled again (idle, needs-you, exited); "
        "errors prompt-stalled / timeout (every send errors prompt-stuck if the composer keeps the text)",
    )
    p.add_argument("--timeout", type=float, help="seconds to wait for it to settle (default: no limit)")
    p.set_defaults(fn=cmd_send)

    p = add("keys", help="send raw tmux key names (Down Enter Escape C-c …) to a session")
    p.add_argument("id")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_keys)

    p = add("explain", help="why a session shows its state: screen, rule, evidence (or classify --file)")
    p.add_argument("id", nargs="?")
    p.add_argument("--file", help="a saved screen to classify with an adapter's rules instead of a live session")
    p.add_argument("-a", "--adapter", default="claude-code", help="whose rules, with --file")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(fn=cmd_explain)

    p = add("tail", help="last lines of a session's pane")
    p.add_argument("id")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(fn=cmd_tail)

    p = add("mode", help="flip a session between unattended and interactive")
    p.add_argument("id")
    p.add_argument("mode", choices=["unattended", "interactive"])
    p.set_defaults(fn=cmd_mode)

    p = add("until", help="set or clear when an unattended session stops (design §6, TD-026)")
    p.add_argument("id")
    p.add_argument("when", nargs="?", help="06:00 (the next one, local), +8h, or an ISO time")
    p.add_argument("--clear", action="store_true", help="remove the stop time: nothing will stop it")
    p.set_defaults(fn=cmd_until)

    for name, help_ in (
        ("grant", "give a session a grant: `orchestrate` lets it act on other sessions (design §4.8)"),
        ("revoke", "take a grant away from a session"),
    ):
        p = add(name, help=help_)
        p.add_argument("id")
        p.add_argument("grants", nargs="+", choices=GRANTS, metavar="grant")
        p.set_defaults(fn=cmd_grants)

    p = add("control", help="say which sessions an orchestrator may act on (design §4.8)")
    p.add_argument("orc", help="the orchestrating session (id or name)")
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("sessions", nargs="+", metavar="session", help="the sessions it controls (id or name)")
    p.set_defaults(fn=cmd_control)

    p = add("progress", help="declare a reference claimed, done, or dropped (design §4.8)")
    p.add_argument("action", choices=["claim", "done", "drop"])
    p.add_argument("ref", help="a ledger id (TD-027), a PR number, or an attention-board line")
    p.add_argument("--pr", help="the PR the work is on")
    p.add_argument("--why", help="why it was dropped")
    p.add_argument("--id", help="the session to report for (default: your own, from AGENTORC_SESSION)")
    p.set_defaults(fn=cmd_progress)

    p = add("finding", help="declare a reference this session filed on the side (design §4.8)")
    p.add_argument("ref")
    p.add_argument("--priority")
    p.add_argument("--id", help="the session to report for (default: your own, from AGENTORC_SESSION)")
    p.set_defaults(fn=cmd_finding)

    p = add("ui", help="serve the web UI (localhost by default; design §4.5 security)")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_ui)

    p = add("service", help="systemd user units for the agent and the UI (install | uninstall | status)")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--no-start",
        action="store_true",
        help="write and enable the units (start at next login/boot) without starting them now",
    )
    p.set_defaults(fn=cmd_service)

    for behavior in ("allow", "deny"):
        p = add(behavior, help=f"{behavior} the pending permission")
        p.add_argument("id")
        p.add_argument("reason", nargs="?")
        p.set_defaults(fn=cmd_decide, behavior=behavior)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if isinstance(getattr(args, "id", None), str) and args.id:
            args.id = resolve(args.id)  # a full id, or a bare name resolved here (TD-030 step 5)
        return args.fn(args)
    except AgentUnavailable as e:
        return fail(args, str(e), 3, hint="start it with: agentorc-agent serve")
    except AgentError as e:
        # the holder's id and state under --json (TD-030); `fail`'s own keywords are not overridable
        return fail(args, str(e), 1, **{k: v for k, v in e.data.items() if k not in ("prose", "code", "message")})


if __name__ == "__main__":
    sys.exit(main())
