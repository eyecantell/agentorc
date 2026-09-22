"""`.agentorc.yml`: one repo's checked-in configuration (design §5), and the role presets over it (§4.8).

```yaml
adapter: claude-code
worktrees: .claude/worktrees
anchor: main-checkout-single
unattended: {workers: 3, ...}         # kept as a block; the loader only knows it is present
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind, icon: wrench}
  manager: {grants: [control], controllers: []}
controllers: [manager-ao-1]           # who may act on a session started here; omitted = nobody
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
ROLE_KEYS = ("brief", "lane", "grants", "profile", "controllers", "icon", "label")
# A role's icon (design §4.8 *Role presets*, 2026-09-19, TD-074): one name from the fixed set the UI
# ships, never markup from a config file. Drawn small and monochrome inside the role badge — a
# label's picture and nothing more. An unknown name is refused when the file is read, as an unknown
# grant is; a role with no icon draws nothing.
ICONS = ("flag", "wrench", "search", "eye", "book", "shield", "terminal")
# `person` is the card's *interactive* mark — the person's own sessions (§4.5 *The card's anatomy*,
# TD-095) — so no role may wear it: a card never shows one glyph for two reasons.
RESERVED_ICONS = {"person": "reserved for the card's mark of an interactive session, the person's own (design §4.5)"}
# A role's display label (design §4.8 *The names*, TD-076): what the role badge, a team header and
# an Inbox row show in place of the bare key. A person's text, drawn escaped; nothing keys on it.
LABEL_CAP = 40
LANE_PLACEHOLDER = "{lane}"
# The team's techlead seat (design §4.9b): its session id, filled at launch as `{lane}` is; `none`
# where the team has none, or the session was started by hand, so a brief reads right either way.
TECHLEAD_PLACEHOLDER = "{techlead}"
NO_TECHLEAD = "none"
# The seat's primer (design §4.9b *Its standing context*): the `context:` path, relative to the home
# checkout, filled at launch; `none` without one, and the techlead brief then reads the repo's map.
CONTEXT_PLACEHOLDER = "{context}"
NO_CONTEXT = "none"

# The built-in presets (design §4.8's table): each a brief template shipped with the package
# (`agentorc/briefs/<role>.md`, `{lane}` filled at launch), a default lane shape, and its grants.
# None names a profile: profile names are the person's (§4.2a, §4.9).
PRESETS: dict[str, dict[str, Any]] = {
    "grinder": {"brief": "grinder.md", "lane": ["free-pick"], "grants": [], "icon": "wrench", "label": "Grinder"},
    "hunter": {"brief": "hunter.md", "lane": ["free"], "grants": [], "icon": "search", "label": "Hunter"},
    "manager": {"brief": "manager.md", "lane": [], "grants": ["control"], "icon": "flag", "label": "Manager"},
    # The go-between (design §4.9b, TD-075): answers teammates' questions from the record, passes the
    # rest up. No grants — it acts on no session; the design's `alarms` grant is not built.
    "techlead": {"brief": "techlead.md", "lane": [], "grants": [], "icon": "book", "label": "Tech lead"},
    "plain": {"brief": None, "lane": [], "grants": [], "icon": None},
}
DEFAULT_ROLE = "plain"
# Renamed roles, old → new (TD-055, TD-076, docs/glossary.md): the old name still resolves for one
# release, wherever a role is named — `--role`, a team definition, an `org.yml` or `.agentorc.yml`
# `roles:` key — and says so once per process, naming the new word. Looked up once, never chained
# (design §4.8 *The names*): no entry's target is itself an old word, so `orchestrator` was
# repointed to `manager` when `lead` was renamed, not left pointing at `lead`.
ROLE_ALIASES: dict[str, str] = {"orchestrator": "manager", "lead": "manager"}
# Reserved role names (design §4.8 *The names*): a word that is decided but not built. Refused by
# name wherever a role is named or defined, so it cannot arrive in live data meaning something
# the entry that builds it then has to read around. Empty since `techlead` was built (TD-075 step 1).
RESERVED_ROLES: dict[str, str] = {}
_warned: set[str] = set()


def canonical_role(name: str) -> str:
    """The role's current name; an old one is accepted with a deprecation line on stderr, and a
    reserved one is a `ValueError` saying why."""
    reserved(name, "role")
    new = ROLE_ALIASES.get(name)
    if new is None:
        return name
    _deprecated(name, new)
    return new


def reserved(name: str, where: str) -> None:
    """Refuse a reserved role name (`RESERVED_ROLES`) with its reason, not as an unknown role."""
    if name in RESERVED_ROLES:
        raise ValueError(f"{where} `{name}` is {RESERVED_ROLES[name]} (design §4.8)")


def _deprecated(old: str, new: str) -> None:
    if old not in _warned:
        _warned.add(old)
        print(
            f"role `{old}` is now `{new}` (TD-076); `{old}` is still accepted for one release — rename it",
            file=sys.stderr,
        )


def deprecated_team_key(old: str, new: str, where: str) -> None:
    if f"key:{old}" not in _warned:
        _warned.add(f"key:{old}")
        print(
            f"{where}: `{old}:` is now `{new}:` (TD-076); `{old}:` is still accepted for one release — rename it",
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
    icon: str | None = None  # one name from `ICONS` (§4.8), or None: the badge draws no picture
    label: str | None = None  # the display label (§4.8 *The names*); None is the default, `display`
    controllers: list[str] = field(default_factory=list)
    controllers_set: bool = False  # a layer said `controllers:` — an empty list then means *nobody*,
    # deliberately, and the repo's default is not fallen back to (review of PR #116)
    sources: list[str] = field(default_factory=list)  # `built-in`, `org`, `repo`: which layers spoke
    brief_source: str = ""  # which layer the brief came from: the template is read from there
    root: Path | None = None  # the repo the role was resolved in; where a repo brief path is relative to

    @property
    def display(self) -> str:
        """What the page shows for this role: its `label:`, or the default — the role's name with
        its first letter raised (design §4.8 *The names*)."""
        return self.label or default_label(self.name)

    @property
    def source(self) -> str:
        """`built-in`, `repo`, `built-in + repo` …: for `ao roles` and the form's note."""
        return " + ".join(self.sources) or "built-in"

    def brief_text(
        self,
        lane: list[str] | None = None,
        *,
        read: Reader | None = None,
        techlead: str | None = None,
        context: str | None = None,
    ) -> str | None:
        """The opening prompt this role gives a session: its template with `{lane}` filled from
        `lane` (default the role's own), `{techlead}` with the team's seat and `{context}` with the
        seat's primer (each `none` without one).
        None for a role without a brief (`plain`). `read` reads a
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
        text = text.replace(TECHLEAD_PLACEHOLDER, techlead or NO_TECHLEAD)
        text = text.replace(CONTEXT_PLACEHOLDER, context or NO_CONTEXT)
        return text.replace(LANE_PLACEHOLDER, ", ".join(lane if lane is not None else self.lane) or "(none given)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "brief": self.brief,
            "lane": list(self.lane),
            "grants": list(self.grants),
            "profile": self.profile,
            "icon": self.icon,
            "label": self.display,
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
        blocks = _mapping(value, where)
        for name in blocks:
            reserved(name, f"{where}.{name}: role")
        cfg.roles = {name: _role_block(name, raw, where) for name, raw in blocks.items()}
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
        raise ValueError(f"{here} must be a mapping ({', '.join(ROLE_KEYS)})")
    out: dict[str, Any] = {}
    for k, v in raw.items():
        k = str(k)
        if k not in ROLE_KEYS:
            raise ValueError(f"{here}.{k} is not a role key ({', '.join(ROLE_KEYS)})")
        if k in ("brief", "profile"):
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{here}.{k} must be a string")
            out[k] = v.strip() if isinstance(v, str) else None
        elif k == "icon":
            # One name from the set the UI ships (§4.8): checked when the file is read, exactly as a
            # grant is, so a typo is a line naming the key and never a blank badge on the page.
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{here}.icon must be a string")
            name = v.strip() if isinstance(v, str) else None
            if name in RESERVED_ICONS:
                raise ValueError(f"{here}.icon: {name!r} is {RESERVED_ICONS[name]}")
            if name is not None and name not in ICONS:
                raise ValueError(f"{here}.icon: unknown icon {name!r} (known: {', '.join(ICONS)})")
            out[k] = name
        elif k == "label":
            # a person's own words for the badge (§4.8 *The names*): one line of text, bounded so a
            # badge stays a badge; drawn escaped, and nothing keys on it (§9 invariant 9)
            if v is not None and (not isinstance(v, str) or not v.strip() or "\n" in v):
                raise ValueError(f"{here}.label must be one line of text")
            if isinstance(v, str) and len(v.strip()) > LABEL_CAP:
                raise ValueError(f"{here}.label is longer than {LABEL_CAP} characters")
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


def default_label(name: str) -> str:
    """A role's label when nothing gives one: its current name — an old one read through the
    renamed-roles table, so `orchestrator` reads *Manager* — with its first letter raised. Quiet:
    it names no rename on stderr, because the page draws old badges on every load."""
    name = ROLE_ALIASES.get(name, name)
    return name[:1].upper() + name[1:]


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
        if "icon" in block:
            role.icon = block["icon"]
        if "label" in block:
            role.label = block["label"]
        if "controllers" in block:
            role.controllers, role.controllers_set = list(block["controllers"]), True
    return role


def roles(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[Role]:
    """Every role resolved, for `ao roles` and the form's pick-list."""
    return [resolve_role(cfg, n, roles_overlay) for n in role_names(cfg, roles_overlay)]
