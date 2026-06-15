from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import RequestLog
from app.utils import extract_usage

router = APIRouter(prefix="/api")


@router.get("/logs")
async def get_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    # Total count
    count_result = await db.execute(select(func.count(RequestLog.id)))
    total = count_result.scalar()

    # Paginated list
    offset = (page - 1) * size
    result = await db.execute(
        select(RequestLog)
        .options(selectinload(RequestLog.provider))
        .order_by(RequestLog.id.desc())
        .offset(offset)
        .limit(size)
    )
    logs = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": log.id,
                "request_id": log.request_id,
                "model_id": log.model_id,
                "provider_id": log.provider_id,
                "provider_name": log.provider.name if log.provider else None,
                "status_code": log.status_code,
                "is_stream": log.is_stream,
                "duration_ms": log.duration_ms,
                "error_message": log.error_message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "client_ip": log.client_ip,
                "usage": extract_usage(log.response_body),
            }
            for log in logs
        ],
    }


@router.get("/logs/{log_id}")
async def get_log_detail(log_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RequestLog)
        .options(selectinload(RequestLog.provider))
        .where(RequestLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if not log:
        return {"error": "Log not found"}

    return {
        "id": log.id,
        "request_id": log.request_id,
        "model_id": log.model_id,
        "provider_id": log.provider_id,
        "provider_name": log.provider.name if log.provider else None,
        "request_body": log.request_body,
        "response_body": log.response_body,
        "status_code": log.status_code,
        "is_stream": log.is_stream,
        "duration_ms": log.duration_ms,
        "error_message": log.error_message,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "client_ip": log.client_ip,
    }


@router.delete("/logs")
async def clear_logs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(delete(RequestLog))
    await db.commit()
    return {"ok": True, "deleted": result.rowcount}
