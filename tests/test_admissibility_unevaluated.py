# tests/test_admissibility_unevaluated.py
"""The gate must fail CLOSED: an output the rubric applies to, but which no predicate has
actually evaluated, is not admitted.

Before this, `non_admitted_output_ids` was a union of *rejections*, so a missing verdict row was
indistinguishable from an evaluated-and-passed one. That is how release v6 shipped 10 outputs into
the public vote pool with no semantic verdict, 2 of them genuinely inadmissible.

Every test here pairs the negative (unevaluated -> gated) with a positive control in the same
test: an evaluated-and-admitted sibling that must stay admitted. Without the control, a predicate
that gated everything would pass the negative assertion just as well.
"""

from __future__ import annotations

import uuid

from app import admissibility
from app.database import SessionLocal, init_db
from app.models import (
    Admissibility,
    Category,
    Completeness,
    Generator,
    ModelOutput,
    Task,
    TraitRubric,
)

# A taxon the organ inventory actually covers — completeness only applies to those.
COVERED_TAXON = "Solanum lycopersicum"


def setup_module(_m):
    init_db()


def _category(db) -> Category:
    c = Category(slug=f"un-{uuid.uuid4().hex[:8]}", name="c")
    db.add(c)
    db.flush()
    return c


def _task(db, *, taxon: str | None = None) -> Task:
    t = Task(title=f"un-{uuid.uuid4().hex[:8]}", prompt="p", category_id=_category(db).id)
    db.add(t)
    db.flush()
    if taxon is not None:
        db.add(TraitRubric(task_id=t.id, taxon=taxon))
        db.flush()
    return t


def _output(db, task, *, is_gold: bool = False) -> ModelOutput:
    g = Generator(slug=f"un-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    o = ModelOutput(
        task_id=task.id,
        generator_id=g.id,
        asset_path="x.glb",
        asset_format="glb",
        is_gold=is_gold,
    )
    db.add(o)
    db.flush()
    return o


def test_completeness_applicable_but_unscored_is_not_admitted():
    with SessionLocal() as db:
        task = _task(db, taxon=COVERED_TAXON)
        unscored = _output(db, task)
        scored = _output(db, task)
        db.add(
            Completeness(output_id=scored.id, category="complete", score=1.0, scorer_version="v1")
        )
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["completeness"])
        assert unscored.id in gated, "an unscored output on a rubric-bearing task must be gated"
        assert scored.id not in gated, "positive control: an evaluated-and-complete output stays in"


def test_completeness_does_not_gate_a_task_it_does_not_apply_to():
    """Applicability is not the same as evaluation. Completeness only applies to tasks that have a
    TraitRubric with an inventory-covered taxon; an output on any other task is unscored forever by
    design and must NOT be gated for it."""
    with SessionLocal() as db:
        no_rubric = _output(db, _task(db))  # no TraitRubric at all
        uncovered = _output(db, _task(db, taxon="Nothing paniculata"))  # rubric, no inventory
        applicable = _output(db, _task(db, taxon=COVERED_TAXON))  # control: this one IS applicable
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["completeness"])
        assert no_rubric.id not in gated, "no rubric -> completeness does not apply"
        assert uncovered.id not in gated, "rubric with no organ inventory -> does not apply"
        assert applicable.id in gated, "positive control: an applicable unscored output IS gated"


def test_structural_unevaluated_is_not_admitted_but_gold_is_exempt():
    """Structural applies to every non-gold output. Gold outputs are held-out ground truth and are
    never structurally scored, so gating them for it would empty the attention checks."""
    with SessionLocal() as db:
        task = _task(db)
        unscored = _output(db, task)
        gold = _output(db, task, is_gold=True)
        scored = _output(db, task)
        db.add(
            Admissibility(
                output_id=scored.id, predicate="structural", admit=True, version="structural-v1"
            )
        )
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["structural"])
        assert unscored.id in gated, "a non-gold output with no structural verdict must be gated"
        assert gold.id not in gated, "gold is outside structural's applicability set"
        assert scored.id not in gated, "positive control: an admitting verdict stays in"


