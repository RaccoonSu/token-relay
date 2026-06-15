"""IP filtering middleware for management API access control."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths that are restricted to localhost only
_LOCALHOST_ONLY_PREFIXES = ("/api", "/static")
_LOCALHOST_ONLY_EXACT = ("/",)

# Allowed localhost addresses
_ALLOWED_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    """Restricts management API and UI to localhost access only.

    Paths under /api/*, /static/*, and / are only accessible from
    127.0.0.1 / ::1. The proxy API (/anthropic/*) remains open to
    other network segments (protected by its own API key auth).
    """

    async def dispatch(self, request: Request, call_next):
        if self._is_localhost_only_path(request.url.path):
            client_ip = self._get_client_ip(request)
            if client_ip not in _ALLOWED_IPS:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: management API is localhost only"},
                )
        return await call_next(request)

    @staticmethod
    def _is_localhost_only_path(path: str) -> bool:
        if path in _LOCALHOST_ONLY_EXACT:
            return True
        return any(path.startswith(prefix) for prefix in _LOCALHOST_ONLY_PREFIXES)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        # Check X-Forwarded-For for proxied requests
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        # Direct connection
        return request.client.host if request.client else ""
