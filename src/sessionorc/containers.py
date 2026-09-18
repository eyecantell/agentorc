"""A container node (design §4.4a "A container node", TD-057 step 3c.2): the home brings a
devcontainer up from a repo's own definition, installs the agent in it at its own version, and
starts it — `ao host up|rebuild|forget|status`.

The person's part is one `nodes:` entry in the home's `hosts.yml`:

```yaml
nodes:
  contractmatch:
    container: {devcontainer: ~/contractmatch}   # the checkout whose .devcontainer defines the image
```

Everything else is derived, under `~/.agentorc/nodes/<name>/.devcontainer/`. Two generated
definitions from the repo's: **`base.json`** is the repo's image — `image` or `build` with its
paths re-anchored, and the repo's `features` — with the person's `mounts`, `customizations` and
`containerEnv` dropped (they carry the person's credentials), built once by `devcontainer build`
and tagged `agentorc-node-<name>-base`; **`devcontainer.json`** is the node's container, built from
a one-stage **generated Dockerfile** on that base that installs tmux as root, with the repo's
`remoteUser`, lifecycle commands and ports kept, each dropped mount stood in by an empty tmpfs at
the same target, the checkout mounted at the **same absolute path** inside as outside, and
agentorc's two mounts added. (A local devcontainer *feature* was the first shape; the CLI takes
one only from the workspace's own `.devcontainer/`, which is the repo's — seen live 2026-09-17 —
so the tmux layer is a stage of agentorc's own on the repo's image instead.) `~/.agentorc/nodes/<name>/` is
`/agentorc` inside: `/agentorc/home` the node's `AGENTORC_HOME`, `/agentorc/venv` the agent at the
home's version from the wheel the promote wrote (`~/.agentorc/wheels/`), `/agentorc/link` the
home's per-node link socket directory (step 3c.1).

Every external call goes through one seam, `Runner.run`, so the suite drives `up` and the
provisioning with a fake `devcontainer`/`docker` recording the calls; the live check on kmaster
is not in the suite (docker is not in `pdm run test`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sessionorc import hosts, paths

INSIDE = "/agentorc"  # the node's volume, inside
INSIDE_HOME = f"{INSIDE}/home"
INSIDE_VENV = f"{INSIDE}/venv"
INSIDE_LINK = f"{INSIDE}/link"
INSIDE_WHEELS = f"{INSIDE}/wheels"
INSIDE_PROFILES = f"{INSIDE}/profiles"
INSIDE_ENV = f"{INSIDE}/env"
ID_LABEL = "dev.agentorc.node"  # the id label `devcontainer up` keys the container on: ours, never VS Code's
PIDFILE = f"{INSIDE_HOME}/agent.pid"
AGENT_LOG = f"{INSIDE_HOME}/agent.log"
MIN_PYTHON = (3, 12)
# The keys of the repo's definition that are the person's, not the image's: dropped, each mount
# replaced by an empty tmpfs at its target (a lifecycle script may assume the path).
DROPPED = ("mounts", "customizations", "containerEnv")

BASE_IMAGE = "agentorc-node-{name}-base"  # what `devcontainer build` tags the repo's image as
INSTALL_TMUX = """#!/bin/sh
# agentorc's layer on the repo's image: tmux with whatever package manager the image has. The
# agent is not installed here — the home installs its own wheel onto the node's volume.
set -e
if command -v tmux >/dev/null 2>&1; then exit 0; fi
if command -v apt-get >/dev/null 2>&1; then
  apt-get update && apt-get install -y --no-install-recommends tmux && rm -rf /var/lib/apt/lists/*
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache tmux
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y tmux && dnf clean all
elif command -v microdnf >/dev/null 2>&1; then
  microdnf install -y tmux && microdnf clean all
else
  echo "agentorc: no package manager found to install tmux (tried apt-get, apk, dnf, microdnf)" >&2
  exit 1
fi
"""


class ContainerError(Exception):
    """`ao host` refuses, naming what is missing or wrong."""


@dataclass
class ContainerNode:
    """One `nodes:` entry with a `container:` block, and where its things live on the home."""

    name: str
    devcontainer: Path  # the checkout whose `.devcontainer/devcontainer.json` defines the image

    @property
    def repo_config(self) -> Path:
        return self.devcontainer / ".devcontainer" / "devcontainer.json"

    @property
    def node_dir(self) -> Path:
        return paths.home() / "nodes" / self.name

    @property
    def config_dir(self) -> Path:
        # Literally `.devcontainer`: the devcontainer CLI takes a local feature only from a child of
        # a folder of that name (seen live, 2026-09-17: "Resolved path must be a child of the
        # .devcontainer/ folder").
        return self.node_dir / ".devcontainer"

    @property
    def config(self) -> Path:
        return self.config_dir / "devcontainer.json"

    @property
    def base_config(self) -> Path:
        return self.config_dir / "base.json"

    @property
    def base_image(self) -> str:
        return BASE_IMAGE.format(name=self.name)

    @property
    def link_dir(self) -> Path:
        return paths.links_dir() / self.name

    @property
    def env_file(self) -> Path:
        return self.node_dir / "env"


def container_nodes() -> dict[str, ContainerNode]:
    """The `nodes:` entries that are containers on this machine: `{container: {devcontainer: …}}`.
    An entry of another shape is a machine, not ours."""
    out: dict[str, ContainerNode] = {}
    for name, flags in hosts.nodes().items():
        c = flags.get("container")
        if isinstance(c, dict) and isinstance(c.get("devcontainer"), str) and c["devcontainer"].strip():
            out[name] = ContainerNode(name, Path(c["devcontainer"].strip()).expanduser().resolve())
    return out


def node(name: str) -> ContainerNode:
    n = container_nodes().get(name)
    if n is None:
        raise ContainerError(
            f"{name} is not a container node: add `nodes: {{{name}: {{container: {{devcontainer: <checkout>}}}}}}` "
            f"to {hosts.hosts_file()}"
        )
    return n


# -- the generated definition ----------------------------------------------------------------------


def _strip_jsonc(text: str) -> str:
    """devcontainer.json is JSON with comments and trailing commas; take both out, and only
    outside strings — a `//` in a URL, a `/*` or a `,}` in a command are content."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':  # copy a string whole, escapes included
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "}]":
            k = len(out) - 1  # a trailing comma: the last non-blank thing before this bracket
            while k >= 0 and out[k].isspace():
                k -= 1
            if k >= 0 and out[k] == ",":
                del out[k]
            out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def read_definition(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise ContainerError(f"no devcontainer definition at {path}") from None
    except ValueError as e:
        raise ContainerError(f"{path} does not parse as devcontainer.json: {e}") from None
    if not isinstance(doc, dict):
        raise ContainerError(f"{path} is not a JSON object")
    return doc


def _mount_target(m: Any) -> str | None:
    if isinstance(m, dict):
        t = m.get("target")
        return t if isinstance(t, str) and t else None
    if isinstance(m, str):
        for part in m.split(","):
            k, _, v = part.partition("=")
            if k.strip() in ("target", "dst", "destination") and v.strip():
                return v.strip()
    return None


def _reanchor(build: Any, base: Path) -> Any:
    """`build.dockerfile` and `build.context` are relative to the repo's `.devcontainer/`; the
    generated file lives elsewhere, so they become absolute."""
    if not isinstance(build, dict):
        return build
    out = dict(build)
    for key in ("dockerfile", "context"):
        v = out.get(key)
        if isinstance(v, str) and v and not os.path.isabs(v):
            out[key] = str((base / v).resolve())
    return out


IMAGE_KEYS = ("image", "build", "dockerFile", "context", "features", "hostRequirements")


def generate_base(repo: dict[str, Any], *, name: str, repo_config_dir: Path) -> dict[str, Any]:
    """The repo's image and nothing of the person's (design §4.4a "The home generates the
    container's definition from the repo's, and keeps the repo's mounts out of it"): what
    `devcontainer build` builds and tags as the base."""
    out: dict[str, Any] = {k: v for k, v in repo.items() if k in IMAGE_KEYS}
    out["name"] = f"agentorc node {name} (base)"
    if "build" in out:
        out["build"] = _reanchor(out["build"], repo_config_dir)
    for key in ("dockerFile", "context"):  # the legacy top-level pair
        v = out.get(key)
        if isinstance(v, str) and v and not os.path.isabs(v):
            out[key] = str((repo_config_dir / v).resolve())
    return out


def generate_node(
    repo: dict[str, Any], *, name: str, checkout: Path, config_dir: Path, node_dir: Path, link_dir: Path
) -> dict[str, Any]:
    """The node's container on the built base: the repo's definition with its image keys
    replaced by the generated Dockerfile, the person's parts out, agentorc's in."""
    out: dict[str, Any] = {k: v for k, v in repo.items() if k not in DROPPED and k not in IMAGE_KEYS}
    out["name"] = f"agentorc node {name}"
    out["build"] = {"dockerfile": str(config_dir / "Dockerfile"), "context": str(config_dir)}
    mounts: list[str] = []
    for m in repo.get("mounts") or []:
        t = _mount_target(m)
        if t:
            mounts.append(f"type=tmpfs,target={t}")  # the person's mount, stood in by nothing
    mounts.append(f"source={node_dir},target={INSIDE},type=bind")
    mounts.append(f"source={link_dir},target={INSIDE_LINK},type=bind")
    out["mounts"] = mounts
    out["workspaceMount"] = f"source={checkout},target={checkout},type=bind"
    out["workspaceFolder"] = str(checkout)
    out["init"] = True
    return out


def dockerfile_text(base_image: str, image_user: str) -> str:
    """One stage on the base: tmux as root, then the image's own user back."""
    lines = [
        "# generated by agentorc (`ao host up`): the repo's image plus what a host needs. Not the repo's.",
        f"FROM {base_image}",
        "USER root",
        "COPY install-tmux.sh /tmp/agentorc-install-tmux.sh",
        "RUN sh /tmp/agentorc-install-tmux.sh && rm -f /tmp/agentorc-install-tmux.sh",
    ]
    if image_user:
        lines.append(f"USER {image_user}")
    return "\n".join(lines) + "\n"


def write_base(n: ContainerNode) -> dict[str, Any]:
    repo = read_definition(n.repo_config)
    out = generate_base(repo, name=n.name, repo_config_dir=n.repo_config.parent)
    n.config_dir.mkdir(parents=True, exist_ok=True)
    n.base_config.write_text(json.dumps(out, indent=2) + "\n")
    return out


def write_node(n: ContainerNode, image_user: str) -> dict[str, Any]:
    repo = read_definition(n.repo_config)
    out = generate_node(
        repo, name=n.name, checkout=n.devcontainer, config_dir=n.config_dir, node_dir=n.node_dir, link_dir=n.link_dir
    )
    n.config_dir.mkdir(parents=True, exist_ok=True)
    install = n.config_dir / "install-tmux.sh"
    install.write_text(INSTALL_TMUX)
    install.chmod(0o755)
    (n.config_dir / "Dockerfile").write_text(dockerfile_text(n.base_image, image_user))
    n.config.write_text(json.dumps(out, indent=2) + "\n")
    return out


# -- the seam ----------------------------------------------------------------------------------------


@dataclass
class Runner:
    """Every external process `ao host` runs. `run` is the one method a fake replaces."""

    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr)
    calls: list[list[str]] = field(default_factory=list)

    def run(self, cmd: list[str], *, check: bool = True, stream: bool = False) -> subprocess.CompletedProcess[str]:
        """stdout is always captured (a result is read from it); `stream` lets stderr through to
        the terminal — a build's progress."""
        self.calls.append(list(cmd))
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=None if stream else subprocess.PIPE, text=True)
        if check and cp.returncode != 0:
            err = (cp.stderr or "").strip().splitlines()[-1:] if cp.stderr else []
            raise ContainerError(f"{' '.join(cmd[:2])} failed ({cp.returncode})" + (f": {err[0]}" if err else ""))
        return cp

    def devcontainer(self) -> str:
        """The devcontainer CLI on the home, or a refusal naming it."""
        found = shutil.which("devcontainer") or shutil.which(
            "devcontainer", path=str(Path("~/.npm-global/bin").expanduser())
        )
        if not found:
            raise ContainerError("the devcontainer CLI is not on the home: `npm install -g @devcontainers/cli`")
        return found


# -- up, provision, start ----------------------------------------------------------------------------


def container_id(r: Runner, name: str) -> str | None:
    """The one container carrying our id label, running or not."""
    cp = r.run(["docker", "ps", "-aq", "--filter", f"label={ID_LABEL}={name}"], check=False)
    ids = (cp.stdout or "").split()
    return ids[0] if ids else None


def container_state(r: Runner, cid: str) -> str:
    cp = r.run(["docker", "inspect", "-f", "{{.State.Status}}", cid], check=False)
    return (cp.stdout or "").strip() if cp.returncode == 0 else "gone"


def build_base(n: ContainerNode, r: Runner, *, rebuild: bool = False) -> str:
    """`devcontainer build` on the repo's image definition, tagged as the node's base; returns the
    image's own user (for the generated Dockerfile to restore after its root step)."""
    cli = r.devcontainer()
    write_base(n)
    cmd = [
        cli,
        "build",
        "--workspace-folder",
        str(n.devcontainer),
        "--config",
        str(n.base_config),
        "--image-name",
        n.base_image,
    ]
    if rebuild:
        cmd.append("--no-cache")
    r.log(f"devcontainer build of {n.name}'s image ({'no cache' if rebuild else 'cached'}) …")
    cp = r.run(cmd, check=False, stream=True)
    result = _last_json(cp.stdout or "")
    if cp.returncode != 0 or result.get("outcome") != "success":
        detail = result.get("message") or (cp.stderr or "").strip().splitlines()[-1:] or [f"exit {cp.returncode}"]
        raise ContainerError(
            f"devcontainer build failed for {n.name}: {detail if isinstance(detail, str) else detail[0]}"
        )
    cp = r.run(["docker", "inspect", "-f", "{{.Config.User}}", n.base_image], check=False)
    return (cp.stdout or "").strip() if cp.returncode == 0 else ""


def up(n: ContainerNode, r: Runner, *, rebuild: bool = False) -> dict[str, Any]:
    """The base built, then `devcontainer up` on the node's definition under our id label —
    idempotent: the CLI finds the container it made before. `rebuild` removes it and builds both
    without cache."""
    cli = r.devcontainer()
    image_user = build_base(n, r, rebuild=rebuild)
    write_node(n, image_user)
    n.link_dir.mkdir(parents=True, exist_ok=True)
    (n.node_dir / "home").mkdir(parents=True, exist_ok=True)
    cmd = [
        cli,
        "up",
        "--workspace-folder",
        str(n.devcontainer),
        "--config",
        str(n.config),
        "--id-label",
        f"{ID_LABEL}={n.name}",
    ]
    if rebuild:
        cmd += ["--remove-existing-container", "--build-no-cache"]
        (n.node_dir / "home" / "agent.pid").unlink(missing_ok=True)  # the old container's, not the new one's
    r.log(f"devcontainer up ({'rebuild' if rebuild else 'idempotent'}) for {n.name} …")
    cp = r.run(cmd, check=False, stream=True)
    result = _last_json(cp.stdout or "")
    if cp.returncode != 0 or result.get("outcome") != "success":
        detail = result.get("message") or (cp.stderr or "").strip().splitlines()[-1:] or [f"exit {cp.returncode}"]
        why = detail if isinstance(detail, str) else detail[0]
        if "Command failed" in why:
            # the repo's own lifecycle command died inside the node: its output is above, and the
            # usual cause is a path hardcoded to where the repo's VS Code container mounts it
            why += (
                f" — a lifecycle command of {n.repo_config} failed inside the node (its output is above); "
                f"the checkout is at {n.devcontainer} inside, the same path as here, and a script that assumes "
                "another path is the repo's part to re-point (TD-057)"
            )
        raise ContainerError(f"devcontainer up failed for {n.name}: {why}")
    return result


def _last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                return d if isinstance(d, dict) else {}
            except ValueError:
                continue
    return {}


def exec_cmd(cid: str, user: str, *args: str, env_file: Path | None = None, detach: bool = False) -> list[str]:
    cmd = ["docker", "exec"]
    if detach:
        cmd.append("-d")
    cmd += ["-u", user, "-e", f"AGENTORC_HOME={INSIDE_HOME}"]
    if env_file is not None and env_file.is_file():
        cmd += ["--env-file", str(env_file)]
    return [*cmd, cid, *args]


def newest_wheel() -> Path:
    wheels = sorted(paths.home().joinpath("wheels").glob("agentorc-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise ContainerError(
            f"no wheel under {paths.home() / 'wheels'}: the promote writes one (`ao service install`, TD-062) — "
            "promote first, then `ao host up`"
        )
    return wheels[-1]


def check_image(n: ContainerNode, r: Runner, cid: str, user: str) -> None:
    """What the image must supply, refused by name: Python 3.12+, and the remote user carrying
    the person's uid (the node's volume is theirs to write on both sides)."""
    cp = r.run(
        exec_cmd(cid, user, "python3", "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"), check=False
    )
    parts = (cp.stdout or "").split()
    ver = tuple(int(x) for x in parts[:2]) if len(parts) >= 2 and all(x.isdigit() for x in parts[:2]) else None
    if cp.returncode != 0 or ver is None:
        raise ContainerError(
            f"the image of {n.name} has no python3: the agent needs Python {'.'.join(map(str, MIN_PYTHON))}+"
        )
    if ver < MIN_PYTHON:
        raise ContainerError(
            f"the image of {n.name} has Python {ver[0]}.{ver[1]}: the agent needs {'.'.join(map(str, MIN_PYTHON))}+"
        )
    cp = r.run(exec_cmd(cid, user, "id", "-u"), check=False)
    uid = (cp.stdout or "").strip()
    mine = str(os.getuid())
    if uid != mine:
        raise ContainerError(
            f"the container's user {user} is uid {uid or '?'} and you are {mine}: pin it in the repo's Dockerfile "
            f"(`useradd --uid {mine} …`) so what it writes on the node's volume is yours"
        )
    cp = r.run(exec_cmd(cid, user, "sh", "-c", "command -v tmux"), check=False)
    if cp.returncode != 0:
        raise ContainerError(
            f"no tmux in {n.name} after the build: agentorc's feature did not install it (see the build log)"
        )


def provision(n: ContainerNode, r: Runner, cid: str, user: str) -> Path:
    """The agent onto the node's volume at the home's version, and the node's own configuration."""
    wheel = newest_wheel()
    wheels = n.node_dir / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    dest = wheels / wheel.name
    if not dest.exists() or dest.stat().st_size != wheel.stat().st_size:
        shutil.copy2(wheel, dest)
    r.log(f"installing {wheel.name} into {INSIDE_VENV} …")
    r.run(exec_cmd(cid, user, "sh", "-c", f"test -x {INSIDE_VENV}/bin/python || python3 -m venv {INSIDE_VENV}"))
    # Two installs: the first brings the dependencies, the second replaces agentorc itself even at
    # the same version — pip never reinstalls a same-version wheel on its own, and agentorc's
    # version does not change per promote (Sonnet review, 2026-09-17: a re-promoted node kept its
    # old code).
    r.run(exec_cmd(cid, user, f"{INSIDE_VENV}/bin/pip", "install", "-q", "--upgrade", f"{INSIDE_WHEELS}/{wheel.name}"))
    r.run(
        exec_cmd(
            cid,
            user,
            f"{INSIDE_VENV}/bin/pip",
            "install",
            "-q",
            "--force-reinstall",
            "--no-deps",
            f"{INSIDE_WHEELS}/{wheel.name}",
        )
    )
    write_node_config(n)
    if n.env_file.is_file():
        cp = r.run(exec_cmd(cid, user, "sh", "-c", "command -v gh", env_file=n.env_file), check=False)
        if cp.returncode == 0:
            r.run(exec_cmd(cid, user, "gh", "auth", "setup-git", env_file=n.env_file), check=False)
    return dest


def write_node_config(n: ContainerNode) -> None:
    """The node's `hosts.yml` and `profiles.yml` under `/agentorc/home`: it is a node of this home,
    dials the mounted link socket, and runs the home's profiles with each `config_dir` under
    `/agentorc/profiles/<profile>/` — logged in once by hand inside, and kept."""
    home_dir = n.node_dir / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / "hosts.yml").write_text(
        yaml.safe_dump(
            {
                "local": {"name": n.name, "vscode_host": n.name},
                "home": hosts.local_host().name,
                "link": {"socket": f"{INSIDE_LINK}/link.sock"},
            },
            sort_keys=False,
        )
    )
    src = paths.home() / "profiles.yml"
    try:
        doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        doc = {}
    profiles = doc.get("profiles") if isinstance(doc, dict) and isinstance(doc.get("profiles"), dict) else {}
    out_profiles = {}
    for pname, spec in profiles.items():
        if not isinstance(spec, dict):
            continue
        s = dict(spec)
        s["config_dir"] = f"{INSIDE_PROFILES}/{pname}"
        out_profiles[pname] = s
    node_doc = {"profiles": out_profiles}
    if isinstance(doc, dict) and doc.get("default"):
        node_doc["default"] = doc["default"]
    (home_dir / "profiles.yml").write_text(yaml.safe_dump(node_doc, sort_keys=False))
    (n.node_dir / "profiles").mkdir(exist_ok=True)


def agent_pid(r: Runner, cid: str, user: str) -> int | None:
    """The pid behind the node's pidfile, if it is alive in the container *and* is the agent — a
    pidfile on the volume outlives the container, and a fresh pid namespace reuses low numbers."""
    script = f"p=$(cat {PIDFILE} 2>/dev/null) && kill -0 $p && grep -q agentorc-agent /proc/$p/cmdline && echo $p"
    cp = r.run(exec_cmd(cid, user, "sh", "-c", script), check=False)
    out = (cp.stdout or "").strip()
    return int(out) if out.isdigit() else None


def start_agent(n: ContainerNode, r: Runner, cid: str, user: str) -> None:
    """`docker exec -d` of the agent, detached, with a pidfile and its log under the node's home —
    the whole of the contract systemd gives the home's own agent."""
    script = f"echo $$ > {PIDFILE}; exec {INSIDE_VENV}/bin/agentorc-agent serve >> {AGENT_LOG} 2>&1"
    r.run(exec_cmd(cid, user, "sh", "-c", script, env_file=n.env_file, detach=True))


def host_up(name: str, r: Runner | None = None, *, rebuild: bool = False) -> dict[str, Any]:
    """`ao host up <name>` (and `rebuild`): from nothing to an agent inside dialing the home."""
    r = r or Runner()
    n = node(name)
    result = up(n, r, rebuild=rebuild)
    cid = str(result.get("containerId") or container_id(r, name) or "")
    if not cid:
        raise ContainerError(f"devcontainer up reported success but no container carries {ID_LABEL}={name}")
    user = str(result.get("remoteUser") or "root")
    check_image(n, r, cid, user)
    wheel = provision(n, r, cid, user)
    pid = agent_pid(r, cid, user)
    if pid is None:
        start_agent(n, r, cid, user)
        r.log(f"agent started in {name}; its log is {n.node_dir / 'home' / 'agent.log'}")
    else:
        r.log(f"agent already running in {name} (pid {pid}); `ao host rebuild {name}` restarts it on {wheel.name}")
    return {
        "node": name,
        "container": cid,
        "user": user,
        "wheel": wheel.name,
        "started": pid is None,
        "pid": pid,
    }


def host_status(name: str, r: Runner | None = None) -> dict[str, Any]:
    r = r or Runner()
    n = node(name)
    cid = container_id(r, name)
    out: dict[str, Any] = {
        "node": name,
        "devcontainer": str(n.devcontainer),
        "container": cid,
        "state": None,
        "pid": None,
    }
    if cid:
        out["state"] = container_state(r, cid)
        if out["state"] == "running":
            user = _remote_user(n)
            out["user"] = user
            out["pid"] = agent_pid(r, cid, user)
            out["reach"] = reach(n, cid, user)
    return out


def _remote_user(n: ContainerNode) -> str:
    try:
        return str(read_definition(n.config).get("remoteUser") or "root")
    except ContainerError:
        return "root"


def reach(n: ContainerNode, cid: str, user: str) -> dict[str, str]:
    """How a person reaches the node's sessions until the remote terminal is built (§4.4a "Reach"):
    by hand, derived from the entry."""
    return {
        "terminal": f"docker exec -u {user} -it {cid} tmux attach",
        "vscode": f"vscode-remote://dev-container+{n.devcontainer.as_posix().encode().hex()}{n.devcontainer}",
    }


def host_forget(name: str, r: Runner | None = None, *, purge: bool = False) -> dict[str, Any]:
    """Remove the container, the link directory and the `nodes:` entry; keep the node's volume
    (its run logs, invariant 3) unless `purge`. Closing the host's records at the home is the
    agent's, over its RPC — the CLI asks for it after this."""
    r = r or Runner()
    n = node(name)
    cid = container_id(r, name)
    if cid:
        r.run(["docker", "rm", "-f", cid], check=False)
    shutil.rmtree(n.link_dir, ignore_errors=True)
    removed = remove_node_entry(hosts.hosts_file(), name)
    if purge:
        shutil.rmtree(n.node_dir, ignore_errors=True)
    return {"node": name, "container": cid, "entry_removed": removed, "volume_kept": not purge}


def remove_node_entry(path: Path, name: str) -> bool:
    """Take `nodes.<name>` out of hosts.yml by lines — the file is the person's, comments and all —
    and verify by parsing that exactly that key went. Refuse rather than rewrite when the shape
    is not one this understands."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    before = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(before, dict) or name not in (before.get("nodes") or {}):
        return False
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i, cut = 0, False
    in_nodes = False
    while i < len(lines):
        line = lines[i]
        if re.match(r"^nodes\s*:", line):
            in_nodes = True
            out.append(line)
            i += 1
            continue
        if in_nodes and re.match(r"^\S", line):
            in_nodes = False
        if in_nodes and re.match(rf"^\s+(-\s+)?{re.escape(name)}\s*(:|#|$)", line):
            indent = len(line) - len(line.lstrip())
            i += 1
            while i < len(lines) and (lines[i].strip() == "" or len(lines[i]) - len(lines[i].lstrip()) > indent):
                i += 1
            cut = True
            continue
        out.append(line)
        i += 1
    new_text = "".join(out)
    after = yaml.safe_load(new_text) if new_text.strip() else {}
    if isinstance(after, dict) and after.get("nodes") is None and "nodes" in after:
        after["nodes"] = {} if isinstance(before.get("nodes"), dict) else []  # the last entry went: `nodes:` alone
    expected = dict(before)
    nodes_before = before.get("nodes")
    if isinstance(nodes_before, dict):
        expected["nodes"] = {k: v for k, v in nodes_before.items() if k != name}
    elif isinstance(nodes_before, list):
        expected["nodes"] = [v for v in nodes_before if v != name]
    if not cut or after != expected:
        raise ContainerError(f"could not remove `nodes.{name}` from {path} cleanly: remove it by hand")
    path.write_text(new_text, encoding="utf-8")
    return True


# -- the promote's new step: the wheel ---------------------------------------------------------------


def write_wheel(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None, *, keep: int = 3
) -> Path | None:
    """`ao service install` (the promote, TD-062) writes the wheel of what it installed to
    `~/.agentorc/wheels/`, which is what a container node is provisioned from. From the source
    directory the install came from (`direct_url.json`), else from the index at the same version.
    Never fatal to the promote: None, and a line, when it cannot."""
    from importlib import metadata

    wheels = paths.home() / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    try:
        dist = metadata.distribution("agentorc")
    except metadata.PackageNotFoundError:
        return None
    src: str | None = None
    raw = dist.read_text("direct_url.json")
    if raw:
        try:
            url = json.loads(raw).get("url", "")
        except ValueError:
            url = ""
        if url.startswith("file://") and Path(url[7:]).is_dir():
            src = url[7:]
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))
    pip = [sys.executable, "-m", "pip"]
    cmd = (
        [*pip, "wheel", "--no-deps", "-q", "-w", str(wheels), src]
        if src
        else [*pip, "download", "--no-deps", "-q", "-d", str(wheels), f"agentorc=={dist.version}"]
    )
    cp = run(cmd)
    if cp.returncode != 0:
        why = (cp.stderr or "").strip().splitlines()[-1:] or [f"exit {cp.returncode}"]
        print(f"[agentorc] no wheel written for container nodes: {why[0]}", file=sys.stderr)
        return None
    built = sorted(wheels.glob("agentorc-*.whl"), key=lambda p: p.stat().st_mtime)
    for old in built[:-keep]:
        old.unlink(missing_ok=True)
    return built[-1] if built else None
