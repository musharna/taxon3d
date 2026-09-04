import pytest
from starlette.testclient import TestClient
from app.main import app

# Hand-maintained on purpose: "public page" is a product decision, not a route property — the
# ungated set also holds admin pages, /health and JSON endpoints, so it cannot be walked off
# app.routes the way tests/test_internal_pages_gate.py derives the gated set. The gated routes
# below only answer 200 when INTERNAL_PAGES_ENABLED is on (the local default); the cross-check
# test at the bottom keeps this list honest against the live gate.
PUBLIC_PATHS = [
    "/",
    "/arena",
    "/leaderboard",
    "/dataset",
    "/tasks",
    "/submit",
    "/coverage",
    "/models",
    "/methodology",
    "/terms",
    "/privacy",
    "/licenses",
]
INTERNAL_SMOKE_PATHS = [
    "/significance",
    "/benchmark",
    "/difficulty",
    "/procedural",
    "/fidelity",
    "/spotlight",
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("path", PUBLIC_PATHS + INTERNAL_SMOKE_PATHS)
def test_public_page_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    assert b"<html" in r.content.lower()


def test_smoke_lists_agree_with_the_live_gate():
    """A path listed as public must not carry the internal gate, and vice versa."""
    from tests.test_internal_pages_gate import internal_gated_paths

    gated = internal_gated_paths()
    assert not (set(PUBLIC_PATHS) & gated), set(PUBLIC_PATHS) & gated
    assert set(INTERNAL_SMOKE_PATHS) <= gated, set(INTERNAL_SMOKE_PATHS) - gated
