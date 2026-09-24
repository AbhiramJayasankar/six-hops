"""LinkedIn import end to end: upload, review, apply, re-import."""

import re
from pathlib import Path

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "Connections.csv").read_bytes()


def upload(client, data=FIXTURE):
    return client.post("/ui/import/linkedin", files={"file": ("Connections.csv", data, "text/csv")})


def change_id(html):
    return re.search(r"/ui/changes/(\w+)/apply", html).group(1)


def apply_all(client, html):
    shown = re.findall(r'name="shown" value="(\d+)"', html)
    return client.post(
        f"/ui/changes/{change_id(html)}/apply", data={"shown": shown, "include": shown}
    )


def graph(client):
    return client.get("/api/graph").json()


def names(g, kind):
    return sorted(n["name"] for n in g["nodes"].values() if n["kind"] == kind)


def test_import_review_apply_then_reimport_is_a_no_op(authed):
    html = upload(authed).text
    assert "LinkedIn import (4 connections)" in html
    assert "1 row without a name was skipped." in html
    assert "New people" in html and "New companies and schools" in html
    assert names(graph(authed), "person") == []  # nothing saved yet

    resp = apply_all(authed, html)
    assert "HX-Trigger" in resp.headers
    g = graph(authed)
    assert names(g, "person") == ["Anita Rao", "Priya Sharma", "Priya Sharma", "Rahul Iyer"]
    assert names(g, "company") == ["Flipkart", "Razorpay"]
    assert len(g["edges"]) == 4 + 3
    assert authed.get("/api/history").json()[0]["summary"] == "LinkedIn import (4 connections)"

    again = upload(authed).text
    assert "Everything here is already in your graph" in again


def test_import_never_touches_strengths_you_set(authed):
    apply_all(authed, upload(authed).text)
    g = graph(authed)
    knows = next(e for e in g["edges"].values() if e["kind"] == "KNOWS")
    authed.post(f"/ui/edge/{knows['id']}", data={"strength": "5", "note": "close friend"})
    assert "Everything here is already in your graph" in upload(authed).text
    assert graph(authed)["edges"][knows["id"]]["strength"] == 5


def test_job_change_on_reimport_is_proposed(authed):
    apply_all(authed, upload(authed).text)
    moved = FIXTURE.replace(b"rahul@example.com,Flipkart", b"rahul@example.com,Stripe")
    html = upload(authed, moved).text
    assert "New companies and schools" in html and "Stripe" in html
    assert "now worked at" in html
    assert "Flipkart is marked as a former employer" in html
    apply_all(authed, html)
    kinds = sorted(e["kind"] for e in graph(authed)["edges"].values() if e["kind"] != "KNOWS")
    assert kinds == ["WORKED_AT", "WORKS_AT", "WORKS_AT", "WORKS_AT"]


def test_existing_person_is_offered_as_a_match_and_choice_is_respected(authed):
    authed.post(
        "/ui/node", data={"kind": "person", "name": "Rahul I", "knows_me": "true", "strength": "5"}
    )
    rahul_i = next(n for n in graph(authed)["nodes"].values() if n["name"] == "Rahul I")
    html = upload(authed).text
    assert "Check these matches" in html
    assert f'name="choice.p1" value="{rahul_i["id"]}" checked' in html  # defaults to the match

    html = authed.post(f"/ui/changes/{change_id(html)}/replan", data={"choice.p1": "new"}).text
    assert 'name="choice.p1" value="new" checked' in html
    apply_all(authed, html)
    assert "Rahul Iyer" in names(graph(authed), "person")


def test_matching_existing_person_adds_linkedin_and_keeps_strength(authed):
    authed.post(
        "/ui/node", data={"kind": "person", "name": "Rahul I", "knows_me": "true", "strength": "5"}
    )
    apply_all(authed, upload(authed).text)
    g = graph(authed)
    rahul = next(n for n in g["nodes"].values() if n["name"] == "Rahul I")
    assert rahul["attrs"]["linkedin"] == "https://www.linkedin.com/in/rahul-iyer"
    assert "Rahul Iyer" in rahul["aliases"]
    knows = [
        e
        for e in g["edges"].values()
        if e["kind"] == "KNOWS" and rahul["id"] in (e["src"], e["dst"])
    ]
    assert [e["strength"] for e in knows] == [5]
    assert "Everything here is already in your graph" in upload(authed).text


def test_unticking_a_person_drops_their_connections(authed):
    html = upload(authed).text
    anita = re.search(r'value="(\d+)" checked>\s*<span>Anita Rao', html).group(1)
    shown = re.findall(r'name="shown" value="(\d+)"', html)
    authed.post(
        f"/ui/changes/{change_id(html)}/apply",
        data={"shown": shown, "include": [i for i in shown if i != anita]},
    )
    g = graph(authed)
    assert "Anita Rao" not in names(g, "person")
    assert len(g["nodes"]) == 1 + 3 + 2


def test_stale_change_is_rechecked_before_applying(authed):
    html = upload(authed).text
    authed.post("/ui/node", data={"kind": "company", "name": "Stripe"})  # graph moves on
    resp = apply_all(authed, html)
    assert "Your graph changed since this was prepared" in resp.text
    assert names(graph(authed), "person") == []
    apply_all(authed, resp.text)
    assert len(names(graph(authed), "person")) == 4


def test_discard_and_double_apply(authed):
    html = upload(authed).text
    cid = change_id(html)
    assert "Discarded" in authed.post(f"/ui/changes/{cid}/discard").text
    assert "already discarded" in apply_all(authed, html).text
    assert graph(authed)["edges"] == {}


def test_bad_file_is_explained(authed):
    html = upload(authed, b"name,phone\n").text
    assert "Connections.csv: there are no" in html


def test_api_flow(authed):
    change = authed.post(
        "/api/import/linkedin", files={"file": ("c.csv", FIXTURE, "text/csv")}
    ).json()
    assert change["status"] == "pending" and len(change["changeset"]["ops"]) == 13
    applied = authed.post(f"/api/changes/{change['id']}/apply", json={"excluded": []}).json()
    assert applied["status"] == "applied"
    assert authed.post(f"/api/changes/{change['id']}/apply", json={}).status_code == 409
    assert authed.get("/api/changes/nope").status_code == 404


def test_large_import_applies(authed):
    header = b"First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
    rows = b"".join(
        f"Person{i},Test,https://linkedin.com/in/p{i},,Company{i % 40},SDE,01 Jan 2020\n".encode()
        for i in range(700)
    )
    html = upload(authed, header + rows).text
    assert "And 500 more, all included." in html
    resp = apply_all(authed, html)
    assert resp.status_code == 200 and "Applied" in resp.text
    assert len(names(graph(authed), "person")) == 700
