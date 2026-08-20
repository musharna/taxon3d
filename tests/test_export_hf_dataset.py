"""The HF dataset export must ship only what we are cleared to redistribute.

The positive control is the load-bearing test here. `seed_all` creates ZERO commercial-source
outputs (verified 2026-08-20: `grep -cE 'source="(api|recon|frontier):' app/seed.py` -> 0), so a
redistribute filter that never ran would produce exactly the same set as one that ran perfectly,
and every "nothing leaked" assertion below would pass on broken code. The fixture therefore
inserts a commercial output on purpose, and `test_display_yields_more_than_redistribute` fails
if the filter is inert.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Admissibility, Generator, ModelOutput, Task
from app.seed import seed_all
from scripts import export_hf_dataset as hf
from tests.factories import mark_evaluated


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def commercial_output(db):
    """A commercial-API output: present under display, dropped under redistribute.

    Without this the positive control cannot fail, because the seed has no commercial sources.
    """
    task = db.execute(select(Task)).scalars().first()
    gen = db.execute(select(Generator)).scalars().first()
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        title="commercial fixture",
        asset_path="fixtures/commercial.glb",
        source="api:fixture-vendor",
        license="proprietary",
    )
    db.add(o)
    db.flush()
    # A hand-built fixture has no structural/semantic verdict, unlike a real generated output
    # (ingest.register_output runs the structural evaluator at generation time). Without this,
    # assert_rubric_coverage's "never evaluated" refusal fires on the fixture itself, before the
    # licence gate ever runs, and the test raises UnevaluatedOutputs instead of exercising the
    # posture filter it exists to test.
    mark_evaluated(db, o)
    db.commit()
    yield o
    # FK enforcement is ON: the Admissibility rows mark_evaluated wrote for this output must go
    # first, or the DELETE on model_output raises IntegrityError instead of cleaning up.
    db.query(Admissibility).filter_by(output_id=o.id).delete()
    db.delete(o)
    db.commit()


def _all_titles_and_slugs(db):
    titles = [t.title for t in db.execute(select(Task)).scalars()]
    slugs = [g.slug for g in db.execute(select(Generator)).scalars()]
    return titles, slugs


def test_redistribute_drops_commercial_sources(db, commercial_output):
    titles, slugs = _all_titles_and_slugs(db)
    inc = hf.resolve_hf_include(db, task_titles=titles, generator_slugs=slugs)
    assert commercial_output.id not in inc.output_ids
    for oid in inc.output_ids:
        o = db.get(ModelOutput, oid)
        assert not o.source.startswith(("api:", "recon:", "frontier:")), o.source


def test_gold_outputs_are_emptied(db):
    titles, slugs = _all_titles_and_slugs(db)
    inc = hf.resolve_hf_include(db, task_titles=titles, generator_slugs=slugs)
    assert inc.gold_output_ids == set()
    for oid in inc.output_ids:
        assert db.get(ModelOutput, oid).is_gold is False


def test_display_yields_more_than_redistribute(db, commercial_output):
    """THE POSITIVE CONTROL. If these are equal the filter is not running and every other
    assertion in this module is vacuous."""
    titles, slugs = _all_titles_and_slugs(db)
    strict = hf.resolve_hf_include(db, task_titles=titles, generator_slugs=slugs)
    loose = hf.resolve_hf_include(db, task_titles=titles, generator_slugs=slugs, posture="display")
    assert len(loose.output_ids) > len(strict.output_ids), (
        "display and redistribute returned the same set — the posture filter is inert"
    )
