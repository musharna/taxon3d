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

from app.models import Comparison, Criterion, Generator, JudgeRating, ModelOutput, Task, Vote
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


@pytest.fixture
def voted_comparisons(db_session, commercial_output):
    """Three real Comparison+Vote pairs exercising the votes-table filters independently.

    `app/seed.py`'s `_FORCE_DELETE_MODELS` wipes `Vote` and `Comparison` on every
    `seed_all(force=True)` and nothing recreates them, so without this fixture
    `tables["votes"]` is empty in every test run and the exclusion loops below iterate zero
    rows — passing identically whether the filtering logic is correct or deleted outright.

    - `normal`: both sides shipped, is_gold=False -> MUST appear in tables["votes"].
    - `gold`: is_gold=True with gold_expected set, on a THIRD shipped output so its pair
      tuple cannot collide with `normal`'s (a,b) -> MUST be excluded by the is_gold filter
      alone.
    - `non_shipping`: is_gold=False but one side is `commercial_output` (dropped by the
      redistribute filter) -> MUST be excluded by the oid_set membership check alone,
      independent of is_gold.

    Built from real shipped output ids (via `resolve_hf_include`, the same call the tests
    make) rather than fabricated numbers, so "shipped" here means exactly what build_tables
    means by it.
    """
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    oids = sorted(inc.output_ids)
    assert len(oids) >= 3, "need >=3 shipped outputs to build a non-colliding fixture"
    a, b, c = oids[0], oids[1], oids[2]
    output_a = db_session.get(ModelOutput, a)
    crit = db_session.execute(select(Criterion)).scalars().first()

    normal = Comparison(
        task_id=output_a.task_id,
        output_a_id=a,
        output_b_id=b,
        criterion_id=crit.id,
        session_id="fixture-normal",
        is_gold=False,
    )
    gold = Comparison(
        task_id=output_a.task_id,
        output_a_id=a,
        output_b_id=c,
        criterion_id=crit.id,
        session_id="fixture-gold",
        is_gold=True,
        gold_expected="a",
    )
    non_shipping = Comparison(
        task_id=output_a.task_id,
        output_a_id=a,
        output_b_id=commercial_output.id,
        criterion_id=crit.id,
        session_id="fixture-nonshipping",
        is_gold=False,
    )
    db_session.add_all([normal, gold, non_shipping])
    db_session.flush()
    db_session.add_all(
        [
            Vote(comparison_id=normal.id, winner="a", session_id="fixture-normal"),
            Vote(comparison_id=gold.id, winner="a", session_id="fixture-gold"),
            Vote(comparison_id=non_shipping.id, winner="a", session_id="fixture-nonshipping"),
        ]
    )
    db_session.flush()
    return {"normal": (a, b), "gold": (a, c), "non_shipping": (a, commercial_output.id)}


@pytest.fixture
def judge_rating_row(db_session, voted_comparisons):
    """One real JudgeRating row for a generator that actually has a shipped output, so
    tables["judge_ratings"] is non-empty and its non-emptiness guard has something to guard.
    """
    output_a = db_session.get(ModelOutput, voted_comparisons["normal"][0])
    crit = db_session.execute(select(Criterion)).scalars().first()
    jr = JudgeRating(
        generator_id=output_a.generator_id,
        criterion_id=crit.id,
        view_condition="single-view",
    )
    db_session.add(jr)
    db_session.flush()
    return jr


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


def test_no_table_leaks_gold_columns(db_session, voted_comparisons, judge_rating_row):
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


def test_votes_exclude_gold_comparisons(db_session, voted_comparisons):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    votes = hf.build_tables(db_session, inc)["votes"]
    # A table that excluded EVERYTHING would also pass the exclusion loop below — guard
    # non-emptiness first so that failure mode fails loudly instead of passing silently.
    assert votes, "no vote rows — the exclusion assertions below would pass vacuously"
    gold_pairs = {
        (c.output_a_id, c.output_b_id)
        for c in db_session.execute(
            select(Comparison).where(Comparison.is_gold.is_(True))
        ).scalars()
    }
    assert gold_pairs, "no gold comparisons in the DB — the fixture didn't create one"
    for row in votes:
        assert (row["output_a_id"], row["output_b_id"]) not in gold_pairs
    # The normal (non-gold, shipped) pair must actually be present, not just absent-of-gold.
    seen = {(r["output_a_id"], r["output_b_id"]) for r in votes}
    assert voted_comparisons["normal"] in seen


