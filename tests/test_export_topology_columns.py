"""The HF `outputs` table carries surface topology, so the open/closed split is filterable.

Requested downstream (awesome-3D-Generative-Models#2, 2026-09-22): the corpus repairs nothing, so
whether a leaf ships as an open sheet or a thin closed solid depends on the generator, and the
only way to find out was to download every mesh and recompute it.
"""

from __future__ import annotations

import numpy as np
import trimesh

from scripts.export_hf_dataset import attach_topology
from app.topology import FIELDS


class _StubDB:
    """Stands in for a Session: `attach_topology` only ever asks for `asset_path`."""

    def __init__(self, paths: dict[int, str]):
        self._paths = paths

    def get(self, _model, oid):
        return type("O", (), {"asset_path": self._paths[oid]})()


def test_outputs_rows_gain_topology_columns(tmp_path, monkeypatch):
    import scripts.export_hf_dataset as mod

    trimesh.creation.box(extents=(1, 1, 0.02)).export(tmp_path / "closed.glb")

    n = 10
    u, v = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-1, 1, n))
    pts = np.column_stack([u.ravel(), v.ravel(), np.zeros(u.size)])
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            faces += [[a, a + 1, a + n + 1], [a, a + n + 1, a + n]]
    trimesh.Trimesh(vertices=pts, faces=np.array(faces), process=False).export(
        tmp_path / "open.glb"
    )

    monkeypatch.setattr(mod, "_asset_root", lambda: tmp_path)
    db = _StubDB({1: "closed.glb", 2: "open.glb", 3: "gone.glb"})
    rows = [{"output_id": 1}, {"output_id": 2}, {"output_id": 3}]

    attach_topology(db, rows)

    for row in rows:
        assert set(FIELDS) <= set(row), f"row {row['output_id']} missing topology columns"

    closed, opened, missing = rows
    assert closed["watertight"] is True
    assert closed["open_edge_fraction"] == 0.0

    # POSITIVE CONTROL for the missing-asset case below: the open sheet in the SAME call is
    # measured normally, so a null there means "unreadable", not "the whole pass silently failed".
    assert opened["watertight"] is False
    assert opened["open_edge_fraction"] > 0.0
    assert opened["boundary_loop_count"] == 1

    # A mesh we cannot read still ships, and the row says why rather than omitting the columns.
    assert missing["open_edge_fraction"] is None
    assert missing["topology_note"] == "asset_unavailable"


def test_card_topology_summary_is_computed_not_hardcoded():
    """The card's topology paragraph must track the rows, or it becomes a lie at the next export.

    Also pins the metric choice: `open_edge_fraction == 0` answers open-vs-closed, and `watertight`
    is reported only as the stricter secondary. Quoting `watertight` as the headline overstates how
    open a corpus is, because `is_watertight` also fails on non-manifold edges and winding — which
    is exactly the error that shipped in the first version of this card (2026-09-22).
    """
    from scripts.export_hf_dataset import _topology_summary

    rows = [
        # closed AND watertight
        {"kingdom": "fungi", "open_edge_fraction": 0.0, "watertight": True},
        {"kingdom": "fungi", "open_edge_fraction": 0.0, "watertight": True},
        # closed boundary but NOT watertight: the case that separates the two metrics
        {"kingdom": "plants", "open_edge_fraction": 0.0, "watertight": False},
        # genuinely open
        {"kingdom": "plants", "open_edge_fraction": 0.30, "watertight": False},
        # unmeasured rows must not be counted in any denominator
        {"kingdom": "plants", "open_edge_fraction": None, "watertight": None},
    ]
    out = _topology_summary(rows)

    assert "3 of 4 meshes (75%) have no open edges" in out, out
    assert "2 (50%) are `watertight`" in out, out
    assert "1 mesh has no boundary" in out, out  # 3 closed - 2 watertight
    # Unmeasured rows are excluded from the per-kingdom denominators too, not just the total:
    # plants is 1 of 2 MEASURED closed, never 1 of 3.
    assert "plants 50%" in out and "fungi 100%" in out, out
    assert "1 of 2 above 5% open" in out, out  # plants: one at 0.30

    # POSITIVE CONTROL: a corpus with nothing measurable says so rather than dividing by zero.
    assert "could not be measured" in _topology_summary(
        [{"kingdom": "plants", "open_edge_fraction": None, "watertight": None}]
    )
