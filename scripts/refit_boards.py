"""Refit both leaderboards after votes change — the step every harvest and release ends with.

A refit has TWO halves, and every recorded mistake here is forgetting the second one:

  * `service.recompute_all`       -> the HUMAN Bradley-Terry board (`rating`, `kingdom_rating`)
  * `service.recompute_judge_all` -> the VLM JUDGE board (`judge_rating`, `kingdom_judge_rating`)

Running only the first leaves the judge board serving whatever fit it last computed. That is
how a self-play-polluted InstantMesh fit kept its #1 slot through a recompute meant to correct
exactly that (2026-07-12), and how an unwarmed kingdom judge cache left the plants leaderboard
taking 11 seconds while the human half looked freshly computed (2026-07-09 — where the standing
"run /admin/recompute" note turned out to have omitted the judge half from the start).

So this script does not offer the halves separately. Skipping the judge refit is not a mode; it
is the bug, and the only way to get it here is to edit the file.

The fit runs HERE, against a local SQLite file, never through the admin routes on the public
instance: the Bradley-Terry bootstrap is the heaviest computation in this codebase and it is
what exhausted the 1 GB Fly machine.

Usage:
    BIO3D_DATABASE_URL=sqlite:///data/study/arena-study.db \
        .venv/bin/python scripts/refit_boards.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, service  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402

#: The condition the site serves and every runbook refit has used. `JudgeRating` is keyed by
#: view condition, so a refit under any other value leaves the served board untouched.
DEFAULT_VIEW_CONDITION = "multi4"


def require_local_db(url: str | None) -> None:
    """Refuse to refit anything but a local SQLite file.

    Moving the fit off the server is the whole point — a server-side URL would put the same
    load back on the server, a round trip at a time. Unset is fine: the default is already a
    local path.
    """
    if url and not url.lower().startswith("sqlite"):
        scheme = url.split("://", 1)[0]
        raise SystemExit(
            f"refusing to refit a non-local database ({scheme}://...). The Bradley-Terry "
            "bootstrap must run against a local SQLite file — point BIO3D_DATABASE_URL at one."
        )


def refit(db, view_condition: str = DEFAULT_VIEW_CONDITION, log=None) -> dict:
    """Refit the human board and then the judge board. Both, always — see the module docstring.

    Each half commits its own scopes, so an interrupted run leaves the human board refit and
    the judge board stale rather than a half-written fit. Re-running is the fix; a refit is
    idempotent for a given set of votes.
    """
    note = log or (lambda _message: None)

    started = time.monotonic()
    human = service.recompute_all(db)
    human_seconds = time.monotonic() - started
    note(f"human board: {human['scopes']} scopes in {human_seconds:.0f}s")

    started = time.monotonic()
    judge = service.recompute_judge_all(db, view_condition=view_condition)
    judge_seconds = time.monotonic() - started
    note(f"judge board ({view_condition}): {judge['criteria']} criteria in {judge_seconds:.0f}s")

    return {
        "human": human,
        "judge": judge,
        "seconds": round(human_seconds + judge_seconds, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refit the human and VLM-judge leaderboards on the current votes.",
        epilog="Set BIO3D_DATABASE_URL to choose the database; it must be a local SQLite file.",
    )
    parser.add_argument(
        "--view-condition",
        default=DEFAULT_VIEW_CONDITION,
        help=f"judge view condition to refit (default: {DEFAULT_VIEW_CONDITION})",
    )
    args = parser.parse_args(argv)

    require_local_db(config.DATABASE_URL)
    # A DB copy can predate a table this refit writes; create_all only adds what is missing.
    init_db()

    print(f"refitting {config.DATABASE_URL}")
    with SessionLocal() as db:
        result = refit(db, view_condition=args.view_condition, log=print)
    print(f"done in {result['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
