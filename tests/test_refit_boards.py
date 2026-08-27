"""scripts/refit_boards.py — the post-harvest board refit, as ONE operation.

A refit has two halves, and the second is the one that gets forgotten. `service.recompute_all`
refits the HUMAN Bradley-Terry board; only `service.recompute_judge_all` refits the VLM judge
board. Running the human half alone leaves the judge board serving its previous fit — that is
how a self-play-polluted InstantMesh fit held #1 after a corrective recompute (2026-07-12), and
how an unwarmed kingdom judge cache left the plants leaderboard at 11s (2026-07-09, where the
standing "run /admin/recompute" note was found to have omitted the judge half outright).

Both incidents are the same omission, so the script makes the halves inseparable and these
tests hold it that way. A refit that writes `Rating` but not `JudgeRating` must fail here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Rating,
    Task,
    Vote,
)
from scripts import refit_boards
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


_PFX = "rb"


def _clear(db):
    gen_ids = [
        g.id
        for g in db.execute(select(Generator).where(Generator.slug.like(f"{_PFX}-g%"))).scalars()
    ]
    if gen_ids:
        # Scope every rating delete to OUR generators: a refit rewrites the GLOBAL
        # (category_id=None) scope shared with every other module's fixtures in this suite run.
        db.query(Rating).filter(Rating.generator_id.in_(gen_ids)).delete(synchronize_session=False)
        db.query(JudgeRating).filter(JudgeRating.generator_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
    task_ids = [t.id for t in db.execute(select(Task).where(Task.title.like("RB %"))).scalars()]
    if task_ids:
        db.query(JudgeVote).filter(JudgeVote.task_id.in_(task_ids)).delete(
            synchronize_session=False
        )
    db.query(Vote).filter(Vote.session_id.like(f"{_PFX}-%")).delete(synchronize_session=False)
    cascade_delete(db, Comparison, Comparison.session_id.like(f"{_PFX}-%"))
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like(f"{_PFX}/%"))
    cascade_delete(db, Task, Task.title.like("RB %"))
    cascade_delete(db, Generator, Generator.slug.like(f"{_PFX}-g%"))
    cascade_delete(db, Category, Category.slug.like(f"{_PFX}-%"))
    db.commit()


def _seed(db):
    """Two generators with BOTH kinds of evidence: one decisive human vote and nine decisive
    JudgeVotes, so a half-done refit is visible as a missing board rather than an empty fit."""
    _clear(db)
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    cat = Category(slug=f"{_PFX}-fungi", name="RB Fungi")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="RB Bolete", prompt="p", active=True)
    db.add(task)
    db.flush()
    g1 = Generator(slug=f"{_PFX}-g1", name="RB-G1")
    g2 = Generator(slug=f"{_PFX}-g2", name="RB-G2")
    db.add_all([g1, g2])
    db.flush()
    o1 = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"{_PFX}/1.glb")
    o2 = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"{_PFX}/2.glb")
    db.add_all([o1, o2])
    db.flush()
    comp = Comparison(
        task_id=task.id,
        output_a_id=o1.id,
        output_b_id=o2.id,
        criterion_id=crit.id,
        session_id=f"{_PFX}-s1",
    )
    db.add(comp)
    db.flush()
    db.add(Vote(comparison_id=comp.id, winner="a", session_id=f"{_PFX}-s1"))
    for i in range(9):
        db.add(
            JudgeVote(
                task_id=task.id,
                output_a_id=o1.id,
                output_b_id=o2.id,
                criterion_id=crit.id,
                winner="a",
                view_condition="multi4",
                judge_model="claude-sonnet-4-6",
                swap_group=f"{_PFX}-jg-{i}",
                rationale="",
            )
        )
    db.commit()
    return cat, task, g1, g2


def _rating_rows(db, gen_ids):
    return db.execute(select(Rating).where(Rating.generator_id.in_(gen_ids))).scalars().all()


def _judge_rows(db, gen_ids):
    return (
        db.execute(select(JudgeRating).where(JudgeRating.generator_id.in_(gen_ids))).scalars().all()
    )


# ------------------------------------------------------------------ the two halves are one


def test_a_refit_writes_the_judge_board_and_not_only_the_human_one():
    """The regression this script exists to prevent: a refit that stops after recompute_all."""
    with SessionLocal() as db:
        _cat, _task, g1, g2 = _seed(db)
        gen_ids = [g1.id, g2.id]

        assert not _rating_rows(db, gen_ids), "precondition: no human board cached yet"
        assert not _judge_rows(db, gen_ids), "precondition: no judge board cached yet"

        refit_boards.refit(db)

        assert _rating_rows(db, gen_ids), "human board was not refit"
        assert _judge_rows(db, gen_ids), "JUDGE board was not refit — the omitted half"

        _clear(db)


def test_the_refit_reports_what_each_half_did():
    with SessionLocal() as db:
        _cat, _task, _g1, _g2 = _seed(db)

        result = refit_boards.refit(db)

        assert result["human"]["status"] == "ok"
        assert result["judge"]["status"] == "ok"
        # Scope count is the human half's own measure of how much board it rewrote; an
        # operator reads it to confirm the refit covered more than the global scope.
        assert result["human"]["scopes"] >= 1

        _clear(db)


def test_the_view_condition_reaches_the_judge_half():
    """JudgeRating is keyed by view condition, so passing one through wrongly would silently
    refit a board nobody reads while leaving the served one stale."""
    with SessionLocal() as db:
        _cat, _task, g1, g2 = _seed(db)
        gen_ids = [g1.id, g2.id]

        refit_boards.refit(db, view_condition="solo")

        conditions = {r.view_condition for r in _judge_rows(db, gen_ids)}
        assert conditions == {"solo"}, f"judge half ignored the view condition: {conditions}"

        _clear(db)


def test_multi4_is_the_default_view_condition():
    """The condition the site serves and every runbook refit has used."""
    with SessionLocal() as db:
        _cat, _task, _g1, _g2 = _seed(db)

        result = refit_boards.refit(db)

        assert result["judge"]["view_condition"] == "multi4"

        _clear(db)


# ------------------------------------------------------------------ where it may point


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@ep-something.neon.tech/arena",
        "postgres://user:pw@host/arena",
    ],
)
def test_a_remote_database_is_refused(url):
    """The fit is deliberately run on the operator's machine. Bradley-Terry with bootstrap is
    the heaviest thing this codebase does — it is what OOM-killed the 1 GB Fly machine — so the
    refit must never be pointed at a server-side database from here."""
    with pytest.raises(SystemExit) as exc:
        refit_boards.require_local_db(url)
    assert "local" in str(exc.value).lower()


def test_a_local_sqlite_database_is_allowed():
    """Positive control: the guard must not refuse the target the script is FOR."""
    refit_boards.require_local_db("sqlite:///data/study/arena-study.db")
