"""Every script that writes a database or an asset in place runs behind app.dbguard.

Each case here drives the script's main() through argv with NO --apply and asserts that it
exits 2 BEFORE opening a session: SessionLocal / init_db in the script's module are replaced by
a tripwire that fails the test if reached. A positive control per named writer shows --apply
lets the same argv through, so the tripwire is proven live rather than merely never hit.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import types

import pytest

from app import config


def _trip(*_a, **_k):
    raise AssertionError("the script opened a database session without --apply")


GUARDED = [
    ("scripts.reseed_gold", ["--task-ids", "1"]),
    ("scripts.import_public", ["--bundle", "{bundle}"]),
    ("scripts.reorient_scans", []),
    ("scripts.score_completeness", []),
    ("scripts.score_completeness_from_sheets", ["--sheets-dir", "{tmp}"]),
    ("scripts.score_semantic", []),
    ("scripts.score_structural", []),
    ("scripts.score_structure_batch", []),
    ("scripts.run_dgen", ["--model", "x/y"]),
    ("scripts.run_dgen_ab", []),
    ("scripts.validate_completeness", []),
    ("scripts.seed_completeness_rubrics", []),
    ("scripts.build_dataset_release", ["--version", "v1", "--tasks", "t", "--generators", "g", "--out", "{tmp}"]),
]


@pytest.fixture
def bundle(tmp_path):
    rows = {"task": [{"id": 1}], "rating": [{"id": 1}, {"id": 2}], "judge_rating": []}
    b = tmp_path / "v9"
    b.mkdir()
    raw = json.dumps(rows).encode()
    (b / "rows.json").write_bytes(raw)
    (b / "manifest.json").write_text(json.dumps({"sha256": hashlib.sha256(raw).hexdigest()}))
    return b


def _argv(spec, tmp_path, bundle):
    return [s.format(tmp=str(tmp_path), bundle=str(bundle)) for s in spec]


@pytest.mark.parametrize("modname,argv", GUARDED, ids=[m.split(".")[1] for m, _ in GUARDED])
def test_bare_run_exits_2_before_any_session(modname, argv, monkeypatch, tmp_path, bundle):
    mod = importlib.import_module(modname)
    monkeypatch.setattr(mod, "SessionLocal", _trip, raising=False)
    monkeypatch.setattr(mod, "init_db", _trip, raising=False)
    monkeypatch.setattr(mod, "import_bundle", _trip, raising=False)
    monkeypatch.setattr(mod, "get_storage", lambda: types.SimpleNamespace(remote=True), raising=False)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////tmp/nowhere.db")
    monkeypatch.setattr("sys.argv", [modname] + _argv(argv, tmp_path, bundle))
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 2


def test_reseed_gold_requires_task_ids_and_has_no_default(monkeypatch):
    import scripts.reseed_gold as rg

    assert not hasattr(rg, "DEFAULT_TASK_IDS")
    monkeypatch.setattr("sys.argv", ["reseed_gold", "--apply"])
    with pytest.raises(SystemExit) as e:
        rg.main()
    assert e.value.code == 2  # argparse: the required argument is missing


def test_reseed_gold_apply_reaches_the_seeder_with_the_given_ids(monkeypatch):
    """Positive control: --apply + --task-ids runs reseed_gold() on exactly those ids."""
    import scripts.reseed_gold as rg

    seen = []
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////tmp/nowhere.db")
    monkeypatch.setattr(rg, "SessionLocal", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(rg, "reseed_gold", lambda db, ids, recut=False: seen.append((ids, recut)) or {"created": 0, "skipped": 0, "detail": []})
    monkeypatch.setattr("sys.argv", ["reseed_gold", "--task-ids", "4,7", "--recut", "--apply"])
    assert rg.main() == 0
    assert seen == [([4, 7], True)]


def test_import_public_bare_run_prints_the_plan_and_imports_nothing(monkeypatch, tmp_path, bundle, capsys):
    from scripts import import_public

    monkeypatch.setattr(import_public, "import_bundle", _trip)
    monkeypatch.setattr(import_public, "get_storage", lambda: types.SimpleNamespace(remote=True))
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:////tmp/nowhere.db")
    monkeypatch.setattr("sys.argv", ["import_public", "--bundle", str(bundle)])
    with pytest.raises(SystemExit) as e:
        import_public.main()
    assert e.value.code == 2
    out = capsys.readouterr().out
    # The plan names the board tables it would clear and the row counts it would load.
    assert "rating" in out and "DELETE" in out.upper() and "2" in out
    assert "task" in out and "merge" in out.lower()


def test_reorient_scans_apply_writes_a_bak_beside_the_original(monkeypatch):
    import scripts.reorient_scans as rs

    saved: dict[str, bytes] = {}
    store = types.SimpleNamespace(read=lambda p: b"RAW", save=lambda p, d: saved.__setitem__(p, d))
    out = types.SimpleNamespace(id=5, source="scan-z", asset_path="ref/a.glb", asset_format="glb")
    monkeypatch.setattr(rs, "is_reference_scan", lambda s: True)
    monkeypatch.setattr(rs, "is_z_up_scan", lambda s: True)
    monkeypatch.setattr(rs, "reorient_glb_bytes", lambda b: b"FIXED")

    res = rs.reorient_outputs([out], store, apply=False)
    assert res["fixed"] == 1 and saved == {}, "a dry run must not write"

    res = rs.reorient_outputs([out], store, apply=True)
    assert res["fixed"] == 1
    assert saved == {"ref/a.glb.bak": b"RAW", "ref/a.glb": b"FIXED"}
