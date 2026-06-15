# Token Relay - API Proxy Gateway Design

## Overview

A lightweight API proxy gateway that unifies three Anthropic-compatible API providers behind a single endpoint. Routes requests by model ID, provides a web-based configuration UI, and logs all requests with full SSE stream aggregation.

## Goals

- Unified entry point for multiple Anthropic-compatible providers
- Switch models/providers without modifying Claude Code configuration
- Full request logging with streaming response aggregation
- Simple web UI for configuration and log viewing

## Providers

| Name     | Base URL                                                         |
|----------|------------------------------------------------------------------|
| 阿里百炼 | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` |
| 智谱     | `https://open.bigmodel.cn/api/anthropic`                         |
| DeepSeek | `https://api.deepseek.com/anthropic`                             |

## Architecture

### Tech Stack

- **Backend**: Python 3.11+ / FastAPI
- **Frontend**: Single HTML file with Vue 3 (CDN)
- **Database**: SQLite via SQLAlchemy
- **HTTP Client**: httpx (async, SSE support)

### Project Structure

```
token-relay/
├── main.py                    # Entry point, starts uvicorn
├── requirements.txt           # Dependencies
├── .env.example               # Environment variable template
├── app/
│   ├── __init__.py
│   ├── config.py              # Global config (port, token, etc.)
│   ├── database.py            # SQLite + SQLAlchemy setup
│   ├── models.py              # SQLAlchemy data models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── proxy.py           # Anthropic API proxy route
│   │   ├── providers.py       # Provider CRUD
│   │   └── logs.py            # Log querying
│   ├── services/
│   │   ├── __init__.py
│   │   ├── proxy_service.py   # Core proxy logic (routing + SSE aggregation)
│   │   └── provider_service.py# Provider management
│   └── static/
│       └── index.html         # Frontend SPA (Vue 3)
└── data/                      # SQLite database directory (gitignored)
```

### Data Models

#### Provider

| Field      | Type      | Description                            |
|------------|-----------|----------------------------------------|
| id         | int (PK)  | Auto-increment primary key             |
| name       | str       | Display name, e.g. "阿里百炼"           |
| base_url   | str       | Provider API base URL                  |
| api_key    | str       | Provider API key                       |
| is_active  | bool      | Whether this provider is enabled       |
| created_at | datetime  |                                        |
| updated_at | datetime  |                                        |

#### ModelMapping

| Field      | Type      | Description                            |
|------------|-----------|----------------------------------------|
| id         | int (PK)  | Auto-increment primary key             |
| model_id   | str       | Model identifier, e.g. "claude-sonnet-4-20250514" |
| provider_id| int (FK)  | Reference to Provider                  |
| is_active  | bool      | Whether this mapping is enabled        |
| created_at | datetime  |                                        |
| updated_at | datetime  |                                        |

**Constraint**: `model_id` must be unique (one model maps to one provider).

#### RequestLog

| Field          | Type      | Description                            |
|----------------|-----------|----------------------------------------|
| id             | int (PK)  | Auto-increment primary key             |
| request_id     | str       | UUID for tracing                       |
| model_id       | str       | Requested model name                   |
| provider_id    | int (FK)  | Actual provider routed to              |
| request_body   | JSON      | Full request body                      |
| response_body  | JSON      | Full response (aggregated for streams)  |
| status_code    | int       | HTTP status code                       |
| is_stream      | bool      | Whether this was a streaming request   |
| duration_ms    | int       | Request duration in milliseconds       |
| error_message  | str/null  | Error details if request failed        |
| created_at     | datetime  |                                        |
| client_ip      | str       | Client IP address                      |

## Proxy Logic

### Request Flow

```
Claude Code
    │  POST /anthropic/v1/messages
    │  Header: x-api-key = <RELAY_API_KEY>
    │
    ▼
FastAPI Proxy
    │
    ├── 1. Validate access token (x-api-key header)
    ├── 2. Parse request body, extract `model` field
    ├── 3. Query ModelMapping table → find Provider
    ├── 4. Replace base_url with Provider's base_url, use Provider's API key
    ├── 5. Forward request to target provider
    │
    ├── [Non-streaming] → Return response directly, save full req/res to log
    │
    └── [Streaming SSE] → Stream chunks back to client
                          Aggregate SSE events in background
                          After stream ends, assemble into standard Anthropic Message JSON
                          Save aggregated result to log
```

### SSE Stream Aggregation

Anthropic SSE event types:

