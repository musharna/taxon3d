"""Pairwise is the default ballot; k-wise is the explicit opt-in.

This inverts the routing PR #121 established. That change was argued on information yield: a pair
yields ONE Bradley-Terry relation and a 4-up ballot yields THREE, so serving a pair where a quad
existed looked like discarding two thirds of what a voter offered.

Two things make that argument weaker than it read, and one observation settled it:

1. The 4-up ballot collects a single best-of pick (`submitKvote(ballotId, bestOutputId)`), not a
   ranking. A quad therefore yields 3 relations from FOUR loaded meshes; two sequential pairs
   yield 2 relations from four. The real ratio is 1.5x per mesh delivered, not 3x per ballot --
   and only if a quad does not cost more completions than two pairs.
2. Ballot weight and the fidelity invariant both scale against k. Since the ballot-uniform
   fidelity rule, LOD coverage multiplies across slots: ~33% per-output coverage yields 0.33^4
   (~1.4%) of ballots eligible at k=4 versus 0.33^2 (~11%) at k=2.
3. The site owner used the live arena and reported the 4-up ballot as overwhelming. Completed
   ballots -- not relations per ballot -- are the scarce input, so a shape that costs completions
   loses even at a favourable relation ratio.

K-wise is NOT deleted: it stays whole behind `?set=kwise`, exactly as pairwise stayed reachable
behind `?set=pair` while k-wise was the default. These tests pin the routing decision and the
follow-up continuity in BOTH directions -- the pin-to-pairwise bug PR #121 fixed has a mirror
image now, where an opted-in voter gets dumped back to pairs after their first vote.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, integrity
from app.database import SessionLocal
from app.main import app
from app.models import Category, Criterion, Generator, GoldPair, ModelOutput, Task
from tests.factories import cascade_delete, delete_outputs_matching

_PFX = "kwdef"


def _clear(db):
    delete_outputs_matching(db, ModelOutput.asset_path.like(f"{_PFX}/%"))
    cascade_delete(db, Task, Task.title.like("KWDEF %"))
    cascade_delete(db, Generator, Generator.slug.like(f"{_PFX}-g%"))
    cascade_delete(db, Category, Category.slug.like(f"{_PFX}-%"))
    db.commit()


def _seed(db, slug_suffix: str, titles: list[str], n_generators: int):
    """One category holding `len(titles)` tasks, each with `n_generators` outputs from
    `n_generators` distinct same-paradigm generators.

    Scoping each case to its own category lets a test ask for a task that CAN fill a quad and one
    that cannot, without depending on what the rest of the suite left in the shared database.
    The paradigm must be on the vote roster: `vote_pool_excluded_generator_ids` drops NULL-paradigm
    generators outright, so a fixture without one is silently invisible to the pool.

    More than one task per category is what makes the FOLLOW-UP tests mean anything. Matchmaking
    will not re-serve a quad the session has already seen, so a single-task category always
    degrades to a pair on the second ballot — indistinguishable from a routing failure, and it
    made the k-wise continuity test fail against code where k-wise was still the default.
    """
    if db.execute(select(Criterion).where(Criterion.slug == "overall")).first() is None:
        db.add(Criterion(slug="overall", name="Overall"))
        db.commit()
    cat = Category(slug=f"{_PFX}-{slug_suffix}", name=f"KWDEF {slug_suffix}")
    db.add(cat)
    db.flush()
    tasks = []
    for t, title in enumerate(titles):
        task = Task(category_id=cat.id, title=f"KWDEF {title}", prompt="p")
        db.add(task)
        db.flush()
        tasks.append(task)
        for i in range(n_generators):
            g = Generator(
                slug=f"{_PFX}-g{slug_suffix}{t}{i}",
                name=f"G{slug_suffix}{t}{i}",
                paradigm="image_recon",
            )
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(
                    task_id=task.id,
                    generator_id=g.id,
                    asset_path=f"{_PFX}/{slug_suffix}{t}{i}.glb",
                    asset_format="glb",
                    source="api:fal:trellis",
                )
            )
    db.commit()
    return cat, tasks


@pytest.fixture
def arena(monkeypatch):
    """A client plus two scoped categories: `quad` can fill a 4-up ballot, `pair` cannot.

    The quad category holds TWO quad-fillable tasks so that a follow-up ballot has somewhere to
    go — see `_seed` on why one task is not enough.

    Admissibility is bypassed for the same reason the kingdom suite bypasses it — these fixture
    outputs have no mesh bytes on disk, and the rubric is orthogonal to which ballot shape gets
    routed.
    """
    monkeypatch.setattr("app.admissibility.non_admitted_output_ids", lambda db, rubric=None: set())
    with SessionLocal() as db:
        _clear(db)
        quad_cat, quad_tasks = _seed(db, "quad", ["Quad Rose", "Quad Fern"], 4)
        pair_cat, pair_tasks = _seed(db, "pair", ["Pair Bolete"], 3)
        quad_ids = {t.id for t in quad_tasks}
        yield TestClient(app), quad_cat.slug, pair_cat.slug, quad_ids, pair_tasks[0].id
        _clear(db)


def test_default_next_serves_a_pair_even_where_a_quad_is_available(arena):
    """No `?set=` at all — the shape a real voter's browser requests — must be 1v1.

    The `quad` fixture exists precisely so this cannot pass by accident: four admitted
    same-paradigm outputs from four distinct generators are sitting there, which is exactly the
    condition under which the old default served a 4-up ballot.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "kind" not in data, f"default ballot was not pairwise: kind={data.get('kind')}"
    assert data["task"]["title"].startswith("KWDEF Quad"), data["task"]
    assert "comparison_id" in data


