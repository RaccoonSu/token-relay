# Token Relay - 用量统计面板设计文档

## 概述

为 Token Relay 新增一个独立的用量统计页面 `/stats`，基于已记录的 `RequestLog` 数据，按供应商、模型、时间三个维度聚合展示 Token 用量。帮助用户了解各供应商/模型的消耗分布和随时间的变化趋势。

## 目标

- 按供应商、模型维度统计 Token 用量（输入/缓存命中/输出/总量）
- 展示时间维度上的用量变化趋势
- 支持灵活的时间范围筛选（预设快捷项 + 自定义日期）
- 支持按供应商、模型进行二次过滤
- 与现有项目架构风格保持一致（后端 SQL 聚合，前端只负责渲染）

## 非目标（YAGNI）

- 不做费用估算（暂不引入模型单价配置）
- 不做请求耗时分析
- 不做导出功能（如需可在后续迭代加入）
- 不做实时刷新（手动刷新即可）

## 架构

### 方案选型

采用**后端 SQL 聚合**方案：新增 `/api/stats/*` 接口，在数据库层用 GROUP BY 完成聚合，前端直接渲染预聚合结果。

理由：
- 项目已有清晰的 Router → Service → DB 分层，用量统计天然适合在 SQL 层聚合
- 大数据量时性能稳定，无需把全部日志传到前端
- 前端保持轻量的渲染职责，与现有架构一致

### 技术栈

沿用现有技术栈，无新增依赖：
- **后端**: FastAPI + SQLAlchemy（async）
- **前端**: Vue 3（CDN 引入）+ Chart.js 4（CDN 引入，用于图表渲染）

### 数据模型变更：RequestLog 增加独立 token 列

当前 token 用量埋在 `RequestLog.response_body` JSON 列里，每次聚合都要解析全部行的 JSON，无法建索引，数据量增长后查询会越来越慢。

为此给 `RequestLog` 增加 4 个独立的 Integer 列：

| 列名 | 来源 | 说明 |
|------|------|------|
| `input_tokens` | `usage.input_tokens` | 输入 token |
| `cache_hit_tokens` | `usage.cache_read_input_tokens` + `usage.cache_creation_input_tokens` | 缓存命中 |
| `output_tokens` | `usage.output_tokens` | 输出 token |
| `total_tokens` | 上述三项之和 | 总用量 |

写入日志时由 [proxy_service.py](../../../app/services/proxy_service.py) 计算并填充。历史数据不重要、不做回填，旧日志的 token 列保持 NULL，聚合时按 0 处理。聚合查询改为对这 4 列直接 `SUM()` + `GROUP BY`，查询飞快。

由于 `init_db()` 的 `create_all` 不会给已存在的表加列，需在启动时对已有数据库执行一次 `ALTER TABLE ADD COLUMN`（幂等，检测列不存在才加）。

## 后端设计

### 共享工具：提取 usage 函数重构

将 [logs.py](../../../app/routers/logs.py) 中现有的 `_extract_usage()` 函数提取到 `app/utils.py`（新建），供 logs 和 stats 共享调用，避免逻辑重复。

函数签名：
```python
def extract_usage(response_body: dict | None) -> dict:
    """从 response_body 提取 token 用量，返回包含
    input_tokens / cache_hit_tokens / output_tokens / total_tokens 的字典。
    缺失字段按 0 处理。"""
```

### 新增路由：`app/routers/stats.py`

挂载在 `/api/stats`，提供 3 个 GET 接口。所有接口均支持以下公共查询参数：
- `start_date`（YYYY-MM-DD，默认最近 7 天起始）
- `end_date`（YYYY-MM-DD，默认今天）
- `provider_id`（可选，按供应商过滤）
- `model_id`（可选，按模型过滤）

#### 1. `GET /api/stats/summary`

返回时间范围内的总量概览，供顶部汇总卡片使用。

响应：
```json
{
  "total_input_tokens": 125000,
  "total_cache_hit_tokens": 80000,
  "total_output_tokens": 45000,
  "total_tokens": 250000,
  "total_requests": 150
}
```

#### 2. `GET /api/stats/usage?group_by=provider|model`

按指定维度聚合，供明细表格使用。`group_by` 必填。

响应（`group_by=provider` 时 `name` 为供应商名称；`group_by=model` 时 `name` 为模型 ID）：
```json
{
  "group_by": "provider",
  "items": [
    {
      "name": "阿里云百炼",
      "input_tokens": 80000,
      "cache_hit_tokens": 50000,
      "output_tokens": 30000,
      "total_tokens": 160000,
      "request_count": 95
    }
  ]
}
```

#### 3. `GET /api/stats/trend`

按天聚合的趋势数据，供趋势图使用。

