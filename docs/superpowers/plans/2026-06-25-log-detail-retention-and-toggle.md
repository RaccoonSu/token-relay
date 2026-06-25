# 日志详情存储优化（定期清理 + 存储开关）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `RequestLog` 的 `request_body`/`response_body` 两个大 JSON 字段在超过 24h 后自动清空，并提供一个开关在关闭时不再落库这两个字段，其余字段（token 用量列、错误信息等）始终保留。

**Architecture:** 进程内 asyncio 后台任务（lifespan 启动）每 6h 执行一次 `UPDATE` 清理；开关复用 `AppSetting` 表 + 进程内存缓存，代理热路径读缓存 O(1)，写端点同步刷新缓存；写入路径在构造 `RequestLog` 时根据开关决定是否填两个字段。

**Tech Stack:** Python 3 + FastAPI + SQLAlchemy(async) + aiosqlite；前端 Vue 3(CDN)；测试 pytest + pytest-asyncio + httpx(MockTransport/ASGITransport)。

参考 spec：`docs/superpowers/specs/2026-06-25-log-detail-retention-and-toggle-design.md`

---

## File Structure

- Create: `app/services/log_setting_service.py` — 开关的读（进程缓存）/写（刷新缓存）+ DB 持久化
- Create: `app/services/log_cleanup_service.py` — `cleanup_old_log_details()` 单次清理 + `run_log_cleanup_loop()` 后台循环
- Create: `tests/test_log_setting_service.py`
- Create: `tests/test_log_cleanup_service.py`
- Create: `tests/test_proxy_detail_switch.py`
- Create: `tests/test_log_detail_setting_router.py`
- Modify: `app/config.py` — 新增 3 个常量
- Modify: `app/services/proxy_service.py` — 3 处构造 `RequestLog` 前，按开关决定是否填详情字段
- Modify: `app/routers/logs.py` — 新增 GET/PUT `/api/log-detail-setting`
- Modify: `main.py` — lifespan 预热开关缓存 + 启动/取消后台清理任务
- Modify: `app/static/index.html` — 日志 tab 工具栏加开关；详情面板对 NULL 友好提示

---

### Task 1: 新增配置常量

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: 在 `app/config.py` 末尾追加 3 个常量**

在 `DEFAULT_TARGET_KEY = "default_target_model_id"` 这一行之后追加：

```python
# 日志详情存储（调用参数 request_body / 响应参数 response_body）
LOG_DETAIL_ENABLED_KEY = "log_detail_enabled"        # AppSetting 中开关的 key
LOG_DETAIL_RETENTION_HOURS = 24                       # 详情保留时长（写死）
LOG_DETAIL_CLEANUP_INTERVAL = 6 * 60 * 60             # 后台清理间隔（秒），每 6 小时一次
```

- [ ] **Step 2: 验证可导入**

Run: `python -c "from app.config import LOG_DETAIL_ENABLED_KEY, LOG_DETAIL_RETENTION_HOURS, LOG_DETAIL_CLEANUP_INTERVAL; print(LOG_DETAIL_ENABLED_KEY, LOG_DETAIL_RETENTION_HOURS, LOG_DETAIL_CLEANUP_INTERVAL)"`
Expected: 输出 `log_detail_enabled 24 21600`，无报错

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: 新增日志详情存储相关配置常量"
```

---

### Task 2: 开关服务（进程缓存 + DB 持久化）

**Files:**
- Create: `app/services/log_setting_service.py`
- Test: `tests/test_log_setting_service.py`

- [ ] **Step 1: 写失败测试 `tests/test_log_setting_service.py`**

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_log_setting_service.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.log_setting_service'`）

- [ ] **Step 3: 实现 `app/services/log_setting_service.py`**

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_log_setting_service.py -v`
Expected: PASS（5 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add app/services/log_setting_service.py tests/test_log_setting_service.py
git commit -m "feat: 新增日志详情存储开关服务（进程缓存 + DB 持久化）"
```

---

### Task 3: 清理服务（单次清理 + 后台循环）

**Files:**
- Create: `app/services/log_cleanup_service.py`
- Test: `tests/test_log_cleanup_service.py`

- [ ] **Step 1: 写失败测试 `tests/test_log_cleanup_service.py`**

```python
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

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
    # 已清空的行不应被重复更新
    already = _make_log("already", hours_ago=30,
                        request_body=None, response_body=None)
    db.add(already)
    await db.commit()
    n = await log_cleanup_service.cleanup_old_log_details(db)
    assert n == 0
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_log_cleanup_service.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.log_cleanup_service'`）

