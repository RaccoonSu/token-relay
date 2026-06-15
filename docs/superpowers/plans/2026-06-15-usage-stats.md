# 用量统计面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Token Relay 新增独立的 `/stats` 用量统计页面，按供应商/模型/时间维度聚合展示 Token 用量，含汇总卡片、趋势图、明细表格，支持时间范围与供应商/模型筛选。

**Architecture:** 给 `RequestLog` 增加 4 个独立 token 列（写入时填充，历史数据不回填），新增 `stats_service` 做 SQL `SUM()` + `GROUP BY` 聚合，新增 `stats` 路由暴露 3 个接口，新增独立的 `stats.html`（Vue 3 + Chart.js）页面挂载在 `/stats`。共享的 usage 提取逻辑提取到 `app/utils.py`。

**Tech Stack:** FastAPI + SQLAlchemy(async) + aiosqlite（后端）；Vue 3 + Chart.js 4（CDN，前端）；pytest + pytest-asyncio（新增测试，本次引入）。

**Spec:** [docs/superpowers/specs/2026-06-15-usage-stats-design.md](../specs/2026-06-15-usage-stats-design.md)

---

## File Structure

**新增：**
- `app/utils.py` — 共享的 `extract_usage()` 纯函数
- `app/services/stats_service.py` — 聚合查询（summary / usage / trend）
- `app/routers/stats.py` — `/api/stats/*` 路由（3 个 GET 接口）
- `app/static/stats.html` — 用量统计前端页面（Vue 3 + Chart.js）
- `tests/__init__.py`、`tests/conftest.py` — 测试基础设施
- `tests/test_utils.py`、`tests/test_stats_service.py`、`tests/test_stats_router.py` — 后端测试

**修改：**
- `app/models.py` — `RequestLog` 增加 4 个 token 列
- `app/database.py` — `init_db()` 加幂等迁移（ALTER TABLE ADD COLUMN）
- `app/services/proxy_service.py` — 写日志时填充 token 列（2 处：non-stream、stream）
- `app/routers/logs.py` — `_extract_usage` 改为引用 `app/utils.py`
- `main.py` — 挂载 stats router、注册 `/stats` 路由
- `app/static/index.html` — 顶部加"用量统计"导航链接
- `requirements.txt` — 加 pytest、pytest-asyncio 开发依赖

---

## Task 1: 引入测试基础设施 + 提取共享 usage 函数

把 [app/routers/logs.py:12-31](../../../app/routers/logs.py) 里的 `_extract_usage()` 提取成 `app/utils.py` 中的共享纯函数 `extract_usage()`，并引入 pytest 测试基础设施。

**Files:**
- Create: `app/utils.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_utils.py`
- Modify: `app/routers/logs.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 在 requirements.txt 加开发依赖**

修改 `requirements.txt`，在文件末尾追加：

```
# 测试依赖
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: 安装新依赖**

Run: `pip install pytest pytest-asyncio`
Expected: 安装成功，无报错

- [ ] **Step 3: 创建 tests 包**

创建空文件 `tests/__init__.py`（内容为空即可）。

- [ ] **Step 4: 创建 app/utils.py（被测函数）**

创建 `app/utils.py`：

```python
def extract_usage(response_body: dict | None) -> dict | None:
    """从 Anthropic 响应体中提取 token 用量。

    返回包含 input_tokens / cache_hit_tokens / output_tokens / total_tokens 的字典；
    若无 usage 字段则返回 None。各数值字段缺失按 0 处理。
    """
    if not response_body or not isinstance(response_body, dict):
        return None
    usage = response_body.get("usage")
    if not usage:
        return None
    input_tokens = usage.get("input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_create = usage.get("cache_creation_input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_hit = cache_read + cache_create
    # input_tokens 已不含缓存部分，真正的总输入需把缓存读+缓存写加上
    total_tokens = input_tokens + cache_read + cache_create + output_tokens
    return {
        "input_tokens": input_tokens,
        "cache_hit_tokens": cache_hit,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
```

- [ ] **Step 5: 写测试 tests/test_utils.py**

创建 `tests/test_utils.py`：

