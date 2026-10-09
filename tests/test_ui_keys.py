"""The pages' keys (design §4.5a **keys** and **?** overlay, §4.5 *Keys on every page*, TD-124).

One table of (keys, page, control) in `app.js` is read by the `keydown` handler and by the `?`
overlay alike. What is tested here is that table, run as itself under node: every key in it names a
control the page draws, no key means two things on one page, and a key never fires while anything
editable has focus or a modifier other than Shift is held."""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

UI = pathlib.Path(__file__).resolve().parent.parent / "src" / "agentorc" / "ui"

PROBE = """
const fs = require("fs");
const noop = () => {};
const el = () => ({ dataset: {}, style: {}, addEventListener: noop, appendChild: noop,
  classList: { toggle: noop, add: noop, remove: noop, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [] });
const document = { documentElement: el(), body: el(), querySelector: () => null, querySelectorAll: () => [],
  addEventListener: noop, createElement: el };
global.window = {}; global.document = document;
global.localStorage = { getItem: () => null, setItem: noop };
global.matchMedia = () => ({ matches: false });
global.setInterval = noop; global.setTimeout = noop; global.clearTimeout = noop;
global.location = { pathname: "/", protocol: "http:", host: "x" };
global.fetch = () => Promise.reject(new Error("the probe makes no calls"));
eval(fs.readFileSync(process.argv[2], "utf8"));
const AO = window.AO;
const t = (tagName, o) => Object.assign({ tagName, closest: () => null }, o || {});
const ev = (key, o) => Object.assign({ key, target: t("DIV") }, o || {});
console.log(JSON.stringify({
  keys: AO.KEYS,
  pages: ["/", "/inbox", "/focus/ao-x", "/new"].map(AO.keyPage),
  names: {
    plain: AO.keyName(ev("j")),
    shift_enter: AO.keyName(ev("Enter", { shiftKey: true })),
    question: AO.keyName(ev("?", { shiftKey: true })),
    ctrl: AO.keyName(ev("k", { ctrlKey: true })),
    alt: AO.keyName(ev("1", { altKey: true })),
    meta: AO.keyName(ev("n", { metaKey: true })),
    input: AO.keyName(ev("j", { target: t("INPUT") })),
    textarea: AO.keyName(ev("j", { target: t("TEXTAREA") })),
    select: AO.keyName(ev("j", { target: t("SELECT") })),
    editable: AO.keyName(ev("j", { target: t("DIV", { isContentEditable: true }) })),
    terminal: AO.keyName(ev("j", { target: t("DIV", { closest: (s) => (s === ".xterm" ? {} : null) }) })),
  },
  entry: {
    org_a: (AO.keyEntry("org", "a") || {}).control,
    inbox_x: (AO.keyEntry("inbox", "x") || {}).control,
    focus_j: AO.keyEntry("focus", "j"),
    focus_1: (AO.keyEntry("focus", "1") || {}).control,
    // the digits on the Inbox: the page's, and the row's when the ringed row has answer buttons
    inbox_1: (AO.keyEntry("inbox", "1") || {}).control,
    inbox_1_answers: AO.keyEntry("inbox", "1", true),
    inbox_3: AO.keyEntry("inbox", "3"),
    inbox_g: (AO.keyEntry("inbox", "g", true) || {}).control,
  },
  labels: ["Open", "▣ Focus", "Snooze ▾", "Open board", "Dismiss"].map(AO.keyLabel),
  role_line: [AO.roleLine({ line: "the team's work" }), AO.roleLine({ line: "x", reply: true }), AO.roleLine({})],
  // the fold's default and the stored choice (TD-194): `get` is the store's, (key, default)
  folded: [
    AO.teamFolded("t", "2", (k, d) => d),
    AO.teamFolded("t", "0", (k, d) => d),
    AO.teamFolded("t", "2", (k, d) => (k === "fold:t" ? true : d)),
    AO.teamFolded("t", "0", (k, d) => (k === "fold:t" ? false : d)),
    AO.teamFolded("", "0", (k, d) => d),
  ],
  org_f: AO.keyEntry("org", "f"),
  // a row's own answer buttons, in order — never those of a row drawn inside it (TD-255)
  answers: (() => {
    const row = {}, inner = {};
    const b = (name, of) => ({ name, closest: () => of });
    row.querySelectorAll = () => [b("first", row), b("nested", inner), b("second", row)];
    return [AO.keyAnswers(row).map((x) => x.name), AO.keyAnswers(null)];
  })(),
}));
"""


