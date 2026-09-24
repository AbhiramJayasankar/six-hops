"""Single-user auth: one password from env sets a signed session cookie.
An optional API token is accepted as `Authorization: Bearer <token>` for curl/scripts."""

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from sixhops.app.config import Settings

PUBLIC_PATHS = ("/login", "/static/", "/healthz")


def check_password(settings: Settings, candidate: str) -> bool:
    return hmac.compare_digest(candidate, settings.app_password.get_secret_value())


def _has_valid_token(settings: Settings, request: Request) -> bool:
    if settings.api_token is None:
        return False
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        token, settings.api_token.get_secret_value()
    )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC_PATHS) or request.session.get("authed"):
            return await call_next(request)
        if _has_valid_token(self.settings, request):
            return await call_next(request)
        if request.headers.get("hx-request") or path.startswith("/api/"):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return RedirectResponse(app_path(request, "/login"), status_code=303)


def app_path(request: Request, path: str) -> str:
    """Relative URL that respects a reverse-proxy mount point (root_path)."""
    return request.scope.get("root_path", "") + path
