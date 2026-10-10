"""The person's `open_in:` (design §5 *The person's own*, TD-095, TD-146): the editor button's label
and link, from `person:` in the home's `settings.yml` as the agent's `settings` read answers it — the
default, `none`, a template of the person's own, and every way a value is refused (named on the page,
the default drawn); `ui.yml` is retired and named as *migrate*."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from agentorc.ui import uiconf

pytestmark = pytest.mark.unit


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTORC_HOME", str(tmp_path))
    uiconf.set_read({"person": {}, "migrate": []})
    yield tmp_path
    uiconf.set_read({"person": {}, "migrate": []})


def write(home, text):
    """`person:` as the `settings` read hands it over: what a YAML line of the person's makes."""
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        doc = {"open_in": object()}  # the agent's reader drops what does not parse; a stand-in for it
    uiconf.set_read({"person": doc, "migrate": []})


def test_no_file_is_vscode_and_its_two_forms(home):
    assert uiconf.open_in() == uiconf.OpenIn()
    local = uiconf.editor_link("/r/my repo?", local=True, remote="km")
    assert local == {"label": "VS Code", "url": "vscode://file/r/my%20repo%3F?windowId=_blank"}
    ssh = uiconf.editor_link("/r", local=False, remote="km")
    assert ssh["url"] == "vscode://vscode-remote/ssh-remote+km/r?windowId=_blank"


def test_none_removes_the_button_everywhere_a_container_included(home):
    write(home, "open_in: none\n")
    assert uiconf.editor_link("/r", local=True, remote="km") is None
    assert uiconf.editor_link("", local=False, remote="km", reach="vscode://vscode-remote/x") is None


def test_a_template_fills_path_and_remote_and_its_label_is_the_persons(home):
    write(home, "open_in: {label: 'Zed <x>', url: 'zed://ssh/{remote}{path}'}\n")
    link = uiconf.editor_link("/r/a b", local=False, remote="km")
    assert link == {"label": "Zed <x>", "url": "zed://ssh/km/r/a%20b"}  # the template escapes the label
    # a template names no container: a container node's record draws no button under it
    assert uiconf.editor_link("", local=False, remote="km", reach="vscode://vscode-remote/x") is None


def test_a_folder_drops_a_templates_line_with_the_colon_before_it(home):
    """§5 `person.open_in` (TD-528, built by TD-529): `{line}` is the file form's; a folder form drops
    it with the one `:` before it, or bare where no `:` is there, and the file form still fills it."""
    write(home, "open_in: {label: Zed, url: 'zed://ssh/{remote}{path}:{line}'}\n")
    assert uiconf.editor_link("/r/d", local=False, remote="km")["url"] == "zed://ssh/km/r/d"
    assert uiconf.editor_file(local=False, remote="km") == "zed://ssh/km{path}:{line}"  # the page fills it
    write(home, "open_in: {label: X, url: 'x://open?dir={path}&l={line}'}\n")
    assert uiconf.editor_link("/r/d", local=False, remote="km")["url"] == "x://open?dir=/r/d&l="
    write(home, "open_in: {label: Zed, url: 'zed://ssh/{remote}{path}'}\n")
    assert uiconf.editor_link("/r/d", local=False, remote="km")["url"] == "zed://ssh/km/r/d"


def test_the_settings_url_note_names_line():
    html = (pathlib.Path(uiconf.__file__).parent / "templates" / "settings.html").read_text()
    note = html[html.index('name="url"') :]
    note = note[: note.index("</label>")]
    assert '<span class="mono">{line}</span> the line, on a file link; a folder drops it with the' in note


