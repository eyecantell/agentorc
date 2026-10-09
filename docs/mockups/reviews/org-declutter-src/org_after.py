"""Render before.html and its after-transform (static files, no live presses)."""
import sys
from playwright.sync_api import sync_playwright
d = "/home/kmaster/ao-shots/orgreview"
js = open(f"{d}/after.js").read()
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000}, color_scheme="dark")
    pg.goto(f"file://{d}/before.html"); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{d}/before-full.png", full_page=True)
    pg.screenshot(path=f"{d}/before-top.png", clip={"x": 0, "y": 0, "width": 1600, "height": 1090})
    pg.evaluate(js); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{d}/after-full.png", full_page=True)
    pg.screenshot(path=f"{d}/after-top.png", clip={"x": 0, "y": 0, "width": 1600, "height": 1000})
    # the team's i open, and the New menu open
    pg.evaluate("""(() => { const p = document.querySelector('#help-team-ao-grind'); p.hidden = false; p.style.display = 'block';
      document.querySelector('.topbar').insertAdjacentHTML('beforeend', '<div class="newmenu"><a class="hl" href="#">Session<span>an agent: profile, role, team, directory</span></a><a href="#">Shell<span>a shell: host + directory, nothing else</span></a></div>'); })()""")
    pg.wait_for_timeout(300)
    pg.screenshot(path=f"{d}/after-panel.png", clip={"x": 0, "y": 0, "width": 1600, "height": 1000})
    b.close()
