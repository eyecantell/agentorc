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

from datetime import UTC, datetime, timedelta
from typing import Any

HISTORY_STEP = timedelta(minutes=10)
HISTORY_SPAN = timedelta(hours=3)
# Two resets this close are one window: the status line hands epoch seconds and the endpoint an
# instant of its own, and a rolling window's reset moves by a little between two reads of it.
RESET_SLACK = timedelta(minutes=5)


def _instant(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _pct(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value >= 0 else None


def clean_windows(raw: Any) -> list[dict[str, Any]]:
    """The windows of a report as the host agent keeps them — `{label, pct, resets}`, a label a
    short string, `pct` a number, `resets` an instant or None — and nothing else a caller sent."""
    out: list[dict[str, Any]] = []
    for w in raw if isinstance(raw, list) else ():
        if not isinstance(w, dict) or not isinstance(w.get("label"), str) or not w["label"].strip():
            continue
        if (pct := _pct(w.get("pct"))) is None:
            continue
        resets = w.get("resets")
        out.append(
            {
                "label": w["label"].strip()[:40],
                "pct": w["pct"] if isinstance(w["pct"], int) else round(pct, 1),  # an endpoint's whole number stays one
                "resets": str(resets) if _instant(resets) is not None else None,
            }
        )
    return out[:8]


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
