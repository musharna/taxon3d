# tests/test_aprime_power.py
"""Power simulation for Study A′. Criterion is assigned per voter for a whole session, so the
criterion contrast is a BETWEEN-voter comparison: these tests pin that the simulation actually
models voter clustering rather than treating ballots as independent."""

from __future__ import annotations

import numpy as np
import pytest

from scripts.paper.aprime_power import (
    WAVE2_CLUSTER_SIZES,
    draw_voters,
    power,
    realized_gap,
    simulate_trial,
    solve_voters,
)


def test_cluster_sizes_are_the_measured_wave2_distribution():
    """Frozen from the study DB on 2026-09-06: 943 ballots over 46 sessions on 2026-08-27."""
    assert sum(WAVE2_CLUSTER_SIZES) == 943
    assert len(WAVE2_CLUSTER_SIZES) == 46
    assert max(WAVE2_CLUSTER_SIZES) == 151


def test_draw_voters_caps_each_voter_and_returns_the_requested_count():
    rng = np.random.default_rng(0)
    sizes = draw_voters(40, cap=20, rng=rng)
    assert len(sizes) == 40
    assert max(sizes) <= 20
    assert min(sizes) >= 1


def test_null_power_is_near_alpha():
    """The calibration control. With no criterion effect the test must reject at about 5%, or
    every power number the script produces is meaningless."""
    p = power(n_voters=40, p_bot=0.35, delta=0.0, n_sims=600, seed=1)
    assert 0.02 < p < 0.09


def test_large_effect_is_detected_almost_always():
    p = power(n_voters=60, p_bot=0.30, delta=0.35, n_sims=300, seed=2)
    assert p > 0.9


def test_power_increases_with_voters():
    lo = power(n_voters=20, p_bot=0.35, delta=0.15, n_sims=400, seed=3)
    hi = power(n_voters=80, p_bot=0.35, delta=0.15, n_sims=400, seed=3)
    assert hi > lo + 0.1


def test_more_voter_heterogeneity_costs_power():
    """The test that proves clustering is modelled: at a FIXED voter count, raising the voter
    random-intercept SD must reduce power. If ballots were treated as independent this would
    not move."""
    tight = power(n_voters=40, p_bot=0.35, delta=0.15, sd_voter=0.2, n_sims=500, seed=4)
    loose = power(n_voters=40, p_bot=0.35, delta=0.15, sd_voter=1.5, n_sims=500, seed=4)
    assert tight > loose + 0.05


def test_more_ballots_per_voter_helps_far_less_than_more_voters():
    """The design consequence: criterion is between-voter, so buying ballots from the same
    people is a weak lever compared with buying more people."""
    more_ballots = power(
        n_voters=30, ballots_per_voter=40, p_bot=0.35, delta=0.15, n_sims=400, seed=5
    )
    more_voters = power(
        n_voters=60, ballots_per_voter=20, p_bot=0.35, delta=0.15, n_sims=400, seed=5
    )
    assert more_voters > more_ballots


def test_realized_gap_reports_the_marginal_not_the_latent_difference():
    """Random intercepts attenuate a latent-scale effect, so the marginal win-rate gap the
    paper would report is smaller than the delta requested. Report what is realized."""
    g = realized_gap(p_bot=0.35, delta=0.15, sd_voter=1.0, sd_pair=0.5, seed=6)
    assert 0.0 < g < 0.15


def test_simulate_trial_returns_a_valid_p_value():
    rng = np.random.default_rng(7)
    p = simulate_trial(n_voters=30, p_bot=0.35, delta=0.15, rng=rng)
    assert 0.0 <= p <= 1.0


def test_solve_voters_finds_the_smallest_grid_point_reaching_target_power():
    n, p = solve_voters(
        p_bot=0.35, delta=0.20, target=0.8, grid=(20, 40, 60, 80, 120), n_sims=300, seed=8
    )
    assert n in (20, 40, 60, 80, 120)
    assert p >= 0.8 or n == 120


# IRON_LAW_OK


def test_cost_uses_the_measured_wave2_rate_per_participant():
    """Wave 2 paid $85.33 for 40 approved participants (project memory, 2026-08-28). Budget is
    set per participant, not per vote, so cost scales with voters."""
    from scripts.paper.aprime_power import WAVE2_APPROVED, WAVE2_COST_USD, cost_for

    assert round(WAVE2_COST_USD / WAVE2_APPROVED, 2) == 2.13
    assert cost_for(40) == pytest.approx(85.33, abs=0.01)
    assert cost_for(120) == pytest.approx(255.99, abs=0.05)


def test_grid_reaches_far_enough_for_the_smallest_effect():
    """A grid that tops out before 80% power reports a censored answer as if it were the
    answer. The default grid must be able to resolve the smallest effect the design cares
    about."""
    from scripts.paper.aprime_power import DEFAULT_GRID

    assert max(DEFAULT_GRID) >= 480


# IRON_LAW_OK


def test_power_curve_covers_the_grid_and_rises():
    """The figure needs the whole curve, not the three solved points; a curve that did not
    rise with voters would mean the grid or the seed was wired wrong."""
    from scripts.paper.aprime_power import power_curve

    pts = power_curve(p_bot=0.35, delta=0.20, grid=(20, 60, 160), n_sims=250, seed=9)
    assert [n for n, _ in pts] == [20, 60, 160]
    assert pts[0][1] < pts[-1][1]
