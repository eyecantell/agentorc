"""Mail between sessions (TD-108 step 1): the host agent's half of design §4.10 — `msg`, `inbox`, threads,
kinds, bounds, outcomes, the person inbox's writes — as a mixin `HostAgent` inherits. Moved as written; the
state it reads is the agent's.
"""

from __future__ import annotations

import contextlib
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sessionorc import (
    adapters,
    agent_common,
    mail,
)
from sessionorc.agent_common import (
    RpcError,
    _clean,
    _clean_answer,
    _oldest_first,
    _parse,
    _prune_tallies,
)
from sessionorc.models import (
    ASK_KINDS,
    MAIL_KINDS,
    PERSON,
    SYSTEM,
    VERDICTS,
    MailEntry,
    Session,
    Tally,
    now_iso,
)


def _shots(shots: Any, kind: str) -> list[str]:
    """A look's screenshots (design §4.10 *A look*, TD-292), checked whole before anything else is
    done with them: a list of repo-relative paths, each a `.png` directly under
    `docs/mockups/reviews/` (`mail.SHOT_RE`), at most `mail.SHOTS_MAX` once repeats are dropped, and
    only on a `steer` or an `ask`. Refused in words, never cleaned into another path: the page serves
    exactly that directory. Who it is addressed to is checked once the addressees are resolved."""
    if shots is None:
        return []
    raw = [shots] if isinstance(shots, str) else shots
    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
        raise RpcError("screenshots are a list of paths (design §4.10 *A look*)")
    if len(raw) > mail.SHOTS_MAX * 2:
        raise RpcError(f"a look names at most {mail.SHOTS_MAX} screenshots: {len(raw)} given (design §4.10)")
    looks: list[str] = []
    for x in raw:
        path = x.strip().removeprefix("./")
        if not mail.SHOT_RE.fullmatch(path):
            raise RpcError(
                f"{x[:120]!r} is not a screenshot a look can name: a .png directly under docs/mockups/reviews/ "
                "of your repo, its name letters, digits, '.', '_' and '-' (design §4.10 *A look*)"
            )
        if path not in looks:
            looks.append(path)
    if len(looks) > mail.SHOTS_MAX:
        raise RpcError(f"a look names at most {mail.SHOTS_MAX} screenshots: {len(looks)} given (design §4.10)")
    if looks and kind not in ("steer", "ask"):
        raise RpcError(f"screenshots ride only on a steer or an ask to the person, not a {kind} (design §4.10)")
    return looks


