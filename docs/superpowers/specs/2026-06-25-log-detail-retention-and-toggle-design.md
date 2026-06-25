# Token Relay - 日志详情存储优化设计文档

## 概述

对 `RequestLog` 中的详细调用参数（`request_body`）和响应参数（`response_body`）两个大 JSON 字段做两件事：

1. **定期清理**：超过 24 小时的记录，把这两个字段置为 `NULL`，其余字段（含 token 用量列、`error_message`、`status_code` 等）保持不变。用于控制数据库体积，避免历史详情无限堆积。
2. **存储开关**：新增「存储调用详情」开关，关闭时新写入的日志不再落库这两个字段，其余字段照常存储。用于在不需要排查时彻底关闭详情采集。

## 目标

- 超 24h 的日志详情自动清理，过程零运维、随应用进程自包含
- 提供一个开关，可即时关闭/开启详情采集，关闭后新日志不再写入 `request_body`/`response_body`
- 开关状态对代理热路径零额外 DB 开销（进程内存缓存）
- 清理与开关都只作用于「调用参数 / 响应参数」两个字段，token 用量统计、错误信息等其他数据不受影响
- 与现有项目架构风格一致（Router → Service → DB，AppSetting 存配置，幂等迁移范式）

## 非目标（YAGNI）

- 不做 24h 阈值的可配置（前端写死 24h，常量定义在代码中）
- 不做清理频率的前端可配置（启动跑一次 + 每 6 小时跑一次，常量写死）
- 不清理/不影响 `error_message`、token 用量列等其他字段
- 不回溯清理开关切换前已写入的历史详情（由定期清理任务按时间统一处理）
- 不做多进程/多 worker 下的缓存广播一致性（见「假设与约束」）

## 假设与约束

- **单进程部署**：项目以 `uvicorn(reload=True)` 单进程运行。开关缓存为进程内全局变量，仅在当前进程内有效。若未来改为多 worker 部署，每个 worker 持有独立缓存，PUT 端点只刷新处理该请求的 worker；其它 worker 的缓存会在下次进程重启后自然对齐。当前不为此引入跨进程广播。
- 数据库为本地 SQLite（aiosqlite），清理使用一条带 `WHERE` 的 `UPDATE` 完成，无需分批。
- 时区：`RequestLog.created_at` 以 UTC 写入（见 `app/models.py`），清理的 `cutoff` 也用 UTC 计算。

## 架构

### 方案选型

**清理触发：进程内后台定时任务（asyncio）**

项目当前没有任何定时任务基础设施，也不依赖系统 cron。采用 FastAPI `lifespan` 启动的 asyncio 后台任务：

- 启动时**立即执行一次**清理（清掉停机期间积累的过期详情）
- 之后每 `LOG_DETAIL_CLEANUP_INTERVAL`（默认 6 小时）执行一次
- 每轮用独立 `async_session`，异常被捕获并记录，**绝不让循环退出**

后果：一条详情最长存活约 `24h + 6h = 30h` 后被清空，但永远不超过该上界；清理负载极低（单条 `UPDATE`）。

**开关读取：进程内存缓存 + 写时刷新**

代理热路径每次请求都要知道「是否存储详情」。采用：

- 模块级缓存 `_cache_enabled: bool | None`（`None` 表示尚未加载）
- 首次访问时懒加载（从 DB 读一次）
- `PUT /api/log-detail-setting` 在写 DB 后**同步刷新缓存**
- 代理路径读缓存，O(1)，零 DB 开销

理由：避免每个代理请求多一次 DB 查询；写操作极低频（手动切开关），写时刷新的代价可忽略。

### 模块划分

```
app/config.py                    新增 3 个常量
app/models.py                    无改动（request_body/response_body 已 nullable JSON）
app/services/log_setting_service 新建：开关的读（缓存）/写（刷新）+ DB 持久化
app/services/log_cleanup_service 新建：清理函数 + 后台循环任务
app/services/proxy_service.py    构造 RequestLog 前读开关，决定是否填详情
app/routers/logs.py              新增 GET/PUT /api/log-detail-setting
main.py                          lifespan 启动后台任务；shutdown 取消；启动加载缓存
app/static/index.html            日志 tab 顶部加开关；详情面板处理 NULL
tests/                           清理 / 开关 / 写入尊重开关 的单测
```

