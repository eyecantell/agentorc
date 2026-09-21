---
name: gh-comment-bodies-go-in-a-file
description: Write gh PR comment and body text to a file and pass --body-file; a `--body "…"` string lets bash run its backticks
metadata:
  type: feedback
---

Never pass prose to `gh` with `--body "…"`. Backticks in a double-quoted bash string are command
substitution, so ``the `try` block`` runs `try` and the text arrives with holes in it. Write the
body to a file (a heredoc quoted as `<<'EOF'`, or the Write tool) and pass `--body-file`. Same for
`gh pr create --body`, and for any `ao msg "…"` that quotes code.

**Why:** it is silent — the command succeeds, the comment posts, and the damage is only visible
by reading the posted text. Two sessions in the agentorc repo hit it on 2026-09-20; the second
had to patch a cadence-review comment through `gh api -X PATCH .../issues/comments/<id>`, which
is also the fix when it happens.

**How to apply:** write the body into the session's scratchpad directory, not `/tmp`
(`cat > "$SCRATCH/body.md" <<'EOF' … EOF`), then `gh pr comment N --body-file "$SCRATCH/body.md"`.
A single-quoted heredoc delimiter is what makes it literal.

Related: [[open-the-pr-before-writing-its-number]].
