# tests/test_aprime_voter_sd.py
"""The voter random-intercept SD is the single assumption the A′ power answer is most
sensitive to (80 vs 240 voters, $171 vs $512), so it is estimated from wave-2 data rather
than assumed. These tests are recovery tests: the estimator must return a known truth."""

from __future__ import annotations

import numpy as np
import pytest

from scripts.paper.aprime_voter_sd import estimate_sd_voter, simulate_ballots


def test_recovers_zero_when_voters_are_homogeneous():
    """Positive control's negative half: with no voter effect the estimator must not invent
    one. A moment estimator that forgot to subtract sampling noise would return ~0.5 here."""
    v, p, y = simulate_ballots(n_voters=120, n_ballots=40, sd_voter=0.0, seed=1)
    assert estimate_sd_voter(v, p, y) < 0.25


def test_recovers_a_known_moderate_voter_sd():
    v, p, y = simulate_ballots(n_voters=120, n_ballots=40, sd_voter=0.8, seed=2)
    assert estimate_sd_voter(v, p, y) == pytest.approx(0.8, abs=0.2)


def test_recovers_a_known_large_voter_sd():
    v, p, y = simulate_ballots(n_voters=120, n_ballots=40, sd_voter=1.5, seed=3)
    assert estimate_sd_voter(v, p, y) == pytest.approx(1.5, abs=0.35)


def test_is_monotone_in_the_truth():
    est = [
        estimate_sd_voter(*simulate_ballots(n_voters=120, n_ballots=40, sd_voter=s, seed=4))
        for s in (0.0, 0.6, 1.2)
    ]
    assert est[0] < est[1] < est[2]


def test_small_voters_are_dropped_not_silently_included():
    """A voter with 2 ballots carries almost no information about their own intercept and
    enormous sampling noise; including them inflates the estimate."""
    v = np.array([0] * 30 + [1] * 30 + [2] * 2)
    p = np.full(len(v), 0.5)
    y = (np.arange(len(v)) % 2).astype(float)
    kept = estimate_sd_voter(v, p, y, min_ballots=10, return_kept=True)[1]
    assert kept == 2


def test_refuses_when_no_voter_has_enough_ballots():
    v = np.array([0, 0, 1, 1])
    p = np.full(4, 0.5)
    y = np.array([1.0, 0.0, 1.0, 0.0])
    with pytest.raises(ValueError, match="no voter"):
        estimate_sd_voter(v, p, y, min_ballots=10)


# IRON_LAW_OK


def test_a_zero_point_estimate_still_reports_a_nonzero_upper_bound():
    """The failure this guards: a small panel clips to sd=0 and the number gets read as 'voters
    are homogeneous, buy the cheap study'. A zero estimate means 'below this panel's detection
    floor', which is a bound, not a value."""
    from scripts.paper.aprime_voter_sd import upper_bound_sd

    v, p, y = simulate_ballots(n_voters=12, n_ballots=12, sd_voter=0.0, seed=10)
    assert estimate_sd_voter(v, p, y) == 0.0
    assert upper_bound_sd(v, p, y, reps=120, seed=10) > 0.2


def test_upper_bound_covers_the_truth():
    from scripts.paper.aprime_voter_sd import upper_bound_sd

    v, p, y = simulate_ballots(n_voters=40, n_ballots=20, sd_voter=1.0, seed=11)
    assert upper_bound_sd(v, p, y, reps=120, seed=11) >= 1.0


def test_upper_bound_tightens_as_the_panel_grows():
    """More voters and more ballots must buy a tighter bound, or the bound is not measuring
    the panel's information."""
    from scripts.paper.aprime_voter_sd import upper_bound_sd

    small = simulate_ballots(n_voters=12, n_ballots=12, sd_voter=0.0, seed=12)
    big = simulate_ballots(n_voters=150, n_ballots=60, sd_voter=0.0, seed=12)
    assert upper_bound_sd(*big, reps=120, seed=12) < upper_bound_sd(*small, reps=120, seed=12)
