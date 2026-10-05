"""Told on Telegram when nobody is looking (design §4.10, TD-319 slice 1): the home's pass over the rows
that newly stop a session — a state row `permission`, `question` or `needs`, an identity alarm, an
open `ask` in the person inbox — telling each once it has stood `notify.HOLD`, is still there and is
not snoozed, bounded by `notify.BURST`; and the send, a detached child under Doppler. A mixin
`HostAgent` inherits; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any

from sessionorc import notify
from sessionorc import settings as settings_mod
from sessionorc.agent_common import _parse, log
from sessionorc.mail import PERSON
from sessionorc.models import now_iso, reference_of


class NotifyMixin:
    def _notify_rows(self) -> dict[str, tuple[str, str]]:
        """Every row standing now that is told when it stops someone: `{key: (began, line)}`. The key
        is the row's snooze key — `<address>|<kind>` for a record's row, `<mail id>` for a question —
        and `line` is built from structured fields alone (§4.10 *What a message says*)."""
        graph = self._graph()
        rows: dict[str, tuple[str, str]] = {}
        for key, (kind, began, _text) in self._attention.items():
            sid, slot = key.split("|", 1)
            s = graph.get(sid)
            if s is None:
                continue
            if slot == "state" and kind in notify.STATE_KINDS:
                rows[f"{sid}|{kind}"] = (began, notify.state_line(s.name or sid, s.team or "", kind))
            elif slot == "alarm" and kind == "alarm":
                rows[f"{sid}|alarm"] = (began, notify.alarm_line(s.name or sid, s.team or ""))
        for e in self.person_inbox:
            if e.kind != "ask" or not e.open or e.from_ == PERSON:
                continue
            s = graph.get(e.from_)
            name, team = (s.name, s.team or "") if s is not None else (e.from_, e.team or "")
            rows[e.id] = (e.passed_up or e.at, notify.ask_line(name or e.from_, team, reference_of(e.about) or ""))
        return rows

    def _notify_snoozed(self, key: str, now: datetime) -> bool:
        if "|" in key:
            until = self.attention_snoozed.get(key)
        else:
            until = next((e.snoozed_until for e in self.person_inbox if e.id == key), None)
        if not until:
            return False
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
            for key, (began, line) in sorted(rows.items(), key=lambda kv: kv[1][0]):
                if key in store.notified:
                    continue
                try:
                    stood = now - _parse(began)
                except (ValueError, TypeError):
                    continue
                if stood < notify.HOLD or stood > notify.HOLD + notify.LATE or self._notify_snoozed(key, now):
                    continue  # not yet; or a backlog, never told (§4.10 *newly*); or the person's *not now*
                link = notify.mail_link(tg["link"], key) if "|" not in key else notify.row_link(tg["link"], key)
                due.append((key, notify.message(line, link)))
        texts: list[str] = []
        stamp = now_iso()
        for key, text in due:
            store.notified[key] = stamp  # told, or held back by the burst: never told afterwards
            sent = [t for t in self._notify_sent if now - t < notify.BURST_WINDOW]
            self._notify_sent = sent
            if len(sent) < notify.BURST:
                texts.append(text)
                self._notify_sent.append(now)
            elif len(sent) == notify.BURST:
                texts.append(notify.MORE)
                self._notify_sent.append(now)
        if gone or due:
            store.save(self.trail, self.attention_snoozed)
        if texts and tg is not None:
            task = asyncio.create_task(self._notify_send(tg["secrets"], texts))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

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
            )
        except FileNotFoundError:
            return "doppler: not installed"
        except OSError as e:
            return f"doppler could not be run: {type(e).__name__}"
        try:
            _, err = await asyncio.wait_for(proc.communicate(text.encode()), notify.CHILD_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return f"no answer in {notify.CHILD_SECONDS:g} s"
        if proc.returncode == 0:
            return None
        return notify.reason(err.decode(errors="replace")) or f"exit {proc.returncode}"

    def _notify_view(self) -> dict[str, Any]:
        """`notify: {last_ok, last_error}` for the `host` read (§4.10): the last send's result."""
        return dict(self._notify_last)
