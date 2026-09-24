"""Rank paths from Me to a target company.

Model: each edge has a probability-like weight p. A path's score is the product of its weights,
so the "strongest" path is the one with the lowest total cost, where cost = -ln p.

- KNOWS edge: p = STRENGTH_P[strength]
- final hop into the target: p = STRENGTH_P[strength] * employment factor (WORKED_AT < WORKS_AT)
- optional institution hops: passing through a shared company/school is a weak tie with combined
  p = institution_tie (split evenly across the edge in and the edge out)
"""

import math
from dataclasses import dataclass, field
from itertools import islice, takewhile

import networkx as nx

from sixhops.core.model import INSTITUTIONS, PEOPLE, GraphSnapshot

STRENGTH_P = {1: 0.20, 2: 0.40, 3: 0.60, 4: 0.80, 5: 0.95}
HARD_MAX_HOPS = 6


@dataclass(frozen=True)
class RankingConfig:
    employment_factor: dict[str, float] = field(
        default_factory=lambda: {"WORKS_AT": 1.0, "WORKED_AT": 0.5}
    )
    via_institutions: bool = False  # allow shared companies/schools as intermediate hops
    institution_tie: float = 0.3
    max_hops: int = 4
    k: int = 5  # paths per list (shortest, strongest)
    search_limit: int = 200  # candidate paths examined per list


@dataclass(frozen=True)
class RankedPath:
    nodes: tuple[str, ...]  # Me, ..., target
    edges: tuple[str, ...]  # edge ids, one per hop
    score: float
    tags: frozenset[str]  # subset of {"shortest", "strongest"}
    first_hop: str | None  # first person after Me: who to message (None when Me is direct)
    referrer: str  # the person whose affiliation reaches the target (Me when direct)

    @property
    def hops(self) -> int:
        return len(self.edges)


@dataclass(frozen=True)
class PathResult:
    target: str
    paths: list[RankedPath]
    people_at_target: int  # people with a WORKS_AT/WORKED_AT edge to the target
    min_hops: int | None  # fewest hops to the target ignoring the cap; None if unreachable


def rank(g: GraphSnapshot, target_id: str, config: RankingConfig | None = None) -> PathResult:
    config = config or RankingConfig()
    target = g.nodes.get(target_id)
    if target is None or target.kind != "company":
        raise ValueError(f"target {target_id!r} is not a company")
    max_hops = min(config.max_hops, HARD_MAX_HOPS)

    graph = _traversal_graph(g, target_id, config)
    source = g.me().id
    at_target = set(graph.neighbors(target_id)) if target_id in graph else set()
    people_at_target = len(at_target - {source})
    if source not in graph or not at_target or not nx.has_path(graph, source, target_id):
        return PathResult(target_id, [], people_at_target, None)

    def usable(nodes: list[str]) -> bool:
        # Skip paths that pass through someone already at the target: the shorter path via that
        # person is always better and is listed on its own.
        return len(nodes) - 1 <= max_hops and not (set(nodes[:-2]) & at_target)

    def candidates(weight: str | None) -> list[list[str]]:
        found = nx.shortest_simple_paths(graph, source, target_id, weight=weight)
        if weight is None:  # yielded in hop order, so stop at the cap
            found = takewhile(lambda p: len(p) - 1 <= max_hops, found)
        return [p for p in islice(found, config.search_limit) if usable(p)]

    by_hops = candidates(weight=None)
    by_cost = candidates(weight="cost")
    shortest = sorted(by_hops, key=lambda p: (len(p), _cost(graph, p)))[: config.k]
    strongest = by_cost[: config.k]  # already in cost order

    tags: dict[tuple[str, ...], set[str]] = {}
    for tag, paths in (("shortest", shortest), ("strongest", strongest)):
        for p in paths:
            tags.setdefault(tuple(p), set()).add(tag)
    ranked = [
        RankedPath(
            nodes=nodes,
            edges=tuple(
                graph.edges[a, b]["edge_id"] for a, b in zip(nodes, nodes[1:], strict=False)
            ),
            score=math.exp(-_cost(graph, nodes)),
            tags=frozenset(t),
            first_hop=next((n for n in nodes[1:-1] if g.nodes[n].kind in PEOPLE), None),
            referrer=nodes[-2],
        )
        for nodes, t in tags.items()
    ]
    ranked.sort(key=lambda p: (-p.score, p.hops))
    min_hops = nx.shortest_path_length(graph, source, target_id)
    return PathResult(target_id, ranked, people_at_target, min_hops)


def _traversal_graph(g: GraphSnapshot, target_id: str, config: RankingConfig) -> nx.Graph:
    """Undirected graph of people (plus the target, plus institutions if enabled) with costs."""
    graph = nx.Graph()
    half_tie = -math.log(config.institution_tie) / 2

    def add(a: str, b: str, cost: float, edge_id: str) -> None:
        # Keep only the cheapest edge between a pair (e.g. both WORKS_AT and WORKED_AT).
        if not graph.has_edge(a, b) or graph.edges[a, b]["cost"] > cost:
            graph.add_edge(a, b, cost=cost, edge_id=edge_id)

    for e in g.edges.values():
        kinds = (g.nodes[e.src].kind, g.nodes[e.dst].kind)
        p = STRENGTH_P[e.strength]
        if e.kind == "KNOWS":
            add(e.src, e.dst, -math.log(p), e.id)
        elif e.dst == target_id and e.kind in config.employment_factor:
            add(e.src, e.dst, -math.log(p * config.employment_factor[e.kind]), e.id)
        elif config.via_institutions and kinds[0] in PEOPLE and kinds[1] in INSTITUTIONS:
            add(e.src, e.dst, half_tie, e.id)
    return graph


def _cost(graph: nx.Graph, nodes: list[str] | tuple[str, ...]) -> float:
    return sum(graph.edges[a, b]["cost"] for a, b in zip(nodes, nodes[1:], strict=False))
