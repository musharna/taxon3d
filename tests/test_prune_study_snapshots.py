"""Pruning the data/study snapshot pile without ever dropping the only copy of a vote.

`data/study` accumulates a `PRE-<operation>` snapshot before every destructive step, which is
the right habit — but it reached 60 files and 419 MB, and nothing ever removed them. The risk in
automating that removal is specific and has happened here: on 2026-07-26, 300 votes were found
living only in a throwaway audit DB, and 80 more were lost because the copy was taken with `cp`
while a WAL was open.

So the guard is not "is this file old" but "does this file hold a vote the live study DB does
not". Age only selects candidates; the subset check is what permits deletion, and a snapshot
that fails it is kept no matter how old.
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts import prune_study_snapshots as prune


def _db(path, votes):
    """A minimal stand-in for a study DB: just the table the pruner reads."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE vote (session_id TEXT, comparison_id INTEGER)")
    con.executemany("INSERT INTO vote VALUES (?, ?)", votes)
    con.commit()
    con.close()


def _age(path, days):
    """Backdate a file so the age window can be exercised without waiting."""
    import os
    import time

    t = time.time() - days * 86400
    os.utime(path, (t, t))


@pytest.fixture
def study(tmp_path):
    live = tmp_path / "arena-study.db"
    _db(live, [("s1", 1), ("s1", 2), ("s2", 3)])
    return tmp_path


def test_a_subset_snapshot_past_the_window_is_removed(study):
    old = study / "arena-study.PRE-THING-20260101_000000.db"
    _db(old, [("s1", 1)])  # strictly fewer votes than live
    _age(old, 90)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert not old.exists()
    assert [p.name for p in result["removed"]] == [old.name]


def test_a_snapshot_holding_a_vote_the_live_db_lacks_is_kept(study):
    """The whole point. This one is older than any window and still must survive."""
    rogue = study / "arena-study.PRE-ROGUE-20260101_000000.db"
    _db(rogue, [("s1", 1), ("s9", 999)])  # 999 exists nowhere else
    _age(rogue, 90)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert rogue.exists(), "deleted the only copy of a vote"
    assert [p.name for p in result["protected"]] == [rogue.name]
    assert result["removed"] == []


def test_the_live_database_is_never_a_candidate(study):
    live = study / "arena-study.db"
    _age(live, 900)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert live.exists()
    assert live not in result["removed"]


def test_a_dry_run_removes_nothing(study):
    old = study / "arena-study.PRE-THING-20260101_000000.db"
    _db(old, [("s1", 1)])
    _age(old, 90)

    result = prune.prune(study, older_than_days=30, apply=False)

    assert old.exists(), "dry run deleted a file"
    assert [p.name for p in result["removed"]] == [old.name], "dry run must still report it"


def test_a_snapshot_inside_the_window_is_kept(study):
    recent = study / "arena-study.PRE-RECENT-20260826_000000.db"
    _db(recent, [("s1", 1)])
    _age(recent, 3)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert recent.exists()
    assert result["removed"] == []


def test_a_file_that_is_not_a_database_is_left_alone(study):
    stray = study / "calibration_labels.csv"
    stray.write_text("a,b\n1,2\n")
    _age(stray, 90)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert stray.exists(), "removed a file it could not read as a DB"
    assert stray in result["unreadable"]


def test_sidecars_go_with_the_snapshot_they_belong_to(study):
    old = study / "arena-study.PRE-THING-20260101_000000.db"
    _db(old, [("s1", 1)])
    wal = study / "arena-study.PRE-THING-20260101_000000.db-wal"
    shm = study / "arena-study.PRE-THING-20260101_000000.db-shm"
    wal.write_bytes(b"")
    shm.write_bytes(b"\0" * 32768)
    for p in (old, wal, shm):
        _age(p, 90)

    prune.prune(study, older_than_days=30, apply=True)

    assert not wal.exists() and not shm.exists(), "orphaned a sidecar"


def test_a_non_empty_wal_protects_its_snapshot(study):
    """A populated WAL may hold committed data the .db file alone does not show — reading the
    snapshot immutable would not see it, so the subset check cannot be trusted here. This is the
    exact shape of the 2026-07-26 loss, so it is refused rather than reasoned about."""
    old = study / "arena-study.PRE-WAL-20260101_000000.db"
    _db(old, [("s1", 1)])
    wal = study / "arena-study.PRE-WAL-20260101_000000.db-wal"
    wal.write_bytes(b"\0" * 4096)
    for p in (old, wal):
        _age(p, 90)

    result = prune.prune(study, older_than_days=30, apply=True)

    assert old.exists() and wal.exists()
    assert old in result["protected"]
