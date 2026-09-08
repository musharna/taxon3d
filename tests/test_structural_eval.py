# tests/test_structural_eval.py
from __future__ import annotations

import trimesh

from app import structural


def _save(mesh, tmp_path, name="m.glb"):
    p = tmp_path / name
    mesh.export(p)
    return str(p)


def test_valid_box_admits(tmp_path):
    v = structural.evaluate_glb(_save(trimesh.creation.box((1, 1, 1)), tmp_path))
    assert v.admit and v.reason == ""


def test_single_triangle_rejected(tmp_path):
    tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
    v = structural.evaluate_glb(_save(tri, tmp_path))
    assert not v.admit
    assert v.reason in ("too_small", "degenerate_bbox")  # 3 verts/1 face AND flat


def test_flat_sheet_rejected_degenerate(tmp_path):
    # A dense but perfectly flat sheet: enough verts/faces, but zero thickness.
    box = trimesh.creation.box((1, 1, 1))
    box.vertices[:, 2] = 0.0  # collapse Z
    v = structural.evaluate_glb(_save(box, tmp_path))
    assert not v.admit and v.reason == "degenerate_bbox"


def test_unreadable_rejected(tmp_path):
    p = tmp_path / "bad.glb"
    p.write_bytes(b"not a glb")
    v = structural.evaluate_glb(str(p))
    assert not v.admit and v.reason in ("unreadable", "empty")


def test_multi_component_plantlike_admits(tmp_path):
    # Two separated boxes = 2 components (plants have many detached leaves) — must ADMIT.
    a = trimesh.creation.box((1, 1, 1))
    b = trimesh.creation.box((1, 1, 1))
    b.apply_translation((5, 0, 0))
    scene = trimesh.Scene([a, b])
    v = structural.evaluate_glb(_save(scene, tmp_path, "scene.glb"))
    assert v.admit


# IRON_LAW_OK


def test_point_cloud_rejected_as_point_cloud_not_empty(tmp_path):
    """A capture scan exported as a GLB point cloud has thousands of vertices and no faces. The
    gate still rejects it (voters cannot judge a point cloud like a mesh), but calling that
    `empty` misdescribed 43 whole-plant scans as broken outputs (C′ dry run, 2026-09-07)."""
    pc = trimesh.PointCloud(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    scene = trimesh.Scene(pc)
    p = tmp_path / "pc.glb"
    scene.export(p)
    v = structural.evaluate_glb(str(p))
    assert not v.admit
    assert v.reason == "point_cloud", v
    assert v.detail["verts"] == 5 and v.detail["faces"] == 0


def test_truly_empty_is_still_empty():
    """Positive control for the rename: zero vertices keeps the `empty` reason."""
    v = structural._verdict_for_mesh(trimesh.Trimesh())
    assert not v.admit and v.reason == "empty"
