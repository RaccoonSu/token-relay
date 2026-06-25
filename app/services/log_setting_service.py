"""日志详情存储开关：进程内缓存 + AppSetting 持久化。

代理热路径每次请求都要知道是否存储详情，因此用进程级缓存避免每个请求多一次 DB
查询；开关写操作极低频，写时同步刷新缓存即可。默认关闭（对齐产品偏好）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LOG_DETAIL_ENABLED_KEY
from app.database import async_session
from app.models import AppSetting

_cache_enabled: bool | None = None  # None = 尚未加载


async def _read_from_db(db: AsyncSession) -> bool:
    """从 AppSetting 读开关，缺失视为 False。"""
    setting = await db.get(AppSetting, LOG_DETAIL_ENABLED_KEY)
    if setting and setting.value:
        return setting.value.strip().lower() == "true"
    return False


async def get_log_detail_enabled(db: AsyncSession | None = None) -> bool:
    """读开关。缓存命中时 O(1)；未加载时懒加载一次。"""
    global _cache_enabled
    if _cache_enabled is None:
        if db is None:
            async with async_session() as s:
                _cache_enabled = await _read_from_db(s)
        else:
            _cache_enabled = await _read_from_db(db)
    return _cache_enabled


async def set_log_detail_enabled(db: AsyncSession, enabled: bool) -> None:
    """写 AppSetting 并同步刷新进程缓存。"""
    global _cache_enabled
    value = "true" if enabled else "false"
    setting = await db.get(AppSetting, LOG_DETAIL_ENABLED_KEY)
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=LOG_DETAIL_ENABLED_KEY, value=value))
    await db.commit()
    _cache_enabled = enabled


async def load_log_detail_enabled(db: AsyncSession) -> None:
    """lifespan 启动时预热缓存。"""
    global _cache_enabled
    _cache_enabled = await _read_from_db(db)


def reset_cache() -> None:
    """重置缓存为未加载状态（主要供测试使用）。"""
    global _cache_enabled
    _cache_enabled = None
