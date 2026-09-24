from pathlib import Path

import pytest

from sixhops.adapters.importers.linkedin import NotAConnectionsFile, parse, to_extraction

FIXTURE = Path(__file__).parent.parent / "fixtures" / "Connections.csv"


def test_parse_skips_preamble_and_nameless_rows():
    result = parse(FIXTURE.read_bytes())
    assert [c.name for c in result.connections] == [
        "Priya Sharma", "Rahul Iyer", "Priya Sharma", "Anita Rao"
    ]  # fmt: skip
    assert result.skipped == 1
    rahul = result.connections[1]
    assert (rahul.email, rahul.company, rahul.position) == (
        "rahul@example.com",
        "Flipkart",
        "Senior SDE",
    )


def test_parse_handles_bom_and_missing_columns():
    data = "﻿First Name,Last Name,Company\nSam,,Stripe\n".encode()
    (sam,) = parse(data).connections
    assert (sam.name, sam.company, sam.url) == ("Sam", "Stripe", "")


def test_parse_rejects_other_files():
    with pytest.raises(NotAConnectionsFile):
        parse(b"name,phone\nx,1\n")


def test_to_extraction_one_company_per_normalized_name():
    ex = to_extraction(parse(FIXTURE.read_bytes()).connections)
    companies = [m for m in ex.mentions if m.kind == "company"]
    assert [c.name for c in companies] == ["Razorpay", "Flipkart"]  # Pvt Ltd variant folded in
    knows = [r for r in ex.relations if r.kind == "KNOWS"]
    assert len(knows) == 4 and all(r.src == "me" and r.strength == 2 for r in knows)
    assert knows[0].note == "LinkedIn connection since Mar 2021"
    person = ex.mentions[0]
    assert person.attrs == {
        "linkedin": "https://www.linkedin.com/in/priya-sharma-1",
        "title": "Software Engineer II",
    }
    works = [(r.src, r.dst, r.strength) for r in ex.relations if r.kind == "WORKS_AT"]
    assert works == [("p0", "c0", 3), ("p1", "c1", 3), ("p2", "c0", 3)]
