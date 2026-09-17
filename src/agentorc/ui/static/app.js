/* agentorc client: /events patches cards by id; Focus drives xterm.js over /term/<id>.
   No framework, no build step (design §4.5). Browser mechanics per design §4.5 "Browser mechanics". */
(function () {
  const AO = (window.AO = {});
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const store = {
    get(k, d) { try { const v = localStorage.getItem("ao." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("ao." + k, JSON.stringify(v)); } catch (e) {} },
  };

  // ---- theme (token swap; system default, manual toggle remembered) ----
  function applyTheme() {
    const t = store.get("theme", null) || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = t;
  }
  applyTheme();

  // ---- the terminal's look (design goal 12, §4.6, TD-038) ----
  // One place, because it was three: the pane's font and size are here, not repeated at the call
  // site. The palette is VS Code's Dark Modern terminal, so the same Claude Code output is the same
  // colour in Focus as in the editor's terminal beside it — which is the point of goal 2, driving
  // the session in place. Goal 12 keeps the pane dark whatever the page theme is, so these are
  // literals rather than tokens: `--term` follows the theme and this must not.
  AO.TERM_OPTS = { cursorBlink: true, fontFamily: '"JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace', fontSize: 13, lineHeight: 1.2, letterSpacing: 0 };
  AO.TERM_THEME = {
    background: "#0b0e12",  // the app's own dark, not VS Code's #1f1f1f: the pane sits in this chrome
    foreground: "#cccccc", cursor: "#aeafad", cursorAccent: "#0b0e12", selectionBackground: "#264f78",
    black: "#000000", red: "#cd3131", green: "#0dbc79", yellow: "#e5e510",
    blue: "#2472c8", magenta: "#bc3fbc", cyan: "#11a8cd", white: "#e5e5e5",
    brightBlack: "#666666", brightRed: "#f14c4c", brightGreen: "#23d18b", brightYellow: "#f5f543",
    brightBlue: "#3b8eea", brightMagenta: "#d670d6", brightCyan: "#29b8db", brightWhite: "#e5e5e5",
  };

  // ---- toasts: the one error surface (design §4.5) ----
  AO.toast = function (text, ok) {
    const el = document.createElement("div");
    el.className = "toast" + (ok ? " ok" : "");
    el.textContent = text;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), ok ? 3000 : 7000);
  };

  // ---- actions: every data-act button posts to /api/sessions/<id>/<act> ----
  async function act(id, action, body) {
    // the top bar's person inbox is no session: its Reply and delete have their own routes (§4.5a)
    const r = await fetch(id === "person" ? `/api/person/${action}` : `/api/sessions/${id}/${action}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}),
    });
    if (!r.ok) { let t = r.statusText; try { t = (await r.json()).detail || t; } catch (e) {} throw new Error(t); }
    return r.json();
  }
  AO.act = act;

  // vscode:// links: hand the URL to the protocol handler without navigating this tab away
  // (a plain click replaced the Org with a blank page when the handler declined — first-use finding).
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest('a[href^="vscode://"]');
    if (!a) return;
    ev.preventDefault();
    const f = document.createElement("iframe"); f.style.display = "none"; f.src = a.href;
    document.body.appendChild(f); setTimeout(() => f.remove(), 3000);
    AO.toast("opening in VS Code…", true);
  });

  // design §4.5a **Message** / Focus Inbox **Reply** (§4.10): one composer for both, a <dialog>.
  // Resolves to what to mail, or null on Cancel / an empty body.
  AO.compose = function (o) {
    const dlg = $("#mailbox");
    $("#mailtitle").textContent = o.reply ? `Reply to ${o.to}` : `Message ${o.to}`;
    $("#mailkindrow").hidden = !!o.reply;
    $("#mailquote").textContent = o.quote ? `re: “${o.quote.length > 160 ? o.quote.slice(0, 160) + "…" : o.quote}”` : "";
    $("#mailkind").value = "note"; $("#mailabout").value = ""; $("#mailtext").value = "";
    return new Promise((resolve) => {
      dlg.addEventListener("close", () => {
        const text = $("#mailtext").value;
        resolve(dlg.returnValue === "send" && text.trim() ? { text, kind: $("#mailkind").value, about: $("#mailabout").value.trim() } : null);
      }, { once: true });
      dlg.returnValue = ""; dlg.showModal(); $("#mailtext").focus();
    });
  };

  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act], [data-copy], [data-sort]");
    if (!b) return;
    if (b.dataset.copy) { navigator.clipboard.writeText(b.dataset.copy).then(() => AO.toast("copied", true)); return; }
    if (b.dataset.sort) { setSort(b.dataset.sort); return; }
    const id = b.dataset.id, action = b.dataset.act;
    let action2 = null;  // the endpoint's name when it differs from the button's (controllers chip)
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    const details = b.closest("details"); if (details) details.open = false;
    try {
      let body = {};
      if (action === "mode") body = { unattended: !b.classList.contains("on") };
      // design §4.5a: **Drop** on a claimed progress item, and the grants chip (§4.8, TD-028 step 4)
      if (action === "drop") body = { ref: b.dataset.ref };
      if (action === "grants") body = b.classList.contains("off") ? { add: [b.dataset.grant] } : { remove: [b.dataset.grant] };
      // design §4.5a Focus **controllers** chip (§4.8, TD-036): remove one by clicking it, add one
      // by id. The agent decides what is allowed; a refusal comes back as the toast below.
      if (action === "uncontrol") { action2 = "controllers"; body = { remove: [b.dataset.who] }; }
      // design §4.5a Focus header **stops** badge (§6, TD-026): the same shape — the person types
      // a time, the agent parses it and says no if it cannot. Empty clears it, deliberately: a
      // session that should run on is a decision, not a restart.
      if (action === "stop") {
        // Filled from the record, not from the badge: the badge reads "stops Mon 06:00" once the
        // stop is not today and gains "· wrap-up sent" after the agent has asked, and both of
        // those come back as a time the agent refuses. The record's UTC instant, shown in the
        // person's own clock as a local ISO string, round-trips exactly.
        const iso = b.dataset.until;
        let now = "";
        if (iso) {
          const d = new Date(iso);
          if (!isNaN(d)) now = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        }
        const when = prompt("Stop this session at… (06:00, +8h, an ISO time; empty to clear)", now);
        if (when === null) return;
        body = { until: when.trim() };
      }
      // design §4.5a **Message**, Focus Inbox **Reply** and delete (§4.10): mail, never a send
      if (action === "message" || action === "reply") {
        const m = await AO.compose({ to: b.dataset.name || id, reply: action === "reply", quote: b.dataset.quote });
        if (!m) return;
        body = action === "reply" ? { reply_to: b.dataset.msg, text: m.text } : m;
      }
      if (action === "unmail") body = { msg: b.dataset.msg };
      if (action === "control-add") {
        const who = prompt("Which session may act on this one? (its id or name from ao status)");
        if (!who) return;
        action2 = "controllers"; body = { add: [who.trim()] };
      }
      const res = await act(id, action2 || action, body);
      if (action === "shell-here" && res.id) location.href = `/focus/${res.id}`;
      if (action === "remove") { const c = $(`#card-${CSS.escape(id)}`); if (c) c.remove(); if (location.pathname.startsWith("/focus/")) location.href = "/"; }
      if (action === "allow" || action === "deny") AO.toast(`${action}: sent through the hook`, true);
      if (action === "drop") AO.toast(`${b.dataset.ref}: dropped`, true);
      if (action === "message" || action === "reply") AO.toast(`mailed to ${(res.delivered || []).join(", ")} — lands in the inbox, nothing typed`, true);
      if (action === "unmail") AO.toast("deleted from this inbox", true);
      if (["message", "reply", "unmail"].includes(action) && typeof AO.refreshInbox === "function") AO.refreshInbox();
      if (["reply", "unmail"].includes(action) && id === "person") AO.refreshPersonInbox(true);
      if (action === "grants") AO.toast(`grants: ${(res.capabilities || []).join(", ") || "none"}`, true);
      if (action === "stop") AO.toast(res.stop_note || "no stop time: nothing will stop this session", true);
      if (action2 === "controllers") {
        AO.toast(`under: ${(res.controllers || []).join(", ") || "nobody"}`, true);
        if (typeof AO.refreshMembership === "function") AO.refreshMembership();
      }
    } catch (e) { AO.toast(`${action} failed: ${e.message}`); }
  });

  // One mail entry, as the Focus Inbox panel and the top bar's person inbox both show it (design
  // §4.5a, §4.10): sender (name, id on hover), kind, `about`, read/unread, age, an `ask`'s state,
  // Reply and delete. `owner` is the inbox it sits in — a session id, or "person". No Reply on an
  // entry the person sent: its answer is the session's, which lands in the person inbox.
  AO.mailEntry = function (e, owner) {
    const ask = e.kind === "ask" || e.kind === "conflict";
    let st = "";
    if (ask) {
      st = e.closed_by ? `answered by ${esc(e.closed_by)}`
        : e.expired_at ? "expired"
        : (e.pending || []).length ? "addressee exited · pending"
        : e.bound ? `open · bound ${esc(new Date(e.bound).toLocaleString())}` : "open";
    }
    const person = owner === "person";
    const reply = e.from === "person" ? ""
      : ` <button class="btn sm ghost" data-act="reply" data-id="${esc(owner)}" data-msg="${esc(e.id)}" data-name="${esc(e.from_name || e.from)}" data-quote="${esc(e.text)}">Reply</button>`;
    const confirmText = person ? "Delete this entry from the person inbox? The sender keeps its copy."
      : "Delete this entry from this session's inbox? The sender keeps its copy.";
    return `<div class="mail${e.read_at ? "" : " unread"}" data-msg="${esc(e.id)}">`
      + `<div class="row gap"><span class="ref" title="${esc(e.from)} · ${esc(e.from_role || "")}">${esc(e.from_name || e.from)}</span>`
      + (person && e.from_name && e.from_name !== e.from ? `<span class="st">${esc(e.from)}</span>` : "")
      + `<span class="st kind">${esc(e.kind)}</span>${e.about ? `<span class="st">re ${esc(e.about)}</span>` : ""}`
      + `<span class="grow"></span><span class="st">${e.read_at ? "read" : "unread"}</span>`
      + `<span class="st age" data-since="${esc(e.at || "")}">${fmtAge(e.at)}</span></div>`
      + (e.reply_to ? `<div class="st">reply to ${esc(e.reply_to)}</div>` : "")
      + `<div class="body">${esc(e.text)}</div>`
      + `<div class="row gap">${st ? `<span class="st${e.expired_at ? " expired" : ""}">${st}</span>` : ""}<span class="grow"></span>${reply}`
      + ` <button class="btn sm ghost" data-act="unmail" data-id="${esc(owner)}" data-msg="${esc(e.id)}" data-confirm="${confirmText}">Delete</button></div></div>`;
  };

  // design §4.5a Org top bar **person inbox** (§4.10). No session record holds it, so nothing on
  // the pushed stream carries its count: the top bar polls the `inbox` RPC (a person's read, which
  // marks nothing). The list is fetched only while the panel is open.
  AO.refreshPersonInbox = async function (list) {
    let got;
    try {
      const r = await fetch("/api/person/inbox");
      if (!r.ok) return;
      got = await r.json();
    } catch (e) { return; }
    const n = got.unread || 0, es = got.entries || [];
    const chip = $("#personunread");
    if (chip) { chip.textContent = n ? String(n) : ""; chip.classList.toggle("hidden", !n); }
    if (!list) return;
    $("#personcount").textContent = es.length ? `${n} unread · ${es.length}` : "";
    $("#personlist").innerHTML = es.length ? es.slice().reverse().map((e) => AO.mailEntry(e, "person")).join("")
      : `<div class="note">Nothing here.</div>`;
  };
  if ($("#personinbox")) {
    $("#personinbox").addEventListener("click", () => { $("#personbox").showModal(); AO.refreshPersonInbox(true); });
    $("#personclose").addEventListener("click", () => $("#personbox").close());
    // only the Org page renders the count server-side: every other page reads it at load
    if (location.pathname !== "/") AO.refreshPersonInbox(false);
    setInterval(() => AO.refreshPersonInbox($("#personbox").open), 20000);
  }

  $("#theme") && $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme;
    store.set("theme", cur === "dark" ? "light" : "dark"); applyTheme();
  });
  $("#shellbtn") && $("#shellbtn").addEventListener("click", () => {
    const d = prompt("Shell in which directory?", store.get("lastdir", "~"));
    if (!d) return; store.set("lastdir", d);
    const f = $("#shellform"); f.querySelector("[name=dir]").value = d; f.submit();
  });

  // ---- countdowns and ages tick locally; transitions arrive as deltas, never from the clock ----
  function fmtAge(iso) {
    if (!iso) return "";
    const s = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
    if (s < 60) return s + "s"; if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"; return Math.floor(s / 86400) + "d";
  }
  setInterval(() => {
    $$(".age[data-since]").forEach((el) => (el.textContent = fmtAge(el.dataset.since)));
    $$(".countdown[data-deadline]").forEach((el) => {
      if (!el.dataset.deadline) return;
      const left = Math.floor((Date.parse(el.dataset.deadline) - Date.now()) / 1000);
      el.textContent = left > 0 ? `via hook · ${Math.floor(left / 60)}m ${String(left % 60).padStart(2, "0")}s left` : "via hook · falling through to the terminal";
    });
  }, 1000);

  // ---- events websocket with backoff; a reconnect reloads the snapshot once ----
  function connectEvents(onEvent) {
    let delay = 500, reconnected = false;
    function open() {
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/events`);
      // The handshake succeeds even when the agent is down (the server accepts, then closes), so
      // "connected" means the first message, not onopen — otherwise a down agent reload-loops.
      ws.onopen = () => { delay = 500; };
      ws.onmessage = (m) => { setDown(false); if (reconnected) { location.reload(); return; } const ev = JSON.parse(m.data); if (ev.event === "usage") onUsage(ev); else onEvent(ev); };
      ws.onclose = () => { setDown(true); reconnected = true; setTimeout(open, delay); delay = Math.min(delay * 2, 10000); };
      ws.onerror = () => ws.close();
    }
    open();
  }
  // ---- usage chip: one span per profile, "5-hour% · weekly%", red at a cap (TD-001) ----
  function onUsage(ev) {
    const chip = $("#usagechip"); if (!chip || !ev.usage) return;
    let el = chip.querySelector(`[data-profile="${CSS.escape(ev.profile)}"]`);
    if (!el) { el = document.createElement("span"); el.dataset.profile = ev.profile; chip.appendChild(el); chip.appendChild(document.createTextNode(" ")); }
    const u = ev.usage, capped = u.five_hour_pct >= 100 || u.weekly_pct >= 100;
    el.textContent = `${ev.profile} ${u.five_hour_pct}%·${u.weekly_pct}%`;
    el.classList.toggle("cap", capped);
    el.title = `5-hour ${u.five_hour_pct}% (resets ${u.five_hour_resets || "?"}) · weekly ${u.weekly_pct}% (resets ${u.weekly_resets || "?"})`;
  }
  function setDown(down) {
    const dot = $("#hostdot"); if (dot) dot.classList.toggle("down", down);
    const b = $("#agentdown"); if (b) b.classList.toggle("hidden", !down);
  }

  // ---- Org (design §4.5 screen 1) ----
  // The page is one or more `.tgroup` sections, each an optional header plus its own `.grid`: one
  // per team when any live session carries a `team` badge (design §4.5a **team groups**, §4.9),
  // and one unnamed, headerless group when none does. Every rule below is per group — Urgent first
  // sorts inside a section (the lead's card first), Pinned order is stored per team.
  let sortMode = store.get("sort", "urgent");
  const sections = () => $$("#groups .tgroup");
  const pinKey = (team) => (team ? "pinned:" + team : "pinned");  // the flat page keeps the old key
  function setSort(m) { sortMode = m; store.set("sort", m); $$("[data-sort]").forEach((b) => b.classList.toggle("on", b.dataset.sort === m)); layout(); }
  function layout() {
    const box = $("#groups"); if (!box) return;
    sections().forEach((sec) => {
      const grid = $(".grid", sec), lead = sec.dataset.lead || "";
      const cards = $$(".sc", grid);
      if (sortMode === "urgent") {
        cards.sort((a, b) => (b.dataset.id === lead) - (a.dataset.id === lead) || (+a.dataset.rank - +b.dataset.rank) || a.dataset.name.localeCompare(b.dataset.name))
          .forEach((c) => grid.appendChild(c));
      } else {
        const order = store.get(pinKey(sec.dataset.team), []);
        cards.sort((a, b) => { const ia = order.indexOf(a.dataset.id), ib = order.indexOf(b.dataset.id); return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib); }).forEach((c) => grid.appendChild(c));
        store.set(pinKey(sec.dataset.team), $$(".sc", grid).map((c) => c.dataset.id));
      }
      cards.forEach((c) => c.classList.toggle("highlight", sortMode === "pinned" && c.dataset.state === "needs-you"));
    });
    applyFilter();
    const shown = $$("#groups .sc").filter((c) => !c.hidden);
    $("#count").textContent = `${shown.length} session${shown.length === 1 ? "" : "s"}`;
    $("#empty").hidden = shown.length > 0;
    const counts = {}; shown.forEach((c) => (counts[c.dataset.state] = (counts[c.dataset.state] || 0) + 1));
    $("#badges").innerHTML = [["needs-you", "needs", "needs you"], ["limited", "limited", "limited"], ["stalled?", "stalled", "stalled"]]
      .filter(([k]) => counts[k]).map(([k, cls, l]) => `<span class="pill s-${cls}"><span class="dot"></span>${counts[k]} ${l}</span>`).join("");
    syncStrip();
  }
  function applyFilter() {
    const raw = ($("#filter") ? $("#filter").value : "").trim(), cmd = $("#showcmd") && $("#showcmd").checked;
    // `team:<name>` is the form the card's team badge writes: an exact match on the badge, not a
    // substring of the card's text, so a team whose name also appears in a branch stays clean.
    const team = /^team:/i.test(raw) ? raw.slice(5).trim().toLowerCase() : null;
    const q = team === null ? raw.toLowerCase() : "";
    $$("#groups .sc").forEach((c) => {
      const hideKind = c.dataset.kind === "command" && !cmd;
      const miss = team !== null ? (c.dataset.team || "").toLowerCase() !== team : !!q && !c.textContent.toLowerCase().includes(q);
      c.hidden = hideKind || miss;
    });
    // A group with nothing left to show goes away with its header; the empty page says so once.
    sections().forEach((sec) => (sec.hidden = !$$(".sc", sec).some((c) => !c.hidden)));
  }
  // The server derives the groups on every render and every delta (only it sees the whole fleet),
  // so a badge or `controllers` change moves cards between groups here without a page reload.
  // `groups` null means the flat grid: one unnamed section, no header anywhere.
  function syncGroups(gs) {
    const box = $("#groups"); if (!box) return;
    box.classList.toggle("flat", !gs);
    const wanted = gs || [{ team: "", lead: "", ids: $$("#groups .sc").map((c) => c.dataset.id), html: "" }];
    const keep = [];
    wanted.forEach((g) => {
      let sec = sections().find((s) => s.dataset.team === g.team);
      if (!sec) {
        sec = document.createElement("section");
        sec.className = "tgroup"; sec.dataset.team = g.team;
        sec.innerHTML = '<div class="grid"></div>';
      }
      sec.dataset.lead = g.lead || "";
      let head = $(".ghead", sec);
      if (g.html) {
        if (!head) { head = document.createElement("div"); head.className = "row gap wrap ghead"; sec.prepend(head); }
        head.innerHTML = g.html;
      } else if (head) head.remove();
      const grid = $(".grid", sec);
      (g.ids || []).forEach((id) => { const c = $(`#card-${CSS.escape(id)}`); if (c && c.parentElement !== grid) grid.appendChild(c); });
      box.appendChild(sec);  // in the server's order
      keep.push(sec);
    });
    sections().forEach((sec) => {
      if (keep.includes(sec)) return;
      const home = $(".grid", keep[0]);  // a card the server did not list keeps its place on the page
      $$(".sc", sec).forEach((c) => home.appendChild(c));
      sec.remove();
    });
  }
  // ---- the Teams strip (design §4.5a Org **Teams** strip, §4.9) ----
  // Live counts come from the cards, never a second request: a team is live when a session carrying
  // its badge is (there is no team record to ask), and the cards are the fleet already, delta by
  // delta. So a start or a stop shows up in the strip the moment its sessions do.
  function syncStrip() {
    const strip = $("#teams"); if (!strip) return;
    const live = {};
    $$("#groups .sc").forEach((c) => {
      const t = c.dataset.team;
      if (t && c.dataset.state !== "exited" && c.dataset.state !== "closed") live[t] = (live[t] || 0) + 1;
    });
    // A live team's controls are on its group's card (design §4.5a **team groups**, 2026-09-16),
    // so the strip shows the definitions with nothing live, and goes away when there are none.
    const defined = new Set();
    $$(".team-row", strip).forEach((row) => {
      const n = live[row.dataset.team] || 0;
      defined.add(row.dataset.team);
      row.dataset.live = n;
      row.hidden = n > 0;
      $(".live", row).textContent = n ? `${n} live` : "stopped";
    });
    if (defined.size) strip.hidden = !$$(".team-row", strip).some((r) => !r.hidden) && !$(".warnish", strip);
    // A header re-rendered for a delta arrives with its buttons hidden: the definitions are the
    // page's, read once into the strip's rows, and this is where the two meet.
    $$("#groups .ghead [data-team-act]").forEach((b) => {
      b.hidden = !(defined.has(b.dataset.team) && live[b.dataset.team] > 0);
      b.disabled = pendingTeams.has(b.dataset.team);  // a fresh header must not re-arm a request in flight
    });
  }
  // A stop returns before its lead does (design §4.9: the members settle first, which is minutes).
  // Nothing pushes that outcome, so the page asks for it — bounded, and only while one is pending —
  // rather than leaving a failure nobody ever sees (design §4.5 "Errors"; review of PR #124).
  async function watchStop(name, lead) {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      let rows = [];
      try { rows = (await (await fetch("/api/teams")).json()).teams || []; } catch (e) { continue; }
      const row = rows.find((t) => t.name === name);
      if (!row) return;
      if (row.error) { AO.toast(`${name}: ${row.error}`); return; }
      if (!row.stopping) { AO.toast(`${name}: ${lead} stopped`, true); return; }
    }
    AO.toast(`${name}: ${lead} is still stopping — see the agent log`);
  }
  // The button is not the guard: a stop moves its members' states at once, each delta re-renders
  // the header, and the fresh Stop would be pressable while the first request is still out — a
  // second wrap-up prompt to every member (review of PR #183). The team's name is the guard.
  const pendingTeams = new Set();
  async function teamAct(name, what, btn) {
    if (pendingTeams.has(name)) return;
    pendingTeams.add(name);
    const stop = what !== "start";
    const url = `/api/teams/${encodeURIComponent(name)}/${stop ? "stop" : "start"}`;
    btn.disabled = true;
    try {
      const r = await fetch(url, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ now: what === "stopnow" }),
      });
      let o = {}; try { o = await r.json(); } catch (e) {}
      // A refused start created nothing (design §4.9): the agent's own message is the whole report,
      // in a toast, as every other RPC error on this page is (design §4.5 "Errors").
      if (!r.ok) throw new Error(o.detail || r.statusText);
      AO.toast(o.text || `${name}: ${(o.sessions || []).length} session${(o.sessions || []).length === 1 ? "" : "s"} started`, true);
      // The same two things `ao team start|stop` says and a request could not: a member the
      // definition starts interactive is out of its lead's reach (design §9 invariant 5), and the
      // lead's own stop happens after the response (review of PR #124).
      (o.out_of_reach || []).forEach((who) =>
        AO.toast(`${name}: ${who} is interactive, so its lead cannot act on it — §9 invariant 5`));
      // TD-042: a brief written for one night cannot start the next. The team started; this is a note.
      (o.unrepeatable || []).forEach((w) => AO.toast(`${name}: ${w}`));
      if (o.lead) watchStop(name, o.lead);
    } catch (e) {
      AO.toast(`${name}: ${e.message}`);
    } finally {
      pendingTeams.delete(name);
      btn.disabled = false;
      syncStrip();  // the button on the page now may not be the one that was pressed
    }
  }

  AO.org = function () {
    $$("[data-sort]").forEach((b) => b.classList.toggle("on", b.dataset.sort === sortMode));
    $("#filter").addEventListener("input", layout);
    $("#showcmd").addEventListener("change", layout);
    $("#retry").addEventListener("click", () => location.reload());
    const box = $("#groups");
    // The card's team badge filters the page to that team; pressing it again clears the box.
    box.addEventListener("click", (e) => {
      const b = e.target.closest(".badge.team"); if (!b) return;
      e.preventDefault();
      const f = $("#filter"), q = "team:" + b.dataset.team;
      f.value = f.value.trim().toLowerCase() === q.toLowerCase() ? "" : q;
      layout();
    });
    // drag to pin: HTML5 drag on cards, order saved by id per group. A card never crosses into
    // another team's grid — the badge decides the group, not where you dropped it.
    box.addEventListener("dragstart", (e) => { const c = e.target.closest(".sc"); if (c) { e.dataTransfer.setData("text/plain", c.dataset.id); c.classList.add("dragging"); } });
    box.addEventListener("dragover", (e) => { if (sortMode === "pinned") e.preventDefault(); });
    box.addEventListener("drop", (e) => {
      if (sortMode !== "pinned") return; e.preventDefault();
      const id = e.dataTransfer.getData("text/plain"), from = $(`#card-${CSS.escape(id)}`), to = e.target.closest(".sc");
      if (!from || !to || from === to || from.parentElement !== to.parentElement) return;
      const grid = to.parentElement, sec = grid.closest(".tgroup");
      grid.insertBefore(from, to);
      store.set(pinKey(sec.dataset.team), $$(".sc", grid).map((c) => c.dataset.id));
    });
    $$("#groups .sc").forEach((c) => (c.draggable = true));
    // Start is in the strip, Stop / Stop now on the team's card: one handler for both places.
    [$("#teams"), box].forEach((el) => el && el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-team-act]");
      if (b) teamAct(b.dataset.team, b.dataset.teamAct, b);
    }));
    layout();
    connectEvents((ev) => {
      if (ev.event === "session") {
        const old = $(`#card-${CSS.escape(ev.id)}`);
        const tpl = document.createElement("template"); tpl.innerHTML = ev.html.trim();
        const fresh = tpl.content.firstElementChild; fresh.draggable = true;
        if (old) old.replaceWith(fresh);
        else {
          const grid = $(".tgroup .grid");  // syncGroups below moves it into its own group
          if (sortMode === "pinned") grid.prepend(fresh); else grid.appendChild(fresh);
        }
        if (ev.groups !== undefined) syncGroups(ev.groups);
        layout();
      } else if (ev.event === "gone") {
        const c = $(`#card-${CSS.escape(ev.id)}`); if (c) c.remove();
        if (ev.groups !== undefined) syncGroups(ev.groups);
        layout();
      } else if (ev.event === "error") AO.toast(ev.text);
    });
  };


  // ---- New session: the anchor rule, shown before you press Start ----
  AO.newSession = function () {
    const dir = $("[name=dir]"), here = $("[name=where][value=here]"), wt = $("[name=where][value=worktree]"), note = $("#occupancy");
    // Declared up here, not beside `nameCheck` below: the occupancy check calls it when it moves
    // the scope to a worktree, and a `const` read before its declaration is a ReferenceError.
    const nm = $("[name=name]"), start = $("button[type=submit]"), nnote = $("#namecheck");
    let seq = 0;
    async function check() {
      const v = dir.value.trim(); const my = ++seq;
      if (!v) { note.textContent = ""; here.disabled = false; return; }
      try {
        const r = await fetch(`/api/occupancy?dir=${encodeURIComponent(v)}`); const o = await r.json();
        if (my !== seq) return;
        if (o.occupants && o.occupants.length) {
          note.innerHTML = `⚠ <b>in use</b> by ${esc(o.occupants.join(", "))} — one agent session per directory (design §9); a new worktree is selected instead.`;
          here.disabled = true; wt.checked = true; nameCheck();  // the scope moved to the repo
        } else {
          here.disabled = false;
          note.textContent = o.git ? "free · a git repo, so a worktree is available" : (o.dir ? "free" : "");
        }
      } catch (e) { note.textContent = ""; here.disabled = false; }
    }
    dir.addEventListener("input", () => { clearTimeout(dir._t); dir._t = setTimeout(check, 250); });
    dir.addEventListener("change", check);

    // Role presets and the Controllers prefill follow the directory (design §4.5a, §4.8 "Defaults
    // fill membership at launch"): the repo's `.agentorc.yml` may redefine a preset or add one, and
    // name who may act on a session started there. The list is rebuilt from /api/roles as the
    // directory changes; the picker's ticks come from the role's `controllers:` when it has any,
    // else the repo's, and a person's untick after that is a decision the form keeps.
    const role = $("#role"), rnote = $("#rolenote"), picker = $("#controllers"), grants = $("#grants");
    let rseq = 0;
    function tickControllers(names) {
      for (const c of picker.querySelectorAll("[name=controller]")) c.checked = names.includes(c.dataset.name) || names.includes(c.value);
    }
    // design §4.5a New session **Grants** checkboxes: the preset's grants used to apply unseen.
    // Ticked from the role, so a person sees the one power in the system before pressing Start —
    // and an untick after that is a decision the form keeps, exactly as Controllers works.
    function tickGrants(names) {
      if (grants) for (const g of grants.querySelectorAll("[name=grant]")) g.checked = names.includes(g.value);
    }
    function applyRole() {
      const o = role.selectedOptions[0]; if (!o) return;
      const own = (o.dataset.controllers || "").split(",").filter(Boolean);
      tickControllers(own.length ? own : (picker.dataset.default || "").split(",").filter(Boolean));
      tickGrants((o.dataset.grants || "").split(",").filter(Boolean));
      $("[name=lane]").placeholder = o.dataset.lane || "TD-027, TD-019 · or free-pick";
    }
    async function loadRoles() {
      const v = dir.value.trim(); const my = ++rseq;
      try {
        const o = await (await fetch(`/api/roles?dir=${encodeURIComponent(v)}`)).json();
        if (my !== rseq) return;
        const keep = role.value;
        role.innerHTML = "";
        for (const r of o.roles) {
          const opt = document.createElement("option");
          opt.value = r.name; opt.dataset.lane = r.lane.join(", "); opt.dataset.controllers = r.controllers.join(",");
          opt.dataset.grants = (r.grants || []).join(",");
          opt.textContent = `${r.name} [${r.source}]` + (r.grants.length ? ` · grants ${r.grants.join(", ")}` : "");
          role.appendChild(opt);
        }
        role.value = [...role.options].some((x) => x.value === keep) ? keep : "plain";
        picker.dataset.default = (o.controllers || []).join(",");
        rnote.textContent = o.error ? `⚠ ${o.error}` : (o.file ? `presets from ${o.file}` : "a preset fills the brief, lane, grants and profile it names; each can be edited before Start");
        applyRole();
      } catch (e) { /* the built-ins rendered with the page still stand */ }
    }
    role.addEventListener("change", applyRole);
    dir.addEventListener("change", loadRoles);
    dir.addEventListener("input", () => { clearTimeout(dir._r); dir._r = setTimeout(loadRoles, 400); });

    // One name, one session (design §4.1, §9 invariant 12): say what Start would do before it is
    // pressed — a live holder is a refusal, so offer Switch to instead; an exited one is replaced
    // and its run log kept. Same texts as `ao new` prints: the agent composes them (TD-030).
    let nseq = 0;
    async function nameCheck() {
      const mine = ++nseq, n = nm.value.trim(), d = dir.value.trim();
      const worktree = $("[name=where][value=worktree]").checked;
      if (!n || !d) { nnote.textContent = ""; start.disabled = false; return; }
      try {
        const q = `dir=${encodeURIComponent(d)}&name=${encodeURIComponent(n)}&worktree=${worktree}`;
        const o = await (await fetch(`/api/name_check?${q}`)).json();
        if (mine !== nseq) return;
        start.disabled = o.verdict === "live";
        if (o.verdict === "live") {
          const to = o.holder_state === "unrecorded" ? "" : ` <a class="btn sm primary" href="/focus/${encodeURIComponent(o.holder)}">Switch to</a>`;
          nnote.innerHTML = `⚠ <b>${esc(o.message)}</b>${to}`;
        } else nnote.innerHTML = o.verdict === "supersede" ? esc(o.message) : "";
      } catch (e) { nnote.textContent = ""; start.disabled = false; }
    }
    nm.addEventListener("input", () => { clearTimeout(nm._t); nm._t = setTimeout(nameCheck, 250); });
    nm.addEventListener("change", nameCheck);
    dir.addEventListener("change", nameCheck);
    for (const r of document.querySelectorAll("[name=where]")) r.addEventListener("change", nameCheck);
    // The Project picker (design §4.5a New session **Project**, §4.9): picking one narrows the
    // Directory list to that project's repos with their checkouts on this host. The paths came
    // down with the page — a project's repos do not change as you type, so there is nothing to
    // ask for. "No project" restores the registered repos and recent directories, unchanged.
    const proj = $("#project"), datalist = $("#recent"), pnote = $("#projectnote");
    const allDirs = $$("option", datalist).map((o) => o.value);
    const options = (vals) => (datalist.innerHTML = vals.map((v) => `<option value="${esc(v)}">`).join(""));
    function applyProject() {
      if (!proj) return;
      const o = proj.selectedOptions[0];
      let repos = [];
      try { repos = JSON.parse((o && o.dataset.repos) || "[]"); } catch (e) { repos = []; }
      if (!o || !o.value) { options(allDirs); pnote.textContent = "optional: the repos in reach, and a Project block naming them in front of the brief"; return; }
      const here = repos.filter((r) => r.path), away = repos.filter((r) => !r.path);
      options(here.map((r) => r.path));
      if (!dir.value.trim() && here.length) { dir.value = here[0].path; check(); loadRoles(); nameCheck(); }
      const mine = here.some((r) => r.path === dir.value.trim());
      pnote.textContent =
        `${here.length} repo${here.length === 1 ? "" : "s"} on this host: ${here.map((r) => r.repo).join(", ") || "none"}`
        + (away.length ? ` · ${away.map((r) => `${r.repo} is on ${r.hosts.join(", ")} — out of reach until phase 2`).join("; ")}` : "")
        + (here.length > 1 ? " · the brief gets the Project block naming them" : "")
        + (mine || !here.length ? "" : " · this directory is not one of them, so none is home");
    }
    if (proj) proj.addEventListener("change", applyProject);
    dir.addEventListener("change", applyProject);

    check();  // both once at load: a prefilled directory and a prefilled name are checked too
    nameCheck();
    applyProject();
    applyRole();  // the ticks the page rendered are the repo's; a role picked later may narrow them
  };

  // ---- Focus ----
  AO.focus = function (s) {
    const id = s.id;
    // scrollback: 0 — tmux owns the history (TD-022). The bridge sets `mouse on` on the session, so
    // tmux asks for mouse tracking and xterm.js forwards the wheel to it (copy mode, its history);
    // a local buffer would only ever hold stale repaints for the wheel to land on when tmux is not
    // tracking. Shift+PageUp/PageDown below are the keyboard path; Shift+drag selects locally.
    const term = new Terminal({ ...AO.TERM_OPTS, theme: { ...AO.TERM_THEME }, scrollback: 0 });
    const fit = new FitAddon.FitAddon(); term.loadAddon(fit);
    term.open($("#term")); fit.fit();
    let ws, delay = 500, paneGone = false;
    // The pane is gone for good: end the terminal and stop reconnecting. The events push says so
    // before any reconnect could, and the server's 4404 says so too (TD-029).
    function endTerm(text) {
      paneGone = true;
      if (ws) { try { ws.onclose = null; ws.close(); } catch (e) { /* already closing */ } }
      term.write(`\r\n\x1b[90m[agentorc] ${text}\x1b[0m\r\n`);
    }
    function openTerm() {
      if (paneGone) return;
      const cols = Number.isFinite(term.cols) && term.cols > 0 ? term.cols : 120, rows = Number.isFinite(term.rows) && term.rows > 0 ? term.rows : 32;
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/term/${encodeURIComponent(id)}?cols=${cols}&rows=${rows}`);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => { ws.send(JSON.stringify({ resize: [cols, rows] })); };
      // The backoff resets on pane output, never on open (TD-029): a connection the server accepts
      // and then ends is not a working terminal, and resetting there retried twice a second forever.
      ws.onmessage = (m) => { delay = 500; term.write(typeof m.data === "string" ? m.data : new Uint8Array(m.data)); };
      let opened = false;
      ws.addEventListener("open", () => { opened = true; });
      ws.onclose = (e) => {
        if (paneGone) return;  // the push already ended it
        if (e.code === 4404) { endTerm("no terminal for this session"); return; }  // final, no retry
        // Say what happened: 1006 before open = the handshake never reached the server (a proxy or
        // port forward that drops websockets is the usual cause); after open = the server closed.
        const why = e.code === 1006 && !opened ? "websocket handshake failed (code 1006) — does your route to the UI pass websockets? ssh -L does"
          : `closed (code ${e.code}${e.reason ? ", " + e.reason : ""})`;
        term.write(`\r\n\x1b[90m[agentorc] terminal ${why} — retrying in ${Math.round(delay / 1000) || 1}s\x1b[0m\r\n`);
        setTimeout(openTerm, delay); delay = Math.min(delay * 2, 10000);
      };
    }
    if (s.state === "closed" || s.pane === false) { paneGone = true; term.write("\x1b[90m[agentorc] this session's pane is gone (killed, closed, or the tmux server restarted).\x1b[0m\r\n"); }
    else openTerm();
    term.onData((d) => ws && ws.readyState === 1 && ws.send(d));
    // Copy / paste: Ctrl+C with a selection copies (no ^C), Ctrl+Shift+C copies, Ctrl+Shift+V and
    // right-click paste; the header buttons do the same for discoverability. Clipboard access
    // needs a secure context (https or localhost) — ssh -L to 127.0.0.1 qualifies.
    const copySel = () => { const t = term.getSelection(); if (t) navigator.clipboard.writeText(t).then(() => AO.toast("copied", true), () => AO.toast("clipboard blocked (needs https or localhost)")); return !!t; };
    const pasteClip = () => navigator.clipboard.readText().then((t) => { if (t && ws && ws.readyState === 1) term.paste(t); }, () => AO.toast("clipboard blocked (needs https or localhost)"));
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      if (e.ctrlKey && e.shiftKey && (e.key === "C" || e.key === "c")) { copySel(); return false; }
      if (e.ctrlKey && e.shiftKey && (e.key === "V" || e.key === "v")) { pasteClip(); return false; }
      if (e.ctrlKey && !e.shiftKey && (e.key === "c" || e.key === "C") && term.hasSelection()) { copySel(); term.clearSelection(); return false; }
      // Plain Ctrl+V pastes text too: passed through, Claude Code reads ^V as "paste an image from
      // the clipboard", which over ssh only produces a "try scp" message (first-use finding).
      if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === "v" || e.key === "V")) { pasteClip(); return false; }
      if (e.shiftKey && e.key === "Insert") { pasteClip(); return false; }
      if (e.shiftKey && (e.key === "PageUp" || e.key === "PageDown")) { ws && ws.readyState === 1 && ws.send(JSON.stringify({ scroll: e.key === "PageUp" ? "up" : "down" })); return false; }
      return true;
    });
    $("#term").addEventListener("contextmenu", (e) => { e.preventDefault(); pasteClip(); });
    $("#tcopy").addEventListener("click", () => { if (!copySel()) AO.toast("select text in the terminal first (Shift+drag: plain drag goes to tmux)"); });
    $("#tpaste").addEventListener("click", pasteClip);
    new ResizeObserver(() => { fit.fit(); ws && ws.readyState === 1 && ws.send(JSON.stringify({ resize: [term.cols, term.rows] })); }).observe($("#term"));
    term.focus();

    const compose = $("#compose");
    compose.addEventListener("keydown", (e) => { if (e.key === "Escape") { term.focus(); } if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#send").click(); });
    $("#send").addEventListener("click", async () => {
      const text = compose.value; if (!text.trim()) return;
      try { await act(id, "send", { text }); compose.value = ""; term.focus(); } catch (e) { banner(`Send failed: ${e.message}`); }
    });

    function banner(text) { const b = $("#fbanner"); b.textContent = text; b.classList.remove("hidden"); setTimeout(() => b.classList.add("hidden"), 7000); }
    function render(v) {
      const cls = v.state_class, scraped = v.scraped ? " scraped" : "";
      let head = `<span class="pill s-${cls}${scraped}"><span class="dot"></span>${v.state_label}</span>`;
      const p = v.pending;
      if (v.state === "needs-you" && p && p.kind === "permission") {
        head += ` <button class="btn sm primary" data-act="allow" data-id="${id}">Allow</button> <button class="btn sm" data-act="deny" data-id="${id}">Deny</button> <span class="meta">${esc(p.text)}</span> <span class="meta countdown" data-deadline="${p.deadline || ""}"></span>`;
        compose.disabled = true; $("#composehint").textContent = "a permission is pending: answer above";
      } else if (v.state === "needs-you" && p) {
        head += ` <span class="meta">${esc(p.kind)}: ${esc(p.text)}</span>`;
        compose.disabled = true; $("#composehint").textContent = "answer in the terminal above";
      } else if (v.external) { compose.disabled = true; $("#composehint").textContent = "started outside agentorc: a read-only card (no terminal, no controls)";
      } else if (v.state === "exited" || v.state === "closed" || v.state === "unreachable") {
        // There is no turn to start or steer: the pane is dead or out of reach. The composer used
        // to sit enabled here and say nothing, which was merely useless; saying "starts a new
        // turn" at a dead session would be a lie, so the state that made the hint necessary is
        // the state that has to be excluded from it. The exited banner below offers the two real
        // next steps (Resume this conversation, New session here). TD-047.
        compose.disabled = true;
        // The banner below offers **Resume this conversation** only on `exited` with an adapter id
        // (a closed session gets New session here and Forget), so the hint must not promise it.
        $("#composehint").textContent = v.state === "unreachable"
          ? "the host agent cannot be reached: nothing can be sent until it is back"
          : v.state === "exited" && v.adapter_id
            ? "this session's process has ended: resume the conversation or start a new one, below"
            : "this session's process has ended: start a new session here, below";
      } else {
        // design §4.3: one button, two jobs. A message to an `idle` session starts a turn; a
        // message to a `working` one steers the turn in flight. Both are the same paste and the
        // same Enter — only the person's intent differs, so it is said in the label and the hint
        // rather than in a second control (§4.5a still governs what controls exist). TD-047.
        compose.disabled = false;
        // `stalled?` is a `working` session that stopped producing output (§4.2) — a turn already
        // in flight, not an idle one — so it steers too, and the hint says the state is uncertain
        // rather than pretending otherwise. `limited` is its own case: §4.2 says nothing the
        // person does unblocks a cap, so it must not claim a turn starts now; the paste lands and
        // waits, and §4.5a's controls for it are Switch profile and Wait.
        compose.disabled = false;
        const steering = v.state === "working" || v.state === "stalled?";
        $("#send").textContent = steering ? "→ Steer" : "→ Send";
        $("#composehint").textContent =
          v.state === "limited" ? "the profile is at its cap: what you send waits until the window resets"
          : v.state === "stalled?" ? "steers the turn in flight — this session looks stalled, so it may not be read until it moves"
          : steering ? "steers the turn in flight — this session is working, and Claude Code queues what you type"
          : "starts a new turn";
      }
      $("#fstate").innerHTML = head;
      // An exited session keeps its dead pane on purpose (exit code, last lines, run log); say so
      // and offer the two useful next steps instead of leaving tmux's "Pane is dead" to explain it.
      const ex = $("#fexited");
      if (v.state === "exited" || v.state === "closed") {
        const code = v.exit_code == null ? "" : ` (exit code ${v.exit_code})`;
        const q = `dir=${encodeURIComponent(v.dir || "")}&adapter=${encodeURIComponent(v.adapter || "claude-code")}`;
        const kept = v.state === "exited" && v.pane !== false;  // a kill/close destroys the pane (TD-023)
        ex.innerHTML = `This session's process has ${esc(v.state)}${esc(code)}. ${kept ? "The pane is kept so its last screen and run log stay readable." : "Its pane is gone (killed, or the tmux server restarted); the run log stays readable."} `
          + (v.adapter_id && v.state === "exited" ? `<a class="btn sm primary" href="/new?${q}&resume=${encodeURIComponent(v.adapter_id)}">Resume this conversation</a> ` : "")
          + `<a class="btn sm" href="/new?${q}">New session here</a> <button class="btn sm ghost" data-act="remove" data-id="${id}">Forget</button>`;
        ex.classList.remove("hidden");
      } else ex.classList.add("hidden");
      $("#adapter_id").textContent = v.adapter_id || "—";
      $("#last_output").textContent = v.last_output ? fmtAge(v.last_output) + " ago" : "—";
      if (v.git) {
        $("#gitline").textContent = v.git.branch + (v.git.ahead ? ` · ${v.git.ahead} ahead` : "") + (v.git.behind ? ` · ${v.git.behind} behind` : "");
        $("#gitfiles").innerHTML = v.git.files.length ? v.git.files.map((f) => `<div>${esc(f)}</div>`).join("") : '<div class="muted">clean</div>';
      }
      renderReports(v);
      renderInbox(v);
      renderGrants(v);
      renderStop(v);
      renderMembership(v);
      const checks = v.ready || [];
      $("#checks").innerHTML = checks.map(([n, ok]) => `<div class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"} ${esc(n)}</div>`).join("");
      $("#closebtn").disabled = !(checks.length && checks.every(([, ok]) => ok) && ["idle", "exited"].includes(v.state));
      $$("[data-act=mode]").forEach((b) => { b.classList.toggle("on", !!v.unattended); b.textContent = v.unattended ? "unattended" : "interactive"; });
    }
    // design §4.5a **Reports** / **grants** chip (§4.8, TD-028 step 4). The lists come from the
    // pushed record, so a `progress` or `finding` call from anywhere shows up here without a reload.
    function renderReports(v) {
      const progress = v.progress || [], findings = v.findings || [];
      $("#reportscard").classList.toggle("hidden", !(progress.length || findings.length));
      $("#progresslist").innerHTML = progress.map((p) => {
        const derived = (p.source || "declared") !== "declared";
        const pr = p.pr ? ` <span class="st">→ #${esc(p.pr)}</span>` : "";
        const why = p.why ? ` <span class="st">${esc(p.why)}</span>` : "";
        const drop = p.status === "claimed"
          ? ` <button class="btn sm ghost" data-act="drop" data-id="${id}" data-ref="${esc(p.ref)}" data-confirm="Drop ${esc(p.ref)}? It is recorded as dropped by you.">Drop</button>` : "";
        return `<div class="rep${derived ? " derived" : ""}"><span class="ref" title="${derived ? "derived by the agent" : "declared by the session"}">${esc(p.ref)}</span>`
          + `<span class="st ${esc(p.status)}">${esc(p.status)}</span>${pr}${why}<span class="grow"></span><span class="st age" data-since="${esc(p.at || "")}">${fmtAge(p.at)}</span>${drop}</div>`;
      }).join("");
      $("#findinglist").innerHTML = findings.map((f) => {
        const derived = (f.source || "declared") !== "declared";
        const pri = f.priority ? ` <span class="st">${esc(f.priority)}</span>` : "";
        return `<div class="rep${derived ? " derived" : ""}"><span class="ref">${esc(f.ref)}</span><span class="st">filed</span>${pri}`
          + `<span class="grow"></span><span class="st age" data-since="${esc(f.at || "")}">${fmtAge(f.at)}</span></div>`;
      }).join("");
    }
    // design §4.5a Focus side panel **Inbox** (§4.10). Bodies never ride the pushed record ("A
    // bounded body"): the record carries `unread` and the `mail` marks, and when those change the
    // panel fetches the entries again through the `inbox` RPC — a person's read, which marks nothing.
    let inboxSig = null, inboxSoon = null, inboxFirst = true;
    function renderInbox(v) {
      const sig = JSON.stringify([v.unread || 0, v.mail || {}]);
      if (sig === inboxSig) return;
      inboxSig = sig; refreshInbox();
    }
    AO.refreshInbox = refreshInbox;
    function refreshInbox() {
      clearTimeout(inboxSoon);
      inboxSoon = setTimeout(async () => {
        let got;
        try {
          const r = await fetch(`/api/sessions/${encodeURIComponent(id)}/inbox`);
          if (!r.ok) return;
          got = await r.json();
        } catch (e) { return; }
        const es = got.entries || [];
        $("#inboxcard").classList.toggle("hidden", !es.length);
        $("#inboxcount").textContent = es.length ? `${got.unread} unread · ${es.length}` : "";
        $("#inboxlist").innerHTML = es.slice().reverse().map((e) => AO.mailEntry(e, id)).join("");
        if (inboxFirst && location.hash === "#inbox" && es.length) $("#inboxcard").scrollIntoView({ block: "nearest" });
        inboxFirst = false;
      }, 150);
    }
    // design §4.5a controllers chip + Members list (§4.8, TD-036). Both are membership, which is
    // read *across* records: this session's delta carries them, but a change on another session
    // (a controller renamed, removed, or gone) does not reach here as a delta for us — hence the
    // debounced re-read below. Without it the chip keeps saying who controlled this session at
    // page load, which is the one thing a membership display must never do.
    function renderMembership(v) {
      const el = $("#fcontrollers");
      if (el) {
        const cs = v.under || [];
        el.innerHTML = (cs.length
          ? "under " + cs.map((c) => `<button class="chip${c.gone ? " scraped" : ""}" data-act="uncontrol" data-id="${esc(id)}" data-who="${esc(c.id)}" title="${esc(c.id)} — click to remove it as a controller${c.gone ? " (its session is gone)" : ""}">${esc(c.name)} ×</button>`).join("")
          : `<span class="note">no controller — nobody may act on this session</span>`)
          + ` <button class="btn sm ghost" data-act="control-add" data-id="${esc(id)}" title="add a controller">+</button>`;
      }
      const box = $("#members"); if (!box) return;
      const ms = v.members || [];
      box.innerHTML = `<div class="h2">Members</div>`
        + (ms.length
          ? ms.map((m) => `<div class="row gap"><a class="name" href="/focus/${encodeURIComponent(m.id)}">${esc(m.name)}</a>`
              + `<span class="meta">${esc(m.state || "")}</span>`
              + (m.lane ? `<span class="meta">${esc(m.lane)}</span>` : "") + `<span class="grow"></span>`
              + (m.report ? `<span class="meta">${esc(m.report)}</span>` : "") + `</div>`).join("")
          : `<div class="note">no members yet — <code>ao control ${esc(v.name || "")} add &lt;session&gt;</code>, or the controllers chip on a session's Focus</div>`)
        + `<div class="note">The sessions this one may act on. It needs both the grant and a place in each session's controllers (design §4.8).</div>`;
    }
    let membershipSoon = null;
    AO.refreshMembership = refreshMembership;
    function refreshMembership() {
      clearTimeout(membershipSoon);
      membershipSoon = setTimeout(async () => {
        try {
          const all = await (await fetch("/api/sessions")).json();
          const mine = all.find((x) => x.id === id);
          if (mine) renderMembership(mine);
        } catch (e) { /* the banner already covers a down agent */ }
      }, 400);
    }
    // design §4.5a Focus header **stops** badge (§6, TD-026): the one place a stop time can be
    // changed after the session started. The agent parses the time and refuses an interactive
    // session, so this only asks — the same division as the grants and controllers chips.
    function renderStop(v) {
      const el = $("#fstop"); if (!el) return;
      el.hidden = !v.unattended;  // a flip to interactive takes the control away, not just the time
      el.textContent = v.stop_note || "no stop time";
      el.classList.toggle("off", !v.stop_note);
      el.dataset.until = v.run_until || "";  // the record's own value: what the edit box is filled from
    }
    function renderGrants(v) {
      const el = $("#fgrants"); if (!el) return;
      const all = (el.dataset.grants || "").split(",").filter(Boolean), held = v.capabilities || [];
      el.innerHTML = all.map((g) => {
        const on = held.includes(g);
        const title = on ? `revoke ${g} — it lets this session act on other sessions` : `grant ${g}: this session could send to, kill and close other sessions`;
        return `<button class="badge${on ? "" : " off"}" data-act="grants" data-id="${id}" data-grant="${esc(g)}" title="${esc(title)}"`
          + `${on ? ` data-confirm="Revoke ${esc(g)} from this session?"` : ` data-confirm="Grant ${esc(g)}? It lets this session send to, kill and close other sessions."`}>${esc(g)}</button>`;
      }).join("");
    }
    render(s);
    connectEvents((ev) => {
      if (ev.event === "session" && ev.id === id) {
        // The pushed delta is authoritative and arrives before any reconnect (TD-029): a closed or
        // pane-less session ends the terminal here, rather than letting it discover it by retrying.
        if (!paneGone && (ev.session.state === "closed" || ev.session.pane === false)) {
          endTerm("this session's pane is gone (see the banner).");
        }
        render(ev.session);
        // Focus is open on it, so a finish here is seen the moment it happens (TD-017)
        if (ev.session.unseen) act(id, "seen", {}).catch(() => {});
      } else if (ev.event === "session" || ev.event === "gone") {
        refreshMembership();  // another session changed: it may be a controller or a member of ours
      }
      if (ev.event === "gone" && ev.id === id) banner("session removed");
    });
  };
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
})();
