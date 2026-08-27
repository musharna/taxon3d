import json
from app import seed
from app.database import SessionLocal, init_db
from app.models import KBallot, Comparison
from tests.factories import make_outputs, overall_criterion


def setup_module(_m):
    init_db()


def test_kballot_in_force_delete_models():
    assert KBallot in seed._FORCE_DELETE_MODELS


def test_kballot_and_ballot_id_persist():
    with SessionLocal() as db:
        # Real parents. These FK columns are enforced, and the literals that used to sit here
        # (task_id=1, output_a_id=1) resolved only when some other module had already seeded
        # this shared temp DB — so the file could not be run on its own.
        outs = make_outputs(db, 4)
        crit = overall_criterion(db)
        b = KBallot(
            task_id=outs[0].task_id,
            criterion_id=crit.id,
            session_id="s",
            output_ids_json=json.dumps([o.id for o in outs]),
        )
        db.add(b)
        db.flush()
        assert b.resolved is False
        c = Comparison(
            task_id=outs[0].task_id,
            output_a_id=outs[0].id,
            output_b_id=outs[1].id,
            criterion_id=crit.id,
            session_id="s",
            ballot_id=b.id,
        )
        db.add(c)
        db.flush()
        assert c.ballot_id == b.id
        db.rollback()
