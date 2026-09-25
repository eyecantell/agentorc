# An interactive UI/UX review makes the change visible

**Written 2026-09-25** at Paul's ask, after TD-156 (the Focus screen) and TD-170 (the repo strip
and the Repo page) were reviewed with him in one cloud session.

**The rule (Paul's words, corrected the same day).** When an interactive UI/UX review with Paul
produces significant changes — a screen redesigned, a control moved, a new page — **make the change
visible to him**: a screenshot, a rendering, a published design page, whichever shows it best, in
the same turn as the proposal or the build, never only the artboard source or a description. The
ways that have worked:

- **the mockups**: regenerate `docs/mockups/gen.py`, then `docs/mockups/shot.sh <out> <Name>…`
  (headless Chromium; on a cloud box symlink `/opt/pw-browsers/chromium-*/chrome-linux/chrome` to
  `~/.cache/ms-playwright/chromium-1194/chrome-linux/chrome`, which is where the script looks);
- **the built page** when something was built: render the template through the real `view()`
  with a fixture record, serve `static/` beside it with `python3 -m http.server`, and shoot it at
  1440 and at 1280 (the session that built TD-156 has the script: `render_focus.py`);
- **a comparison** when two shapes are on the table: one screenshot per shape, the same fixture
  data in both, so the difference is the design and not the data.

Keep the PNGs under `docs/mockups/reviews/<date>-<topic>.png` (the precedent is
`2026-09-21-org-cards.png`) and **send them into the chat** (the file-sending tool) so Paul sees
them without opening the repo. The history line for the round names the screenshot.
