"""The host agent: one process per host, the only writer to `ao-*` tmux sessions (design §4.4, §9).

JSON-lines RPC over a Unix socket: `{"id": n, "method": "...", "params": {...}}` →
`{"id": n, "result": ...}` or `{"id": n, "error": "..."}`. `subscribe` turns the connection into
a stream of `{"event": "session", "session": {...}}` / `{"event": "gone", "id": ...}` lines.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import adapters, hosts, naming, paths, reports
from sessionorc.gitinfo import WorktreeError, ensure_worktree, git_info
from sessionorc.models import (
    GRANTS,
    PROGRESS_STATUSES,
    SOURCES,
    FindingEntry,
    Pending,
    ProgressEntry,
    Session,
    State,
    normalize_ref,
    now_iso,
)
from sessionorc.store import EventQueue, SessionStore
from sessionorc.tmux import DuplicateSession, PaneInfo, Tmux

log = logging.getLogger("agentorc.agent")

TICK_SECONDS = float(os.environ.get("AGENTORC_TICK", "2"))
TAIL_LINES = 15  # cards show the last 3; the screen rules (TD-015) need the dialog above the options
CLOSED_KEEP = timedelta(days=1)
STALL_AFTER = timedelta(minutes=20)
GIT_EVERY = timedelta(seconds=10)  # git status per live session, cheap and cached
# Derived report entries per session (design §4.8, TD-028 step 3): a `gh` call and a little git, so
# a slow cadence. Nothing waits on it and a failure derives nothing (`sessionorc.reports`).
DERIVE_EVERY = timedelta(minutes=5)
CREATE_GRACE = timedelta(seconds=10)  # a pane snapshot older than a session cannot judge it
SEND_STALL_SECONDS = 5.0  # `send(wait=True)`: no sign of the prompt being taken within this → prompt-stalled
PASTE_SHOW_SECONDS = 1.0  # `send`: how long the pasted text gets to appear in the composer before Enter (TD-027)
SUBMIT_SECONDS = 1.5  # `send`: how long the composer gets to empty after Enter, per try (TD-027)
COMPOSER_LINES = 12  # raw rows an adapter's `composer` reads (the composer sits above a status line or two)
SETTLED = ("idle", "needs-you", "exited", "closed", "limited", "stalled?")  # where a `send(wait=True)` ends
# RPCs that act on a session (design §4.8): a caller that is a session needs the `orchestrate`
# grant to run one of these on a session other than itself (§9 invariant 11). `create` targets a
# session that is by definition not the caller; `set_grants` is gated so a session cannot grant
# itself. Reads are never listed here.
ACTING_RPCS = frozenset({"send", "keys", "kill", "close", "set_mode", "remove", "create", "set_grants"})
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption
PRUNE_EVERY = timedelta(hours=1)  # run-log retention sweep (design §4.6, `runs_keep_days`)
# a tool registry's status → our state (Claude Code: busy | idle | shell, the last a `!` command running)
EXTERNAL_STATES = {"busy": "working", "idle": "idle", "shell": "working"}
USAGE_EVERY = 60.0  # seconds between usage polls per profile (TD-001): a slow cadence, never per tick
REMOVED_GUARD_SECONDS = 60.0  # how long a removed session's name is checked against re-adoption


class RpcError(Exception):
    pass


class HostAgent:
    def __init__(
        self, *, tmux: Tmux | None = None, store: SessionStore | None = None, events: EventQueue | None = None
    ):
        paths.ensure_layout()
        self.tmux = tmux or Tmux()
        self.store = store or SessionStore()
        self.events = events or EventQueue()
        self.sessions: dict[str, Session] = self.store.load_all()
        for s in self.sessions.values():
            # `prompt` pendings (Claude's idle_prompt) stopped being an alert on 2026-09-06; a record
            # written before that would otherwise show needs-you until the next hook event.
            if s.pending and s.pending.kind == "prompt":
                s.set_state("idle", confidence="hook")
                self.store.save(s)
        self._dir_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # subscriber → what it was last sent, per session (TD-009: a new tab gets its own snapshot
        # without every other tab being re-sent everything)
        self._subscribers: dict[asyncio.StreamWriter, dict[str, str]] = {}
        self._gone: list[str] = []  # forgotten ids not yet announced (`_forget` → `_push_changes`)
        # (session id, tool_use_id) → the hook's pending decision
        self._waiters: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._git_checked: dict[str, datetime] = {}
        self._derived_at: dict[str, datetime] = {}
        self._derive_task: asyncio.Task[None] | None = None
        # when a hook last reported on a session: a screen-rule verdict never outranks a hook
        # state fresher than STALL_AFTER (design §4.2); a session no hook has reported on yet — the
        # trust dialog case — takes the classifier's verdict at once (TD-015)
        # Seeded on load: a hook-confirmed record was fed by a live hook stream until the agent
        # stopped, and `since` is a transition time, not a hook time — so count it fresh as of now.
        # A restart must never let the screen outrank a state a hook just reported.
        self._last_hook: dict[str, datetime] = {
            sid: datetime.now(UTC) for sid, s in self.sessions.items() if s.confidence == "hook"
        }
        # profile → last usage dict from its adapter (`usage_for`), and when it was last asked
        self._usage: dict[str, dict[str, Any]] = {}
        self._usage_checked: dict[str, float] = {}
        self._usage_task: asyncio.Task[None] | None = None
        self._pre_limited: dict[str, State] = {}  # what a `limited` session was before the cap
        # Sessions removed recently: name → (the removed pane's tmux creation time, a monotonic
        # stamp for expiry). A pane snapshot taken before the remove must not re-adopt the pane it
        # still lists (the tick snapshots in a thread; remove runs between). Keyed by the pane's
        # own creation time, not by clocks compared across a thread hop, so a clock step cannot
        # re-adopt a dead pane and a hand-made session that reuses the name is adopted at once
        # (TD-020, TD-021). `None` for the creation time: the pane was already gone at remove.
        self._removed: dict[str, tuple[int | None, float]] = {}
        self._pruned_at = datetime.min.replace(tzinfo=UTC)  # first tick sweeps
        # Read-only cards for sessions the adapters see outside agentorc (TD-010 a): rebuilt every
        # tick from `adapters.external_sessions()`, never stored, keyed `ext-<tool id>`.
        self._external: dict[str, Session] = {}

    # -- lifecycle ------------------------------------------------------------------------------

    async def serve(self, sock: Path | None = None) -> None:
        sock = sock or paths.socket_path()
        sock.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            sock.unlink()
        self.tmux.ensure_server()
        server = await asyncio.start_unix_server(self._handle_conn, path=str(sock))
        os.chmod(sock, 0o600)
        log.info("listening on %s", sock)
        ticker = asyncio.create_task(self._tick_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            ticker.cancel()
            with contextlib.suppress(FileNotFoundError):
                sock.unlink()

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
                await self._push_changes()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # -- reconcile -------------------------------------------------------------------------------
    #
    # Concurrency model: every mutation of `self.sessions` and of Session objects happens on the
    # event loop, never in a thread. Only the tmux subprocess calls run in threads, and they
    # return plain data. So a tick's reconcile step and an RPC handler can never interleave
    # inside a check-then-act sequence — the loop runs them one at a time.

    async def tick(self) -> None:
        snapshot_at = datetime.now(UTC)
        panes = await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)
        tails = await asyncio.to_thread(lambda: {sid: self.tmux.capture_tail(sid, TAIL_LINES) for sid in panes})
        self._reconcile(panes, tails, snapshot_at)
        await self._refresh_git(snapshot_at)
        if self._derive_task is None or self._derive_task.done():
            # detached for the same reason the usage refresh is: `gh` talks to the network, and the
            # tick and its push must not wait on it (review 2026-09-11)
            self._derive_task = asyncio.create_task(self._derive_reports(snapshot_at))
        if snapshot_at - self._pruned_at > PRUNE_EVERY:
            self._pruned_at = snapshot_at
            # the live set is read here, on the loop (the class's one-writer rule); only the file
            # work goes to the thread
            live = {s.run_log for s in self.sessions.values() if s.run_log and s.state not in ("exited", "closed")}
            await asyncio.to_thread(self._prune_runs, snapshot_at, live)
        if self._usage_task is None or self._usage_task.done():
            # detached: a slow usage endpoint (10 s timeout) must not hold up the tick or its push
            self._usage_task = asyncio.create_task(self._refresh_usage())

    def _prune_runs(self, now: datetime, live: set[str]) -> None:
        """Run-log retention (design §4.6): a log older than `runs_keep_days` goes unless it is in
        `live`, the logs of sessions still running (never truncate a live log: invariant 3). Logs
        of forgotten sessions are the common case — Forget keeps the file until this sweep. `0`
        keeps everything. Runs in a thread: touches files, never `self.sessions`."""
        keep = hosts.local_host().runs_keep_days
        if keep <= 0:
            return
        cutoff = (now - timedelta(days=keep)).timestamp()
        for f in paths.runs_dir().glob("*.log"):
            try:
                if str(f) not in live and f.stat().st_mtime < cutoff:
                    f.unlink()
                    log.info("pruned run log %s (older than %d days)", f.name, keep)
            except OSError:
                continue

    async def _refresh_git(self, now: datetime) -> None:
        due = [
            s
            for s in self.sessions.values()
            if s.dir
            and s.state not in ("closed",)
            and now - self._git_checked.get(s.id, datetime.min.replace(tzinfo=UTC)) > GIT_EVERY
        ]
        if not due:
            return
        results = await asyncio.gather(*(asyncio.to_thread(git_info, s.dir) for s in due), return_exceptions=True)
        infos = {s.id: (r if not isinstance(r, BaseException) else None) for s, r in zip(due, results, strict=True)}
        for s in due:
            self._git_checked[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            info = infos.get(s.id)
            new = info.to_dict() if info else None
            if new != live.git:
                live.git = new
                self.store.save(live)

    async def _derive_reports(self, now: datetime) -> None:
        """A detached task: log a failure, never let it vanish silently, and announce what changed
        (the tick's own push has been and gone by the time this finishes)."""
        try:
            await self._derive_reports_inner(now)
        except Exception:  # noqa: BLE001
            log.exception("deriving reports failed")
        finally:
            await self._push_changes()

    async def _derive_reports_inner(self, now: datetime) -> None:
        """Fill in the report channels the session did not declare (design §4.8, §9 invariant 10):
        its branch, the PRs from it, and the ledger rows those PRs put on main. Every entry is
        marked `derived`, so a declaration stands whatever this finds — the record refuses the
        write, which is why a refusal is not an error. Runs off the branch `_refresh_git` already
        read, on its own slow cadence, and never blocks the tick on a failure."""
        due = [
            s
            for s in self.sessions.values()
            if s.dir
            and not s.external
            and s.state != "closed"
            and (s.git or {}).get("branch")
            and now - self._derived_at.get(s.id, datetime.min.replace(tzinfo=UTC)) > DERIVE_EVERY
        ]
        if not due:
            return
        pending = {
            s.id: [(e.ref, e.pr) for e in s.progress if e.source != "declared" and e.status == "claimed" and e.pr]
            for s in due
        }
        results = await asyncio.gather(
            *(asyncio.to_thread(reports.derive, s.dir, (s.git or {}).get("branch"), pending[s.id]) for s in due),
            return_exceptions=True,
        )
        for s, result in zip(due, results, strict=True):
            self._derived_at[s.id] = now
            live = self.sessions.get(s.id)
            if live is None:
                continue
            if isinstance(result, BaseException):
                log.warning("deriving reports for %s failed: %s", s.id, result)
                continue
            progress, findings = result
            # Lists, not generators: these upserts are the write, and `any()` over a generator
            # would stop at the first change and silently drop every later entry (review 2026-09-11).
            applied = [live.report_progress(e) for e in progress] + [live.report_finding(e) for e in findings]
            changed = any(applied)
            if changed:
                self.store.save(live)

    async def _refresh_usage(self) -> None:
        """Ask each live agent session's adapter for its profile's usage once a minute, in a
        thread; a fetch failure keeps the last answer and never gates anything (design §6).
        Then the `limited` rule: an interactive session on a profile at 100% of a window shows
        `limited` with the reset time, and goes back to what it was once the window resets."""
        try:
            await self._refresh_usage_inner()
        except Exception:  # noqa: BLE001 — a detached task: log, never let it vanish silently
            log.exception("usage refresh failed")
        finally:
            await self._push_changes()

    async def _refresh_usage_inner(self) -> None:
        live = [
            s
            for s in self.sessions.values()
            if s.kind == "interactive" and s.adapter != "shell" and s.state not in ("exited", "closed")
        ]
        mono = time.monotonic()
        due: dict[str, Any] = {}
        for s in live:
            fn = getattr(adapters.get(s.adapter), "usage_for", None)
            if fn and s.profile not in due and mono - self._usage_checked.get(s.profile, -USAGE_EVERY) >= USAGE_EVERY:
                due[s.profile] = fn
        if due:
            results = await asyncio.gather(
                *(asyncio.to_thread(fn, prof) for prof, fn in due.items()), return_exceptions=True
            )
            for prof, r in zip(due, results, strict=True):
                self._usage_checked[prof] = mono
                if isinstance(r, dict) and r != self._usage.get(prof):
                    self._usage[prof] = r
                    await self._broadcast({"event": "usage", "profile": prof, "usage": r})
        for s in live:
            cap = _cap(self._usage.get(s.profile))
            if cap and s.state not in ("limited", "needs-you"):
                # the tool's own endpoint, not the screen: reported, so `hook` (design §9 invariant 4)
                self._pre_limited[s.id] = s.state
                s.set_state("limited", confidence="hook", pending=Pending(kind="limit", text=cap))
                self.store.save(s)
            elif not cap and s.state == "limited" and s.pending and s.pending.kind == "limit":
                # back to what it was (idle stays idle: no hook will come to correct a wrong `working`)
                s.set_state(self._pre_limited.pop(s.id, "working"), confidence="hook")
                self.store.save(s)

    def _reconcile(self, panes: dict[str, PaneInfo], tails: dict[str, list[str]], snapshot_at: datetime) -> None:
        for sid, event in self.events.drain():
            self._apply_event(sid, event)
        now = datetime.now(UTC)
        mono = time.monotonic()
        self._removed = {k: v for k, v in self._removed.items() if mono - v[1] < REMOVED_GUARD_SECONDS}
        for sid, s in list(self.sessions.items()):
            if s.state == "closed":
                if s.closed_at and _parse(s.closed_at) + CLOSED_KEEP < now:
                    self._forget(sid)
                continue
            pane = panes.get(sid)
            if pane is None:
                # A session created after the pane snapshot was taken is not judged by it.
                if _parse(s.created) + CREATE_GRACE < snapshot_at and (s.state != "exited" or s.pane):
                    s.set_state("exited", confidence="scraped")
                    s.pane = False  # gone for good: killed, or the tmux server restarted (TD-023)
                    self.store.save(s)
                continue
            self._observe(s, pane, tails.get(sid, []), now)
        # tmux sessions with our prefix that we have no record of (created by hand, or the
        # store was lost): adopt them minimally as shells so they appear in the Herd.
        for name, pane in panes.items():
            if name not in self.sessions and not self._is_removed_pane(name, pane):
                s = Session(id=name, name=name[len(naming.PREFIX) :], kind="interactive", adapter="shell", dir="")
                s.created = datetime.fromtimestamp(pane.created, UTC).isoformat().replace("+00:00", "Z")
                self.sessions[name] = s
                self._observe(s, pane, tails.get(name, []), now)
        self._reconcile_external()

    def _reconcile_external(self) -> None:
        """Read-only cards for live sessions the adapters see that agentorc did not start (a
        `claude` in a VS Code terminal: no tmux at all; TD-010 a, design §4.1). State comes from
        the tool's registry status, so it is `scraped`. Skipped: a tool id one of our records
        already carries, and any session in a directory where one of our agent sessions is live
        (that is our own pane before its first hook reported the id; invariant 2 says there is
        only one). A card leaves when its process does."""
        try:
            exts = adapters.external_sessions()
        except Exception:  # noqa: BLE001
            log.exception("external sessions")
            return
        ours = {s.adapter_id for s in self.sessions.values() if s.adapter_id}
        taken = {
            Path(s.dir).resolve()
            for s in self.sessions.values()
            if s.dir and s.kind == "interactive" and s.adapter != "shell" and s.state not in ("exited", "closed")
        }
        seen: set[str] = set()
        for ext in exts:
            if (ext.tool_id and ext.tool_id in ours) or (ext.cwd and Path(ext.cwd).resolve() in taken):
                continue
            sid = base = "ext-" + naming.slug(ext.tool_id or ext.name, max_len=48)
            n = 2
            while sid in seen:  # two entries with no tool id and one name: never hide the second
                sid = f"{base}-{n}"
                n += 1
            seen.add(sid)
            s = self._external.get(sid)
            if s is None:
                s = Session(
                    id=sid, name=ext.name, kind="interactive", adapter=ext.adapter, dir=ext.cwd, adapter_id=ext.tool_id
                )
                s.external, s.pane = True, False
                self._external[sid] = s
            s.name, s.dir = ext.name, ext.cwd
            st = EXTERNAL_STATES.get(ext.status or "", "working")
            if st != s.state:
                s.set_state(st, confidence="scraped")
        for sid in list(self._external):
            if sid not in seen:
                del self._external[sid]
                for last in self._subscribers.values():  # as `_forget` does: announce `gone` exactly once
                    last.pop(sid, None)
                self._gone.append(sid)

    def _is_removed_pane(self, name: str, pane: PaneInfo) -> bool:
        """Is this the pane a recent `remove` killed (as a stale snapshot would still list it)? A
        pane born after the removed one is a new session and adopts normally (TD-021)."""
        rem = self._removed.get(name)
        if rem is None:
            return False
        created, _ = rem
        # `created` is tmux's `session_created`, whole seconds. Equal means "could be the same
        # pane", so it is skipped: a pane that was created, exited, observed, removed *and* had
        # its name reused inside one second is the only case this delays (until the guard
        # expires), and `None` (the pane was already gone at remove) is treated the same way.
        return created is None or pane.created <= created

    def _observe(self, s: Session, pane: PaneInfo, tail: list[str], now: datetime) -> None:
        adapter = adapters.get(s.adapter)
        s.tail = [_clean(t) for t in tail]
        s.pane = True
        if s.run_log:
            with contextlib.suppress(OSError):
                mtime = datetime.fromtimestamp(Path(s.run_log).stat().st_mtime, UTC)
                s.last_output = mtime.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if pane.dead:
            s.exit_code = pane.dead_status
            if s.state != "exited":
                s.set_state("exited", confidence="scraped")
        elif adapter.state_source == "scraped":
            st = adapter.classify(pane, tail)
            if st and st != s.state:
                s.set_state(st, confidence="scraped")
        elif (m := self._screen_verdict(s, adapter, now)) is not None:
            # Hook-fed adapter, screen rule fired, no fresher hook state: the labelled fallback
            if m.state != s.state or (m.pending and m.pending != s.pending):
                s.set_state(m.state, confidence="scraped", pending=m.pending)
        elif s.state == "working" and s.last_output and now - _parse(s.last_output) > STALL_AFTER:
            # Hook-fed adapters: the liveness cross-check applies to `working` alone — a
            # `needs-you` or `idle` session is silent by design.
            s.set_state("stalled?", confidence="scraped")
        self.store.save(s)

    def _hook_fresh(self, sid: str, now: datetime) -> bool:
        last = self._last_hook.get(sid)
        return last is not None and now - last <= STALL_AFTER

    def _screen_verdict(self, s: Session, adapter: Any, now: datetime) -> Any:
        """The adapter's screen-rule match for the session's tail, or None when there is no rule
        set, nothing matched, or a hook state is fresher (design §4.2: scraped never outranks it)."""
        explain = getattr(adapter, "explain", None)
        if explain is None or self._hook_fresh(s.id, now):
            return None
        return explain(s.tail)

    def _apply_event(self, sid: str, event: dict[str, Any]) -> None:
        """A hook-fed state transition (design §4.2 table). Adapters map hook names to these."""
        s = self.sessions.get(sid)
        if s is None:
            return
        self._last_hook[sid] = datetime.now(UTC)  # apply time, also for events drained from the offline queue
        if aid := event.get("adapter_id"):
            s.adapter_id = aid
        if delta := event.get("subagent_delta"):
            s.subagents = max(0, s.subagents + int(delta))
        state = event.get("state")
        if state:
            pending = Pending.from_dict(event["pending"]) if event.get("pending") else None
            if state == "needs-you" and self._permission_waiting(sid):
                # Claude Code draws its own dialog a few seconds into a PermissionRequest hook
                # and fires a permission_prompt Notification for it; the hook is still blocking
                # and its answer still wins (verified 2026-09-06). Keep Allow / Deny up.
                pass
            else:
                s.set_state(state, confidence="hook", pending=pending)
        self.store.save(s)

    def _permission_waiting(self, sid: str) -> bool:
        return any(k[0] == sid and not f.done() for k, f in self._waiters.items())

    def _forget(self, sid: str) -> None:
        if self.sessions.pop(sid, None) is None:
            return  # already forgotten (two removes of one id in flight): nothing more to announce
        self.store.delete(sid)
        # Scrub the id from every subscriber's map and queue the one `gone`: whichever
        # `_push_changes` runs next (the caller's or a tick's) announces it exactly once.
        for last in self._subscribers.values():
            last.pop(sid, None)
        for side in (self._git_checked, self._derived_at, self._pre_limited, self._last_hook):  # no key outlives it
            side.pop(sid, None)
        self._gone.append(sid)

    # -- RPC methods -----------------------------------------------------------------------------

    async def rpc_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in (*self.sessions.values(), *self._external.values())]

    async def rpc_get(self, id: str) -> dict[str, Any]:
        return self._get(id, external=True).to_dict()

    async def rpc_create(
        self,
        *,
        name: str,
        dir: str,
        adapter: str = "shell",
        profile: str = "",
        kind: str = "interactive",
        repo: str | None = None,
        worktree: str | None = None,
        argv: list[str] | None = None,
        unattended: bool = False,
        resume: str | None = None,
        prompt: str | None = None,
        capabilities: list[str] | None = None,
        lane: list[str] | None = None,
    ) -> dict[str, Any]:
        directory = Path(dir).expanduser().resolve()
        if not directory.is_dir():
            raise RpcError(f"not a directory: {directory}")
        grants, references = _grants(capabilities or []), _lane(lane or [])  # validate before anything starts
        try:
            ad = adapters.get(adapter)
        except KeyError as e:
            raise RpcError(str(e).strip('"')) from None
        if worktree:
            # Design §4.5 New session "new worktree": the checkout is the repo, the session runs in
            # `<repo>/.claude/worktrees/<name>` on branch <name>, created here if missing.
            repo_root = Path(repo).expanduser().resolve() if repo else directory
            async with self._dir_locks[f"worktree:{repo_root}:{worktree}"]:  # two creates of one name: second reuses
                try:
                    directory = await asyncio.to_thread(ensure_worktree, repo_root, worktree)
                except WorktreeError as e:
                    raise RpcError(str(e)) from None
            repo = str(directory.parent.parent.parent)  # <repo>/.claude/worktrees/<name> → the main checkout
        async with contextlib.AsyncExitStack() as locks:
            await locks.enter_async_context(self._dir_locks[str(directory)])
            if resume:
                # one create per conversation at a time, whatever the directory: two concurrent
                # resumes of one id would otherwise both pass the holder check below (TD-012)
                await locks.enter_async_context(self._dir_locks[f"conversation:{resume}"])
            if kind == "interactive" and adapter != "shell":
                for who in await asyncio.to_thread(self.occupants, directory):
                    raise RpcError(f"{directory} already has agent session {who}; anchor rule (use a worktree)")
            if resume:
                for who in await asyncio.to_thread(self.conversation_holders, resume):
                    raise RpcError(f"conversation {resume} is still live in {who}; kill it first, or Switch to it")
            live = await asyncio.to_thread(lambda: [p.session for p in self.tmux.list_panes()])
            try:
                spec = ad.launch(
                    profile=profile, resume=resume, prompt=prompt, unattended=unattended, cwd=directory, name=name
                )
            except (KeyError, ValueError) as e:
                raise RpcError(str(e).strip('"')) from None
            if argv:
                spec.argv = argv
            taken = set(self.sessions) | set(live)
            for _attempt in range(5):
                sid = naming.session_id(directory, repo, name, taken)
                # Ours win, whatever an adapter sets. AGENTORC_HOME is explicit because the tmux
                # server may predate this agent and carry a different environment; a hook script
                # inside the session must find *this* agent's socket (found the hard way, 2026-09-06).
                env = {**spec.env, "AGENTORC_SESSION": sid, "AGENTORC_HOME": str(paths.home())}
                run_log = paths.runs_dir() / f"{sid}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
                try:
                    await asyncio.to_thread(self._start, sid, directory, spec.argv, env, run_log)
                    break
                except DuplicateSession:
                    taken.add(sid)  # design §4.1: handle tmux's own verdict, not just our check
            else:
                raise RpcError(f"could not find a free session name for {name!r} in {directory}")
            s = Session(
                id=sid,
                name=name,
                kind=kind,  # type: ignore[arg-type]
                adapter=adapter,
                dir=str(directory),
                profile=profile,
                repo=repo,
                worktree=worktree,
                unattended=unattended,
                confidence=ad.state_source,
                run_log=str(run_log),
                adapter_id=spec.adapter_id,
                capabilities=grants,
                lane=references,
            )
            self.sessions[sid] = s
            self.store.save(s)
            self._remember_dir(directory)
            if resume:
                await self._supersede(resume, sid)
        return s.to_dict()

    async def _supersede(self, adapter_id: str, new_sid: str) -> None:
        """A resumed conversation continues in the new session: the exited record it came from is
        closed (kept a day, sorted last) and its dead pane dropped, so the Herd shows one card."""
        for other in list(self.sessions.values()):
            if other.id != new_sid and other.adapter_id == adapter_id and other.state == "exited":
                await asyncio.to_thread(self.tmux.kill_session, other.id)
                other.set_state("closed", confidence=other.confidence)
                other.closed_at = now_iso()
                self.store.save(other)

    def occupants(self, directory: Path) -> list[str]:
        """Who holds the agent slot for `directory` (design §9 invariant 2): agentorc's own live
        agent sessions, plus live sessions the adapters can see that agentorc did not start
        (a VS Code terminal running `claude` in the checkout, say). Shells never count."""
        directory = Path(directory).resolve()
        ours = {s.adapter_id for s in self.sessions.values() if s.adapter_id}
        out = [
            f"{s.id} ({s.state})"
            for s in self.sessions.values()
            if s.kind == "interactive"
            and s.adapter != "shell"
            and s.state not in ("exited", "closed")
            and Path(s.dir).resolve() == directory
        ]
        for ext in adapters.external_sessions():
            if ext.tool_id and ext.tool_id in ours:
                continue  # that is one of ours, seen through the tool's registry
            if Path(ext.cwd).resolve() == directory:
                out.append(f"{ext.name} ({ext.adapter}, outside agentorc{', ' + ext.status if ext.status else ''})")
        return out

    def conversation_holders(self, adapter_id: str) -> list[str]:
        """Who is driving the tool conversation `adapter_id` right now (TD-012): a live record of
        ours, or a live session outside agentorc whose tool id matches. Two panes on one
        conversation is the failure `resume` must not create; an exited record is superseded
        instead (`_supersede`)."""
        out = [
            f"{s.id} ({s.state})"
            for s in self.sessions.values()
            if s.adapter_id == adapter_id and s.state not in ("exited", "closed")
        ]
        ours = {s.adapter_id for s in self.sessions.values() if s.adapter_id}
        for ext in adapters.external_sessions():
            if ext.tool_id == adapter_id and ext.tool_id not in ours:
                out.append(f"{ext.name} ({ext.adapter}, outside agentorc{', ' + ext.status if ext.status else ''})")
        return out

    async def rpc_occupancy(self, dir: str) -> dict[str, Any]:
        directory = Path(dir).expanduser()
        if not directory.is_dir():
            return {"dir": str(directory), "occupants": [], "git": False}
        occ = await asyncio.to_thread(self.occupants, directory)
        is_git = (directory / ".git").exists() or await asyncio.to_thread(lambda: git_info(directory) is not None)
        return {"dir": str(directory.resolve()), "occupants": occ, "git": bool(is_git)}

    def _start(self, sid: str, cwd: Path, argv: list[str] | None, env: dict[str, str], run_log: Path) -> None:
        self.tmux.ensure_server()
        self.tmux.new_session(sid, cwd, argv, env, logfile=run_log)  # log from the first byte (invariant 3)

    async def rpc_kill(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("exited", confidence="scraped")
        s.pane = False  # unlike a natural exit, a kill destroys the pane (TD-023)
        self.store.save(s)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.to_dict()

    async def rpc_close(self, id: str) -> dict[str, Any]:
        s = self._get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        s.set_state("closed", confidence="scraped")
        s.pane = False
        s.closed_at = now_iso()
        self.store.save(s)
        await self._push_changes()  # the Focus terminal ends on this delta, not on a retry (TD-029)
        return s.to_dict()

    async def rpc_remove(self, id: str) -> None:
        s = self._get(id)
        if s.state not in ("exited", "closed"):
            raise RpcError(f"{id} is {s.state}; kill it first")
        # The dead pane is kept until now (exit code, last screen); without this it would be
        # re-adopted as a nameless shell on the next tick (first-use finding 2026-09-06).
        # one extra `list-panes -a` per remove, a person-driven action: accepted
        pane = (await asyncio.to_thread(self.tmux.main_panes, naming.PREFIX)).get(id)
        await asyncio.to_thread(self.tmux.kill_session, id)
        self._forget(id)
        self._removed[id] = (pane.created if pane else None, time.monotonic())
        await self._push_changes()

    async def rpc_send(self, id: str, text: str, wait: bool = False, timeout: float | None = None) -> dict | None:
        """Type a prompt. With `wait` (TD-016, design §4.2): return the record once the session has
        started on *this* prompt and settled again (`SETTLED`). A session that is busy queues the
        prompt behind its current turn, so the wait first lets that turn end, then looks for the
        next one to start. Errors: `prompt-stalled` when nothing starts within `SEND_STALL_SECONDS`
        of the moment it could, `timeout` after `timeout` seconds in total, `removed` if the record
        goes away. Nothing is ever re-sent on a guess (design §4.2)."""
        s = self._get(id)
        if s.pending and s.pending.kind in ("permission", "question"):
            raise RpcError(f"{id} has a pending {s.pending.kind}; answer it in the terminal")
        await self._submit(id, adapters.get(s.adapter), text)
        if not wait:
            return None
        end = None if timeout is None else time.monotonic() + timeout

        def left() -> float | None:
            return None if end is None else max(0.0, end - time.monotonic())

        s = self._get(id)
        if s.state != "idle":
            # Busy: the tool queues the text. Wait for the current turn to end; a stop on anything
            # but idle (a question, an exit) is returned as is — the prompt is still queued behind it.
            rev_sent = s.rev
            if not await self._wait_state(id, lambda x: x.state in SETTLED, left()):
                self._raise_not_settled(id, timeout)
            s = self._get(id)
            if s.state != "idle":
                return s.to_dict()
            if s.rev - rev_sent >= 3:
                # working → idle → working → idle inside one poll: the queued turn already ran.
                # Accepted residual: a permission answered *and* the rest of that same turn finishing
                # inside one 0.1 s poll would look the same; a turn does not end that fast.
                return s.to_dict()
        rev_before = s.rev
        # Started: any transition off this idle (a hook's UserPromptSubmit → working, a scraped
        # working, even an exit) within the stall window.
        stall = SEND_STALL_SECONDS if left() is None else min(SEND_STALL_SECONDS, left())
        if not await self._wait_state(id, lambda x: x.rev != rev_before, stall):
            if id not in self.sessions:
                raise RpcError(f"removed: {id} went away while waiting")
            raise RpcError(f"prompt-stalled: {id} showed no activity within {stall:g} s")
        if not await self._wait_state(id, lambda x: x.state in SETTLED and x.rev != rev_before, left()):
            self._raise_not_settled(id, timeout)
        return self._get(id).to_dict()

    async def _submit(self, sid: str, adapter: Any, text: str) -> None:
        """Paste, Enter, and confirm the prompt left the composer (TD-027, design §4.2). Only an
        adapter that can read its tool's composer (`composer(tail_raw)`, design §4.3) gets the
        confirmation; the rest get the blind paste + Enter. The paste is given a moment to paint
        (Enter sent while the tool is still taking the paste is swallowed — measured 2026-09-10:
        a paste landing within ~0.1 s of the previous submit lost its Enter every time), then the
        composer must empty within `SUBMIT_SECONDS`; one retry with `C-m`, then `prompt-stuck`.
        Only the Enter is ever re-sent, and only with the text visibly still in the composer —
        never the text (design §4.2). A composer that cannot be read (no composer row, or a failed
        capture — `capture_tail` returns [] then) counts as emptied: no evidence is not evidence of a
        stuck prompt."""
        reader = getattr(adapter, "composer", None)
        await asyncio.to_thread(self.tmux.paste, sid, text)
        if reader is None:
            await asyncio.to_thread(self.tmux.send_enter, sid)
            return

        async def composer() -> str | None:
            return reader(await asyncio.to_thread(self.tmux.capture_tail, sid, COMPOSER_LINES, raw=True))

        if not await self._poll(lambda: composer(), PASTE_SHOW_SECONDS):
            log.info(
                "send %s: the paste did not show in the composer within %gs; pressing Enter anyway",
                sid,
                PASTE_SHOW_SECONDS,
            )
        for key in ("Enter", "C-m"):
            await asyncio.to_thread(self.tmux.send_key, sid, key)
            if await self._poll(lambda: _falsy(composer()), SUBMIT_SECONDS):
                return
            log.warning("send %s: the composer still shows the prompt %gs after %s", sid, SUBMIT_SECONDS, key)
        raise RpcError(f"prompt-stuck: {sid} still shows the prompt in its composer after Enter and C-m")

    async def _poll(self, pred: Any, timeout: float) -> bool:
        """Until `await pred()` is truthy; False on timeout."""
        end = time.monotonic() + timeout
        while True:
            if await pred():
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.1)

    def _raise_not_settled(self, sid: str, timeout: float | None) -> None:
        live = self.sessions.get(sid)
        if live is None:
            raise RpcError(f"removed: {sid} went away while waiting")
        raise RpcError(f"timeout: {sid} is still {live.state} after {timeout:g} s")

    async def _wait_state(self, sid: str, pred: Any, timeout: float | None) -> bool:
        """Poll the record on the loop until `pred(session)` holds; False on timeout (None: no limit)
        or when the record is gone."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            s = self.sessions.get(sid)
            if s is None:
                return False
            if pred(s):
                return True
            if end is not None and time.monotonic() >= end:
                return False
            await asyncio.sleep(0.1)

    async def rpc_keys(self, id: str, keys: list[str]) -> None:
        self._get(id)
        await asyncio.to_thread(lambda: self.tmux.run("send-keys", "-t", f"={id}:", *keys))

    async def rpc_explain(self, id: str, lines: int = 40) -> dict[str, Any]:
        """Why the session shows the state it does (design §4.2, TD-015): the current screen, the
        record's state and confidence, the screen rule that would fire on it with its evidence, and
        whether that verdict applies (no fresher hook state) or is outranked."""
        s = self._get(id)
        adapter = adapters.get(s.adapter)
        tail = [_clean(t) for t in await asyncio.to_thread(self.tmux.capture_tail, id, lines)]
        explain = getattr(adapter, "explain", None)
        now = datetime.now(UTC)
        out: dict[str, Any] = {
            "id": id,
            "adapter": s.adapter,
            "state": s.state,
            "confidence": s.confidence,
            "pending": s.pending.to_dict() if s.pending else None,
            "since": s.since,
            "last_hook": self._last_hook[id].replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if id in self._last_hook
            else None,
            "tail": tail,
            "match": None,
            "reason": "",
        }
        if explain is None:
            src = "foreground process" if adapter.state_source == "scraped" else "hooks only"
            out["reason"] = f"{s.adapter} has no screen rules; its state comes from {src}"
            return out
        m = explain(tail)
        out["match"] = m.to_dict() if m else None
        if m is None:
            out["reason"] = "no screen rule matched"
        elif self._hook_fresh(id, now):
            out["reason"] = f"rule {m.rule} matched, but a hook reported within {STALL_AFTER} — the hook state wins"
        else:
            out["reason"] = f"rule {m.rule} matched and no fresher hook state exists — applied as scraped"
        return out

    async def rpc_tail(self, id: str, lines: int = 40) -> list[str]:
        self._get(id)
        return await asyncio.to_thread(self.tmux.capture_tail, id, lines)

    async def rpc_seen(self, id: str) -> dict[str, Any]:
        """A person looked at this session (Focus opened, a card control used). The UI reads
        `since > seen_at` on an `idle` record as "finished while you were away" (design §4.2)."""
        s = self._get(id, external=True)
        s.seen_at = now_iso()
        if not s.external:
            self.store.save(s)
        await self._push_changes()
        return s.to_dict()

    async def rpc_set_mode(self, id: str, unattended: bool) -> dict[str, Any]:
        s = self._get(id)
        s.unattended = bool(unattended)
        self.store.save(s)
        return s.to_dict()

    async def rpc_set_grants(
        self, id: str, add: list[str] | None = None, remove: list[str] | None = None
    ) -> dict[str, Any]:
        """`ao grant` / `ao revoke`, the Focus grants chip (design §4.8): edit `capabilities`. Takes
        effect on the target's next call — the gate reads the record, not a cached copy."""
        s = self._get(id)
        adding, removing = _grants(add or []), _grants(remove or [])
        s.capabilities = [g for g in GRANTS if (g in s.capabilities or g in adding) and g not in removing]
        self.store.save(s)
        await self._push_changes()
        return s.to_dict()

    async def rpc_progress(
        self,
        id: str,
        ref: str,
        status: str = "claimed",
        pr: int | None = None,
        why: str | None = None,
        source: str = "declared",
    ) -> dict[str, Any]:
        """`ao progress claim|done|drop <ref>` (design §4.8): what this session set out to resolve
        and how it went. A report channel is **ungated** — any session may write any record's, the
        Herd renders whichever are non-empty — and one reference is one entry, upserted in place."""
        s = self._get(id)
        if status not in PROGRESS_STATUSES:
            raise RpcError(f"unknown progress status {status!r}; statuses are: {', '.join(PROGRESS_STATUSES)}")
        entry = ProgressEntry(ref=_ref(ref), status=status, pr=_pr(pr), why=why, source=_source(source))
        return await self._report(s, s.report_progress(entry), entry)

    async def rpc_finding(
        self, id: str, ref: str, priority: str | None = None, source: str = "declared"
    ) -> dict[str, Any]:
        """`ao finding <ref> [--priority …]` (design §4.8): a reference this session filed on the
        side. Ungated like `progress`, and upserted by reference the same way."""
        s = self._get(id)
        entry = FindingEntry(ref=_ref(ref), priority=priority, source=_source(source))
        return await self._report(s, s.report_finding(entry), entry)

    async def _report(self, s: Session, applied: bool, entry: Any) -> dict[str, Any]:
        """Save and announce a report entry the record accepted. A refused one (§9 invariant 10: a
        `derived` or `scraped` entry over a `declared` one) is not an error — the caller gets the
        record as it stands, with `refused` naming the entry that did not land."""
        if applied:
            self.store.save(s)
            await self._push_changes()
        out = s.to_dict()
        if not applied:
            out["refused"] = entry.to_dict()
        return out

    async def rpc_hook(self, session: str, **event: Any) -> dict[str, Any] | None:
        """Called by an adapter's hook script. A `permission` event blocks until answered or timed out."""
        if event.get("kind") == "permission":
            return await self._await_permission(session, event)
        self._apply_event(session, event)
        await self._push_changes()
        return None

    async def _await_permission(self, session: str, event: dict[str, Any]) -> dict[str, Any] | None:
        s = self.sessions.get(session)
        if s is None:
            return None
        self._last_hook[session] = datetime.now(UTC)
        wait = float(event.get("wait_seconds") or 600)
        deadline = (datetime.now(UTC) + timedelta(seconds=wait)).replace(microsecond=0)
        tool_use_id = event.get("tool_use_id") or f"{session}:{now_iso()}"
        pending = Pending(
            kind="permission",
            text=event.get("text", ""),
            deadline=deadline.isoformat().replace("+00:00", "Z"),
            tool_use_id=tool_use_id,
        )
        s.set_state("needs-you", confidence="hook", pending=pending)
        self.store.save(s)
        # Register the waiter BEFORE the first await: a client reacting to the push must find it.
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (session, tool_use_id)
        self._waiters[key] = fut
        await self._push_changes()
        try:
            return await asyncio.wait_for(fut, timeout=wait)
        except TimeoutError:
            # Fell through to the terminal dialog: the decision now lives there (design §4.2).
            s.set_state("needs-you", confidence="hook", pending=Pending(kind="question", text=pending.text))
            self.store.save(s)
            await self._push_changes()
            return None
        finally:
            self._waiters.pop(key, None)

    async def rpc_decide(self, id: str, tool_use_id: str, behavior: str, reason: str | None = None) -> None:
        s = self._get(id)
        fut = self._waiters.get((id, tool_use_id))
        if fut is None or fut.done():
            raise RpcError("no pending permission with that id (answered, timed out, or in the terminal)")
        if behavior not in ("allow", "deny"):
            raise RpcError("behavior must be allow or deny")
        fut.set_result({"behavior": behavior, "reason": reason})
        s.set_state("working", confidence="hook")
        self.store.save(s)
        await self._push_changes()

    async def rpc_recent_dirs(self) -> list[str]:
        p = paths.recent_dirs_file()
        return p.read_text().splitlines() if p.is_file() else []

    async def rpc_usage(self) -> dict[str, dict[str, Any]]:
        """Last known usage per profile (TD-001): what the top bar shows."""
        return dict(self._usage)

    async def rpc_adapters(self) -> list[str]:
        return adapters.names()

    async def rpc_ping(self) -> str:
        return "pong"

    # -- helpers ---------------------------------------------------------------------------------

    def _gate(self, caller: Any, method: str, params: dict[str, Any]) -> None:
        """Design §4.8, §9 invariant 11: an acting RPC from a session onto a *different* session
        needs the `orchestrate` grant on the caller's record. No caller (a person's terminal, the
        UI) or a session acting on itself passes as before. A caller this agent does not know is
        a session (the id came from `AGENTORC_SESSION`) and holds no grant. Reads are never gated;
        this is a guard against a confused worker, not a security boundary."""
        if not caller or method not in ACTING_RPCS:
            return
        if method not in ("create", "set_grants") and params.get("id") == caller:
            return
        me = self.sessions.get(str(caller))
        if me is not None and "orchestrate" in me.capabilities:
            return
        target = "a new session" if method == "create" else params.get("id", "?")
        raise RpcError(f"{caller} cannot {method} {target}: needs the orchestrate grant (design §4.8)")

    def _get(self, sid: str, *, external: bool = False) -> Session:
        """A record by id. A registry-only card (`external`) is returned only to callers that
        can act on it read-only; anything else gets a line saying why not."""
        if sid in self._external:
            if external:
                return self._external[sid]
            raise RpcError(f"{sid} was started outside agentorc (a read-only card from the tool's registry)")
        try:
            return self.sessions[sid]
        except KeyError:
            raise RpcError(f"no session {sid}") from None

    def _remember_dir(self, directory: Path) -> None:
        p = paths.recent_dirs_file()
        lines = p.read_text().splitlines() if p.is_file() else []
        lines = [str(directory)] + [ln for ln in lines if ln != str(directory)]
        p.write_text("\n".join(lines[:20]) + "\n")

    # -- streaming -------------------------------------------------------------------------------

    async def _push_changes(self) -> None:
        """Send each subscriber what changed since *it* was last told. One payload per session is
        serialised once; the per-subscriber comparison is a string compare."""
        gone, self._gone = self._gone, []
        if not self._subscribers:
            return
        # sort_keys: the payload is the comparison key too (the UI reads fields by name, never order)
        payloads = {
            sid: json.dumps(s.to_dict(), sort_keys=True) for sid, s in (*self.sessions.items(), *self._external.items())
        }
        for w, last in list(self._subscribers.items()):
            for sid, payload in payloads.items():
                if last.get(sid) != payload:
                    last[sid] = payload
                    await self._send(w, '{"event": "session", "session": ' + payload + "}")
            for sid in gone:
                await self._send(w, json.dumps({"event": "gone", "id": sid}))

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        line = json.dumps(msg)
        for w in list(self._subscribers):
            await self._send(w, line)

    async def _send(self, w: asyncio.StreamWriter, line: str) -> None:
        try:
            w.write((line + "\n").encode())
            await w.drain()
        except (ConnectionError, OSError):
            self._subscribers.pop(w, None)

    # -- connection handling ---------------------------------------------------------------------

    async def _handle_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except ValueError:
                    writer.write(b'{"error": "bad json"}\n')
                    continue
                if req.get("method") == "subscribe":
                    self._subscribers[writer] = {}  # empty: the first push is this tab's full snapshot
                    writer.write((json.dumps({"id": req.get("id"), "result": "subscribed"}) + "\n").encode())
                    await writer.drain()
                    for prof, u in self._usage.items():  # the top bar's figure, before the cards
                        await self._send(writer, json.dumps({"event": "usage", "profile": prof, "usage": u}))
                    await self._push_changes()
                    continue
                writer.write((json.dumps(await self._dispatch(req)) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._subscribers.pop(writer, None)
            writer.close()

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        rid = req.get("id")
        name = req.get("method")
        method = getattr(self, f"rpc_{name}", None)
        if method is None:
            return {"id": rid, "error": f"unknown method {name!r}"}
        params = req.get("params") or {}
        try:
            self._gate(req.get("caller"), str(name), params)
            return {"id": rid, "result": await method(**params)}
        except RpcError as e:
            return {"id": rid, "error": str(e)}
        except TypeError as e:
            return {"id": rid, "error": f"bad params: {e}"}
        except Exception as e:  # noqa: BLE001
            log.exception("rpc %s failed", req.get("method"))
            return {"id": rid, "error": f"{type(e).__name__}: {e}"}


_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")  # title sets etc.
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ESC_OTHER = re.compile(r"\x1b[ -/]*[0-~]")  # remaining ESC sequences (charset, keypad, …)


def _lane(refs: list[str]) -> list[str]:
    """The references a session was handed (design §4.8), in the order given and each canonical, so
    a lane item and the session's own claim are the same string. `free-pick` is a lane of its own."""
    if [r for r in refs if str(r).strip() == "free-pick"]:
        if len(refs) > 1:
            raise RpcError("a lane is either `free-pick` or a list of references, not both")
        return ["free-pick"]
    # deduped after canonicalisation: `--lane TD-027,td-27` is one item, or the lane count the card
    # shows (*1 of 2*) would be a lie about how much work there is (review 2026-09-11)
    return list(dict.fromkeys(_ref(r) for r in refs))


def _ref(ref: str) -> str:
    try:
        return normalize_ref(ref)
    except ValueError as e:
        raise RpcError(str(e)) from None


def _pr(pr: Any) -> int | None:
    """A PR number, however it was typed (`59`, `"#59"`, a URL's tail) — or an error, never a lie."""
    if pr is None or pr == "":
        return None
    try:
        return int(str(pr).lstrip("#"))
    except ValueError:
        raise RpcError(f"not a PR number: {pr!r}") from None


def _source(source: str) -> str:
    if source not in SOURCES:
        raise RpcError(f"unknown report source {source!r}; sources are: {', '.join(SOURCES)}")
    return source


def _grants(names: list[str]) -> list[str]:
    """Validate a list of grant names against `GRANTS`, in canonical order."""
    bad = [n for n in names if n not in GRANTS]
    if bad:
        raise RpcError(f"unknown grant {', '.join(map(str, bad))}; grants are: {', '.join(GRANTS)}")
    return [g for g in GRANTS if g in names]


async def _falsy(coro: Any) -> bool:
    """`not await coro`: a composer that reads empty ("") or unreadable (None) counts as emptied."""
    return not await coro


def _clean(text: str) -> str:
    """Strip ANSI/control bytes and cap width: pane output is untrusted everywhere but xterm.js."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
    return text[:200]


def _cap(usage: dict[str, Any] | None) -> str | None:
    """The pending text for a capped profile, or None. A window whose `resets_at` has passed is
    not a cap any more even before the next poll says so."""
    if not usage:
        return None
    now = datetime.now(UTC)
    for key, label in (("five_hour", "5-hour"), ("weekly", "weekly")):
        try:
            pct = int(usage.get(f"{key}_pct") or 0)
        except (TypeError, ValueError):
            continue
        resets = usage.get(f"{key}_resets")
        if pct < 100:
            continue
        try:
            at = _parse(str(resets)) if resets else None
        except (TypeError, ValueError):
            at = None  # unparseable: still a cap, reset time unknown
        if at is not None and at <= now:
            continue
        return f"{label} cap · resets {at.strftime('%H:%MZ') if at else 'unknown'}"
    return None


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def serve_until_signal(agent: HostAgent, sock: Path | None = None) -> None:
    """Run `agent.serve()` until SIGINT or SIGTERM.

    The signal cancels the serve task rather than stopping the loop, so `serve()`'s `finally`
    (cancel the ticker, unlink the socket file) runs while the loop is still alive and the
    process ends without asyncio's "Task was destroyed but it is pending!" (TD-024).
    """
    # Run this under `asyncio.run`: its shutdown awaits every task still on the loop (the
    # cancelled ticker, a live usage refresh), which is what keeps them from being destroyed pending.
    task = asyncio.ensure_future(agent.serve(sock))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agentorc-agent", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the host agent (foreground)")
    sub.add_parser("rpc", help="stdin/stdout JSON-lines bridge to the local socket (used over ssh)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        asyncio.run(serve_until_signal(HostAgent()))
        return 0
    if args.cmd == "rpc":
        from sessionorc.client import bridge_stdio

        return asyncio.run(bridge_stdio())
    return 2


if __name__ == "__main__":
    sys.exit(main())
