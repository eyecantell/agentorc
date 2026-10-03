---
name: headless-screenshots-on-kmaster
description: "How to screenshot the live agentorc UI on kmaster — snap Firefox headless, from a non-hidden home directory"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 192b2355-2009-4e5e-ab33-69257956a53b
  modified: 2026-10-03T04:40:00.000Z
---

kmaster has no Chromium or Playwright; the snap Firefox takes headless screenshots (verified 2026-09-20):

```bash
D=~/ao-shots; mkdir -p $D/prof
timeout 120 firefox --headless --no-remote --profile $D/prof --window-size 1920,1000 \
  --screenshot $D/inbox.png http://127.0.0.1:8765/inbox
```

The snap sandbox cannot see `/tmp`, the scratchpad, or hidden directories (`~/.cache/...`): the profile and
output must be under a plain home directory, or it fails with "Could not find profile folder" / "already
running". The shot is taken at load, in the light theme, before page JS fills local times and countdowns
(they show as `…`). Read-only GET of a page — fine against the live UI; never press anything there.
Paul's own screenshots arrive at `/mounted/dev/tmp.png`.

**Playwright for anything after load (set up 2026-10-02, Paul's go):** the system Python has no `ensurepip`, so it lives in its own folder, installed with the live venv's pip (`--target`, the live venv untouched), with Chromium headless shell in `~/.cache/ms-playwright/`:

```bash
PYTHONPATH=~/ao-shots/pwlib ~/.local/share/agentorc-venv/bin/python ~/ao-shots/scripts/<script>.py
```

`~/ao-shots/scripts/focus_font.py` (a Focus terminal's font size after it draws) and `inbox_md.py` (the Inbox after its scripts run) are the first two. It waits for scripts, reads live values with `page.evaluate`, and shoots in the dark theme. Against the live UI it stays read-only: GETs and reads, never a press. Presses go to a scratch home ([[scratchpad-too-long-for-unix-sockets]]). Paul's own screenshots still arrive at `/mounted/dev/tmp*.png`; check the file's mtime, an old one is sometimes left there. See [[ui-review-screenshots]].
