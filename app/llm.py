"""OpenAI 兼容 LLM 客户端：按供应商顺序主备切换，支持流式透传。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import ProviderConfig

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)


def _effective_max_tokens(provider: ProviderConfig, max_tokens: int) -> int:
    """DeepSeek V4 的推理内容也占用 token 预算，给它留出足够空间。"""
    if provider.model.startswith("deepseek-v4"):
        return max(max_tokens, 8000)
    return max_tokens


class LLMError(Exception):
    pass


def _chat_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


async def _stream_from_provider(
    client: httpx.AsyncClient,
    provider: ProviderConfig,
    messages: list[dict[str, str]],
    temperature: float = 0.85,
    max_tokens: int = 1200,
) -> AsyncIterator[str]:
    """从单个供应商流式生成，yield 文本增量。首个 token 前的失败抛 LLMError。"""
    payload = {
        "model": provider.model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": _effective_max_tokens(provider, max_tokens),
    }
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    try:
        async with client.stream(
            "POST",
            _chat_url(provider.base_url),
            json=payload,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise LLMError(f"{provider.ident} HTTP {resp.status_code}: {body}")
            produced = False
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    if produced is False:  # 首个增量：剥离网关注入的零宽字符
                        text = text.lstrip("\u200b\ufeff")
                    if text:
                        produced = True
                        yield text
            if not produced:
                raise LLMError(f"{provider.ident} returned empty stream")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise LLMError(f"{provider.ident} transport error: {exc}") from exc


async def stream_generate(
    providers: list[ProviderConfig],
    messages: list[dict[str, str]],
    temperature: float = 0.85,
    max_tokens: int = 1200,
) -> AsyncIterator[str]:
    """依次尝试供应商；一旦某家产出首个增量即锁定该家直到流结束。"""
    if not providers:
        raise LLMError("no providers configured")
    errors: list[str] = []
    for provider in providers:
        try:
            async with httpx.AsyncClient() as client:
                got_any = False
                async for delta in _stream_from_provider(
                    client, provider, messages, temperature, max_tokens
                ):
                    got_any = True
                    yield delta
                if got_any:
                    return
        except LLMError as exc:
            errors.append(str(exc))
            continue
    raise LLMError("; ".join(errors) or "all providers failed")


async def generate(
    providers: list[ProviderConfig],
    messages: list[dict[str, str]],
    temperature: float = 0.85,
    max_tokens: int = 1200,
) -> str:
    chunks: list[str] = []
    async for delta in stream_generate(providers, messages, temperature, max_tokens):
        chunks.append(delta)
    return "".join(chunks)


def build_llm_messages(system_prompt: str, user_content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def estimate_usage(prompt_text: str, completion_text: str) -> dict[str, int]:
    # 中文粗略估算：1 汉字≈1 token 量级，够网关展示用
    return {
        "prompt_tokens": max(1, len(prompt_text) // 2),
        "completion_tokens": max(1, len(completion_text) // 2),
        "total_tokens": max(2, (len(prompt_text) + len(completion_text)) // 2),
    }


def extract_json(text: str) -> dict[str, Any] | None:
    """尽力从模型输出里抠出 JSON 对象（沿用心晴谷的容错思路）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
