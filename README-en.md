English | **[中文](README.md)**

# Token Relay

A lightweight Anthropic API proxy gateway that unifies multiple AI provider endpoints behind a single API. Routes requests by model ID, provides a web UI for configuration, and logs all requests with SSE stream aggregation.

---

## Features

- **Multi-Provider Proxy** — Unifies Anthropic-compatible endpoints from Alibaba Bailian, Zhipu, DeepSeek, and more behind a single entry point
- **Model ID Routing** — Automatically routes requests to the correct provider based on the `model` field; switch models without changing configuration
- **SSE Stream Aggregation** — Forwards streaming responses to clients in real-time while aggregating SSE events into standard Anthropic Message JSON for storage
- **Request Logging** — Records full request/response payloads for every request, viewable in the web UI
- **Web Management UI** — Configure providers, model mappings, and view request logs entirely through the browser
- **Token Authentication** — Proxy endpoint supports custom API key verification for server deployment
- **IP Access Control** — Management UI and API restricted to localhost; proxy endpoint open to all networks

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+ / FastAPI |
| Database | SQLite + SQLAlchemy (async) |
| HTTP Client | httpx (async, SSE streaming) |
| Frontend | Single-file Vue 3 SPA (CDN) |
| Server | Uvicorn |

## Project Structure

```
token-relay/
├── main.py                      # Entry point
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables
├── app/
│   ├── config.py                # Global configuration
│   ├── database.py              # Database connection
│   ├── models.py                # Data models (Provider, ModelMapping, RequestLog)
│   ├── middleware.py             # IP access control middleware
│   ├── routers/
│   │   ├── proxy.py             # Proxy route /anthropic/v1/messages
│   │   ├── providers.py         # Provider & model mapping CRUD API
│   │   └── logs.py              # Request log query API
│   ├── services/
│   │   ├── proxy_service.py     # Core proxy logic: routing + SSE aggregation
│   │   └── provider_service.py  # Provider management logic
│   └── static/
│       └── index.html           # Frontend SPA
└── data/
    └── token_relay.db           # SQLite database file (auto-generated)
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

`.env` contents:

```ini
RELAY_PORT=5020                          # Server port
RELAY_API_KEY=your-secret-key-here       # Auth key for proxy calls
DATABASE_URL=sqlite+aiosqlite:///./data/token_relay.db
```

### 3. Start the Server

```bash
python main.py
```

Visit http://localhost:5020 to open the management UI.

### 4. Configure Providers & Models

In the web management UI:

1. **Providers** — Add providers with name, base URL, and API key
2. **Model Mappings** — Map model IDs to providers

Example provider base URLs:

| Provider | Base URL |
|----------|----------|
| Alibaba Bailian | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic` |
| Zhipu | `https://open.bigmodel.cn/api/anthropic` |
| DeepSeek | `https://api.deepseek.com/anthropic` |

## Usage

### Use with Claude Code

Set environment variables to route Claude Code through the proxy:

```bash
export ANTHROPIC_BASE_URL=http://localhost:5020/anthropic
export ANTHROPIC_API_KEY=your-secret-key-here   # Must match RELAY_API_KEY in .env
```

Then switch models in Claude Code (e.g. `qwen3.7-max`, `deepseek-v4-flash`) — the proxy will automatically route to the correct provider.

### Direct API Calls

The proxy is fully compatible with the Anthropic Messages API format:

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

Streaming (SSE):

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

### Management APIs

| Endpoint | Methods | Description |
|----------|---------|-------------|
| `/api/providers` | GET/POST | List / create providers |
| `/api/providers/{id}` | PUT/DELETE | Update / delete a provider |
| `/api/model-mappings` | GET/POST | List / create model mappings |
| `/api/model-mappings/{id}` | PUT/DELETE | Update / delete a mapping |
| `/api/logs` | GET/DELETE | List logs / clear all logs |
| `/api/logs/{id}` | GET | View log detail (full req/res) |

## Access Control

The server binds to `0.0.0.0` (all interfaces) by default. An IP filtering middleware enforces different access rules per path:

| Path | Access Restriction | Authentication |
|------|-------------------|----------------|
| `/anthropic/*` (Proxy API) | Open to all networks | Requires `x-api-key` or `Authorization: Bearer` header |
| `/api/*` (Management API) | Localhost only (`127.0.0.1` / `::1`) | None (relies on IP restriction) |
| `/`, `/static/*` (Management UI) | Localhost only (`127.0.0.1` / `::1`) | None (relies on IP restriction) |

Non-localhost requests to management paths receive a **403 Forbidden** response.

> **Note**: When deploying behind a reverse proxy (e.g., Nginx), the middleware reads the `X-Forwarded-For` header to determine the real client IP. Ensure your reverse proxy is configured to pass this header correctly, otherwise the management API may become inaccessible or unintentionally exposed.

## Request Logging

- **Non-streaming**: Stores the complete request body and response body as JSON
- **Streaming**: Forwards SSE events to the client in real-time, then aggregates all events into a standard Anthropic Message JSON format for storage, including:
  - `thinking` blocks (chain-of-thought content + signature)
  - `text` blocks (text content)
  - `tool_use` blocks (tool calls with input automatically parsed as JSON objects)
  - `stop_reason`, `usage`, and other metadata

## License

MIT