def test_default_next_still_serves_a_ballot_when_no_quad_exists(arena):
    """Positive control for the test above: a category that could never fill a quad must still
    return a usable ballot, so a 200 on the quad category is evidence about SHAPE rather than
    evidence that the arena happens to serve pairs when it has nothing else."""
    client, _quad_slug, pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={pair_slug}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "kind" not in data, f"expected a pairwise ballot, got {data.get('kind')}"
    assert data["task"]["title"] == "KWDEF Pair Bolete"
    assert "comparison_id" in data


def test_set_kwise_opts_into_the_four_up_ballot(arena):
    """K-wise is demoted, not deleted. Without this the 4-up builder, its endpoint, its grid and
    its reveal become unreachable code — the exact state PR #121 was written to fix, in reverse."""
    client, quad_slug, _pair_slug, quad_task_ids, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=kwise")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("kind") == "kwise", f"?set=kwise did not serve a 4-up ballot: {data.keys()}"
    assert data["task"]["id"] in quad_task_ids
    assert len(data["outputs"]) == 4
    # Four DISTINCT models: a repeat would manufacture a self-match in the BT fit.
    assert len({o["output_id"] for o in data["outputs"]}) == 4


def test_set_kwise_degrades_to_a_pair_when_no_quad_exists(arena):
    """The opt-in cannot 404 a voter whose task has only three generators. This degrade is a
    property of `_build_kwise_comparison` itself and must survive the demotion."""
    client, _quad_slug, pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={pair_slug}&set=kwise")
    assert r.status_code == 200, r.text
    assert "comparison_id" in r.json()


def test_set_pair_is_still_accepted(arena):
    """Redundant with the default now, but the URL is in the wild and in docs. A 404 or a
    surprise 4-up ballot from a link someone bookmarked is a regression either way."""
    client, quad_slug, _pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=pair")
    assert r.status_code == 200, r.text
    assert "kind" not in r.json()


