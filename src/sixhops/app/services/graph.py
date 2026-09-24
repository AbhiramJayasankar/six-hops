"""Graph use-cases: every write is ops -> compile (validate) -> GraphStore.commit."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from sixhops.core.compile import Compiled, apply_mutations, compile_ops, diff
from sixhops.core.model import Edge, GraphSnapshot, Node, canonical
from sixhops.core.ops import InvalidOps, Op
from sixhops.core.validate import check_graph
from sixhops.ports.graph_store import ChangeRecord, GraphStore, NothingToUndo, VersionConflict


class GraphDocument(BaseModel):
    """The JSON import/export format."""

    format: Literal["6hops.graph"] = "6hops.graph"
    schema_version: Literal[1] = 1
    exported_at: datetime | None = None
    nodes: list[Node]
    edges: list[Edge]


class GraphService:
    def __init__(self, store: GraphStore):
        self.store = store

    def snapshot(self) -> GraphSnapshot:
        return self.store.snapshot()

    def apply(
        self, ops: Sequence[Op], *, summary: str, expected_version: int | None = None
    ) -> Compiled:
        """Validate and commit ops. Returns the compiled change (with ids assigned to new nodes)."""
        g = self.store.snapshot()
        if expected_version is not None and expected_version != g.version:
            raise VersionConflict(f"expected graph version {expected_version}, at {g.version}")
        compiled = compile_ops(g, ops)
        if compiled.mutations:
            self.store.commit(
                compiled.mutations,
                expected_version=g.version,
                inverse=compiled.inverse,
                summary=summary,
            )
        return compiled

    def export(self) -> GraphDocument:
        g = self.store.snapshot()
        return GraphDocument(
            exported_at=datetime.now(UTC),
            nodes=list(g.nodes.values()),
            edges=list(g.edges.values()),
        )

    def replace(self, doc: GraphDocument) -> int:
        """Replace the whole graph with `doc`. Recorded in history, so it can be undone."""
        g = self.store.snapshot()
        edges = [_canonical_edge(e) for e in doc.edges]
        new = GraphSnapshot(
            version=g.version + 1,
            nodes={n.id: n for n in doc.nodes},
            edges={e.id: e for e in edges},
        )
        errors = check_graph(new)
        if len(new.nodes) != len(doc.nodes) or len(new.edges) != len(doc.edges):
            errors.append("duplicate ids in import")
        if errors:
            raise InvalidOps(errors)
        return self.store.commit(
            diff(g, new),
            expected_version=g.version,
            inverse=diff(new, g),
            summary=f"Imported graph ({len(new.nodes)} nodes, {len(new.edges)} edges)",
        )

    def history(self, limit: int = 20) -> list[ChangeRecord]:
        return self.store.history(limit)

    def undo(self) -> ChangeRecord:
        g = self.store.snapshot()
        latest = self.store.history(1)
        if not latest:
            raise NothingToUndo()
        if errors := check_graph(apply_mutations(g, latest[0].inverse)):
            raise InvalidOps(errors)
        return self.store.undo_last(expected_version=g.version)


def _canonical_edge(e: Edge) -> Edge:
    src, dst = canonical(e.kind, e.src, e.dst)
    return e.model_copy(update={"src": src, "dst": dst})
