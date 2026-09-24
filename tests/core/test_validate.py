from sixhops.core.model import Edge, GraphSnapshot
from sixhops.core.validate import check_graph
from tests.core.builders import company, edge, graph, knows, me, person, school


def test_valid_graph_has_no_errors():
    g = graph(
        me(), person("rahul"), company("razorpay"), school("iitm"),
        knows("me", "rahul"), edge("WORKS_AT", "rahul", "razorpay"),
        edge("STUDIED_AT", "me", "iitm"), edge("WORKED_AT", "me", "razorpay"),
    )  # fmt: skip
    assert check_graph(g) == []


def test_exactly_one_me():
    assert "exactly one 'me'" in check_graph(GraphSnapshot())[0]
    assert "found 2" in check_graph(graph(me("a"), me("b")))[0]


def test_edge_endpoint_kinds():
    g = graph(
        me(), person("p"), company("c"), school("s"),
        edge("WORKS_AT", "p", "s", id="e1"),     # works at a school
        edge("STUDIED_AT", "p", "c", id="e2"),   # studied at a company
        edge("KNOWS", "c", "p", id="e3"),        # company knows someone
        edge("WORKS_AT", "c", "c", id="e4"),     # company works at itself
    )  # fmt: skip
    errors = check_graph(g)
    assert any("e1" in e and "must be a company" in e for e in errors)
    assert any("e2" in e and "must be a school" in e for e in errors)
    assert any("e3" in e and "source" in e for e in errors)
    assert any("e4" in e and "itself" in e for e in errors)


def test_missing_endpoint():
    g = graph(me(), knows("me", "ghost"))
    assert "missing node" in check_graph(g)[0]


def test_knows_must_be_canonical_and_unique():
    g = graph(
        me(), person("a"), person("b"),
        Edge(id="x", kind="KNOWS", src="b", dst="a"),
        Edge(id="y", kind="KNOWS", src="a", dst="b"),
        Edge(id="z", kind="KNOWS", src="a", dst="b"),
    )  # fmt: skip
    errors = check_graph(g)
    assert any("canonical" in e for e in errors)
    assert any("duplicate" in e for e in errors)
