#!/usr/bin/env python3
"""Emit the agentorc mockup artboards (.dc.html) + canvas.json from one shared style."""
import json, pathlib

OUT = pathlib.Path(__file__).parent

CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
  body { margin: 0; background: #f4f5f7; color: #1c2128; font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; font-size: 13px; line-height: 1.4; }
  a { color: #1f5fa8; text-decoration: none; } a:hover { color: #164a85; text-decoration: underline; }
  .mono { font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace; }
  .pill { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; border-radius: 3px; font-size: 10px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; white-space: nowrap; line-height: 1; }
  .pill .dot { display: none; } .pill.s-needs .dot, .pill.s-limited .dot, .pill.s-stalled .dot { display: block; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .s-working { background: #dbeafe; color: #1e40af; }
  .s-needs { background: #fde68a; color: #7c3d00; }
  .s-idle { background: #e5e7eb; color: #4b5563; }
  .s-stalled { background: #fecaca; color: #991b1b; }
  .s-exited { background: #e5e7eb; color: #6b7280; }
  .s-done { background: #d1fae5; color: #065f46; }
  .s-limited { background: #ede9fe; color: #5b21b6; }
  .s-unreachable { background: #e5e7eb; color: #6b7280; }
  .card.off { opacity: .55; }
  .due { display: flex; align-items: center; gap: 10px; padding: 6px 10px; }
  .due + .due { border-top: 1px solid #eceef1; }
  .pill.scraped { outline: 1px dashed #d9a441; outline-offset: 1px; }
  .badge { display: inline-block; padding: 1px 5px; border: 1px solid #cbd0d6; border-radius: 3px; font-size: 10px; color: #5b6470; font-family: "JetBrains Mono", monospace; }
  .badge.scraped { border-style: dashed; color: #8a5a00; border-color: #d9a441; }
  .badge.toggle { cursor: pointer; padding-left: 4px; white-space: nowrap; }
  .badge.toggle::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; border: 1px solid currentColor; margin-right: 4px; vertical-align: 0; }
  .badge.toggle.on { color: #fff; background: #1c2128; border-color: #1c2128; }
  .badge.toggle.on::before { background: #fff; border-color: #fff; }
  .btn { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border: 1px solid #cbd0d6; border-radius: 4px; background: #fff; color: #1c2128; font-size: 12px; font-weight: 500; white-space: nowrap; }
  .btn.primary { background: #1c2128; color: #fff; border-color: #1c2128; }
  .btn.danger { color: #991b1b; border-color: #e5b4b4; }
  .btn.ghost { border-color: transparent; background: transparent; color: #4b5563; }
  .btn.ghost:hover { background: #eef0f3; }
  .status { display: block; padding: 3px 0 3px 10px; border-left: 2px solid #cbd0d6; font-family: "JetBrains Mono", monospace; font-size: 11.5px; color: #4b5563; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .status.ok { border-color: #059669; } .status.bad { border-color: #dc2626; } .status.lim { border-color: #7c3aed; }
  .btn svg { width: 14px; height: 14px; flex-shrink: 0; }
  .flag svg { width: 13px; height: 13px; flex-shrink: 0; }
  svg { width: 14px; height: 14px; }
  .flag { display: inline-flex; align-items: center; gap: 4px; color: #991b1b; font-size: 11px; font-weight: 500; white-space: nowrap; flex-shrink: 0; }
  .card { background: #fff; border: 1px solid #dfe3e8; border-radius: 6px; }
  .sc { display: flex; flex-direction: column; gap: 16px; padding: 16px; overflow: hidden; position: relative; }
  .sc-body { display: flex; flex-direction: column; gap: 8px; }
  .sc-slot { height: 54px; display: flex; flex-direction: column; gap: 8px; justify-content: flex-start; }
  .sc-foot { display: flex; gap: 6px; align-items: center; }
  .sc .name { font-family: "JetBrains Mono", monospace; font-size: 16px; font-weight: 600; color: #111418; }
  .meta { font-family: "JetBrains Mono", monospace; font-size: 11.5px; color: #6b7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sbar { position: absolute; left: 0; top: 0; bottom: 0; width: 3px; }
  .btn.sm { height: 24px; font-size: 11px; padding: 0 8px; }
  .btn.sm svg { width: 12px; height: 12px; }
  .term.tail { height: 100%; box-sizing: border-box; font-size: 11px; line-height: 1.55; padding: 6px 9px; color: #aab3bf; border-radius: 4px; }
  .topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #1c2128; color: #e6e9ee; }
  .wordmark { font-family: "JetBrains Mono", monospace; font-weight: 500; font-size: 15px; letter-spacing: -.01em; }
  .wordmark b { color: #9ec5ff; font-weight: 500; }
  .tab { padding: 6px 10px; border-radius: 4px; color: #aab3bf; font-weight: 500; }
  .tab.on { background: #2b323b; color: #fff; }
  table { border-collapse: collapse; width: 100%; }
  th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #dfe3e8; }
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
  .field label { font-size: 11px; font-weight: 600; color: #4b5563; text-transform: uppercase; letter-spacing: .04em; }
  .input { height: 32px; padding: 0 10px; border: 1px solid #cbd0d6; border-radius: 4px; background: #fff; display: flex; align-items: center; justify-content: space-between; font-size: 13px; }
  .radio { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid #dfe3e8; border-radius: 4px; background: #fff; }
  .radio.on { border-color: #1c2128; box-shadow: inset 0 0 0 1px #1c2128; }
  .rb { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid #6b7280; box-sizing: border-box; }
  .radio.on .rb { border: 4.5px solid #1c2128; }
  .note { font-size: 12px; color: #6b7280; }
  .rs { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: #374151; }
  .rs > div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .rs-k { display: inline-block; width: 52px; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; font-weight: 600; }
  .switch { display: inline-flex; align-items: center; width: 34px; height: 20px; border-radius: 10px; background: #cbd0d6; padding: 2px; box-sizing: border-box; flex-shrink: 0; }
  .switch .knob { width: 16px; height: 16px; border-radius: 50%; background: #fff; }
  .switch.on { background: #1c2128; justify-content: flex-end; }
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

BAR = {'needs': '#f59e0b', 'limited': '#7c3aed', 'stalled': '#dc2626', 'working': '#2563eb', 'idle': '#9ca3af', 'exited': '#9ca3af', 'done': '#059669', 'unreachable': '#9ca3af'}
RANK = {"needs": 0, "limited": 1, "stalled": 2, "working": 3, "idle": 4, "exited": 5, "done": 6}
VOLATILE = {"laptop"}
def rank(host, state):
    if state == "unreachable":
        return 4 if host in VOLATILE else 2.5
    return RANK[state]

def pill(state, label=None, scraped=False):
    names = {"working": "working", "needs": "needs you", "idle": "idle", "stalled": "stalled?", "exited": "exited", "done": "closed", "limited": "limited", "unreachable": "unreachable"}
    return f'<span class="pill s-{state}{" scraped" if scraped else ""}"><span class="dot"></span>{label or names[state]}</span>'

def head(title):
    return f'<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>{CSS}</helmet>\n'

TAIL = "</x-dc>\n</body>\n</html>\n"

def topbar(active="Herd", narrow=False):
    tabs = "".join(f'<span class="tab{" on" if t == active else ""}">{t}</span>' for t in ["Herd", "Resumable", "Commands", "Attention"])
    return f'''<div class="topbar">
  <span class="wordmark">agent<b>orc</b></span>
  <div style="display: flex; gap: 2px;">{tabs}</div>
  <div style="flex-grow: 1;"></div>
  {"" if narrow else '<span class="mono" style="font-size: 11px; color: #aab3bf;">usage 5h 41% · wk 58%</span><span class="mono" style="font-size: 11px; color: #aab3bf;">hosts: kmaster ● vps ● host1 ● vpnmaster ● laptop ◐</span>'}
  <span class="btn" style="height: 26px; background: transparent; color: #e6e9ee; border-color: #4b5563;">{ICON["term"]}Shell</span><span class="btn primary" style="height: 26px;">{ICON["plus"]}New session</span>
</div>'''

# ---------------- sessions data (realistic, from Paul's setup) ----------------
SESS = [
    ("kmaster", "samscrape", "/home/kmaster/samscrape", [
        ("main", "claude-code · paul (max) · opus", "needs", "2m", "main", "", "hook", "Permission: Bash · git push origin td301-fix", ""),
        ("tdgrind-1", "claude-code · grind (pro) · sonnet", "working", "14s", "wt/tdgrind-1 → td-301", "", "hook", "", "unattended"),
        ("tdgrind-2", "claude-code · grind (pro) · sonnet", "stalled", "47m", "wt/tdgrind-2 → td-296", "3 unpushed", "hook", "no output 47m · creds expire in 0.2h", "unattended"),
        ("tdgrind-3", "claude-code · grind (pro) · sonnet", "limited", "9m", "wt/tdgrind-3 → td-290", "", "hook", "5h window at 100% · resets 02:00 MDT (1h 51m)", "unattended"),
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
  <td><span class="mono" style="font-size: 11px; color: #4b5563;">{where}</span></td>
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
            rows += f'<div class="due" style="padding: 8px 10px; flex-wrap: wrap;"><span class="pill {cls}">{due}</span><span style="font-size: 12px; flex-basis: 100%;">{text}</span><span class="meta">{repo} · {sess}</span><span style="flex-grow: 1;"></span><span class="btn sm">Snooze ▾</span><span class="btn sm">Done</span></div>'
        else:
            rows += f'<div class="due"><span class="pill {cls}" style="width: 78px; justify-content: center;">{due}</span><span style="font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{text}</span><span class="meta" style="flex-shrink: 0;">{repo} · session <a href="#">{sess}</a></span><span style="flex-grow: 1;"></span><span class="btn sm ghost">Snooze ▾</span><span class="btn sm ghost">Done</span></div>'
    return f'''<div class="card" style="display: flex; flex-direction: column;">
    <div class="due" style="padding: 8px 10px; border-bottom: 1px solid #dfe3e8;"><span style="font-weight: 600;">Due</span><span class="muted">4 overdue · 2 today · from the dev-cadence boards</span><span style="flex-grow: 1;"></span><a href="#" style="font-size: 12px;">full board →</a><span class="btn sm ghost" style="padding: 0 4px;">▾</span></div>
    {rows}
  </div>'''

def herd_desktop():
    def card(host, repo, s):
        name, tool, state, age, where, flag, conf, pending, tag = s
        tag_html = f'<span class="badge toggle on" title="click: switch to interactive">{tag}</span>' if tag else ""
        flag_html = f'<span class="flag">{ICON["warn"]}{flag}</span>' if flag else ""
        if state == "needs":
            slot = f'<div class="status" style="border-color: #f59e0b; color: #7c3d00;">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm primary">Allow</span><span class="btn sm">Deny</span><span class="meta" style="align-self: center;">via hook · 9m 12s left</span></div>'
        elif state == "limited":
            slot = f'<div class="status lim">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm">Switch profile…</span><span class="btn sm ghost">Wait</span></div>'
        elif state in ("working", "stalled"):
            tail = pending if pending else "⏺ Edit(scripts/recover_stuck_notices.py)\n▌"
            slot = f'<div class="term tail">{tail}</div>'
        elif state == "done":
            slot = f'<div class="status ok">{pending}</div>'
        elif state == "unreachable":
            slot = f'<div class="status">{pending}</div>'
        elif state == "exited":
            slot = f'<div class="status {"bad" if pending.startswith("not done") else ""}">{pending}</div>'
        elif pending.startswith("ready"):
            slot = f'<div class="status ok">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm">Close session</span></div>'
        elif tool == "shell":
            slot = f'<div class="status">last: $ wg show wg0 · at prompt</div>'
        else:
            slot = f'<div class="status">last: ⏺ Edit(scripts/recover_stuck_notices.py)</div>'
        border = "#f59e0b" if state == "needs" else "#dfe3e8"
        place = f"{host} / {repo}" if repo else f"{host} / {where}"
        return f'''<div class="card sc{" off" if state == "unreachable" else ""}" style="border-color: {border};">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div class="sc-body">
    <div style="display: flex; align-items: center; gap: 8px;"><span class="name">{name}</span>{tag_html}<span style="flex-grow: 1;"></span>{pill(state, scraped=(conf == "scraped"))}</div>
    <div style="display: flex; align-items: center; gap: 8px;"><span class="meta" style="color: #374151;">{place}</span><span style="flex-grow: 1;"></span><span class="meta" style="flex-shrink: 0;">{age}</span></div>
    <div style="display: flex; align-items: center; gap: 8px;"><span class="meta" style="color: #374151;">{where}</span><span style="flex-grow: 1;"></span>{flag_html}</div>
    <div class="meta">{tool}</div>
  </div>
  <div class="sc-slot">{slot}</div>
  <div class="sc-foot"><span class="btn sm primary">{ICON["focus"]}Focus</span><span class="btn sm ghost">{ICON["code"]}VS Code</span><span style="flex-grow: 1;"></span><span class="btn sm ghost" style="padding: 0 4px;">{ICON["more"]}</span></div>
</div>'''
    ordered = []
    for host, repo, path, rows in SESS:
        for r in rows:
            ordered.append((host, repo, r))
    ordered.sort(key=lambda t: rank(t[0], t[2][2]))
    cards = "".join(card(h, r, s) for h, r, s in ordered)
    return head("Herd") + f'''<div style="width: 1440px; min-height: 1360px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  <div style="display: flex; align-items: center; gap: 10px;">
    <span style="font-size: 16px; font-weight: 600;">Herd</span>
    <span class="muted">12 sessions · </span>{pill("needs", "1 needs you")}{pill("limited", "1 limited")}{pill("stalled", "1 stalled")}
    <span style="flex-grow: 1;"></span>
    <span class="input" style="width: 200px; height: 28px; color: #9ca3af;">filter…</span>
    <span class="btn ghost">host: all ▾</span><span class="btn ghost">repo: all ▾</span><span class="btn ghost">profile: all ▾</span><span class="btn ghost" style="color: #9ca3af;">☐ show command runs (2)</span>
    <span style="width: 1px; height: 20px; background: #cbd0d6; margin: 0 8px;"></span>
    <span style="display: inline-flex; border: 1px solid #cbd0d6; border-radius: 4px; overflow: hidden;"><span class="btn" style="border: 0; border-radius: 0; background: #e5e7eb; color: #111418;">Urgent first</span><span class="btn" style="border: 0; border-radius: 0;">Pinned</span></span>
  </div>
  {due_strip()}
  <div class="warn" style="background: #f3f4f6; border-color: #cbd0d6; color: #374151; align-items: center;">{ICON["warn"]}<span><b>laptop</b> unreachable since 14:02 (volatile host, probably asleep) · 1 session · last states kept</span><span style="flex-grow: 1;"></span><span class="btn sm ghost">Retry</span></div>
  <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: start;">{cards}</div>
  <div class="note"><b>Urgent first</b>: needs you → limited → stalled? → working → idle → exited → closed; an unreachable host sorts with idle when it is volatile, after stalled? when it is not. <b>Pinned</b> keeps every card where you dragged it and highlights the ones that need you instead. A dashed outline on a state pill means the state was guessed from the screen (shells, tools without hooks). Allow / Deny answer the permission through the tool's hook, so the dialog never reaches the terminal unless the hook times out. Command runs (kind: command) are on the Commands tab and hidden here by default.</div>
</div>
</div>
''' + TAIL

def herd_phone():
    def card(host, repo, s):
        name, tool, state, age, where, flag, conf, pending, tag = s
        if state == "needs":
            pend = f'<div class="status" style="border-color: #f59e0b; color: #7c3d00; white-space: normal; margin-top: 8px;">{pending}</div>'
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn primary" style="height: 44px; flex-grow: 1; justify-content: center;">Allow</span><span class="btn" style="height: 44px; flex-grow: 1; justify-content: center;">Deny</span><span class="btn" style="height: 44px; width: 44px; justify-content: center;">{ICON["focus"]}</span></div><div class="meta" style="margin-top: 6px;">answered through the hook · 9m 12s before the terminal dialog takes over</div>'
        else:
            cls = {"limited": "lim", "done": "ok", "unreachable": ""}.get(state, "bad" if (state in ("stalled",) or pending.startswith("not done")) else "")
            pend = f'<div class="status {cls}" style="white-space: normal; margin-top: 8px;">{pending}</div>' if pending else ""
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn ghost" style="height: 44px; flex-grow: 1; justify-content: center; border-color: #dfe3e8;">{ICON["focus"]}Focus</span></div>'
        flag_html = f'<div class="flag" style="margin-top: 6px;">{ICON["warn"]}{flag}</div>' if flag else ""
        place = f"{host} / {repo} · {where}" if repo else f"{host} / {where}"
        return f'''<div class="card{" off" if state == "unreachable" else ""}" style="padding: 12px 12px 12px 14px; position: relative; overflow: hidden;">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div style="display: flex; align-items: center; gap: 8px;"><span class="name" style="font-family: 'JetBrains Mono', monospace; font-size: 15px; font-weight: 600;">{name}</span><span style="flex-grow: 1;"></span>{pill(state, scraped=(conf == "scraped"))}</div>
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
<div class="topbar" style="padding: 0 14px; gap: 10px; height: 52px;"><span class="wordmark">agent<b>orc</b></span><span style="flex-grow: 1;"></span><span class="mono" style="font-size: 11px; color: #aab3bf;">5h 41%</span><span class="btn primary" style="height: 32px; width: 32px; padding: 0; justify-content: center;">{ICON["plus"]}</span></div>
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
    return head("Focus") + f'''<div style="width: 1440px; min-height: 980px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 12px 20px; display: flex; gap: 14px; align-items: flex-start;">
  <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0;">
    <div style="display: flex; align-items: center; gap: 10px;">
      <a href="#" class="muted">← Herd</a>
      <span class="mono" style="font-size: 15px; font-weight: 500;">kmaster / samscrape / tdgrind-1</span>
      {pill("needs")}<span class="badge toggle on" title="click: switch to interactive">unattended</span>
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
        <dt>profile</dt><dd class="mono" style="font-size: 11px;">claude-code · grind (pro) · sonnet</dd>
        <dt>adapter id</dt><dd class="mono" style="font-size: 11px;">1c8e0b2f…f42a</dd>
        <dt>tmux</dt><dd class="mono">ao-samscrape-tdgrind-1</dd>
        <dt>started</dt><dd>2026-09-04 20:02 MDT · 3h 14m</dd>
        <dt>last output</dt><dd>14 s ago</dd>
        <dt>policy</dt><dd>window 20:00–06:00 · gate 70/70</dd>
        <dt>run log</dt><dd><a href="#">tdgrind-1-20260904.log</a> · 1.2 MB</dd>
      </dl>
    </div>
    <div class="card" style="padding: 12px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;"><span style="font-weight: 600;">Git</span><span class="mono muted" style="font-size: 11px;">td301-fix · 2 ahead of origin/main</span></div>
      <div class="mono" style="font-size: 11px; line-height: 1.7;">
        <div><span style="color: #065f46;">M</span> scripts/recover_stuck_notices.py</div>
        <div><span style="color: #065f46;">M</span> tests/test_scripts/test_recover_stuck_notices.py</div>
        <div><span style="color: #1f5fa8;">A</span> docs/claude-memory/project_td301.md</div>
      </div>
      <div style="display: flex; gap: 6px; margin-top: 10px;"><span class="btn" style="height: 24px; font-size: 11px;">diff</span><span class="btn" style="height: 24px; font-size: 11px;">log</span><span class="btn" style="height: 24px; font-size: 11px;">PRs</span></div>
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

def new_session():
    return head("New session") + f'''<div style="width: 720px; min-height: 960px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd", narrow=True)}
<div style="padding: 20px 24px; display: flex; flex-direction: column; gap: 16px;">
  <div style="font-size: 16px; font-weight: 600;">New session</div>
  <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px;">
    <div class="field"><label>Host</label><span class="input">kmaster<span class="muted">▾</span></span></div>
    <div class="field"><label>Repo or directory</label><span class="input">samscrape<span class="muted">▾</span></span><span class="note">a registry repo, or type a path (recent: ~/proxmox on host1, /etc/wireguard on vpnmaster)</span></div>
    <div class="field"><label>Adapter</label><span class="input">claude-code <span class="badge">state: hook</span></span><span class="note">or shell (no hooks, no worktrees)</span></div>
    <div class="field"><label>Name</label><span class="input mono">td-302</span></div>
  </div>
  <div class="field"><label>Where</label>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <div class="radio" style="opacity: .55;"><span class="rb"></span><span>Main checkout</span><span class="mono muted" style="font-size: 11px;">/home/kmaster/samscrape</span><span style="flex-grow: 1;"></span><span class="pill s-needs">in use by main</span></div>
      <div class="radio on"><span class="rb"></span><span>New worktree</span><span class="mono muted" style="font-size: 11px;">.claude/worktrees/td-302 from origin/main</span></div>
      <div class="radio"><span class="rb"></span><span>Existing worktree</span><span class="mono muted" style="font-size: 11px;">td-7 (exited) · td-5 (closed, reaped — recreated from its branch)</span></div>
      <div class="radio" style="opacity: .55;"><span class="rb"></span><span>errors-alerts</span><span class="mono muted" style="font-size: 11px;">idle, dirty</span><span style="flex-grow: 1;"></span><span class="pill s-idle">in use — resume from the Herd</span></div>
    </div>
    <div class="warn">{ICON["warn"]}<span>One agent session per directory. The main checkout already hosts <b>main</b>, so a second agent session there is refused, not warned about. Shells and command runs are exempt.</span></div>
  </div>
  <div class="field"><label>Start</label>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <div class="radio on"><span class="rb"></span><span>Fresh</span></div>
      <div class="radio"><span class="rb"></span><span>Resume</span><span class="mono muted" style="font-size: 11px;">pick from Resumable (24 transcripts on kmaster/samscrape)</span></div>
    </div>
  </div>
  <div class="field"><label>Opening prompt (optional)</label><span class="input" style="height: 72px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Paste the brief, or leave empty to start at the prompt.</span></div>
  <div class="field">
    <div class="radio" style="gap: 12px;"><span class="switch"><span class="knob"></span></span><div><div>Unattended</div><div class="note">off: interactive — never paused, nudged, or killed by a policy. on: run window + usage gate from .agentorc.yml apply; brief file required. Disabled for repos without an unattended block, hidden for directory sessions.</div></div></div>
  </div>
  <div style="display: flex; gap: 8px; justify-content: flex-end; padding-top: 6px;"><span class="btn">Cancel</span><span class="btn primary">Start session</span></div>
</div>
</div>
''' + TAIL

def legend():
    rows = [
        ("working", "A hook reported UserPromptSubmit / PreToolUse and output is still flowing."),
        ("needs", "Waiting on you: a permission (Allow / Deny answer it through the tool's hook; the terminal dialog only appears if the hook times out), a question (Focus — the terminal owns menus), or an empty prompt. Sorted to the top."),
        ("limited", "Hit a usage or token cap and is waiting on a reset. Reset time shown. Nothing you do unblocks it except switching the profile (account/model)."),
        ("idle", "Turn finished (Stop hook), nothing pending. Flagged if the tree is dirty or unpushed."),
        ("stalled", "Reported working, but no output for longer than the adapter's stall_after. How a credential lapse shows up."),
        ("exited", "Process ended or tmux session gone. Run log kept. Shows which ready-to-close checks failed."),
        ("done", "You clicked Close: session killed, worktree reaped, card kept a day then filed under Resumable. Only you close a session; the checklist just says when it is ready."),
        ("unreachable", "The host stopped answering, so every card on it flips at once and keeps its last known state, greyed. Sorts with idle on a volatile host (asleep laptop), after stalled? on one that should be up."),
    ]
    body = "".join(f'<tr><td style="width: 120px;">{pill(s)}</td><td>{d}</td></tr>' for s, d in rows)
    return head("Legend") + f'''<div style="width: 760px; min-height: 820px; background: #f4f5f7; padding: 20px 24px; box-sizing: border-box; display: flex; flex-direction: column; gap: 14px;">
  <div style="font-size: 16px; font-weight: 600;">States and badges</div>
  <div class="card"><table><tbody>{body}</tbody></table></div>
  <div class="card" style="padding: 12px; display: flex; flex-direction: column; gap: 8px;">
    <div style="display: flex; gap: 10px; align-items: center;">{pill("working", scraped=True)}<span>Dashed outline: state guessed from the last screen lines (tool without hooks, plain shells). Solid: reported by the tool's hooks.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 11px; color: #4b5563; white-space: nowrap;">claude-code · paul (max) · opus</span><span>Profile line: tool · account · model. Commands, policies, and usage gates key on the profile, so two accounts of one tool are tracked separately.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="badge toggle on">unattended</span><span class="badge toggle">interactive</span><span>Mode toggle, on every card and the Focus header: click flips the session between unattended (run window, usage gate, wrap-up-then-kill, credential checks apply from the next tick) and interactive (never paused, nudged, or killed by a policy). Cards show it only when on; Focus always.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 11px; color: #4b5563; white-space: nowrap;">shell</span><span>A shell is an adapter like any other: scraped state, no profile, exempt from the one-agent-per-directory rule, as are command runs. Predefined command runs are a separate kind and live on the Commands tab.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 11px; color: #4b5563; white-space: nowrap;">kmaster ● laptop ◐</span><span>Host chips in the top bar: ● reachable, ◐ volatile host currently unreachable (asleep), ○ non-volatile host unreachable (a problem).</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="flag">{ICON["warn"]}dirty · 2 unpushed</span><span>Stranded-work flag: idle or exited with uncommitted or unpushed changes.</span></div>
  </div>
</div>
''' + TAIL

def direction_b():
    """Low-fi alternate: card grid instead of table."""
    def c(name, state, sub):
        return f'<div class="card" style="padding: 10px; display: flex; flex-direction: column; gap: 6px;"><div style="display: flex; gap: 8px; align-items: center;"><span class="mono" style="font-weight: 500;">{name}</span><span style="flex-grow: 1;"></span>{pill(state)}</div><div class="muted" style="font-size: 11px;">{sub}</div><div class="term" style="height: 54px; font-size: 11px; padding: 6px 8px; color: #aab3bf;">⏺ Bash(pdm run test)\n  412 passed\n▌</div></div>'
    cards = "".join([
        c("samscrape/main", "needs", "kmaster · 2m · Permission: git push"),
        c("samscrape/tdgrind-1", "working", "kmaster · 14s"),
        c("samscrape/tdgrind-2", "stalled", "kmaster · 47m · 3 unpushed"),
        c("samscrape/errors-alerts", "idle", "kmaster · 3h · dirty"),
        c("contractmatch/main", "idle", "kmaster · 22m"),
        c("dev-cadence/attention-fix", "working", "vps · 1m · gemini"),
    ])
    return head("Alt") + f'''<div style="width: 1100px; min-height: 620px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  <div style="display: flex; align-items: center; gap: 10px;"><span style="font-size: 16px; font-weight: 600;">Herd</span><span class="muted">card grid with live tail — alternate to the table</span></div>
  <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px;">{cards}</div>
  <div class="note">Trade-off: you see the last lines of every session at once, but fewer sessions fit per screen and host/repo grouping is weaker. The table (Main) scales to 20+ sessions; this scales to ~9.</div>
</div>
</div>
''' + TAIL

# ---------------- Resumable / Commands / Attention (round 4) ----------------

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
            title = f'<a href="#" class="mono" style="font-weight: 500;">{nm}</a>'; slug_html = f'<span class="mono muted" style="font-size: 11px;">{sid}</span>'
        else:
            title = f'<a href="#" class="mono" style="font-weight: 500;">{sid}</a>'; slug_html = '<span class="muted" style="font-size: 11px;">no name yet</span>'
        board_html = f'<span class="flag" style="color: #7c3d00;">{ICON["warn"]}{board} on board</span>' if board else '<span class="muted">—</span>'
        return f'''<tr>
  <td style="width: 240px;"><div style="display: flex; flex-direction: column; gap: 3px;"><div style="display: flex; align-items: center; gap: 8px;">{title}{state}</div>{slug_html}</div></td>
  <td style="width: 210px;"><span class="mono" style="font-size: 11px; color: #4b5563;">{where}</span></td>
  <td style="width: 170px;"><span class="mono muted" style="font-size: 11px;">{span}</span></td>
  <td><div class="rs"><div><span class="rs-k">started</span>{started}</div><div><span class="rs-k">ended</span>{ended}</div></div></td>
  <td style="width: 100px;">{board_html}</td>
  <td style="width: 170px;"><div style="display: flex; justify-content: flex-end; gap: 4px;">{act}</div></td>
</tr>'''
    groups = ""
    for host, repo, path, rows in RESUMABLE:
        groups += f'<tr><td colspan="6" style="padding: 0;"><div class="grp"><span>{host} / {repo}</span><span class="path mono" style="font-size: 11px;">{path}</span><span style="flex-grow: 1;"></span><span class="badge">claude-code · ~/.claude/projects</span></div></td></tr>'
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
  <div class="note"><b>Resume</b> opens New session with host, repo, and worktree prefilled and Start = Resume; a reaped worktree is recreated from the recorded branch. <b>Switch to</b> jumps to the running card in the Herd (same session: the name and the adapter id travel together from birth). A session started by hand shows only its id until you <b>Adopt</b> it (attach, give it a name), which is how it enters the Herd. The index is generated on demand from each adapter's transcripts (Claude: the JSONL under ~/.claude/projects, same as list_sessions.py), so crashed and disconnected sessions appear too. Closed sessions are filed here after their day on the Herd.</div>
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
    <div class="grp" style="padding: 0;"><span>{host} / {repo}</span><span class="path mono" style="font-size: 11px;">{path}/.agentorc.yml</span><span style="flex-grow: 1;"></span><span class="btn sm ghost">edit yml</span></div>
    <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; align-items: start;">{cards}</div>
  </div>'''
    groups += f'''<div style="display: flex; flex-direction: column; gap: 8px;">
    <div class="grp" style="padding: 0;"><span>kmaster / contractmatch</span><span class="path mono" style="font-size: 11px;">/home/kmaster/contractmatch</span></div>
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
    <div class="grp" style="padding: 0;"><span>Recent runs</span><span class="path">each run is a session of kind command — same tmux, same log, same Focus; hidden from the Herd unless "show command runs" is on</span></div>
    <div class="card"><table><tbody>{rows}</tbody></table></div>
  </div>
  <div class="note">Buttons come from each repo's checked-in <span class="mono">.agentorc.yml</span> (cmdorc command specs where cmdorc fits). A press starts <span class="mono">ao-&lt;repo&gt;-cmd-&lt;name&gt;</span> in tmux on that host, so the run gets the same Focus, running/exited state and run log as any session, but as kind: command it stays off the Herd and out of the urgency sort. The Attention tab's refresh is the attention command here — no second way to run a script. State is scraped (dashed pill): running while the pane has a process, exited with the exit code from the marker.</div>
</div>
</div>
''' + TAIL

BOARD = [
    ("samscrape", "/home/kmaster/samscrape/docs/user_attention.md", "swept 2026-09-04 03:00 · 26 open · 5 due/overdue", "", [
        ("over", "14d overdue", "2026-08-18", "tdgrind-1", "Get the TD-259 keep-half snapshot off this array — it is the only copy.", "TD-259"),
        ("over", "7d overdue", "2026-08-21", "tdgrind-2", "Decide TD-196 per tool: delete or port. A judgement call, so not done unattended.", "TD-196"),
        ("over", "2d overdue", "2026-08-27", "2fb13646", "Decide whether to turn the signup → CM board writer ON. PAT in Doppler, PR merged, migration applied.", "PR #343"),
        ("today", "due today", "2026-09-01", "tdgrind-2", "Deploy TD-296, then un-flag and republish the two DARPA notices.", "PR #437"),
        ("today", "due today", "2026-08-28", "tdgrind-1", "Deploy TD-036 step 2 (merged after the 05:00 UTC rollout, so not live).", "PR #410"),
        ("soon", "due 09-05", "2026-09-02", "tdgrind-1", "Deploy the R3 consumer migrations — three deployments, fold into the next skaffold run.", "PR #512"),
        ("soon", "due 09-06", "2026-09-04", "tdgrind-2", "PR #577 needs an independent review and a full-suite run before it merges.", "PR #577"),
    ]),
    ("dev-cadence", "/home/kmaster/dev-cadence/docs/user_attention.md", "swept 2026-08-12 · 1 open · 1 overdue", "last sweep 23d ago — run /stranded-work in this repo", [
        ("over", "2d overdue", "2026-08-26", "3168de4c", "Add --fetch to the SessionStart hook line in samscrape, contractmatch and pneuma-ops settings.json (SEED files, sync does not carry it).", "TD-030 · PR #76"),
    ]),
]

def attention():
    dcls = {"over": "s-stalled", "today": "s-needs", "soon": "s-idle"}
    right = ""
    for repo, path, meta, warn, items in BOARD:
        warn_html = f'<div class="warn" style="padding: 6px 10px;">{ICON["warn"]}<span>{warn}</span></div>' if warn else ""
        rows = ""
        for kind, due, date, sess, text, ctx in items:
            rows += f'''<div style="display: grid; grid-template-columns: 96px minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 8px 0; border-top: 1px solid #eceef1;">
  <span class="pill {dcls[kind]}" style="justify-self: start;">{due}</span>
  <div style="display: flex; flex-direction: column; gap: 3px; min-width: 0;"><div style="font-size: 12.5px;">{text}</div><div class="meta">{date} · session <a href="#">{sess}</a> · {ctx}</div></div>
  <div style="display: flex; gap: 4px;"><span class="btn sm ghost">{ICON["focus"]}Focus session</span><span class="btn sm ghost">Snooze ▾</span><span class="btn sm ghost">Done</span></div>
</div>'''
        if repo == "samscrape":
            rows += '''<div style="display: grid; grid-template-columns: 96px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 8px 0; border-top: 1px solid #eceef1;">
  <span class="pill s-idle" style="justify-self: start;">undated</span>
  <div class="muted" style="font-size: 12.5px;">19 more items with no Due date — surfaced only here, never on the Herd strip.</div>
  <span class="btn sm ghost">show ▾</span>
</div>'''
        right += f'''<div class="card" style="padding: 10px 12px 2px; display: flex; flex-direction: column; gap: 6px;">
  <div style="display: flex; align-items: center; gap: 10px;"><span style="font-weight: 600;">{repo}</span><span class="mono muted" style="font-size: 11px;">{meta}</span><span style="flex-grow: 1;"></span><a href="#" class="mono" style="font-size: 11px;">user_attention.md</a></div>
  {warn_html}
  <div style="display: flex; flex-direction: column;">{rows}</div>
</div>'''
    return head("Attention") + f'''<div style="width: 1440px; min-height: 860px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Attention")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  {page_head("Attention", "2 boards · 27 open items · 6 due/overdue · the full dev-cadence board, undated items included", '<span class="mono muted" style="font-size: 11px;">attention command last ran 03:00 MDT</span><span class="btn ghost">repo: all ▾</span><span class="btn ghost">overdue · today · this week · undated ▾</span>')}
  {right}
  <div class="note">The Herd shows only what is overdue or due today, in its Due strip. This tab is the whole board: every repo, undated items too, the same rows the repo's <span class="mono">nudge_user_attention.py --report</span> prints. <b>Focus session</b> opens the session that left the item (matched by adapter id; a closed one opens in Resumable). <b>Snooze</b> and <b>Done</b> are one-line edits the host agent makes to user_attention.md and commits with a message naming the session, so the checkout never sits dirty. Sessions that need you are not repeated here — that is the Herd.</div>
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
 ("#dbeafe", "#172554"), ("#1e40af", "#93c5fd"), ("#e5e7eb", "#2a313b"), ("#fecaca", "#4a1414"), ("#991b1b", "#fca5a5"), ("#d1fae5", "#0b3b2a"), ("#065f46", "#6ee7b7"), ("#ede9fe", "#2e1f5c"), ("#5b21b6", "#c4b5fd"), ("#7c3d00", "#fcd34d"),
 ("background: #0f1419;", "background: #05070a;"), ("background: #f3f4f6;", "background: #1f242c;"),
 ("background: #e5e7eb; color: #f1f3f6;", "background: #3a424d; color: #f1f3f6;"),
]
def darken(html):
    for a, b in DARK:
        html = html.replace(a, b)
    return html

files = {
    "Main.dc.html": herd_desktop(),
    "MainDark.dc.html": darken(herd_desktop()),
    "Phone.dc.html": herd_phone(),
    "Focus.dc.html": focus(),
    "NewSession.dc.html": new_session(),
    "Legend.dc.html": legend(),
    "Resumable.dc.html": resumable(),
    "Commands.dc.html": commands(),
    "Attention.dc.html": attention(),
}
for n, s in files.items():
    (OUT / n).write_text(s)

canvas = {
    "artboards": [
        {"file": "Main.dc.html", "title": "Herd — desktop", "x": 0, "y": 0, "w": 1440, "h": 1360},
        {"file": "Phone.dc.html", "title": "Herd — phone", "x": 1540, "y": 0, "w": 390, "h": 1560},
        {"file": "MainDark.dc.html", "title": "Herd — dark", "x": 2040, "y": 0, "w": 1440, "h": 1360},
        {"file": "Focus.dc.html", "title": "Focus — session", "x": 0, "y": 1460, "w": 1440, "h": 980},
        {"file": "NewSession.dc.html", "title": "New session", "x": 1540, "y": 1840, "w": 720, "h": 960},
        {"file": "Legend.dc.html", "title": "States & badges", "x": 0, "y": 2580, "w": 760, "h": 820},
        {"file": "Resumable.dc.html", "title": "Resumable", "x": 0, "y": 3540, "w": 1440, "h": 700},
        {"file": "Commands.dc.html", "title": "Commands", "x": 1540, "y": 3540, "w": 1440, "h": 820},
        {"file": "Attention.dc.html", "title": "Attention", "x": 0, "y": 4480, "w": 1440, "h": 860},
    ],
    "annotations": [
        {"id": "brief", "x": 0, "y": -150, "w": 520, "text": "agentorc mockups (2026-09-04, static, utilitarian operator console).\nRound 2: card grid chosen; profile line (tool · account · model) replaces the source column; new LIMITED state; attention/pinned sort toggle.\nRound 3: 'Done when' → 'Ready to close' + user-driven Close → closed state; dark artboard added; laptop shown as a volatile host (◐).\nRound 4: Resumable, Commands and Attention tabs added; then the consistency pass — renamed agentorc, Urgent-first sort + Due strip, shells as cards (host1, vpnmaster), command runs off the Herd, unreachable host banner, permissions via hook (no answer buttons under the terminal), Adopt for hand-started sessions."},
    ],
    "launch": {"view": "canvas"},
}
(OUT / "canvas.json").write_text(json.dumps(canvas, indent=2))
print("wrote", ", ".join(files), "canvas.json")
