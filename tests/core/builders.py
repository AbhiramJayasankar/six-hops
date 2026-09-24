"""Tiny helpers for building graphs in tests."""

import itertools

from sixhops.core.model import Edge, GraphSnapshot, Node, canonical


def counter(prefix: str = "id"):
    c = itertools.count(1)
    return lambda: f"{prefix}{next(c)}"


def graph(*items: Node | Edge, version: int = 1) -> GraphSnapshot:
    nodes = {i.id: i for i in items if isinstance(i, Node)}
    edges = {i.id: i for i in items if isinstance(i, Edge)}
    return GraphSnapshot(version=version, nodes=nodes, edges=edges)


def me(id: str = "me", name: str = "Abhiram") -> Node:
    return Node(id=id, kind="me", name=name)


def person(id: str, name: str | None = None, **kw) -> Node:
    return Node(id=id, kind="person", name=name or id.title(), **kw)


def company(id: str, name: str | None = None, **kw) -> Node:
    return Node(id=id, kind="company", name=name or id.title(), **kw)


def school(id: str, name: str | None = None, **kw) -> Node:
    return Node(id=id, kind="school", name=name or id.title(), **kw)


def edge(kind: str, src: str, dst: str, strength: int = 3, note: str = "", id: str | None = None):
    src, dst = canonical(kind, src, dst)
    return Edge(id=id or f"{kind}:{src}:{dst}", kind=kind, src=src, dst=dst,
                strength=strength, note=note)  # fmt: skip


def knows(a: str, b: str, strength: int = 3, note: str = "", id: str | None = None) -> Edge:
    return edge("KNOWS", a, b, strength, note, id)
