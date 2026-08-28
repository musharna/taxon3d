"""A gold pair's GOOD member must be SCORED like any other output — but never GATED on.

Wave 2 (2026-08-27) measured two of six good members drawing 75% and 81% "both are bad", while
voters who DID pick a side were right 86 of 91 times. The decoys discriminate fine; the good
members simply were not good, and nothing caught it — every gold output was exempt from both
scoring predicates, so all 12 carried zero completeness and zero admissibility rows while 92% of
ordinary outputs were scored.

The exemption was written for outputs "the semantic judge cannot fairly read" — a raw reference
scan and an untextured mesh. A gold GOOD member is neither: it is an ordinary textured mesh whose
entire job is to be visibly good, which is exactly the claim a verdict checks. The DECOY keeps the
exemption, being deliberately degenerate.

TWO SETS, AND THEY ARE NOT THE SAME ONE. `applicable_output_ids` feeds the ADMISSION gate — what
may not be exported for lack of a verdict. `enumerate_*_work` feeds the SCORING queue. A good
member belongs in the queue but NOT in the gate: it is never admitted to the corpus, so demanding
a verdict for it fail-closes a bundle over an asset the bundle does not contain. Widening both at
once broke two export/import tests; the last test here is that regression's control.
"""

from __future__ import annotations

from app import integrity, semantic, structural
from app.database import SessionLocal, init_db
from app.models import GoldPair
from tests.factories import a_task_id, make_outputs


def _gold_pair(db):
    """A gold pair over two fresh outputs, both flagged is_gold as production does."""
    good, bad = make_outputs(db, 2)
    for o in (good, bad):
        o.is_gold = True
    db.flush()
    db.add(GoldPair(task_id=a_task_id(db), good_output_id=good.id, bad_output_id=bad.id))
    db.flush()
    return good, bad


def test_gold_good_output_ids_names_only_the_good_member():
    init_db()
    with SessionLocal() as db:
        good, bad = _gold_pair(db)
        ids = integrity.gold_good_output_ids(db)
        assert good.id in ids
        assert bad.id not in ids, (
            "the decoy is degenerate on purpose; a verdict on it measures nothing"
        )
        db.rollback()


def test_the_structural_scoring_queue_picks_up_the_gold_good_member():
    init_db()
    with SessionLocal() as db:
        good, bad = _gold_pair(db)
        queue = set(structural.enumerate_structural_work(db))
        assert good.id in queue, "an unscored good member is exactly what wave 2 shipped"
        assert bad.id not in queue
        db.rollback()


def test_the_semantic_scoring_queue_picks_up_the_gold_good_member():
    init_db()
    with SessionLocal() as db:
        good, bad = _gold_pair(db)
        queue = {w["output_id"] for w in semantic.enumerate_semantic_work(db)}
        assert good.id in queue
        assert bad.id not in queue
        db.rollback()


def test_the_admission_gate_does_NOT_demand_a_verdict_for_gold():
    """The regression two export/import tests caught when both sets were widened together.

    Gold is not shipped in a bundle, so an unscored good member must not fail-close an export.
    Scored, yes; gated on, no.
    """
    init_db()
    with SessionLocal() as db:
        good, bad = _gold_pair(db)
        for applicable in (
            structural.applicable_output_ids(db),
            semantic.applicable_output_ids(db),
        ):
            assert good.id not in applicable, (
                "gating export on gold fail-closes over an absent asset"
            )
            assert bad.id not in applicable
        db.rollback()


def test_an_ordinary_output_is_unaffected():
    """Positive control: the change must not narrow either set for normal outputs."""
    init_db()
    with SessionLocal() as db:
        (plain,) = make_outputs(db, 1)
        db.flush()
        assert plain.id in structural.applicable_output_ids(db)
        assert plain.id in semantic.applicable_output_ids(db)
        assert plain.id in set(structural.enumerate_structural_work(db))
        db.rollback()


def test_a_gold_output_that_is_no_pairs_good_member_stays_exempt():
    """Membership in gold_pair.good_output_id earns the verdict — not the is_gold flag."""
    init_db()
    with SessionLocal() as db:
        (orphan,) = make_outputs(db, 1)
        orphan.is_gold = True
        db.flush()
        assert orphan.id not in set(structural.enumerate_structural_work(db))
        assert orphan.id not in {w["output_id"] for w in semantic.enumerate_semantic_work(db)}
        db.rollback()
