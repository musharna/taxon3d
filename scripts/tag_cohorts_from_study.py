# scripts/tag_cohorts_from_study.py
"""Copy voter-session cohort tags from the study database onto a COPY of a prod pull.

Why this exists (2026-09-06): the harvest writes cohort tags (`internal`, `pilot-1`, `wave-2`)
into `data/study/arena-study.db`; prod never receives them, and 17 pre-launch internal sessions
have no `voter_session` row on prod at all. The board is fit on the study DB, so it excludes
those 262 votes; the Hugging Face export was run from a prod pull, so it kept them. Tagging the
copy first makes "every vote the leaderboard counts" true by construction.

Edits only a scratch copy: a path under data/prod-pulls/ is refused, because a pull is evidence.

Usage:
  python3 -c "import sqlite3; s=sqlite3.connect('data/prod-pulls/<pull>.db'); \\
              d=sqlite3.connect('/scratch/prod_tagged.db'); s.backup(d)"
  .venv/bin/python scripts/tag_cohorts_from_study.py --study data/study/arena-study.db \\
      --prod-copy /scratch/prod_tagged.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def apply_tags(study: Path, prod_copy: Path) -> dict:
    if "prod-pulls" in Path(prod_copy).parts:
        raise ValueError(f"{prod_copy} is under data/prod-pulls/; tag a scratch copy, never a pull")
    s = sqlite3.connect(f"file:{study}?mode=ro", uri=True)
    tags = dict(s.execute("SELECT session_id, cohort FROM voter_session WHERE cohort IS NOT NULL"))
    s.close()
    p = sqlite3.connect(prod_copy)
    existing = dict(p.execute("SELECT session_id, cohort FROM voter_session"))
    created = updated = unchanged = 0
    for sid, cohort in tags.items():
        if sid not in existing:
            # Trust 1.0 and zero counters: exactly what prod's outer join treated the missing row
            # as, so the only thing that changes is the cohort.
            p.execute(
                "INSERT INTO voter_session (session_id, trust, gold_seen, gold_passed, n_votes, "
                "captcha_verified, created, updated, user_id, cohort) "
                "VALUES (?, 1.0, 0, 0, 0, 0, '1970-01-01', '1970-01-01', NULL, ?)",
                (sid, cohort),
            )
            created += 1
        elif existing[sid] != cohort:
            p.execute("UPDATE voter_session SET cohort=? WHERE session_id=?", (cohort, sid))
            updated += 1
        else:
            unchanged += 1
    p.commit()
    q = ",".join("?" * len(tags)) or "''"
    (n_votes,) = p.execute(
        f"SELECT count(*) FROM vote WHERE session_id IN ({q})",  # nosec B608 - only ? placeholders are interpolated; values bound
        list(tags),
    ).fetchone()
    p.close()
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "votes_now_tagged": n_votes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--study", type=Path, required=True)
    ap.add_argument("--prod-copy", type=Path, required=True)
    args = ap.parse_args()
    print(apply_tags(args.study, args.prod_copy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
