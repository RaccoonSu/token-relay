import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.provider_service import (
    list_providers, get_provider, create_provider, update_provider, delete_provider,
    list_mappings, create_mapping, update_mapping, delete_mapping,
)

router = APIRouter(prefix="/api")


# --- Pydantic models ---

class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class MappingCreate(BaseModel):
    model_id: str
    provider_id: int
    is_active: bool = True


class MappingUpdate(BaseModel):
    model_id: str | None = None
    provider_id: int | None = None
    is_active: bool | None = None


# --- Provider endpoints ---

@router.get("/providers")
async def get_providers(db: AsyncSession = Depends(get_db)):
    providers = await list_providers(db)
    return [
        {
            "id": p.id,
            "name": p.name,
            "base_url": p.base_url,
            "api_key": p.api_key,
            "is_active": p.is_active,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in providers
    ]


@router.post("/providers")
async def post_provider(data: ProviderCreate, db: AsyncSession = Depends(get_db)):
    provider = await create_provider(db, data.name, data.base_url, data.api_key, data.is_active)
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
        "is_active": provider.is_active,
    }


@router.put("/providers/{provider_id}")
async def put_provider(provider_id: int, data: ProviderUpdate, db: AsyncSession = Depends(get_db)):
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    provider = await update_provider(db, provider_id, **updates)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
        "is_active": provider.is_active,
    }


@router.delete("/providers/{provider_id}")
async def remove_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await delete_provider(db, provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"ok": True}


# --- Model mapping endpoints ---

@router.get("/model-mappings")
async def get_mappings(db: AsyncSession = Depends(get_db)):
    mappings = await list_mappings(db)
    return [
        {
            "id": m.id,
            "model_id": m.model_id,
            "provider_id": m.provider_id,
            "provider_name": m.provider.name if m.provider else None,
            "is_active": m.is_active,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in mappings
    ]


@router.post("/model-mappings")
async def post_mapping(data: MappingCreate, db: AsyncSession = Depends(get_db)):
    provider = await get_provider(db, data.provider_id)
    if not provider:
        raise HTTPException(status_code=400, detail="Provider not found")
    mapping = await create_mapping(db, data.model_id, data.provider_id, data.is_active)
    return {
        "id": mapping.id,
        "model_id": mapping.model_id,
        "provider_id": mapping.provider_id,
        "is_active": mapping.is_active,
    }


@router.put("/model-mappings/{mapping_id}")
async def put_mapping(mapping_id: int, data: MappingUpdate, db: AsyncSession = Depends(get_db)):
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    mapping = await update_mapping(db, mapping_id, **updates)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {
        "id": mapping.id,
        "model_id": mapping.model_id,
        "provider_id": mapping.provider_id,
        "is_active": mapping.is_active,
    }


@router.delete("/model-mappings/{mapping_id}")
async def remove_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await delete_mapping(db, mapping_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


# --- Known provider model list endpoints ---

# When {base_url}/v1/models returns 404, auto-detect known providers
# and try their dedicated model list endpoints.
# Providers that don't support model listing at all
_UNSUPPORTED_MODELS_PATTERNS = [
    "token-plan.",  # Bailian Token Plan — separate API key, no /v1/models
]

# When {base_url}/v1/models returns 404, auto-detect known providers
# and try their dedicated model list endpoints.
_KNOWN_PROVIDER_FALLBACKS = [
    # Alibaba Bailian / DashScope (standard, not Token Plan)
    ("dashscope.aliyuncs.com", "https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
    ("maas.aliyuncs.com", "https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
    # DeepSeek
    ("api.deepseek.com", "https://api.deepseek.com/v1/models"),
]


def _is_models_unsupported(base_url: str) -> bool:
    """Check if this provider is known to not support model listing."""
    return any(p in base_url for p in _UNSUPPORTED_MODELS_PATTERNS)


def _resolve_models_url(base_url: str) -> str | None:
    """Return a fallback models endpoint if base_url matches a known provider."""
    for domain, fallback_url in _KNOWN_PROVIDER_FALLBACKS:
        if domain in base_url:
            return fallback_url
    return None


# --- Provider models endpoint ---

@router.get("/providers/{provider_id}/models")
async def get_provider_models(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Fetch available models from the upstream provider's models endpoint."""
    provider = await get_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Bail out early for providers known to not support model listing
    if _is_models_unsupported(provider.base_url):
        raise HTTPException(
            status_code=404,
            detail="该供应商（百炼套餐版）不支持模型列表接口，请手动填写 Model ID",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: try {base_url}/v1/models (works for Zhipu etc.)
        target_url = f"{provider.base_url.rstrip('/')}/v1/models"
        try:
            response = await client.get(
                target_url,
                headers={"x-api-key": provider.api_key},
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="上游供应商请求超时")
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="无法连接上游供应商")

        # Step 2: if 404, try known provider fallback
        if response.status_code == 404:
            fallback_url = _resolve_models_url(provider.base_url)
            if fallback_url:
                try:
                    response = await client.get(
                        fallback_url,
                        headers={"Authorization": f"Bearer {provider.api_key}"},
                    )
                except httpx.TimeoutException:
                    raise HTTPException(status_code=504, detail="上游供应商请求超时")
                except httpx.ConnectError:
                    raise HTTPException(status_code=502, detail="无法连接上游供应商")

        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="该供应商不支持模型列表接口",
            )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"上游返回 {response.status_code}: {response.text[:500]}",
            )

    try:
        body = response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="上游返回了无效的 JSON")

    # Anthropic /v1/models: {"data": [{"id": "...", ...}]}
    # OpenAI compatible:   {"data": [{"id": "...", ...}]}
    # Some providers:      plain list [{"id": "..."}]
    if isinstance(body, list):
        models = body
    elif isinstance(body, dict):
        models = body.get("data", [])
    else:
        models = []

    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "models": [
            {
                "id": m.get("id", ""),
                "display_name": m.get("display_name", m.get("name", m.get("id", ""))),
            }
            for m in models if isinstance(m, dict) and m.get("id")
        ],
    }
