## 8. Lessons carried in (dev-cadence + tdgrind)

- **One anchor per checkout** — enforced at creation, not warned about later.
- **Ledger before idle** — the stranded-work flag makes the audit continuous.
- **One writer per shared resource** — only the host agent touches tmux.
- **State from the tool, not from the screen** — scraped state is labelled, never silent.
- **A record that exists only in scrollback is not a record** — pipe-pane from the first byte.
- **A reboot must not need a human** — systemd + linger, not a cron that notices.
- **Verify a real completion, not a catalog** — `credentials_ok()` and `usage()` are live
  calls, and the UI shows when they last succeeded.

**Which side a thing lives on** (TD-159, [ADR 2026-10-08](../decisions/2026-10-08-agentorc-dev-cadence-split.md)).
dev-cadence owns what a repo needs with no agentorc on the machine: the ledger and board formats,
the cadence check, the git hooks, and the skills and scripts that read a checkout. agentorc owns
what needs a host agent, a session record or a second session.
- A file needed both ways stays dev-cadence's. agentorc reads it as a client, and a second reader
  inside agentorc (`sessionorc/ledger.py`, `sessionorc/board.py`) is held equal to the script by a
  test and named in cadence §7's parity table.
- A synced file never calls `ao`. Where it must behave differently in an agentorc session, it
  reads an environment variable that agentorc's launch sets, as `CADENCE_ATTENTION_SCOPE` is
  (TD-425).

