"""The person's own UI configuration (design §5 *The person's own*, TD-095, TD-146): `person:` in the
home's `settings.yml`, which the UI reads **through the host agent's `settings` read** — never the
file, since the UI need not run on the home — and keeps here between reads (`set_read`). No session
and no policy reads it, and nothing here reaches a host agent.

Its first key is **`open_in:`**, the editor button of the card and the Focus header:

- `vscode` — the default, and what a missing key, or no read yet, means: today's two forms;
- `none` — no button anywhere;
- `{label: "…", url: "…"}` — a template of the person's own, which is how any other editor is
  reached. `{path}` is the directory, percent-encoded (TD-011); `{remote}` is the host's
  `vscode_host` from `hosts.yml`. A template must be `scheme://…`, and `javascript`, `data`,
  `vbscript` and `file` are refused as schemes — the scheme parsed, never a substring.

A value that does not parse, or is refused, is **named on the page** and the default button drawn:
the file is the person's own, and a pasted bad line must still not become a link that runs.

**`ui.yml` is retired** (TD-146): it is no longer read, and one still on disk is named — the agent's
`settings` read carries the line (`migrate`), drawn where a refused value is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sessionorc.settings import BOARD_SHOW_DEFAULT, parse_board_show

REFUSED_SCHEMES = ("javascript", "data", "vbscript", "file")
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
# Cursor's own documentation (cursor.com/docs/reference/deeplinks, read 2026-09-21) documents only
# its `anysphere.cursor-deeplink` links, not a file or a remote-folder form, so §5's condition for
# the preset is not met: a Cursor user writes the form as a template (the message says which).
CURSOR_HINT = (
    "cursor is not a preset: Cursor's own documentation does not document a link that opens a folder "
    "(design §5) — write it as a template, {label: Cursor, url: "
    "'cursor://vscode-remote/ssh-remote+{remote}{path}?windowId=_blank'}"
)


@dataclass(frozen=True)
class OpenIn:
    """What the editor button is: `kind` is `vscode`, `none` or `template`; `error` names a value
    that was refused, in which case `kind` is the default."""

    kind: str = "vscode"
    label: str = "VS Code"
    url: str = ""
    error: str = ""


def parse_open_in(raw: object) -> OpenIn:
    """One `open_in:` value, read as §5 says. Never raises: a refusal is the default with a reason."""
    if raw is None or raw == "vscode":
        return OpenIn()
    if raw == "none":
        return OpenIn(kind="none", label="")
    if raw == "cursor":
        return OpenIn(error=CURSOR_HINT)
    if isinstance(raw, dict):
        label, url = raw.get("label"), raw.get("url")
        if not isinstance(label, str) or not label.strip():
            return OpenIn(error="open_in: a template needs a label, the button's words")
        if not isinstance(url, str) or not (m := _SCHEME.match(url.strip())):
            return OpenIn(error=f"open_in: {url!r} is not scheme://… — a template must name its scheme")
        if m.group(1).lower() in REFUSED_SCHEMES:
            return OpenIn(error=f"open_in: the {m.group(1).lower()} scheme is refused (design §5)")
        return OpenIn(kind="template", label=label.strip(), url=url.strip())
    return OpenIn(error=f"open_in: {raw!r} is not vscode, none or {{label, url}}")


WHERE = "settings.yml person.open_in"
_read: dict[str, Any] = {"person": {}, "migrate": [], "teams": {}}  # the last `settings` answer's parts


def set_read(answer: dict[str, Any] | None) -> None:
    """Keep the agent's `settings` answer (design §5): its `person` and its `migrate` lines. None —
    the agent down, or too old to answer — keeps the last one, so a blip never flips the button."""
    if not isinstance(answer, dict):
        return
    person = answer.get("person")
    migrate = answer.get("migrate")
    teams = answer.get("teams")
    _read["person"] = dict(person) if isinstance(person, dict) else {}
    _read["migrate"] = [str(m) for m in migrate] if isinstance(migrate, list) else []
    teams = teams if isinstance(teams, dict) else {}
    _read["teams"] = {str(k): dict(v) for k, v in teams.items() if isinstance(v, dict)}


def team_until(team: str) -> str:
    """A team's own stop time, `teams.<team>.until` (design §5, §6 *Team stop time*), as last read:
    the instant as written, or "" when it is absent, cleared or passed — the team header's **stops**
    note and a compact card's *differs* both read it (§4.5a *team card: stops note*, TD-337)."""
    t = _read["teams"].get(team) or {}
    until = t.get("until")
    if not isinstance(until, str) or not until or t.get("passed"):
        return ""
    try:
        at = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return ""
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return until if at > datetime.now(UTC) else ""


