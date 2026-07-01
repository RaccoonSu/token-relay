import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import DATABASE_URL

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

# NullPool: SQLite 本地文件，开连接便宜；用完即弃，杜绝长耗时上游调用期间
# 连接被占住导致的 QueuePool 耗尽。
engine = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_request_log_token_columns(conn)


async def _ensure_request_log_token_columns(conn):
    """幂等迁移：给已存在的 request_logs 表补 token 列（create_all 不会加列）。"""
    result = await conn.execute(text("PRAGMA table_info(request_logs)"))
    existing_cols = {row[1] for row in result}
    for col in ("input_tokens", "cache_hit_tokens", "output_tokens", "total_tokens"):
        if col not in existing_cols:
            await conn.execute(text(f"ALTER TABLE request_logs ADD COLUMN {col} INTEGER"))


async def get_db():
    async with async_session() as session:
        yield session
