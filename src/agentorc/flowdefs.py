"""Flows: the path an entry takes through a team (design §4.9c, TD-307; built by TD-309).

A flow is a directory holding `flow.yml` — an ordered list of stages, each a role, the lane it gives
and its stage brief — and the briefs beside it. This module finds a flow by name (the package's
built-ins, then the org's `~/.agentorc/flows/<name>/`, then a repo's `.agentorc/flows/<name>/`), reads it,
and says whether it is **usable** (whole: every brief present, every role resolved and of the kind
its stage wants) and whether a team can **follow** it (its members staff every stage). It reads and
judges; it writes nothing, and nothing here starts a session.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentorc import repoconfig
from sessionorc import paths
from sessionorc.models import LANE_WORDS, owner_word

PACKAGE_DIR = Path(__file__).resolve().parent / "flows"
REPO_DIR = Path(".agentorc") / "flows"
FILE = "flow.yml"
BUILTIN = ("td", "build-review", "build")
ORG_SUB = "flows"  # the org's flow directories: `~/.agentorc/flows/<name>/` (§4.9c, TD-313)
FLOW_KEYS = ("stages",)
STAGE_KEYS = ("name", "role", "lane", "brief")
PACKAGE_REF = "package:"
# Today one seat holds a review stage: the team's `techlead:` seat (§4.9b *The reader* knows two
# readers); a review stage of any other seat role is TD-314's design.
REVIEW_ROLE = "techlead"
REF_RE = re.compile(r"(?i)[a-z]{2,6}-\d{1,4}|#?\d{1,6}")


@dataclass
class Stage:
    name: str
    role: str
    lane: list[str] = field(default_factory=list)  # empty: a review stage
    brief: str = ""  # the reference as written
    path: Path | None = None  # the brief's file, resolved; None when the reference cannot be

    @property
    def review(self) -> bool:
        return not self.lane


@dataclass
class Flow:
    name: str
    place: str  # `package` | `repo`
    dir: Path
    stages: list[Stage] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # empty: usable (once `check` has run)

    @property
    def usable(self) -> bool:
        return not self.problems

    def stage_of(self, role: str) -> Stage | None:
        return next((s for s in self.stages if s.role == role), None)

    @property
    def review_stage(self) -> Stage | None:
        return next((s for s in self.stages if s.review), None)


def _read_here(path: Path) -> str | None:
    """A file's text, or None for anything that is not a readable text file (missing, a directory,
    not UTF-8): a flow's brief that cannot be read is a reason it is not usable, never a crash."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _brief_path(ref: str, flow_dir: Path, place_root: Path) -> tuple[Path | None, str]:
    """A stage's `brief:` as a file: relative to the flow's directory; `../<flow>/<file>` a sibling
    flow's **in the same place**; `package:<flow>/<file>` a built-in's from anywhere. Anything that
    leaves its place — an absolute path, a `..` that climbs out — is refused."""
    if ref.startswith(PACKAGE_REF):
        rest = Path(ref[len(PACKAGE_REF) :])
        parts = rest.parts
        if len(parts) != 2 or ".." in parts or rest.is_absolute():
            return None, f"brief {ref!r}: `package:` takes <flow>/<file>"
        return PACKAGE_DIR / rest, ""
    rel = Path(ref)
    if not rel.parts or rel.parts[-1] in (".", ".."):
        return None, f"brief {ref!r}: name a file"
    if rel.is_absolute():
        return None, f"brief {ref!r}: relative to the flow's directory, never absolute"
    parts = rel.parts
    if parts[:1] == ("..",):
        if len(parts) != 3 or ".." in parts[1:]:
            return None, f"brief {ref!r}: `../` reaches a sibling flow's <flow>/<file>, nothing further"
        return place_root / parts[1] / parts[2], ""
    if ".." in parts:
        return None, f"brief {ref!r}: a brief stays in its flow's directory, `../<flow>/` or `package:`"
    return flow_dir / rel, ""


