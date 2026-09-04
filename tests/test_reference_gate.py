import json
import uuid
import pytest
from app.reference_provenance import ReferenceProvenanceError, assert_recon_photos_cleared
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task
from tests.factories import a_category_id


def setup_module(_m):
    init_db()


def _recon(db, input_image):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=a_category_id(db))
    db.add(t)
    db.flush()
    o = ModelOutput(
        task_id=t.id,
        generator_id=g.id,
        asset_path="x.glb",
        asset_format="glb",
        source="api:fal:trellis",
        meta_json=json.dumps({"input_image": input_image}),
    )
    db.add(o)
    db.flush()
    return o


def test_raises_when_reference_sidecar_missing():
    with SessionLocal() as db:
        o = _recon(db, "reference/zzz_ref.jpg")  # 'zzz' has no sidecar
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()


def test_raises_when_input_image_missing():
    with SessionLocal() as db:
        o = _recon(db, None)  # no input_image at all -> unidentifiable taxon
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()


def test_raises_when_input_image_unparseable():
    with SessionLocal() as db:
        o = _recon(db, "reference/nomatch.jpg")  # no '<taxon>_ref' match
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()


def test_returns_when_reference_sidecar_is_valid(tmp_path, monkeypatch):
    """Positive control: a recon whose photo has a valid, redistributable sidecar passes.

    Without this the three tests above could pass on a gate that raises on EVERYTHING."""
    from app import config
    from app.licensing import REDISTRIBUTABLE_LICENSES
    from app.reference_provenance import _REQUIRED

    ref = tmp_path / "reference"
    ref.mkdir()
    record = {k: "x" for k in _REQUIRED}
    record["file"] = "clear_ref.jpg"
    record["license"] = sorted(REDISTRIBUTABLE_LICENSES)[0]
    (ref / "clear_ref.json").write_text(json.dumps(record))
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    with SessionLocal() as db:
        o = _recon(db, "reference/clear_ref.jpg")
        assert_recon_photos_cleared(db, {o.id})  # must return, not raise
        db.rollback()