1. `message_start` — Message skeleton (id, model, role, usage initial)
2. `content_block_start` — Initialize content block (text or tool_use)
3. `content_block_delta` — Incremental content:
   - `text_delta`: append to text content
   - `input_json_delta`: append to tool_use input JSON
4. `content_block_stop` — Content block complete
5. `message_delta` — Message-level updates (stop_reason, final usage)
6. `message_stop` — Message complete

**Aggregation algorithm**:

```python
def aggregate_sse_events(events):
    message = {}  # from message_start
    content_blocks = []

    for event in events:
        if event.type == "message_start":
            message = event.message  # skeleton

        elif event.type == "content_block_start":
            content_blocks.append(event.content_block)

        elif event.type == "content_block_delta":
            block = content_blocks[event.index]
            if event.delta.type == "text_delta":
                block["text"] += event.delta.text
            elif event.delta.type == "input_json_delta":
                block["input"] += event.delta.partial_json

        elif event.type == "content_block_stop":
            # Parse accumulated input JSON for tool_use blocks
            block = content_blocks[event.index]
            if block["type"] == "tool_use" and isinstance(block.get("input"), str):
                block["input"] = json.loads(block["input"])

        elif event.type == "message_delta":
            message["stop_reason"] = event.delta.stop_reason
            message["usage"] = {**message.get("usage", {}), **event.usage}

        elif event.type == "message_stop":
            message["content"] = content_blocks
            message["type"] = "message"

    return message  # Standard Anthropic Message JSON
```

### Error Handling

| Scenario              | Response to Client              | Log Behavior        |
|-----------------------|---------------------------------|---------------------|
| Invalid token         | 401 Unauthorized                | Log with error      |
| Model not mapped      | 400 Bad Request + hint message  | Log with error      |
| Provider returns error| Pass through provider's response| Log error response  |
| Network timeout       | 504 Gateway Timeout             | Log with error      |
| Provider unreachable  | 502 Bad Gateway                 | Log with error      |

## API Design

### Proxy API

```
POST /anthropic/v1/messages
Header: x-api-key = <RELAY_API_KEY>
Body: Standard Anthropic Messages API request body
Response: Standard Anthropic Messages API response (or SSE stream)
```

### Management APIs

#### Providers

```
GET    /api/providers           # List all providers
POST   /api/providers           # Create provider
PUT    /api/providers/{id}      # Update provider
DELETE /api/providers/{id}      # Delete provider
```

#### Model Mappings

```
GET    /api/model-mappings          # List all mappings (with provider info joined)
POST   /api/model-mappings          # Create mapping
PUT    /api/model-mappings/{id}     # Update mapping
DELETE /api/model-mappings/{id}     # Delete mapping
```

#### Request Logs

```
GET    /api/logs                  # List logs (paginated, ?page=1&size=20)
GET    /api/logs/{id}             # Get single log detail
DELETE /api/logs                  # Clear all logs
```

### Authentication

- **Proxy API** (`/anthropic/*`): Requires `x-api-key` header with value matching `RELAY_API_KEY` from `.env`
- **Management API** (`/api/*`): No authentication (local-only access)
- **Static files** (`/`): No authentication (serves the frontend)

## Frontend

Single-page Vue 3 application served at `/` by FastAPI.

### Tabs

1. **供应商管理 (Providers)**
   - Table listing all providers with name, base_url, active status
   - Add/Edit/Delete provider (modal form)
   - API Key field with show/hide toggle

2. **模型映射 (Model Mappings)**
   - Table listing model_id → provider mappings
   - Add/Edit/Delete mapping
   - Provider selection via dropdown (populated from providers list)
   - Toggle active/inactive

3. **请求日志 (Request Logs)**
   - Paginated table: time, model, provider, status, duration, stream/non-stream
   - Click row to expand and view full request body and response body
   - JSON viewer with syntax highlighting (use a lightweight library or simple `<pre>`)

## Configuration

### Environment Variables (.env)

```
RELAY_PORT=5020
RELAY_API_KEY=your-secret-key-here
DATABASE_URL=sqlite:///./data/token_relay.db
```

### Default Port

5020

## Claude Code Configuration

To use the proxy with Claude Code, set the base URL to:

```
ANTHROPIC_BASE_URL=http://localhost:5020/anthropic
ANTHROPIC_API_KEY=your-secret-key-here
```

Claude Code will automatically append `/v1/messages` to the base URL.

## Dependencies

```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
sqlalchemy>=2.0.0
httpx>=0.24.0
python-dotenv>=1.0.0
aiosqlite>=0.19.0
sse-starlette>=1.6.0
```
