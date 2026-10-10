"""`ao doctor`'s verdicts (design §4.7 **`ao doctor`**, TD-465 slice 2): the client's judgement of
the host agent's `doctor` reading, the promote's build line and `ao org check`'s reading — seven
checks, in order, each line in `ao org check`'s words (*ok*, *warning*, *lacking*), every lack
naming its cure, since the command repairs nothing. Pure: the readings come in, the lines go out,
so each verdict is tested against a faked reading."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

CHECKS = ("agent", "tmux", "hooks", "identity", "profiles", "nodes", "org")
# the checks that read the home: on a node they say *home only* and count as neither (§4.7)
HOME_ONLY = frozenset({"nodes", "org"})
# a queued hook line older than this the tick should have applied: it drains within a tick
QUEUE_STALE = 60.0
# how long a probe waits for its scratch launch's first hook (§4.7 `--probe`)
PROBE_WAIT = 30.0
# a session just launched is scraped until its first hook lands: no warning inside this
FIRST_HOOK = 60.0

OK, WARNING, LACKING, HOME = "ok", "warning", "lacking", "home only"


def _row(check: str, verdict: str, text: str, **raw: Any) -> dict[str, Any]:
    return {"check": check, "verdict": verdict, "text": text, **raw}


def _ago(iso: str | None, now: datetime) -> str | None:
    """How long ago an instant was, in a person's words (*12s*, *40 min*, *3h*, *2d*); None unread."""
    secs = _age_s(iso, now)
    return None if secs is None else _words(secs)


