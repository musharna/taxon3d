# Retired scripts

Moved here with `git mv` (history intact) on 2026-09-04 after a repo-wide inbound-reference
grep (`*.py *.md *.yml *.sh *.toml`) found nothing outside the script itself. Nothing here is
wired into a pipeline; import paths are `scripts.archive.<name>`.

| script | superseded by |
|---|---|
| `verify_mobile_ux.py` | `tests/test_mobile_arena.py`, `tests/test_mobile_nav.py` (P0 mobile changes shipped) |
| `verify_onboarding.py` | `tests/test_onboarding.py`, `tests/test_onboarding_in_flow.py` |
| `verify_synced_rotation.py` | `tests/test_synced_rotation.py` |
| `verify_viewer_controls.py` | `tests/test_viewer_controls.py` |
| `source_reference_sidecars.py` | one-shot EXIF provenance-hint scrape over the old MVP gallery; licensing is now backfilled per source by `scripts/backfill_licenses.py` |
| `strip_default_cube.py` | the empty-scene runner fix (agentic runs no longer emit the startup cube); artefact detection in `app/mesh_subject.py` |
| `strip_ground_plane.py` | `app/mesh_subject.py` (scenery-plane classifier applied at ingest) |
| `rerun_cube_only_agentic.py` | the same runner fix; imports `scripts.archive.strip_default_cube` |
| `score_completeness_outputs.py` | `scripts/score_completeness.py --tasks` / `scripts/rederive_completeness.py` |
| `run_calibration_study.py` | calibration study COMPLETE (results in `docs/results/`); `app/calibration.py` holds the reusable logic |
| `build_calibration_set.py` | `app.calibration.build_calibration_set` (`tests/test_calibration.py`) |
| `calibration_report.py` | `app.calibration` report helpers (`tests/test_calibration_report.py`); report already written to `docs/results/` |
| `analyze_trait_calibration.py` | SP4 analysis complete; results in `docs/results/` |
| `infinigen_flower_realize.py` | `scripts/generate_infinigen.py` (`tests/test_generate_infinigen.py`) |

Kept in `scripts/` despite being on the retirement list, because something still references
them: `reorient_scans.py` (the only walker over `app/reorient.py`, now behind `app.dbguard`),
`render_spotlight.py` (`tests/test_render_spotlight.py`), `judge_capture.py` (five judge
scripts + `tests/test_judge_capture_live.py`), `strip_pedestal.py` (`tests/test_mesh_subject.py`
records that the classifier structurally cannot see a plinth — this named list is the only
tool), `disposition_rose_soybean.py` (`tests/test_disposition_rose_soybean.py`,
`scripts/export_hf_dataset.py`), `fetch_benchmarks.py` (`README.md`).
