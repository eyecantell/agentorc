"""The closed markdown subset a message to the person is drawn in (design §4.10 *How a message to a
person is written*, §4.5a *Inbox row: details*; TD-127, built by TD-138).

The UI's **own renderer**, not a vendored library behind a sanitiser, so that *no raw HTML reaches
a page* is a property of this code rather than of a configuration: every character of the source
passes through `html.escape` on its way out, and the only tags emitted are the ones written below.

The subset: paragraphs (a line break inside one is kept), `*em*` and `**strong**`, `` `code` ``,
fenced code blocks, `-` and `1.` lists, and `[text](url)`. Everything else — raw HTML, headings,
images, tables — is drawn as the characters typed.

**A link is the one pressable thing text can make** (§4.5, TD-071 item 8): drawn only when its
target is absolute `http` or `https` and not the UI's own origin, opened in a new tab with
`noopener`, and followed by its host in small print, so link text can never pass for one of the
page's controls. Any other link is its characters.

`fold` is the Inbox's split of a text into what the row draws and what goes under *details*.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

# §4.5a *Inbox row: details*: the backstop for text nobody shaped — no blank line and longer than this.
FOLD_CHARS = 300

_FENCE = re.compile(r"^[ \t]*```")
_UL = re.compile(r"^[ \t]*-[ \t]+(.*)$")
_OL = re.compile(r"^[ \t]*(\d{1,9})[.)][ \t]+(.*)$")
_INLINE = re.compile(
    r"(?P<code>`+)(?P<ctext>.+?)(?P=code)"  # a code span: its contents are characters
    r"|(?<!!)\[(?P<ltext>[^\[\]\n]+)\]\((?P<url>[^()\s]+)\)"  # a link, never an image
    r"|\*\*(?P<strong>(?=\S).+?(?<=\S))\*\*"
    r"|(?<![*\w])\*(?P<em>(?=[^\s*]).*?(?<=[^\s*]))\*(?![*\w])"
)
# the end of a sentence, for the backstop: a stop and then white space or the end
_SENTENCE_END = re.compile(r"[.!?][)\"'”’]*(?=\s|$)")


def _netloc(url: str) -> str:
    return (urlsplit(url).netloc or "").lower()


def _link(text: str, url: str, origin: str | None) -> str | None:
    """The link's HTML, or None when the target is not one this page may draw (§4.10)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if origin and parts.netloc.lower() == _netloc(origin):
        return None  # the UI's own origin: a link there would be a control made from text
    return (
        f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(text)}</a>'
        f' <small class="lhost">{escape(host)}</small>'
    )


def inline(text: str, origin: str | None = None) -> str:
    """One run of text, inline constructs rendered and every other character escaped."""
    out: list[str] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(escape(text[pos : m.start()]))
        pos = m.end()
        if m.group("code"):
            out.append(f"<code>{escape(m.group('ctext'))}</code>")
        elif m.group("url") is not None:
            got = _link(m.group("ltext"), m.group("url"), origin)
            out.append(got if got is not None else escape(m.group(0)))
        elif m.group("strong") is not None:
            out.append(f"<strong>{inline(m.group('strong'), origin)}</strong>")
        else:
            out.append(f"<em>{inline(m.group('em'), origin)}</em>")
    out.append(escape(text[pos:]))
    return "".join(out)


def _lines(lines: list[str], origin: str | None) -> str:
    return "<br>".join(inline(x.strip(), origin) for x in lines)


def _block(lines: list[str], origin: str | None) -> list[str]:
    """One block between blank lines: runs of list items and runs of paragraph lines, in order."""
    out: list[str] = []
    para: list[str] = []
    items: list[list[str]] = []
    kind = ""

    def flush() -> None:
        nonlocal items, para, kind
        if items:
            tag = "ol" if kind == "ol" else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_lines(i, origin)}</li>" for i in items) + f"</{tag}>")
            items, kind = [], ""
        if para:
            out.append(f"<p>{_lines(para, origin)}</p>")
            para = []

    for line in lines:
        ul, ol = _UL.match(line), _OL.match(line)
        if ul or ol:
            k = "ul" if ul else "ol"
            if para or (items and k != kind):
                flush()
            kind = k
            items.append([ul.group(1) if ul else ol.group(2)])  # type: ignore[union-attr]
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1].append(line)  # an indented line continues the item above it
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return out


def render(text: str, origin: str | None = None) -> str:
    """The whole text as HTML from the closed subset (§4.10). `origin` is the UI's own
    `scheme://host:port`, so a link back to the page itself is drawn as characters."""
    out: list[str] = []
    block: list[str] = []
    fence: list[str] | None = None
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence is not None:
            if _FENCE.match(line):
                out.append(f"<pre><code>{escape(chr(10).join(fence))}</code></pre>")
                fence = None
            else:
                fence.append(line)
        elif _FENCE.match(line):
            out.extend(_block(block, origin))
            block, fence = [], []
        elif not line.strip():
            out.extend(_block(block, origin))
            block = []
        else:
            block.append(line)
    if fence is not None:  # an unclosed fence runs to the end, as a reader expects
        out.append(f"<pre><code>{escape(chr(10).join(fence))}</code></pre>")
    out.extend(_block(block, origin))
    return "".join(out)


def paragraph_break(text: str) -> tuple[str, str] | None:
    """`(first paragraph, the rest)` split at the first blank line — one inside a code block does
    not count, and `\r\n` is a line break — or None when the text has no such line. The one
    definition of *a blank line* that `fold` and `ao msg`'s shape warning share (design §4.10)."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = t.split("\n")
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and not line.strip():
            return "\n".join(lines[:i]).rstrip(), "\n".join(lines[i + 1 :]).strip()
    return None


def fold(text: str) -> tuple[str, str]:
    """design §4.5a *Inbox row: details*: `(lead, rest)` — the first paragraph, up to the first
    blank line (`paragraph_break`), and the rest; for text with no blank line and longer than
    `FOLD_CHARS`, the lead ends at the last sentence end before that, else at `FOLD_CHARS`. `rest`
    is empty when there is nothing to fold, and the row then draws no disclosure."""
    split = paragraph_break(text)
    if split is not None:
        return split
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(t) <= FOLD_CHARS:
        return t, ""
    ends = [m.end() for m in _SENTENCE_END.finditer(t, 0, FOLD_CHARS)]
    cut = ends[-1] if ends else FOLD_CHARS
    return t[:cut].rstrip(), t[cut:].strip()
