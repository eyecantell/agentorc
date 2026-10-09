"""Told on Telegram when nobody is looking (design §4.10, TD-319 slices 1 and 2): the home's pass over
the rows that newly stop a session or a team — a state row `permission`, `question` or `needs`, an
identity alarm, an open `ask` in the person inbox, an outcome reported `blocked`, a record the tick
could not restart, a wound-down team whose lanes gained work under `on_work: ask` — telling each once
it has stood `notify.HOLD`, is still there, is not snoozed and was not on a visible page, bounded by
`notify.BURST`; the send, a detached child under Doppler; and `notify_test`. A mixin `HostAgent`
inherits; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sessionorc import agent_common, notify
from sessionorc import settings as settings_mod
from sessionorc import work as work_mod
from sessionorc.agent_common import RpcError, _parse, log
from sessionorc.mail import PERSON
from sessionorc.models import now_iso, reference_of


@dataclass(frozen=True)
class _Row:
    began: str
    line: str
    link: str  # the Inbox's row key, or a mail id when `mail`
    snooze: str  # the attention store's snooze key; for `mail`, the mail id
    mail: bool = False
    until: str | None = None  # a mail row's own `snoozed_until`, read where the entry is kept


def _restart_at(s: Any) -> str:
    """When the restart row (§4.5a **Inbox row: restart**) began, or "" for none: the page's
    `restart_mark` read from the same fields — a ceiling, a hold by work left, an early wanted."""
    if s.superseded_by:
        return ""
    for mark in (s.restart_ceiling, s.restart_blocked):
        if isinstance(mark, dict):
            return str(mark.get("at") or "")
    wanted = s.restart_wanted
    if isinstance(wanted, dict) and wanted.get("early") and s.state in ("idle", "exited"):
        return str(wanted.get("at") or "")
    return ""


def _work_ids(mark: dict[str, Any]) -> list[str]:
    members = mark.get("members") if isinstance(mark.get("members"), dict) else {}
    ids = [str(i) for v in members.values() if isinstance(v, list) for i in v]
    ids += [str(q.get("ref")) for q in mark.get("questions") or [] if isinstance(q, dict) and q.get("ref")]
    return list(dict.fromkeys(ids))


class NotifyMixin:
    def _notify_rows(self) -> dict[str, _Row]:
        """Every row standing now that is told when it stops someone, by a key of its own; each `line`
        built from structured fields alone (§4.10 *What a message says*)."""
        graph = self._graph()
        rows: dict[str, _Row] = {}
        for key, (kind, began, _text) in self._attention.items():
            sid, slot = key.split("|", 1)
            s = graph.get(sid)
            if s is None:
                continue
            name, team = s.name or sid, s.team or ""
            if slot == "state" and kind in notify.STATE_KINDS:
                k = f"{sid}|{kind}"
                rows[k] = _Row(began, notify.state_line(name, team, kind), k, k)
            elif slot == "alarm" and kind == "alarm":
                k = f"{sid}|alarm"
                rows[k] = _Row(began, notify.alarm_line(name, team), k, k)
        for sid, s in graph.items():
            if at := _restart_at(s):
                k = f"{sid}|restart"
                rows[k] = _Row(at, notify.restart_line(s.name or sid, s.team or ""), k, k)
        for e in self.person_inbox:
            if e.from_ == PERSON:
                continue
            s = graph.get(e.from_)
            name, team = (s.name or e.from_, s.team or "") if s is not None else (e.from_, e.team or "")
            ref = reference_of(e.about) or ""
            if e.kind == "ask" and e.open:
                line = notify.ask_line(name, team, ref)
                rows[e.id] = _Row(e.passed_up or e.at, line, e.id, e.id, mail=True, until=e.snoozed_until)
            outcome = e.outcome or {}
            if outcome.get("state") == "blocked":
                line = notify.blocked_line(name, team, ref)
                rows[f"blocked:{e.id}"] = _Row(
                    str(outcome.get("at") or ""), line, e.id, e.id, mail=True, until=e.snoozed_until
                )
        # work the person handed a session (§4.10 *An entry handed to a seat*): its one copy is the
        # holder's, and the Inbox counts it under Needs you once it comes back `blocked`
        for addr, r in graph.items():
            for e in r.inbox:
                outcome = e.outcome or {}
                if e.handed_entry and outcome.get("state") == "blocked":
                    line = notify.blocked_line(r.name or addr, r.team or "", reference_of(e.about) or "")
                    rows[f"blocked:{e.id}"] = _Row(
                        str(outcome.get("at") or ""), line, e.id, e.id, mail=True, until=e.snoozed_until
                    )
        doc = settings_mod.load()
        on_work = {
            name: (t or {}).get("on_work", settings_mod.ON_WORK_DEFAULT) for name, t in settings_mod.teams(doc).items()
        }
        for team, rec in (self._host_rec.get("teams") or {}).items():
            mark = rec.get("work_waiting") if isinstance(rec, dict) else None
            if (
                not isinstance(mark, dict)
                or mark.get("held")
                or on_work.get(team, settings_mod.ON_WORK_DEFAULT) != "ask"
            ):
                continue  # under `on_work: start` a held mark is the home's own wait, and nobody's stop
            if ids := _work_ids(mark):
                k = f"work:{team}"
                # a team that runs on names its finished members (TD-466): the home wrote the mark for them alone
                # *runs on* as the home reads it: its crew alone, a person's session in it keeps nothing live
                crew = work_mod.crew(r for r in graph.values() if r.team == team)
                live = any(r.state not in work_mod.DEAD for r in crew)
                finished = [str(m) for m in mark.get("members") or {}] if live else None  # keyed by name
                line = notify.work_line(team, len(ids), finished)
                rows[k] = _Row(str(mark.get("at") or ""), line, k, f"{k}|work")
        return rows

    def _notify_snoozed(self, row: _Row, now: datetime) -> bool:
        until = row.until if row.mail else self.attention_snoozed.get(row.snooze)
        if not until:
            return False
        if until.startswith("dismissed:"):
            # the restart row's **Dismiss** (§4.5a, TD-103): that one mark's row is gone, as the page reads it
            return until == f"dismissed:{row.began}"
        with contextlib.suppress(ValueError, TypeError):
            return _parse(until) > now
        return False

    def _notify_pass(self, now: datetime) -> None:
        """Once a tick, at the home: what to tell. A told row's key is kept in `notified` until its row
        ends, so it is told once; a row that ends and begins again is a new row. Nothing is sent with
        the switch off or on a node, and nothing here waits: each send is a detached child."""
        store = self.attention_store
        if self.mode != "home":
            return
        rows = self._notify_rows()
        gone = [k for k in store.notified if k not in rows]
        for k in gone:
            del store.notified[k]
        tg = settings_mod.telegram(settings_mod.load())
        due: list[tuple[str, str]] = []
        if tg is not None:
            for key, row in sorted(rows.items(), key=lambda kv: kv[1].began):
                if key in store.notified:
                    continue
                try:
                    stood = now - _parse(row.began)
                except (ValueError, TypeError):
                    continue
                if stood < notify.HOLD or stood > notify.HOLD + notify.LATE or self._notify_snoozed(row, now):
                    continue  # not yet; or a backlog, never told (§4.10 *newly*); or the person's *not now*
                link = notify.mail_link(tg["link"], row.link) if row.mail else notify.row_link(tg["link"], row.link)
                due.append((key, notify.message(row.line, link)))
        watched = self._notify_watched_at is not None and now - self._notify_watched_at <= notify.WATCHED
        texts: list[str] = []
        stamp = now_iso()
        for key, text in due:
            # told, or on a visible page, or held back by the burst: in each case never told afterwards
            store.notified[key] = stamp
            if watched:
                continue  # it was on a screen, where the top bar's count rose (§4.10 *Looking*)
            self._notify_sent = [t for t in self._notify_sent if now - t < notify.BURST_WINDOW]
            if self._notify_quiet_until is not None and now < self._notify_quiet_until:
                continue
            if len(self._notify_sent) < notify.BURST:
                texts.append(text)
            else:
                # the seventh is *and more*, and nothing more until ten minutes have passed with nothing sent
                texts.append(notify.MORE)
                self._notify_quiet_until = now + notify.BURST_WINDOW
            self._notify_sent.append(now)
        if gone or due:
            store.save(self.trail, self.attention_snoozed)
        if texts and tg is not None:
            task = asyncio.create_task(self._notify_send(tg["secrets"], texts))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    def _notify_watching(self, now: datetime) -> None:
        """A person's `inbox` read from a visible page (§4.10 *Looking*): kept in memory only."""
        self._notify_watched_at = now

    async def rpc_notify_test(self, caller: Any = None) -> dict[str, Any]:
        """**Send a test** (§4.5a **You**: **Telegram**, §4.10): one message now with the saved
        secrets and link, whatever `on` says — *agentorc · a test from <home>* and the Inbox's
        address — answering the send's result in words. A person's own (`mail.PERSON_ONLY`), at the
        home alone (`modes.HOME_EDITS` forwards it from a node)."""
        agent_common.person_only(caller, "send a Telegram test", "§4.10")
        if self.mode != "home":
            raise RpcError("notify_test runs at the home (design §4.10): this host is a node")
        tg = settings_mod.notify(settings_mod.load()).get("telegram") or {}
        if not tg.get("secrets"):
            raise RpcError(
                "notify.telegram.secrets is not set: save the Doppler project/config that holds "
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first (design §4.10)"
            )
        link = f"{tg['link']}/inbox" if tg.get("link") else ""
        why = await self._notify_run(tg["secrets"], notify.message(notify.test_line(self.home or self.host), link))
        at = now_iso()
        if why is None:
            self._notify_last["last_ok"] = at
            return {"sent": True, "at": at, "result": f"sent at {at}"}
        self._notify_last["last_error"] = {"at": at, "reason": why}
        return {"sent": False, "at": at, "result": f"the send failed: {why}"}

    async def _notify_send(self, secrets: str, texts: list[str]) -> None:
        """The messages, one child each, in order; the last result kept for the `host` read. A failure
        is not retried, is not a row and is never itself told (§4.10 *A failed send is shown*)."""
        for text in texts:
            why = await self._notify_run(secrets, text)
            at = now_iso()
            if why is None:
                self._notify_last["last_ok"] = at
            else:
                self._notify_last["last_error"] = {"at": at, "reason": why}
                log.warning("telegram send failed: %s", why)

    async def _notify_run(self, secrets: str, text: str) -> str | None:
        """One child under Doppler with `text` on stdin, given `notify.CHILD_SECONDS`: None when it
        exited 0, else its reason in one line. Replaced by a recorder in the tests."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *notify.argv(secrets),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # its own group: a timeout ends Doppler's child, which holds the token, too
            )
        except FileNotFoundError:
            return "doppler: not installed"
        except OSError as e:
            return f"doppler could not be run: {type(e).__name__}"
        try:
            _, err = await asyncio.wait_for(proc.communicate(text.encode()), notify.CHILD_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            return f"no answer in {notify.CHILD_SECONDS:g} s"
        if proc.returncode == 0:
            return None
        return notify.reason(err.decode(errors="replace")) or f"exit {proc.returncode}"

    def _notify_view(self) -> dict[str, Any]:
        """`notify: {last_ok, last_error}` for the `host` read (§4.10): the last send's result."""
        return dict(self._notify_last)
