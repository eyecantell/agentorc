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

from sessionorc import agent_common, hosts
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
            auto, pull_on = await asyncio.to_thread(self._promote_auto), await asyncio.to_thread(self._pull_on)
            async with self._promote_lock:  # a press and the pass never start two runs of one repo
                fetched = None
                if full:  # the pull rides the full readings' cadence and fetches for them (§6 *Pull*)
                    occupied = {str(r): await asyncio.to_thread(self._pull_occupant_or_unread, r) for r in roots}
                    self._pulls, fetched = await asyncio.to_thread(
                        promote_mod.pulls, roots, pull_on, occupied, datetime.now(UTC)
                    )
                readings, notes, bad = await asyncio.to_thread(
                    promote_mod.survey, roots, dict(self._promotes), full, auto, datetime.now(UTC), fetched
                )
            if full:
                self._promote_read_at = time.monotonic()  # after the read: one that raised is retried next tick
            for root, why in bad.items():
                if self._promote_bad.get(root) != why:
                    log.warning("promote: skipping %s: %s", root, why)
            self._promote_bad = bad
            self._promotes = readings
            self._promotes_read = True
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

    def _pull_occupant_or_unread(self, root: str) -> str | None:
        """`pull_occupant`, a read that raises taken as unreadable — the pull waits on that root and
        the pass goes on over the others (review of PR #917)."""
        try:
            return self.pull_occupant(Path(root))
        except Exception:  # noqa: BLE001
            log.exception("pull: the occupants of %s could not be read", root)
            return ""

    @staticmethod
    def _pull_on() -> dict[str, bool]:
        """`repos.<repo>.pull` from `settings.yml`; a repo absent is pulled (§6 *Pull*: Paul's rule is the default)."""
        repos = settings_mod.repos(settings_mod.load())
        return {name: bool(v.get("pull", True)) for name, v in repos.items()}

    def _pulls_view(self) -> dict[str, Any]:
        """`pulls` on `host`: `{<repo>: {at, outcome, why, from, to, commits, occupant}}` (§6 *Pull*)."""
        return {name: dict(r) for name, r in sorted(self._pulls.items())}

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
        agent_common.person_only(caller, f"run {method}", "§6 *Promote*")
        if self.mode != "home":
            raise RpcError(f"{method} runs at the home (design §6 Promote): this host is a node")

    async def rpc_promote(
        self, repo: str = "", sha: str = "", back: bool = False, caller: Any = None
    ) -> dict[str, Any]:
        """The press (design §6 *Promote*, §4.7 `ao promote`, TD-132 slice 2): a person's own, refused
        to a session as `set_settings` is. Takes a fresh reading of the repo, refuses — naming it —
        on precondition (1) (the checkout on main's head, clean) or (3) (a run in flight, a failure
        standing), and goes on through (2), the checks, with what they read in the reply: the press
        is the person's word. Starts `run` detached and returns `{repo, sha, kind, from, started, log,
        checks, checks_why}`; the outcome is `check`'s on a later tick (§6), as it is for `auto`.

        With `sha` or `back` it is a **rollback** (§6 *A rollback*, TD-226): the commit resolved
        after the fetch (hex only, one commit, on main, not live already), or `back`'s — `from` in
        `last.json`, refused under a hold; `run` started in a detached worktree at it, so neither
        (1) nor a failure standing refuses it, and the checks it reports are that commit's."""
        self._promote_person("promote", caller)
        if sha and back:
            raise RpcError("promote takes --sha or --back, not both")
        if sha or back:
            return await self._rollback(repo, sha, back)
        root = self._promote_root(repo)
        name = Path(root).name
        async with self._promote_lock:
            readings, notes, bad = await asyncio.to_thread(promote_mod.survey, [root], {}, True, {}, datetime.now(UTC))
            # a run that reached its commit just now is concluded by this reading: its note is filed
            # here, or the person inbox would never hear of that promote (techlead's read of #666)
            for text in notes:
                log.info("promote: %s", text)
                self._system_note(PERSON, text)
            if root in bad:
                raise RpcError(bad[root])
            r = readings.get(name)
            if r is None:
                raise RpcError(f"{root} has no promote: block in its .agentorc.yml (design §5): nothing to press")
            # the fresh reading replaces the held one whatever the press does next, so the Inbox row
            # a refusal leaves standing reads what was just read
            r["auto"] = self._promotes.get(name, {}).get("auto", False)
            self._promotes[name] = r
            if (u := promote_mod.unmet(r, press=True)) is not None:
                raise RpcError(f"promote {name} refused — {u[1]} (design §6 Promote, precondition: {u[0]})")
            if r.get("live") == r["main"]:
                raise RpcError(f"{name}: live is main's head {r['main'][:7]} already — nothing to promote")
            block = await asyncio.to_thread(promote_mod.block, root)
            assert block is not None  # survey read it a moment ago
            frm = None if r.get("live_why") else r.get("live")
            r["inflight"] = await asyncio.to_thread(
                promote_mod.start, root, name, r["main"], block["run"], "person", r.get("ahead"), "promote", frm
            )
            u = promote_mod.unmet(r)
            r["unmet"] = {"name": u[0], "text": u[1]} if u else None
            self._promote_watch_at = float("-inf")  # the watch starts on the next tick
        log.info("promote: %s pressed by the person at %s", name, r["main"][:12])
        return {
            "repo": name,
            "sha": r["main"],
            "kind": "promote",
            "from": frm,
            "started": r["inflight"]["at"],
            "log": r["inflight"]["log"],
            "checks": r.get("checks"),
            "checks_why": r.get("checks_why"),
        }

    async def _rollback(self, repo: str, sha: str, back: bool) -> dict[str, Any]:
        """`promote` with a commit (§6 *A rollback*): the refusals in §6's order, each naming its
        reason, then `run` in the detached worktree. The hold is written when it concludes."""
        root = self._promote_root(repo)
        name = Path(root).name
        async with self._promote_lock:
            readings, notes, bad = await asyncio.to_thread(promote_mod.survey, [root], {}, True, {}, datetime.now(UTC))
            for text in notes:
                log.info("promote: %s", text)
                self._system_note(PERSON, text)
            if root in bad:
                raise RpcError(bad[root])
            r = readings.get(name)
            if r is None:
                raise RpcError(f"{root} has no promote: block in its .agentorc.yml (design §5): nothing to press")
            r["auto"] = self._promotes.get(name, {}).get("auto", False)
            self._promotes[name] = r
            if back:
                if r.get("held"):
                    raise RpcError(
                        f"promote {name} --back refused — a rollback's hold stands: the last promote was the rollback "
                        "itself; name the commit with --sha, or promote main (design §6 A rollback)"
                    )
                last = await asyncio.to_thread(promote_mod.last, name)
                if not last:
                    raise RpcError(f"promote {name} --back refused — no promote of {name} has concluded here yet")
                if not last.get("from"):
                    raise RpcError(
                        f"promote {name} --back refused — what was live before {str(last.get('sha'))[:7]} "
                        "was never read"
                    )
                sha = str(last["from"])
            full, why = await asyncio.to_thread(promote_mod.resolve, root, sha)
            if not full:
                raise RpcError(f"promote {name} --sha refused — {why} (design §6 A rollback)")
            if r.get("live") == full and not r.get("live_why"):
                raise RpcError(f"promote {name} --sha refused — {full[:7]} is live already")
            if full == r.get("main"):  # a hold on main's head would stand until a Dismiss (review of #757)
                raise RpcError(
                    f"promote {name} --sha refused — {full[:7]} is main's head: that is the plain press, ao promote"
                )
            if (u := promote_mod.unmet(r, press=True, kind="rollback")) is not None:
                raise RpcError(f"promote {name} --sha refused — {u[1]} (design §6 A rollback, precondition: {u[0]})")
            checks, checks_why = await asyncio.to_thread(promote_mod.read_checks, root, full)
            block = await asyncio.to_thread(promote_mod.block, root)
            assert block is not None  # survey read it a moment ago
            frm = None if r.get("live_why") else r.get("live")
            try:
                r["inflight"] = await asyncio.to_thread(
                    promote_mod.start, root, name, full, block["run"], "person", None, "rollback", frm
                )
            except ValueError as e:
                raise RpcError(f"promote {name} --sha failed to start — {e}") from None
            u = promote_mod.unmet(r)
            r["unmet"] = {"name": u[0], "text": u[1]} if u else None
            self._promote_watch_at = float("-inf")
        log.info("promote: %s rolled back by the person to %s from %s", name, full[:12], str(frm)[:12])
        return {
            "repo": name,
            "sha": full,
            "kind": "rollback",
            "from": frm,
            "started": r["inflight"]["at"],
            "log": r["inflight"]["log"],
            "checks": checks,
            "checks_why": checks_why or None,
        }

    async def rpc_clear_promote(self, repo: str = "", caller: Any = None) -> dict[str, Any]:
        """Dismiss's half (design §4.5a *Inbox row: promote*, §6 *The hold*): clears a failure
        standing, or a rollback's hold when no failure stands, after which promoting goes on. A
        person's own. `{repo, cleared, which}`: `which` is `failed`, `held` or None when neither stood."""
        self._promote_person("clear_promote", caller)
        name = Path(self._promote_root(repo)).name
        which = None
        if await asyncio.to_thread(promote_mod.failed, name):
            which = "failed"
        elif await asyncio.to_thread(promote_mod.held, name):
            which = "held"
        if which:
            await asyncio.to_thread(promote_mod.clear, name, which)
        if name in self._promotes:
            r = self._promotes[name]
            r[which or "failed"] = None
            u = promote_mod.unmet(r)
            r["unmet"] = {"name": u[0], "text": u[1]} if u else None
        return {"repo": name, "cleared": which is not None, "which": which}
