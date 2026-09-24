"""Path queries through the JSON API and the side panel."""

import pytest


def edge(kind, src, dst, **kw):
    return {"op": "create_edge", "kind": kind, "src": src, "dst": dst, **kw}


SEED = {
    "summary": "seed",
    "ops": [
        {"op": "create_node", "key": "rahul", "kind": "person", "name": "Rahul"},
        {"op": "create_node", "key": "priya", "kind": "person", "name": "Priya S"},
        {"op": "create_node", "key": "anita", "kind": "person", "name": "Anita"},
        {"op": "create_node", "key": "rzp", "kind": "company", "name": "Razorpay"},
        {"op": "create_node", "key": "far", "kind": "company", "name": "Faraway"},
        {"op": "create_node", "key": "iitm", "kind": "school", "name": "IIT Madras"},
        {"op": "create_edge", "kind": "KNOWS", "src": {"id": "me"}, "dst": {"key": "rahul"},
         "strength": 5, "note": "college roommate"},
        {"op": "create_edge", "kind": "KNOWS", "src": {"key": "rahul"}, "dst": {"key": "priya"},
         "strength": 3, "note": "ex-colleague"},
        edge("WORKS_AT", {"key": "priya"}, {"key": "rzp"}),
        edge("STUDIED_AT", {"id": "me"}, {"key": "iitm"}),
        edge("STUDIED_AT", {"key": "anita"}, {"key": "iitm"}),
        edge("WORKS_AT", {"key": "anita"}, {"key": "far"}),
    ],
}  # fmt: skip


@pytest.fixture
def seeded(authed):
    ids = authed.post("/api/ops", json=SEED).json()["new_ids"]
    return authed, ids


def test_api_returns_ranked_paths(seeded):
    client, ids = seeded
    body = client.get("/api/paths", params={"target": ids["rzp"]}).json()
    (path,) = body["paths"]
    assert path["nodes"] == ["me", ids["rahul"], ids["priya"], ids["rzp"]]
    assert path["hops"] == 3
    assert path["first_hop"] == ids["rahul"] and path["referrer"] == ids["priya"]
    assert set(path["tags"]) == {"shortest", "strongest"}
    assert body["max_hops"] == 4


def test_api_validates_target_and_hop_cap(seeded):
    client, ids = seeded
    assert client.get("/api/paths", params={"target": ids["rahul"]}).status_code == 404
    assert client.get("/api/paths", params={"target": ids["rzp"], "max_hops": 7}).status_code == 422


def test_institution_toggle(seeded):
    client, ids = seeded
    off = client.get("/api/paths", params={"target": ids["far"]}).json()
    assert off["paths"] == [] and off["people_at_target"] == 1
    on = client.get("/api/paths", params={"target": ids["far"], "via_institutions": True}).json()
    assert on["paths"][0]["nodes"] == ["me", ids["iitm"], ids["anita"], ids["far"]]


def test_panel_shows_path_with_ask_and_highlight_data(seeded):
    client, ids = seeded
    html = client.get("/ui/paths", params={"target": "Razorpay"}).text
    assert "Paths to Razorpay" in html
    assert "Ask <strong>Rahul</strong> for an intro to <strong>Priya S</strong>" in html
    assert f'data-path-nodes="me,{ids["rahul"]},{ids["priya"]},{ids["rzp"]}"' in html
    assert "Show longer paths (up to 6 hops)" in html


def test_panel_unreachable_offers_institution_hops(seeded):
    client, _ = seeded
    html = client.get("/ui/paths", params={"target": "Faraway"}).text
    assert "1 person at Faraway, but none connected to you" in html
    assert "via_institutions=true" in html
    html = client.get("/ui/paths", params={"target": "Faraway", "via_institutions": "true"}).text
    assert "alma mater of" in html
    assert "Reach out to <strong>Anita</strong> (shared: IIT Madras) for a referral." in html


def test_panel_unknown_company_suggests(seeded):
    client, _ = seeded
    html = client.get("/ui/paths", params={"target": "Razorpy"}).text
    assert "No company called <strong>Razorpy</strong>" in html
    assert ">Razorpay</a>" in html


def test_panel_beyond_default_cap_offers_longer(authed):
    chain = [
        {"op": "create_node", "key": f"p{i}", "kind": "person", "name": f"P{i}"} for i in range(4)
    ]
    refs = [{"id": "me"}, *({"key": f"p{i}"} for i in range(4))]
    links = [
        {"op": "create_edge", "kind": "KNOWS", "src": a, "dst": b}
        for a, b in zip(refs, refs[1:], strict=False)
    ]
    ops = [
        *chain,
        {"op": "create_node", "key": "t", "kind": "company", "name": "Target"},
        *links,
        {"op": "create_edge", "kind": "WORKS_AT", "src": {"key": "p3"}, "dst": {"key": "t"}},
    ]
    authed.post("/api/ops", json={"ops": ops})
    html = authed.get("/ui/paths", params={"target": "Target"}).text
    assert "No path within 4 hops. The nearest is 5 hops." in html
    html = authed.get("/ui/paths", params={"target": "Target", "max_hops": 6}).text
    assert "5 hops" in html and "data-path-nodes" in html
