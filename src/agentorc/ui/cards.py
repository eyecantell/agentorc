"""A session's card (design §4.5, §4.5a): the view model every page draws a session from (`view`), the
identity alarms on it (§4.8a), the gated view, the card's slot and next act, Ready to close, and the
order and counts a group of cards is drawn in. Moved out of `agentorc.ui.app` unchanged (TD-196) and
re-exported from it, so a route, a template or a test reads each name from the app as before.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentorc import profiles as profiles_mod
from agentorc import repoconfig
from agentorc.ending import NO_CLOSER, closer_words, ending_hover, exit_words, restart_words, waiting_words
from agentorc.org import ANCHOR_WHEN, MANAGER_WHEN
from sessionorc import hosts, identity, mail, naming, work
from sessionorc.adapters import short_model
from sessionorc.agent_common import CLOSED_KEEP, RESTART_CEILING, RESTART_WINDOW, _counted
from sessionorc.models import (
    GRANTS,
    STATE_RANK,
    context_over,
    context_reading,
    has_control,
    pr_marks,
    report_head,
    report_line,
    report_ref,
    start_note,
    stop_note,
    tokens_short,
)
from sessionorc.reports import branch_ref

from .common import _age, _instant, editor_link, host_name

# -- identity alarms (design §4.8a, TD-077 step 2) -------------------------------------------------


def _count(raw: Any) -> int:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def alarm_words(a: dict[str, Any]) -> str:
    """One alarm in words (design §4.8a: `{channel, claimed, rpc, count, at, last}`). The `(others)`
    entry stands for every distinct alarm past the list's room and names no rpc, so it is said as
    what it is rather than printed as a row with empty fields."""
    n = _count(a.get("count"))
    claimed = str(a.get("claimed") or "")
    if claimed == identity.OTHERS:
        return f"and {n} more distinct claim{'' if n == 1 else 's'}"
    channel = str(a.get("channel") or "an unknown channel")
    rpc = str(a.get("rpc") or "a request")
    who = f"claimed to be {claimed}" if claimed else "sent no caller"
    return f"{channel} {who} on {rpc}{'' if n == 1 else f' ×{n}'}"


def alarm_view(raw: Any) -> list[dict[str, Any]]:
    """A record's (or the host's) `identity_alarms` as the page shows them: the words, and the
    first and last time for the browser to put in the person's own clock. **Tolerant by design** —
    a record written by another build, or repaired by hand, must cost its card a mark and not the
    grid (the `_age` rule, review of PR #203), so anything that is not a dict is dropped and every
    field is read as text."""
    if not isinstance(raw, list):
        return []
    out = []
    now = datetime.now(UTC)  # one instant for the whole render, as every other call site takes one
    for a in raw:
        if not isinstance(a, dict):
            continue
        at = a.get("at") if isinstance(a.get("at"), str) else ""
        last = a.get("last") if isinstance(a.get("last"), str) else ""
        # §4.5 screen 6 *Layout* (TD-082): every time on the page is in words from here. These two
        # are instants, so the row upgrades them to the browser's own clock once its script runs —
        # but a page that never runs it, or a screenshot of one, still reads *2h 5m ago*, not `…`.
        out.append({
            "words": alarm_words(a),
            "at": at,
            "last": last or at,
            "at_words": f"{_age(at, now)} ago" if _age(at, now) else "",
            "last_words": f"{_age(last or at, now)} ago" if _age(last or at, now) else "",
            "count": _count(a.get("count")),
        })  # fmt: skip
    return out


def alarm_to_view(raw: Any) -> dict[str, str] | None:
    """Who **Log TD** would hand a record's alarms to (design §4.8a *An alarm's answers*, TD-077 b),
    as the home's `alarm_to` gives it — `{"id", "name"}`, its first live controller — or None for
    *nobody*. A shape another build wrote (a bare string, a dict with no id) is *nobody* too: the
    row then says so in words, and costs nothing but the button."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"].strip():
        return None
    name = raw.get("name")
    return {"id": raw["id"], "name": name if isinstance(name, str) and name.strip() else raw["id"]}


def suspended_note(raw: Any) -> str:
    """The **suspended** mark's words, or "" (design §4.8a *An alarm's answers*, TD-077 a2).

    A suspension **ends no row and so writes no trail**, which makes this mark its only record on
    a page: if it is not drawn, nothing says it happened. So it is drawn wherever the record is —
    and it is a *mark*, never a control, because the two things that lift it are a person's Resume
    and Forget, both of which already exist and neither of which belongs on a badge.

    Tolerant, like every other derived chip here: a record written by another build, or repaired by
    hand, costs its card a mark and never the grid (the `_age` rule)."""
    if not isinstance(raw, dict):
        return ""
    at, by, why = (str(raw.get(k) or "") for k in ("at", "by", "why"))
    if not (at or by or why):
        return "suspended by a person — no detail recorded"
    bits = [f"suspended{f' at {at}' if at else ''}{f' by {by}' if by else ''}"]
    bits.append(why or "no reason recorded")
    return f"{bits[0]}: {bits[1]} — only a person lifts it, by resuming it or forgetting it (design §4.8a)"


def alarm_note(alarms: list[dict[str, Any]]) -> str:
    """The card mark's hover: the newest alarm in words, and how many there are in all. Empty when
    there are none, which is what draws no mark."""
    if not alarms:
        return ""
    newest = max(alarms, key=lambda a: (a["last"], a["at"]))
    rest = f" · {len(alarms)} alarms in all" if len(alarms) > 1 else ""
    return f"identity alarm: {newest['words']}{rest}"


def host_volatile(name: str) -> bool:
    """Whether `hosts.yml` marks the host `volatile` (design §5): this host's own `local` entry, or
    another host's flags under `nodes:` (§4.4a)."""
    local = hosts.local_host()
    if name == local.name:
        return local.volatile
    return hosts._flag(hosts.nodes().get(name, {}).get("volatile"))


