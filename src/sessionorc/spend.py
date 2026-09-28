"""A metered account's spend: the daily ledger and the summed reading (design §4.4 *Usage*, §4.2a
*How a profile is billed*, §6 *Usage gate*; TD-151 slice 3).

The adapter reports **turns** past a byte cursor per transcript (§4.3 *Spend per turn*); this module
adds them to a **ledger per account** — `spend.json` beside `usage.json`, one row per day in the
home's own clock holding tokens by kind and cost, and the cursors per host per transcript — and sums
the rows into the three fixed windows `day`, `week` and `month`, a reading of the same shape as a
polled one (`{windows: [{label, pct, resets}], fetched, reason}`), so the chip, the gate and
`ao status -v` read one kind of thing.

**A turn is counted once.** A cursor moves only after its row is written, and beside each cursor the
ledger keeps the last ledgered entry's `at`, `id`, `response` and tokens:

- a batch holding a turn **before** the cursor it was read from is a rewrite read from 0 again (a
  compaction): every entry before that `at` is dropped, and at that `at` the entries in file order
  up to and including the one with that `id` — every entry at that `at` when none carries it;
- a batch read from the cursor drops any entry **before** that `at`, since `at` never decreases in
  one file: a rewrite that left the file at or past the cursor is caught here (the techlead's read
  of #674);
- one API response is written as several entries, each carrying the response's usage, so a response
  whose entries straddle two reads comes back twice: the second time only what it adds over the
  tokens already ledgered for it is counted.

Checked 2026-09-27 against 328 of this host's Claude Code transcripts, 20 of them resumed: `claude
--resume` appends to the same transcript and no response appears in two files, so the dedup is per
transcript (the techlead's (2) on #674).

Tool-agnostic: a turn is `{at, id, source, offset, response, input, output, cache_read, cache_write,
cost}` and nothing here knows a tool by name. The prices are the profile's, handed in: a cache kind
without a price is charged at `input`, the safe side (§4.2a)."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sessionorc import paths
from sessionorc.models import tokens_short
from sessionorc.store import _atomic_write

KINDS = ("input", "output", "cache_read", "cache_write")
LABELS = ("day", "week", "month")
KEEP = timedelta(days=396)  # thirteen months of rows and of cursors not seen since (§4.4)
NOTE_AT = 80  # the eight-tenths note (§6 *Usage gate*): a fixed fraction, not a setting


def spend_file() -> Path:
    return paths.home() / "spend.json"


class SpendStore:
    """`spend.json`: `{account key: {days: {YYYY-MM-DD: {input, output, cache_read, cache_write,
    turns, cost}}, hosts: {host: {seeded: [profile], cursors: {source: {offset, seen, at, id,
    response, tokens}}}}, noted: {"<profile> <label>": resets}}}`. A missing or unreadable file is an
    empty ledger, never a crash — and, on the first tick after, every profile starts at its
    transcripts' ends again, so nothing is billed twice."""

    def __init__(self, path: Path | None = None):
        self.path = path or spend_file()

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def save(self, ledger: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.path, json.dumps(ledger, indent=1, sort_keys=True))


