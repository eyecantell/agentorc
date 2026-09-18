"""A container node (design §4.4a "A container node", TD-057 step 3c.2): the generated definition,
`ao host up` driven through the subprocess seam with a fake `devcontainer`/`docker`, the
refusals by name, `forget`'s edit of hosts.yml, and the promote's wheel. Docker is not in
`pdm run test`: the live check is on kmaster, by hand."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from conftest import wait_for_sync

from sessionorc import containers, paths
from sessionorc.client import LocalClient
from sessionorc.containers import ContainerError, ContainerNode, Runner

pytestmark = pytest.mark.unit

# contractmatch's `.devcontainer/devcontainer.json`, in shape: a build with relative paths, a remote
# user, the person's two bind mounts under /mounted (one carrying their credentials), VS Code
# customizations, ports, a lifecycle command that assumes a mount's path exists.
CONTRACTMATCH = """{
  "name": "contractmatch dev",
  "postCreateCommand": "sh setup_scripts/postCreate.sh",   // runs `mkdir -p /mounted/dev/...` under set -e
  "build": {
    "dockerfile": "Dockerfile",
    "target": "development",
    "context": "..",
  },
  "remoteUser": "developer",
  "mounts": [
    {"type": "bind", "source": "${localEnv:HOME}${localEnv:USERPROFILE}/dev", "target": "/mounted/dev"},
    {"type": "bind", "source": "${localEnv:HOME}/stuff_for_containers_home",
     "target": "/mounted/stuff_for_containers_home"},
  ],
  "customizations": {"vscode": {"extensions": ["ms-python.python"],
                                "settings": {"dart.flutterRunAdditionalArgs": ["--no-sandbox"]}}},
  "containerEnv": {"MODAL_TOKEN": "the person's"},
  "portsAttributes": {"8080": {"label": "ContractMatch Dev", "onAutoForward": "notify"}},
  "forwardPorts": [8080]
}
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "agentorc-home"
    h.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(h))
    checkout = tmp_path / "contractmatch"
    (checkout / ".devcontainer").mkdir(parents=True)
    (checkout / ".devcontainer" / "devcontainer.json").write_text(CONTRACTMATCH)
    (checkout / ".devcontainer" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (h / "hosts.yml").write_text(
        "local:\n  name: kmaster\n"
        "nodes:\n"
        "  laptop: {volatile: true}   # a machine\n"
        "  contractmatch:\n"
        f"    container: {{devcontainer: {checkout}}}\n"
    )
    (h / "profiles.yml").write_text(
        "default: default\nprofiles:\n"
        "  default: {adapter: claude-code, account: paul}\n"
        "  grind: {adapter: claude-code, account: paul, model: opus, config_dir: ~/.claude-grind}\n"
    )
    (h / "wheels").mkdir()
    (h / "wheels" / "agentorc-0.0.1-py3-none-any.whl").write_bytes(b"wheel")
    return h


# -- the generated definition --------------------------------------------------------------------------


def test_the_base_is_the_repos_image_and_nothing_of_the_persons(home, tmp_path):
    n = containers.node("contractmatch")
    base = containers.write_base(n)
    checkout = tmp_path / "contractmatch"
    # the image, with its build paths re-anchored — the generated file no longer sits beside them
    assert base["build"]["dockerfile"] == str(checkout / ".devcontainer" / "Dockerfile")
    assert base["build"]["context"] == str(checkout) and base["build"]["target"] == "development"
    assert base["name"] == "agentorc node contractmatch (base)"
    # nothing else: not the mounts (credentials), not the customizations, not the env, not the ports
    assert set(base) == {"build", "name"}
    assert containers.read_definition(n.base_config) == base
    assert n.base_image == "agentorc-node-contractmatch-base"


def test_the_node_is_the_repos_with_the_persons_parts_out_and_agentorcs_in(home, tmp_path):
    n = containers.node("contractmatch")
    out = containers.write_node(n, "developer")
    checkout = tmp_path / "contractmatch"
    # kept: the user, the lifecycle command, the ports
    assert out["remoteUser"] == "developer" and out["postCreateCommand"].startswith("sh setup_scripts")
    assert out["forwardPorts"] == [8080] and out["portsAttributes"]["8080"]["label"] == "ContractMatch Dev"
    # the image keys replaced by the generated Dockerfile on the built base
    assert out["build"] == {"dockerfile": str(n.config_dir / "Dockerfile"), "context": str(n.config_dir)}
    assert "image" not in out and "features" not in out
    # dropped: the person's mounts, customizations and containerEnv
    assert "customizations" not in out and "containerEnv" not in out
    assert not any("stuff_for_containers_home" in m and "bind" in m for m in out["mounts"])
    # each dropped mount stood in by an empty tmpfs at its target, so postCreate's mkdir under it lives
    assert "type=tmpfs,target=/mounted/dev" in out["mounts"]
    assert "type=tmpfs,target=/mounted/stuff_for_containers_home" in out["mounts"]
    # agentorc's two mounts, the checkout at the same path both sides, init, the name
    assert f"source={n.node_dir},target=/agentorc,type=bind" in out["mounts"]
    assert f"source={n.link_dir},target=/agentorc/link,type=bind" in out["mounts"]
    assert out["workspaceMount"] == f"source={checkout},target={checkout},type=bind"
    assert out["workspaceFolder"] == str(checkout)
    assert out["init"] is True and out["name"] == "agentorc node contractmatch"
    # the Dockerfile: one stage on the base, tmux as root, the image's own user restored
    df = (n.config_dir / "Dockerfile").read_text()
    assert df.splitlines()[1] == "FROM agentorc-node-contractmatch-base"
    assert "USER root" in df and df.rstrip().endswith("USER developer")
    assert "COPY install-tmux.sh" in df and "RUN sh /tmp/agentorc-install-tmux.sh" in df
    install = n.config_dir / "install-tmux.sh"
    text = install.read_text()
    assert install.stat().st_mode & 0o111 and "apt-get" in text and "apk" in text and "dnf" in text
    assert containers.read_definition(n.config) == out
    # an image with no USER of its own gets no USER line back
    assert "USER" not in containers.dockerfile_text("x", "").split("USER root")[1]


def test_the_repos_features_and_image_go_to_the_base_and_a_string_mount_is_understood(tmp_path):
    repo = {
        "image": "python:3.12",
        "features": {"ghcr.io/devcontainers/features/node:1": {}},
        "mounts": ["source=/x,target=/mounted/x,type=bind", {"type": "volume", "target": "/data"}, "nonsense"],
    }
    base = containers.generate_base(repo, name="n", repo_config_dir=Path("/c/.devcontainer"))
    assert base == {
        "image": "python:3.12",
        "features": {"ghcr.io/devcontainers/features/node:1": {}},
        "name": "agentorc node n (base)",
    }
    out = containers.generate_node(
        repo,
        name="n",
        checkout=Path("/c"),
        config_dir=Path("/n/.devcontainer"),
        node_dir=Path("/n"),
        link_dir=Path("/l"),
    )
    assert out["mounts"][:2] == ["type=tmpfs,target=/mounted/x", "type=tmpfs,target=/data"]
    assert "image" not in out and "features" not in out and out["build"]["context"] == "/n/.devcontainer"


def test_comments_and_trailing_commas_go_and_strings_are_kept_whole():
    text = """{
  // a line comment
  "url": "https://x/y", /* a block
  comment */ "cmd": "echo /* not a comment */ , }",
  "esc": "a \\" quote // still a string",
  "list": [1, 2, ], "obj": {"a": 1, },
}"""
    doc = json.loads(containers._strip_jsonc(text))
    assert doc == {
        "url": "https://x/y",
        "cmd": "echo /* not a comment */ , }",
        "esc": 'a " quote // still a string',
        "list": [1, 2],
        "obj": {"a": 1},
    }


def test_a_missing_or_unparsable_definition_is_refused_by_name(home, tmp_path):
    n = ContainerNode("x", tmp_path / "nowhere")
    with pytest.raises(ContainerError, match="no devcontainer definition at"):
        containers.read_definition(n.repo_config)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ContainerError, match="does not parse"):
        containers.read_definition(bad)


