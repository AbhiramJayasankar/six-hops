from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sixhops.app.templating import templates

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    return templates.TemplateResponse(request, "jobs.html", {"active": "jobs"})
