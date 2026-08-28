"""`pick_good_output` must choose a TYPICAL, admissible mesh — not merely the most-compared one.

The old rule was `sort(key=lambda o: (-o.n_comparisons, o.id))`, justified by a docstring claim
that "any complete textured mesh trounces the triangle decoy, so the exact pick is not critical".
Wave 2 falsified that. Two of six gold pairs drew 75% and 81% "both are bad", and their good
members measured extent_ratio 0.107 and 0.150 — 3rd and 7th percentile of the corpus, and far
below their own taxon's medians (0.326 and 0.435 over 68 and 59 peer meshes). Voters shown a
sliver beside a triangle decline to prefer either, which is an abstention, and an abstention
yields no trust reading at all.

Most-compared is not a quality signal: a bad mesh that happens to be shown often accumulates
comparisons exactly as a good one does. Rank on admissibility, then completeness, then closeness
to the taxon's median shape — and keep n_comparisons only as a tiebreak among equals.
"""

from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import Admissibility, Completeness, ModelOutput
from scripts import reseed_gold
from tests.factories import a_task_id, make_outputs


def _shape(db, out: ModelOutput, extent_ratio: float, *, admit=True, complete=True):
    """Give an output the verdict rows the picker should now be reading."""
    db.add(
        Admissibility(
            output_id=out.id,
            predicate="structural",
            admit=admit,
            reason="" if admit else "degenerate",
            detail_json=json.dumps({"extent_ratio": extent_ratio}),
            version=1,
        )
    )
    db.add(
        Completeness(
            output_id=out.id,
            category="complete" if complete else "fragment",
            score=1.0 if complete else 0.2,
        )
    )
    db.flush()


def test_a_heavily_compared_sliver_loses_to_a_typical_mesh():
    """The wave-2 failure, reduced: the most-compared candidate is the least typical."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        sliver, typical, other = make_outputs(db, 3)
        for o in (sliver, typical, other):
            o.task_id = tid
            o.asset_format = "glb"
        sliver.n_comparisons = 500  # most-seen by far — the OLD rule picks this
        typical.n_comparisons = 3
        other.n_comparisons = 2
        db.flush()
        _shape(db, sliver, 0.107)  # 3rd-percentile sliver, like output 322
        _shape(db, typical, 0.330)  # near the taxon median
        _shape(db, other, 0.320)
        db.flush()

        pick = reseed_gold.pick_good_output(db, tid)
        assert pick is not None
        assert pick.id != sliver.id, "most-compared is not a quality signal"
        assert pick.id in (typical.id, other.id)
        db.rollback()


def test_an_inadmissible_mesh_is_never_picked():
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        rejected, ok = make_outputs(db, 2)
        for o in (rejected, ok):
            o.task_id = tid
            o.asset_format = "glb"
        rejected.n_comparisons = 900
        ok.n_comparisons = 1
        db.flush()
        _shape(db, rejected, 0.330, admit=False)
        _shape(db, ok, 0.330)
        db.flush()
        assert reseed_gold.pick_good_output(db, tid).id == ok.id
        db.rollback()


def test_a_fragment_loses_to_a_complete_mesh():
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        frag, whole = make_outputs(db, 2)
        for o in (frag, whole):
            o.task_id = tid
            o.asset_format = "glb"
        frag.n_comparisons = 900
        whole.n_comparisons = 1
        db.flush()
        _shape(db, frag, 0.330, complete=False)
        _shape(db, whole, 0.330)
        db.flush()
        assert reseed_gold.pick_good_output(db, tid).id == whole.id
        db.rollback()


def test_n_comparisons_still_breaks_ties_between_equals():
    """Positive control: the old signal is retained where it is legitimate — among equals."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        seen, unseen = make_outputs(db, 2)
        for o in (seen, unseen):
            o.task_id = tid
            o.asset_format = "glb"
        seen.n_comparisons = 40
        unseen.n_comparisons = 0
        db.flush()
        _shape(db, seen, 0.330)
        _shape(db, unseen, 0.330)  # identical shape and verdicts
        db.flush()
        assert reseed_gold.pick_good_output(db, tid).id == seen.id
        db.rollback()


def test_unscored_candidates_still_yield_a_pick():
    """A DB with no verdicts at all must not return None — fall back to the old ordering."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        a, b = make_outputs(db, 2)
        for o in (a, b):
            o.task_id = tid
            o.asset_format = "glb"
        a.n_comparisons = 7
        b.n_comparisons = 1
        db.flush()
        assert reseed_gold.pick_good_output(db, tid).id == a.id
        db.rollback()