def test_an_entry_without_a_container_block_is_not_a_container_node(home):
    assert set(containers.container_nodes()) == {"contractmatch"}
    with pytest.raises(ContainerError, match="laptop is not a container node"):
        containers.node("laptop")


# -- up and provisioning, through the seam -------------------------------------------------------------


class Fake(Runner):
    """A `devcontainer` and a `docker` that answer from a table, recording every call."""

    def __init__(self, **answers):
        super().__init__(log=lambda line: None)
        self.answers = {
            "python": "3 12\n",
            "id": f"{os.getuid()}\n",
            "tmux": "/usr/bin/tmux\n",
            "pid": "",
            "up": json.dumps({"outcome": "success", "containerId": "abc123def456", "remoteUser": "developer"}) + "\n",
            "build": json.dumps({"outcome": "success", "imageName": ["agentorc-node-contractmatch-base"]}) + "\n",
            "user": "developer\n",
            "ps": "abc123def456\n",
            "inspect": "running\n",
            "name": "/agentorc-node-cm\n",
            **answers,
        }
        self.cli = "/fake/devcontainer"

    def devcontainer(self):
        return self.cli

    def run(self, cmd, *, check=True, stream=False):
        self.calls.append(list(cmd))
        text = " ".join(cmd)
        rc, out = 0, ""
        if cmd[0] == self.cli:
            out = self.answers[cmd[1]]  # "build" or "up"
            rc = 0 if '"success"' in out else 1
        elif cmd[:2] == ["docker", "ps"]:
            out = self.answers["ps"]
        elif cmd[:2] == ["docker", "inspect"] and "{{.Config.User}}" in cmd:
            out = self.answers["user"]
        elif cmd[:2] == ["docker", "inspect"] and "{{.Name}}" in cmd:
            out = self.answers["name"]
        elif cmd[:2] == ["docker", "inspect"]:
            out = self.answers["inspect"]
        elif "sys.version_info" in text:
            out = self.answers["python"]
            rc = 1 if not out else 0
        elif cmd[-2:] == ["id", "-u"]:
            out = self.answers["id"]
        elif "command -v tmux" in text:
            out = self.answers["tmux"]
            rc = 0 if out else 1
        elif "agent.pid" in text:
            out = self.answers["pid"]
        elif "command -v gh" in text:
            rc = 1
        if check and rc != 0:
            raise ContainerError(f"{cmd[0]} failed ({rc})")
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def execs(self, needle):
        return [c for c in self.calls if c[:2] == ["docker", "exec"] and needle in " ".join(c)]


