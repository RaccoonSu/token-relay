# Token Relay - API 中转站设计文档

## 概述

一个轻量级 API 代理中转站，将三个 Anthropic 兼容的 API 供应商统一在一个入口后面。通过模型 ID 路由请求，提供 Web 配置界面，记录所有请求（含 SSE 流式聚合）。

## 目标

- 多个 Anthropic 兼容供应商的统一入口
- 切换模型/供应商时无需修改 Claude Code 配置
- 完整的请求日志记录，支持流式响应聚合
- 简单的 Web UI 用于配置和查看日志

## 供应商

| 名称     | Base URL                                                         |
|----------|------------------------------------------------------------------|
| 阿里百炼 | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` |
| 智谱     | `https://open.bigmodel.cn/api/anthropic`                         |
| DeepSeek | `https://api.deepseek.com/anthropic`                             |

## 架构

### 技术栈

- **后端**: Python 3.11+ / FastAPI
- **前端**: 单 HTML 文件 + Vue 3（CDN 引入）
- **数据库**: SQLite + SQLAlchemy
- **HTTP 客户端**: httpx（异步，支持 SSE）

### 项目结构

```
token-relay/
├── main.py                    # 入口，启动 uvicorn
├── requirements.txt           # 依赖列表
├── .env.example               # 环境变量模板
├── app/
│   ├── __init__.py
│   ├── config.py              # 全局配置（端口、Token 等）
│   ├── database.py            # SQLite + SQLAlchemy 设置
│   ├── models.py              # SQLAlchemy 数据模型
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── proxy.py           # Anthropic API 代理路由
│   │   ├── providers.py       # 供应商 CRUD
│   │   └── logs.py            # 日志查询
│   ├── services/
│   │   ├── __init__.py
│   │   ├── proxy_service.py   # 代理核心逻辑（路由分发 + SSE 聚合）
│   │   └── provider_service.py# 供应商管理
│   └── static/
│       └── index.html         # 前端单页面（Vue 3）
└── data/                      # SQLite 数据库文件目录（gitignored）
```

### 数据模型

#### Provider（供应商）

| 字段       | 类型       | 说明                           |
|------------|-----------|-------------------------------|
| id         | int (PK)  | 自增主键                       |
| name       | str       | 显示名称，如 "阿里百炼"          |
| base_url   | str       | 供应商 API 基础 URL             |
| api_key    | str       | 供应商 API Key                  |
| is_active  | bool      | 是否启用                       |
| created_at | datetime  | 创建时间                       |
| updated_at | datetime  | 更新时间                       |

#### ModelMapping（模型映射）

| 字段        | 类型       | 说明                                         |
|-------------|-----------|---------------------------------------------|
| id          | int (PK)  | 自增主键                                      |
| model_id    | str       | 模型标识符，如 "claude-sonnet-4-20250514"       |
| provider_id | int (FK)  | 关联的供应商                                   |
| is_active   | bool      | 是否启用                                      |
| created_at  | datetime  | 创建时间                                      |
| updated_at  | datetime  | 更新时间                                      |

**约束**: `model_id` 唯一，一个模型只能映射到一个供应商。

#### RequestLog（请求日志）

| 字段           | 类型       | 说明                                       |
|----------------|-----------|-------------------------------------------|
| id             | int (PK)  | 自增主键                                    |
| request_id     | str       | UUID，用于追踪                              |
| model_id       | str       | 请求的模型名称                              |
| provider_id    | int (FK)  | 实际路由到的供应商                           |
| request_body   | JSON      | 完整请求体                                  |
| response_body  | JSON      | 完整响应（流式调用为聚合后的标准格式）         |
| status_code    | int       | HTTP 状态码                                 |
| is_stream      | bool      | 是否为流式调用                               |
| duration_ms    | int       | 请求耗时（毫秒）                             |
| error_message  | str/null  | 错误信息（如有）                              |
| created_at     | datetime  | 创建时间                                    |
| client_ip      | str       | 客户端 IP 地址                               |

## 代理逻辑

### 请求流程

```
Claude Code
    │  POST /anthropic/v1/messages
    │  Header: x-api-key = <RELAY_API_KEY>
    │
    ▼
FastAPI 代理
    │
    ├── 1. 验证访问 Token（x-api-key 请求头）
    ├── 2. 解析请求体，提取 model 字段
    ├── 3. 查询 ModelMapping 表 → 找到对应的供应商
    ├── 4. 替换 base_url 和 API Key 为供应商的配置
    ├── 5. 转发请求到目标供应商
    │
    ├── [非流式] → 直接返回响应，同时存储完整 req/res 到日志
    │
    └── [流式 SSE] → 逐块转发给客户端
                      后台聚合所有 SSE 事件
                      流结束后组装成标准 Anthropic Message JSON 格式存储
```

### SSE 流式聚合

Anthropic SSE 事件类型：

1. `message_start` — 消息骨架（id, model, role, 初始 usage）
2. `content_block_start` — 内容块开始（text 或 tool_use）
3. `content_block_delta` — 增量内容：
   - `text_delta`: 追加文本内容
   - `input_json_delta`: 追加 tool_use 的 input JSON
4. `content_block_stop` — 内容块结束
5. `message_delta` — 消息级更新（stop_reason, 最终 usage）
6. `message_stop` — 消息结束

**聚合算法**：