```python
from app.utils import extract_usage


def test_extract_usage_none_input():
    assert extract_usage(None) is None


def test_extract_usage_no_usage_field():
    assert extract_usage({"content": []}) is None


def test_extract_usage_full():
    body = {"usage": {
        "input_tokens": 100,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 30,
        "output_tokens": 200,
    }}
    result = extract_usage(body)
    assert result == {
        "input_tokens": 100,
        "cache_hit_tokens": 80,
        "output_tokens": 200,
        "total_tokens": 380,
    }


def test_extract_usage_missing_cache_fields():
    body = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    result = extract_usage(body)
    assert result == {
        "input_tokens": 10,
        "cache_hit_tokens": 0,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_extract_usage_zero_values():
    body = {"usage": {"input_tokens": 0, "output_tokens": 0}}
    result = extract_usage(body)
    assert result["total_tokens"] == 0
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `python -m pytest tests/test_utils.py -v`
Expected: 5 个测试全部 PASSED

- [ ] **Step 7: 重构 logs.py 改用共享函数**

修改 `app/routers/logs.py`：
- 删除第 12-31 行的 `_extract_usage` 函数定义
- 在文件顶部 import 区，把 `from app.models import RequestLog` 改为：

```python
from app.models import RequestLog
from app.utils import extract_usage
```

- 把第 72 行的 `"usage": _extract_usage(log.response_body),` 改为：

```python
"usage": extract_usage(log.response_body),
```

- [ ] **Step 8: 创建 tests/conftest.py（数据库测试 fixture）**

创建 `tests/conftest.py`，供后续 stats_service / router 测试共用：

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base


@pytest_asyncio.fixture
async def engine():
    """内存级 SQLite 引擎，每个测试函数独立。"""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    """已建表的异步会话。"""
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
```

- [ ] **Step 9: 提交**

```bash
git add app/utils.py tests/ app/routers/logs.py requirements.txt
git commit -m "refactor: 提取共享 extract_usage 函数并引入测试基础设施"
```

---

## Task 2: RequestLog 增加 token 列 + 幂等迁移

给 `RequestLog` 增加 4 个 Integer 列，并在 `init_db()` 中对已有数据库做幂等 `ALTER TABLE ADD COLUMN`（`create_all` 不会给已存在的表加列）。

**Files:**
- Modify: `app/models.py:38-54`
- Modify: `app/database.py:18-20`

- [ ] **Step 1: 给 RequestLog 加 4 个列**

修改 `app/models.py`，在 `RequestLog` 类的 `client_ip` 列之后（第 52 行后）、`provider = relationship(...)` 之前，加入 4 个列：

```python
    client_ip = Column(String(50), nullable=True)
    # 用量统计专用列，写日志时填充；历史数据为 NULL，聚合按 0 处理
    input_tokens = Column(Integer, nullable=True)
    cache_hit_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)

    provider = relationship("Provider", back_populates="logs")
```

- [ ] **Step 2: 在 init_db() 加幂等迁移**

修改 `app/database.py`，先在顶部 import 区加入：

```python
from sqlalchemy import text
```

然后把 `init_db` 函数（第 18-20 行）替换为：

```python
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
```

- [ ] **Step 3: 验证迁移对已有数据库生效**

Run（在项目根目录）：
```bash
python -c "
import asyncio
from app.database import init_db, engine
from sqlalchemy import text

async def main():
    await init_db()
    async with engine.connect() as conn:
        result = await conn.execute(text('PRAGMA table_info(request_logs)'))
        cols = [row[1] for row in result]
        print('columns:', cols)
        for c in ('input_tokens','cache_hit_tokens','output_tokens','total_tokens'):
            assert c in cols, f'{c} missing'
        print('OK: all token columns present')

asyncio.run(main())
"
```
Expected: 打印列名列表含 4 个新列，并输出 `OK: all token columns present`

- [ ] **Step 4: 再次运行确保幂等（不报重复列错误）**

重复执行上一步的命令，Expected: 同样输出 OK，无 "duplicate column" 报错

- [ ] **Step 5: 提交**

```bash
git add app/models.py app/database.py
git commit -m "feat: RequestLog 增加 token 列及幂等迁移"
```

---

## Task 3: 写日志时填充 token 列

修改 [app/services/proxy_service.py](../../../app/services/proxy_service.py)，在记录日志时用 `extract_usage()` 填充新列。共两处：`proxy_non_stream` 和 `proxy_stream`。

**Files:**
- Modify: `app/services/proxy_service.py`

- [ ] **Step 1: 顶部 import extract_usage**

修改 `app/services/proxy_service.py` 第 13 行附近，把：

