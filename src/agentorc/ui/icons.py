"""The role icons the UI ships (design §4.8 *Role presets*, §4.5a, TD-074).

One place for the eight pictures `repoconfig.ICONS` names, so a config file never carries markup:
a name is a key here, and a name that is not is drawn as nothing. Each is a 24×24 stroke path drawn
in `currentColor` at ~12px inside the role badge — monochrome, so the state pill stays the one
coloured thing on a card — and `aria-hidden`, because the badge's own text is the label.
"""

from __future__ import annotations

from markupsafe import Markup

# name → the `d` of one `<path>`, stroked. Nothing is filled: a filled glyph at 12px reads as a
# button, and nothing on a card is pressed by its icon.
ICON_PATHS: dict[str, str] = {
    "flag": "M5 21V4M5 4h11l-2 4 2 4H5",
    "wrench": "M15 3a5 5 0 0 0-4.6 7L3 17.4 6.6 21l7.4-7.4A5 5 0 1 0 15 3z",
    "search": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5",
    "eye": "M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "book": "M4 4h9a3 3 0 0 1 3 3v13a3 3 0 0 0-3-3H4zM20 4v13",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
    "terminal": "M4 5h16v14H4zM8 10l3 2-3 2M13 14h4",
    "person": "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21a8 8 0 0 1 16 0",
}


def role_svg(name: str | None) -> Markup:
    """The icon's markup for a template, or nothing at all. A role with no icon, and a name this
    build does not know (an older page, a newer config), draw nothing — never an error on the page."""
    d = ICON_PATHS.get(str(name or ""))
    if not d:
        return Markup("")
    return Markup(
        '<svg class="ricon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'<path d="{d}"></path></svg>'
    )
