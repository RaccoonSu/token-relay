import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base


@pytest_asyncio.fixture
async def engine():
    """内存级 SQLite 引擎，每个测试函数独立。

    用 StaticPool 让所有会话共享同一个底层连接，否则 :memory: 在多连接下
    会各自拿到独立空库，无法观察 proxy_service 内部短会话写入的数据。
    """
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_maker(engine):
    """绑定到测试 engine 的 sessionmaker，供 monkeypatch 替换 proxy_service.async_session。"""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_maker):
    """已建表的异步会话。"""
    async with session_maker() as session:
        yield session