def test_semantic_unevaluated_is_not_admitted():
    with SessionLocal() as db:
        task = _task(db)
        unscored = _output(db, task)
        scored = _output(db, task)
        db.add(
            Admissibility(
                output_id=scored.id, predicate="semantic", admit=True, version="semantic-v2"
            )
        )
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["semantic"])
        assert unscored.id in gated
        assert scored.id not in gated, "positive control"


def test_a_stale_version_verdict_still_counts_as_evaluated():
    """Bumping a predicate's VERSION must not silently empty the arena. 'Never evaluated' is the
    defect being closed here; 'evaluated by an older scorer' is a different and weaker problem,
    handled by re-running the scorer, not by the gate."""
    with SessionLocal() as db:
        task = _task(db)
        stale = _output(db, task)
        db.add(
            Admissibility(
                output_id=stale.id,
                predicate="structural",
                admit=True,
                version="structural-v0-ancient",
            )
        )
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["structural"])
        assert stale.id not in gated


def test_rejection_and_non_evaluation_are_both_gated_together():
    """The composer is a union of two distinct reasons; neither may mask the other."""
    with SessionLocal() as db:
        task = _task(db, taxon=COVERED_TAXON)
        rejected = _output(db, task)
        unscored = _output(db, task)
        admitted = _output(db, task)
        db.add(
            Completeness(
                output_id=rejected.id, category="fragment", score=0.0, scorer_version="v1"
            )
        )
        db.add(
            Completeness(
                output_id=admitted.id, category="complete", score=1.0, scorer_version="v1"
            )
        )
        db.commit()

        gated = admissibility.non_admitted_output_ids(db, rubric=["completeness"])
        assert rejected.id in gated and unscored.id in gated
        assert admitted.id not in gated, "positive control"


def test_assert_rubric_coverage_names_the_unevaluated_outputs():
    """A release must fail LOUD on an unscored output, not quietly ship a smaller bundle.

    Silent shrinkage is the specific trap that cost a release once already: the export's posture
    default dropped 232 provider-licensed outputs and reported success. With the gate failing
    closed, an unscored output would now be filtered out the same silent way — so the export asks
    this question first and refuses."""
    import pytest

    with SessionLocal() as db:
        task = _task(db, taxon=COVERED_TAXON)
        unscored = _output(db, task)
        scored = _output(db, task)
        db.add(
            Completeness(output_id=scored.id, category="complete", score=1.0, scorer_version="v1")
        )
        db.commit()

        # Positive control: a fully-evaluated set passes silently.
        admissibility.assert_rubric_coverage(db, {scored.id}, rubric=["completeness"])

        with pytest.raises(admissibility.UnevaluatedOutputs) as exc:
            admissibility.assert_rubric_coverage(
                db, {scored.id, unscored.id}, rubric=["completeness"]
            )
        msg = str(exc.value)
        assert "completeness" in msg, "the message must name the predicate that has no verdict"
        assert str(unscored.id) in msg, "and the offending output id"
        assert str(scored.id) not in msg, "but not the one that was evaluated"


def test_assert_rubric_coverage_ignores_outputs_outside_the_request():
    """Scoped to the ids asked about: an unscored output that is NOT being released is not the
    release's problem."""
    with SessionLocal() as db:
        task = _task(db, taxon=COVERED_TAXON)
        shipping = _output(db, task)
        not_shipping = _output(db, task)  # unscored, but not in the bundle
        db.add(
            Completeness(
                output_id=shipping.id, category="complete", score=1.0, scorer_version="v1"
            )
        )
        db.commit()
        assert not_shipping.id is not None  # referenced so the fixture is not dead code
        admissibility.assert_rubric_coverage(db, {shipping.id}, rubric=["completeness"])
