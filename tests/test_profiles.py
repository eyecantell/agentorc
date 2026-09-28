"""TD-151 slice 1, design §4.2a *How a profile is billed*: `billing` and `prices` on a profile, checked
and never guessed, the label saying *metered*, and a metered profile never polled for a quota."""

from __future__ import annotations

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
