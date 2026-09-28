"""The promote's policy at the home (design §6 *Promote*, TD-132 slice 1): the three readings of every
registered checkout whose `.agentorc.yml` carries `promote:`, kept as `promotes` on `host`; a run in
flight concluded from `check`; under `auto: true` a run started once main has settled. A mixin
`HostAgent` inherits; the file and subprocess work is `sessionorc.promote`'s, in a thread.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from sessionorc import hosts
from sessionorc import promote as promote_mod
from sessionorc import settings as settings_mod
from sessionorc.agent_common import log
from sessionorc.models import PERSON


class PromoteMixin:
    async def _refresh_promotes(self) -> None:
        """One pass, detached from the tick as the repo facts are (`gh` and `git fetch` talk to the
        network, and a `check` may take seconds): the full readings every `PROMOTE_EVERY`, and
        `check` alone every `PROMOTE_WATCH` while a run is in flight. Each *promoted …* line goes to
        the person inbox as a `system` note (FYI, uncounted, §4.10)."""
        try:
            now_m = time.monotonic()
            full = now_m - self._promote_read_at >= promote_mod.PROMOTE_EVERY
            watching = any(r.get("inflight") for r in self._promotes.values())
            if not full and not (watching and now_m - self._promote_watch_at >= promote_mod.PROMOTE_WATCH):
                return
            self._promote_watch_at = now_m
            roots = hosts.local_host().repos()
            auto = await asyncio.to_thread(self._promote_auto)
            readings, notes, bad = await asyncio.to_thread(
                promote_mod.survey, roots, dict(self._promotes), full, auto, datetime.now(UTC)
            )
            if full:
                self._promote_read_at = time.monotonic()  # after the read: one that raised is retried next tick
            for root, why in bad.items():
                if self._promote_bad.get(root) != why:
                    log.warning("promote: skipping %s: %s", root, why)
            self._promote_bad = bad
            self._promotes = readings
            for text in notes:
                log.info("promote: %s", text)
                self._system_note(PERSON, text)
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next tick tries again
            log.exception("the promote pass failed")

    @staticmethod
    def _promote_auto() -> dict[str, bool]:
        """`repos.<repo>.promote.auto` from `settings.yml`; a repo absent promotes by hand."""
        repos = settings_mod.repos(settings_mod.load())
        return {name: bool((v.get("promote") or {}).get("auto")) for name, v in repos.items()}

    def _promotes_view(self) -> dict[str, Any]:
        """`promotes` on `host`: `{<repo>: {live, main, ahead, checks, auto, inflight, failed, at, …}}`."""
        return {name: dict(r) for name, r in sorted(self._promotes.items())}