- [ ] **Step 3: 实现 `app/services/log_cleanup_service.py`**

```python
"""日志详情定期清理。

后台循环在应用启动时开始，先立即执行一次，之后每 LOG_DETAIL_CLEANUP_INTERVAL
秒执行一次。每轮只把 created_at 早于阈值的记录的 request_body/response_body 置
NULL，其余字段（含 token 用量列、error_message）保持不变。循环内异常被吞掉并记
录，保证循环永不退出。
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LOG_DETAIL_CLEANUP_INTERVAL, LOG_DETAIL_RETENTION_HOURS
from app.database import async_session
from app.models import RequestLog

logger = logging.getLogger("log_cleanup")


async def cleanup_old_log_details(db: AsyncSession) -> int:
    """清空超过保留时长的日志详情字段，返回受影响行数。"""
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
        .values(request_body=None, response_body=None)
    )
    await db.commit()
    return result.rowcount


async def run_log_cleanup_loop() -> None:
    """后台清理循环：先执行一次，再 sleep，异常不退出。"""
    while True:
        try:
            async with async_session() as db:
                n = await cleanup_old_log_details(db)
                logger.info("log detail cleanup: cleared %s rows", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("log detail cleanup failed: %s", e)
        await asyncio.sleep(LOG_DETAIL_CLEANUP_INTERVAL)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_log_cleanup_service.py -v`
Expected: PASS（3 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add app/services/log_cleanup_service.py tests/test_log_cleanup_service.py
git commit -m "feat: 新增日志详情定期清理服务（单次清理 + 后台循环）"
```

---

### Task 4: 代理写入路径尊重开关

**Files:**
- Modify: `app/services/proxy_service.py`
- Test: `tests/test_proxy_detail_switch.py`

- [ ] **Step 1: 写失败测试 `tests/test_proxy_detail_switch.py`**

```python
import httpx
import pytest
from sqlalchemy import select

from app.models import Provider, RequestLog
from app.services import log_setting_service, proxy_service


@pytest.fixture(autouse=True)
def _reset_cache():
    log_setting_service.reset_cache()
    yield
    log_setting_service.reset_cache()


