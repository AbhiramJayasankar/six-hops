"""App factory. Wiring of ports to adapters happens in `wiring.py`; routes stay thin."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sixhops.app.auth import AuthMiddleware
from sixhops.app.config import Settings, get_settings
from sixhops.app.routes import auth, email, graph, jobs, study


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="6hops", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings

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
    for module in (auth, graph, jobs, email, study):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
