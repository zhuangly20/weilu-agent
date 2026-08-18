"""成长手记 → 明信片渲染（Pillow 程序化绘制，明亮暖色风，与 logo 同一视觉体系）。"""
from __future__ import annotations

import io
import random
import re
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_PATH = "assets/fonts/LXGWWenKai-Regular.ttf"

W, H = 1080, 1440

# 小清心暖色明亮系
CREAM_TOP = (255, 249, 240)
CREAM_BOTTOM = (255, 231, 205)
INK = (90, 70, 54)            # 主文字·暖棕
PAPER = INK
PAPER_DIM = (166, 137, 110)   # 次级文字
FIRE_ORANGE = (240, 96, 60)   # 珊瑚橘
FIRE_YELLOW = (255, 196, 107)
GOLD = (232, 154, 43)         # 暖金
PURPLE = (155, 126, 222)      # 小清心紫
SOFT_PINK = (244, 156, 187)   # 小清心粉


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _shorten(text: str, limit: int) -> str:
    """明信片截断：优先在标点处断句，否则硬切加省略号。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for stop in "。！？；，、":
        pos = cut.rfind(stop)
        if pos >= limit // 2:
            return cut[: pos + 1]
    return cut[: limit - 1] + "…"


def parse_handnote(text: str) -> dict:
    """从成长手记文本解析明信片素材；解析失败走兜底文案。"""
    sections: dict[str, str] = {}
    current = None
    takeaways: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^[🕯🪵🔥✨🌱]\s*([^：:]+)[：:]\s*(.*)$", s)
        if m:
            current = m.group(1).strip()
            sections[current] = m.group(2).strip()
            continue
        if current == "值得带走的" and s.startswith("·"):
            takeaways.append(s.lstrip("·").strip())
    message = sections.get("留给下次的", "").strip()
    if not message:
        message = "圆桌会记得你说过的每一句话。"
    if not takeaways:
        takeaways = ["慢一点，也没关系。"]
    theme = sections.get("本场主题", "")
    return {
        "theme": theme,
        "message": _shorten(message, 40),
        "takeaways": [_shorten(t, 20) for t in takeaways[:3]],
    }


def _draw_background(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(CREAM_TOP[i] + (CREAM_BOTTOM[i] - CREAM_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)
    # 暖色小点缀（固定种子，风格取自小清心四色）
    rng = random.Random(2026)
    palette = [GOLD, PURPLE, SOFT_PINK, FIRE_ORANGE]
    for i in range(46):
        x, y = rng.randint(0, W), rng.randint(0, int(H * 0.6))
        r = rng.choice([3, 4, 5, 6])
        color = palette[i % 4]
        alpha = rng.randint(38, 80)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(*color, alpha))
    # 底部暖阳光晕
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([W // 2 - 340, H - 560, W // 2 + 340, H - 40], fill=(255, 196, 107, 80))
    gdraw.ellipse([W // 2 - 170, H - 420, W // 2 + 170, H - 150], fill=(255, 158, 102, 70))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60)))


def _draw_fire(draw: ImageDraw.ImageDraw) -> None:
    cx, base = W // 2, H - 230
    # 柴
    draw.line([(cx - 150, base + 30), (cx + 150, base + 6)], fill=(138, 90, 52), width=34)
    draw.line([(cx - 150, base + 64), (cx + 150, base + 40)], fill=(160, 106, 63), width=34)
    # 火焰
    draw.polygon(
        [(cx, base - 170), (cx + 62, base - 40), (cx + 20, base + 18), (cx - 20, base + 18), (cx - 62, base - 40)],
        fill=FIRE_ORANGE,
    )
    draw.polygon(
        [(cx, base - 104), (cx + 30, base - 30), (cx - 30, base - 30)],
        fill=FIRE_YELLOW,
    )


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, buf = [], ""
    for ch in text:
        if draw.textlength(buf + ch, font=font) > max_w:
            lines.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        lines.append(buf)
    return lines


def render_postcard(
    theme_label: str,
    message: str,
    takeaways: list[str],
    member_names: list[str],
    today: date | None = None,
) -> bytes:
    today = today or date.today()
    img = Image.new("RGBA", (W, H))
    _draw_background(img)
    draw = ImageDraw.Draw(img)
    # 明信片内框
    draw.rounded_rectangle([44, 44, W - 44, H - 44], radius=28, outline=(*GOLD, 120), width=3)
    # 邮票角标
    draw.rounded_rectangle([W - 250, 78, W - 88, 240], radius=10, outline=GOLD, width=3)
    draw.text((W - 169, 120), "圆", font=_font(64), fill=GOLD, anchor="ma")
    draw.text((W - 169, 190), "桌", font=_font(64), fill=GOLD, anchor="ma")

    x = 110
    # 标题
    draw.text((x, 110), "清 心 圆 桌", font=_font(76), fill=PAPER)
    sub = f"{theme_label} · {today.month}月{today.day}日"
    draw.text((x, 224), sub, font=_font(36), fill=PAPER_DIM)
    draw.line([(x, 306), (W - 300, 306)], fill=(*GOLD, 140), width=2)

    y = 360
    draw.text((x, y), "小晴的寄语", font=_font(40), fill=GOLD)
    y += 70
    for line in _wrap(draw, message, _font(44), W - 2 * x)[:2]:
        draw.text((x, y), line, font=_font(44), fill=PAPER)
        y += 70

    y += 48
    draw.text((x, y), "值得带走的", font=_font(40), fill=GOLD)
    y += 70
    for t in takeaways:
        for line in _wrap(draw, "· " + t, _font(42), W - 2 * x)[:1]:
            draw.text((x, y), line, font=_font(42), fill=PAPER)
        y += 64

    # 炉火
    _draw_fire(draw)
    # 落款
    draw.text((W // 2, H - 108), f"小晴 与 {'、'.join(member_names)}", font=_font(34),
              fill=PAPER_DIM, anchor="ma")
    draw.text((W // 2, H - 58), "—— 阳光正好，随时回来 ——", font=_font(26),
              fill=PAPER_DIM, anchor="ma")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
