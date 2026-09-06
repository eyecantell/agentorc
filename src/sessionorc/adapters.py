"""The adapter contract as the host agent sees it, plus the `shell` adapter and the registry.

`sessionorc` knows nothing about any particular tool. Hook-fed adapters (Claude Code, …) live in
`agentorc.adapters.*` and register through the `agentorc.adapters` entry-point group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from sessionorc.models import Confidence, State
from sessionorc.tmux import PaneInfo

SHELLS = {"bash", "zsh", "sh", "fish", "dash", "ksh"}


@dataclass
class LaunchSpec:
    argv: list[str] | None  # None → the person's login shell
    env: dict[str, str] = field(default_factory=dict)
    adapter_id: str | None = None  # the tool's own session id when the adapter chose it at launch


@runtime_checkable
class Adapter(Protocol):
    name: str
    state_source: Confidence

    def launch(
        self, *, profile: str, resume: str | None, prompt: str | None, unattended: bool, cwd: Path, name: str = ""
    ) -> LaunchSpec: ...

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        """Scraped state from the pane; None means "no opinion" (hook-fed adapters)."""
        ...


class ShellAdapter:
    """A plain shell: `idle` at the prompt, `working` while a foreground process runs, `exited`
    when the pane is dead. Scraped by definition (design §4.1)."""

    name = "shell"
    state_source: Confidence = "scraped"

    def launch(
        self, *, profile: str, resume: str | None, prompt: str | None, unattended: bool, cwd: Path, name: str = ""
    ) -> LaunchSpec:
        return LaunchSpec(argv=None)

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        if pane is None or pane.dead:
            return "exited"
        return "idle" if pane.current_command in SHELLS else "working"


class CommandAdapter(ShellAdapter):
    """A `kind: command` run: one argv, running or exited (design §4.5 Commands)."""

    name = "command"

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        if pane is None or pane.dead:
            return "exited"
        return "working"


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get(name: str) -> Adapter:
    if not _REGISTRY:
        load_all()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter {name!r}; known: {sorted(_REGISTRY)}") from None


def names() -> list[str]:
    if not _REGISTRY:
        load_all()
    return sorted(_REGISTRY)


def load_all() -> None:
    register(ShellAdapter())
    register(CommandAdapter())
    for ep in entry_points(group="agentorc.adapters"):
        try:
            obj = ep.load()
            register(obj() if isinstance(obj, type) else obj)
        except Exception:  # noqa: BLE001 — one broken adapter never takes the agent down
            continue
