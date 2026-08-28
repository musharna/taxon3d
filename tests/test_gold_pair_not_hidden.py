"""A gold pair with a hidden member must never be served — its assets 404.

Measured on live prod 2026-08-28: three of six gold pairs carry a hidden output, and hidden
outputs 404 on /media/o/{id} (@9638b86).

    task 12 Zea mays   good 404  bad 404   -> voter sees two broken viewers
    task 11 Solanum    good 200  bad 404   -> nothing to spot; the check is trivially passable
    task 20 Glycine    good 200  bad 404   -> same
    task 13 Pinus      good 200  bad 200   -> loads; its 81% abstention is a sliver, not a 404

`pick_gold_pair` chose uniformly over every GoldPair with no visibility filter, so it kept serving
them. Zea mays' 75% "both are bad" is that: the honest answer when neither mesh renders. It is
also why a served check can consume a voter's ballot and measure nothing.
"""

from __future__ import annotations

from app import matchmaking
from app.database import SessionLocal, init_db
from app.models import GoldPair, _utcnow
from tests.factories import a_task_id, make_outputs


def _pair(db, *, hide_good=False, hide_bad=False):
    good, bad = make_outputs(db, 2)
    for o in (good, bad):
        o.is_gold = True
    if hide_good:
        good.hidden_at = _utcnow()
    if hide_bad:
        bad.hidden_at = _utcnow()
    db.flush()
    gp = GoldPair(task_id=a_task_id(db), good_output_id=good.id, bad_output_id=bad.id)
    db.add(gp)
    db.flush()
    return gp


def _clear(db):
    for gp in db.query(GoldPair).all():
        db.delete(gp)
    db.flush()


def test_a_pair_with_a_hidden_GOOD_member_is_not_served():
    init_db()
    with SessionLocal() as db:
        _clear(db)
        _pair(db, hide_good=True)
        assert matchmaking.pick_gold_pair(db) is None
        db.rollback()


def test_a_pair_with_a_hidden_DECOY_is_not_served():
    """A missing decoy makes the check trivially passable — it measures nothing."""
    init_db()
    with SessionLocal() as db:
        _clear(db)
        _pair(db, hide_bad=True)
        assert matchmaking.pick_gold_pair(db) is None
        db.rollback()


def test_a_fully_visible_pair_is_served():
    """Positive control: without this, the tests above pass on a function that returns None."""
    init_db()
    with SessionLocal() as db:
        _clear(db)
        gp = _pair(db)
        got = matchmaking.pick_gold_pair(db)
        assert got is not None and got.id == gp.id
        db.rollback()


def test_only_the_visible_pair_is_ever_chosen():
    """With a mixed pool, the hidden ones must never come up across many draws."""
    init_db()
    with SessionLocal() as db:
        _clear(db)
        _pair(db, hide_good=True)
        _pair(db, hide_bad=True)
        ok = _pair(db)
        for _ in range(40):
            got = matchmaking.pick_gold_pair(db)
            assert got is not None and got.id == ok.id
        db.rollback()
