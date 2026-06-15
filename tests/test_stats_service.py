from datetime import datetime, timezone, timedelta

from app.models import Provider, RequestLog
from app.services import stats_service


def _log(model_id, provider_id, inp, cache, out, created_at):
    """构造一条 RequestLog（total 自动求和）。"""
    return RequestLog(
        request_id=f"r-{model_id}-{created_at.isoformat()}",
        model_id=model_id,
        provider_id=provider_id,
        request_body={},
        response_body={},
        status_code=200,
        is_stream=False,
        input_tokens=inp,
        cache_hit_tokens=cache,
        output_tokens=out,
        total_tokens=inp + cache + out,
        created_at=created_at,
    )


async def _seed(db):
    p1 = Provider(name="阿里云百炼", base_url="http://a", api_key="k1")
    p2 = Provider(name="DeepSeek", base_url="http://b", api_key="k2")
    db.add_all([p1, p2])
    await db.flush()
    base = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    logs = [
        # 6/10: qwen 100+50+30, deepseek 40+0+10
        _log("qwen3.7-max", p1.id, 100, 50, 30, base),
        _log("deepseek-chat", p2.id, 40, 0, 10, base),
        # 6/11: qwen 200+10+20
        _log("qwen3.7-max", p1.id, 200, 10, 20, base + timedelta(days=1)),
    ]
    db.add_all(logs)
    await db.commit()


async def test_get_summary(db):
    await _seed(db)
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 12, tzinfo=timezone.utc)
    result = await stats_service.get_summary(db, start, end)
    assert result["total_input_tokens"] == 340   # 100+40+200
    assert result["total_cache_hit_tokens"] == 60  # 50+0+10
    assert result["total_output_tokens"] == 60     # 30+10+20
    assert result["total_tokens"] == 460           # 180+50+230
    assert result["total_requests"] == 3


async def test_get_summary_empty(db):
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 11, tzinfo=timezone.utc)
    result = await stats_service.get_summary(db, start, end)
    assert result == {
        "total_input_tokens": 0,
        "total_cache_hit_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "total_requests": 0,
    }


async def test_get_usage_by_provider(db):
    await _seed(db)
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 12, tzinfo=timezone.utc)
    result = await stats_service.get_usage_by_dimension(db, start, end, "provider")
    by_name = {item["name"]: item for item in result}
    assert by_name["阿里云百炼"]["total_tokens"] == 410  # 180+230
    assert by_name["阿里云百炼"]["request_count"] == 2
    assert by_name["DeepSeek"]["total_tokens"] == 50
    assert by_name["DeepSeek"]["request_count"] == 1


async def test_get_usage_by_model(db):
    await _seed(db)
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 12, tzinfo=timezone.utc)
    result = await stats_service.get_usage_by_dimension(db, start, end, "model")
    by_name = {item["name"]: item for item in result}
    assert by_name["qwen3.7-max"]["total_tokens"] == 410
    assert by_name["qwen3.7-max"]["request_count"] == 2
    assert by_name["deepseek-chat"]["total_tokens"] == 50


async def test_get_usage_provider_filter(db):
    await _seed(db)
    from app.models import Provider
    from sqlalchemy import select
    p1 = (await db.execute(select(Provider).where(Provider.name == "阿里云百炼"))).scalar_one()
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 12, tzinfo=timezone.utc)
    result = await stats_service.get_usage_by_dimension(
        db, start, end, "model", provider_id=p1.id
    )
    by_name = {item["name"]: item for item in result}
    assert "qwen3.7-max" in by_name
    assert "deepseek-chat" not in by_name


async def test_get_trend(db):
    await _seed(db)
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 13, tzinfo=timezone.utc)
    result = await stats_service.get_trend(db, start, end)
    by_date = {item["date"]: item for item in result}
    assert by_date["2026-06-10"]["total_tokens"] == 230  # 180+50
    assert by_date["2026-06-10"]["request_count"] == 2
    assert by_date["2026-06-11"]["total_tokens"] == 230
    assert by_date["2026-06-11"]["request_count"] == 1
    # 6/12 无数据，应补 0
    assert by_date["2026-06-12"]["total_tokens"] == 0
    assert by_date["2026-06-12"]["request_count"] == 0


async def test_get_trend_skips_null_token_rows(db):
    """旧日志 token 列为 NULL，不应影响求和（coalesce -> 0），但计入 request_count。"""
    db.add(RequestLog(
        request_id="old-1", model_id="qwen3.7-max", provider_id=None,
        request_body={}, response_body={}, status_code=200,
        created_at=datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc),
        # token 列均为 None
    ))
    await db.commit()
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 11, tzinfo=timezone.utc)
    result = await stats_service.get_trend(db, start, end)
    by_date = {item["date"]: item for item in result}
    # 旧日志 input/cache/output 都 None -> coalesce 0，total_tokens 0
    assert by_date["2026-06-10"]["total_tokens"] == 0
    assert by_date["2026-06-10"]["request_count"] == 1
