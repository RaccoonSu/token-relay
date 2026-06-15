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
