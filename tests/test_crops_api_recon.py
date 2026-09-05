# tests/test_crops_api_recon.py
import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_api_recon import CROPS


@pytest.mark.parametrize(
    "crop,title",
    [
        ("arabidopsis", "Arabidopsis thaliana — single-image → 3D reconstruction"),
        ("pinus", "Pinus sylvestris — single-image → 3D reconstruction"),
    ],
)
def test_new_recon_crops_wired(crop, title):
    assert crop in CROPS, crop
    assert CROPS[crop]["task_title"] == title
    assert_crop_entry(CROPS[crop], file_key="image")


def test_missing_input_fails_when_asset_tree_is_present(tmp_path, monkeypatch):
    """With data/assets present, a single missing input photo is a broken registry, not a
    "no runtime volume" situation — the helper must fail with the path, not skip."""
    from tests import _coverage_helpers as ch

    (tmp_path / "data" / "assets").mkdir(parents=True)
    monkeypatch.setattr(ch, "REPO_ROOT", str(tmp_path))
    entry = {"task_title": next(iter(ch.KNOWN_TITLES)), "input": "data/assets/nope.jpg"}
    with pytest.raises(pytest.fail.Exception, match="nope.jpg"):
        ch.assert_crop_entry(entry, file_key="input")


def test_missing_input_skips_only_when_whole_asset_tree_is_absent(tmp_path, monkeypatch):
    """Positive control for the skip: an absent data/assets tree (CI) still skips."""
    from tests import _coverage_helpers as ch

    monkeypatch.setattr(ch, "REPO_ROOT", str(tmp_path))
    entry = {"task_title": next(iter(ch.KNOWN_TITLES)), "input": "data/assets/nope.jpg"}
    with pytest.raises(pytest.skip.Exception):
        ch.assert_crop_entry(entry, file_key="input")
