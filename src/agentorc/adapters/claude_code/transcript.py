"""Claude Code's transcript as the neutral shape (design §4.3 `read_transcript`, §4.5 screen 9, TD-165).

The file is one JSON object per line under the profile's config directory. It is read **backwards
from a byte offset** in chunks until enough prompts have been passed, so a long run's transcript
costs what is read and no more; a line split by a chunk boundary is carried into the next chunk
rather than dropped. Only here are the tool's field names known — `type`, `message.content`,
`tool_use_id`, `isSidechain`, `compact_boundary` — and none of them leaves this module.

What each line becomes:

- a `user` entry whose content is a string or text blocks: a **prompt** — unless it is the summary
  the tool writes after a compaction (`isCompactSummary`), which the compaction already stands for;
- a `user` entry's `tool_result` blocks: the **result** of the tool call they answer, by id;
- an `assistant` entry's blocks: **text**, a **thought** (`thinking`), or a **tool** call;
- a `system` entry of subtype `compact_boundary`, or a `summary` line: a **compaction**;
- an `isSidechain` entry in the file itself (older builds wrote subagents inline): folded into a
  **sidechain** under the latest Agent call before it, else a sidechain of its own;
- an Agent call whose subagent the tool wrote beside the session (`<session>/subagents/
  agent-*.jsonl`, its `.meta.json` naming the call's id): that file, read the same way, as the
  call's **sidechain**;

and every other line type is skipped.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sessionorc.adapters import Link, Transcript, TranscriptEntry

CHUNK = 256 * 1024  # bytes read per step backwards
HEAD = 64 * 1024  # bytes read from the file's start for its first timestamp
SIDECHAIN_CAP = 200  # entries of one subagent carried; `count` still says how many there were
RESULT_CAP = 20_000  # characters of one tool result carried (the reader folds it anyway)
CALL_CAP = 200  # characters of a call's one line
AGENT_TOOLS = ("Agent", "Task")  # the tool's names for the call that starts a subagent
# which input field is the call's line, in order, as the pane draws `Bash(<command>)` or `Read(<path>)`
CALL_KEYS = ("command", "file_path", "pattern", "path", "url", "query", "description", "prompt", "skill")


def read(
    path: Path, *, before: int | None = None, turns: int = 20, raw: bool = False, subagents: Path | None = None
) -> Transcript:
    """The last `turns` prompts before byte `before` of `path`, and everything after each of them up
    to `before`, as neutral entries. `subagents` is the directory the tool writes a session's
    subagents to. With `raw`, the last `turns` lines before `before`, as the file has them."""
    size = path.stat().st_size
    end = size if before is None else max(0, min(int(before), size))
    turns = max(1, int(turns))
    lines = _lines_back(path, end, turns, count_lines=raw)
    first_at = _first_at(path)
    if raw:
        start = lines[0][0] if lines else end
        return Transcript(
            path=str(path),
            size=size,
            first_at=first_at,
            turns=0,
            before=start or None,
            raw="\n".join(b.decode("utf-8", "replace") for _off, b in lines),
        )
    start = lines[0][0] if lines else end
    entries, prompts, last_at = _entries([b for _off, b in lines], subagents)
    return Transcript(
        path=str(path),
        size=size,
        first_at=first_at,
        last_at=last_at,
        turns=prompts,
        before=start or None,
        entries=entries,
    )


def _lines_back(path: Path, end: int, turns: int, *, count_lines: bool) -> list[tuple[int, bytes]]:
    """`(offset, line)` pairs, oldest first, read backwards from `end` until `turns` prompts (or,
    with `count_lines`, `turns` lines) are held — the first of them starting the list — or the file's
    start is reached. A line cut by a chunk boundary waits for the rest of it. With the prompts held,
    the read goes on only as far as the prompt before them: finding none, it reaches the file's
    start, so an *earlier turns* that would find nothing is never offered (the lines before a first
    prompt are the tool's bookkeeping)."""
    got: list[tuple[int, bytes]] = []  # newest first while reading
    tail: list[tuple[int, bytes]] = []  # read past the held prompts, kept only if no prompt precedes them
    seen = 0
    pos, carry = end, b""
    with path.open("rb") as f:
        while pos > 0:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            block = f.read(step) + carry
            parts = block.split(b"\n")
            carry = parts[0] if pos > 0 else b""  # the first part may be a cut line: wait for its start
            complete = parts[1:] if pos > 0 else parts
            offset = pos + len(parts[0]) + 1 if pos > 0 else 0
            placed: list[tuple[int, bytes]] = []
            for part in complete:
                placed.append((offset, part))
                offset += len(part) + 1
            for off, line in reversed(placed):
                if not line.strip():
                    continue
                counts = count_lines or _is_prompt_line(line)
                if seen >= turns:
                    if counts:
                        return list(reversed(got))  # an earlier turn exists: `before` is where this read began
                    tail.append((off, line))
                    continue
                got.append((off, line))
                if counts:
                    seen += 1
    return list(reversed(got + tail))


def _is_prompt_line(line: bytes) -> bool:
    if b'"user"' not in line:
        return False
    try:
        d = json.loads(line)
    except ValueError:
        return False
    return _prompt_text(d) is not None


def _prompt_text(d: dict[str, Any], inside: bool = False) -> str | None:
    """A prompt's text — a person's at the top level, the Agent call's inside a subagent — or None
    when the entry is not one."""
    if d.get("type") != "user" or (d.get("isSidechain") and not inside) or d.get("isCompactSummary"):
        return None
    content = (d.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        texts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(texts) if texts else None
    return None


def _first_at(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            head = f.read(HEAD)
    except OSError:
        return None
    for line in head.split(b"\n"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("timestamp"):
            return str(d["timestamp"])
    return None


def _call_line(tool_input: Any) -> str:
    """The call's one line: the input field the pane shows, first line, capped."""
    if not isinstance(tool_input, dict):
        return ""
    value = next((tool_input[k] for k in CALL_KEYS if isinstance(tool_input.get(k), str)), None)
    if value is None:
        value = next((v for v in tool_input.values() if isinstance(v, str)), "")
    first = value.strip().splitlines()[0] if value.strip() else ""
    return first if len(first) <= CALL_CAP else first[: CALL_CAP - 1] + "…"


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        text = ""
    return text if len(text) <= RESULT_CAP else text[:RESULT_CAP] + "\n…"


def _entries(
    lines: list[bytes], subagents: Path | None, *, inside: bool = False
) -> tuple[list[TranscriptEntry], int, str | None]:
    """The entries of these lines, oldest first; how many prompts; the last timestamp seen. `inside`
    reads a subagent's own lines, where every entry is a sidechain one and none is folded again."""
    out: list[TranscriptEntry] = []
    groups: list[tuple[TranscriptEntry, list[bytes]]] = []  # an inline sidechain's lines, by where they fold
    calls: dict[str, TranscriptEntry] = {}  # a tool call by its id, for the result that answers it
    agent_call: TranscriptEntry | None = None  # the latest Agent call, for an inline sidechain
    prompts, last_at = 0, None
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        at = str(d["timestamp"]) if d.get("timestamp") else None
        last_at = at or last_at
        kind = d.get("type")
        if d.get("isSidechain") and not inside and kind in ("user", "assistant"):
            if agent_call is not None:
                if agent_call.sidechain is None:
                    agent_call.sidechain = TranscriptEntry(kind="sidechain")
                target = agent_call.sidechain
            elif out and out[-1].kind == "sidechain":
                target = out[-1]
            else:
                target = TranscriptEntry(kind="sidechain", at=at)
                out.append(target)
            if groups and groups[-1][0] is target:
                groups[-1][1].append(line)
            else:
                groups.append((target, [line]))
            continue
        if kind == "summary" or (kind == "system" and d.get("subtype") == "compact_boundary"):
            out.append(TranscriptEntry(kind="compaction", at=at))
            continue
        if kind == "user":
            text = _prompt_text(d, inside)
            if text is not None:
                prompts += 1
                out.append(TranscriptEntry(kind="prompt", at=at, text=text))
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    call = calls.get(str(b.get("tool_use_id") or ""))
                    if call is not None:
                        call.result = _result_text(b.get("content"))
            continue
        if kind != "assistant":
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and str(b.get("text") or "").strip():
                out.append(TranscriptEntry(kind="text", at=at, text=str(b["text"])))
            elif t == "thinking" and str(b.get("thinking") or "").strip():
                thought = str(b["thinking"])
                out.append(TranscriptEntry(kind="thought", at=at, text=thought, lines=len(thought.splitlines())))
            elif t == "tool_use":
                entry = TranscriptEntry(
                    kind="tool", at=at, name=str(b.get("name") or ""), call=_call_line(b.get("input"))
                )
                calls[str(b.get("id") or "")] = entry
                out.append(entry)
                if entry.name in AGENT_TOOLS:
                    agent_call = entry
                    if subagents is not None:
                        entry.sidechain = _subagent(subagents, str(b.get("id") or ""))
    for target, group in groups:
        inner, _p, _l = _entries(group, None, inside=True)
        target.count += len(inner)
        target.entries.extend(inner[: max(0, SIDECHAIN_CAP - len(target.entries))])
    return out, prompts, last_at


# A URL as the session printed it (design §4.2 *The record's `links`*, TD-543): `http(s)://` to the first
# whitespace, closing bracket or quote, and a sentence's trailing punctuation off its end.
URL_RE = re.compile(r"https?://[^\s<>\[\]{}\"'`]+")
URL_TRAIL = ".,;:)"
LOOPBACK_NAMES = frozenset({"localhost", "0.0.0.0"})


def _loopback(url: str) -> bool:
    """Whether the URL's host is this machine's: `localhost`, `0.0.0.0`, or a loopback address."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return True  # a URL that does not parse is no link either
    if not host or host in LOOPBACK_NAMES or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urls_in(text: str) -> list[str]:
    """The http/https URLs in `text`, in order, a loopback host's left out."""
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(URL_TRAIL)
        if "://" in url and url.split("://", 1)[1] and not _loopback(url):
            out.append(url)
    return out


def _line_links(d: dict[str, Any]) -> list[str]:
    """The URLs one top-level entry printed: an `assistant` entry's text blocks, a `user` entry's
    tool results. A person's prompt, a thought, a call's input and a subagent's entry hold none."""
    if d.get("isSidechain") or d.get("isCompactSummary"):
        return []
    content = (d.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    if d.get("type") == "assistant":
        texts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    elif d.get("type") == "user":
        texts = [
            _result_text(b.get("content")) for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
    return [u for t in texts for u in urls_in(t)]


def links(path: Path, cursor: int = 0) -> tuple[list[Link], int]:
    """The URLs the transcript printed past byte `cursor`, oldest first, and the cursor after its last
    whole line (design §4.3 `links`, TD-543) — read forwards from the cursor as `spend` reads, never the
    whole file each pass. A cursor past the file's end means the tool rewrote it: read from 0. A line
    still being written is left for the next read. Raises OSError when the file cannot be read."""
    with path.open("rb") as f:
        size = f.seek(0, 2)
        start = cursor if 0 <= cursor <= size else 0
        f.seek(start)
        out: list[Link] = []
        pos = start
        while pos < size:
            line = f.readline()
            if not line.endswith(b"\n"):
                break
            pos += len(line)
            if b"http" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                at = str(d["timestamp"]) if d.get("timestamp") else None
                out.extend(Link(url=u, at=at) for u in _line_links(d))
    return out, pos


def _subagent(directory: Path, call_id: str) -> TranscriptEntry | None:
    """The subagent the Agent call `call_id` started, from the file the tool wrote beside the
    session: the `.meta.json` whose call id matches names it. None when there is none."""
    if not call_id:
        return None
    try:
        metas = sorted(directory.glob("*.meta.json"))
    except OSError:
        return None
    for meta in metas:
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("toolUseId") != call_id:
            continue
        f = meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl")
        try:
            with f.open("rb") as fh:
                data = fh.read()
        except OSError:
            return None
        inner, _p, _l = _entries([ln for ln in data.split(b"\n") if ln.strip()], None, inside=True)
        return TranscriptEntry(kind="sidechain", count=len(inner), entries=inner[:SIDECHAIN_CAP])
    return None
