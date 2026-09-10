"""Screen rules (design §4.2, TD-015): the manifest format, matching, priority, evidence, and the
Claude Code manifest against the saved screens from the herdr spike."""

from pathlib import Path

import pytest

from agentorc.adapters.claude_code import RULES_FILE, ClaudeCodeAdapter
from sessionorc.screen import Manifest, Rule

pytestmark = pytest.mark.unit

SCREENS = Path(__file__).parent / "fixtures" / "screens"


def screen(name: str) -> list[str]:
    return (SCREENS / f"{name}.txt").read_text().splitlines()


def test_rule_semantics():
    r = Rule(id="r", state="limited", any=["limit"], all=["429"], not_=["resolved"], region=3)
    assert r.match(["429 too many", "limit hit", "ok"]) == ["429 too many", "limit hit"]
    assert r.match(["429 too many", "limit hit", "resolved", "x"]) is None  # `not` vetoes
    assert r.match(["429 too many", "x", "y", "limit hit"]) is None  # 429 fell outside the region
    assert r.match(["limit hit"]) is None  # `all` missing
    assert Rule(id="empty", state="idle").match(["anything"]) is None  # no positive pattern never fires
    assert Rule(id="ci", state="idle", any=["HELLO"]).match(["say hello"]) == ["say hello"]  # case-insensitive


def test_manifest_load_priority_and_pending(tmp_path):
    p = tmp_path / "rules.toml"
    p.write_text(
        'tool = "t"\nversion = 2\n'
        '[[rules]]\nid = "low"\nstate = "idle"\nany = ["x"]\n'
        '[[rules]]\nid = "high"\nstate = "needs-you"\npriority = 5\nany = ["x"]\n'
        'pending = {kind = "question", text = "$line"}\n'
    )
    m = Manifest.load(p)
    assert (m.tool, m.version, [r.id for r in m.rules]) == ("t", 2, ["high", "low"])
    got = m.explain(["a", "  the x line  "])
    assert got is not None and got.rule == "high" and got.state == "needs-you"
    assert got.pending is not None and got.pending.kind == "question" and got.pending.text == "the x line"
    assert got.to_dict()["evidence"] == ["  the x line  "]
    assert m.explain(["nothing"]) is None


@pytest.mark.parametrize(
    ("name", "rule", "state"),
    [
        ("trust-dialog", "trust-dialog", "needs-you"),
        ("usage-limit-reached", "usage-limit", "limited"),
        ("usage-limit-hit", "usage-limit", "limited"),
        ("rate-limited-429", "rate-limited-429", "limited"),
    ],
)
def test_claude_code_rules_on_the_spike_screens(name, rule, state):
    m = Manifest.load(RULES_FILE).explain(screen(name))
    assert m is not None and (m.rule, m.state) == (rule, state)
    assert m.pending is not None and m.pending.text


@pytest.mark.parametrize("name", ["trust-dialog", "usage-limit-reached", "usage-limit-hit", "rate-limited-429"])
def test_rules_fire_within_the_ticks_tail(name):
    """The tick hands the rules `TAIL_LINES` lines; every fixture's key line must be inside them, or
    the rule would fire under `ao explain` (40 lines) and never on a tick."""
    from sessionorc.agent import TAIL_LINES

    assert Manifest.load(RULES_FILE).explain(screen(name)[-TAIL_LINES:]) is not None


def test_claude_code_rules_stay_quiet_on_a_plain_prompt():
    ad = ClaudeCodeAdapter()
    assert ad.explain(screen("plain-prompt")) is None
    assert ad.classify(None, screen("plain-prompt")) is None
    assert ad.classify(None, screen("trust-dialog")) == "needs-you"


def test_painted_text_drops_faint_runs():
    from sessionorc.screen import painted_text

    assert painted_text("plain") == "plain"
    assert painted_text("\x1b[39m❯ \x1b[2mghost\x1b[0m tail") == "❯  tail"
    assert painted_text("\x1b[2mghost\x1b[22mreal\x1b[0m") == "real"
    assert painted_text("\x1b[1;2mbold faint\x1b[0;1mbold") == "bold"
    assert painted_text("\x1b[2mnever reset") == ""
    assert painted_text("\x1b]0;title\x07x\x1b[Ky") == "xy"
