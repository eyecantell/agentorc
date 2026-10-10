"""Claude Code adapter (design §4.2, §4.3): hook-fed state, transcript locator, usage, credentials.

Hooks reach a launched session through `claude --settings <hooks.json>`, a per-launch settings
layer, so nothing in the person's own `settings.json` is edited and hand-started sessions are
untouched. The hook command is `agentorc-hook`, which finds its session from `AGENTORC_SESSION`
in the tmux session's environment.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shlex
import shutil
import sys
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentorc import profiles as profiles_mod
from agentorc.profiles import Profile
from sessionorc import paths
from sessionorc.adapters import ExternalSession, LaunchSpec, Transcript
from sessionorc.models import Confidence, State
from sessionorc.screen import Manifest, Match, painted_text
from sessionorc.tmux import PaneInfo

from . import transcript as transcript_mod

HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
    "PostModelSwitch",  # `/model` mid-session: `to_model` is the model in use from now on (TD-031)
)
TRANSCRIPT_TAIL = 256 * 1024  # bytes of transcript read from the end to find the last assistant turn
# Each model's context window in tokens (design §4.3 `context`, TD-190), by the id's prefix — the
# transcript's `message.model` — read only when no status line has reported the window the session runs
# (`context_report`, TD-295). An id not here has no window: the reading shows the tokens alone. Source:
# Claude Code's *Model configuration*, which gives Fable 5 and later, Sonnet 5 and Opus 4.7 and later a
# native 1M window, and Opus 4.6 and Sonnet 4.6 1M only through the `[1m]` model suffix, which
# `message.model` does not show — so those two rows give the default 200k.
CONTEXT_WINDOWS = (
    ("claude-fable-5", 1_000_000),
    ("claude-mythos-5", 1_000_000),
    ("claude-opus-5", 1_000_000),
    ("claude-opus-4-8", 1_000_000),
    ("claude-opus-4-7", 1_000_000),
    ("claude-opus-4-6", 200_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-sonnet-4-6", 200_000),
    ("claude-haiku-4-5", 200_000),
)
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
log = logging.getLogger("agentorc.claude-code")


@dataclass
class Window:
    """One quota window of this tool, as the core and the UI see it (design §4.3, TD-073). The
    label is the adapter's — nothing above reads it, it is only printed."""

    label: str
    pct: int
    resets: str | None


class UsageRefused(Exception):
    """Why the usage endpoint gave no reading (TD-087). `reason` is one of `rate_limited`,
    `no_credentials`, `no_profile` and `error` — a word the core keys on, never prose — and
    `retry_after` is the endpoint's own `Retry-After` in seconds when it sent one."""

    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.reason, self.retry_after = reason, retry_after


def _retry_after(e: Exception) -> float | None:
    """`Retry-After` as seconds, when the endpoint sent one and it is a number. The date form is
    legal HTTP and is not parsed: an unreadable header is None, and the caller doubles instead."""
    try:
        raw = e.headers.get("Retry-After")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    try:
        return max(float(str(raw).strip()), 0.0) if raw else None
    except (TypeError, ValueError):
        return None


@dataclass
class Usage:
    """Every window this account has, worst last or not — the order is the endpoint's. A tool with
    a daily window, three windows or none reports exactly what it has; no field here is Claude's
    shape imposed on it."""

    windows: list[Window]
    fetched: str


def munge(path: Path | str) -> str:
    """Claude Code's project-dir name: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(path))


def config_dir(profile: Profile) -> Path:
    """The profile's config dir, else what Claude Code itself would use: `CLAUDE_CONFIG_DIR` if
    set in this process's environment, else `~/.claude`."""
    return profile.config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def global_config_file(profile: Profile) -> Path:
    """`.claude.json`: sign-in, MCP servers, per-project state such as trust decisions."""
    # the same fallback as `config_dir()`: under `CLAUDE_CONFIG_DIR` Claude Code keeps `.claude.json` there
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    d = profile.config_dir or (Path(env).expanduser() if env else None)
    return (d / ".claude.json") if d else Path("~/.claude.json").expanduser()


