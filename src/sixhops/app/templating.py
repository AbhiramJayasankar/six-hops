from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from sixhops.app.services.manual import edge_text


def _root(request: Request) -> dict[str, str]:
    # Prefix for every link, so the app works behind a reverse proxy sub-path.
    return {"root": request.scope.get("root_path", "")}


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_root]
)
templates.env.globals["edge_text"] = edge_text
