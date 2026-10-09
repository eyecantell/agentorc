"""`.agentorc.yml`: one repo's checked-in configuration (design §5), and the role presets over it (§4.8).

```yaml
unattended: {workers: 3, ...}         # kept as a block; the loader only knows it is present
roles:                                # §4.8 presets; every key optional, built-ins apply otherwise
  grinder: {brief: docs/briefs/grinder.md, lane: free-pick, profile: grind, icon: wrench}
  manager: {grants: [control], controllers: []}
controllers: [manager-ao-1]           # who may act on a session started here; omitted = nobody
ledger: docs/technical_debt.md
teams: {...}                          # §4.9; passed through for the team step
ready_when: [tree_clean, branch_pushed, pr_merged, no_subagents, ledger_touched]
commands: [{name: test, run: pdm run test}]
```

`unattended`, `ready_when` and `commands` are accepted and read by nothing yet: each waits on the
feature that reads it (§5). `adapter`, `worktrees` and `anchor` are refused, naming where each is
decided instead (`RETIRED`, TD-149).

Missing file: the defaults §5 lists. A malformed file: a `ValueError` naming the key. Read on every
use and cached nowhere (the profiles rule), by the clients only — the host agent never reads it, so
`sessionorc` stays free of it. Roles resolve lowest-first: the package's built-ins (`PRESETS`), an
org-level overlay (`roles_overlay`, which the callers fill from `org.yml` — this module never
reads that file itself), then the
repo's own `roles:`, each overriding per key.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from sessionorc import paths
from sessionorc.models import GRANTS, normalize_context, normalize_review, owner_word

FILE = ".agentorc.yml"
# Keys the file once carried and nothing ever read (TD-149 (1)): each is decided elsewhere, so a
# repo writing one is told where rather than left believing it took effect.
RETIRED = {
    "adapter": "the adapter is chosen per session (`ao new --adapter`, the New session form) and per profile",
    "worktrees": "a session's worktree is always `<repo>/.claude/worktrees/<name>` (sessionorc.gitinfo)",
    "anchor": "one agent session per directory is invariant 2 (design §9), not a per-repo setting",
}
DEFAULT_LEDGER = "docs/technical_debt.md"
DEFAULT_READY_WHEN = ("tree_clean", "branch_pushed", "no_subagents")
ROLE_KEYS = (
    "brief", "lane", "grants", "profile", "controllers", "icon", "label", "review", "context", "message", "prompts",
    "kind",
)  # fmt: skip
# A role's **kind** (design §4.9c *Roles and flows are orthogonal*, TD-309): all a flow's stage, and a
# team's slot, asks of a role. A definition's key — a preset's — and refused in an overlay
# (`org.yml`'s and `.agentorc.yml`'s `roles:`), so no install or repo changes what a flow may ask of
# a role it did not define. A role that writes none (a key-only role) is a `worker`.
KINDS = ("worker", "seat", "manager", "plain")
DEFAULT_KIND = "worker"
# The built-in worker presets' context bound (design §4.8 *A role has a context bound*, *The bound
# has two layers*; TD-190, TD-249): the cost break-even TD-189's research found, since a bound under
# start-up plus one entry restarts after every entry. A repo's own number goes in its `.agentorc.yml`.
WORKER_CONTEXT = {"bound": "300k"}
# And the bound of every role that sets no `context:` (Paul, 2026-09-30): `manager`, `plain` and a
# role an org defines are bounded unless their definition says `context: none`. A seat's record
# takes no default (`teams`): it is short, and on call.
DEFAULT_CONTEXT = WORKER_CONTEXT
# A role's icon (design §4.8 *Role presets*, 2026-09-19, TD-074): one name from the fixed set the UI
# ships, never markup from a config file. Drawn small and monochrome inside the role badge — a
# label's picture and nothing more. An unknown name is refused when the file is read, as an unknown
# grant is; a role with no icon draws nothing.
ICONS = ("flag", "wrench", "search", "eye", "book", "shield", "terminal")
# `person` is the card's *interactive* mark — the person's own sessions (§4.5 *The card's anatomy*,
# TD-095) — so no role may wear it: a card never shows one glyph for two reasons.
RESERVED_ICONS = {"person": "reserved for the card's mark of an interactive session, the person's own (design §4.5)"}
# A role's display label (design §4.8 *The names*, TD-076): what the role badge, a team header and
# an Inbox row show in place of the bare key. A person's text, drawn escaped; nothing keys on it.
LABEL_CAP = 40
# When to message a role (design §4.8 *A role says when to message it*, TD-162, built by TD-171): one
# sentence, drawn where a person chooses whom to write to. The definition's line, never the session's.
MESSAGE_CAP = 120
# A role's saved prompts (design §4.8 *A role has saved prompts*, TD-161, built by TD-170): the chips a
# person presses in place of typing — `{label, text}`, the label one line of at most this many.
PROMPT_LABEL_CAP = 24
LANE_PLACEHOLDER = "{lane}"


def _no_owner(lane: list[str]) -> list[str]:
    """A lane as the brief's slot prints it: its `owner:<word>`s left out (TD-227)."""
    return [w for w in lane if owner_word(w) is None]