def test_voting_a_pairwise_ballot_returns_a_pairwise_follow_up(arena):
    """Default continuity: one vote must not silently promote the voter to the 4-up shape."""
    client, quad_slug, _pair_slug, _, _ = arena
    first = client.get(f"/api/next?criterion=overall&category={quad_slug}").json()
    assert "comparison_id" in first, first

    r = client.post(
        f"/api/vote?criterion=overall&category={quad_slug}",
        json={"comparison_id": first["comparison_id"], "winner": "a"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"  # positive control: the vote itself still records
    assert body["next"] is not None, "voter dead-ended after one vote"
    assert body["next"].get("kind") != "kwise", "follow-up ballot jumped to the 4-up shape"


def test_voting_a_kwise_ballot_returns_a_kwise_follow_up(arena):
    """The mirror image of the pin-to-pairwise bug PR #121 fixed.

    That bug existed because `/api/vote` built its follow-up with one builder unconditionally,
    so whichever shape was NOT the default became a one-ballot dead end. Flipping the default
    moves the exposed side of that asymmetry from pairwise to k-wise: a voter who asked for the
    4-up ballot must keep getting it, rather than being dropped back to pairs after one vote.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    first = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=kwise").json()
    assert first.get("kind") == "kwise", first

    r = client.post(
        f"/api/kvote?criterion=overall&category={quad_slug}&set=kwise",
        json={
            "ballot_id": first["ballot_id"],
            "best_output_id": first["outputs"][0]["output_id"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"  # positive control: the k-wise vote itself still records
    assert body["next"] is not None, "opted-in voter dead-ended after one vote"
    assert body["next"].get("kind") == "kwise", "opted-in voter was dropped back to pairs"


def test_kwise_ballot_carries_the_reference_photos(arena, monkeypatch):
    """A k-wise ballot must show the reference photos, exactly as the pairwise one does.

    Judging "which of these is a better Rosa" with nothing to compare against turns a fidelity
    benchmark into a beauty contest. The opt-in path is used less than the default, which is
    precisely why this needs a test rather than a pair of eyes.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    refs = [{"url": "/media/ref/rose.jpg", "credit": "CC BY 2.0 — someone"}]
    monkeypatch.setattr("app.service.reference_images_for_task", lambda db, task: refs)
    # Attention checks off. They are pairwise by construction and are entitled to displace a
    # quad (test_gold_attention_checks_fire_on_both_ballot_shapes asserts exactly that), so
    # leaving them on would let one stand in for the quad this test is trying to inspect.
    monkeypatch.setattr(config, "GOLD_RATE", 0.0)

    data = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=kwise").json()
    assert data.get("kind") == "kwise", data
    assert data["task"]["references"] == refs
    # The category chip is shared with the 2-up view; without this the k-wise ballot had
    # nothing to put in it and showed the literal string "K-wise" to voters instead.
    assert data["task"]["category"] == "KWDEF quad"

    # Positive control: the default shape carries them too, from the same source.
    pair = client.get(f"/api/next?criterion=overall&category={quad_slug}").json()
    assert pair["task"]["references"] == refs


def test_gold_attention_checks_fire_on_both_ballot_shapes(arena, monkeypatch):
    """Attention checks are injected at the routing point, ahead of both builders, so they must
    be independent of which shape is the default.

    This is the reason gold injection was hoisted out of `_build_comparison` in the first place:
    while it lived inside one builder, changing the default silently took the whole trust layer
    with it. Asserting BOTH shapes here is what makes that hoist load-bearing rather than
    incidental — a future flip back must not be able to darken it either.

    GOLD_RATE=1.0 makes this deterministic: every ballot must be the gold check, even though a
    quad is sitting right there for the taking.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    monkeypatch.setattr(config, "GOLD_RATE", 1.0)
    integrity.reset_rate_limits()

    with SessionLocal() as db:
        task = db.execute(select(Task).where(Task.title == "KWDEF Quad Rose")).scalars().one()
        # generator_id is NOT NULL even for gold; `is_gold` is what keeps these out of
        # matchmaking and the rankings, so borrowing a fixture generator is harmless.
        gen_id = (
            db.execute(select(Generator.id).where(Generator.slug == f"{_PFX}-gquad00"))
            .scalars()
            .one()
        )
        good = ModelOutput(
            task_id=task.id,
            generator_id=gen_id,
            asset_path=f"{_PFX}/gold_good.glb",
            asset_format="glb",
            source="gold",
            is_gold=True,
        )
        bad = ModelOutput(
            task_id=task.id,
            generator_id=gen_id,
            asset_path=f"{_PFX}/gold_bad.glb",
            asset_format="glb",
            source="gold",
            is_gold=True,
        )
        db.add_all([good, bad])
        db.flush()
        db.add(GoldPair(task_id=task.id, good_output_id=good.id, bad_output_id=bad.id))
        db.commit()

    for query in ("", "&set=kwise"):
        r = client.get(f"/api/next?criterion=overall&category={quad_slug}{query}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("kind") != "kwise", f"a k-wise ballot displaced the attention check{query}"
        assert "comparison_id" in data, data
