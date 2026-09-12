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
    const r = await fetch(`/api/sessions/${id}/${action}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}),
    });
    if (!r.ok) { let t = r.statusText; try { t = (await r.json()).detail || t; } catch (e) {} throw new Error(t); }
    return r.json();
  }
  AO.act = act;

  // vscode:// links: hand the URL to the protocol handler without navigating this tab away
  // (a plain click replaced the Herd with a blank page when the handler declined — first-use finding).
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest('a[href^="vscode://"]');
    if (!a) return;
    ev.preventDefault();
    const f = document.createElement("iframe"); f.style.display = "none"; f.src = a.href;
    document.body.appendChild(f); setTimeout(() => f.remove(), 3000);
    AO.toast("opening in VS Code…", true);
  });

  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-act], [data-copy], [data-sort]");
    if (!b) return;
    if (b.dataset.copy) { navigator.clipboard.writeText(b.dataset.copy).then(() => AO.toast("copied", true)); return; }
    if (b.dataset.sort) { setSort(b.dataset.sort); return; }
    const id = b.dataset.id, action = b.dataset.act;
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    const details = b.closest("details"); if (details) details.open = false;
    try {
      let body = {};
      if (action === "mode") body = { unattended: !b.classList.contains("on") };
      const res = await act(id, action, body);
      if (action === "shell-here" && res.id) location.href = `/focus/${res.id}`;
      if (action === "remove") { const c = $(`#card-${CSS.escape(id)}`); if (c) c.remove(); if (location.pathname.startsWith("/focus/")) location.href = "/"; }
      if (action === "allow" || action === "deny") AO.toast(`${action}: sent through the hook`, true);
    } catch (e) { AO.toast(`${action} failed: ${e.message}`); }
  });

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

  // ---- Herd ----
  let sortMode = store.get("sort", "urgent");
  function setSort(m) { sortMode = m; store.set("sort", m); $$("[data-sort]").forEach((b) => b.classList.toggle("on", b.dataset.sort === m)); layout(); }
  function layout() {
    const grid = $("#grid"); if (!grid) return;
    const cards = $$(".sc", grid);
    if (sortMode === "urgent") {
      cards.sort((a, b) => (+a.dataset.rank - +b.dataset.rank) || a.dataset.name.localeCompare(b.dataset.name)).forEach((c) => grid.appendChild(c));
    } else {
      const order = store.get("pinned", []);
      cards.sort((a, b) => { const ia = order.indexOf(a.dataset.id), ib = order.indexOf(b.dataset.id); return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib); }).forEach((c) => grid.appendChild(c));
      store.set("pinned", $$(".sc", grid).map((c) => c.dataset.id));
    }
    cards.forEach((c) => c.classList.toggle("highlight", sortMode === "pinned" && c.dataset.state === "needs-you"));
    applyFilter();
    const shown = $$(".sc", grid).filter((c) => !c.hidden);
    $("#count").textContent = `${shown.length} session${shown.length === 1 ? "" : "s"}`;
    $("#empty").hidden = shown.length > 0;
    const counts = {}; shown.forEach((c) => (counts[c.dataset.state] = (counts[c.dataset.state] || 0) + 1));
    $("#badges").innerHTML = [["needs-you", "needs", "needs you"], ["limited", "limited", "limited"], ["stalled?", "stalled", "stalled"]]
      .filter(([k]) => counts[k]).map(([k, cls, l]) => `<span class="pill s-${cls}"><span class="dot"></span>${counts[k]} ${l}</span>`).join("");
  }
  function applyFilter() {
    const q = ($("#filter") ? $("#filter").value : "").toLowerCase(), cmd = $("#showcmd") && $("#showcmd").checked;
    $$("#grid .sc").forEach((c) => {
      const hideKind = c.dataset.kind === "command" && !cmd;
      c.hidden = hideKind || (q && !c.textContent.toLowerCase().includes(q));
    });
  }
  AO.herd = function () {
    $$("[data-sort]").forEach((b) => b.classList.toggle("on", b.dataset.sort === sortMode));
    $("#filter").addEventListener("input", layout);
    $("#showcmd").addEventListener("change", layout);
    $("#retry").addEventListener("click", () => location.reload());
    // drag to pin: HTML5 drag on cards, order saved by id
    const grid = $("#grid");
    grid.addEventListener("dragstart", (e) => { const c = e.target.closest(".sc"); if (c) { e.dataTransfer.setData("text/plain", c.dataset.id); c.classList.add("dragging"); } });
    grid.addEventListener("dragover", (e) => { if (sortMode === "pinned") e.preventDefault(); });
    grid.addEventListener("drop", (e) => {
      if (sortMode !== "pinned") return; e.preventDefault();
      const id = e.dataTransfer.getData("text/plain"), from = $(`#card-${CSS.escape(id)}`), to = e.target.closest(".sc");
      if (from && to && from !== to) { grid.insertBefore(from, to); store.set("pinned", $$(".sc", grid).map((c) => c.dataset.id)); }
    });
    $$("#grid .sc").forEach((c) => (c.draggable = true));
    layout();
    connectEvents((ev) => {
      if (ev.event === "session") {
        const old = $(`#card-${CSS.escape(ev.id)}`);
        const tpl = document.createElement("template"); tpl.innerHTML = ev.html.trim();
        const fresh = tpl.content.firstElementChild; fresh.draggable = true;
        if (old) old.replaceWith(fresh); else if (sortMode === "pinned") grid.prepend(fresh); else grid.appendChild(fresh);
        layout();
      } else if (ev.event === "gone") {
        const c = $(`#card-${CSS.escape(ev.id)}`); if (c) c.remove(); layout();
      } else if (ev.event === "error") AO.toast(ev.text);
    });
  };

  // ---- New session: the anchor rule, shown before you press Start ----
  AO.newSession = function () {
    const dir = $("[name=dir]"), here = $("[name=where][value=here]"), wt = $("[name=where][value=worktree]"), note = $("#occupancy");
    let seq = 0;
    async function check() {
      const v = dir.value.trim(); const my = ++seq;
      if (!v) { note.textContent = ""; here.disabled = false; return; }
      try {
        const r = await fetch(`/api/occupancy?dir=${encodeURIComponent(v)}`); const o = await r.json();
        if (my !== seq) return;
        if (o.occupants && o.occupants.length) {
          note.innerHTML = `⚠ <b>in use</b> by ${esc(o.occupants.join(", "))} — one agent session per directory (design §9); a new worktree is selected instead.`;
          here.disabled = true; wt.checked = true;
        } else {
          here.disabled = false;
          note.textContent = o.git ? "free · a git repo, so a worktree is available" : (o.dir ? "free" : "");
        }
      } catch (e) { note.textContent = ""; here.disabled = false; }
    }
    dir.addEventListener("input", () => { clearTimeout(dir._t); dir._t = setTimeout(check, 250); });
    dir.addEventListener("change", check);
    check();

    // One name, one session (design §4.1, §9 invariant 12): say what Start would do before it is
    // pressed — a live holder is a refusal, so offer Switch to instead; an exited one is replaced
    // and its run log kept. Same texts as `ao new` prints: the agent composes them (TD-030).
    const nm = $("[name=name]"), start = $("button[type=submit]"), nnote = $("#namecheck");
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
  };

  // ---- Focus ----
  AO.focus = function (s) {
    const id = s.id;
    // scrollback: 0 — tmux owns the history (TD-022). The bridge sets `mouse on` on the session, so
    // tmux asks for mouse tracking and xterm.js forwards the wheel to it (copy mode, its history);
    // a local buffer would only ever hold stale repaints for the wheel to land on when tmux is not
    // tracking. Shift+PageUp/PageDown below are the keyboard path; Shift+drag selects locally.
    const term = new Terminal({ cursorBlink: true, fontFamily: '"JetBrains Mono", Menlo, monospace', fontSize: 13, theme: { background: "#0b0e12" }, scrollback: 0 });
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
      } else { compose.disabled = false; $("#composehint").textContent = ""; }
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
      const checks = v.ready || [];
      $("#checks").innerHTML = checks.map(([n, ok]) => `<div class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"} ${esc(n)}</div>`).join("");
      $("#closebtn").disabled = !(checks.length && checks.every(([, ok]) => ok) && ["idle", "exited"].includes(v.state));
      $$("[data-act=mode]").forEach((b) => { b.classList.toggle("on", !!v.unattended); b.textContent = v.unattended ? "unattended" : "interactive"; });
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
      }
      if (ev.event === "gone" && ev.id === id) banner("session removed");
    });
  };
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
})();
