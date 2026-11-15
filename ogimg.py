"""OpenGraph images (1200x630 PNG) generated locally with Pillow — no services.

og/index.png      — headline + current pass-rate stats (regenerated every run)
og/<slug>.png     — per-server card: status color, name, key numbers

If Pillow or any usable TTF font is missing, generation degrades to Pillow's
built-in default font, and if Pillow itself is missing sitegen skips images
gracefully (pages still get full og: meta tags pointing at og/index.png).
"""
from __future__ import annotations

import re
from pathlib import Path

STATUS_COLORS = {"pass": "#2ea043", "partial": "#d29922", "fail": "#cf222e", "untested": "#6e7681"}
STATUS_LABEL = {"pass": "PASS", "partial": "PARTIAL", "fail": "FAIL", "untested": "UNTESTED"}
BG = "#0d1117"
BORDER = "#30363d"
MUTED = "#8b949e"
FG = "#e6edf3"
LINK = "#58a6ff"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
FONT_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:  # noqa: BLE001
    HAVE_PIL = False


def _fonts():
    bold = regular = None
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            bold = p
            break
    for p in FONT_REGULAR_CANDIDATES:
        if Path(p).is_file():
            regular = p
            break
    return bold, regular


def _load(size: int, path: str | None):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def _chip(d, x, y, text, color, font, pad=14, h=52):
    w = d.textlength(text, font=font)
    d.rounded_rectangle([x, y, x + w + pad * 2, y + h], radius=10, fill="#161b22",
                        outline=color, width=2)
    d.text((x + pad, y + h / 2), text, font=font, fill=color, anchor="lm")
    return x + w + pad * 2 + 14


def available() -> bool:
    return HAVE_PIL


def index_image(path: Path, headline: str, stats: dict, base_url: str) -> bool:
    if not HAVE_PIL:
        return False
    bold, regular = _fonts()
    f_head = _load(64, bold)
    f_big = _load(30, bold)
    f_sub = _load(26, regular)
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 8, H], fill="#2f81f7")
    d.text((70, 60), "MCP HEALTH INDEX", font=f_big, fill=LINK)
    d.text((70, 118), "isolated smoke tests · run daily · zero curation", font=f_sub, fill=MUTED)
    y = 210
    for line in _wrap(d, headline, f_head, W - 140):
        d.text((70, y), line, font=f_head, fill=FG)
        y += 78
    x = 70
    for label, n, color in (("tested", stats["tested"], FG),
                            ("PASS", stats["pass"], STATUS_COLORS["pass"]),
                            ("PARTIAL", stats["partial"], STATUS_COLORS["partial"]),
                            ("FAIL", stats["fail"], STATUS_COLORS["fail"])):
        x = _chip(d, x, y + 40, f"{n} {label}", color, f_sub)
    d.text((70, H - 90), base_url.replace("https://", ""), font=f_sub, fill=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return True


def server_image(path: Path, slug: str, title: str, status: str, tools, call_s, tested: str | None,
                 base_url: str) -> bool:
    if not HAVE_PIL:
        return False
    bold, regular = _fonts()
    f_title = _load(54, bold)
    f_big = _load(40, bold)
    f_sub = _load(24, regular)
    color = STATUS_COLORS.get(status, "#6e7681")
    label = STATUS_LABEL.get(status, "UNTESTED")
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=color)
    d.text((70, 64), "MCP HEALTH INDEX", font=f_sub, fill=LINK)
    y = 130
    for line in _wrap(d, title, f_title, W - 140):
        d.text((70, y), line, font=f_title, fill=FG)
        y += 68
    # status bar
    bw = d.textlength(label, font=f_big) + 56
    d.rounded_rectangle([70, y + 30, 70 + bw, y + 30 + 64], radius=12, fill=color)
    d.text((70 + bw / 2, y + 30 + 32), label, font=f_big, fill="#ffffff", anchor="mm")
    if status == "pass" and tools is not None:
        d.text((70 + bw + 30, y + 30 + 32), f"{tools} tools", font=f_big, fill=FG, anchor="lm")
        if call_s is not None:
            d.text((70 + bw + 30 + d.textlength(f"{tools} tools", font=f_big) + 40, y + 30 + 32),
                   f"{int(call_s * 1000)}ms call", font=f_big, fill=MUTED, anchor="lm")
    elif status == "untested":
        d.text((70 + bw + 30, y + 30 + 32), "queued for testing", font=f_big, fill=MUTED, anchor="lm")
    if tested:
        d.text((70, H - 130), f"last tested {tested[:10]}", font=f_sub, fill=MUTED)
    d.text((70, H - 84), f"report: {base_url.replace('https://', '')}/server/{re.sub(r'[^a-z0-9._-]', '-', slug.lower())[:60]}",
           font=f_sub, fill=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return True
