"""The promote's policy at the home (design §6 *Promote*, TD-132 slice 1): the three readings of every
registered checkout whose `.agentorc.yml` carries `promote:`, kept as `promotes` on `host`; a run in
flight concluded from `check`; under `auto: true` a run started once main has settled. A mixin
`HostAgent` inherits; the file and subprocess work is `sessionorc.promote`'s, in a thread.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sessionorc import hosts, mail
from sessionorc import promote as promote_mod
from sessionorc import settings as settings_mod
from sessionorc.agent_common import RpcError, log
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
            async with self._promote_lock:  # a press and the pass never start two runs of one repo
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

    def _promote_root(self, repo: str) -> str:
        """The registered checkout `repo` names — its name or its path — or the refusal."""
        roots = hosts.local_host().repos()
        found = [r for r in roots if repo in (Path(r).name, r) or Path(r).resolve() == Path(repo).resolve()]
        if not found:
            raise RpcError(f"no registered repo is {repo!r} (the home's repos registry lists {len(roots)})")
        return found[0]

    def _promote_person(self, method: str, caller: Any) -> None:
        if not mail.is_person(caller):
            raise RpcError(
                f"{method} is a person's own: refused to a session (design §6 Promote; a worker never promotes)"
            )
        if self.mode != "home":
            raise RpcError(f"{method} runs at the home (design §6 Promote): this host is a node")

    async def rpc_promote(self, repo: str = "", sha: str = "", caller: Any = None) -> dict[str, Any]:
        """The press (design §6 *Promote*, §4.7 `ao promote`, TD-132 slice 2): a person's own, refused
        to a session as `set_settings` is. Takes a fresh reading of the repo, refuses — naming it —
        on precondition (1) (the checkout on main's head, clean) or (3) (a run in flight, a failure
        standing), and goes on through (2), the checks, with what they read in the reply: the press
        is the person's word. Starts `run` detached and returns `{repo, sha, started, log, checks,
        checks_why}`; the outcome is `check`'s on a later tick (§6), as it is for `auto`."""
        self._promote_person("promote", caller)
        if sha:
            raise RpcError(
                "promote --sha is not built: `run` installs the checkout's tree, and how a rollback's "
                "commit reaches it is not designed yet (TD-132)"
            )
        root = self._promote_root(repo)
        name = Path(root).name
        async with self._promote_lock:
            readings, _notes, bad = await asyncio.to_thread(promote_mod.survey, [root], {}, True, {}, datetime.now(UTC))
            if root in bad:
                raise RpcError(bad[root])
            r = readings.get(name)
            if r is None:
                raise RpcError(f"{root} has no promote: block in its .agentorc.yml (design §5): nothing to press")
            if (u := promote_mod.unmet(r, press=True)) is not None:
                raise RpcError(f"promote {name} refused — {u[1]} (design §6 Promote, precondition: {u[0]})")
            if r.get("live") == r["main"]:
                raise RpcError(f"{name}: live is main's head {r['main'][:7]} already — nothing to promote")
            block = await asyncio.to_thread(promote_mod.block, root)
            assert block is not None  # survey read it a moment ago
            r["inflight"] = await asyncio.to_thread(
                promote_mod.start, root, name, r["main"], block["run"], "person", r.get("ahead")
            )
            u = promote_mod.unmet(r)
            r["unmet"] = {"name": u[0], "text": u[1]} if u else None
            r["auto"] = self._promotes.get(name, {}).get("auto", False)
            self._promotes[name] = r
            self._promote_watch_at = float("-inf")  # the watch starts on the next tick
        log.info("promote: %s pressed by the person at %s", name, r["main"][:12])
        return {
            "repo": name,
            "sha": r["main"],
            "started": r["inflight"]["at"],
            "log": r["inflight"]["log"],
            "checks": r.get("checks"),
            "checks_why": r.get("checks_why"),
        }

    async def rpc_clear_promote(self, repo: str = "", caller: Any = None) -> dict[str, Any]:
        """Dismiss's half (design §4.5a *Inbox row: promote*): clears a failure standing, after which
        promoting goes on. A person's own. `{repo, cleared}`, `cleared` false when none stood."""
        self._promote_person("clear_promote", caller)
        name = Path(self._promote_root(repo)).name
        had = await asyncio.to_thread(promote_mod.failed, name)
        await asyncio.to_thread(promote_mod.clear, name, "failed")
        if name in self._promotes:
            r = self._promotes[name]
            r["failed"] = None
            u = promote_mod.unmet(r)
            r["unmet"] = {"name": u[0], "text": u[1]} if u else None
        return {"repo": name, "cleared": had is not None}
