**[English](README-en.md)** | 中文

# Token Relay

一个轻量级的 Anthropic API 中转站，将多个 AI 供应商的 Anthropic 兼容接口统一代理，通过模型 ID 自动路由到对应供应商。支持 Web 界面配置、请求日志记录和 SSE 流式响应聚合。

## 为什么需要这个项目

在使用 Claude Code 接入自有 API（如阿里百炼、智谱、DeepSeek 等国产模型）时，存在一个明显的痛点：

> **切换供应商需要重启 Claude Code CLI。** 每次在 `cc-switch` 中切换不同的供应商配置后，必须退出并重新启动 Claude Code 才能生效。当你需要在多个供应商的模型之间频繁对比或切换时，这个流程非常繁琐。

Token Relay 的思路很简单：**把所有供应商聚合到同一个 API 地址，通过模型 ID 自动路由**。你只需要在 Claude Code 中配置一次 `ANTHROPIC_BASE_URL` 指向中转站，之后直接在对话中切换模型即可，无需改配置、无需重启。

```
Claude Code ──▶ Token Relay ──┬──▶ 阿里百炼 (qwen3.7-max)
   固定地址                    ├──▶ 智谱 (glm-5)
                              └──▶ DeepSeek (deepseek-v4-flash)
```

---

## 功能特性

- **多供应商统一代理** — 将阿里百炼、智谱、DeepSeek 等 Anthropic 兼容接口聚合到一个入口
- **模型 ID 路由** — 根据请求中的 `model` 字段自动分发到对应供应商，切换模型无需改配置
- **SSE 流式聚合** — 流式请求实时转发给客户端，同时在后台将 SSE 事件聚合为标准 Anthropic Message JSON 存储
- **请求日志** — 记录每次请求的完整入参和出参，支持在前端页面查看
- **Web 管理界面** — 供应商管理、模型映射配置、请求日志查看，全部通过浏览器操作
- **Token 鉴权** — 代理接口支持自定义 API Key 验证，可部署到服务器
- **IP 访问控制** — 管理界面和 API 仅限 localhost 访问，代理接口开放给所有网段

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | Python 3.11+ / FastAPI |
| 数据库 | SQLite + SQLAlchemy (async) |
| HTTP 客户端 | httpx (async, SSE streaming) |
| 前端 | 单文件 Vue 3 SPA (CDN) |
| 服务器 | Uvicorn |

## 项目结构

```
token-relay/
├── main.py                      # 入口文件
├── requirements.txt             # Python 依赖
├── .env                         # 环境变量配置
├── app/
│   ├── config.py                # 全局配置
│   ├── database.py              # 数据库连接
│   ├── models.py                # 数据模型 (Provider, ModelMapping, RequestLog)
│   ├── middleware.py             # IP 访问控制中间件
│   ├── routers/
│   │   ├── proxy.py             # 代理路由 /anthropic/v1/messages
│   │   ├── providers.py         # 供应商 & 模型映射 CRUD API
│   │   └── logs.py              # 请求日志查询 API
│   ├── services/
│   │   ├── proxy_service.py     # 代理核心：路由分发 + SSE 聚合
│   │   └── provider_service.py  # 供应商管理逻辑
│   └── static/
│       └── index.html           # 前端单页应用
└── data/
    └── token_relay.db           # SQLite 数据库文件（自动生成）
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并修改：

```bash
cp .env.example .env
```

`.env` 内容：

```ini
RELAY_PORT=5020                          # 服务端口
RELAY_API_KEY=your-secret-key-here       # 调用代理时的鉴权 Key
DATABASE_URL=sqlite+aiosqlite:///./data/token_relay.db
```

### 3. 启动服务

```bash
python main.py
```

服务启动后访问 http://localhost:5020 打开管理界面。

### 4. 配置供应商和模型

在 Web 管理界面操作：

1. **供应商管理** — 添加供应商，填写名称、Base URL 和 API Key
2. **模型映射** — 添加模型 ID 到供应商的映射关系

支持的供应商 Base URL 示例：

| 供应商 | Base URL |
|--------|----------|
| 阿里百炼 | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` |
| 智谱 | `https://open.bigmodel.cn/api/anthropic` |
| DeepSeek | `https://api.deepseek.com/anthropic` |

## 使用方式

### 在 Claude Code 中使用

设置环境变量让 Claude Code 通过中转站调用：

```bash
export ANTHROPIC_BASE_URL=http://localhost:5020/anthropic
export ANTHROPIC_API_KEY=your-secret-key-here   # 对应 .env 中的 RELAY_API_KEY
```

之后在 Claude Code 中切换模型（如 `qwen3.7-max`、`deepseek-v4-flash`），中转站会自动路由到对应供应商。

### API 直接调用

代理接口完全兼容 Anthropic Messages API 格式：