def pretrust(cwd: Path, profile: Profile) -> bool:
    """First-run quirk: mark `cwd` as trusted so the "trust this folder?" dialog never blocks a
    launched session (no hook reports it). Read-modify-write of the tool's own file, atomic
    replace, skipped when the flag is already set. Returns True when it wrote."""
    path = global_config_file(profile)
    lock = path.with_name(path.name + ".agentorc-lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as lk:
            # Serialises agentorc's own launches. Claude Code does not take this lock, so a
            # rewrite by a running session inside this window is still possible; the window is
            # one read + one write, and losing our flag only means the dialog appears once.
            fcntl.flock(lk, fcntl.LOCK_EX)
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            projects = data.setdefault("projects", {})
            entry = projects.setdefault(str(cwd), {})
            if entry.get("hasTrustDialogAccepted") is True:
                return False
            entry["hasTrustDialogAccepted"] = True
            tmp = path.with_suffix(".json.agentorc-tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(path)
            return True
    except (OSError, ValueError):
        return False


# dev-cadence's one SessionStart line (design §4.2; cadence §3, 2026-09-11). Byte-identical to the
# line in dev-cadence `files/.claude/settings.json` — a parity pair: the repo's own settings carry
# it for hand-started sessions, this layer carries it for the sessions agentorc starts. Since
# 2026-09-25 (dev-cadence TD-055 (b)) the line runs the MAIN checkout's runner — the first entry of
# `git worktree list --porcelain`, computed live, so it is right in a container as on the host — and
# dev-cadence's runner finds its children beside itself (its PR #101, 2026-09-22; a consumer's copy
# under scripts/ is what its last sync carried), so a worktree on an older branch runs the current
# hook set against itself; `$CLAUDE_PROJECT_DIR` alone resolved to that branch's own copy. In the
# main checkout the two are the same directory; outside a git repo `r` is empty and the old path
# plus the `[ -x ]` guard keep it a no-op, as in a directory that is not a dev-cadence consumer.
CADENCE_HOOK_LINE = (
    "r=$(git -C \"${CLAUDE_PROJECT_DIR:-.}\" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p'); "
    'f="${r:-$CLAUDE_PROJECT_DIR}/scripts/cadence_hooks.sh"; if [ -x "$f" ]; then "$f" --session-start; fi'
)
CADENCE_HOOK_TIMEOUT = 150  # > 5 children × the runner's 25 s child timeout
# A session directory whose own SessionStart already runs dev-cadence's hooks — the one runner
# line, or the pre-2026-09-11 per-hook block — gets the plain layer, or each hook would run twice.
# The legacy block runs only the hooks it names (none added after it was seeded); that worktree
# is current again once its branch carries the runner line. Only `.claude/settings.json` is read:
# dev-cadence seeds the line there and nowhere else (a hand-copied line in settings.local.json
# would run the set twice).
# The status line (design §4.4 *A report*, TD-233): redrawn at least this often, in seconds, so a
# session deep in one long tool run still reports; the flag that makes `agentorc-hook` the command.
STATUSLINE_REFRESH = 60
STATUSLINE_FLAG = "--statusline"
CADENCE_WIRED_MARKERS = ("scripts/cadence_hooks.sh", "scripts/nudge_user_attention.py")


def hooks_file(profile: Profile, cadence_line: bool = False, unattended: bool = False, padding: int = 0) -> Path:
    suffix = ("+cadence" if cadence_line else "") + ("+unattended" if unattended else "")
    suffix += f"+pad{padding}" if padding else ""
    return paths.home() / "claude-hooks" / f"{profile.name}{suffix}.json"


def hooks_settings(
    profile: Profile,
    hook_cmd: str = "agentorc-hook",
    cadence_line: bool = False,
    unattended: bool = False,
    padding: int = 0,
) -> dict:
    """The settings layer passed with `--settings`. Hooks, the status line, and two settings; the
    profile's own settings still apply. With `cadence_line`, SessionStart also runs dev-cadence's hook
    runner (CADENCE_HOOK_LINE). With `unattended`, the tool's own peer messages are refused (design
    §4.10 *The tool's own peer channel*, TD-064): its default holds one behind a deliver-or-deny panel
    that no hook reports and nobody at an unattended pane answers; the sender is told, and `ao msg` is
    the channel, and its prompt suggestions are off (TD-201). An interactive launch keeps the tool's
    defaults — its person is there to answer, and to read a suggestion.

    The status line is `agentorc-hook --statusline` on every launch (design §4.4 *A report*,
    TD-233): it reports the account's limits and then runs the status line it displaced, whose
    `padding` is carried here since the layer's is the one the tool draws with."""
    hooks: dict[str, list] = {}
    for ev in HOOK_EVENTS:
        # PermissionRequest may block for the whole permission wait; the others must be instant.
        timeout = profile.permission_wait + 15 if ev == "PermissionRequest" else 10
        hooks[ev] = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": timeout}]}]
    if cadence_line:
        hooks["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": CADENCE_HOOK_LINE, "timeout": CADENCE_HOOK_TIMEOUT}
        )
    layer: dict = {"hooks": hooks}
    layer["statusLine"] = {
        "type": "command",
        "command": f"{shlex.quote(hook_cmd)} {STATUSLINE_FLAG}",
        "refreshInterval": STATUSLINE_REFRESH,
        **({"padding": padding} if padding else {}),
    }
    if unattended:
        layer["crossSessionInbound"] = "refuse"
        # Nobody reads a suggested next prompt at an unattended pane, its generation spends usage
        # after every turn, and it is the suspect for the event that woke an idle grinder four
        # seconds after its Stop (TD-201).
        layer["promptSuggestionEnabled"] = False
    return layer


def repo_wires_cadence(cwd: Path) -> bool:
    """Does `cwd/.claude/settings.json` — the file Claude Code loads for this directory, so in a
    worktree the worktree's own copy — already run dev-cadence's SessionStart hooks? Substring match
    on each SessionStart command; an unreadable or malformed file counts as not wired (the guard in
    CADENCE_HOOK_LINE keeps that harmless)."""
    try:
        data = json.loads((cwd / ".claude" / "settings.json").read_text(encoding="utf-8"))
        groups = data.get("hooks", {}).get("SessionStart", [])
        cmds = [str(h.get("command", "")) for g in groups for h in g.get("hooks", [])]
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    return any(m in c for c in cmds for m in CADENCE_WIRED_MARKERS)


def hook_command() -> str | None:
    """`agentorc-hook` on PATH, else the one beside this interpreter: a container node's host agent
    runs from `/agentorc/venv` with that directory off its PATH (TD-301), and a venv's console
    scripts sit next to its python."""
    found = shutil.which("agentorc-hook")
    if found is not None:
        return found
    beside = Path(sys.executable).parent / "agentorc-hook"
    return str(beside) if beside.is_file() and os.access(beside, os.X_OK) else None


def _resolves(command: str) -> bool:
    """Whether a hook command's program is there to run: its first word on `PATH`, or an executable
    file at that path (`ao doctor`'s hooks check, design §4.7)."""
    try:
        prog = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    if os.sep in prog:
        return os.path.isfile(prog) and os.access(prog, os.X_OK)
    return shutil.which(prog) is not None


def layer_reading(profile: Profile) -> list[dict]:
    """Each settings layer written for `profile`, one per launch shape (plain, `+cadence`,
    `+unattended`, …): its path, and the commands it names — every hook's and the status line's,
    dev-cadence's own lines left out, of any vintage (TD-507) — with whether each resolves (design §4.7 **`ao doctor`**
    *hooks*, TD-465). A layer is written at a launch, so a shape never launched has none."""
    out: list[dict] = []
    for p in sorted((paths.home() / "claude-hooks").glob(f"{profile.name}*.json")):
        if p.stem != profile.name and not p.stem.startswith(f"{profile.name}+"):
            continue  # another profile whose name begins with this one's
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            cmds = [str(h.get("command", "")) for g in doc.get("hooks", {}).values() for e in g for h in e["hooks"]]
            cmds.append(str((doc.get("statusLine") or {}).get("command", "")))
        except (OSError, ValueError, AttributeError, TypeError, KeyError) as e:
            out.append({"path": str(p), "error": str(e) or type(e).__name__, "commands": []})
            continue
        # dev-cadence's line of any vintage — a layer last written before its runner line changed keeps the old one
        named = sorted({c for c in cmds if c and not any(m in c for m in CADENCE_WIRED_MARKERS)})
        out.append({"path": str(p), "commands": [{"command": c, "resolves": _resolves(c)} for c in named]})
    return out


def write_hooks_file(profile: Profile, cwd: Path | None = None, unattended: bool = False) -> Path:
    """Write the layer for this launch: the `+cadence` variant when `cwd` does not wire dev-cadence's
    hooks itself, the `+unattended` variant for an unattended launch. Up to four files per profile,
    chosen by name, so concurrent launches never overwrite each other's choice."""
    cadence_line = cwd is not None and not repo_wires_cadence(cwd)
    displaced = displaced_status_line(cwd, config_dir(profile)) if cwd is not None else None
    padding = displaced_padding(displaced)
    p = hooks_file(profile, cadence_line, unattended, padding)
    p.parent.mkdir(parents=True, exist_ok=True)
    cmd = hook_command()
    if cmd is None:
        # A bare name that the launched session cannot resolve either means no state feed at
        # all for this adapter — say so, since nothing downstream can tell.
        log.warning(
            "agentorc-hook not on PATH (%s); hooks for profile %s may never fire", os.environ.get("PATH"), profile.name
        )
        cmd = "agentorc-hook"
    layer = hooks_settings(profile, cmd, cadence_line, unattended, padding)
    p.write_text(json.dumps(layer, indent=1), encoding="utf-8")
    return p


def displaced_status_line(cwd: Path, cfg_dir: Path) -> dict | None:
    """The status line the launch's layer displaced (design §4.4 *The person's own status line still
    shows*): the first `statusLine` command named by the directory's `.claude/settings.local.json`,
    its `.claude/settings.json`, then the profile's own `settings.json`. Ours is never the one
    displaced — a file that names `agentorc-hook --statusline` is passed over, or the command would
    run itself. An unreadable file names none."""
    for f in (cwd / ".claude" / "settings.local.json", cwd / ".claude" / "settings.json", cfg_dir / "settings.json"):
        try:
            line = json.loads(f.read_text(encoding="utf-8")).get("statusLine")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(line, dict) and isinstance(line.get("command"), str) and line["command"].strip():
            if STATUSLINE_FLAG in line["command"] and "agentorc-hook" in line["command"]:
                continue
            return line
    return None


def displaced_padding(line: dict | None) -> int:
    """The displaced status line's `padding`, a small whole number, else 0."""
    pad = (line or {}).get("padding")
    return int(pad) if isinstance(pad, int | float) and not isinstance(pad, bool) and 0 < pad <= 20 else 0


# The status line's report (design §4.4 *A report*, §4.3 `usage_report`, TD-233): the windows the
# tool hands its status line, by the labels `parse_usage` gives the same windows. A per-model window
# is not in it, and `spend_limit` is a gateway's, not a subscription's.
STATUSLINE_WINDOWS = {"five_hour": "5h", "seven_day": "week"}


def usage_report(payload: dict) -> dict | None:
    """What a session was told about its account's limits, from its status line's stdin:
    `{windows: [{label, pct, resets}], work, sid}` — `pct` to one decimal, `resets` the instant the
    epoch seconds name, `work` the session's running total of API time
    (`cost.total_api_duration_ms`), which grows with each response and by which the command tells a
    fresh report from a redraw, and `sid` the tool's session id, whose running totals those are.
    None when the payload carries no window (an old client, a metered profile, before the session's
    first response)."""
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    windows = []
    for key, label in STATUSLINE_WINDOWS.items():
        w = limits.get(key) if isinstance(limits, dict) else None
        pct = w.get("used_percentage") if isinstance(w, dict) else None
        if not isinstance(pct, int | float) or isinstance(pct, bool):
            continue
        resets = w.get("resets_at")
        try:
            at = datetime.fromtimestamp(float(resets), UTC).replace(microsecond=0)
            iso = at.isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError, OSError):
            iso = None
        windows.append({"label": label, "pct": round(float(pct), 1), "resets": iso})
    if not windows:
        return None
    cost = payload.get("cost")
    work = cost.get("total_api_duration_ms") if isinstance(cost, dict) else None
    ok = isinstance(work, int | float) and not isinstance(work, bool)
    sid = payload.get("session_id")
    return {"windows": windows, "work": work if ok else None, "sid": sid if isinstance(sid, str) else None}


