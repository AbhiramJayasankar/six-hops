"""JSON API for the graph. The HTML UI uses the same service calls."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from sixhops.app.services.graph import GraphDocument, GraphService
from sixhops.core.model import GraphSnapshot
from sixhops.core.ops import Op
from sixhops.ports.graph_store import ChangeRecord

router = APIRouter(prefix="/api")


def graph_service(request: Request) -> GraphService:
    return request.app.state.services.graph


Graph = Annotated[GraphService, Depends(graph_service)]


class OpsRequest(BaseModel):
    ops: list[Op]
    summary: str = "Manual edit"
    expected_version: int | None = None


class OpsResult(BaseModel):
    version: int
    new_ids: dict[str, str]


class HistoryItem(BaseModel):
    id: int
    version_after: int
    summary: str
    created_at: str


@router.get("/graph")
def get_graph(graph: Graph) -> GraphSnapshot:
    return graph.snapshot()


@router.post("/ops")
def post_ops(body: OpsRequest, graph: Graph) -> OpsResult:
    compiled = graph.apply(body.ops, summary=body.summary, expected_version=body.expected_version)
    return OpsResult(version=graph.snapshot().version, new_ids=compiled.new_ids)


@router.get("/history")
def get_history(graph: Graph, limit: int = 20) -> list[HistoryItem]:
    return [_item(r) for r in graph.history(limit)]


@router.post("/undo")
def post_undo(graph: Graph) -> HistoryItem:
    return _item(graph.undo())


@router.get("/graph/export")
def export_graph(graph: Graph) -> Response:
    doc = graph.export()
    filename = f"6hops-{doc.exported_at:%Y%m%d-%H%M%S}.json"
    return Response(
        doc.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/graph/import")
def import_graph(graph: Graph, file: Annotated[UploadFile, File()]) -> dict[str, int]:
    doc = GraphDocument.model_validate_json(file.file.read())
    return {"version": graph.replace(doc)}


def _item(r: ChangeRecord) -> HistoryItem:
    return HistoryItem(
        id=r.id,
        version_after=r.version_after,
        summary=r.summary,
        created_at=r.created_at.isoformat(),
    )
