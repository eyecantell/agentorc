import pytest

from sessionorc import hosts

pytestmark = pytest.mark.unit


def test_local_host_from_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    h = hosts.local_host()
    # hostname fallback and every default
    assert h.name and h.vscode_host == h.name and h.local is False and h.volatile is False
    assert h.runs_keep_days == 30 and h.repos_registry.name == "repos.txt" and "~" not in str(h.repos_registry)
    reg = tmp_path / "repos.txt"
    reg.write_text("# roster\n/home/p/a\n\n/home/p/b\n/home/p/a\n")
    (tmp_path / "hosts.yml").write_text(
        f"local:\n  name: kmaster\n  vscode_host: km\n  local: true\n  volatile: true\n"
        f"  repos_registry: {reg}\n  runs_keep_days: 7\n"
    )
    h = hosts.local_host()
    assert (h.name, h.vscode_host, h.local, h.volatile, h.runs_keep_days) == ("kmaster", "km", True, True, 7)
    assert h.repos() == ["/home/p/a", "/home/p/b"]  # comments, blanks and repeats dropped
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  runs_keep_days: -1\n  repos_registry: /nope\n")
    h = hosts.local_host()
    assert h.runs_keep_days == 30 and h.repos() == []  # a bad value takes the default; a missing registry is empty
    (tmp_path / "hosts.yml").write_text("local:\n  runs_keep_days: 0\n  local: 'false'\n  volatile: 1\n")
    h = hosts.local_host()
    assert (
        h.runs_keep_days == 0 and h.local is False and h.volatile is False
    )  # 0 keeps all; only a real boolean is true
    for bad in ("local: [not a mapping\n", "local: [a, b]\n", "local: true\n", "- just\n- a list\n", ""):
        (tmp_path / "hosts.yml").write_text(bad)
        h = hosts.local_host()  # malformed YAML, a non-mapping, or an empty file: fallback, never a crash
        assert h.name and h.local is False


def test_scratch_home_never_reads_the_machine_roster(tmp_path, monkeypatch):
    """TD-298: a home other than the default, with no `repos_registry:` of its own, reads its own
    `repos.txt` — never dev-cadence's machine roster, whose real checkouts it would pull."""
    roster = tmp_path / "machine" / "repos.txt"
    roster.parent.mkdir()
    roster.write_text("/home/p/real\n")
    monkeypatch.setattr(hosts, "DEFAULT_REPOS_REGISTRY", str(roster))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("AGENTORC_HOME", str(scratch))
    h = hosts.local_host()
    assert h.repos_registry == scratch / "repos.txt" and h.repos() == []
    assert hosts.Host(name="x", vscode_host="x").repos() == []  # the dataclass's default reads the home too
    (scratch / "repos.txt").write_text("/home/p/mine\n")
    assert hosts.local_host().repos() == ["/home/p/mine"]
    # the default home (however it is spelled) keeps the machine roster
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTORC_HOME", "~/.agentorc")
    assert hosts.local_host().repos() == ["/home/p/real"]
    monkeypatch.delenv("AGENTORC_HOME")
    assert hosts.default_repos_registry() == roster


def test_serve_takes_the_tmux_socket_from_the_env(tmp_path, monkeypatch):
    """TD-298: `agentorc-agent serve` honours `AGENTORC_TMUX_SOCKET`, as the tests and `look_home.py`
    do, so a scratch home's agent never lists or drives the default server's sessions."""
    from sessionorc import agent

    seen = []

    async def fake_serve(a, sock=None):
        seen.append(a.tmux.socket_name)

    monkeypatch.setattr(agent, "serve_until_signal", fake_serve)
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    monkeypatch.setenv("AGENTORC_TMUX_SOCKET", "ao-scratch-td298")
    assert agent.main(["serve"]) == 0
    monkeypatch.delenv("AGENTORC_TMUX_SOCKET")
    assert agent.main(["serve"]) == 0
    assert seen == ["ao-scratch-td298", None]


def test_env_overrides_are_gone(tmp_path, monkeypatch):
    """TD-004: the file is the only source; the phase-1 env overrides no longer apply."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: fromfile\n")
    monkeypatch.setenv("AGENTORC_HOST_NAME", "fromenv")
    monkeypatch.setenv("AGENTORC_LOCAL_HOST", "1")
    h = hosts.local_host()
    assert h.name == "fromfile" and h.local is False