## 详细设计

### 1. 配置常量（`app/config.py`）

```python
# 日志详情存储
LOG_DETAIL_ENABLED_KEY = "log_detail_enabled"        # AppSetting 中的 key
LOG_DETAIL_RETENTION_HOURS = 24                       # 详情保留时长（写死）
LOG_DETAIL_CLEANUP_INTERVAL = 6 * 60 * 60             # 后台清理间隔（秒）
```

### 2. 数据模型

**无 schema 变更**。`RequestLog.request_body` 与 `response_body` 已是 `Column(JSON, nullable=True)`（`app/models.py:45-46`）。开关复用现有的 `AppSetting` key-value 表（与 `DEFAULT_TARGET_KEY` 同一模式），布尔值以 `"true"`/`"false"` 字符串存储。

### 3. 开关服务（新建 `app/services/log_setting_service.py`）

进程内缓存 + DB 持久化，复用 `provider_service.get/set_default_target` 的 AppSetting 写法：

```python
_cache_enabled: bool | None = None   # None = 未加载

async def _read_db(db) -> bool:
    """从 AppSetting 读，缺失视为 False（对齐「默认关」）。"""

async def get_log_detail_enabled(db=None) -> bool:
    """代理热路径调用。缓存命中时 O(1；未加载时用传入的 db 或新 session 懒加载一次。"""

async def set_log_detail_enabled(db, enabled: bool) -> None:
    """写 AppSetting，并刷新进程缓存。"""

async def load_log_detail_enabled(db) -> None:
    """lifespan startup 调用，从 DB 预热缓存。"""
```

- 默认值 **False**（开关默认关，符合用户偏好）
- 写入后立即更新 `_cache_enabled`，保证同进程内后续读取立刻生效
- 提供 `load_*` 供启动预热；`get_*` 的懒加载兜底保证测试/未预热场景也能工作

### 4. 清理服务（新建 `app/services/log_cleanup_service.py`）

```python
async def cleanup_old_log_details(db) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOG_DETAIL_RETENTION_HOURS)
    # 只清两个字段，且仅当至少一个非 NULL 时才更新（避免无谓写入）
    result = await db.execute(text("""
        UPDATE request_logs
        SET request_body = NULL, response_body = NULL
        WHERE created_at < :cutoff
          AND (request_body IS NOT NULL OR response_body IS NOT NULL)
    """), {"cutoff": cutoff})
    await db.commit()
    return result.rowcount

async def run_log_cleanup_loop():
    """后台循环：先跑一次，再 sleep，异常不退出。"""
    while True:
        try:
            async with async_session() as db:
                n = await cleanup_old_log_details(db)
                # 记录日志（如 print 或 logging），n 为本次清理行数
        except Exception as e:
            # 记录异常，吞掉，保证循环存活
        await asyncio.sleep(LOG_DETAIL_CLEANUP_INTERVAL)
```

要点：
- `cutoff` 用 UTC；`created_at` 也是 UTC，可直接比较
- `WHERE` 带 `IS NOT NULL` 谓词，使绝大多数已清理的行在后续轮次中被索引层快速跳过，避免重复空更新
- 循环结构为「先执行后 sleep」，保证启动即清理

### 5. 后台任务生命周期（`main.py` lifespan）

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 预热开关缓存
    async with async_session() as db:
        await load_log_detail_enabled(db)
    # 启动后台清理任务
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

### 6. 写入尊重开关（`app/services/proxy_service.py`）

`proxy_non_stream` 与 `proxy_stream`（含流式 `status_code >= 400` 错误分支）共三处构造 `RequestLog` 的位置，改为：