def context_report(payload: dict) -> dict | None:
    """The context the tool tells its status line it runs (design §4.3 `context`, TD-295):
    `{sid, window, tokens}` — `window` from `context_window.context_window_size`, `tokens` the prompt
    `context_window.current_usage` names (input plus both cache counts, as `context` reads a turn),
    None before the session's first response. None when the payload names no window (an old client)."""
    cw = payload.get("context_window") if isinstance(payload, dict) else None
    size = cw.get("context_window_size") if isinstance(cw, dict) else None
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or not isinstance(sid, str) or not sid:
        return None
    use = cw.get("current_usage")
    tokens = None
    if isinstance(use, dict):
        try:
            tokens = sum(
                int(use.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
        except (TypeError, ValueError):
            tokens = None
    return {"sid": sid, "window": size, "tokens": tokens or None}


def context_file(sid: str) -> Path:
    """Where the status line keeps what `context_report` last read for the tool's session `sid`."""
    return paths.home() / "statusline" / "context" / f"{Path(sid).name}.json"


def _instant(at: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None  # a naive time cannot be compared with an aware one


RULES_FILE = Path(__file__).with_name("screen_rules.toml")


# A launch with no prompt (`ao new` without one, Add entry's **Open a session**) lands at the composer:
# its SessionStart `startup` is no turn, and read as `working` the person's own session sat `working`
# with **→ Steer** in its composer until a first turn's Stop (TD-283). The launch says so in the pane's
# environment, since the payload cannot; a launch with a prompt still reports `working` on its start,
# because its prompt runs at once and its `UserPromptSubmit` may lag the SessionStart hooks.
AT_COMPOSER_ENV = "AGENTORC_AT_COMPOSER"
COMPOSER_GLYPH = "❯"  # the composer's prompt glyph; submitted prompts repeat it above, the composer is the last

# The tool's terminal title (design §4.5a **title**, §4.3 `title()`, TD-074). Claude Code writes the
# conversation's name there — the one a person gave it with the tool's own rename (*Error Checker*),
# else the tool's own summary — behind a status glyph that changes as it works (*✳ Error Checker*,
# seen on a live pane 2026-09-19). The decoration comes off; what is left is a name, unless it is the
# tool's own default or a shell's, in which case there is no name to show.
TITLE_GLYPHS = "✳✻✽✶✢✺✵∗·•●◐◓◑◒"  # the status marks the tool cycles through, and the dots beside them
TITLE_DEFAULTS = frozenset({"claude", "claude code"})  # what it sets before the conversation has a name
# A shell's own title, the usual one on a pane the tool has not renamed: `user@host: ~/dir`.
TITLE_HOSTISH = re.compile(r"^[^\s@]+@[^\s:]+:")


def _undecorated(pane_title: str) -> str:
    """The terminal title with the tool's leading status glyphs and spacing removed. Braille cells
    (U+2800–U+28FF) are the spinner's frames — a glyph by position rather than by character."""
    text = pane_title.strip()
    # One mark, and only when a space follows it — the tool's form is `✳ Name`. A name a person
    # chose may open with a character of its own, so nothing else is taken (review of PR #240).
    if len(text) > 1 and text[1].isspace() and (text[0] in TITLE_GLYPHS or "⠀" <= text[0] <= "⣿"):
        text = text[2:]
    elif text and len(text) == 1 and (text in TITLE_GLYPHS or "⠀" <= text <= "⣿"):
        text = ""
    return text.strip()


def _turn(line: bytes, source: str, offset: int) -> dict | None:
    """One transcript line as a turn (`spend`), or None when it is not an `assistant` entry with a
    `usage`: the four token kinds kept apart, because a coding agent's input is mostly cache reads
    at a tenth of the input rate. `response` is the API response's id: `spend` counts a response once
    within a read, and the home's ledger once across two reads it straddles (TD-151)."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant":
        return None
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None

    def n(key: str) -> int:
        v = usage.get(key)
        return int(v) if isinstance(v, int | float) and not isinstance(v, bool) and v > 0 else 0

    return {
        "at": d.get("timestamp"),
        "id": str(d.get("uuid") or ""),
        "source": source,
        "offset": offset,
        "model": str(msg.get("model") or ""),
        "input": n("input_tokens"),
        "output": n("output_tokens"),
        "cache_read": n("cache_read_input_tokens"),
        "cache_write": n("cache_creation_input_tokens"),
        "cost": None,
        "response": str(msg.get("id") or ""),
    }


def write_context_file(conversation: str, text: str) -> Path:
    """The start context as the file `--append-system-prompt-file` names (design §4.1 *No prose in the
    argv*, TD-339): under the home beside the launch scripts, owner-only, rewritten at each launch of
    the conversation and removed with the last record that holds it."""
    path = paths.context_file(conversation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)
    os.chmod(path, 0o600)
    return path


class ClaudeCodeAdapter:
    name = "claude-code"
    label = "Claude"  # the tool's display name: the usage chip's first word, never a key (§4.3, TD-122)
    state_source: Confidence = "hook"
    start_context = True  # `--append-system-prompt-file`, given at every launch of the conversation (§4.3, TD-283)

    def __init__(self, binary: str | None = None, rules: Path | None = None):
        self.binary = binary or "claude"
        self.rules = Manifest.load(rules or RULES_FILE)  # design §4.2 scraped fallback (TD-015)

    # -- launch ----------------------------------------------------------------------------------

    def launch(
        self,
        *,
        profile: str,
        resume: str | None,
        prompt: str | None,
        unattended: bool,
        cwd: Path,
        name: str = "",
        start_context: str | None = None,
    ) -> LaunchSpec:
        prof = profiles_mod.get(profile or None)
        adapter_id = resume or str(uuid.uuid4())
        pretrust(cwd, prof)
        argv = [self.binary, "--settings", str(write_hooks_file(prof, cwd, unattended))]
        argv += ["--resume", resume] if resume else ["--session-id", adapter_id]
        if name:
            argv += ["--name", name]
        if prof.model:
            argv += ["--model", prof.model]
        argv += prof.extra_args
        if unattended:
            argv += prof.unattended_args or ["--dangerously-skip-permissions"]
        if start_context:
            # the system prompt's tail, not a turn: the tool keeps it in no file of the session's, so
            # a resume is handed it again (design §4.3, TD-283) — by file, never as prose in the argv,
            # which every `pkill -f` on the host reads (design §4.1 *No prose in the argv*, TD-339)
            argv += ["--append-system-prompt-file", str(write_context_file(adapter_id, start_context))]
        env = {"AGENTORC_PERMISSION_WAIT": str(prof.permission_wait)}
        if not resume:
            env[AT_COMPOSER_ENV] = "1"  # every launch lands at the composer: `startup` reads idle (TD-283, TD-339)
        if prof.config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(prof.config_dir)
        # the prompt is typed at the composer by the host agent at the first idle, never passed (TD-339)
        return LaunchSpec(argv=argv, env=env, adapter_id=adapter_id, first_prompt=prompt or None)

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        m = self.explain(tail)
        return m.state if m else None

    def explain(self, tail: list[str]) -> Match | None:
        """The screen-rule verdict with its evidence (design §4.2, TD-015): what no hook reports —
        the trust dialog, the tool's own limit message. The agent applies it as `scraped` only
        when no fresher hook state exists."""
        return self.rules.explain(tail)

    def composer(self, tail_raw: list[str]) -> str | None:
        """The text painted in the composer (the last `❯` row of a raw tail), stripped; "" when it
        is empty; None when no composer row is on screen (a dialog, a dead pane). Faint text is not
        counted: the tool paints its suggested next prompt — often the session's own last prompt —
        and its first-run placeholder that way (TD-027, measured 2026-09-10 on 2.1.268)."""
        for row in reversed(tail_raw):
            text = painted_text(row).lstrip()
            if text.startswith(COMPOSER_GLYPH):
                return text[len(COMPOSER_GLYPH) :].strip("\xa0 ")
        return None

    def title(self, pane_title: str) -> str | None:
        """The session's name as the tool holds it (design §4.3 `title()`, §4.5a **title**, TD-074):
        the terminal title with the tool's decoration stripped. `None` when what is left is not a
        name — nothing at all, the tool's own default (*Claude Code*), or a shell's `user@host: dir`
        on a pane the tool never titled. Display only: agentorc never sets it, and a name is set
        where it was set, in the tool's own rename."""
        text = _undecorated(pane_title or "")
        if not text or text.lower() in TITLE_DEFAULTS or TITLE_HOSTISH.match(text):
            return None
        return text

    # -- locators --------------------------------------------------------------------------------

    def transcript_path(self, session_id: str, cwd: Path, profile: Profile | None = None) -> Path | None:
        base = config_dir(profile or profiles_mod.get(None)) / "projects" / munge(cwd)
        p = base / f"{session_id}.jsonl"
        return p if p.is_file() else None

    def _last_turns(self, session_id: str, cwd: Path, profile: str) -> Iterator[dict]:
        """The transcript's top-level `assistant` entries, newest first, from its last
        TRANSCRIPT_TAIL bytes: the one read `model_in_use` and `context` share. Never a sidechain
        entry, which is a subagent's turn rather than the session's; nothing when it cannot tell."""
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return  # an unknown profile: never fall back to another account's config dir
        p = self.transcript_path(session_id, cwd, prof)
        if p is None:
            return
        try:
            with p.open("rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - TRANSCRIPT_TAIL))
                chunk = f.read()
        except OSError:
            return
        for line in reversed(chunk.splitlines()):
            if b'"assistant"' not in line:
                continue
            try:
                d = json.loads(line)  # the first line of the tail may be a fragment: it just fails
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("type") == "assistant" and not d.get("isSidechain"):
                yield d

    def model_in_use(self, session_id: str, cwd: Path, profile: str = "") -> str | None:
        """The model this session is actually running, from the tail of its transcript: every
        `type: assistant` entry carries `message.model` (TD-031). The entry's own field, never a
        grep — `"model": "sonnet"` also appears inside an Agent call's `tool_input`, where it
        names a *requested subagent* model — and never a sidechain entry, which is a subagent's
        turn rather than the session's. None when it cannot tell, which is never an error."""
        for d in self._last_turns(session_id, cwd, profile):
            model = (d.get("message") or {}).get("model")
            if model and model != "<synthetic>":  # system entries carry that, not a model
                return str(model)
        return None

    def context(self, session_id: str, cwd: Path, profile: str = "") -> dict | None:
        """The session's context size now (design §4.3 `context`, §6 rule 5, TD-190):
        `{tokens, at, window}` from the last top-level turn's `usage` — the prompt that turn sent,
        `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens` — stamped with
        the entry's own time, and the model's window when CONTEXT_WINDOWS knows it. None when it
        cannot tell. No field name of the tool's leaves this method."""
        for d in self._last_turns(session_id, cwd, profile):
            msg = d.get("message") or {}
            usage = msg.get("usage")
            if not isinstance(usage, dict) or msg.get("model") == "<synthetic>":
                continue
            try:
                tokens = sum(
                    int(usage.get(k) or 0)
                    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
            except (TypeError, ValueError):
                continue
            if tokens <= 0:
                continue
            model = str(msg.get("model") or "")
            window = next((w for prefix, w in CONTEXT_WINDOWS if model.startswith(prefix)), None)
            return self._reported({"tokens": tokens, "at": d.get("timestamp"), "window": window}, session_id)
        return self._reported(None, session_id)

    @staticmethod
    def _reported(reading: dict | None, session_id: str) -> dict | None:
        """`reading` with what the session's status line last reported (TD-295): its window always,
        and its tokens when the report is later than the transcript's turn, or there is no turn."""
        try:
            rep = json.loads(context_file(session_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return reading
        if not isinstance(rep, dict) or not isinstance(rep.get("window"), int):
            return reading
        tokens, at = rep.get("tokens"), rep.get("at")
        if reading is None:
            return {"tokens": tokens, "at": at, "window": rep["window"]} if isinstance(tokens, int) else None
        reading = {**reading, "window": rep["window"]}
        mine, theirs = _instant(at), _instant(reading.get("at"))
        if isinstance(tokens, int) and tokens > 0 and mine and theirs and mine > theirs:
            reading.update(tokens=tokens, at=at)
        return reading

    def read_transcript(
        self,
        session_id: str,
        cwd: Path,
        profile: str = "",
        *,
        before: int | None = None,
        turns: int = 20,
        raw: bool = False,
    ) -> Transcript | None:
        """The session's transcript as neutral entries (design §4.3 `read_transcript`, TD-165), read
        backwards from `before` — its subagents from the directory the tool writes beside it. None
        when there is no file, or the profile is unknown (never another account's directory)."""
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return None
        p = self.transcript_path(session_id, cwd, prof)
        if p is None:
            return None
        try:
            return transcript_mod.read(
                p, before=before, turns=turns, raw=raw, subagents=p.with_suffix("") / "subagents"
            )
        except OSError:
            return None

    @staticmethod
    def short_model(model: str) -> str:
        """`claude-fable-5-1` → `fable-5-1` (design §4.2a's profile line, TD-031)."""
        return model[len("claude-") :] if model.startswith("claude-") else model

    def registry_entries(self, profile: Profile | None = None) -> list[dict]:
        """Claude Code's own live-session registry (`sessions/<pid>.json`): a cross-check for
        adopted sessions, carrying `status` (busy | idle | shell), `name`, `cwd`, `sessionId`."""
        out = []
        for p in (config_dir(profile or profiles_mod.get(None)) / "sessions").glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def external_sessions(self) -> list[ExternalSession]:
        """Live Claude Code sessions on this host from its registry, whatever started them.
        Entries whose pid is gone, or is now a different process, are dropped: the registry keeps
        `procStart` in the same clock ticks as /proc/<pid>/stat field 22, so equality means the same
        process (the primary test of dev-cadence's anchor guard; its extra fallbacks for entries
        that predate the field are not reproduced here — those rely on /proc existence, which is
        only meaningful within one pid namespace). Every declared profile's config dir is read
        (each account keeps its own registry under its `CLAUDE_CONFIG_DIR`), once per distinct
        directory (TD-013); a profile only this adapter cares about is one whose adapter is ours."""
        seen: set[Path] = set()
        entries: list[dict] = []
        for prof in profiles_mod.load()[0].values():
            d = config_dir(prof).resolve()
            if prof.adapter != self.name or d in seen:
                continue
            seen.add(d)
            entries.extend(self.registry_entries(prof))
        out: list[ExternalSession] = []
        for e in entries:
            pid, start = e.get("pid"), e.get("procStart")
            if not isinstance(pid, int) or not _pid_alive(pid, start):
                continue
            if e.get("cwd"):
                out.append(
                    ExternalSession(
                        adapter=self.name,
                        cwd=str(e["cwd"]),
                        name=str(e.get("name") or e.get("sessionId") or pid),
                        tool_id=e.get("sessionId"),
                        status=e.get("status"),
                    )
                )
        return out

    # -- account ---------------------------------------------------------------------------------

    def _creds(self, profile: Profile) -> dict | None:
        p = config_dir(profile) / ".credentials.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("claudeAiOauth")
        except (OSError, ValueError, AttributeError):
            return None

    def credentials_ok(self, profile: Profile) -> bool | None:
        c = self._creds(profile)
        if not c:
            return None
        # "Recoverable", not "valid right now": a live refresh token means the tool can refresh
        # the access token itself; only a dead refresh token needs a person to log in again.
        exp = c.get("refreshTokenExpiresAt") or c.get("expiresAt")
        if not exp:
            return True
        return datetime.fromtimestamp(int(exp) / 1000, UTC) > datetime.now(UTC)

    def doctor_profiles(self) -> list[dict]:
        """What `ao doctor` reads of each profile this adapter runs (design §4.7 **`ao doctor`**
        *hooks* and *profiles*, TD-465), for the host agent, which cannot read `profiles.yml`
        itself: the config dir, the credentials (`credentials_ok`: True, False for a dead refresh
        token, None for none), a metered profile's key, its settings layers (`layer_reading`), and
        the login line that cures a missing credential (`login`).
        `profiles.yml` that does not parse is one entry carrying `error` in its own words."""
        try:
            profs, _ = profiles_mod.load()
        except (OSError, ValueError, TypeError, AttributeError) as e:
            return [{"error": f"profiles.yml: {e}"}]
        out: list[dict] = []
        for prof in profs.values():
            if prof.adapter != self.name:
                continue
            row: dict = {
                "profile": prof.name,
                "account": prof.account or prof.name,
                "config_dir": str(config_dir(prof)),
                "metered": prof.metered,
                "credentials": None if prof.metered else self.credentials_ok(prof),
                "layers": layer_reading(prof),
                # the cure `ao doctor` prints for no credentials: the core names no tool (§4.3)
                "login": f"CLAUDE_CONFIG_DIR={config_dir(prof)} claude",
            }
            if prof.metered:
                row["key"] = self._key_set(prof)
            out.append(row)
        return out

    def _key_set(self, profile: Profile) -> bool:
        """Whether a metered profile has an API key to run on (§4.2a): `ANTHROPIC_API_KEY` in the
        host agent's environment, or an `env` key or `apiKeyHelper` in its config dir's settings."""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True
        try:
            doc = json.loads((config_dir(profile) / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        env = doc.get("env") if isinstance(doc, dict) else None
        return bool((isinstance(env, dict) and env.get("ANTHROPIC_API_KEY")) or doc.get("apiKeyHelper"))

    def account_for(self, profile: str) -> str | None:
        """The account a profile runs under (§4.2a, TD-122): the core polls usage once per
        account, since one login has one quota however many profiles share it. A profile that
        names no account is its own; an unknown profile says nothing."""
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return None
        return prof.account or prof.name

    def spend(self, profile: str, cursors: dict[str, int] | None = None) -> dict:
        """Spend per turn for a metered profile (design §4.3 *Spend per turn*, TD-151 slice 2):
        `{turns, cursors, reason}` — the design's `(turns, cursors)` with the reason beside them. Every
        transcript under the profile's config directory is read from its cursor (a byte offset; 0
        for one not in `cursors`) to its last whole line, sessions agentorc did not start and
        subagents' transcripts included, since both are billed. A cursor past its file's end means
        the tool rewrote the file (a compaction does), and it is read from 0 again — dropping what
        was already counted is the home's (§4.4). Each `assistant` entry's `usage` is one turn:
        `{at, id, source, offset, model, input, output, cache_read, cache_write, cost}`, `id` the
        entry's `uuid`, `cost` None (the tool does not price its turns; the home does, from the
        profile). One API response is written as one entry per content block, each carrying the
        response's usage, so a read counts a response once — its last entry, keyed by
        `message.id`, which the turn carries as `response`. `reason` is `ok`, or why nothing could be
        read, with the cursors unchanged."""
        before = {str(k): int(v) for k, v in (cursors or {}).items() if isinstance(v, int) and v >= 0}
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return {"turns": [], "cursors": before, "reason": "no_profile"}
        root = config_dir(prof) / "projects"
        if not root.is_dir():
            return {"turns": [], "cursors": before, "reason": f"no transcripts under {root}"}
        try:
            files = sorted(root.rglob("*.jsonl"))
        except OSError as e:
            return {"turns": [], "cursors": before, "reason": f"unreadable: {root}: {type(e).__name__}"}
        turns: list[dict] = []
        after: dict[str, int] = {}
        for p in files:
            key = str(p)
            start = before.get(key, 0)
            try:
                size = p.stat().st_size
                if start > size:
                    start = 0  # rewritten under us: read it again from the top
                if start == size:
                    after[key] = start
                    continue
                with p.open("rb") as f:
                    f.seek(start)
                    data = f.read(size - start)
            except OSError:
                if key in before:
                    after[key] = before[key]
                continue
            whole = data.rfind(b"\n") + 1  # a line still being written is read next time
            after[key] = start + whole
            by_message: dict[str, dict] = {}
            pos = start
            for line in data[:whole].splitlines(keepends=True):
                offset, pos = pos, pos + len(line)
                if b'"assistant"' not in line:
                    continue
                turn = _turn(line, key, offset)
                if turn is not None:
                    by_message[turn["response"] or turn["id"] or f"@{offset}"] = turn
            turns.extend(by_message.values())
        return {"turns": turns, "cursors": after, "reason": "ok"}

    def billing_for(self, profile: str) -> dict | None:
        """How the profile is billed (design §4.2a, TD-151): `{billing, prices}` as `profiles.yml`
        declares it, which the home reads before the cap rule and to price the turns — it cannot
        read a profile itself. None for a profile that does not resolve."""
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return None
        return {"billing": prof.billing, "prices": dict(prof.prices)}

    def usage_for(self, profile: str) -> dict | None:
        """The core-facing form of `usage()`: by profile name, as a plain dict —
        `{"windows": [{"label", "pct", "resets"}, ...], "fetched", "reason": "ok"}` (TD-001,
        TD-073), or **why there is no reading** (TD-087): `{"reason": "rate_limited",
        "retry_after": <seconds or None>}`, `{"reason": "no_credentials"}`, `{"reason": "error"}`.

        A reason, never prose: *rate-limited*, *no credentials*, *no network* and *no profile*
        were one silence — every failure returned `None` — so the chip stayed empty, the journal
        said nothing, the poll asked again a minute later, and a session at its cap was not marked
        `limited` while the endpoint refused us. The core does not act on the words; it logs a
        change of reason once, backs off on `rate_limited` and keeps the last good reading.
        """
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return {"reason": "no_profile"}
        if prof.metered:
            # §4.2a: a metered profile is never polled for a quota — the endpoint answers
            # `no_credentials` to a key, five minutes apart, forever; its bound is its spend (TD-151)
            return {"reason": "metered"}
        try:
            u = self.usage(prof)
        except UsageRefused as e:
            return {"reason": e.reason, "retry_after": e.retry_after}
        return {**asdict(u), "reason": "ok"} if u else {"reason": "error"}

    def usage(self, profile: Profile, timeout: float = 10.0) -> Usage | None:
        """This account's quota windows from the OAuth usage endpoint tdgrind already polls —
        for Claude Code the 5-hour and the weekly one, labelled `5h` and `week`. The token never
        touches argv; nothing here gates anything.

        Raises `UsageRefused` with a **reason** for every failure it can name (TD-087): the
        endpoint answers HTTP 429 `rate_limit_error` when the account's allowance is spent — by
        us, by the tool, by anything else on the same account — and a caller told only `None`
        answers it with another request a minute later. **A 200 whose body does not parse is the
        one that still returns `None`**: `parse_usage` reads what it can and answers nothing when
        the shape is not what it knows, which is a shape question and not a reason; `usage_for`
        turns that into `error`, which is what it is.
        """
        import urllib.error
        import urllib.request

        c = self._creds(profile)
        if not c or not c.get("accessToken"):
            raise UsageRefused("no_credentials")
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {c['accessToken']}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "agentorc (claude-code adapter)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — fixed https URL
                return parse_usage(json.loads(r.read().decode()))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise UsageRefused("rate_limited", _retry_after(e)) from None
            raise UsageRefused("error") from None
        except Exception:  # noqa: BLE001
            raise UsageRefused("error") from None


def _pid_alive(pid: int, proc_start: object) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    if proc_start in (None, ""):
        return True  # older entry without the field: existence is all we have
    try:
        actual = int(stat[stat.rindex(")") + 2 :].split()[19])
        return actual == int(proc_start)
    except (ValueError, IndexError):
        return True


# `seven_day_<x>` keys the endpoint reports that are not a model's weekly window
NOT_A_MODEL = {"oauth_apps"}


def parse_usage(d: dict) -> Usage | None:
    """The endpoint's windows as labelled entries: `5h`, `week` (all models), and every per-model
    weekly window it reports as `week · <Model>` (TD-122) — the account page shows *Fable 17%*
    beside *All models*, and the chip's worst-window rule must see it. A per-model window the
    endpoint sends empty (`null`, or no number) is not one."""
    try:
        f, w = d["five_hour"], d["seven_day"]
        windows = [
            Window(label="5h", pct=int(f["utilization"]), resets=f.get("resets_at")),
            Window(label="week", pct=int(w["utilization"]), resets=w.get("resets_at")),
        ]
    except (KeyError, TypeError, ValueError):
        return None
    for key, v in d.items():
        model = key.removeprefix("seven_day_")
        if model == key or not model or model in NOT_A_MODEL or not isinstance(v, dict):
            continue
        u = v.get("utilization")
        if isinstance(u, int | float) and not isinstance(u, bool):
            windows.append(Window(label=f"week · {model.capitalize()}", pct=int(u), resets=v.get("resets_at")))
    return Usage(windows=windows, fetched=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
