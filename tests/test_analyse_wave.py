"""scripts/analyse_wave.py — cohort metrics, with SERVED and MEASURED kept apart.

The load-bearing case is the one that actually misled a live analysis on 2026-08-27: a voter who
is shown attention checks and abstains on all of them ("both are bad") is SERVED but not
MEASURED. Reading only `gold_seen` made a working scheduler look broken for an hour, because
`gold_outcome` leaves both counters untouched on an abstention by design.

If these two numbers are ever collapsed back into one called "coverage", this file fails.
"""

from __future__ import annotations

import uuid

from app.database import SessionLocal, init_db
from app.models import Comparison, ModelOutput, Vote, VoterSession
from scripts import analyse_wave
from tests.factories import a_task_id, make_outputs, overall_criterion

COHORT = "test-wave"


def _session(db, *, cohort=COHORT, n_votes=10, gold_seen=0, gold_passed=0, trust=1.0) -> str:
    sid = uuid.uuid4().hex
    db.add(
        VoterSession(
            session_id=sid,
            cohort=cohort,
            n_votes=n_votes,
            gold_seen=gold_seen,
            gold_passed=gold_passed,
            trust=trust,
        )
    )
    db.flush()
    return sid


def _gold(db, sid: str, winner: str | None) -> None:
    """Serve a gold check to `sid`, optionally with an answer."""
    outs: list[ModelOutput] = make_outputs(db, 2)
    cmp_ = Comparison(
        task_id=a_task_id(db),
        output_a_id=outs[0].id,
        output_b_id=outs[1].id,
        criterion_id=overall_criterion(db).id,
        session_id=sid,
        is_gold=True,
        gold_expected="a",
    )
    db.add(cmp_)
    db.flush()
    if winner is not None:
        db.add(Vote(comparison_id=cmp_.id, session_id=sid, winner=winner))
        db.flush()


def _clean(db):
    for s in db.query(VoterSession).filter(VoterSession.cohort == COHORT).all():
        db.delete(s)
    db.flush()


def test_an_abstainer_is_SERVED_but_not_MEASURED():
    """The exact shape that made a working scheduler look broken."""
    init_db()
    with SessionLocal() as db:
        _clean(db)
        sid = _session(db, gold_seen=0)  # abstention leaves the counter at 0, by design
        _gold(db, sid, "bad")
        _gold(db, sid, "tie")
        r = analyse_wave.analyse(db, COHORT)
        assert r["voters"] == 1
        assert r["served"] == 1, "served counts the CHECK, not the answer"
        assert r["measured"] == 0, "an abstention yields no trust reading"
        assert r["abstentions"] == 2
        assert r["abstention_rate"] == 1.0
        db.rollback()


def test_a_binary_answer_is_both_served_and_measured():
    """Positive control: without this, the test above passes on a totally broken analyse()."""
    init_db()
    with SessionLocal() as db:
        _clean(db)
        sid = _session(db, gold_seen=1, gold_passed=1)
        _gold(db, sid, "a")
        r = analyse_wave.analyse(db, COHORT)
        assert r["served"] == 1
        assert r["measured"] == 1
        assert r["failed_check"] == 0
        assert r["abstentions"] == 0
        db.rollback()


def test_a_failed_check_is_measured_but_counted_as_failed():
    init_db()
    with SessionLocal() as db:
        _clean(db)
        sid = _session(db, gold_seen=1, gold_passed=0, trust=0.5)
        _gold(db, sid, "b")
        r = analyse_wave.analyse(db, COHORT)
        assert r["measured"] == 1
        assert r["failed_check"] == 1
        assert r["low_trust"] == 1
        db.rollback()


def test_voters_are_counted_by_votes_not_by_sessions():
    """An arena page-load makes a session before anyone votes; the pilot logged 16 for 15."""
    init_db()
    with SessionLocal() as db:
        _clean(db)
        _session(db, n_votes=11)
        _session(db, n_votes=0)  # opened the page, never voted
        r = analyse_wave.analyse(db, COHORT)
        assert r["sessions"] == 2
        assert r["voters"] == 1
        db.rollback()


def test_dominance_share_flags_a_single_voter_over_the_threshold():
    """Pre-registered: >20% from one voter means the frozen counter was not the whole story."""
    init_db()
    with SessionLocal() as db:
        _clean(db)
        _session(db, n_votes=150)
        _session(db, n_votes=10)
        _session(db, n_votes=10)
        r = analyse_wave.analyse(db, COHORT)
        assert r["votes"] == 170
        assert r["top_voter_share"] > 0.2
        assert round(r["top_voter_share"], 2) == 0.88
        db.rollback()


def test_another_cohort_is_not_counted():
    init_db()
    with SessionLocal() as db:
        _clean(db)
        _session(db, n_votes=5)
        _session(db, cohort="someone-else", n_votes=99)
        r = analyse_wave.analyse(db, COHORT)
        assert r["voters"] == 1
        assert r["votes"] == 5
        db.rollback()
