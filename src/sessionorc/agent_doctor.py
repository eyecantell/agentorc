"""`ao doctor`'s host-side readings (design §4.7 **`ao doctor`**, TD-465): the `doctor` RPC gathers,
in one never-gated call, what the host agent alone can read — its tmux server against the one it
first read, the hooks (each profile's settings layers, each live agent session's `confidence` and
newest hook, the queue's unapplied lines), identity, each profile's credentials and usage, the
linked nodes and their builds, and whether `settings.yml` and `hosts.yml` parse. It reads and
judges nothing: the verdicts are the client's, beside `ao org check` and the build line it reads
itself. The one network call it can make is a usage request for an account whose reading is older
than the poll's, one per account, never into a cool-off."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from sessionorc import adapters, agent_common, containers, hosts, paths
from sessionorc import settings as settings_mod
from sessionorc.adapters import CommandAdapter, ShellAdapter
from sessionorc.agent_common import _parse

# the adapters with no hooks: their state is the screen's alone, so `ao doctor` reads no hook for them
_NO_HOOKS = frozenset({ShellAdapter.name, CommandAdapter.name})


def iso_z(t: datetime) -> str:
    return t.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _boot_time() -> float | None:
    """The boot instant in epoch seconds (`btime` of `/proc/stat`), which a process's start in
    clock ticks is counted from."""
    try:
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _started(ticks: int) -> str | None:
    """A process's start, from its clock ticks since boot, as an instant; None when it cannot be told."""
    boot = _boot_time()
    if boot is None or not ticks:
        return None
    return iso_z(datetime.fromtimestamp(boot + ticks / os.sysconf("SC_CLK_TCK"), UTC))


