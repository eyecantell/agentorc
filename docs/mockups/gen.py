#!/usr/bin/env python3
"""Emit the agentorc mockup artboards (.dc.html) + canvas.json from one shared style."""
import json, pathlib, re

OUT = pathlib.Path(__file__).parent

CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
  body { margin: 0; background: #f4f5f7; color: #1c2128; font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; font-size: 14px; line-height: 1.5; }
  a { color: #1f5fa8; text-decoration: none; } a:hover { color: #164a85; text-decoration: underline; }
  .mono { font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace; }
  .pill { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; line-height: 1; }
  .pill .dot { display: none; }
  /* design §4.5a **state icon**, as app.css draws it: a page of cards is read by shape before it is
     read by word, and a glyph never looks like something to press */
  .pill::before { font-size: 11px; line-height: 1; }
  .pill.s-needs::before { content: "▲"; } .pill.s-limited::before { content: "◔"; font-size: 12px; } .pill.s-stalled::before { content: "?"; font-size: 12px; }
  .pill.s-working::before { content: "∿"; font-size: 14px; } .pill.s-idle::before { content: "›_"; font-size: 12px; letter-spacing: -1px; } .pill.s-exited::before { content: "◌"; font-size: 12px; }
  .pill.s-closed::before { content: "✓"; font-size: 12px; } .pill.s-unreachable::before { content: "⌀"; font-size: 12px; } .pill.s-oncall::before { content: "◇"; font-size: 12px; }
  .pill.s-idle.unseen::before { content: "●"; font-size: 11px; letter-spacing: 0; }
  .pill.plain::before { content: none; }  /* a due date or a report's status is not a session state */
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  /* the state tokens (design §4.5 *The card's anatomy*, TD-095): working green — alive; idle blue —
     alive, at rest, may be spoken to (idle · unseen is idle's blue); everything over or out of
     reach one grey (--ended). On a card green means working and nothing else. */
  .s-working { background: #dcfce7; color: #166534; }
  .s-needs { background: #fde68a; color: #7c3d00; }
  .s-idle { background: #dbeafe; color: #1e40af; }
  .s-ended { background: #e5e7eb; color: #4b5563; }
  .s-stalled { background: #fecaca; color: #991b1b; }
  .s-exited, .s-closed, .s-oncall { background: #e5e7eb; color: #4b5563; }
  .s-done { background: #d1fae5; color: #065f46; } /* Focus's reports and checklist only — --done leaves the card */
  .s-limited { background: #ede9fe; color: #5b21b6; }
  .s-unreachable { background: #e5e7eb; color: #4b5563; }
  .card.off { opacity: .55; }
  .due { display: flex; align-items: center; gap: 10px; padding: 6px 10px; }
  .due + .due { border-top: 1px solid #eceef1; }
  .pill.scraped { outline: 1px dashed #d9a441; outline-offset: 1px; }
  .badge { display: inline-block; padding: 1px 5px; border: 1px solid #cbd0d6; border-radius: 3px; font-size: 12px; color: #5b6470; font-family: "JetBrains Mono", monospace; }
  .badge.scraped { border-style: dashed; color: #8a5a00; border-color: #d9a441; }
  .badge.toggle { cursor: pointer; padding-left: 4px; white-space: nowrap; }
  .badge.toggle::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; border: 1px solid currentColor; margin-right: 4px; vertical-align: 0; }
  .badge.toggle.on { color: #fff; background: #1c2128; border-color: #1c2128; }
  .badge.toggle.on::before { background: #fff; border-color: #fff; }
  .btn { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px; border: 1px solid #cbd0d6; border-radius: 4px; background: #fff; color: #1c2128; font-size: 14px; font-weight: 500; white-space: nowrap; }
  .btn.primary { background: #1c2128; color: #fff; border-color: #1c2128; }
  .btn.danger { color: #991b1b; border-color: #e5b4b4; }
  .btn.ghost { border-color: transparent; background: transparent; color: #4b5563; }
  .btn.ghost:hover { background: #eef0f3; }
  .status { display: block; padding: 3px 0 3px 10px; border-left: 2px solid #cbd0d6; font-family: "JetBrains Mono", monospace; font-size: 14px; color: #4b5563; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .status.ok { border-color: #059669; } .status.end { border-color: #9ca3af; color: #374151; } .status.bad { border-color: #dc2626; } .status.lim { border-color: #7c3aed; }
  /* the doing line wraps rather than truncating — it is a sentence, not a log line (§4.5a, TD-074) */
  .status.doing { white-space: normal; overflow-wrap: anywhere; } .meta.doing { color: #374151; }
  /* the role badge's picture, sized to sit on the badge's baseline (src/agentorc/ui/static/app.css) */
  .badge .ricon { vertical-align: -2px; margin-right: 3px; }
  .btn svg { width: 14px; height: 14px; flex-shrink: 0; }
  .flag svg { width: 13px; height: 13px; flex-shrink: 0; }
  svg { width: 14px; height: 14px; }
  .flag { display: inline-flex; align-items: center; gap: 4px; color: #991b1b; font-size: 12px; font-weight: 500; white-space: nowrap; flex-shrink: 0; }
  .card { background: #fff; border: 1px solid #dfe3e8; border-radius: 6px; }
  .sc { display: flex; flex-direction: column; gap: 16px; padding: 16px; overflow: hidden; position: relative; }
  .sc-body { display: flex; flex-direction: column; gap: 8px; }
  /* `min-height`, as the page has it (app.css): a fixed height clipped the doing line's second
     row and stacked the stalled note on top of it (TD-074 step 6) */
  .sc-slot { min-height: 54px; display: flex; flex-direction: column; gap: 8px; justify-content: flex-start; }
  .sc-foot { display: flex; gap: 6px; align-items: center; }
  /* the card's anatomy (design §4.5, TD-095): six rows, the same six on every card, at one height —
     a row with nothing to say stays, empty, rather than moving what is below it */
  .ac { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: 26px 22px 20px 20px 62px 32px; row-gap: 6px; padding: 12px 14px 10px 17px; position: relative; overflow: hidden; }
  .ac .r { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .ac .r > * { flex-shrink: 0; } .ac .r > .fill { flex: 0 1 auto; min-width: 0; } .ac .r .grow { flex: 1; }
  .ac .name { font-family: "JetBrains Mono", monospace; font-size: 15px; font-weight: 600; color: #111418; }
  .ac .meta b { color: #374151; font-weight: 500; }
  .mode { font-size: 12.5px; color: #6b7280; white-space: nowrap; }
  .mode.mine { color: #1c2128; font-weight: 600; display: inline-flex; align-items: center; gap: 3px; }
  .mark { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 11.5px; border: 1px solid #d9a441; color: #7c3d00; }
  .aslot { display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 2px 0 2px 10px; border-left: 2px solid #cbd0d6; font-size: 12px; color: #1c2128; overflow: hidden; }
  .aslot .t { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4; }
  .aslot .cap { font-family: "JetBrains Mono", monospace; font-size: 12px; color: #6b7280; white-space: nowrap; }
  .aslot.need { border-color: #f59e0b; } .aslot.need .t { color: #7c3d00; } .aslot.bad { border-color: #dc2626; } .aslot.lim { border-color: #7c3aed; }
  .aslot.mono .t { font-family: "JetBrains Mono", monospace; font-size: 12.5px; color: #4b5563; }
  /* the quiet foot (second pass, 2026-09-21): the next act outlined at the text's strength, the rest
     plain links at normal strength; dimmed means disabled and nothing else; the only filled button a
     card carries is Allow */
  .ac .foot { display: flex; align-items: center; gap: 10px; min-width: 0; overflow: hidden; }
  .ac .foot .next { white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border: 1px solid #6b7280; border-radius: 4px; color: #1c2128; font-size: 12px; font-weight: 500; }
  .ac .foot .next.fill { background: #b45309; border-color: #b45309; color: #fff; }
  .ac .foot .lk { white-space: nowrap; min-width: 0; overflow: hidden; display: inline-flex; align-items: center; gap: 4px; color: #1c2128; font-size: 12px; }
  .ac .foot svg { width: 13px; height: 13px; }
  .ac.ring { box-shadow: 0 0 0 2px #f59e0b; border-color: #f59e0b; }
  /* the keyboard's ring (TD-124): the browser's focus ring on the card, blue and offset, apart from the amber needs-you one */
  .ac.kring { outline: 2px solid #1f5fa8; outline-offset: 2px; }
  .sc .name { font-family: "JetBrains Mono", monospace; font-size: 16px; font-weight: 600; color: #111418; }
  .meta { font-family: "JetBrains Mono", monospace; font-size: 14px; color: #6b7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sbar { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; }
  .btn.sm { height: 26px; font-size: 12px; padding: 0 8px; }
  .btn.sm svg { width: 12px; height: 12px; }
  .term.tail { height: 100%; box-sizing: border-box; font-size: 12px; line-height: 1.55; padding: 6px 9px; color: #aab3bf; border-radius: 4px; }
  .topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #1c2128; color: #e6e9ee; }
  .wordmark { font-family: "JetBrains Mono", monospace; font-weight: 500; font-size: 15px; letter-spacing: -.01em; }
  .wordmark b { color: #9ec5ff; font-weight: 500; }
  .tab { padding: 6px 10px; border-radius: 4px; color: #aab3bf; font-weight: 500; }
  .tab.on { background: #2b323b; color: #fff; }
  table { border-collapse: collapse; width: 100%; }
  th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #dfe3e8; }
  td { padding: 9px 10px; border-bottom: 1px solid #eceef1; vertical-align: middle; }
  tr.needs td { background: #fffbeb; }
  .grp { display: flex; align-items: center; gap: 10px; padding: 10px 10px 6px; font-weight: 600; font-size: 12px; color: #374151; }
  .grp .path { font-weight: 400; color: #6b7280; }
  .muted { color: #6b7280; }
  .kv { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 4px 10px; font-size: 12px; }
  .kv dt { color: #6b7280; } .kv dd { margin: 0; }
  .term { background: #0f1419; color: #d5dbe3; font-family: "JetBrains Mono", monospace; font-size: 12px; line-height: 1.5; padding: 14px 16px; border-radius: 6px; white-space: pre; overflow: hidden; }
  .term .p { color: #9ec5ff; } .term .q { color: #fde68a; } .term .g { color: #86efac; } .term .d { color: #7d8794; }
  .field { display: flex; flex-direction: column; gap: 5px; }
  .field label { font-size: 12px; font-weight: 600; color: #4b5563; text-transform: uppercase; letter-spacing: .04em; }
  .input { height: 32px; padding: 0 10px; border: 1px solid #cbd0d6; border-radius: 4px; background: #fff; display: flex; align-items: center; justify-content: space-between; font-size: 14px; }
  .radio { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid #dfe3e8; border-radius: 4px; background: #fff; }
  .radio.on { border-color: #1c2128; box-shadow: inset 0 0 0 1px #1c2128; }
  .rb { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid #6b7280; box-sizing: border-box; }
  .radio.on .rb { border: 4.5px solid #1c2128; }
  .note { font-size: 12px; color: #6b7280; }
  .rs { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #374151; }
  .rs > div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .rs-k { display: inline-block; width: 52px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; font-weight: 600; }
  .switch { display: inline-flex; align-items: center; width: 34px; height: 20px; border-radius: 10px; background: #cbd0d6; padding: 2px; box-sizing: border-box; flex-shrink: 0; }
  .switch .knob { width: 16px; height: 16px; border-radius: 50%; background: #fff; }
  .switch.on { background: #1c2128; justify-content: flex-end; }
  /* Inbox (design round 2, 2026-09-20, TD-082): a centred column, sections as plain headings, a row as a card */
  .inboxcol { width: 100%; max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 10px; }
  .isec-h { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 14px 2px 0; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: #6b7280; position: relative; }
  .isec-h .n { font-family: "JetBrains Mono", monospace; letter-spacing: 0; color: #374151; }
  .info { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; border: 1px solid #cbd0d6; background: #fff; color: #4b5563; font-size: 11px; font-weight: 600; text-transform: none; font-style: italic; font-family: Georgia, serif; }
  .info.on { background: #1c2128; color: #fff; border-color: #1c2128; }
  .pop { flex: 0 0 100%; box-sizing: border-box; padding: 8px 12px; border-left: 2px solid #1c2128; background: #fff; color: #374151; border-radius: 0 4px 4px 0; font-size: 12px; font-weight: 400; text-transform: none; letter-spacing: 0; line-height: 1.5; }
  .mcard { position: relative; overflow: hidden; display: flex; flex-direction: column; gap: 8px; padding: 12px 14px 12px 17px; background: #fff; border: 1px solid #dfe3e8; border-radius: 6px; }
  .mcard.hover { border-color: #9aa3b0; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .mcard.focus { outline: 2px solid #1f5fa8; outline-offset: 1px; }
  .mcard .who { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .mcard .who .nm { font-family: "JetBrains Mono", monospace; font-weight: 600; color: #111418; }
  .mcard .txt { font-size: 14px; line-height: 1.5; color: #1c2128; }
  .mcard .ctl { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .kind { font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: #6b7280; }
  .sugg { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 6px 8px; border: 1px dashed #cbd0d6; border-radius: 4px; }
  .sugg .lbl { font-size: 12px; color: #6b7280; }
  .quoted { padding: 6px 10px; border-left: 2px solid #cbd0d6; color: #4b5563; font-size: 14px; }
  .warn { display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; background: #fff7ed; border: 1px solid #fdba74; border-radius: 4px; color: #7c2d12; font-size: 12px; }
</style>
"""

ICON = {
    "focus": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1.5"></rect><path d="M5 7l2 1.5L5 10"></path><path d="M8.5 10.5H11"></path></svg>',
    "code": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 4L2 8l4 4"></path><path d="M10 4l4 4-4 4"></path></svg>',
    "kill": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 4l8 8M12 4l-8 8"></path></svg>',
    "more": '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="3.5" cy="8" r="1.3"></circle><circle cx="8" cy="8" r="1.3"></circle><circle cx="12.5" cy="8" r="1.3"></circle></svg>',
    "plus": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M8 3v10M3 8h10"></path></svg>',
    "clip": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10.5 5.5L6 10a1.6 1.6 0 002.3 2.3l5-5a3 3 0 00-4.3-4.3L3.7 8.3a4.2 4.2 0 006 6l3.5-3.5"></path></svg>',
    "send": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 8h10M8.5 3.5L13 8l-4.5 4.5"></path></svg>',
    "warn": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2.5l6 11H2z"></path><path d="M8 7v3M8 12v.5"></path></svg>',
    "resume": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8a5 5 0 019-3M13 8a5 5 0 01-9 3"></path><path d="M12 2v3H9M4 14v-3h3"></path></svg>',
    "play": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M5 3.5v9l7-4.5z"></path></svg>',
    "term": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1.5"></rect><path d="M5 7l2 1.5L5 10M8.5 10.5H11"></path></svg>',
    "git": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="4" cy="4" r="1.6"></circle><circle cx="4" cy="12" r="1.6"></circle><circle cx="12" cy="6" r="1.6"></circle><path d="M4 5.6v4.8M12 7.6c0 2.4-8 1.2-8 3"></path></svg>',
}

# design §4.8 *Role presets* (TD-074): the role badge's picture. The `d` of each path is copied
# from `src/agentorc/ui/icons.py`, which is where the page's eight live — a mockup that drew its
# own would be showing something the page does not. Monochrome and unfilled, so the state pill
# stays the one coloured thing on a card.
ROLE_ICON = {
    "lead": "M5 21V4M5 4h11l-2 4 2 4H5",
    "manager": "M5 21V4M5 4h11l-2 4 2 4H5",
    "grinder": "M15 3a5 5 0 0 0-4.6 7L3 17.4 6.6 21l7.4-7.4A5 5 0 1 0 15 3z",
    "hunter": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16 16l5 5",
}

# `person` is reserved for the card's *interactive* mark (§4.5 *The card's anatomy*, second pass):
# no role may name it, so a card never shows the same glyph twice for two reasons.
PERSON = "M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21a8 8 0 0 1 16 0"

# a role's label is a title (TD-095 g): the preset's `label:`, else its name with the first letter raised
ROLE_LABEL = {"techlead": "Tech Lead"}

def role_icon(role):
    d = ROLE_ICON.get(role)
    if not d:
        return ""  # a role with no icon draws its word alone, as the page does
    return (f'<svg class="ricon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{d}"></path></svg>')

# the card's left bar, the state token's strong colour: working green, idle blue, and one grey for
# everything over or out of reach (§4.5 *The card's anatomy*, TD-095)
BAR = {'needs': '#f59e0b', 'limited': '#7c3aed', 'stalled': '#dc2626', 'working': '#16a34a', 'idle': '#2563eb', 'exited': '#9ca3af', 'done': '#9ca3af', 'unreachable': '#9ca3af'}
# §4.5 *One order*: … working → unseen idle → idle / unreachable on a volatile host → exited → closed
RANK = {"needs": 0, "limited": 1, "stalled": 2, "working": 3, "unseen": 3.5, "idle": 4, "exited": 5, "done": 6}
VOLATILE = {"laptop"}
def rank(host, state):
    if state == "unreachable":
        return 4 if host in VOLATILE else 2.5
    return RANK[state]

def pill(state, label=None, scraped=False, unseen=False):
    names = {"oncall": "on call", "working": "working", "needs": "needs you", "idle": "idle", "stalled": "stalled?", "exited": "exited", "done": "closed", "limited": "limited", "unreachable": "unreachable"}
    cls = {"done": "closed"}.get(state, state)  # a closed session is grey; s-done is Focus's green tick
    return f'<span class="pill s-{cls}{" unseen" if unseen else ""}{" scraped" if scraped else ""}"><span class="dot"></span>{label or names[state]}</span>'

def head(title):
    return f'<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>{CSS}</helmet>\n'

TAIL = "</x-dc>\n</body>\n</html>\n"

def topbar(active="Org", narrow=False):
    # a tab exists only for a built page (TD-123); an unbuilt screen's own mockup draws its tab, active
    inbox = active if active.startswith("Inbox") else "Inbox 4 · 2"
    live = ["Org", inbox] + ([] if active in ("Org", inbox) else [active])
    tabs = "".join(f'<span class="tab{" on" if t == active or (t == inbox and active.startswith("Inbox")) else ""}">{t}</span>' for t in live)
    return f'''<div class="topbar">
  <span class="wordmark">Shift<b>Lead</b></span>
  <div style="display: flex; gap: 2px;">{tabs}</div>
  <div style="flex-grow: 1;"></div>
  {"" if narrow else '<span class="mono" style="font-size: 12px; color: #aab3bf;">grind · week 58% · paul · 5h 41%</span><span class="mono" style="font-size: 12px; color: #aab3bf;">hosts: kmaster ● vps ● host1 ● vpnmaster ● laptop ◐</span>'}
  <span class="btn" style="height: 26px; background: transparent; color: #e6e9ee; border-color: #4b5563;">{ICON["term"]}Shell</span><span class="btn primary" style="height: 26px;">{ICON["plus"]}New session</span>
</div>'''

# ---------------- sessions data (realistic, from Paul's setup) ----------------
SESS = [
    ("kmaster", "samscrape", "/home/kmaster/samscrape", [
        ("main", "claude-code · paul (max) · opus", "needs", "2m", "main", "", "hook", "Permission: Bash · git push origin td301-fix", ""),
        ("orc-1", "claude-code · grind · opus", "idle", "4m", "wt/orc-1", "", "hook", "next tick in 1m", "unattended"),
        ("tdgrind-1", "claude-code · grind · sonnet", "working", "14s", "wt/tdgrind-1 → td-301", "", "hook", "", "unattended"),
        ("tdgrind-2", "claude-code · grind · sonnet", "stalled", "47m", "wt/tdgrind-2 → td-296", "3 unpushed", "hook", "no output 47m · creds expire in 0.2h", "unattended"),
        ("tdgrind-3", "claude-code · grind · sonnet", "limited", "9m", "wt/tdgrind-3 → td-290", "", "hook", "5h window at 100% · resets 02:00 MDT (1h 51m)", "unattended"),
        ("tdgrind-4", "claude-code · grind · sonnet", "idle", "1h 12m", "wt/tdgrind-4 → td-299-summaries-fallback", "", "hook", "", "unattended"),
        ("techlead-1", "claude-code · grind · opus", "exited", "2h", "wt/techlead-1", "", "hook", "", "unattended"),
        ("errors-alerts", "claude-code · paul (max) · opus", "idle", "3h", "wt/errors-alerts", "dirty · 2 unpushed", "hook", "", ""),
    ]),
    ("kmaster", "contractmatch", "/home/kmaster/contractmatch", [
        ("main", "claude-code · paul (max) · opus", "idle", "22m", "main", "", "hook", "ready to close ✓ · tree clean, nothing open", ""),
    ]),
    ("host1", "", "~/proxmox", [
        ("pve", "shell", "working", "3m", "~/proxmox", "", "scraped", "$ qm list\n VMID NAME     STATUS\n 100  kmaster  running\n▌", ""),
    ]),
    ("vpnmaster", "", "/etc/wireguard", [
        ("wg", "shell", "idle", "1h", "/etc/wireguard", "", "scraped", "", ""),
    ]),
    ("laptop", "notes", "~/notes", [
        ("journal", "claude-code · paul (max) · sonnet", "unreachable", "40m", "main", "", "hook", "laptop asleep since 14:02 · last state: idle", ""),
    ]),
    ("vps", "dev-cadence", "/home/paul/dev-cadence", [
        ("attention-fix", "gemini-cli · paul · 2.5-pro", "working", "1m", "wt/attention-fix", "", "hook", "", ""),
        ("td-7", "claude-code · paul (max) · opus", "exited", "2d", "wt/td-7 → td-7-hook-fetch", "PR #12 open", "hook", "not done: PR not merged", ""),
        ("td-5", "claude-code · paul (max) · opus", "done", "20h", "wt/td-5 (reaped)", "", "hook", "closed by you · PR #11 merged · filed under Resumable in 4h", ""),
    ]),
]

def row(s):
    name, tool, state, age, where, flag, conf, pending, tag = s
    flag_html = f'<span class="flag">{ICON["warn"]}{flag}</span>' if flag else ""
    tag_html = f'<span class="badge toggle on" title="click: switch to interactive">{tag}</span>' if tag else ""
    pend = f'<span class="mono" style="font-size: 12px;">{pending}</span>' if pending else '<span class="muted">—</span>'
    return f'''<tr class="{"needs" if state == "needs" else ""}">
  <td><div style="display: flex; align-items: center; gap: 8px;"><a href="#" class="mono" style="font-weight: 500;">{name}</a>{tag_html}</div></td>
  <td>{pill(state)}</td>
  <td><span class="badge{" scraped" if conf == "scraped" else ""}">{conf}</span></td>
  <td class="muted">{age}</td>
  <td><span class="mono" style="font-size: 12px; color: #4b5563;">{where}</span></td>
  <td>{flag_html}</td>
  <td>{pend}</td>
  <td><div style="display: flex; gap: 4px; justify-content: flex-end;"><span class="btn">{ICON["focus"]}Focus</span><span class="btn">{ICON["code"]}VS Code</span><span class="btn">{ICON["more"]}</span></div></td>
</tr>'''

DUE = [
    ("s-stalled", "14d overdue", "samscrape", "Get the TD-259 keep-half snapshot off this array — it is the only copy.", "tdgrind-1"),
    ("s-stalled", "7d overdue", "samscrape", "Decide TD-196 per tool: delete or port.", "tdgrind-2"),
    ("s-stalled", "2d overdue", "samscrape", "Decide whether to turn the signup → CM board writer ON.", "2fb13646"),
    ("s-stalled", "2d overdue", "dev-cadence", "Add --fetch to the SessionStart hook line in the consumer settings.json files.", "3168de4c"),
    ("s-needs", "due today", "samscrape", "Deploy TD-296, then un-flag and republish the two DARPA notices.", "tdgrind-2"),
    ("s-needs", "due today", "samscrape", "Deploy TD-036 step 2 (merged after the 05:00 UTC rollout, so not live).", "tdgrind-1"),
]

def due_strip(compact=False):
    rows = ""
    for cls, due, repo, text, sess in DUE:
        if compact:
            rows += f'<div class="due" style="padding: 8px 10px; flex-wrap: wrap;"><span class="pill plain {cls}">{due}</span><span style="font-size: 12px; flex-basis: 100%;">{text}</span><span class="meta">{repo} · {sess}</span><span style="flex-grow: 1;"></span><span class="btn sm">Snooze ▾</span><span class="btn sm">Done</span></div>'
        else:
            rows += f'<div class="due"><span class="pill plain {cls}" style="width: 78px; justify-content: center;">{due}</span><span style="font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{text}</span><span class="meta" style="flex-shrink: 0;">{repo} · session <a href="#">{sess}</a></span><span style="flex-grow: 1;"></span><span class="btn sm ghost">Snooze ▾</span><span class="btn sm ghost">Done</span></div>'
    return f'''<div class="card" style="display: flex; flex-direction: column;">
    <div class="due" style="padding: 8px 10px; border-bottom: 1px solid #dfe3e8;"><span style="font-weight: 600;">Due</span><span class="muted">4 overdue · 2 today · from the dev-cadence boards</span><span style="flex-grow: 1;"></span><a href="#" style="font-size: 12px;">full board →</a><span class="btn sm ghost" style="padding: 0 4px;">▾</span></div>
    {rows}
  </div>'''

# What §4.9 added to a session and §4.8 to its record: the team badge, the controllers edge, and the
# two report channels. Keyed by session name; a session with no entry is on no team, under nobody,
# and reporting nothing — which is every session a person starts for themselves.
# `doing` is what the session **says** it is doing (§4.8, TD-074) with the age the card prints
# beside it, and `title` is the name its **tool** holds — Claude Code's own, often the person's
# (*Error Checker*), shown beside agentorc's name and never a control. Both are display only.
EXTRA = {
    "orc-1":     {"team": "samscrape-grind", "role": "manager", "report": "round 41 · 2 wrapped up", "grants": "control",
                  "doing": ("round 41: reading four members, two claims to re-check", "2m")},
    "tdgrind-1": {"team": "samscrape-grind", "role": "grinder", "under": "orc-1", "report": "TD-301 → #811 · 1/3 done", "findings": "2 filed",
                  "doing": ("TD-301: pushing the branch for review", "14s"), "title": "DIU fetcher", "stops": "stops 06:00"},
    "tdgrind-2": {"team": "samscrape-grind", "role": "grinder", "under": "orc-1", "report": "TD-296 → #437 · 2/2 done", "derived": True,
                  "doing": ("TD-296: waiting on CI for #437", "39m"), "kring": True},
    # no `doing` on tdgrind-3 on purpose: it is `limited`, and the slot shows the cap — what needs
    # a person comes first (§4.5a), so a line here would be data no branch draws (review of PR #288)
    "tdgrind-3": {"team": "samscrape-grind", "role": "grinder", "under": "orc-1", "report": "TD-290 · 0/2 done", "findings": "1 filed", "mail": 2},
    # declared itself out of work: plain `idle` — *unseen* is drawn only on an interactive session
    # (§4.2, TD-095 f), its manager read the result; the ending is said once, in the slot, and
    # *ready to close ✓* is its caption
    "tdgrind-4": {"team": "samscrape-grind", "role": "grinder", "under": "orc-1", "report": "#809 · 2/2 done",
                  "ready": True, "ending": "out of work — nothing open on the ledger that my brief lets me take"},
    # the team's seat (§4.9b), ended between questions as designed: `exited` in every payload, drawn
    # *on call* (◇) with what would make it come, and Message… first (§4.5, TD-097)
    "techlead-1": {"team": "samscrape-grind", "role": "techlead", "under": "orc-1", "report": "3 answered", "seat": True},
    "main":      {"findings": "1 filed"},
    # the person's own, finished while nobody was looking: *idle · unseen* (§4.2, interactive only)
    "errors-alerts": {"title": "Error Checker", "unseen": True},
}

# §4.9 team definitions: every team in ~/.agentorc/org.yml and in the repos' .agentorc.yml, its
# source, its manager, and how many of its members are live right now. A team with nothing live
# keeps its card (§4.5a **team groups**, 2026-09-18 — the Teams strip that listed them is retired):
# below the live teams and *No team*, its dead cards folded, Start on its header.
TEAMS = [
    ("samscrape-grind", "org.yml", "orc-1", 4, "samscrape"),
    ("ao-grind", "org.yml", "manager-ao-1", 0, "agentorc"),
    ("cadence-sweep", "dev-cadence/.agentorc.yml", "sweeper", 0, "dev-cadence"),
]
# what a stopped team's header says: its folded sessions and *wound down <t> ago* when every one of
# them declared out of work (§4.9a), else *stopped*; a definition no session carries is the same
# card, empty, headed by what the definition says
STOPPED = {
    "ao-grind": {"folded": 4, "wound": "6h", "place": "kmaster / agentorc"},
    "cadence-sweep": {"def": "Manager sweeper · 1 member · dev-cadence"},
}

def doing_of(name):
    return EXTRA.get(name, {}).get("doing")

def where_row(host, repo, name, where, in_team):
    """row 3 of the card's anatomy (§4.5): `branch <name>`, `wt/<name> ·` only when the worktree
    is not the session's own name, a directory for a session with no repo, and — outside a team's
    own group — `host / repo ·` in front of all of it."""
    wt, branch, extra = None, None, ""
    if where.startswith("wt/"):
        rest = where[3:]
        if rest.endswith(" (reaped)"):
            rest, extra = rest[: -len(" (reaped)")], " · worktree reaped"
        wt, _, branch = rest.partition(" → ")
        branch = branch or wt
    elif where.startswith(("~", "/")):
        branch = None
    else:
        branch = where
    parts = []
    if not in_team:
        parts.append(f"<b>{host}</b> / {repo}" if repo else f"<b>{host}</b> / {where}")
    if wt and wt != name:
        parts.append(f"wt/{wt}")
    if branch:
        parts.append(f"branch <b>{branch}</b>{extra}")
    elif in_team or repo:
        parts.append(where)
    e = EXTRA.get(name, {})
    if e.get("under") and not in_team:
        parts.append(f"under <b>{e['under']}</b>")
    return " · ".join(parts)

def team_desktop():
    def card(host, repo, s, in_team):
        name, tool, state, age, where, flag, conf, pending, tag = s
        e = EXTRA.get(name, {})
        unseen = state == "idle" and e.get("unseen")
        seat = state in ("exited", "done") and e.get("seat")
        ready = e.get("ready") or pending.startswith("ready")
        # (1) name and state: the tool's title only when it differs from the name; the pill says the
        # state and nothing else — a declaration is not a state, so *out of work* is `idle`
        title = e.get("title")
        title_html = (f'<span class="meta fill" title="the session\'s name as its tool holds it">{title}</span>'
                      if title and title != name else "")
        state_pill = (pill("idle", "idle · unseen", unseen=True) if unseen else pill("oncall") if seat
                      else pill(state, scraped=(conf == "scraped")))
        # (2) what it is: role, mode (a word, never pressable), marks, the stops note, the one clock
        role = (f'<span class="badge" title="the role preset it was started under (design §4.8)">'
                f'{role_icon(e["role"])}{ROLE_LABEL.get(e["role"], e["role"].capitalize())}</span>') if e.get("role") else ""
        if tag == "unattended":
            mode = '<span class="mode">unattended</span>'
        else:
            mode = (f'<span class="mode mine" title="interactive: yours — never paused, nudged or killed by a policy">'
                    f'<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" '
                    f'stroke-linecap="round" aria-hidden="true"><path d="{PERSON}"></path></svg>interactive</span>')
        marks = f'<span class="mark" title="unread mail">✉ {e["mail"]}</span>' if e.get("mail") else ""
        # the team badge is what the group says: drawn only outside the team's own group
        team = f'<span class="badge" title="click: filter the grid to this team">{e["team"]}</span>' if e.get("team") and not in_team else ""
        stops = f'<span class="mode">· {e["stops"]}</span>' if e.get("stops") else ""
        # (3) where, at full width, with the dirty / unpushed flag at the right
        flag_html = f'<span class="flag">{ICON["warn"]}{flag}</span>' if flag else ""
        # (4) what runs it, and what it reports — a reference shown once
        rep = e.get("report", "")
        dashed = ' style="border-bottom: 1px dashed #d9a441;"' if e.get("derived") else ""  # derived, not declared
        rep_html = f'<span class="meta"{dashed}>{rep}</span>' if rep else ""
        found = f'<span class="meta">{e["findings"]}</span>' if e.get("findings") else ""
        # (5) the slot: one text, the first that applies, and a caption
        cls, text, cap = "", "", ""
        if state == "needs":
            cls, text, cap = "need", pending, "via hook · 9m 12s left"
        elif state == "limited":
            cls, text = "lim", pending
        elif state == "stalled":
            cls, text = "bad", pending
        elif state == "unreachable":
            text = pending
        elif seat:
            text, cap = "on call — comes on the next question", f"last came · {age} ago"
        elif state == "exited":
            cls, text = "end", "exited · code 0"
        elif state == "done":
            cls, text = "end", pending
        elif e.get("ending"):
            cls, text = "end", e["ending"]
        elif doing_of(name):
            text, age_said = doing_of(name)
            cap = f"says · {age_said} ago"
        elif tool == "shell":
            cls, text = "mono", ("$ wg show wg0 · at prompt" if state == "idle" else pending.splitlines()[-2].strip())
        elif ready:
            cls, text = "mono", "last: ⏺ Bash(git status) — nothing to commit"
        else:
            cls, text = "mono", "last: ⏺ Edit(scripts/recover_stuck_notices.py)"
        if ready and state == "idle":
            cap = "ready to close ✓"
        # (6) the foot: its first button is the next act, by state
        focus = f'<span class="lk">{ICON["focus"]}Focus</span>'
        details = '<span class="lk">Details</span>'
        if state == "needs":
            first = '<span class="next fill">Allow</span><span class="lk">Deny</span>'
            rest = focus
        elif state == "limited":
            first, rest = '<span class="next">Switch profile…</span><span class="lk">Wait</span>', focus
        elif seat:
            first, rest = '<span class="next">Message…</span>', details  # asking it is how it comes
        elif state == "exited":
            first, rest = '<span class="next">Forget</span>', details
        elif state == "done":
            first, rest = '<span class="next">Details</span>', ""
        elif ready and state == "idle":
            first, rest = '<span class="next">Close session</span>', focus
        else:
            first, rest = f'<span class="next">{ICON["focus"]}Focus</span>', ""
        editor = f'<span class="lk">{ICON["code"]}VS Code</span>' if state != "done" else ""
        ring = (" ring" if state == "needs" else "") + (" kring" if e.get("kring") else "")
        off = " off" if state == "unreachable" else ""
        bar = BAR["idle"] if unseen else BAR[state]
        return f'''<div class="card ac{ring}{off}">
  <div class="sbar" style="background: {bar};"></div>
  <div class="r"><span class="name">{name}</span>{title_html}<span class="grow"></span>{state_pill}</div>
  <div class="r">{team}{role}{mode}{stops}{marks}<span class="grow"></span><span class="meta" title="in this state since">{age}</span></div>
  <div class="r"><span class="meta fill">{where_row(host, repo, name, where, in_team)}</span><span class="grow"></span>{flag_html}</div>
  <div class="r"><span class="meta fill">{tool}</span><span class="grow"></span>{rep_html}{found}</div>
  <div class="aslot {cls}"><div class="t">{text}</div>{f'<div class="cap">{cap}</div>' if cap else ""}</div>
  <div class="foot">{first}{rest}{editor}<span class="grow"></span><span class="lk" style="padding: 0 2px; flex-shrink: 0; overflow: visible;">{ICON["more"]}</span></div>
</div>'''

    def key(t):
        host, _, s = t
        e = EXTRA.get(s[0], {})
        r = RANK["unseen"] if s[2] == "idle" and e.get("unseen") else rank(host, s[2])
        # §4.5 *One order*, second pass: (rank, interactive first, name)
        return (r, s[8] == "unattended", s[0])

    ordered = []
    for host, repo, path, rows in SESS:
        for r in rows:
            ordered.append((host, repo, r))
    ordered.sort(key=key)

    # design §4.5a Org **team groups** (§4.9), redrawn to §4.5 *The card's anatomy* (TD-095): the
    # header carries the team, the host / repo its sessions share, the counts by state, its marks and
    # its controls — not its manager, whose card is the first in the group. *No team* is headed by
    # its count and nothing else.
    GRID = "display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: start;"
    WORDS = {"needs": "needs you", "stalled": "stalled?", "done": "closed"}

    def group(title, sub, cards_html, needs=0, team=False):
        flag = pill("needs", f"{needs} needs you") if needs else ""
        acts = ('<span style="flex-grow: 1;"></span><span class="btn sm">Wind down</span><span class="btn sm danger">Stop now</span>'
                if team else "")
        box = "border: 1px solid #cbd0d6; border-radius: 8px; padding: 12px 14px 14px; background: #eceef1;" if team else ""
        return (f'<div style="display: flex; flex-direction: column; gap: 12px; {box}">'
                f'<div style="display: flex; align-items: center; gap: 10px;">'
                f'<span style="font-weight: 600; font-size: 15px;">{title}</span>'
                f'<span class="meta">{sub}</span>{flag}{acts}</div>'
                f'<div style="{GRID}">{cards_html}</div></div>')

    grid = ""
    for team, source, lead, live, repo in TEAMS:
        members = [t for t in ordered if EXTRA.get(t[2][0], {}).get("team") == team]
        if not members:
            continue
        members.sort(key=key)
        counted = list(members)
        members.sort(key=lambda t: t[2][0] != lead)  # stable: the manager's card first, then urgency
        needs = sum(1 for t in members if t[2][2] == "needs")
        places = {f"{h} / {r}" for h, r, _ in members}
        place = places.pop() if len(places) == 1 else "mixed"
        counts = {}  # by state, in the grid's own order (members are sorted already); needs you is the pill
        for h, r, x in counted:
            ex = EXTRA.get(x[0], {})
            w = ("unseen" if x[2] == "idle" and ex.get("unseen") else "on call" if x[2] == "exited" and ex.get("seat")
                 else WORDS.get(x[2], x[2]))
            if x[2] != "needs":
                counts[w] = counts.get(w, 0) + 1
        tally = " · ".join(f"{n} {w}" for w, n in counts.items())
        grid += group(team, f"{place} · {tally}", "".join(card(h, r, x, True) for h, r, x in members), needs, team=True)
    rest = [t for t in ordered if not EXTRA.get(t[2][0], {}).get("team")]
    grid += group("No team", f"{len(rest)} sessions", "".join(card(h, r, x, False) for h, r, x in rest))
    for team, source, lead, live, repo in TEAMS:
        if live:
            continue
        st = STOPPED.get(team, {})
        sub = st.get("place") or st.get("def", "")
        fold = (f'<span class="btn sm ghost" title="a stopped team\'s cards are folded away">{st["folded"]} sessions — show</span>'
                if st.get("folded") else "")
        when = f'wound down {st["wound"]} ago' if st.get("wound") else "stopped"
        grid += (f'<div style="display: flex; align-items: center; gap: 10px; border: 1px solid #cbd0d6; border-radius: 8px; '
                 f'padding: 10px 14px; background: #eceef1;" title="defined in {source}">'
                 f'<span style="font-weight: 600; font-size: 15px;">{team}</span><span class="meta">{sub}</span>{fold}'
                 f'<span style="flex-grow: 1;"></span><span class="meta">{when}</span><span class="btn sm primary">Start</span></div>')
    cards = grid
    # TD-071 (6): the note's sort order is the glyphs a person scans for, not words about them
    ORDER_PILLS = " → ".join([pill("needs"), pill("limited"), pill("stalled"), pill("unreachable", "unreachable (non-volatile)"),
                              pill("working"), pill("idle", "idle · unseen", unseen=True), pill("idle"), pill("exited"),
                              pill("done")])  # an on-call seat sorts as the `exited` / `closed` it is (TD-097)
    return head("Org") + f'''<div style="width: 1440px; min-height: 1560px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  <div style="display: flex; align-items: center; gap: 10px;">
    <span style="font-size: 16px; font-weight: 600;">Org</span>
    <span class="muted">15 sessions · 1 team · </span>{pill("needs", "1 needs you")}{pill("limited", "1 limited")}{pill("stalled", "1 stalled")}
    <span style="flex-grow: 1;"></span>
    <span class="input" style="width: 200px; height: 28px; color: #9ca3af;">filter…</span>
    <span class="btn ghost">host: all ▾</span><span class="btn ghost">repo: all ▾</span><span class="btn ghost">profile: all ▾</span><span class="btn ghost" style="color: #9ca3af;">☐ show command runs (2)</span>
  </div>
  {due_strip()}
  <div class="warn" style="background: #f3f4f6; border-color: #cbd0d6; color: #374151; align-items: center;">{ICON["warn"]}<span><b>laptop</b> unreachable since 14:02 (volatile host, probably asleep) · 1 session · last states kept</span><span style="flex-grow: 1;"></span><span class="btn sm ghost">Retry</span></div>
  <div style="display: flex; flex-direction: column; gap: 22px;">{cards}</div>
  <div class="note"><b>The card's anatomy</b> (design §4.5, TD-095): six rows, the same six on every card, at one height — (1) name, the tool's title only where it differs, the state pill; (2) role, mode (<i>unattended</i> quiet, <b>interactive</b> with the person mark — the person's own stand out), marks, the stops note, and the one clock: how long in this state; (3) where — <span class="mono">branch</span>, <span class="mono">wt/</span> only when the worktree is not the session's name, and outside a team's own group <span class="mono">host / repo</span> in front; (4) tool · account · model, and the report with a reference shown once; (5) the slot, one text and a caption — what needs a person, an ending, what it says it is doing, the last line — with <i>ready to close ✓</i> as the caption; (6) the foot, whose first button is the next act by state, outlined; the rest plain; only <b>Allow</b> is filled. <b>Colour</b>: working green, idle blue (idle · unseen too), everything over or out of reach one grey; amber <i>needs you</i> (ringed) and red <i>stalled?</i> stay the loudest. <b>Order</b>, by the pills' own glyphs: the manager's card first, then {ORDER_PILLS}, and within one urgency an interactive session ahead of an unattended one. A team's header carries its place, its counts by state and <b>Wind down</b> / <b>Stop now</b> — not its manager, whose card is first. A dashed outline on a state pill means the state was guessed from the screen. Command runs are on the Commands tab. <b>Keys</b> (§4.5a <b>keys</b>, TD-124): <span class="mono">j</span> / <span class="mono">k</span> move the keyboard's ring — the blue focus ring on <span class="mono">tdgrind-2</span>, apart from the amber <i>needs you</i> one — through the cards in this order; <span class="mono">g</span> then a team's initial or a group's number jumps; <span class="mono">Enter</span> opens the ringed card's Focus, <span class="mono">a</span> / <span class="mono">d</span> answer its permission; <span class="mono">?</span> lists every key.</div>
</div>
</div>
''' + TAIL

def team_phone():
    def card(host, repo, s):
        name, tool, state, age, where, flag, conf, pending, tag = s
        if state == "needs":
            pend = f'<div class="status" style="border-color: #f59e0b; color: #7c3d00; white-space: normal; margin-top: 8px;">{pending}</div>'
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn primary" style="height: 44px; flex-grow: 1; justify-content: center;">Allow</span><span class="btn" style="height: 44px; flex-grow: 1; justify-content: center;">Deny</span><span class="btn" style="height: 44px; width: 44px; justify-content: center;">{ICON["focus"]}</span></div><div class="meta" style="margin-top: 6px;">answered through the hook · 9m 12s before the terminal dialog takes over</div>'
        else:
            cls = {"limited": "lim", "done": "ok", "unreachable": ""}.get(state, "bad" if (state in ("stalled",) or pending.startswith("not done")) else "")
            pend = f'<div class="status {cls}" style="white-space: normal; margin-top: 8px;">{pending}</div>' if pending else ""
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn ghost" style="height: 44px; flex-grow: 1; justify-content: center; border-color: #dfe3e8;">{ICON["focus"]}Focus</span></div>'
        seat = state == "exited" and EXTRA.get(name, {}).get("seat")
        if seat:  # a seat with nobody in it (§4.5, TD-097): what would make it come, and Message… first
            pend = '<div class="status" style="white-space: normal; margin-top: 8px;">on call — comes on the next question</div>'
            actions = '<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn ghost" style="height: 44px; flex-grow: 1; justify-content: center; border-color: #dfe3e8;">Message…</span></div>'
        flag_html = f'<div class="flag" style="margin-top: 6px;">{ICON["warn"]}{flag}</div>' if flag else ""
        place = f"{host} / {repo} · {where}" if repo else f"{host} / {where}"
        return f'''<div class="card{" off" if state == "unreachable" else ""}" style="padding: 12px 12px 12px 14px; position: relative; overflow: hidden;">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div style="display: flex; align-items: center; gap: 8px;"><span class="name" style="font-family: 'JetBrains Mono', monospace; font-size: 15px; font-weight: 600;">{name}</span><span style="flex-grow: 1;"></span>{pill("oncall") if seat else pill(state, scraped=(conf == "scraped"))}</div>
  <div class="meta" style="margin-top: 4px; color: #374151;">{place}</div>
  <div class="meta" style="margin-top: 2px;">{tool} · {age}</div>
  {flag_html}{pend}{actions}
</div>'''
    ordered = []
    for host, repo, path, rows in SESS:
        for r in rows:
            ordered.append((host, repo, r))
    ordered.sort(key=lambda t: rank(t[0], t[2][2]))
    cards = ""
    for host, repo, r in ordered:
        cards += card(host, repo, r)
    return head("Phone") + f'''<div style="width: 390px; min-height: 1560px; background: #f4f5f7; display: flex; flex-direction: column;">
<div class="topbar" style="padding: 0 14px; gap: 10px; height: 52px;"><span class="wordmark">Shift<b>Lead</b></span><span style="flex-grow: 1;"></span><span class="mono" style="font-size: 12px; color: #aab3bf;">5h 41%</span><span class="btn primary" style="height: 32px; width: 32px; padding: 0; justify-content: center;">{ICON["plus"]}</span></div>
<div style="padding: 12px 12px 20px; display: flex; flex-direction: column; gap: 10px;">
  <div style="display: flex; gap: 6px; overflow: hidden;"><span class="btn" style="height: 32px;">needs you 1</span><span class="btn" style="height: 32px;">due 6</span><span class="btn" style="height: 32px;">all 12</span></div>
  {due_strip(compact=True)}
  {cards}
</div>
</div>
''' + TAIL

def focus():
    term = '''<span class="d">● tdgrind-1 · claude-code · /home/kmaster/samscrape/.claude/worktrees/tdgrind-1</span>

<span class="p">&gt;</span> Pick up TD-301 per the brief; branch td301-fix.

<span class="d">⏺</span> Read(docs/technical_debt.md)
<span class="d">⏺</span> Bash(pdm run test tests/test_scripts/test_recover_stuck_notices.py)
  <span class="g">412 passed in 38.2s</span>
<span class="d">⏺</span> Edit(scripts/recover_stuck_notices.py)

<span class="d">⏺</span> Bash(git push -u origin td301-fix)
<span class="q">  ⏳ waiting for permission (agentorc hook · answer above, or the terminal dialog appears in 9m 12s)</span>
<span class="d">▌</span>'''
    return head("Focus") + f'''<div style="width: 1440px; min-height: 1280px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org")}
<div style="padding: 12px 20px; display: flex; gap: 14px; align-items: flex-start;">
  <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0;">
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
      <a href="#" class="muted">← Org</a>
      <span class="mono" style="font-size: 15px; font-weight: 500;">kmaster / samscrape / tdgrind-1</span>
      {pill("needs")}<span class="badge toggle on" title="click: switch to interactive">unattended</span>
      <span class="meta" title="the session\'s name as its tool holds it — set in the tool, not in agentorc">DIU fetcher</span>
      <span class="badge">samscrape-grind</span><span class="badge">{role_icon("grinder")}grinder</span>
      <span class="badge" title="capabilities: click to grant or revoke (design §4.8)">grants: none</span>
      <span class="badge" title="the sessions that may act on this one; + adds one">under orc-1 ×  +</span>
      <span class="meta doing" title="what this session says it is doing (design §4.8): its own words">TD-301: pushing the branch for review · says · 14s ago</span>
      <span class="btn sm primary">Allow</span><span class="btn sm">Deny</span><span class="meta">Bash · git push -u origin td301-fix</span>
      <span style="flex-grow: 1;"></span>
      <span class="btn">{ICON["term"]}Open shell here</span><span class="btn">{ICON["code"]}VS Code</span><span class="btn">Wrap up</span><span class="btn danger">{ICON["kill"]}Kill</span>
    </div>
    <div class="term" style="height: 560px;">{term}</div>
    <div class="card" style="padding: 10px; display: flex; flex-direction: column; gap: 8px;">
      <div class="input" style="height: 64px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Compose a prompt… multi-line, paste-friendly. Drop files or paste a screenshot here; the terminal above takes keys directly for menus and questions.</div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <span class="btn">{ICON["clip"]}Attach</span>
        <span class="badge">~/.agentorc/attachments/tdgrind-1/spec.pdf</span><span class="badge">screenshot-1402.png</span>
        <span style="flex-grow: 1;"></span>
        <span class="btn primary">{ICON["send"]}Send</span>
      </div>
    </div>
  </div>
  <div style="width: 320px; display: flex; flex-direction: column; gap: 12px; flex-shrink: 0;">
    <div class="card" style="padding: 12px;">
      <div style="font-weight: 600; margin-bottom: 8px;">Session</div>
      <dl class="kv" style="margin: 0;">
        <dt>profile</dt><dd class="mono" style="font-size: 12px;">claude-code · grind (pro) · sonnet</dd>
        <dt>adapter id</dt><dd class="mono" style="font-size: 12px;">1c8e0b2f…f42a</dd>
        <dt>tmux</dt><dd class="mono">ao-samscrape-tdgrind-1</dd>
        <dt>started</dt><dd>2026-09-04 20:02 MDT · 3h 14m</dd>
        <dt>last output</dt><dd>14 s ago</dd>
        <dt>policy</dt><dd>window 20:00–06:00 · gate 70/70</dd>
        <dt>run log</dt><dd><a href="#">tdgrind-1-20260904.log</a> · 1.2 MB</dd>
      </dl>
    </div>
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;"><span style="font-weight: 600;">Git</span><span class="mono muted" style="font-size: 12px;">td301-fix · 2 ahead of origin/main</span></div>
      <div class="mono" style="font-size: 12px; line-height: 1.7;">
        <div><span style="color: #065f46;">M</span> scripts/recover_stuck_notices.py</div>
        <div><span style="color: #065f46;">M</span> tests/test_scripts/test_recover_stuck_notices.py</div>
        <div><span style="color: #1f5fa8;">A</span> docs/claude-memory/project_td301.md</div>
      </div>
      <div style="display: flex; gap: 6px; margin-top: 10px;"><span class="btn" style="height: 24px; font-size: 12px;">diff</span><span class="btn" style="height: 24px; font-size: 12px;">log</span><span class="btn" style="height: 24px; font-size: 12px;">PRs</span></div>
    </div>
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; margin-bottom: 8px;"><span style="font-weight: 600;">Reports</span><span style="flex-grow: 1;"></span><span class="meta">1/3 done · 2 filed</span></div>
      <div style="display: flex; flex-direction: column; gap: 6px; font-size: 12px;">
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">TD-301</span><span class="pill plain s-working">claimed</span><a href="#">#811</a><span class="meta">20:04</span><span style="flex-grow: 1;"></span><span class="badge">declared</span><span class="btn sm ghost">Drop</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">TD-299</span><span class="pill plain s-done">done</span><a href="#">#809</a><span class="meta">18:40</span><span style="flex-grow: 1;"></span><span class="badge scraped">derived</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">TD-288</span><span class="pill plain s-ended">dropped</span><span class="meta">held by tdgrind-3</span><span style="flex-grow: 1;"></span><span class="badge">declared</span></div>
        <div style="border-top: 1px solid #eceef1; padding-top: 6px; display: flex; align-items: center; gap: 6px;"><span class="mono">TD-402</span><span class="badge">filed · medium</span><span class="meta">19:12</span><span style="flex-grow: 1;"></span><span class="badge scraped">derived</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">TD-403</span><span class="badge">filed · low</span><span class="meta">19:48</span><span style="flex-grow: 1;"></span><span class="badge">declared</span></div>
      </div>
      <div class="note" style="margin-top: 8px;">Both channels, as the session declared them or the agent derived them from the branch and its PRs (dashed). <b>Drop</b> records the person's decision as a declaration, so the next tick cannot put the claim back.</div>
    </div>
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; margin-bottom: 8px;"><span style="font-weight: 600;">Ready to close</span><span style="flex-grow: 1;"></span><span class="btn sm" style="opacity: .5;">Close</span></div>
      <div style="display: flex; flex-direction: column; gap: 5px; font-size: 12px;">
        <div><span style="color: #065f46;">✓</span> tree clean</div>
        <div><span style="color: #991b1b;">✗</span> branch pushed</div>
        <div><span style="color: #991b1b;">✗</span> PR merged</div>
        <div><span style="color: #065f46;">✓</span> no subagents running</div>
        <div><span style="color: #991b1b;">✗</span> ledger / attention board updated</div>
      </div>
    </div>
  </div>
</div>
</div>
''' + TAIL

def focus_orchestrator():
    """The lead's Focus (§4.8): the **Members** list is drawn here and only here, because the shipped
    page guards it with `is_orchestrator` and §4.5a's row says the same — a member's screen showing
    it would teach exactly the false UI TD-037 exists to remove (found by the PR #127 review)."""
    term = '''<span class="d">● orc-1 · claude-code · /home/kmaster/samscrape/.claude/worktrees/orc-1</span>

<span class="p">&gt;</span> Tick: check every member, nudge what is stalled, wrap up what is done.

<span class="d">⏺</span> Bash(ao status -v --json)
  <span class="g">4 sessions · tdgrind-1 working · tdgrind-2 stalled? · tdgrind-3 limited</span>
<span class="d">⏺</span> Bash(ao send --wait tdgrind-2 "no output for 47m — say where you are")
  <span class="g">idle · answered in 31s</span>
<span class="d">⏺</span> Bash(scripts/check_cadence.py --pr 811)
  <span class="g">review ✓ · squash ✓ · CI ✓ · ledger ✓ · worktree ✓ · pushed ✓</span>
<span class="d">▌</span>'''
    return head("Focus — orchestrator") + f'''<div style="width: 1440px; min-height: 980px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org")}
<div style="padding: 12px 20px; display: flex; gap: 14px; align-items: flex-start;">
  <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0;">
    <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
      <a href="#" class="muted">← Org</a>
      <span class="mono" style="font-size: 15px; font-weight: 500;">kmaster / samscrape / orc-1</span>
      {pill("idle")}<span class="badge toggle on" title="click: switch to interactive">unattended</span>
      <span class="badge">samscrape-grind</span><span class="badge">{role_icon("lead")}lead</span>
      <span class="badge" title="capabilities: click to grant or revoke (design §4.8)">grants: control ×</span>
      <span class="badge" title="the sessions that may act on this one; + adds one">no controller  +</span>
      <span class="meta doing" title="what this session says it is doing (design §4.8): its own words">round 41: reading four members, two claims to re-check · says · 2m ago</span>
      <span style="flex-grow: 1;"></span>
      <span class="btn">{ICON["term"]}Open shell here</span><span class="btn">{ICON["code"]}VS Code</span><span class="btn">Wrap up</span><span class="btn danger">{ICON["kill"]}Kill</span>
    </div>
    <div class="term" style="height: 520px;">{term}</div>
    <div class="card" style="padding: 10px; display: flex; flex-direction: column; gap: 8px;">
      <div class="input" style="height: 56px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Compose a prompt…</div>
      <div style="display: flex; align-items: center; gap: 8px;"><span class="btn">{ICON["clip"]}Attach</span><span style="flex-grow: 1;"></span><span class="btn primary">{ICON["send"]}Send</span></div>
    </div>
  </div>
  <div style="width: 320px; display: flex; flex-direction: column; gap: 12px; flex-shrink: 0;">
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; margin-bottom: 8px;"><span style="font-weight: 600;">Members</span><span style="flex-grow: 1;"></span><span class="meta">3 members · 1 needs you</span></div>
      <div style="display: flex; flex-direction: column; gap: 5px; font-size: 12px;">
        <div style="display: flex; align-items: center; gap: 6px;"><a href="#" class="mono">tdgrind-1</a>{pill("working")}<span class="meta">TD-301 → #811 · 1/3</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><a href="#" class="mono">tdgrind-2</a>{pill("stalled")}<span class="meta">TD-296 → #437 · 2/2</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><a href="#" class="mono">tdgrind-3</a>{pill("limited")}<span class="meta">TD-290 · 0/2</span></div>
      </div>
      <div class="note" style="margin-top: 8px;">Every session whose <span class="mono">controllers</span> name this one, derived from the records on each tick and never cached.</div>
    </div>
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; margin-bottom: 8px;"><span style="font-weight: 600;">Reports</span><span style="flex-grow: 1;"></span><span class="meta">last tick 20:10</span></div>
      <div style="display: flex; flex-direction: column; gap: 6px; font-size: 12px;">
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">#811</span><span class="pill plain s-done">done</span><span class="meta">merged · cadence ✓</span><span style="flex-grow: 1;"></span><span class="badge">declared</span></div>
        <div style="display: flex; align-items: center; gap: 6px;"><span class="mono">TD-404</span><span class="badge">filed · medium</span><span class="meta">tdgrind-2 stood down</span><span style="flex-grow: 1;"></span><span class="badge">declared</span></div>
      </div>
    </div>
    <div class="note">A lead's own channels read like anyone's: what it claimed, what it filed. Its members are the panel above, derived from their <span class="mono">controllers</span> on each tick and never cached (§4.8). Nothing keys on the <span class="mono">lead</span> role — the panel is there because the session holds the <span class="mono">control</span> grant (§9 invariant 9).</div>
  </div>
</div>
</div>
''' + TAIL

def new_session():
    """The shipped form (`src/agentorc/ui/templates/new.html`), field for field. It used to draw a
    four-way **Where** radio group with an *existing worktree* picker and a separate Fresh/Resume
    pair; the picker was superseded on 2026-09-06 and §4.5a never carried it (TD-037). The Grants
    checkboxes are the one §4.5a row still unbuilt, so they stay off the picture."""
    return head("New session") + f'''<div style="width: 720px; min-height: 1180px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org", narrow=True)}
<div style="padding: 20px 24px; display: flex; flex-direction: column; gap: 16px;">
  <div style="font-size: 16px; font-weight: 600;">New session</div>
  <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px;">
    <div class="field"><label>Host</label><span class="input">kmaster</span></div>
    <div class="field"><label>Project</label><span class="input">samscrape · 2 repos<span class="muted">▾</span></span><span class="note">optional: narrows Directory to the project's repos on this host, and puts a Project block naming them in front of the brief</span></div>
    <div class="field"><label>Directory</label><span class="input mono">/home/kmaster/samscrape</span><span class="note">a repo checkout, a worktree, or any directory (registered repos and recent ones offered)</span><span class="note" style="color: #7c3d00;">held by <b>main</b> (idle) — pick a new worktree, or another directory</span></div>
    <div class="field"><label>Adapter</label><span class="input">claude-code<span class="muted">▾</span></span><span class="note">claude-code: state from hooks · shell: guessed from the screen</span></div>
    <div class="field"><label>Name</label><span class="input mono">td-302</span><span class="note" style="color: #065f46;">free — nothing holds that name here</span></div>
    <div class="field"><label>Profile</label><span class="input">the role's, else claude-code · paul (max) · opus (default)<span class="muted">▾</span></span><span class="note">tool · account · model, from ~/.agentorc/profiles.yml</span></div>
    <div class="field"><label>Role</label><span class="input">grinder [built-in + repo]<span class="muted">▾</span></span><span class="note">a preset fills the brief, lane, grants and profile it names; each can be edited before Start</span></div>
    <div class="field"><label>Lane</label><span class="input mono">TD-027, TD-019</span><span class="note">the references this session is handed, in order; empty: the role's default (free-pick)</span></div>
    <div class="field"><label>Resume (optional)</label><span class="input mono" style="color: #9ca3af;">the tool's session id</span></div>
  </div>
  <div class="field"><label>Where</label>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <div class="radio" style="opacity: .55;"><span class="rb"></span><div><div>This directory</div><div class="note">the checkout itself, or any directory</div></div><span style="flex-grow: 1;"></span><span class="pill plain s-ended">in use by main</span></div>
      <div class="radio on"><span class="rb"></span><div><div>New worktree</div><div class="note">for a git repo: <span class="mono">.claude/worktrees/&lt;name&gt;</span> on branch <span class="mono">&lt;name&gt;</span>, from origin's default branch; reused if it exists</div></div><span class="input mono" style="width: 240px; margin-left: auto;">td-302</span></div>
    </div>
  </div>
  <div class="field"><label>Opening prompt (optional)</label><span class="input" style="height: 72px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Paste the brief, or leave empty to start at the prompt — a role fills it from its template.</span></div>
  <div class="field">
    <div class="radio" style="gap: 12px;"><span class="switch"><span class="knob"></span></span><div><div>Unattended</div><div class="note">off: interactive — never paused, nudged, or killed by a policy. on: run window + usage gate from .agentorc.yml apply. Disabled for repos without an unattended block, hidden for directory sessions.</div></div></div>
  </div>
  <div class="field"><label>Controllers</label>
    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
      <div class="radio on" style="gap: 8px;"><span class="rb"></span><div><div>orc-1</div><div class="note mono">ao-samscrape-orc-1</div></div></div>
      <div class="radio" style="gap: 8px;"><span class="rb"></span><div><div>orchestrator-ao-1</div><div class="note mono">ao-agentorc-orchestrator-ao-1</div></div></div>
    </div>
    <span class="note">The sessions that may act on this one (send, wrap up, kill, close) — the ones holding <span class="mono">control</span>, since nothing else could. None ticked: nobody may, which is the default; add one later from Focus or with <span class="mono">ao control</span>. Ticked in advance: the role's or the repo's <span class="mono">controllers:</span>.</span>
  </div>
  <div class="warn">{ICON["warn"]}<span>One agent session per directory. The main checkout already hosts <b>main</b>, so a second agent session there is refused, not warned about. Shells and command runs are exempt.</span></div>
  <div style="display: flex; gap: 8px; justify-content: flex-end; padding-top: 6px;"><span class="btn">Cancel</span><span class="btn primary">Start session</span></div>
</div>
</div>
''' + TAIL

def legend():
    rows = [
        ("working", "Green: alive. A hook reported UserPromptSubmit / PreToolUse and output is still flowing. On a card green means working and nothing else."),
        ("needs", "Waiting on you: a permission (Allow / Deny answer it through the tool's hook; the terminal dialog only appears if the hook times out), a question (Focus — the terminal owns menus), or an empty prompt. Sorted to the top."),
        ("limited", "Hit a usage or token cap and is waiting on a reset. Reset time shown. Nothing you do unblocks it except switching the profile (account/model)."),
        ("idle", "Blue: alive, at rest, and may be spoken to. Turn finished (Stop hook), nothing pending. Flagged if the tree is dirty or unpushed."),
        ("unseen", "Still <span class=\"mono\">idle</span> in every payload, drawn in idle's blue with its own glyph and words: an <b>interactive</b> session finished and nobody has looked since — never an unattended one, whose manager read the result. A declaration is not a state — how it ended is said in the slot, <i>ready to close ✓</i> in its caption."),
        ("stalled", "Reported working, but no output for longer than the adapter's stall_after. How a credential lapse shows up."),
        ("exited", "Grey, like everything over or out of reach. Process ended or tmux session gone. Run log kept; the slot says <i>exited · code N</i>, and the foot's first button is Forget."),
        ("done", "Grey. You clicked Close: session killed, worktree reaped, card kept a day then filed under Resumable. Only you close a session; the checklist just says when it is ready. The slot says <i>closed by you</i>, in the text colour — no longer green."),
        ("unreachable", "Grey, and the card is dimmed. The host stopped answering, so every card on it flips at once and keeps its last known state. Sorts with idle on a volatile host (asleep laptop), after stalled? on one that should be up."),
    ]
    def lpill(s):
        return pill("idle", "idle · unseen", unseen=True) if s == "unseen" else pill(s)
    body = "".join(f'<tr><td style="width: 150px;">{lpill(s)}</td><td>{d}</td></tr>' for s, d in rows)
    # the state tokens the page's stylesheet carries (design §4.5 *The card's anatomy*, TD-095)
    tokens = [
        ("--working", "#16a34a", "working", "green — alive"),
        ("--idle", "#2563eb", "idle, idle · unseen", "blue — alive, at rest, may be spoken to"),
        ("--new", "#2563eb", "unread, <i>new</i> mail", "blue, idle's values under its own name, so the accent for what is new survives <i>working</i> turning green; an idle card with unread mail is blue twice, which reads rightly"),
        ("--ended", "#9ca3af", "exited, closed, unreachable", "one grey for everything over or out of reach"),
        ("--needs", "#f59e0b", "needs you", "amber, and the card is ringed — with red, the loudest on the page"),
        ("--stalled", "#dc2626", "stalled?", "red"),
        ("--limited", "#7c3aed", "limited", "violet"),
    ]
    tok = "".join(f'<tr><td class="mono" style="width: 150px; font-size: 12.5px;"><span style="display: inline-block; width: 10px; height: 10px; border-radius: 2px; background: {c}; margin-right: 6px; vertical-align: -1px;"></span>{t}</td>'
                  f'<td class="mono" style="font-size: 12.5px; width: 230px;">{st}</td><td>{d}</td></tr>' for t, c, st, d in tokens)
    return head("Legend") + f'''<div style="width: 900px; min-height: 1180px; background: #f4f5f7; padding: 20px 24px; box-sizing: border-box; display: flex; flex-direction: column; gap: 14px;">
  <div style="font-size: 16px; font-weight: 600;">States and badges</div>
  <div class="card"><table><tbody>{body}</tbody></table></div>
  <div class="card"><table><tbody>{tok}</tbody></table></div>
  <div class="card" style="padding: 12px; display: flex; flex-direction: column; gap: 8px;">
    <div style="display: flex; gap: 10px; align-items: center;">{pill("working", scraped=True)}<span>Dashed outline: state guessed from the last screen lines (tool without hooks, plain shells). Solid: reported by the tool's hooks.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 12px; color: #4b5563; white-space: nowrap;">claude-code · paul (max) · opus</span><span>Profile line: tool · account · model. Commands, policies, and usage gates key on the profile, so two accounts of one tool are tracked separately.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mode" style="white-space: nowrap;">unattended</span><span class="mode mine" style="white-space: nowrap;"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="{PERSON}"></path></svg>interactive</span><span>The mode, on every card as a word, never pressable: <i>unattended</i> is quiet (run window, usage gate, wrap-up-then-kill and credential checks apply), <b>interactive</b> carries the person mark at the text's full strength — the person's own sessions are the ones that stand out (never paused, nudged, or killed by a policy). The toggle is in the card's <b>more</b> and on the Focus header. The person glyph is reserved for this mark: no role may name it.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mark" style="white-space: nowrap;">✉ 2</span><span>Marks, on the card's second row: unread mail, an identity alarm, <i>suspended</i>. Never pressable.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="meta" style="flex-shrink: 0;">TD-301 → #811 · 1/3 done</span><span class="meta" style="flex-shrink: 0;">#809 · 2/2 done</span><span>The report line: a reference is shown once — where the entry's reference is the PR itself it is never <span class="mono">#809 → #809</span>. Dashed underline: derived by the host agent, not declared.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 12px; color: #4b5563; white-space: nowrap;">shell</span><span>A shell is an adapter like any other: scraped state, no profile, exempt from the one-agent-per-directory rule, as are command runs. Predefined command runs are a separate kind and live on the Commands tab.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 12px; color: #4b5563; white-space: nowrap;">kmaster ● laptop ◐</span><span>Host chips in the top bar: ● reachable, ◐ volatile host currently unreachable (asleep), ○ non-volatile host unreachable (a problem).</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="flag">{ICON["warn"]}dirty · 2 unpushed</span><span>Stranded-work flag: idle or exited with uncommitted or unpushed changes.</span></div>
  </div>
</div>
''' + TAIL

def direction_b():
    """Low-fi alternate: card grid instead of table."""
    def c(name, state, sub):
        return f'<div class="card" style="padding: 10px; display: flex; flex-direction: column; gap: 6px;"><div style="display: flex; gap: 8px; align-items: center;"><span class="mono" style="font-weight: 500;">{name}</span><span style="flex-grow: 1;"></span>{pill(state)}</div><div class="muted" style="font-size: 12px;">{sub}</div><div class="term" style="height: 54px; font-size: 12px; padding: 6px 8px; color: #aab3bf;">⏺ Bash(pdm run test)\n  412 passed\n▌</div></div>'
    cards = "".join([
        c("samscrape/main", "needs", "kmaster · 2m · Permission: git push"),
        c("samscrape/tdgrind-1", "working", "kmaster · 14s"),
        c("samscrape/tdgrind-2", "stalled", "kmaster · 47m · 3 unpushed"),
        c("samscrape/errors-alerts", "idle", "kmaster · 3h · dirty"),
        c("contractmatch/main", "idle", "kmaster · 22m"),
        c("dev-cadence/attention-fix", "working", "vps · 1m · gemini"),
    ])
    return head("Alt") + f'''<div style="width: 1100px; min-height: 620px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  <div style="display: flex; align-items: center; gap: 10px;"><span style="font-size: 16px; font-weight: 600;">Org</span><span class="muted">card grid with live tail — alternate to the table</span></div>
  <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;">{cards}</div>
  <div class="note">Trade-off: you see the last lines of every session at once, but fewer sessions fit per screen and host/repo grouping is weaker. The table (Main) scales to 20+ sessions; this scales to ~9.</div>
</div>
</div>
''' + TAIL

# ---------------- Resumable / Commands (round 4; the Attention screen struck, TD-123) ----------------

def page_head(title, summary, right=""):
    return f'''<div style="display: flex; align-items: center; gap: 10px;">
    <span style="font-size: 16px; font-weight: 600;">{title}</span>
    <span class="muted">{summary}</span>
    <span style="flex-grow: 1;"></span>{right}
  </div>'''

RESUMABLE = [
    ("kmaster", "samscrape", "/home/kmaster/samscrape", [
        # (id, slug, where, span, started, ended, board, live)
        ("923484c8", "main", "main", "09-04 14:20 → 16:58", "Lets talk about designing an agent monitor/controller. We have tdgrind agents that run here…", "There's no separate handoff. The mockup canvas is a Claude Code artifact…", 0, "active"),
        ("826ece0e", "errors-alerts", "wt/errors-alerts → errors_alerts", "08-28 00:10 → 09-04 16:08", "I am still getting consistent errors via Telegram and email. Are those because something needs to be fixed…", "Done end to end. The TD-143 decision is live and the backlog is draining. What shipped (PR #584…", 0, "active"),
        ("f39686ef", "", "wt/board-automation → board_automation", "08-27 02:07 → 09-04 12:41", "Wire the signup → CM board writer behind a flag; PAT comes from Doppler.", "Verification checklist complete; leaving the flag OFF until you decide (board item added).", 1, "hand"),
        ("2fb13646", "", "main", "08-27 09:12 → 08-27 11:40", "Review PR #343 and apply migration 071 on staging.", "Merged and applied. One decision left for you on the board: turn the writer ON?", 1, ""),
        ("0502decc", "td-276", "wt/td-276 → td276-recurrence", "08-24 20:03 → 08-25 02:10", "Pick up TD-276 per the brief; branch td276-recurrence.", "Compaction recap: ladder paged twice on debut; what recurrence_count should mean is your call.", 1, ""),
    ]),
    ("vps", "dev-cadence", "/home/paul/dev-cadence", [
        ("3168de4c", "main", "main", "08-26 07:30 → 08-26 08:05", "Add --fetch to the SessionStart --report --due-only hook line in the consumer repos.", "Done in dev-cadence; the consumer settings.json files are SEED, so listed on the board for you.", 1, ""),
        ("b71d02e9", "td-4", "wt/td-4 (reaped) → td-4-registry", "08-30 19:00 → 08-30 21:12", "Implement the registry reader per TD-4.", "PR #9 merged; tree clean, pushed.", 0, "closed"),
    ]),
]

def resumable():
    def r(host, repo, path, s):
        sid, nm, where, span, started, ended, board, live = s
        if live == "active":
            state = pill("working", "active now")
            act = f'<span class="btn sm">{ICON["focus"]}Switch to</span>'
        elif live == "closed":
            state = pill("done", "closed 5d")
            act = f'<span class="btn sm primary">{ICON["resume"]}Resume</span>'
        elif live == "hand":
            state = '<span class="badge">started by hand</span>'
            act = f'<span class="btn sm">Adopt…</span><span class="btn sm primary">{ICON["resume"]}Resume</span>'
        else:
            state = ""
            act = f'<span class="btn sm primary">{ICON["resume"]}Resume</span>'
        if nm:
            title = f'<a href="#" class="mono" style="font-weight: 500;">{nm}</a>'; slug_html = f'<span class="mono muted" style="font-size: 12px;">{sid}</span>'
        else:
            title = f'<a href="#" class="mono" style="font-weight: 500;">{sid}</a>'; slug_html = '<span class="muted" style="font-size: 12px;">no name yet</span>'
        board_html = f'<span class="flag" style="color: #7c3d00;">{ICON["warn"]}{board} on board</span>' if board else '<span class="muted">—</span>'
        return f'''<tr>
  <td style="width: 240px;"><div style="display: flex; flex-direction: column; gap: 3px;"><div style="display: flex; align-items: center; gap: 8px;">{title}{state}</div>{slug_html}</div></td>
  <td style="width: 210px;"><span class="mono" style="font-size: 12px; color: #4b5563;">{where}</span></td>
  <td style="width: 170px;"><span class="mono muted" style="font-size: 12px;">{span}</span></td>
  <td><div class="rs"><div><span class="rs-k">started</span>{started}</div><div><span class="rs-k">ended</span>{ended}</div></div></td>
  <td style="width: 100px;">{board_html}</td>
  <td style="width: 170px;"><div style="display: flex; justify-content: flex-end; gap: 4px;">{act}</div></td>
</tr>'''
    groups = ""
    for host, repo, path, rows in RESUMABLE:
        groups += f'<tr><td colspan="6" style="padding: 0;"><div class="grp"><span>{host} / {repo}</span><span class="path mono" style="font-size: 12px;">{path}</span><span style="flex-grow: 1;"></span><span class="badge">claude-code · ~/.claude/projects</span></div></td></tr>'
        groups += "".join(r(host, repo, path, s) for s in rows)
    right = f'''<span class="input" style="width: 200px; height: 28px; color: #9ca3af;">search transcripts…</span>
    <span class="btn ghost">host: all ▾</span><span class="btn ghost">repo: all ▾</span><span class="btn ghost">last 30 days ▾</span>
    <span style="width: 1px; height: 20px; background: #cbd0d6; margin: 0 8px;"></span>
    <span style="display: inline-flex; border: 1px solid #cbd0d6; border-radius: 4px; overflow: hidden;"><span class="btn" style="border: 0; border-radius: 0; background: #e5e7eb; color: #111418;">Recent</span><span class="btn" style="border: 0; border-radius: 0;">Closed</span><span class="btn" style="border: 0; border-radius: 0;">With board items</span></span>'''
    return head("Resumable") + f'''<div style="width: 1440px; min-height: 700px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Resumable")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  {page_head("Resumable", "31 transcripts · 2 hosts · 3 active now", right)}
  <div class="card"><table><thead><tr><th>Session</th><th>Where</th><th>Start → last</th><th>Started / ended</th><th>Board</th><th></th></tr></thead><tbody>{groups}</tbody></table></div>
  <div class="note"><b>Resume</b> opens New session with host, repo, and worktree prefilled and Start = Resume; a reaped worktree is recreated from the recorded branch. <b>Switch to</b> jumps to the running card in the Org (same session: the name and the adapter id travel together from birth). A session started by hand shows only its id until you <b>Adopt</b> it (attach, give it a name), which is how it enters the Org. The index is generated on demand from each adapter's transcripts (Claude: the JSONL under ~/.claude/projects, same as list_sessions.py), so crashed and disconnected sessions appear too. Closed sessions are filed here after their day on the Org.</div>
</div>
</div>
''' + TAIL

COMMANDS = [
    ("kmaster", "samscrape", "/home/kmaster/samscrape", [
        # (name, run, last)  last: (state, when, summary)
        ("test", "pdm run test", ("exited", "1h ago", "exit 0 · 412 passed")),
        ("cluster", "./scripts/cluster-status.sh", ("working", "8s", "")),
        ("attention", "python scripts/nudge_user_attention.py --report", ("exited", "3h ago", "exit 0 · 5 due/overdue")),
    ]),
    ("vps", "dev-cadence", "/home/paul/dev-cadence", [
        ("test", "pytest -q", ("exited", "2d ago", "exit 0 · 96 passed")),
        ("sync-all", "./sync-all.sh", ("exited", "2d ago", "exit 1 · SKIP samscrape: cadence-sync PR open")),
    ]),
]

def commands():
    def cmd(repo, c):
        name, run, (state, when, summary) = c
        if state == "working":
            last = f'<div class="status" style="border-color: #2563eb; color: #1e40af;">running · {when}</div>'
            act = f'<span class="btn sm">{ICON["focus"]}Focus</span><span class="btn sm danger">{ICON["kill"]}Stop</span>'
        else:
            bad = summary.startswith("exit 1")
            last = f'<div class="status {"bad" if bad else "ok"}">{summary} · {when}</div>'
            act = f'<span class="btn sm primary">{ICON["play"]}Run</span><span class="btn sm ghost">log</span>'
        return f'''<div class="card" style="padding: 12px; display: flex; flex-direction: column; gap: 8px; position: relative; overflow: hidden;">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div style="display: flex; align-items: center; gap: 8px;"><span class="name" style="font-size: 14px;">{name}</span><span style="flex-grow: 1;"></span>{pill(state, "running" if state == "working" else state, scraped=True)}</div>
  <div class="meta">$ {run}</div>
  {last}
  <div style="display: flex; gap: 6px;">{act}</div>
</div>'''
    groups = ""
    for host, repo, path, cmds in COMMANDS:
        cards = "".join(cmd(repo, c) for c in cmds)
        groups += f'''<div style="display: flex; flex-direction: column; gap: 8px;">
    <div class="grp" style="padding: 0;"><span>{host} / {repo}</span><span class="path mono" style="font-size: 12px;">{path}/.agentorc.yml</span><span style="flex-grow: 1;"></span><span class="btn sm ghost">edit yml</span></div>
    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; align-items: start;">{cards}</div>
  </div>'''
    groups += f'''<div style="display: flex; flex-direction: column; gap: 8px;">
    <div class="grp" style="padding: 0;"><span>kmaster / contractmatch</span><span class="path mono" style="font-size: 12px;">/home/kmaster/contractmatch</span></div>
    <div class="note" style="padding: 10px 12px; border: 1px dashed #cbd0d6; border-radius: 6px;">No <span class="mono">.agentorc.yml</span> in this repo. Add a <span class="mono">commands:</span> list to get buttons here; each press starts a <span class="mono">ao-contractmatch-cmd-&lt;name&gt;</span> session.</div>
  </div>'''
    runs = [
        ("ao-samscrape-cmd-cluster", "kmaster", "working", "8s", "kubectl get pods -n samscrape\nNAME                         READY   STATUS    AGE\ncurate-input-28471            1/1     Running   3h\n▌"),
        ("ao-samscrape-cmd-test", "kmaster", "exited", "1h", "412 passed in 38.2s · exit 0"),
        ("ao-samscrape-cmd-attention", "kmaster", "exited", "3h", "Attention report — 2 board(s), 27 open item(s), 6 due/overdue · exit 0"),
        ("ao-dev-cadence-cmd-sync-all", "vps", "exited", "2d", "SKIP samscrape: cadence-sync PR open · exit 1"),
    ]
    rows = ""
    for tmux, host, state, age, out in runs:
        if state == "working":
            body = f'<div class="term tail" style="height: 64px;">{out}</div>'
        else:
            body = f'<div class="status {"bad" if "exit 1" in out else "ok"}">{out}</div>'
        rows += f'''<tr>
  <td style="width: 260px;"><a href="#" class="mono" style="font-weight: 500;">{tmux}</a></td>
  <td style="width: 90px;" class="muted">{host}</td>
  <td style="width: 100px;">{pill(state, "running" if state == "working" else state, scraped=True)}</td>
  <td style="width: 60px;" class="muted">{age}</td>
  <td>{body}</td>
  <td style="width: 150px;"><div style="display: flex; gap: 4px; justify-content: flex-end;"><span class="btn sm">{ICON["focus"]}Focus</span><span class="btn sm ghost">log</span></div></td>
</tr>'''
    return head("Commands") + f'''<div style="width: 1440px; min-height: 820px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Commands")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 16px;">
  {page_head("Commands", "5 commands in 2 repos · 1 running", '<span class="btn ghost">host: all ▾</span><span class="btn ghost">repo: all ▾</span>')}
  {groups}
  <div style="display: flex; flex-direction: column; gap: 8px;">
    <div class="grp" style="padding: 0;"><span>Recent runs</span><span class="path">each run is a session of kind command — same tmux, same log, same Focus; hidden from the Org unless "show command runs" is on</span></div>
    <div class="card"><table><tbody>{rows}</tbody></table></div>
  </div>
  <div class="note">Buttons come from each repo's checked-in <span class="mono">.agentorc.yml</span> (cmdorc command specs where cmdorc fits). A press starts <span class="mono">ao-&lt;repo&gt;-cmd-&lt;name&gt;</span> in tmux on that host, so the run gets the same Focus, running/exited state and run log as any session, but as kind: command it stays off the Org and out of the urgency sort. The attention report's refresh is the attention command here — no second way to run a script. State is scraped (dashed pill): running while the pane has a process, exited with the exit code from the marker.</div>
</div>
</div>
''' + TAIL

# ---------------- Inbox (design round 2, 2026-09-20, TD-082 — option A, Paul's pick) ----------------
INBOX_BLURB = {
    "Needs you": "Counted — the top bar's first number is exactly this list. A pending permission or question, a limited or stalled session, an exited session with unpushed work, an open ask, a paused steer, an outcome reported blocked. What is on a tool's clock first, then oldest first.",
    "Steering": "Not counted. Open steers whose clock is running, soonest first, each with the default it will take. Doing nothing is a valid answer: at its bound the session goes with its default.",
    "Waiting on them": "Not counted — these wait on a session, not on you. Questions you answered whose asker has not yet said what came of it. Dismiss says you do not need to hear back.",
    "Answered for you": "Not counted. Questions a teammate answered for you from the record, each with its source. Overrule sends your word to the asker.",
    "FYI": "The second number, never added to the first. Notes, what resolved itself (the trail), and closed questions, kept for 12 hours. Nothing here has to be acted on; it opens itself when there is something new.",
}


def isec(title, count, extra="", opened=False):
    pop = f'<div class="pop">{INBOX_BLURB[title]}</div>' if opened else ""
    mark = '<span style="font-size: 10px;">▾</span>' if title == "FYI" else ""
    return f'''<div class="isec-h">{mark}<span>{title}</span><span class="n">{count}</span><span class="info{" on" if opened else ""}">i</span><span style="flex-grow: 1;"></span>{extra}{pop}</div>'''


def mcard(bar, kind, name, team, age, body, controls, cls="", state=None):
    badge = f'<span class="badge">{team}</span>' if team else '<span class="badge" style="border-style: dashed;">no team</span>'
    return f'''<div class="mcard {cls}"><span class="sbar" style="background: {bar};"></span>
  <div class="who">{pill(*state) if state else f'<span class="kind">{kind}</span>'}<span class="nm">{name}</span>{badge}<span style="flex-grow: 1;"></span><span class="meta">{age}</span></div>
  {body}
  <div class="ctl">{controls}</div>
</div>'''


def fold(summary, open_=False, body=""):
    """TD-127: the *details* disclosure under a row's first paragraph — a quiet unbordered line, never a button."""
    mark = "▾" if open_ else "▸"
    inner = f'<div class="txt" style="margin-top: 6px; color: #374151;">{body}</div>' if open_ else ""
    return f'<div style="margin-top: 2px;"><span class="muted" style="font-size: 12px; cursor: default;"><span style="font-size: 10px;">{mark}</span> {summary}</span>{inner}</div>'


def inbox_rows():
    """The rows every Inbox artboard draws (needs, steering, waiting, fyi), in the page's order."""
    b = lambda label, c="": f'<span class="btn sm {c}">{label}</span>'
    gap = '<span style="flex-grow: 1;"></span>'
    needs = [
        mcard(BAR["needs"], "", "tdgrind-ao-2", "ao-grind", "asked 40s ago · 4m 20s left",
              '<div class="txt">Permission · <span class="mono">Bash</span> · <span class="mono">git push origin td073-usage-chips</span></div><div class="meta">doing 3m ago: TD-073: usage chips side by side — pushing for review</div>',
              b("Allow", "primary") + b("Deny") + gap + b(ICON["focus"] + "Open", "ghost"), cls="hover", state=("needs", "permission")),
        mcard(BAR["needs"], "ask", "tdgrind-ao-1", "ao-grind", "18m ago · about TD-079",
              '<div class="txt">TD-079: a permission answered from Focus and again from the Inbox within five seconds — <b>two trail rows, or one?</b> I will go with two unless you say otherwise; the fixture changes either way.</div>'
              + fold("details")
              + '<div class="sugg"><span class="lbl">suggested by tdgrind-ao-1</span>' + b("“Two rows — the how is the point”") + b("“One row, the later how wins”") + '</div>',
              b("Reply", "primary") + b("Snooze ▾") + gap + b("Delete", "ghost danger") + b(ICON["focus"] + "Open", "ghost"), cls="focus"),
        mcard(BAR["exited"], "", "push", "", "exited 2h ago",
              '<div class="txt">Exited with unpushed work — <b>2 commits only on this machine</b>, measured against <span class="mono">origin/td068-prompt-refused</span>.</div><div class="meta">Ready to close: tree clean ✓ · pushed ✗ · PR none</div>',
              b(ICON["resume"] + "Reopen and push", "primary") + b("Resume") + b("Snooze ▾") + gap + b(ICON["focus"] + "Open", "ghost"), state=("exited", "exited")),
        mcard(BAR["needs"], "promote", "agentorc", "", "main moved 12m ago",
              '<div class="txt">live <span class="mono">485d28b</span> · main <span class="mono">9c1e0f2</span>, <b>3 commits ahead</b> · checks <b style="color: #16a34a;">green</b> · auto off</div><div class="meta">the checkout is on main with a clean tree — the press makes it live; a rollback is <span class="mono">ao promote --sha</span></div>',
              b("Promote", "primary") + b("Snooze ▾") + gap + b(ICON["focus"] + "Open", "ghost")),
        mcard(BAR["needs"], "board", "agentorc", "ao-grind", "1d overdue",
              '<div class="txt"><span class="muted">grinder-ao-2 on kmaster · <b style="color: #374151;">still on TD-122 — grinder-ao-2 holds it</b></span></div>'
              '<div class="txt">Live look pending once #517 is merged and promoted: the usage chip is one per account (TD-122). On the Org the top bar should read <i>Claude · paul · week n%</i> once, not once per profile.</div>'
              '<div class="meta">on the board — Reply writes under your name on this line and, while a session holds TD-122, mails it there too; Snooze moves its Due: date; Done checks it off</div>',
              b("Reply", "primary") + b("Snooze ▾") + b("Done") + gap + b("Open board", "ghost")),
        mcard(BAR["stalled"], "outcome · blocked", "lead-cm-1", "cm-grind", "reported 6m ago",
              '<div class="quoted">You answered “Use the staging key” 1h ago to: <i>Which Stripe key should the worker API tests use?</i></div><div class="txt">Blocked: the staging key is not in Doppler’s <span class="mono">dev</span> config, and I cannot add one.</div>',
              b("Reply", "primary") + b("Dismiss") + gap + b(ICON["focus"] + "Open", "ghost")),
    ]
    steering = [
        mcard(BAR["working"], "steer", "orchestrator-ao-1", "ao-grind", "9m ago · <b>21m left</b>",
              '<div class="txt">tdgrind-ao-1 has ended its run with the ledger still holding work. Restart it with fresh context, or stop the team for the night?</div><div class="meta">will go with: <b style="color: #374151;">Restart it once</b> — doing nothing is a valid answer</div>'
              '<div class="sugg"><span class="lbl">suggested by orchestrator-ao-1</span>' + b("“Restart it once” · default") + b("“Stop the team”") + '</div>',
              b("Reply") + b("Go with it", "primary") + b("Pause") + gap + b(ICON["focus"] + "Open", "ghost")),
    ]
    waiting = [
        mcard("#cbd0d6", "answered · waiting for the outcome", "tdgrind-ao-1", "ao-grind", "answered 52m ago",
              '<div class="quoted">You answered “Rebase, do not merge main in” to: <i>#269 conflicts with main since #267 — rebase or merge?</i></div><div class="meta">doing 4m ago: TD-079 1b: rebased, re-running the suite before the review</div>',
              b("Dismiss") + gap + b(ICON["focus"] + "Open", "ghost")),
    ]
    answered = [
        mcard("#cbd0d6", "answered for you · source", "techlead-ao-1", "ao-grind", "24m ago · answered grinder-ao-1",
              '<div class="quoted">grinder-ao-1 asked: <i>#517 is ready — read it against the usage design and merge?</i></div>'
              '<div class="txt"><b>Merged #517</b> — read against §4.2a; one gap left, not a blocker (a follow-up in TD-122).</div>'
              + fold("details", open_=True, body=
                     'Measured against §4.2a <i>Profiles</i> and §4.5a <i>usage chip</i>: one chip per account, the poll once per account, <span class="mono">rate_limited</span> gone from the log.'
                     '<ul style="margin: 6px 0 0 18px; padding: 0;"><li>the gate reads the account, not the profile — as designed</li><li><b>gap</b>: the per-model window is read but not drawn; TD-122 (3)</li><li>the ledger line says 09-22, the PR 09-23 — the PR is right</li></ul>'
                     'Source: design §4.2a, §4.5a <i>usage chip</i>; <a href="#" style="color: #1f5fa8;">the review comment</a> <span class="muted" style="font-size: 12px;">github.com</span>'),
              b("Overrule") + b("Dismiss", "ghost")),
    ]
    fyi = [
        mcard("#cbd0d6", "trail · new", "tdgrind-ao-2", "ao-grind", "12m ago",
              '<div class="txt muted">A permission (<span class="mono">Bash · pdm run test</span>) was <b style="color: #374151;">allowed from Focus</b>.</div>', b("Put on the board", "ghost") + b("Dismiss", "ghost")),
        mcard("#cbd0d6", "note · new", "tdgrind-ao-1", "ao-grind", "31m ago",
              '<div class="txt">done: TD-068 — PR #272 merged</div>', b("Put on the board", "ghost") + b("Dismiss", "ghost")),
    ]
    return needs, steering, waiting, answered, fyi


def inbox(picks=False):
    """Screen 6 with the rail (TD-129). `picks=True` is `InboxRail.dc.html`: ao-grind and *Needs you* pressed."""
    needs, steering, waiting, answered, fyi = inbox_rows()
    fyi_extra = '<span class="btn sm ghost" style="text-transform: none; letter-spacing: 0;">Dismiss all</span>'
    if not picks:
        secs = [("Needs you", 6, False), ("Steering", 1, False), ("Waiting on them", 1, False), ("Answered for you", 1, False), ("FYI", 14, False)]
        teams = [("ao-grind", 3, False), ("cm-grind", 1, False), ("guardians", 0, False), ("no team", 2, False)]
        kinds = [("questions", 1, False), ("steering", 1, False), ("session states", 3, False), ("board items", 1, False), ("notes", 3, False), ("trail", 6, False)]
        body = (isec("Needs you", 6, opened=True) + "".join(needs) + isec("Steering", 1) + "".join(steering)
                + isec("Waiting on them", 1) + "".join(waiting) + isec("Answered for you", 1) + "".join(answered) + isec("FYI", "2 new · 14", fyi_extra) + "".join(fyi)
                + '<div class="muted" style="padding: 2px 2px 0; font-size: 12px;">12 earlier entries — <a href="#">show</a> · 1 snoozed — <a href="#">show</a></div>')
        summary, title = "", "Inbox"
        note = ("Design notes, not page text. <b>The rail</b> (TD-129, Paul's shape, 2026-09-24): under the title, which has the top line to itself; left of the column, sticky, three groups of toggles — the sections in the page's order, the teams with their <i>Needs you</i> counts, the coarse kinds — and the find box. Nothing pressed here, so every count is the whole. Within a group picks are OR'd, across groups AND'd, the find a fourth group; nothing picked means all. The first group is <i>Urgency</i> — what orders the page — not <i>Sections</i>, which names nothing a person looks for, and not <i>State</i>, a session's word and a kind below. The typed <span class=\"mono\">team:</span> box is gone: a filter that is a control is not typed. "
                "<b>One centred column</b> (1100 px at most) beside it — a queue reads in order, top to bottom. <b>A section is a heading</b>, not a box: its name, its count, and an <i>i</i> mark that holds the blurb (drawn open on <i>Needs you</i>). <b>A row is a card</b>: its own surface, a hover state (first card) and a keyboard focus ring (second) — <span class=\"mono\">j</span> / <span class=\"mono\">k</span> move the ring, <span class=\"mono\">Enter</span> opens a mail row's page, <span class=\"mono\">o</span>, <span class=\"mono\">a</span>, <span class=\"mono\">d</span>, <span class=\"mono\">r</span>, <span class=\"mono\">s</span>, <span class=\"mono\">x</span> press the row's own Open, Allow, Deny, Reply, Snooze and Dismiss or Done (§4.5a <b>keys</b>, TD-124). The state pill and the kind label are flat and unbordered so they never read as buttons; everything bordered is a control. Suggested answers stay in their own dashed group, in quotation marks. <b>A board row</b> (TD-126) says its sender's standing before the press — <i>still on TD-122 — grinder-ao-2 holds it</i>, <i>moved on</i> or <i>gone</i> — and carries <b>Reply</b>: the words go on the board line under Paul's name always, and to the lease holder as well while one exists; a reply is not Done. <b>A message has one shape</b> (TD-127): its first paragraph is the whole of what you need, the rest folds under <i>details</i> — closed on the ask row, open on the <i>answered for you</i> row, where the reading is a rendered list from the closed markdown subset and the one link carries its host after its text.")
    else:
        secs = [("Needs you", "2 of 6", True), ("Steering", "0 of 1", False), ("Waiting on them", "0 of 1", False), ("Answered for you", "0 of 1", False), ("FYI", "0 of 14", False)]
        teams = [("ao-grind", "2 of 3", True), ("cm-grind", "0 of 1", False), ("guardians", "0 of 0", False), ("no team", "0 of 2", False)]
        kinds = [("questions", "1 of 1", False), ("steering", "0 of 1", False), ("session states", "1 of 3", False), ("board items", "0 of 0", False), ("notes", "0 of 3", False), ("trail", "0 of 6", False)]
        body = (isec("Needs you", "2 of 6") + "".join(needs[:2])
                + '<div class="muted" style="padding: 8px 2px 0; font-size: 12px;">Steering, Waiting on them, Answered for you and FYI are not picked — press them in the rail, or <b>Clear filters</b>.</div>')
        summary, title = "", "Inbox"
        note = ("Design notes, not page text. <b>The rail with picks</b> (TD-129): <i>ao-grind</i> and <i>Needs you</i> pressed — Paul's workflow, one team's <i>Needs you</i> worked through, then the next team's. <b>Every count is a count of rows on the page now</b>: a picked line reads its share, an unpicked line in a group with a pick reads <i>0 of all</i>, dimmed (it contributes nothing until pressed, and <i>all</i> says what it would bring), an unpicked line in a group without a pick reads its share under the other groups' picks. The title row is <i>Inbox</i> alone: the picks are on the left, so a <i>showing …</i> line and a needs-you pill beside it were noise. <i>Needs you</i> reads <i>2 of 6</i> because the top bar's number is never filtered. A section not picked is not drawn; one picked and emptied by the other groups would draw its heading and its empty line — a filter shows or hides rows and never re-orders the queue. Every count reads <i>n of all</i> while anything is picked or typed and a plain number otherwise. <b>Clear filters</b> appears at the rail's head while anything is picked or typed and clears the lot. The URL is <span class=\"mono\">/inbox?team=ao-grind&amp;sec=needs</span>: a filtered Inbox is a link, remembered per browser for a bare <span class=\"mono\">/inbox</span>. "
                "Not designed: a preview pane (TD-129 option b) — at any width a row's text opens its page (<i>Inbox — message</i>).")
    return head(title) + f'''<div style="width: 1440px; min-height: {1960 if not picks else 1000}px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Inbox 6 · 2")}
<div style="padding: 16px 20px 28px;">
<div style="max-width: 1324px; margin: 0 auto 12px;">{page_head(title, summary)}</div>
<div style="display: flex; gap: 24px; align-items: flex-start; max-width: 1324px; margin: 0 auto;">
{rail(secs, teams, kinds, all_on=picks)}
<div class="inboxcol" style="margin: 0; flex: 1 1 auto; min-width: 0;">
  {body}
  <div class="note" style="padding-top: 10px; border-top: 1px solid #dfe3e8; margin-top: 8px;">{note}</div>
</div>
</div>
</div>
</div>
''' + TAIL


def rail(secs, teams, kinds, all_on=False, find=""):
    """The Inbox rail (design §4.5 screen 6 *The rail*, TD-129): three groups of toggles and the find box."""
    def line(label, n, on):
        dim = n == 0 or str(n).startswith("0 ")
        st = "display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 5px; font-size: 14px; cursor: default;"
        if on:
            st += " background: #e6e9ee; font-weight: 600; box-shadow: inset 3px 0 0 #1f5fa8;"
        col = "#9ca3af" if dim else "#1c2128"
        ncol = "#9ca3af" if dim else ("#111418" if on else "#6b7280")
        return f'<div style="{st} color: {col};"><span style="flex-grow: 1;">{label}</span><span class="mono" style="font-size: 12px; color: {ncol};">{n}</span></div>'
    def group(title, lines, blurb):
        return (f'<div style="font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: #6b7280; padding: 12px 8px 4px;" title="{blurb}">{title}</div>'
                + "".join(line(*l) for l in lines))
    allbtn = ('<span class="btn sm" style="width: 100%; justify-content: center; margin-bottom: 6px;">Clear filters</span>' if all_on
              else '<div style="height: 6px;"></div>')
    findbox = (f'<div class="input" style="height: 28px; color: {"#1c2128" if find else "#9ca3af"}; font-size: 12px; display: flex; align-items: center; gap: 6px; padding: 0 8px; white-space: nowrap;" title="every word typed must match, in any order — a fourth pick, AND’d with the rail’s">{find or "find…"}<span style="flex-grow: 1;"></span>'
               + (f'<span class="mono" style="font-size: 12px; color: #6b7280;">3 of 83</span>' if find else '<span class="mono" style="font-size: 12px; color: #9ca3af;">/</span>') + '</div>')
    return f'''<div style="flex: 0 0 200px; position: sticky; top: 16px; display: flex; flex-direction: column; font-size: 14px;">
  {allbtn}
  {findbox}
  {group("Urgency", secs, "the page's sections, in its order — needs you, on a clock, waiting on them, none")}
  {group("Teams", teams, "every team a row carries — the count is what that team needs from you")}
  {group("Kinds", kinds, "the coarse kind of a row")}
</div>'''


def inbox_message():
    """Screen 6's message page (design §4.5 screen 6 *The message page*, TD-129): one entry, whole, with its thread and the row's controls at the foot."""
    b = lambda label, c="": f'<span class="btn sm {c}">{label}</span>'
    gap = '<span style="flex-grow: 1;"></span>'
    entry = mcard(BAR["needs"], "ask", "tdgrind-ao-1", "ao-grind", "18m ago · about TD-079 · asked in Needs you",
        '<div class="txt">TD-079: a permission answered from Focus and again from the Inbox within five seconds — <b>two trail rows, or one?</b> I will go with two unless you say otherwise; the fixture changes either way.</div>'
        + fold("details", open_=True, body='What I looked at: §4.10 <i>The trail</i> says a row per <span class="mono">{sid, kind, how}</span> and that two answers to one permission are one event; <span class="mono">sessionorc/trail.py</span> coalesces on the three-tuple as written, so today two <i>hows</i> are two rows. The TD-079 test fixture assumes one. My default is two rows — the <i>how</i> is the point of the trail — and the fixture changes. Cost of the other reading: the coalesce key loses <span class="mono">how</span> and the row says <i>answered from Focus and the Inbox</i>.'),
        "", cls="")
    thread = [
        mcard("#cbd0d6", "ask → techlead-ao-1", "tdgrind-ao-1", "ao-grind", "41m ago",
              '<div class="txt muted">Same question, asked of the techlead first (§4.9b). <i>Passed up</i> 18m ago with a recommendation.</div>', ""),
        mcard("#cbd0d6", "passed up · recommendation", "techlead-ao-1", "ao-grind", "18m ago",
              '<div class="txt">Not written down anywhere I can cite — passing it up. My recommendation: <b>two rows</b>; the trail exists to say <i>how</i> a thing resolved, and one row would have to say two hows.</div>', ""),
        mcard("#cbd0d6", "system", "home", "", "18m ago",
              '<div class="txt muted">Delivered to you under <i>Needs you</i>; tdgrind-ao-1 was told it waits on you (no bound — an ask to the person does not expire).</div>', ""),
    ]
    controls = ('<div class="sugg"><span class="lbl">suggested by techlead-ao-1</span>' + b("“Two rows — the how is the point” · recommended") + b("“One row, the later how wins”") + '</div>'
                + '<div class="ctl">' + b("Reply", "primary") + b("Snooze ▾") + gap + b("Delete", "ghost danger") + b(ICON["focus"] + "Open", "ghost") + '</div>')
    return head("Inbox — message") + f'''<div style="width: 1440px; min-height: 1180px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Inbox 5 · 2")}
<div style="padding: 16px 20px 28px;">
<div class="inboxcol">
  <div style="display: flex; align-items: center; gap: 10px;"><a href="#" style="font-size: 14px; color: #1f5fa8; text-decoration: none;">← Back</a><span class="muted" style="font-size: 12px;">to Inbox · ao-grind · Needs you · row 2 of 2</span><span style="flex-grow: 1;"></span><span class="muted mono" style="font-size: 12px;">k ↑ previous · j ↓ next · Esc back</span></div>
  {entry}
  {isec("Answer", "")}
  <div class="mcard" style="gap: 8px;">{controls}</div>
  {isec("Thread", 3)}
  {"".join(thread)}
  <div class="muted" style="padding: 2px 2px 0; font-size: 12px;">earlier entries pruned — the thread reaches as far as retention keeps it</div>
  <div class="note" style="padding-top: 10px; border-top: 1px solid #dfe3e8; margin-top: 8px;">Design notes, not page text. <b>The message page</b> (<span class="mono">/inbox/&lt;id&gt;</span>, TD-129): one mail entry whole, reached from the row's text, from <span class="mono">Enter</span> on the ringed row, and from a trail row's <i>re</i>. Three parts, in Paul's order (2026-09-24): <b>the entry</b> — the row's head, the text whole with <i>details</i> open (TD-127's shape, when it lands); <b>the answer</b> — the row's own controls again, the same RPCs, so the entry is answered here without scrolling and the page returns to the list when the answer removes the row; <b>the thread</b> — the question it answers, the replies it drew (the person's own among them), the outcome, the pass-up and its recommendation, the <i>system</i> notes — oldest first, each in its kind's row shape, none of it a control built from text, gathered at the home by <span class="mono">root</span> across the person inbox and the records' mailboxes, as far as retention keeps it. <b>Back</b> (and <span class="mono">Esc</span>) returns to the list at the same row, ringed, with the filters as they were; <span class="mono">j</span> / <span class="mono">k</span> walk the filtered list without going back. A state row and a board row have no page: Open and Open board stay theirs. Reading marks nothing. No preview pane: at any width the entry opens as this page.</div>
</div>
</div>
</div>
''' + TAIL


def inbox_phone():
    """Screen 6 below 720 px (design §4.5 screen 6 *Narrow*, TD-129): the rail as a chip row and a filter sheet, 44 px controls."""
    def chip(label, n, on=False):
        st = "height: 32px; white-space: nowrap;" + (" background: #1c2128; color: #fff; border-color: #1c2128;" if on else "")
        return f'<span class="btn" style="{st}">{label}<span class="mono" style="font-size: 12px; opacity: .8;">{n}</span></span>'
    def pcard(bar, kind, name, team, age, body, buttons, state=None):
        btns = "".join(f'<span class="btn{" primary" if i == 0 and len(buttons) > 1 else ""}" style="height: 44px; flex-grow: 1; justify-content: center;">{l}</span>' for i, l in enumerate(buttons))
        badge = f'<span class="badge">{team}</span>' if team else '<span class="badge" style="border-style: dashed;">no team</span>'
        who = pill(*state) if state else f'<span class="kind">{kind}</span>'
        return f'''<div class="mcard" style="padding: 12px 12px 12px 15px;"><span class="sbar" style="background: {bar};"></span>
  <div class="who">{who}<span class="nm" style="font-size: 14px;">{name}</span>{badge}<span style="flex-grow: 1;"></span><span class="meta">{age}</span></div>
  {body}
  <div style="display: flex; gap: 8px; margin-top: 2px;">{btns}</div>
</div>'''
    cards = [
        pcard(BAR["needs"], "", "tdgrind-ao-2", "ao-grind", "4m 20s left",
              '<div class="txt">Permission · <span class="mono">Bash</span> · <span class="mono">git push origin td073-usage-chips</span></div>',
              ["Allow", "Deny", ICON["focus"]], state=("needs", "permission")),
        pcard(BAR["needs"], "ask", "tdgrind-ao-1", "ao-grind", "18m ago",
              '<div class="txt">The trail coalesces by <span class="mono">{sid, kind, how}</span>. A permission answered from Focus and one from the Inbox within five seconds — two trail rows, or one? <a href="#" style="color: #1f5fa8;">whole entry →</a></div>',
              ["Reply", "Snooze ▾"]),
    ]
    return head("Inbox — phone") + f'''<div style="width: 390px; min-height: 1000px; background: #f4f5f7; display: flex; flex-direction: column;">
<div class="topbar" style="padding: 0 14px; gap: 10px; height: 52px;"><span class="wordmark">Shift<b>Lead</b></span><span class="tab on" style="height: 28px;">Inbox 5 · 2</span><span style="flex-grow: 1;"></span><span class="btn primary" style="height: 32px; width: 32px; padding: 0; justify-content: center;">{ICON["plus"]}</span></div>
<div style="padding: 12px 12px 20px; display: flex; flex-direction: column; gap: 10px;">
  <div style="display: flex; align-items: center; gap: 8px;"><span style="font-size: 16px; font-weight: 600;">Inbox</span><span class="muted" style="font-size: 12px;">5 need you</span></div>
  <div style="display: flex; gap: 6px;">{chip("Filters ▾", "2")}<div style="display: flex; gap: 6px; overflow: hidden; min-width: 0;">{chip("ao-grind", "2 of 2", on=True)}{chip("cm-grind", "0 of 1")}{chip("no team", "0 of 2")}{chip("guardians", "0 of 0")}</div></div>
  {isec("Needs you", "2 of 5")}
  {"".join(cards)}
  <div class="muted" style="padding: 4px 2px 0; font-size: 12px;">Steering · Waiting on them · FYI are not picked — <a href="#">Filters ▾</a> or <a href="#">Clear filters</a>.</div>
  <div class="note" style="padding-top: 10px; border-top: 1px solid #dfe3e8; margin-top: 8px;">Design notes, not page text. <b>Narrow</b> (below 720 px, TD-129): the rail is not drawn; a chip row of the teams from the rail's own Teams list, with their <i>Needs you</i> counts, the picked ones first so a pick never scrolls out of sight, behind one pinned <b>Filters ▾</b> chip carrying the number of picks that opens a full-screen sheet holding the three groups as the same toggles, the find box, <b>Clear filters</b> and <b>Done</b> — the filter screen in practice, not a page of its own, since the picks are the list's URL. The chips and the sheet are the rail's toggles drawn twice from one list. The URL is the desktop's for the same picks. One column; controls 44 px high as the phone's Org cards. A long entry's text is cut with <i>whole entry →</i>, which is the message page — there is no pane at any width.</div>
</div>
</div>
''' + TAIL


def type_scale():
    """The type scale (design §4.5 *Type scale*, TD-130): six tokens and where each is used, at the size it draws."""
    rows = [
        ("--t-body", "14 px · 1.5", "IBM Plex Sans", "Everything a person reads: body text, a mail body, a board line, a question, an input.", "font-size: 14px; line-height: 1.5;"),
        ("--t-title", "16 px", "JetBrains Mono 600 · Plex 600", "A card's name, a page's title, a group head — tdgrind-ao-1 · Inbox", "font-size: 16px; font-weight: 600;"),
        ("--t-btn", "13 px", "IBM Plex Sans 500", "A button's label — Reply · Snooze ▾ · Open board", "font-size: 13px; font-weight: 500;"),
        ("--t-small", "12 px", "IBM Plex Sans", "Small print in lower case: an age, a due word, a count, a caption, a toast — never smaller than this.", "font-size: 12px;"),
        ("--t-mono", "13 px · 1.55", "JetBrains Mono", "The terminal and mono reading text — $ pdm run test … 212 passed in 41.3s", "font-family: 'JetBrains Mono', monospace; font-size: 13px; line-height: 1.55;"),
        ("--t-mono-s", "12.5 px", "JetBrains Mono", "Mono small print: doing 3m ago: TD-073 — pushing for review · asked 40s ago · 4m 20s left", "font-family: 'JetBrains Mono', monospace; font-size: 12.5px; color: #6b7280;"),
        ("--t-cap", "11 px · caps", "IBM Plex Sans 600, tracked", "UPPERCASE MARKS ONLY — PERMISSION · ASK · NEEDS YOU · TEAM · KIND", "font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #6b7280;"),
    ]
    trs = "".join(f'<tr><td class="mono" style="font-size: 12.5px; color: #374151; white-space: nowrap; padding: 12px 16px 12px 0; vertical-align: top;">{t}</td><td style="font-size: 12px; color: #6b7280; white-space: nowrap; padding: 12px 16px 12px 0; vertical-align: top;">{px}<br><span style="font-size: 11px;">{face}</span></td><td style="{st} padding: 12px 0; border-bottom: 1px solid #eceef1;">{sample}</td></tr>' for t, px, face, sample, st in rows)
    before = '<span style="font-size: 13px;">13 px body</span> · <span style="font-size: 12px;">12 px mail body</span> · <span style="font-size: 11px;">11 px small print</span> · <span style="font-size: 10px; text-transform: uppercase; letter-spacing: .04em; font-weight: 600;">10 px pill</span>'
    return head("Type scale") + f'''<div style="width: 1440px; min-height: 760px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Org")}
<div style="padding: 20px 28px 28px; max-width: 1100px;">
  <div style="font-size: 16px; font-weight: 600;">Type scale</div>
  <div class="muted" style="font-size: 12px; margin-top: 4px;">six tokens on <span class="mono">:root</span>, theme-independent; no rule outside the token block names a pixel size (design §4.5 <i>Type scale</i>, TD-130)</div>
  <table style="border-collapse: collapse; margin-top: 16px; width: 100%;">{trs}</table>
  <div style="margin-top: 20px; padding: 12px 14px; background: #fff; border: 1px solid #dfe3e8; border-radius: 6px;">
    <div style="font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #6b7280;">before, for the eye</div>
    <div style="margin-top: 6px;">{before}</div>
  </div>
  <div class="note" style="padding-top: 10px; border-top: 1px solid #dfe3e8; margin-top: 16px;">Design notes, not page text. The reading text moves to the base size (14) instead of sitting below it; small print is 12 and never smaller in lower case; capitals at 11 read as lower case does at 12.5, so the tracked marks keep one step down. Mono sits one pixel under the sans it shares a line with, since JetBrains Mono's x-height is the larger. Heights follow the scale in <span class="mono">em</span> — the card stays six rows of one height and grows with its text; a button is 30, a small one 26, an input 34. No density setting: the browser's zoom, remembered per site, is one already. Every other artboard on this canvas is regenerated at the scale.</div>
</div>
</div>
''' + TAIL


def settings_page():
    """Screen 8, Settings (design §4.5, TD-100 (4), 2026-09-25): the page that writes a setting and shows every other value with where it lives."""
    b = lambda label, c="": f'<span class="btn sm {c}">{label}</span>'
    gap = '<span style="flex-grow: 1;"></span>'
    imark = '<span class="info" title="where this comes from">i</span>'
    def field(label, value, hint="", w=110):
        h = f'<span class="muted mono" style="font-size: 12px;">{hint}</span>' if hint else ""
        return f'<span style="display: inline-flex; align-items: center; gap: 8px; margin-right: 18px;"><span class="muted" style="font-size: 12px;">{label}</span><span class="input" style="width: {w}px; height: 28px; display: inline-flex; align-items: center; padding: 0 8px; font-size: 13px;">{value}</span>{h}</span>'
    def ro(label, value, dflt=False):
        d = '<span class="muted" style="font-size: 12px;">default</span>' if dflt else ""
        return f'<div style="display: flex; gap: 10px; align-items: baseline; padding: 3px 0;"><span class="muted mono" style="font-size: 12.5px; width: 150px; flex: none;">{label}</span><span style="font-size: 14px;">{value}</span>{d}</div>'
    def card(head, badge, body, controls, note=""):
        return f'''<div class="mcard"><span class="sbar" style="background: #cbd0d6;"></span>
  <div class="who"><span class="nm">{head}</span>{f'<span class="badge">{badge}</span>' if badge else ""}<span style="flex-grow: 1;"></span>{note}</div>
  {body}
  <div class="ctl">{controls}</div>
</div>'''
    def sec(title, count, open_i=False, blurb=""):
        pop = f'<div class="pop">{blurb}</div>' if open_i else ""
        return f'<div class="isec-h"><span>{title}</span><span class="n">{count}</span><span class="info{" on" if open_i else ""}">i</span><span style="flex-grow: 1;"></span>{pop}</div>'
    usage = card("grind", "account paul · Claude", 
        '<div class="txt">' + field("5h reserve", "30", "→ line 70%") + field("week reserve", "10/day", "→ line 60% · 4 days left · moves Thu 07:00") + '</div>'
        '<div class="meta">applies on the next tick · the chip shows the line</div>',
        b("Save", "primary"))
    usage2 = card("paul", "account paul · Claude",
        '<div class="txt">' + field("5h reserve", "", "no line") + field("week reserve", "", "no line") + '</div>',
        b("Save", "primary"))
    usage3 = card("grind-api", "account api-key · Claude · metered · $3 in / $15 out per M",
        '<div class="txt">' + field("day amount", "$5", "spent $3.20 · 64% · resets 00:00", 80) + field("week amount", "$20", "spent $11.80 · 59% · resets Mon 00:00", 80) + field("month amount", "", "no line · spent $31.40", 80) + '</div>'
        '<div class="meta">applies on the next tick · the chip reads day $3.20 / $5 · the spend is the account’s, the amounts this profile’s</div>',
        b("Save", "primary") + gap + '<span class="muted" style="font-size: 12px;">prices in profiles.yml · by hand — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    teams = card("ao-grind", "team",
        '<div class="txt">' + field("stop time", "06:00", "Fri 06:00 · every member and seat") + field("reserve priority", "10", "+10 on grind’s reserve → line 60% for this team", 60) + '</div>'
        '<div class="txt muted" style="opacity: .6;">' + field("schedule", "at the reset of grind’s week", "not built — TD-133", 220) + '</div>',
        b("Save", "primary") + b("Clear stop time") + gap + '<span class="muted" style="font-size: 12px;">defined in org.yml — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    repos = card("agentorc", "repo · /home/kmaster/agentorc",
        '<div class="txt">' + field("promote auto", "off", "the Inbox row offers the press; on: the home promotes 10 min after main moves", 60) + '</div>'
        + ro("ledger", "docs/technical_debt.md") + ro("roles", "grinder, techlead, manager, designer · review: techlead reads src/sessionorc/**, docs/briefs/**") + ro("promote.run", "pip install --upgrade … && ao service install") + ro("promote.check", "sessionorc.build.info()[\"commit\"]"),
        b("Save", "primary") + gap + '<span class="muted" style="font-size: 12px;">.agentorc.yml · read per call · by PR — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    you = card("yours everywhere", "",
        '<div class="txt">' + field("editor", "vscode", "the card’s and Focus’s button; none removes it") + '</div>'
        '<div class="txt">' + field("terminal size", "13", "px", 60) + field("terminal face", "JetBrains Mono", "monospace always the fallback · ligatures off", 160) + '</div>',
        b("Save", "primary"))
    browser = card("this browser", "",
        ro("theme", "dark · the ◐ toggle in the top bar") + ro("mine", "off") + ro("Inbox folds", "FYI open · Answered open") + ro("shell directory", "~"),
        b("Reset this browser", "ghost danger") + gap + '<span class="muted" style="font-size: 12px;">remembered in this browser only</span>')
    hosts = card("kmaster", "home",
        ro("name", "kmaster") + ro("vscode_host", "kmaster") + ro("volatile", "false", dflt=True) + ro("repos_registry", "~/.config/dev-cadence/repos.txt", dflt=True) + ro("runs_keep_days", "30", dflt=True) + ro("identity", "enforce") + ro("nodes", "contractmatch (container, volatile)"),
        gap + '<span class="muted" style="font-size: 12px;">hosts.yml · name, home and identity read at start — <b>restart the host agent to apply</b> — the rest per request · by hand — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    profiles = card("grind", "default",
        ro("adapter", "claude-code", dflt=True) + ro("account", "paul") + ro("model", "fable-5-1") + ro("config_dir", "~/.claude-grind") + ro("permission_wait", "600", dflt=True),
        gap + '<span class="muted" style="font-size: 12px;">profiles.yml · read per call · by hand — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    org = card("ao-grind", "team · projects: agentorc",
        ro("manager", "manager-ao-1 · unattended · control") + ro("techlead", "techlead-ao-1") + ro("members", "grinder ×2 · designer ×1") + ro("host", "kmaster", dflt=True),
        gap + '<span class="muted" style="font-size: 12px;">org.yml · read per call by the clients, never the agent · by hand — <a href="#" style="color: #1f5fa8;">Open file</a></span>')
    return head("Settings") + f'''<div style="width: 1440px; min-height: 2320px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Settings")}
<div style="padding: 16px 20px 28px;">
<div class="inboxcol">
  {page_head("Settings", "")}
  {sec("Usage", 3, True, "The reserves the usage gate pauses a profile’s unattended sessions at: what you keep back of each window for your own work, per profile, per window as the tool names them. Grouped by account, since the reading is the account’s. A metered profile takes an amount per window instead — money with prices, tokens without — against the account’s spend. A change applies on the next tick; the line beside each field is what the chip will show. Set from here, from ao gate, never by hand.")}
  {usage}{usage2}{usage3}
  {sec("Teams", 1)}
  {teams}
  {sec("Repos", 1)}
  {repos}
  {sec("You", 2)}
  {you}{browser}
  {sec("Hosts", 1)}
  {hosts}
  {sec("Profiles", 2)}
  {profiles}
  {sec("Org", 1)}
  {org}
  <div class="note" style="padding-top: 10px; border-top: 1px solid #dfe3e8; margin-top: 8px;">Design notes, not page text. <b>Screen 8, Settings</b> (TD-100 (4), 2026-09-25): the one page that writes a setting — a value a person turns without redefining anything — and shows every other configured value with where it lives. The line: a <i>definition</i> (what a team, a repo, a host or a profile is) stays in its file and is read-only here, each with an <i>i</i> mark naming the file, when it is re-read and whether a change needs the host agent restarted, and an <b>Open file</b> button through the editor setting; a <i>setting</i> lives in the home’s <span class="mono">settings.yml</span> and is written only through <span class="mono">set_settings</span>. Usage first, because the reserves are what Paul moves weekly, and a metered profile’s card (TD-128, reconciled 2026-09-25) takes amounts — the window’s 100 — against the account’s spend; Teams hold the three per-team settings (schedule — disabled until TD-133 — stop time, reserve priority); Repos hold the promote’s <i>auto</i>, moved out of the checked-in file; You in two halves by write path. On a node every editable value reads <i>set at kmaster</i> and writes forward to the home. Nothing here draws a constant, the terminal’s colours, or a density switch.</div>
</div>
</div>
</div>
''' + TAIL


DARK = [
 ("background: #f4f5f7; color: #1c2128;", "background: #0e1116; color: #d7dce3;"),
 ("#f4f5f7", "#0e1116"), ("background: #fff;", "background: #171b22;"), ("#dfe3e8", "#2a313b"), ("#eceef1", "#242a33"),
 ("#111418", "#f1f3f6"), ("#1c2128", "#e6e9ee"), ("#374151", "#c3c9d2"), ("#4b5563", "#9aa3b0"), ("#6b7280", "#7d8794"), ("#cbd0d6", "#3a424d"),
 (".topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #e6e9ee;", ".topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #05070a;"),
 (".btn.primary { background: #e6e9ee; color: #fff; border-color: #e6e9ee; }", ".btn.primary { background: #e6e9ee; color: #0e1116; border-color: #e6e9ee; }"),
 (".tab.on { background: #2b323b; color: #fff; }", ".tab.on { background: #2b323b; color: #fff; }"),
 ("#fffbeb", "#2a2410"), ("#fde68a", "#6b4d00"), ("#f5f3ff", "#221a33"), ("#ddd6fe", "#4c3a80"),
 ("#dcfce7", "#123d27"), ("#166534", "#bbf7d0"), ("#dbeafe", "#172554"), ("#1e40af", "#93c5fd"), ("#e5e7eb", "#2a313b"), ("#fecaca", "#4a1414"), ("#991b1b", "#fca5a5"), ("#d1fae5", "#0b3b2a"), ("#065f46", "#6ee7b7"), ("#ede9fe", "#2e1f5c"), ("#5b21b6", "#c4b5fd"), ("#7c3d00", "#fcd34d"),
 ("background: #0f1419;", "background: #05070a;"), ("background: #f3f4f6;", "background: #1f242c;"),
 ("background: #e5e7eb; color: #f1f3f6;", "background: #3a424d; color: #f1f3f6;"),
]
def darken(html):
    for a, b in DARK:
        html = html.replace(a, b)
    return html

files = {
    "Main.dc.html": team_desktop(),
    "MainDark.dc.html": darken(team_desktop()),
    "Phone.dc.html": team_phone(),
    "Focus.dc.html": focus(),
    "FocusOrc.dc.html": focus_orchestrator(),
    "NewSession.dc.html": new_session(),
    "Legend.dc.html": legend(),
    "Resumable.dc.html": resumable(),
    "Commands.dc.html": commands(),
    "Inbox.dc.html": inbox(),
    "InboxRail.dc.html": inbox(picks=True),
    "InboxMessage.dc.html": inbox_message(),
    "InboxPhone.dc.html": inbox_phone(),
    "Type.dc.html": type_scale(),
    "TypeDark.dc.html": darken(type_scale()),
    "Settings.dc.html": settings_page(),
    "SettingsDark.dc.html": darken(settings_page()),
}
for n, s in files.items():
    (OUT / n).write_text(s)

# The canvas layout is computed, never typed: an artboard's height comes from the file it was just
# written to, and each column stacks with a fixed gutter. Two artboards drew on top of each other
# when Focus grew and a second Focus was inserted (found by the PR #127 review) — a hand-kept `y` is
# a bug waiting for the next screen to change size.
LAYOUT = [
    # (file, title, column)
    ("Main.dc.html", "Org — desktop", 0),
    ("Inbox.dc.html", "Inbox", 0),
    ("InboxRail.dc.html", "Inbox — filtered", 0),
    ("InboxMessage.dc.html", "Inbox — message", 0),
    ("Focus.dc.html", "Focus — member", 0),
    ("FocusOrc.dc.html", "Focus — orchestrator", 0),
    ("Legend.dc.html", "States & badges", 0),
    ("Resumable.dc.html", "Resumable", 0),
    ("Phone.dc.html", "Org — phone", 1),
    ("InboxPhone.dc.html", "Inbox — phone", 1),
    ("NewSession.dc.html", "New session", 1),
    ("Commands.dc.html", "Commands", 1),
    ("Settings.dc.html", "Settings", 1),
    ("SettingsDark.dc.html", "Settings — dark", 2),
    ("MainDark.dc.html", "Org — dark", 2),
    ("Type.dc.html", "Type scale", 2),
    ("TypeDark.dc.html", "Type scale — dark", 2),
]
COLUMN_X = {0: 0, 1: 1540, 2: 3120}
GUTTER = 100


def artboards():
    """Every artboard placed from its own `width` / `min-height`, column by column."""
    out, y = [], dict.fromkeys(COLUMN_X, 0)
    for name, title, col in LAYOUT:
        text = (OUT / name).read_text()
        m = re.search(r"width: (\d+)px; min-height: (\d+)px", text)
        w, h = (int(m[1]), int(m[2])) if m else (1440, 900)
        out.append({"file": name, "title": title, "x": COLUMN_X[col], "y": y[col], "w": w, "h": h})
        y[col] += h + GUTTER
    return out


canvas = {
    "artboards": artboards(),
    "annotations": [
        {"id": "brief", "x": 0, "y": -150, "w": 520, "text": "agentorc mockups (2026-09-04, static, utilitarian operator console).\nRound 2: card grid chosen; profile line (tool · account · model) replaces the source column; new LIMITED state; attention/pinned sort toggle.\nRound 3: 'Done when' → 'Ready to close' + user-driven Close → closed state; dark artboard added; laptop shown as a volatile host (◐).\nRound 4: Resumable, Commands and Attention tabs added; then the consistency pass — renamed agentorc, Urgent-first sort + Due strip, shells as cards (host1, vpnmaster), command runs off the Org, unreachable host banner, permissions via hook (no answer buttons under the terminal), Adopt for hand-started sessions.\nRound 6 (2026-09-21, TD-095): the Org card redrawn to §4.5 *The card's anatomy* — six rows at one height, the name once, one clock, the mode a word (interactive marked, unattended quiet), the slot one text with *ready to close ✓* as its caption, the quiet foot led by the next act; working green, idle blue, one grey for everything over; a team's header without its manager; the legend gains the state tokens.\nRound 9 (2026-09-25, TD-100 (4)): screen 8, Settings, in both themes.\nRound 8 (2026-09-25, TD-130): the type scale — body 14, small 12, caps 11, mono 13 / 12.5 — applied to every artboard, with a ladder artboard in both themes.\nRound 7 (2026-09-24, TD-129): the Inbox gains the rail — sections, teams and kinds as toggles with counts, the find box — and three artboards beside it: the rail with picks, the message page, the phone layout.\nRound 5 (2026-09-13, TD-037): caught up with a week of shipped UI — the home screen is the Org, not the Team; team groups with a header per team (lead, project, needs-you) and the card's team / role badges, under chip and report line; the Teams strip with Start / Stop; Focus gains the grants and controllers chips and the Reports panel, and a second Focus artboard draws the lead's Members list, which only a session holding `orchestrate` ever sees; New session is redrawn field for field from the shipped form — the four-way Where radio group with its existing-worktree picker and the Fresh/Resume pair never existed."},
    ],
    "launch": {"view": "canvas"},
}
(OUT / "canvas.json").write_text(json.dumps(canvas, indent=2))
print("wrote", ", ".join(files), "canvas.json")
