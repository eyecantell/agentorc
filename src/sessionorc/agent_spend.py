"""A metered account's reading at the home (design §4.4 *Usage*, §4.2a, §6 *Usage gate*; TD-151
slice 3): on every tick the adapter's `spend` for each metered profile a live session runs under,
added to the daily ledger per account (`sessionorc.spend`, `spend.json`), summed into the three
windows and served under every profile of the account as a polled reading is — `_usage`, the
`usage` event, `usage.json` — with `pct` from that profile's amount. A mixin `HostAgent` inherits.

A node runs the same pass over its own sessions, and its turns reach the home over the link as
`spend {account, profile, prices, turns, cursors}` (slice 4): the ledger and its cursors are the
home's, and the account's sums come back to the node in the reply and as `usage {account, sums}`.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from typing import Any

from sessionorc import adapters, agent_common, link
from sessionorc import settings as settings_mod
from sessionorc import spend as spend_mod
from sessionorc.agent_common import _usage_key, log
from sessionorc.models import PERSON, now_iso

BILLING_KEEP = 60.0  # seconds a profile's billing, read for the gate, is kept before it is read again


def _offsets(held: dict[str, Any]) -> dict[str, int]:
    """`{source: offset}` from a ledger host's cursors, or a reply's plain ones."""
    out = {}
    for src, c in (held.get("cursors") or {}).items():
        v = c.get("offset") if isinstance(c, dict) else c
        if isinstance(v, int) and not isinstance(v, bool):
            out[str(src)] = v
    return out


def _amount(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


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
        if self.mode == "node":
            node_sums = await self._spend_to_home(groups, now)
        else:
            node_sums = {}
            await self._spend_at_home(groups, now)
        if self.mode == "home" and now.date().isoformat() != self._spend_pruned:
            self._spend_pruned = now.date().isoformat()
            for acct in self._spend.values():
                spend_mod.prune(acct, now.date())
        whole = settings_mod.load()
        amounts = settings_mod.amounts(whole)
        changed = False
        for key, g in groups.items():
            acct = self._spend.get(key) or {}
            window_sums = node_sums[key] if self.mode == "node" else spend_mod.sums(acct, now)
            for prof in g["profiles"]:
                reading = spend_mod.reading(window_sums, amounts.get(prof) or {}, now_iso())
                reading |= {"account": g["account"], "tool": g["tool"]}
                if why := self._spend_reason.get(prof):
                    reading["reason"] = why  # the chip's *spend unknown* (§4.3); the sum so far still stands
                if self.mode == "home":
                    # the person inbox is the home's: a node's accounts are noted in `_push_spend_usage`
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
        if self.mode == "home":
            await self._save_spend()
            await self._push_spend_usage(now)

    async def _save_spend(self) -> None:
        text = json.dumps(self._spend, sort_keys=True)
        if text != self._spend_saved:
            await asyncio.to_thread(self.spend_store.save, self._spend)
            self._spend_saved = text

    async def _spend_at_home(self, groups: dict[str, dict[str, Any]], now: datetime) -> None:
        """The home reads its own transcripts into the ledger, one profile after another."""
        for key, g in groups.items():
            acct = self._spend.setdefault(key, {})
            for prof, prices in sorted(g["profiles"].items()):
                # read one profile after another, in a fixed order: two profiles sharing a directory
                # read it once, and the first by name prices its turns
                host = (acct.get("hosts") or {}).get(self.host) or {}
                r = await self._read_spend(g["ad"], prof, _offsets(host))
                if r is not None:
                    spend_mod.ingest(acct, self.host, prof, r, prices, now.date(), now.tzinfo)

    async def _read_spend(self, ad: Any, prof: str, cursors: dict[str, int]) -> dict[str, Any] | None:
        """The adapter's `spend` from `cursors`, or None with the reason kept for the chip's *spend
        unknown* (§4.3) and logged once per change."""
        try:
            r = await asyncio.to_thread(ad.spend, prof, cursors)
        except Exception as e:  # noqa: BLE001 — the adapter's failure is a reason, never the pass's
            r = {"reason": f"error: {type(e).__name__}"}
        if not isinstance(r, dict) or r.get("reason") != "ok":
            why = str((r or {}).get("reason") or "error") if isinstance(r, dict) else "error"
            if self._spend_reason.get(prof) != why:
                log.info("spend for %s: %s", prof, why)  # once per change
            self._spend_reason[prof] = why
            return None
        self._spend_reason.pop(prof, None)
        return r

    # -- a node's turns (§4.4 *Usage*, §4.4a; TD-151 slice 4) --------------------------------------

    async def _spend_to_home(self, groups: dict[str, dict[str, Any]], now: datetime) -> dict[str, Any]:
        """A node's pass: the ledger and its cursors are the home's, so each metered profile's turns
        go home as `spend {account, profile, prices, turns, cursors}` and the reply's cursors are the
        ones read from next. The first call on each link is a read (no turns, no cursors): the
        cursors the home holds for this host, whether the profile is seeded there, and the account's
        sums. While a profile is not seeded, only the cursors travel — the home takes them as the
        ends — plus the turns of transcripts it already holds a cursor for (a shared directory's
        known transcript keeps counting); a long batch goes in pieces (`spend.pieces`). Offline, or
        when a send fails, the account's figure is the home's last sums plus this node's own turns
        past its last acknowledged cursors (`spend.figure`), written nowhere. Returns each account's
        window sums, which the caller makes each profile's reading from with this node's replica of
        the amounts, as the home does."""
        mux = self._home_mux if self._snapshot_sent else None
        out: dict[str, Any] = {}
        for key, g in groups.items():
            extra: list[dict[str, Any]] = []
            for prof, prices in sorted(g["profiles"].items()):
                if mux is not None and await self._spend_send(mux, key, g, prof, prices):
                    continue
                acked = self._spend_acked.get(prof)
                if acked is None:
                    continue  # never acknowledged here: nothing to read a figure from
                r = await self._read_spend(g["ad"], prof, acked)
                if r is not None:
                    for t in r.get("turns") or ():
                        if isinstance(t, dict) and t.get("source") in acked:
                            extra.append(t | {"cost": spend_mod.cost_of(spend_mod._tokens(t), t.get("cost"), prices)})
            out[key] = spend_mod.figure(self._spend_held.get(key), extra, None, now)
        return out

    async def _spend_send(self, mux: Any, key: str, g: dict[str, Any], prof: str, prices: dict[str, float]) -> bool:
        """One profile's read and send over `mux`; False when the link failed under it, and the
        figure is then this node's to make."""
        head = {"account": key, "label": g["account"], "tool": g["tool"], "profile": prof, "prices": prices}

        async def call(turns: list[dict[str, Any]], cursors: dict[str, int]) -> dict[str, Any]:
            reply = await mux.request("spend", timeout=link.LINK_SILENCE, **head, turns=turns, cursors=cursors)
            if not isinstance(reply, dict) or not isinstance(reply.get("cursors"), dict):
                raise link.LinkError(f"a spend reply without cursors: {reply!r}")
            state = {"cursors": _offsets(reply), "seeded": bool(reply.get("seeded"))}
            self._spend_link[prof] = state
            self._spend_acked[prof] = dict(state["cursors"])
            if isinstance(reply.get("sums"), dict):
                self._spend_held[key] = reply["sums"]
            return state

        try:
            state = self._spend_link.get(prof) or await call([], {})
            r = await self._read_spend(g["ad"], prof, state["cursors"])
            if r is None:
                return True  # the adapter's reason, not the link's: the held sums stand
            turns = [t for t in r.get("turns") or () if isinstance(t, dict)]
            if not state["seeded"]:
                turns = [t for t in turns if t.get("source") in state["cursors"]]
            cursors = {s: o for s, o in (r.get("cursors") or {}).items() if isinstance(o, int)}
            if not turns and state["seeded"] and cursors == state["cursors"]:
                return True  # nothing new: the home pushes the sums when another host moves them
            for piece in spend_mod.pieces({"turns": turns, "cursors": cursors}):
                await call(piece["turns"], piece["cursors"])
        except (link.LinkError, link.LinkClosed, TimeoutError) as e:
            # nothing moved that the home did not answer for: the next pass sends it again, and the
            # home drops what it already ledgered turn by turn (§4.4)
            log.warning("spend for %s did not reach %s: %s", prof, self.home, e)
            self._spend_link.pop(prof, None)
            return False
        return True

    def _take_usage(self, params: dict[str, Any]) -> None:
        """`usage {account, sums}` from the home (§4.4a): the account's window sums, from which the
        next pass makes each profile's reading with this node's replica of the amounts."""
        if self.mode == "node" and isinstance(params.get("sums"), dict) and params.get("account"):
            self._spend_held[str(params["account"])] = params["sums"]

    async def _take_spend(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """A node's `spend` (§4.4 *Usage*): its turns into the account's ledger at the home, under that
        host's cursors — each turn at or before the cursor held for its transcript dropped, so a
        resent batch is never counted twice — and the reply the cursors, whether the profile is
        seeded, and the account's sums. With no turns and no cursors it is a read. The home learns
        the node's accounts here, and pushes their sums to it as they move (`_push_spend_usage`)."""
        key, prof = str(params.get("account") or ""), str(params.get("profile") or "")
        turns, cursors = params.get("turns") or [], params.get("cursors") or {}
        if not key or not prof or not isinstance(turns, list) or not isinstance(cursors, dict):
            raise link.LinkError("a spend names an account and a profile, with turns and cursors")
        prices = params.get("prices") if isinstance(params.get("prices"), dict) else {}
        prices = {k: float(v) for k, v in prices.items() if _amount(v)}
        # a figure the node sent is kept only when it is one: a negative or non-finite cost or price
        # would poison the account's ledger for every host sharing it
        turns = [t | {"cost": t.get("cost") if _amount(t.get("cost")) else None} for t in turns if isinstance(t, dict)]
        now = datetime.now().astimezone()
        acct = self._spend.setdefault(key, {})
        if turns or cursors:
            spend_mod.ingest(acct, host, prof, {"turns": turns, "cursors": cursors}, prices, now.date(), now.tzinfo)
            await self._save_spend()
        named = self._spend_named.setdefault(host, {}).setdefault(key, set())
        named.add(prof)
        self._spend_node_prices[prof] = prices  # a profile only a node defines is still metered here
        window_sums = spend_mod.sums(acct, now)
        self._spend_told[(host, key)] = self._spend_mark(window_sums, named)
        h = (acct.get("hosts") or {}).get(host) or {}
        return {"cursors": _offsets(h), "seeded": prof in (h.get("seeded") or []), "sums": window_sums}

    def _spend_mark(self, window_sums: dict[str, Any], profiles: set[str]) -> str:
        """What a node's readings of an account would show: each named profile's `pct` per window
        from the amounts (the node's replica is this file) and each window's reset. A push goes when
        it changes — a whole point, or a roll (§4.4a)."""
        amounts = settings_mod.amounts(settings_mod.load())
        marks = {p: spend_mod.reading(window_sums, amounts.get(p) or {}, "")["windows"] for p in sorted(profiles)}
        return json.dumps({p: [(w["pct"], w["resets"]) for w in ws] for p, ws in marks.items()}, sort_keys=True)

    async def _push_spend_usage(self, now: datetime) -> None:
        """Each linked node's accounts, `usage {account, sums}`, where its readings moved since it was
        last told — by the home's own turns or another node's. A notification, refused not queued:
        a node whose link is down reads the sums in the reply to its first `spend` on the next dial.
        The eight-tenths note for a node's profiles is filed here, at the home, once per window."""
        amounts = settings_mod.amounts(settings_mod.load())
        noted: dict[str, set[str]] = {}
        for accounts in self._spend_named.values():
            for key, profiles in accounts.items():
                noted.setdefault(key, set()).update(profiles)
        for key, profiles in noted.items():
            if key in self._spend:
                window_sums = spend_mod.sums(self._spend[key], now)
                for prof in sorted(profiles):
                    self._spend_notes(
                        self._spend[key], prof, spend_mod.reading(window_sums, amounts.get(prof) or {}, "")
                    )
        for host, accounts in list(self._spend_named.items()):
            mux = self._link_muxes.get(host)
            if mux is None:
                continue
            for key, profiles in list(accounts.items()):
                acct = self._spend.get(key) or {}
                window_sums = spend_mod.sums(acct, now)
                mark = self._spend_mark(window_sums, profiles)
                if self._spend_told.get((host, key)) == mark:
                    continue
                try:
                    async with asyncio.timeout(agent_common.REPORT_WRITE):
                        await mux.notify("usage", account=key, sums=window_sums)
                except link.LinkClosed:
                    break
                except (link.LinkError, TimeoutError) as e:
                    log.warning("the spend sums did not reach %s: %s", host, e)
                    break
                self._spend_told[(host, key)] = mark

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

    def _billing_of(self, profile: str) -> dict[str, Any] | None:
        """`{billing, prices}` when `profile` is billed `metered`, else None — asked of the adapter of
        any record running under it, and with none of every adapter this host knows, so a person can
        set an amount before the profile's first session (`set_settings`, §6 *Usage gate*). A profile
        this host's adapters do not bill as metered but a node's `spend` has named is metered, at the
        prices the node declared: the home's `profiles.yml` need not define a node's profile."""
        records = [*self.sessions.values(), *(r for recs in self.remote.values() for r in recs.values())]
        names = sorted({r.adapter for r in records if r.profile == profile and r.adapter != "shell"})
        for name in names or adapters.names():
            try:
                fn = getattr(adapters.get(name), "billing_for", None)
                b = fn(profile) if fn else None
            except Exception as e:  # noqa: BLE001 — an adapter's lookup never stops the write
                log.warning("billing_for %s on %s failed: %s — read as a subscription", profile, name, e)
                b = None
            if isinstance(b, dict) and b.get("billing") == "metered":
                return {"billing": "metered", "prices": dict(b.get("prices") or {})}
        if profile in self._spend_node_prices:
            return {"billing": "metered", "prices": dict(self._spend_node_prices[profile])}
        return None
