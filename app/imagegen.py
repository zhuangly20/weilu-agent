"""文生图（圆桌画会）：把大家的文字笔触合成为一幅画。

供应商：OpenAI 兼容 /images/generations 端点（当前 xcode.best 的 gpt-image-2）。
返回压缩后的 JPEG 字节；任何失败返回 None（上层降级为纯文字画会，不打断流程）。
"""
from __future__ import annotations

import base64
import io
import os

import httpx

from .config import _load_dotenv

_load_dotenv()


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


async def generate_painting(prompt: str, timeout: float = 150.0) -> bytes | None:
    base, key, model = _image_cfg()
    if not base or not key:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base}/images/generations",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "prompt": prompt[:1200], "n": 1, "size": "1024x1024"},
                timeout=timeout,
            )
        if resp.status_code != 200:
            return None
        data = resp.json()["data"][0]
        if data.get("b64_json"):
            raw = base64.b64decode(data["b64_json"])
        elif data.get("url"):
            async with httpx.AsyncClient() as client:
                img_resp = await client.get(data["url"], timeout=60)
            if img_resp.status_code != 200:
                return None
            raw = img_resp.content
        else:
            return None
        return _compress(raw)
    except Exception:
        return None
