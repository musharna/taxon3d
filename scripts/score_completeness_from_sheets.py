# scripts/score_completeness_from_sheets.py
"""Score organism-level completeness from PRE-RENDERED contact sheets (no browser).

Used to VALIDATE the metric against the calibration corpus using the same contact sheets the
human labels were made from (view-parity with the GT). Production scoring uses
scripts/score_completeness.py, which renders a fresh turntable sheet per output.

Reads `{output_id}_{condition}.png` from --sheets-dir and reuses the shipped
`app.completeness.score_outputs` seam (VLM organ-presence read -> derive -> upsert). Restricts
to eligible outputs that actually have a sheet on disk (and an optional --outputs allowlist).

Anthropic client from app.llm. Dry-run by default; --apply writes, and the study DB needs
--allow-study on top (app.dbguard)."""

from __future__ import annotations

import argparse
import os
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl

_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app.dbguard import add_write_target_args, confirm_write_target
from app.llm import anthropic_client
from app.completeness import enumerate_completeness_work, score_outputs


def main() -> int:
    ap = argparse.ArgumentParser(description="Score completeness from pre-rendered sheets.")
    ap.add_argument("--sheets-dir", required=True, help="dir holding {output_id}_{condition}.png")
    ap.add_argument("--condition", default="multi4", help="sheet condition suffix (default multi4)")
    ap.add_argument(
        "--outputs",
        default="",
        help="comma output-id allowlist (default: all eligible with a sheet)",
    )
    ap.add_argument("--scorer-version", default="completeness-v1-calib")
    add_write_target_args(ap)
    args = ap.parse_args()
    confirm_write_target(args, purpose="score completeness from sheets; upsert Completeness rows")

    client = anthropic_client()

    def sheet_for(output_id: int) -> bytes:
        path = os.path.join(args.sheets_dir, f"{output_id}_{args.condition}.png")
        with open(path, "rb") as f:
            return f.read()

    def _has_sheet(output_id: int) -> bool:
        return os.path.exists(os.path.join(args.sheets_dir, f"{output_id}_{args.condition}.png"))

    allow = {int(x) for x in args.outputs.split(",") if x.strip()} or None

    init_db()
    with SessionLocal() as db:
        from app.models import Task, TraitRubric

        task_ids = [t.id for t in db.query(Task).join(TraitRubric, TraitRubric.task_id == Task.id)]
        work = enumerate_completeness_work(db, task_ids)
        work = [
            w
            for w in work
            if _has_sheet(w["output_id"]) and (allow is None or w["output_id"] in allow)
        ]
        summary = score_outputs(
            db, work, client=client, sheet_for=sheet_for, scorer_version=args.scorer_version
        )
        db.commit()
    print(f"eligible-with-sheet scored: {summary}")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
