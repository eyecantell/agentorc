#!/usr/bin/env python3
"""Emit the sessionherd mockup artboards (.dc.html) + canvas.json from one shared style."""
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
  .pill.scraped { outline: 1px dashed #d9a441; outline-offset: 1px; }
  .badge { display: inline-block; padding: 1px 5px; border: 1px solid #cbd0d6; border-radius: 3px; font-size: 10px; color: #5b6470; font-family: "JetBrains Mono", monospace; }
  .badge.scraped { border-style: dashed; color: #8a5a00; border-color: #d9a441; }
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
    "git": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="4" cy="4" r="1.6"></circle><circle cx="4" cy="12" r="1.6"></circle><circle cx="12" cy="6" r="1.6"></circle><path d="M4 5.6v4.8M12 7.6c0 2.4-8 1.2-8 3"></path></svg>',
}

BAR = {'needs': '#f59e0b', 'limited': '#7c3aed', 'stalled': '#dc2626', 'working': '#2563eb', 'idle': '#9ca3af', 'exited': '#9ca3af', 'done': '#059669'}

def pill(state, label=None, scraped=False):
    names = {"working": "working", "needs": "needs you", "idle": "idle", "stalled": "stalled?", "exited": "exited", "done": "closed", "limited": "limited"}
    return f'<span class="pill s-{state}{" scraped" if scraped else ""}"><span class="dot"></span>{label or names[state]}</span>'

def head(title):
    return f'<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>{CSS}</helmet>\n'

TAIL = "</x-dc>\n</body>\n</html>\n"

def topbar(active="Herd", width_extra=""):
    tabs = "".join(f'<span class="tab{" on" if t == active else ""}">{t}</span>' for t in ["Herd", "Resumable", "Commands", "Attention"])
    return f'''<div class="topbar">
  <span class="wordmark">session<b>herd</b></span>
  <div style="display: flex; gap: 2px;">{tabs}</div>
  <div style="flex-grow: 1;"></div>
  <span class="mono" style="font-size: 11px; color: #aab3bf;">usage 5h 41% · wk 58%</span>
  <span class="mono" style="font-size: 11px; color: #aab3bf;">hosts: kmaster ● vps ● laptop ◐</span>
  <span class="btn primary" style="height: 26px;">{ICON["plus"]}New session</span>
</div>'''

# ---------------- sessions data (realistic, from Paul's setup) ----------------
SESS = [
    ("kmaster", "samscrape", "/home/kmaster/samscrape", [
        ("main", "claude-code · paul (max) · opus", "needs", "2m", "main", "", "hook", "Permission: Bash · git push origin td301-fix", ""),
        ("tdgrind-1", "claude-code · grind (pro) · sonnet", "working", "14s", "wt/tdgrind-1 → td-301", "", "hook", "", "unattended"),
        ("tdgrind-2", "claude-code · grind (pro) · sonnet", "stalled", "47m", "wt/tdgrind-2 → td-296", "3 unpushed", "hook", "no output 47m · creds expire in 0.2h", "unattended"),
        ("tdgrind-3", "claude-code · grind (pro) · sonnet", "limited", "9m", "wt/tdgrind-3 → td-290", "", "hook", "5h window at 100% · resets 02:00 MDT (1h 51m)", "unattended"),
        ("errors-alerts", "claude-code · paul (max) · opus", "idle", "3h", "wt/errors-alerts", "dirty · 2 unpushed", "hook", "", ""),
        ("cmd-test", "shell · pdm run test", "exited", "1h", "wt/tdgrind-1", "", "scraped", "exit 0 · 412 passed", ""),
    ]),
    ("kmaster", "contractmatch", "/home/kmaster/contractmatch", [
        ("main", "claude-code · paul (max) · opus", "idle", "22m", "main", "", "hook", "ready to close ✓ · tree clean, nothing open", ""),
    ]),
    ("vps", "dev-cadence", "/home/paul/dev-cadence", [
        ("attention-fix", "gemini-cli · paul · 2.5-pro", "working", "1m", "wt/attention-fix", "", "hook", "", ""),
        ("td-7", "claude-code · paul (max) · opus", "exited", "2d", "wt/td-7 → td-7-hook-fetch", "PR #12 open", "hook", "not done: PR not merged", ""),
        ("td-5", "claude-code · paul (max) · opus", "done", "3d", "wt/td-5 (reaped)", "", "hook", "closed by you · PR #11 merged", ""),
    ]),
]

def row(s):
    name, tool, state, age, where, flag, conf, pending, tag = s
    flag_html = f'<span class="flag">{ICON["warn"]}{flag}</span>' if flag else ""
    tag_html = f'<span class="badge">{tag}</span>' if tag else ""
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

def herd_desktop():
    def card(host, repo, s):
        name, tool, state, age, where, flag, conf, pending, tag = s
        tag_html = f'<span class="badge">{tag}</span>' if tag else ""
        flag_html = f'<span class="flag">{ICON["warn"]}{flag}</span>' if flag else ""
        if state == "needs":
            slot = f'<div class="status" style="border-color: #f59e0b; color: #7c3d00;">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm primary">Allow</span><span class="btn sm">Deny</span><span class="btn sm ghost">Answer…</span></div>'
        elif state == "limited":
            slot = f'<div class="status lim">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm">Switch profile…</span><span class="btn sm ghost">Wait</span></div>'
        elif state in ("working", "stalled"):
            tail = pending if pending else "⏺ Edit(scripts/recover_stuck_notices.py)\n▌"
            slot = f'<div class="term tail">{tail}</div>'
        elif state == "done":
            slot = f'<div class="status ok">{pending}</div>'
        elif state == "exited":
            slot = f'<div class="status {"bad" if pending.startswith("not done") else ""}">{pending}</div>'
        elif pending.startswith("ready"):
            slot = f'<div class="status ok">{pending}</div><div style="display: flex; gap: 6px;"><span class="btn sm">Close session</span></div>'
        else:
            slot = f'<div class="status">last: ⏺ Edit(scripts/recover_stuck_notices.py)</div>'
        border = "#f59e0b" if state == "needs" else "#dfe3e8"
        return f'''<div class="card sc" style="border-color: {border};">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div class="sc-body">
    <div style="display: flex; align-items: center; gap: 8px;"><span class="name">{name}</span>{tag_html}<span style="flex-grow: 1;"></span>{pill(state, scraped=(conf == "scraped"))}</div>
    <div style="display: flex; align-items: center; gap: 8px;"><span class="meta" style="color: #374151;">{host} / {repo}</span><span style="flex-grow: 1;"></span><span class="meta" style="flex-shrink: 0;">{age}</span></div>
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
    rank = {"needs": 0, "limited": 1, "stalled": 2, "working": 3, "idle": 4, "exited": 5, "done": 6}
    ordered.sort(key=lambda t: rank[t[2][2]])
    cards = "".join(card(h, r, s) for h, r, s in ordered)
    return head("Herd") + f'''<div style="width: 1440px; min-height: 1060px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;">
  <div style="display: flex; align-items: center; gap: 10px;">
    <span style="font-size: 16px; font-weight: 600;">Herd</span>
    <span class="muted">10 sessions · </span>{pill("needs", "1 needs you")}{pill("limited", "1 limited")}{pill("stalled", "1 stalled")}
    <span style="flex-grow: 1;"></span>
    <span class="input" style="width: 200px; height: 28px; color: #9ca3af;">filter…</span>
    <span class="btn ghost">host: all ▾</span><span class="btn ghost">repo: all ▾</span><span class="btn ghost">profile: all ▾</span>
    <span style="width: 1px; height: 20px; background: #cbd0d6; margin: 0 8px;"></span>
    <span style="display: inline-flex; border: 1px solid #cbd0d6; border-radius: 4px; overflow: hidden;"><span class="btn" style="border: 0; border-radius: 0; background: #e5e7eb; color: #111418;">Attention</span><span class="btn" style="border: 0; border-radius: 0;">Pinned</span></span>
  </div>
  <div style="display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: start;">{cards}</div>
  <div class="note"><b>Attention</b> sort: needs you → limited → stalled? → working → idle → exited → closed. <b>Pinned</b> keeps every card where you dragged it and highlights the ones that need you instead. A dashed outline on a state pill means the state was guessed from the screen (tool without hooks), not reported.</div>
</div>
</div>
''' + TAIL

def herd_phone():
    def card(host, repo, s):
        name, tool, state, age, where, flag, conf, pending, tag = s
        if state == "needs":
            pend = f'<div class="status" style="border-color: #f59e0b; color: #7c3d00; white-space: normal; margin-top: 8px;">{pending}</div>'
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn primary" style="height: 44px; flex-grow: 1; justify-content: center;">Allow</span><span class="btn" style="height: 44px; flex-grow: 1; justify-content: center;">Deny</span><span class="btn" style="height: 44px; width: 44px; justify-content: center;">{ICON["focus"]}</span></div>'
        else:
            cls = {"limited": "lim", "done": "ok"}.get(state, "bad" if (state in ("stalled",) or pending.startswith("not done")) else "")
            pend = f'<div class="status {cls}" style="white-space: normal; margin-top: 8px;">{pending}</div>' if pending else ""
            actions = f'<div style="display: flex; gap: 8px; margin-top: 10px;"><span class="btn ghost" style="height: 44px; flex-grow: 1; justify-content: center; border-color: #dfe3e8;">{ICON["focus"]}Focus</span></div>'
        flag_html = f'<div class="flag" style="margin-top: 6px;">{ICON["warn"]}{flag}</div>' if flag else ""
        return f'''<div class="card" style="padding: 12px 12px 12px 14px; position: relative; overflow: hidden;">
  <div class="sbar" style="background: {BAR[state]};"></div>
  <div style="display: flex; align-items: center; gap: 8px;"><span class="name" style="font-family: 'JetBrains Mono', monospace; font-size: 15px; font-weight: 600;">{name}</span><span style="flex-grow: 1;"></span>{pill(state, scraped=(conf == "scraped"))}</div>
  <div class="meta" style="margin-top: 4px; color: #374151;">{host} / {repo} · {where}</div>
  <div class="meta" style="margin-top: 2px;">{tool} · {age}</div>
  {flag_html}{pend}{actions}
</div>'''
    ordered = []
    for host, repo, path, rows in SESS:
        for r in rows:
            ordered.append((host, repo, r))
    rank = {"needs": 0, "limited": 1, "stalled": 2, "working": 3, "idle": 4, "exited": 5, "done": 6}
    ordered.sort(key=lambda t: rank[t[2][2]])
    cards = ""
    for host, repo, r in ordered:
        cards += card(host, repo, r)
    return head("Phone") + f'''<div style="width: 390px; min-height: 1220px; background: #f4f5f7; display: flex; flex-direction: column;">
<div class="topbar" style="padding: 0 14px; gap: 10px; height: 52px;"><span class="wordmark">session<b>herd</b></span><span style="flex-grow: 1;"></span><span class="mono" style="font-size: 11px; color: #aab3bf;">5h 41%</span><span class="btn primary" style="height: 32px; width: 32px; padding: 0; justify-content: center;">{ICON["plus"]}</span></div>
<div style="padding: 12px 12px 20px; display: flex; flex-direction: column; gap: 10px;">
  <div style="display: flex; gap: 6px; overflow: hidden;"><span class="btn" style="height: 32px;">needs you 1</span><span class="btn" style="height: 32px;">stalled 1</span><span class="btn" style="height: 32px;">all 10</span></div>
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
<span class="q">┌─ Permission ───────────────────────────────────────────────┐
│ Bash: git push -u origin td301-fix                            │
│ ❯ 1. Yes                                                     │
│   2. Yes, and don't ask again for git push in this session   │
│   3. No, tell Claude what to do differently                  │
└──────────────────────────────────────────────────────────────┘</span>
<span class="d">▌</span>'''
    return head("Focus") + f'''<div style="width: 1440px; min-height: 900px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 12px 20px; display: flex; gap: 14px; align-items: flex-start;">
  <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0;">
    <div style="display: flex; align-items: center; gap: 10px;">
      <a href="#" class="muted">← Herd</a>
      <span class="mono" style="font-size: 15px; font-weight: 500;">kmaster / samscrape / tdgrind-1</span>
      {pill("needs")}<span class="badge">unattended</span>
      <span style="flex-grow: 1;"></span>
      <span class="btn">{ICON["code"]}VS Code</span><span class="btn">Wrap up</span><span class="btn danger">{ICON["kill"]}Kill</span>
    </div>
    <div class="term" style="height: 560px;">{term}</div>
    <div class="card" style="padding: 10px; display: flex; flex-direction: column; gap: 8px;">
      <div class="input" style="height: 64px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Type a prompt… (Enter sends; the terminal above also takes keys directly)</div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <span class="btn">{ICON["clip"]}Attach file</span>
        <span class="badge">~/.sessionherd/attachments/tdgrind-1/spec.pdf</span>
        <span style="flex-grow: 1;"></span>
        <span class="btn">Answer 1 · Yes</span><span class="btn">Answer 3 · No</span>
        <span class="btn primary">{ICON["send"]}Send</span>
      </div>
    </div>
  </div>
  <div style="width: 320px; display: flex; flex-direction: column; gap: 12px; flex-shrink: 0;">
    <div class="card" style="padding: 12px;">
      <div style="font-weight: 600; margin-bottom: 8px;">Session</div>
      <dl class="kv" style="margin: 0;">
        <dt>profile</dt><dd class="mono" style="font-size: 11px;">claude-code · grind (pro) · sonnet</dd>
        <dt>resume</dt><dd class="mono" style="font-size: 11px;">1c8e…f42a</dd>
        <dt>tmux</dt><dd class="mono">sh-samscrape-tdgrind-1</dd>
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
    return head("New session") + f'''<div style="width: 720px; min-height: 860px; background: #f4f5f7; display: flex; flex-direction: column;">
{topbar("Herd")}
<div style="padding: 20px 24px; display: flex; flex-direction: column; gap: 16px;">
  <div style="font-size: 16px; font-weight: 600;">New session</div>
  <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px;">
    <div class="field"><label>Host</label><span class="input">kmaster<span class="muted">▾</span></span></div>
    <div class="field"><label>Repo</label><span class="input">samscrape<span class="muted">▾</span></span></div>
    <div class="field"><label>Adapter</label><span class="input">claude-code <span class="badge">state: hook</span></span></div>
    <div class="field"><label>Name</label><span class="input mono">td-302</span></div>
  </div>
  <div class="field"><label>Where</label>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <div class="radio" style="opacity: .55;"><span class="rb"></span><span>Main checkout</span><span class="mono muted" style="font-size: 11px;">/home/kmaster/samscrape</span><span style="flex-grow: 1;"></span><span class="pill s-needs">in use by main</span></div>
      <div class="radio on"><span class="rb"></span><span>New worktree</span><span class="mono muted" style="font-size: 11px;">.claude/worktrees/td-302 from origin/main</span></div>
      <div class="radio"><span class="rb"></span><span>Existing worktree</span><span class="mono muted" style="font-size: 11px;">errors-alerts (idle, dirty)</span></div>
    </div>
    <div class="warn">{ICON["warn"]}<span>One session per main checkout (dev-cadence §1). The main checkout already hosts <b>main</b>, so a second session there is refused, not warned about.</span></div>
  </div>
  <div class="field"><label>Start</label>
    <div style="display: flex; flex-direction: column; gap: 6px;">
      <div class="radio on"><span class="rb"></span><span>Fresh</span></div>
      <div class="radio"><span class="rb"></span><span>Resume</span><span class="mono muted" style="font-size: 11px;">pick from Resumable (24 transcripts on kmaster/samscrape)</span></div>
    </div>
  </div>
  <div class="field"><label>Opening prompt (optional)</label><span class="input" style="height: 72px; align-items: flex-start; padding: 8px 10px; color: #9ca3af;">Paste the brief, or leave empty to start at the prompt.</span></div>
  <div class="field"><label>Mode</label>
    <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;">
      <div class="radio on"><span class="rb"></span><div><div>Interactive</div><div class="note">never paused, nudged, or killed by a policy</div></div></div>
      <div class="radio"><span class="rb"></span><div><div>Unattended</div><div class="note">run window + usage gate apply; brief file required</div></div></div>
    </div>
  </div>
  <div style="display: flex; gap: 8px; justify-content: flex-end; padding-top: 6px;"><span class="btn">Cancel</span><span class="btn primary">Start session</span></div>
</div>
</div>
''' + TAIL

def legend():
    rows = [
        ("working", "A hook reported UserPromptSubmit / PreToolUse and output is still flowing."),
        ("needs", "Waiting on you: a permission, a question, or an empty prompt. Pending text shown inline. Sorted to the top."),
        ("limited", "Hit a usage or token cap and is waiting on a reset. Reset time shown. Nothing you do unblocks it except switching the profile (account/model)."),
        ("idle", "Turn finished (Stop hook), nothing pending. Flagged if the tree is dirty or unpushed."),
        ("stalled", "Reported working, but no output for longer than the adapter's stall_after. How a credential lapse shows up."),
        ("exited", "Process ended or tmux session gone. Run log kept. Shows which ready-to-close checks failed."),
        ("done", "You clicked Close: session killed, worktree reaped, card kept a day then filed under Resumable. Only you close a session; the checklist just says when it is ready."),
    ]
    body = "".join(f'<tr><td style="width: 120px;">{pill(s)}</td><td>{d}</td></tr>' for s, d in rows)
    return head("Legend") + f'''<div style="width: 760px; min-height: 640px; background: #f4f5f7; padding: 20px 24px; box-sizing: border-box; display: flex; flex-direction: column; gap: 14px;">
  <div style="font-size: 16px; font-weight: 600;">States and badges</div>
  <div class="card"><table><tbody>{body}</tbody></table></div>
  <div class="card" style="padding: 12px; display: flex; flex-direction: column; gap: 8px;">
    <div style="display: flex; gap: 10px; align-items: center;">{pill("working", scraped=True)}<span>Dashed outline: state guessed from the last screen lines (tool without hooks, plain shells). Solid: reported by the tool's hooks.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="mono" style="font-size: 11px; color: #4b5563; white-space: nowrap;">claude-code · paul (max) · opus</span><span>Profile line: tool · account · model. Commands, policies, and usage gates key on the profile, so two accounts of one tool are tracked separately.</span></div>
    <div style="display: flex; gap: 10px; align-items: center;"><span class="badge">unattended</span><span>Policies apply: run window, usage gate, wrap-up-then-kill, credential checks.</span></div>
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

DARK = [
 ("background: #f4f5f7; color: #1c2128;", "background: #0e1116; color: #d7dce3;"),
 ("#f4f5f7", "#0e1116"), ("background: #fff;", "background: #171b22;"), ("#dfe3e8", "#2a313b"), ("#eceef1", "#242a33"),
 ("#111418", "#f1f3f6"), ("#1c2128", "#e6e9ee"), ("#374151", "#c3c9d2"), ("#4b5563", "#9aa3b0"), ("#6b7280", "#7d8794"), ("#cbd0d6", "#3a424d"),
 (".topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #e6e9ee;", ".topbar { display: flex; align-items: center; gap: 16px; height: 48px; padding: 0 20px; background: #05070a;"),
 (".btn.primary { background: #e6e9ee; color: #fff; border-color: #e6e9ee; }", ".btn.primary { background: #e6e9ee; color: #0e1116; border-color: #e6e9ee; }"),
 (".tab.on { background: #2b323b; color: #fff; }", ".tab.on { background: #2b323b; color: #fff; }"),
 ("#fffbeb", "#2a2410"), ("#fde68a", "#6b4d00"), ("#f5f3ff", "#221a33"), ("#ddd6fe", "#4c3a80"),
 ("#dbeafe", "#172554"), ("#1e40af", "#93c5fd"), ("#e5e7eb", "#2a313b"), ("#fecaca", "#4a1414"), ("#991b1b", "#fca5a5"), ("#d1fae5", "#0b3b2a"), ("#065f46", "#6ee7b7"), ("#ede9fe", "#2e1f5c"), ("#5b21b6", "#c4b5fd"), ("#7c3d00", "#fcd34d"),
 ("background: #0f1419;", "background: #05070a;"),
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
}
for n, s in files.items():
    (OUT / n).write_text(s)

canvas = {
    "artboards": [
        {"file": "Main.dc.html", "title": "Herd — desktop", "x": 0, "y": 0, "w": 1440, "h": 1060},
        {"file": "Phone.dc.html", "title": "Herd — phone", "x": 1540, "y": 0, "w": 390, "h": 1220},
        {"file": "MainDark.dc.html", "title": "Herd — dark", "x": 2040, "y": 0, "w": 1440, "h": 1060},
        {"file": "Focus.dc.html", "title": "Focus — session", "x": 0, "y": 1200, "w": 1440, "h": 900},
        {"file": "NewSession.dc.html", "title": "New session", "x": 1540, "y": 1400, "w": 720, "h": 860},
        {"file": "Legend.dc.html", "title": "States & badges", "x": 0, "y": 2240, "w": 760, "h": 640},
    ],
    "annotations": [
        {"id": "brief", "x": 0, "y": -150, "w": 520, "text": "sessionherd mockups (2026-09-04, static, utilitarian operator console).\nRound 2: card grid chosen; profile line (tool · account · model) replaces the source column; new LIMITED state; attention/pinned sort toggle.\nRound 3: 'Done when' → 'Ready to close' + user-driven Close → closed state; dark artboard added; laptop shown as a volatile host (◐)."},
    ],
    "launch": {"view": "canvas"},
}
(OUT / "canvas.json").write_text(json.dumps(canvas, indent=2))
print("wrote", ", ".join(files), "canvas.json")
