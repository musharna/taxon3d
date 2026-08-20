"""Assets must not carry `Set-Cookie`, or a shared cache refuses to hold them.

Measured against the live instance on 2026-08-20, minutes after taxon3d.org went behind the
Cloudflare proxy. Nothing was being cached at the edge, and the two cache statuses named two
different faults:

* `GET /static/og-default.png` -> **BYPASS**. `.png` is on Cloudflare's default-cacheable
  extension list, so the edge wanted to cache it and declined. The response carried
  `Set-Cookie: bio3d_session=...`, freshly minted on every request, and Cloudflare will not
  cache a response that sets a cookie.
* `GET /media/o/400.glb` -> **DYNAMIC**, i.e. never eligible at all. That is the separate,
  dashboard-side Cache Rule question; this module does not test it.

`ensure_session` suppresses the cookie only for paths in `_CACHEABLE_PATHS` — the read-only HTML
pages. Asset routes were never in that set, so every mesh and every static file minted a session,
and the proxy flip bought nothing on the bytes that actually dominate a ballot.

The fix must stay on our side of the wire. Cloudflare CAN be told to cache a `Set-Cookie`
response, and doing so here would be a vulnerability, not a shortcut: the edge would hand one
visitor's `bio3d_session` to every visitor served that cached mesh afterwards, collapsing vote
dedup and the gold/trust accounting onto a single identity. `ensure_session`'s own docstring
already names this hazard for HTML pages; assets are the same hazard on a bigger payload.

The positive control matters as much as the assertion. A middleware that simply stopped issuing
cookies would pass every "no Set-Cookie" check while breaking voting, so each test that asserts
absence on an asset also asserts the cookie is still issued on `/arena` in the same run.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import SESSION_COOKIE, app
from app.models import ModelOutput
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture
def visitor() -> TestClient:
    """A first-time visitor: no cookie jar, exactly like a crawler or a cold browser."""
    return TestClient(app)


@pytest.fixture(scope="module")
def output_id() -> int:
    with SessionLocal() as db:
        o = db.query(ModelOutput).filter(ModelOutput.asset_path.isnot(None)).first()
        assert o is not None, "seed produced no output with an asset"
        return o.id


def _sets_session(resp) -> bool:
    return any(
        SESSION_COOKIE in v for k, v in resp.headers.items() if k.lower() == "set-cookie"
    )


def test_a_mesh_does_not_mint_a_session(visitor, output_id):
    """The bytes that dominate a ballot. This is the response Cloudflare must be able to hold."""
    asset = visitor.get(f"/media/o/{output_id}.glb")
    assert asset.status_code == 200
    assert not _sets_session(asset), (
        "mesh response carries Set-Cookie, so a shared cache will not store it"
    )

    # Positive control, same run: the cookie must still be issued where a session is real.
    live = TestClient(app).get("/arena")
    assert live.status_code == 200
    assert _sets_session(live), (
        "no session issued on /arena either — the middleware is broken, not scoped"
    )


def test_a_static_file_does_not_mint_a_session(visitor):
    static = visitor.get("/static/favicon.svg")
    assert static.status_code == 200
    assert not _sets_session(static), (
        "static asset carries Set-Cookie; measured live as cf-cache-status: BYPASS"
    )

    live = TestClient(app).get("/arena")
    assert live.status_code == 200
    assert _sets_session(live), (
        "no session issued on /arena either — the middleware is broken, not scoped"
    )


def test_an_asset_request_alone_never_establishes_a_session(visitor, output_id):
    """A visitor who only ever fetches assets must end with an empty cookie jar.

    Guards the whole-visit shape rather than one response: if any asset route still minted a
    session, a mesh prefetch would silently identify the visitor before they ever loaded a page.
    """
    visitor.get(f"/media/o/{output_id}.glb")
    visitor.get("/static/favicon.svg")
    visitor.get("/static/shell.js")
    assert SESSION_COOKIE not in visitor.cookies, (
        f"assets alone established {SESSION_COOKIE}={visitor.cookies.get(SESSION_COOKIE)!r}"
    )