def view(
    s: dict[str, Any],
    fleet: list[dict[str, Any]] | None = None,
    *,
    fleet_known: bool = True,
    icons: dict[tuple[str, str], tuple[str, str, str]] | None = None,
    seats: Mapping[str, str] | None = None,
    repos: Mapping[str, Any] | None = None,
    waits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a card or the Focus header needs, computed once. `fleet` is the other records,
    needed only for the membership directions (design §4.8): who controls this session, and — for
    a lead — which sessions it controls. Without it both come back empty, which is what a
    caller that has only one record should show. `seats` is the ids a team definition names as a
    seat, each with what would make it come (`teamrun.seat_ids`): without it, a seat with nobody in
    it is drawn as the `exited` it is. `waits` is `waits_of` the person inbox: without it, no card
    says what it waits on."""
    now = datetime.now(UTC)
    d = dict(s)
    state = s["state"]
    # `closed` is its own class since TD-095: it was `done`, drawn green, and on a card green now
    # means working and nothing else (design §4.5 *The card's anatomy*) — closed is over, and grey.
    d["state_class"] = {"needs-you": "needs", "stalled?": "stalled"}.get(state, state)
    d["state_label"] = {"needs-you": "needs you", "closed": "closed"}.get(state, state)
    d["rank"] = STATE_RANK.get(state, 9)
    # Finished while nobody was looking (design §4.2, TD-017): not a state, a rendering of `idle`
    # that sorts just above the idle it will become once someone opens Focus. Both stamps are whole
    # seconds, so a finish in the same second as the last look reads as seen. **Interactive only**
    # (Paul, 2026-09-21, TD-095 f): an unattended session's result is its manager's to read, and
    # its slot already says how it ended — a finished worker is plain `idle`.
    d["unseen"] = (
        state == "idle" and not s.get("unattended") and (not s.get("seen_at") or (s.get("since") or "") > s["seen_at"])
    )
    if d["unseen"]:
        d["state_label"] = "idle · unseen"  # not *finished*: that word is a declaration's (§4.9a, TD-095 e)
        d["rank"] = STATE_RANK["idle"] - 0.5
    # A seat with nobody in it reads *on call* (design §4.5 *The card's anatomy*, TD-097): composed
    # as *idle · unseen* is, from `exited` / `closed` and the definition — the state stays what it
    # is in every payload. A seat ends between questions by design, and drawn as *exited* the one
    # card behaving exactly as designed looked like the one that had failed.
    seats = seats or {}
    d["seat"] = state in DEAD and s.get("id") in seats
    # design §4.5a **Restart** (§6 rule 2 *A person's restart*, TD-250): where the control is drawn — a
    # supervised record that is idle, exited or closed and is not a seat (rule 3 fills one); the
    # host agent makes every other refusal, in its own words. A press with no restart mark confirms.
    marks = ("restart_wanted", "restart_ceiling", "restart_blocked")
    d["restartable"] = bool(s.get("supervised")) and state in ("idle", "exited", "closed")
    d["restartable"] = d["restartable"] and not s.get("seat") and not s.get("superseded_by")
    d["restart_marked"] = any(isinstance(s.get(k), dict) for k in marks)
    d["seat_when"] = seats.get(s.get("id") or "", "") if d["seat"] else ""
    trig, counted = s.get("seat") or {}, (s.get("seat_count") or {}).get("prs")
    if d["seat_when"] and trig.get("trigger") == "prs" and isinstance(counted, int) and not s.get("seat_due"):
        # the tick's count toward it (§6 rule 3, TD-103): *on call — runs after 10 PRs · 4 of 10*
        d["seat_when"] += f" · {counted} of {trig.get('after')}"
    if d["seat"]:
        d["state_class"], d["state_label"] = "oncall", "on call"
    # the word the Org filter's `state:` matches (§4.5a **filter…**, TD-176): the pill's, hyphenated
    d["pill_word"] = "on-call" if d["seat"] else "unseen" if d["unseen"] else str(state).rstrip("?")
    # §4.10 *When it is read* (TD-168): the composer's sentence for each kind, the host agent's; a
    # seat the definition names reads as one whatever the record's own `seat` says, so the pair is
    # the seat's from here (`mail.read_when` is the one function either way)
    rw = s.get("read_when") if isinstance(s.get("read_when"), dict) else {}
    if d["seat"]:
        rw = {k: mail.read_when(None, k, now, seat=True) for k in ("ask", "note")}
    d["read_when"] = {k: str(rw.get(k) or "") for k in ("ask", "note")}
    d["age"] = _age(s.get("since"), now)
    # a seat's *last came* is when it came — the record's `created`, the fill (§6 rule 3) — not
    # `since`, which for a seat that has left is when it left (TD-200: *last came · 0s ago*)
    d["came_age"] = _age(s.get("created"), now) if d["seat"] else ""
    # *on call* is read from the seat's record, never from a screen: no dashed pill for it (TD-296 #10)
    d["scraped"] = s.get("confidence") == "scraped" and not d["seat"]  # a `tick` state is observed (TD-490)
    # Another host's record, as the home shows it (design §4.4a): its own host on the card, and a
    # VS Code link only when a container node's reach names one — the ssh URL below is built from
    # *this* host's alias, which would open the wrong machine.
    d["host"] = s.get("host") or host_name()
    here = d["host"] == host_name()
    # An unreachable host's reason, and what the home is doing about a container node (§4.4a
    # "The home supervises it"): the overlay's line, on the state pill's title and as the flag.
    hl = s.get("host_link") or {}
    sup = hl.get("supervisor") or {}
    d["host_note"] = sup.get("doing") or (hl.get("why", "") if state == "unreachable" else "")
    # A volatile host's silence is expected (a laptop asleep), so its unreachable card sorts with
    # `idle`, not among the urgent (design §4.5 *One order, no control*, TD-004).
    if state == "unreachable" and host_volatile(d["host"]):
        d["rank"] = STATE_RANK["idle"]
    # The editor button (§4.5a, §5 *The person's own*, TD-095): the person's `open_in:`. A container
    # node's record reaches VS Code by attaching to that container (§4.4a "Reach"), from what the
    # home derived when the node dialed in; any other host's record has no link.
    reach = "" if here else str((hl.get("reach") or {}).get("vscode") or "")
    d["editor"] = editor_link(str(s.get("dir") or ""), reach) if here or reach else None
    d["place"] = f"{d['host']} / {Path(s['repo']).name}" if s.get("repo") else f"{d['host']} / {s.get('dir', '')}"
    git = s.get("git") or {}
    where = s.get("dir", "")
    if s.get("repo") and s.get("dir") and s["dir"] != s["repo"]:
        where = f"wt/{Path(s['dir']).name}"
    if git.get("branch"):
        where += f" → {git['branch']}"
    d["where"] = where
    # design §4.5 *The card's anatomy*, row 3 (TD-095): **where**, alone on its row — the branch
    # by name, shortened in the middle so both ends read, whole on hover; a detached HEAD by its
    # short sha; the directory for a session with no repo. `wt/<name> ·` leads only when the
    # worktree is not the session's own name, which on a team is every member's.
    wt = Path(s["dir"]).name if s.get("repo") and s.get("dir") and s["dir"] != s["repo"] else ""
    d["wt_prefix"] = f"wt/{wt} · " if wt and wt != s.get("name") else ""
    branch = str(git.get("branch") or "")
    if branch == "(detached)":
        oid = str(git.get("oid") or "")
        d["branch_full"] = f"detached at {oid[:7]}" if oid else "detached HEAD"
    elif branch and branch != "?":
        d["branch_full"] = f"branch {branch}"
    else:
        d["branch_full"] = "" if s.get("repo") else str(s.get("dir") or "")
    d["branch_line"] = _middle(d["branch_full"], BRANCH_SHOWN)
    # what leads row 3 outside a team's own group, where the header does not say it: `host / repo ·`,
    # or `host /` before the directory of a session with no repo
    d["place_prefix"] = f"{d['place']} · " if s.get("repo") else f"{d['host']} / "
    flags = []
    if git.get("dirty"):
        flags.append("dirty")
    if git.get("unpushed"):
        # the one measure (design §4.2, TD-080): *exists only on this machine*, never *unmerged*
        flags.append(f"{git['unpushed']} unpushed")
    d["flag"] = " · ".join(flags) if state in ("idle", "exited", "stalled?", "needs-you") and flags else ""
    # the compact card's form (TD-200): clipped to fit, *47 unpushed* once read *⚠ 4* — a different
    # count. The number alone stays true; the full words are its `title`.
    short = [f for f in ("dirty" if git.get("dirty") else "", str(git.get("unpushed") or "")) if f]
    d["flag_short"] = " · ".join(short) if d["flag"] else ""
    prof = s.get("profile") or ""
    if s.get("adapter") == "shell":
        d["profile_line"] = "shell"
    else:
        # tool · account · model (design §4.2a). The third part is the model actually in use when
        # the adapter can tell; the profile's declared model is an intent, so it says so (TD-031).
        declared = None
        try:
            p = profiles_mod.get(prof or None)
            line = " · ".join([p.adapter, p.account or p.name])
            declared = p.model
        except (KeyError, ValueError):
            line = f"{s.get('adapter')} · {prof or 'default'}"
        if observed := short_model(str(s.get("adapter") or ""), s.get("model")):
            line += f" · {observed}"
        elif declared:
            line += f" · {declared} (profile)"
        d["profile_line"] = line
    # the context reading after the model (design §4.5 row 4, TD-190): *231k* on the card, *231k of
    # 1M* in Focus; absent where the adapter cannot tell
    d["context_short"] = context_reading(s, of_window=False)
    d["context_line"] = context_reading(s)
    # red past the role's bound (§4.8 *A role has a context bound*, §6 rule 5): the bound rides in
    # the title, so the red says what it is measured against
    d["context_over"] = context_over(s)
    # **Told at start**'s heading (§4.5a, TD-283 part 2): the start context's length in lines
    told = s.get("start_context")
    d["start_lines"] = sum(1 for x in told.splitlines() if x.strip()) if isinstance(told, str) else 0
    bound = s.get("context_bound")
    d["context_bound"] = tokens_short(bound) if isinstance(bound, int) and bound > 0 else ""
    # A record whose `pending` is not a dict — another build, a hand repair — costs its card its
    # pending line and nothing more, the rule `doing` and `out_of_work` already follow: every
    # reader below (the card, the Focus header, `state_kind`) gets one shape (review of PR #251).
    pend = s.get("pending")
    pend = pend if isinstance(pend, dict) else {}
    d["pending"] = pend
    d["deadline"] = pend.get("deadline") or ""
    # The report channels (design §4.8, §4.5a card **report line**, TD-028 step 4). One line, shown
    # only when a channel is non-empty: `report_line` is the same text `ao status -v` prints — one
    # formatter, so the card and the CLI cannot drift — and the findings count rides beside it. The
    # line is dashed when the entry it leads with was derived rather than declared, exactly as a
    # scraped state is; the `~` in the text says *which* entry, the dash says "not from the session".
    findings = s.get("findings") or []
    head = report_head(s)
    # **the PR's mark** (§4.5a card **report line**, TD-193): from the readings of the record's own
    # repo, `repos` — none passed, none marked; `pr_marks` rides for the Reports panel's links
    marks = pr_marks(s, repos)
    d["pr_marks"] = {str(n): w for n, w in marks.items()}
    d["report"] = report_line(s, marks)
    d["report_ref"] = report_ref(s, marks)
    d["report_derived"] = bool(head and head.get("source", "declared") != "declared")
    d["findings_line"] = f"{len(findings)} filed" if findings else ""
    # design §4.5a card / Focus header **out of work** chip (§4.9a, TD-053 step 6). Not a state —
    # the session still reads `idle` or `exited` — and shown for any session that declared it, since
    # a hand-started worker may run out too. The words are fixed and the `why` is the hover, because
    # the reason is a paragraph naming every entry the session looked at: a card cannot hold it, and
    # a card that tried would push the report line off. Dropped the moment the session claims again,
    # which is the record's own rule (§4.9a: a session that claims has work again).
    oow = s.get("out_of_work")
    oow = oow if isinstance(oow, dict) else {}  # one malformed record must not empty the grid
    d["out_of_work"] = (
        {"why": str(oow.get("why") or "").strip(), "age": _age(oow.get("at"), now)} if oow.get("at") else None
    )
    d["brief_changed"] = brief_changed_view(s.get("brief_changed"))
    d["flow_mark"] = flow_mark(s, hosts.local_host().name) if s.get("team") else None
    d["waiting"] = waiting_view((waits or {}).get(s.get("id")))
    # **waiting** (§4.2 *Waiting*, §4.5 *The card's anatomy*, TD-418, built by TD-428): an `idle` session
    # that waits on someone — a claim whose PR the repo reading holds open, or the mark above — is drawn
    # *waiting*, teal, in the order after `working`; it stays `idle` in every payload. *idle · unseen*
    # wins over it: the person's cue to go and look, and the slot still says the wait.
    d["review_wait"] = review_wait(s, fleet, repos) if state == "idle" else ""
    d["wait_reason"] = (d["waiting"] or {}).get("text") or (f"waiting · {d['review_wait']}" if d["review_wait"] else "")
    if state == "idle" and not d["unseen"] and d["wait_reason"]:
        d["state_class"] = d["state_label"] = d["pill_word"] = "waiting"
        d["rank"] = STATE_RANK["working"] + 0.25  # after `working`, before an unseen `idle` (idle - 0.5)
    # design §4.5a **restart wanted** chip (§4.9a *A run that ends with work left*, TD-083): the
    # third ending — *my run is over and my lane is not*. Shaped exactly like `out_of_work` above,
    # and for the same reasons: fixed words, the `why` on hover because it is a sentence a card
    # cannot hold, and one malformed record costs that card its chip and not the grid.
    #
    # **`early` is carried, and it is the one thing the design did not have to say.** The home
    # marks a restart asked for inside `RESTART_EARLY` of the record's own start, and a controller
    # **does not act on it** — a run that was over before it began did not run out of context. So
    # an early one must not read as an ordinary one: a person seeing the same chip would expect the
    # same thing to happen next, and nothing will. What made it early is the home's reading too
    # (§4.9a *Early is decided from the record*, TD-249 slice 6): `repeat`, the entry a run reported
    # again or left a third time, and `decided`, the words — nothing here reads a clock.
    # design §4.5a **restarted** chip (§6 *Keeping a team running*, TD-485, built by TD-487): where this
    # run came from — never on a seat, which ends and is filled by design
    d["restarted"] = None if s.get("seat") or s.get("id") in seats else restarted_view(s.get("restarts"), now)
    rw = s.get("restart_wanted")
    rw = rw if isinstance(rw, dict) else {}
    d["restart_wanted"] = (
        {
            "why": str(rw.get("why") or "").strip(),
            "age": _age(rw.get("at"), now),
            "early": bool(rw.get("early")),
            "repeat": str(rp.get("ref") or "") if isinstance(rp := rw.get("repeat"), dict) else "",
            "decided": str(rw.get("decided") or "").strip(),
            "at": str(rw.get("at")),  # the Inbox's restart row is keyed on it (§4.5a, TD-103)
        }
        if rw.get("at")
        else None
    )
    # design §4.8a (TD-077 step 2): the identity alarms kept on this record — requests that named
    # this session from somewhere it does not live. A **mark**, never a control: it says *a person
    # should look*, and what to do about it is a row in the Inbox. Shaped like the chips above, so
    # one malformed entry costs that card its mark and not the grid.
    d["alarms"] = alarm_view(s.get("identity_alarms"))
    d["alarm_note"] = alarm_note(d["alarms"])
    d["suspended_note"] = suspended_note(s.get("suspended"))
    # design §4.5a card **doing** line (§4.8, TD-074): what the session says it is doing, always with
    # its age — *says · 11m ago* — so a stale line reads as stale. Text a model wrote: shown, never
    # acted on, and escaped like everything else. `None` for a session that has said nothing, which
    # is what makes the slot fall back to the tail; shaped like the chip above, so one malformed
    # record costs that card its line and not the grid.
    doing = s.get("doing")
    doing = doing if isinstance(doing, dict) else {}
    text = str(doing.get("text") or "").strip() if isinstance(doing.get("text"), str) else ""
    d["doing"] = {"text": text, "age": _age(doing.get("at"), now)} if text else None
    # design §4.5a card / Focus header **title** (§4.3 `title()`, TD-074): the session's name as its
    # tool holds it, observed from the pane and cleaned there. Display only and always shown when
    # there is one — it is a name, not a status, so it is not a fallback for the `doing` line. Empty
    # for every adapter that gives none, which is what draws nothing.
    tool_title = s.get("title")
    d["title"] = tool_title.strip() if isinstance(tool_title, str) else ""
    # on a card only when it says something the name does not (TD-095): a team's members are
    # titled by their names, and the same word twice is noise. Focus shows it as before.
    d["title_shown"] = d["title"] if d["title"] != s.get("name") else ""
    # design §4.5 *The Focus screen's anatomy* (TD-156, Paul): a session is the person's **own** —
    # theirs to close — when it is interactive or carries no team badge; an unattended team member
    # runs itself and its team's Wind down or Start closes it, so Focus offers Close session as
    # the next act, and *ready to close ✓*, only on an own session, and folds its checklist away
    d["own"] = not s.get("team") or not s.get("unattended")
    # The role's icon (design §4.8 *Role presets*): resolved here from the role's *name* — nothing in
    # the core keys on a role (§9 invariant 9) and no icon is stored on the record. Without a map
    # (a caller that did not resolve one) the badge draws its word alone, as it always has.
    # the role badge (design §4.8 *The names*, TD-076): its picture and its **label** — what it
    # shows in place of the bare key. Resolved with the icon, off the render path; a view built
    # without `icons` still says the role, by its default label, never nothing.
    look = (icons or {}).get((str(s.get("repo") or ""), str(s.get("role") or "")))
    role = str(s.get("role") or "")
    d["role_icon"], d["role_label"], d["message_line"] = look or (
        "",
        repoconfig.default_label(role),
        str((repoconfig.PRESETS.get(role) or {}).get("message") or ""),
    )
    # §4.8 *A role says when to message it* (TD-171): the definition's line, drawn as text — the
    # composer's first line and the Message control's `title`; "" for a role without one
    # design §6 / §4.5a: when this session stops, from the same formatter `ao status -v` uses, in
    # the host's local clock. Empty for every session nothing will stop, which is most of them.
    d["stop_note"] = stop_note(s)
    d["start_note"] = start_note(s)  # §6 *Start time*, §4.5a **starts** note (TD-152): a scheduled record's
    d["gated"] = gated_view(s.get("gated"))  # the usage gate's pause (§6, TD-100): a mark, never a state
    d["brief_unsent"] = brief_unsent(s)  # §4.5a **brief not sent** (TD-339): the slot's and Focus's mark
    d["grants_all"] = list(GRANTS)
    # The Focus header's mode toggle, under the name of what it does (design §4.5a, TD-096): Take
    # over an unattended session; hand an interactive one back where there is someone to hand it
    # to — its `controllers` as written, or a team — and otherwise just switch it.
    if s.get("unattended"):
        d["mode_act"] = "Take over"
        d["mode_title"] = (
            "you are watching: the terminal is read-only. Take over switches this session to interactive "
            "and gives you the keyboard — its controllers and the policies leave it alone until you hand it back"
        )
    else:
        d["mode_act"] = "Hand back" if s.get("controllers") or s.get("team") else "Switch to unattended"
        d["mode_title"] = (
            "switch this session to unattended: the terminal goes read-only, and its manager and the policies "
            "pick it up again. A stop time that passed while you held it is cleared; one still ahead stays"
        )
    # Membership, both directions (design §4.8, §4.5a, TD-036 step 3). `controllers` is on the
    # record; `members` is derived across the records on every render and never stored — the same
    # rule `ao status -v` follows, so the page and the CLI cannot disagree. A controller whose
    # session is gone keeps its entry and shows as its bare id: §4.8 surfaces it rather than
    # silently releasing the worker.
    by_id = {o.get("id"): o for o in (fleet or [])}
    # `controllers` stays exactly as the record has it — a list of ids, the same shape
    # `ao status --json` prints. The display form goes under its own name, so nothing downstream
    # has to know which of two shapes it was handed (review 2026-09-13).
    d["under"] = [
        {"id": c, "name": (by_id.get(c) or {}).get("name") or c, "gone": c not in by_id}
        for c in (s.get("controllers") or [])
    ]
    d["members"] = [
        {
            "id": o["id"],
            "name": o.get("name") or o["id"],
            "state": o.get("state"),
            "lane": ", ".join(o.get("lane") or []),
            "report": report_line(o, pr_marks(o, repos)),
        }
        for o in (fleet or [])
        if s.get("id") in (o.get("controllers") or [])
    ]
    # *under `<manager>`* is not drawn inside a team's own group when that manager is the only
    # controller (§4.5a, TD-095): the group says it. The card cannot know which group it is drawn
    # in, so it marks the chip and the stylesheet hides it there — a filtered grid still shows it.
    only = by_id.get(d["under"][0]["id"]) if len(d["under"]) == 1 else None
    d["under_is_manager"] = bool(
        only and s.get("team") and only.get("team") == s.get("team") and has_control(only.get("capabilities"))
    )
    d["holds_control"] = has_control(s.get("capabilities"))
    # `fleet_known=False`: the caller asked for the fleet and did not get it. An empty members list
    # then means *unknown*, and Ready to close must not read it as *none* (review of PR #195).
    d["ready"] = ready_to_close(s, d["members"] if fleet_known else None)
    d["ready_ok"] = bool(d["ready"]) and all(ok for _, ok in d["ready"])
    d["not_ready"] = [name for name, ok in d["ready"] if not ok]  # what *more ▾ → Close* says it waits on
    # §6 rule 4 (TD-103): nudged once in this idle stretch and still idle another twenty minutes
    # later — the host agent is done, and it is for a person or its manager to judge
    # `idle_open` on the record is that reading, the tick's (§6 rule 3, TD-259): the slot, the seat's
    # trigger and the Inbox row all draw from it, and the page derives nothing of its own.
    d["open_work"] = state == "idle" and isinstance(s.get("idle_open"), dict)
    # how long a closed card stays (§4.5 row 6, TD-266): said only where the reap's own clock is set
    d["closed_keep"] = closed_keep() if state == "closed" and s.get("closed_at") else ""
    # who closed it (§4.5 row 5 (b), TD-265): a session named by its name where the fleet holds it
    names = {str(o.get("id")): str(o.get("name") or "") for o in fleet or []}
    d["closer_text"] = closer_words(s, names)
    # an exit, and how (§4.5 row 5 (b), TD-490): the record's `ended` in `ending.exit_words`' words
    d["exit_text"] = exit_words(s, names, now) if state == "exited" else ""
    # the ending with its time: the pill's hover where nothing outranks it, and always the Focus end banner's first line
    d["ending_text"] = ending_hover(s, names, now)
    d["pill_title"] = pill_title(d, d["ending_text"])
    d["slot"] = card_slot(d)
    d["next_act"] = next_act(d)
    return d


def pill_title(d: Mapping[str, Any], ending: str = "") -> str:
    """design §4.5a **state pill hover** (TD-490): what the state rests on, one text, the first that
    applies — an unreachable host's reason, a *waiting* pill's reason, a seat on call, the ending on
    `exited` and `closed` (row 5 (b)'s words and the time: both are the tick's own readings, never
    *guessed*), *guessed from the screen* on a `scraped` state, *reported by the tool* on a `hook` one,
    and on another state of the tick's own (`stalled?`) that the host agent observed it (§4.2). The
    card, the Inbox row and the Focus header draw this one text."""
    if d.get("host_note"):
        return str(d["host_note"])
    if d.get("state_class") == "waiting":
        return f"{d.get('wait_reason') or ''} — idle in every payload, on someone else’s move"
    if d.get("seat"):
        return "a seat on call: read from its record, not from a screen"
    if ending:
        return ending
    if d.get("scraped"):
        return "guessed from the screen"
    if d.get("confidence") == "tick":
        return "observed by the host agent, not reported by the tool"
    return "reported by the tool"


def _clock(iso: Any) -> str:
    """An instant as the host's local clock, *14:00*, with the day once it is not today (*Thu
    14:00*) — `stop_note`'s form, so a time on a card reads the same wherever it is. "" for anything
    unreadable, which costs a line its time and never the page."""
    at = _instant(iso)
    if at is None:
        return ""
    at = at.astimezone()
    return ("" if at.date() == datetime.now().astimezone().date() else at.strftime("%a ")) + f"{at:%H:%M}"


def brief_unsent(s: Mapping[str, Any]) -> dict[str, str] | None:
    """design §4.5a **brief not sent** (§4.1 *No prose in the argv*, TD-339): the host agent typed the
    brief at the composer `FIRST_PROMPT_TRIES` times and the composer did not take it, or no hook
    reached the record by `FIRST_PROMPT_BOUND` (TD-348) — *brief not sent · <reason>*, with the cure
    on hover. None while it is not so, and on an ended record."""
    err = s.get("first_prompt_error")
    if not err or s.get("state") in ("exited", "closed"):
        return None
    text = f"brief not sent · {err}"
    # §4.1 *A brief whose first hook is lost* (TD-348): no hook reached the record within the bound
    what = (
        "no hook reached this session within five minutes of its launch, so its brief was never sent"
        if err == "no hook since launch"
        else "the host agent typed this session's brief at its composer and the composer did not take it"
    )
    full = f"{text}: {what} — send it (Focus, or ao send); the mark goes with the next prompt"
    return {"text": text, "full": full}


def gated_view(raw: Any) -> dict[str, str] | None:
    """design §4.5a **paused · usage** mark (§6 *Usage gate*, TD-100 slice 3): the record's `gated`
    as the words a card's slot and the Focus header show — *paused · usage — grind week 75% ≥ 70%,
    line moves 14:00 · pause sent* — composed from the mark's own fields, never from anything the
    session said. None when there is no mark, or none this can read: one malformed record costs its
    card the mark and not the grid. A mark, not pressable; it goes when the resume send clears it."""
    if not isinstance(raw, dict):
        return None
    pct, line = raw.get("pct"), raw.get("line")
    if any(isinstance(n, bool) or not isinstance(n, int | float) for n in (pct, line)):
        return None  # a bool is an int to isinstance, and `True%` is no reading
    prof = str(raw.get("profile") or "default")
    # under a team's reserve priority (§6, TD-146) the line is the team's, and the words say whose
    te = raw.get("team_extra") if isinstance(raw.get("team_extra"), dict) else {}
    n = te.get("n")
    team = f" ({te.get('team')} +{n})" if te.get("team") and isinstance(n, int) and not isinstance(n, bool) else ""
    text = f"paused · usage{team} — {prof} {raw.get('label') or '?'} {pct:g}% ≥ {line:g}%"
    if nxt := _clock(raw.get("next")):
        # a flat reserve's line moves only at the reset, where the honest word is *resets* (§4.5a)
        same = _instant(raw.get("next")) is not None and _instant(raw.get("next")) == _instant(raw.get("resets"))
        text += f", {'resets' if same else 'line moves'} {nxt}"
    if raw.get("sent_at"):
        text += " · pause sent"
    since = _clock(raw.get("since"))
    full = (
        f"{text}. Paused by the usage gate{f' since {since}' if since else ''}: the profile crossed the line "
        "its reserve makes (design §6), so the session was asked to pause"
        + ("" if raw.get("sent_at") else " — the ask is not typed yet, it waits for a clear composer")
        + ". It resumes by itself when every window is back under its line; to go on now, Take over, "
        + (
            f"or lower the reserve with `ao gate`, or {te.get('team')}'s priority in settings.yml "
            f"(teams.{te.get('team')}.reserve — the line is the profile's less {n})."
            if team
            else "or lower the reserve with `ao gate`."
        )
    )
    return {"text": text, "full": full}


BRANCH_SHOWN = 34  # characters of row 3's branch a card shows before it shortens it in the middle


def _middle(text: str, width: int) -> str:
    """`text` shortened in the middle to `width` characters, so both ends read — a branch is told
    apart by its prefix (`td095-`) and its end (`-rows`) alike. Whole when it fits."""
    if len(text) <= width:
        return text
    keep = width - 1
    return f"{text[: keep - keep // 2]}…{text[len(text) - keep // 2 :]}"


# design §4.5a team card **flow changed — Apply** (§4.9c *Switching*, TD-309): what the page read of each
# team's flow when it was last drawn — the flow it runs, by team, and the members whose records differ
# from it, by id, each `act` (`teamrun.differences`). Set by the app's `flow_views`, read by `flow_mark`
# on every card a delta draws, since a delta reads no plan (as `inbox.PULLS` is)
FLOWS: dict[str, str] = {}
FLOW_ACTS: dict[str, str] = {}
NODE_RESTART = "flow changed — restarts when it next works or at the team's next Start"


def flow_mark(s: Mapping[str, Any], here: str = "") -> dict[str, str] | None:
    """A member's flow mark (design §4.5a *flow changed — Apply*, §4.9c *Switching*): *sits out under
    <flow>* on a member the flow sits out — winding down (`sit_out`) or closed for it (`closed_for:
    {why: sit_out}`), its card kept until Forget, unless the flow now starts it again — and *— close it
    yourself* on a person's session it would sit out, which nothing winds down; *flow changed* on a
    node member whose host is not answering, which Apply skips; and on an
    idle node member relaunched, which the tick does not restart, *flow changed — restarts when it
    next works or at the team's next Start*. None otherwise; a mark, never pressable."""
    flow = FLOWS.get(str(s.get("team") or "")) or "its flow"
    closed_for = s.get("closed_for") if isinstance(s.get("closed_for"), dict) else {}
    act = FLOW_ACTS.get(str(s.get("id") or ""))
    node = bool(here and s.get("host") and s.get("host") != here)
    if (s.get("sit_out") or closed_for.get("why") == "sit_out") and act != "start":  # one the flow starts again: no
        text = f"sits out under {flow}"
        why = "the team's flow has no stage for its role — its card stays until Forget"
        return {"text": text, "full": f"{text}: {why}", "first": True}  # ahead of `doing`: winding down is what it does
    if act == "left":
        text = f"sits out under {flow} — close it yourself"
        return {"text": text, "full": f"{text}: a person's session is never wound down by a switch (design §4.9c)"}
    if node and act and s.get("state") == "unreachable":  # Apply skipped it: its host was not answering
        why = f"its record differs from {flow} — Apply on the team card"
        return {"text": "flow changed", "full": f"flow changed: {why}"}
    if node and s.get("relaunch") and s.get("state") == "idle":
        return {"text": NODE_RESTART, "full": f"{NODE_RESTART}: the tick does not restart a member on a node"}
    return None