- **流程不变**：照常解析 `response_body`、`extract_usage`、设置 `error_message`、`status_code`、`duration_ms`
- **仅构造 `RequestLog` 时**读取开关：
  ```python
  store_detail = await get_log_detail_enabled()
  log_entry = RequestLog(
      ...,
      request_body=request_body if store_detail else None,
      response_body=response_body if store_detail else None,
      ...
  )
  ```
- token 用量列（`input_tokens` 等）**始终填充**（来自 `extract_usage`，不依赖开关）
- 流式分支用独立 `async_session`，读开关走缓存，无额外 DB 查询

> 关键不变量：关闭开关后，`status_code` / `duration_ms` / `error_message` / token 用量列仍完整落库，仅两个大 JSON 字段为 `NULL`。用量统计面板、日志列表的 usage 列均不受影响。

### 7. API 端点（`app/routers/logs.py`）

```python
class LogDetailSettingUpdate(BaseModel):
    enabled: bool

@router.get("/log-detail-setting")
async def get_log_detail_setting(db: AsyncSession = Depends(get_db)):
    return {"enabled": await get_log_detail_enabled(db)}

@router.put("/log-detail-setting")
async def put_log_detail_setting(data: LogDetailSettingUpdate, db: AsyncSession = Depends(get_db)):
    await set_log_detail_enabled(db, data.enabled)   # 内部已刷新缓存
    return {"enabled": data.enabled}
```

### 8. 前端（`app/static/index.html`）

- 日志 tab 工具栏（[index.html:185-191](app/static/index.html#L185-L191) 现有「清空日志」旁）新增一个 toggle：「存储调用详情」
- 切到 logs tab 时 `GET /api/log-detail-setting` 拉取状态
- 切换时 `PUT /api/log-detail-setting`，乐观更新 UI，失败回滚并提示
- 日志详情面板（`toggleLogDetail`）：当 `request_body` / `response_body` 为 `null` 时，显示提示文案「详情未存储（开关已关闭或已过期清理）」，而非渲染空白

### 9. 测试（沿用 `tests/conftest.py` 内存 SQLite fixture）

1. **`cleanup_old_log_details`**
   - 造 1 条 `created_at` 在 25h 前、1 条在 1h 前，两者 `request_body`/`response_body` 均非空、`status_code`/`error_message`/`input_tokens` 有值
   - 执行清理：断言老记录两字段变 `NULL`、其余字段（含 `status_code`、`error_message`、`input_tokens`）**原样保留**；新记录两字段**不受影响**
2. **`get/set_log_detail_enabled`**
   - 缺省读取为 `False`
   - `set(True)` 后 `get()` 返回 `True`，且 DB 中 AppSetting 值为 `"true"`
3. **写入尊重开关**
   - 在开关 `False` 下走 `proxy_non_stream`（mock httpx 响应），断言落库的 `request_body`/`response_body` 为 `NULL`，而 `input_tokens`/`output_tokens`/`status_code` 正常填充
   - 开关 `True` 下断言两字段正常落库
   - 流式分支同断言

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 后台任务异常导致清理永久停止 | 循环内 `try/except` 吞异常并记录，循环不退出 |
| 缓存与 DB 不一致（多进程） | 已在「假设与约束」注明单进程假设；当前部署为单进程 |
| 清理 UPDATE 锁表影响代理写入 | SQLite 本地、`WHERE created_at < cutoff` 命中行有限，且每 6h 才跑一次，影响可忽略 |
| 关闭开关后历史详情仍残留 | 由定期清理按时间统一处理；用户也可用现有「清空日志」按钮立即清空全部 |

## 验收标准

- [ ] 开关默认关闭：新日志的 `request_body`/`response_body` 为 `NULL`，usage 列正常
- [ ] 开关打开后，新写入的日志详情正常落库
- [ ] 超 24h 的记录，仅 `request_body`/`response_body` 被置 `NULL`，其他字段不变
- [ ] 应用启动时自动清理一次过期详情，之后每 6h 一次
- [ ] 后台任务异常不中断后续清理
- [ ] 前端日志 tab 可切换开关，详情面板对 NULL 字段显示友好提示
- [ ] 全部新增单测通过
