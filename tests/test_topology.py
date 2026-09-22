"""Topology metrics for the HF dataset's open/closed split.

The metrics exist because a leaf generated as a single open sheet and the same leaf generated as a
thin closed solid are DIFFERENT DATA for anyone doing radiative transfer, shell mechanics, or leaf
area, and nothing in the corpus records which one you have.
"""

from __future__ import annotations

import numpy as np
import trimesh
import trimesh.visual

from app.topology import mesh_topology


def _write(tmp_path, mesh, name="m.glb"):
    p = tmp_path / name
    mesh.export(p)
    return str(p)


def _open_sheet():
    """A curved single-sheet lamina: one boundary loop, no interior."""
    n = 12
    u, v = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-0.4, 0.4, n))
    z = 0.25 * u**2
    pts = np.column_stack([u.ravel(), v.ravel(), z.ravel()])
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            faces += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    return trimesh.Trimesh(vertices=pts, faces=np.array(faces), process=False)


def test_open_sheet_reports_open_edges_and_one_loop(tmp_path):
    t = mesh_topology(_write(tmp_path, _open_sheet()))
    assert t["watertight"] is False
    assert t["open_edge_fraction"] > 0.0
    assert t["boundary_loop_count"] == 1


def test_closed_solid_reports_no_open_edges(tmp_path):
    t = mesh_topology(_write(tmp_path, trimesh.creation.box(extents=(1, 1, 0.02))))
    assert t["watertight"] is True
    assert t["open_edge_fraction"] == 0.0
    assert t["boundary_loop_count"] == 0


def test_split_vertices_do_not_fake_an_open_surface(tmp_path):
    """The failure that would silently invalidate the whole column.

    glTF stores per-face attributes, so an exporter routinely emits a closed mesh with every
    triangle owning its own copy of each vertex. Counting edges without merging first makes a
    watertight box look 100% open, which would mislabel the entire corpus as open surfaces.
    """
    box = trimesh.creation.box(extents=(1, 1, 0.02))
    split = trimesh.Trimesh(
        vertices=box.vertices[box.faces].reshape(-1, 3),
        faces=np.arange(len(box.faces) * 3).reshape(-1, 3),
        process=False,
    )
    assert not split.is_watertight, "fixture must start unmerged, else it proves nothing"
    t = mesh_topology(_write(tmp_path, split))
    assert t["open_edge_fraction"] == 0.0
    assert t["watertight"] is True
    assert t["boundary_loop_count"] == 0


def test_two_holes_are_two_loops(tmp_path):
    """Boundary loops count HOLES, not boundary edges."""
    box = trimesh.creation.box(extents=(1, 1, 1))
    keep = [i for i in range(len(box.faces)) if i not in (0, 6)]
    punched = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
    t = mesh_topology(_write(tmp_path, punched))
    assert t["watertight"] is False
    assert t["boundary_loop_count"] == 2


def test_point_cloud_is_reported_not_crashed(tmp_path):
    """Capture scans are point clouds; they have no edges at all (C-prime, 2026-09-07)."""
    p = tmp_path / "pc.glb"
    trimesh.Scene([trimesh.PointCloud(np.random.rand(50, 3))]).export(p)
    t = mesh_topology(str(p))
    assert t["open_edge_fraction"] is None
    assert t["topology_note"] == "point_cloud"


def test_unreadable_mesh_records_the_error_rather_than_raising(tmp_path):
    p = tmp_path / "bad.glb"
    p.write_bytes(b"not a glb")
    t = mesh_topology(str(p))
    assert t["open_edge_fraction"] is None
    assert t["topology_note"] and t["topology_note"] != "point_cloud"


def test_uv_seams_are_not_holes(tmp_path):
    """A textured closed mesh is closed. Caught on real corpus meshes, not on fixtures.

    trimesh's default `merge_vertices()` refuses to merge two vertices at the same POSITION when
    their UV or normal differs, which is exactly what a textured GLB emits along every seam. On 18
    of 24 sampled corpus meshes that made a watertight asset read as up to 29% open edges. A UV
    seam is a texturing artifact, not a geometric boundary, so topology must merge across it.
    """
    box = trimesh.creation.box(extents=(1, 1, 0.02))
    verts = box.vertices[box.faces].reshape(-1, 3)
    faces = np.arange(len(box.faces) * 3).reshape(-1, 3)
    # Same positions, different UVs per face-vertex: default merge leaves these split.
    uv = np.random.default_rng(0).random((len(verts), 2))
    split = trimesh.Trimesh(
        vertices=verts,
        faces=faces,
        visual=trimesh.visual.TextureVisuals(uv=uv),
        process=False,
    )
    t = mesh_topology(_write(tmp_path, split, "uv.glb"))
    assert t["open_edge_fraction"] == 0.0, "UV seams reported as holes"
    assert t["watertight"] is True
    assert t["boundary_loop_count"] == 0
