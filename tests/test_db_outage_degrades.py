"""A database outage must degrade the site, not destroy it.

WHAT HAPPENED. `init_db()` ran at import time (`main.py`, module level). When the database
became unreachable, that raised during module import, so uvicorn never finished starting, the
process exited 1, and the machine reboot-looped. Fly then returned 503 for EVERYTHING —
including static assets and pages that need no database at all. A database problem became a
total process failure.

WHAT SHOULD HAPPEN. The app boots regardless; requests that genuinely need the database answer
503 with a page a human can read; `/healthz` says the app is alive and the database is not, so
the difference is visible instead of guessed at.

The `/healthz` distinction matters for recovery, not just diagnosis: a health check that fails
because a dependency is down invites the platform to kill and restart a process that is working
correctly, which is the loop this is meant to end.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app


@pytest.fixture
def db_down(monkeypatch):
    """Every route to the database raises, as in a real outage.

    BOTH seams, deliberately. Overriding the `get_db` dependency alone looks sufficient and is
    not: `/healthz` and the session middleware open their own `SessionLocal`, so a
    dependency-only fixture left `/healthz` cheerfully reporting `database: ok` while the
    database was unreachable — the precise false all-clear that endpoint exists to prevent.
    That is how this fixture was first written, and the healthz test caught it.
    """

    def _fail():
        raise OperationalError("SELECT 1", {}, Exception("connection failed: quota exceeded"))

    def _fail_session(*_args, **_kwargs):
        _fail()

    # Two signatures on purpose. FastAPI INTROSPECTS a dependency override's signature to build
    # the request model, so a `*args, **kwargs` override is read as untyped query parameters and
    # the route answers 422 before the body ever runs — masking the 503 this file is testing.
    # `SessionLocal`, being called directly as a context manager, needs the permissive one.
    app.dependency_overrides[get_db] = _fail
    monkeypatch.setattr("app.main.SessionLocal", _fail_session)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_db, None)


def test_a_database_outage_answers_503_rather_than_500(db_down):
    """503 says "come back"; 500 says "this is broken". Crawlers treat them differently — a 500
    invites de-indexing, which would undo the discovery work this site needs."""
    resp = db_down.get("/leaderboard")
    assert resp.status_code == 503, f"got {resp.status_code}, expected 503"
    body = resp.text.lower()
    assert "unavailable" in body or "temporarily" in body, (
        f"the 503 body is not human-readable: {resp.text[:200]!r}"
    )
    assert "traceback" not in body and "operationalerror" not in body, (
        "the outage page leaks an internal traceback to visitors"
    )


def test_healthz_is_liveness_only_and_never_touches_the_database(db_down):
    """fly.toml checks /healthz every 30s with a 5s timeout, and a failed check kills the
    machine. So this endpoint must not depend on the database at ALL — not merely tolerate it
    being down, but never wait on it, because a SLOW database would fail the check and restart
    a process that is serving correctly.

    Asserted with the database raising: if the handler touched it, this would error rather than
    return.
    """
    resp = db_down.get("/healthz")
    assert resp.status_code == 200, "healthz failed while the database was down"
    body = resp.json()
    assert body["status"] == "ok"
    assert "database" not in body, (
        "healthz reports database state, so it is querying the database — a slow database will "
        "now fail the platform health check and kill a working machine. Use /readyz."
    )


def test_readyz_reports_the_database_as_down(db_down):
    """The diagnostic half of the split: monitoring needs to see the outage somewhere."""
    resp = db_down.get("/readyz")
    assert resp.status_code == 200, "readyz should report the outage, not become one"
    assert resp.json().get("database") == "down", f"readyz hid the outage: {resp.json()}"


def test_readyz_reports_the_database_as_up_when_it_is():
    """Positive control. Without it, a `/readyz` hard-coded to "down" satisfies the test above
    and the field cannot distinguish the two states it exists to distinguish."""
    resp = TestClient(app).get("/readyz")
    assert resp.status_code == 200
    assert resp.json().get("database") == "ok"


def test_pages_that_need_no_database_still_serve_during_an_outage(db_down):
    """The point of degrading rather than dying.

    `/privacy` and `/terms` are static prose. During the real outage they returned 503 along
    with everything else, because the process was gone — not because they needed anything.
    """
    for path in ("/privacy", "/terms"):
        resp = db_down.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code} with the DB down"


def test_init_db_failure_does_not_propagate():
    """The import-time fatality itself, at the seam that had it.

    Asserted by calling the guarded initializer with a failing implementation rather than by
    re-importing the module, which pytest has already imported and cached.
    """
    from app import main

    def _boom():
        raise OperationalError("CREATE TABLE", {}, Exception("connection failed"))

    assert main._init_db_safely(_boom) is False, (
        "a failing init_db was reported as success; the app would start believing its schema "
        "is present"
    )

    def _fine():
        return None

    assert main._init_db_safely(_fine) is True, (
        "a SUCCESSFUL init was reported as failure — the guard swallows outcomes rather than "
        "exceptions, so the flag would never mean anything"
    )