def _age_s(iso: str | None, now: datetime) -> float | None:
    try:
        return max((now - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return None


def _words(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _since(iso: str | None) -> str:
    """A server's start as *Tue 07:02*, in local time."""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().strftime("%a %H:%M")
    except (TypeError, ValueError):
        return "an unread start"


def agent(
    version: str,
    promote: dict[str, Any] | None,
    ahead: dict[str, Any],
    node: bool,
    watch: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The host agent answers (it did, or nothing below was read), its version, and this repo's live
    build against `main`: the promote's reading when the repo carries one (`host.promotes`, §6
    *Promote*), else the running build against `origin/main` in its source (`build.ahead`). Behind
    is a warning: the policy may hold on purpose (§6 *A rollback*). At the home the line also says
    whether the watch stands (§4.10 *When the home itself is down*, TD-497): its timer and last run
    on the line, or a warning of its own naming the press that installs it."""
    rows = _build(version, promote, ahead, node)
    if node or watch is None:
        return rows
    if watch.get("loaded") and watch.get("active") == "active":
        rows[0]["text"] += f" · watch: {WATCH_TIMER}, last run {_hhmm(watch.get('last'))}"
        rows[0]["watch"] = watch
        return rows
    why = "no watch timer" if not watch.get("loaded") else f"the watch timer is {watch.get('active') or 'inactive'}"
    text = (
        f"agent — {why}: nothing outside the user manager tells you the host agent is down "
        "(`sudo ao service install --system`)"
    )
    return [*rows, _row("agent", WARNING, text, watch=watch)]


WATCH_TIMER = "agentorc-watch.timer"
_CLOCK = re.compile(r"\b(\d\d:\d\d):\d\d\b")


def _hhmm(last: Any) -> str:
    """systemd's `LastTriggerUSec` (*Fri 2026-10-09 14:35:02 BST*) as *14:35*; *none yet* before the first."""
    m = _CLOCK.search(str(last or ""))
    return m.group(1) if m else "none yet"


def _build(version: str, promote: dict[str, Any] | None, ahead: dict[str, Any], node: bool) -> list[dict[str, Any]]:
    if node:
        return [_row("agent", HOME, f"agent — {version}; the build against main is read at the home")]
    if promote:
        live, main = str(promote.get("live") or "")[:7], str(promote.get("main") or "")[:7]
        auto = "auto " + ("on" if promote.get("auto") else "off") + (" · held" if promote.get("held") else "")
        if live and live == main:
            return [_row("agent", OK, f"agent — {version}, live {live} = main", promote=promote)]
        if not live or not main:
            why = promote.get("live_why") if not live else promote.get("main_why")
            text = f"agent — {version}, live {live or '?'}, main {main or '?'}: not read ({why or 'unknown'})"
            return [_row("agent", WARNING, text, promote=promote)]
        n = promote.get("ahead")
        behind = f", {n} behind" if isinstance(n, int) else ""
        text = f"agent — live {live}, main {main}{behind} · {auto}"
        return [_row("agent", WARNING, text, promote=promote)]
    if "ahead" not in ahead:
        text = f"agent — {version}: cannot compare with main ({ahead.get('why')})"
        return [_row("agent", WARNING, text, build=ahead)]
    if ahead["ahead"]:
        n = ahead["ahead"]
        text = f"agent — {version}, main {n} commit{'' if n == 1 else 's'} ahead: not live until the next promote"
        return [_row("agent", WARNING, text, build=ahead)]
    return [_row("agent", OK, f"agent — {version}, live = main", build=ahead)]


def tmux(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The server against the one the agent first read, and where it runs (§4.1)."""
    pid = r.get("pid")
    if not pid:
        return [_row("tmux", LACKING, "tmux — no server", tmux=r)]
    if r.get("replaced"):
        was = (r.get("first") or {}).get("pid")
        text = f"tmux — server replaced under the agent: was {was}, now {pid} (restart the host agent)"
        return [_row("tmux", LACKING, text, tmux=r)]
    since = _since(r.get("started"))
    if r.get("runs") == "user":
        text = (
            f"tmux — server {pid} under the user manager ({r.get('cgroup')}): a stop of `systemd --user` "
            "ends every session; `ao service install` prints the system unit"
        )
        return [_row("tmux", WARNING, text, tmux=r)]
    if r.get("runs") == "system":
        unit = Path(str(r.get("cgroup") or "")).name
        return [_row("tmux", OK, f"tmux — server {pid} since {since}, system unit {unit}", tmux=r)]
    return [_row("tmux", OK, f"tmux — server {pid} since {since}, not under systemd ({r.get('cgroup')})", tmux=r)]


def hooks(r: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Each profile's layers name a hook command that resolves; each live agent session is fed by
    hooks; the queue holds nothing the tick should have applied."""
    out: list[dict[str, Any]] = []
    cure = "(the next launch rewrites it; `agentorc-hook` must be on PATH or beside the interpreter)"
    for p in r.get("layers") or []:
        for layer in p.get("layers") or []:
            name = Path(str(layer.get("path"))).name
            if layer.get("error"):
                text = f"hooks — layer {name} unreadable: {layer['error']} {cure}"
                out.append(_row("hooks", LACKING, text, layer=layer))
                continue
            cmds = layer.get("commands") or []
            if cmds and not any(c.get("resolves") for c in cmds):
                text = f"hooks — layer {name} names no hook command that resolves {cure}"
                out.append(_row("hooks", LACKING, text, layer=layer))
            elif bad := [c["command"] for c in cmds if not c.get("resolves")]:
                # some resolve: the layer still fires hooks, and the one that does not may be a
                # status line the person put there, so a warning naming it rather than a lack
                text = f"hooks — layer {name} names {', '.join(bad)}, which does not resolve"
                out.append(_row("hooks", WARNING, text, layer=layer))
    sessions = r.get("sessions") or []
    for s in sessions:
        young = (_age_s(s.get("since"), now) or 0.0) < FIRST_HOOK
        if s.get("confidence") != "hook" and not (young and not s.get("last_hook")):  # not a launch's first seconds
            text = f"hooks — {s['id']} scraped for {_ago(s.get('since'), now) or 'an unread time'}"
            out.append(_row("hooks", WARNING, text, session=s))
    q = r.get("queue") or {}
    if q.get("lines") and (q.get("written") or 0) > QUEUE_STALE:
        n = q["lines"]
        text = (
            f"hooks — {n} queued hook line{'' if n == 1 else 's'} not applied, newest {int(q['written'])}s ago "
            "(the tick is not draining it: restart the host agent)"
        )
        out.append(_row("hooks", LACKING, text, queue=q))
    if out:
        return out
    fed = [s for s in sessions if s.get("last_hook")]
    newest = min((a for s in fed if (a := _age_s(s["last_hook"], now)) is not None), default=None)
    n = len(sessions)
    text = f"hooks — {n} session{'' if n == 1 else 's'} fed by hooks" if n else "hooks — no live agent session"
    if newest is not None:
        text += f", newest {_words(newest)} ago"
    return [_row("hooks", OK, text, hooks=r)]


def probe(profile: str, took: float | None, tail: list[str], wait: float = PROBE_WAIT) -> dict[str, Any]:
    """A probe's line (`--probe`): the scratch launch's first hook and how long it took, or none in
    the wait, with the pane's last lines under it."""
    if took is not None:
        return _row("hooks", OK, f"hooks — probe {profile}: SessionStart in {took:.1f}s", probe=profile, took=took)
    last = "".join(f"\n    {t}" for t in tail if t.strip())
    return _row("hooks", LACKING, f"hooks — probe {profile}: no hook in {int(wait)}s{last}", probe=profile, tail=tail)


def probe_failed(profile: str, what: str, why: str) -> dict[str, Any]:
    return _row("hooks", LACKING, f"hooks — probe {profile}: {what}: {why}", probe=profile)


def probe_left(profile: str, sid: str, why: str) -> dict[str, Any]:
    """A probe whose record could not be killed or removed: the person cleans it up by name."""
    text = f"hooks — probe {profile}: {sid} not cleaned up ({why}): `ao kill {sid}`, then `ao forget {sid}`"
    return _row("hooks", WARNING, text, probe=profile, id=sid)


def identity(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The mode, the detached-process check and the alarms standing (§4.8a)."""
    n = len(r.get("alarms") or []) + sum(len(a) for a in (r.get("sessions") or {}).values())
    check = "on" if r.get("detached_check") else "off"
    if n:
        text = f"identity — {n} alarm{'' if n == 1 else 's'} standing (`ao identity`)"
        return [_row("identity", WARNING, text, identity=r)]
    return [_row("identity", OK, f"identity — {r.get('mode')}, detached-process check {check}", identity=r)]


def _windows(usage: dict[str, Any]) -> str:
    return " · ".join(f"{w.get('label')} {round(float(w.get('pct') or 0))}%" for w in usage.get("windows") or [])


def profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each profile: its credentials, then its usage endpoint's answer; a metered profile is never
    polled, only its key read (§4.2a). The login cure is the adapter's own words (`login`), since
    the core names no tool."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if "profile" not in row:
            out.append(_row("profiles", LACKING, f"profiles — {row.get('error') or 'unreadable'}", profile=row))
            continue
        name = row["profile"]
        login = f"log in: `{row['login']}`" if row.get("login") else f"log in under {row.get('config_dir')}"
        if row.get("metered"):
            if row.get("key"):
                out.append(_row("profiles", OK, f"profile {name} — metered, key set", profile=row))
            else:
                out.append(_row("profiles", LACKING, f"profile {name} — metered, no key", profile=row))
            continue
        if row.get("credentials") is None:
            out.append(_row("profiles", LACKING, f"profile {name} — no credentials ({login})", profile=row))
            continue
        if row.get("credentials") is False:
            out.append(_row("profiles", LACKING, f"profile {name} — credentials expired ({login})", profile=row))
            continue
        u = row.get("usage")
        if u is None:  # an adapter with no usage endpoint
            out.append(_row("profiles", OK, f"profile {name} — credentials good", profile=row))
        elif u.get("cooling") or u.get("reason") == "rate_limited":
            wait = u.get("cooling") or u.get("retry_after")
            retry = f", retry in {int(wait)}s" if wait else ""
            out.append(_row("profiles", WARNING, f"profile {name} — usage rate-limited{retry}", profile=row))
        elif u.get("windows"):
            text = f"profile {name} — credentials good, usage {_windows(u)}"
            out.append(_row("profiles", OK, text, profile=row))
        elif u.get("reason") == "no_credentials":
            out.append(_row("profiles", LACKING, f"profile {name} — no credentials ({login})", profile=row))
        else:
            why = f": {u['error']}" if u.get("error") else ""
            out.append(_row("profiles", LACKING, f"profile {name} — usage endpoint error{why}", profile=row))
    return out or [_row("profiles", OK, "profiles — none defined")]


def nodes(rows: list[dict[str, Any]], node: bool) -> list[dict[str, Any]]:
    """Each linked node: its link and, for a container node, its build against the home's wheel —
    `ao host status`' reading."""
    if node:
        return [_row("nodes", HOME, "nodes — read at the home")]
    if not rows:
        return [_row("nodes", OK, "nodes — none linked")]
    out: list[dict[str, Any]] = []
    for r in rows:
        name, link = r["node"], r.get("link") or {}
        if not link.get("up"):
            why = link.get("why") or "never linked"
            out.append(_row("nodes", LACKING, f"node {name} — link down: {why} (`ao host up {name}`)", node=r))
            continue
        b = r.get("build") or {}
        if b.get("home") and b.get("running") != b.get("home"):
            built = b.get("running") or "unknown"
            text = f"node {name} — build {built} behind the home's {b['home']} (`ao host rebuild {name}`)"
            out.append(_row("nodes", LACKING, text, node=r))
            continue
        build = f", build {b['running']} = home" if b.get("home") else ""
        out.append(_row("nodes", OK, f"node {name} — link up{build}", node=r))
    return out


def org(
    got: dict[str, Any] | None, files: dict[str, Any], teams: int, node: bool, why: str = ""
) -> list[dict[str, Any]]:
    """`ao org check`'s reading, a line per lack and warning, and whether `settings.yml` and
    `hosts.yml` parse, in the reader's own words."""
    out = [
        _row("org", LACKING, f"{name} — {f['error']}", file=f) for name, f in sorted(files.items()) if f.get("error")
    ]
    if node:
        return [*out, _row("org", HOME, "org — read at the home")]
    if got is None:
        return [*out, _row("org", LACKING, f"org — {why}")]
    out += [_row("org", LACKING, f"org — {line}") for line in got.get("lacks") or []]
    out += [_row("org", WARNING, f"org — {line}") for line in got.get("warnings") or []]
    return out or [_row("org", OK, f"org — {teams} team{'' if teams == 1 else 's'}")]


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`{ok, lacks, warnings, checks}` and the closing line: *n lacking, n warnings*, or *ok: n checks*,
    counting the checks that read something — a *home only* one counts as neither (§4.7)."""
    checks = len({r["check"] for r in rows if r["verdict"] != HOME})
    lacks = sum(r["verdict"] == LACKING for r in rows)
    warnings = sum(r["verdict"] == WARNING for r in rows)
    warned = f", {warnings} warning{'' if warnings == 1 else 's'}" if warnings else ""
    line = f"{lacks} lacking{warned}" if lacks else f"ok: {checks} check{'' if checks == 1 else 's'}{warned}"
    return {"ok": not lacks, "lacks": lacks, "warnings": warnings, "checks": rows, "line": line}


def line(r: dict[str, Any]) -> str:
    return f"{r['verdict']}: {r['text']}"
