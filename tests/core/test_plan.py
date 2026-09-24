from sixhops.core.compile import compile_ops
from sixhops.core.extraction import Extraction, Mention, Relation
from sixhops.core.ops import CreateEdge, CreateNode, ExistingRef, NewRef, UpdateEdge, UpdateNode
from sixhops.core.plan import NEW, PlanPolicy, plan, select_ops
from tests.core.builders import company, edge, graph, knows, me, person

RAHUL_PRIYA = Extraction(
    mentions=[
        Mention(key="rahul", kind="person", name="Rahul"),
        Mention(key="priya", kind="person", name="Priya"),
        Mention(key="rzp", kind="company", name="Razorpay"),
    ],
    relations=[
        Relation(kind="KNOWS", src="rahul", dst="priya", note="used to work together"),
        Relation(kind="WORKS_AT", src="priya", dst="rzp"),
    ],
)


def seeded():
    return graph(
        me(), person("r", "Rahul"), person("ps", "Priya S"), company("c", "Razorpay"),
        knows("me", "r", 5), edge("WORKS_AT", "ps", "c"),
    )  # fmt: skip


def test_the_readme_example_links_existing_people_and_skips_known_facts():
    g = seeded()
    cs = plan(RAHUL_PRIYA, g)
    by_key = {r.mention_key: r for r in cs.resolutions}
    assert by_key["rahul"].choice == "r" and by_key["rahul"].auto
    assert by_key["rzp"].choice == "c" and by_key["rzp"].auto
    assert by_key["priya"].choice == "ps" and not by_key["priya"].auto  # proposed, not assumed
    assert "Razorpay" in by_key["priya"].candidates[0].reason  # context boost explains itself

    # Priya S learns the alias "Priya"; the only new fact is Rahul knows Priya S.
    assert cs.ops == [
        UpdateNode(node_id="ps", add_aliases=["Priya"]),
        CreateEdge(kind="KNOWS", src=ExistingRef(id="r"), dst=ExistingRef(id="ps"),
                   note="used to work together", strength_inferred=True),
    ]  # fmt: skip
    compile_ops(g, cs.ops)  # always valid


def test_choosing_new_creates_a_separate_person():
    cs = plan(RAHUL_PRIYA, seeded(), decisions={"priya": NEW})
    assert CreateNode(key="priya", kind="person", name="Priya") in cs.ops
    assert CreateEdge(kind="WORKS_AT", src=NewRef(key="priya"), dst=ExistingRef(id="c"),
                      strength_inferred=True) in cs.ops  # fmt: skip
    compile_ops(seeded(), cs.ops)


def test_choosing_a_specific_node_overrides_resolution():
    g = graph(me(), person("a", "Priya S"), person("b", "Priya K"))
    ex = Extraction(mentions=[Mention(key="p", kind="person", name="Priya")],
                    relations=[Relation(kind="KNOWS", src="me", dst="p")])  # fmt: skip
    cs = plan(ex, g, decisions={"p": "b"})
    assert cs.resolutions[0].choice == "b"
    assert cs.ops[-1] == CreateEdge(kind="KNOWS", src=ExistingRef(id="me"),
                                    dst=ExistingRef(id="b"), strength_inferred=True)  # fmt: skip


def test_ambiguous_exact_names_are_never_auto_linked():
    g = graph(me(), person("a", "Priya"), person("b", "Priya"))
    cs = plan(Extraction(mentions=[Mention(key="p", kind="person", name="Priya")]), g)
    assert not cs.resolutions[0].auto
    assert len(cs.resolutions[0].candidates) == 2


def test_new_everything_on_an_empty_graph():
    g = graph(me())
    cs = plan(RAHUL_PRIYA, g)
    assert cs.resolutions == []
    assert [type(op).__name__ for op in cs.ops] == ["CreateNode"] * 3 + ["CreateEdge"] * 2
    compile_ops(g, cs.ops)


def test_existing_edge_is_updated_in_chat_but_not_on_import():
    g = seeded()
    ex = Extraction(mentions=[Mention(key="r", kind="person", name="Rahul")],
                    relations=[Relation(kind="KNOWS", src="me", dst="r", strength=3,
                                        note="college roommate")])  # fmt: skip
    (update,) = plan(ex, g).ops
    assert update == UpdateEdge(edge_id="KNOWS:me:r", strength=3, note="college roommate")
    assert plan(ex, g, policy=PlanPolicy(update_existing_edges=False)).ops == []


def test_same_fact_twice_is_a_no_op():
    ex = Extraction(mentions=[Mention(key="r", kind="person", name="Rahul")],
                    relations=[Relation(kind="KNOWS", src="me", dst="r")])  # fmt: skip
    assert plan(ex, seeded()).ops == []


def test_job_change_marks_old_employer_as_former():
    g = seeded()
    ex = Extraction(
        mentions=[Mention(key="p", kind="person", name="Priya S"),
                  Mention(key="s", kind="company", name="Stripe")],
        relations=[Relation(kind="WORKS_AT", src="p", dst="s")],
    )  # fmt: skip
    cs = plan(ex, g)
    assert UpdateEdge(edge_id="WORKS_AT:ps:c", kind="WORKED_AT") in cs.ops
    assert any("former employer" in w for w in cs.warnings)
    compile_ops(g, cs.ops)


def test_left_company_flips_works_at_to_worked_at():
    ex = Extraction(
        mentions=[Mention(key="p", kind="person", name="Priya S"),
                  Mention(key="c", kind="company", name="Razorpay")],
        relations=[Relation(kind="WORKED_AT", src="p", dst="c")],
    )  # fmt: skip
    assert plan(ex, seeded()).ops == [UpdateEdge(edge_id="WORKS_AT:ps:c", kind="WORKED_AT")]


