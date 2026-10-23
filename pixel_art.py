"""Tiny pixel-art toolkit: a 5x7 bitmap font, 7x7 state icons and stepped-corner boxes.

Everything is drawn on a low-resolution layer (1 unit = PX screen pixels) and scaled
up with nearest-neighbour, so edges stay crisp like classic pixel-art speech bubbles.
"""

from __future__ import annotations

import re
import unicodedata

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Pillow is only needed by the renderer, not by the bridge
    from PIL import ImageDraw

PX = 2  # screen pixels per art pixel

_FONT_ROWS = {
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": "### .#. .#. .#. .#. .#. ###",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "!": "# # # # # . #",
    "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    ".": ". . . . . . #",
    ",": ".. .. .. .. .. .# #.",
    "'": "# # . . . . .",
    "-": "... ... ... ### ... ... ...",
    " ": "... ... ... ... ... ... ...",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": ".#. ##. .#. .#. .#. .#. ###",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": ".###. #.... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ....# .###.",
    ":": ". # . . . # .",
    ";": ". # . . . # #",
    "@": ".###. #...# #.### #.#.# #.### #.... .###.",
    "#": ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.",
    "/": "....# ...#. ...#. ..#.. .#... .#... #....",
    "(": ".# #. #. #. #. #. .#",
    ")": "#. .# .# .# .# .# #.",
    "¿": "..#.. ..... ..#.. .#... #.... #...# .###.",
    "¡": "# . # # # # #",
    "Ñ": ".##.# #.##. #...# ##..# #.#.# #..## #...#",
    '"': "#.# #.# ... ... ... ... ...",
    "+": "... ... .#. ### .#. ... ...",
    "=": "... ... ### ... ### ... ...",
    "&": ".##.. #..#. #.#.. .#... #.#.# #..#. .##.#",
    "%": "##..# ##.#. ...#. ..#.. .#... .#.## #..##",
    "_": "..... ..... ..... ..... ..... ..... #####",
    "*": "..... #.#.# .###. ##### .###. #.#.# .....",
    "[": "## #. #. #. #. #. ##",
    "]": "## .# .# .# .# .# ##",
}
FONT = {ch: [row for row in rows.split()] for ch, rows in _FONT_ROWS.items()}
GLYPH_H = 7
LETTER_GAP = 1
LINE_GAP = 3

ICONS = {
    "idle": ["#####..", "...#...", "..#....", ".####..", "....###", ".....#.", "....###"],
    "thinking": [".###...", "#...#..", "...#...", "..#....", ".......", "..#..#.", "......#"],
    "working": ["...##..", "..##...", ".##....", "#######", "....##.", "...##..", "..##..."],
    "testing": [".###...", ".#.#...", ".#.#...", "#...#..", "#.#.#..", "#...#..", ".###..."],
    "waiting": ["..#....", "..#....", "..#....", "..#....", "..#....", ".......", "..#...."],
    "done": [".......", "......#", ".....#.", "#...#..", ".#.#...", "..#....", "......."],
    "error": ["#.....#", ".#...#.", "..#.#..", "...#...", "..#.#..", ".#...#.", "#.....#"],
}


PUNCTUATION = str.maketrans({"—": "-", "–": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "\t": " "})


def normalize_text(text: str) -> str:
    """Uppercase text using only glyphs the font has: accents dropped (Ñ kept), emoji removed."""
    out = []
    for ch in text.upper().replace("\n", " ").translate(PUNCTUATION):
        if ch in FONT:
            out.append(ch)
            continue
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
        if base and all(c in FONT for c in base):
            out.append(base)
        elif unicodedata.category(ch) in ("So", "Sk", "Cs", "Cf", "Mn"):
            continue  # emoji, modifiers, joiners
        else:
            out.append("?")
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def glyph(ch: str) -> list[str]:
    return FONT.get(ch.upper(), FONT[" "])


def text_width(text: str) -> int:
    widths = [len(glyph(ch)[0]) for ch in text]
    return sum(widths) + LETTER_GAP * max(len(widths) - 1, 0)


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color) -> None:
    x, y = xy
    for ch in text:
        rows = glyph(ch)
        for dy, row in enumerate(rows):
            for dx, cell in enumerate(row):
                if cell == "#":
                    draw.point((x + dx, y + dy), fill=color)
        x += len(rows[0]) + LETTER_GAP


def truncate(text: str, width: int) -> str:
    """One line cut character by character (not by word) to fit, ending in '...' when cut."""
    text = text.upper()
    if text_width(text) <= width:
        return text
    while text and text_width(text.rstrip() + "...") > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _split_long(word: str, width: int) -> list[str]:
    """Break a word wider than the line (URLs, ids) into pieces that fit."""
    pieces, piece = [], ""
    for ch in word:
        if piece and text_width(piece + ch) > width:
            pieces.append(piece)
            piece = ""
        piece += ch
    return pieces + [piece]


def wrap(text: str, width: int, max_lines: int | None = None) -> list[str]:
    """Word-wrap uppercase text to a width in art pixels, ending with '...' when cut at max_lines."""
    words = [piece for word in text.upper().split() for piece in _split_long(word, width)]
    lines = _wrap_words(words, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_width(last + "...") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


def _wrap_words(words: list[str], width: int) -> list[str]:
    lines, line = [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if text_width(candidate) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    return lines + [line]


def text_block_height(lines: list[str]) -> int:
    return len(lines) * GLYPH_H + (len(lines) - 1) * LINE_GAP


def draw_icon(draw: ImageDraw.ImageDraw, xy: tuple[int, int], state: str, color) -> None:
    x, y = xy
    for dy, row in enumerate(ICONS[state]):
        for dx, cell in enumerate(row):
            if cell == "#":
                draw.point((x + dx, y + dy), fill=color)


def stepped_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill, border) -> None:
    """Rectangle with 2-step pixel corners and a 1-unit border."""
    x0, y0, x1, y1 = box
    if fill is not None:
        draw.rectangle((x0 + 1, y0 + 2, x1 - 1, y1 - 2), fill=fill)
        draw.rectangle((x0 + 2, y0 + 1, x1 - 2, y1 - 1), fill=fill)
    for segment in ((x0 + 2, y0, x1 - 2, y0), (x0 + 2, y1, x1 - 2, y1),
                    (x0, y0 + 2, x0, y1 - 2), (x1, y0 + 2, x1, y1 - 2)):
        draw.line(segment, fill=border)
    for point in ((x0 + 1, y0 + 1), (x1 - 1, y0 + 1), (x0 + 1, y1 - 1), (x1 - 1, y1 - 1)):
        draw.point(point, fill=border)


def tail(draw: ImageDraw.ImageDraw, base_x: int, base_y: int, fill, border, size: int = 6) -> None:
    """Stepped speech-bubble tail hanging from a box's bottom edge, pointing down-left."""
    tip = (base_x - size // 2, base_y + size)
    draw.polygon([(base_x, base_y), (base_x + size, base_y), tip], fill=fill)
    draw.line([(base_x, base_y), tip], fill=border)
    draw.line([(base_x + size, base_y), tip], fill=border)
    draw.line((base_x + 1, base_y, base_x + size - 1, base_y), fill=fill)
