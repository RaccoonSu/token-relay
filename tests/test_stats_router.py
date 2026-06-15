from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.models import Provider, RequestLog
from main import app


async def _override_db(db):
    """把 app 的 get_db 依赖替换为测试会话。"""
    async def dependency():
        yield db
    app.dependency_overrides[get_db] = dependency


async def test_summary_endpoint(db):
    # 种子数据
    p = Provider(name="阿里云百炼", base_url="http://a", api_key="k")
    db.add(p)
    await db.flush()
    db.add(RequestLog(
        request_id="r1", model_id="qwen3.7-max", provider_id=p.id,
        request_body={}, response_body={}, status_code=200,
        input_tokens=100, cache_hit_tokens=50, output_tokens=30, total_tokens=180,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    ))
    await db.commit()

    await _override_db(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats/summary", params={
            "start_date": "2026-06-10", "end_date": "2026-06-11",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_input_tokens"] == 100
    assert data["total_tokens"] == 180
    assert data["total_requests"] == 1
    app.dependency_overrides.clear()


async def test_usage_endpoint_group_by_model(db):
    p = Provider(name="DeepSeek", base_url="http://b", api_key="k")
    db.add(p)
    await db.flush()
    for i in range(3):
        db.add(RequestLog(
            request_id=f"r{i}", model_id="deepseek-chat", provider_id=p.id,
            request_body={}, response_body={}, status_code=200,
            input_tokens=10, cache_hit_tokens=0, output_tokens=5, total_tokens=15,
            created_at=datetime(2026, 6, 10, 12, i, tzinfo=timezone.utc),
        ))
    await db.commit()

    await _override_db(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats/usage", params={
            "group_by": "model",
            "start_date": "2026-06-10", "end_date": "2026-06-11",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_by"] == "model"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["name"] == "deepseek-chat"
    assert item["request_count"] == 3
    assert item["total_tokens"] == 45
    app.dependency_overrides.clear()


async def test_trend_endpoint_fills_missing_days(db):
    db.add(RequestLog(
        request_id="r1", model_id="qwen3.7-max", provider_id=None,
        request_body={}, response_body={}, status_code=200,
        input_tokens=10, cache_hit_tokens=0, output_tokens=5, total_tokens=15,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    ))
    await db.commit()

    await _override_db(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats/trend", params={
            "start_date": "2026-06-10", "end_date": "2026-06-13",
        })
    assert resp.status_code == 200
    days = resp.json()["days"]
    assert len(days) == 4  # 6/10, 6/11, 6/12, 6/13 (end_date inclusive)
    assert days[0]["date"] == "2026-06-10"
    assert days[0]["total_tokens"] == 15
    assert days[1]["total_tokens"] == 0  # 6/11 补 0
    assert days[3]["date"] == "2026-06-13"  # end_date 当天纳入
    assert days[3]["total_tokens"] == 0  # 6/13 无数据，补 0
    app.dependency_overrides.clear()


async def test_usage_invalid_group_by_returns_400(db):
    await _override_db(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats/usage", params={
            "group_by": "invalid", "start_date": "2026-06-10", "end_date": "2026-06-11",
        })
    assert resp.status_code == 400
    app.dependency_overrides.clear()


async def test_malformed_date_returns_422(db):
    await _override_db(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats/summary", params={
            "start_date": "not-a-date", "end_date": "2026-06-11",
        })
    assert resp.status_code == 422
    app.dependency_overrides.clear()
