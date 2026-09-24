"""Graph page and its HTMX side-panel partials. Every write builds ops and goes through
GraphService, exactly like the JSON API."""

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from sixhops.app.routes.api import Graph
from sixhops.app.services.graph import GraphDocument, GraphService
from sixhops.app.services.manual import (
    EDGE_TEXT,
    connect_ops,
    connect_target_kinds,
    node_label,
    other_name,
    pick_node,
)
from sixhops.app.templating import templates
from sixhops.core.model import PEOPLE, GraphSnapshot
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    ExistingRef,
    InvalidOps,
    MergeNodes,
    NewRef,
    Op,
    UpdateEdge,
    UpdateNode,
)
from sixhops.core.resolve import candidates, find_duplicates
from sixhops.ports.graph_store import NothingToUndo, VersionConflict

router = APIRouter()
HTML = HTMLResponse

PERSON_ATTRS = ("title", "email", "linkedin")
INSTITUTION_ATTRS = ("domain",)


@router.get("/", response_class=HTML)
def graph_page(request: Request, graph: Graph):
    return templates.TemplateResponse(request, "graph/page.html", {"active": "graph"})


# --- panels (GET) --------------------------------------------------------------------------


@router.get("/ui/panel", response_class=HTML)
def home_panel(request: Request, graph: Graph):
    return _home(request, graph.snapshot(), graph)


@router.get("/ui/node/{node_id}", response_class=HTML)
def node_panel(request: Request, node_id: str, graph: Graph):
    return _node(request, graph.snapshot(), node_id)


@router.get("/ui/duplicates", response_class=HTML)
def duplicates_panel(request: Request, graph: Graph):
    return _duplicates(request, graph.snapshot())


@router.get("/ui/edge/{edge_id}", response_class=HTML)
def edge_panel(request: Request, edge_id: str, graph: Graph):
    return _edge(request, graph.snapshot(), edge_id)


# --- writes (POST) -------------------------------------------------------------------------


@router.post("/ui/node", response_class=HTML)
def create_node(
    request: Request,
    graph: Graph,
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()],
    knows_me: Annotated[bool, Form()] = False,
    strength: Annotated[int, Form()] = 3,
    confirm_new: Annotated[bool, Form()] = False,
):
    g = graph.snapshot()
    if not confirm_new and (check := _duplicate_check(g, name, kind)):
        fields = {"kind": kind, "name": name, "strength": strength, "confirm_new": "true"}
        if knows_me:
            fields["knows_me"] = "true"
        check |= {"action": "/ui/node", "fields": fields, "use_field": None}
        return _home(request, g, graph, check=check)

    def ops(g: GraphSnapshot) -> list[Op]:
        created: list[Op] = [CreateNode(key="new", kind=kind, name=name.strip())]
        if knows_me and kind == "person":
            me = ExistingRef(id=g.me().id)
            created.append(
                CreateEdge(kind="KNOWS", src=me, dst=NewRef(key="new"), strength=strength)
            )
        return created

    return _write(request, graph, ops, f"Added {name.strip()}", show="new")


@router.post("/ui/node/{node_id}", response_class=HTML)
def update_node(
    request: Request,
    node_id: str,
    graph: Graph,
    name: Annotated[str, Form()],
    aliases: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    attr_title: Annotated[str, Form()] = "",
    attr_email: Annotated[str, Form()] = "",
    attr_linkedin: Annotated[str, Form()] = "",
    attr_domain: Annotated[str, Form()] = "",
):
    form_attrs = {"title": attr_title, "email": attr_email, "linkedin": attr_linkedin,
                  "domain": attr_domain}  # fmt: skip

    def ops(g: GraphSnapshot) -> list[Op]:
        fields = PERSON_ATTRS if g.nodes[node_id].kind in PEOPLE else INSTITUTION_ATTRS
        return [
            UpdateNode(
                node_id=node_id,
                name=name.strip() or None,
                aliases=[a.strip() for a in aliases.split(",") if a.strip()],
                attrs={k: form_attrs[k].strip() for k in fields},
                notes=notes,
            )
        ]

    return _write(request, graph, ops, lambda g: f"Edited {g.nodes[node_id].name}", show=node_id)


