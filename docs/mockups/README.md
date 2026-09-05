# Mockup sources

`gen.py` emits the `.dc.html` artboards and `canvas.json` for the design canvas at
https://claude.ai/code/artifact/0e14af3a-5e5a-4d9c-88b2-74205c394c04 (Claude Design preview).
Edit `gen.py`, run it, then `./shot.sh` for a quick headless-Chromium look (PNGs in /tmp/agentorc-shots), then re-seed
The artboards are plain HTML inside `<x-dc>`; to preview one locally, move the `<helmet>` contents
into `<head>`, drop the wrappers, and open it in a browser (see the session that built them).
