"""Report one recruited cohort against the metrics its wave pre-registered.

WHY THIS EXISTS AS CODE, NOT A QUERY TYPED AFTER THE FACT: the wave-2 analysis was
pre-registered before launch, and an analysis assembled once the numbers are visible is an
analysis chosen to suit them. Writing it down as a function fixes the definitions while the
result is still unknown.

THE DEFINITION THAT MATTERS — attention-check coverage is TWO numbers, never one:

  * `served`   — voters shown at least one attention check. Property of the SCHEDULER.
  * `measured` — voters whose answer produced a trust reading. Property of the ANSWERS.

Collapsing them into a single word, "coverage", is a real mistake with a cost: wave 2's plan
predicted "coverage reaches 100%", read 57%, and looked for ~an hour like the scheduler had
failed. It had not. Every uncounted voter had been served checks (one of them four times) and
abstained on all of them, and `gold_outcome` leaves both counters alone on an abstention BY
DESIGN — "both are bad" is an honest answer to a pair whose good member is unconvincing, so it
must not score as a failure, and therefore cannot score as a pass either.

Consequence worth stating plainly, because it bounds every future wave: while abstention is
neutral, `measured == clusters` is NOT AN ACHIEVABLE TARGET. Do not set it as one again. The
lever that raises `measured` is better-separated gold pairs, not the scheduler.

Counts voters as `n_votes > 0`, never as sessions: an arena page-load creates a VoterSession
before anyone votes, so sessions over-count people (the pilot logged 16 for 15).

Usage:
    BIO3D_DATABASE_URL=sqlite:///data/study/arena-study.db \
        .venv/bin/python scripts/analyse_wave.py wave-2
"""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Comparison, Vote, VoterSession

# An answer scores only if it names a side; see integrity.gold_outcome, which this MUST match.
BINARY = ("a", "b")


def analyse(db: Session, cohort: str) -> dict:
    """Compute the pre-registered metrics for one cohort."""
    sessions = list(db.execute(select(VoterSession).where(VoterSession.cohort == cohort)).scalars())
    voters = [s for s in sessions if (s.n_votes or 0) > 0]

    served_ids = set(
        db.execute(
            select(Comparison.session_id)
            .where(Comparison.is_gold.is_(True))
            .group_by(Comparison.session_id)
        ).scalars()
    )
    served = [s for s in voters if s.session_id in served_ids]
    measured = [s for s in voters if (s.gold_seen or 0) > 0]
    failed = [s for s in measured if (s.gold_passed or 0) < (s.gold_seen or 0)]

    gold_answers = list(
        db.execute(
            select(Vote.winner)
            .join(Comparison, Comparison.id == Vote.comparison_id)
            .join(VoterSession, VoterSession.session_id == Comparison.session_id)
            .where(Comparison.is_gold.is_(True), VoterSession.cohort == cohort)
        ).scalars()
    )
    abstained = [w for w in gold_answers if w not in BINARY]

    ballots = [int(s.n_votes or 0) for s in voters]
    total = sum(ballots)
    # Dominance is the pre-registered check on whether the frozen-counter bug explained the
    # pilot's one-voter-30% result. If a single voter still exceeds 20% with the counter fixed,
    # the finding is about voter behaviour and the cluster-level bootstrap is what answers it.
    top_share = (max(ballots) / total) if total else 0.0

    return {
        "cohort": cohort,
        "sessions": len(sessions),
        "voters": len(voters),
        "votes": total,
        "served": len(served),
        "measured": len(measured),
        "failed_check": len(failed),
        "gold_answers": len(gold_answers),
        "abstentions": len(abstained),
        "abstention_rate": round(len(abstained) / len(gold_answers), 3) if gold_answers else 0.0,
        "top_voter_share": round(top_share, 3),
        "median_votes": sorted(ballots)[len(ballots) // 2] if ballots else 0,
        "low_trust": len([s for s in voters if (s.trust if s.trust is not None else 1.0) < 1.0]),
    }


def format_report(r: dict) -> str:
    pct = lambda n: f"{100 * n / r['voters']:.0f}%" if r["voters"] else "n/a"  # noqa: E731
    return "\n".join(
        [
            f"cohort            : {r['cohort']}",
            f"sessions          : {r['sessions']}  (voters = n_votes>0: {r['voters']})",
            f"votes             : {r['votes']}  median/head {r['median_votes']}",
            f"checks SERVED     : {r['served']} of {r['voters']} ({pct(r['served'])})  <- scheduler",
            f"checks MEASURED   : {r['measured']} of {r['voters']} ({pct(r['measured'])})  <- answers",
            f"  failed a check  : {r['failed_check']}",
            f"  low trust       : {r['low_trust']}",
            f"gold answers      : {r['gold_answers']}  abstained {r['abstentions']}"
            f" ({100 * r['abstention_rate']:.0f}%)",
            f"top voter share   : {100 * r['top_voter_share']:.0f}%"
            f"  {'-> ABOVE the 20% pre-registered threshold' if r['top_voter_share'] > 0.2 else ''}",
        ]
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with SessionLocal() as db:
        print(format_report(analyse(db, argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
