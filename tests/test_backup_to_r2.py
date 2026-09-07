"""Retention for the scheduled prod-DB backup: prune by age, never below a floor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.backup_to_r2 import to_prune

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _keys(n_days: list[int]) -> list[tuple[str, datetime]]:
    return [(f"prod-db/arena.{d}.db", NOW - timedelta(days=d)) for d in n_days]


def test_prunes_only_past_the_window():
    got = to_prune(_keys([1, 2, 3, 4, 5, 6, 7, 8, 31, 40]), keep_days=30, now=NOW)
    assert got == ["prod-db/arena.31.db", "prod-db/arena.40.db"]


def test_never_leaves_fewer_than_the_floor():
    """Everything is stale after a month of failed runs; the last seven copies still stay."""
    got = to_prune(_keys([35, 36, 37, 38, 39, 40, 41, 42, 43]), keep_days=30, now=NOW)
    assert got == ["prod-db/arena.42.db", "prod-db/arena.43.db"]


def test_empty_bucket_prunes_nothing():
    assert to_prune([], keep_days=30, now=NOW) == []