def test_host_up_brings_it_up_provisions_it_and_starts_the_agent(home, tmp_path):
    r = Fake()
    out = containers.host_up("contractmatch", r)
    n = containers.node("contractmatch")
    assert out == {
        "node": "contractmatch",
        "container": "abc123def456",
        "user": "developer",
        "wheel": "agentorc-0.0.1-py3-none-any.whl",
        "started": True,
        "pid": None,
    }
    # the base built from the repo's image definition and tagged; then `devcontainer up` on the
    # node's definition, keyed on our id label, never the repo's own
    build = r.calls[0]
    assert build[:2] == ["/fake/devcontainer", "build"] and "--no-cache" not in build
    assert build[build.index("--config") + 1] == str(n.base_config)
    assert build[build.index("--image-name") + 1] == "agentorc-node-contractmatch-base"
    assert ["docker", "inspect", "-f", "{{.Config.User}}", "agentorc-node-contractmatch-base"] in r.calls
    assert (n.config_dir / "Dockerfile").read_text().rstrip().endswith("USER developer")
    up = r.calls[2]
    assert up[:2] == ["/fake/devcontainer", "up"] and "--remove-existing-container" not in up
    assert (
        up[up.index("--config") + 1] == str(n.config)
        and up[up.index("--id-label") + 1] == "dev.agentorc.node=contractmatch"
    )
    assert up[up.index("--workspace-folder") + 1] == str(tmp_path / "contractmatch")
    assert n.link_dir.is_dir() and (n.node_dir / "home").is_dir()
    # the wheel copied onto the volume, the venv made, the wheel installed — as the remote user
    assert (n.node_dir / "wheels" / "agentorc-0.0.1-py3-none-any.whl").read_bytes() == b"wheel"
    venv = r.execs("python3 -m venv /agentorc/venv")
    assert venv and venv[0][venv[0].index("-u") + 1] == "developer"
    assert r.execs("/agentorc/venv/bin/pip install -q --upgrade /agentorc/wheels/agentorc-0.0.1-py3-none-any.whl")
    # and agentorc itself replaced even at the same version: pip would otherwise keep the old code
    assert r.execs("pip install -q --force-reinstall --no-deps /agentorc/wheels/agentorc-0.0.1-py3-none-any.whl")
    # the node's own configuration: a node of this home, dialing the mounted socket, the profiles rewritten
    hosts_yml = (n.node_dir / "home" / "hosts.yml").read_text()
    assert "home: kmaster" in hosts_yml and "name: contractmatch" in hosts_yml
    assert "socket: /agentorc/link/link.sock" in hosts_yml
    profiles = (n.node_dir / "home" / "profiles.yml").read_text()
    assert "config_dir: /agentorc/profiles/grind" in profiles and "config_dir: /agentorc/profiles/default" in profiles
    assert "~/.claude-grind" not in profiles and "default: default" in profiles
    assert (n.node_dir / "profiles").is_dir()
    # the agent started detached, with a pidfile and its log under the node's home, AGENTORC_HOME set
    start = r.execs("agentorc-agent serve")
    assert len(start) == 1 and "-d" in start[0] and "AGENTORC_HOME=/agentorc/home" in start[0]
    assert "echo $$ > /agentorc/home/agent.pid" in start[0][-1] and ">> /agentorc/home/agent.log" in start[0][-1]
    assert "--env-file" not in start[0]  # no env file written: nothing to read in


