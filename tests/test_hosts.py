from agentorc import hosts


def test_local_host_from_file_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    for var in ("AGENTORC_HOST_NAME", "AGENTORC_VSCODE_HOST", "AGENTORC_LOCAL_HOST"):
        monkeypatch.delenv(var, raising=False)
    h = hosts.local_host()
    assert h.name and h.vscode_host == h.name and h.local is False  # hostname fallback
    (tmp_path / "hosts.yml").write_text("local:\n  name: kmaster\n  vscode_host: km\n")
    h = hosts.local_host()
    assert (h.name, h.vscode_host, h.local) == ("kmaster", "km", False)
    monkeypatch.setenv("AGENTORC_LOCAL_HOST", "1")
    assert hosts.local_host().local is True
    (tmp_path / "hosts.yml").write_text("local: [not a mapping\n")
    assert hosts.local_host().name  # a broken file degrades to the fallback, never raises
