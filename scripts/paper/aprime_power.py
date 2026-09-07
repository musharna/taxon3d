# scripts/paper/aprime_power.py
"""Power simulation for Study A′, the mixed-pair criterion-crossover wave.

The design assigns each voter ONE criterion for a whole session, so the estimand — the
difference in P(rejected side chosen) between the visual and botanical arms — is a
BETWEEN-voter contrast. Ballots from the same voter are not independent replicates of it.
This simulation therefore parameterises the design in VOTERS, draws each voter's ballot count
from the measured wave-2 distribution, and tests the contrast on voter-level means.

Why a voter-level Welch test rather than the spec's mixed-effects logistic model: fitting a
GLMM tens of thousands of times is not affordable here, and with criterion assigned per voter
the GLMM's criterion contrast is itself essentially a between-voter comparison. The voter-level
test is the conservative approximation of it — it discards within-voter information that the
GLMM would use for the random-intercept variance but not for the contrast. `--check-glmm`
prints one grid point in a form `lme4` can verify. Power reported here is a floor, not a
ceiling.

Usage:
  .venv/bin/python scripts/paper/aprime_power.py --out data/paper/aprime/power.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Ballots per session on 2026-08-27 (wave 2), measured read-only against
# data/study/arena-study.db on 2026-09-06: 943 ballots over 46 sessions. The distribution is
# what matters, not the mean — three sessions cast more than 60 ballots and one cast 151, so a
# design that assumed 20 ballots from everyone would badly misstate the effective sample size.
WAVE2_CLUSTER_SIZES: tuple[int, ...] = (
    (1,) * 3
    + (10,) * 7
    + (11,) * 12
    + (12,) * 2
    + (13,) * 2
    + (14,) * 1
    + (15,) * 4
    + (16,) * 3
    + (17,) * 2
    + (18,) * 1
    + (20,) * 1
    + (29,) * 1
    + (31,) * 3
    + (60,) * 2
    + (101,) * 1
    + (151,) * 1
)
MEAN_CLUSTER = sum(WAVE2_CLUSTER_SIZES) / len(WAVE2_CLUSTER_SIZES)

# Defaults for the nuisance parameters. sd_voter is the voter random-intercept SD on the logit
# scale; 0.8 is a moderate amount of voter heterogeneity and is swept in the report.
# 0.8 is the CONSERVATIVE planning value, not a measurement. scripts/paper/aprime_voter_sd.py
# measures the voter random-intercept SD on wave-2's 666 decisive ballots and returns a point
# estimate of 0.0 with a 95% upper bound of 0.5 — the expensive end is ruled out for the
# arena's existing `overall` task. A' asks a less familiar question (botanical plausibility on
# mixed pairs), where voters may spread out more, so the budget is planned at 0.8 and the
# sensitivity sweep reports what 0.5 would buy.
SD_VOTER = 0.8
SD_PAIR = 0.5
MIXED_FRAC = 0.6  # design target: 60% mixed pairs, 40% admitted-admitted filler
N_PAIRS = 200
ALPHA = 0.05
CAP_FRAC = 0.05  # per-voter cap: no voter may cast more than 5% of total ballots

# Wave 2 (2026-08-28): $85.33 paid for 40 approved participants. Prolific is paid per
# participant-session, not per vote, so the budget scales with VOTERS — the same quantity the
# criterion contrast is powered on. Buying more ballots from the same people costs money and
# buys almost no power.
WAVE2_COST_USD = 85.33
WAVE2_APPROVED = 40
DEFAULT_GRID: tuple[int, ...] = (20, 30, 40, 60, 80, 120, 160, 240, 320, 480, 640)


def cost_for(n_voters: int) -> float:
    """Prolific cost at the wave-2 rate per approved participant."""
    return n_voters * WAVE2_COST_USD / WAVE2_APPROVED


def _logit(p: float) -> float:
    return float(np.log(p / (1.0 - p)))


def draw_voters(n_voters: int, cap: int, rng: np.random.Generator) -> np.ndarray:
    """Ballot counts for n_voters, resampled from the wave-2 distribution and capped."""
    sizes = rng.choice(np.asarray(WAVE2_CLUSTER_SIZES), size=n_voters, replace=True)
    return np.minimum(sizes, cap).astype(int)


def simulate_trial(
    n_voters: int,
    p_bot: float,
    delta: float,
    *,
    rng: np.random.Generator,
    sd_voter: float = SD_VOTER,
    sd_pair: float = SD_PAIR,
    mixed_frac: float = MIXED_FRAC,
    n_pairs: int = N_PAIRS,
    ballots_per_voter: int | None = None,
) -> float:
    """One simulated wave. Returns the one-sided p-value for visual > botanical."""
    if ballots_per_voter is None:
        cap = max(1, int(round(CAP_FRAC * n_voters * MEAN_CLUSTER)))
        sizes = draw_voters(n_voters, cap, rng)
    else:
        sizes = np.full(n_voters, int(ballots_per_voter), dtype=int)

    # Half the voters get each criterion. Criterion is a voter-level property by design.
    visual = np.zeros(n_voters, dtype=bool)
    visual[: n_voters // 2] = True
    rng.shuffle(visual)

    b0 = _logit(p_bot)
    b1 = _logit(min(p_bot + delta, 0.999)) - b0 if delta != 0.0 else 0.0
    u = rng.normal(0.0, sd_voter, size=n_voters)
    v = rng.normal(0.0, sd_pair, size=n_pairs)

    # Only mixed pairs carry the contrast; filler ballots are simulated and discarded, which is
    # what the primary analysis does with them.
    n_mixed = rng.binomial(sizes, mixed_frac)
    means = np.full(n_voters, np.nan)
    for i in range(n_voters):
        k = int(n_mixed[i])
        if k == 0:
            continue
        pair = rng.integers(0, n_pairs, size=k)
        eta = b0 + (b1 if visual[i] else 0.0) + u[i] + v[pair]
        y = rng.random(k) < 1.0 / (1.0 + np.exp(-eta))
        means[i] = y.mean()

    ok = ~np.isnan(means)
    a, b = means[ok & visual], means[ok & ~visual]
    if len(a) < 2 or len(b) < 2:
        return 1.0
    t, p_two = stats.ttest_ind(a, b, equal_var=False)
    # One-sided in the direction H1 predicts: the visual arm picks the rejected side more.
    return float(p_two / 2.0 if t > 0 else 1.0 - p_two / 2.0)


def power(
    n_voters: int,
    p_bot: float,
    delta: float,
    *,
    n_sims: int = 1000,
    seed: int = 0,
    alpha: float = ALPHA,
    **kw,
) -> float:
    rng = np.random.default_rng(seed)
    hits = sum(simulate_trial(n_voters, p_bot, delta, rng=rng, **kw) < alpha for _ in range(n_sims))
    return hits / n_sims


def realized_gap(
    p_bot: float,
    delta: float,
    *,
    sd_voter: float = SD_VOTER,
    sd_pair: float = SD_PAIR,
    seed: int = 0,
    n: int = 200_000,
) -> float:
    """The MARGINAL win-rate gap a latent-scale delta actually produces.

    Random intercepts attenuate a logit-scale effect toward zero, so asking for delta=0.15 on
    the conditional scale does not deliver a 15-point difference in observed win rates. The
    paper reports the marginal gap, so the simulation must say what it is."""
    rng = np.random.default_rng(seed)
    b0 = _logit(p_bot)
    b1 = _logit(min(p_bot + delta, 0.999)) - b0 if delta != 0.0 else 0.0
    e = rng.normal(0.0, sd_voter, n) + rng.normal(0.0, sd_pair, n)
    hi = 1.0 / (1.0 + np.exp(-(b0 + b1 + e)))
    lo = 1.0 / (1.0 + np.exp(-(b0 + e)))
    return float(hi.mean() - lo.mean())


def power_curve(
    p_bot: float,
    delta: float,
    *,
    grid: tuple[int, ...] = DEFAULT_GRID,
    n_sims: int = 600,
    seed: int = 0,
    **kw,
) -> list[tuple[int, float]]:
    """Power at every grid point, for the figure."""
    return [(n, power(n, p_bot, delta, n_sims=n_sims, seed=seed, **kw)) for n in grid]


def solve_voters(
    p_bot: float,
    delta: float,
    *,
    target: float = 0.8,
    grid: tuple[int, ...] = DEFAULT_GRID,
    n_sims: int = 1000,
    seed: int = 0,
    **kw,
) -> tuple[int, float]:
    """Smallest voter count on the grid reaching `target` power; the last point if none does."""
    last = (grid[-1], 0.0)
    for n in grid:
        p = power(n, p_bot, delta, n_sims=n_sims, seed=seed, **kw)
        last = (n, p)
        if p >= target:
            return n, p
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description="Study A′ power simulation (voter-level contrast).")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260906)
    args = ap.parse_args()

    deltas = [0.10, 0.15, 0.20]
    baselines = [0.25, 0.35, 0.45]
    rows = []
    for p_bot in baselines:
        for d in deltas:
            n, p = solve_voters(p_bot, d, n_sims=args.sims, seed=args.seed)
            rows.append(
                {
                    "p_botanical": p_bot,
                    "delta_conditional": d,
                    "gap_marginal": round(realized_gap(p_bot, d, seed=args.seed), 4),
                    "voters_for_80pct": n,
                    "power_at_that_n": round(p, 3),
                    "ballots_at_that_n": int(round(n * MEAN_CLUSTER)),
                    "cost_usd": round(cost_for(n), 2),
                    "censored": p < 0.8,
                }
            )
    sens = []
    for sd in (0.4, 0.5, 0.8, 1.2):
        n, p = solve_voters(0.35, 0.15, n_sims=args.sims, seed=args.seed, sd_voter=sd)
        sens.append(
            {
                "sd_voter": sd,
                "voters_for_80pct": n,
                "power_at_that_n": round(p, 3),
                "cost_usd": round(cost_for(n), 2),
                "censored": p < 0.8,
            }
        )

    curve = [
        {
            "delta_conditional": d,
            "gap_marginal": round(realized_gap(0.35, d, seed=args.seed), 4),
            "points": [
                {"voters": n, "power": round(pw, 3), "cost_usd": round(cost_for(n), 2)}
                for n, pw in power_curve(0.35, d, n_sims=max(400, args.sims // 3), seed=args.seed)
            ],
        }
        for d in deltas
    ]

    out = {
        "design": {
            "sd_voter": SD_VOTER,
            "sd_pair": SD_PAIR,
            "mixed_fraction": MIXED_FRAC,
            "n_pairs": N_PAIRS,
            "alpha": ALPHA,
            "one_sided": True,
            "cap_frac": CAP_FRAC,
            "mean_ballots_per_voter": round(MEAN_CLUSTER, 2),
            "test": "Welch t on voter-level mixed-pair means (conservative vs the GLMM)",
        },
        "sims": args.sims,
        "seed": args.seed,
        "rows": rows,
        "sd_voter_sensitivity": sens,
        "curve": curve,
        "wave2_reference": {
            "ballots": 943,
            "sessions": 46,
            "approved": WAVE2_APPROVED,
            "cost_usd": WAVE2_COST_USD,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
