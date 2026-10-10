"""The kill guard (design §4.3 *A kill the guard refuses*, TD-489): a net under §4.8's process rule.

A session signals only a pid it started and holds. `refuse` reads a `Bash` command's shape and names
the commands that reach what nobody looked at — `pkill` or `killall` as a command word; `kill` of `-1`,
of `1` or of `$PPID`; `kill` of a substitution that runs `ps` or `pgrep`; a command that both reads a
parent pid and runs `kill`. A literal pid, a variable, a job and a pidfile pass: the guard cannot tell
a held pid from a found one, and the rule allows them. It reads text only and calls nothing.
"""

from __future__ import annotations

import re

REASON = (
    "agentorc: refused — a kill by pattern, of a parent or of everything; "
    "signal only a pid you started and hold (design §4.8)"
)

# Words that run the command after them, skipped to find the command word (with their own options).
WRAPPERS = {"sudo", "env", "nohup", "exec", "command", "builtin", "nice", "setsid", "time", "xargs", "timeout"}
# A wrapper's options that take the next word as their value: `sudo -u bob pkill x` runs `pkill`.
WRAPPER_VALUES = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-D"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"},
}
# Shell keywords a command word may follow inside a compound command.
KEYWORDS = {"do", "then", "else", "elif", "!", "if", "while", "until", "{", "}"}
BY_PATTERN = {"pkill", "killall"}
# A kill's target that reaches everything, init, or the caller's parent.
WIDE_TARGETS = {"-1", "1", "$PPID", "${PPID}"}
SEPARATORS = re.compile(r"\$\(|&&|\|\||[;&|\n()`]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `kill … $(ps …` or `kill … `pgrep …` — a substitution that finds pids by looking across the machine.
KILL_OF_A_SEARCH = re.compile(r"(?:^|[^\w-])kill\b[^;&|\n]*(?:\$\(|`)\s*(?:sudo\s+)?(?:ps|pgrep)\b")
PARENT_PID = re.compile(r"\bppid\b", re.IGNORECASE)
# A parameter expansion inside double quotes, kept by the mask: `kill "$PPID"` is `kill $PPID`.
EXPANSION = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}\"]*\})")
# A here-document's operator and delimiter (`<<EOF`, `<<-'EOF'`); `<<<` is a here-string, not one.
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def blank_bodies(command: str, i: int, out: list[str], pending: list[tuple[str, bool]]) -> int:
    """At the newline at `i` that ends a line with here-documents open: blank each body to `_`
    through its delimiter's line, keeping the newlines, and return where the command goes on."""
    out.append("\n")
    i += 1
    for word, tabs in pending:
        while i < len(command):
            end = command.find("\n", i)
            end = len(command) if end < 0 else end
            line = command[i:end]
            done = (line.lstrip("\t") if tabs else line) == word
            out.append(line if done else "_" * len(line))
            if end < len(command):
                out.append("\n")
            i = end + 1
            if done:
                break
    pending.clear()
    return i


def mask(command: str) -> str:
    """The command with quoted text blanked to `_`, so a word only echoed or matched is no command;
    a `$( … )` or backtick substitution and a `$NAME` inside double quotes are kept, since they run.
    A here-document's body and a `#` comment are blanked too: text a command reads, never runs."""
    out: list[str] = []
    pending: list[tuple[str, bool]] = []  # here-documents opened on the current line: (delimiter, `<<-`)
    i, n = 0, len(command)
    quote = ""  # "", "'" or '"'
    depth = 0  # open `$(` inside the current double quote
    tick = False  # inside a backtick inside the current double quote
    while i < n:
        c = command[i]
        code = quote == "" or (quote == '"' and (depth or tick))
        if code and c == "<" and not command.startswith("<<<", i) and (m := HEREDOC.match(command, i)):
            pending.append((m.group(3), m.group(1) == "-"))
            out.append(m.group())
            i = m.end()
            continue
        if code and c == "\n" and pending:
            i = blank_bodies(command, i, out, pending)
            continue
        if quote == "" and c == "#" and (not out or out[-1][-1:] in " \t\n;&|("):
            end = command.find("\n", i)
            end = n if end < 0 else end
            out.append("_" * (end - i))
            i = end
            continue
        if quote == "'":
            if c == "'":
                quote = ""
                out.append(c)
            else:
                out.append("_")
        elif quote == '"':
            if c == "\\" and i + 1 < n:
                out.append("__" if not (depth or tick) else command[i : i + 2])
                i += 2
                continue
            if not (depth or tick) and (m := EXPANSION.match(command, i)):
                out.append(m.group())
                i = m.end()
                continue
            if command.startswith("$(", i):
                depth += 1
                out.append("$(")
                i += 2
                continue
            if c == "`":
                tick = not tick
                out.append(c)
            elif c == ")" and depth:
                depth -= 1
                out.append(c)
            elif c == '"' and not (depth or tick):
                quote = ""
                out.append(c)
            else:
                out.append(c if (depth or tick) else "_")
        elif c == "\\" and i + 1 < n:
            out.append(command[i : i + 2])
            i += 2
            continue
        elif c in "'\"":
            quote, depth, tick = c, 0, False
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def command_word(words: list[str]) -> tuple[str, list[str]]:
    """The segment's command word and its arguments, past assignments, keywords and wrappers."""
    i = 0
    while i < len(words):
        w = words[i]
        if ASSIGNMENT.match(w) or w in KEYWORDS:
            i += 1
        elif (name := w.rsplit("/", 1)[-1]) in WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[i])):
                # the wrapper's own options, a value its option takes, and `timeout`'s duration
                i += 2 if words[i] in WRAPPER_VALUES.get(name, ()) else 1
        else:
            return w.lstrip("\\").rsplit("/", 1)[-1], words[i + 1 :]  # `\pkill` skips an alias, nothing else
    return "", []


def kill_targets(args: list[str]) -> list[str]:
    """What a `kill` signals: its arguments past the signal option. `kill -1` alone is a target, as
    `kill -9 -1` is; `kill -1 1234` is SIGHUP to 1234."""
    if not args:
        return []
    rest = args
    if rest[0] in ("-s", "-n"):
        rest = rest[2:]
    elif rest[0] == "--":
        return rest[1:]
    elif rest[0].startswith("-") and len(rest) > 1:
        rest = rest[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    return rest


def refuse(command: str) -> str | None:
    """The reason to refuse `command`, or None when its shape is none §4.3 lists."""
    masked = mask(command)
    kills = False
    for segment in SEPARATORS.split(masked):
        word, args = command_word(segment.split())
        if word in BY_PATTERN:
            return REASON
        if word == "kill":
            kills = True
            if any(t.strip('"') in WIDE_TARGETS for t in kill_targets(args)):
                return REASON
    if not kills:
        return None
    if KILL_OF_A_SEARCH.search(masked) or PARENT_PID.search(masked):
        return REASON
    return None
