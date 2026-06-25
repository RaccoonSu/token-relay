import pytest

from app.config import LOG_DETAIL_ENABLED_KEY
from app.models import AppSetting
from app.services import log_setting_service


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个测试前后重置进程缓存，避免相互污染。"""
    log_setting_service.reset_cache()
    yield
    log_setting_service.reset_cache()


async def test_default_is_false(db):
    # 未设置时缺省为 False（对齐「默认关」）
    assert await log_setting_service.get_log_detail_enabled(db) is False


async def test_set_true_persists_and_caches(db):
    await log_setting_service.set_log_detail_enabled(db, True)

    # 缓存命中：不传 db 也能读到（命中进程缓存，不回退真实 DB）
    assert await log_setting_service.get_log_detail_enabled() is True

    # DB 持久化为字符串 "true"
    setting = await db.get(AppSetting, LOG_DETAIL_ENABLED_KEY)
    assert setting is not None
    assert setting.value == "true"


async def test_set_false_back(db):
    await log_setting_service.set_log_detail_enabled(db, True)
    await log_setting_service.set_log_detail_enabled(db, False)
    assert await log_setting_service.get_log_detail_enabled(db) is False


async def test_lazy_load_reads_db_value(db):
    # 绕过 set，直接写 DB，验证 get 的懒加载从 DB 读取
    db.add(AppSetting(key=LOG_DETAIL_ENABLED_KEY, value="true"))
    await db.commit()
    log_setting_service.reset_cache()
    assert await log_setting_service.get_log_detail_enabled(db) is True


async def test_load_warms_cache_from_db(db):
    db.add(AppSetting(key=LOG_DETAIL_ENABLED_KEY, value="true"))
    await db.commit()
    log_setting_service.reset_cache()
    await log_setting_service.load_log_detail_enabled(db)
    # load 后缓存命中，不传 db 即可读到
    assert await log_setting_service.get_log_detail_enabled() is True
