---
name: unattended-workers-run-inside-agentorc
description: Paul wants TD-grind workers for agentorc launched as agentorc sessions (ao new --unattended), never from a laptop-bound terminal
metadata:
  type: feedback
---

Launch unattended TD-grind workers for this repo as agentorc sessions with
`ao new -w <name> --unattended --prompt "$(cat docs/briefs/<name>.md)"`, not via the samscrape
`tdgrind.sh` supervisor or a terminal on Paul's laptop. First one: `tdgrind-ao-1`, 2026-09-09.

**Why:** Paul said on 2026-09-09 "our tdgrind sessions should be in agentorc so we can close
this laptop". An ao session lives in tmux on kmaster and shows on the Herd; a VS Code terminal
dies with the laptop. Also: in auto mode the permission classifier blocks writes outside the
repo (`~/.tdgrind-agentorc`, crontab), so the supervisor route could not be set up anyway.

**How to apply:** briefs live in `docs/briefs/` (see its README). Each brief carries its own
stop time and usage rule because there is no supervisor yet (design §6, phase 3). Tell the
worker to avoid live `ao`/service commands: it runs inside the agentorc it would be poking.
