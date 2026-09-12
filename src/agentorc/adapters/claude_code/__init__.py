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
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentorc import profiles as profiles_mod
from agentorc.profiles import Profile
from sessionorc import paths
from sessionorc.adapters import ExternalSession, LaunchSpec
from sessionorc.models import Confidence, State
from sessionorc.screen import Manifest, Match, painted_text
from sessionorc.tmux import PaneInfo

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
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
log = logging.getLogger("agentorc.claude-code")


@dataclass
class Usage:
    five_hour_pct: int
    weekly_pct: int
    five_hour_resets: str | None
    weekly_resets: str | None
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
# it for hand-started sessions, this layer carries it for the sessions agentorc starts, so a
# worktree whose settings predate a hook change still runs the current set. The `[ -x ]` guard
# makes it a no-op in a directory that is not a dev-cadence consumer.
CADENCE_HOOK_LINE = 'f="$CLAUDE_PROJECT_DIR/scripts/cadence_hooks.sh"; if [ -x "$f" ]; then "$f" --session-start; fi'
CADENCE_HOOK_TIMEOUT = 150  # > 5 children × the runner's 25 s child timeout
# A session directory whose own SessionStart already runs dev-cadence's hooks — the one runner
# line, or the pre-2026-09-11 per-hook block — gets the plain layer, or each hook would run twice.
CADENCE_WIRED_MARKERS = ("scripts/cadence_hooks.sh", "scripts/nudge_user_attention.py")


def hooks_file(profile: Profile, cadence_line: bool = False) -> Path:
    suffix = "+cadence" if cadence_line else ""
    return paths.home() / "claude-hooks" / f"{profile.name}{suffix}.json"


def hooks_settings(profile: Profile, hook_cmd: str = "agentorc-hook", cadence_line: bool = False) -> dict:
    """The settings layer passed with `--settings`. Only hooks; the profile's own settings still apply.
    With `cadence_line`, SessionStart also runs dev-cadence's hook runner (CADENCE_HOOK_LINE)."""
    hooks: dict[str, list] = {}
    for ev in HOOK_EVENTS:
        # PermissionRequest may block for the whole permission wait; the others must be instant.
        timeout = profile.permission_wait + 15 if ev == "PermissionRequest" else 10
        hooks[ev] = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": timeout}]}]
    if cadence_line:
        hooks["SessionStart"][0]["hooks"].append(
            {"type": "command", "command": CADENCE_HOOK_LINE, "timeout": CADENCE_HOOK_TIMEOUT}
        )
    return {"hooks": hooks}


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


def write_hooks_file(profile: Profile, cwd: Path | None = None) -> Path:
    """Write the layer for this launch: the `+cadence` variant when `cwd` does not wire dev-cadence's
    hooks itself. Two files per profile, chosen by name, so concurrent launches into different
    directories never overwrite each other's choice."""
    cadence_line = cwd is not None and not repo_wires_cadence(cwd)
    p = hooks_file(profile, cadence_line)
    p.parent.mkdir(parents=True, exist_ok=True)
    cmd = shutil.which("agentorc-hook")
    if cmd is None:
        # A bare name that the launched session cannot resolve either means no state feed at
        # all for this adapter — say so, since nothing downstream can tell.
        log.warning(
            "agentorc-hook not on PATH (%s); hooks for profile %s may never fire", os.environ.get("PATH"), profile.name
        )
        cmd = "agentorc-hook"
    p.write_text(json.dumps(hooks_settings(profile, cmd, cadence_line), indent=1), encoding="utf-8")
    return p


RULES_FILE = Path(__file__).with_name("screen_rules.toml")


COMPOSER_GLYPH = "❯"  # the composer's prompt glyph; submitted prompts repeat it above, the composer is the last


class ClaudeCodeAdapter:
    name = "claude-code"
    state_source: Confidence = "hook"

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
    ) -> LaunchSpec:
        prof = profiles_mod.get(profile or None)
        adapter_id = resume or str(uuid.uuid4())
        pretrust(cwd, prof)
        argv = [self.binary, "--settings", str(write_hooks_file(prof, cwd))]
        argv += ["--resume", resume] if resume else ["--session-id", adapter_id]
        if name:
            argv += ["--name", name]
        if prof.model:
            argv += ["--model", prof.model]
        argv += prof.extra_args
        if unattended:
            argv += prof.unattended_args or ["--dangerously-skip-permissions"]
        if prompt:
            if prompt.startswith("-"):
                argv.append("--")  # a pasted brief that starts with '-' is a prompt, not an option
            argv.append(prompt)
        env = {"AGENTORC_PERMISSION_WAIT": str(prof.permission_wait), "AGENTORC_PROFILE": prof.name}
        if prof.config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(prof.config_dir)
        return LaunchSpec(argv=argv, env=env, adapter_id=adapter_id)

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

    # -- locators --------------------------------------------------------------------------------

    def transcript_path(self, session_id: str, cwd: Path, profile: Profile | None = None) -> Path | None:
        base = config_dir(profile or profiles_mod.get(None)) / "projects" / munge(cwd)
        p = base / f"{session_id}.jsonl"
        return p if p.is_file() else None

    def model_in_use(self, session_id: str, cwd: Path, profile: str = "") -> str | None:
        """The model this session is actually running, from the tail of its transcript: every
        `type: assistant` entry carries `message.model` (TD-031). The entry's own field, never a
        grep — `"model": "sonnet"` also appears inside an Agent call's `tool_input`, where it
        names a *requested subagent* model — and never a sidechain entry, which is a subagent's
        turn rather than the session's. None when it cannot tell, which is never an error."""
        try:
            prof = profiles_mod.get(profile or None)
        except (KeyError, ValueError):
            return None  # an unknown profile: never fall back to another account's config dir
        p = self.transcript_path(session_id, cwd, prof)
        if p is None:
            return None
        try:
            with p.open("rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - TRANSCRIPT_TAIL))
                chunk = f.read()
        except OSError:
            return None
        for line in reversed(chunk.splitlines()):
            if b'"assistant"' not in line:
                continue
            try:
                d = json.loads(line)  # the first line of the tail may be a fragment: it just fails
            except ValueError:
                continue
            if d.get("type") != "assistant" or d.get("isSidechain"):
                continue
            model = (d.get("message") or {}).get("model")
            if model and model != "<synthetic>":  # system entries carry that, not a model
                return str(model)
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

    def usage_for(self, profile: str) -> dict | None:
        """The core-facing form of `usage()`: by profile name, as a plain dict (TD-001)."""
        try:
            u = self.usage(profiles_mod.get(profile or None))
        except (KeyError, ValueError):
            return None
        return asdict(u) if u else None

    def usage(self, profile: Profile, timeout: float = 10.0) -> Usage | None:
        """5-hour and weekly utilisation from the OAuth usage endpoint tdgrind already polls.
        The token never touches argv; a failure returns None (never gates anything)."""
        import urllib.request

        c = self._creds(profile)
        if not c or not c.get("accessToken"):
            return None
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
        except Exception:  # noqa: BLE001
            return None


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


def parse_usage(d: dict) -> Usage | None:
    try:
        f, w = d["five_hour"], d["seven_day"]
        return Usage(
            five_hour_pct=int(f["utilization"]),
            weekly_pct=int(w["utilization"]),
            five_hour_resets=f.get("resets_at"),
            weekly_resets=w.get("resets_at"),
            fetched=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
    except (KeyError, TypeError, ValueError):
        return None