def parse(name: str, place: str, flow_dir: Path, text: str | None, place_root: Path) -> Flow:
    """A flow from its `flow.yml` text, every structural problem collected rather than raised: a
    flow that fails is *not usable*, named with every reason (§4.9c)."""
    flow = Flow(name=name, place=place, dir=flow_dir)
    where = f"flow {name} ({flow_dir / FILE})"
    if text is None:
        flow.problems.append(f"{where}: no {FILE}")
        return flow
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        flow.problems.append(f"{where}: not valid YAML ({e})")
        return flow
    if not isinstance(data, dict):
        flow.problems.append(f"{where}: the top level must be a mapping with `stages:`")
        return flow
    for k in data:
        if k not in FLOW_KEYS:
            flow.problems.append(f"{where}: {k!r} is not a flow key ({', '.join(FLOW_KEYS)})")
    raw = data.get("stages")
    if not isinstance(raw, list) or not raw:
        flow.problems.append(f"{where}: `stages:` must be a non-empty list")
        return flow
    for i, st in enumerate(raw):
        key = f"{where}: stages[{i}]"
        if not isinstance(st, dict):
            flow.problems.append(f"{key} must be a mapping ({', '.join(STAGE_KEYS)})")
            continue
        for k in st:
            if k not in STAGE_KEYS:
                flow.problems.append(f"{key}.{k} is not a stage key ({', '.join(STAGE_KEYS)})")
        sname, role, brief = (str(st.get(k) or "").strip() for k in ("name", "role", "brief"))
        lane = st.get("lane")
        if lane is None:
            lane = []
        if not isinstance(lane, list) or not all(isinstance(w, str) and w.strip() for w in lane):
            flow.problems.append(f"{key}.lane must be a list of lane words")
            lane = []
        for k, v in (("name", sname), ("role", role), ("brief", brief)):
            if not v:
                flow.problems.append(f"{key}.{k} is required")
        stage = Stage(name=sname or f"#{i}", role=role, lane=[w.strip() for w in lane], brief=brief)
        if brief:
            stage.path, why = _brief_path(brief, flow_dir, place_root)
            if why:
                flow.problems.append(f"{key}: {why}")
        flow.stages.append(stage)
    names = [s.name for s in flow.stages]
    for n in dict.fromkeys(names):
        if names.count(n) > 1:
            flow.problems.append(f"{where}: two stages are called {n!r} — a stage's name is unique in its flow")
    roles = [s.role for s in flow.stages if s.role]
    for r in dict.fromkeys(roles):
        if roles.count(r) > 1:
            flow.problems.append(f"{where}: two stages name the role {r!r} — a member would have two lanes")
    return flow


def org_dir() -> Path:
    """The org's flow directories (design §4.9c *Where flows and roles live*, TD-313): the home's
    `flows/`, every team's to use and kept in the home's history beside `org.yml`."""
    return paths.home() / ORG_SUB


def find(name: str, repo_root: Path | None = None, *, read: repoconfig.Reader | None = None) -> Flow | None:
    """The flow called `name` for a team of the repo at `repo_root`, resolved as a team's name is —
    the org's over a repo's (§4.9c): a built-in (`td`, `build-review`, `build`, the package's), else
    the org's `~/.agentorc/flows/<name>/`, read on this host, else the repo's
    `.agentorc/flows/<name>/` read through `read` (this host's disk by default, a node's checkout
    across the link). None: no such flow. A directory that takes a built-in's name is never read —
    the built-in is the flow — and a repo's that takes an org flow's name is shadowed by it."""
    if name in BUILTIN:
        d = PACKAGE_DIR / name
        return parse(name, "package", d, _read_here(d / FILE), PACKAGE_DIR)
    if not name or "/" in name or name.startswith("."):
        return None
    org = org_dir()
    if (org / name / FILE).is_file():
        return parse(name, "org", org / name, _read_here(org / name / FILE), org)
    if repo_root is None:
        return None
    base = Path(repo_root).expanduser() / REPO_DIR
    text = (read or _read_here)(base / name / FILE)
    if text is None:
        return None
    return parse(name, "repo", base / name, text, base)


def _lane_ok(word: str, role: repoconfig.Role) -> bool:
    """A lane word §6 rule 6 knows (`free-pick`, `design-first`, `owner:<word>`, a ledger id or a
    PR number), or one its role's lane shape takes — a hunter-shaped role (its default lane `free`)
    takes `free` and any area (§4.8's table)."""
    if word in LANE_WORDS or owner_word(word) is not None:
        return True
    if REF_RE.fullmatch(word.strip()):  # a named item: a ledger id or a PR number (`normalize_ref`'s shapes)
        return True
    return "free" in role.lane


