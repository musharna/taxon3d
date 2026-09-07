"""The admin secret must never travel in a URL.

`GET /admin?token=...` put the shared admin token into browser history, referer headers and every
proxy log between the operator and Fly (repo audit 2026-09-06). Admin HTML pages now accept only
the `bio3d_admin` cookie, which a `POST /admin/login` form sets; the same cookie is the admin
bypass on the media routes. Form-POSTed tokens on the mutating admin actions are unchanged (a
request body is not a URL)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config
from app.main import ADMIN_COOKIE, app


def _login(client: TestClient, token: str):
    return client.post(
        "/admin/login", data={"token": token, "next": "/admin"}, follow_redirects=False
    )


def test_query_string_token_no_longer_opens_admin_pages():
    c = TestClient(app)
    assert c.get("/admin", params={"token": config.ADMIN_TOKEN}).status_code == 401
    assert c.get("/admin/moderation", params={"token": config.ADMIN_TOKEN}).status_code == 401


def test_login_form_sets_the_cookie_and_the_pages_open():
    c = TestClient(app)
    r = _login(c, config.ADMIN_TOKEN)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert ADMIN_COOKIE in r.cookies
    assert config.ADMIN_TOKEN not in r.headers["location"]
    # positive control: the cookie alone is enough for both admin pages
    assert c.get("/admin").status_code == 200
    assert c.get("/admin/moderation").status_code == 200


def test_wrong_token_does_not_log_in():
    c = TestClient(app)
    r = _login(c, "wrong")
    assert r.status_code == 401
    assert ADMIN_COOKIE not in r.cookies
    assert c.get("/admin").status_code == 401


def test_login_page_renders_without_a_token():
    c = TestClient(app)
    r = c.get("/admin/login")
    assert r.status_code == 200
    assert 'name="token"' in r.text and 'type="password"' in r.text


def test_next_is_restricted_to_admin_pages():
    """An open redirect on the login form would let a phishing link bounce a logged-in operator
    anywhere. Only admin paths are honoured; anything else lands on /admin."""
    c = TestClient(app)
    r = c.post(
        "/admin/login",
        data={"token": config.ADMIN_TOKEN, "next": "https://evil.example/"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin"
