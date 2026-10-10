import json

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
    t = service.tmux_unit_text("paul", "/home/paul")
    assert "User=paul\n" in t and "ExecStart=" in t and t.split("ExecStart=")[1].split("\n")[0].endswith("tmux -D")
    assert ":/home/paul/.local/bin:" in t and "Environment=LANG=C.UTF-8\n" in t
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


def test_the_watch_unit_texts():
    """Design §4.10 *When the home itself is down* (TD-497): a oneshot as the person whose
    `ExecStartPre` is root's and starts the user manager, on a system timer every five minutes."""
    t = service.watch_service_text("paul", 1000, "/home/paul")
    for line in (
        "Type=oneshot",
        "User=paul",
        "ExecStartPre=-+/usr/bin/systemctl start user@1000.service",
        "Environment=LANG=C.UTF-8",
    ):
        assert f"{line}\n" in t
    assert t.split("ExecStart=")[1].split("\n")[0].endswith("agentorc-watch")
    assert ":/home/paul/.local/bin:" in t, "doppler and the venv resolve as the agent unit's PATH does"
    assert "[Install]" not in t, "the timer is what is enabled, never the service"
    timer = service.watch_timer_text()
    for line in ("OnBootSec=2min", "OnUnitActiveSec=5min", "Unit=agentorc-watch.service", "WantedBy=timers.target"):
        assert f"{line}\n" in timer
    assert list(service.system_units("paul", 1000)) == [
        "agentorc-tmux.service",
        "agentorc-watch.service",
        "agentorc-watch.timer",
    ]


def test_install_stages_the_system_units_only_when_absent_or_differing(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "SYSTEM_DIR", tmp_path / "etc")
    units = service.system_units("paul", os.getuid())
    staged = service.stage_system_units("paul")
    assert staged == [tmp_path / "home" / "systemd" / name for name in units]
    assert [p.read_text() for p in staged] == list(units.values())
    (tmp_path / "etc").mkdir()
    for name, text in units.items():
        (tmp_path / "etc" / name).write_text(text)
    (tmp_path / "etc" / "agentorc-tmux.service").write_text("[Service]\nExecStart=/old/tmux -D\n")
    assert service.stage_system_units("paul") == staged[:1], "an installed unit that differs is staged again"
    (tmp_path / "etc" / "agentorc-tmux.service").write_text(units["agentorc-tmux.service"])
    (tmp_path / "etc" / "agentorc-watch.timer").unlink()
    assert service.stage_system_units("paul") == staged[2:], "the watch's timer absent beside a current tmux unit"
    (tmp_path / "etc" / "agentorc-watch.timer").write_text(units["agentorc-watch.timer"])
    assert service.stage_system_units("paul") == [], "every one current: no root line"


def test_install_system_refuses_anyone_but_root(monkeypatch):
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError, match=r"root's to install: run `sudo .*ao service install --system`"):
        service.install_system()


def test_install_system_writes_the_unit_made_again_as_root_and_starts_it(tmp_path, monkeypatch):
    """Never the staged file: any session can write it, and root would run what it says (review)."""
    import getpass
    import pwd

    me = getpass.getuser()
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "SYSTEM_DIR", tmp_path / "etc")
    (tmp_path / "etc").mkdir()
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(service.subprocess, "run", lambda argv, **kw: ran.append(argv) or Done())
    monkeypatch.setattr(service, "_server_answers", lambda uid: False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(RuntimeError, match="no person to run the tmux server as"):
        service.install_system()
    monkeypatch.setenv("SUDO_USER", me)
    for staged in service.stage_system_units(me):  # rewritten by a session
        staged.write_text("[Service]\nUser=root\nExecStart=/bin/sh -c 'evil'\n")
    pw = pwd.getpwnam(me)
    units = service.system_units(me, pw.pw_uid, pw.pw_dir)
    assert service.install_system() == [str(tmp_path / "etc" / name) for name in units]
    for name, text in units.items():
        assert (tmp_path / "etc" / name).read_text() == text
    assert ran[1:] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "agentorc-tmux.service", "agentorc-watch.timer"],
    ]
    monkeypatch.setattr(service, "_server_answers", lambda uid: True)  # the old server, not the unit's
    with pytest.raises(RuntimeError, match="already answers on .* default socket"):
        service.install_system()


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
    monkeypatch.setattr(
        service, "stage_system_units", lambda: [tmp_path / "agentorc-tmux.service", tmp_path / "agentorc-watch.timer"]
    )
    assert cli.main(["service", "install"]) == 0
    out = capsys.readouterr().out
    staged = f"{tmp_path / 'agentorc-tmux.service'}, {tmp_path / 'agentorc-watch.timer'}"
    assert f"staged {staged}; run once as root: sudo " in out
    assert out.rstrip().endswith("ao service install --system")
    monkeypatch.setattr(service, "stage_system_units", lambda: [])
    assert cli.main(["service", "install"]) == 0
    assert "run once as root" not in capsys.readouterr().out
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    assert cli.main(["service", "install", "--system"]) == 1
    assert "root's to install" in capsys.readouterr().err



def test_ao_service_install_says_a_failed_stage_and_still_succeeds(monkeypatch, capsys):
    from agentorc import cli

    monkeypatch.setattr(service, "install", lambda **kw: ["u.service"])
    monkeypatch.setattr(service, "status", lambda: "agentorc-agent: active")

    def unwritable():
        raise OSError("read-only home")

    monkeypatch.setattr(service, "stage_system_units", unwritable)
    assert cli.main(["--json", "service", "install"]) == 0
    out = capsys.readouterr()
    assert "the system units could not be staged (read-only home)" in out.err
    reply = json.loads(out.out)
    assert reply["staged"] == [] and reply["root"] is None


def test_ao_service_install_system_names_what_it_wrote(monkeypatch, capsys):
    from agentorc import cli

    monkeypatch.setattr(service, "install_system", lambda: ["/etc/systemd/system/agentorc-tmux.service"])
    assert cli.main(["--json", "service", "install", "--system"]) == 0
    assert json.loads(capsys.readouterr().out) == {"written": ["/etc/systemd/system/agentorc-tmux.service"]}
    assert cli.main(["service", "install", "--system"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote /etc/systemd/system/agentorc-tmux.service; ")
    assert f"{service.TMUX_UNIT} and {service.WATCH_UNIT}.timer enabled and started" in out

def test_the_watch_reads_as_not_loaded_on_a_host_without_systemd(monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(service.subprocess, "run", missing)
    assert service.watch_reading() == {"loaded": False}


def test_status_names_the_watch_timer(monkeypatch):
    class Out:
        def __init__(self, stdout):
            self.stdout, self.stderr, self.returncode = stdout, "", 0

    def fake(argv, **kw):
        if argv[:2] == ["systemctl", "show"]:
            return Out(shown)
        return Out("active\n")

    monkeypatch.setattr(service.subprocess, "run", fake)
    monkeypatch.setattr(service, "tmux_placement", lambda: "tmux: no server")
    shown = "LoadState=loaded\nActiveState=active\nLastTriggerUSec=Fri 2026-10-09 14:35:02 BST\n"
    assert service.watch_reading() == {"loaded": True, "active": "active", "last": "Fri 2026-10-09 14:35:02 BST"}
    assert service.status().endswith("tmux: no server\nagentorc-watch.timer: active")
    shown = "LoadState=not-found\nActiveState=inactive\nLastTriggerUSec=n/a\n"
    assert service.status().endswith("agentorc-watch.timer: not installed")