def check(
    flow: Flow,
    cfg: repoconfig.RepoConfig,
    overlay: dict[str, dict[str, Any]] | None = None,
    *,
    read: repoconfig.Reader | None = None,
) -> Flow:
    """Fill `flow.problems` with what keeps it from being **usable** (§4.9c *A flow is usable only
    when it is whole*): a brief that is not a file, a role that does not resolve here or is not of
    the kind its stage wants — a `worker` where the stage gives a lane, the `techlead` seat where it
    gives none — a lane word nothing knows. `cfg` is the team's repo (a repo flow's roles are that
    repo's, the package's and the org's). Returns `flow`."""
    where = f"flow {flow.name}"
    for st in flow.stages:
        key = f"{where}: stage {st.name}"
        if st.path is not None:
            # the package's and the org's files are this host's own; a repo's is read where its checkout is
            here = flow.place in ("package", "org") or st.path.is_relative_to(PACKAGE_DIR)
            reader = _read_here if here else read
            try:
                text = (reader or _read_here)(st.path)
            except (OSError, UnicodeDecodeError) as e:  # a node's checkout across the link that did not answer
                flow.problems.append(f"{key}: its brief {st.brief!r} could not be read ({e})")
            else:
                if text is None:
                    flow.problems.append(f"{key}: its brief {st.brief!r} is not a file ({st.path})")
        if not st.role:
            continue
        try:
            role = repoconfig.resolve_role(cfg, st.role, overlay)
        except (KeyError, ValueError) as e:
            flow.problems.append(f"{key}: {str(e).strip(chr(34))}")
            continue
        if st.review:
            if role.kind != "seat":
                flow.problems.append(f"{key}: a review stage names a seat, and {st.role!r} is a {role.kind}")
            elif st.role != REVIEW_ROLE:
                flow.problems.append(
                    f"{key}: today only the `techlead` seat holds a review stage — another seat's is TD-314's design"
                )
        else:
            if role.kind != "worker":
                flow.problems.append(
                    f"{key}: a stage that gives a lane names a worker, and {st.role!r} is a {role.kind}"
                )
            for w in st.lane:
                if not _lane_ok(w, role):
                    flow.problems.append(
                        f"{key}: lane word {w!r} is not one §6 rule 6 knows, nor {st.role}'s lane shape"
                    )
    return flow


def load(
    name: str,
    cfg: repoconfig.RepoConfig,
    overlay: dict[str, dict[str, Any]] | None = None,
    *,
    read: repoconfig.Reader | None = None,
) -> Flow | None:
    """`find` then `check`: the flow as a team of `cfg`'s repo would run it, or None when there is
    no flow of that name."""
    flow = find(name, cfg.root, read=read)
    return check(flow, cfg, overlay, read=read) if flow is not None else None


