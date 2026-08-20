"""Export the redistribute-cleared Taxon3D corpus as a Hugging Face dataset.

Separate from `export_public.py` because the OUTPUT SHAPE differs: flat `meshes/<id>.glb` plus
JSONL tables, versus the site bundle's `rows.json` + `assets/<original path>` + `gt/` + LODs +
manifest. Both share the licence and admissibility gate by IMPORTING it — a second copy of that
predicate is how a mesh we have no right to ship would eventually ship.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import admissibility
from app import config
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


def _asset_root() -> Path:
    """Indirection so a test can point the root somewhere empty and prove we fail loud."""
    return Path(config.ASSET_DIR)


_CARD = """---
license: cc-by-4.0
task_categories:
  - image-to-3d
  - text-to-3d
tags:
  - 3d
  - biology
  - organisms
  - human-preference
  - evaluation
---

# Taxon3D — an admissibility-gated organism corpus

Generated 3D models of organisms, each carrying a **reference-free judgement of whether it is
biologically admissible** — not just whether people liked it.

Most 3D generation benchmarks rank outputs by human preference alone. Taxon3D runs every candidate
through a pre-vote admissibility gate first: an output is admitted only if it passes *every*
predicate (structural integrity, semantic identity, organism completeness). The `admissibility`
table is that judgement, and it is the point of this dataset.

Live arena: https://taxon3d.org

## Contents

| file | rows | what it is |
| --- | --- | --- |
| `meshes/<output_id>.glb` | {n_meshes} | Original, uncompressed meshes |
| `admissibility.jsonl` | {n_admissibility} | **The headline.** One row per (output, predicate) |
| `completeness.jsonl` | {n_completeness} | Per-organ checklist behind the completeness predicate |
| `outputs.jsonl` | {n_outputs} | Taxon, kingdom, paradigm, generator, licence, attribution |
| `votes.jsonl` | {n_votes} | Resolved human pairwise comparisons |
| `judge_ratings.jsonl` | {n_judge_ratings} | VLM-judge Bradley-Terry ratings per generator |

## What is NOT here, and why

- **Commercial-API outputs.** A large part of the live arena is generated by commercial services
  whose terms permit display but not redistribution. Those outputs are excluded here. **This corpus
  is therefore not the whole arena**, and per-generator counts are not a sample of it.
- **Reference / input photos.** Recon outputs are reconstructed from photographs. The photos are
  not redistributed here, so recon rows are not end-to-end reproducible from this dataset alone.
- **Attention-check assets.** The arena uses gold decoy pairs to detect inattentive voters.
  Publishing them would destroy the mechanism, so they and their comparisons are excluded.

## Licence

`CC-BY-4.0`. Per-item attribution is in `outputs.jsonl` (`license`, `attribution`,
`external_url`) — honour the per-item terms, which may be narrower than the collection licence.

## Meshes are originals, not what voters saw

Every admissibility verdict was computed by rendering the **original** mesh, so the originals ship.
The live site serves a compressed derivative. See `TRANSFORM.md` to reproduce exactly what voters
saw. This matters: presentation affects preference, so the mesh a judgement refers to is part of
the judgement.
"""

_TRANSFORM = """# Reproducing the voter-facing meshes

`meshes/` holds ORIGINALS. The meshes voters actually saw were derived at export time by two
deterministic steps, in this order:

1. **Texture downscale** — `app/texture_downscale.py`, PIL Lanczos resize re-encoded as WebP
   quality 97. (Measured alternative for the record: `gltf-transform resize` scored 28.5 dB PSNR
   against 43.5 dB for this path, which is why it is not used.)
2. **Geometry compression** — `app/mesh_compress.py`, Draco via `gltf-transform`. Kept only when
   it actually shrinks the file; when Draco enlarges a mesh the original ships unchanged.

Both live in the public repository: https://github.com/musharna/taxon3d

The `votes.jsonl` rows refer to the derived meshes. The `admissibility.jsonl` and
`completeness.jsonl` rows refer to the originals in `meshes/`.
"""


def write_cards(out_dir: Path, tables: dict[str, list[dict]], n_meshes: int) -> None:
    """Write the public-facing README.md dataset card and TRANSFORM.md into out_dir.

    Counts are read from `tables`/`n_meshes`, never hardcoded — a stale hardcoded count would be
    a lie that survives every future export.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(
        _CARD.format(
            n_meshes=n_meshes,
            n_outputs=len(tables["outputs"]),
            n_admissibility=len(tables["admissibility"]),
            n_completeness=len(tables["completeness"]),
            n_votes=len(tables["votes"]),
            n_judge_ratings=len(tables["judge_ratings"]),
        ),
        encoding="utf-8",
    )
    (out / "TRANSFORM.md").write_text(_TRANSFORM, encoding="utf-8")


def copy_meshes(db: Session, inc: IncludeSet, out_dir: Path) -> int:
    """Copy each cleared output's ORIGINAL mesh to out_dir/meshes/<output_id>.glb.

    No Draco, no texture downscale, no LOD. The site bundle applies those to a COPY at export
    time; the admissibility verdicts describe the original, so the original is what ships.
    Renaming to <output_id>.glb also drops the descriptive asset keys (e.g.
    `commissioned/openrouter-anthropic-claude-opus-4-8_11.glb`) and gives the tables a join key.
    """
    meshes = Path(out_dir) / "meshes"
    meshes.mkdir(parents=True, exist_ok=True)
    root = _asset_root()
    written = 0
    for oid in sorted(inc.output_ids):
        o = db.get(ModelOutput, oid)
        src = root / o.asset_path
        if not src.exists():
            raise FileNotFoundError(f"output {oid}: asset missing at {src}")
        shutil.copyfile(src, meshes / f"{oid}.glb")
        written += 1
    return written
