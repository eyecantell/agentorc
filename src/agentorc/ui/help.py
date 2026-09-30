"""The help text (design §4.5a *The help text*, §4.5 screen 10 *Help*; TD-157, built by TD-167).

What the *i* marks, the `title` tooltips and the Help page say: one paragraph per control — *what it
does · when you would press it · what it does not do*. **This table is the one source in the code**,
and `tests/test_help.py` holds it equal to the design's list word for word and key for key, so the
design is the one place the text is written. A control's `title` is its paragraph's first
sentence; a mark's panel and the Help page carry the whole. Fixed text in the source, never a
session's (TD-071 item 8).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Help:
    key: str  # the control's id: the Help page's heading anchor, and what a template asks for
    name: str  # the control as the design names it
    where: str  # where it sits, in the design's words
    text: str  # the paragraph, verbatim


HELP: tuple[Help, ...] = (
    Help(
        "start",
        "Start",
        "team card",
        (
            "Runs the team again from its definition: every check first, then the concluded sessions closed "
            "under the wrap-up's own safety check, then the manager and the members created with their "
            "briefs. Press it to run the team again — after a wind-down, or when a run has concluded and you "
            "want the next. It is not a message: a running session is mailed with Message…, and a session "
            "holding uncommitted or unpushed work refuses the start instead of being closed."
        ),
    ),
    Help(
        "wind-down",
        "Wind down",
        "team card",
        (
            "Sends the wrap-up to every member and then to the manager: each finishes what it holds, pushes, "
            "and exits. Press it when the team should stop after the work in hand, not in the middle of it. "
            "It kills nothing — Stop now does — and forgets nothing."
        ),
    ),
    Help(
        "stop-now",
        "Stop now",
        "team card",
        (
            "Kills every session carrying this team's badge, at once, whatever it holds. Press it when "
            "waiting for a wind-down is worse than losing the turn in flight. Worktrees and unpushed work "
            "stay on disk under the cards, which read exited; Forget is a separate press."
        ),
    ),
    Help(
        "fold",
        "the fold",
        "*n sessions* on a team's card, and a click on its header",
        (
            "Shows or hides a team's cards and its summary; a stopped team's are folded away by default. "
            "Press it to see what the team left — its repo's numbers, the claims still held, what each member "
            "last said it was doing — to read their mail, or to Forget them. It changes nothing on any record; "
            "which teams you have unfolded is remembered in this browser."
        ),
    ),
    Help(
        "forget",
        "Forget",
        "a card's foot, the exited banner",
        (
            "Drops this session's record: the card, its report line and its mail. Press it when a finished "
            "session's card is clutter — its work merged, or pushed and accounted for. It stops nothing, "
            "since a live session offers no Forget; the worktree and the run log stay on disk, and the "
            "conversation stays in the tool's own files, where the Resumable list finds it. On a team the "
            "seat stays: the definition names it, and Start fills it again."
        ),
    ),
    Help(
        "forget-all",
        "Forget all",
        "team card",
        (
            "The Forget of every exited or closed card of this team, in one press. Press it when the team's "
            "run is over and everything it pushed has landed. It never forgets a card carrying work that "
            "exists only on this machine — those it names, and you forget them one at a time with the flag in "
            "view — nor a seat on call."
        ),
    ),
    Help(
        "kill",
        "Kill",
        "Focus header, *more ▾*",
        (
            "Ends this session's process now and destroys its pane; the record stays, reading exited, and the "
            "worktree stays. Press it when a session is stuck or running away and a Wrap up would not be "
            "read. It is not Close, which also reaps the worktree, and not Forget, which drops the record; "
            "both are still there after it."
        ),
    ),
    Help(
        "close",
        "Close",
        "*Close session* on a card's foot, the Focus side panel, *more ▾*",
        (
            "Kills the session and reaps its worktree; the record reads closed. Press it when the work is "
            "merged and every line of Ready to close is green. It is offered only when that checklist passes "
            "— Kill always is — and it is the one press that removes a worktree."
        ),
    ),
    Help(
        "wrap-up",
        "Wrap up",
        "Focus header, *more ▾*",
        (
            "Sends the wrap-up prompt — the one a policy sends before a stop — so the session finishes, "
            "pushes and ledgers what it holds, then ends its turn. Press it when you want the work saved "
            "rather than the process stopped. It kills nothing: the session ends when it says it has, and a "
            "stop time does the same on a clock."
        ),
    ),
    Help(
        "resume",
        "Resume",
        "the exited banner, a Resumable row",
        (
            "Starts the conversation again under this name, on this record: the same mail, a new process at "
            "the tool's composer. Press it when you want to continue a finished session's conversation. To "
            "only read it, press Transcript instead: a resume is a start, which a manager may act on and "
            "which has to be closed again."
        ),
    ),
    Help(
        "message",
        "Message…",
        "a seat's card, *more ▾*, Focus header",
        (
            "Mails a question or a note into this session's inbox; the session reads it when it next looks, "
            "and a question fills a seat on call. Press it to ask or tell a session something without typing "
            "into its terminal. It types nothing into the pane — Send does — and it starts no team: a stopped "
            "team is started by Start."
        ),
    ),
    Help(
        "switch-profile",
        "Switch profile…",
        "a `limited` card",
        (
            "Re-launches this session under another profile with its conversation carried over. Press it when "
            "the account it runs on is capped and another is not. It is a new process on the same record; "
            "Wait leaves the session where it is until the account resets."
        ),
    ),
    Help(
        "promote",
        "Promote",
        "Inbox row: promote",
        (
            "Makes main's head live for this repo: the home starts the repo's own promote run with it, and a "
            "note says when it is live. Press it when what is merged should be what runs, and on a held row "
            "once main holds the cure: a press that concludes ends the hold. It never goes back to an older "
            "commit — that is `ao promote --sha` or `--back` — and it is refused, in words, while a run is "
            "in flight, a failure stands, or the checkout is not clean on main."
        ),
    ),
    Help(
        "promote-snooze",
        "Snooze ▾",
        "Inbox row: promote",
        (
            "Sets this row aside until the time you pick: +1 day, +1 week or a date. Press it when you "
            "promote in batches and want to be left alone until then. It changes nothing at the home: more "
            "merges meanwhile do not bring the row back, and a snoozed failure or hold still stops the "
            "policy."
        ),
    ),
    Help(
        "promote-dismiss",
        "Dismiss",
        "Inbox row: promote",
        (
            "Clears the home's own mark on this repo: a failed promote first, and a rollback's hold when no "
            "failure stands. Press it once you have read the failure, or when what a rollback went back from "
            "may go live again. It moves nothing live: after it, promoting goes on — under `auto`, to main's "
            "head at the next pass."
        ),
    ),
)

BY_KEY: dict[str, Help] = {h.key: h for h in HELP}

# §4.5a the ***i*** mark row: one mark per control group, and the group's controls in its order
GROUPS: dict[str, tuple[str, ...]] = {
    "team": ("start", "wind-down", "stop-now", "forget-all", "fold", "forget", "resume", "close", "message"),
    "focus": ("wrap-up", "kill", "close", "message"),
    "exited": ("resume", "forget"),
}
# where each group's *every control → Help* lands on the Help page
GROUP_SCREEN = {"team": "org", "focus": "focus", "exited": "focus"}

# §4.5 screen 10: the Help page, by screen in the §4.5a table's order, each paragraph once
SCREENS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "org",
        "Org",
        ("start", "wind-down", "stop-now", "fold", "forget-all", "forget", "close", "message", "switch-profile"),
    ),
    ("focus", "Focus", ("wrap-up", "kill", "resume")),
    ("inbox", "Inbox", ("promote", "promote-snooze", "promote-dismiss")),
)


def first_sentence(key: str) -> str:
    """A control's `title` (§4.5a *The help text*): its paragraph's first sentence."""
    text = BY_KEY[key].text
    end = next(
        (i + 1 for i, c in enumerate(text) if c in ".!?" and (i + 1 == len(text) or text[i + 1] == " ")), len(text)
    )
    return text[:end]
