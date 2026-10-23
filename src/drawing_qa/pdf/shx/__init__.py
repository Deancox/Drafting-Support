"""Reading text from sheets plotted without SHX text notes, by decoding the letter strokes.

AutoCAD draws every letter from a fixed SHX font, so one character at one size and rotation is
always the same few paths. Letters are grouped from the paths (glyphs.py), learned from readable
sheets where an SHX note lies over the strokes (book.py), and looked up exactly on unreadable
sheets (decode.py). Design: docs/superpowers/specs/2026-10-23-shx-stroke-decoding-design.md.
"""
