# tests/test_fig_aprime.py
"""Real-execution check: the A′ power figure renders from a fixture and uses the house theme."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "paper" / "fig_aprime_power.R"

pytestmark = pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not installed")


def _fixture(tmp_path: Path) -> Path:
    curve = [
        {
            "delta_conditional": d,
            "gap_marginal": g,
            "points": [
                {"voters": n, "power": pw, "cost_usd": round(n * 85.33 / 40, 2)}
                for n, pw in zip((20, 60, 120, 240), (0.2, 0.5, 0.75, 0.95))
            ],
        }
        for d, g in ((0.10, 0.085), (0.15, 0.128), (0.20, 0.170))
    ]
    p = tmp_path / "power.json"
    p.write_text(
        json.dumps({"curve": curve, "wave2_reference": {"cost_usd": 85.33, "approved": 40}})
    )
    return p


def test_fig_script_writes_png(tmp_path):
    out = tmp_path / "fig.png"
    proc = subprocess.run(
        ["Rscript", str(SCRIPT), str(_fixture(tmp_path)), str(out)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 1000


def test_fig_script_fails_loud_without_input(tmp_path):
    proc = subprocess.run(
        ["Rscript", str(SCRIPT), str(tmp_path / "missing.json"), str(tmp_path / "x.png")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0


def test_fig_script_sources_the_house_theme_and_defines_no_inline_style():
    src = SCRIPT.read_text()
    assert (
        'source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))'
        in src
    )
    assert "theme_grey" not in src
    assert "theme(" not in src.replace("theme_taxon3d_xy(", "")
