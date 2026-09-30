"""An account's usage reading, merged from what its sessions report and what the endpoint answers
(design §4.4 *Usage, reported first and asked for last*, TD-233 slice 2).

A reading is `{windows: [{label, pct, resets, at, source, history}], fetched, source, by, reason}`.
Each window carries its own `at` (when a fresh report or an answer last confirmed its number) and
`source` (`reported` or `asked`); `fetched`, `source` and `by` on the reading are the newest
confirmation's, which is what the chip's age reads. Windows merge by what a window can do, not by
who spoke last: inside one `resets` its use never falls, so the highest percentage stands, and a
later `resets` replaces it outright. The short history beside each window — the newest reading and
one per `HISTORY_STEP` for `HISTORY_SPAN` before it — is what the gate's projection takes its rate
from (§6 *Usage gate*, slice 4). Adapter-neutral: labels are the adapter's, never named here."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

HISTORY_STEP = timedelta(minutes=10)
HISTORY_SPAN = timedelta(hours=3)
# Two resets this close are one window: the status line hands epoch seconds and the endpoint an
# instant of its own, and a rolling window's reset moves by a little between two reads of it.
RESET_SLACK = timedelta(minutes=5)
# The most history points a window can hold (one per step over the span, and the newest): what a
# reading from the home is cut to.
HISTORY_POINTS = int(HISTORY_SPAN / HISTORY_STEP) + 2


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


PCT_MAX = 1000  # a percentage past this is no reading: `_cap`'s `int()` of an `inf` would stop every pass


def _pct(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, int):
        return float(value) if 0 <= value <= PCT_MAX else None
    return value if math.isfinite(value) and 0 <= value <= PCT_MAX else None


def clean_windows(raw: Any) -> list[dict[str, Any]]:
    """The windows of a report as the host agent keeps them — `{label, pct, resets}`, a label a
    short string, `pct` a number, `resets` an instant or None — and nothing else a caller sent."""
    out: dict[str, dict[str, Any]] = {}
    for w in raw if isinstance(raw, list) else ():
        if not isinstance(w, dict) or not isinstance(w.get("label"), str) or not w["label"].strip():
            continue
        if (pct := _pct(w.get("pct"))) is None:
            continue
        resets = w.get("resets")
        label = w["label"].strip()[:40]
        n = {
            "label": label,
            "pct": w["pct"] if isinstance(w["pct"], int) else round(pct, 1),  # an endpoint's whole number stays one
            "resets": str(resets) if _instant(resets) is not None else None,
        }
        if label in out and out[label]["pct"] >= n["pct"]:
            continue  # a label twice in one report: the higher stands, as it would across two
        out[label] = n
    return list(out.values())[:8]


def _history(held: list[Any], at: str, pct: float) -> list[dict[str, Any]]:
    """`held` with (`at`, `pct`) added: the newest point always, one per `HISTORY_STEP` before it
    (a newest point less than a step after the one before it is replaced, not added to), and
    nothing older than `HISTORY_SPAN` behind the newest."""
    when = _instant(at)
    hist = [h for h in held if isinstance(h, dict) and _instant(h.get("at")) is not None]
    if when is None:
        return hist
    hist = [h for h in hist if _instant(h["at"]) < when]  # type: ignore[operator]
    if len(hist) >= 2 and _instant(hist[-1]["at"]) - _instant(hist[-2]["at"]) < HISTORY_STEP:  # type: ignore[operator]
        hist.pop()  # the last was only ever the newest point: this one supersedes it
    hist.append({"at": at, "pct": pct})
    return [h for h in hist if when - _instant(h["at"]) <= HISTORY_SPAN]  # type: ignore[operator]


def _older(a: Any, b: Any) -> bool:
    """Whether instant `a` is before instant `b`, both known."""
    ta, tb = _instant(a), _instant(b)
    return ta is not None and tb is not None and ta < tb


def _later(a: Any, b: Any) -> bool:
    """Whether reset `a` is later than reset `b` by more than `RESET_SLACK` — a known reset is
    later than an unknown one."""
    ta, tb = _instant(a), _instant(b)
    if ta is None:
        return False
    return tb is None or ta - tb > RESET_SLACK


def merge(
    held: dict[str, Any] | None,
    windows: list[dict[str, Any]],
    *,
    at: str,
    source: str,
    fresh: bool,
    by: str | None = None,
) -> dict[str, Any]:
    """`held` with `windows` folded in, as a new reading. `fresh` says the numbers were confirmed
    at `at` (an answer, or a report after a response): only then does any `at` move, and a report
    that is not fresh can still raise a number or bring a later reset, never an age. The
    endpoint's answer (`source` `asked`) is the account's own and sets each window it names
    outright, so a number it lowers is lowered; reports, each one session's view of it, merge by
    the rule above. A window
    `held` has and `windows` does not (the endpoint's per-model one, in a report) is kept as it
    was. `reason` is left as `held` had it: it is the last poll's word, not the report's."""
    held = held or {}
    old = {str(w.get("label")): w for w in held.get("windows") or () if isinstance(w, dict)}
    out: dict[str, dict[str, Any]] = {k: dict(v) for k, v in old.items()}
    moved = False
    for w in windows:
        o = old.get(w["label"])
        if source == "asked" and o is not None and _older(at, o.get("at")):
            continue  # an answer asked before a fresher report landed: the report stands
        if source == "asked":
            same = (
                o is not None and not _later(w["resets"], o.get("resets")) and not _later(o.get("resets"), w["resets"])
            )
            n = {**w, "at": at, "source": source}
            n["history"] = _history(list(o.get("history") or ()) if same else [], at, w["pct"])
        elif o is None or _later(w["resets"], o.get("resets")):
            n = {**w, "at": at if fresh else None, "source": source}
            n["history"] = _history([], at, w["pct"]) if fresh else []
        elif _later(o.get("resets"), w["resets"]):
            continue  # a report of the window before its reset: already over
        else:
            n = dict(o)
            n["pct"] = max(_pct(o.get("pct")) or 0.0, w["pct"])
            if fresh:
                n["at"], n["source"] = at, source
                n["history"] = _history(list(o.get("history") or ()), at, n["pct"])
        out[w["label"]] = n
        moved = moved or fresh
    reading: dict[str, Any] = {**held, "windows": list(out.values())}
    if moved or (fresh and source == "asked"):  # an answer is a confirmation even of no window
        reading.update(fetched=at, source=source)
        if by:
            reading["by"] = by
        else:
            reading.pop("by", None)
    reading.setdefault("reason", "ok")
    return reading


def adopt(held: dict[str, Any] | None, theirs: Any) -> dict[str, Any]:
    """`held`, a node's reading of an account, with the home's reading of it taken in (§4.4 *A
    node's sessions report to their node*): the home merges every host's reports and holds the
    account's reading, so each window the home confirmed later than the node did, or whose reset
    is later, is the home's — its number, `at` and history — and every other window stays as the
    node holds it, a window only one of them has included. `fetched`, `source` and `by` follow the
    newer confirmation. `reason`, `retry_after` and `cool_until` stay the node's: they are its own
    asks' word, and the home never asks for a node's account."""
    held = held or {}
    if not isinstance(theirs, dict):
        return held
    old = {str(w.get("label")): w for w in held.get("windows") or () if isinstance(w, dict)}
    out: dict[str, dict[str, Any]] = {k: dict(v) for k, v in old.items()}
    for w in theirs.get("windows") or ():
        if not isinstance(w, dict) or not (clean := clean_windows([w])):
            continue
        label = clean[0]["label"]
        o = old.get(label)
        # a roll is read only between two known resets: a window whose reset one side does not know
        # is the same window, and its newer confirmation stands (the review of this send)
        known = o is not None and _instant(o.get("resets")) is not None and _instant(clean[0]["resets"]) is not None
        if o is not None and not (known and _later(clean[0]["resets"], o.get("resets"))):
            if known and _later(o.get("resets"), clean[0]["resets"]):
                continue  # the home's is the window before its reset: the node has seen it roll
            if not (_older(o.get("at"), w.get("at")) or (o.get("at") is None and _instant(w.get("at")))):
                continue  # one window, and the node confirmed it no earlier than the home
        n: dict[str, Any] = {**clean[0], "at": w.get("at") if _instant(w.get("at")) else None}
        n["source"] = w.get("source") if w.get("source") in ("reported", "asked") else "reported"
        points = [
            {"at": str(h["at"]), "pct": p}
            for h in (w.get("history") if isinstance(w.get("history"), list) else ())
            if isinstance(h, dict) and _instant(h.get("at")) is not None and (p := _pct(h.get("pct"))) is not None
        ]
        n["history"] = sorted(points, key=lambda h: _instant(h["at"]))[-HISTORY_POINTS:]  # type: ignore[arg-type, return-value]
        out[label] = n
    reading: dict[str, Any] = {**held, "windows": list(out.values())}
    if _instant(theirs.get("fetched")) is not None and (
        _instant(held.get("fetched")) is None or _older(held.get("fetched"), theirs.get("fetched"))
    ):
        reading["fetched"] = str(theirs["fetched"])
        reading["source"] = theirs.get("source") if theirs.get("source") in ("reported", "asked") else "reported"
        if isinstance(theirs.get("by"), str) and theirs["by"]:
            reading["by"] = theirs["by"][:80]
        else:
            reading.pop("by", None)
    reading.setdefault("reason", "ok")
    return reading


def asked_only_due(reading: dict[str, Any] | None, now: datetime, every: float, watched: set[str]) -> bool:
    """Whether a window only the endpoint gives wants asking although the reading is young (§4.4
    *Usage*: once an hour, while a reserve names it or it stood within ten points of its cap): a
    window whose own `at` is older than `every` seconds while the reading's newest confirmation is
    a report."""
    if not reading or reading.get("source") != "reported":
        return False
    for w in reading.get("windows") or ():
        if not isinstance(w, dict) or w.get("source") != "asked":
            continue
        pct = _pct(w.get("pct")) or 0.0
        if str(w.get("label")) not in watched and pct < 90:
            continue
        when = _instant(w.get("at"))
        if when is None or (now - when).total_seconds() >= every:
            return True
    return False


# The projection (§6 *A reading the gate can no longer trust*, TD-233 slice 4): the rate is the rise
# per hour between the oldest and the newest kept points inside RATE_SPAN before the newest, and there
# is none unless they span RATE_MIN.
RATE_SPAN = timedelta(hours=2)
RATE_MIN = timedelta(minutes=20)


def rate(window: dict[str, Any]) -> float | None:
    """The window's rise in points per hour from its history, never below zero, or None when its
    kept points inside `RATE_SPAN` span less than `RATE_MIN`."""
    points = []
    for h in window.get("history") or ():
        if isinstance(h, dict) and (t := _instant(h.get("at"))) is not None and (p := _pct(h.get("pct"))) is not None:
            points.append((t, p))
    if not points:
        return None
    points.sort()
    newest = points[-1]
    inside = [pt for pt in points if newest[0] - pt[0] <= RATE_SPAN]
    oldest = inside[0]
    span = newest[0] - oldest[0]
    if span < RATE_MIN:
        return None
    return max(0.0, (newest[1] - oldest[1]) / (span.total_seconds() / 3600))


def project(
    windows: list[dict[str, Any]] | None, now: datetime, max_age: float | None, fetched: Any = None
) -> list[dict[str, Any]] | None:
    """The gate's one reader (§6 *A reading the gate can no longer trust*): each window as the gate
    reads it. A window confirmed within `max_age` seconds (its own `at`, else the reading's
    `fetched`), one with no age at all, one past its reset (`settings.lines` marks it unknown) and
    every window under `max_age` None (`off`) are the reading itself. An older one is **projected**
    — the held percentage plus its `rate` times its age, at most 100 — and carries `projected:
    {from, rate, age}` (the age in seconds); with no rate it is `unknown: "rate"` and carries `age`."""
    if windows is None or max_age is None:
        return windows
    out = []
    for w in windows:
        at = _instant(w.get("at")) or _instant(fetched)
        resets = _instant(w.get("resets"))
        held = _pct(w.get("pct"))
        if at is None or held is None or (resets is not None and resets <= now):
            out.append(w)
            continue
        age = (now - at).total_seconds()
        if age <= max_age:
            out.append(w)
            continue
        r = rate(w)
        if r is None:
            out.append({**w, "unknown": "rate", "age": round(age)})
            continue
        pct = min(100.0, held + r * age / 3600)
        out.append(
            {**w, "pct": round(pct, 1), "projected": {"from": w.get("pct"), "rate": round(r, 2), "age": round(age)}}
        )
    return out
