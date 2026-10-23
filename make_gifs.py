"""Render one 240x240 GIF per screen state from an OpenPets spritesheet.

The pet talks through a speech bubble. Bubble styles:
  neon     dark pixel-art bubble with a glowing colored outline (default)
  sticker  white pixel-art bubble with a black outline and a colored state icon
  rpg      retro game dialog box across the top with the pet's name tab
  soft     smooth rounded bubble with a regular font

Usage: uv run --with pillow make_gifs.py [--pet miss-minute] [--style neon] [--out gifs]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import pixel_art as px
from states import STATES

PETS_DIR = Path.home() / "Library/Application Support/@open-pets/desktop/pets"
CELL_W, CELL_H = 192, 208
SIZE = 240
LOW = SIZE // px.PX  # pixel-art layer resolution
MARGIN = 12  # empty border around everything, in screen pixels
M = MARGIN // px.PX  # the same margin in pixel-art units
PET_H = 140
PET_W = round(CELL_W * PET_H / CELL_H)
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"

WHITE = (250, 248, 240)
INK = (24, 24, 28)


def dim(color, factor: float):
    return tuple(round(c * factor) for c in color)


def typed(lines: list[str], dots: int | None) -> list[str]:
    """Animate a trailing '...' as typing dots without changing the layout."""
    if dots is None or not lines[-1].endswith("..."):
        return lines
    return lines[:-1] + [lines[-1][:-3] + "." * dots]


# --- pixel-art bubbles -------------------------------------------------------------------

def pixel_bubble(layer: Image.Image, state: str, text: str, accent, frame: int, dots, style: str) -> None:
    d = ImageDraw.Draw(layer)
    x0, y0, x1 = 48, M + 1, LOW - M - 1
    icon_w, pad = 7, 5
    text_x = x0 + pad + icon_w + 4
    lines = px.wrap(text, x1 - pad - text_x)
    content_h = max(px.GLYPH_H, px.text_block_height(lines))
    y1 = y0 + content_h + 2 * pad
    pulse = state == "waiting" and frame % 2 == 1
    tail_x = x0 + 8

    if style == "sticker":
        fill, border, ink, icon = WHITE, INK, INK, accent
        halo = accent if pulse else WHITE
    else:  # neon
        fill, border, ink, icon = (14, 14, 24), accent, accent, accent
        halo = dim(accent, 0.75 if pulse else 0.35)

    px.stepped_box(d, (x0 - 1, y0 - 1, x1 + 1, y1 + 1), fill=halo, border=halo)
    px.tail(d, tail_x - 1, y1, fill=halo, border=halo, size=8)
    px.stepped_box(d, (x0, y0, x1, y1), fill=fill, border=border)
    px.tail(d, tail_x, y1, fill=fill, border=border)

    middle = (y0 + y1) // 2
    px.draw_icon(d, (x0 + pad, middle - px.GLYPH_H // 2), state, icon)
    y = middle - px.text_block_height(lines) // 2
    for line in typed(lines, dots):
        px.draw_text(d, (text_x, y), line, ink)
        y += px.GLYPH_H + px.LINE_GAP


def rpg_dialog(layer: Image.Image, state: str, text: str, accent, frame: int, dots, name: str) -> None:
    d = ImageDraw.Draw(layer)
    x0, y0, x1 = M, M + 6, LOW - M - 1
    y1 = y0 + 30
    px.stepped_box(d, (x0, y0, x1, y1), fill=(20, 26, 70), border=WHITE)
    px.stepped_box(d, (x0 + 2, y0 + 2, x1 - 2, y1 - 2), fill=None, border=(110, 120, 175))

    label = name.upper()
    tab = (x0 + 5, y0 - 6, x0 + 5 + px.text_width(label) + 7, y0 + 5)
    px.stepped_box(d, tab, fill=accent, border=WHITE)
    px.draw_text(d, (tab[0] + 4, tab[1] + 3), label, INK)

    lines = px.wrap(text, x1 - x0 - 16)
    y = y0 + 9
    for line in typed(lines, dots):
        px.draw_text(d, (x0 + 7, y), line, WHITE)
        y += px.GLYPH_H + px.LINE_GAP
    if frame % 2 == 0:  # blinking "more" arrow
        ax, ay = x1 - 9, y1 - 7
        for i, width in enumerate((5, 3, 1)):
            d.line((ax + i, ay + i, ax + i + width - 1, ay + i), fill=accent)


# --- message notification ----------------------------------------------------------------

MESSAGE_ACCENT = (230, 90, 210)
MESSAGE_LINES = 5
MESSAGE_PET_H = 104
MESSAGE_ICON = ["#######", "##...##", "#.#.#.#", "#..#..#", "#.....#", "#.....#", "#######"]


WAITING_ACCENT = STATES["waiting"][4]
MEETING_ACCENT = (90, 170, 255)
LOCK_ICON = ["..###..", ".#...#.", ".#...#.", "#######", "###.###", "###.###", "#######"]
CARDS = {  # --card kind -> (accent, icon)
    "running": ((60, 200, 220), ["#######", ".#...#.", "..#.#..", "...#...", "..#.#..", ".#...#.", "#######"]),
    "ok": ((80, 220, 100), px.ICONS["done"]),
    "failed": ((255, 70, 70), px.ICONS["error"]),
    "sso": ((255, 150, 40), LOCK_ICON),
}
MEETING_ICON = [".#...#.", "#######", "#.....#", "#.#.#.#", "#.....#", "#.#.#.#", "#######"]


def message_bubble(layer: Image.Image, title: str, body: list[str], frame: int,
                   accent=MESSAGE_ACCENT, icon=MESSAGE_ICON) -> None:
    """Wide neon bubble: icon + title on the first line, already-wrapped body lines below."""
    d = ImageDraw.Draw(layer)
    x0, y0, x1 = M, M + 1, LOW - M - 1
    pad = 5
    y1 = y0 + 2 * pad + px.text_block_height([title] + body)
    halo = dim(accent, 0.75 if frame % 2 else 0.35)

    px.stepped_box(d, (x0 - 1, y0 - 1, x1 + 1, y1 + 1), fill=halo, border=halo)
    px.tail(d, x0 + 13, y1, fill=halo, border=halo, size=8)
    px.stepped_box(d, (x0, y0, x1, y1), fill=(14, 14, 24), border=accent)
    px.tail(d, x0 + 14, y1, fill=(14, 14, 24), border=accent)

    for dy, row in enumerate(icon):
        for dx, cell in enumerate(row):
            if cell == "#":
                d.point((x0 + pad + dx, y0 + pad + dy), fill=accent)
    px.draw_text(d, (x0 + pad + 11, y0 + pad), title, accent)
    y = y0 + pad + px.GLYPH_H + px.LINE_GAP
    for line in body:
        px.draw_text(d, (x0 + pad, y), line, (235, 235, 240))
        y += px.GLYPH_H + px.LINE_GAP


def message_width() -> int:
    return (LOW - M - 1) - M - 2 * 5


def chat_message(sender: str, text: str) -> tuple[str, list[str]]:
    width = message_width()
    title = px.wrap(px.normalize_text(sender) + ":", width - 11, max_lines=1)[0]
    return title, px.wrap(px.normalize_text(text) or "...", width, max_lines=MESSAGE_LINES - 1)


def waiting_message(lines: list[str]) -> tuple[str, list[str]]:
    """'I NEED YOU!' plus one entry per waiting session, each wrapped on its own."""
    width, body = message_width(), []
    for line in lines:
        body += px.wrap(px.normalize_text(line), width)
    if len(body) > MESSAGE_LINES - 1:
        body = body[:MESSAGE_LINES - 2] + [f"+{len(lines) - 1} MORE..."] if len(lines) > 1 \
            else px.wrap(" ".join(body), width, max_lines=MESSAGE_LINES - 1)
    return "I NEED YOU!", body or ["..."]


def build_message_gif(sheet: Image.Image, title: str, body: list[str], out: Path,
                      accent=MESSAGE_ACCENT, icon=MESSAGE_ICON) -> int:
    """GIF of the pet waving under a wide bubble (chat message or attention request)."""
    row, frames, loop_ms = 3, 4, 1000  # waving
    pet_w = round(CELL_W * MESSAGE_PET_H / CELL_H)
    images = []
    for col in range(frames):
        canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        pet = cell.resize((pet_w, MESSAGE_PET_H), Image.LANCZOS)
        canvas.paste(pet, (MARGIN, SIZE - MARGIN - MESSAGE_PET_H), pet)
        layer = Image.new("RGBA", (LOW, LOW), (0, 0, 0, 0))
        message_bubble(layer, title, body, col, accent, icon)
        big = layer.resize((SIZE, SIZE), Image.NEAREST)
        canvas.paste(big, (0, 0), big)
        images.append(canvas.quantize(colors=64, dither=Image.Dither.NONE))
    images[0].save(out, save_all=True, append_images=images[1:], duration=loop_ms // frames, loop=0, optimize=True)
    return out.stat().st_size


# --- Calcifer review notification --------------------------------------------------------

# verdict -> (title, subtitle, color, pet spritesheet row, frames, loop ms)
CALCIFER_VERDICTS = {
    "approved": ("APROBADO", "", (80, 220, 100), 4, 5, 840),
    "approved_suggestions": ("APROBADO", "CON SUGERENCIAS", (240, 210, 60), 4, 5, 840),
    "rejected": ("RECHAZADO", "", (255, 70, 70), 5, 8, 1220),
    "human_review": ("REVISION", "HUMANA", (255, 150, 40), 6, 6, 1200),
    None: ("COMENTARIO", "", (255, 140, 60), 3, 4, 1000),
}
CALCIFER_PET_H = 78

# Original pixel-art fire sprite (two flicker frames): o flame, y core, w eye, k pupil/mouth.
FLAME_TIPS = [
    ["......o......", ".....oo......", "....ooo..o...", "...oooo.oo...", "..ooyyooooo.."],
    [".......o.....", "..o....oo....", "..oo..ooo....", "...oo.oooo...", "..oooyyoooo.."],
]
FLAME_BODY = [
    ".ooyyyyyyooo.", ".oyyyyyyyyyo.", "oyywwyyywwyyo", "oyywkyyywkyyo", "oyywwyyywwyyo",
    "oyyyyyyyyyyyo", "oyyykkkkkyyyo", ".oyyykkkyyyo.", ".ooyyyyyyyoo.", "..ooooooooo..", "...ooooooo...",
]
FLAME_COLORS = {"o": (255, 110, 20), "y": (255, 205, 60), "w": (255, 255, 255), "k": (35, 20, 20)}


def draw_flame(d: ImageDraw.ImageDraw, xy: tuple[int, int], frame: int) -> None:
    x, y = xy
    for dy, row in enumerate(FLAME_TIPS[frame % 2] + FLAME_BODY):
        for dx, cell in enumerate(row):
            if cell in FLAME_COLORS:
                d.point((x + dx, y + dy), fill=FLAME_COLORS[cell])


def draw_tall_text(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color) -> None:
    """The 5x7 font stretched to double height, for headlines."""
    x, y = xy
    for ch in text:
        rows = px.glyph(ch)
        for dy, row in enumerate(rows):
            for dx, cell in enumerate(row):
                if cell == "#":
                    d.line((x + dx, y + 2 * dy, x + dx, y + 2 * dy + 1), fill=color)
        x += len(rows[0]) + px.LETTER_GAP


def calcifer_card(layer: Image.Image, verdict: str | None, reason: str, frame: int) -> None:
    title, subtitle, color, *_ = CALCIFER_VERDICTS.get(verdict, CALCIFER_VERDICTS[None])
    d = ImageDraw.Draw(layer)
    x0, y0, x1 = M, M + 1, LOW - M - 1
    pad = 5
    reason_lines = px.wrap(px.normalize_text(reason) or "...", x1 - x0 - 2 * pad, max_lines=2 if subtitle else 3)
    title_y = y0 + pad + px.GLYPH_H + 2
    subtitle_y = title_y + 2 * px.GLYPH_H + 3  # below the double-height title
    reason_y = (subtitle_y + px.GLYPH_H if subtitle else subtitle_y - 3) + 5
    y1 = reason_y + px.text_block_height(reason_lines) + pad
    halo = dim(color, 0.75 if frame % 2 else 0.35)
    px.stepped_box(d, (x0 - 1, y0 - 1, x1 + 1, y1 + 1), fill=halo, border=halo)
    px.stepped_box(d, (x0, y0, x1, y1), fill=(14, 14, 24), border=color)

    draw_flame(d, (x0 + pad, y0 + pad), frame)
    text_x = x0 + pad + 13 + 5
    px.draw_text(d, (text_x, y0 + pad), "CALCIFER", FLAME_COLORS["o"])
    draw_tall_text(d, (text_x, title_y), title, color)
    if subtitle:
        px.draw_text(d, (x0 + pad, subtitle_y), subtitle, color)
    y = reason_y
    for line in reason_lines:
        px.draw_text(d, (x0 + pad, y), line, (235, 235, 240))
        y += px.GLYPH_H + px.LINE_GAP


def build_calcifer_gif(sheet: Image.Image, verdict: str | None, reason: str, out: Path) -> int:
    """Calcifer's review verdict, with the pet reacting below (jumps, fails or waits)."""
    *_, row, frames, loop_ms = CALCIFER_VERDICTS.get(verdict, CALCIFER_VERDICTS[None])
    pet_w = round(CELL_W * CALCIFER_PET_H / CELL_H)
    images = []
    for col in range(frames):
        canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        pet = cell.resize((pet_w, CALCIFER_PET_H), Image.LANCZOS)
        canvas.paste(pet, ((SIZE - pet_w) // 2, SIZE - MARGIN - CALCIFER_PET_H), pet)
        layer = Image.new("RGBA", (LOW, LOW), (0, 0, 0, 0))
        calcifer_card(layer, verdict, reason, col)
        big = layer.resize((SIZE, SIZE), Image.NEAREST)
        canvas.paste(big, (0, 0), big)
        images.append(canvas.quantize(colors=64, dither=Image.Dither.NONE))
    images[0].save(out, save_all=True, append_images=images[1:], duration=max(loop_ms // frames, 60),
                   loop=0, optimize=True)
    return out.stat().st_size


# --- idle summary: story-point progress --------------------------------------------------

def summary_color(percent: int | None):
    if percent is None:
        return (150, 150, 150)
    return (80, 220, 100) if percent < 75 else (240, 210, 60) if percent <= 100 else (255, 70, 70)


SUMMARY_PET_H = 40
ROW_STYLES = {  # summary row kind -> (color, icon)
    "meeting": (MEETING_ACCENT, MEETING_ICON),
    "deploy_ok": ((80, 220, 100), px.ICONS["done"]),
    "deploy_failed": ((255, 70, 70), px.ICONS["error"]),
    "slack": (MESSAGE_ACCENT, MESSAGE_ICON),
    "agents": ((190, 120, 255), ["..#.#..", "#######", "#.#.#.#", "#######", "#.....#", "#.###.#", "#######"]),
}


def task_box(d: ImageDraw.ImageDraw, title: str, percent: int | None, line: str, text: str, frame: int) -> int:
    """Story-point box at the top; returns its bottom y."""
    color = summary_color(percent)
    x0, y0, x1 = M, M + 1, LOW - M - 1
    pad = 4
    bar_y = y0 + pad + px.GLYPH_H + 3
    big_y = bar_y + 5 + 3
    body_y = big_y + 2 * px.GLYPH_H + 3
    body = [px.truncate(px.normalize_text(text) or "...", x1 - x0 - 2 * pad)]
    y1 = body_y + px.GLYPH_H + pad
    halo = dim(color, 0.35 if frame % 2 else 0.25)
    px.stepped_box(d, (x0 - 1, y0 - 1, x1 + 1, y1 + 1), fill=halo, border=halo)
    px.stepped_box(d, (x0, y0, x1, y1), fill=(14, 14, 24), border=color)
    px.draw_text(d, (x0 + pad, y0 + pad), px.normalize_text(title), color)
    bar_x0, bar_x1 = x0 + pad, x1 - pad
    d.rectangle((bar_x0, bar_y, bar_x1, bar_y + 4), outline=dim(color, 0.6))
    if percent:
        filled = bar_x0 + 1 + round((bar_x1 - bar_x0 - 2) * min(percent, 100) / 100)
        d.rectangle((bar_x0 + 1, bar_y + 1, max(bar_x0 + 1, filled), bar_y + 3), fill=color)
    big = "--" if percent is None else f"{percent}%"
    draw_tall_text(d, (x0 + pad, big_y), big, color)
    px.draw_text(d, (x0 + pad + px.text_width(big) + 6, big_y + px.GLYPH_H // 2 + 1), px.normalize_text(line),
                 (235, 235, 240))
    px.draw_text(d, (x0 + pad, body_y), body[0], (170, 170, 180))
    return y1


def summary_layer(layer: Image.Image, title: str, percent: int | None, line: str, text: str,
                  rows: list, frame: int) -> None:
    d = ImageDraw.Draw(layer)
    y = (task_box(d, title, percent, line, text, frame) + 6) if title else M + 2
    x0, x1 = M + 1, LOW - M - 1
    for kind, row_text in rows[:4]:
        color, icon = ROW_STYLES.get(kind, ((200, 200, 200), px.ICONS["idle"]))
        for dy, icon_row in enumerate(icon):
            for dx, cell in enumerate(icon_row):
                if cell == "#":
                    d.point((x0 + dx, y + dy), fill=color)
        # leave the bottom-right corner to the pet
        width = x1 - (x0 + 10) - (SUMMARY_PET_W_ART + 2 if y + px.GLYPH_H > PET_TOP_ART else 0)
        px.draw_text(d, (x0 + 10, y), px.truncate(row_text, width), (225, 225, 230))
        y += px.GLYPH_H + 3


SUMMARY_PET_W = round(CELL_W * SUMMARY_PET_H / CELL_H)
SUMMARY_PET_W_ART = SUMMARY_PET_W // px.PX + 1
PET_TOP_ART = (SIZE - MARGIN - SUMMARY_PET_H) // px.PX


def build_summary_gif(sheet: Image.Image, title: str, percent: int | None, line: str, text: str,
                      rows: list, out: Path) -> int:
    """The idle screen: story-point progress and today's facts, with a small resting pet.

    Over budget, the pet switches to its worried ("failed") animation.
    """
    over_budget = percent is not None and percent > 100
    row, frames, loop_ms = (5, 8, 1220) if over_budget else (0, 6, 5500)  # failed / idle
    images = []
    for col in range(frames):
        canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        pet = cell.resize((SUMMARY_PET_W, SUMMARY_PET_H), Image.LANCZOS)
        canvas.paste(pet, (SIZE - MARGIN - SUMMARY_PET_W, SIZE - MARGIN - SUMMARY_PET_H), pet)
        layer = Image.new("RGBA", (LOW, LOW), (0, 0, 0, 0))
        summary_layer(layer, title, percent, line, text, rows, col)
        big = layer.resize((SIZE, SIZE), Image.NEAREST)
        canvas.paste(big, (0, 0), big)
        images.append(canvas.quantize(colors=64, dither=Image.Dither.NONE))
    images[0].save(out, save_all=True, append_images=images[1:], duration=loop_ms // frames, loop=0, optimize=True)
    return out.stat().st_size


# --- smooth bubble -----------------------------------------------------------------------

def soft_bubble(canvas: Image.Image, state: str, text: str, accent, frame: int, dots) -> None:
    if state == "waiting" and frame % 2 == 1:
        return
    draw = ImageDraw.Draw(canvas)
    box = (96, MARGIN, SIZE - MARGIN, MARGIN + 76)
    tail_pts = [(114, 76), (138, 80), (100, 108)]
    draw.rounded_rectangle(box, radius=16, fill=WHITE, outline=accent, width=3)
    draw.polygon(tail_pts, fill=WHITE)
    draw.line([tail_pts[0], tail_pts[2], tail_pts[1]], fill=accent, width=3, joint="curve")
    for size in range(22, 11, -1):
        font = ImageFont.truetype(FONT_PATH, size, index=1)
        words, lines, line = text.split(), [], ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= 112 or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
        line_h = font.getbbox("Ag")[3] + 3
        if len(lines) * line_h <= 60 and all(draw.textlength(l, font=font) <= 112 for l in lines):
            break
    y = box[1] + (box[3] - box[1] - len(lines) * line_h) // 2
    for line in typed(lines, dots):
        x = box[0] + (box[2] - box[0] - draw.textlength(line, font=font)) // 2
        draw.text((x, y), line, font=font, fill=INK)
        y += line_h


# --- frames ------------------------------------------------------------------------------

def render_frame(cell: Image.Image, state: str, frame: int, style: str, pet_name: str) -> Image.Image:
    _, _, _, text, accent = STATES[state]
    dots = frame % 4 if text.endswith("...") else None
    canvas = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    pet = cell.resize((PET_W, PET_H), Image.LANCZOS)
    pet_x = (SIZE - PET_W) // 2 if style == "rpg" else MARGIN
    canvas.paste(pet, (pet_x, SIZE - MARGIN - PET_H), pet)

    if style == "soft":
        soft_bubble(canvas, state, text, accent, frame, dots)
        return canvas
    layer = Image.new("RGBA", (LOW, LOW), (0, 0, 0, 0))
    if style == "rpg":
        rpg_dialog(layer, state, text, accent, frame, dots, pet_name)
    else:
        pixel_bubble(layer, state, text, accent, frame, dots, style)
    big = layer.resize((SIZE, SIZE), Image.NEAREST)
    canvas.paste(big, (0, 0), big)
    return canvas


def build_state_gif(sheet: Image.Image, state: str, out: Path, style: str, pet_name: str) -> int:
    row, frames, loop_ms, _, _ = STATES[state]
    images = []
    for col in range(frames):
        cell = sheet.crop((col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H))
        frame = render_frame(cell, state, col, style, pet_name)
        images.append(frame.quantize(colors=64, dither=Image.Dither.NONE))
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=max(loop_ms // frames, 60),
        loop=0,
        optimize=True,
    )
    return out.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pet", default="miss-minute")
    parser.add_argument("--outfit", default="default", help="OpenPets wardrobe outfit id (default look otherwise)")
    parser.add_argument("--style", choices=["sticker", "neon", "rpg", "soft"], default="neon")
    parser.add_argument("--out", default="gifs")
    parser.add_argument("--only", default="", help="comma-separated states to render (default: all)")
    parser.add_argument("--summary-title", default=None,
                        help="render only the idle summary GIF (op-summary.gif) with this title")
    parser.add_argument("--summary-percent", default="", help="story-point budget used, empty if unknown")
    parser.add_argument("--summary-line", default="", help="e.g. '10 H LEFT'")
    parser.add_argument("--summary-rows", default="[]", help='JSON [[kind, text], ...] (meeting, deploy_ok, ...)')
    parser.add_argument("--card", default=None, choices=["running", "ok", "failed", "sso"],
                        help="render only a generic notification card (op-card.gif) with --card-title and --message-text")
    parser.add_argument("--card-title", default="")
    parser.add_argument("--meeting-title", default=None,
                        help="render only a meeting reminder GIF (op-meeting.gif); text from --message-text")
    parser.add_argument("--waiting-detail", default=None,
                        help="render only the detailed I-need-you GIF; '|'-separated lines (op-waiting-detail.gif)")
    parser.add_argument("--message-from", default=None, help="render only a chat message GIF (op-message.gif)")
    parser.add_argument("--message-text", default="", help="text for --message-from or --calcifer")
    parser.add_argument("--calcifer", default=None, choices=[k for k in CALCIFER_VERDICTS if k] + ["comment"],
                        help="render only a Calcifer review GIF (op-calcifer.gif) with this verdict")
    args = parser.parse_args()

    pet_dir = PETS_DIR / args.pet
    try:
        meta = json.loads((pet_dir / "pet.json").read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"pet '{args.pet}' not found in {PETS_DIR}: {exc}")
    sheet_path = pet_dir / meta.get("spritesheetPath", "spritesheet.webp")
    if args.outfit and args.outfit != "default":
        outfits = {o.get("id"): o for o in (meta.get("wardrobe") or {}).get("outfits", [])}
        if args.outfit in outfits:
            sheet_path = pet_dir / outfits[args.outfit]["spritesheetPath"]
        else:
            print(f"unknown outfit {args.outfit!r} for {args.pet}; using the default look")
    if sheet_path.suffix.lower() not in (".webp", ".png"):
        raise SystemExit(f"unsupported spritesheet format: {sheet_path.name}")
    sheet = Image.open(sheet_path).convert("RGBA")
    if sheet.width < 8 * CELL_W or sheet.height < 9 * CELL_H:
        raise SystemExit(f"unexpected spritesheet size {sheet.size} for {args.pet}")
    pet_name = meta.get("displayName", args.pet)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.calcifer is not None:
        verdict = None if args.calcifer == "comment" else args.calcifer
        size = build_calcifer_gif(sheet, verdict, args.message_text, out_dir / "op-calcifer.gif")
        print(f"op-calcifer.gif  {size // 1024} KB")
        return
    if args.summary_title is not None:
        percent = int(args.summary_percent) if args.summary_percent.strip() else None
        size = build_summary_gif(sheet, args.summary_title, percent, args.summary_line, args.message_text,
                                 json.loads(args.summary_rows), out_dir / "op-summary.gif")
        print(f"op-summary.gif  {size // 1024} KB")
        return
    if args.card is not None:
        accent, icon = CARDS[args.card]
        body = px.wrap(px.normalize_text(args.message_text) or "...", message_width(), max_lines=MESSAGE_LINES - 1)
        size = build_message_gif(sheet, px.normalize_text(args.card_title or args.card), body,
                                 out_dir / "op-card.gif", accent=accent, icon=icon)
        print(f"op-card.gif  {size // 1024} KB")
        return
    if args.meeting_title is not None:
        body = px.wrap(px.normalize_text(args.message_text) or "...", message_width(), max_lines=MESSAGE_LINES - 1)
        size = build_message_gif(sheet, px.normalize_text(args.meeting_title), body, out_dir / "op-meeting.gif",
                                 accent=MEETING_ACCENT, icon=MEETING_ICON)
        print(f"op-meeting.gif  {size // 1024} KB")
        return
    if args.waiting_detail is not None:
        title, body = waiting_message([l for l in args.waiting_detail.split("|") if l.strip()])
        size = build_message_gif(sheet, title, body, out_dir / "op-waiting-detail.gif",
                                 accent=WAITING_ACCENT, icon=px.ICONS["waiting"])
        print(f"op-waiting-detail.gif  {size // 1024} KB")
        return
    if args.message_from is not None:
        size = build_message_gif(sheet, *chat_message(args.message_from, args.message_text), out_dir / "op-message.gif")
        print(f"op-message.gif  {size // 1024} KB")
        return
    states = [s for s in args.only.split(",") if s] or list(STATES)
    unknown = [s for s in states if s not in STATES]
    if unknown:
        raise SystemExit(f"unknown state(s): {', '.join(unknown)}")
    total = 0
    for state in states:
        size = build_state_gif(sheet, state, out_dir / f"op-{state}.gif", args.style, pet_name)
        total += size
        print(f"op-{state}.gif  {size // 1024} KB")
    print(f"total  {total // 1024} KB")


if __name__ == "__main__":
    main()
