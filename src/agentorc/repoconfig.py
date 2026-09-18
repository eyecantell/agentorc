"""`.agentorc.yml`: one repo's checked-in configuration (design §5), and the role presets over it (§4.8).

```yaml
adapter: claude-code
worktrees: .claude/worktrees
anchor: main-checkout-single
unattended: {workers: 3, ...}         # kept as a block; the loader only knows it is present
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind}
  lead: {grants: [control], controllers: []}
controllers: [orchestrator-ao-1]      # who may act on a session started here; omitted = nobody
ledger: docs/technical_debt.md
teams: {...}                          # §4.9; passed through for the team step
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands: [{name: test, run: pdm run test}]
```

Missing file: the defaults §5 lists. A malformed file: a `ValueError` naming the key. Read on every
use and cached nowhere (the profiles rule), by the clients only — the host agent never reads it, so
`sessionorc` stays free of it. Roles resolve lowest-first: the package's built-ins (`PRESETS`), an
org-level overlay (`roles_overlay`, which the callers fill from `org.yml` — this module never
reads that file itself), then the
repo's own `roles:`, each overriding per key.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from sessionorc.models import GRANT_ALIASES, GRANTS

FILE = ".agentorc.yml"
DEFAULT_ADAPTER = "claude-code"
DEFAULT_WORKTREES = ".claude/worktrees"
DEFAULT_ANCHOR = "main-checkout-single"
DEFAULT_LEDGER = "docs/technical_debt.md"
DEFAULT_READY_WHEN = ("tree_clean", "branch_pushed", "no_subagents")
ROLE_KEYS = ("brief", "lane", "grants", "profile", "controllers")
LANE_PLACEHOLDER = "{lane}"

# The built-in presets (design §4.8's table): each a brief template shipped with the package
# (`agentorc/briefs/<role>.md`, `{lane}` filled at launch), a default lane shape, and its grants.
# None names a profile: profile names are the person's (§4.2a, §4.9).
PRESETS: dict[str, dict[str, Any]] = {
    "grinder": {"brief": "grinder.md", "lane": ["free-pick"], "grants": []},
    "hunter": {"brief": "hunter.md", "lane": ["free"], "grants": []},
    "lead": {"brief": "lead.md", "lane": [], "grants": ["control"]},
    "plain": {"brief": None, "lane": [], "grants": []},
}
DEFAULT_ROLE = "plain"
# Renamed roles, old → new (TD-055, docs/glossary.md): the old name still resolves for one release,
# wherever a role is named — `--role`, a team definition, an `org.yml` or `.agentorc.yml` `roles:`
# key — and says so once per process, naming the new word.
ROLE_ALIASES: dict[str, str] = {"orchestrator": "lead"}
_warned: set[str] = set()


def canonical_role(name: str) -> str:
    """The role's current name; an old one is accepted with a deprecation line on stderr."""
    new = ROLE_ALIASES.get(name)
    if new is None:
        return name
    _deprecated(name, new)
    return new


def _deprecated(old: str, new: str) -> None:
    if old not in _warned:
        _warned.add(old)
        print(
            f"role `{old}` is now `{new}` (TD-055); `{old}` is still accepted for one release — rename it",
            file=sys.stderr,
        )


def deprecated_grant(old: str, where: str) -> None:
    if f"grant:{old}" not in _warned:
        _warned.add(f"grant:{old}")
        print(
            f"{where}: grant `{old}` is now `{GRANT_ALIASES[old]}` (TD-055); `{old}` is still accepted for one release",
            file=sys.stderr,
        )