def _patch_httpx(monkeypatch, response_body, status_code=200):
    """让 proxy_service 内的 httpx.AsyncClient 走 MockTransport。"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code, json=response_body)
    )
    monkeypatch.setattr(
        proxy_service.httpx,
        "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(transport=transport),
    )


async def _seed_provider(db):
    p = Provider(name="t", base_url="http://x/api", api_key="k")
    db.add(p)
    await db.commit()
    return p


async def test_non_stream_skips_details_when_disabled(db, monkeypatch):
    await log_setting_service.set_log_detail_enabled(db, False)
    _patch_httpx(monkeypatch, {
        "id": "msg", "type": "message",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    provider = await _seed_provider(db)

    body, code = await proxy_service.proxy_non_stream(
        db, provider, {"model": "m", "x": 1}, "127.0.0.1"
    )

    assert code == 200
    log = (await db.execute(select(RequestLog))).scalar_one()
    # 详情字段未落库
    assert log.request_body is None
    assert log.response_body is None
    # 用量列与状态码正常
    assert log.input_tokens == 10
    assert log.output_tokens == 5
    assert log.status_code == 200


async def test_non_stream_stores_details_when_enabled(db, monkeypatch):
    await log_setting_service.set_log_detail_enabled(db, True)
    resp = {
        "id": "msg", "type": "message",
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    _patch_httpx(monkeypatch, resp)
    provider = await _seed_provider(db)

    await proxy_service.proxy_non_stream(
        db, provider, {"model": "m", "x": 1}, "127.0.0.1"
    )

    log = (await db.execute(select(RequestLog))).scalar_one()
    assert log.request_body == {"model": "m", "x": 1}
    assert log.response_body == resp
    assert log.input_tokens == 7
    assert log.output_tokens == 3
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_proxy_detail_switch.py::test_non_stream_skips_details_when_disabled -v`
Expected: FAIL（详情字段被落库，断言 `is None` 失败）

- [ ] **Step 3: 修改 `app/services/proxy_service.py`**

**(a) 顶部 import 区**（在 `from app.utils import extract_usage` 之后新增一行）：

```python
from app.services.log_setting_service import get_log_detail_enabled
```

**(b) `proxy_non_stream`**：在 `start_time = time.time()` 之后新增一行读开关；构造 `RequestLog` 时与设置 `response_body` 时按开关三元。最终该函数前半段应为：

```python
async def proxy_non_stream(
    db: AsyncSession,
    provider: Provider,
    request_body: dict,
    client_ip: str,
) -> tuple[dict, int]:
    """Forward a non-streaming request and log it."""
    request_id = str(uuid.uuid4())
    start_time = time.time()
    store_detail = await get_log_detail_enabled()

    target_url = f"{provider.base_url.rstrip('/')}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
    }

    log_entry = RequestLog(
        request_id=request_id,
        model_id=request_body.get("model", ""),
        provider_id=provider.id,
        request_body=request_body if store_detail else None,
        is_stream=False,
        client_ip=client_ip,
    )
```

并在设置响应体的那一行改为三元（原 `log_entry.response_body = response_body`）：

```python
        log_entry.response_body = response_body if store_detail else None
```

> 注意：`extract_usage(response_body)` 仍然照常执行（用的是内存中的 `response_body` 变量，不依赖 DB 存储），token 用量列不受影响。其余 error/duration/status 逻辑保持不变。

**(c) `proxy_stream`**：在函数开头 `start_time = time.time()` 之后新增读开关：

```python
async def proxy_stream(
    provider: Provider,
    request_body: dict,
    client_ip: str,
) -> AsyncGenerator[str, None]:
    """Forward a streaming request, yield SSE chunks, and log the aggregated response."""
    request_id = str(uuid.uuid4())
    start_time = time.time()
    store_detail = await get_log_detail_enabled()
```

**流式错误分支**构造 `RequestLog` 处（`status_code >= 400` 分支内），把 `request_body=request_body` 与 `response_body=error_body` 改为三元：

```python
                        log_entry = RequestLog(
                            request_id=request_id,
                            model_id=request_body.get("model", ""),
                            provider_id=provider.id,
                            request_body=request_body if store_detail else None,
                            response_body=error_body if store_detail else None,
                            status_code=status_code,
                            is_stream=True,
                            duration_ms=duration_ms,
                            error_message=error_message,
                            client_ip=client_ip,
                        )
```

**流式正常分支**构造 `RequestLog` 处（函数末尾），同样改两行为三元：

```python
        log_entry = RequestLog(
            request_id=request_id,
            model_id=request_body.get("model", ""),
            provider_id=provider.id,
            request_body=request_body if store_detail else None,
            response_body=aggregated_response if store_detail else None,
            status_code=status_code,
            is_stream=True,
            duration_ms=duration_ms,
            error_message=error_message,
            client_ip=client_ip,
        )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_proxy_detail_switch.py -v`
Expected: PASS（2 个测试全过）

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `pytest -q`
Expected: 全部 PASS（含原有 stats/utils 测试）

- [ ] **Step 6: Commit**

```bash
git add app/services/proxy_service.py tests/test_proxy_detail_switch.py
git commit -m "feat: 代理写入路径根据开关决定是否落库调用/响应详情"
```

---

### Task 5: 开关 API 端点

**Files:**
- Modify: `app/routers/logs.py`
- Test: `tests/test_log_detail_setting_router.py`

- [ ] **Step 1: 写失败测试 `tests/test_log_detail_setting_router.py`**

```python
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.models import AppSetting
from app.config import LOG_DETAIL_ENABLED_KEY
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_log_detail_setting_router.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 修改 `app/routers/logs.py`**

顶部 import 区，把：
```python
from app.models import RequestLog
from app.utils import extract_usage
```
改为：
```python
from app.models import RequestLog
from app.services.log_setting_service import (
    get_log_detail_enabled,
    set_log_detail_enabled,
)
from app.utils import extract_usage
```
并新增 Pydantic 模型（紧跟 `router = APIRouter(prefix="/api")` 之后）：
```python
class LogDetailSettingUpdate(BaseModel):
    enabled: bool
```
（需在 import 中加入 `from pydantic import BaseModel`。）

在文件末尾（`clear_logs` 端点之后）新增两个端点：
```python
@router.get("/log-detail-setting")
async def get_log_detail_setting(db: AsyncSession = Depends(get_db)):
    return {"enabled": await get_log_detail_enabled(db)}


@router.put("/log-detail-setting")
async def put_log_detail_setting(
    data: LogDetailSettingUpdate, db: AsyncSession = Depends(get_db)
):
    await set_log_detail_enabled(db, data.enabled)  # 内部已刷新缓存
    return {"enabled": data.enabled}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_log_detail_setting_router.py -v`
Expected: PASS（2 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add app/routers/logs.py tests/test_log_detail_setting_router.py
git commit -m "feat: 新增日志详情存储开关的 GET/PUT API"
```

---

### Task 6: 后台清理任务生命周期

**Files:**
- Modify: `main.py`

> 说明：后台 asyncio 循环与 lifespan 装配难以无副作用地自动化测试（会触发真实 engine / 真实 DB），核心清理逻辑已在 Task 3 覆盖。本任务做实现 + 启动期手动验证。

- [ ] **Step 1: 修改 `main.py` 的 `lifespan`**

把整个文件改为（保留原有 import 并新增）：

```python
import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import RELAY_PORT
from app.database import async_session, init_db
from app.middleware import LocalhostOnlyMiddleware
from app.routers import proxy, providers, logs, stats
from app.services.log_cleanup_service import run_log_cleanup_loop
from app.services.log_setting_service import load_log_detail_enabled


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 预热开关缓存
    async with async_session() as db:
        await load_log_detail_enabled(db)
    # 启动后台清理任务（启动即执行一次，之后每 6h 一次）
    app.state.log_cleanup_task = asyncio.create_task(run_log_cleanup_loop())
    try:
        yield
    finally:
        app.state.log_cleanup_task.cancel()
        try:
            await app.state.log_cleanup_task
        except asyncio.CancelledError:
            pass
```

（文件其余部分——`app = FastAPI(...)`、`include_router`、`mount`、路由、`__main__`——保持不变。）

- [ ] **Step 2: 启动验证后台任务运行**

Run: `python main.py`（启动后观察日志）
Expected: 启动无报错；后台任务立即执行一次清理（可在控制台/日志看到清理行数）。手动 Ctrl+C 关闭时无 `Task was destroyed but it is pending!` 报错（说明任务被优雅取消）。

- [ ] **Step 3: 跑全量测试确认无回归**

Run: `pytest -q`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: lifespan 启动日志详情清理后台任务并预热开关缓存"
```

---

### Task 7: 前端开关 + 详情面板对 NULL 友好提示

**Files:**
- Modify: `app/static/index.html`

> 说明：前端为 CDN Vue 单文件，无自动化测试，做实现 + 手动验证。

- [ ] **Step 1: 修改日志 tab 工具栏，加入开关**

把 `app/static/index.html` 中（约 186-192 行）：
```html
    <div class="toolbar">
      <span>共 {{ logsTotal }} 条记录</span>
      <div style="display:flex; gap:8px;">
        <button class="btn btn-primary" @click="loadLogs()">刷新</button>
        <button class="btn btn-danger" @click="clearLogs()">清空日志</button>
      </div>
    </div>
```
替换为：
```html
    <div class="toolbar">
      <span>共 {{ logsTotal }} 条记录</span>
      <div style="display:flex; gap:12px; align-items:center;">
        <label style="display:flex; gap:6px; align-items:center; font-size:14px; color:#475569; cursor:pointer; user-select:none;">
          <input type="checkbox" v-model="logDetailEnabled" @change="saveLogDetailSetting()"> 存储调用详情
        </label>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-primary" @click="loadLogs()">刷新</button>
          <button class="btn btn-danger" @click="clearLogs()">清空日志</button>
        </div>
      </div>
    </div>
```

- [ ] **Step 2: 详情面板对 NULL 友好提示**

把请求体区块（约 227-233 行）：
```html
                  <div class="log-section">
                    <div class="section-header">
                      <h4>请求体 (Request)</h4>
                      <button class="btn-copy" :class="{copied: copyState.request}" @click="copyText(JSON.stringify(logDetail.request_body, null, 2), 'request')">复制</button>
                    </div>
                    <pre>{{ JSON.stringify(logDetail.request_body, null, 2) }}</pre>
                  </div>
```
替换为：
```html
                  <div class="log-section">
                    <div class="section-header">
                      <h4>请求体 (Request)</h4>
                      <button v-if="logDetail.request_body !== null" class="btn-copy" :class="{copied: copyState.request}" @click="copyText(JSON.stringify(logDetail.request_body, null, 2), 'request')">复制</button>
                    </div>
                    <pre v-if="logDetail.request_body !== null">{{ JSON.stringify(logDetail.request_body, null, 2) }}</pre>
                    <pre v-else style="color:#94a3b8; font-style:italic;">（详情未存储：开关已关闭或已过期清理）</pre>
                  </div>
```

把响应体区块（约 234-240 行）：
```html
                  <div class="log-section">
                    <div class="section-header">
                      <h4>响应体 (Response)</h4>
                      <button class="btn-copy" :class="{copied: copyState.response}" @click="copyText(JSON.stringify(logDetail.response_body, null, 2), 'response')">复制</button>
                    </div>
                    <pre>{{ JSON.stringify(logDetail.response_body, null, 2) }}</pre>
                  </div>
```
替换为：
```html
                  <div class="log-section">
                    <div class="section-header">
                      <h4>响应体 (Response)</h4>
                      <button v-if="logDetail.response_body !== null" class="btn-copy" :class="{copied: copyState.response}" @click="copyText(JSON.stringify(logDetail.response_body, null, 2), 'response')">复制</button>
                    </div>
                    <pre v-if="logDetail.response_body !== null">{{ JSON.stringify(logDetail.response_body, null, 2) }}</pre>
                    <pre v-else style="color:#94a3b8; font-style:italic;">（详情未存储：开关已关闭或已过期清理）</pre>
                  </div>
```

- [ ] **Step 3: 新增 Vue 状态与方法**

在 `setup()` 内（`const logDetail = ref(null)` 之后）新增一行状态：
```javascript
    const logDetailEnabled = ref(false)
```

在 `loadLogs` 函数之前新增两个方法：
```javascript
    async function loadLogDetailSetting() {
      try {
        const data = await api('/api/log-detail-setting')
        logDetailEnabled.value = !!data.enabled
      } catch (e) {
        // 静默失败，不影响日志列表加载
      }
    }

    async function saveLogDetailSetting() {
      const prev = !logDetailEnabled.value
      try {
        await api('/api/log-detail-setting', {
          method: 'PUT',
          body: JSON.stringify({ enabled: logDetailEnabled.value }),
        })
      } catch (e) {
        logDetailEnabled.value = prev  // 乐观更新失败则回滚
        alert('保存失败：' + (e && e.message ? e.message : e))
      }
    }
```

修改 `loadLogs`，在函数开头加一行拉取开关状态（最终如下）：
```javascript
    async function loadLogs() {
      await loadLogDetailSetting()
      const data = await api(`/api/logs?page=${logsPage.value}&size=${logsSize}`)
      logs.value = data.items
      logsTotal.value = data.total
      expandedLog.value = null
      logDetail.value = null
    }
```

在 `return { ... }` 对象里加入 `logDetailEnabled` 和两个方法（在 `loadLogs, toggleLogDetail, clearLogs, formatTime,` 这一行附近加入）：
```javascript
      logs, logsTotal, logsPage, logsSize, expandedLog, logDetail,
      logDetailEnabled, loadLogDetailSetting, saveLogDetailSetting,
      loadLogs, toggleLogDetail, clearLogs, formatTime,
```

- [ ] **Step 4: 手动验证**

Run: `python main.py`，浏览器打开 `http://localhost:5020` → 「请求日志」tab
Expected:
- 工具栏出现「存储调用详情」复选框，默认未勾选
- 勾选/取消切换：PUT 请求成功，刷新后状态保持
- 关闭时发一条请求：日志列表 token 列正常显示，点开详情显示「（详情未存储：…）」
- 开启时发一条请求：详情正常显示请求体/响应体

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html
git commit -m "feat: 日志页加入存储详情开关，详情面板对未存储友好提示"
```

---

## Self-Review（计划作者已执行）

**Spec 覆盖：**
- 定期清理 24h → Task 3（cleanup）+ Task 6（loop/lifespan）✓
- 开关默认关、放日志 tab、复用 AppSetting → Task 2（service）+ Task 5（API）+ Task 7（前端）✓
- 阈值写死 24h、开关可切、进程内存缓存 + 写时刷新 → Task 1 常量 + Task 2 缓存设计 ✓
- 仅清/影响 request_body/response_body，其他字段不变 → Task 3 测试断言其他字段保留 ✓
- usage 列不受开关影响 → Task 4 测试断言 input/output_tokens 仍填充 ✓
- 三处写入位置（non_stream / stream 错误 / stream 正常）→ Task 4 步骤 3 (b)(c) 覆盖 ✓
- 前端详情面板 NULL 友好提示 → Task 7 步骤 2 ✓

**占位符扫描：** 无 TBD/TODO，每个代码步骤均给出完整代码。

**类型一致性：** `get_log_detail_enabled(db=None)` / `set_log_detail_enabled(db, bool)` / `load_log_detail_enabled(db)` / `reset_cache()` 在 Task 2 定义，Task 4/5/6 使用一致；常量名 `LOG_DETAIL_ENABLED_KEY`/`LOG_DETAIL_RETENTION_HOURS`/`LOG_DETAIL_CLEANUP_INTERVAL` 在 Task 1 定义，Task 2/3/6 引用一致。
