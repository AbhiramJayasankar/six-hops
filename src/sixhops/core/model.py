"""Graph domain model: nodes, edges and an immutable-by-convention snapshot."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal["me", "person", "company", "school"]
EdgeKind = Literal["KNOWS", "WORKS_AT", "WORKED_AT", "STUDIED_AT"]

PEOPLE: frozenset[str] = frozenset({"me", "person"})
INSTITUTIONS: frozenset[str] = frozenset({"company", "school"})

# Allowed destination kinds per edge kind. The source is always a person or me.
EDGE_DST: dict[str, frozenset[str]] = {
    "KNOWS": PEOPLE,
    "WORKS_AT": frozenset({"company"}),
    "WORKED_AT": frozenset({"company"}),
    "STUDIED_AT": frozenset({"school"}),
}

Strength = Field(default=3, ge=1, le=5)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Node(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: NodeKind
    name: str = Field(min_length=1)
    aliases: list[str] = []
    attrs: dict[str, str] = {}  # person: title, email, linkedin; institution: domain
    notes: str = ""


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: EdgeKind
    src: str  # always a person or me
    dst: str  # KNOWS: person or me (canonical: src < dst); *_AT: an institution
    strength: int = Strength
    note: str = ""


def canonical(kind: str, src: str, dst: str) -> tuple[str, str]:
    """KNOWS is undirected, so store its endpoints in sorted order."""
    return (dst, src) if kind == "KNOWS" and dst < src else (src, dst)


class GraphSnapshot(BaseModel):
    version: int = 0
    nodes: dict[str, Node] = {}
    edges: dict[str, Edge] = {}

    def me(self) -> Node:
        return next(n for n in self.nodes.values() if n.kind == "me")

    def incident(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if node_id in (e.src, e.dst)]

    def find_edge(self, kind: str, src: str, dst: str) -> Edge | None:
        src, dst = canonical(kind, src, dst)
        return next(
            (e for e in self.edges.values() if (e.kind, e.src, e.dst) == (kind, src, dst)), None
        )
