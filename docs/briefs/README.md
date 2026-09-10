# Briefs for unattended agentorc sessions

A brief is the opening prompt of an unattended worker. Launch one from the main checkout (the
session gets its own worktree and branch, per design §4.5 "new worktree"):

```
pdm run ao new -d ~/agentorc -w tdgrind-ao-1 --unattended --prompt "$(cat docs/briefs/tdgrind-ao-1.md)" tdgrind-ao-1
```

There is no supervisor for these yet (design §6 usage gate and the run-window policy are
phase 3), so each brief carries its own stop time and usage rule. Edit the date and stop time
in the brief before relaunching. The samscrape workers still run under samscrape's
`scripts/tdgrind.sh` supervisor; moving them into agentorc is the phase 3 work.
