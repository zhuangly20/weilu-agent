"""清心圆桌 · 清小搭标准协议接入服务（OpenAI 兼容）。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import director, prompts, protocol
from .config import load_settings
from .llm import estimate_usage
from .webui import register_webui

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("weilu")

app = FastAPI(title="weilu-agent", version="1.0.0")

PROBE_REPLY = "你好，小晴在呢。"


def check_auth(authorization: str | None) -> None:
    settings = load_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing credential")
    if authorization[len("Bearer "):].strip() != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid credential")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


register_webui(app)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")


@app.get("/files/{token}")
async def serve_file(token: str):
    """附件临时下载（无鉴权：token 本身不可猜测，TTL 15分钟，清小搭会即时转存）。"""
    from . import files as file_store

    entry = file_store.get(token)
    if not entry:
        raise HTTPException(status_code=404, detail="file not found or expired")
    data, mime = entry
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/v1/models")
async def models(authorization: str | None = Header(None)):
    check_auth(authorization)
    return {
        "object": "list",
        "data": [{"id": "weilu-agent", "object": "model", "owned_by": "weilu"}],
    }


def _parse_body(body: dict) -> tuple[list[dict], bool, int | None]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")
    stream = body.get("stream", False)
    if isinstance(stream, str):  # 严格按 JSON 布尔解析，字符串 "false" 不能当真
        stream = stream.strip().lower() == "true"
    elif not isinstance(stream, bool):
        stream = False
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int):
        max_tokens = None
    return messages, stream, max_tokens


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(None)):
    check_auth(authorization)
    settings = load_settings()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json body")

    messages, stream, max_tokens = _parse_body(body)

    # 探测快路径：网关会发 stream:true, max_tokens:1 的最小对话，直接回 canned 文案
    if max_tokens is not None and max_tokens <= 2:
        usage = estimate_usage("probe", PROBE_REPLY)
        if stream:
            return StreamingResponse(
                iter(protocol.stream_frames(PROBE_REPLY, usage)),
                media_type="text/event-stream",
            )
        return JSONResponse(protocol.full_response(PROBE_REPLY, usage))

    plan = director.plan_turn(messages)
    prompt_text = plan.system_prompt + plan.user_content

    if not stream:
        try:
            text, issues, attachments = await director.execute_plan(plan, settings.providers)
        except Exception:
            logger.exception("nonstream generate failed, degrade to fallback text")
            text, issues, attachments = prompts.LLM_FALLBACK_TEXT, ["llm-error"], []
        usage = estimate_usage(prompt_text, text)
        if issues:
            logger.warning("validate issues=%s stage=%s", issues, plan.meta.get("stage"))
        logger.info("turn stage=%s theme=%s crisis=%s len=%d attach=%d",
                    plan.meta.get("stage"), plan.meta.get("theme"),
                    plan.meta.get("crisis"), len(text), len(attachments))
        return JSONResponse(protocol.full_response(text, usage, attachments=attachments))

    async def sse():
        final_text = ""
        attachments: list = []
        yield protocol.sse_frame({"role": "assistant"})  # 首帧：role 帧
        async for item in director.stream_plan(plan, settings.providers):
            kind, payload = item[0], item[1]
            if kind == "delta":
                if payload:
                    yield protocol.sse_frame({"content": payload})
            elif kind == "attachments":
                attachments = payload
            else:  # final（可带第三段附件）
                final_text = payload
                if len(item) > 2 and item[2]:
                    attachments = item[2]
        usage = estimate_usage(prompt_text, final_text)
        extra = {"x_soda": {"attachments": attachments}} if attachments else None
        yield protocol.sse_frame({}, finish="stop", usage=usage, extra=extra)
        yield protocol.sse_done()
        logger.info("stream turn stage=%s theme=%s crisis=%s len=%d attach=%d",
                    plan.meta.get("stage"), plan.meta.get("theme"),
                    plan.meta.get("crisis"), len(final_text), len(attachments))

    return StreamingResponse(sse(), media_type="text/event-stream")
