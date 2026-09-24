"""Turn ops into primitive mutations, plus the inverse mutations used for undo.

Approach: apply the ops to a working copy of the graph, check the invariants on the result,
then diff before/after. The diff is the forward mutation list; the reverse diff is the undo.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sixhops.core.model import Edge, GraphSnapshot, Node, canonical, new_id
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    DelEdge,
    DeleteEdge,
    DeleteNode,
    DelNode,
    ExistingRef,
    InvalidOps,
    MergeNodes,
    Mutation,
    NodeRef,
    Op,
    PutEdge,
    PutNode,
    UpdateEdge,
    UpdateNode,
)
from sixhops.core.validate import check_graph


@dataclass(frozen=True)
class Compiled:
    after: GraphSnapshot
    mutations: list[Mutation]
    inverse: list[Mutation]
    new_ids: dict[str, str]  # CreateNode key -> assigned node id


def compile_ops(
    g: GraphSnapshot, ops: Sequence[Op], make_id: Callable[[], str] = new_id
) -> Compiled:
    """Raises InvalidOps if an op is malformed or the resulting graph breaks an invariant."""
    work = _Working(g, make_id)
    for op in ops:
        work.apply(op)
    after = GraphSnapshot(version=g.version + 1, nodes=work.nodes, edges=work.edges)
    if errors := check_graph(after):
        raise InvalidOps(errors)
    return Compiled(after, diff(g, after), diff(after, g), dict(work.keys))


def diff(before: GraphSnapshot, after: GraphSnapshot) -> list[Mutation]:
    """Mutations that turn `before` into `after`, ordered so references stay valid."""
    return [
        *(PutNode(node=n) for i, n in after.nodes.items() if before.nodes.get(i) != n),
        *(PutEdge(edge=e) for i, e in after.edges.items() if before.edges.get(i) != e),
        *(DelEdge(edge_id=i) for i in before.edges if i not in after.edges),
        *(DelNode(node_id=i) for i in before.nodes if i not in after.nodes),
    ]


def apply_mutations(g: GraphSnapshot, mutations: Sequence[Mutation]) -> GraphSnapshot:
    nodes, edges = dict(g.nodes), dict(g.edges)
    for m in mutations:
        match m:
            case PutNode(node=n):
                nodes[n.id] = n
            case PutEdge(edge=e):
                edges[e.id] = e
            case DelEdge(edge_id=i):
                edges.pop(i, None)
            case DelNode(node_id=i):
                nodes.pop(i, None)
    return GraphSnapshot(version=g.version + 1, nodes=nodes, edges=edges)


class _Working:
    """A mutable copy of the graph that ops are applied to, one at a time."""

    def __init__(self, g: GraphSnapshot, make_id: Callable[[], str]):
        self.nodes: dict[str, Node] = dict(g.nodes)
        self.edges: dict[str, Edge] = dict(g.edges)
        self.keys: dict[str, str] = {}
        self.make_id = make_id

    def apply(self, op: Op) -> None:
        match op:
            case CreateNode():
                if op.key in self.keys:
                    raise InvalidOps([f"duplicate new-node key {op.key!r}"])
                node = Node(id=self.make_id(), **op.model_dump(exclude={"op", "key"}))
                self.nodes[node.id] = node
                self.keys[op.key] = node.id
            case UpdateNode():
                self._update_node(op)
            case MergeNodes():
                self._merge(op)
            case CreateEdge():
                src, dst = canonical(op.kind, self._resolve(op.src), self._resolve(op.dst))
                if self._find_edge(op.kind, src, dst):
                    raise InvalidOps([f"{op.kind} edge already exists"])
                edge = Edge(
                    id=self.make_id(),
                    kind=op.kind,
                    src=src,
                    dst=dst,
                    strength=op.strength,
                    note=op.note,
                )
                self.edges[edge.id] = edge
            case UpdateEdge():
                edge = self._edge(op.edge_id)
                kind = op.kind or edge.kind
                src, dst = canonical(kind, edge.src, edge.dst)
                self.edges[edge.id] = edge.model_copy(
                    update={"kind": kind, "src": src, "dst": dst}
                    | _given(strength=op.strength, note=op.note)
                )
            case DeleteNode():
                if self._node(op.node_id).kind == "me":
                    raise InvalidOps(["the 'me' node cannot be deleted"])
                for e in self._incident(op.node_id):
                    del self.edges[e.id]
                del self.nodes[op.node_id]
            case DeleteEdge():
                del self.edges[self._edge(op.edge_id).id]

    def _update_node(self, op: UpdateNode) -> None:
        node = self._node(op.node_id)
        name = op.name.strip() if op.name else node.name
        attrs = {k: v for k, v in (node.attrs | op.attrs).items() if v != ""}
        self.nodes[node.id] = node.model_copy(
            update={
                "name": name,
                "aliases": _aliases(name, _or(op.aliases, node.aliases), op.add_aliases),
                "attrs": attrs,
            }
            | _given(notes=op.notes)
        )

    def _merge(self, op: MergeNodes) -> None:
        keep, drop = self._node(op.keep_id), self._node(op.drop_id)
        if keep.id == drop.id:
            raise InvalidOps(["cannot merge a node into itself"])
        if drop.kind == "me":
            raise InvalidOps(["the 'me' node cannot be merged away; merge the other node into it"])
        if keep.kind != drop.kind and not (keep.kind == "me" and drop.kind == "person"):
            raise InvalidOps([f"cannot merge a {drop.kind} into a {keep.kind}"])

        self.nodes[keep.id] = keep.model_copy(
            update={
                "aliases": _aliases(keep.name, keep.aliases, [drop.name, *drop.aliases]),
                "attrs": drop.attrs | keep.attrs,  # keep's values win
                "notes": _join(keep.notes, drop.notes),
            }
        )
        for e in self._incident(drop.id):
            del self.edges[e.id]
            src = keep.id if e.src == drop.id else e.src
            dst = keep.id if e.dst == drop.id else e.dst
            if src == dst:  # keep and drop knew each other: nothing left to connect
                continue
            src, dst = canonical(e.kind, src, dst)
            if twin := self._find_edge(e.kind, src, dst):
                self.edges[twin.id] = twin.model_copy(
                    update={
                        "strength": max(twin.strength, e.strength),
                        "note": _join(twin.note, e.note),
                    }
                )
            else:
                self.edges[e.id] = e.model_copy(update={"src": src, "dst": dst})
        del self.nodes[drop.id]

    # --- lookups ---

    def _resolve(self, ref: NodeRef) -> str:
        if isinstance(ref, ExistingRef):
            return self._node(ref.id).id
        if ref.key not in self.keys:
            raise InvalidOps([f"unknown new-node key {ref.key!r}"])
        return self.keys[ref.key]

    def _node(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise InvalidOps([f"unknown node {node_id!r}"])
        return self.nodes[node_id]

    def _edge(self, edge_id: str) -> Edge:
        if edge_id not in self.edges:
            raise InvalidOps([f"unknown edge {edge_id!r}"])
        return self.edges[edge_id]

    def _incident(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if node_id in (e.src, e.dst)]

    def _find_edge(self, kind: str, src: str, dst: str) -> Edge | None:
        return next(
            (e for e in self.edges.values() if (e.kind, e.src, e.dst) == (kind, src, dst)), None
        )


def _given(**fields: object) -> dict[str, object]:
    return {k: v for k, v in fields.items() if v is not None}


def _or(value, default):
    return default if value is None else value


def _aliases(name: str, current: list[str], extra: list[str]) -> list[str]:
    seen = {name.casefold()}
    out: list[str] = []
    for alias in (a.strip() for a in [*current, *extra]):
        if alias and alias.casefold() not in seen:
            seen.add(alias.casefold())
            out.append(alias)
    return out


def _join(a: str, b: str) -> str:
    parts = [p for p in (a.strip(), b.strip()) if p]
    return "\n".join(dict.fromkeys(parts))
