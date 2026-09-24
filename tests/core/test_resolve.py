import pytest

from sixhops.core.model import Node
from sixhops.core.resolve import (
    AUTO_LINK,
    SUGGEST,
    candidates,
    find_duplicates,
    normalize_url,
    score,
    tokens,
)
from tests.core.builders import company, edge, graph, knows, me, person, school


def s(name, kind, node, **attrs):
    return score(name, kind, attrs, node)[0]


def test_tokens_normalize_case_accents_titles_and_suffixes():
    assert tokens("Dr. Anita  Rāo", "person") == ["anita", "rao"]
    assert tokens("Razorpay Software Pvt. Ltd.", "company") == ["razorpay"]
    assert tokens("Labs", "company") == ["labs"]  # never strip the whole name


@pytest.mark.parametrize(
    "mention, existing, low, high",
    [
        ("Priya", "Priya S", SUGGEST, AUTO_LINK),  # propose, don't auto-link
        ("Priya S", "Priya Sharma", SUGGEST, AUTO_LINK),
        ("Priya S", "Priya K", 0, SUGGEST),  # conflicting initials
        ("Priya Sharma", "Priya Verma", 0, SUGGEST),
        ("Rahul", "Raul", SUGGEST, AUTO_LINK),  # typo
        ("Sharma Priya", "Priya Sharma", SUGGEST, AUTO_LINK),
        ("Priya Sharma", "Priya Sharma", AUTO_LINK, 1.01),
        ("Rahul", "Anita", 0, SUGGEST),
    ],
)
def test_person_name_scores(mention, existing, low, high):
    assert low <= s(mention, "person", person("x", existing)) < high


@pytest.mark.parametrize(
    "mention, existing, kind, low, high",
    [
        ("Razorpay Software Pvt Ltd", "Razorpay", "company", AUTO_LINK, 1.01),
        ("IIT Madras", "Indian Institute of Technology Madras", "school", SUGGEST, AUTO_LINK),
        ("Flipcart", "Flipkart", "company", SUGGEST, AUTO_LINK),
        ("Meta", "Metabase", "company", 0, SUGGEST),
    ],
)
def test_institution_name_scores(mention, existing, kind, low, high):
    node = Node(id="x", kind=kind, name=existing)
    assert low <= s(mention, kind, node) < high


def test_alias_match_links():
    node = person("x", "Priya Sharma", aliases=["PS"])
    assert s("ps", "person", node) == 0.95


def test_linkedin_url_decides_either_way():
    node = person("x", "Priya S", attrs={"linkedin": "https://in.linkedin.com/in/priya-s/"})
    assert s("Totally Different", "person", node, linkedin="linkedin.com/in/priya-s") == 1.0
    assert s("Priya S", "person", node, linkedin="linkedin.com/in/someone-else") == 0.0
    assert normalize_url("HTTPS://www.LinkedIn.com/in/a/?x=1") == "linkedin.com/in/a"


def test_email_match():
    node = person("x", "P", attrs={"email": "Priya@Example.com"})
    assert s("Someone", "person", node, email="priya@example.com") == 1.0


def test_candidates_are_kind_filtered_and_ranked():
    g = graph(me(), person("p1", "Priya S"), person("p2", "Priya Sharma"), company("c", "Priya"))
    found = candidates(g, "Priya S", "person")
    assert [c.node_id for c in found] == ["p1", "p2"]
    assert candidates(g, "Priya", "company")[0].node_id == "c"


def test_person_mention_can_match_me():
    g = graph(me(name="Abhiram J"), person("p", "Rahul"))
    assert candidates(g, "Abhiram", "person")[0].node_id == "me"


def test_context_boost_prefers_the_connected_candidate_but_never_auto_links():
    g = graph(
        me(), person("a", "Priya S"), person("b", "Priya K"), company("rzp", "Razorpay"),
        edge("WORKS_AT", "a", "rzp"),
    )  # fmt: skip
    plain = {c.node_id: c.score for c in candidates(g, "Priya", "person")}
    assert plain["a"] == plain["b"] == 0.8
    boosted = candidates(g, "Priya", "person", context=frozenset({"rzp"}))
    assert boosted[0].node_id == "a" and boosted[0].score == pytest.approx(0.9)
    assert "Razorpay" in boosted[0].reason
    assert all(c.score < AUTO_LINK for c in boosted)


def test_find_duplicates_keeps_the_better_connected_node():
    g = graph(
        me(), person("p1", "Priya"), person("p2", "Priya S"), person("r", "Rahul"),
        school("s1", "IIT Madras"), school("s2", "Indian Institute of Technology Madras"),
        knows("me", "p2"), knows("r", "p2"), edge("STUDIED_AT", "me", "s1"),
    )  # fmt: skip
    pairs = {(d.keep_id, d.drop_id) for d in find_duplicates(g)}
    assert ("p2", "p1") in pairs
    assert ("s1", "s2") in pairs
    assert not any("r" in pair for pair in pairs)


def test_find_duplicates_never_drops_me():
    g = graph(me(name="Abhiram"), person("p", "Abhiram"))
    (d,) = find_duplicates(g)
    assert (d.keep_id, d.drop_id) == ("me", "p")


# --- property: the blocking index never hides a match that a full scan would find -----------

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

WORDS = ["priya", "sharma", "s", "rahul", "raul", "iit", "indian", "institute", "of",
         "technology", "madras", "razorpay", "pvt", "ltd", "flipkart", "flipcart", "dr",
         "anita"]  # fmt: skip
names = st.lists(st.sampled_from(WORDS), min_size=1, max_size=5).map(" ".join)
kinds = st.sampled_from(["person", "company", "school"])


@given(st.lists(st.tuples(kinds, names), min_size=1, max_size=12), kinds, names)
def test_index_finds_everything_a_full_scan_finds(existing, kind, name):
    from sixhops.core.model import GraphSnapshot

    nodes = {"me": Node(id="me", kind="me", name="Abhiram")}
    for i, (k, n) in enumerate(existing):
        nodes[f"n{i}"] = Node(id=f"n{i}", kind=k, name=n)
    g = GraphSnapshot(nodes=nodes)
    allowed = {"person", "me"} if kind == "person" else {kind}
    full = {
        node.id
        for node in g.nodes.values()
        if node.kind in allowed and score(name, kind, {}, node)[0] >= SUGGEST
    }
    assert {c.node_id for c in candidates(g, name, kind, limit=100)} == full
