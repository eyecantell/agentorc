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

  // The WebGL renderer (TD-038 (c)): crisper and lighter than the DOM renderer's element per cell,
  // and what VS Code's terminal uses. Without WebGL, or when the GPU takes the context back, the
  // DOM renderer simply stays or returns, so this can only ever improve the pane.
  AO.termRenderer = function (term) {
    if (typeof WebglAddon === "undefined") return;
    try {
      const gl = new WebglAddon.WebglAddon();
      gl.onContextLoss(() => gl.dispose());
      term.loadAddon(gl);
    } catch (e) { /* no WebGL here: the DOM renderer draws the pane */ }
  };
  // xterm.js measures a cell once, at open; the bundled face (app.css) may still be on its way, and
  // a cell measured on the fallback font leaves every glyph misplaced once it lands. So when the
  // face was not ready at open, measure again when it is. Two sets, because xterm.js ignores an
  // option set to the value it already has.
  AO.termFont = function (term, fit) {
    const spec = `${AO.TERM_OPTS.fontSize}px "JetBrains Mono"`;
    if (!document.fonts || document.fonts.check(spec)) return;
    document.fonts.load(spec).then(() => {
      term.options.fontFamily = "monospace";
      term.options.fontFamily = AO.TERM_OPTS.fontFamily;
      fit.fit();
    }).catch(() => { /* the fallback stack stays: still a monospace pane */ });
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

  // the editor button (vscode://, or the person's own `open_in:` scheme, design §5): hand the URL to
  // the protocol handler without navigating this tab away (a plain click replaced the Org with a
  // blank page when the handler declined — first-use finding).
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a.editor");
    if (!a) return;
    ev.preventDefault();
    const f = document.createElement("iframe"); f.style.display = "none"; f.src = a.href;
    document.body.appendChild(f); setTimeout(() => f.remove(), 3000);
    AO.toast(`opening in ${a.dataset.label || "the editor"}…`, true);
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
    const b = ev.target.closest("[data-act], [data-copy]");
    if (!b) return;
    if (b.dataset.copy) { navigator.clipboard.writeText(b.dataset.copy).then(() => AO.toast("copied", true)); return; }
    const id = b.dataset.id, action = b.dataset.act;
    let action2 = null;  // the endpoint's name when it differs from the button's (controllers chip)
    // design §4.5a **Inbox row: state** (TD-069 step 2): a state row on the Inbox page carries the
    // card's own controls, so the press goes to the card's own route — and the row, which is the
    // state and not a copy of it, leaves the moment the state is answered.
    const staterow = b.closest(".staterow");
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    // A choice made in a row's *more ▾* or *Snooze* menu folds that menu — and only a menu: the
    // nearest `<details>` of any kind used to be closed, and a control that sits in no menu (an FYI
    // row's Dismiss, the snoozed list's Unsnooze) has the *section* as its nearest one, so dismissing
    // an entry closed the FYI list under the person (Paul, 2026-09-20; TD-079).
    const menu = b.closest("details.more"); if (menu) menu.open = false;
    try {
      let body = {};
      // The Focus header's toggle carries the mode it is drawn for (TD-096: it is re-drawn in place
      // from the pushed delta, and `.btn.on` is a style); a card's *more* entry carries `.on`.
      if (action === "mode") body = { unattended: "unattended" in b.dataset ? b.dataset.unattended !== "1" : !b.classList.contains("on") };
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
      // design §4.5a **Inbox row: suggested answers** (§4.10, TD-070): a press is an ordinary
      // reply — the same route, the same RPC, the same wake — and what it sends is **the index**,
      // never the label. The server looks the text up from the entry it holds, so a tampered DOM
      // cannot make the person "say" something else under a given index, and the RPC re-checks
      // that the two agree. No confirm: a reply is mail, and a wrong press is followed by another.
      if (action === "answer") { action2 = "reply"; body = { reply_to: b.dataset.msg, answer: Number(b.dataset.index) }; }
      if (action === "unmail") body = { msg: b.dataset.msg };
      // design §4.5a **Inbox row: identity alarm** (§4.8a): a record's list, or — with no id — the
      // host's own. A person's act; the agent refuses it to every session. The control is
      // **Dismiss** and the wire name is `identity_ack`: a wire name is not a control, so the
      // rename of 2026-09-20 did not touch it (§4.5a).
      if (action === "identity_ack") body = { id: b.dataset.who || "" };
      // §4.8a *An alarm's answers* (TD-077 a2): **Suspend** — a person's own act on a session, and
      // the one control on an alarm row that **leaves the row standing**: it stops the session, it
      // does not answer the alarm. The confirm is the page's own words (the global gate reads
      // `data-confirm` before this runs), and `why` is left to the agent, which composes it from
      // the alarm's own fields — a page that wrote its own reason would be writing the record.
      if (action === "suspend") body = { id: b.dataset.who || "" };
      // §4.8a *An alarm's answers* (TD-077 b): **Log TD** — an answer, so the row goes. The agent
      // picks the controller and writes the words; the page sends only whose alarms they are.
      if (action === "identity_log") body = { id: b.dataset.who || "" };
      // design §4.5a **Inbox row** controls (§4.10, TD-069 step 1): the person's own acts on their
      // own inbox. Each posts to `/api/person/<action>`, which calls the RPC caller-less; the agent
      // is the one that decides what may be done, and its refusal comes back as a toast.
      if (["pause", "resume", "gowithit"].includes(action)) body = { msg: b.dataset.msg };
      if (action === "unsnooze") { action2 = "snooze"; body = { msg: b.dataset.msg }; }  // no `until` clears it
      if (action === "snooze") {
        const until = snoozeUntil(b.dataset.when);
        if (!until) return;
        body = { msg: b.dataset.msg, until };
      }
      // design §4.10 *The Inbox is a queue* (TD-079 step 2): **Dismiss** — the one way a `note`,
      // an outcome debt or a trail entry leaves. A list of ids, because *Dismiss all* sends the
      // ones on screen; one press sends a list of one, through the same route and the same RPC.
      if (action === "dismiss") body = { msg: [b.dataset.msg] };
      // §4.5a **Inbox row: state**: a state row's snooze. It is keyed on the record **and the row
      // kind** — the home has no mail entry to hang it on — and no `until` is the clear.
      if (action === "attention_snooze") {
        body = { id: b.dataset.sid, kind: b.dataset.row };
        if (b.dataset.when) {
          const until = snoozeUntil(b.dataset.when);
          if (!until) return;
          body.until = until;
        }
      }
      if (action === "control-add") {
        const who = prompt("Which session may act on this one? (its id or name from ao status)");
        if (!who) return;
        action2 = "controllers"; body = { add: [who.trim()] };
      }
      // design §4.5a **Resume** / **Resume with changes…** / **Reopen and push** (TD-081 step 2).
      // One press: the server builds the create from the record and answers either the new
      // session's id or the filled-in form to finish by hand — *it is not a guess*. **Resume with
      // changes…** and **Reopen and push** are the same route: the first asks for the form
      // outright, the second adds the page's own first prompt.
      if (action === "resume-form") {
        const r = await act(id, "resume", { form: true });
        location.href = r.form || `/new`;
        return;
      }
      if (action === "resume" || action === "reopen-push") {
        const r = await act(id, "resume", { push: action === "reopen-push" });
        if (r.form) { AO.toast(`resume needs the form: ${r.why}`, true); location.href = r.form; return; }
        AO.toast("resumed — same name, same record, its mail came with it", true);
        location.href = `/focus/${r.id}`;
        return;
      }
      const res = await act(id, action2 || action, body);
      if (action === "shell-here" && res.id) location.href = `/focus/${res.id}`;
      if (action === "remove") { const c = $(`#card-${CSS.escape(id)}`); if (c) c.remove(); if (location.pathname.startsWith("/focus/")) location.href = "/"; }
      if (action === "allow" || action === "deny") AO.toast(`${action}: sent through the hook`, true);
      if (action === "drop") AO.toast(`${b.dataset.ref}: dropped`, true);
      if (action === "message" || action === "reply") AO.toast(`mailed to ${(res.delivered || []).join(", ")} — lands in the inbox, nothing typed`, true);
      if (action === "answer") AO.toast(`answered ${(res.delivered || []).join(", ")} — the reply is the answer you pressed`, true);
      if (action === "unmail") AO.toast(res.declined ? "declined — the sender is told (design §4.10)" : "deleted from this inbox", true);
      if (["message", "reply", "answer", "unmail"].includes(action) && typeof AO.refreshInbox === "function") AO.refreshInbox();
      if (action === "snooze") AO.toast("snoozed — it comes back at that time; the sender is not told", true);
      if (action === "unsnooze") AO.toast("back in its section", true);
      if (action === "pause") AO.toast("paused — the sender is told not to take its default yet", true);
      if (action === "resume") AO.toast("resumed — the clock runs again, with what was left", true);
      if (action === "gowithit") AO.toast("go with it — the sender takes its default now", true);
      // the wire name stays `identity_ack`; the control is **Dismiss** (§4.5a, renamed 2026-09-20)
      if (action === "identity_ack") AO.toast("dismissed — the agent's log keeps every alarm, a line each", true);
      if (action === "identity_log") AO.toast(`logged → ${(res.to && (res.to.name || res.to.id)) || b.dataset.to || "its controller"}: it owes you an outcome on them`, true);  // `to` is {id, name}
      if (action === "suspend") AO.toast(`${b.dataset.name || "it"} is suspended — only you lift it, by resuming it or forgetting it`, true);
      if (action === "dismiss") AO.toast(`dismissed ${(res.dismissed || body.msg || []).length || 1} — the sender is told where one was owed`, true);
      if (action === "attention_snooze") AO.toast(res.snoozed_until ? "snoozed — the row comes back at that time; the state itself is untouched" : "back in its section", true);
      // the state is answered, so the row is gone: it is taken out here rather than waited for, and
      // the refresh below puts back whatever the record actually says. **Suspend is the exception**
      // (§4.8a): it acts on the session and *leaves the row standing* — the alarm is still there to
      // be answered — so the row is refreshed in place rather than taken out from under the person.
      if (staterow && action !== "suspend") staterow.remove();
      if ((staterow || id === "person") && typeof AO.refreshInboxPage === "function") {
        // the control that was pressed is about to go with its row, and while it holds the focus
        // the refresh below would politely decline to redraw the section it sits in
        if (document.activeElement === b) b.blur();
        AO.refreshInboxPage();
      }
      if (action === "grants") AO.toast(`grants: ${(res.capabilities || []).join(", ") || "none"}`, true);
      if (action === "stop") AO.toast(res.stop_note || "no stop time: nothing will stop this session", true);
      if (action2 === "controllers") {
        AO.toast(`under: ${(res.controllers || []).join(", ") || "nobody"}`, true);
        if (typeof AO.refreshMembership === "function") AO.refreshMembership();
      }
    } catch (e) {
      // Answered twice — two tabs, or the tool timed out into its own dialog between the poll and
      // the press — is not a failure to shout about: the state is simply no longer pending, and
      // the refresh below shows what it is now (design §4.5a **Inbox row: state**).
      if (staterow && /no pending permission/i.test(e.message)) {
        AO.toast("already answered — nothing was sent twice", true);
        staterow.remove();
        if (typeof AO.refreshInboxPage === "function") AO.refreshInboxPage();
        return;
      }
      AO.toast(`${action === "identity_log" ? "Log TD" : action} failed: ${e.message}`);  // a control is not its wire name
      if (staterow && typeof AO.refreshInboxPage === "function") AO.refreshInboxPage();  // put the row back
    }
  });

  // One mail entry, as the Focus Inbox panel shows it (design §4.5a **Focus Inbox**, §4.10):
  // sender (name, id on hover), kind, `about`, read/unread, age, an `ask`'s state, Reply and
  // delete. `owner` is the session whose inbox it sits in — the person inbox has the Inbox page's
  // rows instead, since 2026-09-19 (TD-069 step 1). No Reply on an entry the person sent: its
  // answer is the session's, which lands in the person inbox.
  // A `steer` carries the default it will take and lapses at its bound; an `ask` to the person
  // carries no bound at all and never expires; `system` is a sender and is never replied to
  // (design §4.10, 2026-09-19, TD-069 step 0).
  AO.mailEntry = function (e, owner) {
    const ask = e.kind === "ask" || e.kind === "steer" || e.kind === "conflict";
    let st = "";
    if (ask) {
      st = e.closed_reason === "lapsed" ? "lapsed · the sender went with its default"
        : e.closed_reason === "go_with_it" ? "closed · go with it"
        : e.closed_reason === "declined" ? "declined"
        : e.closed_reason === "asker_gone" ? "closed · the asker is gone"
        : e.closed_reason === "asked_person" ? "closed · the asker took it to the person"
        : e.closed_by ? `answered by ${esc(e.closed_by)}`
        : e.expired_at ? "expired"
        : e.paused_at ? "paused · the clock is stopped"
        : (e.pending || []).length ? "addressee exited · pending"
        : e.bound ? `${e.kind === "steer" ? "lapses" : "open · bound"} ${esc(new Date(e.bound).toLocaleString())}`
        : "open · no bound";
    }
    const reply = (e.from === "person" || e.from === "system") ? ""
      : ` <button class="btn sm ghost" data-act="reply" data-id="${esc(owner)}" data-msg="${esc(e.id)}" data-name="${esc(e.from_name || e.from)}" data-quote="${esc(e.text)}">Reply</button>`;
    const confirmText = "Delete this entry from this session's inbox? The sender keeps its copy.";
    return `<div class="mail${e.read_at ? "" : " unread"}" data-msg="${esc(e.id)}">`
      + `<div class="row gap"><span class="ref" title="${esc(e.from)} · ${esc(e.from_role || "")}">${esc(e.from_name || e.from)}</span>`
      + `<span class="st kind">${esc(e.kind)}</span>${e.about ? `<span class="st">re ${esc(e.about)}</span>` : ""}`
      + `<span class="grow"></span><span class="st">${e.read_at ? "read" : "unread"}</span>`
      + `<span class="st age" data-since="${esc(e.at || "")}">${fmtAge(e.at)}</span></div>`
      + (e.reply_to ? `<div class="st">reply to ${esc(e.reply_to)}</div>` : "")
      + `<div class="body">${esc(e.text)}</div>`
      + (e.default ? `<div class="st">unless you say otherwise: ${esc(e.default)}</div>` : "")
      + `<div class="row gap">${st ? `<span class="st${e.expired_at ? " expired" : ""}">${st}</span>` : ""}<span class="grow"></span>${reply}`
      + ` <button class="btn sm ghost" data-act="unmail" data-id="${esc(owner)}" data-msg="${esc(e.id)}" data-confirm="${confirmText}">Delete</button></div></div>`;
  };

  // design §4.5a Org top bar **Inbox** (§4.5 screen 6, TD-069 step 1). The control is a link to
  // `/inbox`, and its number is that page's **Needs you** section — computed server-side in one
  // place (`inbox_sections`), so the top bar and the page cannot disagree. No session record holds
  // the person inbox, so nothing on the pushed stream carries it: this polls the same route the
  // page does, a person's read that marks nothing. (The dialog that hung here until 2026-09-19
  // relied on that same rule and is retired with the page's arrival.)
  // design §4.5a **team header** → *answered for you* count (§4.9b, TD-075): the rows' team and
  // time from the poll, counted here against the newest one this browser has seen with the Inbox's
  // *Answered for you* group open — the browser's own memory, as FYI's *new* mark is, so looking
  // changes nothing at the home. A mark, never a control. `null` is *not known* (the host agent is
  // down): the headers keep what they showed rather than claim nothing was answered.
  const ANSWERED_SEEN = "inboxansweredseenat";
  let answeredMarks = null;
  AO.answeredCounts = function (marks, seen) {
    const n = {};
    (marks || []).forEach((m) => {
      const at = m.at || "";
      if (!seen || at > seen) n[m.team || ""] = (n[m.team || ""] || 0) + 1;
    });
    return n;
  };
  function syncAnsweredMarks() {
    if (!answeredMarks) return;
    const n = AO.answeredCounts(answeredMarks, store.get(ANSWERED_SEEN, ""));
    $$("[data-answered-team]").forEach((el) => {
      const k = n[el.dataset.answeredTeam] || 0;
      el.textContent = k ? `${k} answered for you` : "";
      el.classList.toggle("hidden", !k);
    });
  }
  AO.refreshInboxCount = async function () {
    let got;
    try {
      const r = await fetch("/api/person/inbox");
      if (!r.ok) return null;
      got = await r.json();
    } catch (e) { return null; }
    // `needs: null` is *not known* — the host agent is down — and a chip that read 0 would be a
    // lie in the one place a person looks to see whether anything is waiting (TD-069).
    const chip = $("#personneeds"), n = got.needs || 0;
    if (chip && got.needs !== null) { chip.textContent = n ? String(n) : ""; chip.classList.toggle("hidden", !n); }
    // §4.10 *The Inbox is a queue* (TD-079 step 2): FYI's own quiet number, *Inbox 1 · 5*, never
    // added to the first. `null` is *not known* here too — a chip reading 0 with the host agent
    // down would be the same lie the first one refuses to tell.
    const fyi = $("#personfyi"), m = got.fyi_n || 0;
    if (fyi && got.fyi_n !== null && got.fyi_n !== undefined) { fyi.textContent = m ? `· ${m}` : ""; fyi.classList.toggle("hidden", !m); }
    if (Array.isArray(got.answered_marks)) { answeredMarks = got.answered_marks; syncAnsweredMarks(); }
    return got;
  };
  if ($("#personneeds")) {
    // the Org page and the Inbox page render the count server-side; every other page reads it at
    // load. On the Inbox page the poll is the page's own, which refreshes the rows as well.
    // The Org reads it at load too: its number is server-rendered, but the team headers' *answered
    // for you* marks are counted in the browser (§4.9b) and would otherwise wait a whole poll.
    if (location.pathname !== "/inbox") AO.refreshInboxCount();
    setInterval(() => (AO.refreshInboxPage || AO.refreshInboxCount)(), 20000);
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
  // A time left, in the very words `_left` in `ui/app.py` renders it into the row (§4.5 screen 6
  // *Layout*, TD-082): the server draws the number, this only keeps it moving, and because both
  // spell it the same way nothing on the row jumps when the first tick lands. "" = already past.
  function fmtLeft(iso) {
    const s = Math.floor((Date.parse(iso) - Date.now()) / 1000);
    if (!(s > 0)) return "";
    if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    return `${Math.floor(s / 86400)}d`;
  }
  setInterval(() => {
    $$(".age[data-since]").forEach((el) => (el.textContent = fmtAge(el.dataset.since)));
    $$(".countdown[data-deadline]").forEach((el) => {
      if (!el.dataset.deadline) return;
      const left = fmtLeft(el.dataset.deadline);
      el.textContent = left ? `via hook · ${left} left` : "via hook · falling through to the terminal";
    });
    // design §4.5a **Inbox row: `steer`**: the time left, ticking here — the lapse itself is the
    // home's, and arrives as a changed entry on the next poll, never from this clock.
    $$(".timeleft[data-deadline]").forEach((el) => {
      if (!el.dataset.deadline) return;
      const left = fmtLeft(el.dataset.deadline);
      el.textContent = left ? `${left} left — then it goes with its default` : "the time is up: the sender goes with its default";
    });
    showLocalTimes();
  }, 1000);

  // A stored instant (UTC) shown in the browser's own clock: a person who snoozed until *tomorrow
  // 08:00* must read back tomorrow 08:00, not the UTC instant behind it — which stays on the
  // element's title. It does not change once written, so each element is written once; the callers
  // are the tick above and whatever has just put new rows on the page.
  function showLocalTimes() {
    $$(".localtime[data-at]").forEach((el) => {
      if (el.dataset.shown === "1") return;
      const d = new Date(Date.parse(el.dataset.at || ""));
      el.textContent = isNaN(d) ? el.dataset.at || "" : d.toLocaleString();
      el.dataset.shown = "1";
    });
  }

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
  // ---- usage chip: one span per profile, its **worst** window, the rest on hover (TD-001, TD-073) ----
  // The windows and their labels are the adapter's — this file names no window of any one tool, so a
  // tool with one daily window or three windows prints what it has. `usage: null` means that profile
  // has no live session any more (the core prunes it): its chip goes.
  const NEAR_CAP = 80;  // "at or near a cap": never collapsed into +n, whatever the room (Paul 2026-09-19)
  // Why the last poll gave no reading: the adapter's `reason` word in words (§4.2, TD-087). Keyed on
  // the word, never on text; a word this table does not know is printed as itself.
  const USAGE_WHY = {
    rate_limited: "rate-limited by the usage endpoint", no_credentials: "no credentials for this profile",
    no_profile: "no such profile", error: "the usage endpoint could not be read",
  };
  // One profile's chip, or null for none — `usage_chip` in app.py is the same rule for the server's
  // render, and the tests hold the two to the same cases. **A held reading goes stale, not out**
  // (TD-087): a refused poll keeps the last good windows, drawn dimmed with *· stale* and, on hover,
  // when they were read and why the poll since failed; a refusal with nothing ever held is
  // `<profile>: no reading yet`. An `ok` with no windows is a tool that reports no quota: no chip.
  AO.usageChip = function (profile, u) {
    if (!u || typeof u !== "object") return null;
    const windows = (Array.isArray(u.windows) ? u.windows : []).filter((w) => w && typeof w.pct === "number");
    const reason = String(u.reason || "ok"), stale = reason !== "ok";
    if (!windows.length && !stale) return null;
    let why = "";
    if (stale) {
      why = "the last poll was refused: " + (USAGE_WHY[reason] || reason);
      if (typeof u.retry_after === "number") why += `, which asked to be left ${Math.max(1, Math.ceil(u.retry_after / 60))} min`;
    }
    if (!windows.length) return { text: `${profile}: no reading yet`, title: `no usage reading for ${profile} yet — ${why}`, pct: 0, cls: "stale" };
    const ws = windows.slice().sort((a, b) => b.pct - a.pct), worst = ws[0];
    let title = ws.map((w) => `${w.label} ${w.pct}% (resets ${w.resets || "?"})`).join(" · ");
    let cls = worst.pct >= 100 ? "cap" : worst.pct >= NEAR_CAP ? "near" : "";
    let text = `${profile} · ${worst.label} ${worst.pct}%`;  // the numbers say what they are (2026-09-18)
    if (stale) { title = `held reading from ${u.fetched || "an unknown time"} — ${why}. ${title}`; text += " · stale"; cls = (cls + " stale").trim(); }
    return { text, title, pct: worst.pct, cls };
  };
  function onUsage(ev) {
    const chip = $("#usagechip"); if (!chip) return;
    let el = chip.querySelector(`[data-profile="${CSS.escape(ev.profile)}"]`);
    const c = AO.usageChip(ev.profile, ev.usage);
    // The chip and the space after it were added together, so they go together: a profile that
    // comes and goes all day would otherwise leave a text node behind each time, and those widths
    // are what `fitUsage` measures against (review of PR #279).
    if (!c) { if (el) { const sep = el.nextSibling; if (sep && sep.nodeType === 3) sep.remove(); el.remove(); } fitUsage(); return; }
    if (!el) { el = document.createElement("span"); el.dataset.profile = ev.profile; chip.insertBefore(el, $("#usagemore")); chip.insertBefore(document.createTextNode(" "), $("#usagemore")); }
    el.dataset.pct = c.pct;
    el.textContent = c.text;
    el.className = c.cls;
    el.title = c.title;
    fitUsage();
  }
  // Chips side by side while they fit; past that the worst profiles and `+n`, which shows the rest
  // on hover. No rotation: a display that rotates hides the number at the moment it is looked at,
  // and the one that matters may be the one off screen (TD-073, decided by Paul 2026-09-19).
  function fitUsage() {
    const chip = $("#usagechip"); if (!chip) return;
    const more = $("#usagemore"), spans = [...chip.querySelectorAll("[data-profile]")];
    spans.forEach((s) => s.classList.remove("hidden"));
    if (!more) return;
    more.classList.add("hidden"); more.textContent = ""; more.title = "";
    // Hide the least important first: lowest worst-window percentage, and never one at or near a cap.
    const droppable = spans.filter((s) => (+s.dataset.pct || 0) < NEAR_CAP).sort((a, b) => (+a.dataset.pct || 0) - (+b.dataset.pct || 0));
    const hidden = [];
    while (chip.scrollWidth > chip.clientWidth + 1 && droppable.length) {
      const s = droppable.shift(); s.classList.add("hidden"); hidden.push(s);
      more.textContent = `+${hidden.length}`; more.classList.remove("hidden");
    }
    if (hidden.length) more.title = hidden.map((s) => `${s.textContent} — ${s.title}`).join("\n");
  }
  function setDown(down) {
    const dot = $("#hostdot"); if (dot) dot.classList.toggle("down", down);
    const b = $("#agentdown"); if (b) b.classList.toggle("hidden", !down);
  }

  // ---- Org (design §4.5 screen 1) ----
  // The page is one or more `.tgroup` sections, each an optional header plus its own `.grid`: one
  // per team when any session carries a `team` badge or any team is defined (design §4.5a **team
  // groups**, §4.9), and one unnamed, headerless group otherwise. One order, no control (design
  // §4.5, 2026-09-18): inside a group the manager's card, then urgency, then name.
  const sections = () => $$("#groups .tgroup");
  function layout() {
    const box = $("#groups"); if (!box) return;
    sections().forEach((sec) => {
      const grid = $(".grid", sec), manager = sec.dataset.manager || "";
      const cards = $$(".sc", grid);
      // `card_order` in app.py: urgency, then an interactive session ahead of an unattended one (TD-095)
      cards.sort((a, b) => (b.dataset.id === manager) - (a.dataset.id === manager) || (+a.dataset.rank - +b.dataset.rank)
        || (!!b.dataset.mine - !!a.dataset.mine) || a.dataset.name.localeCompare(b.dataset.name))
        .forEach((c) => grid.appendChild(c));
    });
    applyFilter();
    const shown = $$("#groups .sc").filter((c) => !c.hidden);
    $("#count").textContent = `${shown.length} session${shown.length === 1 ? "" : "s"}`;
    $("#empty").hidden = shown.length > 0;
    const counts = {}; shown.forEach((c) => (counts[c.dataset.state] = (counts[c.dataset.state] || 0) + 1));
    $("#badges").innerHTML = [["needs-you", "needs", "needs you"], ["limited", "limited", "limited"], ["stalled?", "stalled", "stalled"]]
      .filter(([k]) => counts[k]).map(([k, cls, l]) => `<span class="pill s-${cls}"><span class="dot"></span>${counts[k]} ${l}</span>`).join("");
    syncTeams();
  }
  function applyFilter() {
    const raw = ($("#filter") ? $("#filter").value : "").trim(), cmd = $("#showcmd") && $("#showcmd").checked;
    const mine = !!$("#mine") && $("#mine").getAttribute("aria-pressed") === "true";
    // `team:<name>` is the form the card's team badge writes: an exact match on the badge, not a
    // substring of the card's text, so a team whose name also appears in a branch stays clean.
    const team = /^team:/i.test(raw) ? raw.slice(5).trim().toLowerCase() : null;
    const q = team === null ? raw.toLowerCase() : "";
    $$("#groups .sc").forEach((c) => {
      const hideKind = c.dataset.kind === "command" && !cmd;
      const miss = team !== null ? (c.dataset.team || "").toLowerCase() !== team : !!q && !c.textContent.toLowerCase().includes(q);
      c.hidden = hideKind || miss || (mine && !c.dataset.mine);  // *mine* composes with the box (§4.5a)
    });
    // A group with nothing left to show goes away with its header; the empty page says so once.
    // A team's card stays while no filter is set, sessions or none: it is where Start lives.
    const filtering = !!raw || mine;
    sections().forEach((sec) => {
      sec.hidden = !$$(".sc", sec).some((c) => !c.hidden) && (filtering || !sec.dataset.team);
      sec.classList.toggle("filtering", filtering);  // a filter shows what it matched, folded or not
    });
  }
  // The server derives the groups on every render and every delta (only it sees the whole fleet),
  // so a badge or `controllers` change moves cards between groups here without a page reload.
  // `groups` null means the flat grid: one unnamed section, no header anywhere.
  function syncGroups(gs) {
    const box = $("#groups"); if (!box) return;
    box.classList.toggle("flat", !gs);
    const wanted = gs || [{ team: "", manager: "", ids: $$("#groups .sc").map((c) => c.dataset.id), html: "" }];
    const keep = [];
    wanted.forEach((g) => {
      let sec = sections().find((s) => s.dataset.team === g.team);
      if (!sec) {
        sec = document.createElement("section");
        sec.className = "tgroup"; sec.dataset.team = g.team;
        sec.innerHTML = '<div class="grid"></div>';
      }
      sec.dataset.manager = g.manager || "";
      sec.dataset.live = g.live || 0;
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
      // A card the server did not list keeps its place on the page — somewhere it can be seen: the
      // first group may now be a stopped team, folded (review of PR #226), so *No team* or a live
      // team is preferred.
      const dest = keep.find((k) => !k.dataset.team) || keep.find((k) => +k.dataset.live) || keep[0];
      const home = $(".grid", dest);
      $$(".sc", sec).forEach((c) => home.appendChild(c));
      sec.remove();
    });
  }
  // ---- a team's card: the fold, and a request in flight (design §4.5a **team groups**) ----
  // A team with nothing live folds its cards away: they have exited and are waiting for Forget, and
  // a page of them buries what is running. The choice is kept per team; a live team never folds.
  const foldKey = (team) => "fold:" + team;
  function syncTeams() {
    sections().forEach((sec) => {
      const team = sec.dataset.team, b = $(".ghead .fold", sec);
      const folded = !!team && !+sec.dataset.live && !!b && store.get(foldKey(team), true);
      sec.classList.toggle("folded", folded);
      if (b) b.textContent = `${b.dataset.n} session${b.dataset.n === "1" ? "" : "s"} — ${folded ? "show" : "hide"}`;
    });
    // a header re-rendered for a delta must not re-arm a request in flight
    $$("#groups .ghead [data-team-act]").forEach((b) => (b.disabled = pendingTeams.has(b.dataset.team)));
    syncAnsweredMarks();  // …nor lose the answered-for-you mark the browser counted (§4.9b)
  }
  // A stop returns before its manager does (design §4.9: the members settle first, which is minutes).
  // Nothing pushes that outcome, so the page asks for it — bounded, and only while one is pending —
  // rather than leaving a failure nobody ever sees (design §4.5 "Errors"; review of PR #124).
  async function watchStop(name, manager) {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      let rows = [];
      try { rows = (await (await fetch("/api/teams")).json()).teams || []; } catch (e) { continue; }
      const row = rows.find((t) => t.name === name);
      if (!row) return;
      if (row.error) { AO.toast(`${name}: ${row.error}`); return; }
      if (!row.stopping) { AO.toast(`${name}: ${manager} stopped`, true); return; }
    }
    AO.toast(`${name}: ${manager} is still stopping — see the agent log`);
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
      // definition starts interactive is out of its manager's reach (design §9 invariant 5), and the
      // manager's own stop happens after the response (review of PR #124).
      (o.out_of_reach || []).forEach((who) =>
        AO.toast(`${name}: ${who} is interactive, so its manager cannot act on it — §9 invariant 5`));
      // TD-042: a brief written for one night cannot start the next. The team started; this is a note.
      (o.unrepeatable || []).forEach((w) => AO.toast(`${name}: ${w}`));
      // What `ao team start` prints to stderr and the team still started on: a techlead seat without
      // its primer, or one that came up under another id (design §4.9b).
      (o.notes || []).forEach((w) => AO.toast(`${name}: ${w}`));
      if (o.manager) watchStop(name, o.manager);
    } catch (e) {
      AO.toast(`${name}: ${e.message}`);
    } finally {
      pendingTeams.delete(name);
      btn.disabled = false;
      syncTeams();  // the button on the page now may not be the one that was pressed
    }
  }

  // ---- Inbox (design §4.5 screen 6, §4.5a **Inbox page**; TD-069 step 1) ----
  // The rows are rendered by the server, by the same template the page was rendered with, and a
  // poll swaps a whole section's markup: nothing here composes markup out of what a session wrote.
  // What lives in the browser is what belongs to this browser — the filter, the FYI fold — exactly
  // as the Org's filter and team folds do.
  const IN_SECS = ["needs", "steering", "waiting", "answered", "fyi", "snoozed"];

  // §4.5a **Snooze**: 1 h · tomorrow 08:00 · a date. Returned as a UTC instant, whole seconds,
  // which is what the entry stores; the prompt is in the person's own clock.
  function snoozeUntil(when) {
    const iso = (d) => new Date(d.getTime() - d.getMilliseconds()).toISOString().replace(/\.\d+Z$/, "Z");
    const d = new Date();
    if (when === "1h") { d.setHours(d.getHours() + 1); return iso(d); }
    if (when === "tomorrow") { d.setDate(d.getDate() + 1); d.setHours(8, 0, 0, 0); return iso(d); }
    const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const s = prompt("Snooze until… (YYYY-MM-DDTHH:MM, your own clock)", local);
    if (!s) return null;
    const t = new Date(s.trim());
    if (isNaN(t)) { AO.toast(`${s}: not a time I can read`); return null; }
    if (t <= new Date()) { AO.toast("that time has passed: a snooze goes forwards"); return null; }
    return iso(t);
  }

  // design §4.5a **Inbox: section heading, the i mark** (§4.5 screen 6 *Layout*, TD-082). The mark
  // is a `<button>`, so Enter and Space press it; the paragraph it holds is in the page always and
  // merely `hidden`, because it is also the button's `aria-describedby` and a description may
  // point at a hidden node but never at a missing one. Pressed, it opens **in place** under the
  // heading, pushing the rows down rather than covering one; which are open is remembered here.
  // Inside FYI's `<summary>` the press must not also fold the section, which is the stopPropagation.
  function setupInfoMarks() {
    $$(".inboxpage .imark").forEach((b) => {
      const para = document.getElementById(b.dataset.info);
      if (!para) return;
      const key = "inboxinfo:" + b.dataset.info;
      const show = (on) => { para.hidden = !on; b.setAttribute("aria-expanded", on ? "true" : "false"); if (on) para.classList.remove("peek"); };
      show(store.get(key, false));
      b.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        const on = para.hidden;
        show(on); store.set(key, on);
      });
      // §4.5a: *a tooltip on hover **and keyboard focus***. `title` is the hover one — the UA's,
      // which is also what a page with no script still has — but no browser shows a `title` to a
      // keyboard, so focus gets the same words the other way: the very paragraph, floated over the
      // rows rather than pushing them down, which is what a press does. The same node, so the
      // tooltip and the description cannot drift apart. `:focus-visible`, so a mouse press — which
      // focuses too, and opens the paragraph in place — does not also float a copy of it.
      b.addEventListener("focus", () => { if (para.hidden && b.matches(":focus-visible")) para.classList.add("peek"); });
      b.addEventListener("blur", () => para.classList.remove("peek"));
    });
  }
  AO.inbox = function () {
    const f = $("#ifilter");
    setupInfoMarks();
    f.value = store.get("inboxfilter", "");
    $("#sec-fyi").open = store.get("inboxfyi", false);  // folded by default, remembered here
    $("#sec-fyi").addEventListener("toggle", () => store.set("inboxfyi", $("#sec-fyi").open));
    // §4.9b *Answered for you*: open until the person folds it, and remembered here as FYI's is
    const ans = $("#sec-answered");
    if (ans) {
      ans.open = store.get("inboxanswered", true);
      ans.addEventListener("toggle", () => { store.set("inboxanswered", ans.open); markAnsweredSeen(); });
    }
    f.addEventListener("input", () => { store.set("inboxfilter", f.value.trim()); inboxFilter(); });
    // the row's team badge filters to that team; pressing it again clears the box (as on the Org)
    document.addEventListener("click", (e) => {
      const b = e.target.closest(".mailrow .badge.team"); if (!b) return;
      e.preventDefault();
      const q = "team:" + b.dataset.team;
      f.value = f.value.trim().toLowerCase() === q.toLowerCase() ? "" : q;
      store.set("inboxfilter", f.value); inboxFilter();
    });
    // §4.10: **Dismiss all** — the entries this browser has **on screen**, by id, never
    // *everything FYI holds now*: mail that arrived after the page was drawn is what must not go
    // unseen. The confirm says the number it is about, and the ids come off the DOM for that
    // reason. It sits in FYI's `<summary>`, so its press must not also fold the section.
    const all = $("#dismissall");
    if (all) all.addEventListener("click", async (e) => {
      e.preventDefault(); e.stopPropagation();
      const ids = [...$$("#rows-fyi .mailrow")].map((r) => r.dataset.msg).filter(Boolean);
      if (!ids.length) return AO.toast("nothing in FYI to dismiss", true);
      if (!confirm(`Dismiss ${ids.length} FYI ${ids.length === 1 ? "entry" : "entries"}? Only the ones on screen now — anything that arrives after this is untouched.`)) return;
      try {
        await act("person", "dismiss", { msg: ids });
        AO.toast(`dismissed ${ids.length} — the sender is told where one was owed`, true);
      } catch (err) { AO.toast(`dismiss failed: ${err.message}`); }
      refreshInbox();
    });
    AO.refreshInboxPage = refreshInbox;
    inboxFilter();
    refreshInbox();
  };

  // design §4.5 *no silent failure path* / TD-029: **the terminal's two client rules, kept pure**,
  // for the reason `maySwapSection` below is — a rule that lives inside a closure is a rule no test
  // can reach, and TD-029's entry said for nine days that its client half could not be tested
  // "because it is JavaScript, which this repo has no harness for". The harness is the node probe
  // in `tests/test_ui_inbox.py`; what was missing was something for it to call.
  //
  // **The pane is gone for good.** `closed` or `pane: false` — from the record the page was drawn
  // with, or from a pushed delta, which is authoritative and arrives before any reconnect could.
  AO.paneIsGone = function (s) {
    return !!s && (s.state === "closed" || s.pane === false);
  };
  // **What a closed terminal socket means**, and it is the whole of TD-029's loop: a connection the
  // server accepts and then ends is *not* a working terminal, so the backoff must not reset here —
  // it resets on the first byte of pane output and nowhere else. `4404` is the server saying the
  // pane is gone, which is final and never retried. Everything else retries, with the delay
  // doubling to a ceiling. `opened` is whether the socket ever opened: a 1006 before it did is a
  // handshake that never reached the server, which is a different thing to say to a person.
  // `reason` is the close frame's, when there is one. It is **part of the sentence, so it is part
  // of the rule**: the caller appending it separately is how the two came apart in review — the
  // call site suppressed it for every 1006 where the original suppressed it only for a 1006 the
  // socket never opened. Nothing can reach that difference today (a 1006 is client-synthesised and
  // carries no reason, and this server sends none), which is exactly why it had to be closed here
  // rather than left as a comment.
  // A keystroke frame that is only mouse-wheel reports — the one thing a read-only attach passes
  // (the server's `WHEEL_ONLY`, `pty_bridge.py`: buttons 64/65 with any of the modifier bits).
  AO.isWheel = function (d) {
    return /^(?:\x1b\[<(\d+);\d+;\d+[Mm])+$/.test(d) && [...d.matchAll(/\x1b\[<(\d+);/g)].every((m) => (Number(m[1]) & ~0x1d) === 64);
  };
  AO.termClose = function (code, opened, delay, reason) {
    if (code === 4404) return { retry: false, final: true, delay, why: "no terminal for this session" };
    if (code === 1006 && !opened) {
      const why = "websocket handshake failed (code 1006) — does your route to the UI pass websockets? ssh -L does";
      return { retry: true, final: false, delay: Math.min(delay * 2, 10000), why };
    }
    const why = `closed (code ${code}${reason ? ", " + reason : ""})`;
    return { retry: true, final: false, delay: Math.min(delay * 2, 10000), why };
  };

  // Whether the poll may replace this section's rows now. It may not while the person is inside
  // them: an open Snooze menu would close under the press, and a swap would take the focus of
  // someone tabbing through a row's controls. Neither is worth a few seconds' freshness — the next
  // poll does it, and the count above never waits. Kept pure (element in, boolean out) so the rule
  // is one readable line rather than conditions spread through the swap.
  AO.maySwapSection = function (el, focused) {
    if (!el) return false;
    if (el.querySelector("details[open]")) return false;  // a menu the person has opened
    return !(focused && focused !== document.body && el.contains(focused));
  };

  // §4.9b: the team header's *answered for you* count runs from the last time the person **opened
  // the group** — here, the newest row on screen while the group is open and the tab is in view.
  function markAnsweredSeen() {
    const sec = $("#sec-answered");
    if (!sec || !sec.open || sec.classList.contains("hidden") || document.visibilityState === "hidden") return;
    let newest = store.get(ANSWERED_SEEN, "");
    $$("#rows-answered .mailrow").forEach((r) => { if ((r.dataset.at || "") > newest) newest = r.dataset.at; });
    if (newest) store.set(ANSWERED_SEEN, newest);
  }

  async function refreshInbox() {
    const got = await AO.refreshInboxCount();
    const down = $("#agentdown");
    if (down) {
      // the banner the page renders at load, turned on and off by the poll: a host agent that goes
      // away under an open page must not leave its rows looking current (design §4.5)
      // `.hidden` the class, never the attribute: `.warn` sets `display`, which beats the UA's
      // `[hidden]` rule — the trap `.badge[hidden]` is commented for in app.css, and the Org's own
      // banner avoids the same way (review of PR #271)
      down.classList.toggle("hidden", !(got && got.agent_down));
      if (got && got.agent_down && got.why) $("#agentdownwhy").textContent = got.why;
    }
    // …and nothing else changes while it is down: an empty `html` would blank every section and a
    // `needs` of 0 would claim nothing is waiting, which is precisely what is not known (§4.5)
    if (!got || got.agent_down || !got.html) return;
    IN_SECS.forEach((k) => {
      const el = $("#rows-" + k);
      if (el && AO.maySwapSection(el, document.activeElement)) el.innerHTML = got.html[k] || "";
    });
    // *Waiting on them* is empty for most people most of the time, so it draws only when it has
    // something — like the snoozed box (§4.5a **Inbox section: Waiting on them**).
    const w = $("#sec-waiting");
    if (w) w.classList.toggle("hidden", !(got.sections && (got.sections.waiting || []).length));
    // *Answered for you* (§4.9b) the same way: most teams have no techlead, and an empty heading
    // there would be a section that is never anything
    const a = $("#sec-answered");
    if (a) a.classList.toggle("hidden", !(got.sections && (got.sections.answered || []).length));
    markAnsweredSeen();
    // §4.10: **FYI opens itself when its count is higher than this browser last saw it**, and is
    // otherwise as the person left it. That comparison is the browser's own — nothing on an entry
    // and nothing at the home changes, so reading still changes no row. A folded, uncounted FYI
    // is how five notes sat unseen for three days, which is the failure this closes.
    const fyi = $("#sec-fyi"), n = got.fyi_n || 0;
    if (fyi && got.fyi_n !== null && got.fyi_n !== undefined) {
      if (n > store.get("inboxfyiseen", 0)) { fyi.open = true; store.set("inboxfyi", true); }
      store.set("inboxfyiseen", n);
      // …and the entries that are why: an FYI entry newer than this browser last saw is marked
      // **new** — a mark and never a control, and the browser's own memory, as the fold is:
      // nothing on the entry and nothing at the home changes, so reading still changes no row.
      const seen = store.get("inboxfyiseenat", ""), rowsFyi = $$("#rows-fyi .mailrow");
      let newest = seen;
      rowsFyi.forEach((r) => {
        const at = r.dataset.at || "";
        r.classList.toggle("isnew", !!seen && !!at && at > seen);
        if (at > newest) newest = at;
      });
      if (newest) store.set("inboxfyiseenat", newest);
    }
    showLocalTimes();
    $("#needsn").textContent = got.needs || 0;
    $("#needspill").classList.toggle("hidden", !got.needs);
    const sn = got.snoozed_n || 0;
    $("#snoozedbox").hidden = !sn;
    $("#snoozedlabel").textContent = `${sn} snoozed — show`;
    inboxFilter();
  }

  // `team:<name>` is an exact match on the row's badge, `team:` alone is what carries none; any
  // other text matches the sender, the message and its `about` — the fields a person searches by.
  function inboxFilter() {
    const box = $("#ifilter"); if (!box) return;
    const raw = box.value.trim();
    const team = /^team:/i.test(raw) ? raw.slice(5).trim().toLowerCase() : null;
    const q = team === null ? raw.toLowerCase() : "";
    $$(".mailrow").forEach((r) => {
      const has = (r.dataset.team || "").toLowerCase();
      r.hidden = team !== null ? has !== team : !!q && !(r.dataset.find || "").includes(q);
    });
    IN_SECS.forEach((k) => {
      const sec = $("#sec-" + k); if (!sec) return;
      const n = $$(`#rows-${k} .mailrow`).filter((r) => !r.hidden).length;
      const all = $$(`#rows-${k} .mailrow`).length;
      $("#n-" + k).textContent = raw && n !== all ? `${n} of ${all}` : all ? `${all}` : "";
      const empty = $(".note.empty", sec); if (empty) empty.hidden = n > 0;
    });
  }

  AO.org = function () {
    $("#filter").addEventListener("input", layout);
    // *mine* (§4.5a, TD-095): a toggle this browser remembers, as it remembers a team's fold
    const mineBtn = $("#mine");
    const setMine = (on) => { mineBtn.setAttribute("aria-pressed", on ? "true" : "false"); mineBtn.classList.toggle("on", on); };
    setMine(!!store.get("mine", false));
    mineBtn.addEventListener("click", () => { const on = mineBtn.getAttribute("aria-pressed") !== "true"; store.set("mine", on); setMine(on); layout(); });
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
    // Start, Wind down and Stop now are all on the team's card, and so is its fold.
    box.addEventListener("click", (e) => {
      const b = e.target.closest("[data-team-act]");
      // a concluded team's Start closes its sessions first, and its confirm names them (TD-099)
      if (b) return b.dataset.confirm && !confirm(b.dataset.confirm) ? undefined : teamAct(b.dataset.team, b.dataset.teamAct, b);
      const f = e.target.closest("[data-fold]");
      if (f) { store.set(foldKey(f.dataset.fold), !store.get(foldKey(f.dataset.fold), true)); syncTeams(); }
    });
    layout();
    connectEvents((ev) => {
      if (ev.event === "session") {
        const old = $(`#card-${CSS.escape(ev.id)}`);
        const tpl = document.createElement("template"); tpl.innerHTML = ev.html.trim();
        const fresh = tpl.content.firstElementChild;
        if (old) old.replaceWith(fresh);
        else {
          const grid = $(".tgroup .grid");  // syncGroups below moves it into its own group
          grid.appendChild(fresh);
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
        } else if (o.verdict === "suspended") {
          // design §4.8a *An alarm's answers* (TD-077 a2): **a person's create is the lift**, which
          // is why Start stays enabled here where a `live` holder disables it. The agent's own
          // sentence is printed as it wrote it — the why, the when and the two ways out are all in
          // it, and a page that recomposed them from parts is how two surfaces come to say
          // different things about one record. The frame is all this adds: what pressing Start does.
          nnote.innerHTML = `⚠ <b>${esc(o.message)}</b><br>Starting it here <b>lifts the suspension</b>`
            + ` — that is a person's act, and yours.${o.holder ? ` <a class="btn sm" href="/focus/${encodeURIComponent(o.holder)}">Look at it first</a>` : ""}`;
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
    AO.termRenderer(term);
    AO.termFont(term, fit);
    let ws, delay = 500, paneGone = false;
    // design §4.5 *Focus watches* (TD-096): an unattended session's attach is read-only. The server
    // decides and drops the keys (§4.6); the page learns it from the attach's first frame and only
    // says so — `mode` is the record's as last seen, and a change to it re-attaches.
    let readOnly = false, mode = !!s.unattended, hinted = 0;
    // The pane is gone for good: end the terminal and stop reconnecting. The events push says so
    // before any reconnect could, and the server's 4404 says so too (TD-029).
    function endTerm(text) {
      paneGone = true;
      if (ws) { try { ws.onclose = null; ws.close(); } catch (e) { /* already closing */ } }
      term.write(`\r\n\x1b[90m[agentorc] ${text}\x1b[0m\r\n`);
    }
    function openTerm() {
      if (paneGone) return;
      readOnly = false;  // until this attach says otherwise, in its first frame
      const cols = Number.isFinite(term.cols) && term.cols > 0 ? term.cols : 120, rows = Number.isFinite(term.rows) && term.rows > 0 ? term.rows : 32;
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/term/${encodeURIComponent(id)}?cols=${cols}&rows=${rows}`);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => { ws.send(JSON.stringify({ resize: [cols, rows] })); };
      // The backoff resets on pane output, never on open (TD-029): a connection the server accepts
      // and then ends is not a working terminal, and resetting there retried twice a second forever.
      ws.onmessage = (m) => {
        if (typeof m.data === "string" && m.data.startsWith("{")) {
          // The attach's own word, not pane output: it does not reset the backoff (TD-029).
          let c = null; try { c = JSON.parse(m.data); } catch (e) { c = null; }
          if (c && "read_only" in c) {
            readOnly = !!c.read_only;
            if (readOnly) term.write("\x1b[90m[agentorc] watching: this session is unattended, so the terminal is read-only — Take over (above) to type.\x1b[0m\r\n");
            return;
          }
        }
        delay = 500; term.write(typeof m.data === "string" ? m.data : new Uint8Array(m.data));
      };
      let opened = false;
      ws.addEventListener("open", () => { opened = true; });
      ws.onclose = (e) => {
        if (paneGone) return;  // the push already ended it
        // The rule is `AO.termClose` (above), so a test can reach it; this is what acts on it.
        const v = AO.termClose(e.code, opened, delay, e.reason);
        if (v.final) { endTerm(v.why); return; }
        term.write(`\r\n\x1b[90m[agentorc] terminal ${v.why} — retrying in ${Math.round(delay / 1000) || 1}s\x1b[0m\r\n`);
        setTimeout(openTerm, delay); delay = v.delay;
      };
    }
    // A mode change seen in the feed: end this attach without the retry path and open a new one,
    // which reads the mode afresh (§4.6).
    function reattach() {
      if (paneGone) return;
      if (ws) { try { ws.onclose = null; ws.close(); } catch (e) { /* already closing */ } }
      delay = 500; openTerm();
    }
    if (AO.paneIsGone(s)) { paneGone = true; term.write("\x1b[90m[agentorc] this session's pane is gone (killed, closed, or the tmux server restarted).\x1b[0m\r\n"); }
    else openTerm();
    term.onData((d) => {
      if (!ws || ws.readyState !== 1) return;
      // A read-only attach passes the wheel (tmux scrolls its history with it) and drops the rest,
      // server-side; the page only spares the round trip and says why nothing happened.
      if (readOnly && !AO.isWheel(d)) {
        if (Date.now() - hinted > 5000) { hinted = Date.now(); AO.toast("watching: the terminal is read-only — Take over to type"); }
        return;
      }
      ws.send(d);
    });
    // Copy / paste: Ctrl+C with a selection copies (no ^C), Ctrl+Shift+C copies, Ctrl+Shift+V and
    // right-click paste; the header buttons do the same for discoverability. Clipboard access
    // needs a secure context (https or localhost) — ssh -L to 127.0.0.1 qualifies.
    const copySel = () => { const t = term.getSelection(); if (t) navigator.clipboard.writeText(t).then(() => AO.toast("copied", true), () => AO.toast("clipboard blocked (needs https or localhost)")); return !!t; };
    const pasteClip = () => readOnly ? AO.toast("watching: paste is off — Take over to type") : navigator.clipboard.readText().then((t) => { if (t && ws && ws.readyState === 1) term.paste(t); }, () => AO.toast("clipboard blocked (needs https or localhost)"));
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
      } else if (v.state === "unreachable" && p && p.host_unreachable) {
        // design §4.4a: the hook still blocks on its node, and an answer from here cannot reach it
        head += ` <span class="meta">${esc(p.kind)}: ${esc(p.text)} — host unreachable: answer it at ${esc(v.host)}, in the tool's own dialog</span>`;
        compose.disabled = true; $("#composehint").textContent = "the host agent cannot be reached: nothing can be sent until it is back";
      } else if (v.state === "exited" || v.state === "closed" || v.state === "unreachable") {
        // There is no turn to start or steer: the pane is dead or out of reach. The composer used
        // to sit enabled here and say nothing, which was merely useless; saying "starts a new
        // turn" at a dead session would be a lie, so the state that made the hint necessary is
        // the state that has to be excluded from it. The exited banner below offers the two real
        // next steps (Resume, New session here). TD-047.
        compose.disabled = true;
        // The banner below offers **Resume** only on a record that holds a tool session id — an
        // `exited` or a `closed` one, from TD-081 step 2 — so the hint must not promise it where
        // there is none (a `shell`, a command run).
        $("#composehint").textContent = v.state === "unreachable"
          ? "the host agent cannot be reached: nothing can be sent until it is back"
          : (v.state === "exited" || v.state === "closed") && v.adapter_id
            ? "this session's process has ended: resume it under its own name, or start a new one, below"
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
      // The mode toggle, named for what it does, and what it governs (design §4.5a, TD-096): the
      // composer is closed on an unattended session — it types, and typing is the disruption.
      const ma = $("#fmodeact");
      if (ma && v.mode_act) {
        ma.textContent = v.mode_act; ma.title = v.mode_title || "";
        ma.dataset.unattended = v.unattended ? "1" : "0";
        ma.classList.toggle("primary", !!v.unattended);
      }
      const fm = $("#fmode"); if (fm) fm.textContent = v.unattended ? "unattended" : "interactive";
      const cp = $("#composer"); if (cp) cp.classList.toggle("hidden", !!v.unattended);
      const tp = $("#tpaste");
      if (tp) tp.title = "paste the clipboard into the terminal (Ctrl+V, Ctrl+Shift+V, Shift+Insert, or right-click)" + (v.unattended ? " — not while you are watching: Take over first" : "");
      // §4.8a (TD-077 a2): the **suspended** mark rides the pushed delta like the state does. It
      // is drawn server-side at load and lives *outside* `#fstate` — a suspension is not a state —
      // so without this a person watching the very session that is suspended from somewhere else
      // would see the state change and not the mark, and the mark is the suspension's only record
      // (review of PR #306). Always in the page, hidden until it is true, as the banners are.
      // …and the two report-line marks that stand beside it, for the same reason (review of PR
      // #323): *out of work* and *restart wanted* are declared by the session while a person may
      // be watching it, and a chip drawn only at load would show neither the declaration nor the
      // claim that clears it. The words are the template's; this keeps them current.
      const oow = $("#foow");
      if (oow) {
        const o = v.out_of_work;
        oow.classList.toggle("hidden", !o);
        oow.title = (o && o.why) || "no reason recorded";
        oow.textContent = "out of work" + (o && o.age ? ` ${o.age}` : "");
      }
      const rw = $("#frw");
      if (rw) {
        const r = v.restart_wanted, early = !!(r && r.early);
        rw.classList.toggle("hidden", !r);
        rw.classList.toggle("early", early);
        rw.title = ((r && r.why) || "no reason recorded")
          + (early ? " — asked inside its own first half hour, so a controller does not act on it: this one is for a person (design §4.9a)" : "");
        rw.textContent = "restart wanted" + (early ? " · early" : "") + (r && r.age ? ` ${r.age}` : "");
      }
      // …and the usage gate's pause (§4.5a **paused · usage**, TD-100): written and cleared by the
      // host agent's tick while a person may be watching, so it rides the delta too.
      const gt = $("#fgated");
      if (gt) {
        gt.classList.toggle("hidden", !v.gated);
        gt.title = (v.gated && v.gated.full) || "";
        gt.textContent = (v.gated && v.gated.text) || "paused · usage";
      }
      const susp = $("#fsuspended");
      if (susp) {
        susp.classList.toggle("hidden", !v.suspended_note);
        susp.title = v.suspended_note || "";
      }
      // An exited session keeps its dead pane on purpose (exit code, last lines, run log); say so
      // and offer the two useful next steps instead of leaving tmux's "Pane is dead" to explain it.
      const ex = $("#fexited");
      if (v.state === "exited" || v.state === "closed") {
        const code = v.exit_code == null ? "" : ` (exit code ${v.exit_code})`;
        const q = `dir=${encodeURIComponent(v.dir || "")}&adapter=${encodeURIComponent(v.adapter || "claude-code")}`;
        const kept = v.state === "exited" && v.pane !== false;  // a kill/close destroys the pane (TD-023)
        ex.innerHTML = `This session's process has ${esc(v.state)}${esc(code)}. ${kept ? "The pane is kept so its last screen and run log stay readable." : "Its pane is gone (killed, or the tmux server restarted); the run log stays readable."} `
          // design §4.5a **Focus (exited / closed)** (TD-081 step 2, Paul: *a resume option that
          // requires no input from me*): **Resume** is one press and no form — the same name, so
          // the record is replaced in place and keeps its mail — and **Resume with changes…** is
          // that same create as a filled-in form. A `closed` record resumes too: §4.5a says
          // *`exited` or `closed` holding a tool session id*, and the link that stood here
          // offered neither, which is how a resume became a second record beside the first.
          + (v.adapter_id && (v.state === "exited" || v.state === "closed")
            ? `<button class="btn sm primary" data-act="resume" data-id="${id}">Resume</button> `
              + `<button class="btn sm" data-act="resume-form" data-id="${id}">Resume with changes…</button> `
            : "")
          + `<a class="btn sm" href="/new?${q}">New session here</a> <button class="btn sm ghost" data-act="remove" data-id="${id}">Forget</button>`;
        ex.classList.remove("hidden");
      } else ex.classList.add("hidden");
      $("#adapter_id").textContent = v.adapter_id || "—";
      $("#last_output").textContent = v.last_output ? fmtAge(v.last_output) + " ago" : "—";
      if (v.git) {
        // the one measure first (design §4.2, TD-080: *only on this machine*), then `ahead`, which
        // is *unmerged* and a different question — the template renders the same three parts
        const unpushed = v.git.unpushed ? ` · ${v.git.unpushed} unpushed${v.git.pushed_against ? ` vs ${v.git.pushed_against}` : ""}` : "";
        $("#gitline").textContent = v.git.branch + unpushed + (v.git.ahead ? ` · ${v.git.ahead} ahead` : "") + (v.git.behind ? ` · ${v.git.behind} behind` : "");
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
        // a reference is shown once (§4.5a **report line**, TD-095): an entry whose reference is
        // its PR never reads `#359 → #359` — the rule `report_line` follows for the card
        const pr = p.pr && String(p.ref) !== `#${p.pr}` ? ` <span class="st">→ #${esc(p.pr)}</span>` : "";
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
          ? "under " + cs.map((c) => `<button class="badge controller${c.gone ? " scraped" : ""}" data-act="uncontrol" data-id="${esc(id)}" data-who="${esc(c.id)}" title="${esc(c.id)} — click to remove it as a controller${c.gone ? " (its session is gone)" : ""}">${esc(c.name)} ×</button>`).join("")
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
        if (!paneGone && AO.paneIsGone(ev.session)) {
          endTerm("this session's pane is gone (see the banner).");
        }
        render(ev.session);
        if (!!ev.session.unattended !== mode) { mode = !!ev.session.unattended; reattach(); }
        // Focus is open on it, so a finish here is seen the moment it happens (TD-017)
        if (ev.session.unseen) act(id, "seen", {}).catch(() => {});
      } else if (ev.event === "session" || ev.event === "gone") {
        refreshMembership();  // another session changed: it may be a controller or a member of ours
      }
      if (ev.event === "gone" && ev.id === id) banner("session removed");
    });
  };
  // The top bar is on every page and its chips are rendered server-side, so the fit is set up here
  // rather than in any one page's init (the script tag is at the end of the body: the DOM is up).
  window.addEventListener?.("resize", fitUsage);  // `?.`: the node probe of test_ui_inbox has no real window
  fitUsage();
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
})();
