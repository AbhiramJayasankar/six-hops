import pytest

from sixhops.app.services.manual import connect_ops, pick_node
from sixhops.core.compile import compile_ops
from sixhops.core.ops import InvalidOps
from tests.core.builders import company, graph, me, person, school

G = graph(
    me(),
    person("p1", "Priya", aliases=["PS"]),
    person("p2", "Priya"),
    company("c", "Razorpay"),
    school("s", "IIT Madras"),
)


def test_pick_by_id_suffix_name_or_alias():
    assert pick_node(G, "Razorpay (#c)", {"company"}).id == "c"
    assert pick_node(G, "razorpay", {"company"}).id == "c"
    assert pick_node(G, "ps", {"person"}).id == "p1"
    assert pick_node(G, "Razorpay (#c)", {"person"}) is None
    assert pick_node(G, "Nobody", {"person"}) is None


def test_ambiguous_name_asks_to_pick():
    with pytest.raises(InvalidOps, match="several nodes"):
        pick_node(G, "Priya", {"person"})


def test_connect_from_institution_points_edge_at_it():
    ops = connect_ops(G, "s", "STUDIED_AT", "Arjun", 3, "batchmate")
    after = compile_ops(G, ops).after
    (edge,) = after.edges.values()
    assert after.nodes[edge.src].name == "Arjun"
    assert edge.dst == "s" and edge.note == "batchmate"


def test_connect_person_to_new_school():
    ops = connect_ops(G, "me", "STUDIED_AT", "NIT Trichy", 2, "")
    after = compile_ops(G, ops).after
    new = next(n for n in after.nodes.values() if n.name == "NIT Trichy")
    assert new.kind == "school"


def test_institution_cannot_know():
    with pytest.raises(InvalidOps, match="can't KNOW"):
        connect_ops(G, "c", "KNOWS", "Priya (#p1)", 3, "")
