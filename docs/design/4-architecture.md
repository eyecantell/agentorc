## 4. Architecture

```
laptop browser ──https──▶ agentorc UI (one process on the home, `agentorc[ui]`; a pty per open
                              │  terminal: `tmux attach` ↔ xterm.js websocket; ssh to a node's host, §4.6)
                              ▼  Unix socket
                        home host agent (kmaster, §4.4a): the org's graph, mail, settings
                          ├─ tmux server (systemd user unit, linger on)
                          ├─ state dir  ~/.agentorc/sessions/<id>.json  ◀── adapter hooks write here
                          ├─ run logs   ~/.agentorc/runs/<session>.log  ◀── tmux pipe-pane, continuous
                          ├─ policies   (§6: the tick — stop time, usage gate, seats, restarts, promote)
                          └─ repos from ~/.config/dev-cadence/repos.txt (+ ~/.agentorc/hosts.yml)
                              ▲ the node→home link (ssh, or a per-node socket)
              ┌───────────────┴───────────────┐
        node host agent                  node host agent
        (a container on kmaster)         (laptop, vps … — a machine node, not yet in use)
          each with its own tmux server, state dir, run logs and stopping policies (§4.4a)
```


