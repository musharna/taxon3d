"""Export the redistribute-cleared Taxon3D corpus as a Hugging Face dataset.

Separate from `export_public.py` because the OUTPUT SHAPE differs: flat `meshes/<id>.glb` plus
JSONL tables, versus the site bundle's `rows.json` + `assets/<original path>` + `gt/` + LODs +
manifest. Both share the licence and admissibility gate by IMPORTING it — a second copy of that
predicate is how a mesh we have no right to ship would eventually ship.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admissibility
from app import public_export
from app.kingdoms import KINGDOM_OF
from app.models import (
    Admissibility,
    Category,
    Comparison,
    Completeness,
    Criterion,
    Generator,
    JudgeRating,
    ModelOutput,
    Task,
    Vote,
)
from app.public_export import IncludeSet
from app.reference_provenance import (
    assert_recon_photos_cleared,
    assert_recon_photos_cleared_for_gold,
)


def _iso(value):
    return value.isoformat() if value is not None else None


def resolve_hf_include(
    db: Session,
    *,
    task_titles: list[str],
    generator_slugs: list[str],
    posture: str = "redistribute",
) -> IncludeSet:
    """Run the full export gate chain and return the cleared include set.

    `posture` exists so the test suite can run this identical path at "display" and assert it
    yields strictly more outputs. Without that control, a filter that never ran is
    indistinguishable from one that ran perfectly, because the seed corpus contains no
    commercial-source outputs.

    Gold outputs are emptied unconditionally: `ModelOutput.is_gold` marks attention-check decoys
    and `Comparison.gold_expected` records the answer, so publishing either lets anyone pass the
    check and collapses `trust = (gold_passed + 1) / (gold_seen + 1)`.
    """
    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs
    )
    # Refuse BEFORE filtering: the gate treats "never evaluated" as "not admitted", so an unscored
    # output would otherwise vanish silently and the export would report success on a short corpus.
    admissibility.assert_rubric_coverage(db, inc.output_ids | inc.gold_output_ids)
    gated = admissibility.non_admitted_output_ids(db)
    public_export.filter_include_for_posture(db, inc, posture, gated)
    public_export.filter_gold_for_posture(db, inc, posture, gated)
    if posture == "redistribute":
        public_export.check_licenses(db, inc.output_ids)
        assert_recon_photos_cleared(db, inc.output_ids)
        assert_recon_photos_cleared_for_gold(db, inc.gold_output_ids)
    # Gold never ships from THIS export, at any posture. filter_gold_for_posture narrows the set
    # for licensing; we drop it entirely for anti-gaming.
    inc.gold_output_ids = set()
    return inc


def build_tables(db: Session, inc: IncludeSet) -> dict[str, list[dict]]:
    """Build the five JSONL tables for the cleared include set.

    Every table is keyed on `output_id`, and every row emitted here must reference an output that
    is actually shipping — a verdict pointing at a mesh nobody can see is worse than no verdict.
    """
    oids = sorted(inc.output_ids)
    oid_set = set(oids)

    outputs: list[dict] = []
    for oid in oids:
        o = db.get(ModelOutput, oid)
        task = db.get(Task, o.task_id)
        gen = db.get(Generator, o.generator_id)
        cat = db.get(Category, task.category_id)
        outputs.append(
            {
                "output_id": o.id,
                "task_title": task.title,
                "kingdom": KINGDOM_OF.get(cat.slug, "unknown"),
                "paradigm": gen.paradigm,
                "generator_slug": gen.slug,
                "source": o.source,
                "license": o.license,
                "attribution": o.attribution,
                "external_url": o.external_url,
                "created": _iso(o.created),
                "mesh_path": f"meshes/{o.id}.glb",
            }
        )

    adm = [
        {
            "output_id": a.output_id,
            "predicate": a.predicate,
            "admit": a.admit,
            "reason": a.reason,
            "detail_json": a.detail_json,
            "version": a.version,
            "computed": _iso(a.computed),
        }
        for a in db.execute(
            select(Admissibility).where(Admissibility.output_id.in_(oids))
        ).scalars()
    ]

    comp = [
        {
            "output_id": c.output_id,
            "category": c.category,
            "score": c.score,
            "checklist_json": c.checklist_json,
            "judge_model": c.judge_model,
            "scorer_version": c.scorer_version,
            "computed": _iso(c.computed),
        }
        for c in db.execute(select(Completeness).where(Completeness.output_id.in_(oids))).scalars()
    ]

    # Resolved votes only, gold comparisons excluded, and BOTH sides must be shipping — otherwise
    # a row would point at a mesh the reader cannot inspect.
    votes: list[dict] = []
    rows = db.execute(
        select(Comparison, Vote)
        .join(Vote, Vote.comparison_id == Comparison.id)
        .where(Comparison.is_gold.is_(False))
    ).all()
    for c, v in rows:
        if c.output_a_id not in oid_set or c.output_b_id not in oid_set:
            continue
        crit = db.get(Criterion, c.criterion_id)
        votes.append(
            {
                "output_a_id": c.output_a_id,
                "output_b_id": c.output_b_id,
                "winner": v.winner,
                "criterion": crit.slug if crit else None,
                "created": _iso(v.created),
            }
        )

    # Only generators that actually have a mesh in this dataset. Written as a plain set
    # comprehension rather than a truthiness `and` — a generator id of 0 would silently drop out
    # of an `and`-based filter.
    gen_ids = {db.get(ModelOutput, r["output_id"]).generator_id for r in outputs}
    judge = [
        {
            "generator_slug": db.get(Generator, j.generator_id).slug,
            "criterion": (db.get(Criterion, j.criterion_id).slug if j.criterion_id else None),
            "view_condition": j.view_condition,
            "bt_score": j.bt_score,
            "bt_lower": j.bt_lower,
            "bt_upper": j.bt_upper,
            "n_games": j.n_games,
            "judge_model": j.judge_model,
            "updated": _iso(j.updated),
        }
        for j in db.execute(
            select(JudgeRating).where(JudgeRating.generator_id.in_(gen_ids))
        ).scalars()
    ]

    return {
        "outputs": outputs,
        "admissibility": adm,
        "completeness": comp,
        "votes": votes,
        "judge_ratings": judge,
    }