```python
from app.database import async_session
from app.models import Provider, ModelMapping, RequestLog
```

改为：

```python
from app.database import async_session
from app.models import Provider, ModelMapping, RequestLog
from app.utils import extract_usage
```

- [ ] **Step 2: proxy_non_stream 填充 token 列**

在 `proxy_non_stream` 中（第 176 行 `db.add(log_entry)` 之前），插入填充逻辑。找到：

```python
    db.add(log_entry)
    await db.commit()

    return response_body, status_code
```

在其前面（`db.add(log_entry)` 之前）插入：

```python
    # 填充 token 用量列
    usage = extract_usage(response_body)
    if usage:
        log_entry.input_tokens = usage["input_tokens"]
        log_entry.cache_hit_tokens = usage["cache_hit_tokens"]
        log_entry.output_tokens = usage["output_tokens"]
        log_entry.total_tokens = usage["total_tokens"]

    db.add(log_entry)
```

即把原来的 `db.add(log_entry)` 这一行替换为上面整段。

- [ ] **Step 3: proxy_stream 填充 token 列（成功分支）**

在 `proxy_stream` 末尾（第 278 行 `async with async_session() as db:` 块内），找到：

```python
    async with async_session() as db:
        log_entry = RequestLog(
            request_id=request_id,
            model_id=request_body.get("model", ""),
            provider_id=provider.id,
            request_body=request_body,
            response_body=aggregated_response,
            status_code=status_code,
            is_stream=True,
            duration_ms=duration_ms,
            error_message=error_message,
            client_ip=client_ip,
        )
        db.add(log_entry)
        await db.commit()
```

在 `db.add(log_entry)` 之前插入：

```python
        # 填充 token 用量列
        usage = extract_usage(aggregated_response)
        if usage:
            log_entry.input_tokens = usage["input_tokens"]
            log_entry.cache_hit_tokens = usage["cache_hit_tokens"]
            log_entry.output_tokens = usage["output_tokens"]
            log_entry.total_tokens = usage["total_tokens"]

        db.add(log_entry)
```

- [ ] **Step 4: proxy_stream 填充 token 列（错误分支，status_code >= 400）**

在 `proxy_stream` 的错误分支（第 226-240 行的 `async with async_session() as db:` 块），找到：

```python
                    async with async_session() as db:
                        log_entry = RequestLog(
                            request_id=request_id,
                            model_id=request_body.get("model", ""),
                            provider_id=provider.id,
                            request_body=request_body,
                            response_body=error_body,
                            status_code=status_code,
                            is_stream=True,
                            duration_ms=duration_ms,
                            error_message=error_message,
                            client_ip=client_ip,
                        )
                        db.add(log_entry)
                        await db.commit()
```

错误响应通常无 usage，这里也调用 `extract_usage` 做一致处理（无 usage 则各列保持 NULL）。在 `db.add(log_entry)` 之前插入：

```python
                        usage = extract_usage(error_body)
                        if usage:
                            log_entry.input_tokens = usage["input_tokens"]
                            log_entry.cache_hit_tokens = usage["cache_hit_tokens"]
                            log_entry.output_tokens = usage["output_tokens"]
                            log_entry.total_tokens = usage["total_tokens"]

                        db.add(log_entry)
```

- [ ] **Step 5: 冒烟验证服务能启动**

Run: `python -c "from app.services.proxy_service import proxy_non_stream, proxy_stream; print('import OK')"`
Expected: 输出 `import OK`，无语法/导入错误

- [ ] **Step 6: 提交**

```bash
git add app/services/proxy_service.py
git commit -m "feat: 写日志时填充 token 用量列"
```

---

## Task 4: stats_service 聚合查询

新增 `app/services/stats_service.py`，提供 3 个聚合函数：`get_summary`、`get_usage_by_dimension`、`get_trend`。全部用 SQL `SUM()` + `GROUP BY`，token 列为 NULL 时用 `coalesce(..., 0)` 按 0 处理。

**Files:**
- Create: `app/services/stats_service.py`
- Create: `tests/test_stats_service.py`

- [ ] **Step 1: 写测试 tests/test_stats_service.py**

创建 `tests/test_stats_service.py`：

```python
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
    end = datetime(2026, 6, 12, tzinfo=timezone.utc)
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
```

- [ ] **Step 2: 运行测试确认全部失败（模块/函数不存在）**