def _when(value: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _tokens(t: dict[str, Any]) -> dict[str, int]:
    out = {}
    for k in KINDS:
        v = t.get(k)
        out[k] = int(v) if isinstance(v, int | float) and not isinstance(v, bool) and v > 0 else 0
    return out


def cost_of(tokens: dict[str, int], given: Any, prices: dict[str, float] | None) -> float | None:
    """A turn's cost: the tool's own where it priced the turn, else the profile's prices per million
    tokens — a kind without a price charged at `input` (§4.2a) — else None: a priceless profile's
    reading is tokens."""
    if isinstance(given, int | float) and not isinstance(given, bool):
        return float(given)
    if not prices:
        return None
    base = prices.get("input", 0.0)
    return sum(n * prices.get(k, base) for k, n in tokens.items()) / 1_000_000


def _cursor_of(cur: Any) -> dict[str, Any]:
    return cur if isinstance(cur, dict) else {}


def ingest(
    acct: dict[str, Any],
    host: str,
    profile: str,
    result: dict[str, Any],
    prices: dict[str, float] | None,
    today: date,
    tz: Any = None,
) -> int:
    """Add one `spend` call's turns to the account's ledger `acct` (mutated) and move the cursors
    for `host`; the number of turns counted. The first call for `profile` on `host` counts nothing
    and only takes the cursors — the transcripts' ends — so a key that ran unmetered for months
    does not bill its history to the first day (§4.4); a transcript that appears later has no
    cursor and is read from 0."""
    h = acct.setdefault("hosts", {}).setdefault(host, {})
    seeded = h.setdefault("seeded", [])
    cursors = h.setdefault("cursors", {})
    seeding = profile not in seeded
    by_source: dict[str, list[dict[str, Any]]] = {}
    for t in result.get("turns") or ():
        if isinstance(t, dict) and isinstance(t.get("source"), str) and isinstance(t.get("offset"), int):
            by_source.setdefault(t["source"], []).append(t)
    counted = 0
    stamp = today.isoformat()
    for source, offset in (result.get("cursors") or {}).items():
        if not isinstance(offset, int):
            continue
        cur = _cursor_of(cursors.get(source))
        turns = sorted(by_source.pop(source, []), key=lambda t: t["offset"])
        keep = [] if seeding and not cur else _fresh(cur, turns)  # a shared directory's known transcript keeps counting
        for t, tokens in keep:
            _add(acct, t, tokens, prices, tz)
            counted += 1
        new = {"offset": offset, "seen": stamp}
        last = turns[-1] if turns else None
        if last is not None:
            new |= {
                "at": last.get("at"),
                "id": str(last.get("id") or ""),
                "response": str(last.get("response") or ""),
                "tokens": _tokens(last),
            }
        else:
            new |= {k: cur[k] for k in ("at", "id", "response", "tokens") if k in cur}
        cursors[source] = new  # moved after the row is written: the one write holds both
    if seeding:
        seeded.append(profile)
    return counted


def _add(
    acct: dict[str, Any], t: dict[str, Any], tokens: dict[str, int], prices: dict[str, float] | None, tz: Any
) -> None:
    """One turn into its day's row, on the day its `at` carries in `tz`."""
    when = _when(t.get("at"))
    day = (when.astimezone(tz) if when else datetime.now(tz)).date().isoformat()
    row = acct.setdefault("days", {}).setdefault(day, {k: 0 for k in KINDS} | {"turns": 0, "cost": None})
    for k in KINDS:
        row[k] = int(row.get(k) or 0) + tokens[k]
    row["turns"] = int(row.get("turns") or 0) + 1
    c = cost_of(tokens, t.get("cost"), prices)
    if c is not None:
        row["cost"] = round(float(row.get("cost") or 0.0) + c, 6)  # at this tick's prices, never rewritten


def _fresh(cur: dict[str, Any], turns: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, int]]]:
    """The turns of one transcript's batch not already in the ledger, each with the tokens to count
    (the module's docstring has the three rules)."""
    if not cur:
        return [(t, _tokens(t)) for t in turns]
    last_at = _when(cur.get("at"))
    rewrite = any(t["offset"] < int(cur.get("offset") or 0) for t in turns)
    if rewrite and last_at is not None:
        at_edge = [i for i, t in enumerate(turns) if _when(t.get("at")) == last_at]
        with_id = [i for i in at_edge if str(turns[i].get("id") or "") == cur.get("id")]
        cut = with_id[-1] if with_id else (at_edge[-1] if at_edge else -1)
        turns = [t for i, t in enumerate(turns) if i > cut and (w := _when(t.get("at"))) is not None and w >= last_at]
    elif last_at is not None:
        turns = [t for t in turns if (w := _when(t.get("at"))) is None or w >= last_at]
    out = []
    before = cur.get("tokens") if isinstance(cur.get("tokens"), dict) else {}
    for i, t in enumerate(turns):
        tokens = _tokens(t)
        if i == 0 and cur.get("response") and str(t.get("response") or "") == cur["response"]:
            # the same API response, straddling two reads: only what it adds is new
            tokens = {k: max(0, n - int(before.get(k) or 0)) for k, n in tokens.items()}
            if not any(tokens.values()):
                continue
        out.append((t, tokens))
    return out


