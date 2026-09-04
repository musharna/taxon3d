"""P2 items from the 2026-09-04 repo audit, one test each (see memory repo_health_audit_2026-09-04)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app import config, integrity, service
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Comparison, Vote
from tests.test_calibration_mode import _seed_calibration


def setup_module(_m):
    init_db()


# --- admin token -----------------------------------------------------------------------------

def test_admin_token_compare_is_constant_time(monkeypatch):
    import hmac

    from app import main as main_mod

    seen = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append(True)
        return real(a, b)

    monkeypatch.setattr(main_mod.hmac, "compare_digest", spy)
    with pytest.raises(Exception):
        main_mod._require_admin("wrong")
    assert seen, "_require_admin must compare with hmac.compare_digest, not !="


def test_moderation_actions_do_not_put_the_token_in_the_redirect_url():
    c = TestClient(app)
    r = c.post(
        "/admin/submissions/999999/approve",
        data={"token": config.ADMIN_TOKEN},
        follow_redirects=False,
    )
    if r.status_code == 303:
        assert config.ADMIN_TOKEN not in r.headers["location"]
    # positive control: after a token-bearing GET, the page is reachable WITHOUT the query token
    assert c.get("/admin/moderation", params={"token": config.ADMIN_TOKEN}).status_code == 200
    assert c.get("/admin/moderation").status_code == 200
    # and a fresh client with neither cookie nor token is still refused
    assert TestClient(app).get("/admin/moderation").status_code == 401


# --- upload cap ------------------------------------------------------------------------------

def test_submit_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(config, "SUBMIT_MAX_BYTES", 1024)
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)
    big = io.BytesIO(b"x" * 2048)
    r = TestClient(app).post(
        "/api/submit",
        data={"task_id": 1, "generator_slug": "g"},
        files={"file": ("big.glb", big, "model/gltf-binary")},
    )
    assert r.status_code == 413, r.text


# --- vote race ---------------------------------------------------------------------------------

def test_concurrent_double_vote_is_409_not_500(monkeypatch):
    with SessionLocal() as db:
        _seed_calibration(db)
    owner = TestClient(app)
    cid = owner.get("/api/next?set=calibration").json()["comparison_id"]

    real_dedup = integrity.already_voted_pair

    def racing_dedup(db, *a, **k):
        # A second request for the same ballot commits between our None-check and our INSERT
        # (before this session has taken the write lock), so our flush hits the UNIQUE index.
        with SessionLocal() as other:
            comp = other.get(Comparison, cid)
            other.add(Vote(comparison_id=cid, winner="b", session_id=comp.session_id))
            other.commit()
        return real_dedup(db, *a, **k)

    monkeypatch.setattr(integrity, "already_voted_pair", racing_dedup)
    r = owner.post("/api/vote", json={"comparison_id": cid, "winner": "a"})
    assert r.status_code == 409, r.text


# --- research JSON gating ----------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/completeness.json", "/api/dgen.json"])
def test_research_json_is_internal_only(monkeypatch, path):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False)
    assert TestClient(app).get(path).status_code == 404
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True)
    assert TestClient(app).get(path).status_code == 200  # positive control


# --- /api/next limiter -------------------------------------------------------------------------

def test_api_next_is_rate_limited_per_ip(monkeypatch):
    monkeypatch.setattr(integrity, "check_next_rate_limit", lambda ip: False)
    assert TestClient(app).get("/api/next").status_code == 429


def test_rate_limiter_forgets_idle_keys():
    lim = integrity.InMemoryRateLimiter()
    for i in range(3000):
        lim.allow(f"k{i}")
    for dq in lim._buckets.values():  # simulate every window elapsing
        dq.clear()
    for _ in range(lim.SWEEP_EVERY):
        lim.allow("probe")
    assert len(lim._buckets) < 10, len(lim._buckets)


# --- verified leaderboard memo -----------------------------------------------------------------

def test_verified_leaderboard_reuses_bt_fit_until_votes_change(monkeypatch):
    from app import ranking

    calls = {"bt": 0}
    real_bt = ranking.bradley_terry

    def counting(*a, **k):
        calls["bt"] += 1
        return real_bt(*a, **k)

    monkeypatch.setattr(ranking, "bradley_terry", counting)
    with SessionLocal() as db:
        service.verified_leaderboard_rows(db, "overall", "all")
        service.verified_leaderboard_rows(db, "overall", "all")
    assert calls["bt"] <= 1


# --- duplicated constant -----------------------------------------------------------------------

def test_completeness_uses_the_vote_roster_constant():
    import inspect

    from app import completeness

    src = inspect.getsource(completeness)
    assert '("image_recon", "text_native")' not in src
    assert "ARENA_VOTE_PARADIGMS" in src
