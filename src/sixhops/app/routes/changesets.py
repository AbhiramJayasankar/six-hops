"""Reviewing proposed changes, and the LinkedIn import that proposes them."""

import json
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from sixhops.adapters.importers import linkedin
from sixhops.app.services.changesets import (
    GROUPS,
    ChangeSetService,
    NotFound,
    NotPending,
    PendingChange,
    Policy,
    describe,
)
from sixhops.app.templating import templates
from sixhops.core.ops import InvalidOps

router = APIRouter()
HTML = HTMLResponse
SHOW_PER_GROUP = 200  # the rest of a big group is listed as "and N more" (still included)

# Imports only add what's missing: existing strengths and notes are yours and stay as they are.
IMPORT_POLICY = Policy(
    update_existing_edges=False,
    strengths_inferred=False,
)


def changes(request: Request) -> ChangeSetService:
    return request.app.state.services.changes


# --- LinkedIn import -------------------------------------------------------------------------


def _propose_linkedin(request: Request, data: bytes) -> tuple[PendingChange, int]:
    parsed = linkedin.parse(data)
    if not parsed.connections:
        raise linkedin.NotAConnectionsFile("The file has a header but no connections in it.")
    n = len(parsed.connections)
    change = changes(request).propose(
        linkedin.to_extraction(parsed.connections),
        title=f"LinkedIn import ({n} connection{'' if n == 1 else 's'})",
        source="linkedin",
        policy=IMPORT_POLICY,
    )
    return change, parsed.skipped


@router.get("/ui/import/linkedin", response_class=HTML)
def linkedin_form(request: Request):
    return templates.TemplateResponse(request, "graph/panel_linkedin.html", {"errors": []})


@router.post("/ui/import/linkedin", response_class=HTML)
def linkedin_upload(request: Request, file: Annotated[UploadFile, File()]):
    try:
        change, skipped = _propose_linkedin(request, file.file.read())
    except linkedin.NotAConnectionsFile as exc:
        return templates.TemplateResponse(
            request, "graph/panel_linkedin.html", {"errors": [str(exc)]}
        )
    notice = None
    if skipped:
        rows = "1 row without a name was" if skipped == 1 else f"{skipped} rows without a name were"
        notice = f"{rows} skipped."
    return _review(request, change, notice=notice)


# --- review ----------------------------------------------------------------------------------


@router.get("/ui/changes/{change_id}", response_class=HTML)
def review(request: Request, change_id: str):
    return _review(request, _get(request, change_id))


@router.post("/ui/changes/{change_id}/replan", response_class=HTML)
async def replan(request: Request, change_id: str):
    form = await request.form()
    decisions = {
        k.removeprefix("choice."): str(v) for k, v in form.items() if k.startswith("choice.")
    }
    try:
        change = changes(request).replan(change_id, decisions)
    except (NotFound, NotPending) as exc:
        return _gone(exc)
    return _review(request, change)


@router.post("/ui/changes/{change_id}/apply", response_class=HTML)
async def apply(request: Request, change_id: str):
    # Two fields per listed change; a big import lists up to SHOW_PER_GROUP per group.
    form = await request.form(max_fields=4 * SHOW_PER_GROUP * len(GROUPS) + 100)
    shown = {int(v) for v in form.getlist("shown")}
    included = {int(v) for v in form.getlist("include")}
    try:
        change = changes(request).apply(change_id, excluded=shown - included)
    except (NotFound, NotPending) as exc:
        return _gone(exc)
    except InvalidOps as exc:
        return _review(request, _get(request, change_id), errors=exc.errors)
    if change.stale:
        notice = (
            "Your graph changed since this was prepared, so it was checked again. Review and apply."
        )
        return _review(request, change, notice=notice)
    response = templates.TemplateResponse(request, "graph/panel_applied.html", {"change": change})
    response.headers["HX-Trigger"] = json.dumps({"graph-changed": {"select": ""}})
    return response


@router.post("/ui/changes/{change_id}/discard", response_class=HTML)
def discard(request: Request, change_id: str):
    try:
        changes(request).discard(change_id)
    except (NotFound, NotPending) as exc:
        return _gone(exc)
    return HTMLResponse(
        '<p class="muted">Discarded. Nothing was changed.</p>'
        f'<p><a href="#" hx-get="{request.scope.get("root_path", "")}/ui/panel" '
        'hx-target="#panel" data-deselect>Back to overview</a></p>'
    )


# --- JSON API --------------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    excluded: list[int] = []


@router.post("/api/import/linkedin")
def api_linkedin(request: Request, file: Annotated[UploadFile, File()]) -> PendingChange:
    try:
        change, _ = _propose_linkedin(request, file.file.read())
    except linkedin.NotAConnectionsFile as exc:
        raise HTTPException(422, str(exc)) from exc
    return change


@router.get("/api/changes/{change_id}")
def api_get(request: Request, change_id: str) -> PendingChange:
    return _get(request, change_id)


@router.post("/api/changes/{change_id}/replan")
def api_replan(request: Request, change_id: str, decisions: dict[str, str]) -> PendingChange:
    try:
        return changes(request).replan(change_id, decisions)
    except NotPending as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/changes/{change_id}/apply")
def api_apply(request: Request, change_id: str, body: ApplyRequest) -> PendingChange:
    _get(request, change_id)
    try:
        return changes(request).apply(change_id, excluded=set(body.excluded))
    except NotPending as exc:
        raise HTTPException(409, str(exc)) from exc


# --- helpers ---------------------------------------------------------------------------------


def _get(request: Request, change_id: str) -> PendingChange:
    try:
        return changes(request).get(change_id)
    except NotFound as exc:
        raise HTTPException(404, "no such change") from exc


def _gone(exc: Exception) -> HTMLResponse:
    text = "That change no longer exists." if isinstance(exc, NotFound) else f"{exc}."
    return HTMLResponse(f'<div class="alert error" role="alert">{text[0].upper()}{text[1:]}</div>')


def _review(request: Request, change: PendingChange, notice=None, errors=None):
    g = request.app.state.services.graph.snapshot()
    lines = describe(change.changeset, g) if change.status == "pending" else []
    groups = {name: [ln for ln in lines if ln.group == name] for name in GROUPS}
    resolutions = change.changeset.resolutions
    return templates.TemplateResponse(
        request,
        "graph/panel_changeset.html",
        {
            "change": change,
            "groups": {k: v for k, v in groups.items() if v},
            "show_per_group": SHOW_PER_GROUP,
            "asking": [r for r in resolutions if not r.auto],
            "matched": [r for r in resolutions if r.auto],
            "notice": notice,
            "errors": errors or [],
        },
    )
