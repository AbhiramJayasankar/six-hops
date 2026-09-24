from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sixhops.app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def graph_page(request: Request):
    return templates.TemplateResponse(request, "graph.html", {"active": "graph"})