@router.post("/ui/node/{node_id}/connect", response_class=HTML)
def connect(
    request: Request,
    node_id: str,
    graph: Graph,
    kind: Annotated[str, Form()],
    other: Annotated[str, Form()],
    strength: Annotated[int, Form()] = 3,
    note: Annotated[str, Form()] = "",
    confirm_new: Annotated[bool, Form()] = False,
):
    g = graph.snapshot()
    if (
        not confirm_new
        and node_id in g.nodes
        and (check := _connect_check(g, node_id, kind, other))
    ):
        fields = {"kind": kind, "other": other, "strength": strength, "note": note,
                  "confirm_new": "true"}  # fmt: skip
        check |= {"action": f"/ui/node/{node_id}/connect", "fields": fields, "use_field": "other"}
        return _node(request, g, node_id, check=check)
    return _write(
        request,
        graph,
        lambda g: connect_ops(g, node_id, kind, other, strength, note),
        lambda g: f"{g.nodes[node_id].name}: {EDGE_TEXT.get(kind, kind)} {other_name(other)}",
        show=node_id,
    )


@router.post("/ui/node/{node_id}/merge", response_class=HTML)
def merge(request: Request, node_id: str, graph: Graph, other: Annotated[str, Form()]):
    def ops(g: GraphSnapshot) -> list[Op]:
        keep = g.nodes[node_id]
        kinds = {"person"} if keep.kind in PEOPLE else {keep.kind}
        drop = pick_node(g, other, kinds)
        if drop is None:
            raise InvalidOps([f"no {'/'.join(sorted(kinds))} named {other!r}"])
        return [MergeNodes(keep_id=node_id, drop_id=drop.id, reason="manual")]

    return _write(
        request,
        graph,
        ops,
        lambda g: f"Merged {other_name(other)} into {g.nodes[node_id].name}",
        show=node_id,
    )


@router.post("/ui/node/{node_id}/delete", response_class=HTML)
def delete_node(request: Request, node_id: str, graph: Graph):
    return _write(
        request,
        graph,
        lambda g: [DeleteNode(node_id=node_id)],
        lambda g: f"Deleted {g.nodes[node_id].name}",
    )


@router.post("/ui/edge/{edge_id}", response_class=HTML)
def update_edge(
    request: Request,
    edge_id: str,
    graph: Graph,
    strength: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
    kind: Annotated[str | None, Form()] = None,
):
    return _write(
        request,
        graph,
        lambda g: [UpdateEdge(edge_id=edge_id, kind=kind or None, strength=strength, note=note)],
        lambda g: f"Edited {_edge_text(g, edge_id)}",
        show_edge=edge_id,
    )


@router.post("/ui/edge/{edge_id}/delete", response_class=HTML)
def delete_edge(request: Request, edge_id: str, graph: Graph, back: Annotated[str, Form()] = ""):
    return _write(
        request,
        graph,
        lambda g: [DeleteEdge(edge_id=edge_id)],
        lambda g: f"Removed {_edge_text(g, edge_id)}",
        show=back,
    )


@router.post("/ui/undo", response_class=HTML)
def undo(request: Request, graph: Graph):
    try:
        record = graph.undo()
        message = f"Undid: {record.summary}"
    except (NothingToUndo, VersionConflict, InvalidOps) as exc:
        return _home(request, graph.snapshot(), graph, errors=[_message(exc)])
    return _changed(_home(request, graph.snapshot(), graph, flash=message))


@router.post("/ui/import", response_class=HTML)
def import_graph(request: Request, graph: Graph, file: Annotated[UploadFile, File()]):
    try:
        graph.replace(GraphDocument.model_validate_json(file.file.read()))
    except (InvalidOps, ValidationError) as exc:
        return _home(request, graph.snapshot(), graph, errors=_messages(exc))
    return _changed(_home(request, graph.snapshot(), graph, flash="Imported. Undo reverts it."))


# --- helpers -------------------------------------------------------------------------------


def _write(
    request: Request,
    graph: GraphService,
    build: Callable[[GraphSnapshot], list[Op]],
    summary: str | Callable[[GraphSnapshot], str],
    *,
    show: str = "",
    show_edge: str = "",
):
    """Build ops against the current graph, apply them, and re-render the right panel."""
    g = graph.snapshot()
    try:
        text = summary if isinstance(summary, str) else summary(g)
        compiled = graph.apply(build(g), summary=text, expected_version=g.version)
    except (InvalidOps, VersionConflict, ValidationError, KeyError) as exc:
        errors = _messages(exc)
        if show_edge:
            return _edge(request, g, show_edge, errors=errors)
        if show in g.nodes:
            return _node(request, g, show, errors=errors)
        return _home(request, g, graph, errors=errors)

    after = graph.snapshot()
    target = compiled.new_ids.get(show, show)
    if show_edge and show_edge in after.edges:
        response = _edge(request, after, show_edge, flash="Saved.")
    elif target in after.nodes:
        response = _node(request, after, target, flash="Saved.")
    else:
        response = _home(request, after, graph, flash=f"{text}.")
    return _changed(response, select=target if target in after.nodes else "")


