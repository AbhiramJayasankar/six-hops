from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sixhops.app.templating import templates

router = APIRouter()


@router.get("/email", response_class=HTMLResponse)
def email_page(request: Request):
    return templates.TemplateResponse(request, "email.html", {"active": "email"})
