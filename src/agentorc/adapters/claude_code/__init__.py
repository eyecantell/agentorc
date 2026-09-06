"""Claude Code adapter (design §4.2, §4.3): hook-fed state, transcript locator, usage, credentials.

Hooks reach a launched session through `claude --settings <hooks.json>`, a per-launch settings
layer, so nothing in the person's own `settings.json` is edited and hand-started sessions are
untouched. The hook command is `agentorc-hook`, which finds its session from `AGENTORC_SESSION`
in the tmux session's environment.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentorc import profiles as profiles_mod
from agentorc.profiles import Profile
from sessionorc import paths
from sessionorc.adapters import LaunchSpec
from sessionorc.models import Confidence, State
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
)
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


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
    return profile.config_dir or Path("~/.claude").expanduser()


def global_config_file(profile: Profile) -> Path:
    """`.claude.json`: sign-in, MCP servers, per-project state such as trust decisions."""
    return (profile.config_dir / ".claude.json") if profile.config_dir else Path("~/.claude.json").expanduser()


def pretrust(cwd: Path, profile: Profile) -> bool:
    """First-run quirk: mark `cwd` as trusted so the "trust this folder?" dialog never blocks a
    launched session (no hook reports it). Read-modify-write of the tool's own file, atomic
    replace, skipped when the flag is already set. Returns True when it wrote."""
    path = global_config_file(profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return False
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(cwd), {})
    if entry.get("hasTrustDialogAccepted") is True:
        return False
    entry["hasTrustDialogAccepted"] = True
    tmp = path.with_suffix(".json.agentorc-tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        return False
    return True


def hooks_file(profile: Profile) -> Path:
    return paths.home() / "claude-hooks" / f"{profile.name}.json"


def hooks_settings(profile: Profile, hook_cmd: str = "agentorc-hook") -> dict:
    """The settings layer passed with `--settings`. Only hooks; the profile's own settings still apply."""
    hooks: dict[str, list] = {}
    for ev in HOOK_EVENTS:
        # PermissionRequest may block for the whole permission wait; the others must be instant.
        timeout = profile.permission_wait + 15 if ev == "PermissionRequest" else 10
        hooks[ev] = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": timeout}]}]
    return {"hooks": hooks}


def write_hooks_file(profile: Profile) -> Path:
    p = hooks_file(profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    cmd = shutil.which("agentorc-hook") or "agentorc-hook"
    p.write_text(json.dumps(hooks_settings(profile, cmd), indent=1), encoding="utf-8")
    return p


class ClaudeCodeAdapter:
    name = "claude-code"
    state_source: Confidence = "hook"

    def __init__(self, binary: str | None = None):
        self.binary = binary or "claude"

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
        argv = [self.binary, "--settings", str(write_hooks_file(prof))]
        argv += ["--resume", resume] if resume else ["--session-id", adapter_id]
        if name:
            argv += ["--name", name]
        if prof.model:
            argv += ["--model", prof.model]
        argv += prof.extra_args
        if unattended:
            argv += prof.unattended_args or ["--dangerously-skip-permissions"]
        if prompt:
            argv.append(prompt)
        env = {"AGENTORC_PERMISSION_WAIT": str(prof.permission_wait), "AGENTORC_PROFILE": prof.name}
        if prof.config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(prof.config_dir)
        return LaunchSpec(argv=argv, env=env, adapter_id=adapter_id)

    def classify(self, pane: PaneInfo | None, tail: list[str]) -> State | None:
        return None  # hook-fed; the agent's liveness cross-check is the only scraped verdict

    # -- locators --------------------------------------------------------------------------------

    def transcript_path(self, session_id: str, cwd: Path, profile: Profile | None = None) -> Path | None:
        base = config_dir(profile or profiles_mod.get(None)) / "projects" / munge(cwd)
        p = base / f"{session_id}.jsonl"
        return p if p.is_file() else None

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
        exp = c.get("refreshTokenExpiresAt") or c.get("expiresAt")
        if not exp:
            return True
        return datetime.fromtimestamp(int(exp) / 1000, UTC) > datetime.now(UTC)

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