Run: `python -m pytest tests/test_stats_service.py -v`
Expected: 全部 FAIL 或 ERROR（`ModuleNotFoundError: No module named 'app.services.stats_service'`）

- [ ] **Step 3: 实现 app/services/stats_service.py**

创建 `app/services/stats_service.py`：

```python
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
            .group_by(RequestLog.provider_id)
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
```

- [ ] **Step 4: 配置 pytest-asyncio 模式**

在项目根目录创建 `pytest.ini`：

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: 运行 stats_service 测试，确认全部通过**

Run: `python -m pytest tests/test_stats_service.py -v`
Expected: 7 个测试全部 PASSED

- [ ] **Step 6: 运行全部测试确认无回归**

Run: `python -m pytest tests/ -v`
Expected: test_utils.py(5) + test_stats_service.py(7) 全部 PASSED

- [ ] **Step 7: 提交**

```bash
git add app/services/stats_service.py tests/test_stats_service.py pytest.ini
git commit -m "feat: 新增 stats_service 聚合查询（summary/usage/trend）"
```

---

## Task 5: stats 路由 + 挂载

新增 `app/routers/stats.py`，提供 3 个 GET 接口；在 `main.py` 挂载 router。

**Files:**
- Create: `app/routers/stats.py`
- Create: `tests/test_stats_router.py`
- Modify: `main.py:12,27`

- [ ] **Step 1: 写测试 tests/test_stats_router.py**

创建 `tests/test_stats_router.py`：

```python
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


async def test_summary_endpoint(db, engine):
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
    assert len(days) == 3  # 6/10, 6/11, 6/12
    assert days[0]["date"] == "2026-06-10"
    assert days[0]["total_tokens"] == 15
    assert days[1]["total_tokens"] == 0  # 补 0
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
```

注意：`_override_db` 把依赖替换为 conftest 的 `db` fixture 会话，使 router 用同一个内存库。

- [ ] **Step 2: 运行测试确认失败（路由不存在）**

Run: `python -m pytest tests/test_stats_router.py -v`
Expected: 全部 FAIL（404 或 import 错误，因为 stats router 还没挂载）

- [ ] **Step 3: 实现 app/routers/stats.py**

创建 `app/routers/stats.py`：

```python
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _parse_range(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime]:
    """解析日期字符串为 (start, end_exclusive) datetime（UTC）。
    默认：最近 7 天（含今天）。"""
    today = datetime.now(timezone.utc).date()
    if end_date:
        end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        end_day = today
    if start_date:
        start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start_day = today - timedelta(days=6)

    start_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    # end 取次日 0 点作为排他上界，使 end_date 当天整日纳入
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_dt, end_dt


@router.get("/summary")
async def summary(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(start_date, end_date)
    return await stats_service.get_summary(db, start, end, provider_id, model_id)


@router.get("/usage")
async def usage(
    group_by: str = Query(...),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if group_by not in ("provider", "model"):
        raise HTTPException(status_code=400, detail="group_by must be 'provider' or 'model'")
    start, end = _parse_range(start_date, end_date)
    items = await stats_service.get_usage_by_dimension(
        db, start, end, group_by, provider_id, model_id
    )
    return {"group_by": group_by, "items": items}


@router.get("/trend")
async def trend(
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    provider_id: int | None = Query(None),
    model_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(start_date, end_date)
    return {"days": await stats_service.get_trend(db, start, end, provider_id, model_id)}
```

- [ ] **Step 4: 在 main.py 挂载 stats router**

修改 `main.py`：
- 第 12 行 `from app.routers import proxy, providers, logs` 改为：

```python
from app.routers import proxy, providers, logs, stats
```

- 在第 27 行 `app.include_router(logs.router)` 之后加一行：

```python
app.include_router(logs.router)
app.include_router(stats.router)
```

- [ ] **Step 5: 运行 stats_router 测试，确认通过**

Run: `python -m pytest tests/test_stats_router.py -v`
Expected: 4 个测试全部 PASSED

- [ ] **Step 6: 运行全部测试**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASSED（utils 5 + stats_service 7 + stats_router 4）

- [ ] **Step 7: 提交**

```bash
git add app/routers/stats.py tests/test_stats_router.py main.py
git commit -m "feat: 新增 /api/stats 路由（summary/usage/trend）"
```

---

## Task 6: 前端 stats.html 页面

