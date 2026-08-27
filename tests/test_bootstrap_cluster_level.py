"""Which level the Bradley-Terry cluster bootstrap resamples at: ballot, or voter.

`ranking._bootstrap_scores` already resamples whole CLUSTERS rather than individual pairs, for
the right reason — the K-1 pairs derived from one k-wise ballot are not independent, so per-pair
resampling fake-tightens the intervals. But the key it clusters on is built in
`service._scope_rows`, and it was a BALLOT key. One person's hundred ballots therefore counted as
a hundred independent clusters, which is the same error one level up: a voter's ballots are no
more independent of each other than a ballot's pairs are.

The 2026-08-25 pilot made that concrete — a single voter supplied 30% of all ballots.

The level is configurable rather than simply corrected because changing it MOVES PUBLISHED RANKS
(measured: all four paradigms), which is a methods decision and not a bug fix. `ballot` stays the
default so nothing published moves without someone choosing it. These tests pin both levels and,
most importantly, pin the default — a silent flip of that default is the failure that would
matter.

Note this changes only the intervals. The point estimates are the same either way: the MLE is fit
on the same matches, and clustering only governs how they are resampled. It also cannot touch
"firm", which is `n_games >= 30` (a vote count) and never a property of a confidence interval.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app import config, service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    KBallot,
    ModelOutput,
    Task,
    Vote,
)
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


_PFX = "bcl"


def _clear(db):
    # Order matters, and FK enforcement is on: comparison.ballot_id references kballot.id, so the
    # comparisons must go before the ballots they point at. Deleting the ballots first raises
    # "FOREIGN KEY constraint failed" in a LATER test — the leftovers only collide when something
    # next tries to clean up after them, which makes it read as an unrelated failure.
    db.query(Vote).filter(Vote.session_id.like(f"{_PFX}-%")).delete(synchronize_session=False)
    cascade_delete(db, Comparison, Comparison.session_id.like(f"{_PFX}-%"))
    task_ids = [t.id for t in db.execute(select(Task).where(Task.title.like("BCL %"))).scalars()]
    if task_ids:
        db.query(KBallot).filter(KBallot.task_id.in_(task_ids)).delete(synchronize_session=False)
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like(f"{_PFX}/%"))
    cascade_delete(db, Task, Task.title.like("BCL %"))
    cascade_delete(db, Generator, Generator.slug.like(f"{_PFX}-g%"))
    cascade_delete(db, Category, Category.slug.like(f"{_PFX}-%"))
    db.commit()


def _fixture(db):
    """Two same-paradigm generators and a task to hang comparisons off.

    Same paradigm matters: `_scope_rows` drops cross-paradigm pairs outright, so generators in
    different paradigms would yield an empty row list and every assertion below would pass
    vacuously.
    """
    _clear(db)
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    cat = Category(slug=f"{_PFX}-cat", name="BCL Category")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="BCL Task", prompt="p", active=True)
    db.add(task)
    db.flush()
    g1 = Generator(slug=f"{_PFX}-g1", name="BCL-G1", paradigm="image_recon")
    g2 = Generator(slug=f"{_PFX}-g2", name="BCL-G2", paradigm="image_recon")
    db.add_all([g1, g2])
    db.flush()
    o1 = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"{_PFX}/1.glb")
    o2 = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"{_PFX}/2.glb")
    db.add_all([o1, o2])
    db.flush()
    return crit, task, g1, g2, o1, o2


def _pairwise_vote(db, crit, task, o1, o2, session_id: str):
    """A native pairwise ballot (ballot_id NULL) cast by `session_id`."""
    comp = Comparison(
        task_id=task.id,
        output_a_id=o1.id,
        output_b_id=o2.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comp)
    db.flush()
    db.add(Vote(comparison_id=comp.id, winner="a", session_id=session_id))
    db.flush()
    return comp


def _kwise_ballot(db, crit, task, o1, o2, session_id: str, relations: int = 2):
    """One k-wise ballot resolving into `relations` comparisons that share its ballot_id."""
    kb = KBallot(
        task_id=task.id,
        criterion_id=crit.id,
        session_id=session_id,
        output_ids_json=json.dumps([o1.id, o2.id]),
        best_output_id=o1.id,
        resolved=True,
    )
    db.add(kb)
    db.flush()
    comps = []
    for _ in range(relations):
        comp = Comparison(
            task_id=task.id,
            output_a_id=o1.id,
            output_b_id=o2.id,
            criterion_id=crit.id,
            session_id=session_id,
            ballot_id=kb.id,
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id=session_id))
        db.flush()
        comps.append(comp)
    return kb, comps


def _group_keys(db, crit) -> list[int]:
    """The bootstrap cluster key of every admissible row, in scan order."""
    return [gkey for _a, _b, _w, gkey in service._scope_rows(db, crit.id)]


# ------------------------------------------------------------------ the default is what ships


def test_the_default_cluster_level_is_ballot():
    """Guards the published numbers. Changing the level moves ranks in every paradigm, so the
    default flipping without a deliberate decision is the failure this file most cares about."""
    assert config.BT_CLUSTER_LEVEL == "ballot"


# ------------------------------------------------------------------ ballot level (today)


def test_one_voters_ballots_are_separate_clusters_at_ballot_level():
    """The behaviour being preserved as the default — and the contrast that shows the voter
    level below is doing something rather than being wired to nothing."""
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-solo")
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-solo")
        db.commit()

        keys = _group_keys(db, crit)
        assert len(keys) == 2
        assert len(set(keys)) == 2, "one voter's two ballots collapsed at ballot level"

        _clear(db)


# ------------------------------------------------------------------ voter level


def test_one_voters_ballots_are_one_cluster_at_voter_level(monkeypatch):
    monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voter")
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-solo")
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-solo")
        db.commit()

        keys = _group_keys(db, crit)
        assert len(keys) == 2
        assert len(set(keys)) == 1, "one voter's ballots are still separate clusters"

        _clear(db)


def test_different_voters_are_different_clusters_at_voter_level(monkeypatch):
    """Positive control for the test above: the voter level must not collapse EVERYTHING into a
    single cluster, which would also satisfy 'one voter, one cluster'."""
    monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voter")
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-alice")
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-bob")
        db.commit()

        keys = _group_keys(db, crit)
        assert len(keys) == 2
        assert len(set(keys)) == 2, "two different voters were merged into one cluster"

        _clear(db)


def test_a_kwise_ballots_relations_still_share_a_cluster_at_voter_level(monkeypatch):
    """Voter clustering must SUBSUME ballot clustering, not replace it: the k-wise pairs that
    already moved together must keep doing so, since they are nested inside the voter."""
    monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voter")
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _kwise_ballot(db, crit, task, o1, o2, f"{_PFX}-quad", relations=3)
        db.commit()

        keys = _group_keys(db, crit)
        assert len(keys) == 3
        assert len(set(keys)) == 1

        _clear(db)


def test_two_ballots_from_one_voter_merge_across_ballot_shapes(monkeypatch):
    """A voter who casts a k-wise ballot AND a pairwise one is still one cluster. At ballot level
    these are two keys drawn from two different namespaces (kballot id, and negated comparison
    id); at voter level the namespace is the person."""
    monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voter")
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _kwise_ballot(db, crit, task, o1, o2, f"{_PFX}-mixed", relations=2)
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-mixed")
        db.commit()

        keys = _group_keys(db, crit)
        assert len(keys) == 3
        assert len(set(keys)) == 1

        _clear(db)


# ------------------------------------------------------------------ a typo must not be silent


def test_an_unrecognised_cluster_level_is_refused(monkeypatch):
    """Falling back to the default on a typo is the worst outcome available: the operator would
    believe they had measured voter clustering while the ballot fit was published."""
    monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voterr")
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-solo")
        db.commit()

        with pytest.raises(ValueError, match="BIO3D_BT_CLUSTER_LEVEL"):
            service._scope_rows(db, crit.id)

        _clear(db)


# ------------------------------------------------------------------ the point estimate is fixed


def test_the_level_moves_intervals_but_not_the_point_estimate(monkeypatch):
    """The claim that makes this safe to offer as a switch at all: same matches, same fit. If a
    level change moved the point estimate, it would be changing the answer and not the
    uncertainty around it."""
    with SessionLocal() as db:
        crit, task, _g1, _g2, o1, o2 = _fixture(db)
        for who in ("alice", "alice", "alice", "bob", "carol"):
            _pairwise_vote(db, crit, task, o1, o2, f"{_PFX}-{who}")
        db.commit()

        monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "ballot")
        by_ballot, _ = service._matches_for_scope(db, crit.id)
        monkeypatch.setattr(config, "BT_CLUSTER_LEVEL", "voter")
        by_voter, _ = service._matches_for_scope(db, crit.id)

        assert by_ballot == by_voter, "the match list itself must not depend on the cluster level"

        _clear(db)
