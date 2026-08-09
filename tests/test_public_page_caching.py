"""Public pages are edge-cacheable, and a cacheable response never carries a cookie.

WHY THIS EXISTS. No HTML response set a `Cache-Control` header, and Cloudflare does not cache
HTML without one, so every page view reached Postgres. A 447-request crawl became 447 full
database renders and exhausted the project's monthly transfer quota, which suspended the
database and took the site down. Once search engines index the 86 sitemap URLs, their crawlers
do the same thing on a schedule.

THE HAZARD THIS GUARDS. The naive fix — add `Cache-Control: public` — is a vulnerability here.
`ensure_session` issues a session cookie to any request that arrives without one, and a crawler
never sends cookies, so the very responses a crawler triggers are the ones carrying
`Set-Cookie`. A shared cache storing one of those would serve one visitor's session id to
everybody who followed. The session id is what identifies a voter's ballot history.

So the mechanism was never "we forgot a header" — it was that EVERY response is personalized,
which is what makes every response uncacheable. The fix removes the personalization from pages
that never needed it: a page that neither reads nor writes a session does not get a cookie, and
only such a page may be publicly cached.

`test_no_response_is_both_cookie_bearing_and_publicly_cacheable` is the load-bearing one. The
others exist so it cannot pass by making everything uncacheable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

#: Read-only pages: no forms, no per-visitor content, safe for a shared cache to hold.
PUBLIC_CACHEABLE = [
    "/",
    "/leaderboard",
    "/models",
    "/organisms",
    "/dataset",
    "/methodology",
    "/terms",
    "/privacy",
    "/licenses",
    "/coverage",
]

#: Pages that are session-bearing, user-specific or write-capable. These MUST stay uncached.
NEVER_CACHEABLE = ["/arena", "/submit"]


def _fresh(path: str):
    """A request with NO cookies — a crawler, or a first-time visitor.

    A shared TestClient persists cookies between calls, which would hide the entire bug: the
    second request onward would arrive WITH a session cookie, so `ensure_session` would not
    issue one and every response would look cookie-free.
    """
    return TestClient(app).get(path)


def _is_public_cacheable(resp) -> bool:
    cc = resp.headers.get("cache-control", "").lower()
    return "public" in cc or "s-maxage" in cc


@pytest.mark.parametrize("path", PUBLIC_CACHEABLE + NEVER_CACHEABLE)
def test_no_response_is_both_cookie_bearing_and_publicly_cacheable(path):
    """The security invariant. A cached `Set-Cookie` is one visitor's session handed to all."""
    resp = _fresh(path)
    if not _is_public_cacheable(resp):
        return  # uncacheable responses may set whatever cookies they like
    assert "set-cookie" not in {k.lower() for k in resp.headers}, (
        f"{path} is publicly cacheable AND sets a cookie "
        f"({resp.headers.get('set-cookie')!r}). A shared cache would store that cookie and "
        "serve one visitor's session id to everyone who requested the page afterwards."
    )


@pytest.mark.parametrize("path", PUBLIC_CACHEABLE)
def test_public_pages_are_edge_cacheable(path):
    """Positive control: without this, the invariant above passes trivially by caching nothing —
    which is exactly the state that took the site down."""
    resp = _fresh(path)
    assert resp.status_code == 200, resp.text[:200]
    cc = resp.headers.get("cache-control", "")
    assert "s-maxage" in cc.lower(), (
        f"{path} has no shared-cache directive (Cache-Control: {cc!r}), so Cloudflare will not "
        "cache it and every crawler hit reaches Postgres."
    )


@pytest.mark.parametrize("path", NEVER_CACHEABLE)
def test_session_bearing_pages_are_not_publicly_cached(path):
    """The other direction: the arena serves a ballot built for one session. Caching it at the
    edge would hand every visitor the same comparison and the same session."""
    resp = _fresh(path)
    assert not _is_public_cacheable(resp), (
        f"{path} is publicly cacheable (Cache-Control: "
        f"{resp.headers.get('cache-control')!r}) but is session-specific."
    )


def test_the_arena_still_issues_a_session_cookie():
    """Positive control for the cookie change itself.

    Suppressing the cookie on cacheable pages must not suppress it where voting needs it. If
    this fails, every ballot is anonymous to the dedup logic and voters get repeat comparisons.
    """
    resp = _fresh("/arena")
    cookies = resp.headers.get_list("set-cookie")
    assert any("session" in c.lower() for c in cookies), (
        f"/arena issued no session cookie; vote dedup and history depend on it. Got: {cookies}"
    )


def test_a_public_page_issues_no_session_cookie():
    """The change that MAKES caching safe, asserted directly rather than inferred from the
    invariant — which would also pass if the page simply stopped being cacheable."""
    cookies = _fresh("/privacy").headers.get_list("set-cookie")
    assert not any("session" in c.lower() for c in cookies), (
        f"/privacy issued a session cookie, so it can never be publicly cached. Got: {cookies}"
    )


def test_the_checker_would_catch_a_violation():
    """Negative control for `_is_public_cacheable`.

    Every assertion above is skipped or satisfied when this helper returns False, so a helper
    that never fires would turn the whole module green while the bug shipped.
    """

    class _R:
        def __init__(self, cc):
            self.headers = {"cache-control": cc} if cc else {}

    assert _is_public_cacheable(_R("public, s-maxage=300"))
    assert _is_public_cacheable(_R("s-maxage=60"))
    assert not _is_public_cacheable(_R("private, no-store"))
    assert not _is_public_cacheable(_R(None))
