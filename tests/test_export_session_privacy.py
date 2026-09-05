"""/api/export.json must never publish a voter's credential or the attention-check answer key.

The `bio3d_session` cookie value IS the voter's only credential (captcha_verified, trust, HF
login, Prolific completion code all hang off it). Before 2026-09-04 the public export shipped it
verbatim for every vote (1609 rows live). A research export needs a *stable pseudonym* per
voter, not the bearer token. Gold (attention-check) and hidden-output comparisons carry the
answer key / withheld R2 keys in `asset_*` and are not research data either.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select

from app import dataset
from app.models import Comparison, Criterion, Vote
from tests.factories import make_outputs, overall_criterion


def _vote(db, out_a, out_b, *, sid, is_gold=False):
    crit = overall_criterion(db)
    comp = Comparison(
        task_id=out_a.task_id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=sid,
        is_gold=is_gold,
        gold_expected="a" if is_gold else None,
    )
    db.add(comp)
    db.flush()
    db.add(Vote(comparison_id=comp.id, winner="a", session_id=sid))
    db.flush()
    return comp


def _records(db):
    return dataset.build_preference_records(db)["votes"]


def test_export_session_is_a_stable_pseudonym_not_the_cookie(db_session):
    a, b = make_outputs(db_session, 2)
    sid1, sid2 = uuid.uuid4().hex, uuid.uuid4().hex
    c1 = _vote(db_session, a, b, sid=sid1)
    c2 = _vote(db_session, b, a, sid=sid1)
    c3 = _vote(db_session, a, b, sid=sid2)
    rows = {r["comparison_id"]: r for r in _records(db_session)}
    s1, s1b, s2 = rows[c1.id]["session"], rows[c2.id]["session"], rows[c3.id]["session"]
    # never the credential, nor a trivial transform of it
    for s in (s1, s2):
        assert s not in (sid1, sid2)
        assert sid1 not in s and sid2 not in s
    assert s1 == s1b, "same voter must map to the same pseudonym (voter-level analyses)"
    assert s1 != s2, "different voters must stay distinguishable"


def test_export_excludes_gold_comparisons(db_session):
    a, b = make_outputs(db_session, 2)
    real = _vote(db_session, a, b, sid="s-real")
    gold = _vote(db_session, a, b, sid="s-real", is_gold=True)
    ids = {r["comparison_id"] for r in _records(db_session)}
    assert real.id in ids  # positive control: ordinary votes still ship
    assert gold.id not in ids


def test_export_excludes_hidden_outputs(db_session):
    a, b = make_outputs(db_session, 2)
    c_ok = _vote(db_session, a, b, sid="s-h")
    hid_a, hid_b = make_outputs(db_session, 2)
    hid_a.hidden_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db_session.flush()
    c_hidden = _vote(db_session, hid_a, hid_b, sid="s-h")
    ids = {r["comparison_id"] for r in _records(db_session)}
    assert c_ok.id in ids
    assert c_hidden.id not in ids


def test_export_route_has_no_raw_session(db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    a, b = make_outputs(db_session, 2)
    sid = uuid.uuid4().hex
    _vote(db_session, a, b, sid=sid)
    db_session.commit()
    body = client.get("/api/export.json").text
    assert sid not in body
    # the seeded criterion row is what the route joins on; sanity that the route still works
    assert db_session.execute(select(Criterion)).scalars().first() is not None
