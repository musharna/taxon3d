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

from app.models import Generator, ModelOutput, Task
from app.seed import seed_all
from scripts import export_hf_dataset as hf
from tests.factories import mark_evaluated


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture
def commercial_output(db_session):
    """A commercial-API output: present under display, dropped under redistribute.

    Without this the positive control cannot fail, because the seed has no commercial sources.

    Uses conftest.py's `db_session` (outer transaction, rolled back on teardown) rather than a
    committing session: this row and the Admissibility verdicts `mark_evaluated` writes for it
    never outlive this test, and never leak into other modules (e.g. test_public_export.py's
    display-posture assertions) sharing the suite's one temp-DB engine.
    """
    task = db_session.execute(select(Task)).scalars().first()
    gen = db_session.execute(select(Generator)).scalars().first()
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        title="commercial fixture",
        asset_path="fixtures/commercial.glb",
        source="api:fixture-vendor",
        license="proprietary",
    )
    db_session.add(o)
    db_session.flush()
    # A hand-built fixture has no structural/semantic verdict, unlike a real generated output
    # (ingest.register_output runs the structural evaluator at generation time). Without this,
    # assert_rubric_coverage's "never evaluated" refusal fires on the fixture itself, before the
    # licence gate ever runs, and the test raises UnevaluatedOutputs instead of exercising the
    # posture filter it exists to test.
    mark_evaluated(db_session, o)
    return o


def _all_titles_and_slugs(db_session):
    titles = [t.title for t in db_session.execute(select(Task)).scalars()]
    slugs = [g.slug for g in db_session.execute(select(Generator)).scalars()]
    return titles, slugs


def test_redistribute_drops_commercial_sources(db_session, commercial_output):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    assert commercial_output.id not in inc.output_ids
    for oid in inc.output_ids:
        o = db_session.get(ModelOutput, oid)
        assert not o.source.startswith(("api:", "recon:", "frontier:")), o.source


def test_gold_outputs_are_emptied(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    assert inc.gold_output_ids == set()
    for oid in inc.output_ids:
        assert db_session.get(ModelOutput, oid).is_gold is False


def test_display_yields_more_than_redistribute(db_session, commercial_output):
    """THE POSITIVE CONTROL. If these are equal the filter is not running and every other
    assertion in this module is vacuous."""
    titles, slugs = _all_titles_and_slugs(db_session)
    strict = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    loose = hf.resolve_hf_include(
        db_session, task_titles=titles, generator_slugs=slugs, posture="display"
    )
    assert len(loose.output_ids) > len(strict.output_ids), (
        "display and redistribute returned the same set — the posture filter is inert"
    )


FORBIDDEN_KEYS = {"is_gold", "gold_expected"}


def test_no_table_leaks_gold_columns(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    assert set(tables) == {
        "outputs",
        "admissibility",
        "completeness",
        "votes",
        "judge_ratings",
    }
    for name, rows in tables.items():
        for row in rows:
            leaked = FORBIDDEN_KEYS & set(row)
            assert not leaked, f"{name} row leaked {leaked}"


def test_votes_exclude_gold_comparisons(db_session):
    from app.models import Comparison

    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    votes = hf.build_tables(db_session, inc)["votes"]
    gold_pairs = {
        (c.output_a_id, c.output_b_id)
        for c in db_session.execute(
            select(Comparison).where(Comparison.is_gold.is_(True))
        ).scalars()
    }
    for row in votes:
        assert (row["output_a_id"], row["output_b_id"]) not in gold_pairs


def test_every_vote_row_references_shipped_outputs(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    shipped = {r["output_id"] for r in tables["outputs"]}
    for row in tables["votes"]:
        assert row["output_a_id"] in shipped
        assert row["output_b_id"] in shipped


def test_admissibility_rows_reference_shipped_outputs(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    shipped = {r["output_id"] for r in tables["outputs"]}
    assert tables["admissibility"], "no admissibility rows — the headline table is empty"
    for row in tables["admissibility"]:
        assert row["output_id"] in shipped


def test_outputs_carry_licence_and_attribution_fields(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    for row in hf.build_tables(db_session, inc)["outputs"]:
        assert "license" in row and "attribution" in row
        assert row["mesh_path"] == f"meshes/{row['output_id']}.glb"
