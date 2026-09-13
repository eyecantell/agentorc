"""`.agentorc.yml`: one repo's checked-in configuration (design §5), and the role presets over it (§4.8).

```yaml
adapter: claude-code
worktrees: .claude/worktrees
anchor: main-checkout-single
unattended: {workers: 3, ...}         # kept as a block; the loader only knows it is present
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind}
  orchestrator: {grants: [orchestrate], controllers: []}
controllers: [orchestrator-ao-1]      # who may act on a session started here; omitted = nobody
ledger: docs/technical_debt.md
teams: {...}                          # §4.9; passed through for the team step
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands: [{name: test, run: pdm run test}]
```

Missing file: the defaults §5 lists. A malformed file: a `ValueError` naming the key. Read on every
use and cached nowhere (the profiles rule), by the clients only — the host agent never reads it, so
`sessionorc` stays free of it. Roles resolve lowest-first: the package's built-ins (`PRESETS`), an
org-level overlay (`roles_overlay`; `org.yml` is a later step and is not read here), then the
repo's own `roles:`, each overriding per key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from sessionorc.models import GRANTS

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
    "orchestrator": {"brief": "orchestrator.md", "lane": [], "grants": ["orchestrate"]},
    "plain": {"brief": None, "lane": [], "grants": []},
}
DEFAULT_ROLE = "plain"


@dataclass
class Role:
    """A preset, resolved: what `ao new --role` and the New session form fill in from it."""

    name: str
    brief: str | None = None  # a package template name (built-in) or a repo-relative path (repo)
    lane: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    profile: str | None = None
    controllers: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # `built-in`, `org`, `repo`: which layers spoke
    brief_source: str = ""  # which layer the brief came from: the template is read from there
    root: Path | None = None  # the repo the role was resolved in; where a repo brief path is relative to

    @property
    def source(self) -> str:
        """`built-in`, `repo`, `built-in + repo` …: for `ao roles` and the form's note."""
        return " + ".join(self.sources) or "built-in"

    def brief_text(self, lane: list[str] | None = None) -> str | None:
        """The opening prompt this role gives a session: its template with `{lane}` filled from
        `lane` (default the role's own). None for a role without a brief (`plain`)."""
        if not self.brief:
            return None
        if self.brief_source == "built-in":
            text = resources.files("agentorc").joinpath("briefs", self.brief).read_text(encoding="utf-8")
        else:
            path = Path(self.brief).expanduser()
            if not path.is_absolute():
                path = (self.root or Path.cwd()) / path
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                raise ValueError(f"role {self.name!r}: brief {path} cannot be read ({e.strerror or e})") from None
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


def load(repo_root: Path | str) -> RepoConfig:
    """`<repo>/.agentorc.yml`, or the defaults when there is none."""
    root = Path(repo_root).expanduser()
    path = root / FILE
    cfg = RepoConfig(root=root)
    if not path.is_file():
        return cfg
    cfg.path = path
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
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
            if bad := [g for g in grants if g not in GRANTS]:
                raise ValueError(f"{here}.grants: unknown grant {bad[0]!r} (known: {', '.join(GRANTS)})")
            out[k] = grants
        else:  # lane, controllers
            out[k] = _str_list(v, f"{here}.{k}")
    return out


def role_names(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Every role that resolves here: the built-ins first, then what the layers add, each once."""
    return list(dict.fromkeys([*PRESETS, *(roles_overlay or {}), *cfg.roles]))


def resolve_role(cfg: RepoConfig, name: str, roles_overlay: dict[str, dict[str, Any]] | None = None) -> Role:
    """Built-in < `roles_overlay` (the org layer, a later step) < the repo's `roles:`, per key.
    An unknown name is a `KeyError` naming it and what would have resolved."""
    layers = [
        ("built-in", PRESETS.get(name)),
        ("org", (roles_overlay or {}).get(name)),
        ("repo", cfg.roles.get(name)),
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
            role.controllers = list(block["controllers"])
    return role


def roles(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[Role]:
    """Every role resolved, for `ao roles` and the form's pick-list."""
    return [resolve_role(cfg, n, roles_overlay) for n in role_names(cfg, roles_overlay)]