# The team's techlead seat (design §4.9b): its session id, filled at launch as `{lane}` is; `none`
# where the team has none, or the session was started by hand, so a brief reads right either way.
TECHLEAD_PLACEHOLDER = "{techlead}"
NO_TECHLEAD = "none"
# The team's manager (TD-113 (a)): its session id, filled at launch as `{techlead}` is; `none` where
# a person leads the team or the session was started by hand, so a member reports to no one it made up.
MANAGER_PLACEHOLDER = "{manager}"
NO_MANAGER = "none"
# The seat's primer (design §4.9b *Its standing context*): the `context:` path, relative to the home
# checkout, filled at launch; `none` without one, and the techlead brief then reads the repo's map.
CONTEXT_PLACEHOLDER = "{context}"
NO_CONTEXT = "none"
# A repo's brief (design §4.8 *A repo's brief is a supplement*, TD-114): filled into the template's
# *This repo's rules* section; `none` where the repo gives nothing, and the template stands alone.
REPO_PLACEHOLDER = "{repo}"
NO_REPO = "none"
# What a new ledger entry needs (design §4.9 *Add an entry to the ledger*, TD-219): written once, in
# the package's `entry.md`, and read both ways — a section of the techlead's brief, where `{entry}`
# takes it with its own slots said in words (the repo and type ride on the handed message), and the
# opening lines of the composer a person's entry session starts with, the slots filled from the form.
ENTRY_TEMPLATE = "entry.md"
ENTRY_PLACEHOLDER = "{entry}"
# A template's shape for a seat on call (design §6 *What is left is judgement, and a seat holds it*,
# TD-247): the manager's, which reads one `seat_due` and ends, where `manager.md` rounds on `ao wait`.
ON_CALL_BRIEFS = {"manager.md": "manager_on_call.md"}
# A team's path (design §4.9c *A brief has three layers*, TD-309): `{flow}` the generated path lines,
# `{stage}` the stage brief's text, handed as a file slot so an edit to it is *brief changed* (§6 rule
# 7). A session with no flow reads `none` in `{flow}` and its template's `<stem>.stage.md` — the path
# words a team with no flow is told, moved out of the template — in `{stage}`, `none` where the
# package ships none.
FLOW_PLACEHOLDER = "{flow}"
STAGE_PLACEHOLDER = "{stage}"
NO_FLOW = "none"
NO_STAGE = "none"
STAGE_SUFFIX = ".stage.md"
# A role defined outside the package (design §4.9c *Where flows and roles live*, TD-313): a directory
# holding `role.yml` (the `ROLE_KEYS`, `kind` among them, never `brief:`) and `template.md` (its
# mechanics, with the template slots) — the org's `~/.agentorc/roles/<name>/` over a repo's
# `.agentorc/roles/<name>/`, each whole, a peer of a preset. A manager's may ship
# `template_on_call.md`, its shape on call as `ON_CALL_BRIEFS` gives a preset's.
ROLE_DIR = Path(".agentorc") / "roles"
# A path set's name (§4.9c *A repo names its path sets*, TD-315): `held: {core: [...], ui: [...]}`
HELD_SET_RE = re.compile(r"[a-z0-9-]+")
ORG_ROLES_SUB = "roles"
ROLE_FILE = "role.yml"
ROLE_TEMPLATE = "template.md"
ROLE_TEMPLATE_ON_CALL = "template_on_call.md"
ENTRY_SLOTS = ("{repo}", "{type}", "{ledger}")
HANDED_ENTRY = {
    "{repo}": "the repo its `entry` names",
    "{type}": "the `type` its `entry` carries",
    "{ledger}": "that repo's ledger file (`.agentorc.yml`'s `ledger:`, `docs/technical_debt.md` by default)",
}


def entry_text(repo: str, type_: str, ledger: str) -> str:
    """`entry.md` with its three slots filled: the rules a drafter of a new ledger entry follows."""
    text = resources.files("agentorc").joinpath("briefs", ENTRY_TEMPLATE).read_text(encoding="utf-8")
    for slot, value in zip(ENTRY_SLOTS, (repo, type_, ledger), strict=True):
        text = text.replace(slot, value)
    return text.strip()


# The built-in presets (design §4.8's table): each a brief template shipped with the package
# (`agentorc/briefs/<role>.md`, `{lane}` filled at launch), a default lane shape, and its grants.
# None names a profile: profile names are the person's (§4.2a, §4.9).
PRESETS: dict[str, dict[str, Any]] = {
    "grinder": {
        "kind": "worker",
        "brief": "grinder.md",
        "lane": ["free-pick"],
        "grants": [],
        "icon": "wrench",
        "label": "Grinder",
        "context": WORKER_CONTEXT,
        "message": "its own card only: the entry it holds, a finding on its PR",
    },
    "hunter": {
        "kind": "worker",
        "brief": "hunter.md",
        "lane": ["free"],
        "grants": [],
        "icon": "search",
        "label": "Hunter",
        "context": WORKER_CONTEXT,
        "message": "an area to look at; it files, never fixes",
    },
    "manager": {
        "kind": "manager",
        "brief": "manager.md",
        "lane": [],
        "grants": ["control"],
        "icon": "flag",
        "label": "Manager",
        "message": "the team's work: what it picks, its pace, a member that is stuck or should stop",
    },
    # The go-between (design §4.9b, TD-075): answers teammates' questions from the record, passes the
    # rest up. No grants — it acts on no session, and identity alarms go to the person (§4.8a).
    "techlead": {
        "kind": "seat",
        "brief": "techlead.md",
        "lane": [],
        "grants": [],
        "icon": "book",
        "label": "Tech Lead",
        "message": "a PR on a held path, the architecture, or a question that is answered somewhere in the docs"
        " — an ask fills the seat",
    },
    # A seat with a trigger (design §4.9b, TD-098): hunter-shaped — checks one area every n merged
    # PRs or every so often, files what it finds, and ends. The area is its brief's, never a lane.
    "auditor": {
        "kind": "seat",
        "brief": "auditor.md",
        "lane": [],
        "grants": [],
        "icon": "eye",
        "label": "Auditor",
        "context": WORKER_CONTEXT,
        "message": "what its trigger counts: the last n PRs, the period",
    },
    # The anchor seat (design §4.9b *The anchor seat*, TD-381): the checkout's own work, from the lane
    # word `anchor`, in the home repo's main checkout. No icon: the design names none.
    "anchor": {
        "kind": "seat",
        "brief": "anchor.md",
        "lane": ["anchor"],
        "grants": [],
        "label": "Anchor",
        "context": WORKER_CONTEXT,
        "message": "the checkout's own work: an anchor-owned entry, a live check, a decided board line",
    },
    # The design stage's role (design §4.9c *The designer gets a template*, TD-309): no icon and no
    # label, so no designer card's badge changes at the build.
    "designer": {
        "kind": "worker",
        "brief": "designer.md",
        "lane": ["design-first", "owner:designer"],
        "grants": [],
        "message": "a design-first entry, a control's shape, a screen",
    },
    "plain": {"kind": "plain", "brief": None, "lane": [], "grants": [], "icon": None},
}
DEFAULT_ROLE = "plain"
# Reads one file by its absolute path and returns its text, or None when there is no such file;
# raises `OSError` when it exists and cannot be read. `_read_here` is this host's disk.
Reader = Callable[[Path], "str | None"]


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_LINE = re.compile(r"^\s*(```|~~~)")


