"""The adapter registry: built-ins, the entry-point adapter, and one broken adapter never taking
the others down (design §4.3)."""

import pytest

from sessionorc import adapters
from sessionorc.adapters import CommandAdapter, LaunchSpec, ShellAdapter

pytestmark = pytest.mark.unit


def test_builtins_and_entry_point_are_registered():
    assert {"shell", "command", "claude-code"} <= set(adapters.names())
    assert isinstance(adapters.get("shell"), ShellAdapter)
    assert isinstance(adapters.get("command"), CommandAdapter)
    assert adapters.get("claude-code").state_source == "hook"
    with pytest.raises(KeyError, match="unknown adapter 'nope'; known: \\["):
        adapters.get("nope")


class Good:
    name = "good-ep"
    state_source = "hook"

    def launch(self, *, profile, resume, prompt, unattended, cwd, name=""):
        return LaunchSpec(argv=None)

    def classify(self, pane, tail):
        return None


class FakeEntryPoint:
    def __init__(self, obj):
        self._obj = obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


def test_broken_entry_point_is_skipped(monkeypatch):
    """`get`/`names` only call load_all() on an empty registry and earlier tests filled it, so
    swap in an empty one (restored at teardown) and call load_all() directly."""
    monkeypatch.setattr(adapters, "_REGISTRY", {})
    monkeypatch.setattr(
        adapters,
        "entry_points",
        lambda group: [FakeEntryPoint(ImportError("boom")), FakeEntryPoint(Good), FakeEntryPoint(Good())],
    )
    adapters.load_all()
    assert adapters.names() == ["command", "good-ep", "shell"]
    assert isinstance(adapters.get("good-ep"), Good)  # a class is instantiated, an instance kept


def test_registry_restored_after_the_swap():
    assert "claude-code" in adapters.names() and "good-ep" not in adapters.names()
