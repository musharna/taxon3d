#!/usr/bin/env python3
"""Pull votes cast on the PUBLIC instance back into the internal study database.

Why this has to exist as a standing step, not a one-off rescue:

Every vote a real visitor casts on taxon3d.org lands in the public Postgres and nowhere
else. The internal study DB is the source of truth the leaderboards are FITTED on, and the export
promotes those fitted boards outward. So the moment the public site takes a vote, the two
databases diverge, and every subsequent release publishes rankings computed without the newest
real human input — while vote volume is the single thing bottlenecking every board.

Measured 2026-07-31: 70 live votes (ids 463..532) existed in no internal database at all. They
were not at risk of deletion — `import_public` merges by primary key — but they were invisible to
every ranking, which is the quieter and worse failure. This is the same shape as the 2026-07-26
incident where 300 votes accumulated in a throwaway audit DB.

Safety, in order:

* snapshot the study DB first via sqlite3's backup API, NEVER `cp` — copying a WAL database with
  `cp` silently drops whatever is still in the -wal, which is how 80 rescued votes were lost on
  2026-07-26;
* refuse on any primary-key collision rather than overwriting an existing internal row;
* insert rows only, never recompute ratings here. Boards are refitted on the internal instance
  afterwards. Never against the public deploy: the BT bootstrap OOM-kills the 1 GB machine.

Usage:

    # see what would move, touch nothing
    python scripts/harvest_live_votes.py --dry-run

    # do it (snapshots first, prints the snapshot path)
    python scripts/harvest_live_votes.py --apply

Reads the public database URL from BIO3D_PUBLIC_DATABASE_URL, or BIO3D_DATABASE_URL when that is
unset (the deploy env file sets the latter).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import bindparam, create_engine, text  # noqa: E402

from app.config import normalize_database_url  # noqa: E402

DEFAULT_STUDY = Path("data/study/arena-study.db")

#: Columns are read from the live table and intersected with the study schema, so a column that
#: exists on only one side never silently drops a value or explodes the insert.
#:
#: `voter_session` is here because a vote without its session row cannot be interpreted: `cohort`
#: is what separates a recruited, paid vote from an ambient one, and `trust`/gold are what let a
#: fit weigh or exclude it. Omitting it left 33 sessions holding votes in study with no session
#: row at all, and stranded the paid pilot's provenance as the only copy on one Fly volume.
#: It is keyed and scoped differently from the other two — see `plan()`.
TABLES = ("comparison", "vote", "voter_session")


class HarvestConflict(RuntimeError):
    """A row we were about to insert already exists internally with that primary key."""


def study_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def snapshot(study_path: Path) -> Path:
    """sqlite3 backup API — never `cp`. Returns the snapshot path."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = study_path.with_suffix(f".PRE-HARVEST-{stamp}.db")
    src = sqlite3.connect(f"file:{study_path}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    return dst


def plan(study_path: Path, public_url: str) -> dict:
    """Everything that would move, computed without writing anything."""
    con = sqlite3.connect(f"file:{study_path}?mode=ro", uri=True)
    max_vote = con.execute("select coalesce(max(id), 0) from vote").fetchone()[0]
    have_votes = {r[0] for r in con.execute("select id from vote")}
    have_comps = {r[0] for r in con.execute("select id from comparison")}
    have_sessions = {r[0] for r in con.execute("select session_id from voter_session")}
    # Every session holding a vote internally, not just the sessions of the votes moving in THIS
    # run: a session harvested before this table was covered could otherwise never be backfilled,
    # and a re-run once the votes have already landed would find nothing to do.
    voted_sessions = {r[0] for r in con.execute("select distinct session_id from vote")}
    cols = {t: study_columns(con, t) for t in TABLES}
    con.close()

    eng = create_engine(normalize_database_url(public_url))
    with eng.connect() as c:
        vote_rows = [
            dict(r._mapping)
            for r in c.execute(
                text("select * from vote where id > :m order by id"), {"m": max_vote}
            )
        ]
        comp_ids = sorted({r["comparison_id"] for r in vote_rows})
        comp_rows = []
        if comp_ids:
            # `IN` with an expanding bindparam, not Postgres's `= any(:ids)`. The public
            # instance is a SQLite file on a Fly volume now, and SQLite has no any(); the
            # Postgres spelling made this the one line that could not run against production.
            # Expanding renders the placeholders per dialect, so both backends work.
            comp_stmt = text("select * from comparison where id in :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            comp_rows = [dict(r._mapping) for r in c.execute(comp_stmt, {"ids": comp_ids})]

        want_sessions = sorted(
            (voted_sessions | {r["session_id"] for r in vote_rows}) - have_sessions
        )
        sess_rows = []
        if want_sessions:
            sess_stmt = text("select * from voter_session where session_id in :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            sess_rows = [dict(r._mapping) for r in c.execute(sess_stmt, {"ids": want_sessions})]

    new_comps = [r for r in comp_rows if r["id"] not in have_comps]
    return {
        "study_max_vote_id": max_vote,
        "votes": vote_rows,
        "comparisons": new_comps,
        "voter_sessions": sess_rows,
        "vote_collisions": sorted({r["id"] for r in vote_rows} & have_votes),
        "comparison_collisions": sorted({r["id"] for r in comp_rows} & have_comps),
        "gold_votes": sum(1 for r in comp_rows if r.get("is_gold")),
        "sessions": len({r.get("session_id") for r in vote_rows}),
        "cohorts": sorted({str(r["cohort"]) for r in sess_rows if r.get("cohort")}),
        "columns": cols,
    }


def apply(study_path: Path, p: dict) -> dict:
    """Insert sessions, then comparisons, then votes (FK order). Caller has already snapshotted.

    Session rows are inserted with `or ignore` rather than guarded by the collision refusal that
    protects votes and comparisons. That difference is deliberate. A vote id is a per-database
    sequence, so the same id can name two DIFFERENT votes across two databases and overwriting
    one would silently reattribute a real human judgement — hence the hard refusal. A session id
    is a random 64-hex token, so a shared id is the SAME session on both sides and there is
    nothing to misattribute; the row is only a counter snapshot. Refusing on it would mean that
    once any session exists on both sides, every later harvest fails outright. Keeping the
    internal row is still the rule — `or ignore` never overwrites.
    """
    if p["vote_collisions"] or p["comparison_collisions"]:
        raise HarvestConflict(
            f"refusing to overwrite internal rows — vote ids {p['vote_collisions'][:8]}, "
            f"comparison ids {p['comparison_collisions'][:8]}"
        )
    con = sqlite3.connect(study_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        for row in p.get("voter_sessions", []):
            keep = p["columns"]["voter_session"]
            use = [k for k in keep if k in row]
            con.execute(
                f"insert or ignore into voter_session ({','.join(use)}) "
                f"values ({','.join('?' for _ in use)})",
                [row[k] for k in use],
            )
        for table, rows in (("comparison", p["comparisons"]), ("vote", p["votes"])):
            keep = p["columns"][table]
            for row in rows:
                use = [k for k in keep if k in row]
                con.execute(
                    f"insert into {table} ({','.join(use)}) values ({','.join('?' for _ in use)})",
                    [row[k] for k in use],
                )
        con.commit()
        n_votes = con.execute("select count(*) from vote").fetchone()[0]
        n_comps = con.execute("select count(*) from comparison").fetchone()[0]
        n_sess = con.execute("select count(*) from voter_session").fetchone()[0]
    finally:
        con.close()
    return {
        "votes_total": n_votes,
        "comparisons_total": n_comps,
        "voter_sessions_total": n_sess,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--study", default=str(DEFAULT_STUDY))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.dry_run == a.apply:
        raise SystemExit("choose exactly one of --dry-run / --apply")

    public_url = os.environ.get("BIO3D_PUBLIC_DATABASE_URL") or os.environ.get("BIO3D_DATABASE_URL")
    if not public_url:
        raise SystemExit(
            "set BIO3D_PUBLIC_DATABASE_URL (or BIO3D_DATABASE_URL) to the PUBLIC database"
        )
    study = Path(a.study)
    if not study.is_file():
        raise SystemExit(f"no study database at {study}")

    p = plan(study, public_url)
    print(f"study max vote id      : {p['study_max_vote_id']}")
    print(f"live votes to harvest  : {len(p['votes'])}  ({p['sessions']} distinct sessions)")
    print(f"new comparisons needed : {len(p['comparisons'])}")
    print(f"  gold among them      : {p['gold_votes']}  (attention checks; never feed rankings)")
    print(
        f"voter sessions to add  : {len(p['voter_sessions'])}"
        f"  cohorts={p['cohorts'] or '-'}  (provenance: cohort/trust/gold)"
    )
    print(
        f"pk collisions          : votes={len(p['vote_collisions'])} "
        f"comparisons={len(p['comparison_collisions'])}"
    )

    # Sessions are checked too, not just votes: a backfill run has zero new votes by definition
    # (the votes landed in an earlier harvest) and gating solely on votes would report "nothing
    # to harvest" while the provenance for 33 sessions sat unharvested on the public instance.
    if not p["votes"] and not p["voter_sessions"]:
        print("\nnothing to harvest — study is level with the public instance.")
        return 0
    if a.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    snap = snapshot(study)
    print(f"\nsnapshot (sqlite3 backup API, not cp): {snap}")
    res = apply(study, p)
    print(
        f"study now: votes={res['votes_total']}  comparisons={res['comparisons_total']}"
        f"  voter_sessions={res['voter_sessions_total']}"
    )
    print(
        "\nNEXT: refit the boards on the INTERNAL instance "
        "(never /admin/recompute* against the public deploy — the BT bootstrap OOM-kills "
        "the 1 GB machine), then export a fresh bundle."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
