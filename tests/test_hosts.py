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
    (tmp_path / "hosts.yml").write_text("local:\n  runs_keep_days: 0\n")
    assert hosts.local_host().runs_keep_days == 0  # 0 is a value: keep everything
    for bad in ("local: [not a mapping\n", "local: [a, b]\n", "local: true\n", "- just\n- a list\n", ""):
        (tmp_path / "hosts.yml").write_text(bad)
        h = hosts.local_host()  # malformed YAML, a non-mapping, or an empty file: fallback, never a crash
        assert h.name and h.local is False


def test_env_overrides_are_gone(tmp_path, monkeypatch):
    """TD-004: the file is the only source; the phase-1 env overrides no longer apply."""
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    (tmp_path / "hosts.yml").write_text("local:\n  name: fromfile\n")
    monkeypatch.setenv("AGENTORC_HOST_NAME", "fromenv")
    monkeypatch.setenv("AGENTORC_LOCAL_HOST", "1")
    h = hosts.local_host()
    assert h.name == "fromfile" and h.local is False
