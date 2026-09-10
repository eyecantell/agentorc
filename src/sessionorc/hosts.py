"""`~/.agentorc/hosts.yml` — the hosts agentorc knows about (design §5). Phase 1 reads only the
`local` entry, on the machine the UI and the host agent share; the ssh transport entries arrive
with phase 2 (TD-004). Both processes read it: the UI for the name and VS Code links, the host
agent for run-log retention.

```yaml
local:
  name: kmaster            # what the UI shows
  vscode_host: kmaster     # the ssh alias VS Code Remote-SSH resolves (your ~/.ssh/config)
  local: false             # true → vscode://file/… links (the UI runs on the machine you sit at)
  volatile: false          # true → a laptop: an unreachable agent is expected (asleep), not an alert
  repos_registry: ~/.config/dev-cadence/repos.txt   # one main-checkout path per line; `#` comments
  runs_keep_days: 30       # run logs of exited/closed sessions older than this are deleted; 0 keeps all
```

Without the file the machine's short hostname stands in for `name` and `vscode_host`, and every
other field takes its default. A malformed file is the same as no file: never a crash.
"""

from __future__ import annotations

import functools
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml

from sessionorc import paths

DEFAULT_REPOS_REGISTRY = "~/.config/dev-cadence/repos.txt"
DEFAULT_RUNS_KEEP_DAYS = 30


@dataclass
class Host:
    name: str
    vscode_host: str
    local: bool = False
    volatile: bool = False
    repos_registry: Path = Path(DEFAULT_REPOS_REGISTRY).expanduser()
    runs_keep_days: int = DEFAULT_RUNS_KEEP_DAYS

    def repos(self) -> list[str]:
        """The registry's main-checkout paths, in file order; a missing file is an empty list."""
        try:
            lines = self.repos_registry.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[str] = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and line not in out:
                out.append(line)
        return out


def hosts_file() -> Path:
    return paths.home() / "hosts.yml"


@functools.lru_cache(maxsize=8)
def _read_local_cached(path: str, mtime_ns: int) -> dict:
    """Parsed `local:` mapping, cached per (path, mtime) so a render loop does not re-parse YAML."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    local = doc.get("local") if isinstance(doc, dict) else None
    return local if isinstance(local, dict) else {}  # `local: true` / a list: not a mapping → ignore


def _read_local(p: Path) -> dict:
    try:
        return _read_local_cached(str(p), p.stat().st_mtime_ns)
    except OSError:
        return {}


def _days(v: object) -> int:
    """`runs_keep_days`: a non-negative int, else the default (a string, a float, a negative)."""
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return DEFAULT_RUNS_KEEP_DAYS
    return v


def local_host() -> Host:
    """The host this process runs on, from the file's `local` entry; with no file, the machine's
    short hostname stands in for both name fields."""
    data = _read_local(hosts_file())
    fallback = socket.gethostname().split(".")[0]
    name = str(data.get("name") or fallback)
    registry = data.get("repos_registry")
    return Host(
        name=name,
        vscode_host=str(data.get("vscode_host") or name),
        local=bool(data.get("local", False)),
        volatile=bool(data.get("volatile", False)),
        repos_registry=Path(str(registry) if registry else DEFAULT_REPOS_REGISTRY).expanduser(),
        runs_keep_days=_days(data.get("runs_keep_days", DEFAULT_RUNS_KEEP_DAYS)),
    )
