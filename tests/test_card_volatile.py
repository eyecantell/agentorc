"""An unreachable card on a volatile host sorts with `idle` (design §4.5 *One order, no control*, TD-004)."""

from __future__ import annotations

from agentorc.ui import cards
from sessionorc.models import STATE_RANK


def rec(host, state="unreachable"):
    return {"id": f"s@{host}", "name": "s", "state": state, "dir": "/tmp", "kind": "agent", "host": host}


def test_an_unreachable_card_sorts_by_whether_its_host_is_volatile(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text(
        "local:\n  name: kmaster\nnodes:\n  laptop: {volatile: true}\n  vps: {}\n  box: {volatile: false}\n"
    )
    assert cards.view(rec("laptop"))["rank"] == STATE_RANK["idle"]
    for host in ("vps", "box", "gone"):  # flagged off, unflagged, and a host the file no longer names
        assert cards.view(rec(host))["rank"] == STATE_RANK["unreachable"]
    assert cards.view(rec("laptop", state="working"))["rank"] == STATE_RANK["working"]  # only the silence moves


def test_this_hosts_own_local_entry_says_whether_it_is_volatile(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: laptop\n  volatile: true\n")
    assert cards.host_volatile("laptop")
    assert not cards.host_volatile("kmaster")