def visible(roots: Collection[Path | str], overlay: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every flow the org can see on this host (§4.7 `ao org`, §4.9c *What is shown*): the package's
    built-ins, then the org's `~/.agentorc/flows/<name>/`, then each registered checkout's
    `.agentorc/flows/<name>/`, each `{name, source, usable, problems}` — `source` `package` or the
    flow's directory. A directory that takes a built-in's name, and a repo's that takes an org flow's,
    is listed with `shadowed` set, as `find` never reads it; a checkout whose `.agentorc.yml` cannot
    be read is skipped (`with_repos` names it). An org flow is judged against the package's roles."""
    out: list[dict[str, Any]] = []
    package_cfg = repoconfig.load_text(None, PACKAGE_DIR)
    for name in BUILTIN:
        flow = find(name)
        if flow is not None:
            check(flow, package_cfg, overlay)
            out.append({"name": name, "source": "package", "usable": flow.usable, "problems": list(flow.problems)})
    org = org_dir()
    try:
        org_names = (
            sorted(p.name for p in org.iterdir() if not p.name.startswith(".") and (p / FILE).is_file())
            if org.is_dir()
            else []
        )
    except OSError:
        org_names = []
    for name in org_names:
        row = {"name": name, "source": str(org / name), "usable": False, "problems": []}
        if name in BUILTIN:
            row["shadowed"] = f"not read: {name} is a built-in's name, and the built-in is the flow"
        elif (flow := load(name, package_cfg, overlay)) is not None:
            row.update(usable=flow.usable, problems=list(flow.problems))
        out.append(row)
    for root in roots:
        where = Path(root).expanduser()
        base = where / REPO_DIR
        if not base.is_dir():
            continue
        try:
            cfg = repoconfig.load(where)
        except (OSError, ValueError):
            continue
        try:
            dirs = sorted(p for p in base.iterdir() if (p / FILE).is_file())
        except OSError:  # unreadable, or gone since `is_dir`: nothing of it can be listed
            continue
        for d in dirs:
            row: dict[str, Any] = {"name": d.name, "source": str(d), "usable": False, "problems": []}
            if d.name in BUILTIN:
                row["shadowed"] = f"not read: {d.name} is a built-in's name, and the built-in is the flow"
            elif d.name in org_names:
                row["shadowed"] = f"not read: the org's flow {d.name} ({org / d.name}) is the flow"
            elif (flow := load(d.name, cfg, overlay)) is None:  # `_read_here` reads an unreadable file as none
                row["problems"] = [f"flow {d.name}: its {FILE} could not be read ({d / FILE})"]
            else:
                row.update(usable=flow.usable, problems=list(flow.problems))
            out.append(row)
    return out


def unfollowable(
    flow: Flow,
    staffed: Collection[str],
    *,
    techlead: bool,
    held: Collection[str],
    team: str = "",
    node: str = "",
) -> list[str]:
    """Why a team cannot follow `flow` (§4.9c *Every listed flow must be followable*), in the shared
    words' middle part, or [] when it can: an org flow, or a stage naming an org role, for a team
    that runs on the node `node` (its briefs are read there, and the org's directories are the
    home's, §4.4a); a member stage whose role
    the team starts no member of; the review stage when the team has no `techlead:` seat, or when
    nothing would be held (its repos write no `held:`). `staffed` is the roles the definition's
    members take."""
    out: list[str] = []
    if node and flow.place == "org":
        out.append(f"an org flow, and {team} runs on {node} — define it in the repo")
    if node:  # the org's role directories are the home's too (§4.9c): *security is an org role*
        out += [f"{st.role} is an org role" for st in flow.stages if st.role and repoconfig.org_role(st.role)]
    for st in flow.stages:
        if st.review:
            if not techlead:
                out.append("no techlead seat — add `techlead:` to the team")
            elif not held:
                out.append("nothing held — write held:")
        elif st.role not in staffed:
            out.append(f"no {st.role} — add one to members:")
    return out


def cannot_follow(flow: str, team: str, reasons: Collection[str]) -> str:
    """The shared words (§4.9c): *build-review cannot be followed by dc-grind: nothing held — write
    held:, or drop build-review from flows:*."""
    return f"{flow} cannot be followed by {team}: {'; '.join(reasons)}, or drop {flow} from flows:"


def strip(flow: Flow, *, techlead: str = "") -> str:
    """The team card's **flow strip** and `ao team flow`'s (§4.9c *What is shown*): the stages in order
    and the person last — *design → build → review → you, through techlead-ao-1*."""
    route = f"through {techlead}" if techlead else "directly"
    return " → ".join([st.name for st in flow.stages] + [f"you, {route}"])


def path_line(flow: Flow, role: str | None, *, techlead: str = "", held: Collection[str] = ()) -> str:
    """The `{flow}` text a member of `role` is told (§4.9c item 5): the stages in order by role, its
    own stage marked, the review stage by its seat's id and the paths held, and the person last with
    how the team's questions get there — *td: design (designer) → **build** (grinder) → review
    (techlead-ao-1, on src/sessionorc/**) → you, through techlead-ao-1.* No member's id is in it, so a
    member added or removed changes no sibling's brief. A role with no stage reads *you stand
    outside it*."""
    parts = []
    for st in flow.stages:
        who = st.role
        if st.review:
            who = ", on ".join(x for x in (techlead or st.role, ", ".join(held)) if x)
        word = f"**{st.name}**" if st.role == role else st.name
        parts.append(f"{word} ({who})")
    route = f"through {techlead}" if techlead else "directly"
    text = f"{flow.name}: {' → '.join(parts)} → you, {route}."
    if role is None or flow.stage_of(role) is None:
        text += " You stand outside it."
    return text


def under(flow: Flow, role: str | None, *, techlead: str = "", held: Collection[str] = ()) -> repoconfig.UnderFlow:
    """What `Role.compose` fills a member of `role` started under `flow` with: the `{flow}` line and
    its stage's brief for `{stage}` — none for a role the flow gives no stage."""
    stage = flow.stage_of(role) if role else None
    return repoconfig.UnderFlow(
        text=path_line(flow, role, techlead=techlead, held=held), stage=stage.path if stage else None
    )
