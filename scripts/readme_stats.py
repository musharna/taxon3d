# scripts/readme_stats.py
"""Regenerate the headline numbers the README quotes, from a database, into docs/stats/readme.json.

The README's stats table was hand-maintained and drifted (2026-09-06 audit: it said 502 votable
outputs across 56 entrants while the live board had 488 across 52). `tests/test_readme_stats.py`
asserts the README matches this file, so the only way to change a headline number is to re-run
this against the CURRENT PROD SNAPSHOT — never the study DB, which holds internal outputs the
public board does not show.

Usage:
  BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/prod-pulls/<latest>.db" \\
  .venv/bin/python scripts/readme_stats.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import Generator, ModelOutput, Task, Vote  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "stats" / "readme.json"


def compute(db: Session) -> dict:
    """What the public board shows: visible, non-gold outputs; the model generators behind
    them (the decoy is a control, not an entrant); active tasks; votes cast."""
    visible = ModelOutput.is_gold.is_(False) & ModelOutput.hidden_at.is_(None)
    return {
        "outputs_votable": db.execute(
            select(func.count(ModelOutput.id)).where(visible)
        ).scalar_one(),
        "entrants": db.execute(
            select(func.count(func.distinct(ModelOutput.generator_id)))
            .join(Generator, Generator.id == ModelOutput.generator_id)
            .where(visible, Generator.kind == "model")
        ).scalar_one(),
        "tasks_active": db.execute(
            select(func.count(Task.id)).where(Task.active.is_(True))
        ).scalar_one(),
        "votes": db.execute(select(func.count(Vote.id))).scalar_one(),
    }


def main() -> int:
    from app.database import SessionLocal

    with SessionLocal() as db:
        stats = compute(db)
    stats["measured"] = date.today().isoformat()
    OUT.write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
