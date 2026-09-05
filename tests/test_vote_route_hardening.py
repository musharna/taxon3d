"""Vote-route hardening from the 2026-09-04 audit.

Three defects, one file: (1) /api/vote and /api/kvote accepted a ballot id that was served to
a DIFFERENT session — ids are sequential, so a script could consume other voters' ballots and
have its gold answers scored against the wrong session; (2) the captcha check (a blocking
outbound siteverify call) ran BEFORE the rate limits, so junk requests cost a network round-trip
each; (3) calibration ballots skipped the hidden-output gate that every other builder applies.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import integrity
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Comparison, KBallot, ModelOutput
from tests.test_calibration_mode import _seed_calibration


def setup_module(_m):
    init_db()


def _own_calibration_ballot(client: TestClient) -> int:
    r = client.get("/api/next?set=calibration")
    assert r.status_code == 200 and "comparison_id" in r.json(), r.text
    return r.json()["comparison_id"]


def test_vote_on_another_sessions_comparison_is_refused():
    with SessionLocal() as db:
        _seed_calibration(db)
    owner, stranger = TestClient(app), TestClient(app)
    cid = _own_calibration_ballot(owner)
    r = stranger.post("/api/vote", json={"comparison_id": cid, "winner": "a"})
    assert r.status_code == 403, r.text
    # positive control: the session the ballot was served to can still vote it
    r = owner.post("/api/vote", json={"comparison_id": cid, "winner": "a"})
    assert r.status_code == 200, r.text


def test_kvote_on_another_sessions_ballot_is_refused():
    with SessionLocal() as db:
        _seed_calibration(db)
        outs = db.query(ModelOutput).order_by(ModelOutput.id.desc()).limit(2).all()
        ballot = KBallot(
            task_id=outs[0].task_id,
            criterion_id=db.query(Comparison).count() or 1,
            session_id=f"someone-else-{uuid.uuid4().hex}",
            output_ids_json=json.dumps([o.id for o in outs]),
        )
        db.add(ballot)
        db.commit()
        bid = ballot.id
    stranger = TestClient(app)
    r = stranger.post("/api/kvote", json={"ballot_id": bid, "best_output_id": None})
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("route,payload", [
    ("/api/vote", {"comparison_id": 1, "winner": "a"}),
    ("/api/kvote", {"ballot_id": 1, "best_output_id": None}),
])
def test_rate_limit_is_checked_before_captcha(monkeypatch, route, payload):
    calls = {"captcha": 0}

    def counting_captcha(db, sid, token):
        calls["captcha"] += 1
        return False

    monkeypatch.setattr(integrity, "captcha_ok_for_session", counting_captcha)
    monkeypatch.setattr(integrity, "check_rate_limit", lambda sid: False)
    r = TestClient(app).post(route, json=payload)
    assert r.status_code == 429
    assert calls["captcha"] == 0, "a rate-limited request must not cost a siteverify call"
    # positive control: once the limiter admits the request, the captcha gate does run
    monkeypatch.setattr(integrity, "check_rate_limit", lambda sid: True)
    monkeypatch.setattr(integrity, "check_ip_rate_limit", lambda ip: True)
    r = TestClient(app).post(route, json=payload)
    assert r.status_code == 403
    assert calls["captcha"] == 1


def test_calibration_skips_pairs_with_a_hidden_output():
    with SessionLocal() as db:
        _seed_calibration(db)
        o = db.query(ModelOutput).order_by(ModelOutput.id.desc()).first()
        o.hidden_at = dt.datetime.utcnow()
        db.commit()
    body = TestClient(app).get("/api/next?set=calibration").json()
    assert body.get("done") is True, body
    assert body["progress"] == {"voted": 0, "total": 0}
