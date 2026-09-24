from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from sixhops.app.auth import app_path, check_password
from sixhops.app.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, name="login_form")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    if not check_password(request.app.state.settings, password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong password."}, status_code=401
        )
    request.session["authed"] = True
    return RedirectResponse(app_path(request, "/"), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(app_path(request, "/login"), status_code=303)
