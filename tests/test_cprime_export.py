# tests/test_cprime_export.py
"""Export builds its population from the app's gate composer and writes blind rater sheets.
Uses the suite's isolated DB + ASSET_DIR (conftest)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app import config
from app.admissibility import Verdict
from app.database import SessionLocal
from app.judge_render import contact_sheet_path
from app.models import Completeness, TraitRubric
from app.structural import upsert_verdict
from tests.factories import make_outputs

from scripts.paper import cprime_strata
from scripts.paper.cprime_export import build_populations, export


def _sheet(oid: int) -> Path:
    p = Path(config.ASSET_DIR) / contact_sheet_path(oid, "turntable")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG fake " + str(oid).encode())
    return p


def _seed(db):
    """6 outputs on one task: 2 admitted, 1 struct empty, 1 novel multiple, 1 sem-also, 1 completeness-only."""
    outs = make_outputs(db, 6)
    ok = Verdict(True, "")
    db.add(TraitRubric(taxon="Zea mays", task_id=outs[0].task_id, traits_json="[]"))
    plan = [
        ("admitted", ok, Verdict(True, "", {"code": "ok"}), "complete"),
        ("admitted", ok, Verdict(True, "", {"code": "ok"}), "complete"),
        ("struct_empty", Verdict(False, "empty"), Verdict(False, "not_the_organism", {"code": "not_the_organism"}), "fragment"),
        ("novel_multiple", ok, Verdict(False, "multiple", {"code": "multiple"}), "complete"),
        ("sem_also_completeness", ok, Verdict(False, "sub_part", {"code": "sub_part"}), "isolated-organ"),
        ("completeness_only", ok, Verdict(True, "", {"code": "ok"}), "fragment"),
    ]
    expect = {}
    for o, (stratum, sv, mv, cat) in zip(outs, plan):
        upsert_verdict(db, o.id, "structural", sv, "structural-v1")
        upsert_verdict(db, o.id, "semantic", mv, "semantic-v2")
        db.add(Completeness(output_id=o.id, category=cat, checklist_json="{}"))
        expect[o.id] = stratum
        _sheet(o.id)
    db.commit()
    return outs, expect


def test_build_populations_uses_gate_and_drops_completeness_only():
    with SessionLocal() as db:
        outs, expect = _seed(db)
        pops, info = build_populations(db)
    ids = {o.id for o in outs}
    got = {oid: s for s, lst in pops.items() for oid in lst if oid in ids}
    assert got == {oid: s for oid, s in expect.items() if s != "completeness_only"}
    assert info[-1]["excluded_completeness_only"] >= 1
    novel = [o for o in outs if expect[o.id] == "novel_multiple"][0]
    assert info[novel.id]["taxon"] == "Zea mays"
    assert info[novel.id]["semantic_code"] == "multiple"


def test_export_writes_blind_sheets_and_private_manifest(tmp_path):
    with SessionLocal() as db:
        outs, expect = _seed(db)
        # Scope the export to these outputs by shrinking targets; other tests' rows may exist.
        targets = {s: 1 for s in cprime_strata.STRATA}
        counts = export(db, tmp_path, seed=1, asset_dir=Path(config.ASSET_DIR), targets=targets, calibration_n=1)
    assert counts["main"] >= 3
    manifest = list(csv.DictReader(open(tmp_path / "manifest.csv")))
    rater = list(csv.DictReader(open(tmp_path / "rater_sheet.csv")))
    assert set(rater[0].keys()) == {"anon_id", "taxon", "sheet_file", "label", "note"}
    assert all(r["label"] == "" and r["note"] == "" for r in rater)
    anon_in_manifest = {m["anon_id"] for m in manifest if m["set"] == "main"}
    assert {r["anon_id"] for r in rater} == anon_in_manifest
    # every sheet copied, named by anon id only
    for r in rater:
        assert (tmp_path / "sheets" / r["sheet_file"]).exists()
        assert "output" not in r["sheet_file"]
    # structural sheet lists only struct_* items and points at meshes
    # The mesh pass covers the MAIN set only: calibration is a uniform contact-sheet alignment
    # exercise, and an item shown to one rater as a mesh and to another as a sheet would confound
    # the very agreement the calibration round exists to establish.
    struct = list(csv.DictReader(open(tmp_path / "structural_sheet.csv")))
    struct_anon = {
        m["anon_id"] for m in manifest if m["stratum"].startswith("struct_") and m["set"] == "main"
    }
    assert {s["anon_id"] for s in struct} == struct_anon
    calib_struct = {
        m["anon_id"] for m in manifest if m["stratum"].startswith("struct_") and m["set"] == "calibration"
    }
    calib = list(csv.DictReader(open(tmp_path / "calibration_sheet.csv")))
    assert not (calib_struct & {s["anon_id"] for s in struct})
    assert calib_struct <= {c["anon_id"] for c in calib}
    assert (tmp_path / "README.txt").exists()


def test_export_fails_loud_on_missing_sheet(tmp_path):
    with SessionLocal() as db:
        outs, expect = _seed(db)
        victim = [o for o in outs if expect[o.id] == "novel_multiple"][0]
        (Path(config.ASSET_DIR) / contact_sheet_path(victim.id, "turntable")).unlink()
        with pytest.raises(FileNotFoundError, match=str(victim.id)):
            export(db, tmp_path, seed=1, asset_dir=Path(config.ASSET_DIR),
                   targets={s: 50 for s in cprime_strata.STRATA}, calibration_n=0)