def _changed(response: HTMLResponse, select: str = "") -> HTMLResponse:
    """Tell graph.js to refresh the canvas (and optionally select a node)."""
    response.headers["HX-Trigger"] = json.dumps({"graph-changed": {"select": select}})
    return response


def _duplicate_check(g: GraphSnapshot, name: str, kind: str) -> dict | None:
    """Existing nodes a new entry might duplicate, for the "is this someone you have?" prompt."""
    if kind not in ("person", "company", "school") or not name.strip():
        return None
    found = candidates(g, name.strip(), kind)
    if not found:
        return None
    return {
        "name": name.strip(),
        "matches": [(g.nodes[c.node_id], c.score, c.reason) for c in found],
    }


def _connect_check(g: GraphSnapshot, node_id: str, kind: str, other: str) -> dict | None:
    """Like _duplicate_check, for the "connect to a new name" path of the connect form."""
    try:
        kinds = connect_target_kinds(g.nodes[node_id], kind)
        if pick_node(g, other, kinds):
            return None  # an existing node was picked: nothing new is created
    except (InvalidOps, KeyError):
        return None  # connect_ops reports the error itself
    new_kind = "person" if "person" in kinds else next(iter(kinds))
    return _duplicate_check(g, other_name(other), new_kind)


def _duplicates(request, g: GraphSnapshot):
    pairs = [(g.nodes[d.keep_id], g.nodes[d.drop_id], d) for d in find_duplicates(g)]
    return templates.TemplateResponse(request, "graph/panel_duplicates.html", {"pairs": pairs})


def _home(request, g: GraphSnapshot, graph, errors=None, flash=None, check=None):
    counts = {
        k: sum(1 for n in g.nodes.values() if n.kind == k) for k in ("person", "company", "school")
    }
    return templates.TemplateResponse(
        request,
        "graph/panel_home.html",
        {
            "g": g,
            "counts": counts,
            "history": graph.history(8),
            "errors": errors or [],
            "flash": flash,
            "node_options": _options(g),
            "check": check,
        },
    )


def _node(request, g: GraphSnapshot, node_id: str, errors=None, flash=None, check=None):
    node = g.nodes.get(node_id)
    if node is None:
        return HTMLResponse('<p class="muted">That node no longer exists.</p>')
    edges = sorted(g.incident(node_id), key=lambda e: (e.kind, -e.strength))
    connections = [(e, g.nodes[e.dst if e.src == node_id else e.src]) for e in edges]
    return templates.TemplateResponse(
        request,
        "graph/panel_node.html",
        {
            "node": node,
            "connections": connections,
            "attr_fields": PERSON_ATTRS if node.kind in PEOPLE else INSTITUTION_ATTRS,
            "errors": errors or [],
            "flash": flash,
            "node_options": _options(g),
            "is_person": node.kind in PEOPLE,
            "check": check,
        },
    )


def _edge(request, g: GraphSnapshot, edge_id: str, errors=None, flash=None):
    edge = g.edges.get(edge_id)
    if edge is None:
        return HTMLResponse('<p class="muted">That connection no longer exists.</p>')
    return templates.TemplateResponse(
        request,
        "graph/panel_edge.html",
        {
            "edge": edge,
            "src": g.nodes[edge.src],
            "dst": g.nodes[edge.dst],
            "errors": errors or [],
            "flash": flash,
        },
    )


def _edge_text(g: GraphSnapshot, edge_id: str) -> str:
    e = g.edges[edge_id]
    return f"{g.nodes[e.src].name} {EDGE_TEXT[e.kind]} {g.nodes[e.dst].name}"


def _options(g: GraphSnapshot) -> dict[str, list[str]]:
    by_kind: dict[str, list[str]] = {"person": [], "company": [], "school": []}
    for n in sorted(g.nodes.values(), key=lambda n: n.name.casefold()):
        by_kind.setdefault("person" if n.kind == "me" else n.kind, []).append(node_label(n))
    return by_kind


def _message(exc: Exception) -> str:
    if isinstance(exc, NothingToUndo):
        return "Nothing to undo."
    if isinstance(exc, VersionConflict):
        return "The graph changed in the meantime. Reload and try again."
    return str(exc)


def _messages(exc: Exception) -> list[str]:
    if isinstance(exc, InvalidOps):
        return exc.errors
    if isinstance(exc, ValidationError):
        return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    if isinstance(exc, KeyError):
        return ["That node or connection no longer exists."]
    return [_message(exc)]