```bash
curl -X POST http://localhost:5020/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-secret-key-here" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "qwen3.7-max",
    "max_tokens": 4096,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

流式调用（SSE）：

```bash
curl -X POST http://localhost:5020/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-secret-key-here" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "deepseek-v4-flash",
    "max_tokens": 4096,
    "stream": true,
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 管理 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/providers` | GET/POST | 供应商列表 / 创建 |
| `/api/providers/{id}` | PUT/DELETE | 更新 / 删除供应商 |
| `/api/model-mappings` | GET/POST | 模型映射列表 / 创建 |
| `/api/model-mappings/{id}` | PUT/DELETE | 更新 / 删除映射 |
| `/api/default-target` | GET/PUT | 获取 / 设置 `token-relay-default` 转发的真实模型 |
| `/api/logs` | GET/DELETE | 查看日志列表 / 清空日志 |
| `/api/logs/{id}` | GET | 查看日志详情（含完整 req/res） |

## Claude Code 模型槽位配置

Claude Code 通过 `/model` 命令切换模型，但只提供 **5 个固定槽位**。每个槽位可通过一个环境变量映射到任意真实模型。完整示例（`~/.claude/settings.json`）：

```jsonc
{
  "env": {
    // 中转站连接
    "ANTHROPIC_AUTH_TOKEN": "your-relay-api-key",
    "ANTHROPIC_BASE_URL": "http://localhost:5020/anthropic",

    // 5 个槽位配置
    "ANTHROPIC_MODEL": "glm-5.2[1M]",                    // Default 槽位
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "qwen3.7-max[1M]",   // opus 槽位
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1M]", // sonnet 槽位
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",    // haiku 槽位
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "token-relay-default",  // 第 5 槽位（动态）
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": "Token Relay Default",

    // 其他
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
```

| 槽位 | 环境变量 | 说明 |
|------|---------|------|
| Default | `ANTHROPIC_MODEL` | 启动默认模型 |
| opus | `ANTHROPIC_DEFAULT_OPUS_MODEL` | `/model opus` 触发 |
| sonnet | `ANTHROPIC_DEFAULT_SONNET_MODEL` | `/model sonnet` 触发 |
| haiku | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `/model haiku` 触发 |
| Custom | `ANTHROPIC_CUSTOM_MODEL_OPTION` | 只能加 1 个自定义槽位 |

- **`[1M]` 后缀**：表示启用 100 万 token 上下文窗口。中转站会自动剥掉该后缀再用基础模型名转发上游，无需在映射表里创建带后缀的条目。
- **槽位上限**：Claude Code 不支持无限添加槽位。如果模型数量超过 5 个，请使用下方的 `token-relay-default` 机制。

## token-relay-default：通过 Web UI 动态切换任意模型

### 痛点

Claude Code 只有 5 个固定槽位，修改槽位配置需要重启。如果你有 6 个以上的模型需要切换（如阿里百炼、智谱、DeepSeek 各 2 个），5 个槽位不够用。

### 解决思路

把第 5 个槽位（`ANTHROPIC_CUSTOM_MODEL_OPTION`）固定指向一个虚拟 ID `token-relay-default`，**由中转站决定它实际转发到哪个真实模型**。切换操作完全在中转站的 Web UI 完成，即时生效，**无需重启 Claude Code**。

```
Claude Code  ──▶ model: "token-relay-default"
                     │
                     ▼
            Token Relay 查询目标（例如 glm-5.1）
                     │
                     ▼
            重写 model 为 "glm-5.1"，转发给智谱
```

### 启用步骤

1. **在 `~/.claude/settings.json` 的 `env` 中配置第 5 槽位**：

    ```json
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "token-relay-default",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": "Token Relay Default"
    ```

2. **重启 Claude Code**，在 `/model` 选择器里会多出一个 **Token Relay Default** 槽位。选中它。

3. **打开中转站 Web 界面**（http://localhost:5020）→ **模型映射** tab → 顶部蓝色 **默认模型** 面板：
   - 从下拉框选择要转发的真实模型
   - 点击 **应用**
   - 立即生效，无需重启 Claude Code

4. 之后 Claude Code 继续使用 Token Relay Default 槽位，每次想换模型只需在网页上改一下目标即可。

### 内部机制

- 收到 `model: "token-relay-default"` 请求时，中转站读取数据库中 `app_settings` 表的 `default_target_model_id` 配置
- 把请求体里的 `model` 字段重写为目标真实模型 ID
- 用真实模型 ID 走正常的映射查找流程，找到供应商后转发
- 如果默认目标未配置，返回 400 错误提示
- 管理页面提供 `GET/PUT /api/default-target` 接口读写目标

## 访问控制

服务默认绑定 `0.0.0.0`（所有网卡），通过 IP 过滤中间件对不同路径实施差异化访问控制：

| 路径 | 访问限制 | 认证方式 |
|------|---------|---------|
| `/anthropic/*` (代理 API) | 不限 IP，允许所有网段访问 | 需要 `x-api-key` 或 `Authorization: Bearer` 请求头 |
| `/api/*` (管理 API) | 仅允许 `127.0.0.1` / `::1` 访问 | 无认证（依赖 IP 限制） |
| `/`、`/static/*` (管理界面) | 仅允许 `127.0.0.1` / `::1` 访问 | 无认证（依赖 IP 限制） |

非 localhost 来源访问管理路径时将返回 **403 Forbidden**。

> **提示**：如果通过反向代理（如 Nginx）部署，中间件会读取 `X-Forwarded-For` 头获取真实客户端 IP。请确保反向代理正确传递该头，否则管理 API 可能无法正常访问或意外开放。

## 请求日志

- **非流式请求**：存储完整的请求体和响应体 JSON
- **流式请求**：实时转发 SSE 事件给客户端，流结束后将所有事件聚合为标准 Anthropic Message JSON 格式存储，包括：
  - `thinking` blocks（思维链内容 + signature）
  - `text` blocks（文本内容）
  - `tool_use` blocks（工具调用，input 自动解析为 JSON 对象）
  - `stop_reason`、`usage` 等元信息

## 许可证

MIT
