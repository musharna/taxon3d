# tests/test_cprime_ingest.py
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.paper.cprime_ingest import (
    adjudicate,
    estimate,
    go_no_go,
    human_inadmissible,
    read_labels,
    render_markdown,
)


def _csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def test_read_labels_rejects_bad_code(tmp_path):
    p = _csv(
        tmp_path / "l.csv",
        [{"anon_id": "c001", "taxon": "x", "sheet_file": "c001.png", "label": "okay", "note": ""}],
    )
    with pytest.raises(ValueError, match="c001"):
        read_labels(p)


def test_read_labels_rejects_empty(tmp_path):
    p = _csv(
        tmp_path / "l.csv",
        [{"anon_id": "c002", "taxon": "x", "sheet_file": "c002.png", "label": "", "note": ""}],
    )
    with pytest.raises(ValueError, match="c002"):
        read_labels(p)


def test_adjudicate_uses_third_rater_only_on_disagreement():
    r1 = {"c001": "ok", "c002": "multiple", "c003": "sub_part"}
    r2 = {"c001": "ok", "c002": "ok", "c003": "ok"}
    r3 = {"c002": "multiple"}
    final = adjudicate(r1, r2, r3)
    assert final["c001"] == {"label": "ok", "source": "agree"}
    assert final["c002"] == {"label": "multiple", "source": "adjudicated"}
    assert final["c003"] == {"label": "unresolved", "source": "unresolved"}


def test_human_inadmissible_mapping():
    assert human_inadmissible("ok") is False
    assert human_inadmissible("multiple") is True
    assert human_inadmissible("indeterminate") is None


def _manifest():
    rows = []
    for i in range(10):  # novel_multiple: pop 20, sampled 10
        rows.append(
            {
                "anon_id": f"c{i + 1:03d}",
                "stratum": "novel_multiple",
                "inclusion_prob": "0.5",
                "set": "main",
            }
        )
    for i in range(10, 20):  # admitted: pop 100, sampled 10
        rows.append(
            {
                "anon_id": f"c{i + 1:03d}",
                "stratum": "admitted",
                "inclusion_prob": "0.1",
                "set": "main",
            }
        )
    rows.append(
        {
            "anon_id": "c999",
            "stratum": "admitted",
            "inclusion_prob": "0.000000",
            "set": "calibration",
        }
    )
    return rows


def test_estimate_ppv_fn_and_weighted_rates():
    man = _manifest()
    # raters agree everywhere: 9 of 10 novel are inadmissible, 1 ok; admitted: 1 of 10 inadmissible, 1 indeterminate
    labels = {f"c{i + 1:03d}": "multiple" for i in range(9)}
    labels["c010"] = "ok"
    for i in range(10, 20):
        labels[f"c{i + 1:03d}"] = "ok"
    labels["c011"] = "sub_part"
    labels["c012"] = "indeterminate"
    final = adjudicate(labels, labels, None)
    res = estimate(man, final, labels, labels)
    assert res["n_main"] == 20 and res["n_calibration"] == 1
    assert res["per_stratum"]["novel_multiple"]["ppv"] == pytest.approx(0.9)
    assert res["novel_ppv"]["k"] == 9 and res["novel_ppv"]["n"] == 10
    assert res["admitted_fn"] == pytest.approx(
        {"k": 1, "n": 9, "rate": 1 / 9, "ci": res["admitted_fn"]["ci"]}
    )
    assert res["n_indeterminate"] == 1
    # weighted sensitivity: human-inadmissible = 9 novel (w=2) + 1 admitted (w=10). gate rejected the 9 novel.
    assert res["weighted"]["sensitivity"] == pytest.approx(18 / 28)
    # weighted specificity: human-ok = 1 novel (w=2) + 8 admitted (w=10). gate admitted the 8.
    assert res["weighted"]["specificity"] == pytest.approx(80 / 82)
    assert res["agreement"]["raw"] == pytest.approx(1.0)
    assert res["agreement"]["alpha_r1_r2"] == pytest.approx(1.0)


def test_go_no_go_thresholds():
    ok = {"novel_ppv": {"ppv": 0.85}, "admitted_fn": {"rate": 0.05}, "n_unresolved": 0}
    assert go_no_go(ok)[0] is True
    assert go_no_go({**ok, "novel_ppv": {"ppv": 0.79}})[0] is False
    assert go_no_go({**ok, "admitted_fn": {"rate": 0.1}})[0] is False
    assert go_no_go({**ok, "n_unresolved": 1})[0] is False


def test_render_markdown_has_stratum_table():
    man = _manifest()
    labels = {m["anon_id"]: "ok" for m in man}
    final = adjudicate(labels, labels, None)
    md = render_markdown(estimate(man, final, labels, labels))
    assert "| stratum |" in md and "novel_multiple" in md and "go/no-go" in md.lower()