def test_host_up_is_idempotent_and_an_env_file_reaches_the_agent(home):
    n = containers.node("contractmatch")
    n.node_dir.mkdir(parents=True)
    n.env_file.write_text("GH_TOKEN=ghp_x\nGIT_AUTHOR_NAME=grinder\n")
    r = Fake(pid="4242\n")
    out = containers.host_up("contractmatch", r)
    assert out["started"] is False and out["pid"] == 4242
    assert "grep -q agentorc-agent /proc/$p/cmdline" in r.execs("agent.pid")[0][-1]  # alive *and* the agent
    assert not r.execs("agentorc-agent serve")  # a live pid behind the pidfile: not started twice
    assert r.execs("command -v gh")[0][r.execs("command -v gh")[0].index("--env-file") + 1] == str(n.env_file)
    (n.node_dir / "home").mkdir(parents=True, exist_ok=True)
    (n.node_dir / "home" / "agent.pid").write_text("4242\n")
    r2 = Fake()
    containers.host_up("contractmatch", r2, rebuild=True)
    assert not (n.node_dir / "home" / "agent.pid").exists()  # the old container's pid is not the new one's
    assert "--no-cache" in r2.calls[0]
    up = r2.calls[2]
    assert "--remove-existing-container" in up and "--build-no-cache" in up
    start = r2.execs("agentorc-agent serve")
    assert start and "--env-file" in start[0]


@pytest.mark.parametrize(
    ("answers", "words"),
    [
        ({"python": "3 11\n"}, "has Python 3.11: the agent needs 3.12+"),
        ({"python": ""}, "has no python3"),
        ({"id": "1234567\n"}, "is uid 1234567 and you are"),
        ({"tmux": ""}, "no tmux in contractmatch after the build"),
        (
            {"up": json.dumps({"outcome": "error", "message": "no such container user"}) + "\n"},
            "up failed .*no such container",
        ),
        (
            {"build": json.dumps({"outcome": "error", "message": "no such Dockerfile"}) + "\n"},
            "build failed .*no such Dockerfile",
        ),
    ],
)
def test_host_up_refuses_naming_what_is_missing(home, answers, words):
    with pytest.raises(ContainerError, match=words):
        containers.host_up("contractmatch", Fake(**answers))


def test_no_wheel_and_no_cli_are_refused_before_anything_runs(home, monkeypatch):
    for w in (paths.home() / "wheels").iterdir():
        w.unlink()
    r = Fake()
    with pytest.raises(ContainerError, match="no wheel under .*promote first"):
        containers.host_up("contractmatch", r)
    assert not r.execs("agentorc-agent serve")
    plain = Runner(log=lambda line: None)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(ContainerError, match="devcontainer CLI is not on the home"):
        containers.host_up("contractmatch", plain)
    assert plain.calls == []


def test_status_and_reach_are_derived_from_the_entry(home):
    r = Fake(pid="7\n")
    containers.write_node(containers.node("contractmatch"), "developer")
    out = containers.host_status("contractmatch", r)
    assert out["container"] == "abc123def456" and out["state"] == "running" and out["pid"] == 7
    assert out["reach"]["terminal"] == "docker exec -u developer -it abc123def456 tmux attach"
    assert out["reach"]["container"] == "abc123def456" and out["reach"]["user"] == "developer"
    assert out["reach"]["name"] == "agentorc-node-cm"
    # VS Code attaches to *this* container by docker's name — the `dev-container+` form would open
    # the person's own container from the repo's definition (3c.5)
    link = out["reach"]["vscode"].removeprefix("vscode://vscode-remote/attached-container+")
    head, _, path = link.removesuffix("?windowId=_blank").partition("/")
    assert (
        json.loads(bytes.fromhex(head)) == {"containerName": "/agentorc-node-cm"} and "/" + path == out["devcontainer"]
    )
    gone = containers.host_status("contractmatch", Fake(ps=""))
    assert gone["container"] is None and gone["state"] is None and "reach" not in gone
    # what the home records on the link when the node dials in, and what the terminal runs
    n = containers.node("contractmatch")
    assert containers.observe_reach(n, Fake(inspect="exited\n")) is None
    assert containers.observe_reach(n, Fake(ps="")) is None
    assert containers.observe_reach(n, Fake())["container"] == "abc123def456"
    argv = containers.attach_argv_in("abc123def456", "developer", "ao-cm-w")
    assert argv[:6] == ["docker", "exec", "-u", "developer", "-it", "abc123def456"]
    assert argv[6:] == ["tmux", "attach", "-t", "=ao-cm-w:", ";", "set-option", "-t", "=ao-cm-w:", "mouse", "on"]


