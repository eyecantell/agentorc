"""TD-258 slice 3, design §6 *Keeping a team running* rule 12: conventions relayed by the tick. The
home runs `scripts/cadence_changes.py --json` in a member's registry root, writes the headings the
member started with on its `conventions_seen`, and sends one `system` note for those that landed
after its `created`."""

from __future__ import annotations

import json

from sessionorc import conventions
from sessionorc.models import SYSTEM, Session


def _entry(day: str, title: str, landed: str | None) -> dict:
    return {"heading": f"## {day} — {title}", "date": day, "title": title, "body": "Do: it.", "landed": landed}


OLD = _entry("2026-09-27", "the rules are numbered", "2026-09-28T10:41:49-04:00")
NEW = _entry("2026-09-29", "a review comment's first line carries the verdict", "2026-09-29T17:18:40-04:00")
CREATED = "2026-09-29T12:00:00Z"  # after OLD landed (14:41Z the day before), before NEW (21:18Z)


def _view(e: dict) -> dict:
    return {k: e[k] for k in ("heading", "date", "title", "landed")}


def test_the_scripts_json_is_read():
    got = conventions.parse(json.dumps({"ref": "origin/trunk", "entries": [OLD, {"date": "x"}, "junk", NEW]}))
    assert got == {"ref": "origin/trunk", "entries": [_view(OLD), _view(NEW)]}
    for bad in ("", "not json", "[]", json.dumps({"ref": "origin/main"})):
        assert conventions.parse(bad) is None, bad


def test_no_script_is_no_reading(tmp_path):
    assert not conventions.has_script(tmp_path) and conventions.read(tmp_path) is None
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "cadence_changes.py"
    script.write_text("import sys\nsys.exit(2)\n")
    assert conventions.read(tmp_path) is None, "exit 2: the origin has no default branch"
    script.write_text(f"print({json.dumps(json.dumps({'ref': 'origin/main', 'entries': [OLD]}))})\n")
    assert conventions.read(tmp_path) == {"ref": "origin/main", "entries": [_view(OLD)]}


def test_what_a_reading_makes_of_a_record():
    unread = _entry("2026-09-30", "landed unknown", None)
    # the first reading: what landed at or before the create is seen untold, what landed after is told
    assert conventions.sort([NEW, OLD, unread], None, CREATED) == ([NEW["heading"], OLD["heading"]], [NEW])
    assert conventions.sort([OLD], None, CREATED) == ([OLD["heading"]], [])
    # a later reading: only a heading not held, and each once
    assert conventions.sort([NEW, OLD], [OLD["heading"]], CREATED) == ([OLD["heading"], NEW["heading"]], [NEW])
    assert conventions.sort([NEW, OLD], [OLD["heading"], NEW["heading"]], CREATED)[1] == []
    # an entry whose landed the script could not read waits for the next reading, then is sorted
    assert conventions.sort([unread], [], CREATED) == ([], [])
    late = {**unread, "landed": "2026-09-30T08:00:00+00:00"}
    assert conventions.sort([late], [], CREATED) == ([late["heading"]], [late])
    # a heading that turns out to have landed before the create joins without a word
    early = {**unread, "landed": "2026-09-01T08:00:00+00:00"}
    assert conventions.sort([early], [], CREATED) == ([early["heading"]], [])
    assert conventions.sort([OLD], None, "") is None, "a created that cannot be read writes nothing"


def test_the_fixed_words():
    assert conventions.note([NEW], "origin/main") == (
        "docs/cadence-changes.md gained 1 entry since you started: "
        '"2026-09-29 — a review comment\'s first line carries the verdict" — read it on `origin/main`, then go on'
    )
    four = [_entry("2026-10-01", f"rule {n}", "2026-10-01T00:00:00Z") for n in range(4)]
    assert conventions.note(four, "origin/trunk") == (
        'docs/cadence-changes.md gained 4 entries since you started: "2026-10-01 — rule 0", '
        '"2026-10-01 — rule 1", "2026-10-01 — rule 2" and 1 more — read them on `origin/trunk`, then go on'
    )
    assert '"an odd heading"' in conventions.note([{"heading": "## an odd heading"}], "origin/main")


# ── the tick ────────────────────────────────────────────────────────────────────────────────────