def _aliased(blocks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """A `roles:` mapping with old keys folded into their new names. When a layer spells both, the
    new key's values win per key: it is the one the person wrote since the rename."""
    if not any(old in blocks for old in ROLE_ALIASES):
        return blocks
    out = {k: v for k, v in blocks.items() if k not in ROLE_ALIASES}
    for old, new in ROLE_ALIASES.items():
        if old in blocks:
            _deprecated(old, new)
            out[new] = {**(blocks[old] or {}), **(blocks.get(new) or {})}
    return out


# Reads one file by its absolute path and returns its text, or None when there is no such file;
# raises `OSError` when it exists and cannot be read. `_read_here` is this host's disk.
Reader = Callable[[Path], "str | None"]


def _read_here(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


@dataclass
class Role:
    """A preset, resolved: what `ao new --role` and the New session form fill in from it."""

    name: str
    brief: str | None = None  # a package template name (built-in) or a repo-relative path (repo)
    lane: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    profile: str | None = None
    controllers: list[str] = field(default_factory=list)
    controllers_set: bool = False  # a layer said `controllers:` — an empty list then means *nobody*,
    # deliberately, and the repo's default is not fallen back to (review of PR #116)
    sources: list[str] = field(default_factory=list)  # `built-in`, `org`, `repo`: which layers spoke
    brief_source: str = ""  # which layer the brief came from: the template is read from there
    root: Path | None = None  # the repo the role was resolved in; where a repo brief path is relative to

    @property
    def source(self) -> str:
        """`built-in`, `repo`, `built-in + repo` …: for `ao roles` and the form's note."""
        return " + ".join(self.sources) or "built-in"

    def brief_text(self, lane: list[str] | None = None, *, read: Reader | None = None) -> str | None:
        """The opening prompt this role gives a session: its template with `{lane}` filled from
        `lane` (default the role's own). None for a role without a brief (`plain`). `read` reads a
        repo's brief file — this host's disk by default, or another host's checkout across the link
        (design §4.4a "Teams across hosts", TD-057 step 4b.3); a built-in template is always the
        package's own."""
        if not self.brief:
            return None
        if self.brief_source == "built-in":
            text = resources.files("agentorc").joinpath("briefs", self.brief).read_text(encoding="utf-8")
        else:
            path = Path(self.brief).expanduser()
            if not path.is_absolute():
                path = (self.root or Path.cwd()) / path
            try:
                text = (read or _read_here)(path)
            except OSError as e:
                raise ValueError(f"role {self.name!r}: brief {path} cannot be read ({e.strerror or e})") from None
            if text is None:
                raise ValueError(f"role {self.name!r}: brief {path} cannot be read (no such file)")
        return text.replace(LANE_PLACEHOLDER, ", ".join(lane if lane is not None else self.lane) or "(none given)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "brief": self.brief,
            "lane": list(self.lane),
            "grants": list(self.grants),
            "profile": self.profile,
            "controllers": list(self.controllers),
            "source": self.source,
        }


@dataclass
class RepoConfig:
    root: Path | None = None  # the directory the file was looked for in
    path: Path | None = None  # the file itself, when it exists
    adapter: str = DEFAULT_ADAPTER
    worktrees: str = DEFAULT_WORKTREES
    anchor: str = DEFAULT_ANCHOR
    unattended: dict[str, Any] | None = None  # the whole block, or None: no unattended mode
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)  # the repo's own overrides, per key
    controllers: list[str] = field(default_factory=list)
    ledger: str = DEFAULT_LEDGER
    ready_when: list[str] = field(default_factory=lambda: list(DEFAULT_READY_WHEN))
    commands: list[dict[str, Any]] = field(default_factory=list)
    teams: dict[str, Any] = field(default_factory=dict)  # §4.9; read by the team step, passed through here


def load(repo_root: Path | str, *, read: Reader | None = None) -> RepoConfig:
    """`<repo>/.agentorc.yml`, or the defaults when there is none. `read` is how the file is read:
    this host's disk by default, another host's checkout across the link for a team started there
    (TD-057 step 4b.3) — one loader for both, `load_text`."""
    root = Path(repo_root).expanduser()
    return load_text((read or _read_here)(root / FILE), root)


def load_text(text: str | None, repo_root: Path | str) -> RepoConfig:
    """A repo's config from the text of its `.agentorc.yml` (None: the file is not there — the
    defaults), wherever that text was read."""
    root = Path(repo_root).expanduser()
    path = root / FILE
    cfg = RepoConfig(root=root)
    if text is None:
        return cfg
    cfg.path = path
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML ({e})") from None
    if data is None:
        return cfg
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a mapping of keys, not {type(data).__name__}")
    for key, value in data.items():
        _apply(cfg, str(key), value, path)
    return cfg


def discover(start: Path | str) -> RepoConfig:
    """The config for the repo `start` is in: the nearest `.agentorc.yml` walking up from `start`
    (a worktree carries the checked-in file, so a session's own directory finds it), stopping at
    the first `.git` — a directory session has no repo file and gets the defaults for its own dir."""
    here = Path(start).expanduser().resolve()
    for d in (here, *here.parents):
        if (d / FILE).is_file():
            return load(d)
        if (d / ".git").exists():
            return RepoConfig(root=d)
    return RepoConfig(root=here)


def _apply(cfg: RepoConfig, key: str, value: Any, path: Path) -> None:
    where = f"{path}: `{key}`"
    if key in ("adapter", "worktrees", "anchor", "ledger"):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where} must be a non-empty string")
        setattr(cfg, key, value.strip())
    elif key == "unattended":
        if value is None:
            cfg.unattended = None
        elif not isinstance(value, dict):
            raise ValueError(f"{where} must be a mapping (workers, brief, window, …)")
        else:
            cfg.unattended = dict(value)
    elif key == "roles":
        cfg.roles = {name: _role_block(name, raw, where) for name, raw in _mapping(value, where).items()}
    elif key == "controllers":
        cfg.controllers = _str_list(value, where)
    elif key == "ready_when":
        cfg.ready_when = _str_list(value, where)
    elif key == "commands":
        cfg.commands = _commands(value, where)
    elif key == "teams":
        cfg.teams = _mapping(value, where)
    else:
        raise ValueError(f"{where} is not a `.agentorc.yml` key (design §5)")


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    return {str(k): v for k, v in value.items()}