# -- forget ------------------------------------------------------------------------------------------------


def test_forget_removes_the_container_the_link_directory_and_the_entry_and_keeps_the_volume(home):
    n = containers.node("contractmatch")
    n.link_dir.mkdir(parents=True)
    (n.node_dir / "home" / "runs").mkdir(parents=True)
    r = Fake()
    out = containers.host_forget("contractmatch", r)
    assert ["docker", "rm", "-f", "abc123def456"] in r.calls
    assert not n.link_dir.exists() and (n.node_dir / "home" / "runs").is_dir()
    assert out["entry_removed"] is True and out["volume_kept"] is True
    text = (home / "hosts.yml").read_text()
    assert "contractmatch" not in text and "laptop: {volatile: true}   # a machine" in text  # the comment survived
    with pytest.raises(ContainerError, match="not a container node"):
        containers.node("contractmatch")


def test_forget_purge_deletes_the_volume_and_a_list_shaped_nodes_entry_is_understood(home, tmp_path):
    n = containers.node("contractmatch")
    (n.node_dir / "home").mkdir(parents=True)
    containers.host_forget("contractmatch", Fake(ps=""), purge=True)
    assert not n.node_dir.exists()
    f = tmp_path / "h.yml"
    f.write_text("local: {name: k}\nnodes:\n  - a\n  - b   # gone\n  - c\n")
    assert containers.remove_node_entry(f, "b") is True
    assert f.read_text() == "local: {name: k}\nnodes:\n  - a\n  - c\n"
    assert containers.remove_node_entry(f, "zzz") is False


def test_the_last_entry_leaves_an_empty_nodes_key(tmp_path):
    f = tmp_path / "h.yml"
    f.write_text("local:\n  name: k\nnodes:\n  contractmatch:\n    container: {devcontainer: ~/contractmatch}\n")
    assert (
        containers.remove_node_entry(f, "contractmatch") is True
    )  # seen live 2026-09-17: `nodes:` alone parses as None
    assert f.read_text() == "local:\n  name: k\nnodes:\n"
    f.write_text("nodes:\n  - only\n")
    assert containers.remove_node_entry(f, "only") is True and f.read_text() == "nodes:\n"


def test_a_shape_it_cannot_edit_cleanly_is_refused_not_rewritten(tmp_path):
    f = tmp_path / "h.yml"
    text = 'nodes: {"contractmatch": {container: {devcontainer: /x}}, laptop: {}}\n'
    f.write_text(text)
    with pytest.raises(ContainerError, match="remove it by hand"):
        containers.remove_node_entry(f, "contractmatch")
    assert f.read_text() == text


async def test_the_home_closes_a_forgotten_hosts_records_and_keeps_them(agent):
    from test_link import record

    agent.sessions.clear()
    agent._take_records("laptop", [record("ao-x-w"), record("ao-x-v", state="exited")], whole=True)
    async with LocalClient() as c:
        out = await c.call("forget_host", host="laptop")
        assert out == {"host": "laptop", "closed": 2, "kept": 2}
        listed = {s["id"]: s for s in await c.call("list")}
        assert listed["ao-x-w@laptop"]["state"] in ("closed", "unreachable")  # closed under the overlay
        assert agent.remote["laptop"]["ao-x-w"].state == "closed"
        with pytest.raises(Exception, match="cannot forget itself"):
            await c.call("forget_host", host=agent.host)
    async with LocalClient(caller="ao-x-w") as c:  # a session: refused
        with pytest.raises(Exception, match="a person's act"):
            await c.call("forget_host", host="laptop")


# -- the promote's wheel -----------------------------------------------------------------------------------