def test_invalid_and_self_relations_are_skipped_with_warnings():
    g = graph(me(), person("ps", "Priya S"))
    ex = Extraction(
        mentions=[Mention(key="a", kind="person", name="Priya S"),
                  Mention(key="b", kind="person", name="Priya S."),
                  Mention(key="c", kind="company", name="Razorpay")],
        relations=[Relation(kind="WORKS_AT", src="c", dst="a"),   # company can't work at a person
                   Relation(kind="KNOWS", src="a", dst="ghost"),    # unknown mention
                   Relation(kind="KNOWS", src="a", dst="b")],       # same person twice
    )  # fmt: skip
    cs = plan(ex, g)
    assert len(cs.warnings) == 3
    assert not [op for op in cs.ops if isinstance(op, CreateEdge)]


def test_attributes_and_alias_update_existing_node_once():
    g = graph(me(), person("p", "Priya S", attrs={"title": "SDE"}))
    ex = Extraction(mentions=[Mention(key="p", kind="person", name="Priya S",
                                      attrs={"title": "SDE2", "email": "p@x.in"})])  # fmt: skip
    (op,) = plan(ex, g).ops
    assert op == UpdateNode(node_id="p", attrs={"title": "SDE2", "email": "p@x.in"})
    after = compile_ops(g, [op]).after
    assert plan(ex, after).ops == []


def test_select_ops_cascades_to_dependent_edges():
    cs = plan(RAHUL_PRIYA, graph(me()))
    create_priya = next(i for i, op in enumerate(cs.ops) if getattr(op, "key", None) == "priya")
    kept = select_ops(cs.ops, {create_priya})
    assert not any(getattr(op, "key", None) == "priya" for op in kept)
    assert all("priya" not in (getattr(op.src, "key", ""), getattr(op.dst, "key", ""))
               for op in kept if isinstance(op, CreateEdge))  # fmt: skip
    compile_ops(graph(me()), kept)


# --- property: whatever the extraction, the plan is valid against the graph -------------------

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tests.core.test_undo_properties import SEED  # noqa: E402

NAMES = ["Priya", "Priya S", "priya s.", "Rahul", "Raul", "Razorpay", "Razorpay Pvt Ltd",
         "Flipkart", "IIT Madras", "Abhiram"]  # fmt: skip
KINDS = ["person", "company", "school"]


@st.composite
def extractions(draw):
    mentions = [
        Mention(key=f"m{i}", kind=draw(st.sampled_from(KINDS)), name=draw(st.sampled_from(NAMES)))
        for i in range(draw(st.integers(0, 5)))
    ]
    keys = ["me", *(m.key for m in mentions), "ghost"]
    relations = draw(
        st.lists(
            st.builds(
                Relation,
                kind=st.sampled_from(["KNOWS", "WORKS_AT", "WORKED_AT", "STUDIED_AT"]),
                src=st.sampled_from(keys),
                dst=st.sampled_from(keys),
                strength=st.none() | st.integers(1, 5),
                note=st.sampled_from(["", "college", "ex-colleague"]),
            ),
            max_size=6,
        )
    )
    return Extraction(mentions=mentions, relations=relations)


@given(extractions(), st.booleans(), st.data())
def test_any_plan_compiles(ex, update_edges, data):
    g = SEED
    decisions = {}
    for m in ex.mentions:  # sometimes override: new, or an arbitrary (maybe invalid) node
        if data.draw(st.booleans()):
            decisions[m.key] = data.draw(st.sampled_from([NEW, *sorted(g.nodes)]))
    cs = plan(ex, g, decisions, PlanPolicy(update_existing_edges=update_edges))
    compile_ops(g, cs.ops)
    assert not any(type(op).__name__.startswith("Delete") for op in cs.ops)


def test_same_name_different_linkedin_stays_two_people():
    def priya(key):
        return Mention(key=key, kind="person", name="Priya Sharma",
                       attrs={"linkedin": f"linkedin.com/in/{key}"})  # fmt: skip

    ex = Extraction(mentions=[
        priya("a"),
        priya("b"),
        Mention(key="c", kind="person", name="Rahul"),
        Mention(key="d", kind="person", name="rahul"),
    ])  # fmt: skip
    created = [op.key for op in plan(ex, graph(me())).ops if isinstance(op, CreateNode)]
    assert created == ["a", "b", "c"]


def test_two_different_people_are_not_both_matched_to_one_entry():
    g = graph(me(), person("ps", "Priya S"))
    ex = Extraction(mentions=[
        Mention(key="a", kind="person", name="Priya Sharma", attrs={"linkedin": "l.com/in/a"}),
        Mention(key="b", kind="person", name="Priya Sharma", attrs={"linkedin": "l.com/in/b"}),
    ])  # fmt: skip
    cs = plan(ex, g)
    assert [r.choice for r in cs.resolutions] == ["ps", NEW]
    assert [r.choice for r in plan(ex, g, {"a": NEW}).resolutions] == [NEW, "ps"]


def test_being_connected_to_me_is_not_evidence():
    g = graph(me(), person("a", "Anita"), knows("me", "a"))
    ex = Extraction(mentions=[Mention(key="x", kind="person", name="Anita Rao")],
                    relations=[Relation(kind="KNOWS", src="me", dst="x")])  # fmt: skip
    (r,) = plan(ex, g).resolutions
    assert r.candidates[0].score == 0.8 and "connected" not in r.candidates[0].reason
