(() => {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; };
  const imark = (title) => `<button class="helpmark" type="button" title="${title.replace(/"/g, '&quot;')}">i</button>`;
  document.head.appendChild(el(`<style>
    .facet .fhead { min-height: 26px; }
    .bar, .blocks { min-height: 26px; height: 26px; }
    .bseg, .blk { min-height: 26px; height: 26px; font-weight: 600; }
    .flowchip { text-decoration: none; cursor: pointer; }
    .tgroup .ghead .foldmail { display: inline-block !important; }
    .lanechip { margin-left: 6px; flex: none; } .sc .r2 .cline { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .facet .kind { white-space: nowrap; } .frepo .fhead > .seg { margin-left: auto; }
    .lanecnt { display: inline-block; margin-left: 6px; padding: 0 5px; border-radius: 3px; border: 1px solid; font-weight: 700; color: var(--fg); } .lanecnt.k-pickable { background: color-mix(in srgb, var(--k-pickable) 25%, transparent); border-color: var(--k-pickable); } .lanecnt.k-design { background: color-mix(in srgb, var(--k-design) 25%, transparent); border-color: var(--k-design); } .lanecnt.solid.k-pickable { background: var(--k-pickable); color: var(--seg-fg); } .lanecnt.solid.k-design { background: var(--k-design); color: var(--seg-fg); }
    .tsum { grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr) minmax(0, 1.2fr); }
    .rollup.lone { grid-template-columns: 2fr 1fr; }
    .defblock { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .newmenu { position: absolute; right: 18px; top: 46px; background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 4px; display: flex; flex-direction: column; z-index: 9; min-width: 210px; }
    .newmenu a { padding: 6px 10px; color: var(--fg); text-decoration: none; font-size: var(--t-small); border-radius: 4px; }
    .newmenu a:hover, .newmenu a.hl { background: var(--hover); }
    .newmenu span { color: var(--muted); display: block; font-size: var(--t-cap); }
  </style>`));
  // 4: one "+ New" in the top bar
  const sh = $('#shellbtn'); if (sh) sh.remove();
  const nb = $('.topbar a[href="/new"]'); if (nb) nb.textContent = '+ New ▾';
  // 1: mine and show command runs become filter tokens
  $('#mine')?.remove(); $('#showcmd')?.closest('label')?.remove();
  const f = $('#filter'); if (f) { f.placeholder = 'filter…  team:  state:  mine  kind:command'; f.style.width = '340px'; }
  // 3: the Agents blurb becomes an i
  const ag = $('.rollup .facet'); if (ag) { const m = $(':scope > .meta', ag); if (m) m.remove();
    $('.fhead', ag).insertAdjacentHTML('beforeend', imark('in urgency order, as the cards sort · a pill filters the page to that state')); }
  // Needs you: hide a row at 0
  $$('.rollup .fneeds .nrow').forEach((r) => { if ($('b.big', r)?.textContent.trim() === '0') r.remove(); });
  // 5, 6: team headers
  $$('.tgroup').forEach((g) => {
    const h = $('.ghead', g); if (!h) return; const team = g.dataset.team;
    $$('.unfoldonly', h).forEach((e) => e.remove());
    const fs = $('.flowstrip', h);
    if (fs) { const name = (fs.textContent.match(/flow: ([\w-]+)/) || [])[1];
      fs.replaceWith(el(`<a class="badge flowchip" href="/settings#team-${team}" title="${fs.textContent.replace(/^· /, '')} — pick the flow on Settings">flow: ${name}</a>`)); }
    $('select.flowpick', h)?.remove();
    // the definition: Members…, where it is defined, Open file → the i panel
    const mem = $('button[data-members], button[disabled]', h);
    const why = mem?.nextElementSibling?.matches('span.meta') ? mem.nextElementSibling : null;
    const open = $('a.btn', h);
    const nc = $('.notconcluded', h);
    const panel = $('.helppanel', h);
    const parts = [mem, why, open].filter(Boolean);
    if (panel && (parts.length || nc)) {
      const blk = el('<div class="defblock"><b>Definition</b></div>');
      parts.forEach((p) => blk.appendChild(p));
      blk.appendChild(el(`<a class="btn sm ghost" href="/settings#team-${team}">Flow on Settings →</a>`));
      panel.prepend(blk);
      if (nc) { panel.insertBefore(nc, blk.nextSibling); }
    }
    // ✉ n beside the name, folded or not
    const mail = $('.foldmail', h); const fc = $('.flowchip', h) || $('.meta', h);
    if (mail && fc) fc.after(mail);
    // the answered mark before the grow
    const am = $('.answeredmark', h); const grow = $('.grow', h); if (am && grow) grow.before(am);
    // the i after the fold
    const im = $('.helpmark', h), fold = $('.fold', h); if (im && fold) fold.after(im);
  });
  // 7: the lanes line → an i on Technical debt, counts on the members' cards
  $$('.laneline').forEach((ln) => {
    const txt = ln.textContent.trim(); const head = ln.closest('.facet') && $$('.fhead', ln.closest('.facet'))[1];
    const team = ln.closest('.tgroup')?.dataset.team;
    const pk = (txt.match(/(\d+) pickable/) || [])[1], df = (txt.match(/(\d+) design-first/) || [])[1];
    if (head) { const k = $('.kind', head); k.insertAdjacentHTML('afterend', imark(txt)); }
    ln.remove();
    $$(`.tgroup[data-team="${team}"] .card`).forEach((c) => {
      const role = $('.cline', c)?.textContent || ''; const pill = $('.r1 .pill', c); if (!pill) return;
      const n = /^Grinder/.test(role) ? +pk : /^Designer/.test(role) ? +df : 0; if (!n) return;
      const k = /^Grinder/.test(role) ? 'k-pickable' : 'k-design';
      const busy = !/out of work/.test(role);  // solid only when it says it has nothing, with entries waiting
      pill.insertAdjacentHTML('beforeend', `<span class="lanecnt ${k}${busy ? '' : ' solid'}" title="${n} ${k === 'k-design' ? 'design-first' : 'pickable'} entries in its lane">${n}</span>`);
    });
  });
  // round 2: the + card says New session; Doing loses its gloss
  $$('.plusgo .meta').forEach((m) => { m.textContent = 'New session'; });
  $$('.fv[data-fv="doing"] .fhead > .meta').forEach((m) => m.remove());
  // a team with nothing live draws no summary
  $$('.tgroup[data-live="0"] .tsum').forEach((t) => t.remove());
  // the rollup drops the TD and PR facets while one team is live: that team's own facets say it
  const live = $$('.tgroup').filter((g) => +g.dataset.live > 0);
  if (live.length === 1) { const fs = $$('.rollup > .facet'); fs[1]?.remove(); fs[2]?.remove(); $('.rollup')?.classList.add('lone'); }
})();
