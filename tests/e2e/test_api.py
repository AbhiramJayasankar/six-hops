import json


def create_priya(client):
    body = {
        "summary": "add Priya",
        "ops": [
            {"op": "create_node", "key": "p", "kind": "person", "name": "Priya"},
            {"op": "create_node", "key": "c", "kind": "company", "name": "Razorpay"},
            {"op": "create_edge", "kind": "KNOWS", "src": {"id": "me"}, "dst": {"key": "p"}},
            {"op": "create_edge", "kind": "WORKS_AT", "src": {"key": "p"}, "dst": {"key": "c"}},
        ],
    }
    resp = client.post("/api/ops", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_me_is_bootstrapped_from_settings(authed):
    g = authed.get("/api/graph").json()
    assert [n["name"] for n in g["nodes"].values()] == ["Abhiram"]


def test_ops_apply_and_undo(authed):
    result = create_priya(authed)
    assert result["version"] == 1
    assert set(result["new_ids"]) == {"p", "c"}
    assert len(authed.get("/api/graph").json()["edges"]) == 2
    assert authed.get("/api/history").json()[0]["summary"] == "add Priya"

    assert authed.post("/api/undo").status_code == 200
    assert authed.get("/api/graph").json()["edges"] == {}
    assert authed.post("/api/undo").status_code == 409


def test_invalid_ops_return_422_with_reasons(authed):
    body = {
        "ops": [{"op": "create_edge", "kind": "WORKS_AT", "src": {"id": "me"}, "dst": {"id": "me"}}]
    }
    resp = authed.post("/api/ops", json=body)
    assert resp.status_code == 422
    assert any("must be a company" in e for e in resp.json()["detail"])


def test_stale_expected_version_returns_409(authed):
    body = {"ops": [], "expected_version": 42}
    assert authed.post("/api/ops", json=body).status_code == 409


def test_export_import_round_trip_is_undoable(authed):
    create_priya(authed)
    exported = authed.get("/api/graph/export")
    assert "attachment" in exported.headers["content-disposition"]
    doc = exported.json()

    authed.post("/api/undo")  # back to just "me"
    files = {"file": ("g.json", json.dumps(doc), "application/json")}
    assert authed.post("/api/graph/import", files=files).status_code == 200
    g = authed.get("/api/graph").json()
    assert len(g["nodes"]) == 3 and len(g["edges"]) == 2

    authed.post("/api/undo")
    assert len(authed.get("/api/graph").json()["nodes"]) == 1


def test_import_rejects_invalid_graph(authed):
    doc = {"nodes": [{"id": "x", "kind": "person", "name": "No me"}], "edges": []}
    files = {"file": ("g.json", json.dumps(doc), "application/json")}
    resp = authed.post("/api/graph/import", files=files)
    assert resp.status_code == 422
    assert "exactly one 'me'" in resp.json()["detail"][0]
    bad = authed.post("/api/graph/import", files={"file": ("g.json", "not json", "text/plain")})
    assert bad.status_code == 422


def test_api_requires_auth(client):
    assert client.get("/api/graph").status_code == 401
