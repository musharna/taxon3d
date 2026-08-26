"""Tests for vote integrity / anti-abuse: rate limit, dedup, gold trust, gating, captcha."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import config, integrity, service
from app.database import SessionLocal
from app.main import app
from app.models import Comparison, Criterion, ModelOutput, Vote, VoterSession
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


def _overall_id(db) -> int:
    return db.query(Criterion).filter_by(slug="overall").one().id


def _two_real_outputs(db) -> tuple[ModelOutput, ModelOutput]:
    # Two non-gold outputs from the same task.
    out = db.query(ModelOutput).filter_by(is_gold=False).first()
    mate = (
        db.query(ModelOutput)
        .filter(ModelOutput.task_id == out.task_id, ModelOutput.id != out.id, ~ModelOutput.is_gold)
        .first()
    )
    return out, mate


def test_rate_limit(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "VOTE_RATE_LIMIT", 3)
    integrity.reset_rate_limits()
    codes = []
    for _ in range(6):
        nxt = client.get("/api/next?set=pair").json()
        r = client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})
        codes.append(r.status_code)
    assert 429 in codes  # the limiter kicked in within the burst
    integrity.reset_rate_limits()


def test_dedup_same_pair():
    with SessionLocal() as db:
        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=cid,
            session_id="dedup-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id="dedup-sess"))
        db.commit()
        # Same unordered pair (either order) is a duplicate for this session.
        assert integrity.already_voted_pair(db, "dedup-sess", a.id, b.id, cid) is True
        assert integrity.already_voted_pair(db, "dedup-sess", b.id, a.id, cid) is True
        # A different session is unaffected.
        assert integrity.already_voted_pair(db, "other-sess", a.id, b.id, cid) is False


def test_gold_check_updates_trust(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "GOLD_RATE", 1.0)  # every comparison is a gold check
    integrity.reset_rate_limits()

    def vote_gold(correct: bool):
        nxt = client.get("/api/next").json()
        cid = nxt["comparison_id"]
        with SessionLocal() as db:
            comp = db.get(Comparison, cid)
            assert comp.is_gold is True
            expected = comp.gold_expected
        winner = expected if correct else ("a" if expected == "b" else "b")
        client.post("/api/vote", json={"comparison_id": cid, "winner": winner})

    vote_gold(correct=True)
    sid = client.cookies.get("bio3d_session")
    with SessionLocal() as db:
        vs = db.get(VoterSession, sid)
        assert vs.gold_seen == 1 and vs.gold_passed == 1
        assert vs.trust == 1.0

    for _ in range(5):
        vote_gold(correct=False)
    with SessionLocal() as db:
        vs = db.get(VoterSession, sid)
        assert vs.gold_seen >= 6
        assert vs.trust < config.TRUST_THRESHOLD  # repeated failures sink trust


def test_a_non_binary_gold_answer_abstains_rather_than_failing():
    """ "Both are bad" is not a wrong answer to an attention check.

    A gold pair is a real output against a degenerate decoy, and it asks one question: can you
    tell them apart. `bad` and `tie` answer neither yes nor no — they decline to prefer. Scoring
    them as failures measured willingness to pick a winner instead, and 10 of the 12 failures in
    the 2026-08-25 recruited pilot were exactly this, with 22% of that cohort's real ballots
    non-binary. It left the single largest contributor sitting at trust 0.500 — the admission
    threshold — one such answer away from having all 100 of their votes silently dropped from
    every board.

    Abstention means the check was never observed: neither counter moves, so trust is unchanged.
    """
    for winner in ("bad", "tie"):
        assert integrity.gold_outcome(winner, "a") is None
        assert integrity.gold_outcome(winner, "b") is None

    # Positive control in the same test: a real answer must still be scored, or "nothing is ever
    # a failure" would satisfy the assertions above just as well.
    assert integrity.gold_outcome("a", "a") is True
    assert integrity.gold_outcome("b", "a") is False


def test_an_abstained_gold_leaves_both_counters_and_trust_untouched():
    """The end-to-end shape of the above: an abstention must not consume the session's check."""
    with SessionLocal() as db:
        sid = "abstain-" + uuid.uuid4().hex[:12]
        integrity.record_gold_outcome(db, sid, True)  # one real, passed check
        db.commit()
        before = db.get(VoterSession, sid)
        seen_before, passed_before, trust_before = (
            before.gold_seen,
            before.gold_passed,
            before.trust,
        )

        outcome = integrity.gold_outcome("bad", "a")
        if outcome is not None:  # exactly what the vote endpoint does
            integrity.record_gold_outcome(db, sid, outcome)
        db.commit()

        after = db.get(VoterSession, sid)
        assert (after.gold_seen, after.gold_passed) == (seen_before, passed_before)
        assert after.trust == trust_before


def test_low_trust_votes_excluded_from_ranking():
    with SessionLocal() as db:
        # Isolate: clear votes from earlier tests so the match set is exactly ours.
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.query(VoterSession).delete()
        db.commit()

        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        gen_a, gen_b = a.generator_id, b.generator_id

        def cast(session_id, winner, trust):
            db.add(VoterSession(session_id=session_id, trust=trust))
            comp = Comparison(
                task_id=a.task_id,
                output_a_id=a.id,
                output_b_id=b.id,
                criterion_id=cid,
                session_id=session_id,
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner=winner, session_id=session_id))

        cast("hi-trust", "a", 1.0)  # A beats B  (counts)
        cast("lo-trust", "b", 0.1)  # B beats A  (should be excluded)
        db.commit()

        matches, _groups = service._matches_for_scope(db, cid, None)
    assert (gen_a, gen_b) in matches  # high-trust vote present
    assert (gen_b, gen_a) not in matches  # low-trust vote excluded