def repeated_headings(text: str) -> list[str]:
    """The Markdown headings a filled brief carries twice (design §4.8, TD-114 *Transition*): a
    repo's brief that is still a whole brief repeats the template's own headings, and by the
    precedence rule its stale copy would win — so a start names them and goes ahead. A heuristic
    read of the text, never a guarantee against a re-worded duplicate; fenced code is skipped."""
    seen: dict[str, int] = {}
    fenced = False
    for line in text.split("\n"):
        if _FENCE_LINE.match(line):
            fenced = not fenced
            continue
        if not fenced and (m := _HEADING.match(line)):
            key = m.group(2).strip()
            seen[key] = seen.get(key, 0) + 1
    return [h for h, n in seen.items() if n > 1]


@dataclass(frozen=True)
class UnderFlow:
    """What a member started under a flow is told of it (design §4.9c item 5): `text`, the `{flow}`
    lines (`flowdefs.under` makes them), and `stage`, its stage brief's file — None where the flow
    gives its role no stage (a manager, a member outside the flows, a seat with no review stage)."""

    text: str
    stage: Path | None = None


def _stage_default(template: str) -> Path | None:
    """`<stem>.stage.md` beside the installed template, the `{stage}` of a session with no flow."""
    src = Path(str(resources.files("agentorc").joinpath("briefs", Path(template).stem + STAGE_SUFFIX)))
    return src if src.is_file() else None


def _stage_text(stage: Path, read: Reader | None) -> str:
    """A stage brief's text as `brief.fill` reads a file slot: stripped, `none` when empty. A file of
    the package is this host's own; a repo flow's is read through `read`, so a node member's is read
    on its checkout across the link (design §4.4a, TD-309) as `flowdefs.check` read it."""
    package = Path(str(resources.files("agentorc")))
    # `flowdefs.PACKAGE_DIR` is resolved and `_stage_default` is not: either spelling is the package's
    here = read is None or any(stage.is_relative_to(p) for p in (package, package.resolve()))
    try:
        text = (_read_here if here else read)(stage)
    except OSError as e:
        raise ValueError(f"stage brief {stage} cannot be read ({e.strerror or e})") from None
    if text is None:
        raise ValueError(f"stage brief {stage} cannot be read (no such file)")
    return text.strip() or NO_STAGE


def prefixed(prompt_from: dict[str, Any] | None, block: str) -> dict[str, Any] | None:
    """`prompt_from` with the text put in front of the brief (§4.9's Project block) as `prefix`,
    which a replay puts back in front as it is: the block is the org's reach at the start, not a
    brief's file (design §6 rule 7)."""
    return {**prompt_from, "prefix": block} if prompt_from and block else prompt_from


