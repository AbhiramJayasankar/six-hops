"""Path queries: "how do I reach company X?" as JSON and as a side-panel partial."""

import difflib
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from sixhops.app.routes.api import Graph
from sixhops.app.services.manual import edge_text, other_name, pick_node
from sixhops.app.templating import templates
from sixhops.core.model import INSTITUTIONS, Edge, GraphSnapshot, Node
from sixhops.core.ops import InvalidOps
from sixhops.core.paths import HARD_MAX_HOPS, PathResult, RankedPath, RankingConfig, rank

router = APIRouter()
DEFAULT_MAX_HOPS = 4

MaxHops = Annotated[int, Query(ge=1, le=HARD_MAX_HOPS)]


class PathOut(BaseModel):
    nodes: list[str]
    edges: list[str]
    hops: int
    score: float
    tags: list[str]
    first_hop: str | None
    referrer: str


class PathsOut(BaseModel):
    target: str
    paths: list[PathOut]
    people_at_target: int
    min_hops: int | None
    max_hops: int


@router.get("/api/paths")
def api_paths(
    graph: Graph,
    target: str,
    max_hops: MaxHops = DEFAULT_MAX_HOPS,
    via_institutions: bool = False,
) -> PathsOut:
    g = graph.snapshot()
    if target not in g.nodes or g.nodes[target].kind != "company":
        raise HTTPException(404, f"no company with id {target!r}")
    result = rank(g, target, RankingConfig(max_hops=max_hops, via_institutions=via_institutions))
    return PathsOut(
        target=target,
        paths=[_path_out(p) for p in result.paths],
        people_at_target=result.people_at_target,
        min_hops=result.min_hops,
        max_hops=max_hops,
    )


@router.get("/ui/paths", response_class=HTMLResponse)
def ui_paths(
    request: Request,
    graph: Graph,
    target: str = "",
    max_hops: MaxHops = DEFAULT_MAX_HOPS,
    via_institutions: bool = False,
):
    g = graph.snapshot()
    context: dict = {
        "query": target,
        "max_hops": max_hops,
        "hard_max": HARD_MAX_HOPS,
        "via_institutions": via_institutions,
        "company": None,
        "suggestions": [],
        "errors": [],
    }
    try:
        company = pick_node(g, target, {"company"}) if target.strip() else None
    except InvalidOps as exc:
        company, context["errors"] = None, exc.errors
    if company is None:
        context["suggestions"] = _suggest(g, other_name(target))
        return templates.TemplateResponse(request, "graph/panel_paths.html", context)

    config = RankingConfig(max_hops=max_hops, via_institutions=via_institutions)
    result = rank(g, company.id, config)
    context |= {"company": company, "result": result, "views": _views(g, result)}
    return templates.TemplateResponse(request, "graph/panel_paths.html", context)


@dataclass(frozen=True)
class Hop:
    edge: Edge
    to: Node
    kind_text: str
    style: str  # "former" (WORKED_AT), "shared" (through an institution), or ""


@dataclass(frozen=True)
class PathView:
    path: RankedPath
    start: Node
    hops: list[Hop]
    first_hop: Node | None
    referrer: Node
    shared: Node | None  # institution Me shares with the first hop, when the path starts with one


def _views(g: GraphSnapshot, result: PathResult) -> list[PathView]:
    views = []
    for p in result.paths:
        hops = [
            Hop(g.edges[e], g.nodes[n], edge_text(g.edges[e], prev), _style(g, g.edges[e], p))
            for e, prev, n in zip(p.edges, p.nodes, p.nodes[1:], strict=False)
        ]
        first = g.nodes[p.first_hop] if p.first_hop else None
        second = g.nodes[p.nodes[1]]
        shared = second if second.kind in INSTITUTIONS and p.hops > 1 else None
        views.append(PathView(p, g.nodes[p.nodes[0]], hops, first, g.nodes[p.referrer], shared))
    return views


def _style(g: GraphSnapshot, edge: Edge, path: RankedPath) -> str:
    target = path.nodes[-1]
    if any(g.nodes[n].kind in INSTITUTIONS and n != target for n in (edge.src, edge.dst)):
        return "shared"
    return "former" if edge.kind == "WORKED_AT" else ""


def _suggest(g: GraphSnapshot, text: str) -> list[Node]:
    companies = {n.name: n for n in g.nodes.values() if n.kind == "company"}
    if not text:
        return []
    close = difflib.get_close_matches(text, list(companies), n=5, cutoff=0.5)
    starts = [name for name in companies if name.casefold().startswith(text.casefold()[:3])]
    return [companies[name] for name in dict.fromkeys([*close, *starts])][:5]


def _path_out(p: RankedPath) -> PathOut:
    return PathOut(
        nodes=list(p.nodes),
        edges=list(p.edges),
        hops=p.hops,
        score=round(p.score, 4),
        tags=sorted(p.tags),
        first_hop=p.first_hop,
        referrer=p.referrer,
    )
