"""The HTMX side panel: every form posts, builds ops, and refreshes the canvas."""

import json


def graph(client):
    return client.get("/api/graph").json()


def by_name(g, name):
    return next(n for n in g["nodes"].values() if n["name"] == name)


def test_page_and_home_panel_render(authed):
    assert 'id="cy"' in authed.get("/").text
    panel = authed.get("/ui/panel").text
    assert "Your network" in panel and "Add a node" in panel


def test_add_person_who_knows_me_then_connect_to_new_company(authed):
    resp = authed.post(
        "/ui/node", data={"kind": "person", "name": "Rahul", "knows_me": "true", "strength": "4"}
    )
    assert "Rahul" in resp.text
    trigger = json.loads(resp.headers["HX-Trigger"])["graph-changed"]
    rahul = by_name(graph(authed), "Rahul")
    assert trigger["select"] == rahul["id"]

    resp = authed.post(
        f"/ui/node/{rahul['id']}/connect",
        data={"kind": "WORKS_AT", "other": "Razorpay", "strength": "3", "note": "SDE2"},
    )
    assert "Saved." in resp.text
    g = graph(authed)
    rzp = by_name(g, "Razorpay")
    assert rzp["kind"] == "company"
    kinds = sorted(e["kind"] for e in g["edges"].values())
    assert kinds == ["KNOWS", "WORKS_AT"]

    # Connecting again to the picked existing node is a duplicate and is reported, not applied.
    resp = authed.post(
        f"/ui/node/{rahul['id']}/connect",
        data={"kind": "WORKS_AT", "other": f"Razorpay (#{rzp['id']})"},
    )
    assert "already exists" in resp.text
    assert "HX-Trigger" not in resp.headers


def test_edit_node_replaces_aliases_and_attrs(authed):
    authed.post("/ui/node", data={"kind": "person", "name": "Priya"})
    priya = by_name(graph(authed), "Priya")
    authed.post(
        f"/ui/node/{priya['id']}",
        data={"name": "Priya S", "aliases": "Priya, PS", "attr_title": "SDE2", "notes": "PyCon"},
    )
    node = graph(authed)["nodes"][priya["id"]]
    assert node["name"] == "Priya S"
    assert node["aliases"] == ["Priya", "PS"]
    assert node["attrs"] == {"title": "SDE2"}
    authed.post(f"/ui/node/{priya['id']}", data={"name": "Priya S", "aliases": "PS"})
    assert graph(authed)["nodes"][priya["id"]]["aliases"] == ["PS"]


def test_edit_edge_and_switch_to_worked_at(authed):
    authed.post("/ui/node", data={"kind": "person", "name": "Rahul"})
    rahul = by_name(graph(authed), "Rahul")
    authed.post(f"/ui/node/{rahul['id']}/connect", data={"kind": "WORKS_AT", "other": "Flipkart"})
    (edge,) = [e for e in graph(authed)["edges"].values() if e["kind"] == "WORKS_AT"]
    assert "worked at (former)" in authed.get(f"/ui/edge/{edge['id']}").text
    authed.post(
        f"/ui/edge/{edge['id']}", data={"kind": "WORKED_AT", "strength": "5", "note": "left 2024"}
    )
    updated = graph(authed)["edges"][edge["id"]]
    assert (updated["kind"], updated["strength"], updated["note"]) == ("WORKED_AT", 5, "left 2024")


def test_merge_delete_and_undo_from_panel(authed):
    authed.post("/ui/node", data={"kind": "person", "name": "Priya", "knows_me": "true"})
    authed.post("/ui/node", data={"kind": "person", "name": "Priya S"})
    g = graph(authed)
    keep, drop = by_name(g, "Priya S"), by_name(g, "Priya")

    resp = authed.post(f"/ui/node/{keep['id']}/merge", data={"other": f"Priya (#{drop['id']})"})
    assert resp.status_code == 200
    g = graph(authed)
    assert drop["id"] not in g["nodes"]
    assert g["nodes"][keep["id"]]["aliases"] == ["Priya"]
    assert len(g["edges"]) == 1

    authed.post(f"/ui/node/{keep['id']}/delete")
    assert keep["id"] not in graph(authed)["nodes"]

    resp = authed.post("/ui/undo")
    assert "Undid: Deleted Priya S" in resp.text
    authed.post("/ui/undo")
    assert drop["id"] in graph(authed)["nodes"]


def test_me_cannot_be_deleted_and_errors_render_in_panel(authed):
    resp = authed.post("/ui/node/me/delete")
    assert "cannot be deleted" in resp.text
    assert "me" in graph(authed)["nodes"]


def test_undo_with_empty_history(authed):
    assert "Nothing to undo" in authed.post("/ui/undo").text


def test_import_via_panel_rejects_garbage(authed):
    resp = authed.post("/ui/import", files={"file": ("g.json", "{}", "application/json")})
    assert "alert error" in resp.text


def test_history_summaries_are_descriptive(authed):
    authed.post("/ui/node", data={"kind": "person", "name": "Rahul"})
    rahul = by_name(graph(authed), "Rahul")
    authed.post(f"/ui/node/{rahul['id']}/connect", data={"kind": "WORKED_AT", "other": "Flipkart"})
    summaries = [h["summary"] for h in authed.get("/api/history").json()]
    assert summaries == ["Rahul: worked at Flipkart", "Added Rahul"]


def test_bad_form_values_render_errors_not_500(authed):
    authed.post("/ui/node", data={"kind": "person", "name": "Rahul", "knows_me": "true"})
    (edge,) = graph(authed)["edges"].values()
    resp = authed.post(f"/ui/edge/{edge['id']}", data={"kind": "LOVES", "strength": "9"})
    assert resp.status_code == 200 and "alert error" in resp.text
    resp = authed.post("/ui/node", data={"kind": "planet", "name": "Pluto"})
    assert resp.status_code == 200 and "alert error" in resp.text
