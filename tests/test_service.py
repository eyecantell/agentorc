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


def test_the_ui_address_has_one_source(monkeypatch):
    """`ao ui`, `agentorc-ui` and `ao service install` default to the same bind and port, read from
    one place (TD-149 (7)): three literals once had to be kept in step by hand."""
    import uvicorn

    from agentorc import cli
    from agentorc.ui import app

    p = cli.build_parser()
    for argv in (["ui"], ["service", "status"]):
        a = p.parse_args(argv)
        assert (a.bind, a.port) == (service.DEFAULT_BIND, service.DEFAULT_PORT)
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.update(k))
    assert app.main([]) == 0
    assert (seen["host"], seen["port"]) == (service.DEFAULT_BIND, service.DEFAULT_PORT)
    assert f"--bind {service.DEFAULT_BIND} --port {service.DEFAULT_PORT}" in service.unit_text("agentorc-ui")


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


def test_the_tmux_system_unit_text():
    """Design §4.1 (TD-488, TD-495): the server as the person, in the foreground, outside the user
    manager — no bus and no runtime dir for tmux to open a scope with."""
    t = service.tmux_unit_text("paul", "/usr/bin/tmux")
    assert "User=paul\n" in t and "ExecStart=/usr/bin/tmux -D\n" in t
    for line in (
        "Type=simple",
        "Restart=always",
        "KillMode=control-group",
        "Delegate=yes",
        "WantedBy=multi-user.target",
    ):
        assert f"{line}\n" in t
    assert "XDG_RUNTIME_DIR" not in t and "DBUS_SESSION_BUS_ADDRESS" not in t
    assert "default.target" not in t, "a system unit, never the user manager's"


def test_install_stages_the_system_unit_only_when_it_is_absent_or_differs(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "SYSTEM_DIR", tmp_path / "etc")
    staged = service.stage_tmux_unit("paul")
    assert staged == tmp_path / "home" / "systemd" / "agentorc-tmux.service"
    assert staged.read_text() == service.tmux_unit_text("paul")
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "agentorc-tmux.service").write_text("[Service]\nExecStart=/old/tmux -D\n")
    assert service.stage_tmux_unit("paul") == staged, "an installed unit that differs is staged again"
    (tmp_path / "etc" / "agentorc-tmux.service").write_text(service.tmux_unit_text("paul"))
    assert service.stage_tmux_unit("paul") is None, "the installed one is current: no root line"


def test_install_system_refuses_anyone_but_root(monkeypatch):
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError, match=r"root's to install: run `sudo .*ao service install --system`"):
        service.install_system()


def test_install_system_copies_the_staged_unit_and_starts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "SYSTEM_DIR", tmp_path / "etc")
    (tmp_path / "etc").mkdir()
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(service.subprocess, "run", lambda argv, **kw: ran.append(argv) or Done())
    with pytest.raises(FileNotFoundError, match="nothing staged"):
        service.install_system()
    staged = service.stage_tmux_unit("paul")
    assert service.install_system() == str(tmp_path / "etc" / "agentorc-tmux.service")
    assert (tmp_path / "etc" / "agentorc-tmux.service").read_text() == staged.read_text()
    assert ran == [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "agentorc-tmux.service"]]


def test_status_says_where_the_tmux_server_runs(monkeypatch):
    from sessionorc import identity
    from sessionorc.tmux import Tmux

    monkeypatch.setattr(Tmux, "server_pid", lambda self: 4100)
    for cg, said in (
        ("/system.slice/agentorc-tmux.service", "ok: tmux — server 4100, system unit agentorc-tmux.service"),
        (
            "/user.slice/user-1000.slice/user@1000.service/app.slice/agentorc-agent.service",
            "warning: tmux — server 4100 under the user manager (/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/agentorc-agent.service): a stop of `systemd --user` ends every session; "
            "`ao service install` prints the system unit",
        ),
        ("/user.slice/user-1000.slice/session-3.scope", "tmux: server 4100, not under systemd"),
    ):
        monkeypatch.setattr(identity.LinuxProc, "cgroup", lambda self, pid, cg=cg: cg)
        assert service.tmux_placement().startswith(said)
    monkeypatch.setattr(Tmux, "server_pid", lambda self: None)
    assert service.tmux_placement() == "tmux: no server"


def test_ao_service_install_prints_the_root_line_only_when_staged(tmp_path, monkeypatch, capsys):
    from agentorc import cli

    monkeypatch.setattr(service, "install", lambda **kw: ["u.service"])
    monkeypatch.setattr(service, "status", lambda: "agentorc-agent: active")
    monkeypatch.setattr(service, "stage_tmux_unit", lambda: tmp_path / "agentorc-tmux.service")
    assert cli.main(["service", "install"]) == 0
    out = capsys.readouterr().out
    assert f"staged {tmp_path / 'agentorc-tmux.service'}; run once as root: sudo " in out
    assert out.rstrip().endswith("ao service install --system")
    monkeypatch.setattr(service, "stage_tmux_unit", lambda: None)
    assert cli.main(["service", "install"]) == 0
    assert "run once as root" not in capsys.readouterr().out
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    assert cli.main(["service", "install", "--system"]) == 1
    assert "root's to install" in capsys.readouterr().err
