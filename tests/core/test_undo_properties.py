"""Property tests: for random op sequences, applying a change and then its inverse restores the
exact previous graph content (nodes and edges). The version number is not restored on purpose:
it only ever increases, because it is the optimistic-concurrency token.

Ops are drawn against the current graph so most of them are meaningful; ops that the core rejects
(InvalidOps) are skipped, exactly as the app would reject them.
"""

import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sixhops.adapters.store.sqlite import SqliteGraphStore
from sixhops.core.compile import apply_mutations, compile_ops, diff
from sixhops.core.model import GraphSnapshot
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    ExistingRef,
    InvalidOps,
    MergeNodes,
    NewRef,
    UpdateEdge,
    UpdateNode,
)
from tests.core.builders import company, counter, edge, graph, knows, me, person, school

NAMES = ["Priya", "Priya S", "Rahul", "Anita", "Razorpay", "Flipkart", "IIT Madras", "Sam"]
TEXT = st.sampled_from(["", "college", "ex-colleague", "met at PyCon", "SDE2"])
EDGE_KINDS = st.sampled_from(["KNOWS", "WORKS_AT", "WORKED_AT", "STUDIED_AT"])
STRENGTH = st.integers(min_value=1, max_value=5)


def content(g: GraphSnapshot):
    return g.nodes, g.edges


SEED = graph(
    me(), person("p1", "Priya"), person("p2", "Priya S"), person("p3", "Rahul"),
    company("c1", "Razorpay"), company("c2", "Flipkart"), school("s1", "IIT Madras"),
    knows("me", "p1", 4), knows("p1", "p3", 2), knows("p2", "p3", 5),
    edge("WORKS_AT", "p1", "c1"), edge("WORKED_AT", "p2", "c2"), edge("STUDIED_AT", "me", "s1"),
)  # fmt: skip

DST_KIND = {"KNOWS": {"me", "person"}, "WORKS_AT": {"company"}, "WORKED_AT": {"company"},
            "STUDIED_AT": {"school"}}  # fmt: skip


@st.composite
def change(draw, g: GraphSnapshot):
    """One change set (1-3 ops) drawn against graph `g`, biased towards valid edge ops."""
    node_ids = sorted(g.nodes)
    edge_ids = sorted(g.edges)
    people = [i for i in node_ids if g.nodes[i].kind in {"me", "person"}]

    @st.composite
    def create_edge(draw, new_keys: dict[str, str]):
        kind = draw(EDGE_KINDS)
        dst_pool = [ExistingRef(id=i) for i in node_ids if g.nodes[i].kind in DST_KIND[kind]]
        dst_pool += [NewRef(key=k) for k, kd in new_keys.items() if kd in DST_KIND[kind]]
        src_pool = [ExistingRef(id=i) for i in people]
        src_pool += [NewRef(key=k) for k, kd in new_keys.items() if kd == "person"]
        if not dst_pool or not src_pool:
            dst_pool = src_pool = [ExistingRef(id=i) for i in node_ids]
        return CreateEdge(kind=kind, src=draw(st.sampled_from(src_pool)),
                          dst=draw(st.sampled_from(dst_pool)), strength=draw(STRENGTH),
                          note=draw(TEXT))  # fmt: skip

    def one_op(new_keys: dict[str, str]):
        options = [
            st.builds(
                CreateNode,
                key=st.just(f"k{len(new_keys)}"),
                kind=st.sampled_from(["person", "company", "school"]),
                name=st.sampled_from(NAMES),
                aliases=st.lists(st.sampled_from(NAMES), max_size=2),
                attrs=st.dictionaries(st.sampled_from(["title", "email"]), TEXT, max_size=2),
            ),
            st.builds(
                UpdateNode,
                node_id=st.sampled_from(node_ids),
                name=st.none() | st.sampled_from(NAMES),
                aliases=st.none() | st.lists(st.sampled_from(NAMES), max_size=2),
                add_aliases=st.lists(st.sampled_from(NAMES), max_size=2),
                attrs=st.dictionaries(st.sampled_from(["title", "email"]), TEXT, max_size=2),
                notes=st.none() | TEXT,
            ),
            # Merges are the riskiest op, so draw them often and mostly between people.
            st.builds(MergeNodes, keep_id=st.sampled_from(people), drop_id=st.sampled_from(people)),
            st.builds(
                MergeNodes, keep_id=st.sampled_from(node_ids), drop_id=st.sampled_from(node_ids)
            ),
            create_edge(new_keys),
            create_edge(new_keys),
            st.builds(DeleteNode, node_id=st.sampled_from(node_ids)),
        ]
        if edge_ids:
            options += [
                st.builds(
                    UpdateEdge,
                    edge_id=st.sampled_from(edge_ids),
                    kind=st.none() | EDGE_KINDS,
                    strength=st.none() | STRENGTH,
                    note=st.none() | TEXT,
                ),
                st.builds(DeleteEdge, edge_id=st.sampled_from(edge_ids)),
            ]
        return st.one_of(options)

    ops, new_keys = [], {}
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        op = draw(one_op(new_keys))
        if isinstance(op, CreateNode):
            new_keys[op.key] = op.kind
        ops.append(op)
    return ops


def run_changes(data, steps: int):
    """Yield (before, compiled) for each accepted change in a random sequence."""
    g = SEED
    ids = counter("n")
    for _ in range(steps):
        ops = data.draw(change(g))
        try:
            compiled = compile_ops(g, ops, make_id=ids)
        except InvalidOps:
            continue
        yield g, compiled
        g = compiled.after


@settings(deadline=None)
@given(st.data())
def test_each_change_is_exactly_undone_by_its_inverse(data):
    for before, compiled in run_changes(data, steps=12):
        forward = apply_mutations(before, compiled.mutations)
        assert content(forward) == content(compiled.after)
        assert content(apply_mutations(compiled.after, compiled.inverse)) == content(before)


@settings(deadline=None)
@given(st.data())
def test_undoing_a_whole_sequence_walks_back_to_the_start(data):
    changes = list(run_changes(data, steps=10))
    g = changes[-1][1].after if changes else SEED
    for before, compiled in reversed(changes):
        assert content(g) == content(compiled.after)
        g = apply_mutations(g, compiled.inverse)
        assert content(g) == content(before)
    assert content(g) == content(SEED)


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_store_repeated_undo_restores_every_earlier_snapshot(data):
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteGraphStore(f"sqlite:///{tmp}/g.db")
        store.ensure_me("Abhiram")
        _load(store, SEED)
        g = store.snapshot()
        history = [content(g)]
        ids = counter("n")
        for _ in range(8):
            ops = data.draw(change(g))
            try:
                compiled = compile_ops(g, ops, make_id=ids)
            except InvalidOps:
                continue
            if not compiled.mutations:
                continue  # the app does not record no-op changes
            store.commit(
                compiled.mutations,
                expected_version=g.version,
                inverse=compiled.inverse,
                summary="step",
            )
            g = store.snapshot()
            history.append(content(g))

        assert content(store.snapshot()) == history[-1]
        for expected in reversed(history[:-1]):
            store.undo_last(expected_version=store.snapshot().version)
            assert content(store.snapshot()) == expected
        assert store.history() == []
        store.engine.dispose()


def _load(store: SqliteGraphStore, g: GraphSnapshot) -> None:
    """Load the seed graph as one change, then clear it from history so undo stops there."""
    current = store.snapshot()
    store.commit(diff(current, g), expected_version=current.version, inverse=[], summary="seed")
    with store.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM changes")
