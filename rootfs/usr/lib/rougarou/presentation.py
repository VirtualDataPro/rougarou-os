"""A single, width-aware terminal welcome. No cursor motion or external probes."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys
import textwrap
import tomllib
import unicodedata


def plain(value: str) -> str:
    return "".join(c for c in str(value) if not unicodedata.category(c).startswith("C"))


def cells(value: str) -> int:
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in value)


def fit(value: str, width: int) -> str:
    output = ""
    for char in plain(value):
        if cells(output + char) > width:
            break
        output += char
    return output


def welcome_text(share: Path, status: str = "", *, columns: int | None = None,
                 rows: int | None = None, term: str | None = None,
                 color: bool | None = None, tty: bool | None = None) -> str:
    size = shutil.get_terminal_size((80, 25))
    columns = max(1, columns if columns is not None else size.columns)
    rows = max(1, rows if rows is not None else size.lines)
    tty = sys.stdout.isatty() if tty is None else tty
    term = os.environ.get("TERM", "dumb") if term is None else term
    color = tty and "NO_COLOR" not in os.environ and term != "dumb" if color is None else color
    palette = tomllib.loads((share / "colors.toml").read_text())
    version = plain((share / "version").read_text().strip())
    hostname = plain(os.uname().nodename)

    def paint(text: str, role: str) -> str:
        if not color or not text:
            return text
        if term in {"linux", "vt100", "vt102", "ansi"}:
            code = "32" if role == "accent" else "37" if role == "foreground" else "90"
        else:
            value = palette[role].lstrip("#")
            code = "38;2;" + ";".join(str(int(value[i:i + 2], 16)) for i in (0, 2, 4))
        return f"\033[{code}m{text}\033[0m"

    heading = [("ROUGAROU OS", "accent"), (version, "muted"), ("", "muted"),
               (hostname, "foreground"), ("HEADLESS / OPERATOR-OWNED", "muted"), ("", "muted")]
    commands = [("a                 Start your agent", "foreground"),
                ("rougarou jobs     Managed work", "foreground"),
                ("rougarou doctor   Check this host", "foreground"),
                ("rougarou setup    Connect accounts", "muted")]
    # Linux consoles and simple serial terminals cannot reliably draw braille.
    unicode_ok = tty and term not in {"linux", "vt100", "vt102", "ansi", "dumb"}
    try:
        "⢸⣷".encode(sys.stdout.encoding or "ascii")
    except (LookupError, UnicodeError):
        unicode_ok = False
    artwork = (share / ("wolf.txt" if unicode_ok else "wolf-ascii.txt")).read_text().splitlines()
    while artwork and not artwork[-1].strip():
        artwork.pop()
    art_width = max((cells(line) for line in artwork), default=0)
    full = tty and columns >= art_width + 38 and rows >= len(artwork) + 3
    available = columns - art_width - 4 if full else columns - 2
    status_lines = textwrap.wrap(plain(status), max(1, available), break_long_words=True)[:3]
    if full:
        # Anchor to the owner's reference: the 20-line wolf has its heading on
        # row 5, status on row 11 and commands on rows 13–16. Status wrapping
        # may push commands down, but never shifts the heading or hostname.
        start = max(0, (len(artwork) - 12) // 2)
        panel = [("", "muted")] * start + heading
        panel += [(line, "muted") for line in status_lines]
        command_start = max(start + 8, len(panel) + 1)
        panel += [("", "muted")] * (command_start - len(panel)) + commands
        lines = []
        for index in range(max(len(artwork), len(panel))):
            left = artwork[index] if index < len(artwork) else ""
            right, role = panel[index] if index < len(panel) else ("", "muted")
            line = " " + paint(left, "accent") if left else ""
            if right:
                line += " " * (art_width - cells(left) + (3 if left else 4)) + paint(fit(right, available), role)
            lines.append(line)
        return "\n".join(lines).rstrip() + "\n"
    # Small screens and redirected output stay compact and readable.
    compact = [(f"ROUGAROU OS  /  {version}", "accent"), (hostname, "muted")]
    if status_lines:
        compact += [(line, "muted") for line in status_lines]
    compact += [("", "muted"), ("a   agent    |    rougarou help", "foreground")]
    return "\n".join(paint(fit(line, columns), role) for line, role in compact[:max(1, rows - 2)]) + "\n"
