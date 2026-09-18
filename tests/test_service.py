import pytest

from agentorc import service

pytestmark = pytest.mark.unit


def test_unit_text_shapes():
    a = service.unit_text("agentorc-agent", home="/x/home")
    assert "KillMode=process" in a  # restarting the agent must never take the tmux server down
    assert "agentorc-agent serve" in a and "Environment=AGENTORC_HOME=/x/home" in a
    assert "Environment=PATH=" in a and "WantedBy=default.target" in a
    u = service.unit_text("agentorc-ui", bind="127.0.0.1", port=8765)
    assert "--bind 127.0.0.1 --port 8765" in u and "After=agentorc-agent.service" in u
    assert "KillMode" not in u


def test_install_writes_the_wheel_before_it_restarts_the_units(tmp_path, monkeypatch):
    """The restarted agent takes its nodes' `hello`s at once and compares their build with the
    newest wheel (design §4.4a "The home supervises it"): written after the restart, the wheel
    left a window in which a node could be re-provisioned from the previous build (seen live at
    the promote of PR #225)."""
    from sessionorc import containers

    order = []
    monkeypatch.setattr(service, "UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr(containers, "write_wheel", lambda: order.append("wheel") or tmp_path / "w.whl")

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(service, "_systemctl", lambda *a: order.append(a[0]) or Done())
    written = service.install()
    assert order == ["wheel", "daemon-reload", "enable", "restart"]
    assert written[-1] == str(tmp_path / "w.whl") and len(written) == len(service.UNITS) + 1


def test_a_wheel_that_cannot_be_written_never_stops_the_units(tmp_path, monkeypatch, capsys):
    """Review of PR #227: written first, the wheel must not be what stops a promote."""
    from sessionorc import containers

    order = []
    monkeypatch.setattr(service, "UNIT_DIR", tmp_path / "units")

    def refuses():
        raise PermissionError("wheels: read-only")

    monkeypatch.setattr(containers, "write_wheel", refuses)

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(service, "_systemctl", lambda *a: order.append(a[0]) or Done())
    written = service.install()
    assert order == ["daemon-reload", "enable", "restart"] and len(written) == len(service.UNITS)
    assert "the wheel could not be written (PermissionError: wheels: read-only)" in capsys.readouterr().err
