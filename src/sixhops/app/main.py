"""App factory. Wiring of ports to adapters happens in `wiring.py`; routes stay thin."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from sixhops.app.auth import AuthMiddleware
from sixhops.app.config import Settings, get_settings
from sixhops.app.routes import api, auth, changesets, email, graph, jobs, paths, study
from sixhops.app.wiring import build_services
from sixhops.core.ops import InvalidOps
from sixhops.ports.graph_store import NothingToUndo, VersionConflict


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="6hops", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.services = build_services(settings)

    # Middleware added last runs first: sessions must wrap auth.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="sixhops_session",
        https_only=settings.cookie_secure,
        same_site="lax",
        max_age=60 * 60 * 24 * 30,
    )

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    for module in (auth, api, graph, paths, changesets, jobs, email, study):
        app.include_router(module.router)

    _add_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidOps)
    def invalid_ops(_: Request, exc: InvalidOps):
        return JSONResponse({"detail": exc.errors}, status_code=422)

    @app.exception_handler(VersionConflict)
    def conflict(_: Request, exc: VersionConflict):
        return JSONResponse({"detail": f"graph changed, reload and retry ({exc})"}, status_code=409)

    @app.exception_handler(ValidationError)
    def invalid_input(_: Request, exc: ValidationError):
        return JSONResponse({"detail": [e["msg"] for e in exc.errors()]}, status_code=422)

    @app.exception_handler(NothingToUndo)
    def nothing_to_undo(_: Request, __: NothingToUndo):
        return JSONResponse({"detail": "nothing to undo"}, status_code=409)
