from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, null

from app.models import RequestLog
from app.services import log_cleanup_service


def _make_log(request_id, hours_ago, **kwargs):
    base = dict(
        request_id=request_id,
        model_id="m",
        provider_id=None,
        request_body={"a": 1},
        response_body={"b": 2},
        status_code=200,
        error_message=f"err-{request_id}",
        is_stream=False,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )
    base.update(kwargs)
    return RequestLog(**base)


async def test_cleanup_clears_old_details_only(db):
    old = _make_log("old", hours_ago=25)
    fresh = _make_log("fresh", hours_ago=1)
    db.add_all([old, fresh])
    await db.commit()

    n = await log_cleanup_service.cleanup_old_log_details(db)

    assert n == 1
    await db.refresh(old)
    await db.refresh(fresh)
    # 老记录：详情字段被清空
    assert old.request_body is None
    assert old.response_body is None
    # 老记录：其他字段原样保留
    assert old.status_code == 200
    assert old.error_message == "err-old"
    assert old.input_tokens == 10
    assert old.total_tokens == 15
    # 新记录：完全不受影响
    assert fresh.request_body == {"a": 1}
    assert fresh.response_body == {"b": 2}
    assert fresh.input_tokens == 10


async def test_cleanup_boundary_exactly_24h_kept(db):
    # 刚好 23h，应保留（边界 < 24h 不清）
    young = _make_log("young", hours_ago=23)
    db.add(young)
    await db.commit()
    n = await log_cleanup_service.cleanup_old_log_details(db)
    assert n == 0
    await db.refresh(young)
    assert young.request_body == {"a": 1}


async def test_cleanup_skips_already_null_rows(db):
    # 已是真 SQL NULL 的行不应被重复更新（用 null() 插入真 NULL，
    # 因为 ORM 直接传 None 会被 JSON 类型序列化为 'null' 字符串）
    await db.execute(insert(RequestLog).values(
        request_id="already", model_id="m", provider_id=None,
        request_body=null(), response_body=null(),
        status_code=200, error_message="err-already", is_stream=False,
        created_at=datetime.now(timezone.utc) - timedelta(hours=30),
    ))
    await db.commit()
    n = await log_cleanup_service.cleanup_old_log_details(db)
    assert n == 0
