# scripts/paper/aprime_voter_sd.py
"""Estimate the voter random-intercept SD from real wave-2 ballots.

Why this exists: the A′ power answer swings from 80 voters ($171) to 240 voters ($512) purely
on the assumed voter heterogeneity `sd_voter`. That is too much of the budget to leave to a
guess when 943 real ballots from 46 real voters are sitting in the study DB.

Method (moment estimator on the logit scale). Fit Bradley-Terry to get a per-ballot predicted
probability p that the voter picks side A, from generator strengths alone. For voter i,

    V_i = Σ p(1-p)          (Fisher information about that voter's intercept)
    û_i = Σ (y - p) / V_i   (one-step estimate of the intercept)
    Var(û_i | u_i) ≈ 1/V_i  (sampling noise)

so Var(u) ≈ mean(û²) - mean(1/V), clipped at zero. Subtracting the sampling term is the whole
trick: without it a perfectly homogeneous panel returns a large spurious SD.

The BT fit is LEAVE-ONE-VOTER-OUT. Fitting on all ballots and then measuring residuals of the
same ballots lets each voter drag the strengths toward their own choices, which shrinks their
residuals and biases the SD DOWN — that is, toward the cheaper study. The bias runs in the
direction that flatters the budget, so it is not a bias worth tolerating.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

MIN_BALLOTS = 10


def estimate_sd_voter(
    voter: np.ndarray,
    p: np.ndarray,
    y: np.ndarray,
    *,
    min_ballots: int = MIN_BALLOTS,
    return_kept: bool = False,
):
    """Moment estimate of the voter random-intercept SD on the logit scale."""
    voter = np.asarray(voter)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)

    us, invs = [], []
    for v in np.unique(voter):
        m = voter == v
        if m.sum() < min_ballots:
            continue
        info = float((p[m] * (1.0 - p[m])).sum())
        if info <= 0:
            continue
        us.append(float((y[m] - p[m]).sum()) / info)
        invs.append(1.0 / info)
    if not us:
        raise ValueError(
            f"no voter has at least {min_ballots} ballots; the voter SD is not estimable "
            "from this data"
        )
    var = float(np.mean(np.square(us)) - np.mean(invs))
    sd = float(np.sqrt(max(var, 0.0)))
    return (sd, len(us)) if return_kept else sd


def _voter_info(voter, p, min_ballots):
    """Per-voter Fisher information about their own intercept, for voters above the cut."""
    out = []
    for v in np.unique(voter):
        m = voter == v
        if m.sum() < min_ballots:
            continue
        info = float((p[m] * (1.0 - p[m])).sum())
        if info > 0:
            out.append((m, info))
    return out


def upper_bound_sd(
    voter: np.ndarray,
    p: np.ndarray,
    y: np.ndarray,
    *,
    min_ballots: int = MIN_BALLOTS,
    reps: int = 400,
    seed: int = 0,
    alpha: float = 0.05,
    grid: tuple[float, ...] = tuple(round(0.1 * i, 1) for i in range(0, 31)),
) -> float:
    """One-sided upper confidence bound on the voter SD, by simulation.

    A point estimate that clips to zero says the voter SD is BELOW this panel's detection
    floor. It does not say the SD is zero, and a reader who spends money on the difference
    needs the bound, not the clip. This returns the largest true SD that would still produce
    an estimate as small as the observed one at least `alpha` of the time — everything above
    it is ruled out by the data at that level."""
    voter = np.asarray(voter)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    observed = estimate_sd_voter(voter, p, y, min_ballots=min_ballots)
    cells = _voter_info(voter, p, min_ballots)
    if not cells:
        raise ValueError(f"no voter has at least {min_ballots} ballots")
    eta = np.log(p / (1.0 - p))
    rng = np.random.default_rng(seed)

    bound = 0.0
    for true_sd in grid:
        hits = 0
        for _ in range(reps):
            u = rng.normal(0.0, true_sd, len(cells))
            us, invs = [], []
            for i, (m, info) in enumerate(cells):
                q = 1.0 / (1.0 + np.exp(-(eta[m] + u[i])))
                yy = (rng.random(int(m.sum())) < q).astype(float)
                us.append(float((yy - p[m]).sum()) / info)
                invs.append(1.0 / info)
            est = np.sqrt(max(np.mean(np.square(us)) - np.mean(invs), 0.0))
            hits += est <= observed
        if hits / reps >= alpha:
            bound = true_sd
    return bound


def simulate_ballots(
    n_voters: int, n_ballots: int, sd_voter: float, seed: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Synthetic ballots with a KNOWN voter SD, for recovery tests.

    p is the model-implied probability the voter picks side A; the voter's intercept shifts the
    realised outcome away from it, exactly as a real voter's taste would."""
    rng = np.random.default_rng(seed)
    u = rng.normal(0.0, sd_voter, n_voters)
    voter = np.repeat(np.arange(n_voters), n_ballots)
    eta = rng.normal(0.0, 0.7, voter.size)  # pair strength difference on the logit scale
    p = 1.0 / (1.0 + np.exp(-eta))
    y = (rng.random(voter.size) < 1.0 / (1.0 + np.exp(-(eta + u[voter])))).astype(float)
    return voter, p, y