def test_internal_cohort_votes_are_excluded_from_ranking(monkeypatch):
    """Votes cast before a public instance existed must not fit the published boards.

    Commit 239bce1 (2026-07-28 02:54 -0400) brought the public instance up. 440 of the corpus's
    849 votes predate it and therefore cannot be visitor traffic — 421 of them from one session,
    which is half of every ranking. They stay in the DB as research data; this filter is what
    keeps them out of what gets published.

    Cohort is compared explicitly against NULL because SQL `not in` yields NULL for a NULL
    column, which would silently drop every untagged (i.e. ordinary) voter from the boards —
    the exact opposite of the intent. The untagged assertion below is that positive control.
    """
    monkeypatch.setattr(config, "EXCLUDED_COHORTS", frozenset({"internal"}))
    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.query(VoterSession).delete()
        db.commit()

        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        gen_a, gen_b = a.generator_id, b.generator_id

        def cast(session_id, winner, cohort):
            db.add(VoterSession(session_id=session_id, trust=1.0, cohort=cohort))
            comp = Comparison(
                task_id=a.task_id,
                output_a_id=a.id,
                output_b_id=b.id,
                criterion_id=cid,
                session_id=session_id,
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner=winner, session_id=session_id))

        cast("ambient", "a", None)  # untagged ordinary voter -> counts
        cast("prelaunch", "b", "internal")  # pre-launch internal    -> excluded
        db.commit()

        matches, _groups = service._matches_for_scope(db, cid, None)
    assert (gen_a, gen_b) in matches, "an untagged voter was dropped — the NULL case is broken"
    assert (gen_b, gen_a) not in matches, "an internal-cohort vote reached the published board"


def test_a_recruited_cohort_still_counts(monkeypatch):
    """Positive control for the filter itself: only the named cohorts are excluded.

    Without this, setting EXCLUDED_COHORTS to something over-broad — or excluding every tagged
    session — would pass the test above while silently discarding the 335 votes we paid for.
    """
    monkeypatch.setattr(config, "EXCLUDED_COHORTS", frozenset({"internal"}))
    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.query(VoterSession).delete()
        db.commit()

        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        gen_a, gen_b = a.generator_id, b.generator_id

        db.add(VoterSession(session_id="paid", trust=1.0, cohort="pilot-1"))
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=cid,
            session_id="paid",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id="paid"))
        db.commit()

        matches, _groups = service._matches_for_scope(db, cid, None)
    assert (gen_a, gen_b) in matches, "a recruited pilot vote was excluded"


def test_the_trend_sparkline_scopes_votes_the_same_way_the_ranking_does(monkeypatch):
    """`generator_trend_series` says it "mirrors `_matches_for_scope`'s scope filters".

    A mirror asserted only in a docstring is not enforced, and this pair has already drifted
    once: the cohort exclusion landed on the ranking query while the sparkline beside it kept
    counting the same votes. A row would then show a rank fitted on 409 votes next to a trend
    line drawn from 849 — the discrepancy visible to any reader, with no failing test.
    """
    monkeypatch.setattr(config, "EXCLUDED_COHORTS", frozenset({"internal"}))
    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.query(VoterSession).delete()
        db.commit()

        cid = _overall_id(db)
        a, b = _two_real_outputs(db)

        def cast(session_id, winner, cohort):
            db.add(VoterSession(session_id=session_id, trust=1.0, cohort=cohort))
            comp = Comparison(
                task_id=a.task_id,
                output_a_id=a.id,
                output_b_id=b.id,
                criterion_id=cid,
                session_id=session_id,
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner=winner, session_id=session_id))

        # Four ambient wins for A, four internal wins for B. Four is the floor: the function
        # returns [] for a generator with fewer than 4 scoped votes, so a thinner setup cannot
        # tell "correctly filtered" from "produced nothing".
        for i in range(4):
            cast(f"ambient-{i}", "a", None)
        for i in range(4):
            cast(f"prelaunch-{i}", "b", "internal")
        db.commit()

        matches, _ = service._matches_for_scope(db, cid, None)
        series = service.generator_trend_series(db, cid, None)

    ranking_games = len(matches)
    trend_games = sum(1 for v in series.get(a.generator_id, []) if v is not None)
    assert ranking_games == 4, f"the board should see four ambient votes, saw {ranking_games}"
    assert trend_games > 0, "sparkline produced nothing at all — harness broken, not a pass"
    # Every value the sparkline reports for A must be a clean win, because the only vote it is
    # allowed to see is the ambient one A won. Any internal vote leaking in drags this below 1.
    assert all(v == 1.0 for v in series[a.generator_id] if v is not None), (
        f"internal-cohort votes reached the trend sparkline: {series[a.generator_id]}"
    )


def test_captcha_gate(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    # verify_captcha now does REAL provider verification (SP1); stub it to accept any
    # non-empty token and reject empty, so this integration test exercises the vote-gate
    # WIRING (main.py → verify_captcha → 403). Provider logic is unit-tested in
    # test_captcha.py; a live provider call here would fail-close and 403 the valid leg.
    monkeypatch.setattr(integrity, "verify_captcha", lambda token, **kw: bool(token))
    integrity.reset_rate_limits()
    nxt = client.get("/api/next?set=pair").json()
    blocked = client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})
    assert blocked.status_code == 403
    nxt2 = client.get("/api/next?set=pair").json()
    ok = client.post(
        "/api/vote",
        json={"comparison_id": nxt2["comparison_id"], "winner": "a"},
        headers={"X-Captcha-Token": "any-non-empty"},
    )
    assert ok.status_code == 200


def test_methodology_page_renders():
    r = TestClient(app).get("/methodology")
    assert r.status_code == 200
    assert "integrity" in r.text.lower()
    assert "Gold attention checks" in r.text
