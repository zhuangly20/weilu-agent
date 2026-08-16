"""成长手记 → 明信片渲染（Pillow 程序化绘制，深夜炉火风，与 logo 同一视觉体系）。"""
from __future__ import annotations

import io
import random
import re
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_PATH = "assets/fonts/LXGWWenKai-Regular.ttf"

W, H = 1080, 1440

# 夜色系
NIGHT_TOP = (22, 30, 46)
NIGHT_BOTTOM = (33, 44, 66)
PAPER = (245, 234, 216)       # 暖白
PAPER_DIM = (196, 186, 168)   # 次级文字
FIRE_ORANGE = (255, 140, 46)
FIRE_YELLOW = (255, 196, 107)
GOLD = (230, 190, 120)


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
        message = "炉火会记得今晚的每一句话。"
    if not takeaways:
        takeaways = ["慢一点，也没关系。"]
    theme = sections.get("今晚主题", "")
    return {
        "theme": theme,
        "message": _shorten(message, 40),
        "takeaways": [_shorten(t, 20) for t in takeaways[:3]],
    }


def _draw_background(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(NIGHT_TOP[i] + (NIGHT_BOTTOM[i] - NIGHT_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)
    # 星星（固定随机种子，每张卡片星空一致）
    rng = random.Random(2026)
    for _ in range(90):
        x, y = rng.randint(0, W), rng.randint(0, int(H * 0.55))
        r = rng.choice([1, 1, 2, 2, 3])
        alpha = rng.randint(70, 200)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(232, 226, 208, alpha))
    # 底部炉火光晕（单独一层高斯模糊）
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([W // 2 - 330, H - 560, W // 2 + 330, H - 40], fill=(255, 140, 46, 90))
    gdraw.ellipse([W // 2 - 170, H - 430, W // 2 + 170, H - 160], fill=(255, 190, 100, 110))
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
    draw.rounded_rectangle([44, 44, W - 44, H - 44], radius=28, outline=(230, 190, 120, 90), width=3)
    # 邮票角标
    draw.rounded_rectangle([W - 250, 78, W - 88, 240], radius=10, outline=GOLD, width=3)
    draw.text((W - 169, 120), "围", font=_font(64), fill=GOLD, anchor="ma")
    draw.text((W - 169, 190), "炉", font=_font(64), fill=GOLD, anchor="ma")

    x = 110
    # 标题
    draw.text((x, 110), "围 炉 夜 话", font=_font(76), fill=PAPER)
    sub = f"{theme_label} · {today.month}月{today.day}日"
    draw.text((x, 224), sub, font=_font(36), fill=PAPER_DIM)
    draw.line([(x, 306), (W - 300, 306)], fill=(230, 190, 120, 120), width=2)

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
    draw.text((W // 2, H - 58), "—— 炉火不熄，随时回来 ——", font=_font(26),
              fill=(150, 140, 124), anchor="ma")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
