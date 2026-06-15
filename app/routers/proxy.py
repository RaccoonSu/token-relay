import json

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RELAY_API_KEY
from app.database import get_db
from app.services.proxy_service import resolve_provider, proxy_non_stream, proxy_stream

router = APIRouter(prefix="/anthropic")


def verify_api_key(request: Request):
    api_key = request.headers.get("x-api-key", "")
    if api_key != RELAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/v1/messages")
async def proxy_messages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_api_key),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model_id = body.get("model", "")
    if not model_id:
        raise HTTPException(status_code=400, detail="Missing 'model' field")

    result = await resolve_provider(db, model_id)
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
        response_body, status_code = await proxy_non_stream(db, provider, body, client_ip)
        return JSONResponse(content=response_body, status_code=status_code)