def _parse_error(path: Path) -> str | None:
    """Why a YAML file does not parse, in the reader's own words; None when it parses to a mapping,
    is empty, or is absent (an absent file is a home with no such settings)."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as e:
        return str(e)
    except yaml.YAMLError as e:
        return " ".join(str(e).split())
    return None if doc is None or isinstance(doc, dict) else f"not a mapping: a {type(doc).__name__}"


class DoctorMixin:
    async def rpc_doctor(self) -> dict[str, Any]:
        """`ao doctor`'s host-side readings (design §4.7 **`ao doctor`**, TD-465): `{host, tmux,
        hooks, identity, profiles, nodes, files}`, raw, for the client to judge. A never-gated read
        (§4.8a): it tells a session nothing it could not learn by trying, and writes nothing."""
        profiles = await asyncio.to_thread(self._doctor_profile_rows)
        return {
            "host": self.host,
            "tmux": await self._doctor_tmux(),
            "hooks": self._doctor_hooks(profiles),
            "identity": await self.rpc_identity(),
            "profiles": await self._doctor_usage(profiles),
            "nodes": self._doctor_nodes(),
            "files": {
                str(p.name): {"path": str(p), "error": _parse_error(p)}
                for p in (settings_mod.settings_file(), hosts.hosts_file())
            },
        }

    async def _doctor_tmux(self) -> dict[str, Any]:
        """The server now — pid, start, cgroup — and the one the agent first read, so a server
        replaced under the agent (kmaster's, 2026-09-20) reads as `replaced`."""
        pid = await asyncio.to_thread(self.tmux.server_pid)
        now = self._id_server(pid)
        first = self._id_tmux_first
        return {
            "pid": pid,
            "started": _started(now[1]) if now else None,
            "cgroup": self.proc.cgroup(pid) if pid else None,
            "first": {"pid": first[0], "started": _started(first[1])} if first else None,
            "replaced": bool(first and now and first != now),
        }

    def _doctor_profile_rows(self) -> list[dict[str, Any]]:
        """Each adapter's own reading of its profiles (`doctor_profiles`, §4.3's optional method):
        the core cannot read a profile itself."""
        rows: list[dict[str, Any]] = []
        for name in adapters.names():
            fn = getattr(adapters.get(name), "doctor_profiles", None)
            if fn is None:
                continue
            try:
                got = fn()
            except Exception as e:  # an adapter's fault is a reading, never the RPC's failure
                got = [{"error": f"{name}: {e}"}]
            rows.extend({"adapter": name, **r} for r in got)
        return rows

    def _doctor_hooks(self, profiles: list[dict[str, Any]]) -> dict[str, Any]:
        """Each live agent session of this host — its `confidence` and its newest hook event — and
        the queue: lines a hook wrote that the tick has not applied, and the oldest one's age."""
        sessions = []
        for s in sorted(self.sessions.values(), key=lambda r: r.id):
            if (s.host or self.host) != self.host or s.state in ("exited", "closed", "scheduled"):
                continue
            if s.adapter in _NO_HOOKS:
                continue  # a shell has no hooks to fire
            last = self._last_hook.get(s.id)
            sessions.append(
                {
                    "id": s.id,
                    "confidence": s.confidence,
                    "state": s.state,
                    "since": s.since,
                    "last_hook": iso_z(last) if last else None,
                }
            )
        queued, oldest = 0, None
        for p in paths.events_dir().glob("*.jsonl"):
            try:
                queued += sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
                oldest = min(oldest or p.stat().st_mtime, p.stat().st_mtime)
            except OSError:
                continue
        return {
            "layers": [
                {"profile": r.get("profile"), "layers": r.get("layers") or []} for r in profiles if "profile" in r
            ],
            "sessions": sessions,
            "queue": {"lines": queued, "age": round(time.time() - oldest, 1) if oldest else None},
        }

    async def _doctor_usage(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each profile's row with its account's usage beside it: the host agent's last reading when
        it is younger than the poll's period, else one request per account — and none into a
        rate limit's cool-off, which keeps the last reading and says so. A metered profile is never
        polled (§4.2a)."""
        from sessionorc.agent_tick import _usage_key  # the tick's own keying, one poll per account

        asked: dict[str, dict[str, Any]] = {}
        out: list[dict[str, Any]] = []
        mono = time.monotonic()
        for row in profiles:
            row = dict(row)
            out.append(row)
            if "profile" not in row or row.get("metered"):
                continue
            ad = adapters.get(str(row["adapter"]))
            fn = getattr(ad, "usage_for", None)
            if fn is None:
                continue
            key, _account = _usage_key(ad, str(row["adapter"]), str(row["profile"]))
            if key not in asked:
                reading = dict(self._usage_acct.get(key) or self._usage.get(str(row["profile"])) or {})
                wait = self._usage_wait.get(key, agent_common.USAGE_FRESH)
                cooling = key in self._usage_wait and mono - self._usage_checked.get(key, -wait) < wait
                if self._doctor_young(reading):
                    asked[key] = {**reading, "source": "reading"}
                elif cooling:
                    asked[key] = {
                        **reading,
                        "source": "reading",
                        "cooling": round(wait - (mono - self._usage_checked[key])),
                    }
                else:
                    try:
                        got = await asyncio.to_thread(fn, str(row["profile"]))
                    except Exception as e:
                        got = {"reason": "error", "error": str(e)}
                    asked[key] = {**(got or {"reason": "error"}), "source": "asked"}
            row["usage"] = asked[key]
        return out

    @staticmethod
    def _doctor_young(reading: dict[str, Any]) -> bool:
        try:
            age = (datetime.now(UTC) - _parse(str(reading.get("fetched")))).total_seconds()
        except (ValueError, TypeError):
            return False
        return 0 <= age < agent_common.USAGE_FRESH and bool(reading.get("windows"))

    def _doctor_nodes(self) -> list[dict[str, Any]]:
        """Each node `hosts.yml` names: its link as the home holds it (`host.links`), and for a
        container node the build its agent was started on against the wheel the home would
        provision now — `ao host status`'s build reading, with no docker call."""
        out: list[dict[str, Any]] = []
        boxes = containers.container_nodes()
        home = containers.home_build() if boxes else ""
        for name in sorted(hosts.nodes()):
            link = dict(self.links.get(name) or {})
            row: dict[str, Any] = {"node": name, "link": link or None, "container": name in boxes}
            if name in boxes:
                row["build"] = {
                    "running": str(link.get("build") or containers.running_build(boxes[name])),
                    "home": home,
                }
            out.append(row)
        return out
