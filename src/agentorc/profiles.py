"""Profiles: (adapter, account, model), declared once per host in `~/.agentorc/profiles.yml` (design §4.2a).

```yaml
default: paul
profiles:
  paul:  {adapter: claude-code, account: paul,  model: opus,   config_dir: ~/.claude}
  grind: {adapter: claude-code, account: grind, model: sonnet, config_dir: ~/.claude-grind,
          permission_wait: 600, unattended_args: [--dangerously-skip-permissions]}
```

With no file, one implicit profile named `default` uses the tool's own default config directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sessionorc import paths

DEFAULT_PERMISSION_WAIT = 600  # seconds; long enough to reach a phone (design §4.2)


@dataclass
class Profile:
    name: str
    adapter: str = "claude-code"
    account: str = ""
    model: str | None = None
    config_dir: Path | None = None  # the tool's per-account config directory
    permission_wait: int = DEFAULT_PERMISSION_WAIT
    extra_args: list[str] = field(default_factory=list)
    unattended_args: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [self.adapter, self.account or self.name]
        if self.model:
            parts.append(self.model)
        return " · ".join(parts)


def profiles_file() -> Path:
    return paths.home() / "profiles.yml"


def load(path: Path | None = None) -> tuple[dict[str, Profile], str]:
    """Return (profiles by name, default profile name)."""
    path = path or profiles_file()
    if not path.is_file():
        return {"default": Profile(name="default")}, "default"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, Profile] = {}
    for name, raw in (data.get("profiles") or {}).items():
        raw = dict(raw or {})
        cfg = raw.pop("config_dir", None)
        out[name] = Profile(
            name=name,
            adapter=raw.pop("adapter", "claude-code"),
            account=str(raw.pop("account", "") or ""),
            model=raw.pop("model", None),
            config_dir=Path(cfg).expanduser() if cfg else None,
            permission_wait=int(raw.pop("permission_wait", DEFAULT_PERMISSION_WAIT)),
            extra_args=list(raw.pop("extra_args", []) or []),
            unattended_args=list(raw.pop("unattended_args", []) or []),
        )
    if not out:
        out["default"] = Profile(name="default")
    default = data.get("default") or next(iter(out))
    if default not in out:
        raise ValueError(f"profiles.yml: default {default!r} is not a declared profile ({sorted(out)})")
    return out, default


def get(name: str | None, path: Path | None = None) -> Profile:
    profiles, default = load(path)
    key = name or default
    try:
        return profiles[key]
    except KeyError:
        raise KeyError(f"unknown profile {key!r}; known: {sorted(profiles)}") from None
