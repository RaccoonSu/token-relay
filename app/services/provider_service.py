from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Provider, ModelMapping


async def list_providers(db: AsyncSession) -> list[Provider]:
    result = await db.execute(select(Provider).order_by(Provider.id))
    return list(result.scalars().all())


async def get_provider(db: AsyncSession, provider_id: int) -> Provider | None:
    return await db.get(Provider, provider_id)


async def create_provider(db: AsyncSession, name: str, base_url: str, api_key: str = "", is_active: bool = True) -> Provider:
    provider = Provider(name=name, base_url=base_url, api_key=api_key, is_active=is_active)
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def update_provider(db: AsyncSession, provider_id: int, **kwargs) -> Provider | None:
    provider = await db.get(Provider, provider_id)
    if not provider:
        return None
    for key, value in kwargs.items():
        if hasattr(provider, key):
            setattr(provider, key, value)
    await db.commit()
    await db.refresh(provider)
    return provider


async def delete_provider(db: AsyncSession, provider_id: int) -> bool:
    # Delete associated mappings first
    await db.execute(delete(ModelMapping).where(ModelMapping.provider_id == provider_id))
    result = await db.execute(delete(Provider).where(Provider.id == provider_id))
    await db.commit()
    return result.rowcount > 0


async def list_mappings(db: AsyncSession) -> list[ModelMapping]:
    result = await db.execute(
        select(ModelMapping)
        .options(selectinload(ModelMapping.provider))
        .order_by(ModelMapping.id)
    )
    return list(result.scalars().all())


async def create_mapping(db: AsyncSession, model_id: str, provider_id: int, is_active: bool = True) -> ModelMapping:
    mapping = ModelMapping(model_id=model_id, provider_id=provider_id, is_active=is_active)
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


async def update_mapping(db: AsyncSession, mapping_id: int, **kwargs) -> ModelMapping | None:
    mapping = await db.get(ModelMapping, mapping_id)
    if not mapping:
        return None
    for key, value in kwargs.items():
        if hasattr(mapping, key):
            setattr(mapping, key, value)
    await db.commit()
    await db.refresh(mapping)
    return mapping


async def delete_mapping(db: AsyncSession, mapping_id: int) -> bool:
    result = await db.execute(delete(ModelMapping).where(ModelMapping.id == mapping_id))
    await db.commit()
    return result.rowcount > 0