def prune(acct: dict[str, Any], today: date) -> None:
    """Drop the rows and the cursors older than thirteen months (§4.4)."""
    oldest = (today - KEEP).isoformat()
    days = acct.get("days") or {}
    for d in [d for d in days if d < oldest]:
        del days[d]
    for h in (acct.get("hosts") or {}).values():
        cursors = h.get("cursors") or {}
        for s in [s for s, c in cursors.items() if str(_cursor_of(c).get("seen") or "") < oldest]:
            del cursors[s]


def bounds(now: datetime) -> dict[str, tuple[date, datetime]]:
    """Each window's first day and its reset, in `now`'s own zone (the home's): `day` rolls at
    midnight, `week` on Monday, `month` on the first (§4.2a)."""
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    nxt_month = (first + timedelta(days=32)).replace(day=1)

    def at(d: date) -> datetime:
        # a local midnight with **that** day's offset: `now` from `datetime.now().astimezone()` carries a
        # fixed offset, so a boundary past a DST change is converted through the system's zone
        naive = datetime(d.year, d.month, d.day)
        return naive.replace(tzinfo=UTC) if now.tzinfo is UTC else naive.astimezone()

    return {
        "day": (today, at(today + timedelta(days=1))),
        "week": (monday, at(monday + timedelta(days=7))),
        "month": (first, at(nxt_month)),
    }