def test_the_promote_writes_the_wheel_of_what_it_installed_and_keeps_the_newest(home, monkeypatch):
    wheels = paths.home() / "wheels"
    calls = []

    def fake_pip(cmd):
        calls.append(cmd)
        Path(cmd[cmd.index("-w") + 1], "agentorc-9.9.9-py3-none-any.whl").write_bytes(b"new")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    for i in range(3):
        p = wheels / f"agentorc-0.0.{i}-py3-none-any.whl"
        p.write_bytes(b"old")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    built = containers.write_wheel(fake_pip, keep=2)
    assert built is not None and built.name == "agentorc-9.9.9-py3-none-any.whl"
    assert calls[0][:5] == [calls[0][0], "-m", "pip", "wheel", "--no-deps"] and calls[0][-1].startswith("/")
    assert sorted(p.name for p in wheels.iterdir()) == [
        "agentorc-0.0.2-py3-none-any.whl",
        "agentorc-9.9.9-py3-none-any.whl",
    ]
    assert containers.newest_wheel().name == "agentorc-9.9.9-py3-none-any.whl"
    failing = lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "no network")  # noqa: E731
    assert containers.write_wheel(failing) is None  # never fatal to the promote: None and a line
    assert containers.newest_wheel().name == "agentorc-9.9.9-py3-none-any.whl"  # the last one stands


# -- the supervisor (design §4.4a "The home supervises it", step 3c.3) ---------------------------------


@pytest.mark.parametrize(
    ("cstate", "alive", "why", "expected"),
    [
        (None, False, "", ("up", "container gone — bringing it up")),
        ("gone", False, "", ("up", "container gone — bringing it up")),
        ("exited", False, "closed by the other end", ("up", "container exited — bringing it up")),
        ("paused", False, "", ("unpause", "container paused — unpausing it")),
        ("dead", False, "", ("up", "container dead — bringing it up")),
        ("running", False, "", ("start", "agent not running inside — starting it")),
        (
            "running",
            True,
            "refused: link protocol 0 here is 1: promote both ends to one build",
            ("provision", "agent inside is an older build — re-provisioning it"),
        ),
        ("running", True, "refused: cm is not an authorised node", ("wait", containers.WAITING)),  # not ours to fix
        ("running", True, "closed by the other end", ("wait", containers.WAITING)),  # alive and dialing: wait
        ("running", True, "", ("wait", containers.WAITING)),
    ],
)
def test_the_decision_over_what_the_tick_observed(cstate, alive, why, expected):
    assert containers.decide(cstate, alive, why) == expected


@pytest.mark.parametrize(
    ("cstate", "alive", "expected"),
    [
        (None, False, ("wait", "container gone — volatile, left as the person left it")),
        ("exited", False, ("wait", "container exited — volatile, left as the person left it")),
        ("paused", False, ("wait", "container paused — volatile, left as the person left it")),
        ("running", False, ("start", "agent not running inside — starting it")),  # inside a running one: still ours
        ("running", True, ("wait", containers.WAITING)),
    ],
)
def test_a_volatile_node_is_never_started_by_the_home(cstate, alive, expected):
    """Design §4.4a: `volatile: true` is right for a container the person stops — the home starts
    the agent inside a running one, and never the container itself (3c.4)."""
    assert containers.decide(cstate, alive, "", volatile=True) == expected


def test_observe_and_act_through_the_seam(home):
    n = containers.node("contractmatch")
    assert containers.observe(n, Fake(ps="")) == (None, False, "")
    assert containers.observe(n, Fake(inspect="exited\n")) == ("exited", False, "")
    containers.write_node(n, "developer")
    assert containers.observe(n, Fake(pid="")) == ("running", False, "developer")
    assert containers.observe(n, Fake(pid="9\n")) == ("running", True, "developer")
    r = Fake(pid="")
    containers.act(n, r, "start", "developer")
    assert len(r.execs("agentorc-agent serve")) == 1 and not [c for c in r.calls if c[0] == r.cli]
    r = Fake(pid="9\n")
    containers.act(n, r, "provision", "developer")
    assert r.execs("pip install") and r.execs("kill $p") and r.execs("agentorc-agent serve")
    r = Fake(ps="")
    with pytest.raises(ContainerError, match="went away"):
        containers.act(n, r, "start", "developer")
    r = Fake(inspect="paused\n")
    containers.act(n, r, "unpause", "")
    assert ["docker", "unpause", "abc123def456"] in r.calls
    r = Fake()
    containers.act(n, r, "up", "")
    assert [c for c in r.calls if c[0] == r.cli][1][1] == "up"  # the whole idempotent host_up


