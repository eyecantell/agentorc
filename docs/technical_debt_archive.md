

## TD-267: Build the closed card: Forget first in its foot, a menu of what applies, and the hover that says when the record goes

**Priority:** Medium
**Type:** feature
**Added:** 2026-10-01 (the designer, TD-266's build)
**Owner:** grinder
**Kind:** build
**Status:** Done
**Location:** `src/agentorc/ui/cards.py` (`next_act`; the `closed` branch of the slot: `full`), `src/agentorc/ui/templates/card.html` (the foot's `forget` branch; the `nopane` variable; the *more ⋯* menu), `src/agentorc/ui/static/app.js` (the exited / closed banner — Resume, New session here, Forget — is the script's, about lines 2745–2763; `focus.html` holds only the empty `#fexited`), `src/agentorc/ui/help.py` (the `forget` entry's *where* already reads *a card's foot*), `docs/mockups/gen.py` (the closed card's foot: already Forget · Details, regenerated with the design), `tests/test_ui_org.py`

**Why:** design §4.5 *The card's anatomy* row 6 and §4.5a **Forget** / **Details** / **more ▾** (TD-266): a closed session in no team sat on the Org with Details as its lead and a menu of Wrap up, Kill, Close, Switch to unattended, Open shell here, Pop out and Copy tmux command — every one acting on a process or a pane that was gone — and no Forget; Paul looked for the way to remove it and did not find it. The `remove` RPC accepts a `closed` record already (`rpc_remove`: `exited` or `closed`); `ao forget` and the Details page's banner reach it; the card did not.

**Resolved:** 2026-10-02 (PR #912) — built as designed: `cards.next_act` leads a closed card with Forget, `card.html`'s `nopane` menu draws Message…, Restart and Forget (not on a seat), and `cards.closed_keep` gives the hover's and the Details banner's *forgotten by itself a day after the close* from `CLOSED_KEEP`. The lasting content is design §4.5 row 6 and §4.5a **more ▾** / **Details** / **Forget**; `tests/test_ui_org.py` holds it.

**Done when:** a closed session in no team is removed from its card on the Org with one press of Forget, its *more ⋯* holds nothing that acts on a gone process, and its hover says the record goes by itself a day after the close; the tests above pass.

**Related:** TD-266 (the design), TD-095 (the card's anatomy, the foot's rule), TD-262 / TD-265 (the closer's words in the same slot — the hover's tail follows them), TD-156 (Forget all on a team's header), TD-250 (Restart in the menu), TD-077 (Forget refused on a suspended record).
