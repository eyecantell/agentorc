"""The adapter contract as the host agent sees it, plus the `shell` adapter and the registry.

`sessionorc` knows nothing about any particular tool. Hook-fed adapters (Claude Code, …) live in
`agentorc.adapters.*` and register through the `agentorc.adapters` entry-point group.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from sessionorc.models import Confidence, State
from sessionorc.tmux import PaneInfo

log = logging.getLogger("agentorc.adapters")

SHELLS = {"bash", "zsh", "sh", "fish", "dash", "ksh"}


@dataclass
class LaunchSpec:
    argv: list[str] | None  # None → the person's login shell
    env: dict[str, str] = field(default_factory=dict)
    adapter_id: str | None = None  # the tool's own session id when the adapter chose it at launch
    # the prompt the launch did not put in the argv (design §4.1 *No prose in the argv*, TD-339): the
    # host agent types it once the session reports its first `idle`; None when there is none
    first_prompt: str | None = None


@runtime_checkable
class Adapter(Protocol):
    name: str
    label: str  # the tool's display name ("Claude"): the usage chip's first word, never a key (§4.3, TD-122)
    state_source: Confidence

    def launch(
        self, *, profile: str, resume: str | None, prompt: str | None, unattended: bool, cwd: Path, name: str = ""
    ) -> LaunchSpec: ...

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        """Scraped state from the pane; None means "no opinion" (hook-fed adapters)."""
        ...

    # Optional, looked up with getattr:
    #   start_context: bool                               True when `launch` takes `start_context`, text the
    #                                                      session holds from its start with no turn run for it
    #                                                      (design §4.3, TD-283); `create` hands it only to such
    #                                                      an adapter, and refuses it in words for any other
    #   explain(tail) -> screen.Match | None               the screen-rule verdict with its evidence
    #                                                      (hook-fed adapters: applied as `scraped` only
    #                                                      when no fresher hook state exists; TD-015)
    #   title(pane_title: str) -> str | None              the session's name as the tool holds it, read from
    #                                                      the terminal title the tool set (tmux `#{pane_title}`,
    #                                                      carried on `PaneInfo.title`) with the tool's own
    #                                                      decoration removed; None when what is there is not a
    #                                                      name — the tool's default, a shell's `user@host: path`.
    #                                                      Display only: agentorc has no rename of its own
    #                                                      (design §4.3, §4.5a **title**, TD-074). An adapter
    #                                                      without it gives no title, which is the `shell` case
    #   external_sessions() -> list[ExternalSession]      live sessions of the tool started elsewhere
    #   model_in_use(session_id: str, cwd: Path, profile: str) -> str | None
    #                                                      the model the session is running now, if the tool
    #                                                      says anywhere (TD-031); None = cannot tell. Keyed by
    #                                                      the profile *name*, like `usage_for`
    #   context(session_id: str, cwd: Path, profile: str) -> dict | None
    #                                                      the session's context size now, `{tokens, at,
    #                                                      window}`: the prompt its last turn sent, that
    #                                                      turn's time, the model's window or None; None =
    #                                                      cannot tell (design §4.3, §6 rule 5, TD-190).
    #                                                      Keyed by the profile *name*, like `model_in_use`
    #   short_model(model: str) -> str                     that name as a display shortens it
    #   read_transcript(session_id: str, cwd: Path, profile: str, *, before: int | None, turns: int,
    #                   raw: bool) -> Transcript | None   the tool's transcript as the neutral shape below —
    #                                                      the last `turns` prompts and what followed them,
    #                                                      before byte `before` (None: the file's end); with
    #                                                      `raw`, the file's last `turns` lines as text. None
    #                                                      when there is no file. No field name of the tool's
    #                                                      leaves it (design §4.3, §4.5 screen 9, TD-165)
    #   account_for(profile: str) -> str | None           the account the profile runs under (§4.2a, TD-122):
    #                                                      usage is polled, cached and backed off once per
    #                                                      `(adapter, account)`; None, or no method, keys on
    #                                                      the profile itself
    #   usage_for(profile: str) -> dict | None            {"windows": [{"label", "pct", "resets"}, ...],
    #                                                      "fetched", "reason": "ok"} — every quota window this
    #                                                      account has, the labels the adapter's and printed by
    #                                                      nothing but the chip (TD-073); one window, three or
    #                                                      none are all legal. **Or why there is no reading**
    #                                                      (TD-087): {"reason": "rate_limited", "retry_after":
    #                                                      <seconds or None>} / {"reason": "no_credentials"} /
    #                                                      {"reason": "no_profile"} / {"reason": "error"} — a
    #                                                      word the core keys on, never prose. None is still
    #                                                      legal and means the same as "error". Never gates
    #                                                      anything (design §4.3 `usage()`, keyed by the profile
    #                                                      *name* because this package cannot build a Profile)
    #                                                      {"reason": "metered"}: the profile is billed by spend,
    #                                                      and its quota is never polled (§4.2a, TD-151)
    #   spend(profile: str, cursors: dict[str, int]) -> dict
    #                                                      a metered profile's turns since `cursors` (§4.3 *Spend
    #                                                      per turn*, TD-151): {"turns": [Turn], "cursors":
    #                                                      {transcript: byte offset}, "reason": "ok" | why};
    #                                                      a Turn is {at, id, source, offset, response, model,
    #                                                      input, output, cache_read, cache_write, cost} —
    #                                                      `response` the API response's id, so the home
    #                                                      counts a response once across reads; four token
    #                                                      kinds, never folded; `cost` None where the tool does
    #                                                      not price its own turns. A cursor past its
    #                                                      transcript's end is a rewrite, read from 0 again
    #   doctor_profiles() -> list[dict]                   each profile this adapter runs, as `ao doctor` reads it
    #                                                      (design §4.7, TD-465): {profile, account, config_dir,
    #                                                      metered, credentials: True | False | None, key (metered
    #                                                      only), layers: [{path, commands: [{command, resolves}]}
    #                                                      | {path, error}]}; or one {error} when the profiles
    #                                                      file does not parse
    #   billing_for(profile: str) -> dict | None           {"billing": "subscription" | "metered", "prices":
    #                                                      {kind: per million}} as the profile declares it
    #                                                      (§4.2a, TD-151): the home reads it before the cap
    #                                                      rule, and prices the turns with it. No method, or
    #                                                      None: a subscription — never guessed


# -- the transcript's neutral shape (design §4.5 screen 9 *The adapter renders, the core draws*) --

ENTRY_KINDS = ("prompt", "text", "thought", "tool", "compaction", "sidechain")


@dataclass
class TranscriptEntry:
    """One entry of a transcript as every client draws it. `kind` is one of `ENTRY_KINDS`: a
    `prompt` or `text` carries `text` and `at`; a `thought` its `text` and `lines`; a `tool` its
    `name`, the one-line `call` (its input's first line, as the pane draws it), its `result` (None
    until one came back) and, for a call that started a subagent, `sidechain`; a `sidechain` its
    `count` and `entries`; a `compaction` its `at`. Nothing here is named after a tool's field."""

    kind: str
    at: str | None = None
    text: str = ""
    lines: int = 0
    name: str = ""
    call: str = ""
    result: str | None = None
    count: int = 0
    entries: list[TranscriptEntry] = field(default_factory=list)
    sidechain: TranscriptEntry | None = None

    def to_dict(self) -> dict:
        """The entry with only the fields its kind carries: what the RPC and `--json` hand over."""
        out: dict = {"kind": self.kind}
        if self.at:
            out["at"] = self.at
        if self.kind in ("prompt", "text", "thought"):
            out["text"] = self.text
        if self.kind == "thought":
            out["lines"] = self.lines
        if self.kind == "tool":
            out.update(name=self.name, call=self.call, result=self.result)
            if self.sidechain is not None:
                out["sidechain"] = self.sidechain.to_dict()
        if self.kind == "sidechain":
            out.update(count=self.count, entries=[e.to_dict() for e in self.entries])
        return out


