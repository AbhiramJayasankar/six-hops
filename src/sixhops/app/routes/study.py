from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sixhops.app.templating import templates

router = APIRouter()


@router.get("/study", response_class=HTMLResponse)
def study_page(request: Request):
    return templates.TemplateResponse(request, "study.html", {"active": "study"})