def _read_here(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


@dataclass
class Role:
    """A preset, resolved: what `ao new --role` and the New session form fill in from it."""

    name: str
    brief: str | None = None  # a package template name (built-in) or a repo-relative path (repo)
    lane: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    profile: str | None = None
    icon: str | None = None  # one name from `ICONS` (§4.8), or None: the badge draws no picture
    label: str | None = None  # the display label (§4.8 *The names*); None is the default, `display`
    message: str | None = None  # when to message it (§4.8, TD-171): one sentence, or None for none
    prompts: list[dict[str, str]] = field(default_factory=list)  # saved prompts (§4.8, TD-170), in file order
    controllers: list[str] = field(default_factory=list)
    review: dict[str, Any] | None = None  # who reads its PRs (design §4.9b *The reader*, TD-093)
    kind: str = DEFAULT_KIND  # §4.9c (TD-309): `worker`, `seat`, `manager` or `plain`, from its definition
    context_bound: int | None = None  # tokens past which §6 rule 5 tells it to end its run (§4.8, TD-190)
    context_default: bool = False  # no layer said `context:`: the bound is `DEFAULT_CONTEXT`'s (TD-249)
    controllers_set: bool = False  # a layer said `controllers:` — an empty list then means *nobody*,
    # deliberately, and the repo's default is not fallen back to (review of PR #116)
    sources: list[str] = field(default_factory=list)  # `built-in`, `org`, `repo`: which layers spoke
    brief_source: str = ""  # which layer the brief came from: the template is read from there
    root: Path | None = None  # the repo the role was resolved in; where a repo brief path is relative to
    defined: Path | None = None  # its directory, for a role defined in one (§4.9c, TD-313): `template.md` is there
    defined_place: str = ""  # `org` or `repo`: where `defined` is — the org's is this host's own disk

    @property
    def display(self) -> str:
        """What the page shows for this role: its `label:`, or the default — the role's name with
        its first letter raised (design §4.8 *The names*)."""
        return self.label or default_label(self.name)

    @property
    def source(self) -> str:
        """`built-in`, `repo`, `built-in + repo` …: for `ao roles` and the form's note."""
        return " + ".join(self.sources) or "built-in"

    @property
    def template(self) -> str | None:
        """The package template this role's brief is built on (design §4.8, TD-114): the built-in
        preset's of the same name, whatever layer spoke last — a repo's brief is a supplement to it,
        never a replacement. None for a role the package does not ship (the repo's brief is then the
        whole brief) and for `plain`."""
        return (PRESETS.get(self.name) or {}).get("brief")

    def _path(self, brief: str) -> Path:
        path = Path(brief).expanduser()
        return path if path.is_absolute() else (self.root or Path.cwd()) / path

    def _read(self, brief: str, read: Reader | None) -> str:
        path = self._path(brief)
        try:
            text = (read or _read_here)(path)
        except OSError as e:
            raise ValueError(f"role {self.name!r}: brief {path} cannot be read ({e.strerror or e})") from None
        if text is None:
            raise ValueError(f"role {self.name!r}: brief {path} cannot be read (no such file)")
        return text

    def brief_text(
        self,
        lane: list[str] | None = None,
        *,
        read: Reader | None = None,
        techlead: str | None = None,
        context: str | None = None,
        manager: str | None = None,
        supplement: str | None = None,
    ) -> str | None:
        """The opening prompt alone: `compose`'s text (below)."""
        return self.compose(
            lane, read=read, techlead=techlead, context=context, manager=manager, supplement=supplement
        )[0]

    def compose(
        self,
        lane: list[str] | None = None,
        *,
        read: Reader | None = None,
        techlead: str | None = None,
        context: str | None = None,
        manager: str | None = None,
        supplement: str | None = None,
        on_call: bool = False,
        flow: UnderFlow | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """The opening prompt this role gives a session: its template with `{repo}` filled from the
        repo's brief (design §4.8 *A repo's brief is a supplement*, TD-114) — `supplement`, a path,
        when a team definition or `ao new --brief` gives one, in place of the role's own
        `roles.<name>.brief`, never beside it — then `{lane}` from `lane` (default the role's own),
        `{techlead}` with the team's seat, `{manager}` with its manager and `{context}` with the
        seat's primer (each `none` without one), in the supplement's text as in the template's.
        A role the package ships no template for takes the repo's brief as the whole brief; None
        for one with neither (`plain`). `read` reads a repo's file — this host's disk by default, or
        another host's checkout across the link (design §4.4a "Teams across hosts", TD-057 step
        4b.3), a repo flow's stage brief with it; a template is always the package's own. `on_call`
        takes the template's seat shape where the package ships one (`ON_CALL_BRIEFS`: a manager on
        call, design §6 rule 3, TD-259).
        `flow` is what a member started under a team's current flow is told of it (§4.9c item 5):
        `{flow}` and `{stage}` from it; without one, `none` and the template's `<stem>.stage.md`. Every
        preset's template wraps it, flow or none — the designer's too, since TD-310 cut its one repo
        brief to a supplement (§4.9c *The designer gets a template*).

        Beside the text, what it was made from (design §6 *Keeping a team running* rule 7, TD-217):
        `prompt_from = {base, slots}` — `base` the template's path as installed, or the repo's brief
        for a role with no template; `slots` each placeholder, in the order it is filled, as
        `{file: <absolute path>}` (the repo's brief, whose text goes in stripped, `none` when empty) or
        `{text: …}` — so
        a replay can fill `base` again from the files as they are then. None with no text."""
        own = self.brief if self.brief and self.brief_source not in ("", "built-in") else None
        extra = supplement or own
        slots: dict[str, dict[str, str]] = {}
        template = ON_CALL_BRIEFS.get(self.template, self.template) if on_call and self.template else self.template
        default_stage: Path | None = None
        if self.defined is not None:
            src_path = self.defined / (ROLE_TEMPLATE_ON_CALL if on_call else ROLE_TEMPLATE)
            text, base = self._defined_template(src_path, read, on_call), str(src_path)
        elif template is None:
            if not extra:
                return None, None
            text = self._read(extra, read)
            base = str(self._path(extra))
        else:
            src = resources.files("agentorc").joinpath("briefs", template)
            text = src.read_text(encoding="utf-8")
            base = str(src)
            default_stage = _stage_default(template)
        if self.defined is not None or template is not None:
            added = self._read(extra, read).strip() if extra else ""
            text = text.replace(REPO_PLACEHOLDER, added or NO_REPO)
            slots[REPO_PLACEHOLDER] = {"file": str(self._path(extra))} if extra else {"text": NO_REPO}
            stage = flow.stage if flow is not None else default_stage
            text = text.replace(FLOW_PLACEHOLDER, flow.text if flow is not None else NO_FLOW)
            slots[FLOW_PLACEHOLDER] = {"text": flow.text if flow is not None else NO_FLOW}
            text = text.replace(STAGE_PLACEHOLDER, _stage_text(stage, read) if stage else NO_STAGE)
            slots[STAGE_PLACEHOLDER] = {"file": str(stage)} if stage else {"text": NO_STAGE}
        if self.template is not None and ENTRY_PLACEHOLDER in text:  # the techlead's template alone carries it
            handed = entry_text(*(HANDED_ENTRY[slot] for slot in ENTRY_SLOTS))
            text = text.replace(ENTRY_PLACEHOLDER, handed)
            slots[ENTRY_PLACEHOLDER] = {"text": handed}
        fills = (
            (TECHLEAD_PLACEHOLDER, techlead or NO_TECHLEAD),
            (MANAGER_PLACEHOLDER, manager or NO_MANAGER),
            (CONTEXT_PLACEHOLDER, context or NO_CONTEXT),
            # an owner word narrows what the home tells the member of (§6 rule 6), never its lane's words
            (LANE_PLACEHOLDER, ", ".join(_no_owner(lane if lane is not None else self.lane)) or "(none given)"),
        )
        for slot, value in fills:
            text = text.replace(slot, value)
            slots[slot] = {"text": value}
        return text, {"base": base, "slots": slots}

    def _defined_template(self, path: Path, read: Reader | None, on_call: bool) -> str:
        """A defined role's template (§4.9c): the org's read on this host, a repo's through `read`
        (a node's checkout across the link). A manager with no `template_on_call.md` is a standing
        manager only."""
        reader = _read_here if self.defined_place == "org" else (read or _read_here)
        try:
            text = reader(path)
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(
                f"role {self.name!r}: {path} cannot be read ({getattr(e, 'strerror', None) or e})"
            ) from None
        if text is None:
            if on_call:
                raise ValueError(
                    f"role {self.name!r}: no {ROLE_TEMPLATE_ON_CALL} in {self.defined} — without one it can be a "
                    "standing manager only (design §4.9c)"
                )
            raise ValueError(f"role {self.name!r}: no {ROLE_TEMPLATE} in {self.defined} (design §4.9c)")
        return text

    def bound_for(self, unattended: bool) -> int | None:
        """The bound a session started by hand in this role is given (design §4.8 *The bound has two
        layers*, TD-249): what the role's definition wrote, always; the default of a role that wrote
        none only for an unattended session — a person's own is never told it is over a bound nobody
        set. One rule for `ao new`, the New session form and Add entry's *Open a session*."""
        return None if self.context_default and not unattended else self.context_bound

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "brief": self.brief,
            "lane": list(self.lane),
            "grants": list(self.grants),
            "profile": self.profile,
            "icon": self.icon,
            "label": self.display,
            "message": self.message,
            "prompts": [dict(p) for p in self.prompts],
            "context_bound": self.context_bound,
            "context_default": self.context_default,
            "controllers": list(self.controllers),
            "source": self.source,
        }


@dataclass
class RepoConfig:
    root: Path | None = None  # the directory the file was looked for in
    path: Path | None = None  # the file itself, when it exists
    unattended: dict[str, Any] | None = None  # the whole block, or None: no unattended mode
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)  # the repo's own overrides, per key
    controllers: list[str] = field(default_factory=list)
    ledger: str = DEFAULT_LEDGER
    ready_when: list[str] = field(default_factory=lambda: list(DEFAULT_READY_WHEN))
    commands: list[dict[str, Any]] = field(default_factory=list)
    teams: dict[str, Any] = field(default_factory=dict)  # §4.9; read by the team step, passed through here
    # §5 `promote:` (§6 *Promote*, TD-120): `{run, check}`, accepted and checked now so writing the
    # designed block does not break `ao new` in that repo; `sessionorc.promote` runs it at the home (TD-132)
    promote: dict[str, str] | None = None
    # §4.9c (TD-309): the repo's held paths, top level — what a flow's review stage waits for. None:
    # the key is absent (a review stage then has nothing to hold); never an empty list
    held: list[str] | None = None
    # §4.9c (TD-315): `held:` written as a mapping of named sets, `{core: [...], ui: [...]}` — a review
    # stage's `held: <set>` names one; None when `held:` is a list (or absent). `held` is then their union
    held_sets: dict[str, list[str]] | None = None
    # how the repo's files were read (`load`'s `read`): its role directories are read the same way (§4.9c)
    read: Reader | None = field(default=None, repr=False, compare=False)


