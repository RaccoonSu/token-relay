"""IP filtering middleware for management API access control.

用纯 ASGI 实现而非继承 BaseHTTPMiddleware：后者用 anyio cancel scope 包下游，
StreamingResponse 流式结束时可能取消正在进行的清理（如 aiosqlite 连接 close），
在 NullPool 下大量并发会冒出 CancelledError 噪音甚至连接泄漏。
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Paths that are restricted to localhost only
_LOCALHOST_ONLY_PREFIXES = ("/api", "/static")
_LOCALHOST_ONLY_EXACT = ("/",)

# Allowed localhost addresses
_ALLOWED_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

# 在途 HTTP 请求计数（单事件循环内自增自减，无需锁）。供后台清理任务判断
# 当前是否有访问：有访问时跳过清理，避免与代理请求抢 SQLite 写锁。
_active_requests: int = 0


def get_active_request_count() -> int:
    """返回当前在途的 HTTP 请求数。"""
    return _active_requests


class LocalhostOnlyMiddleware:
    """Restricts management API and UI to localhost access only.

    Paths under /api/*, /static/*, and / are only accessible from
    127.0.0.1 / ::1. The proxy API (/anthropic/*) remains open to
    other network segments (protected by its own API key auth).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        global _active_requests
        _active_requests += 1
        try:
            path = scope.get("path", "")
            if self._is_localhost_only_path(path):
                client_ip = self._get_client_ip(scope)
                if client_ip not in _ALLOWED_IPS:
                    response = JSONResponse(
                        status_code=403,
                        content={"detail": "Forbidden: management API is localhost only"},
                    )
                    await response(scope, receive, send)
                    return

            await self.app(scope, receive, send)
        finally:
            _active_requests -= 1

    @staticmethod
    def _is_localhost_only_path(path: str) -> bool:
        if path in _LOCALHOST_ONLY_EXACT:
            return True
        return any(path.startswith(prefix) for prefix in _LOCALHOST_ONLY_PREFIXES)

    @staticmethod
    def _get_client_ip(scope: Scope) -> str:
        # Check X-Forwarded-For header first
        headers = scope.get("headers", ())
        for name, value in headers:
            if name == b"x-forwarded-for":
                return value.decode("latin-1").split(",")[0].strip()
        # Direct connection
        client = scope.get("client")
        return client[0] if client else ""