def test_every_vote_row_references_shipped_outputs(db_session, voted_comparisons):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    assert tables["votes"], "no vote rows — the membership assertions below would pass vacuously"
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


def test_judge_ratings_reference_shipped_generators(db_session, judge_rating_row):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    assert tables["judge_ratings"], "no judge_ratings rows — the table is empty"
    shipped_slugs = {r["generator_slug"] for r in tables["outputs"]}
    for row in tables["judge_ratings"]:
        assert row["generator_slug"] in shipped_slugs


def test_outputs_carry_licence_and_attribution_fields(db_session):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    for row in hf.build_tables(db_session, inc)["outputs"]:
        assert "license" in row and "attribution" in row
        assert row["mesh_path"] == f"meshes/{row['output_id']}.glb"


def test_meshes_are_byte_identical_originals(db_session, tmp_path):
    """Uncompressed and unmodified. The admissibility verdicts describe THESE bytes — every
    verdict was computed by rendering ASSET_DIR/asset_path (app/judge_render.py:114), so any
    transform here silently decouples the headline table from the meshes it describes."""
    from pathlib import Path

    from app import config

    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    written = hf.copy_meshes(db_session, inc, tmp_path)

    assert written > 0
    for oid in inc.output_ids:
        src = Path(config.ASSET_DIR) / db_session.get(ModelOutput, oid).asset_path
        if not src.exists():
            continue
        dst = tmp_path / "meshes" / f"{oid}.glb"
        assert dst.exists(), f"missing mesh for output {oid}"
        assert dst.read_bytes() == src.read_bytes(), f"mesh {oid} was modified in transit"


def test_missing_source_mesh_raises(db_session, tmp_path, monkeypatch):
    """Fail loud, never skip. A short export that reports success is the failure mode this
    whole gate chain exists to prevent."""
    from pathlib import Path

    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    monkeypatch.setattr(hf, "_asset_root", lambda: Path(tmp_path / "definitely-not-here"))
    with pytest.raises(FileNotFoundError):
        hf.copy_meshes(db_session, inc, tmp_path)


def test_card_states_the_licence_and_the_exclusions(db_session, tmp_path):
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    tables = hf.build_tables(db_session, inc)
    hf.write_cards(tmp_path, tables, n_meshes=len(tables["outputs"]))

    card = (tmp_path / "README.md").read_text()
    assert "CC-BY-4.0" in card
    # A reader must not infer the corpus is the whole arena.
    assert "commercial" in card.lower()
    # Recon inputs are absent by design; say so rather than let it look like an oversight.
    assert "reference photo" in card.lower() or "input photo" in card.lower()

    transform = (tmp_path / "TRANSFORM.md").read_text()
    assert "mesh_compress" in transform
    assert "texture_downscale" in transform


def test_end_to_end_tree_is_clean(db_session, tmp_path, commercial_output):
    """Real execution against a real DB and a real directory — assert on what landed on disk."""
    import json

    titles, slugs = _all_titles_and_slugs(db_session)
    manifest = hf.export_hf(db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path)

    for name in ("outputs", "admissibility", "completeness", "votes", "judge_ratings"):
        path = tmp_path / f"{name}.jsonl"
        assert path.exists(), f"{name}.jsonl not written"
        for line in path.read_text().splitlines():
            row = json.loads(line)
            assert "is_gold" not in row and "gold_expected" not in row

    assert (tmp_path / "README.md").exists()
    assert (tmp_path / "TRANSFORM.md").exists()

    shipped_ids = {
        json.loads(line)["output_id"]
        for line in (tmp_path / "outputs.jsonl").read_text().splitlines()
    }
    assert commercial_output.id not in shipped_ids
    for oid in shipped_ids:
        o = db_session.get(ModelOutput, oid)
        assert o.is_gold is False
        assert o.hidden_at is None
        assert not o.source.startswith(("api:", "recon:", "frontier:"))
        assert o.license is not None or o.source.startswith("bio3d-arena")

    assert manifest["posture"] == "redistribute"
    assert manifest["counts"]["outputs"] == len(shipped_ids)


def test_dry_run_writes_nothing(db_session, tmp_path):
    titles, slugs = _all_titles_and_slugs(db_session)
    manifest = hf.export_hf(
        db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path, dry_run=True
    )
    assert manifest["dry_run"] is True
    assert not (tmp_path / "outputs.jsonl").exists()
    assert not (tmp_path / "meshes").exists()
