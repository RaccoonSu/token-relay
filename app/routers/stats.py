from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _parse_range(start_date: date | None, end_date: date | None) -> tuple[datetime, datetime]:
    """解析日期为 (start, end_exclusive) datetime（UTC）。
    start_date/end_date 为包含（用户视角）；end 取次日 0 点作为排他上界。
    默认：最近 7 天（含今天）。"""
    today = datetime.now(timezone.utc).date()
    end_day = end_date if end_date else today
    start_day = start_date if start_date else today - timedelta(days=6)

    start_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    # end 取次日 0 点作为排他上界，使 end_date 当天整日纳入（end_date 为包含）
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt


@router.get("/summary")
async def summary(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(start_date, end_date)
    return await stats_service.get_summary(db, start, end, provider_id, model_id)


@router.get("/usage")
async def usage(
    group_by: str = Query(...),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if group_by not in ("provider", "model"):
        raise HTTPException(status_code=400, detail="group_by must be 'provider' or 'model'")
    start, end = _parse_range(start_date, end_date)
    items = await stats_service.get_usage_by_dimension(
        db, start, end, group_by, provider_id, model_id
    )
    return {"group_by": group_by, "items": items}


@router.get("/trend")
async def trend(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(start_date, end_date)
    return {"days": await stats_service.get_trend(db, start, end, provider_id, model_id)}
