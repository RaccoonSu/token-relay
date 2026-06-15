import json
import uuid
import time
import copy
from typing import AsyncGenerator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Provider, ModelMapping, RequestLog


async def resolve_provider(db: AsyncSession, model_id: str) -> tuple[Provider, ModelMapping] | None:
    """Find the provider for a given model_id."""
    result = await db.execute(
        select(ModelMapping)
        .options(selectinload(ModelMapping.provider))
        .where(ModelMapping.model_id == model_id, ModelMapping.is_active == True)
    )
    mapping = result.scalar_one_or_none()
    if mapping and mapping.provider and mapping.provider.is_active:
        return mapping.provider, mapping
    return None


def aggregate_sse_event(message: dict, content_blocks: list, event_type: str, data: dict):
    """Aggregate a single SSE event into the message structure."""
    if event_type == "message_start":
        message.update(data.get("message", {}))

    elif event_type == "content_block_start":
        index = data.get("index", len(content_blocks))
        block = data.get("content_block", {})
        # Initialize text or input fields
        if block.get("type") == "text":
            block.setdefault("text", "")
        elif block.get("type") == "tool_use":
            block.setdefault("input", "")
        # Insert at the right index
        while len(content_blocks) <= index:
            content_blocks.append({})
        content_blocks[index] = block

    elif event_type == "content_block_delta":
        index = data.get("index", 0)
        delta = data.get("delta", {})
        if index < len(content_blocks):
            block = content_blocks[index]
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif delta_type == "input_json_delta":
                block["input"] = block.get("input", "") + delta.get("partial_json", "")

    elif event_type == "content_block_stop":
        index = data.get("index", 0)
        if index < len(content_blocks):
            block = content_blocks[index]
            if block.get("type") == "tool_use" and isinstance(block.get("input"), str):
                try:
                    block["input"] = json.loads(block["input"]) if block["input"] else {}
                except json.JSONDecodeError:
                    block["input"] = {}

    elif event_type == "message_delta":
        delta = data.get("delta", {})
        usage = data.get("usage", {})
        if "stop_reason" in delta:
            message["stop_reason"] = delta["stop_reason"]
        if "stop_sequence" in delta:
            message["stop_sequence"] = delta["stop_sequence"]
        if usage:
            existing_usage = message.get("usage", {})
            message["usage"] = {**existing_usage, **usage}

    elif event_type == "message_stop":
        message["content"] = content_blocks
        message["type"] = "message"


def build_aggregated_response(message: dict, content_blocks: list) -> dict:
    """Build the final aggregated response from collected events."""
    result = copy.deepcopy(message)
    result["content"] = copy.deepcopy(content_blocks)
    result["type"] = "message"
    return result


async def proxy_non_stream(
    db: AsyncSession,
    provider: Provider,
    request_body: dict,
    client_ip: str,
) -> tuple[dict, int]:
    """Forward a non-streaming request and log it."""
    request_id = str(uuid.uuid4())
    start_time = time.time()

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
        request_body=request_body,
        is_stream=False,
        client_ip=client_ip,
    )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                target_url,
                json=request_body,
                headers=headers,
            )

        duration_ms = int((time.time() - start_time) * 1000)
        status_code = response.status_code

        try:
            response_body = response.json()
        except Exception:
            response_body = {"raw": response.text}

        log_entry.response_body = response_body
        log_entry.status_code = status_code
        log_entry.duration_ms = duration_ms

        if status_code >= 400:
            log_entry.error_message = json.dumps(response_body, ensure_ascii=False)

    except httpx.TimeoutException:
        duration_ms = int((time.time() - start_time) * 1000)
        log_entry.status_code = 504
        log_entry.duration_ms = duration_ms
        log_entry.error_message = "Gateway Timeout"
        response_body = {"error": {"type": "timeout_error", "message": "Gateway Timeout"}}
        status_code = 504

    except httpx.ConnectError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_entry.status_code = 502
        log_entry.duration_ms = duration_ms
        log_entry.error_message = f"Bad Gateway: {str(e)}"
        response_body = {"error": {"type": "connection_error", "message": "Bad Gateway"}}
        status_code = 502

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_entry.status_code = 500
        log_entry.duration_ms = duration_ms
        log_entry.error_message = str(e)
        response_body = {"error": {"type": "internal_error", "message": str(e)}}
        status_code = 500

    db.add(log_entry)
    await db.commit()

    return response_body, status_code


async def proxy_stream(
    db: AsyncSession,
    provider: Provider,
    request_body: dict,
    client_ip: str,
) -> AsyncGenerator[str, None]:
    """Forward a streaming request, yield SSE chunks, and log the aggregated response."""
    request_id = str(uuid.uuid4())
    start_time = time.time()

    target_url = f"{provider.base_url.rstrip('/')}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
    }

    message = {}
    content_blocks = []
    status_code = 200
    error_message = None
    all_raw_lines = []
    current_event_type = ""

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                target_url,
                json=request_body,
                headers=headers,
            ) as response:
                status_code = response.status_code

                if status_code >= 400:
                    # Non-streaming error response
                    await response.aread()
                    try:
                        error_body = response.json()
                    except Exception:
                        error_body = {"raw": response.text}
                    error_message = json.dumps(error_body, ensure_ascii=False)

                    duration_ms = int((time.time() - start_time) * 1000)
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

                    # Yield the error as SSE
                    yield f"data: {json.dumps(error_body, ensure_ascii=False)}\n\n"
                    return

                async for line in response.aiter_lines():
                    if not line.strip():
                        yield "\n"
                        continue

                    all_raw_lines.append(line)
                    yield f"{line}\n"

                    # Parse SSE events for aggregation
                    if line.startswith("event:"):
                        current_event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            aggregate_sse_event(message, content_blocks, current_event_type, data)
                        except json.JSONDecodeError:
                            pass

    except httpx.TimeoutException:
        status_code = 504
        error_message = "Gateway Timeout"
    except httpx.ConnectError as e:
        status_code = 502
        error_message = f"Bad Gateway: {str(e)}"
    except Exception as e:
        status_code = 500
        error_message = str(e)

    # Build aggregated response and save log
    duration_ms = int((time.time() - start_time) * 1000)
    aggregated_response = build_aggregated_response(message, content_blocks)

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