def load(repo_root: Path | str, *, read: Reader | None = None) -> RepoConfig:
    """`<repo>/.agentorc.yml`, or the defaults when there is none. `read` is how the file is read:
    this host's disk by default, another host's checkout across the link for a team started there
    (TD-057 step 4b.3) — one loader for both, `load_text`."""
    root = Path(repo_root).expanduser()
    cfg = load_text((read or _read_here)(root / FILE), root)
    cfg.read = read
    for name in cfg.roles:
        # an overlay is not a definition (design §4.9c): a key naming no preset and no directory is refused
        if name not in PRESETS and definition(cfg, name) is None:
            raise ValueError(f"{cfg.path}: `roles`.{name}: {_unknown(cfg, name)}")
    return cfg


def load_text(text: str | None, repo_root: Path | str) -> RepoConfig:
    """A repo's config from the text of its `.agentorc.yml` (None: the file is not there — the
    defaults), wherever that text was read."""
    root = Path(repo_root).expanduser()
    path = root / FILE
    cfg = RepoConfig(root=root)
    if text is None:
        return cfg
    cfg.path = path
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: not valid YAML ({e})") from None
    if data is None:
        return cfg
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a mapping of keys, not {type(data).__name__}")
    for key, value in data.items():
        _apply(cfg, str(key), value, path)
    return cfg


def discover(start: Path | str) -> RepoConfig:
    """The config for the repo `start` is in: the nearest `.agentorc.yml` walking up from `start`
    (a worktree carries the checked-in file, so a session's own directory finds it), stopping at
    the first `.git` — a directory session has no repo file and gets the defaults for its own dir."""
    here = Path(start).expanduser().resolve()
    for d in (here, *here.parents):
        if (d / FILE).is_file():
            return load(d)
        if (d / ".git").exists():
            return RepoConfig(root=d)
    return RepoConfig(root=here)


