# app/topology.py
"""Per-mesh surface topology: is this asset an open sheet or a closed solid?

The corpus stores whatever each generator produced — nothing is hole-filled or repaired — so a
leaf may arrive as a single open lamina or as a thin closed solid depending only on which model
made it. That distinction is load-bearing downstream (per-side optical properties, shell elements,
leaf area, two-sided BSDFs) and was previously invisible: a user had to download the GLB and
recompute it. These metrics are descriptive ONLY. Nothing here gates admission, and no mesh is
modified.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

#: Every key this module promises, so a caller can build a table without a mesh in hand.
FIELDS = ("watertight", "open_edge_fraction", "boundary_loop_count", "topology_note")

_ABSENT = dict.fromkeys(FIELDS)


def _empty(note: str) -> dict:
    return {**_ABSENT, "topology_note": note}


def _boundary_loop_count(boundary_edges) -> int:
    """Number of holes, tracing the boundary as loops rather than as a blob of edges.

    Counted by joining edges through vertices of boundary-degree 2 ONLY. A pinched boundary — one
    vertex where two holes meet, e.g. two faces removed from a cube that share a corner — leaves
    that vertex at degree 4; joining through it would report the two holes as one.
    """
    if not len(boundary_edges):
        return 0

    incident: dict[int, list[int]] = {}
    for idx, (a, b) in enumerate(boundary_edges):
        incident.setdefault(int(a), []).append(idx)
        incident.setdefault(int(b), []).append(idx)

    parent = list(range(len(boundary_edges)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge_ids in incident.values():
        if len(edge_ids) != 2:  # a pinch (or a dangling edge): do not join across it
            continue
        ra, rb = find(edge_ids[0]), find(edge_ids[1])
        if ra != rb:
            parent[ra] = rb

    return len({find(i) for i in range(len(boundary_edges))})


def mesh_topology(path: str) -> dict:
    """Return surface-topology metrics for one GLB.

    Never raises on a bad asset: a corpus that admits whatever a generator emitted contains
    unreadable and degenerate files, and one of them must not abort an export of 292 meshes. The
    failure is recorded in `topology_note` rather than swallowed, so it stays visible in the table.
    """
    import trimesh  # local import: heavy, and this module is imported by export tooling

    try:
        loaded = trimesh.load(path, force="scene")
    except Exception as exc:  # noqa: BLE001 - the reason ships in the row
        return _empty(f"unreadable: {type(exc).__name__}: {exc}")

    geoms = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
    meshes = [g for g in geoms if isinstance(g, trimesh.Trimesh) and len(g.faces)]
    if not meshes:
        # `force="mesh"` would silently DROP PointCloud geometry and report this as empty; capture
        # scans are point clouds and have no edges to describe (C-prime dry run, 2026-09-07).
        points = sum(len(getattr(g, "vertices", ())) for g in geoms)
        return _empty("point_cloud" if points else "empty")

    mesh = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]

    # MUST merge first, and MUST merge across UV/normal splits. glTF stores attributes per
    # face-vertex, so exporters routinely split every vertex; on an unmerged mesh every edge is
    # used once and a watertight solid reads as 100% open. trimesh's default keeps vertices that
    # share a position but differ in UV or normal SEPARATE, which is every texture seam — measured
    # on 18 of 24 sampled corpus meshes, taking watertight assets up to 0.29 open-edge fraction.
    # A seam is a texturing artifact, not a hole, so topology merges on position alone.
    mesh = mesh.copy()
    mesh.merge_vertices(merge_tex=True, merge_norm=True)

    edges = np.sort(np.asarray(mesh.edges), axis=1)
    if not len(edges):
        return _empty("no_edges")
    counts = Counter(map(tuple, edges))
    boundary = [e for e, c in counts.items() if c == 1]

    return {
        "watertight": bool(mesh.is_watertight),
        "open_edge_fraction": round(len(boundary) / len(counts), 6),
        "boundary_loop_count": _boundary_loop_count(boundary),
        "topology_note": None,
    }
