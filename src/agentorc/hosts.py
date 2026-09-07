"""`~/.agentorc/hosts.yml` — the hosts the UI knows about (design §5). Phase 1 reads only the
`local` entry; ssh transport and volatile hosts arrive with phase 2 (TD-004).

```yaml
local:
  name: kmaster          # what the UI shows
  vscode_host: kmaster   # the ssh alias VS Code Remote-SSH resolves (your ~/.ssh/config)
  local: false           # true → vscode://file/… links (the UI runs on the machine you sit at)
```
"""

from __future__ import annotations

import functools
import os
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml

from sessionorc import paths


@dataclass
class Host:
    name: str
    vscode_host: str
    local: bool = False


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


def local_host() -> Host:
    """The host this UI runs on. Env vars override the file (kept for one-off runs); with neither,
    the machine's short hostname stands in for both fields."""
    data = _read_local(hosts_file())
    fallback = socket.gethostname().split(".")[0]
    name = os.environ.get("AGENTORC_HOST_NAME") or str(data.get("name") or fallback)
    vscode = os.environ.get("AGENTORC_VSCODE_HOST") or str(data.get("vscode_host") or name)
    local = os.environ.get("AGENTORC_LOCAL_HOST") == "1" or bool(data.get("local", False))
    return Host(name=name, vscode_host=vscode, local=local)
