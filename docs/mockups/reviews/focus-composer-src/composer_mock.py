"""Read-only: open a live Focus page and reshape its DOM into the composer mockups; never presses anything.
Writes current.png, collapsed.png, collapsed-open.png, sidebar.png under ~/ao-shots/composer/."""
import sys
from playwright.sync_api import sync_playwright
sid = sys.argv[1]
OUT = "/home/kmaster/ao-shots/composer/"
PREP = """() => {
  // look as Paul's interactive Focus does: no read-only line, composer shown, no Take over
  document.querySelector('#termro').classList.add('hidden');
  if (!document.querySelector('#mockbar')) document.querySelector('#composer').classList.remove('hidden');
  for (const b of document.querySelectorAll('#fhead ~ .row button, .main button')) if (b.textContent.includes('Take over')) b.textContent = 'Close session';
}"""
FILL = """(barH) => {  // the terminal takes the height down to the bar
  const t = document.querySelector('#term'); const top = t.getBoundingClientRect().top;
  t.style.height = (innerHeight - top - barH - 26) + 'px';
}"""
BAR = """() => {
  const c = document.querySelector('#composer'); c.classList.add('hidden');
  const bar = document.createElement('div'); bar.className = 'card'; bar.id = 'mockbar';
  bar.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 10px;margin-top:8px;';
  bar.innerHTML = `<button class="btn sm" style="flex:1;justify-content:flex-start;text-align:left;opacity:.75;font-weight:400">✎ Compose a prompt…  <span class="muted" style="margin-left:8px">(c · or paste / drop a file)</span></button>`
    + document.querySelector('#attach').outerHTML.replace('id="attach"', '')
    + `<button class="btn primary sm">→ Send</button>`;
  c.after(bar);
}"""
OPEN = """() => {  // expanded: floats over the terminal's foot, so the terminal never reflows
  const c = document.querySelector('#composer'); const t = document.querySelector('#term');
  c.classList.remove('hidden'); document.querySelector('#mockbar').style.visibility = 'hidden';
  const r = t.getBoundingClientRect();
  c.style.cssText = `position:fixed;left:${r.left}px;width:${r.width}px;bottom:14px;z-index:20;box-shadow:0 -6px 24px rgba(0,0,0,.55);`;
  const ta = document.querySelector('#compose'); ta.style.height = '150px';
  ta.value = "Pick up TD-472 next. Keep the terminal paste on the composer's road:\\n- a text-less file paste uploads through attach\\n- the path goes in as a bracketed paste\\n\\n/home/kmaster/.agentorc/attachments/ao-agentorc-ao-paul/paste-20261009-084349.png";
  const hint = document.querySelector('#composehint'); hint.textContent = 'Esc folds it back · Ctrl+Enter sends';
}"""
SIDE = """() => {
  const c = document.querySelector('#composer'); const side = document.querySelector('#side');
  const sess = side.querySelector('[data-side="session"]');
  c.style.cssText = 'margin-top:12px;'; (sess || side.lastElementChild).after(c);
  document.querySelector('#compose').style.height = '140px';
}"""
SIDE_FOLDED = """() => {  // the best case: every other side card folded
  for (const d of document.querySelectorAll('#side details')) d.open = false;
}"""
def page(b):
    pg = b.new_page(viewport={"width": 1920, "height": 940}, color_scheme="dark")
    # freeze the page's state: /events never opens, so no push re-renders the header or the composer
    pg.add_init_script("""(() => { const W = window.WebSocket; window.WebSocket = function (u, p) {
      if (String(u).includes('/events')) return { readyState: 0, send() {}, close() {}, addEventListener() {}, removeEventListener() {} };
      return new W(u, p); }; window.WebSocket.prototype = W.prototype; Object.assign(window.WebSocket, W); })()""")
    pg.goto(f"http://127.0.0.1:8765/focus/{sid}"); pg.wait_for_timeout(6000); pg.evaluate(PREP); pg.wait_for_timeout(300)
    return pg
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = page(b); pg.wait_for_timeout(800); pg.evaluate(PREP); pg.wait_for_timeout(2500); pg.screenshot(path=OUT + "current.png"); pg.close()
    pg = page(b); pg.evaluate(BAR); pg.evaluate(FILL, 46); pg.wait_for_timeout(1500); pg.evaluate(PREP); pg.wait_for_timeout(2500); pg.screenshot(path=OUT + "collapsed.png")
    pg.evaluate(OPEN); pg.wait_for_timeout(500); pg.evaluate(PREP); pg.wait_for_timeout(2500); pg.screenshot(path=OUT + "collapsed-open.png"); pg.close()
    pg = page(b); pg.evaluate(SIDE); pg.evaluate(FILL, 0); pg.wait_for_timeout(1500); pg.evaluate(PREP); pg.wait_for_timeout(2500); pg.screenshot(path=OUT + "sidebar.png")
    pg.evaluate(SIDE_FOLDED); pg.wait_for_timeout(500); pg.evaluate(PREP); pg.wait_for_timeout(2500); pg.screenshot(path=OUT + "sidebar-folded.png"); pg.close()
    b.close()
