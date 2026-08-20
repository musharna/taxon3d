"""Export the redistribute-cleared Taxon3D corpus as a Hugging Face dataset.

Separate from `export_public.py` because the OUTPUT SHAPE differs: flat `meshes/<id>.glb` plus
JSONL tables, versus the site bundle's `rows.json` + `assets/<original path>` + `gt/` + LODs +
manifest. Both share the licence and admissibility gate by IMPORTING it — a second copy of that
predicate is how a mesh we have no right to ship would eventually ship.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import admissibility
from app import public_export
from app.public_export import IncludeSet
from app.reference_provenance import (
    assert_recon_photos_cleared,
    assert_recon_photos_cleared_for_gold,
)


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
