"""Log a TestClient in as admin the way an operator does: token once, in a POST body."""

from __future__ import annotations

from fastapi.testclient import TestClient


def login_admin(client: TestClient, token: str) -> None:
    r = client.post("/admin/login", data={"token": token}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