def restarted_view(restarts: Any, now: datetime) -> dict[str, str] | None:
    """The **restarted** chip (design §4.5a, TD-485, TD-487): the `restarts` entries younger than
    `RESTART_WINDOW` whose `why` is a restart — every one but `start`, a scheduled start's own create,
    and `fill`, a seat's. `text` is the Focus header's, *restarted ·* and the newest entry in
    `ending.restart_words`' words, as `ao status -v` says it; `short` the card's, the why's words alone
    (*restarted · cache lapsed*); `hover` every entry in the window, newest first, in the full words
    with its age, and the count toward the ceiling by the ceiling's own rule (*2 of 3 in 2 h*); `until`
    when the window passes the newest entry, so the chip and the ceiling keep one clock. None when no
    entry is one; a malformed entry is passed over."""
    entries = [r for r in restarts if isinstance(r, dict)] if isinstance(restarts, list) else []
    window = []
    for r in entries:
        at = _instant(r.get("at"))
        if at is not None and now - at < RESTART_WINDOW and r.get("why") not in ("start", "fill"):
            window.append((at, r))
    if not window:
        return None
    window.sort(key=lambda p: p[0], reverse=True)
    newest = window[0][1]
    short = "cache lapsed" if newest.get("why") == "cache" else str(newest.get("why") or "") or "restarted"
    hours = round(RESTART_WINDOW.total_seconds() / 3600)
    lines = [f"{restart_words(r)}, {_age(r.get('at'), now)} ago" for _, r in window]
    try:
        counted = len(_counted(entries, now))
    except (TypeError, ValueError, AttributeError):  # a malformed `done`: this card's count, not the grid
        counted = len(window)
    lines.append(f"{counted} of {RESTART_CEILING} in {hours} h")
    return {
        "short": f"restarted · {short}",
        "text": f"restarted · {restart_words(newest)}",
        "hover": "\n".join(lines),
        "until": (window[0][0] + RESTART_WINDOW).isoformat(),  # the page hides it then, delta or not
    }


