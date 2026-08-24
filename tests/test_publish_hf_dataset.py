"""Tests for the HF upload step.

Scope is deliberately narrow: the upload call itself is `huggingface_hub`'s to get right. What is
ours — and what actually broke on the dev machine — is deciding WHICH token to use and refusing to
upload a tree that is not a finished export.
"""

import json

import pytest

from scripts import publish_hf_dataset as pub


def _tree(tmp_path, *, meshes=2, manifest_meshes=None, omit=()):
    """Build a minimal export tree. `manifest_meshes` desynchronises the manifest from the disk."""
    d = tmp_path / "tree"
    (d / "meshes").mkdir(parents=True)
    for i in range(meshes):
        (d / "meshes" / f"{i}.glb").write_bytes(b"glTF-fake")
    for name in pub.REQUIRED:
        if name in omit:
            continue
        if name == "manifest.json":
            n = meshes if manifest_meshes is None else manifest_meshes
            (d / name).write_text(json.dumps({"counts": {"meshes": n}, "accounting": {}}))
        else:
            (d / name).write_text("x")
    return d


def test_stored_write_token_beats_a_read_scoped_env_var(tmp_path):
    """The dev machine exported a READ token as $HF_TOKEN while a WRITE token sat in the CLI file.

    `huggingface_hub` reads the env var first, so every upload 403s with a permissions error that
    says nothing about there being two tokens. This inversion is the whole point of the helper, so
    it is the first thing asserted.
    """
    tf = tmp_path / "token"
    tf.write_text("hf_WRITE_token\n")
    got = pub.resolve_write_token(env={"HF_TOKEN": "hf_READ_token"}, token_file=tf)
    assert got == "hf_WRITE_token", "env var won — this is the 403-on-upload bug"


def test_env_token_is_used_when_no_stored_token_exists(tmp_path):
    """POSITIVE CONTROL for the test above: the env var is not ignored, it is only outranked.

    Without this, deleting the env branch entirely would still pass the preference test, and a
    machine with only $HF_TOKEN set would fail at upload time instead of here.
    """
    got = pub.resolve_write_token(env={"HF_TOKEN": "hf_ONLY_token"}, token_file=tmp_path / "absent")
    assert got == "hf_ONLY_token"


def test_empty_stored_token_falls_through_rather_than_returning_blank(tmp_path):
    """An empty token file must not shadow a working env var.

    `hf auth logout` can leave the file in place; returning "" from it would authenticate as
    nobody and fail with an opaque 401.
    """
    tf = tmp_path / "token"
    tf.write_text("   \n")
    assert pub.resolve_write_token(env={"HF_TOKEN": "hf_env"}, token_file=tf) == "hf_env"


def test_no_token_anywhere_raises_with_the_fix_in_the_message(tmp_path):
    with pytest.raises(RuntimeError, match="hf auth login"):
        pub.resolve_write_token(env={}, token_file=tmp_path / "absent")


def test_complete_tree_passes(tmp_path):
    """POSITIVE CONTROL for the refusal tests below: a good tree is accepted.

    A guard that rejects everything would satisfy every `pytest.raises` here while making the
    script useless, and no negative test can tell the difference.
    """
    manifest = pub.assert_tree_complete(_tree(tmp_path, meshes=3))
    assert manifest["counts"]["meshes"] == 3


@pytest.mark.parametrize("missing", ["README.md", "manifest.json", "outputs.jsonl"])
def test_incomplete_tree_is_refused(tmp_path, missing):
    with pytest.raises(RuntimeError, match=missing):
        pub.assert_tree_complete(_tree(tmp_path, omit=(missing,)))


def test_mesh_count_mismatch_is_refused(tmp_path):
    """The partial-export case: the tables describe a corpus the meshes do not match.

    Caught here because after upload it is a public dataset whose rows point at absent files.
    """
    with pytest.raises(RuntimeError, match="mesh count mismatch"):
        pub.assert_tree_complete(_tree(tmp_path, meshes=2, manifest_meshes=5))


def test_manifest_without_a_mesh_count_is_refused(tmp_path):
    """Fail loud rather than skipping the check when the manifest cannot answer it.

    A missing key read as "no expectation" would silently disable the mismatch guard above for
    exactly the malformed manifests most likely to accompany a broken export.
    """
    d = _tree(tmp_path)
    (d / "manifest.json").write_text(json.dumps({"counts": {}}))
    with pytest.raises(RuntimeError, match="counts.meshes"):
        pub.assert_tree_complete(d)


def test_a_file_is_not_a_tree(tmp_path):
    f = tmp_path / "notadir"
    f.write_text("x")
    with pytest.raises(RuntimeError, match="not a directory"):
        pub.assert_tree_complete(f)
