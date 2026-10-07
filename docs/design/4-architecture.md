## 4. Architecture

```
laptop browser ──https──▶ agentorc UI (one process on any host with `agentorc[ui]`; a pty per open
                              │  terminal: `ssh -tt host tmux attach` ↔ xterm.js websocket)
                              │  ssh transport (no public ports on hosts beyond ssh)
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        host agent        host agent       host agent
        (kmaster)         (vps)            (host1, vpnmaster, laptop …)
          │  ├─ tmux server (systemd user unit, linger on)
          │  ├─ state dir  ~/.agentorc/sessions/<id>.json  ◀── adapter hooks write here
          │  ├─ run logs   ~/.agentorc/runs/<session>.log  ◀── tmux pipe-pane, continuous
          │  └─ policies   (run window, usage gate, reap worktrees, anchor rule)
          └─ repos from ~/.config/dev-cadence/repos.txt (+ ~/.agentorc/hosts.yml)
```