def _apply(cfg: RepoConfig, key: str, value: Any, path: Path) -> None:
    where = f"{path}: `{key}`"
    if key == "ledger":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where} must be a non-empty string")
        cfg.ledger = value.strip()
    elif key == "unattended":
        if value is None:
            cfg.unattended = None
        elif not isinstance(value, dict):
            raise ValueError(f"{where} must be a mapping (workers, brief, window, …)")
        else:
            cfg.unattended = dict(value)
    elif key == "roles":
        blocks = _mapping(value, where)
        cfg.roles = {name: _role_block(name, raw, where) for name, raw in blocks.items()}
    elif key == "controllers":
        cfg.controllers = _str_list(value, where)
    elif key == "ready_when":
        cfg.ready_when = _str_list(value, where)
    elif key == "commands":
        cfg.commands = _commands(value, where)
    elif key == "teams":
        cfg.teams = _mapping(value, where)
    elif key == "promote":
        cfg.promote = _promote(value, where)
    elif key == "held" and isinstance(value, dict):
        sets: dict[str, list[str]] = {}
        for name, globs in value.items():
            name = str(name)
            if not HELD_SET_RE.fullmatch(name):
                raise ValueError(f"{where}.{name}: a path set's name is a word of lower-case letters, digits and -")
            sets[name] = _str_list(globs, f"{where}.{name}")
            if not sets[name]:
                raise ValueError(f"{where}.{name} names no path — a set that holds nothing is no set (design §4.9c)")
        if not sets:
            raise ValueError(f"{where} names no path set — a review that holds nothing is no key (design §4.9c)")
        cfg.held_sets = sets
        cfg.held = list(dict.fromkeys(g for globs in sets.values() for g in globs))
    elif key == "held":
        held = _str_list(value, where)
        if not held:
            raise ValueError(f"{where} names no path — a review that holds nothing is no key (design §4.9c)")
        cfg.held = held
    elif key in RETIRED:
        raise ValueError(f"{where} is not a `.agentorc.yml` key any more (design §5): {RETIRED[key]}")
    else:
        raise ValueError(f"{where} is not a `.agentorc.yml` key (design §5)")


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    return {str(k): v for k, v in value.items()}


PROMOTE_KEYS = ("run", "check")


def _promote(value: Any, where: str) -> dict[str, str] | None:
    """`promote: {run, check}` (design §5): both commands, each a non-empty string. `auto` is not
    this file's: it is the person's switch, in `settings.yml` (§5, the Settings page)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping ({', '.join(PROMOTE_KEYS)})")
    out: dict[str, str] = {}
    for k, v in value.items():
        k = str(k)
        if k == "auto":
            raise ValueError(f"{where}.auto is not this file's: it is `repos.<repo>.promote.auto` in settings.yml")
        if k not in PROMOTE_KEYS:
            raise ValueError(f"{where}.{k} is not a promote key ({', '.join(PROMOTE_KEYS)})")
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{where}.{k} must be a command, a non-empty string")
        out[k] = v.strip()
    missing = [k for k in PROMOTE_KEYS if k not in out]
    if missing:
        raise ValueError(f"{where} needs {' and '.join(missing)} (design §5)")
    return out


def _str_list(value: Any, where: str) -> list[str]:
    """`[a, b]` or a single `a` (a lane written as `free-pick`, say) → the list; nothing else."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        raise ValueError(f"{where} must be a list of names")
    return [x.strip() for x in items]


def _commands(value: Any, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of {{name, run}} entries")
    out = []
    for i, entry in enumerate(value):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("run"):
            raise ValueError(f"{where}[{i}] needs `name` and `run`")
        out.append(dict(entry))
    return out


def _role_block(name: str, raw: Any, where: str) -> dict[str, Any]:
    """One `roles.<name>:` entry, checked per key; only the keys given are kept, so the merge in
    `resolve_role` overrides exactly what the file says and nothing more."""
    here = f"{where}.{name}"
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{here} must be a mapping ({', '.join(ROLE_KEYS)})")
    out: dict[str, Any] = {}
    for k, v in raw.items():
        k = str(k)
        if k not in ROLE_KEYS:
            raise ValueError(f"{here}.{k} is not a role key ({', '.join(ROLE_KEYS)})")
        if k == "kind":
            raise ValueError(
                f"{here}.kind: a role's kind is its definition's — a preset's — and no overlay changes it "
                "(design §4.9c)"
            )
        if k in ("brief", "profile"):
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{here}.{k} must be a string")
            out[k] = v.strip() if isinstance(v, str) else None
        elif k == "icon":
            # One name from the set the UI ships (§4.8): checked when the file is read, exactly as a
            # grant is, so a typo is a line naming the key and never a blank badge on the page.
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{here}.icon must be a string")
            name = v.strip() if isinstance(v, str) else None
            if name in RESERVED_ICONS:
                raise ValueError(f"{here}.icon: {name!r} is {RESERVED_ICONS[name]}")
            if name is not None and name not in ICONS:
                raise ValueError(f"{here}.icon: unknown icon {name!r} (known: {', '.join(ICONS)})")
            out[k] = name
        elif k == "label":
            # a person's own words for the badge (§4.8 *The names*): one line of text, bounded so a
            # badge stays a badge; drawn escaped, and nothing keys on it (§9 invariant 9)
            if v is not None and (not isinstance(v, str) or not v.strip() or "\n" in v):
                raise ValueError(f"{here}.label must be one line of text")
            if isinstance(v, str) and len(v.strip()) > LABEL_CAP:
                raise ValueError(f"{here}.label is longer than {LABEL_CAP} characters")
            out[k] = v.strip() if isinstance(v, str) else None
        elif k == "message":
            # §4.8 *A role says when to message it* (TD-171): one sentence, checked as `label:` is;
            # `null` (or an empty string) takes a built-in's default away
            if v is not None and (not isinstance(v, str) or "\n" in v):
                raise ValueError(f"{here}.message must be one line of text")
            if isinstance(v, str) and len(v.strip()) > MESSAGE_CAP:
                raise ValueError(f"{here}.message is longer than {MESSAGE_CAP} characters")
            out[k] = v.strip() or None if isinstance(v, str) else None
        elif k == "prompts":
            out[k] = _prompts(v, f"{here}.prompts")
        elif k == "review":
            # §4.9b *The reader* (TD-093): checked by the one function the host agent also applies,
            # so a typo is a line naming the key when the file is read, never a PR nobody holds
            try:
                out[k] = normalize_review(v, chain=False)
            except ValueError as e:
                raise ValueError(f"{here}.{e}") from None
        elif k == "context":
            # §4.8 *A role has a context bound* (TD-190): `{bound: 200k}`, or `none` to take a
            # built-in's away — checked by the one function the host agent's create applies too
            try:
                out[k] = {"bound": normalize_context(v)}
            except ValueError as e:
                raise ValueError(f"{here}.{e}") from None
        elif k == "grants":
            grants = _str_list(v, f"{here}.grants")
            grants = list(dict.fromkeys(grants))
            if bad := [g for g in grants if g not in GRANTS]:
                raise ValueError(f"{here}.grants: unknown grant {bad[0]!r} (known: {', '.join(GRANTS)})")
            out[k] = grants
        else:  # lane, controllers
            out[k] = _str_list(v, f"{here}.{k}")
    return out


def _prompts(v: Any, here: str) -> list[dict[str, str]]:
    """A `prompts:` list (design §4.8 *A role has saved prompts*, TD-170), checked when the file is
    read: a list of `{label, text}`, the label one line of at most `PROMPT_LABEL_CAP` characters,
    the text non-empty and kept verbatim — no substitution, what is typed is what the file says.
    `null` is no prompts."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(f"{here} must be a list of {{label, text}}")
    out: list[dict[str, str]] = []
    for i, p in enumerate(v):
        at = f"{here}[{i}]"
        if not isinstance(p, dict) or set(p) - {"label", "text"}:
            raise ValueError(f"{at} must be a mapping of label and text")
        label, text = p.get("label"), p.get("text")
        if not isinstance(label, str) or not label.strip() or "\n" in label:
            raise ValueError(f"{at}.label must be one line of text")
        if len(label.strip()) > PROMPT_LABEL_CAP:
            raise ValueError(f"{at}.label is longer than {PROMPT_LABEL_CAP} characters")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{at}.text must be the prompt, not empty")
        out.append({"label": label.strip(), "text": text})
    return out


def default_label(name: str) -> str:
    """A role's label when nothing gives one: its name with its first letter raised."""
    return name[:1].upper() + name[1:]


def held_for(cfg: RepoConfig, name: str = "") -> list[str] | None:
    """The paths a review stage holds in `cfg`'s repo (§4.9c): its set `name`'s, or with none named
    every held path (the list, or the union of the sets). None: the repo defines no such set."""
    if not name:
        return list(cfg.held) if cfg.held else None
    return list((cfg.held_sets or {}).get(name) or []) or None


def org_roles_dir() -> Path:
    """The org's role directories (design §4.9c, TD-313): the home's `roles/`, every team's to use."""
    return paths.home() / ORG_ROLES_SUB


def _dir_names(base: Path, *, any_dir: bool = False) -> list[str]:
    """The role directories under `base` on this host, by name: each a directory holding `role.yml`
    (`any_dir`: every directory, so `visible` names one that lacks it)."""
    try:
        return sorted(
            p.name
            for p in base.iterdir()
            if not p.name.startswith(".") and ((p / ROLE_FILE).is_file() or (any_dir and p.is_dir()))
        )
    except OSError:  # no such directory, or unreadable: none listed here
        return []


def org_role(name: str) -> bool:
    """Whether `name` is defined by the org's directory — not a preset, and the org's `roles/<name>/`
    holds `role.yml` — so a team on a node cannot use it (§4.9c: the org's directories are the home's)."""
    return name not in PRESETS and _plain_name(name) and (org_roles_dir() / name / ROLE_FILE).is_file()


def _plain_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".")


