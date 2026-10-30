"""Renders storyboard panels onto Japanese-style e-konte (絵コンテ) pages.

Columns: CUT | PICTURE | ACTION | DIALOGUE | LENGTH, with each cut's window in the scene.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAGE_W, PAGE_H = 1800, 2546  # A4 ratio
MARGIN = 60
HEADER_H = 190
COLS = [("CUT", 100), ("PICTURE", 760), ("ACTION", 400), ("DIALOGUE", 280), ("LENGTH", 0)]  # LENGTH takes the remainder
PAPER = (247, 244, 236)
INK = (30, 30, 34)
FAINT = (150, 146, 138)
ACCENT = (196, 48, 43)

FONT_DIRS = [Path(__file__).parent / "fonts", Path("/usr/share/fonts"), Path("/System/Library/Fonts"), Path("/Library/Fonts")]
LATIN_FONTS = ["DejaVuSans.ttf", "NotoSans-Regular.ttf", "Helvetica.ttc", "Arial.ttf"]
LATIN_BOLD = ["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "Helvetica.ttc", "Arial Bold.ttf"]
CJK_FONTS = ["NotoSansJP-Bold.otf", "NotoSansCJK-Bold.ttc", "NotoSansCJKjp-Bold.otf", "NotoSansJP-Bold.ttf", "ヒラギノ角ゴシック W6.ttc", "Hiragino Sans GB.ttc"]


def _find(names):
    for d in FONT_DIRS:
        if not d.exists():
            continue
        for name in names:
            hits = list(d.rglob(name))
            if hits:
                return str(hits[0])
    return None


def _font(names, size):
    path = _find(names)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def _wrap(draw, text, font, width):
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= width:
                line = trial
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _fit(img, w, h):
    """Center-crop to the frame's aspect ratio, then resize."""
    iw, ih = img.size
    target = w / h
    if iw / ih > target:
        nw = int(ih * target)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / target)
        img = img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
    return img.resize((w, h), Image.LANCZOS)


def _secs(seconds):
    return f"{round(seconds, 1):g}s"


