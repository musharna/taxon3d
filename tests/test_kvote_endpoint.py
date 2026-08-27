import json

from app.database import SessionLocal, init_db
from app.models import Comparison, KBallot
from tests.factories import make_outputs, overall_criterion


def setup_module(_m):
    init_db()


def _quad(db):
    """Four outputs sharing one task, plus the criterion — the real ancestry a k-wise ballot
    needs.

    This file used to force ModelOutput rows into existence at literal ids (10-13, 20-23) with
    `task_id=1, generator_id=1`, and assert against those literals. Those parent ids resolved
    only when some other module had already seeded this shared temp DB, so the file could not
    be run on its own. Nothing here actually needs a particular id — only that the ids are real
    and distinct — so the fixture now mints its own and the assertions read them back.
    """
    return make_outputs(db, 4), overall_criterion(db)


def _ballot(db, outs, crit):
    b = KBallot(
        task_id=outs[0].task_id,
        criterion_id=crit.id,
        session_id="s",
        output_ids_json=json.dumps([o.id for o in outs]),
    )
    db.add(b)
    db.flush()
    return b


def test_resolve_kballot_decomposes_to_three_pairs():
    from app import service

    with SessionLocal() as db:
        outs, crit = _quad(db)
        best, rest = outs[0], outs[1:]
        b = _ballot(db, outs, crit)
        n = service.resolve_kballot(db, b, best_output_id=best.id, session_id="s")
        assert n == 3
        assert b.resolved is True
        comps = db.query(Comparison).filter_by(ballot_id=b.id).all()
        assert len(comps) == 3
        # every derived comparison has the best output in slot a and winner 'a'
        rest_ids = {o.id for o in rest}
        for c in comps:
            assert c.output_a_id == best.id and c.output_b_id in rest_ids
            assert c.vote.winner == "a"
        db.rollback()


def test_resolve_all_bad_makes_no_relations():
    from app import service

    with SessionLocal() as db:
        outs, crit = _quad(db)
        b = _ballot(db, outs, crit)
        n = service.resolve_kballot(db, b, best_output_id=None, session_id="s")
        assert n == 0
        assert b.resolved is True
        assert db.query(Comparison).filter_by(ballot_id=b.id).count() == 0
        db.rollback()