def definition(cfg: RepoConfig, name: str) -> tuple[str, Path, str] | None:
    """Where the role `name` is defined outside the package (§4.9c), the org's over a repo's, whole:
    `(place, directory, role.yml's text)`, or None — a preset's name is never read from a directory
    (a directory taking one is refused: `visible`). The org's is read on this host; a repo's
    through `cfg.read`, as its `.agentorc.yml` was."""
    if name in PRESETS or not _plain_name(name):
        return None
    for place, base, reader in (
        ("org", org_roles_dir(), _read_here),
        ("repo", Path(cfg.root) / ROLE_DIR if cfg.root is not None else None, cfg.read or _read_here),
    ):
        if base is None:
            continue
        d = base / name
        try:
            text = reader(d / ROLE_FILE)
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"{d / ROLE_FILE} cannot be read ({getattr(e, 'strerror', None) or e})") from None
        if text is not None:
            return place, d, text
    return None


def _defined_block(name: str, place: str, d: Path, text: str, read: Reader | None) -> dict[str, Any]:
    """A role directory's `role.yml` read as a `roles:` entry is, with its `kind` (§4.9c): a
    `brief:` refused — the directory's `template.md` is its mechanics — and the template required."""
    where = f"{d / ROLE_FILE}"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{where}: not valid YAML ({e})") from None
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError(f"{where}: the top level must be a mapping ({', '.join(ROLE_KEYS)})")
    data = {str(k): v for k, v in data.items()}
    if "brief" in data:
        raise ValueError(
            f"{where}: `brief:` is not a defined role's — its mechanics are its {ROLE_TEMPLATE}, and a repo's "
            f"supplement for it is that repo's `roles.{name}.brief` (design §4.9c)"
        )
    kind = data.pop("kind", DEFAULT_KIND)
    if kind not in KINDS:
        raise ValueError(f"{where}: kind {kind!r} is not a kind ({', '.join(KINDS)})")
    block = _role_block(name, data, f"{where}: roles")
    block["kind"] = kind
    try:
        template = (_read_here if place == "org" else (read or _read_here))(d / ROLE_TEMPLATE)
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{d / ROLE_TEMPLATE} cannot be read ({getattr(e, 'strerror', None) or e})") from None
    if template is None:
        raise ValueError(
            f"{d}: no {ROLE_TEMPLATE} — a role directory holds {ROLE_FILE} and its template (design §4.9c)"
        )
    return block


