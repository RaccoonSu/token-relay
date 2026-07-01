"""日志详情定期清理。

后台循环在应用启动时开始，先立即执行一次，之后每 LOG_DETAIL_CLEANUP_INTERVAL
秒执行一次。每轮只把 created_at 早于阈值的记录的 request_body/response_body 置
NULL，其余字段（含 token 用量列、error_message）保持不变。循环内异常被吞掉并记
录，保证循环永不退出。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import null, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LOG_DETAIL_CLEANUP_INTERVAL, LOG_DETAIL_RETENTION_HOURS
from app.database import async_session
from app.middleware import get_active_request_count
from app.models import RequestLog

# 复用 uvicorn 的 logger，使清理日志能在控制台看到（uvicorn 已配置 handler）
logger = logging.getLogger("uvicorn.error")


async def cleanup_old_log_details(db: AsyncSession) -> int:
    """清空超过保留时长的日志详情字段，返回受影响行数。

    用 sqlalchemy.null() 写真正的 SQL NULL（而非 JSON 'null' 字符串），
    这样后续轮次中 WHERE 的 IS NOT NULL 谓词能跳过已清理的行，避免重复 UPDATE。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOG_DETAIL_RETENTION_HOURS)
    result = await db.execute(
        update(RequestLog)
        .where(
            RequestLog.created_at < cutoff,
            or_(
                RequestLog.request_body.isnot(None),
                RequestLog.response_body.isnot(None),
            ),
        )
        .values(request_body=null(), response_body=null())
    )
    await db.commit()
    return result.rowcount


async def _run_cleanup_tick() -> None:
    """单次清理决策：有在途请求则跳过，否则执行清理并记录。

    有访问时跳过（debug 记录），避免与代理请求抢 SQLite 写锁，也避免在访问
    高峰冒出 "cleared 0 rows" 这类噪音日志。
    """
    active = get_active_request_count()
    if active > 0:
        logger.debug("log detail cleanup: skipped (%s active requests)", active)
        return
    async with async_session() as db:
        n = await cleanup_old_log_details(db)
        logger.info("log detail cleanup: cleared %s rows", n)


async def run_log_cleanup_loop() -> None:
    """后台清理循环：先执行一次，再 sleep，异常不退出。"""
    while True:
        try:
            await _run_cleanup_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("log detail cleanup failed: %s", e)
        await asyncio.sleep(LOG_DETAIL_CLEANUP_INTERVAL)
