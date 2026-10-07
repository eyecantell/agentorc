#!/usr/bin/env python3
"""Split `docs/design.md` into one file per section under `docs/design/` (TD-346, built by TD-351).

    python3 scripts/split_design.py [--check]

Cuts at every `## ` heading and, inside §4, every `### ` heading, and writes each piece to
`docs/design/<§>-<slug>.md`: the heading line first, then the section's lines as they stand.
The one change made to a line is a relative link's path, which gains a `../` so that it still
lands where it did from `docs/` (`](decisions/…)` → `](../decisions/…)`). `docs/design.md` is
then rewritten as the index: the title and the status paragraph as they stand, the rule in one
line, and one line per file in § order.

Run once, on whatever `main` held the day the split landed, and kept for the record. It refuses
a `docs/design.md` that is already the index. `--check` writes nothing and prints the plan.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs" / "design.md"
OUT = ROOT / "docs" / "design"

# TD-346's list: the § to its file, in index order. §4 is its own preamble, the lines before §4.1.
FILES = {
    "1": "1-problem.md",
    "2": "2-goals.md",
    "3": "3-prior-art.md",
    "4": "4-architecture.md",
    "4.1": "4.1-session-substrate.md",
    "4.2": "4.2-state-feed.md",
    "4.2a": "4.2a-profiles.md",
    "4.3": "4.3-adapter-contract.md",
    "4.4": "4.4-host-agent.md",
    "4.4a": "4.4a-home-and-nodes.md",
    "4.5": "4.5-ui.md",
    "4.5a": "4.5a-controls.md",
    "4.5b": "4.5b-reachability.md",
    "4.5c": "4.5c-product-direction.md",
    "4.6": "4.6-transport.md",
    "4.7": "4.7-cli.md",
    "4.8": "4.8-capabilities-roles.md",
    "4.8a": "4.8a-identity.md",
    "4.9": "4.9-org-team-project.md",
    "4.9a": "4.9a-winding-down.md",
    "4.9b": "4.9b-techlead.md",
    "4.9c": "4.9c-flows.md",
    "4.10": "4.10-messages.md",
    "5": "5-configuration.md",
    "6": "6-policies.md",
    "7": "7-phases.md",
    "8": "8-lessons.md",
    "9": "9-invariants.md",
    "10": "10-open-questions.md",
    "11": "11-references.md",
}

HEADING = re.compile(r"^(##|###) (\d+(?:\.\d+[a-z]?)?)\.? (.*)$")
# a relative link: not a URL, not an anchor, not rooted, not already climbing
LINK = re.compile(r"\]\((?![a-z][a-z0-9+.-]*:|#|/|\.\./)")
RULE = (
    "A change in behaviour is a change to the design first, and the dated fact goes to "
    "[`design-history.md`](design-history.md); a control that is not in §4.5a's table does not exist."
)


def split(text: str) -> tuple[str, list[tuple[str, str, str, list[str]]]]:
    """The title block, and `(§, title, heading line, lines)` for each section in file order."""
    lines = text.split("\n")
    head, sections, in_fence = [], [], False
    for ln in lines:
        if ln.startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(ln)
        if m and (m.group(1) == "##" or m.group(2).startswith("4.")):
            sections.append((m.group(2), m.group(3), ln, [ln]))
        elif sections:
            sections[-1][3].append(ln)
        else:
            head.append(ln)
    return "\n".join(head), sections


# One line per file for the index: what a reader finds there (TD-346: the first sentence or a gloss).
GLOSS = {
    "1": "the person running many agent sessions across hosts, and what that costs them today.",
    "2": "what agentorc must do, and what it does not try to.",
    "3": "the tools surveyed, what was taken from each, and why none is the substrate.",
    "4": "the picture: browser, UI, host agents, tmux, and the home that joins them.",
    "4.1": "one tmux session per conversation; names, the anchor rule, worktrees, plain shells.",
    "4.2": "the states, fed by the tool's hooks first and by screen rules, labelled, where none fires.",
    "4.2a": "a profile is a tool, an account and a model; how a session is launched on one.",
    "4.3": "what a tool adapter supplies: argv, hooks, pane classification, the composer.",
    "4.4": "the per-host process that owns tmux, the record, the tick and the RPC.",
    "4.4a": "one session graph across hosts: the home, its nodes, and how a container node is made.",
    "4.5": "the pages — Org, Focus, Inbox, New session, Repo, Settings, Help — and what each shows.",
    "4.5a": "every control, what it does and who executes it; a control not in this table does not exist.",
    "4.5b": "how someone who never opened a port reaches the page, and a hosted service's shape.",
    "4.5c": "the two products this architecture serves, and who owns the host in each.",
    "4.6": "the links between UI, host agents and panes; reconnects, backoff, `unreachable`.",
    "4.7": "the `ao` command: its verbs, its JSON, what each one mutates.",
    "4.8": "what a session may do to agentorc: grants, controllers, report channels, role presets, briefs.",
    "4.8a": "how the host agent knows which session is calling, and what its gates are and are not.",
    "4.9": "the org, its teams and projects; how a team is defined, started and stopped.",
    "4.9a": "how a team that runs out of work winds down, and a run's three words.",
    "4.9b": "the techlead seat: what it reads, what it answers, and what still reaches the person.",
    "4.9c": "the path an entry takes through a team: flows, stages, `held:` and the review.",
    "4.10": "mail between sessions and to the person: kinds, bounds, outcomes, threads, looks.",
    "5": "the settings file, the repo's `.agentorc.yml`, and where each setting lives.",
    "6": "the tick's policies: usage gates, run windows, restarts, promote, balance.",
    "7": "the phase plan, re-baselined against what runs, and what each phase still lacks.",
    "8": "lessons carried in from dev-cadence and tdgrind.",
    "9": "the rules that hold whatever else changes.",
    "10": "the dated log of questions, each a decision that changes what gets built.",
    "11": "files, repos and documents the design cites.",
}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    text = DESIGN.read_text(encoding="utf-8")
    head, sections = split(text)
    if not sections:
        sys.exit("docs/design.md holds no `## ` section: it is the index already")
    got = [s[0] for s in sections]
    if got != list(FILES):
        sys.exit(f"the sections are not TD-346's list: {got}")
    # the title block is the title and the status paragraph; it ends in blank lines before §1
    title_block = head.rstrip("\n")
    moved = {num: "\n".join(LINK.sub("](../", ln) for ln in body) for num, _, _, body in sections}
    # nothing lost or reflowed: undo the link prefix and the files are the old file minus its title block
    joined = "\n".join(moved[n] for n in FILES).replace("](../", "](")
    rest = text[len(head) + 1 :] if head else text
    if joined != rest.replace("](../", "]("):
        sys.exit("the split does not join back into the old file")
    index = [title_block, "", RULE, ""]
    index += ["The design is the files under [`design/`](design/), one per section and one per subsection of §4;"]
    index += ["grep the directory, read a section by its file.", ""]
    for num, title, _, _ in sections:
        index.append(f"- **§{num}** [{title}](design/{FILES[num]}) — {GLOSS[num]}")
    index_text = "\n".join(index) + "\n"
    if check:
        for num in FILES:
            print(f"docs/design/{FILES[num]}: {moved[num].count(chr(10)) + 1} lines")
        print(index_text)
        return 0
    OUT.mkdir(exist_ok=True)
    for num, name in FILES.items():
        # each file keeps its section's trailing blank line (§11's is the old file's last newline), so
        # `cat` in index order joins them back into the old file minus its title block
        body = moved[num]
        (OUT / name).write_text(body if num == "11" else body + "\n", encoding="utf-8")
    DESIGN.write_text(index_text, encoding="utf-8")
    print(f"wrote {len(FILES)} files under docs/design/ and the index at docs/design.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
