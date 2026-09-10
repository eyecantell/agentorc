---
name: claude-code-composer-ghost-text
description: A `❯ text` at the bottom of a Claude Code pane is often faint suggested-next-prompt ghost text, not an unsubmitted prompt — check with capture-pane -e
metadata:
  type: project
---

Claude Code (2.1.268, measured 2026-09-10) paints a suggested next prompt into the composer in
faint text (SGR `\e[2m`), and a session's own last prompt is a common suggestion. On a plain
`capture-pane` it is indistinguishable from typed text. The four "stuck composers" that opened
TD-027 were this plus one dead pane; nothing had been sent to them.

**Why:** a wrong reading here turns "nobody sent the wrap-up" (TD-026 gap 1) into "the send
was swallowed", and fixes the wrong thing.

**How to apply:** before calling a prompt unsubmitted, `tmux capture-pane -p -e` and look at
the attributes: typed text is `\e[39m`, a slash command `\e[38;5;153m`, ghost text `\e[2m`. In
code use `sessionorc.screen.painted_text` / the adapter's `composer()`. Related:
[[agentorc-td-grind-mechanics]].
