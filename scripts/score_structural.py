# scripts/score_structural.py
"""Backfill structural admissibility verdicts for every output lacking a current-version one.
Pure trimesh geometry — no VLM, no browser. Dry-run by default; --apply writes, the study DB
needs --allow-study (app.dbguard).
Usage: PYTHONPATH=. BIO3D_DATABASE_URL=sqlite:///<copy> .venv/bin/python scripts/score_structural.py --apply"""

from __future__ import annotations

import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl

_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app import structural
from app.database import SessionLocal, init_db
from app.dbguard import add_write_target_args, confirm_write_target


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    add_write_target_args(ap)
    args = ap.parse_args()
    confirm_write_target(args, purpose="backfill structural Admissibility verdicts")
    init_db()
    with SessionLocal() as db:
        work = structural.enumerate_structural_work(db)
        print(f"structural backfill: {len(work)} outputs to evaluate", flush=True)
        res = structural.evaluate_outputs(db, work)
        db.commit()
        rejected = len(structural.StructuralPredicate().rejected_output_ids(db))
        print(
            f"done: scored={res['scored']} errors={res['errors']} total_rejected={rejected}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