@pytest.fixture
def container_home(tmp_path, monkeypatch):
    """A home whose hosts.yml names a container node `cm`; set up before the `agent` fixture reads
    the same AGENTORC_HOME."""
    h = tmp_path / "home"
    h.mkdir()
    checkout = tmp_path / "cm"
    (checkout / ".devcontainer").mkdir(parents=True)
    (checkout / ".devcontainer" / "devcontainer.json").write_text('{"image": "python:3.12", "remoteUser": "developer"}')
    (h / "hosts.yml").write_text(
        f"local:\n  name: kmaster\nnodes:\n  cm:\n    container: {{devcontainer: {checkout}}}\n"
    )
    (h / "wheels").mkdir()
    (h / "wheels" / "agentorc-0.0.1-py3-none-any.whl").write_bytes(b"wheel")
    monkeypatch.setattr(containers, "SUPERVISE_FIRST", 0.05)
    monkeypatch.setattr(containers, "SUPERVISE_MAX", 0.2)
    monkeypatch.setattr(containers, "SUPERVISE_GRACE", 0.15)
    # The agent's default runner is `containers.Runner(...)`, looked up per round — so the fake is
    # in place before the agent's first tick, which runs the moment the `agent` fixture starts it.
    holder = {"make": lambda: Fake(ps=""), "fakes": []}

    def make(**kw):
        f = holder["make"]()
        holder["fakes"].append(f)
        return f

    monkeypatch.setattr(containers, "Runner", make)
    return holder


async def test_the_home_brings_a_gone_container_back_and_says_so_on_the_card(container_home, agent):
    from conftest import wait_for
    from test_link import record

    fakes = container_home["fakes"]  # the default fake: the container is gone
    agent._take_records("cm", [record("ao-cm-w", host="cm")], whole=True)
    assert await wait_for(lambda: bool(fakes), timeout=5.0, step=0.05)
    assert await wait_for(lambda: "done, waiting" in agent.supervision["cm"]["doing"], timeout=5.0, step=0.05)
    sup = agent.supervision["cm"]
    assert sup["doing"] == "container gone — bringing it up: done, waiting for it to dial in" and sup["attempts"] == 0
    assert [c for c in fakes[0].calls if c[0] == fakes[0].cli][1][1] == "up"  # the whole idempotent host_up
    assert fakes[0].execs("agentorc-agent serve")
    v = agent._view(agent.remote["cm"]["ao-cm-w"])
    assert v["state"] == "unreachable" and v["host_link"]["supervisor"]["doing"] == sup["doing"]
    # the link comes up: the supervisor stands down and the overlay is plain again
    agent.links["cm"] = {"up": True, "since": "now", "why": "linked"}
    assert await wait_for(lambda: "cm" not in agent.supervision, timeout=5.0, step=0.05)
    assert "supervisor" not in agent._view(agent.remote["cm"]["ao-cm-w"])["host_link"]


@pytest.fixture
def volatile_home(container_home, tmp_path):
    """`container_home` with `volatile: true` on the node and a stopped container — before the
    `agent` fixture's first tick, which would otherwise bring it up."""
    hosts_yml = tmp_path / "home" / "hosts.yml"
    hosts_yml.write_text(hosts_yml.read_text().replace("    container:", "    volatile: true\n    container:"))
    container_home["make"] = lambda: Fake(inspect="exited\n")
    return container_home


async def test_the_home_derives_a_container_nodes_reach_when_it_dials_in(container_home, agent):
    """3c.5: one docker look per hello, kept on the link state, so every card of the node carries
    `host_link.reach` — and a look that fails, or a node not running, leaves no reach."""
    from test_link import record

    container_home["make"] = lambda: Fake(pid="9\n")
    agent._take_records("cm", [record("ao-cm-w", host="cm")], whole=True)
    agent.links["cm"] = {"up": True, "since": "now", "why": "linked"}
    await agent._note_reach("cm")
    reach = agent.links["cm"]["reach"]
    assert reach["container"] == "abc123def456" and reach["user"] == "developer"
    assert reach["vscode"].startswith("vscode://vscode-remote/attached-container+")
    assert agent._view(agent.remote["cm"]["ao-cm-w"])["host_link"]["reach"] == reach
    agent.links["cm"] = {"up": True, "since": "now", "why": "linked"}  # a new link: derived again, or not
    container_home["make"] = lambda: Fake(ps="")
    await agent._note_reach("cm")
    assert "reach" not in agent.links["cm"]
    agent.links["cm"] = {"up": False, "since": "now", "why": "down"}
    await agent._note_reach("cm")
    assert "reach" not in agent.links["cm"]
    await agent._note_reach("laptop")  # not a container node: nothing


