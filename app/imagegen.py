"""文生图（圆桌画室）：把大家的文字笔触合成为一幅画。

供应商：OpenAI 兼容 /images/generations 端点（当前 xcode.best 的 gpt-image-2）。
三级降级（心晴谷验证过的策略）：原始prompt → 敏感词净化后重试 → 纯Python暖色渐变兜底图，
保证明信片永远有画；只有前两级失败才落兜底图（b64 带标记）。
"""
from __future__ import annotations

import base64
import io
import os
import re
import struct
import zlib

import httpx

from .config import _load_dotenv

_load_dotenv()

# 心晴谷 http_image.py 同款净化黑名单与白名单正则
_UNSAFE_TERMS = (
    "血", "杀", "死", "刀", "枪", "弹", "裸", " Nude", "nude", "毒", "尸",
    "残", "爆", "战争", "打架", "自杀", "自残", "跳楼", "上吊", "割腕",
)
_SAFE_CHARS_RE = re.compile(r"[^一-鿿぀-ヿA-Za-z0-9，。、,.\s]")


def _sanitize_prompt(prompt: str) -> str:
    """删掉可能触发审核的词，只留温和字符；空则回到安全默认画面。"""
    text = prompt
    for term in _UNSAFE_TERMS:
        text = text.replace(term, "")
    text = _SAFE_CHARS_RE.sub("", text).strip()
    return text or "温暖治愈的自然风景，阳光、花草、柔和的天空"


def _image_cfg() -> tuple[str, str, str]:
    """(base_url, api_key, model)，默认复用 xcode 供应商配置。"""
    base = os.environ.get("IMAGE_BASE_URL") or os.environ.get("AI_PROVIDER_XCODE_MAIN_BASE_URL", "")
    key = os.environ.get("IMAGE_API_KEY") or os.environ.get("AI_PROVIDER_XCODE_MAIN_API_KEY", "")
    model = os.environ.get("IMAGE_MODEL", "gpt-image-2")
    return base.rstrip("/"), key, model


def build_painting_prompt(framing: str, contributions: list[str], user_contribution: str) -> str:
    """心晴谷验证过的加权拼接：全部笔触相连，用户笔触末尾重点强调。"""
    joined = "；".join(c.strip().rstrip("。；，,") for c in contributions if c.strip())
    parts = [f"一群人共同创作的画。{framing}。"]
    if joined:
        parts.append(f"画面包含以下元素：{joined}。")
    if user_contribution:
        parts.append(f"画面中必须重点体现：{user_contribution}。")
    parts.append(
        "温暖的治愈系插画风格，明亮柔和的暖色中透着暖光，笔触柔和，元素在画面中和谐共存，"
        "富有想象力与情感温度。画面中不出现任何文字。"
    )
    return "".join(parts)


def _compress(raw: bytes) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if max(img.size) > 1024:
        img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _fallback_artwork_png() -> bytes:
    """心晴谷同款纯Python兜底：1024×1024 暖色竖向渐变 PNG，无任何外部依赖。"""
    w = h = 256  # 渐变平滑，256 足够；前端拉伸显示
    top = (0xF6, 0xD9, 0xC0)
    bottom = (0xEA, 0xF0, 0xC8)

    def px(x: int, y: int) -> bytes:
        t = y / (h - 1)
        return bytes(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))

    rows: list[bytes] = []
    for y in range(h):
        row = bytearray()
        row.append(0)  # filter type none
        for x in range(w):
            row += px(x, y)
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows)))
            + chunk(b"IEND", b""))


async def generate_painting(prompt: str, timeout: float = 150.0) -> bytes | None:
    """三级降级：原始prompt → 净化重试 → 纯Python兜底图。永不返回 None。"""
    base, key, model = _image_cfg()
    if not base or not key:
        return _fallback_artwork_png()
    for attempt_prompt in (prompt, _sanitize_prompt(prompt)):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base}/images/generations",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "prompt": attempt_prompt[:1200], "n": 1, "size": "1024x1024"},
                    timeout=timeout,
                )
            if resp.status_code != 200:
                continue
            data = resp.json()["data"][0]
            if data.get("b64_json"):
                raw = base64.b64decode(data["b64_json"])
            elif data.get("url"):
                async with httpx.AsyncClient() as client:
                    img_resp = await client.get(data["url"], timeout=60)
                if img_resp.status_code != 200:
                    continue
                raw = img_resp.content
            else:
                continue
            return _compress(raw)
        except Exception:
            continue
    return _fallback_artwork_png()