def _str_list(value: Any, where: str) -> list[str]:
    """`[a, b]` or a single `a` (a lane written as `free-pick`, say) → the list; nothing else."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        raise ValueError(f"{where} must be a list of names")
    return [x.strip() for x in items]


def _commands(value: Any, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of {{name, run}} entries")
    out = []
    for i, entry in enumerate(value):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("run"):
            raise ValueError(f"{where}[{i}] needs `name` and `run`")
        out.append(dict(entry))
    return out


def _role_block(name: str, raw: Any, where: str) -> dict[str, Any]:
    """One `roles.<name>:` entry, checked per key; only the keys given are kept, so the merge in
    `resolve_role` overrides exactly what the file says and nothing more."""
    here = f"{where}.{name}"
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{here} must be a mapping (brief, lane, grants, profile, controllers)")
    out: dict[str, Any] = {}
    for k, v in raw.items():
        k = str(k)
        if k not in ROLE_KEYS:
            raise ValueError(f"{here}.{k} is not a role key ({', '.join(ROLE_KEYS)})")
        if k in ("brief", "profile"):
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{here}.{k} must be a string")
            out[k] = v.strip() if isinstance(v, str) else None
        elif k == "grants":
            grants = _str_list(v, f"{here}.grants")
            for old in dict.fromkeys(g for g in grants if g in GRANT_ALIASES):
                deprecated_grant(old, f"{here}.grants")
            grants = list(dict.fromkeys(GRANT_ALIASES.get(g, g) for g in grants))
            if bad := [g for g in grants if g not in GRANTS]:
                raise ValueError(f"{here}.grants: unknown grant {bad[0]!r} (known: {', '.join(GRANTS)})")
            out[k] = grants
        else:  # lane, controllers
            out[k] = _str_list(v, f"{here}.{k}")
    return out


def role_names(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Every role that resolves here: the built-ins first, then what the layers add, each once."""
    return list(dict.fromkeys([*PRESETS, *_aliased(roles_overlay or {}), *_aliased(cfg.roles)]))


def resolve_role(cfg: RepoConfig, name: str, roles_overlay: dict[str, dict[str, Any]] | None = None) -> Role:
    """Built-in < `roles_overlay` (the org layer, a later step) < the repo's `roles:`, per key.
    An unknown name is a `KeyError` naming it and what would have resolved. A renamed role's old
    name resolves to the new one (`ROLE_ALIASES`), and the returned `Role` carries the new name."""
    name = canonical_role(name)
    layers = [
        ("built-in", PRESETS.get(name)),
        ("org", _aliased(roles_overlay or {}).get(name)),
        ("repo", _aliased(cfg.roles).get(name)),
    ]
    spoke = [(src, block) for src, block in layers if block is not None]
    if not spoke:
        raise KeyError(f"unknown role {name!r}; known: {', '.join(role_names(cfg, roles_overlay))}")
    role = Role(name=name, root=cfg.root)
    for src, block in spoke:
        role.sources.append(src)
        if "brief" in block:
            role.brief, role.brief_source = block["brief"], src
        if "lane" in block:
            role.lane = list(block["lane"])
        if "grants" in block:
            role.grants = list(block["grants"])
        if "profile" in block:
            role.profile = block["profile"]
        if "controllers" in block:
            role.controllers, role.controllers_set = list(block["controllers"]), True
    return role


def roles(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[Role]:
    """Every role resolved, for `ao roles` and the form's pick-list."""
    return [resolve_role(cfg, n, roles_overlay) for n in role_names(cfg, roles_overlay)]
