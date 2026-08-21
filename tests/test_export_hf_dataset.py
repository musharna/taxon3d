"""The HF dataset export must ship only what we are cleared to redistribute.

The positive control is the load-bearing test here. `seed_all` creates ZERO commercial-source
outputs (verified 2026-08-20: `grep -cE 'source="(api|recon|frontier):' app/seed.py` -> 0), so a
redistribute filter that never ran would produce exactly the same set as one that ran perfectly,
and every "nothing leaked" assertion below would pass on broken code. The fixture therefore
inserts a commercial output on purpose, and `test_display_yields_more_than_redistribute` fails
if the filter is inert.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
import yaml
from sqlalchemy import select

from app import reference_provenance
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


@pytest.fixture
def hidden_output(db_session):
    """An output that passes EVERY other gate and is withdrawn only by `hidden_at`.

    Constructed to make the hidden filter the sole reason it is absent, because that is the only
    way the assertion can fail on broken code:

    - `source="bio3d-arena"` -> `public_export.is_own_output` is True, so the redistribute licence
      filter keeps it even with `license=None`.
    - `mark_evaluated` -> structural + semantic verdicts exist, so neither `assert_rubric_coverage`
      nor `non_admitted_output_ids` touches it.
    - `is_gold=False` (default) -> the gold purge does not reach it.
    - `asset_path` is copied from a real shipped output, so `copy_meshes` finds bytes on disk. With
      a fabricated path, deleting the hidden filter would make this test fail with
      FileNotFoundError from copy_meshes — a green-to-red transition for the wrong reason, which is
      indistinguishable from a broken fixture.

    `test_end_to_end_tree_is_clean`'s `assert o.hidden_at is None` looked like it covered this and
    did not: `app/seed.py` never sets `hidden_at` (`grep -c hidden_at app/seed.py` -> 0, verified
    2026-08-20), so it asserted None over rows that could not have been anything else.
    """
    task = db_session.execute(select(Task)).scalars().first()
    gen = db_session.execute(select(Generator)).scalars().first()
    donor = (
        db_session.execute(
            select(ModelOutput).where(
                ModelOutput.source == "bio3d-arena", ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .first()
    )
    assert donor is not None, "no bio3d-arena output in the seed to borrow a real asset_path from"
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        title="hidden fixture",
        asset_path=donor.asset_path,
        source="bio3d-arena",
        hidden_at=dt.datetime(2026, 8, 19, 12, 0, 0),
    )
    db_session.add(o)
    db_session.flush()
    mark_evaluated(db_session, o)
    return o


@pytest.fixture
def recon_output(db_session):
    """An internal recon: `source="bio3d-arena"` WITH a recorded `input_image`.

    That combination is exactly what `reference_provenance.assert_recon_photos_cleared` treats as
    a recon needing its input photo cleared (`is_internal_recon`), and it is otherwise fully
    shippable — own source, evaluated, not gold, not hidden. So whether it survives
    `resolve_hf_include` is decided by the photo-clearance gate alone.
    """
    task = db_session.execute(select(Task)).scalars().first()
    gen = db_session.execute(select(Generator)).scalars().first()
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        title="internal recon fixture",
        asset_path="fixtures/internal-recon.glb",
        source="bio3d-arena",
        meta_json=json.dumps({"input_image": "reference/testonly_fixture_ref.jpg"}),
    )
    db_session.add(o)
    db_session.flush()
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


def test_hidden_outputs_never_ship(db_session, tmp_path, hidden_output):
    """A withdrawn output must not reach the tarball, at any posture.

    The live site enforces this per request — `/media/o/{id}` 404s a hidden output — and a flat
    download has no request to intercept, so the enforcement point moves to export. Both reasons an
    output gets hidden are irreversible once published: automatic withdrawal on voter flags
    (app/flags.py) and manual withdrawal for a rights reason
    (scripts/disposition_rose_soybean.py).
    """
    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    assert inc.output_ids, "empty include set — the exclusion below would pass vacuously"
    assert hidden_output.id not in inc.output_ids

    # Hidden-ness is not a posture question. The looser posture must drop it too, otherwise the
    # filter is riding on the redistribute licence rules rather than on hidden_at.
    loose = hf.resolve_hf_include(
        db_session, task_titles=titles, generator_slugs=slugs, posture="display"
    )
    assert hidden_output.id not in loose.output_ids

    # ...and on disk, through the real writer, not just in the include set.
    hf.export_hf(db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path)
    shipped = {
        json.loads(line)["output_id"]
        for line in (tmp_path / "outputs.jsonl").read_text().splitlines()
    }
    assert shipped, "no rows in outputs.jsonl"
    assert hidden_output.id not in shipped
    assert not (tmp_path / "meshes" / f"{hidden_output.id}.glb").exists()


def test_withdrawn_bytes_never_ship_under_a_twins_id(db_session, tmp_path, hidden_output):
    """A withdrawn mesh must not ship under a DIFFERENT output's id.

    Two `ModelOutput` rows may point at one `asset_path` — by design for gold decoys
    (`public_export.effective_provenance` exists for exactly that), and in practice outside that
    case too: on data/study/arena-study.db, row 322 is hidden while row 100 is visible and both
    name `uploads/6a22a1f2eddb43ff837582abcf5c436d.glb` (verified 2026-08-20).

    Keying the withdrawal on the ROW is correct for the live site and wrong here. `/media/o/322`
    404s while `/media/o/100` keeps serving the same bytes, and that is fine because hiding is a
    per-publication act that can be undone. `copy_meshes` copies `root / o.asset_path`, so an
    export writes those same bytes as `meshes/100.glb` — and a published tarball cannot be undone.

    The decisive fact is that `hidden_at` records no reason (app/models.py:121-123 is a bare
    nullable timestamp). It is written by voter-flag withdrawal (app/flags.py:55-56) and by
    licensing withdrawal (scripts/disposition_rose_soybean.py:73-77) alike, so the export cannot
    tell "this render is bad" from "we may not distribute these bytes". Under that ambiguity the
    costs are asymmetric: over-filtering loses one row of corpus, under-filtering is an
    unretractable distribution. So the asset is withdrawn if ANY row claiming it is hidden.

    The `hidden_output` fixture borrows `donor.asset_path` from a real shipped output, which is
    what makes the aliasing real rather than staged.
    """
    donor = (
        db_session.execute(
            select(ModelOutput).where(
                ModelOutput.asset_path == hidden_output.asset_path,
                ModelOutput.id != hidden_output.id,
                ModelOutput.hidden_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    assert donor is not None, "fixture no longer aliases a visible output — the test is vacuous"

    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)

    # POSITIVE CONTROL, in the same run: the export still ships outputs whose asset nobody
    # withdrew. Without it, a filter that dropped everything would satisfy the assertion below.
    survivors = inc.output_ids - {donor.id, hidden_output.id}
    assert survivors, "nothing survived — a broken gate chain would pass the exclusion vacuously"

    assert donor.id not in inc.output_ids, (
        f"output {donor.id} ships bytes withdrawn as output {hidden_output.id}"
        f" ({hidden_output.asset_path})"
    )

    hf.export_hf(db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path)
    assert not (tmp_path / "meshes" / f"{donor.id}.glb").exists()
    shipped = {
        json.loads(line)["output_id"]
        for line in (tmp_path / "outputs.jsonl").read_text().splitlines()
    }
    assert donor.id not in shipped
    assert shipped, "no rows in outputs.jsonl"


def test_recon_photo_gate_runs_and_raises(db_session, recon_output, monkeypatch):
    """The reference-photo clearance gate must be REACHED, and must raise rather than drop.

    `cleared_reference_images()` reads sidecars from the gitignored `data/` tree, so this is the
    gate most likely to fire on a real first export and the one whose failure mode matters most:
    silently dropping an uncleared recon would ship a short corpus and report success, while
    raising names the output and the photo.

    Both directions are asserted in one test. The positive control (cleared -> the output is in
    the include set) is what proves the gate is actually on this code path and that the negative
    result below is the gate refusing, not the output being filtered out for some unrelated
    reason.
    """
    titles, slugs = _all_titles_and_slugs(db_session)
    real = reference_provenance.cleared_reference_images()
    photo = "testonly_fixture_ref.jpg"
    assert photo not in real

    # Cleared: the gate is reached, passes, and the recon ships.
    monkeypatch.setattr(reference_provenance, "cleared_reference_images", lambda: real | {photo})
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    assert recon_output.id in inc.output_ids, (
        "the recon fixture is filtered out before the photo gate — the refusal below would prove"
        " nothing about this output"
    )

    # Un-cleared: the same call raises, naming this output. Not a bare Exception — a
    # ReferenceProvenanceError is the gate refusing; anything else is a different bug wearing the
    # same green tick.
    monkeypatch.setattr(reference_provenance, "cleared_reference_images", lambda: real)
    with pytest.raises(
        reference_provenance.ReferenceProvenanceError,
        match=rf"output {recon_output.id}: recon input",
    ):
        hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)


def test_rubric_coverage_asks_only_about_outputs_this_posture_ships(db_session):
    """Coverage is scoped to the posture-eligible set, and that scoping cuts both ways.

    Direction 1 — an unevaluated output that WOULD ship must still abort the export. The gate
    treats "never evaluated" as "not admitted", so without the refusal it would vanish and the
    export would report success on a short corpus. Asserted first, because it is what the scoping
    could plausibly have broken.

    Direction 2 — an unevaluated output the redistribute licence filter drops anyway
    (`source="api:"`) must NOT abort it. Coverage used to be asked of the raw pre-filter set, so a
    commercial output nobody had scored could fail an export that was never going to include it.

    Deliberately does not use the `commercial_output` fixture: that one calls `mark_evaluated`, so
    it cannot exercise direction 2 at all.
    """
    from app.admissibility import UnevaluatedOutputs

    titles, slugs = _all_titles_and_slugs(db_session)
    task = db_session.execute(select(Task)).scalars().first()
    gen = db_session.execute(select(Generator)).scalars().first()

    def _add(source):
        o = ModelOutput(
            task_id=task.id,
            generator_id=gen.id,
            title=f"unevaluated {source}",
            asset_path=f"fixtures/unevaluated-{source.replace(':', '-')}.glb",
            source=source,
        )
        db_session.add(o)
        db_session.flush()
        return o

    # Direction 2 first: a commercial output with no verdict at all must be invisible to coverage.
    _add("api:fixture-vendor")
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    assert inc.output_ids, "include set went empty — coverage would be vacuous either way"

    # Direction 1: an own-source (therefore shipping) output with no verdict must abort.
    shipping = _add("bio3d-arena")
    with pytest.raises(UnevaluatedOutputs, match=str(shipping.id)):
        hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)


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


def test_judge_ratings_carry_the_category_scope(db_session, judge_rating_row):
    """`uq_judge_rating_scope` is (generator_id, category_id, criterion_id, view_condition).
    Dropping category_id from the export is harmless only while every row is NULL; once
    per-kingdom judge boards populate, two rows would share a key and disagree about bt_score with
    nothing in the file to tell them apart. Asserted with a real category-scoped row rather than
    just checking the key exists, because a hardcoded `"category": None` would pass that."""
    from app.models import Category

    cat = db_session.execute(select(Category)).scalars().first()
    assert cat is not None
    scoped = JudgeRating(
        generator_id=judge_rating_row.generator_id,
        category_id=cat.id,
        criterion_id=judge_rating_row.criterion_id,
        view_condition=judge_rating_row.view_condition,
    )
    db_session.add(scoped)
    db_session.flush()

    titles, slugs = _all_titles_and_slugs(db_session)
    inc = hf.resolve_hf_include(db_session, task_titles=titles, generator_slugs=slugs)
    rows = hf.build_tables(db_session, inc)["judge_ratings"]
    cats = {r["category"] for r in rows}
    assert cat.slug in cats, f"category-scoped rating lost its scope: {cats}"
    assert None in cats, "the all-kingdoms row must keep a null category, not be coerced"


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
        # No `if not src.exists(): continue` guard here, deliberately. copy_meshes raises
        # FileNotFoundError on the first missing source (test_missing_source_mesh_raises), so the
        # guard could never fire — and if copy_meshes were ever changed to skip instead of raise,
        # that same guard would silently turn this byte-identity test into a no-op.
        src = Path(config.ASSET_DIR) / db_session.get(ModelOutput, oid).asset_path
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
    # The gold/attention-check exclusion is the one bullet whose disappearance is not merely a
    # documentation gap: readers who do not know decoy pairs were withheld will read the corpus
    # as a complete sample of what voters saw. Substring checks on the other two bullets already
    # existed; this one did not, so it could have been deleted with the suite still green.
    low = card.lower()
    assert "attention-check" in low and "gold" in low, "gold-exclusion bullet is gone from the card"

    # Parse the YAML front matter for real. Hugging Face reads this block to set the dataset's
    # licence and tags; a card whose YAML does not parse renders as a wall of raw text on the hub,
    # and no substring assertion above would notice, because every substring would still be there.
    assert card.startswith("---\n")
    front_matter = card.split("---\n", 2)[1]
    meta = yaml.safe_load(front_matter)
    assert isinstance(meta, dict), f"front matter is not a YAML mapping: {meta!r}"
    assert meta["license"] == "cc-by-4.0"
    assert "3d" in meta["tags"] and "biology" in meta["tags"]
    assert meta["task_categories"], "task_categories must not be empty"

    transform = (tmp_path / "TRANSFORM.md").read_text()
    assert "mesh_compress" in transform
    assert "texture_downscale" in transform


def test_end_to_end_tree_is_clean(db_session, tmp_path, commercial_output):
    """Real execution against a real DB and a real directory — assert on what landed on disk."""

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


def test_export_refuses_non_empty_out_dir(db_session, tmp_path):
    """A second run into the same directory must refuse, not merge.

    `meshes/` is keyed on output id, so a stale mesh is not overwritten — it survives beside
    tables that no longer list it and is uploaded anyway. The concrete danger: `resolve_hf_include`
    accepts `posture="display"`, so an earlier display-posture run can leave commercial-API meshes
    in `meshes/`, and a redistribute run reusing the directory would publish them under a card
    that says commercial outputs are excluded.
    """
    stale = tmp_path / "meshes"
    stale.mkdir()
    (stale / "999999.glb").write_bytes(b"stale mesh from an earlier run")

    titles, slugs = _all_titles_and_slugs(db_session)
    with pytest.raises(RuntimeError, match="non-empty directory"):
        hf.export_hf(db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path)

    # Refuse, don't clean: deleting a directory the caller named is not this script's call.
    assert (stale / "999999.glb").exists()
    assert not (tmp_path / "outputs.jsonl").exists()


def test_manifest_carries_a_licence_histogram(db_session, tmp_path):
    """The card declares a blanket `license: cc-by-4.0`, but `REDISTRIBUTABLE_LICENSES` also
    admits CC-BY-SA and ODbL — share-alike terms that are not narrower than CC-BY. The publisher
    needs the real per-item mix visible before upload, which is why export_public.py has always
    written one."""
    titles, slugs = _all_titles_and_slugs(db_session)
    manifest = hf.export_hf(db_session, task_titles=titles, generator_slugs=slugs, out_dir=tmp_path)
    assert "licenses" in manifest
    hist = manifest["licenses"]
    assert hist, "licence histogram is empty"
    assert sum(hist.values()) == manifest["counts"]["outputs"], (
        "histogram does not account for every shipped output"
    )
    on_disk = json.loads((tmp_path / "manifest.json").read_text())
    assert on_disk["licenses"] == hist


def test_export_hf_empty_include_set_raises(db_session, tmp_path):
    """A zero-output export must never report success — that's the exact failure this whole
    gate chain exists to prevent (see module docstring / task brief). Driven through the real
    gate chain with task/generator names that match nothing in the seeded DB, not a monkeypatched
    IncludeSet, so a break upstream in resolve_hf_include's own filtering would also be caught
    here rather than only by this guard.
    """
    out = tmp_path / "should-not-be-created"
    with pytest.raises(RuntimeError, match="include set is empty"):
        hf.export_hf(
            db_session,
            task_titles=["nonexistent-task-title-zzz"],
            generator_slugs=["nonexistent-generator-slug-zzz"],
            out_dir=out,
        )
    assert not out.exists(), "raise happened after disk writes started"