def _load(day: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Real wave ballots with leave-one-voter-out BT predictions."""
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.ranking import _fit_strengths

    q = text(
        """
        SELECT v.session_id AS voter, oa.generator_id AS a, ob.generator_id AS b,
               CASE WHEN v.winner='a' THEN 1 ELSE 0 END AS chose_a
        FROM vote v
        JOIN comparison c ON c.id = v.comparison_id
        JOIN model_output oa ON oa.id = c.output_a_id
        JOIN model_output ob ON ob.id = c.output_b_id
        WHERE date(v.created) = :day AND c.is_gold = 0 AND v.winner IN ('a','b')
        """
    )
    with SessionLocal() as db:
        rows = [tuple(r) for r in db.execute(q, {"day": day}).all()]
    if not rows:
        raise ValueError(f"no decisive non-gold ballots on {day}; wrong database or wrong day")

    # Players are generator ids, the same unit the public leaderboard ranks.
    names = sorted({r[1] for r in rows} | {r[2] for r in rows})
    idx = {n: i for i, n in enumerate(names)}
    voters = sorted({r[0] for r in rows})

    voter_arr, p_arr, y_arr = [], [], []
    for held in voters:
        rest = [r for r in rows if r[0] != held]
        matches = [(idx[a], idx[b]) if ca else (idx[b], idx[a]) for _, a, b, ca in rest]
        s = _fit_strengths(list(range(len(names))), matches, reg=0.1)
        for vt, a, b, ca in (r for r in rows if r[0] == held):
            d = s[idx[a]] - s[idx[b]]
            voter_arr.append(voters.index(vt))
            p_arr.append(1.0 / (1.0 + np.exp(-d)))
            y_arr.append(float(ca))
    meta = {"ballots": len(rows), "voters": len(voters), "generators": len(names), "day": day}
    return np.array(voter_arr), np.array(p_arr), np.array(y_arr), meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate the A′ voter random-intercept SD.")
    ap.add_argument("--day", default="2026-08-27", help="wave day, YYYY-MM-DD")
    ap.add_argument("--min-ballots", type=int, default=MIN_BALLOTS)
    ap.add_argument("--reps", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    voter, p, y, meta = _load(args.day)
    sd, kept = estimate_sd_voter(voter, p, y, min_ballots=args.min_ballots, return_kept=True)
    ub = upper_bound_sd(voter, p, y, min_ballots=args.min_ballots, reps=args.reps, seed=args.seed)
    out = {
        **meta,
        "min_ballots": args.min_ballots,
        "voters_used": kept,
        "sd_voter_point": round(sd, 4),
        "sd_voter_upper_95": round(ub, 4),
        "note": (
            "A point estimate of 0 means below this panel's detection floor, not absent. Plan "
            "against the upper bound. Wave 2 judged `overall` on ordinary pairs; A' asks a "
            "less familiar question (botanical plausibility on mixed pairs), where voter "
            "heterogeneity may be larger, so this bound transfers only as a lower anchor."
        ),
    }
    print(json.dumps(out, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
