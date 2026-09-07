# tests/test_fig_cprime.py
"""Real-execution check: the R figure script runs on a fixture and writes a PNG."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "paper" / "fig_cprime_ppv.R"

pytestmark = pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not installed")


def _fixture(tmp_path: Path) -> Path:
    per = {}
    for i, s in enumerate(
        [
            "struct_degenerate_bbox",
            "struct_empty",
            "novel_multiple",
            "novel_not_the_organism",
            "novel_sub_part",
            "sem_also_completeness",
            "sem_only_other",
            "admitted",
        ]
    ):
        k, n = (i + 2, i + 3)
        per[s] = {
            "sampled": n,
            "labelled": n,
            "inadmissible": k,
            "ppv": k / n,
            "ppv_ci": [max(0, k / n - 0.2), min(1, k / n + 0.1)],
        }
    res = {
        "per_stratum": per,
        "novel_ppv": {"ppv": 0.85},
        "admitted_fn": {"rate": 0.04, "ci": [0.01, 0.1]},
        "go": True,
    }
    p = tmp_path / "results.json"
    p.write_text(json.dumps(res))
    return p


def test_fig_script_writes_png(tmp_path):
    out = tmp_path / "fig.png"
    proc = subprocess.run(
        ["Rscript", str(SCRIPT), str(_fixture(tmp_path)), str(out)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 1000


def test_fig_script_fails_loud_without_input(tmp_path):
    proc = subprocess.run(
        ["Rscript", str(SCRIPT), str(tmp_path / "missing.json"), str(tmp_path / "x.png")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0


def test_fig_script_sources_the_house_theme():
    src = SCRIPT.read_text()
    assert (
        'source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))'
        in src
    )
    assert "theme_grey" not in src and "theme(" not in src.replace("theme_taxon3d(", "")
