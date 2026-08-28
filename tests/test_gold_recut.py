"""Re-cutting an existing gold pair must replace it WITHOUT orphaning its history.

`reseed_gold` is idempotent by design — it skips any task that already has a GoldPair — so the
2026-08-27 picker fix could not reach pairs 2 and 3, the two measured at 75% and 81% "both are
bad". Re-cutting needs an explicit opt-in.

The old gold outputs must be RETAINED, not deleted: every historical `Comparison` on those pairs
references them by output id, and 148 gold answers already exist. Deleting would orphan them.
Retire by hiding instead, which is what keeps them out of serving while the votes still resolve.
"""

from __future__ import annotations

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import GoldPair, ModelOutput
from scripts import reseed_gold
from tests.factories import a_task_id, make_outputs


def _seed_one(db, tid):
    """A task with candidates and an existing gold pair, as production has."""
    outs = make_outputs(db, 3)
    for o in outs:
        o.task_id = tid
        o.asset_format = "glb"
    db.flush()
    reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None)
    return db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().one()


def test_without_recut_an_existing_pair_is_still_skipped():
    """Control: the idempotence that makes reseed safe to re-run must survive."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        before = _seed_one(db, tid)
        res = reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None)
        assert res["created"] == 0 and res["skipped"] == 1
        after = db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().one()
        assert after.good_output_id == before.good_output_id
        db.rollback()


def test_recut_replaces_the_pair():
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        before = _seed_one(db, tid)
        res = reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None, recut=True)
        assert res["created"] == 1
        after = db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().one()
        assert after.good_output_id != before.good_output_id
        assert after.bad_output_id != before.bad_output_id
        db.rollback()


def test_recut_RETAINS_the_old_gold_outputs():
    """They carry 148 existing gold answers between them; deleting would orphan those."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        before = _seed_one(db, tid)
        old_ids = (before.good_output_id, before.bad_output_id)
        reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None, recut=True)
        for oid in old_ids:
            assert db.get(ModelOutput, oid) is not None, "history references this row"
        db.rollback()


def test_recut_HIDES_the_old_gold_outputs():
    """Retained for history, but must stop being servable."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        before = _seed_one(db, tid)
        old_ids = (before.good_output_id, before.bad_output_id)
        reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None, recut=True)
        for oid in old_ids:
            assert db.get(ModelOutput, oid).hidden_at is not None
        db.rollback()


def test_recut_leaves_exactly_one_pair_for_the_task():
    """A stale GoldPair left beside the new one would make pick_gold_pair nondeterministic."""
    init_db()
    with SessionLocal() as db:
        tid = a_task_id(db)
        _seed_one(db, tid)
        reseed_gold.reseed_gold(db, [tid], build_decoy=lambda p: None, recut=True)
        pairs = db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().all()
        assert len(pairs) == 1
        db.rollback()
