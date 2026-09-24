"""GraphStore on SQLAlchemy Core. Developed against SQLite; written to stay portable to Postgres."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import (
    JSON,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import make_url

from sixhops.core.model import Edge, GraphSnapshot, Node
from sixhops.core.ops import DelEdge, DelNode, Mutation, PutEdge, PutNode
from sixhops.ports.graph_store import ChangeRecord, NothingToUndo, VersionConflict

HISTORY_LIMIT = 100  # undo depth kept on disk

metadata = MetaData()
meta_t = Table(
    "meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Integer, nullable=False),
)
nodes_t = Table(
    "nodes",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("name", String, nullable=False),
    Column("aliases", JSON, nullable=False),
    Column("attrs", JSON, nullable=False),
    Column("notes", Text, nullable=False),
)
edges_t = Table(
    "edges",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("src", String, ForeignKey("nodes.id"), nullable=False),
    Column("dst", String, ForeignKey("nodes.id"), nullable=False),
    Column("strength", Integer, nullable=False),
    Column("note", Text, nullable=False),
    UniqueConstraint("kind", "src", "dst"),
)
changes_t = Table(
    "changes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version_before", Integer, nullable=False),
    Column("version_after", Integer, nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("inverse", Text, nullable=False),  # JSON list of mutations
)

pending_t = Table(
    "pending_changes",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("status", String, nullable=False),
    Column("payload", Text, nullable=False),  # JSON, owned by the app's ChangeSet service
)

_mutations = TypeAdapter(list[Mutation])


class SqliteGraphStore:
    def __init__(self, database_url: str):
        url = make_url(database_url)
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)
        if url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _sqlite_pragmas)
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            if conn.scalar(select(meta_t.c.value).where(meta_t.c.key == "version")) is None:
                conn.execute(insert(meta_t).values(key="version", value=0))

    def ensure_me(self, name: str) -> None:
        """Create the single 'me' node on first run. Not recorded in history."""
        with self.engine.begin() as conn:
            if conn.scalar(select(nodes_t.c.id).where(nodes_t.c.kind == "me")) is None:
                _apply(conn, [PutNode(node=Node(id="me", kind="me", name=name))])

    # --- GraphStore port ---

    def snapshot(self) -> GraphSnapshot:
        with self.engine.connect() as conn:
            version = conn.scalar(select(meta_t.c.value).where(meta_t.c.key == "version"))
            nodes = [Node(**row._mapping) for row in conn.execute(select(nodes_t))]
            edges = [Edge(**row._mapping) for row in conn.execute(select(edges_t))]
        return GraphSnapshot(
            version=version, nodes={n.id: n for n in nodes}, edges={e.id: e for e in edges}
        )

    def commit(
        self,
        mutations: Sequence[Mutation],
        *,
        expected_version: int,
        inverse: Sequence[Mutation],
        summary: str,
    ) -> int:
        with self.engine.begin() as conn:
            new_version = _bump_version(conn, expected_version)
            _apply(conn, mutations)
            conn.execute(
                insert(changes_t).values(
                    version_before=expected_version,
                    version_after=new_version,
                    summary=summary,
                    created_at=datetime.now(UTC),
                    inverse=_mutations.dump_json(list(inverse)).decode(),
                )
            )
            _prune_history(conn)
        return new_version

    def history(self, limit: int = 20) -> list[ChangeRecord]:
        query = select(changes_t).order_by(changes_t.c.id.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [_record(row) for row in conn.execute(query)]

    def undo_last(self, *, expected_version: int) -> ChangeRecord:
        with self.engine.begin() as conn:
            row = conn.execute(select(changes_t).order_by(changes_t.c.id.desc()).limit(1)).first()
            if row is None:
                raise NothingToUndo()
            record = _record(row)
            if record.version_after != expected_version:
                raise VersionConflict("graph changed outside the undo history")
            new_version = _bump_version(conn, expected_version)
            _apply(conn, record.inverse)
            conn.execute(delete(changes_t).where(changes_t.c.id == record.id))
            # The graph now matches the state after the previous change, so re-anchor that record
            # to the new version; repeated undos can then walk further back.
            previous = select(func.max(changes_t.c.id)).scalar_subquery()
            conn.execute(
                update(changes_t)
                .where(changes_t.c.id == previous)
                .values(version_after=new_version)
            )
        return record


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def _bump_version(conn: Connection, expected: int) -> int:
    """Compare-and-swap on the graph version; the only concurrency control needed."""
    result = conn.execute(
        update(meta_t)
        .where(meta_t.c.key == "version", meta_t.c.value == expected)
        .values(value=meta_t.c.value + 1)
    )
    if result.rowcount != 1:
        raise VersionConflict(f"expected graph version {expected}")
    return expected + 1


def _apply(conn: Connection, mutations: Sequence[Mutation]) -> None:
    """Apply in an order that never trips a constraint mid-transaction:
    remove changed/deleted edges, upsert nodes, re-insert edges, then delete nodes."""
    put_nodes = [m.node for m in mutations if isinstance(m, PutNode)]
    put_edges = [m.edge for m in mutations if isinstance(m, PutEdge)]
    del_edges = [m.edge_id for m in mutations if isinstance(m, DelEdge)]
    del_nodes = [m.node_id for m in mutations if isinstance(m, DelNode)]

    if touched := del_edges + [e.id for e in put_edges]:
        conn.execute(delete(edges_t).where(edges_t.c.id.in_(touched)))
    for n in put_nodes:
        values = n.model_dump()
        if conn.execute(update(nodes_t).where(nodes_t.c.id == n.id).values(values)).rowcount == 0:
            conn.execute(insert(nodes_t).values(values))
    if put_edges:
        conn.execute(insert(edges_t), [e.model_dump() for e in put_edges])
    if del_nodes:
        conn.execute(delete(nodes_t).where(nodes_t.c.id.in_(del_nodes)))


def _prune_history(conn: Connection) -> None:
    keep = select(changes_t.c.id).order_by(changes_t.c.id.desc()).limit(HISTORY_LIMIT)
    conn.execute(delete(changes_t).where(changes_t.c.id.not_in(keep.scalar_subquery())))


def _record(row) -> ChangeRecord:
    data = dict(row._mapping)
    data["inverse"] = json.loads(data["inverse"])
    return ChangeRecord(**data)


class SqlitePendingChanges:
    """Proposed-but-not-applied ChangeSets (chat, imports), kept across restarts.
    Stored as opaque JSON: this is app state, not part of the GraphStore port."""

    def __init__(self, store: SqliteGraphStore):
        self.engine = store.engine

    def put(self, change_id: str, status: str, payload: str) -> None:
        values = {"status": status, "payload": payload}
        with self.engine.begin() as conn:
            updated = conn.execute(
                update(pending_t).where(pending_t.c.id == change_id).values(values)
            ).rowcount
            if not updated:
                conn.execute(
                    insert(pending_t).values(id=change_id, created_at=datetime.now(UTC), **values)
                )

    def get(self, change_id: str) -> str | None:
        with self.engine.connect() as conn:
            return conn.scalar(select(pending_t.c.payload).where(pending_t.c.id == change_id))
