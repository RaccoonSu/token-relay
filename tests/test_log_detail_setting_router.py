from httpx import ASGITransport, AsyncClient

from app.config import LOG_DETAIL_ENABLED_KEY
from app.database import get_db
from app.models import AppSetting
from app.services import log_setting_service
from main import app


async def _override_db(db):
    async def dependency():
        yield db
    app.dependency_overrides[get_db] = dependency


async def test_get_default_setting(db):
    await _override_db(db)
    log_setting_service.reset_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/log-detail-setting")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    app.dependency_overrides.clear()


async def test_put_setting_persists_and_refreshes(db):
    await _override_db(db)
    log_setting_service.reset_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/log-detail-setting",
            json={"enabled": True},
        )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}

    # DB 已持久化
    setting = await db.get(AppSetting, LOG_DETAIL_ENABLED_KEY)
    assert setting.value == "true"
    # 进程缓存已刷新
    assert await log_setting_service.get_log_detail_enabled() is True
    app.dependency_overrides.clear()
