#!/usr/bin/env python3
# SYNCED FILE — canonical copy: eyecantell/dev-cadence files/scripts/ledger.py
# Edit it there and re-run sync.sh; an edit made in a consumer repo is overwritten (sync.sh --verify detects one).
"""What in the TD ledger may be picked now, and what is blocked on what (TD-064).

    ledger.py --pickable [--ledger PATH] [--archive PATH] [--json]

An entry may carry one optional field, between **Status:** and **Location:** (cadence.md §2):

    **Blocked by:** TD-047, TD-042
    **Blocked by:** decision (Paul) — docs/user_attention.md, 2026-09-25 item

Comma-separated entry IDs, then optionally ``decision (<who>)`` — last, because the pointer
after it runs to the end of the line and may hold anything. **Pickable is derived, never written:** an entry is pickable when
it has no Blocked by field, or every entry it names is archived and it names no decision.
So archiving a blocker makes its dependents pickable with no further edit.

An entry may also carry ``**Type:** debt | feature`` (cadence.md §2.11); no field reads as debt.
Within one Priority, debt is picked before features.

Prints the pick order — Priority (High, Medium, Low, then anything else), then Type (debt
before feature), then summary-table order — then the blocked entries with their blockers, then flags: a blocker that names no
entry in the ledger or the archive, an item the field cannot read, a Type it does not know
(read as debt), and a block whose
blockers are all archived (pickable now; the field can go). Which entries a given picker
also excludes (a sibling's lease, a brief's own rules) is the picker's business, not this.

Defaults: ``docs/technical_debt.md`` and ``docs/technical_debt_archive.md`` at the top of
the git work tree the command runs in. Offline, read-only. Exit 0; 2 when the ledger cannot
be read. A detector, never a gate (cadence.md §7).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^## (TD-(\d+)):\s*(.*)$", re.MULTILINE)
SECTION_RE = re.compile(r"^## ", re.MULTILINE)
ROW_RE = re.compile(r"^\|\s*(TD-(\d+))\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|", re.MULTILINE)
PRIORITY_RE = re.compile(r"^\*\*Priority:\*\*\s*(\w+)", re.MULTILINE)
# Parity (cadence.md §7): the §2 entry shape and the seed template's field write this.
BLOCKED_RE = re.compile(r"^\*\*Blocked by:\*\*[ \t]*(.*?)[ \t]*$", re.MULTILINE)  # one line only
# Parity (cadence.md §7): §2.11 and the seed template's field write this; TYPES is its vocabulary.
TYPE_RE = re.compile(r"^\*\*Type:\*\*[ \t]*(.*?)[ \t]*$", re.MULTILINE)
TYPES = ("debt", "feature")  # pick order within a Priority; the first is the default
ID_ITEM_RE = re.compile(r"^TD-(\d+)$")
DECISION_RE = re.compile(r"\bdecision\s*\(([^)]+)\)", re.IGNORECASE)
PRIORITIES = ("high", "medium", "low")


def _num(tid: str) -> int:
    return int(tid.split("-", 1)[1])


def entries(text: str) -> list[dict]:
    """The ledger's entry bodies, in file order: id, title, priority, blocked (raw or None)."""
    out = []
    for m in HEADING_RE.finditer(text):
        nxt = SECTION_RE.search(text, m.end())  # a body ends at the next `## ` heading of any kind
        body = text[m.end(): nxt.start() if nxt else len(text)]
        pm = PRIORITY_RE.search(body)
        bm = BLOCKED_RE.search(body)
        tm = TYPE_RE.search(body)
        if bm and not bm.group(1):
            bm = None  # an empty field (the template's line left blank) blocks nothing
        out.append({"id": m.group(1), "title": m.group(3).strip(),
                    "priority": pm.group(1) if pm else None,
                    "type_raw": tm.group(1) if tm and tm.group(1) else None,
                    "blocked_raw": bm.group(1) if bm else None})
    return out


