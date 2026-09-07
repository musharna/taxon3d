"""The README's headline numbers must equal the committed snapshot that scripts/readme_stats.py
writes. Hand-edited numbers drift; a number that can only change by re-measuring cannot."""

from __future__ import annotations

import json
from pathlib import Path

from app.database import SessionLocal
from app.models import Generator, ModelOutput
from scripts.readme_stats import compute
from tests.factories import make_outputs

ROOT = Path(__file__).resolve().parent.parent


def test_readme_quotes_the_snapshot():
    snap = json.loads((ROOT / "docs" / "stats" / "readme.json").read_text())
    readme = (ROOT / "README.md").read_text()
    assert (
        f"{snap['outputs_votable']} votable 3D models across {snap['entrants']} entrants" in readme
    )
    assert f"{snap['tasks_active']} active" in readme
    assert "measured" in snap and snap["measured"] >= "2026-09-06"


def test_compute_counts_visible_model_outputs_only():
    """Hidden outputs, gold controls and the decoy generator are not entrants on the board."""
    with SessionLocal() as db:
        before = compute(db)
        outs = make_outputs(db, 3)
        gen = db.get(Generator, outs[0].generator_id)
        gen.kind = "model"
        outs[1].hidden_at = outs[1].created
        outs[2].is_gold = True
        db.commit()
        after = compute(db)
        # only outs[0] is visible + non-gold
        assert after["outputs_votable"] == before["outputs_votable"] + 1
        assert after["entrants"] >= before["entrants"]
        for o in outs:
            db.delete(db.get(ModelOutput, o.id))
        db.commit()
