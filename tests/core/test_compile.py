import pytest

from sixhops.core.compile import apply_mutations, compile_ops
from sixhops.core.ops import (
    CreateEdge,
    CreateNode,
    DeleteEdge,
    DeleteNode,
    ExistingRef,
    InvalidOps,
    NewRef,
    UpdateEdge,
    UpdateNode,
)
from tests.core.builders import company, counter, edge, graph, knows, me, person


def run(g, *ops):
    return compile_ops(g, ops, make_id=counter("n"))


def test_create_nodes_and_edges_by_new_ref():
    g = graph(me())
    out = run(
        g,
        CreateNode(key="priya", kind="person", name="Priya"),
        CreateNode(key="rzp", kind="company", name="Razorpay"),
        CreateEdge(kind="KNOWS", src=ref("me"), dst=NewRef(key="priya"), strength=4),
        CreateEdge(kind="WORKS_AT", src=NewRef(key="priya"), dst=NewRef(key="rzp")),
    )
    priya, rzp = out.new_ids["priya"], out.new_ids["rzp"]
    assert out.after.nodes[priya].name == "Priya"
    assert out.after.find_edge("KNOWS", priya, "me").strength == 4
    assert out.after.find_edge("WORKS_AT", priya, rzp) is not None
    assert out.after.version == g.version + 1


def test_knows_is_stored_canonically_whichever_way_round():
    g = graph(me(), person("zed"), person("amy"))
    out = run(g, CreateEdge(kind="KNOWS", src=ref("zed"), dst=ref("amy")))
    (e,) = out.after.edges.values()
    assert (e.src, e.dst) == ("amy", "zed")


def ref(node_id):
    return ExistingRef(id=node_id)


@pytest.mark.parametrize(
    "op, message",
    [
        (UpdateNode(node_id="ghost", name="x"), "unknown node"),
        (DeleteEdge(edge_id="ghost"), "unknown edge"),
        (CreateEdge(kind="KNOWS", src=NewRef(key="nope"), dst=ref("me")), "unknown new-node key"),
        (CreateEdge(kind="KNOWS", src=ref("me"), dst=ref("rahul")), "already exists"),
        (CreateEdge(kind="WORKS_AT", src=ref("me"), dst=ref("rahul")), "must be a company"),
        (CreateEdge(kind="KNOWS", src=ref("me"), dst=ref("me")), "itself"),
        (DeleteNode(node_id="me"), "cannot be deleted"),
        (UpdateEdge(edge_id="KNOWS:me:rahul", kind="WORKS_AT"), "must be a company"),
    ],
)
def test_invalid_ops_are_rejected(op, message):
    g = graph(me(), person("rahul"), knows("me", "rahul"))
    with pytest.raises(InvalidOps, match=message):
        run(g, op)


def test_duplicate_new_key_rejected():
    with pytest.raises(InvalidOps, match="duplicate new-node key"):
        run(
            graph(me()),
            CreateNode(key="a", kind="person", name="A"),
            CreateNode(key="a", kind="person", name="B"),
        )


def test_update_node_merges_attrs_and_dedupes_aliases():
    g = graph(me(), person("p", "Priya S", aliases=["PS"], attrs={"title": "SDE", "email": "x"}))
    out = run(
        g,
        UpdateNode(
            node_id="p",
            add_aliases=["ps", "Priya", " ", "priya s"],
            attrs={"title": "SDE2", "email": ""},
        ),
    )
    node = out.after.nodes["p"]
    assert node.aliases == ["PS", "Priya"]
    assert node.attrs == {"title": "SDE2"}


def test_update_edge_changes_kind_and_strength():
    g = graph(me(), person("p"), company("c"), edge("WORKS_AT", "p", "c", id="e"))
    out = run(g, UpdateEdge(edge_id="e", kind="WORKED_AT", strength=5))
    assert out.after.edges["e"].kind == "WORKED_AT"
    assert out.after.edges["e"].strength == 5


def test_delete_node_removes_its_edges():
    g = graph(me(), person("p"), company("c"), knows("me", "p"), edge("WORKS_AT", "p", "c"))
    out = run(g, DeleteNode(node_id="p"))
    assert "p" not in out.after.nodes
    assert out.after.edges == {}


def test_mutations_and_inverse_round_trip():
    g = graph(me(), person("p"), company("c"), knows("me", "p"), edge("WORKS_AT", "p", "c"))
    out = run(
        g,
        CreateNode(key="q", kind="person", name="Q"),
        CreateEdge(kind="KNOWS", src=NewRef(key="q"), dst=ref("p")),
        UpdateNode(node_id="p", name="Priya"),
        DeleteNode(node_id="c"),
    )
    forward = apply_mutations(g, out.mutations)
    assert (forward.nodes, forward.edges) == (out.after.nodes, out.after.edges)
    back = apply_mutations(out.after, out.inverse)
    assert (back.nodes, back.edges) == (g.nodes, g.edges)


def test_no_op_produces_no_mutations():
    g = graph(me(), person("p"))
    out = run(g, UpdateNode(node_id="p"))
    assert out.mutations == [] and out.inverse == []
