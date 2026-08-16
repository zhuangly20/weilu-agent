"""生成文件的临时存取：内存存储 + 公开下载端点。

清小搭收到 attachments 后会立即转存到自己的 OSS，因此这里只需
短时可用（15分钟 TTL、最多200个），进程重启丢失也无影响。
"""
from __future__ import annotations

import secrets
import time

_STORE: dict[str, tuple[bytes, str, float]] = {}  # token -> (data, mime, ts)
TTL_SECONDS = 15 * 60
MAX_ENTRIES = 200


def put(data: bytes, mime: str = "image/png", ext: str = "png") -> str:
    now = time.time()
    if len(_STORE) >= MAX_ENTRIES:
        for tok in [t for t, (_, _, ts) in _STORE.items() if now - ts > TTL_SECONDS][: max(0, len(_STORE) - MAX_ENTRIES + 1)]:
            _STORE.pop(tok, None)
        if len(_STORE) >= MAX_ENTRIES:
            _STORE.pop(next(iter(_STORE)))
    token = f"{secrets.token_urlsafe(16)}.{ext}"
    _STORE[token] = (data, mime, now)
    return token


def get(token: str) -> tuple[bytes, str] | None:
    entry = _STORE.get(token)
    if not entry:
        return None
    data, mime, ts = entry
    if time.time() - ts > TTL_SECONDS:
        _STORE.pop(token, None)
        return None
    return data, mime