```python
def aggregate_sse_events(events):
    message = {}  # 来自 message_start
    content_blocks = []

    for event in events:
        if event.type == "message_start":
            message = event.message  # 消息骨架

        elif event.type == "content_block_start":
            content_blocks.append(event.content_block)

        elif event.type == "content_block_delta":
            block = content_blocks[event.index]
            if event.delta.type == "text_delta":
                block["text"] += event.delta.text
            elif event.delta.type == "input_json_delta":
                block["input"] += event.delta.partial_json

        elif event.type == "content_block_stop":
            # 解析 tool_use 的 input JSON
            block = content_blocks[event.index]
            if block["type"] == "tool_use" and isinstance(block.get("input"), str):
                block["input"] = json.loads(block["input"])

        elif event.type == "message_delta":
            message["stop_reason"] = event.delta.stop_reason
            message["usage"] = {**message.get("usage", {}), **event.usage}

        elif event.type == "message_stop":
            message["content"] = content_blocks
            message["type"] = "message"

    return message  # 标准 Anthropic Message JSON
```

### 错误处理

| 场景             | 客户端响应                     | 日志行为           |
|-----------------|-------------------------------|-------------------|
| Token 无效       | 401 Unauthorized              | 记录错误           |
| 模型未映射       | 400 Bad Request + 提示信息      | 记录错误           |
| 供应商返回错误    | 透传供应商的响应                | 记录错误响应       |
| 网络超时         | 504 Gateway Timeout            | 记录错误           |
| 供应商不可达      | 502 Bad Gateway               | 记录错误           |

## API 设计

### 代理 API

```
POST /anthropic/v1/messages
Header: x-api-key = <RELAY_API_KEY>
Body: 标准 Anthropic Messages API 请求体
Response: 标准 Anthropic Messages API 响应（或 SSE 流）
```

### 管理 API

#### 供应商管理

```
GET    /api/providers           # 获取供应商列表
POST   /api/providers           # 创建供应商
PUT    /api/providers/{id}      # 更新供应商
DELETE /api/providers/{id}      # 删除供应商
```

#### 模型映射

```
GET    /api/model-mappings          # 获取映射列表（含供应商信息）
POST   /api/model-mappings          # 创建映射
PUT    /api/model-mappings/{id}     # 更新映射
DELETE /api/model-mappings/{id}     # 删除映射
```

#### 请求日志

```
GET    /api/logs                  # 获取日志列表（分页，?page=1&size=20）
GET    /api/logs/{id}             # 获取单条日志详情
DELETE /api/logs                  # 清空日志
```

### 认证

- **代理 API**（`/anthropic/*`）：需要 `x-api-key` 请求头，值匹配 `.env` 中的 `RELAY_API_KEY`
- **管理 API**（`/api/*`）：无需认证（仅本地访问）
- **静态文件**（`/`）：无需认证（提供前端页面）

## 前端

基于 Vue 3 的单页应用，由 FastAPI 在 `/` 路径提供。

### 标签页

1. **供应商管理**
   - 表格展示所有供应商：名称、base_url、启用状态
   - 新增/编辑/删除供应商（弹窗表单）
   - API Key 输入框支持显示/隐藏切换

2. **模型映射**
   - 表格展示 model_id → 供应商的映射关系
   - 新增/编辑/删除映射
   - 供应商选择下拉框（从供应商列表加载）
   - 启用/禁用切换

3. **请求日志**
   - 分页表格：时间、模型、供应商、状态码、耗时、流式/非流式
   - 点击行展开查看完整请求体和响应体
   - JSON 语法高亮显示

## 配置

### 环境变量（.env）

```
RELAY_PORT=5020
RELAY_API_KEY=your-secret-key-here
DATABASE_URL=sqlite:///./data/token_relay.db
```

### 默认端口

5020

## Claude Code 配置

在 Claude Code 中设置 base URL 为：

```
ANTHROPIC_BASE_URL=http://localhost:5020/anthropic
ANTHROPIC_API_KEY=your-secret-key-here
```

Claude Code 会自动在 base URL 后拼接 `/v1/messages`。

## 依赖

```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
sqlalchemy>=2.0.0
httpx>=0.24.0
python-dotenv>=1.0.0
aiosqlite>=0.19.0
sse-starlette>=1.6.0
```

## 验证方式

### 测试 Token

使用阿里百炼的 Token 进行验证：

```
sk-sp-D.IPXMD.BIM0.MEUCIQDabakdrvPKNXOZI2qqtuA1vmLjBoUuBQ1U2elIQt117wIgEl7PRGYRJ7r8Q8U3jbIvTSBydWXaeN+6bHnJDxN++dg=
```

### 验证步骤

1. 启动服务：`python main.py`
2. 在前端页面配置阿里百炼供应商，填入上述 Token
3. 添加模型映射，如 `qwen-max` → 阿里百炼
4. 发送非流式测试请求：

```bash
curl -X POST http://localhost:5020/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-relay-key" \
  -d '{
    "model": "qwen-max",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

5. 发送流式测试请求：

```bash
curl -X POST http://localhost:5020/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-relay-key" \
  -d '{
    "model": "qwen-max",
    "max_tokens": 100,
    "stream": true,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

6. 在前端日志页面查看请求记录，确认：
   - 非流式请求的完整入参出参已存储
   - 流式请求的 SSE 已聚合为标准 JSON 格式存储