class MailMixin:
    # -- mail (design §4.10, TD-052 step 1) ------------------------------------------------------

    async def rpc_msg(
        self,
        text: str,
        to: list[str] | str | None = None,
        kind: str = "note",
        about: str | None = None,
        reply_to: str | None = None,
        bound: float | None = None,
        cites: list[str] | None = None,
        default: str | None = None,
        answers: list[str] | str | None = None,
        answer: Any = None,
        outcome: str | None = None,
        for_: str | None = None,
        thread: str | None = None,
        nonce: str | None = None,
        caller: Any = None,
        source: str | None = None,
        pr: Any = None,
        shots: Any = None,
        verdict: Any = None,
    ) -> dict[str, Any]:
        """`ao msg <to>… "…" [--kind] [--about] [--reply-to]` (design §4.10): put an attributed
        entry in each addressee's inbox. Nothing is typed anywhere. Gated by §4.10's graph, never
        by invariant 11 — messaging is not acting — and bounded as that section lists: a recipient
        cap on the addressees the sender named, all or nothing across them, a copy to the other
        controllers of the session it is `about` (exempt from both), the exchange bound per thread
        and per pair, the mailbox depth, the body cap, and an `ask`'s wall-clock bound. `bound` is
        the `ask`'s, in seconds, else `mail.ASK_BOUND` — and an `ask` to the person carries none at
        all (§4.10 *What a person is asked*, 2026-09-19): `default` is a `steer`'s, required on it.
        `answers` are the sender's likely answers on a question, and `answer` the zero-based index
        of the one a reply picked (§4.10 *Suggested answers*, 2026-09-20, TD-070). A retry carrying
        the same `nonce` returns the first send's verdict. `source` is where a reply's answer is
        written down (§4.9b): such a reply is filed to the person as *answered for you*. The reply
        names what landed, what was copied, and what was forwarded to a resumed successor."""
        sender = PERSON if mail.is_person(caller) else str(caller)
        key = (sender, str(nonce)) if nonce else None
        if key and key in self._nonces:
            result, error = self._nonces[key]
            if error is not None:
                raise error
            return dict(result or {})
        try:
            result = await self._msg(
                sender,
                text,
                to,
                kind,
                about,
                reply_to,
                bound,
                cites,
                default,
                answers,
                answer,
                outcome,
                for_,
                thread,
                source,
                pr,
                shots,
                verdict,
            )
        except RpcError as e:
            if key:
                self._remember_nonce(key, (None, e))
            raise
        if key:
            self._remember_nonce(key, (result, None))
        return result

    @staticmethod
    def _question_to_take_up(me: Session | None, mid: str) -> MailEntry | None:
        """The caller's own **open** `ask` or `steer` to a session, which `--thread` takes to the
        person when that session has not answered it (design §4.9b *When it cannot answer*,
        `TECHLEAD_WAIT`) — or None when `mid` is no question of the caller's to a session, and the
        thread is the person's own (`_owing_question`). Refused: a question passed up already, since
        the person holds it; one already closed, since nothing waits on it."""
        held = [e for e in (me.outbox if me is not None else []) if e.id == mid and PERSON not in e.to]
        if not held:
            return None
        e = held[0]
        if e.kind not in ("ask", "steer"):
            raise RpcError(f"{mid} is a {e.kind}: only an ask or a steer is taken to the person on its thread")
        if e.passed_up:
            raise RpcError(
                f"{mid} was passed up at {e.passed_up}: the person holds it already — wait for their answer "
                "(design §4.9b)"
            )
        if not e.open:
            raise RpcError(
                f"{mid} is closed ({e.closed_reason or 'answered'}): nothing waits on it — read its answer with "
                "ao inbox, or ask the person afresh (design §4.9b)"
            )
        return e

    def _owing_question(self, sender: str, mid: str) -> MailEntry:
        """The entry `mid` settles, checked to owe an outcome from this caller (design §4.10
        *Outcomes*). Every refusal says which of the five it is, because the remedy differs:
        wait, nothing, ask again, nothing, and it is not yours.

        Two directions since TD-077 b. The caller's **own question to the person**, held in the
        person inbox — the original case — and work the person **handed** the caller, which lives
        in the caller's own inbox and is checked there first: it is the same debt and the same
        command, and the only difference is which way the work travelled."""
        s = self._graph().get(self._addr(sender))
        if s is not None:
            for e in s.inbox:
                if e.id == mid and e.handed:
                    if e.outcome:
                        raise RpcError(
                            f"{mid} is settled — its outcome is recorded as {e.outcome.get('state')}. If more is "
                            "needed, ask again on the thread: --thread <id> (design §4.10 *Outcomes*)"
                        )
                    return e
        held = [e for e in self.person_inbox if e.id == mid]
        if not held or held[0].from_ != sender:
            raise RpcError(
                f"the person inbox holds no question {mid} from {sender}: an outcome names your own question to "
                "the person, by the id `ao msg` printed when it landed (design §4.10 *Outcomes*)"
            )
        e = held[0]
        if e.kind not in ASK_KINDS:
            raise RpcError(f"{mid} is a {e.kind}: only a question to the person is answered, and only one owes back")
        if e.open:
            raise RpcError(f"{mid} has not been answered yet: there is nothing to report on it (design §4.10)")
        if e.outcome:
            raise RpcError(
                f"{mid} is settled — its outcome is recorded as {e.outcome.get('state')}. If more is needed, "
                "ask again on the thread: --thread <id> (design §4.10 *Outcomes*)"
            )
        if e.closed_reason not in ("replied", "go_with_it"):
            raise RpcError(
                f"{mid} closed as {e.closed_reason}: nothing is owed on a question nobody answered (design §4.10)"
            )
        return e

    def _remember_nonce(self, key: tuple[str, str], verdict: Any) -> None:
        self._nonces[key] = verdict
        while len(self._nonces) > mail.NONCES_KEEP:
            self._nonces.popitem(last=False)

    async def _msg(
        self,
        sender: str,
        text: str,
        to: list[str] | str | None,
        kind: str,
        about: str | None,
        reply_to: str | None,
        bound: float | None,
        cites: list[str] | None,
        default: str | None = None,
        answers: list[str] | str | None = None,
        answer: Any = None,
        outcome: str | None = None,
        for_: str | None = None,
        thread: str | None = None,
        source: str | None = None,
        pr: Any = None,
        shots: Any = None,
        verdict: Any = None,
    ) -> dict[str, Any]:
        """One message, every rule of §4.10 in the order it applies. Long on purpose: the order is
        the design (validate, resolve the thread, forward, gate all-or-nothing, cap, count, land).
        The records are the org's one graph (§4.4a, step 5): an addressee on another host is its
        record here, its inbox the home's copy — landed whether or not its link is up, and said so."""
        records = self._graph()
        if kind not in MAIL_KINDS:
            raise RpcError(f"unknown message kind {kind!r}; kinds are: {', '.join(MAIL_KINDS)}")
        if pr is not None:
            # §4.9b *The reader* (TD-093): a PR put in front of its reader is a question
            if kind != "ask":
                raise RpcError(f"a PR number rides only on an `ask` (design §4.9b *The reader*), not a {kind}")
            if isinstance(pr, bool) or not isinstance(pr, int) or pr < 1:
                raise RpcError(f"`pr` is a pull request's number, a positive integer, not {pr!r}")
        looks = _shots(shots, kind)
        text = str(text or "").strip()
        if not text:
            raise RpcError("a message needs a body")
        if len(text.encode()) > mail.TEXT_CAP:
            raise RpcError(
                f"message body over {mail.TEXT_CAP} bytes: cite a `sends` id or a reference instead (design §4.10)"
            )
        if sender == SYSTEM:
            raise RpcError(
                f"{SYSTEM!r} is the home's own name on a note about your message: no session sends as it (design §4.10)"
            )
        me = records.get(sender) if sender != PERSON else None
        if sender != PERSON and me is None:
            raise RpcError(f"{sender} cannot send mail: this host agent has no record of it (design §4.10)")
        named = [self._addr(x) for x in ([to] if isinstance(to, str) else list(to or [])) if str(x).strip()]
        named = list(dict.fromkeys(named))
        if PERSON in named and sender == PERSON:
            raise RpcError("the person inbox is how a session reaches a person; a person's own note is a board line")
        if SYSTEM in named:
            raise RpcError(
                f"{SYSTEM!r} is the home's own name on a note about your message: it addresses nobody (design §4.10)"
            )
        if looks and named != [PERSON]:
            raise RpcError(
                "screenshots ride only on a steer or an ask to the person: a look is the person's (design §4.10)"
            )
        # -- a `steer` carries the one line it will go with, cleaned and capped as a `doing` line --
        line = _clean(str(default or "").split("\n", 1)[0]).strip()[: mail.DEFAULT_CAP]
        if kind == "steer" and not line:
            raise RpcError(
                'a steer says what it will do unless told otherwise: --default "<the line you will go with>" '
                "(design §4.10)"
            )
        if kind != "steer" and line:
            raise RpcError(f"only a steer carries a default: {kind} says what it says (design §4.10)")
        # -- where a reply's answer is written down (§4.9b): one line, checked, never parsed ------
        src = None
        if source is not None:
            if not isinstance(source, str):
                raise RpcError("a source is one line of text (design §4.9b)")
            raw = source.strip()
            src = _clean(raw).strip()
            # every line break Python knows — `\r`, `\u2028` too — not only `\n`: `_clean` would
            # otherwise join two lines silently (review of PR #346)
            if not src or len(raw.splitlines()) > 1 or len(raw) > mail.SOURCE_CAP:
                raise RpcError(
                    f"a source is one line of at most {mail.SOURCE_CAP} characters: the file and section, or "
                    "the decision's date (design §4.9b)"
                )
            if not reply_to:
                raise RpcError("a source says where a reply's answer is written down: --reply-to <id> (design §4.9b)")
            if sender == PERSON:
                raise RpcError("a person's answer needs no source: it is the person's word (design §4.9b)")
        # -- the sender's likely answers: a field of its own, not counted toward `TEXT_CAP` --------
        # Design §4.10 *Suggested answers* (TD-070). Each is cleaned more strictly than displayed
        # text is (`_clean_answer`); one that cleans to nothing, or that repeats an earlier one
        # exactly (compared after cleaning, case-sensitively), is dropped; a fifth is refused.
        # RPC input is raw JSON from any local process, so the shape is checked and the work bounded
        # before any of it is cleaned: a list of strings, and no more of them than could ever matter
        # (`ANSWERS_MAX` kept plus as many again dropped as blanks or repeats).
        if answers is not None and not isinstance(answers, (str, list)):
            raise RpcError("answers must be a list of lines (design §4.10)")
        offered = [answers] if isinstance(answers, str) else list(answers or [])
        if any(not isinstance(a, str) for a in offered):
            raise RpcError("answers must be a list of lines (design §4.10)")
        if len(offered) > mail.ANSWERS_MAX * 2:
            raise RpcError(f"an ask carries at most four answers: {len(offered)} given (design §4.10)")
        picks: list[str] = []
        for raw in offered:
            one = _clean_answer(raw)
            if one and one not in picks:
                picks.append(one)
        if offered and kind not in ASK_KINDS:
            raise RpcError(f"only a question carries answers: a {kind} says what it says (design §4.10)")
        if len(picks) > mail.ANSWERS_MAX:  # the word below is `mail.ANSWERS_MAX`, which is 4
            raise RpcError(f"an ask carries at most four answers: {len(picks)} given (design §4.10)")
        # -- a reply belongs to its root's thread, and answers an entry the replier holds ----------
        replied: MailEntry | None = None
        copies: list[str] = []
        overrule = ""  # the answerer, when this replies to an *answered for you* FYI (§4.9b)
        if kind == "reply" and not reply_to:
            raise RpcError("a reply names the entry it answers: --reply-to <id> (design §4.10)")
        if reply_to:
            held = [e for e in me.inbox if e.id == reply_to] if me is not None else self._person_holds(reply_to)
            if not held:
                raise RpcError(
                    f"{sender} holds no entry {reply_to} in its inbox: a reply names one it was addressed or "
                    f"copied (design §4.10)"
                )
            replied = held[0]
            if replied.from_ == SYSTEM:
                raise RpcError(
                    "a system note reports what happened to your own message; there is nobody to reply to "
                    "(design §4.10)"
                )
            if sender == PERSON and replied.orphaned and not replied.open:
                raise RpcError(
                    f"{reply_to} is already closed ({replied.closed_reason}): its asker is gone (design §4.10)"
                )
            if sender == PERSON and replied.orphaned:
                # §4.10 *A question about a reference outlives its asker*: the answer goes to the
                # board and to whoever holds the reference, never to a record that is not there
                if kind != "reply" or named:
                    raise RpcError(f"{reply_to} is orphaned: the person answers it with a reply alone (design §4.10)")
                if answer is not None and (
                    isinstance(answer, bool)
                    or not isinstance(answer, int)
                    or not 0 <= answer < len(replied.answers)
                    or text != replied.answers[answer]
                ):
                    raise RpcError("that is not one of the suggested answers, word for word (design §4.10)")
                return await self._answer_orphan(replied, text, "replied", answer)
            if not named and replied.answered:
                # A reply to an *answered for you* FYI (§4.9b) — the Overrule path — goes to the
                # asker with a copy to the answerer, on the question's own thread: the ordinary
                # default below would send it to the answerer, who filed the FYI.
                named = [str(replied.answered.get("asker") or "")]
                overrule = str(replied.answered.get("answerer") or "")
            if not named:
                if replied.from_ == PERSON and sender == PERSON:
                    raise RpcError(f"{reply_to} is a person's own message: name the addressee")
                named = [replied.from_]  # a session answering a person answers into the person inbox
            # replies in a copied thread are copied to the same set (design §4.10): the thread's
            # copies — a copy that failed to land included, so the set is the one meant — and its
            # other addressees, so the other lead of a `conflict` sees how it was settled
            same_set = dict.fromkeys([*replied.copies, *replied.copies_failed, *replied.to, replied.from_])
            if overrule:
                same_set = {overrule: None}
            copies = [x for x in same_set if x not in (sender, PERSON, *named)]
        # -- a reply may *pick* one of the answers the entry it answers carries (§4.10, TD-070) ----
        # **The home checks it**: `answer` must index the `answers` of the entry `reply_to` names
        # and `text` must equal that answer exactly — or be that answer, a blank line, and the
        # replier's own words after it (`--pick <n> "text"`) — else the reply is refused — a session can call
        # this RPC directly, and a receiver must not be asked to trust an index the text does not
        # bear out. Everything else about the reply is unchanged: same gate, same tallies, same
        # close (`replied`), same wake.
        picked: int | None = None
        if answer is not None:
            no = "that is not one of the suggested answers"
            if replied is None:
                raise RpcError(f"{no}: an answer picks one on an entry — name it with --reply-to <id> (design §4.10)")
            if isinstance(answer, bool) or not isinstance(answer, int):
                raise RpcError(f"{no}: the index is a whole number, counted from 0 (design §4.10)")
            if not replied.answers:
                raise RpcError(f"{no}: {replied.id} carries no answers at all (design §4.10)")
            if not 0 <= answer < len(replied.answers):
                raise RpcError(f"{no}: {replied.id} carries {len(replied.answers)} of them (design §4.10)")
            said = replied.answers[answer]
            # `text` is stripped above, so words after the blank line are never blank
            if text != said and not text.startswith(said + "\n\n"):
                raise RpcError(f"{no}: the text of a picked answer is that answer, word for word (design §4.10)")
            picked = answer
        # -- a reader's verdict on a PR's ask (§4.9c, TD-315 slice 1): a word, never read from the text ----
        reads_pr = kind == "reply" and replied is not None and replied.kind == "ask" and replied.pr is not None
        three = " | ".join(VERDICTS)
        if verdict is not None:
            if not reads_pr:
                raise RpcError(
                    f"a verdict rides only on a reply to an ask that carries a PR (design §4.9c), not this {kind}"
                )
            if verdict not in VERDICTS:
                raise RpcError(f"a verdict is one of {three}, not {verdict!r} (design §4.9c)")
        elif reads_pr and sender != PERSON:
            raise RpcError(
                f"a reply to PR #{replied.pr}'s ask says what the read came to: --verdict {three} — pass, nothing "
                "against it and not merged; merged, you merged it; findings, the text says what (design §4.9c)"
            )
        if not named:
            raise RpcError("a message names its addressees: there is no broadcast (design §4.10)")
        if len(named) > mail.RECIPIENT_CAP:
            raise RpcError(
                f"{len(named)} addressees is more than the cap of {mail.RECIPIENT_CAP} (design §4.10: no broadcast)"
            )
        # -- what a person is asked (design §4.10, 2026-09-19): alone, unbounded, never a conflict --
        if PERSON in named:
            if kind == "conflict":
                raise RpcError(
                    "a conflict never names the person: it is put to your controllers, and if they cannot "
                    "settle it, ask the person about it with --kind ask (design §4.10)"
                )
            if kind in ("ask", "steer") and len(named) > 1:
                raise RpcError(
                    f"the person is asked alone: a {kind} naming the person names nobody else — send it to the "
                    f"person on its own, and a note to the others (design §4.10)"
                )
            if kind == "ask" and bound is not None:
                raise RpcError(
                    "an ask to the person carries no bound and never expires: send a steer with --default "
                    "<the line you will go with> --bound <seconds> if you can go on without an answer "
                    "(design §4.10)"
                )
        # -- outcomes: an answer is followed to what became of it (§4.10 *Outcomes*, TD-079) -------
        # `--outcome … --for <id>` settles a question the person answered; `--thread <id>` asks
        # again on the same thread and settles the first as `asked_again`. Both name an entry the
        # **home** verifies — unlike `--about`, which is free text nobody checks.
        settle: MailEntry | None = None
        taken: MailEntry | None = None  # the caller's own open question to a session, taken to the person
        onto: MailEntry | None = None  # a handed entry whose thread a question joins, its debt left standing
        state = str(outcome or "").strip()
        if state and not for_:
            raise RpcError("an outcome names the question it settles: --for <ask id> (design §4.10 *Outcomes*)")
        if for_ and not state:
            raise RpcError(
                "--for names the question an outcome settles: give it one, --outcome done|blocked|dropped "
                "(design §4.10 *Outcomes*)"
            )
        if state:
            if state not in mail.OUTCOME_STATES:
                raise RpcError(
                    f"unknown outcome {state!r}; an asker reports one of: {', '.join(mail.OUTCOME_STATES)} "
                    "(design §4.10 *Outcomes*)"
                )
            if kind != "note":
                raise RpcError(f"an outcome is a note about how the work went, not a {kind} (design §4.10)")
            if named != [PERSON]:
                raise RpcError(
                    "an outcome is reported to the person who answered: ao msg person --outcome … --for <id>"
                )
            settle = self._owing_question(sender, str(for_))
        if thread:
            if kind not in ("ask", "steer"):
                raise RpcError(
                    f"--thread asks again on a question's own thread: a {kind} settles nothing "
                    "(design §4.10 *Outcomes*)"
                )
            if named != [PERSON]:
                raise RpcError("--thread follows up a question put to the person: name `person` as the addressee")
            taken = self._question_to_take_up(me, str(thread))
            if taken is None:
                owed = self._owing_question(sender, str(thread))
                # a question about an entry the person handed the caller goes on its thread and settles
                # nothing: the entry is the person's, not a question of the caller's, and it still owes
                # its outcome (§4.10 *An entry handed to a seat*, TD-218 slice 3)
                if owed.handed_entry:
                    onto = owed
                else:
                    settle = owed
        # -- forwarding: a closed record a live one superseded hands its mail on -------------------
        forwarded: dict[str, str] = {}

        def follow(ids: list[str]) -> list[str]:
            resolved: list[str] = []
            for asked in ids:
                sid, seen = asked, {asked}
                while (r := records.get(sid)) is not None and r.state == "closed" and r.superseded_by:
                    sid = r.superseded_by
                    if sid in seen:
                        break
                    seen.add(sid)
                if sid != asked:
                    forwarded[asked] = sid  # the id the sender wrote → the record continuing it, however many hops
                if sid not in resolved:
                    resolved.append(sid)
            return resolved

        named = follow(named)
        # -- the gate, per addressee, all or nothing ------------------------------------------------
        for sid in named:
            if sid not in records and sid != PERSON:
                raise RpcError(f"no session {sid}")
            if (reason := mail.message_gate(records, sender, sid, controllers=self._ctl)) is not None:
                raise RpcError(reason)
        cited: list[str] = []
        if kind == "conflict":
            if len(named) < 2:
                raise RpcError("a conflict is an ask to two or more controllers at once (design §4.10)")
            cited = [str(c) for c in (cites or [])]
            known = {e.id for e in me.sends} if me is not None else set()
            if not cited or any(c not in known for c in cited):
                raise RpcError(
                    "a conflict cites the `sends` it cannot reconcile by id — the ids `ao status` prints for this "
                    "session (design §4.10)"
                )
        # -- copies: a controller's mail `about` its member reaches the member's other controllers --
        subject = records.get(self._addr(about)) if about and not reply_to and me is not None else None
        if subject is not None and sender in self._ctl(subject):
            copies = [c for c in self._ctl(subject) if c not in (sender, *named)]
        # a copy follows a resume as an addressee does: a passer, or a thread's other controller,
        # resumed since the thread began gets its copy on the record that continues it
        copies = [c for c in follow(copies) if c in records and c not in (sender, *named)]
        # -- what this message counts as ------------------------------------------------------------
        closes = replied is not None and kind == "reply" and replied.open
        counts = sender != PERSON and not closes  # a person's message is never counted; a first reply is free
        # A reporting note and a follow-up both belong to the **question's own thread** (design
        # §4.10 *Outcomes*): the person reads the answer and what came of it in one place, and the
        # thread's exchange bound counts them where they belong (review of PR #267).
        on_thread = settle or taken or onto
        root = on_thread.root if on_thread is not None else (replied.root if replied is not None else "")
        now = datetime.now(UTC)
        if counts and (mail.THREAD_BOUND is not None or mail.PAIR_BOUND is not None):
            self._check_bounds(sender, named, root, now)
        advice = None
        if PERSON in named:
            if kind in ("ask", "steer") and me is not None and mail.OUTCOMES_OWED_MAX is not None:
                # The debt's own bound (design §4.10 *Outcomes*): it never holds the sender's slot
                # in the person-inbox depths — a long night of answered questions must not cost a
                # worker the ability to ask — but an asker that has stopped reporting is stopped.
                owed = [x for x in me.owed() if x != (settle.id if settle is not None else None)]
                if len(owed) >= mail.OUTCOMES_OWED_MAX:
                    raise RpcError(
                        f"you owe {len(owed)} outcomes to the person: report them first "
                        f'(ao msg person --outcome done|blocked|dropped "<one line>" --for <id>): '
                        f"{', '.join(owed[: mail.OUTCOMES_OWED_MAX])} (design §4.10 *Outcomes*)",
                        owed=owed,
                    )
            self._check_person_depth(sender, question=kind in ASK_KINDS)
            if kind == "ask":
                # One line of advice from the home, not a gate — the per-sender depth is the gate
                # (design §4.10 "Which to send is the brief's to teach"): counted before this send.
                held = sum(1 for e in self.person_inbox if e.from_ == sender and e.kind == "ask" and e.open)
                if held >= mail.OPEN_ASK_ADVICE:
                    advice = f"you have {held} open asks to the person: is this one needed, or a steer?"
        # -- answered from the record: the person is told (§4.9b *Everything answered for the person
        # is told to the person*). A reply to the person needs no FYI — the person reads it. The FYI
        # is a message to the person like any other, so a full person inbox refuses the reply rather
        # than let an answer steer a team unseen.
        fyi = bool(src) and replied is not None and PERSON not in named
        if fyi:
            self._check_person_depth(sender, question=False)
        if mail.MAILBOX_DEPTH is not None:
            for sid in named:
                if sid != PERSON and records[sid].unread() >= mail.MAILBOX_DEPTH:
                    raise RpcError(
                        f"{sid}'s inbox holds {mail.MAILBOX_DEPTH} unread entries: the send is refused, not dropped "
                        f"(design §4.10)"
                    )
        # -- land it ----------------------------------------------------------------------------------
        mid = "m-" + secrets.token_hex(6)
        root = root or mid
        at = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        entry = MailEntry(
            id=mid, from_=sender, to=list(named), at=at, kind=kind, text=text, about=about, reply_to=reply_to, root=root
        )
        entry.cites = cited
        entry.default = line or None
        entry.source = src
        entry.answers = list(picks)  # data the sender proposed, on the envelope (§4.10, TD-070)
        entry.answer = picked
        entry.pr = pr
        entry.verdict = verdict
        entry.shots = looks
        entry.team = (me.team or None) if me is not None else None  # the envelope carries its sender's team (§4.10)
        if kind in ASK_KINDS and not (kind == "ask" and PERSON in named):
            # `bound` is None exactly when the addressee is the person and the kind is `ask`: that
            # one never expires, and the person inbox's depths are what bound it instead (§4.10).
            span = timedelta(seconds=float(bound)) if bound is not None else mail.ASK_BOUND
            entry.bound = (now + span).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        landed: list[str] = []
        failed: list[str] = []
        for sid in copies:
            if mail.MAILBOX_DEPTH is not None and records[sid].unread() >= mail.MAILBOX_DEPTH:
                failed.append(sid)  # a copy never sinks a send: dropped and recorded (design §4.10)
            else:
                landed.append(sid)
        entry.copies, entry.copies_failed = landed, failed
        touched: list[Session] = []
        for sid in (*named, *landed):
            if sid == PERSON:
                self.person_inbox.append(self._copy(entry))
                self.person_store.save(self.person_inbox)
                continue
            r = records[sid]
            r.inbox.append(self._copy(entry))
            touched.append(r)
        if me is not None:
            me.outbox.append(self._copy(entry))
            touched.append(me)
        if counts:
            for r in touched:
                r.threads.setdefault(root, Tally()).count += 1
            if replied is None and me is not None:  # replying to nothing: counted under the pair too
                for sid in named:
                    if sid == PERSON:
                        continue  # the person inbox keeps no tally: its depths bound it instead
                    for t in (self._pair(me, sid, now), self._pair(records[sid], sender, now)):
                        t.at.append(at)
                        t.count = len(t.at)  # the window's count, kept as a field so pruning cannot reset it
        if overrule and sender == PERSON:
            # An Overrule is work the person handed the asker (§4.9b, §4.8a *An alarm's answers*):
            # it owes an outcome — on the asker's copy only, never the answerer's.
            for e in records[named[0]].inbox if named[0] in records else []:
                if e.id == mid:
                    e.handed = True
        fyi_id = None
        if fyi and replied is not None:
            fyi_id = "m-" + secrets.token_hex(6)
            note = MailEntry(
                id=fyi_id,
                from_=sender,
                to=[PERSON],
                at=at,
                kind="note",
                text=text,
                about=replied.about,
                root=root,
            )
            note.team = entry.team
            note.answered = {"question": replied.text, "asker": replied.from_, "answerer": sender, "source": src}
            self.person_inbox.append(note)
            self.person_store.save(self.person_inbox)
        if closes and replied is not None:
            # a pressed answer is kept on the question too, which is where the person's Inbox reads
            # what was answered (§4.5a *Waiting on them*); a typed reply leaves it None (TD-296 #14)
            self._mark(replied.id, closed_by=mid, closed_at=at, closed_reason="replied", answer=picked)
        if taken is not None:
            # the question goes to the person, so the session it was put to is no longer asked it:
            # its seat stops counting it, and a late answer from it closes nothing (§4.9b)
            self._close_entry(taken.id, "asked_person", at)
        if settle is not None:
            # Written on every copy, as a close is: the person's Inbox lists it under the question,
            # and the asker's own card stops saying it owes one. `by` is this entry — an ordinary
            # note of the person inbox, listed under its question and pruned as any FYI entry is.
            self._mark(
                settle.id,
                outcome={
                    "state": state or "asked_again",
                    "text": _clean(text)[: mail.DEFAULT_CAP],
                    "at": at,
                    "by": mid,
                },
            )
        if sender == PERSON and reply_to:
            # A person's message into a thread resets it (design §4.10): tally and `bound_hit`
            # cleared on every record holding it, so the sessions may reply to the ruling.
            for r in records.values():
                if root in r.threads:
                    r.threads[root] = Tally()
                    if r not in touched:
                        touched.append(r)
        if sender == PERSON:
            for sid in named:  # a person's message, reply or not, refills each addressee's budget
                if sid != PERSON:
                    self._refill(records[sid])
        for r in touched:
            self._save(r)
        await self._push_changes()
        now = datetime.now(UTC)
        away = {
            sid
            for sid in (*named, *landed)
            if sid != PERSON
            and records[sid].host != self.host
            and not (self.links.get(records[sid].host) or {}).get("up")
        }
        # §4.10 *When it is read* (TD-168): one sentence per addressee, after delivery — the kind a
        # reply reads as a note does, an ask's own bound, a session's sender told of a spent budget
        span = None
        with contextlib.suppress(TypeError, ValueError):
            span = _parse(entry.bound) - _parse(entry.at) if entry.bound else None
        read_when = {
            sid: mail.read_when(
                records[sid],
                entry.kind,
                now,
                person=sender == PERSON,
                bound=span,
                unreachable=sid in away,
                rings=getattr(adapters.get(records[sid].adapter), "composer", None) is not None,
                # a person's answer on a handed entry's thread fills a seat on call (TD-218)
                # — a reply that closes the seat's own question on the thread of an entry it still owes
                # (review of #760: an outcome note or a seat's FYI on the thread refills nothing)
                refills=sender == PERSON
                and entry.kind == "reply"
                and bool(closes)
                and replied is not None
                and replied.kind == "ask"
                and replied.from_ == sid
                and any(e.id == root and e.handed_entry and e.owes for e in records[sid].inbox),
            )
            for sid in named
            if sid != PERSON and sid in records
        }
        return {
            # exhaustion is visible to the sender (design §4.10): the mail landed, and it wakes nobody
            "wake_budget_spent": [
                sid for sid in (*named, *landed) if sid != PERSON and mail.wake_budget_spent(records[sid], now)
            ],
            # landed at the home while its host's link is down (§4.4a "When the recipient's host is
            # unreachable"): nothing waits anywhere but the mailbox, and the sender is told
            "unreachable": [sid for sid in (*named, *landed) if sid in away],
            "read_when": read_when,
            "entry": entry.to_dict(),
            "delivered": list(named),
            "copies": landed,
            "copies_failed": failed,
            "forwarded": forwarded,
            "closed": replied.id if closes and replied is not None else None,
            "answered_for_you": fyi_id,  # the FYI filed to the person for a reply with a source (§4.9b)
            # advice, not a refusal: the id comes back either way (design §4.10)
            "advice": advice,
        }

    @staticmethod
    def _copy(entry: MailEntry) -> MailEntry:
        """A record's own copy of an entry: same values, no shared lists."""
        return replace(
            entry,
            to=list(entry.to),
            copies=list(entry.copies),
            copies_failed=list(entry.copies_failed),
            pending=list(entry.pending),
            cites=list(entry.cites),
            # every addressee's copy carries the answers — a `conflict` is read and picked between
            # sessions, so each controller's copy must hold them (§4.10 *Suggested answers*)
            answers=list(entry.answers),
            shots=list(entry.shots),
        )

    def _pair(self, r: Session, other: str, now: datetime) -> Tally:
        """The pair tally `r` keeps for `other`, its window rolled forward: entries older than
        `PAIR_WINDOW` fall out, and `count` is what is left."""
        t = r.threads.setdefault(f"pair:{other}", Tally())
        cutoff = now - mail.PAIR_WINDOW
        t.at = [x for x in t.at if _parse(x) >= cutoff]
        t.count = len(t.at)
        return t

    def _check_bounds(self, sender: str, named: list[str], root: str, now: datetime) -> None:
        """A send is refused when the sender's tally, or any named addressee's, is at the bound —
        never a copy recipient's — and `bound_hit` is written on every record holding the thread
        so the other side learns the exchange stopped (design §4.10 "A bounded exchange")."""
        records = self._graph()
        me = records[sender]
        if root:
            limit = mail.THREAD_BOUND
            if limit is None:
                return
            at_bound = [
                sid
                for sid in (sender, *named)
                if sid != PERSON and records[sid].threads.get(root, Tally()).count >= limit
            ]
            if at_bound:
                for r in records.values():
                    if root in r.threads:
                        r.threads[root].bound_hit = True
                        self._save(r)
                raise RpcError(
                    f"thread {root} is at its bound of {limit} entries ({', '.join(at_bound)}): the send is "
                    f"refused — write the user_attention.md line yourself, with the thread attached (design §4.10)"
                )
            return
        limit = mail.PAIR_BOUND
        if limit is None:
            return
        for sid in named:
            if sid == PERSON:
                continue
            mine, theirs = self._pair(me, sid, now), self._pair(records[sid], sender, now)
            if mine.count >= limit or theirs.count >= limit:
                mine.bound_hit = theirs.bound_hit = True
                self._save(me)
                self._save(records[sid])
                raise RpcError(
                    f"{sender} and {sid} have exchanged {limit} messages replying to nothing inside "
                    f"{mail.PAIR_WINDOW}: the send is refused — write the user_attention.md line yourself "
                    f"(design §4.10)"
                )

    def _person_holds(self, msg_id: str) -> list[MailEntry]:
        """Where a person's `--reply-to` looks: the person inbox first (a session's message to the
        person), then every session's copies (a person answering from a session's Inbox panel)."""
        return [e for e in self.person_inbox if e.id == msg_id] + [
            e for r in self._graph().values() for e in r.holds(msg_id)
        ]

    def _check_person_depth(self, sender: str, *, question: bool) -> None:
        """The person inbox's depths (design §4.10 *The numbers*): it fills exactly when the person
        has been away, so the refusal is a redirect to the channel with a `Due:` date.

        Two counts from 2026-10-04 (TD-324), each with its depth and per-sender depth: a question
        (an `ask`, a `steer`, a `conflict`, a pass-up) is counted against the **open questions**, so
        one worker cannot fill the Inbox with asks that never lapse; anything else against the
        **FYIs**, every other entry still there, so a seat's notes never stop its next question."""
        if question:
            counted = [e for e in self.person_inbox if e.open]
            depth, per, what = mail.PERSON_INBOX_DEPTH, mail.PERSON_SENDER_DEPTH, "open questions"
        else:
            counted = [e for e in self.person_inbox if not e.open]
            depth, per, what = mail.PERSON_FYI_DEPTH, mail.PERSON_FYI_SENDER_DEPTH, "FYIs"
        full = None
        if depth is not None and len(counted) >= depth:
            full = f"the person inbox holds {depth} {what}"
        elif per is not None and sum(1 for e in counted if e.from_ == sender) >= per:
            full = f"the person inbox holds {per} {what} from {sender}"
        if full:
            raise RpcError(
                f"{full}: the person is away — write the line on user_attention.md with a Due: date, "
                f"the channel that reaches an absent person (design §4.10)"
            )

    def _mark(self, msg_id: str, **fields: Any) -> None:
        """Write the same fact on every copy of one message, so both cards show it: the home is
        the one writer (design §4.4a). `pending=<id>` appends to the list; anything else is set."""
        for r in self._graph().values():
            copies = r.holds(msg_id)
            if not copies:
                continue
            for e in copies:
                for k, v in fields.items():
                    if k == "pending":
                        if v not in e.pending:
                            e.pending.append(v)
                    else:
                        setattr(e, k, v)
            self._save(r)
        mine = [e for e in self.person_inbox if e.id == msg_id]
        for e in mine:
            for k, v in fields.items():
                if k == "pending":
                    if v not in e.pending:
                        e.pending.append(v)
                else:
                    setattr(e, k, v)
        if mine:
            self.person_store.save(self.person_inbox)
        if fields.get("outcome"):
            self._look_back(msg_id, fields["outcome"])

    def _look_back(self, handed: str, outcome: dict[str, Any]) -> None:
        """**The seat's outcome ends the wait** (design §4.10 *A look*, §4.5a **Send to reviewer**,
        TD-292 slice 4): a look snoozed for the `handed` `ask` that just closed comes back — its
        `snoozed_for` cleared, and the outcome's line, when it has one, written on it as
        `looked_by: {seat, text}`. Any end of the debt brings it back: an outcome, the person's
        Dismiss of the handed row. The look is found by the handed entry's `look`, not by its own
        `snoozed_for`, so a look the person unsnoozed sooner still gets the seat's line."""
        held = next(((r.id, e) for r in self._graph().values() for e in r.inbox if e.id == handed and e.look), None)
        if held is None:
            return
        seat, h = held
        looks = [e for e in self.person_inbox if e.id == h.look]
        if not looks:
            return
        line = str(outcome.get("text") or "").strip()
        if line and outcome.get("state") == "blocked":
            line = f"blocked: {line}"
        for e in looks:
            if e.snoozed_for == handed:
                e.snoozed_for = None
            if line:
                e.looked_by = {"seat": seat, "text": line}
        self.person_store.save(self.person_inbox)

    def _close_entry(self, msg_id: str, reason: str, at: str) -> None:
        """Design §4.10 "One way of being closed": `closed_reason` is set whenever an entry closes,
        by whatever path, and the fields that existed before it are kept and still written —
        `expired` sets `expired_at` as today, and `lapsed`, `declined`, `go_with_it` and
        `asker_gone` set `closed_at` alone. (`replied` is the send path's, which writes `closed_by`
        and `closed_at` with the reply that answered.)"""
        if reason == "expired":
            self._mark(msg_id, expired_at=at, closed_reason=reason)
        else:
            self._mark(msg_id, closed_at=at, closed_reason=reason)

    def _system_note(self, to: str, text: str, *, wake: str = "note", team: str | None = None) -> None:
        """A `note` from `system` written **straight into the sender's mailbox** (design §4.10 "How
        the sender hears that one closed without a reply"): it does not pass through the send path,
        so no gate, no tally and no depth sees it, and no session can send as `system`. It reports
        what happened to the reader's own message and is never an instruction; `--reply-to` naming
        one is refused.

        `wake` is which of the section's three rules applies:

        - `person` — a decline, a *Go with it* or a **pause**, the three by which a person releases
          a sender that may be blocked in `ao wait`, and a person's **drop** of its claim (TD-150),
          news it must act on: they wake as a person's `reply` does and **refill** the budget;
        - `note` — a **resume**, ordinary: it wakes within the budget like any `note`;
        - `uncharged` — a **lapse**: outside the budget, neither spending nor refilling it, so a
          spent budget cannot hold a sender past the bound it set itself. It is carried on the note
          (`MailEntry.uncharged`) rather than beside the records, so it survives the two things
          that happen between a lapse and the wake it earns: a **resume**, which moves the note to
          the new record, and a host-agent **restart**, which reloads it (review of PR #245).

        A sender that has since been resumed is followed to its successor, as mail addressed to a
        superseded record is (§4.10 lifecycle): the note is about the conversation, not the id.

        `team` is the team a note is about, stamped where a sender's would be, so the Inbox files it
        under that team rather than *No team* (§4.10 *An envelope carries its sender's team*)."""
        entry = MailEntry(id="m-" + secrets.token_hex(6), from_=SYSTEM, to=[to], at=now_iso(), kind="note", text=text)
        entry.team = team or None
        entry.uncharged = wake == "uncharged"
        if to == PERSON:
            self.person_inbox.append(entry)
            self.person_store.save(self.person_inbox)
            return
        records = self._graph()
        sid, seen = to, {to}
        while (r := records.get(sid)) is not None and r.state == "closed" and r.superseded_by:
            sid = r.superseded_by
            if sid in seen:
                break
            seen.add(sid)
        r = records.get(sid)
        if r is None:
            return  # the sender's record is gone: there is nobody left to tell
        entry.to = [sid]
        r.inbox.append(entry)
        if wake == "person":
            self._refill(r)  # saves the record and pokes the waits
        else:
            self._save(r)
            self._poke_waits()

    async def rpc_pass_up(
        self, id: str, recommend: str, answers: list[str] | str | None = None, caller: Any = None
    ) -> dict[str, Any]:
        """`ao msg --pass-up <id> --recommend "<line>" [--answer …]` (design §4.9b *Passing up
        keeps the thread and the asker*, TD-075 step 3): the addressee of an open `ask` or `steer`
        hands it to the person, once, with a recommendation.

        The person inbox gets **the asker's own entry** — same id, same sender, kind, text, default
        and bound (passing up buys no time) — so everything §4.10 already does with a reply follows
        by itself: the person's reply goes to the asker, is copied to the passer (who is in the
        entry's `to`), closes every copy, and the asker owes an outcome on it as on any question
        the person answered. On that copy the passer's suggested answers are its `answers`, the
        recommendation first, so the person's part is one press; the recommendation itself is
        `recommend`, labelled as the passer's. Open to the entry's addressee only — a copy
        recipient did not receive the question — and never to the person."""
        if mail.is_person(caller):
            raise RpcError("the person is the top of the ladder: there is nobody to pass a question up to (§4.9b)")
        me = self._graph().get(self._addr(str(caller)))
        if me is None:
            raise RpcError(f"{caller} cannot pass anything up: this host agent has no record of it (design §4.10)")
        held = [e for e in me.inbox if e.id == id]
        if not held:
            raise RpcError(f"{me.id} holds no entry {id}: pass up a question addressed to you (design §4.9b)")
        e = held[0]
        if e.kind not in ("ask", "steer"):
            raise RpcError(f"{id} is a {e.kind}: only an ask or a steer is passed up (design §4.9b)")
        if me.id not in e.to:
            raise RpcError(f"{id} was copied to you, not addressed to you: its addressee passes it up (design §4.9b)")
        if e.passed_up:
            raise RpcError(f"{id} was passed up at {e.passed_up}: a question goes to the person once (design §4.9b)")
        if not e.open:
            raise RpcError(f"{id} is already closed ({e.closed_reason}): there is nothing to pass up (design §4.9b)")
        if e.from_ == PERSON:
            raise RpcError(f"{id} is the person's own question: answer it (design §4.9b)")
        line = _clean(str(recommend or "").split("\n", 1)[0]).strip()[: mail.DEFAULT_CAP]
        if not line:
            raise RpcError('a question goes up with your recommendation: --recommend "<one line>" (design §4.9b)')
        if answers is not None and not isinstance(answers, (str, list)):
            raise RpcError("answers must be a list of lines (design §4.10)")
        offered = [answers] if isinstance(answers, str) else list(answers or [])
        if any(not isinstance(a, str) for a in offered) or len(offered) > mail.ANSWERS_MAX * 2:
            raise RpcError("answers must be a list of at most four lines (design §4.10)")
        picks: list[str] = []
        for raw in [line, *offered]:  # the recommendation is the first suggested answer (§4.9b)
            one = _clean_answer(raw)
            if one and one not in picks:
                picks.append(one)
        if len(picks) > mail.ANSWERS_MAX:
            raise RpcError(
                f"a question carries at most four answers, the recommendation among them: {len(picks)} given "
                "(design §4.10)"
            )
        self._check_person_depth(e.from_, question=True)  # it is the asker's question in the person inbox
        at = now_iso()
        rec = {"by": me.id, "text": line}
        up = self._copy(e)
        up.read_at = None
        up.answers = picks
        up.passed_up, up.recommend = at, rec
        self._mark(id, passed_up=at, recommend=rec)  # every copy says it went up, and with what
        self.person_inbox.append(up)
        self.person_store.save(self.person_inbox)
        await self._push_changes()
        return {"id": PERSON, "msg": id, "passed_up": at, "recommend": rec, "answers": picks}

    async def rpc_inbox(
        self,
        id: str | None = None,
        unread: bool = False,
        caller: Any = None,
        sent: bool = False,
        watching: bool = False,
    ) -> dict[str, Any]:
        """`ao inbox [--unread]` (design §4.10): a session reads its own inbox and nobody else's;
        that read — and nothing else — sets `read_at` (lifecycle stage 2: delivered into a turn).
        A person (no caller) reads any session's inbox, as the Inbox panel does, and sets nothing:
        a person is not the session. A person naming no session reads the org's person inbox, and
        that read sets nothing either. Each entry says whether its sender is one of the reader's
        controllers, a person, or neither — the rule stated where the mail is read.

        `sent` (design §4.9b, TD-075 step 4) reads the **outbox** instead — a session's own, and a
        person any session's, exactly as the inbox is read — and marks nothing: it is what the
        session itself sent, so there is nothing to have read. The person inbox keeps no outbox.

        `watching` (§4.10 *Told on Telegram*, *Looking*; TD-319): the person's read from a page whose
        document is visible, which the home keeps the time of — a row whose hold ends soon after is
        not told. A session's read never says so."""
        if watching and mail.is_person(caller):
            self._notify_watching(datetime.now(UTC))
        if sent:
            return self._sent(id, caller)
        if mail.is_person(caller):
            if not id or id == PERSON:
                held = [e for e in self.person_inbox if not (unread and e.read_at)]
                # a handed entry still owed → its addressee, whose own open question on the thread
                # the person's reply would close and so refill the seat
                handed = {e.id: s.id for s in self.sessions.values() for e in s.inbox if e.handed_entry and e.owes}
                return {
                    "id": PERSON,
                    "entries": [
                        {
                            **e.to_dict(),
                            "from_role": mail.from_role(self._graph(), PERSON, e.from_, controllers=self._ctl),
                            # on a handed entry's thread (TD-218): the Reply composer's line is `refill`
                            "on_handed": e.open and e.kind == "ask" and handed.get(e.root) == e.from_,
                        }
                        for e in held
                    ],
                    # work the person handed a session (§4.10 *An entry handed to a seat*, TD-218 slice 3):
                    # its one copy is the holder's, so the person's read lists it beside the inbox —
                    # while it owes its outcome, or came back `blocked` — for the row under *Waiting on
                    # them*; Dismiss names its id, and its outcome is written on that copy
                    "handed": [
                        {**e.to_dict(), "holder": addr, "holder_name": r.name, "holder_state": r.state}
                        for addr, r in self._graph().items()
                        for e in r.inbox
                        if e.handed_entry and (e.owes or (e.outcome or {}).get("state") == "blocked")
                    ],
                    "threads": {},
                    "sends": [],
                    "unread": sum(1 for e in self.person_inbox if not e.read_at),
                    # design §4.10 *The Inbox is a queue* (TD-079): the endings of the state rows
                    # the Inbox showed, newest first, and the person's *not now* on a state row.
                    # Neither is mail and both belong to the home, so the person's read carries
                    # them here rather than making the page ask twice.
                    "trail": [dict(e) for e in self.trail],
                    "attention_snoozed": dict(self.attention_snoozed),
                }
            s = self._find(self._addr(id))  # another host's too: the mailbox is the home's (step 5)
            mark = False
        else:
            me = self._addr(caller)
            if id and self._addr(id) != me:
                raise RpcError(f"{me} cannot read {id}'s inbox: nobody reads another session's inbox (design §4.10)")
            s = self._find(me)
            mark = True
        entries = [e for e in s.inbox if not (unread and e.read_at)]
        if mark and any(not e.read_at for e in entries):
            at = now_iso()
            for e in entries:
                e.read_at = e.read_at or at
            self._bell_cleared(s)  # a read answers the bell (§4.10, TD-347)
            self._save(s)
            await self._push_changes()
        who = self._address(s)
        return {
            "id": who,
            "entries": [
                {**e.to_dict(), "from_role": mail.from_role(self._graph(), who, e.from_, controllers=self._ctl)}
                for e in entries
            ],
            "threads": {k: t.to_dict() for k, t in s.threads.items()},
            "sends": [e.to_dict() for e in s.sends[-3:]],
            "unread": s.unread(),
        }

    def _sent(self, id: str | None, caller: Any) -> dict[str, Any]:
        if mail.is_person(caller):
            if not id or id == PERSON:
                raise RpcError("the person inbox keeps no sent list: name the session whose sent mail to read")
            s = self._find(self._addr(id))
        else:
            me = self._addr(caller)
            if id and self._addr(id) != me:
                raise RpcError(f"{me} cannot read {id}'s sent mail: nobody reads another session's mail (design §4.10)")
            s = self._find(me)
        return {
            "id": self._address(s),
            "sent": True,
            "entries": [e.to_dict() for e in s.outbox],
            "unread": s.unread(),
        }

    async def rpc_pr_reads(self, id: str, pr: int, caller: Any = None) -> dict[str, Any]:
        """`pr_reads {id, pr}` (design §4.9c *Whose turn it is*, TD-315 slice 2): the `ask`s session
        `id` sent carrying `pr`, oldest first, each one's addressees, time, whether it is open, and
        the `verdict` of its last reply with that reply's time — structured fields and never text, as
        `prs_waiting` is. Answered to the author itself, to a session one of those asks is addressed
        to, and to a person; the home answers it and times nothing. A read, marking nothing."""
        try:
            pr = int(pr)
        except (TypeError, ValueError):
            raise RpcError(f"pr is a PR's number, not {pr!r}") from None
        s = self._find(self._addr(id))
        asks = [e for e in s.outbox if e.kind == "ask" and e.pr == pr]
        if not mail.is_person(caller) and not self._is_self(s, caller):
            me = self._addr(caller)
            if not any(self._addr(x) == me for e in asks for x in e.to):
                raise RpcError(
                    f"{me} cannot read {id}'s reads of PR #{pr}: its author, a reader it asked, or a person "
                    f"reads them (design §4.9c)"
                )
        replies: dict[str, MailEntry] = {}
        for e in s.inbox:  # the replies landed in the asker's inbox, oldest first
            if e.kind == "reply" and e.reply_to:
                replies[e.reply_to] = e
        reads = []
        for e in sorted(asks, key=lambda e: e.at):
            last = replies.get(e.id)
            reads.append(
                {
                    "id": e.id,
                    "to": list(e.to),
                    "at": e.at,
                    "open": e.open,
                    "verdict": last.verdict if last else None,
                    "replied_at": last.at if last else None,
                }
            )
        return {"id": self._address(s), "pr": pr, "asks": reads}

    async def rpc_thread(self, msg: str, caller: Any = None) -> dict[str, Any]:
        """`ao inbox --thread <id>` and the Inbox's message page (design §4.7, §4.5 screen 6,
        TD-136): the whole thread of one entry in the person inbox — every entry sharing its
        `root`, gathered across the person inbox and every record's inbox and outbox, one per id
        (the person inbox's copy first, then the first found), oldest first. The person's own
        replies are in it though the person inbox keeps no sent list: they live in the askers'
        inboxes. `pruned` says the root itself is held nowhere any more, so the thread starts
        after a gap. A read, marking nothing; **a person's only** — a thread spans other
        sessions' mailboxes, which no session reads (§4.10)."""
        agent_common.person_only(caller, "read a thread, gathered across every session's mailbox", "§4.10")
        held = [e for e in self.person_inbox if e.id == msg]
        if not held:
            raise RpcError(f"the person inbox holds no entry {msg}")
        root = held[0].root
        boxes = [self.person_inbox, *(box for s in self.sessions.values() for box in (s.inbox, s.outbox))]
        found: dict[str, MailEntry] = {}
        chains: list[list[str]] = []  # each mailbox's thread entries in the order they landed there
        for box in boxes:
            chain = [e.id for e in box if e.root == root]
            for e in box:
                if e.root == root:
                    found.setdefault(e.id, e)
            chains.append(chain)
        graph = self._graph()
        return {
            "id": msg,
            "root": root,
            "entries": [
                {**e.to_dict(), "from_role": mail.from_role(graph, PERSON, e.from_, controllers=self._ctl)}
                for e in _oldest_first(found, chains)
            ],
            "pruned": root not in found,
        }

    async def rpc_inbox_delete(self, msg: str, id: str | None = None, caller: Any = None) -> dict[str, Any]:
        """The Inbox panel's delete (design §4.10 lifecycle, §4.5a): a person removes one entry
        from one session's inbox — that record's copy only, so the sender's and any other
        addressee's copies stay, and a thread stays one thread on their side. Refused to every
        session, itself included: a session's inbox is read-only to it through the RPCs, and an
        entry leaves outside its lifecycle only with its record or by a person's hand. Naming no
        session (or `person`) deletes from the org's person inbox — the top bar's delete — and the
        sender's copy stays there too.

        **Deleting is declining, and nothing vanishes at once** (design §4.10, 2026-09-19): in the
        person inbox, deleting an *open* `ask` or `steer` closes it `declined` — a deletion is an
        answer, and silence is not — and the entry stays for the retention window like any closed
        one; the asker is told by a `system` note that wakes it as a person's reply does. A `note`,
        or anything already closed, is removed outright, as it always was."""
        agent_common.person_only(caller, "delete mail", "§4.10")
        if not id or id == PERSON:
            held = [e for e in self.person_inbox if e.id == msg]
            if not held:
                raise RpcError(f"the person inbox holds no entry {msg}")
            if held[0].open:
                e = held[0]
                self._close_entry(msg, "declined", now_iso())
                self._system_note(e.from_, f"{e.kind} {msg} declined by the person", wake="person")
                await self._push_changes()
                return {
                    "id": PERSON,
                    "deleted": msg,
                    "declined": True,
                    "unread": sum(1 for x in self.person_inbox if not x.read_at),
                }
            kept = [e for e in self.person_inbox if e.id != msg]
            self.person_inbox = kept
            self.person_store.save(kept)
            return {"id": PERSON, "deleted": msg, "declined": False, "unread": sum(1 for e in kept if not e.read_at)}
        s = self._find(self._addr(id))
        kept = [e for e in s.inbox if e.id != msg]
        if len(kept) == len(s.inbox):
            raise RpcError(f"{s.id}'s inbox holds no entry {msg}")
        # **A debt is not deleted away** (design §4.8a *An alarm's answers*, §4.10 *Outcomes*;
        # review of PR #318). A handed entry is the first debt-bearing mail that lives in an
        # ordinary session's **inbox** rather than in the asker's outbox, so it is the first that
        # this delete could reach — and it computes `owes` from the entry, so deleting the object
        # would discharge the debt with no outcome, no trail and nobody told. Dismiss it from the
        # person's Inbox instead, which ends it *and* tells the session, or let it report one. (A
        # question passed up owes on its asker's outbox, never on a copy here: `owes_for`.)
        if owing := [e for e in s.inbox if e.id == msg and e.owes_for(session_inbox=True)]:
            raise RpcError(
                f"{msg} is work the person handed {s.id} and it still owes an outcome: deleting it would "
                "settle nothing and tell nobody. Let it report one — ao msg person --outcome "
                f'done|blocked|dropped "<line>" --for {msg} — or Dismiss the row, which ends the debt '
                "and says so (design §4.8a, §4.10 *Outcomes*)",
                owes=owing[0].id,
            )
        s.inbox = kept
        _prune_tallies(s)  # a delete is the other way an entry leaves (review of PR #214)
        self._save(s)
        await self._push_changes()
        return {"id": self._address(s), "deleted": msg, "unread": s.unread()}
