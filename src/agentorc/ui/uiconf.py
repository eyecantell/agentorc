"""The person's own UI configuration (design §5 *The person's own*, TD-095 second pass): `ui.yml`
beside `hosts.yml` and `org.yml` in the agentorc home, read by the UI process and by nothing else —
no session and no policy reads it, and nothing here reaches a host agent.

Its first key is **`open_in:`**, the editor button of the card and the Focus header:

- `vscode` — the default, and what a missing file or key means: today's two forms;
- `none` — no button anywhere;
- `{label: "…", url: "…"}` — a template of the person's own, which is how any other editor is
  reached. `{path}` is the directory, percent-encoded (TD-011); `{remote}` is the host's
  `vscode_host` from `hosts.yml`. A template must be `scheme://…`, and `javascript`, `data`,
  `vbscript` and `file` are refused as schemes — the scheme parsed, never a substring.

A value that does not parse, or is refused, is **named on the page** and the default button drawn:
the file is the person's own, and a pasted bad line must still not become a link that runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from sessionorc import paths

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


def ui_file() -> Path:
    return paths.home() / "ui.yml"


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


_cache: dict[str, tuple[float, OpenIn]] = {}


def open_in() -> OpenIn:
    """The person's `open_in:`, re-read when the file changes. A file that is missing is the
    default; one that does not parse is named, and the default drawn."""
    f = ui_file()
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return OpenIn()
    hit = _cache.get(str(f))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = yaml.safe_load(f.read_text()) or {}
        got = parse_open_in(data.get("open_in")) if isinstance(data, dict) else OpenIn(error=f"{f} is not a mapping")
    except (OSError, yaml.YAMLError) as e:
        got = OpenIn(error=f"{f} could not be read: {e.__class__.__name__}")
    if got.error and not got.error.startswith(str(f)):
        got = OpenIn(error=f"{f}: {got.error}")
    _cache[str(f)] = (mtime, got)
    return got


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