def brief_changed_view(bc: Any) -> dict[str, str] | None:
    """Design §4.5a **brief changed** chip (§6 rule 7, TD-217): the fixed words, and on hover the
    files that changed by name and when — paths the home read, nothing a session wrote. A mark,
    never pressable, and not a state; a malformed field costs the chip and not the grid."""
    if not isinstance(bc, dict) or not bc.get("at"):
        return None
    try:
        when = datetime.fromisoformat(str(bc["at"]).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        when = "?"
    paths = bc.get("paths") if isinstance(bc.get("paths"), list) else []
    names = ", ".join(Path(str(p)).name for p in paths) or "its files"
    return {
        "text": "brief changed",
        "full": (
            f"brief changed — {names} · changed {when}: a file its brief was made from reads otherwise as "
            "merged; it takes the new brief when it is next started (design §6 rule 7)"
        ),
    }


def waits_of(entries: Any) -> dict[str, list[dict[str, Any]]]:
    """What each sender waits on, read from the person inbox the page holds (design §4.5a **waiting**
    mark, TD-274): `work.waiting_of`'s `{id, ref, bound}` per question, keyed by the entry's sender,
    each with `text`, the question's first paragraph, for the hover — the page's own read, since the
    home's `host` reading carries no question's text (§4.9a)."""
    es = [e for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else []
    texts = {e.get("id"): str(e.get("text") or "").strip().split("\n\n")[0].strip() for e in es}
    out = work.waiting_of(es)
    for qs in out.values():
        for q in qs:
            q["text"] = texts.get(q["id"]) or ""
    return out


def review_wait(
    s: Mapping[str, Any], fleet: Collection[Mapping[str, Any]] | None, repos: Mapping[str, Any] | None
) -> str:
    """What a claim of the record waits on in review (§4.2 *Waiting*, TD-428): *review #1302* when a
    `claimed` entry's PR — its own `pr`, the tick's `review_pr`, or else an open PR whose head branch
    names the reference, the *review* phase's reading (`org.motion_rows`) — is one the repo reading
    holds open; *review #1302 with the techlead* while a seat's `prs_waiting.asks` holds this
    record's ask for that PR, the seat named by its role. "" when it waits on no review, and with no
    reading: never guessed."""
    want = str(Path(str(s.get("repo") or "")).resolve()) if s.get("repo") else None
    # the record's repo's reading, `pr_marks`' lookup
    r = next((r for root, r in (repos or {}).items() if want and str(Path(str(root)).resolve()) == want), None)
    opened = [
        p for p in ((r if isinstance(r, dict) else {}).get("prs") or {}).get("open") or [] if isinstance(p, Mapping)
    ]
    numbers = {p["number"] for p in opened if isinstance(p.get("number"), int)}
    by_branch: dict[str, int] = {}
    for p in opened:
        if isinstance(p.get("number"), int) and (ref := branch_ref(p.get("branch"))):
            by_branch.setdefault(ref, p["number"])
    pr = None
    for p in s.get("progress") or ():
        if not isinstance(p, Mapping) or p.get("status") != "claimed":
            continue
        got = p.get("pr") or p.get("review_pr") or by_branch.get(str(p.get("ref") or ""))
        if isinstance(got, int) and got in numbers:
            pr = got
    if pr is None:
        return ""
    me = str(s.get("id") or "")
    for o in fleet or ():
        w = o.get("prs_waiting")
        for a in (w.get("asks") or ()) if isinstance(w, Mapping) else ():
            if isinstance(a, Mapping) and a.get("pr") == pr and naming.split_address(str(a.get("from")))[0] == me:
                return f"review #{pr} with the {o.get('role') or o.get('name') or 'reader'}"
    return f"review #{pr}"


def waiting_view(questions: Any) -> dict[str, str] | None:
    """The **waiting** mark (§4.5a, TD-274) as the slot and the Focus chip draw it: `ending.waiting_words`
    for the text, and on hover the sooner question's first paragraph. None when it waits on nothing."""
    words = waiting_words(questions)
    if not words:
        return None
    first = next((q for q in questions if isinstance(q, Mapping) and q.get("ref")), {})
    asked = str(first.get("text") or "")
    return {"text": words, "full": f"{words} — {asked}" if asked else words}


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _declared(d: dict[str, Any]) -> tuple[str, str] | None:
    """A declaration (§4.9a) as the slot says it, `(text, hover)`: the fixed words, what it waits on
    where it waits (§4.5a **waiting**), then the first line of its reason; the whole reason and when
    it was said are the hover — a card holds one clock."""
    said = d["out_of_work"] or d["restart_wanted"]
    if not said:
        return None
    words = "out of work" if d["out_of_work"] else "restart wanted"
    if not d["out_of_work"] and said["early"]:
        words += (f" · repeats {said['repeat']}" if said["repeat"] else " · early") + " — for a person"
    why = said["why"]
    # what it waits on follows the fixed words, before the reason a card cuts (§4.5a **waiting**, TD-274)
    w = d.get("waiting")
    text = words + (f" · {w['text']}" if w else "") + (f" — {_first_line(why)}" if why else "")
    when = f" {said['age']} ago" if said["age"] else ""
    full = f"{words}{when} — {why or 'no reason recorded'}"
    if not d["out_of_work"] and said["early"]:
        full += f" — {said['decided'] or 'early'}, so a controller does not act on it (design §4.9a)"
    if w:
        full += f" · {w['full']}"
    return text, full


def seat_occupant(occupants: list[str], fleet: Collection[Mapping[str, Any]], now: datetime) -> str:
    """The New session form's words for a checkout a seat holds (§4.5a *New session: directory field →
    occupancy: a seat*, TD-387), or "": *anchor-ao-1 holds it (a seat, working · TD-299)* when the
    first holder the occupancy check names that is a seat — `<id> (<state>)`, `<id>@<host>` for a container node's —
    is a record the fleet holds with a `seat`, its claim the newest unexpired declared one. Display only."""
    seats = {str(r.get("id")): r for r in fleet if isinstance(r, Mapping) and isinstance(r.get("seat"), dict)}
    for o in occupants:
        sid, _, rest = str(o).partition(" ")
        if (rec := seats.get(sid.split("@", 1)[0])) is not None:
            break
    else:
        return ""
    state = rest.strip("()") or str(rec.get("state") or "")
    claims = [
        p
        for p in rec.get("progress") or ()
        if isinstance(p, Mapping) and p.get("status") == "claimed" and (p.get("source") or "declared") == "declared"
    ]
    live = [p for p in claims if (at := _instant(p.get("at"))) is not None and now - at < mail.LEASE_TTL]
    claim = max(live, key=lambda p: str(p.get("at") or ""), default=None)
    what = f"a seat, {state}" + (f" · {claim['ref']}" if claim and claim.get("ref") else "")
    return f"{rec.get('name') or sid} holds it ({what})"


def seat_held_words(held: Mapping[str, Any]) -> str:
    """Why an anchor seat's fill waits, in the card's words (§4.5a *card: on call — the anchor seat's
    words*), from the record's `seat_held` (TD-386): *a session holds it: <who>* where `by` is a
    session, else the tree's own reading as the tick wrote it (*branch td-x, 2 files uncommitted*)."""
    by, why = str(held.get("by") or ""), str(held.get("why") or "")
    if by and by != "checkout":
        return f"a session holds it: {by}"
    return why or "the checkout is not clean"


def card_slot(d: dict[str, Any]) -> dict[str, Any]:
    """The card's slot (design §4.5 *The card's anatomy*, row 5; §4.5a **doing**, TD-095): **one
    text, the first that applies**, and a caption. (a) what needs a person or explains a stop, (b)
    an ending — exited, closed, or a declaration — (c) what the session says it is doing, then the
    *brief changed* mark where it says nothing, (d) its last output. The caption: the time a pending
    answer has left, else *ready to close ✓* whenever the checklist passes, else *says · age* under a
    `doing` line. `text` is a session's or a tool's
    words: escaped by the template, shown, never a control.

    `kind` picks the rule's colour (`needs`, `lim`, `bad`, `ok`, `doing`, `tail`, or "") and `full`
    is the hover; a working pane's two lines keep their line break (`tail`)."""
    state, pend = d["state"], d["pending"]
    ptext = str(pend.get("text") or "")
    kind, text, full = "", "", ""
    if state == "needs-you" and pend:
        # a hook permission says its tool and command; a question says that it is one
        kind = "needs"
        text = ptext if pend.get("kind") == "permission" else f"{pend.get('kind')}: {ptext}"
    elif state == "unreachable" and pend and pend.get("host_unreachable"):
        # design §4.4a "Permission prompts follow the same line": the waiter is on the node
        kind, text = "needs", f"{pend.get('kind')}: {ptext} — answer it at {d['host']}"
    elif state == "limited" and pend:
        kind, text = "lim", ptext
    elif state == "stalled?" and pend:
        # design §4.2: "a `stalled?` that can say why" — a screen rule's note (TD-032)
        kind, text = "needs", ptext
    elif state == "unreachable" and d["host_note"]:
        text = d["host_note"]
    elif d.get("gated"):
        # the usage gate's pause explains a stop (§4.5a **paused · usage**, TD-100); it waits behind
        # a permission, a question, a limit or a stall above, which are a person's to answer
        kind, text, full = "lim", d["gated"]["text"], d["gated"]["full"]
    elif d.get("brief_unsent"):
        # §4.5a **brief not sent** (§4.1 *No prose in the argv*, TD-339): live and idle with nothing to
        # do, amber as the design words it — the needs family's colour, never its ring
        kind, text, full = "needs", d["brief_unsent"]["text"], d["brief_unsent"]["full"]
    elif d.get("seat") and isinstance(d.get("restart_ceiling"), dict):
        # §6 rule 3's fill ceiling (TD-103): the seats sharing its controller were filled six times
        # in the hour, and this one's fill tripped it — an ending, as the crash ceiling's is
        n = d["restart_ceiling"].get("count")
        kind, text = "bad", f"fills exhausted · {n if isinstance(n, int) else '?'} in 1 h"
        full = (
            f"{text}: the host agent filled this seat and the ones beside it as often as it will "
            "(design §6) — it is yours now: Resume it, or Forget it"
        )
    elif d.get("seat"):
        # what would make it come (§4.5): the techlead's trigger is a question landing (§4.9b)
        # (§4.9b) — or a seat's own trigger: after n PRs, every so often (TD-098)
        text = f"on call — {d.get('seat_when') or 'comes on the next question'}"
        held = d.get("seat_held") if isinstance(d.get("seat_held"), dict) else None
        if d.get("seat_when") == ANCHOR_WHEN and held:
            # the anchor seat's fill held off (§6 rule 3 *The anchor seat*, TD-386): the checkout is the
            # person's while a session holds it or its tree is off its default branch or dirty
            text = f"on call — the checkout is yours · {seat_held_words(held)}"
            full = (
                f"{text}: its lane has work, and the host agent fills the seat once the checkout is free — "
                "no session in it, on its default branch, nothing uncommitted (design §6)"
            )
        elif d.get("seat_when") == ANCHOR_WHEN:
            # the anchor seat (§4.9b *The anchor seat*, §6 rule 3's `work` trigger, TD-381)
            full = (
                f"{text}: new work in its checkout's lane fills the seat, in the checkout itself, and it ends "
                "again once it has run (design §6)"
            )
        elif d.get("seat_when") == MANAGER_WHEN:
            # a manager on call (§4.9, §6 rule 3's `team` trigger, TD-259)
            full = (
                f"{text}: a question to it, a member's permission, a stalled member or one idle with its work "
                "open fills the seat, and it ends again once it has acted (design §6)"
            )
        elif not d.get("seat_when") or d["seat_when"] == "comes on the next question":
            full = f"{text}: a question to it fills the seat, and it ends again once it has answered (design §4.9b)"
        else:
            full = f"{text}: the host agent fills the seat when that comes due, and it ends once it has run (§6)"
    elif state == "exited" and isinstance(d.get("restart_ceiling"), dict):
        # an ending (§4.5 row 5 (b), §6 *Keeping a team running* rule 1, TD-103): the tick restarted
        # it as often as it will, and the session is a person's now
        n = d["restart_ceiling"].get("count")
        kind, text = "bad", f"restarts exhausted · {n if isinstance(n, int) else '?'} in 2 h"
        full = (
            f"{text}: it exited on its own each time and the host agent restarted it, up to its ceiling "
            "(design §6) — it is yours now: Resume it, or Forget it"
        )
    elif state in ("exited", "closed"):
        if state == "exited":
            # an exit, and how (TD-490): `ending.exit_words`, and the time on hover as the pill says it
            ended = d.get("ended") if isinstance(d.get("ended"), dict) else {}
            code = ended.get("code") if ended else d.get("exit_code")
            kind, text = ("bad" if code else ""), d.get("exit_text") or "exited"
            full = d.get("pill_title") if str(d.get("pill_title") or "").startswith(text) else text
        else:
            # who closed it (§4.5 row 5 (b), TD-265): the record's closer in `ending.closer_words`' words
            kind, text = "ok", d["closer_text"]
            full = f"{text} at {d['closed_at']} · {d['closed_keep']}" if d.get("closed_at") else text
            if text == "closed":
                full += f" — {NO_CLOSER}"
            if fm := d.get("flow_mark"):  # *sits out under <flow>*: the card stays until Forget (§4.5a)
                text, full = fm["text"], f"{fm['full']} · {full}"
        if said := _declared(d):
            # a record that declared keeps the declaration after its ending: it is what explains it
            text, full = f"{text} — {said[0]}", f"{full} — {said[1]}"
        elif w := d.get("waiting"):  # what it waits on, where it declared nothing (§4.5a **waiting**, TD-274)
            text, full = f"{text} · {w['text']}", f"{full} · {w['full']}"
    elif said := _declared(d):
        text, full = said
    elif d.get("waiting"):
        # alone, on a session that declared nothing: still an ending's place, row 5 (b)
        text, full = d["waiting"]["text"], d["waiting"]["full"]
    elif d.get("review_wait"):
        # the *waiting* pill's reason (§4.2 *Waiting*, TD-428): its claim's PR is open, in review
        text = f"waiting · {d['review_wait']}"
        full = f"{text}: it holds a claim whose pull request is open — idle on someone else's move (design §4.2)"
    elif d.get("open_work"):
        kind, text = "lim", "idle · open work"
        full = (
            "idle with its work open: the host agent nudged it once, twenty minutes into this stretch, and it "
            "is still idle — yours or its manager's to judge (design §6)"
        )
    elif (d.get("flow_mark") or {}).get("first"):
        # winding down for a switch (§4.5a **flow changed — Apply**, TD-309): what it is doing now is that
        text, full = d["flow_mark"]["text"], d["flow_mark"]["full"]
    elif d["doing"]:
        kind, text = "doing", d["doing"]["text"]
    elif d.get("flow_mark"):
        # the flow's other marks (TD-309): a mark, not a state, so only where no `doing` line stands
        text, full = d["flow_mark"]["text"], d["flow_mark"]["full"]
    elif d.get("brief_changed"):
        # rule 7's mark (§4.5a **brief changed**, TD-217): not an ending — it stands on a working member
        # — so it takes the slot only where no `doing` line does, in place of the tail
        text, full = d["brief_changed"]["text"], d["brief_changed"]["full"]
    elif state in ("working", "stalled?"):
        # the pane's last two lines, as they stand — for a shell or a command run that is the work
        tail = [str(line) for line in (d.get("tail") or [])[-2:] if str(line).strip()]
        kind, text = "tail", "\n".join(tail) or "at prompt"
    else:
        tail_last = (d.get("tail") or [""])[-1]
        text = f"last: {tail_last}" if tail_last else "at prompt"
    caption, ccls = "", ""
    if state == "needs-you" and pend.get("kind") == "permission" and pend.get("tool_use_id"):
        caption, ccls = "via hook", "countdown"  # the page's clock fills in the time left
    elif d.get("seat"):
        # never *ready to close ✓*: a seat is not closed while the definition names it (§4.5)
        # *last ran* for a seat with a trigger: it runs its brief rather than answering (§4.5, TD-098)
        came = "last came" if d.get("seat_when") in ("", "comes on the next question", MANAGER_WHEN) else "last ran"
        caption = came + (f" · {d['came_age']} ago" if d.get("came_age") else "")
    elif d["ready_ok"] and state in ("idle", "exited"):
        caption, ccls = "ready to close ✓", "ready"
    elif kind == "doing":
        caption = "says" + (f" · {d['doing']['age']} ago" if d["doing"]["age"] else "")
    return {"kind": kind, "text": text, "full": full or text, "caption": caption, "ccls": ccls}


def closed_keep() -> str:
    """*forgotten by itself a day after the close* — the fixed words of a closed card's hover and the
    Details banner (§4.5 row 6, TD-266), the day read from `CLOSED_KEEP`, the reap's one source."""
    days = CLOSED_KEEP / timedelta(days=1)
    span = "a day" if days == 1 else f"{days:g} days"
    return f"forgotten by itself {span} after the close"


def next_act(d: dict[str, Any]) -> str:
    """The foot's first button, by state (design §4.5 *The card's anatomy*, row 6, TD-095): what a
    person would press next. `allow` (with Deny beside it) for a hook permission; `forget` for an
    exited or a closed session, ready to close or not — there is no process left to close (TD-266);
    `close` for an idle session the checklist passes **when it is the person's own** (§4.5 *Whose session it is*,
    TD-156: an unattended team member is closed by its team, and its Close stays in *more ▾*);
    `details` when the pane is gone; else `focus`. A `limited`
    session's *Switch profile…* / *Wait* have no route yet, so it falls to Focus. A seat on call
    (TD-097) → `message`: asking it is how it comes, and it is never Forget or Close session."""
    state, pend = d["state"], d["pending"]
    if state == "needs-you" and pend.get("kind") == "permission" and pend.get("tool_use_id"):
        return "allow"
    if d.get("seat"):
        return "message"
    if state in ("exited", "closed"):
        return "forget"  # nothing is left to close on either (TD-266)
    if state == "idle" and d["ready_ok"] and d.get("own", True):
        return "close"
    if d.get("pane") is False:
        return "details"
    return "focus"


def ready_to_close(s: dict[str, Any], members: list[dict[str, Any]] | None = ()) -> list[tuple[str, bool]]:
    """Phase 1 subset of the checklist (design §4.2): tree clean, branch pushed, no subagents — and,
    for a session other sessions list as a controller, no live member. That last one comes from the
    control graph, not from a role: it covers a lead, a director over leads, and a session attached
    by hand with `ao control`, and a session that controls nothing never sees it. A lead idle
    between rounds with its log pushed used to read *ready to close ✓* over three working members,
    one click from orphaning them (seen 2026-09-17)."""
    git = s.get("git") or {}
    checks = []
    if s.get("dir") and git:
        checks.append(("tree clean", git.get("dirty", 0) == 0))
        # one measure, computed by the host agent and read here (design §4.2, TD-080): a branch
        # with no upstream is no longer *not pushed* by definition — rule 3 looks for the commit
        # on the remote-tracking branches, which is what a merged worker on a detached HEAD needs
        pushed = git.get("unpushed", 0) == 0
        label = (
            "branch pushed"
            if pushed or not git.get("pushed_against")
            else f"branch pushed (vs {git['pushed_against']})"
        )  # noqa: E501
        checks.append((label, pushed))
    checks.append(("no subagents running", (s.get("subagents") or 0) == 0))
    # design §4.2 / §4.10 *Outcomes* (TD-079): the person answered this session's question and has
    # not been told what came of it. `ao progress none` is refused on the same fact; this row is
    # for a session that exits some other way and never declares anything.
    owed = (s.get("mail") or {}).get("owed") or []
    checks.append((f"outcomes reported ({len(owed)} owed)" if owed else "outcomes reported", not owed))
    # Design §4.2, §4.9a (TD-072, TD-141): mail nobody read is mail nobody triaged. `ao progress
    # none` refuses on the same fact; this row is for a session that exits some other way.
    unread = s.get("unread") or 0
    checks.append((f"mail read ({unread} unread — `ao inbox`)" if unread else "mail read", not unread))
    if members is None:
        checks.append(("members unknown — the host agent did not list the sessions; reload", False))
    elif members:
        up = [m["name"] for m in members if m.get("state") not in DEAD]
        label = f"no live members ({', '.join(up)} — stop the team first)" if up else "no live members"
        checks.append((label, not up))
    return checks


DEFS_TTL = 5.0  # seconds the events stream keeps the team definitions it read (design §4.5a)
NO_TEAM = ""  # the group key for sessions carrying no `team` badge; rendered as *No team*, last
DEAD = ("exited", "closed")


def card_order(v: dict[str, Any]) -> tuple[float, bool, str]:
    """The grid's one order (design §4.5 *One order, no control*): urgency first, then — within one
    urgency — an `interactive` session ahead of an unattended one (TD-095, second pass: the person's
    own are what a person looks for), then the name. The manager's card is placed first before any
    of this, by `team_groups` and by the page's layout."""
    return (v["rank"], bool(v.get("unattended")), str(v.get("name") or ""))


# The header's counts, in the order the grid sorts by (§4.5 *One order*). *needs you* is not among
# them: the header carries it as its ringed mark, which is what a person scans a page of headers for.
COUNT_ORDER = (
    ("limited", "limited"),
    ("stalled?", "stalled?"),
    ("unreachable", "unreachable"),
    ("working", "working"),
    ("waiting", "waiting"),
    ("unseen", "unseen"),
    ("idle", "idle"),
    ("oncall", "on call"),
    ("exited", "exited"),
    ("closed", "closed"),
)


def state_counts(members: list[dict[str, Any]]) -> list[str]:
    """A team header's counts by state — `["1 limited", "2 working", "1 unseen"]`, in urgency order,
    zeros left out. An idle session nobody has looked at counts as *unseen*, and a seat with nobody
    in it as *on call* (TD-097), as their pills say."""
    tally: dict[str, int] = {}
    for m in members:
        key = "unseen" if m.get("unseen") else "oncall" if m.get("seat") else str(m.get("state") or "")
        key = "waiting" if m.get("pill_word") == "waiting" else key
        tally[key] = tally.get(key, 0) + 1
    return [f"{tally[k]} {label}" for k, label in COUNT_ORDER if tally.get(k)]


def group_place(members: list[dict[str, Any]]) -> str:
    """Where a team's sessions are, said once in its header so no card has to (TD-095): the
    `host / repo` they share, or *mixed* when they do not. Empty for a group with no sessions."""
    places = {str(m.get("place") or "") for m in members}
    return "" if not places else places.pop() if len(places) == 1 else "mixed"


def prs_waiting(members: Collection[dict[str, Any]], now: datetime | None = None) -> dict[str, Any] | None:
    """Design §4.5a **team header** → *PRs waiting* (§4.9b *The reader*, TD-093): the held PRs
    put in front of the team's reader and not yet answered, from each record's `prs_waiting` — the
    seat's, in practice — as `{n, age, by}` of the oldest. A count and a time, never the entries; `by`
    names the seat by its role, as its card is titled (*the techlead*, §4.5 *The page names a seat by
    its role*). None when nothing waits, or when no record carries the field (a host agent older than it)."""
    holding = [m for m in members if isinstance(m.get("prs_waiting"), dict)]
    holding = [m for m in holding if isinstance(m["prs_waiting"].get("n"), int)]
    got = [m["prs_waiting"] for m in holding]
    n = sum(int(w.get("n") or 0) for w in got)
    if n <= 0:
        return None
    oldest = min((str(w["oldest"]) for w in got if w.get("oldest")), default="")
    by = sorted({str(m.get("role") or m.get("name") or "seat") for m in holding if m["prs_waiting"].get("n")})
    return {"n": n, "age": _age(oldest, now or datetime.now(UTC)), "by": " and the ".join(by)}