新增独立的 `app/static/stats.html`（Vue 3 + Chart.js，CDN 引入），复用 index.html 的 CSS 风格。含时间范围选择器、供应商/模型筛选器、5 个汇总卡片、趋势图（Chart.js mixed chart）、按供应商/按模型两个明细表格。

**Files:**
- Create: `app/static/stats.html`
- Modify: `main.py`（注册 `/stats` 路由）

- [ ] **Step 1: 创建 app/static/stats.html**

创建 `app/static/stats.html`（完整内容）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Token Relay - 用量统计</title>
<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script src="https://unpkg.com/chart.js@4"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f7fa; color: #333; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
h1 { font-size: 24px; margin-bottom: 20px; color: #1a1a2e; display: flex; justify-content: space-between; align-items: center; }
.nav-link { font-size: 14px; color: #3b82f6; text-decoration: none; }
.card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 20px; margin-bottom: 16px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 12px; flex-wrap: wrap; }
.range-btns { display: flex; gap: 0; }
.range-btn { padding: 6px 14px; border: 1px solid #e2e8f0; background: #fff; cursor: pointer; font-size: 13px; color: #64748b; }
.range-btn:first-child { border-radius: 6px 0 0 6px; }
.range-btn:last-child { border-radius: 0 6px 6px 0; }
.range-btn + .range-btn { border-left: none; }
.range-btn.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }
.filters { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-group { display: flex; align-items: center; gap: 6px; }
.filter-group label { font-size: 13px; color: #64748b; white-space: nowrap; }
select, input[type="date"] { padding: 6px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 13px; background: #fff; min-width: 140px; }
.cards-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 16px; }
.stat-card { border-radius: 8px; padding: 16px; text-align: center; }
.stat-card .label { font-size: 12px; color: #64748b; margin-bottom: 6px; }
.stat-card .value { font-size: 22px; font-weight: 700; }
.stat-card .unit { font-size: 11px; color: #94a3b8; margin-top: 2px; }
.c-input { background: #f0f9ff; } .c-input .value { color: #1e40af; }
.c-cache { background: #fef3c7; } .c-cache .value { color: #92400e; }
.c-output { background: #f0fdf4; } .c-output .value { color: #166534; }
.c-total { background: #ede9fe; } .c-total .value { color: #5b21b6; }
.c-req { background: #fdf2f8; } .c-req .value { color: #9d174d; }
.chart-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 20px; margin-bottom: 16px; }
.chart-card h3 { font-size: 15px; margin-bottom: 12px; color: #1a1a2e; }
.chart-wrap { height: 280px; }
.tables-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.table-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 16px; }
.table-card h3 { font-size: 15px; margin-bottom: 12px; color: #1a1a2e; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 8px; text-align: right; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
th { background: #f8fafc; font-weight: 600; color: #475569; }
th:first-child, td:first-child { text-align: left; }
td.total-col { font-weight: 600; color: #5b21b6; }
.empty { text-align: center; color: #94a3b8; padding: 40px 0; font-size: 14px; }
.loading { text-align: center; color: #94a3b8; padding: 40px 0; }
.refresh-btn { padding: 6px 14px; background: #3b82f6; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
.refresh-btn:hover { background: #2563eb; }
</style>
</head>
<body>
<div id="app">
  <div class="container">
    <h1>
      <span>📊 用量统计</span>
      <a href="/" class="nav-link">← 返回管理页面</a>
    </h1>

    <!-- 工具栏：时间范围 + 筛选器 + 刷新 -->
    <div class="toolbar">
      <div class="range-btns">
        <button class="range-btn" :class="{active: rangePreset==='today'}" @click="setRange('today')">今天</button>
        <button class="range-btn" :class="{active: rangePreset==='7d'}" @click="setRange('7d')">最近7天</button>
        <button class="range-btn" :class="{active: rangePreset==='30d'}" @click="setRange('30d')">最近30天</button>
        <button class="range-btn" :class="{active: rangePreset==='custom'}" @click="rangePreset='custom'">自定义</button>
      </div>
      <div class="filters">
        <template v-if="rangePreset==='custom'">
          <div class="filter-group">
            <input type="date" v-model="customStart">
            <span>~</span>
            <input type="date" v-model="customEnd">
            <button class="refresh-btn" @click="loadAll">应用</button>
          </div>
        </template>
        <div class="filter-group">
          <label>供应商</label>
          <select v-model="filterProviderId" @change="onProviderFilterChange">
            <option :value="null">全部供应商</option>
            <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
        </div>
        <div class="filter-group">
          <label>模型</label>
          <select v-model="filterModelId" @change="loadAll">
            <option :value="null">全部模型</option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <button class="refresh-btn" @click="loadAll">刷新</button>
      </div>
    </div>

    <!-- 汇总卡片 -->
    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <div class="cards-grid">
        <div class="stat-card c-total">
          <div class="label">总用量</div>
          <div class="value">{{ fmt(summary.total_tokens) }}</div>
          <div class="unit">tokens</div>
        </div>
        <div class="stat-card c-input">
          <div class="label">总输入</div>
          <div class="value">{{ fmt(summary.total_input_tokens) }}</div>
          <div class="unit">tokens</div>
        </div>
        <div class="stat-card c-cache">
          <div class="label">缓存命中</div>
          <div class="value">{{ fmt(summary.total_cache_hit_tokens) }}</div>
          <div class="unit">tokens ({{ pct(summary.total_cache_hit_tokens, summary.total_tokens) }})</div>
        </div>
        <div class="stat-card c-output">
          <div class="label">总输出</div>
          <div class="value">{{ fmt(summary.total_output_tokens) }}</div>
          <div class="unit">tokens</div>
        </div>
        <div class="stat-card c-req">
          <div class="label">请求次数</div>
          <div class="value">{{ fmt(summary.total_requests) }}</div>
          <div class="unit">次</div>
        </div>
      </div>

      <!-- 趋势图 -->
      <div class="chart-card">
        <h3>📈 Token 用量趋势</h3>
        <div class="chart-wrap"><canvas ref="trendCanvas"></canvas></div>
      </div>

      <!-- 明细表格 -->
      <div class="tables-grid">
        <div class="table-card">
          <h3>按供应商</h3>
          <div v-if="usageByProvider.length===0" class="empty">暂无数据</div>
          <table v-else>
            <thead><tr>
              <th>名称</th><th>总量</th><th>输入</th><th>缓存</th><th>输出</th><th>次数</th>
            </tr></thead>
            <tbody>
              <tr v-for="item in usageByProvider" :key="item.name">
                <td>{{ item.name }}</td>
                <td class="total-col">{{ fmt(item.total_tokens) }}</td>
                <td>{{ fmt(item.input_tokens) }}</td>
                <td>{{ fmt(item.cache_hit_tokens) }}</td>
                <td>{{ fmt(item.output_tokens) }}</td>
                <td>{{ item.request_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="table-card">
          <h3>按模型</h3>
          <div v-if="usageByModel.length===0" class="empty">暂无数据</div>
          <table v-else>
            <thead><tr>
              <th>名称</th><th>总量</th><th>输入</th><th>缓存</th><th>输出</th><th>次数</th>
            </tr></thead>
            <tbody>
              <tr v-for="item in usageByModel" :key="item.name">
                <td>{{ item.name }}</td>
                <td class="total-col">{{ fmt(item.total_tokens) }}</td>
                <td>{{ fmt(item.input_tokens) }}</td>
                <td>{{ fmt(item.cache_hit_tokens) }}</td>
                <td>{{ fmt(item.output_tokens) }}</td>
                <td>{{ item.request_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</div>

<script>
const { createApp, ref, reactive, onMounted, nextTick, computed } = Vue;

createApp({
  setup() {
    const rangePreset = ref('7d');
    const customStart = ref('');
    const customEnd = ref('');
    const filterProviderId = ref(null);
    const filterModelId = ref(null);
    const providers = ref([]);
    const allModels = ref([]);  // 全部模型，用于级联
    const summary = reactive({ total_input_tokens:0, total_cache_hit_tokens:0, total_output_tokens:0, total_tokens:0, total_requests:0 });
    const usageByProvider = ref([]);
    const usageByModel = ref([]);
    const trendDays = ref([]);
    const loading = ref(false);
    const trendCanvas = ref(null);
    let chart = null;

    // 当前供应商下的可选模型（级联）
    const availableModels = computed(() => {
      if (filterProviderId.value === null) return allModels.value;
      return allModels.value;  // 模型列表来自全部日志，provider 级联在切换时由后端过滤体现
    });

    function todayStr(offsetDays = 0) {
      const d = new Date();
      d.setDate(d.getDate() + offsetDays);
      return d.toISOString().slice(0, 10);
    }

    function rangeParams() {
      if (rangePreset.value === 'today') {
        const t = todayStr(0);
        return { start_date: t, end_date: t };
      }
      if (rangePreset.value === '7d') {
        return { start_date: todayStr(-6), end_date: todayStr(0) };
      }
      if (rangePreset.value === '30d') {
        return { start_date: todayStr(-29), end_date: todayStr(0) };
      }
      // custom
      return { start_date: customStart.value, end_date: customEnd.value };
    }

    function filterParams() {
      const p = {};
      if (filterProviderId.value !== null) p.provider_id = filterProviderId.value;
      if (filterModelId.value !== null) p.model_id = filterModelId.value;
      return p;
    }

    function fmt(n) {
      if (n === undefined || n === null) return '0';
      return Number(n).toLocaleString('zh-CN');
    }
    function pct(part, total) {
      if (!total) return '0%';
      return (part / total * 100).toFixed(1) + '%';
    }

    function setRange(preset) {
      rangePreset.value = preset;
      loadAll();
    }

    function onProviderFilterChange() {
      // 切换供应商时，若当前选中模型不属于该供应商则清空
      filterModelId.value = null;
      loadAll();
    }

    async function fetchJson(url, params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== null && v !== undefined && v !== '') qs.append(k, v);
      }
      const resp = await fetch(url + '?' + qs.toString());
      return resp.json();
    }

    async function loadAll() {
      loading.value = true;
      try {
        const base = { ...rangeParams(), ...filterParams() };
        const [s, up, um, tr] = await Promise.all([
          fetchJson('/api/stats/summary', base),
          fetchJson('/api/stats/usage', { ...base, group_by: 'provider' }),
          fetchJson('/api/stats/usage', { ...base, group_by: 'model' }),
          fetchJson('/api/stats/trend', base),
        ]);
        Object.assign(summary, s);
        usageByProvider.value = up.items;
        usageByModel.value = um.items;
        trendDays.value = tr.days;
        await nextTick();
        renderChart();
      } finally {
        loading.value = false;
      }
    }

    async function loadProviders() {
      const resp = await fetch('/api/providers');
      const data = await resp.json();
      providers.value = data.providers || data.items || data || [];
    }

    async function loadModels() {
      // 从按模型的统计里拿不到全部历史模型（受时间范围影响），
      // 这里用一个足够大的范围（最近 365 天）取模型列表用于下拉
      const data = await fetchJson('/api/stats/usage', {
        start_date: todayStr(-364), end_date: todayStr(0), group_by: 'model',
      });
      allModels.value = (data.items || []).map(i => i.name);
    }

    function renderChart() {
      const ctx = trendCanvas.value;
      if (!ctx) return;
      const labels = trendDays.value.map(d => d.date);
      const inp = trendDays.value.map(d => d.input_tokens);
      const cache = trendDays.value.map(d => d.cache_hit_tokens);
      const out = trendDays.value.map(d => d.output_tokens);
      const total = trendDays.value.map(d => d.total_tokens);

      if (chart) chart.destroy();
      chart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: '输入', data: inp, backgroundColor: '#3b82f6', stack: 'tokens' },
            { label: '缓存命中', data: cache, backgroundColor: '#fbbf24', stack: 'tokens' },
            { label: '输出', data: out, backgroundColor: '#22c55e', stack: 'tokens' },
            { label: '总用量', data: total, type: 'line', borderColor: '#8b5cf6',
              backgroundColor: '#8b5cf6', borderDash: [5, 4], tension: 0.3, pointRadius: 3, fill: false },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
          plugins: { tooltip: { mode: 'index', intersect: false } },
        },
      });
    }

    onMounted(async () => {
      await loadProviders();
      await loadModels();
      await loadAll();
    });

    return {
      rangePreset, customStart, customEnd, filterProviderId, filterModelId,
      providers, availableModels, summary, usageByProvider, usageByModel,
      loading, trendCanvas, fmt, pct, setRange, onProviderFilterChange, loadAll,
    };
  },
}).mount('#app');
</script>
</body>
</html>
```

- [ ] **Step 2: 在 main.py 注册 /stats 路由**

修改 `main.py`，在现有的 `@app.get("/")` 路由（第 34-36 行）之后，加入 `/stats` 路由：

```python
@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/stats")
async def stats_page():
    return FileResponse(os.path.join(static_dir, "stats.html"))
```

- [ ] **Step 3: 启动服务冒烟验证页面可访问**

Run: `python main.py`（后台启动）
然后在另一个终端：
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5020/stats
```
Expected: 返回 `200`
（验证后停止服务）

- [ ] **Step 4: 提交**

```bash
git add app/static/stats.html main.py
git commit -m "feat: 新增 /stats 用量统计前端页面（Vue + Chart.js）"
```

---

## Task 7: index.html 加导航链接

在现有管理页面顶部加一个"用量统计"入口，跳转到 `/stats`。

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: 在标题旁加导航链接**

修改 `app/static/index.html` 第 99 行，把：

```html
  <h1>Token Relay - API 中转站</h1>
```

改为：

```html
  <h1>Token Relay - API 中转站 <a href="/stats" style="font-size:14px;color:#3b82f6;text-decoration:none;margin-left:12px;">用量统计 →</a></h1>
```

- [ ] **Step 2: 验证链接渲染**

Run: `python main.py`，浏览器访问 `http://localhost:5020/`，确认标题旁出现"用量统计 →"链接，点击跳转到 `/stats`。
（验证后停止服务）

- [ ] **Step 3: 提交**

```bash
git add app/static/index.html
git commit -m "feat: 管理页面加入用量统计导航链接"
```

---

## Task 8: 端到端手动验证

启动完整服务，验证用量统计页面在真实数据下的端到端行为。

**Files:** 无（仅验证）

- [ ] **Step 1: 启动服务**

Run: `python main.py`
Expected: uvicorn 启动，监听 5020 端口，init_db 完成迁移无报错

- [ ] **Step 2: 触发几条真实请求产生用量数据**

通过 Claude Code 或直接 curl 调用代理 `/anthropic/v1/messages`，发起 2-3 次请求（含流式与非流式），确保产生带 usage 的日志。例如：

```bash
curl -X POST http://localhost:5020/anthropic/v1/messages \
  -H "x-api-key: <RELAY_API_KEY>" -H "content-type: application/json" \
  -d '{"model":"<某个已映射模型>","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'
```

- [ ] **Step 3: 验证接口返回正确**

```bash
curl -s "http://localhost:5020/api/stats/summary?start_date=$(date -u +%F)&end_date=$(date -u +%F)"
curl -s "http://localhost:5020/api/stats/usage?group_by=provider&start_date=$(date -u +%F)&end_date=$(date -u +%F)"
curl -s "http://localhost:5020/api/stats/trend?start_date=$(date -u +%F)&end_date=$(date -u +%F)"
```
Expected: 三个接口都返回 JSON，summary 的 total_requests > 0，total_tokens 等于 input+cache+output 之和

- [ ] **Step 4: 浏览器验证页面交互**

访问 `http://localhost:5020/stats`，验证：
- 汇总卡片数字与接口一致
- 趋势图渲染出柱状图 + 总量虚线
- 按供应商/按模型表格有数据，总量列 = 输入+缓存+输出
- 切换"今天/最近7天/最近30天"数据刷新
- 选择某个供应商筛选后，模型/表格数据随之变化
- 自定义日期范围可正常应用

- [ ] **Step 5: 验证空数据场景**

构造一个无请求的日期范围（如自定义一个未来日期），确认页面显示"暂无数据"，趋势图不报错。

- [ ] **Step 6: 停止服务，提交（如有验证中发现的修复）**

若无修复则无需提交。如发现并修复了问题，按问题分别提交。

- [ ] **Step 7: 更新 MEMORY.md 记录用量统计功能上线（可选）**

在 `C:\Users\admin\.claude\projects\c--Users-admin-vscode-ssh-workspace-token-relay\memory\` 下，更新或新增 memory 记录 RequestLog 已具备独立 token 列、用量统计页面已上线。

---

## Self-Review 备注

- **时区说明**：`stats_service` 按 `created_at`（UTC 存储）的日期分组，"天"边界为 UTC 0 点。对东八区用户，深夜（北京时间 0–8 点）的请求会归到 UTC 前一天。当前版本接受此行为；如需本地时区分组，可在后续迭代中在 `_parse_range` / `func.date` 处做时区偏移。
- **模型下拉数据源**：前端"模型"下拉用最近 365 天的 `usage?group_by=model` 结果填充，保证覆盖历史用过的模型；供应商级联在切换供应商时清空已选模型（后端按 provider_id 过滤体现）。
