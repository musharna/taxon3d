"""Hiding an output must stop the site serving its mesh.

MEASURED LIVE on taxon3d.org 2026-08-11: every one of the 14 hidden outputs in the production
database returned **200** from `/media/o/{id}.glb`. Twelve of them (rose 267-272/274, soybean
246-249/253) were hidden on 2026-07-21 as the LICENSING control — `rose_ref.json` and
`soybean_ref.json` do not exist, so those meshes are derivative works of photographs with no
recorded provenance. They were withheld from publication deliberately, and the media route
published them anyway.

`media_asset` resolved the row and checked only that it existed and its blob was present. It
never consulted `hidden_at`. This is the same shape as the reference-input leakage: an invariant
enforced on one route is not enforced. `service.reference_images_for_task` and
`completeness.recon_reliability_flags` were both taught to exclude hidden outputs in July; these
two routes were not.

Why 404 and not 403: the arena's asset URLs are deliberately opaque and output-scoped, so a
withheld output should be indistinguishable from one that never existed. 403 would confirm the
id is real.

Why admin still gets through: moderation has to be able to look at what it just hid, and
`/admin/moderation` renders these same assets. The bypass reuses the existing `?token=` admin
convention rather than inventing a second one.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import config
from app import mesh_lod
from app.database import SessionLocal
from app.main import app
from app.models import ModelOutput
from app.seed import seed_all

client = TestClient(app)

ADMIN_TOKEN = "test-admin-token-hidden-media"


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture()
def visible_output():
    """A seeded GLB whose asset really is on disk, restored to visible after each test."""
    with SessionLocal() as db:
        o = (
            db.query(ModelOutput)
            .filter(ModelOutput.asset_path.isnot(None), ModelOutput.asset_format == "glb")
            .first()
        )
        assert o is not None, "seed produced no GLB output"
        assert (config.ASSET_DIR / o.asset_path).is_file(), "fixture asset missing on disk"
        oid = o.id
        yield oid
        db.query(ModelOutput).filter(ModelOutput.id == oid).update({"hidden_at": None})
        db.commit()


def _hide(oid: int) -> None:
    with SessionLocal() as db:
        db.query(ModelOutput).filter(ModelOutput.id == oid).update(
            {"hidden_at": datetime(2026, 8, 11)}
        )
        db.commit()


def test_hiding_an_output_stops_the_site_serving_its_mesh(visible_output):
    oid = visible_output

    # Positive control, in the same test: the route serves this output while it is visible, so a
    # 404 below means hiding did it and not a broken fixture.
    assert client.get(f"/media/o/{oid}.glb").status_code == 200

    _hide(oid)
    assert client.get(f"/media/o/{oid}.glb").status_code == 404


def test_an_admin_can_still_fetch_a_hidden_output(visible_output, monkeypatch):
    """Moderation must be able to see what it hid; `/admin/moderation` renders these assets."""
    monkeypatch.setattr(config, "ADMIN_TOKEN", ADMIN_TOKEN)
    oid = visible_output
    _hide(oid)

    assert client.get(f"/media/o/{oid}.glb").status_code == 404
    assert client.get(f"/media/o/{oid}.glb?token={ADMIN_TOKEN}").status_code == 200
    # A wrong token is not a bypass.
    assert client.get(f"/media/o/{oid}.glb?token=wrong").status_code == 404


def test_the_lod_route_hides_too(visible_output):
    """The LOD companion is a second door to the same withheld mesh.

    The LOD file is created first on purpose: without it the route 404s whether or not the fix
    exists, and the assertion would pass against the vulnerable code.
    """
    oid = visible_output
    with SessionLocal() as db:
        o = db.get(ModelOutput, oid)
        rel = mesh_lod.lod_path(o.asset_path)
    lod_abs = config.ASSET_DIR / rel
    lod_abs.parent.mkdir(parents=True, exist_ok=True)
    lod_abs.write_bytes((config.ASSET_DIR / o.asset_path).read_bytes())
    try:
        # Positive control: the LOD is genuinely servable before hiding.
        assert client.get(f"/media/o/{oid}.lod.glb").status_code == 200

        _hide(oid)
        assert client.get(f"/media/o/{oid}.lod.glb").status_code == 404
    finally:
        lod_abs.unlink(missing_ok=True)
