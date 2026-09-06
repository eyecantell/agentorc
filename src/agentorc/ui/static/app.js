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
      ws.onmessage = (m) => { setDown(false); if (reconnected) { location.reload(); return; } onEvent(JSON.parse(m.data)); };
      ws.onclose = () => { setDown(true); reconnected = true; setTimeout(open, delay); delay = Math.min(delay * 2, 10000); };
      ws.onerror = () => ws.close();
    }
    open();
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

  // ---- Focus ----
  AO.focus = function (s) {
    const id = s.id;
    const term = new Terminal({ cursorBlink: true, fontFamily: '"JetBrains Mono", Menlo, monospace', fontSize: 13, theme: { background: "#0b0e12" }, scrollback: 5000 });
    const fit = new FitAddon.FitAddon(); term.loadAddon(fit);
    term.open($("#term")); fit.fit();
    let ws, delay = 500;
    function openTerm() {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/term/${encodeURIComponent(id)}?cols=${term.cols}&rows=${term.rows}`);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => { delay = 500; ws.send(JSON.stringify({ resize: [term.cols, term.rows] })); };
      ws.onmessage = (m) => term.write(typeof m.data === "string" ? m.data : new Uint8Array(m.data));
      ws.onclose = () => { term.write("\r\n\x1b[90m[agentorc] terminal disconnected — reconnecting\x1b[0m\r\n"); setTimeout(openTerm, delay); delay = Math.min(delay * 2, 10000); };
    }
    openTerm();
    term.onData((d) => ws && ws.readyState === 1 && ws.send(d));
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
      } else { compose.disabled = false; $("#composehint").textContent = ""; }
      $("#fstate").innerHTML = head;
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
    connectEvents((ev) => { if (ev.event === "session" && ev.id === id) render(ev.session); if (ev.event === "gone" && ev.id === id) banner("session removed"); });
  };
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
})();