class _Script:
    """What the script would say in each root, and where it was run."""

    def __init__(self, monkeypatch):
        self.says: dict[str, list[dict] | None] = {}
        self.ran: list[str] = []
        monkeypatch.setattr(conventions, "read", self._read)

    def _read(self, root, **kw):
        self.ran.append(str(root))
        got = self.says.get(str(root))
        return None if got is None else {"ref": "origin/main", "entries": [_view(e) for e in got]}


def _member(agent, name: str, root: str, created: str = CREATED, **fields) -> Session:
    s = Session(id=f"ao-x-{name}", name=name, kind="agent", adapter="shell", dir="", repo=root, created=created)
    s.host = agent.host
    s.supervised, s.unattended, s.state = True, True, "working"
    for k, v in fields.items():
        setattr(s, k, v)
    agent.sessions[s.id] = s
    return s


def _notes(s: Session) -> list[str]:
    return [e.text for e in s.inbox if e.from_ == SYSTEM]


async def test_a_member_created_before_an_entry_landed_is_told_once_and_one_created_after_never(agent, monkeypatch):
    script, root = _Script(monkeypatch), "/repo"
    agent._repos[root] = {}
    before = _member(agent, "before", root)
    after = _member(agent, "after", root, created="2026-09-30T00:00:00Z")
    script.says[root] = [OLD]
    await agent._conventions_pass([before, after])
    assert before.conventions_seen["headings"] == [OLD["heading"]] and _notes(before) == []
    assert script.ran == [root], "one run a root, whatever its members"
    script.says[root] = [NEW, OLD]
    await agent._conventions_pass([before, after])
    assert _notes(before) == [conventions.note([_view(NEW)], "origin/main")]
    assert before.conventions_seen["headings"] == [OLD["heading"], NEW["heading"]]
    assert _notes(after) == [] and after.conventions_seen["headings"] == [OLD["heading"], NEW["heading"]]
    at = before.conventions_seen["at"]
    await agent._conventions_pass([before, after])
    assert len(_notes(before)) == 1 and before.conventions_seen["at"] == at, "told once, and nothing written again"


async def test_an_entry_landed_between_the_create_and_the_first_reading_is_told(agent, monkeypatch):
    script, root = _Script(monkeypatch), "/repo"
    agent._repos[root] = {}
    script.says[root] = [NEW, OLD]
    s = _member(agent, "w", root)
    await agent._conventions_pass([s])
    assert len(_notes(s)) == 1 and NEW["title"] in _notes(s)[0] and OLD["title"] not in _notes(s)[0]


async def test_a_seat_a_finished_member_and_a_root_with_no_reading_are_told_nothing(agent, monkeypatch):
    script, root = _Script(monkeypatch), "/repo"
    agent._repos[root], agent._repos["/silent"] = {}, {}
    script.says[root] = [NEW, OLD]
    quiet = {
        "seat": {"seat": {"trigger": "asks"}},
        "finished": {"out_of_work": {"at": "2026-09-29T13:00:00Z", "why": "nothing"}},
        "unsupervised": {"supervised": False},
        "replaced": {"superseded_by": "ao-x-next"},
        "noroot": {"repo": "/elsewhere"},
    }
    recs = [_member(agent, name, root, **fields) for name, fields in quiet.items()]
    await agent._conventions_pass(recs)
    assert script.ran == [] and all(r.conventions_seen is None and _notes(r) == [] for r in recs)
    silent = _member(agent, "silent", "/silent")
    await agent._conventions_pass([silent])
    assert script.ran == ["/silent"] and silent.conventions_seen is None, "no reading writes nothing"


async def test_a_restart_is_written_afresh(agent, monkeypatch):
    """The replay's record is a new one: no `conventions_seen`, a new `created`, so what its
    SessionStart hook printed is seen untold."""
    script, root = _Script(monkeypatch), "/repo"
    agent._repos[root] = {}
    script.says[root] = [NEW, OLD]
    old = _member(agent, "w", root)
    await agent._conventions_pass([old])
    assert len(_notes(old)) == 1
    again = _member(agent, "w", root, created="2026-09-30T00:00:00Z")
    assert again.conventions_seen is None
    await agent._conventions_pass([old, again])
    assert len(_notes(old)) == 1, "the record that is no longer the run is left alone"
    assert _notes(again) == [] and again.conventions_seen["headings"] == [NEW["heading"], OLD["heading"]]


def test_the_field_is_the_homes():
    from sessionorc import models

    assert "conventions_seen" in models.HOME_OWNED
