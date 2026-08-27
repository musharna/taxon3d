"""Prune the data/study snapshot pile, without ever dropping the only copy of a vote.

A `PRE-<operation>` snapshot is taken before every destructive step on the study DB, which is
the right habit and the reason more than one incident here was recoverable. Nothing ever removed
them, so the directory reached 60 files and 419 MB.

Age alone must not authorise a deletion. The danger is specific and has happened: on 2026-07-26,
300 votes were found living only in a throwaway audit DB, and 80 more were lost because the copy
was taken with `cp` while a WAL was open. So age only nominates a candidate; what PERMITS
removal is proving the snapshot's votes are already in the live study DB. A snapshot that fails
that check is kept regardless of age, and so is one whose WAL is non-empty — a populated WAL can
hold committed rows the .db file alone does not show, which is exactly how those 80 went missing.

Dry run by default. Nothing is deleted without --apply.

Usage:
    .venv/bin/python scripts/prune_study_snapshots.py                 # report only
    .venv/bin/python scripts/prune_study_snapshots.py --older-than 30 --apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LIVE_NAME = "arena-study.db"
SIDECAR_SUFFIXES = ("-wal", "-shm")


def _votes(path: Path) -> set[tuple] | None:
    """Every (session_id, comparison_id) in `path`, or None if it is not a readable study DB.

    Opened `immutable=1`: this must not be able to write, and must not leave a -wal beside a
    snapshot it was only supposed to inspect. The pair is the right key because comparison_id is
    unique per vote and both values are append-only, so it survives the id reassignment a refit
    performs on derived tables.
    """
    try:
        con = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    except sqlite3.Error:
        return None
    try:
        return set(con.execute("SELECT session_id, comparison_id FROM vote").fetchall())
    except sqlite3.Error:
        return None  # not a study DB (a CSV, a sidecar, a corrupt file) — never a candidate
    finally:
        con.close()


def _sidecars(snapshot: Path) -> list[Path]:
    return [p for s in SIDECAR_SUFFIXES if (p := snapshot.with_name(snapshot.name + s)).exists()]


def prune(study_dir: Path, older_than_days: int = 30, apply: bool = False) -> dict:
    """Report (and with apply=True, delete) snapshots that are safely redundant.

    Returns `removed` / `kept_recent` / `protected` / `unreadable`, so a caller can show what
    was spared and why — a pruner that only reports what it deleted hides its own mistakes.
    """
    study_dir = Path(study_dir)
    live = study_dir / LIVE_NAME
    live_votes = _votes(live)
    if live_votes is None:
        raise SystemExit(f"refusing to prune: cannot read the live study DB at {live}")

    cutoff = time.time() - older_than_days * 86400
    out: dict = {
        "removed": [],
        "kept_recent": [],
        "protected": [],
        "unreadable": [],
        "freed_bytes": 0,
    }

    for path in sorted(study_dir.iterdir()):
        if not path.is_file() or path.name == LIVE_NAME:
            continue
        if path.name.endswith(SIDECAR_SUFFIXES):
            continue  # handled with the snapshot they belong to, never on their own

        votes = _votes(path)
        if votes is None:
            out["unreadable"].append(path)
            continue
        if path.stat().st_mtime >= cutoff:
            out["kept_recent"].append(path)
            continue

        extra = votes - live_votes
        wal = path.with_name(path.name + "-wal")
        if extra or (wal.exists() and wal.stat().st_size > 0):
            out["protected"].append(path)
            continue

        # Sized BEFORE the unlink: measuring afterwards reports 0, because the files whose
        # bytes are being counted no longer exist.
        out["freed_bytes"] += path.stat().st_size + sum(s.stat().st_size for s in _sidecars(path))
        out["removed"].append(path)
        if apply:
            for side in _sidecars(path):
                side.unlink()
            path.unlink()

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune redundant data/study snapshots, keeping any that hold a unique vote."
    )
    parser.add_argument(
        "--study-dir", default="data/study", help="directory holding arena-study.db"
    )
    parser.add_argument(
        "--older-than", type=int, default=30, help="only consider snapshots older than N days"
    )
    parser.add_argument("--apply", action="store_true", help="actually delete (default: report)")
    args = parser.parse_args(argv)

    result = prune(Path(args.study_dir), older_than_days=args.older_than, apply=args.apply)

    freed = result["freed_bytes"] / 1e6
    verb = "removed" if args.apply else "would remove"
    for path in result["removed"]:
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime) if path.exists() else None
        print(f"  {verb}: {path.name}" + (f"  ({stamp:%Y-%m-%d})" if stamp else ""))
    for path in result["protected"]:
        print(f"  KEPT (holds votes the live DB lacks, or a non-empty WAL): {path.name}")

    print(
        f"\n{verb} {len(result['removed'])} snapshot(s), {freed:.0f} MB; "
        f"kept {len(result['kept_recent'])} recent, "
        f"{len(result['protected'])} protected, "
        f"{len(result['unreadable'])} not a database"
    )
    if not args.apply and result["removed"]:
        print("dry run — nothing deleted. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
