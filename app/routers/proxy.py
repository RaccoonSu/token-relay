import json
import time

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import RELAY_API_KEY, DEFAULT_MODEL_ALIAS
from app.database import get_db, async_session
from app.models import Provider, ModelMapping
from app.services.proxy_service import resolve_provider, proxy_non_stream, proxy_stream
from app.services.provider_service import get_default_target

router = APIRouter(prefix="/anthropic")


def verify_api_key(request: Request):
    # Claude Code sends token in Authorization: Bearer <token> or x-api-key header
    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            api_key = auth[7:]
    if api_key != RELAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.api_route("", methods=["GET", "HEAD"])
async def health_check():
    """Health check endpoint for Claude Code to verify the relay is alive."""
    return JSONResponse({"status": "ok"})


@router.get("/v1/models")
async def list_models(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    """OpenAI-compatible model list endpoint for Claude Code gateway model discovery.

    Claude Code only surfaces models whose id starts with `claude` or `anthropic`,
    so we prepend `claude-` to non-conforming ids and expose the original name via
    `display_name`. The proxy strips the prefix back off when routing requests.
    """
    result = await db.execute(
        select(ModelMapping)
        .options(selectinload(ModelMapping.provider))
        .where(ModelMapping.is_active == True)
        .order_by(ModelMapping.id)
    )
    mappings = result.scalars().all()

    now_ts = int(time.time())
    models = []
    for m in mappings:
        if not m.provider or not m.provider.is_active:
            continue
        raw_id = m.model_id
        if raw_id.startswith(("claude-", "anthropic-")):
            public_id = raw_id
        else:
            public_id = f"claude-{raw_id}"
        models.append({
            "id": public_id,
            "display_name": raw_id,
            "object": "model",
            "created": now_ts,
            "owned_by": m.provider.name,
        })

    return JSONResponse({"object": "list", "data": models})


@router.post("/v1/messages")
async def proxy_messages(
    request: Request,
    _=Depends(verify_api_key),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model_id = body.get("model", "")
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing 'model' field")

    # 短会话完成 provider 解析后立即归还连接；上游调用期间不再持有 DB 会话。
    async with async_session() as db:
        # Resolve the virtual default alias to whatever real model the user picked
        # in the management UI. The alias is fixed in Claude Code's slot config;
        # switching models happens here without any Claude Code restart.
        if model_id == DEFAULT_MODEL_ALIAS:
            target = await get_default_target(db)
            if not target:
                raise HTTPException(
                    status_code=400,
                    detail=f"默认别名 '{DEFAULT_MODEL_ALIAS}' 尚未配置目标模型，请在管理页面设置",
                )
            model_id = target
            body["model"] = target

        result = await resolve_provider(db, model_id)
        if not result and model_id.startswith("claude-"):
            # Claude Code gateway discovery prefixes non-claude ids with "claude-";
            # strip it so the original mapping can be resolved.
            result = await resolve_provider(db, model_id[len("claude-"):])
            if result:
                body["model"] = model_id[len("claude-"):]

    if not result:
        raise HTTPException(
            status_code=400,
            detail=f"No active provider mapping found for model '{model_id}'"
        )

    provider, mapping = result
    client_ip = request.client.host if request.client else "unknown"
    is_stream = body.get("stream", False)

    if is_stream:
        return StreamingResponse(
            proxy_stream(provider, body, client_ip),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        response_body, status_code = await proxy_non_stream(provider, body, client_ip)
        return JSONResponse(content=response_body, status_code=status_code)
