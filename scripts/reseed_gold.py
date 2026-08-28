"""Re-seed gold attention-check pairs for an existing (non-fresh-seed) DB.

A gold pair shows a clearly-good output vs an obviously-bad decoy with a known answer;
an attentive voter must pick the good one (the arena serves these at config.GOLD_RATE and
uses them to score voter trust, never feeding rankings). Both referenced outputs are
is_gold=True so they stay out of normal matchmaking + rankings.

Unlike app.seed._seed_gold (synthetic good shape, for fresh DBs), this pairs a REAL
high-quality output — referenced as an anonymous is_gold copy, no file duplication — against
a degenerate-triangle decoy, so the check is maximally unambiguous (real plant vs triangle).
Idempotent: a task that already has a GoldPair is skipped. Run with the study env set."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.assets_gen import build_degenerate  # noqa: E402
from app.models import (  # noqa: E402
    Admissibility,
    Completeness,
    Generator,
    GoldPair,
    ModelOutput,
    Task,
)
from app.sourcing import is_reference_scan, is_untextured_output  # noqa: E402

# Recon tasks with rich textured output sets — good variety for repeat voters.
DEFAULT_TASK_IDS = [11, 12, 13, 19, 20, 10]


def _vote_excluded(o: ModelOutput) -> bool:
    """Same exclusion the perceptual vote pool uses: raw scans + untextured blobs."""
    return is_reference_scan(o.source) or is_untextured_output(o)


def pick_good_output(db, task_id: int) -> ModelOutput | None:
    """Best textured, votable, non-gold GLB output for a task (the 'good' answer).

    Ranked on QUALITY, then typicality, with n_comparisons kept only as a tiebreak among equals:

        1. no failing admissibility verdict
        2. completeness == "complete"
        3. closest to the task's MEDIAN extent_ratio  (typicality)
        4. most-compared, then lowest id                (stable tiebreak)

    This used to sort on `-n_comparisons` alone, justified by "any complete textured mesh trounces
    the triangle decoy, so the exact pick is not critical". Wave 2 falsified that claim. Two of six
    pairs drew 75% and 81% "both are bad", and their good members measured extent_ratio 0.107 and
    0.150 — 3rd and 7th percentile of the corpus, and far below their own taxa's medians (0.326
    over 68 peers, 0.435 over 59). A voter shown a sliver beside a triangle declines to prefer
    either, and an abstention yields no trust reading at all, so the pair measures nothing.

    Most-compared is not a quality signal: a poor mesh shown often accumulates comparisons exactly
    as a good one does. Typicality is step 3 because "admissible" is necessary and nowhere near
    sufficient — BOTH offenders passed admissibility.

    Degrades safely: with no verdict rows at all, every candidate ties on 1-3 and the old
    most-compared ordering is what remains."""
    task = db.get(Task, task_id)
    if task is None:
        return None
    cands = [
        o
        for o in task.outputs
        if not o.is_gold and not _vote_excluded(o) and o.asset_format == "glb"
    ]
    if not cands:
        return None
    return min(cands, key=_quality_key(db, cands))


def _quality_key(db, cands):
    """Ranking key over a task's candidates. See pick_good_output for why each term is there."""
    ids = [o.id for o in cands]
    fails = {
        oid
        for (oid,) in db.execute(
            select(Admissibility.output_id).where(
                Admissibility.output_id.in_(ids), Admissibility.admit.is_(False)
            )
        ).all()
    }
    complete = {
        oid
        for (oid, cat) in db.execute(
            select(Completeness.output_id, Completeness.category).where(
                Completeness.output_id.in_(ids)
            )
        ).all()
        if cat == "complete"
    }
    extents: dict[int, float] = {}
    for oid, detail in db.execute(
        select(Admissibility.output_id, Admissibility.detail_json).where(
            Admissibility.output_id.in_(ids), Admissibility.predicate == "structural"
        )
    ).all():
        try:
            v = json.loads(detail or "{}").get("extent_ratio")
        except (TypeError, ValueError):
            v = None
        if isinstance(v, (int, float)):
            extents[oid] = float(v)
    med = statistics.median(extents.values()) if extents else None

    def key(o: ModelOutput):
        er = extents.get(o.id)
        # Unknown shape sorts as "no worse than typical" rather than last: a missing verdict is
        # not evidence of a bad mesh, and penalising it would just re-select on scoring coverage.
        dev = abs(er - med) if (er is not None and med is not None) else 0.0
        return (o.id in fails, o.id not in complete, dev, -o.n_comparisons, o.id)

    return key


def _get_or_create_calibration_generator(db) -> Generator:
    gen = db.execute(select(Generator).where(Generator.slug == "calibration")).scalars().first()
    if gen is None:
        gen = Generator(
            slug="calibration", name="Calibration (gold)", kind="decoy", is_anonymous=True
        )
        db.add(gen)
        db.flush()
    return gen


def reseed_gold(db, task_ids, *, build_decoy=build_degenerate) -> dict:
    """Create one gold pair per task (good = is_gold copy of a real output, bad = decoy).

    Returns counts. Idempotent: tasks with an existing GoldPair are skipped."""
    calib = _get_or_create_calibration_generator(db)
    asset_dir = Path(config.ASSET_DIR)
    created = skipped = 0
    detail = []
    for tid in task_ids:
        existing = db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().first()
        if existing is not None:
            skipped += 1
            detail.append((tid, "skip: gold pair exists"))
            continue
        good_src = pick_good_output(db, tid)
        if good_src is None:
            skipped += 1
            detail.append((tid, "skip: no votable good output"))
            continue
        # 'good' is an anonymous is_gold copy that reuses the real GLB (no file copy).
        good = ModelOutput(
            task_id=tid,
            generator_id=calib.id,
            title="gold-good",
            asset_path=good_src.asset_path,
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "good", "source_output_id": good_src.id}),
        )
        bad_rel = f"gold/task{tid}__bad.glb"
        build_decoy(asset_dir / bad_rel)
        bad = ModelOutput(
            task_id=tid,
            generator_id=calib.id,
            title="gold-bad",
            asset_path=bad_rel,
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "bad"}),
        )
        db.add_all([good, bad])
        db.flush()
        db.add(
            GoldPair(
                task_id=tid,
                good_output_id=good.id,
                bad_output_id=bad.id,
                note=f"reseed: real output {good_src.id} vs degenerate decoy",
            )
        )
        created += 1
        detail.append((tid, f"created: good<-output {good_src.id}"))
    db.commit()
    return {"created": created, "skipped": skipped, "detail": detail}


def main() -> int:
    import argparse

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tasks",
        default=",".join(str(t) for t in DEFAULT_TASK_IDS),
        help="comma task ids to seed gold pairs for",
    )
    args = ap.parse_args()
    task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
    with SessionLocal() as db:
        res = reseed_gold(db, task_ids)
    for tid, msg in res["detail"]:
        print(f"  task {tid}: {msg}")
    print(f"gold reseed: created={res['created']} skipped={res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
