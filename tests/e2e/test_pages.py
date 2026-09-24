import pytest


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_pages_redirect_to_login_when_anonymous(client):
    resp = client.get("/jobs", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    assert client.post("/login", data={"password": "nope"}).status_code == 401


@pytest.mark.parametrize(
    "path, text",
    [
        ("/", "Graph"),
        ("/jobs", "Jobs"),
        ("/email", "Cold email"),
        ("/study", "Study"),
    ],
)
def test_pages_render_after_login(authed, path, text):
    resp = authed.get(path)
    assert resp.status_code == 200
    assert text in resp.text


def test_bearer_token_bypasses_login(client):
    resp = client.get("/jobs", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    bad = client.get("/api/x", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401


def test_logout_clears_session(authed):
    authed.post("/logout")
    assert authed.get("/", follow_redirects=False).status_code == 303
