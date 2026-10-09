"""Read-only: open the live Org page, let scripts run, save the rendered DOM without scripts."""
import re, sys
from playwright.sync_api import sync_playwright
out = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000}, color_scheme="dark")
    pg.goto("http://127.0.0.1:8765/")
    pg.wait_for_timeout(6000)
    html = pg.evaluate("document.documentElement.outerHTML")
    html = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
    html = html.replace("<head>", '<head><base href="http://127.0.0.1:8765/">', 1)
    open(out, "w").write("<!doctype html>\n" + html)
    pg.screenshot(path=out.replace(".html", ".png"), full_page=True)
    b.close()
