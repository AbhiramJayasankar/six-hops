import pytest

from sixhops.core.paths import RankingConfig, rank
from tests.core.builders import company, edge, graph, knows, me, person, school


def names(result):
    return [list(p.nodes) for p in result.paths]


def test_shortest_and_strongest_differ_and_are_tagged():
    g = graph(
        me(), person("a"), person("b"), person("c"), company("t"),
        knows("me", "a", 1), edge("WORKS_AT", "a", "t", 3),              # 2 hops, weak
        knows("me", "b", 5), knows("b", "c", 5), edge("WORKS_AT", "c", "t", 5),  # 3 hops, strong
    )  # fmt: skip
    strong, short = rank(g, "t", RankingConfig(k=1)).paths
    assert list(strong.nodes) == ["me", "b", "c", "t"]
    assert strong.tags == {"strongest"}
    assert strong.score == pytest.approx(0.95**3)
    assert list(short.nodes) == ["me", "a", "t"]
    assert short.tags == {"shortest"}
    assert short.score == pytest.approx(0.2 * 0.6)
    assert (strong.first_hop, strong.referrer) == ("b", "c")


def test_worked_at_is_discounted():
    g = graph(
        me(), person("a"), person("b"), company("t"),
        knows("me", "a", 4), edge("WORKED_AT", "a", "t"),
        knows("me", "b", 4), edge("WORKS_AT", "b", "t"),
    )  # fmt: skip
    current, former = rank(g, "t").paths
    assert current.referrer == "b" and former.referrer == "a"
    assert former.score == pytest.approx(current.score * 0.5)


def test_best_affiliation_edge_is_used_when_both_exist():
    g = graph(
        me(), person("a"), company("t"),
        knows("me", "a"),
        edge("WORKED_AT", "a", "t", id="old"), edge("WORKS_AT", "a", "t", id="new"),
    )  # fmt: skip
    (path,) = rank(g, "t").paths
    assert path.edges[-1] == "new"


def test_unreachable_target_reports_people_there():
    g = graph(
        me(), person("a"), person("x"), company("t"), knows("me", "a"), edge("WORKS_AT", "x", "t")
    )
    result = rank(g, "t")
    assert result.paths == [] and result.people_at_target == 1 and result.min_hops is None


def test_isolated_me_and_empty_company():
    g = graph(me(), person("x"), company("t"), company("u"), edge("WORKS_AT", "x", "t"))
    assert rank(g, "t").paths == []
    assert rank(g, "u").people_at_target == 0


def test_target_must_be_a_company():
    g = graph(me(), person("a"), school("s"))
    for target in ("a", "s", "ghost"):
        with pytest.raises(ValueError):
            rank(g, target)


def test_me_directly_at_target_suppresses_longer_paths():
    g = graph(
        me(), person("a"), company("t"),
        edge("WORKS_AT", "me", "t"), knows("me", "a"), edge("WORKS_AT", "a", "t"),
    )  # fmt: skip
    result = rank(g, "t")
    assert names(result) == [["me", "t"]]
    assert result.paths[0].first_hop is None and result.paths[0].referrer == "me"
    assert result.people_at_target == 1


def test_paths_through_someone_already_at_target_are_skipped():
    g = graph(
        me(), person("p"), person("q"), company("t"),
        knows("me", "p", 5), knows("p", "q", 5),
        edge("WORKS_AT", "p", "t"), edge("WORKS_AT", "q", "t"),
    )  # fmt: skip
    assert names(rank(g, "t")) == [["me", "p", "t"]]


def chain(length):
    """me - p1 - p2 ... p(length-1) - t : a path of exactly `length` hops."""
    people = [person(f"p{i}") for i in range(1, length)]
    ids = ["me", *(p.id for p in people)]
    links = [knows(a, b) for a, b in zip(ids, ids[1:], strict=False)]
    return graph(me(), *people, company("t"), *links, edge("WORKS_AT", ids[-1], "t"))


def test_default_cap_is_four_hops_and_longer_is_opt_in():
    g = chain(5)
    default = rank(g, "t")
    assert default.paths == [] and default.min_hops == 5
    longer = rank(g, "t", RankingConfig(max_hops=6))
    assert [p.hops for p in longer.paths] == [5]


def test_hard_cap_is_six_hops():
    assert rank(chain(6), "t", RankingConfig(max_hops=99)).paths
    assert rank(chain(7), "t", RankingConfig(max_hops=99)).paths == []


def test_institution_hops_only_when_enabled():
    g = graph(
        me(), person("anita"), person("rahul"), school("iitm"), company("fk"), company("t"),
        edge("STUDIED_AT", "me", "iitm"), edge("STUDIED_AT", "anita", "iitm"),
        edge("WORKS_AT", "anita", "t", 3),
        edge("WORKED_AT", "me", "fk"), edge("WORKED_AT", "rahul", "fk"),
        edge("WORKS_AT", "rahul", "t", 5),
    )  # fmt: skip
    assert rank(g, "t").paths == []
    via = rank(g, "t", RankingConfig(via_institutions=True))
    assert names(via) == [["me", "fk", "rahul", "t"], ["me", "iitm", "anita", "t"]]
    assert via.paths[0].score == pytest.approx(0.3 * 0.95)
    assert via.paths[1].score == pytest.approx(0.3 * 0.6)


def test_k_limits_each_list():
    people = [person(f"p{i}") for i in range(10)]
    g = graph(
        me(), company("t"), *people,
        *(knows("me", p.id, 1 + i % 5) for i, p in enumerate(people)),
        *(edge("WORKS_AT", p.id, "t") for p in people),
    )  # fmt: skip
    result = rank(g, "t", RankingConfig(k=3))
    assert len(result.paths) <= 6
    strongest = [p for p in result.paths if "strongest" in p.tags]
    assert len(strongest) == 3
    assert sorted(g.edges[p.edges[0]].strength for p in strongest) == [4, 5, 5]


def test_first_hop_is_first_person_not_an_institution():
    g = graph(
        me(), person("anita"), person("vik"), school("iitm"), company("t"),
        edge("STUDIED_AT", "me", "iitm"), edge("STUDIED_AT", "anita", "iitm"),
        knows("anita", "vik"), edge("WORKS_AT", "vik", "t"),
    )  # fmt: skip
    (path,) = rank(g, "t", RankingConfig(via_institutions=True)).paths
    assert (path.first_hop, path.referrer) == ("anita", "vik")
