## 7. Phases

The plan, re-baselined against what runs. Each phase states what is built and what is not.

1. **PoC, kmaster, Claude Code only.** Host agent + Claude Code adapter (hooks, transcript
   locator, usage, creds) + team page with the `/events` websocket + focus page with the pty
   bridge + new-session flow + VS Code link + the `shell` adapter, the Shell button and the
   hook-channel permission answer. Desktop only; no tab filters. Success test: every session
   Paul has open on kmaster shows the right state within 5 s of a change, and a permission
   prompt can be answered from the browser.
   **Built**, and the success test passes. Also built here, ahead of phase 5: the `ao --skill`
   text (TD-019). Attachments (TD-002): Attach, drop and paste, for a
   session on this host. **Not built:** the phone layout (TD-003).
2. **Second host.** `hosts.yml`, the **home and node** split (§4.4a; it replaces a hub-and-spoke
   ssh transport at about the same cost): the `host` field and `id@host` addresses, home and
   node modes, the multiplexed node→home link over ssh, nodes reporting records and executing
   acts the home gates, the UI talking to the home. herdr does not replace this step
   ([ADR](../decisions/2026-09-10-herdr-spike.md)).
   **Built:** home and node modes, the link, and a container node on the home's machine
   (§4.4a *A container node*). **Not built:** a machine node in use — the VPS or laptop added
   as a node and a session started there from the UI, mail from a laptop worker to its kmaster
   manager through a laptop sleep, the laptop closed for an hour with the session still there
   (TD-057); the phone's route in (WireGuard client, or the Cloudflare tunnel) and the phone
   layout (Org + narrow Focus); the terminal over the link; attachment upload over ssh (drag
   and drop, picker, paste — the copy path is the same plumbing).
3. **tdgrind migration.** Port tdgrind's supervisor into policies (§6) driven by samscrape's
   `.agentorc.yml`; run both side by side for one window with tdgrind's cron disabled and the
   host agent's policies enabled; compare `tdgrind runs` reports against agentorc run logs;
   then delete `tdgrind.sh` from samscrape (ledger a TD there for the swap and the cron line
   in `infra/kmaster/crontab`). **Capabilities, report channels and presets** (§4.8) open this
   phase, because the migration is the first time several workers run at once and the Org has
   to say what each is doing and keep them off each other: the caller check and the `control`
   grant first, then `progress` / `findings` with `ao progress` / `ao finding`, the derived
   source on the tick, the card's report line and the Focus Reports panel, and last the
   presets — with a manager run as a session for a few evenings before its mechanical rules
   become policies here.
   **Built:** the stop time (`ao until`); the usage gate is in build. The samscrape team is
   defined, its briefs ported from tdgrind. **Not built:** the run window, stall,
   credential-lapse, exit-reap and stranded-work policies; tdgrind's cron still runs, so the
   side-by-side window has not started.
4. **Commands + board.** `.agentorc.yml` buttons (cmdorc where it fits), command-kind sessions
   and the Commands page, the Resumable page (§4.5 screen 4), the Due strip on the Org,
   stranded-work flags; each page gets its top-bar tab when it is built (TD-123). **Not
   started**, except the board's Snooze / Done write-back, built for the Inbox's board rows
   (TD-069 step 3); the Attention tab is struck (§4.5 screen 7).
5. **Second adapter.** Gemini CLI (hook-fed if the OSC 9 / hooks story verifies) or a scraped
   plain-shell adapter, whichever proves the contract better. Publish to PyPI, write the
   adapter-author guide. **Not started**, except that `ao --skill` and `ao team --skill` are
   built; what remains of TD-019 here is offering to install the skill from the New session
   flow.

