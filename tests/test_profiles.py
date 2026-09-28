"""TD-151 slice 1, design §4.2a *How a profile is billed*: `billing` and `prices` on a profile, checked
and never guessed, the label saying *metered*, and a metered profile never polled for a quota."""

from __future__ import annotations

import json
import unittest.mock as um

import pytest

from agentorc import profiles
from agentorc.adapters.claude_code import ClaudeCodeAdapter

pytestmark = pytest.mark.unit


def _load(tmp_path, body: str):
    f = tmp_path / "profiles.yml"
    f.write_text(body)
    return profiles.load(f)[0]


def test_billing_defaults_to_subscription_and_a_metered_profile_carries_its_prices(tmp_path):
    got = _load(
        tmp_path,
        "profiles:\n  paul: {account: paul}\n"
        "  api: {account: key, billing: metered, prices: {input: 3, output: 15, cache_read: 0.3}}\n"
        "  local: {billing: metered}\n",
    )
    assert got["paul"].billing == "subscription" and not got["paul"].metered and got["paul"].prices == {}
    assert got["api"].metered and got["api"].prices == {"input": 3.0, "output": 15.0, "cache_read": 0.3}
    assert got["api"].label == "claude-code · key · metered"
    assert got["local"].metered and got["local"].prices == {}  # a self-hosted model: tokens, no price


@pytest.mark.parametrize(
    "block, why",
    [
        ("{billing: prepaid}", "billing is subscription or metered"),
        ("{prices: {input: 3}}", "prices belongs to a metered profile"),
        ("{billing: metered, prices: [3]}", "prices is a mapping"),
        ("{billing: metered, prices: {tokens: 3}}", "prices.tokens is not a token kind"),
        ("{billing: metered, prices: {input: -1}}", "is a price per million tokens"),
        ("{billing: metered, prices: {input: yes}}", "is a price per million tokens"),
    ],
)
def test_a_wrong_billing_is_the_files_error_never_a_guess(tmp_path, block, why):
    with pytest.raises(ValueError, match=why):
        _load(tmp_path, f"profiles:\n  p: {block}\n")


def test_a_metered_profile_is_never_polled_for_a_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "profiles.yml").write_text(
        "profiles:\n  api: {account: key, billing: metered}\n  paul: {account: paul}\n"
    )
    ad = ClaudeCodeAdapter()
    with um.patch.object(ClaudeCodeAdapter, "usage", side_effect=AssertionError("polled")) as polled:
        assert ad.usage_for("api") == {"reason": "metered"}
        polled.assert_not_called()
    with um.patch.object(ClaudeCodeAdapter, "usage", return_value=None):
        assert ad.usage_for("paul") == {"reason": "error"}  # a subscription profile is polled as ever


# ── slice 2: the adapter's spend (design §4.3 *Spend per turn*) ─────────────────────────────────


def _entry(uid: str, mid: str, *, inp=10, out=5, cr=1000, cw=0, kind="assistant", model="claude-sonnet-5") -> str:
    usage = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cw,
    }
    return json.dumps({"type": kind, "uuid": uid, "timestamp": f"2026-09-27T10:00:0{uid[-1]}Z",
                       "message": {"id": mid, "model": model, "usage": usage}}) + "\n"  # fmt: skip


@pytest.fixture
def metered(tmp_path, monkeypatch):
    home, cfg = tmp_path / "home", tmp_path / "claude-api"
    home.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(home))
    (home / "profiles.yml").write_text(f"profiles:\n  api: {{account: key, billing: metered, config_dir: {cfg}}}\n")
    proj = cfg / "projects" / "-w-repo"
    proj.mkdir(parents=True)
    return proj


def test_spend_reads_every_transcript_past_its_cursor_one_turn_per_response(metered):
    main = metered / "s1.jsonl"
    sub = metered / "s1" / "subagents" / "agent-a.jsonl"
    sub.parent.mkdir(parents=True)
    # one response written as two content-block entries, a user line, and a second response
    main.write_text(_entry("u1", "m1") + _entry("u2", "m1") + '{"type": "user"}\n' + _entry("u3", "m2", cr=0, cw=200))
    sub.write_text(_entry("u4", "m3", model="claude-haiku-4-5"))
    got = ClaudeCodeAdapter().spend("api", {})
    assert got["reason"] == "ok"
    by = {t["id"]: t for t in got["turns"]}
    assert set(by) == {"u2", "u3", "u4"}  # m1 once, by its last entry; the subagent's turn billed too
    assert (by["u3"]["input"], by["u3"]["output"], by["u3"]["cache_read"], by["u3"]["cache_write"]) == (10, 5, 0, 200)
    assert by["u2"]["cost"] is None and by["u2"]["source"] == str(main) and by["u4"]["model"] == "claude-haiku-4-5"
    assert main.read_bytes()[by["u3"]["offset"] :].startswith(b'{"type": "assistant", "uuid": "u3"')
    assert got["cursors"] == {str(main): main.stat().st_size, str(sub): sub.stat().st_size}
    # nothing new: nothing counted, the cursors stand
    again = ClaudeCodeAdapter().spend("api", got["cursors"])
    assert again["turns"] == [] and again["cursors"] == got["cursors"]
    # a line still being written is left for the next read
    with main.open("a") as f:
        f.write(_entry("u5", "m4") + '{"type": "assistant", "uuid": "u6"')
    later = ClaudeCodeAdapter().spend("api", got["cursors"])
    assert [t["id"] for t in later["turns"]] == ["u5"] and later["cursors"][str(main)] < main.stat().st_size


def test_a_cursor_past_the_end_is_a_rewrite_read_from_the_top(metered):
    main = metered / "s1.jsonl"
    main.write_text(_entry("u1", "m1"))
    got = ClaudeCodeAdapter().spend("api", {str(main): 10_000})
    assert [t["id"] for t in got["turns"]] == ["u1"] and got["cursors"][str(main)] == main.stat().st_size


def test_spend_says_why_when_it_cannot_read(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "profiles.yml").write_text(f"profiles:\n  api: {{billing: metered, config_dir: {tmp_path / 'none'}}}\n")
    ad = ClaudeCodeAdapter()
    assert ad.spend("nope", {"x": 3}) == {"turns": [], "cursors": {"x": 3}, "reason": "no_profile"}
    got = ad.spend("api", {"x": 3})
    assert got["turns"] == [] and got["cursors"] == {"x": 3} and got["reason"].startswith("no transcripts under ")
