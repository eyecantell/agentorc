"""A metered account's reading at the home (design §4.4 *Usage*, §4.2a, §6 *Usage gate*; TD-151
slice 3): on every tick the adapter's `spend` for each metered profile a live session runs under,
added to the daily ledger per account (`sessionorc.spend`, `spend.json`), summed into the three
windows and served under every profile of the account as a polled reading is — `_usage`, the
`usage` event, `usage.json` — with `pct` from that profile's amount. A mixin `HostAgent` inherits.

A node's turns reach the home over the link (`spend {account, turns}`) in slice 4; until then the
pass runs at the home alone, over the home's own sessions.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from sessionorc import adapters
from sessionorc import settings as settings_mod
from sessionorc import spend as spend_mod
from sessionorc.agent_common import _usage_key, log
from sessionorc.models import PERSON, now_iso

BILLING_KEEP = 60.0  # seconds a profile's billing, read for the gate, is kept before it is read again


def _metered_of(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], dict[str, float]]:
    """`{(adapter, profile): prices}` for each pair whose adapter says the profile is billed
    `metered` (`billing_for`, §4.3); a pair whose adapter has no such method, or cannot say, is a
    subscription — never guessed (§4.2a)."""
    out = {}
    for name, prof in pairs:
        try:
            fn = getattr(adapters.get(name), "billing_for", None)
            b = fn(prof) if fn else None
        except Exception:  # noqa: BLE001 — an adapter's lookup never stops the tick
            b = None
        if isinstance(b, dict) and b.get("billing") == "metered":
            prices = b.get("prices")
            out[(name, prof)] = dict(prices) if isinstance(prices, dict) else {}
    return out


class SpendMixin:
    async def _refresh_spend(self) -> None:
        """One pass, detached from the tick as the usage poll is (it reads transcripts)."""
        try:
            await self._refresh_spend_inner()
        except Exception:  # noqa: BLE001 — a detached task: log it, and the next tick tries again
            log.exception("the spend pass failed")

    async def _refresh_spend_inner(self) -> None:
        # one definition of live with the quota poll (`_usage_live`), or one pass would write a reading
        # the other drops, tick after tick (the techlead's read of #681)
        live = {(s.adapter, s.profile) for s in self._usage_live()}
        metered = await asyncio.to_thread(_metered_of, live)
        self._metered = {prof for _, prof in metered}
        self._billing_seen = {p: (time.monotonic(), p in self._metered) for _, p in live}
        groups: dict[str, dict[str, Any]] = {}  # account key → {account, tool, adapter, profiles: {prof: prices}}
        for (name, prof), prices in metered.items():
            ad = adapters.get(name)
            key, account = _usage_key(ad, name, prof)
            g = groups.setdefault(
                key, {"account": account, "tool": str(getattr(ad, "label", "") or name), "ad": ad, "profiles": {}}
            )
            g["profiles"][prof] = prices
        now = datetime.now().astimezone()  # the home's own clock: its midnight, Monday and first (§4.2a)
        for key, g in groups.items():
            acct = self._spend.setdefault(key, {})
            for prof, prices in sorted(g["profiles"].items()):
                # read one profile after another, in a fixed order: two profiles sharing a directory
                # read it once, and the first by name prices its turns
                host = (acct.get("hosts") or {}).get(self.host) or {}
                cursors = {
                    src: int(c.get("offset") or 0)
                    for src, c in (host.get("cursors") or {}).items()
                    if isinstance(c, dict)
                }
                try:
                    r = await asyncio.to_thread(g["ad"].spend, prof, cursors)
                except Exception as e:  # noqa: BLE001 — the adapter's failure is a reason, never the pass's
                    r = {"reason": f"error: {type(e).__name__}"}
                if not isinstance(r, dict) or r.get("reason") != "ok":
                    why = str((r or {}).get("reason") or "error") if isinstance(r, dict) else "error"
                    if self._spend_reason.get(prof) != why:
                        log.info("spend for %s (account %s): %s", prof, key, why)  # once per change
                    self._spend_reason[prof] = why
                    continue
                self._spend_reason.pop(prof, None)
                spend_mod.ingest(acct, self.host, prof, r, prices, now.date(), now.tzinfo)
        if now.date().isoformat() != self._spend_pruned:
            self._spend_pruned = now.date().isoformat()
            for acct in self._spend.values():
                spend_mod.prune(acct, now.date())
        whole = settings_mod.load()
        amounts = settings_mod.amounts(whole)
        changed = False
        for key, g in groups.items():
            acct = self._spend.get(key) or {}
            window_sums = spend_mod.sums(acct, now)
            for prof in g["profiles"]:
                reading = spend_mod.reading(window_sums, amounts.get(prof) or {}, now_iso())
                reading |= {"account": g["account"], "tool": g["tool"]}
                if why := self._spend_reason.get(prof):
                    reading["reason"] = why  # the chip's *spend unknown* (§4.3); the sum so far still stands
                self._spend_notes(acct, prof, reading)
                was = self._usage.get(prof) or {}
                if {k: v for k, v in was.items() if k != "fetched"} != {
                    k: v for k, v in reading.items() if k != "fetched"
                }:
                    self._usage[prof] = reading
                    changed = True
                    await self._broadcast({"event": "usage", "profile": prof, "usage": reading})
        # a metered profile no live session runs under any more leaves the top bar, as a polled one does
        shown = {p for g in groups.values() for p in g["profiles"]}
        for prof in [p for p in self._metered_shown if p not in shown and p in self._usage]:
            self._usage.pop(prof, None)
            changed = True
            await self._broadcast({"event": "usage", "profile": prof, "usage": None})
        self._metered_shown = shown
        if changed:
            self.usage_store.save(self._usage)
        text = json.dumps(self._spend, sort_keys=True)
        if text != self._spend_saved:
            await asyncio.to_thread(self.spend_store.save, self._spend)
            self._spend_saved = text

    def _spend_notes(self, acct: dict[str, Any], prof: str, reading: dict[str, Any]) -> None:
        """At eight tenths of an amount, one `system` note to the person inbox — *grind-api · day
        $4.10 of $5* — an FYI, uncounted, once per window (§6 *Usage gate*); the window is known by
        its `resets`, so the next day's crossing files the next note."""
        noted = acct.setdefault("noted", {})
        for w in reading["windows"]:
            pct, amount = w.get("pct"), w.get("amount")
            if not amount or not isinstance(pct, int) or pct < spend_mod.NOTE_AT:
                continue
            k = f"{prof} {w['label']}"
            if noted.get(k) == w["resets"]:
                continue
            noted[k] = w["resets"]
            got = spend_mod.spent_of(amount, {"total": w["spent"]["total"], "cost": w["spent"]["cost"]}) or 0.0
            text = f"{prof} · {w['label']} {spend_mod.say(amount, got)} of {spend_mod.say(amount, amount['value'])}"
            log.info("spend: %s", text)
            self._system_note(PERSON, text)

    def _is_metered(self, profile: str) -> bool:
        """Whether `profile` is billed `metered`, known **before** the gate judges (§4.4: keyed on the
        billing, read before the windows): from the spend pass when it has run, else asked of the
        adapter of any record running under it and kept for `BILLING_KEEP`. After a restart the gate
        runs before the detached pass has, and a profile read as a subscription would lose its
        amounts and lift every pause made at them (the techlead's read of #681)."""
        seen = self._billing_seen.get(profile)
        if seen is not None and time.monotonic() - seen[0] < BILLING_KEEP:
            return seen[1]
        records = [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]
        pairs = {(r.adapter, r.profile) for r in records if r.profile == profile and r.adapter != "shell"}
        metered = bool(_metered_of(pairs))
        self._billing_seen[profile] = (time.monotonic(), metered)
        return metered

    def _gate_reserves(self, whole: dict[str, Any], profile: str) -> dict[str, Any] | None:
        """The reserves the gate reads for `profile`: a subscription profile's percents, or — for a
        metered one — a line at the amount itself on each window that has one (§6 *Usage gate*:
        the amount is the window's 100, and a team's priority lowers it), since its `pct` is
        already the spend over the amount. Keyed on the profile's billing, never on the reading."""
        if self._is_metered(profile):
            by_label = settings_mod.amounts(whole).get(profile)
            return {label: 0 for label in by_label} if by_label else None
        return settings_mod.reserves(whole).get(profile)
