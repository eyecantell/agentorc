"""A container node (design §4.4a "A container node", TD-057 step 3c.2): the generated definition,
`ao host up` driven through the subprocess seam with a fake `devcontainer`/`docker`, the
refusals by name, `forget`'s edit of hosts.yml, and the promote's wheel. Docker is not in
`pdm run test`: the live check is on kmaster, by hand."""

import json
import os
import subprocess
from pathlib import Path

import pytest

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
    assert not r.execs("agentorc-agent serve")  # a live pid behind the pidfile: not started twice
    assert r.execs("command -v gh")[0][r.execs("command -v gh")[0].index("--env-file") + 1] == str(n.env_file)
    r2 = Fake()
    containers.host_up("contractmatch", r2, rebuild=True)
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
    assert out["reach"]["vscode"].startswith("vscode-remote://dev-container+")
    gone = containers.host_status("contractmatch", Fake(ps=""))
    assert gone["container"] is None and gone["state"] is None and "reach" not in gone


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