响应：
```json
{
  "days": [
    {
      "date": "2026-06-10",
      "input_tokens": 20000,
      "cache_hit_tokens": 12000,
      "output_tokens": 8000,
      "total_tokens": 40000,
      "request_count": 30
    }
  ]
}
```

### 新增 Service：`app/services/stats_service.py`

封装所有聚合查询逻辑，沿用现有项目的 Service 分层风格。核心查询模式：对 `RequestLog` 按 `created_at` 时间范围、`provider_id`、`model_id` 过滤后，GROUP BY 指定字段，对 token 字段求和、对记录数计数。

按供应商聚合时需 JOIN `Provider` 表获取供应商名称；按模型聚合直接按 `RequestLog.model_id` 分组。

日期聚合使用 SQLite 的 `DATE(created_at)` 函数提取日期部分。

## 前端设计

### 页面路由：独立 `/stats`

- 新增 `app/static/stats.html`（独立 Vue 应用，复用 index.html 的 CSS 样式风格）
- 在 [main.py](../../../main.py) 注册路由：`GET /` 返回 index.html，`GET /stats` 返回 stats.html
- 两个页面之间互相加导航链接（index.html 顶部加"用量统计"入口，stats.html 顶部加"返回"入口）

### 页面布局（纵向堆叠）

```
┌──────────────────────────────────────────┐
│ 📊 用量统计              [今天][7天][30天][📅自定义] │
├──────────────────────────────────────────┤
│ 供应商 [全部▾]   模型 [全部▾]                │  ← 筛选器
├────────┬────────┬────────┬────────┬──────┤
│ 总用量  │ 总输入  │ 缓存命中 │ 总输出  │请求次数│  ← 5 个汇总卡片
├────────┴────────┴────────┴────────┴──────┤
│   📈 Token 用量趋势（堆叠柱状图 + 总量虚线）      │  ← Chart.js mixed chart
├────────────────────┬─────────────────────┤
│ 按供应商（表格）       │ 按模型（表格）          │  ← 并排明细表格
│ 名称|总量|输入|缓存|输出|次数 │ 名称|总量|输入|缓存|输出|次数│
└────────────────────┴─────────────────────┘
```

### 组件职责

1. **时间范围选择器**：预设快捷项（今天/最近7天/最近30天）+ 自定义日期范围选择器。切换时重新拉取所有数据。
2. **筛选器**：供应商下拉（选项从 Provider 表动态读取）、模型下拉（若已选供应商，只显示该供应商下的模型，级联联动）。变更时重新拉取所有数据。
3. **汇总卡片**：5 个卡片展示总用量、总输入、缓存命中（含占比百分比）、总输出、请求次数。
4. **趋势图**：Chart.js mixed chart —— 堆叠柱状图展示输入/缓存命中/输出构成，叠加一条紫色虚线展示每日总用量走势。横轴为日期，缺失日期补 0 值保证折线连续。
5. **明细表格**：并排两个表格，分别按供应商和模型展示用量明细，列含 名称|总量|输入|缓存|输出|次数。

### 数据加载

时间范围或筛选器任一变化时，并行请求 3 个接口（summary、usage×2、trend），全部完成后更新视图。

## 边界情况处理

- **时间范围内无数据**：后端返回 0 值；前端趋势图和表格显示"暂无数据"占位。
- **日志无 usage 字段**（旧数据或错误请求）：token 各项按 0 处理，不影响聚合。
- **趋势图日期补齐**：按所选时间范围补齐每一天，无请求的日期补一条全 0 记录，保证折线连续。
- **供应商/模型删除后**：明细表格中仍展示历史数据对应项（基于 `provider_id`/`model_id` 的 JOIN，若供应商已删则名称显示为"未知供应商"）。

## 文件清单

**新增：**
- `app/utils.py` — 共享的 usage 提取函数
- `app/routers/stats.py` — 用量统计路由（3 个接口）
- `app/services/stats_service.py` — 聚合查询逻辑
- `app/static/stats.html` — 用量统计前端页面

**修改：**
- `app/models.py` — `RequestLog` 增加 `input_tokens`/`cache_hit_tokens`/`output_tokens`/`total_tokens` 4 个 Integer 列
- `app/database.py` — `init_db()` 加幂等的 `ALTER TABLE ADD COLUMN` 迁移逻辑
- `app/services/proxy_service.py` — 写日志时用 `extract_usage()` 填充新列
- `main.py` — 注册 `/stats` 路由、挂载 stats router
- `app/routers/logs.py` — 将 `_extract_usage()` 替换为引用 `app/utils.py` 的共享函数
- `app/static/index.html` — 顶部加"用量统计"导航链接

## 测试验证

- 启动服务后访问 `/stats`，切换不同时间范围确认数据正确
- 选择特定供应商/模型筛选，确认联动过滤生效
- 验证汇总卡片、趋势图、明细表格数据一致（总量应等于各分项之和）
- 验证空数据场景的占位展示
