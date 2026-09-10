"""Test-only pane program: a one-line composer that can swallow Enters (TD-027).

Paints `>> <text>` on its last row, requests bracketed paste, and on Enter prints
`SUBMITTED <text>` and clears. Argument: how many Enters to swallow after each paste — 0 is a
well-behaved tool, 1 needs the agent's `C-m` retry, 2 defeats it. After a submit it paints the
submitted text back into the composer in faint (SGR 2), the way Claude Code paints a suggested next
prompt, so a reader that counts faint text as content sees a stuck composer that is not.
"""

import os
import sys
import tty

PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"


def main() -> None:
    swallow = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    fd = sys.stdin.fileno()
    tty.setcbreak(fd)
    out = sys.stdout
    out.write("\x1b[?2004h>> ")
    out.flush()
    buf = ""
    in_paste = False
    to_swallow = 0
    while True:
        data = os.read(fd, 4096).decode(errors="replace")
        if not data:
            return
        i = 0
        while i < len(data):
            if data.startswith(PASTE_START, i):
                in_paste, to_swallow, i = True, swallow, i + len(PASTE_START)
                continue
            if data.startswith(PASTE_END, i):
                in_paste, i = False, i + len(PASTE_END)
                continue
            ch = data[i]
            i += 1
            if ch in "\r\n" and not in_paste:
                if to_swallow > 0:
                    to_swallow -= 1
                    continue
                out.write(f"\r\x1b[K>> {buf}\nSUBMITTED {buf}\n>> \x1b[2m{buf}\x1b[0m")
                buf = ""
                out.flush()
                continue
            buf += ch
        out.write(f"\r\x1b[K>> {buf}")
        out.flush()


if __name__ == "__main__":
    main()
