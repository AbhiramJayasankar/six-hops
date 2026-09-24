from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates


def _root(request: Request) -> dict[str, str]:
    # Prefix for every link, so the app works behind a reverse proxy sub-path.
    return {"root": request.scope.get("root_path", "")}


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_root]
)