def visible(roots: Collection[Path | str] = ()) -> list[dict[str, Any]]:
    """Every role directory this host can see (§4.7 `ao org`, §4.9c), as `flowdefs.visible` lists
    flows: the org's, then each checkout's in `roots`, each `{name, source, usable, problems}` —
    `source` its directory. A directory taking a preset's name is not usable (a preset is redefined
    by key only); a repo's the org's shadows is listed with `shadowed` set, never read; one that is
    not whole (its `role.yml` not a role, no `template.md`) is not usable, with the reason."""
    out: list[dict[str, Any]] = []
    org_names = _dir_names(org_roles_dir())
    places = [("org", org_roles_dir())] + [("repo", Path(r).expanduser() / ROLE_DIR) for r in roots]
    for place, base in places:
        for name in _dir_names(base, any_dir=True):
            d = base / name
            row: dict[str, Any] = {"name": name, "source": str(d), "usable": False, "problems": []}
            if name in PRESETS:
                row["problems"] = [f"{d}: {name} is a preset's name — a preset is redefined by key only (design §4.9c)"]
            elif place == "repo" and name in org_names:
                row["shadowed"] = f"not read: the org's role {name} ({org_roles_dir() / name}) is the role"
            elif not (d / ROLE_FILE).is_file():
                row["problems"] = [f"{d}: no {ROLE_FILE} — a role directory holds {ROLE_FILE} and {ROLE_TEMPLATE}"]
            else:
                try:
                    _defined_block(name, place, d, _read_here(d / ROLE_FILE) or "", None)
                except (OSError, ValueError) as e:
                    row["problems"] = [str(e)]
                else:
                    row["usable"] = True
            out.append(row)
    return out


def _unknown(cfg: RepoConfig, name: str) -> str:
    where = f" in {cfg.root}" if cfg.root is not None else ""
    return (
        f"unknown role {name!r} — no preset, no {org_roles_dir() / name}/ and no {ROLE_DIR / name}/{where} "
        "(an overlay is not a definition, design §4.9c)"
    )


def role_names(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Every role that resolves here: the built-ins first, then the org's role directories, then the
    repo's (§4.9c), each once — an overlay's key adds none, since it defines nothing. A repo read
    across the link lists the directories its own `roles:` names; this host's lists them all."""
    repo = _dir_names(Path(cfg.root) / ROLE_DIR) if cfg.root is not None and cfg.read is None else []
    named = list(cfg.roles) if cfg.read is not None else []  # `load` refused a key naming no definition
    return list(dict.fromkeys([*PRESETS, *_dir_names(org_roles_dir()), *repo, *named]))


def resolve_role(cfg: RepoConfig, name: str, roles_overlay: dict[str, dict[str, Any]] | None = None) -> Role:
    """The role's definition — a preset, else the org's role directory, else the repo's (§4.9c,
    TD-313), whole — then `roles_overlay` (`org.yml`'s `roles:`) < the repo's `roles:`, per key.
    An unknown name — a key no preset and no directory defines among them — is a `KeyError` naming
    it, the directories that would define it, and what would have resolved; a directory that is not
    whole is a `ValueError`."""
    found = definition(cfg, name)
    defined = _defined_block(name, found[0], found[1], found[2], cfg.read) if found else None
    if name not in PRESETS and defined is None:
        raise KeyError(f"{_unknown(cfg, name)}; known: {', '.join(role_names(cfg, roles_overlay))}")
    layers = [
        ("built-in", PRESETS.get(name)),
        (f"{found[0]} role" if found else "", defined),
        ("org", (roles_overlay or {}).get(name)),
        ("repo", cfg.roles.get(name)),
    ]
    spoke = [(src, block) for src, block in layers if block is not None]
    role = Role(name=name, root=cfg.root)
    if found:
        role.defined_place, role.defined = found[0], found[1]
    for src, block in spoke:
        role.sources.append(src)
        if "kind" in block:  # a preset's alone: `_role_block` refuses it in an overlay
            role.kind = block["kind"]
        if "brief" in block:
            role.brief, role.brief_source = block["brief"], src
        if "lane" in block:
            role.lane = list(block["lane"])
        if "grants" in block:
            role.grants = list(block["grants"])
        if "profile" in block:
            role.profile = block["profile"]
        if "icon" in block:
            role.icon = block["icon"]
        if "label" in block:
            role.label = block["label"]
        if "message" in block:
            role.message = block["message"]
        if "prompts" in block:  # replaced whole at each layer, never merged (§4.8): a repo's list is the list
            role.prompts = [dict(p) for p in block["prompts"]]
        if "review" in block:
            # every layer through the one check — the org's `roles:` is checked when read too (TD-149), and
            # `normalize_review` is idempotent, so a second pass over its output changes nothing
            try:
                role.review = normalize_review(block["review"], chain=False)
            except ValueError as e:
                raise ValueError(f"{src} roles.{name}.{e}") from None
        if "context" in block:
            try:
                role.context_bound = normalize_context(block["context"])
            except ValueError as e:
                raise ValueError(f"{src} roles.{name}.{e}") from None
        if "controllers" in block:
            role.controllers, role.controllers_set = list(block["controllers"]), True
    if not any("context" in block for _, block in spoke):
        # no layer spoke: the default (§4.8 *The bound has two layers*); `context: none` is a layer speaking
        role.context_bound, role.context_default = normalize_context(DEFAULT_CONTEXT), True
    return role


def roles(cfg: RepoConfig, roles_overlay: dict[str, dict[str, Any]] | None = None) -> list[Role]:
    """Every role resolved, for `ao roles` and the form's pick-list. A role directory that is not
    whole is left out, never an error: one broken directory must not empty every pick-list, and
    `ao org check` names it (`visible`)."""
    out = []
    for n in role_names(cfg, roles_overlay):
        try:
            out.append(resolve_role(cfg, n, roles_overlay))
        except (KeyError, ValueError):
            if n in PRESETS:  # a preset's own keys: the overlay's error is the person's to see
                raise
    return out


def unread(path: Path) -> str | None:
    """A `Reader` for a directory whose files are not read from here (the New session form's config of
    a directory outside any checkout on another host, `ui.app.config_on`): nothing in it is read,
    rather than this host's disk at the same path."""
    return None