def sums(acct: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    """`{label: {tokens: {kind: n}, total, cost, resets}}` over the ledger's rows, `cost` None when
    no row in the window was priced."""
    out = {}
    for label, (start, resets) in bounds(now).items():
        tokens = {k: 0 for k in KINDS}
        cost: float | None = None
        for d, row in (acct.get("days") or {}).items():
            if not isinstance(row, dict) or d < start.isoformat() or d > now.date().isoformat():
                continue
            for k in KINDS:
                tokens[k] += int(row.get(k) or 0)
            if isinstance(row.get("cost"), int | float):
                cost = (cost or 0.0) + float(row["cost"])
        out[label] = {
            "tokens": tokens,
            "total": sum(tokens.values()),
            "cost": None if cost is None else round(cost, 6),
            "resets": resets.isoformat(),
        }
    return out


def spent_of(amount: dict[str, Any], s: dict[str, Any]) -> float | None:
    """The window's spend in the amount's unit, or None when the amount is money and nothing was
    priced (a money amount on a priceless profile makes no line)."""
    if amount.get("unit") == "tok":
        return float(s["total"])
    return None if s["cost"] is None else float(s["cost"])


def reading(window_sums: dict[str, dict[str, Any]], amounts: dict[str, dict[str, Any]], fetched: str) -> dict[str, Any]:
    """The account's sums as this profile's reading: a `Window` per label, `pct` the spend over this
    profile's amount for that window (floored, so the pause lands at the amount and not a cent
    before) or None without one, and `spent`/`amount` beside it for the chip. The reading is the
    account's and the amount the profile's, exactly as a reserve is (§4.2a)."""
    windows = []
    for label in LABELS:
        s = window_sums[label]
        amount = amounts.get(label)
        pct = None
        if amount:
            got = spent_of(amount, s)
            if got is not None:
                pct = math.floor(round(100 * got / amount["value"], 6)) if amount["value"] > 0 else 100
        windows.append(
            {
                "label": label,
                "pct": pct,
                "resets": s["resets"],
                "spent": {"tokens": s["tokens"], "total": s["total"], "cost": s["cost"]},
                **({"amount": amount} if amount else {}),
            }
        )
    return {"windows": windows, "fetched": fetched, "reason": "ok"}


def say(amount: dict[str, Any], value: float) -> str:
    """An amount or a spend in its unit, as the note and the chip write it: `$4.10`, `1.2M tok`."""
    if amount.get("unit") == "tok":
        return f"{tokens_short(int(value))} tok"
    return f"${value:,.2f}".removesuffix(".00")


# -- a node's road (§4.4 *Usage*, §4.4a; TD-151 slice 4) ------------------------------------------

PIECE_BYTES = 1_000_000  # a `spend` frame's turns, well inside `link.FRAME_LIMIT`: a long batch goes in pieces


def pieces(result: dict[str, Any], budget: int | None = None) -> list[dict[str, Any]]:
    """One `spend` call's `{turns, cursors}` cut into pieces a link frame carries, in order. A piece
    ends at a turn's `offset` — its line's start — so the cursor it carries for a transcript cut
    across two pieces is where the next piece's first turn begins: the home resumes there, and a
    piece that never lands is read again from it. A transcript with no turns rides in the first
    piece; every other transcript's end is in the piece that holds its last turn."""
    budget = PIECE_BYTES if budget is None else budget
    ends = {s: o for s, o in (result.get("cursors") or {}).items() if isinstance(o, int)}
    by_source: dict[str, list[dict[str, Any]]] = {}
    for t in result.get("turns") or ():
        if isinstance(t, dict) and t.get("source") in ends and isinstance(t.get("offset"), int):
            by_source.setdefault(t["source"], []).append(t)
    out: list[dict[str, Any]] = [{"turns": [], "cursors": {s: o for s, o in ends.items() if s not in by_source}}]
    size = 0
    for source in sorted(by_source):
        for t in sorted(by_source[source], key=lambda t: t["offset"]):
            n = len(json.dumps(t))
            if out[-1]["turns"] and size + n > budget:
                if out[-1]["turns"][-1]["source"] == source:
                    out[-1]["cursors"][source] = t["offset"]  # cut inside this transcript: resume at this line
                out.append({"turns": [], "cursors": {}})
                size = 0
            out[-1]["turns"].append(t)
            size += n
        out[-1]["cursors"][source] = ends[source]
    return out


def figure(
    held: dict[str, dict[str, Any]] | None, turns: list[dict[str, Any]], prices: dict[str, float] | None, now: datetime
) -> dict[str, dict[str, Any]]:
    """A node's offline figure (§4.4 *Usage*): the account's sums the home last sent, a window whose
    reset has passed counted from nothing, plus this node's own turns read since its last
    acknowledged cursor — for the gate and the chip alone, written nowhere. The home's next sums
    replace it on reconnect."""
    extra: dict[str, Any] = {"days": {}}
    for t in turns:
        if isinstance(t, dict):
            _add(extra, t, _tokens(t), prices, now.tzinfo)
    mine = sums(extra, now)
    out = {}
    for label in LABELS:
        m = mine[label]
        h = (held or {}).get(label)
        if not isinstance(h, dict) or not (w := _when(h.get("resets"))) or w <= now:
            out[label] = m  # never held, or rolled since: this window starts from nothing
            continue
        tokens = {k: int((h.get("tokens") or {}).get(k) or 0) + m["tokens"][k] for k in KINDS}
        costs = [c for c in (h.get("cost"), m["cost"]) if isinstance(c, int | float)]
        out[label] = {
            "tokens": tokens,
            "total": sum(tokens.values()),
            "cost": round(sum(costs), 6) if costs else None,
            "resets": h["resets"],
        }
    return out
