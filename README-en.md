English | **[中文](README.md)**

# Token Relay

A lightweight Anthropic API proxy gateway that unifies multiple AI provider endpoints behind a single API. Routes requests by model ID, provides a web UI for configuration, and logs all requests with SSE stream aggregation.

## Why This Project

When using Claude Code with self-hosted API providers (Alibaba Bailian, Zhipu, DeepSeek, etc.), there is a frustrating pain point:

> **Switching providers requires restarting Claude Code CLI.** Every time you use `cc-switch` to change the provider configuration, you must exit and restart Claude Code for the change to take effect. This becomes extremely tedious when you frequently compare or switch between models from different providers.

Token Relay solves this with a simple idea: **aggregate all providers behind a single API endpoint, and route by model ID**. You configure `ANTHROPIC_BASE_URL` once to point at the relay, then switch models directly in your conversation — no config changes, no restarts.

```
Claude Code ──▶ Token Relay ──┬──▶ Alibaba Bailian (qwen3.7-max)
   fixed URL                  ├──▶ Zhipu (glm-5)
                              └──▶ DeepSeek (deepseek-v4-flash)
```

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
| `/api/default-target` | GET/PUT | Get / set the real model that `token-relay-default` forwards to |
| `/api/logs` | GET/DELETE | List logs / clear all logs |
| `/api/logs/{id}` | GET | View log detail (full req/res) |

## Claude Code Model Slot Configuration

Claude Code switches models via the `/model` command, but only exposes **5 fixed slots**. Each slot can be mapped to any real model via an environment variable. Full example (`~/.claude/settings.json`):

```jsonc
{
  "env": {
    // Relay connection
    "ANTHROPIC_AUTH_TOKEN": "your-relay-api-key",
    "ANTHROPIC_BASE_URL": "http://localhost:5020/anthropic",

    // 5 slot mappings
    "ANTHROPIC_MODEL": "glm-5.2[1M]",                    // Default slot
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "qwen3.7-max[1M]",   // opus slot
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1M]", // sonnet slot
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",    // haiku slot
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "token-relay-default",  // 5th slot (dynamic)
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": "Token Relay Default",

    // Misc
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
```

| Slot | Env Var | Notes |
|------|---------|-------|
| Default | `ANTHROPIC_MODEL` | Startup default model |
| opus | `ANTHROPIC_DEFAULT_OPUS_MODEL` | Triggered by `/model opus` |
| sonnet | `ANTHROPIC_DEFAULT_SONNET_MODEL` | Triggered by `/model sonnet` |
| haiku | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Triggered by `/model haiku` |
| Custom | `ANTHROPIC_CUSTOM_MODEL_OPTION` | One extra custom slot only |

- **`[1M]` suffix**: Requests a 1M-token context window. The relay strips the suffix and forwards the base model name upstream, so you don't need a separate mapping with the suffix.
- **Slot limit**: Claude Code does not support adding unlimited slots. If you have more than 5 models, use the `token-relay-default` mechanism below.

## token-relay-default: Switch Any Model via Web UI

### The Problem

Claude Code only offers 5 fixed slots. Changing a slot mapping requires a restart. If you have 6+ models to switch between (e.g., two models each from Alibaba, Zhipu, and DeepSeek), 5 slots are not enough.

### The Solution

Pin the 5th slot (`ANTHROPIC_CUSTOM_MODEL_OPTION`) to a virtual ID `token-relay-default`. **The relay decides which real model this alias forwards to.** Switching happens in the relay's Web UI and takes effect instantly — **no Claude Code restart required**.

```
Claude Code  ──▶ model: "token-relay-default"
                     │
                     ▼
            Token Relay looks up target (e.g. glm-5.1)
                     │
                     ▼
            Rewrites model to "glm-5.1", forwards to Zhipu
```

### Setup

1. **Configure the 5th slot in `~/.claude/settings.json`**:

    ```json
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "token-relay-default",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": "Token Relay Default"
    ```

2. **Restart Claude Code**. A **Token Relay Default** slot will appear in the `/model` picker. Select it.

3. **Open the relay Web UI** (http://localhost:5020) → **Model Mappings** tab → the blue **Default Model** panel at the top:
   - Pick the real model you want to forward to from the dropdown
   - Click **Apply**
   - Takes effect immediately, no Claude Code restart needed

4. From then on, keep using the Token Relay Default slot in Claude Code. Whenever you want to switch models, just change the target in the Web UI.

### How it Works Internally

- When the relay receives a request with `model: "token-relay-default"`, it reads the `default_target_model_id` setting from the `app_settings` table
- Rewrites the `model` field in the request body to the real target model ID
- Resolves the provider via the normal mapping lookup and forwards the request
- Returns a 400 error if no default target is configured
- The Web UI exposes `GET/PUT /api/default-target` to read and write the target

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
