import pytest

from sixhops.core.compile import apply_mutations, compile_ops
from sixhops.core.ops import InvalidOps, MergeNodes
from tests.core.builders import company, edge, graph, knows, me, person


def merge(g, keep, drop):
    return compile_ops(g, [MergeNodes(keep_id=keep, drop_id=drop)])


def test_merge_repoints_edges_and_unions_identity():
    g = graph(
        me(),
        person("ps", "Priya S", attrs={"title": "SDE2"}, notes="met at PyCon"),
        person("p", "Priya", aliases=["Pri"], attrs={"title": "SDE", "email": "p@x.in"}),
        person("rahul"),
        company("rzp"),
        knows("rahul", "p", 4, "ex-colleague"),
        edge("WORKS_AT", "p", "rzp"),
    )
    after = merge(g, "ps", "p").after
    assert "p" not in after.nodes
    ps = after.nodes["ps"]
    assert ps.aliases == ["Priya", "Pri"]
    assert ps.attrs == {"title": "SDE2", "email": "p@x.in"}  # keep's values win
    assert ps.notes == "met at PyCon"
    assert after.find_edge("KNOWS", "rahul", "ps").note == "ex-colleague"
    assert after.find_edge("WORKS_AT", "ps", "rzp") is not None
    assert all("p" not in (e.src, e.dst) for e in after.edges.values())


def test_merge_collapses_duplicate_edges_keeping_max_strength():
    g = graph(
        me(), person("a"), person("b"), person("rahul"),
        knows("rahul", "a", 2, "school"), knows("rahul", "b", 4, "work"),
    )  # fmt: skip
    after = merge(g, "a", "b").after
    (e,) = after.edges.values()
    assert e.strength == 4
    assert e.note == "school\nwork"


def test_merge_drops_edge_between_the_two_nodes():
    g = graph(me(), person("a"), person("b"), knows("a", "b"))
    assert merge(g, "a", "b").after.edges == {}


def test_merge_keeps_canonical_order_after_repoint():
    # "z" folds into "a": the edge z-m must become a-m, stored as (a, m).
    g = graph(me("m"), person("a"), person("z"), knows("m", "z"))
    (e,) = merge(g, "a", "z").after.edges.values()
    assert (e.src, e.dst) == ("a", "m")


def test_person_can_be_merged_into_me():
    g = graph(me(), person("dup", "Abhiram J"), company("c"), edge("WORKED_AT", "dup", "c"))
    after = merge(g, "me", "dup").after
    assert after.nodes["me"].aliases == ["Abhiram J"]
    assert after.find_edge("WORKED_AT", "me", "c") is not None


@pytest.mark.parametrize(
    "keep, drop, message",
    [
        ("p", "me", "cannot be merged away"),
        ("p", "c", "cannot merge a company into a person"),
        ("p", "p", "into itself"),
        ("p", "ghost", "unknown node"),
    ],
)
def test_invalid_merges(keep, drop, message):
    g = graph(me(), person("p"), company("c"))
    with pytest.raises(InvalidOps, match=message):
        merge(g, keep, drop)


def test_merge_is_undoable():
    g = graph(
        me(), person("a"), person("b"), person("r"), company("c"),
        knows("r", "a", 2), knows("r", "b", 5), knows("a", "b"), edge("WORKS_AT", "b", "c"),
    )  # fmt: skip
    out = merge(g, "a", "b")
    back = apply_mutations(out.after, out.inverse)
    assert (back.nodes, back.edges) == (g.nodes, g.edges)