def _probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the key table is JavaScript, and nothing else runs it")
    probe = pathlib.Path(tempfile.mkdtemp()) / "keys_probe.js"
    probe.write_text(PROBE)
    out = subprocess.run([node, str(probe), str(UI / "static" / "app.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _needle(sel: str) -> str:
    """What a selector's last compound part leaves in the markup: an id, an attribute, a class, or
    the element's own tag — enough to say the page draws the control, not a CSS engine."""
    last = re.split(r"[\s>]+", sel.split(",")[0].strip())[-1]
    if last.startswith("#"):
        return f'id="{last[1:]}"'
    m = re.search(r"\[([\w-]+)(?:=(\"[^\"]*\"))?\]", last)
    if m:
        return f"{m.group(1)}={m.group(2)}" if m.group(2) else f"{m.group(1)}="
    if "." in last:
        return 'class="' + last.split(".")[1]
    return "<" + last


def _org_html(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  local: true\n")
    from agentorc.ui.app import team_groups, templates, view

    records = [
        {
            "id": "ao-w",
            "name": "worker",
            "state": "needs-you",
            "dir": "/tmp/x",
            "kind": "agent",
            "team": "t",
            "pending": {"kind": "permission", "tool_use_id": "tu-1", "text": "Bash: ls"},
        },
    ]
    vs = [view(r, records) for r in records]
    return templates.get_template("org.html").render(
        sessions=vs,
        groups=team_groups(vs),
        counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [], "source": "", "notes": []},
        host="kmaster",
        active="Org",
        agent_down=False,
        volatile=False,
        usage={},
    )


@pytest.mark.unit
def test_every_key_names_a_control_the_page_draws(monkeypatch, tmp_path):
    """§4.5a **keys**: *a key is a name for a control in this table* and does nothing a button
    cannot. The Org is rendered with a card holding a permission, so its Allow and Deny are drawn;
    the Inbox's row controls are read from the row templates, which draw every kind of row."""
    got = _probe()
    org = _org_html(monkeypatch, tmp_path)
    parts = ("base.html", "inbox.html", "inbox_rail.html", "inbox_row.html")
    inbox = "".join((UI / "templates" / f).read_text() for f in parts)
    # the message page (TD-136): its own head and the entry's row, drawn by the row's macros
    msg = "".join((UI / "templates" / f).read_text() for f in ("base.html", "inbox_entry.html", "inbox_row.html"))
    # the overlay, and its ? for the mouse
    assert 'id="keyhelp"' in org and 'id="keyrows"' in org and 'id="keyhelpbtn"' in org
    assert 'class="card sc' in org and 'tabindex="0"' in org  # a card is a tab stop: the ring is its focus ring
    for k in got["keys"]:
        assert k["control"], k
        if not k.get("sel"):
            assert any(k.get(f) for f in ("move", "g", "help", "step")), k  # the few keys that press nothing
            continue
        focus = (UI / "templates" / "focus.html").read_text()  # the side panel's put-away (TD-412)
        pages = {"all": [org, inbox], "org": [org], "inbox": [inbox], "msg": [msg], "focus": [focus]}[k["page"]]
        # `/` focuses whichever filter box the page has: the Org's or the Inbox's
        needles = [_needle(s) for s in k["sel"].split(",")]
        for html in pages:
            assert any(n in html for n in needles), (k, needles)
        for word in k.get("text") or []:
            assert f">{word}<" in inbox or f"'{word}'" in inbox, (k, word)


@pytest.mark.unit
def test_no_key_means_two_things_on_one_page():
    """One exception, the design's own (§4.5a **keys**: the ring, TD-255): on a ringed Inbox row
    that has answer buttons `1`–`4` press them, and `1` and `2` are the page's on every other row —
    the `nth` entry, which `keyEntry` reaches only when told the row has answers."""
    got = _probe()
    for page in ("org", "inbox", "msg", "focus", "other"):
        seen = {}
        for k in got["keys"]:
            if k["page"] not in ("all", page) or k.get("nth"):
                continue
            for key in k["keys"]:
                assert key not in seen, f"{page}: {key} is both {seen[key]!r} and {k['control']!r}"
                seen[key] = k["control"]


@pytest.mark.unit
def test_a_key_fires_only_when_nothing_editable_has_focus():
    """§4.5 *Keys on every page*: the handler returns at once on an input, a textarea, anything
    `contenteditable` and the terminal's element, and when a modifier other than Shift is held."""
    got = _probe()
    assert got["pages"] == ["org", "inbox", "focus", "other"]
    n = got["names"]
    assert n["plain"] == "j" and n["shift_enter"] == "Shift+Enter" and n["question"] == "?"
    assert all(n[k] is None for k in ("ctrl", "alt", "meta", "input", "textarea", "select", "editable", "terminal"))
    e = got["entry"]
    assert e["org_a"].startswith("Allow") and e["inbox_x"].startswith("Dismiss")
    assert e["focus_j"] is None  # no ring on Focus: it shows one session (§4.5 screen 2)
    assert e["focus_1"] == "Org"  # the page keys work there
    assert got["labels"] == ["Open", "Focus", "Snooze", "Open board", "Dismiss"]


@pytest.mark.unit
def test_the_digits_yield_to_a_row_with_answer_buttons_and_g_is_go_with_it():
    """§4.5a **keys**: the ring (TD-255): `1`–`4` press a ringed row's answer buttons in the order
    written and are the page's on any other row; `3` and `4` name nothing but an answer; `g` is the
    row's **Go with it**. Both an `ask`'s suggested answers and a board item's are `.btn.answer`."""
    got = _probe()
    js = (UI / "static" / "app.js").read_text()
    e = got["entry"]
    assert e["inbox_1"] == "Org"
    nth = e["inbox_1_answers"]
    assert nth["nth"] and nth["ring"] and nth["sel"] == ".btn.answer" and nth["keys"] == ["1", "2", "3", "4"]
    assert e["inbox_3"]["nth"]  # no page key: on a row without answers the handler leaves it alone
    assert got["answers"] == [["first", "second"], []]
    assert "if (!k || (k.nth && !answers.length)) return;" in js
    assert (
        'const keeps = ["suspend", "board_decide", "board_reply", "board_notright", "board_add"].includes(key);' in js
    )  # the row stays
    assert e["inbox_g"] == "Go with it"
    assert [k["keys"] for k in got["keys"] if k.get("nth")] == [["1", "2", "3", "4"]]
    row = (UI / "templates" / "inbox_row.html").read_text()
    assert row.count('class="btn sm answer"') == 2  # the `answers` macro and the board row's, no third shape
    assert "AO.keyAnswers(ringed(page))" in js and "if (a && !a.disabled) a.click()" in js


@pytest.mark.unit
def test_the_composer_opens_on_the_roles_line_and_a_reply_shows_none():
    """§4.5a **Message** (TD-171): the composer's first line is the addressee's role's `message:`
    sentence, under its name; a Reply shows none, since the sender chose already."""
    assert _probe()["role_line"] == ["Message it about the team's work", "", ""]


@pytest.mark.unit
def test_a_team_opens_as_its_state_says_until_a_choice_is_stored():
    """§4.5a *team card: fold* (TD-194): a live team — a concluded one is live — opens unfolded and
    one with nothing live folded; a stored `fold:<team>` wins whatever the team's state; *No team*
    never folds. `f` on the Org is the ringed card's fold."""
    got = _probe()
    assert got["folded"] == [False, True, True, False, False]
    f = got["org_f"]
    assert f["ring"] and f["fold"] and f["sel"] == "[data-fold]" and f["control"].startswith("fold its team")
