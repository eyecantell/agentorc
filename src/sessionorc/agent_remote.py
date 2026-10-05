"""The host agent's cross-host half (TD-108 step 1): the calls a node makes that the home answers
(design §4.4a *Mail across hosts*) and the acts across the link at the home (§4.4a, TD-057 step 4a), as
a mixin `HostAgent` inherits. Moved as written; the state it reads is the agent's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from pathlib import Path
from typing import Any

from sessionorc import (
    agent_common,
    containers,
    hosts,
    link,
    mail,
    modes,
    naming,
    shots,
)
from sessionorc import settings as settings_mod
from sessionorc import usage as usage_mod
from sessionorc.agent_common import (
    ACT_TIMEOUT,
    HOME_EDITS,
    INTENT_FIELDS,
    NODE_ACTS,
    NODE_READS,
    RpcError,
    closer_of,
    launch_params,
    log,
    read_checkout,
    stat_dir,
)
from sessionorc.gitinfo import WorktreeError, worktree_path
from sessionorc.models import (
    PERSON,
    FindingEntry,
    NotTheSameSession,
    ProgressEntry,
    Session,
    apply_node,
    has_control,
    now_iso,
)


class RemoteMixin:
    # -- a node's calls the home answers (design §4.4a "Mail across hosts", TD-057 step 5) --------

    def _from_home_form(self, address: str) -> str:
        """An address in the home's form, read at this node: the home's own sessions bare there are
        `id@home` here, and `id@<this node>` is bare."""
        sid, h = naming.split_address(address)
        return naming.qualify(f"{sid}@{h or self.home}", local=self.host)

    async def _forward(self, rid: Any, name: str, params: dict[str, Any], caller: Any) -> dict[str, Any]:
        """A node hands a call it cannot serve to the home over the link — `forward {rpc, params,
        caller, token}` — and answers with the home's verdict, every address in it rewritten into
        this host's form. A cancelled call (a `wait` whose client went away) is cancelled at the
        home too, by its token, so no ghost wait is charged a wake there. Never queued: a link
        that drops while the call is out is that call's error."""
        mux = self._home_mux
        if mux is None:
            raise RpcError(f"{self.home} (home) is unreachable from {self.host}; refused, not queued (design §4.4a)")
        token = secrets.token_hex(8)
        # A `wait` is bounded by its own timeout at the home; everything else within ACT_TIMEOUT —
        # pings keep a link up past a dispatch that hangs (review of PR #217)
        timeout = None if name == "wait" else ACT_TIMEOUT
        try:
            reply = await mux.request("forward", timeout=timeout, rpc=name, params=params, caller=caller, token=token)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await mux.notify("cancel", token=token)
            raise
        except link.LinkError as e:
            return {"id": rid, "error": f"{self.home}: {e}"}
        except link.LinkClosed:
            return {"id": rid, "error": f"the link to {self.home} dropped while {name} was out: its verdict is unknown"}
        except TimeoutError:
            with contextlib.suppress(Exception):
                await mux.notify("cancel", token=token)
            return {
                "id": rid,
                "error": f"{self.home} did not answer {name} within {timeout:g} s: its verdict is unknown",
            }
        reply = reply if isinstance(reply, dict) else {}
        out = naming.readdress({k: v for k, v in reply.items() if k != "id"}, self._from_home_form)
        return {"id": rid, **out}

    async def _forwarded(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """The home's end: a node's call run here as that node's session — the caller is
        `id@host`, every address in the params read from that host's point of view, a `create`
        landing on that host unless it says otherwise — through the same dispatch a local caller
        gets, gate and routing included. The reply is the dispatch's, addresses in the home's
        form; the node rewrites them. Registered by token until it returns so the node can
        cancel it."""
        rpc = str(params.get("rpc") or "")
        if rpc in modes.HOME_ONLY:
            # a checkout's files are read by a caller at the home, for a team start there (4b.3);
            # a node — a person or a session on it — never reads another host's files through here
            return {"id": 0, "error": f"{rpc} is not served to a call from {host}: ask at the home (design §4.4a)"}
        p = naming.readdress(dict(params.get("params") or {}), lambda a: self._from_host(a, host))
        if rpc == "set_controllers":
            for key in ("add", "remove"):
                if p.get(key):
                    p[key] = [self._from_host(x, host) for x in p[key]]
        if rpc == "create":
            p.setdefault("host", host)
        if params.get("caller") is None:
            # A person at a node acts only on that node's records (§4.4a): `id@<this home>` collapsed
            # to a home record above, and the person gate would then have passed it (security read of
            # PR #217). The target's host, read after the rewrite, has to be the link's own. A call
            # that names no session — the person inbox, a `wait` — names nothing to bound; the wait
            # is scoped to the node's host instead. A `create` for another host is refused at the
            # node before it gets here; the check is kept as a second line.
            if rpc == "wait":
                p["only_host"] = host
            where: str | None = None
            if rpc == "create":
                where = str(p.get("host") or host)
            elif rpc in modes.PERSON_NODE_BOUND and p.get("id") and p["id"] != PERSON:
                where = naming.split_address(str(p["id"]))[1] or self.host
            if where is not None and where != host:
                return {
                    "id": 0,
                    "error": (
                        f"a person at {host} may {rpc} only {host}'s sessions: {p.get('id') or p.get('host')} "
                        f"is on {where} (design §4.4a)"
                    ),
                }
        req = {"id": 0, "method": rpc, "params": p, "caller": params.get("caller")}
        task = asyncio.ensure_future(self._dispatch(req, link_host=host))
        token = str(params.get("token") or "")
        if token:
            self._forwarded_calls[token] = task
        try:
            return await task
        finally:
            if token:
                self._forwarded_calls.pop(token, None)
            if not task.done():
                task.cancel()

    def _cancel_forwarded(self, token: str) -> None:
        task = self._forwarded_calls.pop(str(token), None)
        if task is not None and not task.done():
            task.cancel()

    # -- acts across the link, at the home (design §4.4a, TD-057 step 4a) -----------------------

    def _act_host(self, method: str, params: dict[str, Any]) -> str | None:
        """The host an act is for when it is not this one: the address in `id`, or `create`'s
        (and `name_check`'s) `host`. None means *here*, and the method runs as it always has."""
        if method not in NODE_ACTS and method not in HOME_EDITS and method not in NODE_READS:
            return None
        if method in ("create", "name_check"):
            h = str(params.get("host") or "")
        else:
            _rid, h = naming.split_address(str(params.get("id") or ""))
        return h if h and h != self.host else None

    def _from_host(self, address: Any, host: str) -> str:
        """An address as `host`'s node stores it, read from here: its bare ids are `id@host`, and
        an `id@<this home>` is bare."""
        sid, h = naming.split_address(str(address))
        return naming.qualify(f"{sid}@{h or host}", local=self.host)

    def _to_host(self, address: Any, host: str) -> str:
        """The inverse: an address as this home stores it, written for `host`'s node."""
        sid, h = naming.split_address(str(address))
        return naming.qualify(f"{sid}@{h or self.host}", local=host)

    def _graph(self) -> dict[str, Session]:
        """One graph for the gates and the mailbox (§4.4a "The gate reads one graph", steps 4a and
        5): this host's records under their ids and every other host's under `id@host` — the
        records themselves, so what the mailbox writes lands on the record `_save` knows the host
        of. A remote record's `controllers` are stored as its node writes them; `_ctl` reads them
        from here, and every gate takes it. `self.sessions` itself is never widened — the tick,
        the anchor rule and the pane reads stay this host's."""
        g: dict[str, Session] = dict(self.sessions)
        for host, recs in self.remote.items():
            for rid, r in recs.items():
                g[f"{rid}@{host}"] = r
        return g

    def _ctl(self, s: Session) -> list[str]:
        """A record's `controllers` as this host addresses them (§4.4a "Every address crosses in
        the reader's form"): its own as stored, another host's re-addressed."""
        return s.controllers if s.host == self.host else [self._from_host(x, s.host) for x in s.controllers]

    def _answers_for(self, s: Session, graph: dict[str, Session] | None = None) -> dict[str, str] | None:
        """The session that answers for this record (design §4.8a *An alarm's answers*): its
        **first live controller**, in the order `controllers` holds them — the session that
        created it (§4.8 *Create adds the creator*), which for a team's member is its lead.

        Read from the control graph, never from a badge: §9 invariant 9 says nothing that acts
        keys on `team` or `role`, and the host agent does not read `org.yml`. `None` where there
        is no such session — a record a person started with no controller, a lead's own record,
        a controller that has exited — and the page says so in words where it is missing."""
        graph = self._graph() if graph is None else graph
        for c in self._ctl(s):
            other = graph.get(c)
            if other is not None and other.state not in ("exited", "closed"):
                return {"id": self._address(other), "name": other.name}
        return None

    def _address(self, s: Session) -> str:
        return s.id if s.host == self.host else f"{s.id}@{s.host}"

    def _node_mux(self, host: str) -> link.Mux:
        """The live link to `host`, or the refusal in words: *unreachable since <when> — <why>*,
        never queued (§4.4a "When the recipient's host is unreachable")."""
        if self.mode != "home":
            raise RpcError(f"{self.host} is a node of {self.home}: it acts on its own sessions only, ask the home")
        mux = self._link_muxes.get(host)
        if mux is not None:
            return mux
        state = self.links.get(host)
        if state is None and host not in hosts.nodes() and host not in self.remote:
            raise RpcError(f"unknown host {host}: not under `nodes:` in {self.host}'s hosts.yml")
        since = (state or {}).get("since") or "the home started"
        why = (state or {}).get("why") or "not connected since the home started"
        raise RpcError(f"runs on {host}: unreachable since {since} — {why}; refused, not queued (design §4.4a)")

    async def rpc_host_dir(self, host: str, dir: str) -> dict[str, Any]:
        """Whether `dir` exists on `host` (design §4.4a "Teams across hosts"): `ao team start`'s
        *every checkout exists on the record's host*, asked of that host's node. A read."""
        if host == self.host:
            return {"host": host, **await asyncio.to_thread(stat_dir, dir)}
        mux = self._node_mux(host)
        try:
            seen = await mux.request("stat", timeout=ACT_TIMEOUT, dir=dir)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None
        return {"host": host, **(seen if isinstance(seen, dict) else {"dir": dir, "exists": False})}

    async def rpc_host_repos(self, host: str) -> dict[str, Any]:
        """The checkouts `host`'s registry lists (design §4.9 *Where a repo's team lands*, TD-229 slice
        3): a repo-defined team that no `place:` names and this host's registry does not hold lands
        on the one linked node whose registry does, and the clients ask each node here. A read, as
        `host_dir` is: paths, never a file's text. `{host, repos}`; an unreachable node is refused
        in words, never queued."""
        if host == self.host:
            return {"host": host, "repos": await asyncio.to_thread(lambda: hosts.local_host().repos())}
        mux = self._node_mux(host)
        try:
            got = await mux.request("repos", timeout=ACT_TIMEOUT)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None
        repos = got.get("repos") if isinstance(got, dict) else None
        if not isinstance(repos, list):
            # never read as *holds no repo*: the landing rule would send the team elsewhere on it
            raise RpcError(f"{host} answered its registry with no list of checkouts")
        return {"host": host, "repos": [str(r) for r in repos]}

    async def rpc_host_files(
        self, host: str, dir: str, paths: list[str] | None = None, caller: Any = None
    ) -> dict[str, Any]:
        """The text of files in a checkout on `host` (design §4.4a "Teams across hosts", step
        4b.3): a team start's repo config and briefs, read where the checkout is — `paths` relative
        to `dir`, confined to it, bounded (`read_checkout`). A read, like `host_dir`, and yet more
        than an existence check: a person's, or a session's holding `control` — one that could
        start that team anyway. Never for a call forwarded from a node (`_forwarded` refuses it):
        a laptop does not read another host's files through the home."""
        if not mail.is_person(caller):
            me = self._graph().get(self._addr(caller))
            if me is None or not has_control(me.capabilities):
                raise RpcError(f"{caller} cannot read files on {host}: needs the control grant (design §4.4a)")
        if host == self.host:
            try:
                got = await asyncio.wait_for(asyncio.to_thread(read_checkout, dir, paths), ACT_TIMEOUT)
            except ValueError as e:
                raise RpcError(str(e)) from None
            except TimeoutError:
                raise RpcError(f"reading {dir} did not finish within {ACT_TIMEOUT:g} s") from None
            return {"host": host, **got}
        mux = self._node_mux(host)
        try:
            got = await mux.request("files", timeout=ACT_TIMEOUT, dir=dir, paths=list(paths or []))
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None
        return {"host": host, **(got if isinstance(got, dict) else {"dir": dir, "files": {}})}

    async def rpc_host_shot(self, host: str, repo: str, name: str, head: bool = False) -> dict[str, Any]:
        """One screenshot of a look, read on the host whose registry holds its repo (design §4.5a
        **Inbox row: a look**, TD-300): `docs/mockups/reviews/<name>` at `origin/<default>` of the
        checkout named `repo` there (`shots.read`), over the link's `shot` for a node. `{host, repo,
        name, exists, png}` — `png` the image in base64, empty with `head` (the row's address test)
        or where origin does not hold it. A name of any other shape is refused in words; a repo
        that host does not register reads as not there. A read, like `host_repos`: one file of the
        one directory a look may name, never a path the caller chose."""
        if not mail.SHOT_NAME.fullmatch(str(name or "")):
            raise RpcError(f"{name!r} is not a screenshot a look can name: <name>.png under docs/mockups/reviews/")
        if host == self.host:
            got = await asyncio.to_thread(shots.reading, str(repo or ""), name, bool(head))
        else:
            got = await self._ask_node(host, "shot", repo=str(repo or ""), name=name, head=bool(head))
            if not isinstance(got, dict):
                raise RpcError(f"{host} answered the screenshot with nothing")
        return {
            "host": host,
            "repo": repo,
            "name": name,
            "exists": bool(got.get("exists")),
            "png": str(got.get("png") or ""),
        }

    async def _ask_node(self, host: str, method: str, **params: Any) -> Any:
        """One link request to `host`'s node, every failure an `RpcError` in words: refused while
        the link is down (`_node_mux`), the node's own refusal, or *<host> did not answer*."""
        mux = self._node_mux(host)
        try:
            return await mux.request(method, timeout=ACT_TIMEOUT, **params)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer: {e or 'the link dropped'}") from None

    def _occupant_from(self, who: str, host: str) -> str:
        """A node's occupant line (`<id> (<state>)`, or an agent outside agentorc) in the home's
        form: a record of that node's is `<id>@<host>`, as the home's own reading names it."""
        sid, sep, rest = who.partition(" ")
        return f"{sid}@{host}{sep}{rest}" if sid in self.remote.get(host, {}) else who

    async def rpc_host_occupancy(self, host: str, dir: str) -> dict[str, Any]:
        """Who holds the agent slot for `dir` on `host` (design §4.4a *The New session form on
        another host*, TD-294): `occupancy` there, over the link method of that name — its live
        records in the directory and the agents its adapters see, a directory that is not there
        reading as empty. For a container node on this machine, whose checkout is one directory
        here and there, this host's own reading is added, as the create's anchor check does. A read
        like `host_dir`, and in `modes.HOME_ONLY`: it names another host's sessions."""
        if not str(dir or "").strip():
            return {"host": host, "dir": "", "occupants": [], "git": False}
        if host == self.host:
            return {"host": host, **await self.rpc_occupancy(dir)}
        got = await self._ask_node(host, "occupancy", dir=dir)
        if not isinstance(got, dict) or not isinstance(got.get("occupants"), list):
            raise RpcError(f"{host} answered its occupancy with no list of occupants")
        theirs = [self._occupant_from(str(o), host) for o in got["occupants"]]
        if host in containers.container_nodes():
            here = Path(dir).expanduser()
            theirs = await asyncio.to_thread(lambda: self.occupants(here) if here.is_dir() else []) + theirs
        seen: dict[str, str] = {}  # one line per occupant: the home's copy of a record and the node's own
        for who in theirs:
            seen.setdefault(who.partition(" ")[0], who)
        return {
            "host": host,
            "dir": str(got.get("dir") or dir),
            "occupants": list(seen.values()),
            "git": bool(got.get("git")),
        }

    def worktrees_here(self, repo: str) -> list[dict[str, Any]]:
        """`repo`'s worktrees on this host — the directories under its main checkout's
        `.claude/worktrees/` that git knows, by name — each with whether anyone is in it, the
        occupancy reading's verdict (TD-294). `[]` outside a git repository. Blocking: a thread's."""
        if not str(repo or "").strip():
            return []
        try:
            root = worktree_path(Path(repo).expanduser(), "")
            found = sorted(p for p in root.iterdir() if (p / ".git").exists()) if root.is_dir() else []
        except (WorktreeError, OSError):
            return []
        return [{"name": p.name, "path": str(p), "occupied": bool(self.occupants(p))} for p in found]

    async def rpc_host_worktrees(self, host: str, repo: str) -> dict[str, Any]:
        """**Where**'s chips for a repo on `host` (design §4.4a *The New session form on another
        host*, TD-294): `[{name, path, occupied}]` in one call, the listing and the occupancy both
        made where the directories are — the `worktrees` link method. A container node's worktree
        is one directory here and there, so this host's occupancy of it counts too. A read, in
        `modes.HOME_ONLY` as `host_occupancy` is."""
        if host == self.host:
            return {"host": host, "repo": repo, "worktrees": await asyncio.to_thread(self.worktrees_here, repo)}
        got = await self._ask_node(host, "worktrees", repo=repo)
        listed = got.get("worktrees") if isinstance(got, dict) else None
        if not isinstance(listed, list):
            raise RpcError(f"{host} answered its worktrees with no list")
        out = [
            {"name": str(w.get("name") or ""), "path": str(w.get("path") or ""), "occupied": bool(w.get("occupied"))}
            for w in listed
            if isinstance(w, dict) and w.get("path")
        ]
        if host in containers.container_nodes():

            def held_here() -> None:
                for w in out:
                    if not w["occupied"] and Path(w["path"]).is_dir():
                        w["occupied"] = bool(self.occupants(Path(w["path"])))

            await asyncio.to_thread(held_here)
        return {"host": host, "repo": repo, "worktrees": out}

    async def _check_occupancy_for(self, host: str, params: dict[str, Any]) -> None:
        """The anchor rule (§9 invariant 2) for a create routed to a container node on this
        machine (3c.4): the checkout is one directory here and there, and the node cannot see this
        host's sessions, so the home checks its own records — and the other container nodes' —
        before the create crosses. The node then checks its own as it always has. A machine node
        is not checked: its path is another directory."""
        if host not in containers.container_nodes():
            return
        if params.get("kind", "interactive") != "interactive" or params.get("adapter", "shell") == "shell":
            return
        directory = Path(str(params.get("dir") or "")).expanduser().resolve()
        if params.get("worktree"):
            # the same place `create` puts it — one resolution, the main checkout's, shared with it
            repo = Path(str(params.get("repo") or directory)).expanduser().resolve()
            try:
                directory = await asyncio.to_thread(worktree_path, repo, str(params["worktree"]))
            except WorktreeError:
                return  # the node's own create refuses it in its own words
        if not directory.is_dir():
            return  # the node's own create says whether it exists there
        for who in await asyncio.to_thread(self.occupants, directory):
            raise RpcError(f"{directory} already has agent session {who}; anchor rule (use a worktree)")

    async def _route_read(self, method: str, params: dict[str, Any], host: str) -> Any:
        """A read of another host's pane (§4.4a, step 4b.1): served by that host's node through the
        `read` link method, ungated, and refused as unreachable — never queued — while the link is
        down. The reply is the node's, untouched but for its addresses."""
        rid, _h = naming.split_address(str(params.get("id") or ""))
        if rid not in self.remote.get(host, {}):
            raise RpcError(f"no session {rid}@{host}")
        mux = self._node_mux(host)
        try:
            reply = await mux.request("read", timeout=ACT_TIMEOUT, rpc=method, params={**params, "id": rid})
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except (link.LinkClosed, TimeoutError) as e:
            raise RpcError(f"{host} did not answer {method}: {e or 'the link dropped'}") from None
        return naming.readdress(reply, lambda a: self._from_host(a, host))

    async def _route_act(self, method: str, params: dict[str, Any], caller: Any, host: str) -> Any:
        """An act on another host's record, gated here already: executed by that host's node and
        its verdict returned (§4.4a). Refused in words — never queued — while the link is down.
        A home-owned edit is applied to this home's copy after the node took it, so the two agree
        and the caller's reply is the home's view."""
        rid, _h = naming.split_address(str(params.get("id") or ""))
        if method not in ("create", "name_check") and rid not in self.remote.get(host, {}):
            raise RpcError(f"no session {rid}@{host}")
        if method == "create":
            await self._check_occupancy_for(host, params)
        home_copy = self.remote.get(host, {}).get(rid) if method == "close" else None
        if home_copy is not None:
            # any close clears the tick's mark; the tick writes its own after this returns (§6 rule 2, TD-237)
            home_copy.closed_for = None
            self._save(home_copy)
        mux = self._node_mux(host)
        sent = dict(params)
        sent.pop("prompt_from", None)  # the launch record's alone, and the home writes a node's (§6 rule 7)
        if rid:
            sent["id"] = rid
        for key in ("controllers", "add", "remove") if method in ("create", "set_controllers") else ():
            if sent.get(key):
                sent[key] = [self._to_host(x, host) for x in sent[key]]
        who = None if mail.is_person(caller) else self._to_host(caller, host)
        timeout: float | None = ACT_TIMEOUT
        if method == "send" and sent.get("wait"):
            timeout = None if sent.get("timeout") is None else float(sent["timeout"]) + ACT_TIMEOUT
        try:
            reply = await mux.request("act", timeout=timeout, rpc=method, params=sent, caller=who)
        except link.LinkError as e:
            raise RpcError(f"{host}: {e}") from None
        except link.LinkClosed:
            raise RpcError(
                f"the link to {host} dropped while {method} was out: its verdict is unknown — read the record"
            ) from None
        except TimeoutError:
            raise RpcError(f"{host} did not answer {method} within {timeout:g} s: its verdict is unknown") from None
        # §4.10 rule 2: *for a node's session the home sees the replica change and knows its own
        # `decide`s, so `by you` is always known*. The act itself ran at the node, on the node's own
        # bookkeeping, so the home records the word here — where it knows both the record and what
        # was pressed — or the trail would read *resolved* for every node-hosted row (review of #269).
        if method == "decide" and rid:
            self._attention_ended(f"{rid}@{host}", self._answered_by(str(params.get("behavior")), caller))
        elif method == "identity_ack" and rid:
            # the control is **Dismiss**; `identity_ack` is only its wire name, which stays because
            # it is in `NODE_ACTS` and renaming it there is a protocol change that buys a person
            # nothing (§4.8a *An alarm's answers*, TD-077 a1)
            self._attention_ended(f"{rid}@{host}", "dismissed by you", "alarm")
        reply = reply if isinstance(reply, dict) else {}
        if home_copy is not None:
            # who closed it is the home's to record (§4.5 row 5 (b), TD-265), and only once the node
            # took the close: a refused or unreachable one leaves a live record with no closer. The
            # node writes the same word from the params it is handed; an older node drops them.
            home_copy.closer = closer_of(caller, params.get("closer"), now_iso())
            self._save(home_copy)
        if reply.get("record"):
            self._take_records(host, [reply["record"]], whole=False)
            held = self.remote.get(host, {}).get(str(reply["record"].get("id"))) if method == "create" else None
            if held is not None and held.supervised:  # the home holds a node session's launch record (§6)
                # what the node made of the call — an auto-name, a worktree's resolved repo — so a
                # replay recreates this session, not a new one (review of PR #447)
                made = {**launch_params(params), "name": held.name, "repo": held.repo, "worktree": held.worktree}
                self._write_launch(f"{held.id}@{host}", held, {k: v for k, v in made.items() if v is not None})
            if method == "create" and mail.is_person(caller):
                self._lift_by_person(host, str(reply["record"].get("id") or ""))
        elif reply.get("gone") and rid:
            self._forget_remote(host, rid)
        result = reply.get("result")
        if method in HOME_EDITS:
            # the home's own copy carries the edit, and its view — addressed — is what the caller gets
            await getattr(self, f"rpc_{method}")(**params)
            result = self._view(self.remote[host][rid])
        elif method == "name_check" and isinstance(result, dict):
            for key in ("id", "holder"):  # the node's bare ids, addressed for the caller (review of PR #213)
                if isinstance(result.get(key), str) and result[key].startswith(naming.PREFIX):
                    result[key] = f"{result[key]}@{host}"
        elif isinstance(result, dict) and result.get("host") == host and result.get("id"):
            # the node answered with its view of the record: the caller gets this home's, addressed
            held = self.remote.get(host, {}).get(str(result["id"]))
            result = self._view(held) if held is not None else {**result, "id": f"{result['id']}@{host}"}
        await self._push_changes()
        return result

    async def _serve_link(self, info: Any, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One node's link, for as long as it lasts. The host name is the one sshd's forced command
        announced — it came from `authorized_keys`, never from the node — and everything the link
        may do is decided against that name."""
        host = str((info or {}).get("host") or "").strip() if isinstance(info, dict) else ""
        said_hello = False

        async def from_node(method: str, params: dict[str, Any]) -> Any:
            nonlocal said_hello
            if method == "hello":
                refusal = self._link_refusal(host, params)
                if refusal:
                    asyncio.get_running_loop().call_later(0.2, mux.close, refusal)  # after the reply is written
                    if host in hosts.nodes() and "protocol" in refusal:
                        # an authorised node on an older build: the supervisor re-provisions a container
                        self.links[host] = {"up": False, "since": now_iso(), "why": f"refused: {refusal}"}
                    raise link.LinkError(refusal)
                old = self._link_muxes.get(host)
                if old is not None and old is not mux:
                    old.close("replaced by a newer link from the same host")
                self._link_muxes[host] = mux
                self._intent_sent.pop(host, None)  # nothing is pushed to a link before its snapshot
                self.links[host] = {"up": True, "since": now_iso(), "why": "linked"}
                self._note_build(host, str(params.get("build") or ""))
                said_hello = True
                log.info("link from %s: up", host)
                if host in containers.container_nodes():
                    task = asyncio.ensure_future(self._note_reach(host))  # one docker look, off the link
                    self._bg.add(task)
                    task.add_done_callback(self._bg.discard)
                await self._push_changes()  # the overlay lifts on its cards
                return {"protocol": link.PROTOCOL, "home": self.host, "host": host}
            if not said_hello:
                raise link.LinkError("say hello first")
            if method == "ping":
                return "pong"
            if method in ("snapshot", "report"):
                taken = self._take_records(host, params.get("records") or [], whole=method == "snapshot")
                if method == "snapshot":
                    self._intent_sent[host] = {}  # the intent goes out whole once, then as it changes
                    # and the settings once per dial (§4.4a *Settings, replicated*, TD-147), after the
                    # reply: nothing is pushed to a link before its snapshot is taken
                    task = asyncio.ensure_future(self._push_settings(host))
                    self._bg.add(task)
                    task.add_done_callback(self._bg.discard)
                await self._push_changes()
                return {"taken": taken}
            if method == "gone":
                for rid in params.get("ids") or []:
                    self._forget_remote(host, str(rid))
                await self._push_changes()
                return None
            if method == "derived":
                return await self._take_derived(host, params)
            if method == "spend":
                return await self._take_spend(host, params)
            if method == "usage_report":
                await self._take_usage_report(host, params)
                return None
            if method == "forward":
                return await self._forwarded(host, params)
            if method == "cancel":
                self._cancel_forwarded(str(params.get("token") or ""))
                return None
            raise link.LinkError(f"unknown link method {method!r}")

        mux = link.Mux(reader, writer, from_node)
        why = "the link's reader failed"
        try:
            why = await mux.run()
        finally:  # however it ended: a link the home still calls up after it has gone is the worst answer
            if self._link_muxes.get(host) is mux:
                del self._link_muxes[host]
                self._intent_sent.pop(host, None)
                self.links[host] = {"up": False, "since": now_iso(), "why": why}
                log.warning("link from %s: down — %s", host, why)
                with contextlib.suppress(Exception):
                    await self._push_changes()  # its cards go `unreachable` now, not at the next tick

    async def _take_usage_report(self, host: str, params: dict[str, Any]) -> None:
        """A report one of a node's sessions made (§4.4 *A node's sessions report to their node*,
        TD-233 slice 2), merged here as this home's own sessions' are, only for a record of the
        link's host. The account is the key the node gave, which its credentials decide (a
        profile of one name may be another login here); it must be in the record's adapter's
        namespace, and it is remembered for the record's profile, so the poll keeps and shows it
        while that session lives."""
        s = self.remote.get(host, {}).get(str(params.get("id") or ""))
        key = str(params.get("account") or "")
        if s is None or not key.startswith(f"{s.adapter}:") or len(key) <= len(s.adapter) + 1:
            log.warning("a usage report from %s names no record of that host, or no account of it: dropped", host)
            return
        windows = usage_mod.clean_windows(params.get("windows"))
        if not windows or not any(r is s for r in self._usage_remote_live()):
            return  # a report for a record that is no live tool session names no key
        self._usage_remote_keys[(host, s.profile)] = key  # before the merge, whose spread reads it
        await self._usage_merge_report(s, windows, bool(params.get("fresh")), key)

    async def _push_usage_readings(self) -> None:
        """The home's reading of each account a node keyed one of its live sessions' profiles to,
        sent to that node as `usage_reading {account, reading}` (§4.4 *A node's sessions report to
        their node*) whenever it moved since that link was last told: every host's reports merge
        here, so a node's gate and its fallback's age read the whole account and not only its own
        sessions' view. A notification on the road the settings and the metered sums take, refused
        not queued: a link that is down is told again once it is back and the reading next moves."""
        if self.mode != "home":
            return
        pairs = {(h, k) for (h, _), k in self._usage_remote_keys.items()}
        for pair in [p for p in self._usage_told if p not in pairs]:
            del self._usage_told[pair]
        for host, key in sorted(pairs):
            mux, acct = self._link_muxes.get(host), self._usage_acct.get(key)
            if mux is None or acct is None or host not in self._intent_sent:
                continue  # nothing is pushed to a link before its snapshot is taken
            reading = {k: v for k, v in acct.items() if k in ("windows", "fetched", "source", "by")}
            mark = json.dumps(reading, sort_keys=True)
            told = self._usage_told.get((host, key))
            if told is not None and told[0] is mux and told[1] == mark:
                continue
            try:
                async with asyncio.timeout(agent_common.REPORT_WRITE):
                    await mux.notify("usage_reading", account=key, reading=reading)
            except (link.LinkError, link.LinkClosed, TimeoutError, OSError) as e:
                log.warning("the usage reading of %s did not reach %s: %s", key, host, e)
                continue
            self._usage_told[(host, key)] = (mux, mark)

    async def _take_derived(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """A node's tick derived reports for one of its records (§4.4a, step 4b.2): applied as this
        home's own tick applies its own — upserted under §9 invariant 10, the branch claims named
        retired — and only for a record of the link's host. A derived report is never `declared`:
        one that says it is is refused whole."""
        rid = str(params.get("id") or "")
        s = self.remote.get(host, {}).get(rid)
        if s is None:
            raise link.LinkError(f"{rid}@{host}: no such record here")
        try:
            progress = [ProgressEntry.from_dict(e) for e in params.get("progress") or []]
            findings = [FindingEntry.from_dict(e) for e in params.get("findings") or []]
        except (TypeError, ValueError, AttributeError) as e:
            raise link.LinkError(f"bad derived report: {e}") from None
        if any(e.source == "declared" for e in (*progress, *findings)):
            raise link.LinkError("a derived report is never declared: a session declares through its own `ao`")
        applied = [s.report_progress(e) for e in progress] + [s.report_finding(e) for e in findings]
        applied += [s.note_review(e) for e in progress]  # as the home's own tick does (TD-150)
        changed = any(applied) | s.retire_branch_claims(str(r) for r in params.get("retire") or [])
        if changed:
            self._save(s)
            await self._push_changes()
        return {"changed": changed}

    async def _push_settings(self, host: str | None = None) -> None:
        """The home's `settings.yml`, whole, to `host`'s link or — after a `set_settings` write — to
        every node whose link is up (§4.4a *Settings, replicated*, TD-147). A notification: a write may
        start at the home with no node involved, so it is a broadcast and never a reply. Refused, not
        queued, as the intent push is: a node whose link is down is sent the file on its next dial."""
        if self.mode != "home":
            return
        doc = await asyncio.to_thread(settings_mod.read)
        if doc is None:
            # absent or broken: not settings. `load` reads it as `{}`, which gates nothing here while
            # it is mended — sent, it would replace every node's good replica with nothing
            log.warning("settings.yml is absent or cannot be read: not sent, and each node keeps its replica")
            return
        for h, mux in list(self._link_muxes.items()):
            if host is not None and h != host:
                continue
            try:
                async with asyncio.timeout(agent_common.REPORT_WRITE):
                    await mux.notify("settings", doc=doc)
            except link.LinkClosed:
                continue
            except (link.LinkError, TimeoutError) as e:
                log.warning("the settings did not reach %s: %s — it is sent again on its next dial", h, e)

    async def _push_intent(self) -> None:
        """What changed in the home-owned fields, or the unread count, of a linked node's records
        since that node was last told (§4.4a, step 4b.2) — everything, once, after its snapshot.
        `controllers` go as stored: another host's record keeps them in its node's form here
        (step 4a). A notification, bounded like a report: a push that cannot be written gives the
        link up, and the next link's snapshot pushes everything again. Never queued."""
        for host, sent in list(self._intent_sent.items()):
            mux = self._link_muxes.get(host)
            if mux is None:
                continue
            recs = self.remote.get(host, {})
            out = []
            for rid, r in recs.items():
                d = r.to_dict()
                item = {
                    "id": rid,
                    "host": host,
                    **{k: d[k] for k in sorted(INTENT_FIELDS) if k in d},
                    "unread": r.unread(),
                    "wake_budget_spent": r.wake_budget_spent(),
                    "owed": r.owed(),  # the outcome debt rides with the unread hint (§4.10 *Outcomes*)
                    "asks_waiting": r.asks_waiting(home=self.host),  # the seat's count (§4.9b)
                }
                payload = json.dumps(item, sort_keys=True)
                if sent.get(rid) != payload:
                    out.append((rid, payload, item, r))
            for rid in [x for x in sent if x not in recs]:
                del sent[rid]
            if not out:
                continue
            try:
                async with asyncio.timeout(agent_common.REPORT_WRITE):
                    await mux.notify("intent", records=[item for _, _, item, _ in out])
            except link.LinkClosed:
                continue
            except link.LinkError as e:  # refused here, unreadable there: start the link over (TD-066)
                log.error("an intent push to %s was not written: %s", host, e)
                mux.close(f"an intent push could not be written: {e}")
                continue
            except TimeoutError:
                mux.close(f"an intent push could not be written within {agent_common.REPORT_WRITE:g} s")
                continue
            for rid, payload, _, r in out:  # marked as told only once it went
                # and only while the record is the one it was told of: a supersession taken during
                # the write cleared this id's entry, and must not see it written back (TD-326)
                if self.remote.get(host, {}).get(rid) is r:
                    sent[rid] = payload

    def _note_build(self, host: str, build: str) -> None:
        """What the node says it runs, kept on its link state — and, for a container node, whether
        it is behind what this home would provision now (§4.4a "The home supervises it": *at its
        own version*). A promote changes no protocol number, so a node can stay linked many builds
        behind, answering *unknown link method* to everything newer; the supervisor re-provisions
        one marked `stale`. A machine node's build is recorded and nothing more: the home does
        not install there."""
        state = self.links.get(host)
        if state is None:
            return
        state["build"] = build
        if host in containers.container_nodes():
            ours = containers.home_build()
            if ours and build != ours:
                if "stale" not in state:
                    log.warning(
                        "link from %s: the node runs build %s, this home's is %s", host, build or "unknown", ours
                    )
                state["stale"] = f"the node runs build {build or 'unknown'}, this home's is {ours}"
            else:
                state.pop("stale", None)

    async def _note_reach(self, host: str) -> None:
        """How a container node's sessions are reached (§4.4a "Reach", 3c.5): looked up (three
        docker calls) once when it dials in and kept on its link state, so every card of its carries `host_link.reach` —
        the Focus terminal and `ao focus` run `docker exec … tmux attach` from it, and the card's
        VS Code link attaches to that container. A failed look is a log line and no reach."""
        n = containers.container_nodes().get(host)
        state = self.links.get(host)
        if n is None or state is None or not state.get("up"):
            return
        try:
            seen = await asyncio.to_thread(containers.observe_reach, n, self.container_runner())
        except Exception as e:  # noqa: BLE001 — docker failing is a log line, never a dead link
            log.warning("link from %s: could not derive its reach: %s", host, e)
            return
        if seen and self.links.get(host) is state:
            state["reach"] = seen
            await self._push_changes()

    def _take_records(self, host: str, records: list[Any], *, whole: bool) -> int:
        """A node's snapshot or report (§4.4a): only records whose `host` is the name this link's
        key is bound to; a known one takes the node-owned fields, an unknown one is adopted whole;
        and a snapshot is the truth about which sessions that host has."""
        mine = self.remote.setdefault(host, {})
        seen: set[str] = set()
        for raw in records:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            if raw.get("host") != host:
                log.warning("link from %s: dropped a report about %s@%s", host, raw.get("id"), raw.get("host"))
                continue
            rid = str(raw["id"])
            seen.add(rid)  # listed, whether or not it could be taken: a snapshot forgets only what it omits
            try:
                if self._take_supersession(host, mine, rid, raw):
                    pass  # a new session: taken whole, the records it replaced or continued dealt with
                elif rid in mine:
                    try:
                        apply_node(mine[rid], raw)
                    except NotTheSameSession:
                        if mine[rid].state != "closed":
                            raise  # a live record that disagrees on identity is another session: refused
                        # §4.4a, §9 invariant 12: a closed record of this id is superseded by the
                        # node's new one — replaced in place, as a new session of a name replaces a
                        # finished one on one host (`_take_name`); its run log stays on its node.
                        log.info("link from %s: %s supersedes the closed record of the same id", host, rid)
                        mine[rid] = Session.from_dict(raw)
                else:
                    mine[rid] = Session.from_dict(raw)  # adopted, the replica's home-owned fields and all
            except (NotTheSameSession, TypeError, ValueError, KeyError) as e:
                log.warning("link from %s: could not take %s: %s", host, rid, e)
                continue
            self._remote_store(host).save(mine[rid])
        if whole:
            for rid in [r for r in mine if r not in seen]:
                self._forget_remote(host, rid)
        return len(seen)

    def _take_supersession(self, host: str, mine: dict[str, Session], rid: str, raw: dict[str, Any]) -> bool:
        """A node's record that `supersedes` others it has not yet told this home about (design
        §4.4a, TD-057): what the name rule and a resume did **there** — to the node's replicas,
        which hold no mail — done here to the home's own copies, which do. Once per supersession:
        the home's copy carries the same list afterwards, and a later report is an ordinary one.

        - **Replaced in place** (the same id: a fresh start under a name, `--keep-mail`, a resume
          under the same name) — the record is a new session, so it is taken **whole**, as an
          unknown one is adopted: its home-owned fields are the ones its create set, never the old
          run's `out_of_work`, `progress` or `doing`. The old run's mail moves to it when `mail`
          says so (a resume, `--keep-mail`), and otherwise goes with the old record, as it does
          on one host (`_take_name`); its attention rows end *forgotten*.
        - **Continued under another id** (a resume): the old record's mail moves to the new one,
          and the old one records `superseded_by`, so mail still addressed to it is forwarded.

        Returns whether the record was taken here (the caller then does not apply it again)."""
        told = [x for x in raw.get("supersedes") or [] if isinstance(x, dict) and x.get("id")]
        held = mine.get(rid)
        if not told or (held is not None and held.supersedes == told):
            return False
        new = Session.from_dict(raw)
        for x in told:
            old_id = str(x["id"])
            old = held if old_id == rid else mine.get(old_id)
            if old is None:
                continue  # never known here (a home that started after it went): nothing to move
            if old.suspended and not new.suspended:
                # **Except `suspended`** (§4.8a, the anchor's read of PR #371): the mark is the home's,
                # and a node's word is not one of the roads that lift it. The node refuses a session
                # from its replica's copy of the mark, but that copy is only as fresh as the last push —
                # suspended while the link was down, the name retaken there, the link back. A person's
                # own create through the home lifts it (`_route_act`), as it does on one host.
                new.suspended = dict(old.suspended)
                log.warning("link from %s: %s took the name of suspended %s — the mark stands on it", host, rid, old_id)
            if x.get("mail"):
                self._move_mail(old, new)
            elif old is held:
                self._attention_gone(old, "forgotten")
                self._scrub(self._address(old))
            if old is not held:
                # the home's field (§4.4a), as the graph addresses the successor — `_msg`'s forwarding
                # walk reads it there; the node's own close of the old record arrives by report
                old.superseded_by = self._address(new)
                self._remote_store(host).save(old)
        mine[rid] = new
        # the node's record is a new one too: what it was last told under this id was told to the
        # record it replaced, so the intent goes again whole — else a mark pushed to the old record
        # before the name was retaken reads as told and never reaches the new one (TD-326)
        self._intent_sent.get(host, {}).pop(rid, None)
        log.info("link from %s: %s supersedes %s", host, rid, ", ".join(str(x["id"]) for x in told))
        return True

    def _lift_by_person(self, host: str, rid: str) -> None:
        """A person's `create` through the home, on a node: the supersession it made is one the home
        authorised, so it lifts a suspension as a person's create or resume does on one host
        (§4.8a) — on the new record, which `_take_supersession` may have marked from the report
        that overtook this reply, and on the conversation it resumed. Only this create's own
        supersessions (`at` is its start), never an older one the record still lists."""
        mine = self.remote.get(host, {})
        rec = mine.get(rid)
        if rec is None:
            return
        for x in rec.supersedes:
            if not isinstance(x, dict) or x.get("at") != rec.created:
                continue
            old = mine.get(str(x.get("id") or ""))
            for r in (rec, old):
                if r is not None and r.suspended:
                    r.suspended = None
                    self._remote_store(host).save(r)

    def _forget_remote(self, host: str, rid: str) -> None:
        if self.remote.get(host, {}).pop(rid, None) is None:
            return
        self._drop_launch(f"{rid}@{host}")
        self._remote_store(host).delete(rid)
        address = f"{rid}@{host}"
        for last in self._subscribers.values():
            last.pop(address, None)
        self._gone.append(address)

    def _link_refusal(self, host: str, hello: dict[str, Any]) -> str | None:
        """Why this home does not take a link from `host`, or None (§4.4a "Who may connect")."""
        if self.mode != "home":
            return f"{self.host} is a node of {self.home}, not a home: there is one home, and a node takes no links"
        if not host:
            return "the link named no host (`agentorc-agent link --host <name>` in authorized_keys)"
        if host == self.host:
            return f"{host} is this home's own name: a node's key must be bound to the node's name"
        if host not in hosts.nodes():
            return f"{host} is not an authorised node: add it under `nodes:` in {self.host}'s hosts.yml"
        claimed = str(hello.get("host") or "")
        if claimed and claimed != host:
            return (
                f"this link is bound to {host}, and the node calls itself {claimed}: the name the home binds "
                "(the `--host` in authorized_keys, or the `nodes:` entry whose socket this is) and the node's "
                "`local: {name: …}` must agree"
            )
        if hello.get("protocol") != link.PROTOCOL:
            return f"link protocol {hello.get('protocol')!r} here is {link.PROTOCOL}: promote both ends to one build"
        return None