def parse_blocked(raw: str) -> tuple[list[str], list[str], list[str]]:
    """(entry ids, decision holders, unreadable items) of one Blocked by value."""
    ids, who, bad = [], [], []
    d = DECISION_RE.search(raw)
    if d:
        who.append(d.group(1).strip())
        rest = raw[d.end():].strip()
        if rest.startswith(","):  # a list continuing past the decision: it was not written last
            bad.append(f"after the decision: {rest[1:].strip()}")
        raw = raw[: d.start()]  # otherwise what follows the decision is its pointer, never read
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        if ID_ITEM_RE.match(item):
            ids.append(item)
        else:
            bad.append(item)
    return ids, who, bad


def pickable(ledger: str, archive: str) -> dict:
    live = entries(ledger)
    live_nums = {_num(e["id"]) for e in live}
    archived = {int(m.group(2)) for m in HEADING_RE.finditer(archive)}
    rows = {int(m.group(2)): (i, m.group(4)) for i, m in enumerate(ROW_RE.finditer(ledger))}
    pick, blocked, flags = [], [], []
    for pos, e in enumerate(live):
        n = _num(e["id"])
        order, table_prio = rows.get(n, (len(rows) + pos, None))
        prio = e["priority"] or table_prio or "?"
        rank = PRIORITIES.index(prio.lower()) if prio.lower() in PRIORITIES else len(PRIORITIES)
        etype = (e["type_raw"] or TYPES[0]).lower()
        if etype not in TYPES:
            flags.append(f"⚠ {e['id']}: unknown Type {e['type_raw']!r} — write one of "
                         f"{' | '.join(TYPES)}; read as {TYPES[0]}")
            etype = TYPES[0]
        rec = {"id": e["id"], "title": e["title"], "priority": prio, "type": etype,
               "_key": (rank, TYPES.index(etype), order)}
        if e["blocked_raw"] is None:
            pick.append(rec)
            continue
        ids, who, bad = parse_blocked(e["blocked_raw"])
        for item in bad:
            flags.append(f"⚠ {e['id']}: cannot read Blocked by item {item!r} — "
                         "write TD-NNN or decision (<who>)")
        open_ids = []
        for b in ids:
            bn = _num(b)
            if bn in live_nums:
                open_ids.append(b)
            elif bn not in archived:
                flags.append(f"⚠ {e['id']}: Blocked by {b}, which names no entry in the ledger or the archive")
                open_ids.append(b)  # unknown: keep the block — the safe direction
        if not open_ids and not who and not bad:
            flags.append(f"ℹ {e['id']}: every blocker is archived ({', '.join(ids)}) — pickable; "
                         "the Blocked by field can go")
            pick.append(rec)
            continue
        rec["blocked_by"] = open_ids + [f"decision ({w})" for w in who] + [repr(b) for b in bad]
        blocked.append(rec)
    for lst in (pick, blocked):
        lst.sort(key=lambda r: r["_key"])
        for r in lst:
            del r["_key"]
    return {"pickable": pick, "blocked": blocked, "flags": flags}


def _toplevel() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                       timeout=10, check=False)
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else Path.cwd()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--pickable", action="store_true", required=True,
                    help="print the pick order, the blocked entries and the flags")
    ap.add_argument("--ledger", help="default: docs/technical_debt.md at the work tree's top")
    ap.add_argument("--archive", help="default: technical_debt_archive.md beside the ledger")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    ledger = Path(a.ledger) if a.ledger else _toplevel() / "docs" / "technical_debt.md"
    archive = Path(a.archive) if a.archive else ledger.with_name("technical_debt_archive.md")
    try:
        text = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"ledger: cannot read {ledger}: {e}", file=sys.stderr)
        return 2
    try:
        arch = archive.read_text(encoding="utf-8", errors="replace")
    except OSError:
        arch = ""  # no archive yet: nothing is archived
    res = pickable(text, arch)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print(f"Pickable ({len(res['pickable'])}), in pick order:")
    for r in res["pickable"]:
        print(f"  {r['id']:<7} {r['priority']:<7} {r['type']:<8} {r['title']}")
    if not res["pickable"]:
        print("  (none)")
    if res["blocked"]:
        print(f"Blocked ({len(res['blocked'])}):")
        for r in res["blocked"]:
            print(f"  {r['id']:<7} {r['priority']:<7} {r['type']:<8} {r['title']} — by {', '.join(r['blocked_by'])}")
    for f in res["flags"]:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