@pytest.mark.parametrize(
    "value, why",
    [
        ("cursor", "Cursor's own documentation"),  # not a preset: §5's condition is not met
        ("{label: X, url: 'javascript://alert(1)'}", "javascript scheme is refused"),
        ("{label: X, url: 'JavaScript://alert(1)'}", "javascript scheme is refused"),
        ("{label: X, url: 'data://text/html,x'}", "data scheme is refused"),
        ("{label: X, url: 'file:///etc/passwd'}", "file scheme is refused"),
        ("{label: X, url: 'javascript:alert(1)//x://y'}", "is not scheme://"),  # the scheme is parsed
        ("{label: X, url: 'no scheme here'}", "is not scheme://"),
        ("{url: 'zed://{path}'}", "needs a label"),
        ("emacs", "is not vscode, none"),
        ("[unclosed", "is not vscode, none"),
    ],
)
def test_a_refused_value_is_named_and_the_default_is_drawn(home, value, why):
    write(home, f"open_in: {value}\n")
    o = uiconf.open_in()
    assert why in o.error and o.error.startswith("settings.yml person.open_in: ")
    assert uiconf.editor_link("/r", local=True, remote="km")["url"].startswith("vscode://file/r")


def test_vscode_file_is_the_vscode_scheme_not_the_file_scheme(home):
    """§5: the scheme is the part before `://`, parsed — `vscode://file…` is scheme `vscode`."""
    write(home, "open_in: {label: VS Code here, url: 'vscode://file{path}'}\n")
    assert uiconf.open_in().error == ""
    assert uiconf.editor_link("/r", local=False, remote="km")["url"] == "vscode://file/r"


def test_the_org_page_names_a_refused_value(home, monkeypatch):
    from agentorc.ui.app import templates

    write(home, "open_in: cursor\n")
    html = templates.get_template("org.html").render(
        sessions=[], groups=None, counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [{"name": "t"}], "source": "", "notes": []}, host="h", active="Org",
        agent_down=False, volatile=False, usage={}, editor_note=uiconf.open_in().error,
    )  # fmt: skip
    assert 'id="editornote"' in html and "open_in refused" in html


def test_the_card_and_focus_draw_the_persons_label_escaped_and_none_draws_nothing(home):
    from agentorc.ui.app import templates, view

    rec = {"id": "ao-w", "name": "w", "kind": "agent", "adapter": "claude-code", "dir": "/r", "state": "idle",
           "since": "2026-09-21T01:00:00Z", "confidence": "hook", "pane": True, "tail": [],
           "created": "2026-09-21T00:00:00Z"}  # fmt: skip
    write(home, "open_in: {label: 'Zed <x>', url: 'zed://ssh/{remote}{path}'}\n")
    card = templates.get_template("card.html").render(s=view(rec))
    assert 'class="btn sm link editor"' in card and "‹› Zed &lt;x&gt;</a>" in card
    focus = templates.get_template("focus.html").render(s={**view(rec), "grants_all": []}, host="h", active="Org")
    assert "‹› Zed &lt;x&gt;</a>" in focus
    write(home, "open_in: none\n")
    assert (
        "editor"
        not in templates.get_template("card.html").render(s=view(rec)).split('class="sc-foot"')[1].split("<details")[0]
    )


def test_a_failed_read_keeps_the_last_and_a_retired_ui_yml_is_named(home):
    """TD-146: None from the agent (down, or too old for the read) keeps the last answer, so a blip
    never flips the button; a `ui.yml` left on disk comes back as the read's `migrate` line."""
    write(home, "open_in: none\n")
    uiconf.set_read(None)
    assert uiconf.open_in().kind == "none"
    assert uiconf.migrate_note() == ""
    uiconf.set_read(
        {"person": {}, "migrate": ["/h/ui.yml: ui.yml is no longer read — its open_in lives under person:"]}
    )
    assert "no longer read" in uiconf.migrate_note() and uiconf.open_in() == uiconf.OpenIn()
    from agentorc.ui.app import templates

    html = templates.get_template("org.html").render(
        sessions=[], groups=None, counts=dict.fromkeys(("needs-you", "limited", "stalled?"), 0),
        strip={"teams": [{"name": "t"}], "source": "", "notes": []}, host="h", active="Org",
        agent_down=False, volatile=False, usage={}, editor_note="", migrate_note=uiconf.migrate_note(),
    )  # fmt: skip
    assert 'id="migratenote"' in html and "ui.yml is no longer read" in html
