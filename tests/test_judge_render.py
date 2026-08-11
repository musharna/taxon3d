from __future__ import annotations

import io

from PIL import Image

from app import judge_render
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _png(color, size=64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def test_contact_sheet_path_convention():
    assert judge_render.contact_sheet_path(42, "multi4") == "renders/42_multi4.png"


def test_tile_contact_sheet_dimensions_2x2():
    tiles = [_png(c, 64) for c in ("red", "green", "blue", "white")]
    out = judge_render.tile_contact_sheet(tiles, cols=2, rows=2)
    img = Image.open(io.BytesIO(out))
    assert img.size == (128, 128)  # 2*64 x 2*64


def test_render_contact_sheets_writes_file_and_is_idempotent(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    calls = {"n": 0}

    def fake_capture(glb_abs, azimuths, elev):
        calls["n"] += 1
        return [_png("red", 64) for _ in azimuths]

    with SessionLocal() as db:
        cat = Category(slug="jr-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jr-task", prompt="p")
        gen = Generator(slug="jr-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "x.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.commit()

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert res == {"rendered": 1, "errors": 0, "failures": []}
        sheet = tmp_path / judge_render.contact_sheet_path(out.id, "multi4")
        assert sheet.exists() and sheet.stat().st_size > 0
        # Idempotent: second call skips (no new capture).
        res2 = judge_render.render_contact_sheets(
            db, [out.id], "multi4", capture_multi=fake_capture
        )
        assert res2 == {"rendered": 0, "errors": 0, "failures": []}
        assert calls["n"] == 1


def test_render_contact_sheets_rerenders_when_mesh_is_rewritten(tmp_path, monkeypatch):
    """A sheet cached before its mesh was rewritten does not depict that mesh any more.

    This is the confound behind the 2026-08-10 completeness correction: the ground-plane
    strip and the default-cube fix rewrote GLBs in place, the cached sheets kept showing
    the pre-fix meshes, and 18 of 43 verdicts described objects that no longer existed.
    """
    import os

    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    calls = {"n": 0}

    def fake_capture(glb_abs, azimuths, elev):
        calls["n"] += 1
        return [_png("red", 64) for _ in azimuths]

    with SessionLocal() as db:
        cat = Category(slug="jrs-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jrs-task", prompt="p")
        gen = Generator(slug="jrs-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        glb = tmp_path / "seed" / "rewritten.glb"
        glb.write_bytes(b"glTF-stub-BEFORE")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/rewritten.glb")
        db.add(out)
        db.commit()

        judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        sheet = tmp_path / judge_render.contact_sheet_path(out.id, "multi4")
        assert calls["n"] == 1 and sheet.exists()

        # Rewrite the mesh, as strip_ground_plane.py / strip_default_cube.py do. Stamp the
        # mtime forward explicitly: the write alone may land inside the sheet's timestamp
        # granularity, which would make the test pass for the wrong reason.
        glb.write_bytes(b"glTF-stub-AFTER-the-ground-plane-was-stripped")
        future = sheet.stat().st_mtime + 10
        os.utime(glb, (future, future))

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert res == {"rendered": 1, "errors": 0, "failures": []}
        assert calls["n"] == 2, "stale sheet was served for a mesh that had been rewritten"
        # And the refreshed sheet is itself reusable — the fix must not re-render forever.
        res3 = judge_render.render_contact_sheets(
            db, [out.id], "multi4", capture_multi=fake_capture
        )
        assert res3["rendered"] == 0
        assert calls["n"] == 2


def test_render_contact_sheets_rerenders_when_source_mesh_is_missing(tmp_path, monkeypatch):
    """No source to compare against means the sheet's freshness cannot be established.

    Re-rendering surfaces the missing mesh through the normal capture failure path rather
    than silently blessing a cached image nothing can vouch for.
    """
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    calls = {"n": 0}

    def fake_capture(glb_abs, azimuths, elev):
        calls["n"] += 1
        return [_png("blue", 64) for _ in azimuths]

    with SessionLocal() as db:
        cat = Category(slug="jrm-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jrm-task", prompt="p")
        gen = Generator(slug="jrm-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        glb = tmp_path / "seed" / "vanishes.glb"
        glb.write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/vanishes.glb")
        db.add(out)
        db.commit()

        judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert calls["n"] == 1
        glb.unlink()

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert res["rendered"] == 1, "a sheet whose source is gone was reused unchecked"
        assert calls["n"] == 2


def test_render_contact_sheets_surfaces_capture_failure(tmp_path, monkeypatch):
    """A capture exception is logged + recorded in failures, not silently swallowed."""
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)

    def boom_capture(glb_abs, azimuths, elev):
        raise RuntimeError("headless browser timed out")

    with SessionLocal() as db:
        cat = Category(slug="jrf-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jrf-task", prompt="p")
        gen = Generator(slug="jrf-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "y.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/y.glb")
        db.add(out)
        db.commit()

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=boom_capture)
        assert res["rendered"] == 0
        assert res["errors"] == 1
        assert len(res["failures"]) == 1
        assert res["failures"][0]["oid"] == out.id
        # the real cause is surfaced, not lost
        assert "headless browser timed out" in res["failures"][0]["error"]
        # no empty/partial sheet left behind
        sheet = tmp_path / judge_render.contact_sheet_path(out.id, "multi4")
        assert not sheet.exists()