async def test_a_stopped_volatile_container_is_left_alone_and_the_card_says_so(volatile_home, agent):
    from conftest import wait_for
    from test_link import record

    assert containers.container_nodes()["cm"].volatile is True
    fakes = volatile_home["fakes"]
    agent._take_records("cm", [record("ao-cm-w", host="cm")], whole=True)
    assert await wait_for(
        lambda: "volatile" in agent.supervision.get("cm", {}).get("doing", ""), timeout=5.0, step=0.05
    )
    await asyncio.sleep(0.4)  # a few rounds of the grace
    assert agent.supervision["cm"]["doing"] == "container exited — volatile, left as the person left it"
    assert not [c for f in fakes for c in f.calls if c[0] == f.cli]  # no `up`, ever
    assert agent._view(agent.remote["cm"]["ao-cm-w"])["host_link"]["supervisor"]["doing"].startswith("container exited")


async def test_a_failing_action_backs_off_and_the_card_says_why(container_home, agent):
    from conftest import wait_for

    fakes = container_home["fakes"]
    container_home["make"] = lambda: Fake(
        pid="", up=json.dumps({"outcome": "error", "message": "no space left"}) + "\n", ps=""
    )
    assert await wait_for(lambda: agent.supervision.get("cm", {}).get("attempts", 0) >= 2, timeout=5.0, step=0.05)
    sup = agent.supervision["cm"]
    assert "failed — devcontainer up failed for cm: no space left" in sup["doing"] and sup["error"]
    assert len(fakes) >= 2  # retried, with backoff, not on every tick
    # running, the agent alive, the link not up yet: nothing to do but wait
    container_home["make"] = lambda: Fake(pid="9\n")
    agent.supervision["cm"]["next"] = 0.0
    assert await wait_for(
        lambda: (
            "waiting for it to dial in" in agent.supervision["cm"]["doing"] and not agent.supervision["cm"]["error"]
        ),
        timeout=5.0,
        step=0.05,
    )


def test_backoff_starts_at_first_and_doubles_to_max_and_a_success_resets(container_home, agent, monkeypatch):
    monkeypatch.setattr(containers, "SUPERVISE_FIRST", 5.0)
    monkeypatch.setattr(containers, "SUPERVISE_MAX", 30.0)
    monkeypatch.setattr(containers, "SUPERVISE_GRACE", 1.0)
    sup = {"doing": "", "since": "", "attempts": 0, "next": 0.0, "error": ""}
    delays = []
    for _ in range(5):
        agent._supervised("cm", sup, "x: failed", failed=True)
        delays.append(round(sup["next"] - __import__("time").monotonic(), 1))
    assert delays == [5.0, 10.0, 20.0, 30.0, 30.0] and sup["attempts"] == 5 and sup["error"]
    agent._supervised("cm", sup, "x: done", failed=False)
    assert sup["attempts"] == 0 and not sup["error"] and round(sup["next"] - __import__("time").monotonic(), 1) == 1.0


async def test_a_runner_can_be_cancelled_mid_action_and_shutdown_and_forget_do_it(container_home, agent):
    import threading
    import time as _time

    from conftest import wait_for

    started = threading.Event()

    class Slow(Fake):
        def run(self, cmd, *, check=True, stream=False):
            if cmd[0] == self.cli and cmd[1] == "build":
                started.set()
                while not self.cancelled:  # a build that takes as long as it is allowed to
                    _time.sleep(0.01)
                raise ContainerError("cancelled")
            return super().run(cmd, check=check, stream=stream)

    container_home["make"] = lambda: Slow(ps="")
    assert await wait_for(started.is_set, timeout=5.0, step=0.05)  # awaited: the tick runs on this loop
    task, r = agent._supervising["cm"]
    async with LocalClient() as c:
        await c.call("forget_host", host="cm")  # a person forgot it: the action in flight is cut
    assert r.cancelled and "cm" not in agent.supervision
    assert await wait_for(task.done, timeout=5.0, step=0.05)
    # a real Runner's process is terminated by cancel
    real = Runner(log=lambda line: None)
    holder = {}

    def go():
        try:
            real.run(["sleep", "30"])
        except ContainerError as e:
            holder["err"] = str(e)

    t = threading.Thread(target=go)
    t.start()
    assert wait_for_sync(lambda: real.proc is not None, timeout=5.0)
    real.cancel()
    t.join(5.0)
    assert not t.is_alive() and holder["err"] == "cancelled"
