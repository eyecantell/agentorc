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

A top-level **`home: <host name>`** (design §4.4a, TD-057 step 2) makes this host agent a *node* of
that home; without it, or naming this host itself, the agent is the home — phase 1 exactly. The
link between them (step 3a) reads two more top-level keys:

```yaml
# on the home: who may dial in. A list of names, or a mapping when a node has flags.
nodes:
  laptop: {volatile: true}
  contractmatch: {container: {devcontainer: ~/contractmatch}}   # a container node the home runs
                                                               # (§4.4a, `sessionorc.containers`)
# on a node: how to dial — one `link:` key, of one of these shapes. Default: `ssh -T <home>`
# with the home's name as the ssh alias.
link: {ssh: kmaster}                        # or {command: [...]}, run as given (tests; any other transport)
# link: {socket: /agentorc/link/link.sock}  # a container node on the home's own machine (step 3c):
#                                           # the home's per-node socket, its directory mounted in
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
    # design §4.8a: `off | observe | enforce`, read by `sessionorc.identity.mode_of` ('' = the default)
    identity: str = ""
    # design §4.4a *A node that carries no person*: false on an agents-only node
    person: bool = True

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


@functools.lru_cache(maxsize=8)
def _read_home_cached(path: str, mtime_ns: int) -> str:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    v = doc.get("home") if isinstance(doc, dict) else None
    return v.strip() if isinstance(v, str) else ""  # `home: true` / a mapping: not a name → ignored


def home_name() -> str:
    """The host that holds the org's session graph (design §4.4a): the file's top-level `home:`,
    else this host. A malformed value is the same as none — never a crash, and never a node by
    accident."""
    p = hosts_file()
    try:
        named = _read_home_cached(str(p), p.stat().st_mtime_ns)
    except OSError:
        named = ""
    return named or local_host().name


@functools.lru_cache(maxsize=8)
def _read_top_cached(path: str, mtime_ns: int) -> dict:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _top() -> dict:
    p = hosts_file()
    try:
        return _read_top_cached(str(p), p.stat().st_mtime_ns)
    except OSError:
        return {}


def nodes() -> dict[str, dict]:
    """The hosts authorised to link to this home (design §4.4a "Who may connect"): `nodes:` as a
    list of names or a mapping of name → flags. Anything else is nobody — a malformed list never
    authorises a host by accident."""
    raw = _top().get("nodes")
    if isinstance(raw, list):
        return {n.strip(): {} for n in raw if isinstance(n, str) and n.strip()}
    if isinstance(raw, dict):
        return {
            n.strip(): (v if isinstance(v, dict) else {}) for n, v in raw.items() if isinstance(n, str) and n.strip()
        }
    return {}


# What keeps an unattended dialer from hanging on a prompt, and notices a dead peer within a minute.
SSH_OPTIONS = ("-T", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3")


def link_command() -> list[str]:
    """How this node dials its home (design §4.4a "Where to dial"): `link: {command: [...]}` as
    given, else `ssh -T <target> agentorc-agent link` with `link: {ssh: <target>}` or the home's
    name as the target. The remote command is decoration — the key's forced command replaces it."""
    raw = _top().get("link")
    cfg = raw if isinstance(raw, dict) else {}
    command = cfg.get("command")
    if isinstance(command, list) and command and all(isinstance(c, str) for c in command):
        return list(command)
    target = cfg.get("ssh")
    target = target.strip() if isinstance(target, str) and target.strip() else home_name()
    return ["ssh", *SSH_OPTIONS, target, "agentorc-agent", "link"]


def link_socket() -> Path | None:
    """A container node's way to its home (design §4.4a "A container node", TD-057 step 3c): the
    per-node link socket the home binds, reached through the mounted directory — no ssh, no key,
    no network. `link: {socket: <path>}`; None when the node dials by command."""
    raw = _top().get("link")
    cfg = raw if isinstance(raw, dict) else {}
    s = cfg.get("socket")
    return Path(s.strip()).expanduser() if isinstance(s, str) and s.strip() else None


def is_node() -> bool:
    """This host agent dials a home that is not itself."""
    return home_name() != local_host().name


def _read_local(p: Path) -> dict:
    try:
        return _read_local_cached(str(p), p.stat().st_mtime_ns)
    except OSError:
        return {}


def _flag(v: object) -> bool:
    """A YAML boolean; anything else (`"false"` is a string, and truthy) is False."""
    return v is True


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
        local=_flag(data.get("local")),
        volatile=_flag(data.get("volatile")),
        repos_registry=Path(str(registry) if registry else DEFAULT_REPOS_REGISTRY).expanduser(),
        runs_keep_days=_days(data.get("runs_keep_days", DEFAULT_RUNS_KEEP_DAYS)),
        identity=str(data.get("identity") or ""),
        person=data.get("person") is not False,
    )
