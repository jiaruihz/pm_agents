"""Optional HTTP Basic Auth for the dashboard, gated by an env var.

The dashboard exposes real trading data, so any public exposure must be
authenticated. This middleware is OFF unless `DASHBOARD_AUTH` is set, so local
dev and tests are unaffected. Set it on the public host (e.g. N100 systemd):

    Environment=DASHBOARD_AUTH=user:the-strong-password

`/health` stays open so liveness probes work without credentials.
"""

from __future__ import annotations

import base64
import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

OPEN_PATHS = {"/health"}
_REALM = "Weather Dashboard"


def _credentials() -> tuple[str, str] | None:
    raw = os.environ.get("DASHBOARD_AUTH", "").strip()
    if not raw or ":" not in raw:
        return None
    user, _, pw = raw.partition(":")
    return user, pw


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic Auth on every request when DASHBOARD_AUTH is set."""

    async def dispatch(self, request: Request, call_next):
        creds = _credentials()
        if creds is None or request.url.path in OPEN_PATHS:
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                u, _, p = decoded.partition(":")
                # constant-time compare to avoid timing leaks
                if hmac.compare_digest(u, creds[0]) and hmac.compare_digest(p, creds[1]):
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{_REALM}"'},
            content="Authentication required",
        )