def render_pages(plan, panels, cuts_per_page=5):
    """plan: StoryboardPlan; panels: list of PIL images in shot order. Returns list of PIL pages."""
    f_title = _font(LATIN_BOLD, 46)
    f_sub = _font(LATIN_FONTS, 24)
    f_head = _font(LATIN_BOLD, 22)
    f_cut = _font(LATIN_BOLD, 54)
    f_body = _font(LATIN_FONTS, 25)
    f_small = _font(LATIN_FONTS, 21)
    f_time = _font(LATIN_BOLD, 34)
    cjk_path = _find(CJK_FONTS)
    f_kanji = ImageFont.truetype(cjk_path, 64) if cjk_path else None

    widths = [w for _, w in COLS]
    widths[-1] = PAGE_W - 2 * MARGIN - sum(widths[:-1])
    xs = [MARGIN]
    for w in widths:
        xs.append(xs[-1] + w)

    pic_w = widths[1] - 30
    row_h = (PAGE_H - MARGIN - HEADER_H - 50 - MARGIN) // cuts_per_page
    pic_h = min(int(pic_w * 9 / 16), row_h - 30)
    pic_w = int(pic_h * 16 / 9)

    shots = plan.shots
    n_pages = math.ceil(len(shots) / cuts_per_page)
    total = sum(s.seconds for s in shots)
    pages, elapsed = [], 0.0

    for p in range(n_pages):
        page = Image.new("RGB", (PAGE_W, PAGE_H), PAPER)
        d = ImageDraw.Draw(page)

        # Header
        if f_kanji:
            d.text((MARGIN, MARGIN - 6), "絵コンテ", font=f_kanji, fill=ACCENT)
            tx = MARGIN + d.textlength("絵コンテ", font=f_kanji) + 30
        else:
            tx = MARGIN
        d.text((tx, MARGIN), plan.title.upper(), font=f_title, fill=INK)
        logline, room = plan.logline, PAGE_W - MARGIN - tx
        if d.textlength(logline, font=f_sub) > room:
            while d.textlength(logline + "…", font=f_sub) > room and " " in logline:
                logline = logline.rsplit(" ", 1)[0].rstrip(",.;")
            logline += "…"
        d.text((tx, MARGIN + 62), logline, font=f_sub, fill=FAINT)
        meta = f"PACING: {plan.pacing.upper()}   ·   {len(shots)} CUTS   ·   {_secs(total)} SCENE   ·   PAGE {p + 1}/{n_pages}"
        d.text((MARGIN, MARGIN + 110), meta, font=f_head, fill=INK)

        # Column headers
        top = MARGIN + HEADER_H
        d.rectangle([xs[0], top, xs[-1], top + 50], fill=INK)
        for (label, _), x, w in zip(COLS, xs, widths):
            tw = d.textlength(label, font=f_head)
            d.text((x + (w - tw) / 2, top + 13), label, font=f_head, fill=PAPER)
        y0 = top + 50

        for r, shot in enumerate(shots[p * cuts_per_page:(p + 1) * cuts_per_page]):
            idx = p * cuts_per_page + r
            y = y0 + r * row_h
            d.line([xs[0], y + row_h, xs[-1], y + row_h], fill=INK, width=2)

            # CUT number
            num = str(shot.cut)
            d.text((xs[0] + (widths[0] - d.textlength(num, font=f_cut)) / 2, y + 20), num, font=f_cut, fill=INK)

            # PICTURE
            px = xs[1] + (widths[1] - pic_w) // 2
            py = y + (row_h - pic_h) // 2
            if idx < len(panels):
                page.paste(_fit(panels[idx].convert("RGB"), pic_w, pic_h), (px, py))
            d.rectangle([px - 1, py - 1, px + pic_w, py + pic_h], outline=INK, width=3)

            # ACTION: shot size / angle / move, then the action note
            tx, tw = xs[2] + 16, widths[2] - 32
            ty = y + 16
            camera = f"{shot.shot_size.upper()} · {shot.angle}"
            for line in _wrap(d, camera, f_head, tw):
                d.text((tx, ty), line, font=f_head, fill=ACCENT)
                ty += 28
            if shot.camera_move and shot.camera_move.lower() != "static":
                d.text((tx, ty), f"cam: {shot.camera_move}", font=f_small, fill=FAINT)
                ty += 28
            ty += 8
            for line in _wrap(d, shot.action, f_body, tw):
                d.text((tx, ty), line, font=f_body, fill=INK)
                ty += 32

            # DIALOGUE + SFX
            tx, tw = xs[3] + 14, widths[3] - 28
            ty = y + 16
            for line in _wrap(d, shot.dialogue, f_body, tw):
                d.text((tx, ty), line, font=f_body, fill=INK)
                ty += 32
            if shot.sfx:
                ty += 10
                for line in _wrap(d, f"SFX: {shot.sfx}", f_small, tw):
                    d.text((tx, ty), line, font=f_small, fill=FAINT)
                    ty += 26

            # LENGTH: cut length, then where the cut sits in the scene
            start = elapsed
            elapsed += shot.seconds
            cx = xs[4] + widths[4] / 2
            t1 = _secs(shot.seconds)
            d.text((cx - d.textlength(t1, font=f_time) / 2, y + 24), t1, font=f_time, fill=INK)
            t2 = f"{round(start, 1):g}–{_secs(elapsed)}"
            d.text((cx - d.textlength(t2, font=f_small) / 2, y + 70), t2, font=f_small, fill=FAINT)

        # Grid lines
        bottom = y0 + row_h * cuts_per_page
        for x in xs:
            d.line([x, top, x, bottom], fill=INK, width=2)
        d.rectangle([xs[0], top, xs[-1], bottom], outline=INK, width=3)
        pages.append(page)
    return pages
