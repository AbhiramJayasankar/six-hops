"""Contract tests for GraphStore. A future adapter (e.g. Neo4j) should pass the same tests."""

import pytest

from sixhops.adapters.store.sqlite import SqliteGraphStore
from sixhops.core.compile import compile_ops
from sixhops.core.ops import CreateEdge, CreateNode, ExistingRef, MergeNodes, NewRef
from sixhops.ports.graph_store import NothingToUndo, VersionConflict


@pytest.fixture
def store(tmp_path):
    s = SqliteGraphStore(f"sqlite:///{tmp_path / 'nested' / 'g.db'}")
    s.ensure_me("Abhiram")
    return s


def commit(store, *ops, summary="test"):
    g = store.snapshot()
    out = compile_ops(g, ops)
    store.commit(out.mutations, expected_version=g.version, inverse=out.inverse, summary=summary)
    return out


def test_bootstrap_creates_me_once(store):
    store.ensure_me("Someone else")
    g = store.snapshot()
    assert g.version == 0
    assert [n.name for n in g.nodes.values()] == ["Abhiram"]
    assert store.history() == []


def test_commit_persists_and_bumps_version(store):
    out = commit(
        store,
        CreateNode(key="p", kind="person", name="Priya", attrs={"title": "SDE"}),
        CreateEdge(kind="KNOWS", src=ExistingRef(id="me"), dst=NewRef(key="p"), note="college"),
    )
    g = store.snapshot()
    assert g.version == 1
    assert (g.nodes, g.edges) == (out.after.nodes, out.after.edges)


def test_stale_version_is_rejected(store):
    out = compile_ops(store.snapshot(), [CreateNode(key="p", kind="person", name="P")])
    with pytest.raises(VersionConflict):
        store.commit(out.mutations, expected_version=7, inverse=out.inverse, summary="x")
    assert store.snapshot().version == 0


def test_history_and_undo_walk_back(store):
    before = store.snapshot()
    commit(store, CreateNode(key="a", kind="person", name="A"), summary="add A")
    middle = store.snapshot()
    commit(store, CreateNode(key="b", kind="person", name="B"), summary="add B")

    assert [r.summary for r in store.history()] == ["add B", "add A"]
    assert store.undo_last(expected_version=2).summary == "add B"
    assert store.snapshot().nodes == middle.nodes
    store.undo_last(expected_version=3)
    after = store.snapshot()
    assert after.nodes == before.nodes and after.version == 4
    with pytest.raises(NothingToUndo):
        store.undo_last(expected_version=4)


def test_undo_requires_current_version(store):
    commit(store, CreateNode(key="a", kind="person", name="A"))
    with pytest.raises(VersionConflict):
        store.undo_last(expected_version=0)


def test_merge_with_edge_collapse_commits_and_undoes(store):
    out = commit(
        store,
        CreateNode(key="a", kind="person", name="Priya"),
        CreateNode(key="b", kind="person", name="Priya S"),
        CreateNode(key="r", kind="person", name="Rahul"),
        CreateEdge(kind="KNOWS", src=NewRef(key="r"), dst=NewRef(key="a"), strength=2),
        CreateEdge(kind="KNOWS", src=NewRef(key="r"), dst=NewRef(key="b"), strength=5),
    )
    before = store.snapshot()
    ids = out.new_ids
    commit(store, MergeNodes(keep_id=ids["b"], drop_id=ids["a"]))
    merged = store.snapshot()
    assert ids["a"] not in merged.nodes
    assert [e.strength for e in merged.edges.values()] == [5]

    store.undo_last(expected_version=merged.version)
    restored = store.snapshot()
    assert (restored.nodes, restored.edges) == (before.nodes, before.edges)


def test_data_survives_reopen(store, tmp_path):
    commit(store, CreateNode(key="a", kind="person", name="A"))
    reopened = SqliteGraphStore(str(store.engine.url))
    assert reopened.snapshot() == store.snapshot()
