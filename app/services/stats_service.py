from datetime import datetime, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RequestLog, Provider

# 可聚合的 token 列（NULL 视为 0）
_INP = func.coalesce(RequestLog.input_tokens, 0)
_CACHE = func.coalesce(RequestLog.cache_hit_tokens, 0)
_OUT = func.coalesce(RequestLog.output_tokens, 0)
_TOTAL = func.coalesce(RequestLog.total_tokens, 0)


def _filters(start: datetime, end: datetime, provider_id: int | None, model_id: str | None):
    """构造时间范围 + 可选维度过滤条件。end 为排他上界（即 < end）。"""
    conds = [RequestLog.created_at >= start, RequestLog.created_at < end]
    if provider_id is not None:
        conds.append(RequestLog.provider_id == provider_id)
    if model_id is not None:
        conds.append(RequestLog.model_id == model_id)
    return and_(*conds)


async def get_summary(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> dict:
    """时间范围内的总量概览。"""
    stmt = select(
        func.coalesce(func.sum(_INP), 0),
        func.coalesce(func.sum(_CACHE), 0),
        func.coalesce(func.sum(_OUT), 0),
        func.coalesce(func.sum(_TOTAL), 0),
        func.count(RequestLog.id),
    ).where(_filters(start, end, provider_id, model_id))
    row = (await db.execute(stmt)).one()
    return {
        "total_input_tokens": int(row[0]),
        "total_cache_hit_tokens": int(row[1]),
        "total_output_tokens": int(row[2]),
        "total_tokens": int(row[3]),
        "total_requests": int(row[4]),
    }


def _row_to_item(name, row) -> dict:
    return {
        "name": name,
        "input_tokens": int(row[0]),
        "cache_hit_tokens": int(row[1]),
        "output_tokens": int(row[2]),
        "total_tokens": int(row[3]),
        "request_count": int(row[4]),
    }


async def get_usage_by_dimension(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    group_by: str,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> list[dict]:
    """按 provider 或 model 聚合，返回按 total_tokens 降序的明细列表。"""
    agg = (
        func.coalesce(func.sum(_INP), 0),
        func.coalesce(func.sum(_CACHE), 0),
        func.coalesce(func.sum(_OUT), 0),
        func.coalesce(func.sum(_TOTAL), 0),
        func.count(RequestLog.id),
    )

    if group_by == "model":
        stmt = (
            select(RequestLog.model_id, *agg)
            .where(_filters(start, end, provider_id, model_id))
            .group_by(RequestLog.model_id)
            .order_by(func.coalesce(func.sum(_TOTAL), 0).desc())
        )
        return [_row_to_item(row[0], row[1:]) for row in (await db.execute(stmt)).all()]

    if group_by == "provider":
        # LEFT JOIN 取供应商名，已删的供应商显示"未知供应商"
        name = func.coalesce(Provider.name, "未知供应商")
        stmt = (
            select(name, *agg)
            .outerjoin(Provider, RequestLog.provider_id == Provider.id)
            .where(_filters(start, end, provider_id, model_id))
            .group_by(name)
            .order_by(func.coalesce(func.sum(_TOTAL), 0).desc())
        )
        return [_row_to_item(row[0], row[1:]) for row in (await db.execute(stmt)).all()]

    raise ValueError(f"invalid group_by: {group_by}")


async def get_trend(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    provider_id: int | None = None,
    model_id: str | None = None,
) -> list[dict]:
    """按天聚合趋势，补齐范围内无数据的天为全 0。"""
    day = func.date(RequestLog.created_at)
    stmt = (
        select(
            day,
            func.coalesce(func.sum(_INP), 0),
            func.coalesce(func.sum(_CACHE), 0),
            func.coalesce(func.sum(_OUT), 0),
            func.coalesce(func.sum(_TOTAL), 0),
            func.count(RequestLog.id),
        )
        .where(_filters(start, end, provider_id, model_id))
        .group_by(day)
        .order_by(day)
    )
    rows = {(str(r[0])): r for r in (await db.execute(stmt)).all()}

    # 补齐每一天
    result = []
    cur = start.date()
    last = end.date() - timedelta(days=1)  # end 为排他上界
    while cur <= last:
        key = cur.isoformat()
        if key in rows:
            r = rows[key]
            result.append({
                "date": key,
                "input_tokens": int(r[1]),
                "cache_hit_tokens": int(r[2]),
                "output_tokens": int(r[3]),
                "total_tokens": int(r[4]),
                "request_count": int(r[5]),
            })
        else:
            result.append({
                "date": key,
                "input_tokens": 0,
                "cache_hit_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
            })
        cur += timedelta(days=1)
    return result