@dataclass
class Transcript:
    """What `read_transcript` returns: the file's `path` and `size`, its `first_at` and this read's
    `last_at`, the prompts in this read (`turns`), `before` — the byte offset that asks for the
    entries before these, None at the file's start — and the `entries`, oldest first. `raw` is the
    file's own lines when they were asked for instead."""

    path: str
    size: int
    first_at: str | None = None
    last_at: str | None = None
    turns: int = 0
    before: int | None = None
    entries: list[TranscriptEntry] = field(default_factory=list)
    raw: str | None = None

    def to_dict(self) -> dict:
        out = {
            "path": self.path,
            "size": self.size,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "turns": self.turns,
            "before": self.before,
            "entries": [e.to_dict() for e in self.entries],
        }
        if self.raw is not None:
            out["raw"] = self.raw
        return out


def short_model(adapter: str, model: str | None) -> str:
    """An observed model name as its adapter spells it short (TD-031) — `claude-fable-5-1` reads
    `fable-5-1` on a card. An adapter with no opinion, or none at all: the name as observed."""
    if not model:
        return ""
    try:
        fn = getattr(get(adapter), "short_model", None)
    except KeyError:
        fn = None
    return str(fn(model)) if fn else model


@dataclass
class ExternalSession:
    """A live session of the tool that agentorc did not start (a VS Code terminal, a plain
    `claude` in a shell): enough to enforce the anchor rule against it (design §9 invariant 2)."""

    adapter: str
    cwd: str
    name: str
    tool_id: str | None
    status: str | None


def external_sessions() -> list[ExternalSession]:
    """Every registered adapter's view of live sessions outside agentorc. Optional per adapter."""
    if not _REGISTRY:
        load_all()
    out: list[ExternalSession] = []
    for ad in list(_REGISTRY.values()):
        fn = getattr(ad, "external_sessions", None)
        if fn is None:
            continue
        try:
            out.extend(fn())
        except Exception:  # noqa: BLE001 — a broken registry never blocks a create
            continue
    return out


class ShellAdapter:
    """A plain shell: `idle` at the prompt, `working` while a foreground process runs, `exited`
    when the pane is dead. Scraped by definition (design §4.1)."""

    name = "shell"
    label = "Shell"  # a display name like any adapter's; a shell reports no quota, so it draws no chip
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
            log.exception("adapter %s failed to load and is unavailable", getattr(ep, "name", ep))
            continue