def open_in() -> OpenIn:
    """The person's `open_in:` as last read. None read yet, or no key, is the default; a value
    that does not parse is named, and the default drawn."""
    got = parse_open_in(_read["person"].get("open_in"))
    return OpenIn(error=f"{WHERE}: {got.error}") if got.error else got


def copy_on_select() -> bool:
    """The person's `terminal.copy_on_select` as last read (design §4.5a *Focus: copy on select*, §5
    `person:`, TD-174): on unless they turned it off — a pane that is mostly read is where a line is
    lifted out, and the selection is the lift."""
    term = _read["person"].get("terminal")
    got = term.get("copy_on_select") if isinstance(term, dict) else None
    return got if isinstance(got, bool) else True


def composer() -> str:
    """The person's `composer` as last read (design §4.5a *Focus composer* **the bar**, §5 `person:`,
    TD-500): `folded`, the bar under the terminal, unless they picked `open`."""
    return "open" if _read["person"].get("composer") == "open" else "folded"


def terminal() -> dict[str, Any]:
    """The person's terminal face and size as last read (design goal 12, §5 `person.terminal`, the
    Settings page; TD-148): `{size, face}`, each None where they set none — the pane's own default
    (13 px, JetBrains Mono) stands. `monospace` is appended by the page whatever the face is."""
    term = _read["person"].get("terminal")
    term = term if isinstance(term, dict) else {}
    size, face = term.get("size"), term.get("face")
    return {
        "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
        "face": face.strip() if isinstance(face, str) and face.strip() else None,
    }


def board_show() -> str:
    """The person's `inbox.board_show` as last read (design §5 `person:`, §4.5 screen 6 *The board's
    horizon*; TD-220): which board items the Inbox lists before they are due. Unset, or a value that
    does not parse, is the default `next:10`."""
    inbox = _read["person"].get("inbox")
    try:
        return parse_board_show(inbox.get("board_show") if isinstance(inbox, dict) else None)
    except ValueError:
        return BOARD_SHOW_DEFAULT


def migrate_note() -> str:
    """The line naming a retired `ui.yml` still on disk, or ""."""
    return _read["migrate"][0] if _read["migrate"] else ""


def editor_link(directory: str, *, local: bool, remote: str, reach: str = "") -> dict[str, str] | None:
    """The editor button for one directory, or None for no button. `local` is whether the UI runs
    on the machine the person sits at (`hosts.yml`'s `local`), `remote` the host's `vscode_host`,
    and `reach` the link a container node's record carries (§4.4a), which only the VS Code default
    knows how to follow: a template names no container, so a container's record draws none."""
    o = open_in()
    if o.kind == "none":
        return None
    if reach:
        return {"label": "VS Code", "url": reach} if o.kind == "vscode" else None
    if not directory:
        return None
    # percent-encoded, `/` kept so the path reads as a path (TD-011)
    path = quote(directory, safe="/")
    if o.kind == "template":
        return {"label": o.label, "url": o.url.replace("{path}", path).replace("{remote}", remote)}
    return {"label": "VS Code", "url": vscode_link(directory, local=local, remote=remote)}


def vscode_link(directory: str, *, local: bool, remote: str) -> str:
    """The default's two forms (design §4.5): `vscode://vscode-remote/ssh-remote+<alias><path>` — the
    alias must be in the person's own ~/.ssh/config — or `vscode://file/…` when the UI runs where
    the person sits. The path is percent-encoded: a space or `?` in a directory name would otherwise
    produce a URI the browser silently drops (TD-011); `/` stays, so the path reads as a path."""
    path = quote(directory, safe="/")
    if local:
        return f"vscode://file{path}?windowId=_blank"
    # windowId=_blank: a new VS Code window. Without it the handler reuses the current window and
    # replaces whatever it was showing (first-use finding 2026-09-06).
    return f"vscode://vscode-remote/ssh-remote+{remote}{path}?windowId=_blank"
