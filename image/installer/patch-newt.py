#!/usr/bin/env python3
"""Patch the reviewed Debian cdebconf 0.280 newt frontend, failing on drift.

The upstream source and its license remain intact. This changes presentation,
not question handling, password entry, partitioning or accessibility controls.
"""
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
text = source.read_text()
palette = '''struct newtColors newtAltColorPalette = {
    "white", "black",          /* root */
    "green", "black",          /* border */
    "white", "black",          /* window */
    "black", "black",          /* shadow */
    "brightgreen", "black",    /* title */
    "white", "black",          /* button */
    "black", "green",          /* active button */
    "white", "black",          /* checkbox */
    "black", "green",          /* active checkbox */
    "white", "blue",           /* entry; blue is the console surface */
    "green", "black",          /* label */
    "white", "black",          /* listbox */
    "black", "green",          /* active listbox */
    "white", "black",          /* textbox */
    "white", "black",          /* active textbox */
    "gray", "black",           /* help line */
    "brightgreen", "black",    /* root text */
    "green", "black",          /* progress full / empty */
    "gray", "black",           /* disabled entry */
    "green", "black",          /* compact button */
    "black", "green",          /* active selected listbox */
    "green", "black"           /* selected listbox */
};'''
text, count = re.subn(r'struct newtColors newtAltColorPalette = \{.*?\n\};', palette, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit("Reviewed cdebconf palette block not found")
old = '''    palette = getenv("FRONTEND_BACKGROUND");
    if (palette == NULL || strcmp(palette, "dark") != 0)
        newtAltColorPalette = newtDefaultColorPalette;'''
if text.count(old) != 1 or text.count('    const char *palette;') != 1:
    raise SystemExit("Reviewed cdebconf theme initialization not found")
text = text.replace(old, '    /* Rougarou keeps the same palette throughout installation. */')
text = text.replace('    const char *palette;\n', '')
old = '    newtSetColors(newtAltColorPalette);\n    newtCls();'
if text.count(old) != 1:
    raise SystemExit("Reviewed cdebconf display initialization not found")
text = text.replace(old, old + '\n    newtDrawRootText(2, 0, "ROUGAROU OS  /  SERVER INSTALLATION");')
old = 'newtDrawRootText(0, 0, text);'
if text.count(old) != 2:
    raise SystemExit("Reviewed cdebconf information header not found")
# The question remains the native window title; keep the single-line brand
# header instead of reintroducing a conflicting distribution header.
text = text.replace(old, 'newtDrawRootText(2, 0, "ROUGAROU OS  /  SERVER INSTALLATION");')
source.write_text(text)
