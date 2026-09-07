"""Cohort tags live in the STUDY database (the harvest writes them there); prod never had them.
Anything exported from a prod copy therefore keeps votes the published board excludes — 262
pre-launch internal votes on 2026-09-06, from 17 sessions that have no voter_session row on
prod at all. scripts/tag_cohorts_from_study.py copies the tags onto a prod COPY so the export
and the board disagree about nothing."""

from __future__ import annotations

import sqlite3

from scripts.tag_cohorts_from_study import apply_tags

DDL = """
CREATE TABLE voter_session (
  session_id VARCHAR(64) PRIMARY KEY, trust FLOAT NOT NULL, gold_seen INTEGER NOT NULL,
  gold_passed INTEGER NOT NULL, n_votes INTEGER NOT NULL, captcha_verified BOOLEAN NOT NULL,
  created DATETIME NOT NULL, updated DATETIME NOT NULL, user_id INTEGER, cohort VARCHAR(40));
CREATE TABLE vote (id INTEGER PRIMARY KEY, session_id VARCHAR(64), comparison_id INTEGER,
  winner VARCHAR(8), created DATETIME);
"""


def _db(path, sessions, votes):
    c = sqlite3.connect(path)
    c.executescript(DDL)
    for sid, cohort in sessions:
        c.execute(
            "INSERT INTO voter_session VALUES (?,1.0,0,0,0,0,'2026-06-01','2026-06-01',NULL,?)",
            (sid, cohort),
        )
    for sid in votes:
        c.execute(
            "INSERT INTO vote (session_id, comparison_id, winner, created) VALUES (?,1,'a','2026-06-02')",
            (sid,),
        )
    c.commit()
    c.close()


def test_missing_sessions_are_created_and_existing_ones_retagged(tmp_path):
    study = tmp_path / "study.db"
    prod = tmp_path / "prod.db"
    _db(study, [("s-int", "internal"), ("s-pilot", "pilot-1"), ("s-none", None)], [])
    # prod: s-int has votes but NO session row; s-pilot exists untagged; s-ambient is prod-only
    _db(prod, [("s-pilot", None), ("s-ambient", None)], ["s-int", "s-int", "s-pilot"])
    report = apply_tags(study, prod)
    assert report == {"created": 1, "updated": 1, "unchanged": 0, "votes_now_tagged": 3}
    c = sqlite3.connect(prod)
    rows = dict(c.execute("SELECT session_id, cohort FROM voter_session"))
    assert rows == {"s-int": "internal", "s-pilot": "pilot-1", "s-ambient": None}
    # positive control: a trust of 1.0 on the created row, so the board's trust filter keeps it
    # exactly as prod's outer-join NULL did; only the cohort changes what is counted.
    assert c.execute("SELECT trust FROM voter_session WHERE session_id='s-int'").fetchone() == (
        1.0,
    )


def test_refuses_a_target_that_looks_like_the_live_pull(tmp_path):
    """The script edits a COPY. A path under data/prod-pulls/ is evidence, never a scratch file."""
    import pytest

    study = tmp_path / "study.db"
    _db(study, [], [])
    with pytest.raises(ValueError, match="prod-pulls"):
        apply_tags(study, tmp_path / "data" / "prod-pulls" / "arena.x.db")
