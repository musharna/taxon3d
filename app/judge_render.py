"""Multi-view contact-sheet rendering for the VLM judge.

Pure logic + an injected `capture_multi`; no browser import here (the Playwright
driver lives in scripts/judge_capture.py). Sheets cache to disk by convention
`renders/{output_id}_{condition}.png` under ASSET_DIR, and are reused only while
they are newer than the mesh they depict — see `sheet_is_current`."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

from PIL import Image

from . import config
from .models import ModelOutput

logger = logging.getLogger(__name__)

CONDITIONS: dict[str, dict] = {
    "single": {"azimuths": [30], "elev": 75, "cols": 1, "rows": 1},
    "multi4": {"azimuths": [0, 90, 180, 270], "elev": 70, "cols": 2, "rows": 2},
    "turntable": {
        "azimuths": [0, 45, 90, 135, 180, 225, 270, 315],
        "elev": 70,
        "cols": 4,
        "rows": 2,
    },
}


def contact_sheet_path(output_id: int, condition: str) -> str:
    return f"renders/{output_id}_{condition}.png"


def sheet_is_current(sheet_abs: Path, glb_abs: Path) -> bool:
    """Is a cached contact sheet still a picture of the mesh it was rendered from?

    A contact sheet is a derived artifact, so the make rule applies: it is reusable only
    while it is newer than its source. The cache used to ask only whether a file existed
    at `renders/{output_id}_{condition}.png`, which made every sheet look permanently
    fresh. Meshes are rewritten in place — the default-cube fix, the ground-plane strip —
    and each rewrite silently left the judge scoring an object that no longer existed. On
    2026-08-10 that accounted for 18 wrong completeness verdicts across 42 stale sheets,
    with the `agentic` paradigm still being judged on damage repaired weeks earlier.

    A missing or unreadable source means freshness cannot be established at all, so the
    cached sheet is not trusted: re-rendering surfaces the real problem through the normal
    capture-failure path instead of blessing an image nothing can vouch for.
    """
    try:
        sheet = sheet_abs.stat()
    except OSError:
        return False
    if sheet.st_size == 0:
        return False
    try:
        source = glb_abs.stat()
    except OSError:
        return False
    return sheet.st_mtime_ns >= source.st_mtime_ns


def _stamp_current(sheet_abs: Path, glb_abs: Path) -> None:
    """Make a freshly-rendered sheet satisfy `sheet_is_current` by construction.

    Without this, currency is an accident of wall-clock ordering. A mesh whose mtime sits
    in the future — clock skew, a restored or copied-forward file — could never be caught
    up to, so every run would re-render it and spend another browser capture and another
    VLM call on a sheet that was already correct.
    """
    try:
        source_ns = glb_abs.stat().st_mtime_ns
        sheet_ns = sheet_abs.stat().st_mtime_ns
    except OSError:
        return
    if sheet_ns < source_ns:
        os.utime(sheet_abs, ns=(source_ns, source_ns))


def tile_contact_sheet(pngs: list[bytes], cols: int, rows: int) -> bytes:
    """Composite PNG bytes row-major into a single PNG. All tiles assumed same size."""
    tiles = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw, rows * th), (111, 118, 124))  # match render gray
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet.paste(tile, (c * tw, r * th))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def render_contact_sheets(db, output_ids: list[int], condition: str, *, capture_multi) -> dict:
    """Render+tile a contact sheet per output for `condition`. Idempotent: skips outputs
    whose cached sheet is still current with its mesh (`sheet_is_current`), and re-renders
    the rest. `capture_multi(glb_abs, azimuths, elev) -> list[bytes]` (one PNG per azimuth)
    is injected (Playwright in prod, stub in tests)."""
    spec = CONDITIONS[condition]
    renders_dir = Path(config.ASSET_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0
    failures: list[dict] = []  # {oid, error} per output that failed — surfaced, not swallowed
    for oid in output_ids:
        abs_path = Path(config.ASSET_DIR) / contact_sheet_path(oid, condition)
        try:
            # The mesh is looked up before the skip decision, not after: without it there
            # is nothing to judge the cached sheet's freshness against.
            out = db.get(ModelOutput, oid)
            if out is None:
                raise LookupError(f"ModelOutput {oid} not found")
            glb_abs = Path(config.ASSET_DIR) / out.asset_path
            if sheet_is_current(abs_path, glb_abs):
                continue
            tiles = capture_multi(str(glb_abs), spec["azimuths"], spec["elev"])
            sheet = tile_contact_sheet(tiles, spec["cols"], spec["rows"])
            abs_path.write_bytes(sheet)
            _stamp_current(abs_path, glb_abs)
            rendered += 1
        except Exception as exc:  # noqa: BLE001 — best-effort batch: log + record, don't abort
            logger.warning(
                "contact-sheet render failed for output %s (%s): %s",
                oid,
                condition,
                exc,
                exc_info=True,
            )
            failures.append({"oid": oid, "error": f"{type(exc).__name__}: {exc}"})
    return {"rendered": rendered, "errors": len(failures), "failures": failures}
