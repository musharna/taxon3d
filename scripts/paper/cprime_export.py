# scripts/paper/cprime_export.py
"""Export the Study C′ audit set: population from the live gate, stratified sample, contact
sheets under anonymized ids, a PRIVATE manifest, and BLIND rater sheets.

READ-ONLY against the database. Sheets come from the existing render cache
(`renders/{id}_turntable.png`, the same image the semantic judge saw); a missing sheet is an error,
never a silent skip — a skipped item would change the inclusion probability we just recorded.

Usage (study DB, read-only):
  BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
  BIO3D_DATA_DIR="$(pwd)/data" \
  .venv/bin/python scripts/paper/cprime_export.py --out data/paper/cprime --seed 20260905
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import config  # noqa: E402
from app.admissibility import non_admitted_output_ids  # noqa: E402
from app.judge_render import contact_sheet_path  # noqa: E402
from app.models import Admissibility, Completeness, ModelOutput, Task, TraitRubric  # noqa: E402
from scripts.paper.cprime_strata import (  # noqa: E402
    CALIBRATION_N,
    SENSITIVITY_N,
    STRATA,
    TARGETS,
    anonymize,
    assign_stratum,
    draw_calibration,
    plan_sample,
)

RUBRIC = ["structural", "completeness", "semantic"]
CONDITION = "turntable"
BLIND_FIELDS = ["anon_id", "taxon", "sheet_file", "label", "note"]
MESH_FIELDS = ["anon_id", "taxon", "mesh_file", "label", "note"]
MANIFEST_FIELDS = [
    "anon_id",
    "output_id",
    "stratum",
    "inclusion_prob",
    "set",
    "task_id",
    "taxon",
    "asset_path",
    "structural_reason",
    "semantic_code",
    "completeness_category",
]


def build_populations(db: Session) -> tuple[dict[str, list[int]], dict[int, dict]]:
    """Population per stratum + per-output info. Rejection comes from the gate composer over the
    FULL rubric so the frame is exactly what voters never saw. Completeness-only rejects and
    unevaluated outputs are outside this audit and are counted, not classified."""
    rejected = non_admitted_output_ids(db, rubric=RUBRIC)
    taxon_by_task = dict(db.execute(select(TraitRubric.task_id, TraitRubric.taxon)).all())
    title_by_task = dict(db.execute(select(Task.id, Task.title)).all())
    verdicts: dict[int, dict] = {}
    for a in db.execute(select(Admissibility)).scalars():
        v = verdicts.setdefault(a.output_id, {})
        if a.predicate == "structural":
            v["structural_reason"] = "" if a.admit else a.reason
            v["structural_seen"] = True
        elif a.predicate == "semantic":
            v["semantic_code"] = json.loads(a.detail_json or "{}").get("code")
            v["semantic_seen"] = True
    completeness = dict(db.execute(select(Completeness.output_id, Completeness.category)).all())

    pops: dict[str, list[int]] = {s: [] for s in STRATA}
    info: dict[int, dict] = {-1: {"excluded_completeness_only": 0, "excluded_unevaluated": 0}}
    outs = db.execute(
        select(ModelOutput).where(ModelOutput.is_gold.is_(False), ModelOutput.hidden_at.is_(None))
    ).scalars()
    for o in outs:
        v = verdicts.get(o.id, {})
        admitted = o.id not in rejected
        if not admitted and not v.get("structural_reason") and not v.get("semantic_seen"):
            # An output is classified by whichever predicate rejected it. A structural reject is
            # complete on its own: the semantic judge is never run on an empty mesh, so demanding
            # a semantic row too would drop every `empty` output out of the frame. Only when
            # NOTHING that could explain the rejection has looked at it is it truly unevaluated —
            # the gate failed closed on it. Count those; never classify them.
            info[-1]["excluded_unevaluated"] += 1
            continue
        try:
            stratum = assign_stratum(
                admitted=admitted,
                structural_reason=v.get("structural_reason", ""),
                semantic_code=v.get("semantic_code"),
                completeness_category=completeness.get(o.id),
            )
        except ValueError:
            info[-1]["excluded_completeness_only"] += 1
            continue
        pops[stratum].append(o.id)
        info[o.id] = {
            "task_id": o.task_id,
            "taxon": clean_taxon(taxon_by_task.get(o.task_id) or title_by_task.get(o.task_id, "")),
            "asset_path": o.asset_path,
            "structural_reason": v.get("structural_reason", ""),
            "semantic_code": v.get("semantic_code") or "",
            "completeness_category": completeness.get(o.id) or "",
            "admitted": admitted,
        }
    return pops, info


def clean_taxon(name: str) -> str:
    """Strip a task title's criterion suffix, leaving the organism.

    Two tasks have no TraitRubric, so their taxon falls back to Task.title, which reads like
    "Zea mays — botanical plausibility". The rater is asked about the organism; the criterion
    name is noise on a blind sheet."""
    return name.split(" — ", 1)[0].strip() if name else name


def assert_frame_nonempty(pops: dict[str, list[int]]) -> None:
    """Refuse to export from a database that contains no outputs at all.

    A mistyped BIO3D_DATABASE_URL is silent, not loud: pointing SQLAlchemy at
    `sqlite:///...db?mode=ro&uri=true` makes SQLite treat the whole string as a FILENAME, so it
    creates a brand-new schema-only database of that literal name and every query returns
    nothing. Without this check the export would cheerfully write a 266-item sampling plan drawn
    from a population of zero, and the emptiness would only surface when a rater opened an empty
    CSV. An empty frame always means the connection is wrong, never that the corpus is."""
    if not any(pops.values()):
        raise ValueError(
            "refusing to export: the gate frame contains no outputs. The database is almost "
            "certainly the wrong one — check BIO3D_DATABASE_URL, and note that SQLite query "
            "parameters (?mode=ro&uri=true) are read as part of the FILENAME and silently "
            "produce an empty database."
        )


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def export(
    db: Session,
    out_dir: Path,
    *,
    seed: int,
    asset_dir: Path,
    targets: dict[str, int] | None = None,
    calibration_n: int = CALIBRATION_N,
    sensitivity_n: int = SENSITIVITY_N,
) -> dict:
    targets = TARGETS if targets is None else targets
    pops, info = build_populations(db)
    assert_frame_nonempty(pops)
    task_of = {oid: d["task_id"] for oid, d in info.items() if oid != -1}
    main = plan_sample(pops, targets, task_of=task_of, seed=seed)
    main_ids = {r["output_id"] for r in main}
    calib = draw_calibration(pops, main_ids, n=calibration_n, seed=seed + 1)
    anon = anonymize([r["output_id"] for r in main + calib], seed=seed + 2)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheets").mkdir(exist_ok=True)
    manifest: list[dict] = []
    for set_name, rows in (("main", main), ("calibration", calib)):
        for r in rows:
            oid = r["output_id"]
            src = asset_dir / contact_sheet_path(oid, CONDITION)
            if not (src.exists() and src.stat().st_size > 0):
                raise FileNotFoundError(f"no contact sheet for output {oid}: {src}")
            a = anon[oid]
            shutil.copyfile(src, out_dir / "sheets" / f"{a}.png")
            manifest.append(
                {
                    "anon_id": a,
                    "output_id": oid,
                    "stratum": r["stratum"],
                    "inclusion_prob": f"{r['inclusion_prob']:.6f}",
                    "set": set_name,
                    **info[oid],
                }
            )
    manifest.sort(key=lambda m: m["anon_id"])
    _write_csv(out_dir / "manifest.csv", MANIFEST_FIELDS, manifest)

    def blind(rows):
        return [
            {
                "anon_id": m["anon_id"],
                "taxon": m["taxon"],
                "sheet_file": f"{m['anon_id']}.png",
                "label": "",
                "note": "",
            }
            for m in rows
        ]

    def mesh(rows):
        return [
            {
                "anon_id": m["anon_id"],
                "taxon": m["taxon"],
                "mesh_file": str(asset_dir / m["asset_path"]),
                "label": "",
                "note": "",
            }
            for m in rows
        ]

    main_rows = [m for m in manifest if m["set"] == "main"]
    calib_rows = [m for m in manifest if m["set"] == "calibration"]
    struct_rows = [m for m in main_rows if m["stratum"].startswith("struct_")]
    _write_csv(out_dir / "rater_sheet.csv", BLIND_FIELDS, blind(main_rows))
    _write_csv(out_dir / "calibration_sheet.csv", BLIND_FIELDS, blind(calib_rows))
    _write_csv(out_dir / "structural_sheet.csv", MESH_FIELDS, mesh(struct_rows))
    _write_csv(out_dir / "sensitivity_sheet.csv", MESH_FIELDS, mesh(main_rows[:sensitivity_n]))
    (out_dir / "README.txt").write_text(
        "Study C' audit set.\n"
        "manifest.csv is PRIVATE: it maps anon ids to outputs, strata and gate verdicts. Never send it to a rater.\n"
        "rater_sheet.csv + sheets/  -> raters 1 and 2 (label every row), then rater 3 for disagreements.\n"
        "calibration_sheet.csv      -> all raters first; discussed, then excluded from analysis.\n"
        "structural_sheet.csv       -> the 3D-literate rater; judge the mesh, not a sheet.\n"
        "sensitivity_sheet.csv      -> one rater re-labels these from the interactive mesh.\n"
        "Codes: ok | multiple | sub_part | not_the_organism | indeterminate. See docs/paper/cprime-rater-instructions.md.\n"
    )
    per = {s: sum(1 for m in main_rows if m["stratum"] == s) for s in STRATA}
    pop_sizes = {s: len(pops[s]) for s in STRATA}
    return {
        "main": len(main_rows),
        "calibration": len(calib_rows),
        "structural": len(struct_rows),
        "sensitivity": min(sensitivity_n, len(main_rows)),
        "per_stratum": per,
        "population": pop_sizes,
        "excluded": info[-1],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the Study C′ audit set (read-only).")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260905)
    args = ap.parse_args()
    from app.database import SessionLocal

    with SessionLocal() as db:
        counts = export(db, args.out, seed=args.seed, asset_dir=Path(config.ASSET_DIR))
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
