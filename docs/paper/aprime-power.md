# Study A′ — power, measured before recruitment

Date 2026-09-06. Produced by `scripts/paper/aprime_power.py` (1500 simulations per grid point,
seed 20260906) and `scripts/paper/aprime_voter_sd.py`. Results: `data/paper/aprime/power.json`,
`data/paper/aprime/voter_sd.json`, figure `data/paper/aprime/fig_aprime_power.png`.

## The finding that changes the design

**A′ is powered in voters, not ballots.** The design assigns each voter one criterion for a
whole session, so the visual-versus-botanical contrast is a *between-voter* comparison. Extra
ballots from an already-recruited voter add almost nothing to it.

The design spec's §4 sketch — "roughly 150–200 independent judgments per criterion before
clustering" — is in the wrong unit. At the planning assumptions, 80% power needs **120 voters,
about 2,460 ballots**: an order of magnitude more ballots than that sketch implies, because the
sketch counts ballots as if each were an independent judgment of the criterion effect.

Analysing at the ballot level is not merely optimistic, it is invalid. Under the null, a Welch
test on ballots rejects at **14.8%** against a nominal 5% (simulated on the same generative
model, 600 replicates). The primary analysis must cluster on voter; this simulation tests the
voter-level contrast, which is the conservative approximation of the spec's mixed-effects model.

## Voters needed for 80% power

Conditional effect `delta` is the logit-scale shift; `gap` is the marginal win-rate difference
it actually produces once random intercepts attenuate it — the quantity the paper would report.
Cost is Prolific at wave 2's measured rate ($85.33 for 40 approved participants = $2.13/head).

| marginal gap | voters | ballots | cost |
| ------------ | ------ | ------- | ---- |
| 17 points    | 60     | ~1,230  | $128 |
| 13 points    | 120    | ~2,460  | $256 |
| 9 points     | 240    | ~4,920  | $512 |

The baseline rate P(rejected wins | botanical) barely matters: 0.25, 0.35 and 0.45 all give the
same answer at every effect size. That is a useful robustness result — the design does not need
the baseline pinned before recruiting.

## The assumption that does matter, and its measurement

Voter heterogeneity (`sd_voter`, the voter random-intercept SD) swings the answer three-fold:

| sd_voter | voters | cost |
| -------- | ------ | ---- |
| 0.4      | 80     | $171 |
| 0.5      | 80     | $171 |
| 0.8      | 120    | $256 |
| 1.2      | 240    | $512 |

Rather than assume it, `aprime_voter_sd.py` estimates it from wave 2's 666 decisive non-gold
ballots (46 voters, 18 generators), using leave-one-voter-out Bradley–Terry predictions and a
moment estimator that subtracts the sampling term. Leave-one-out matters: fitting on all
ballots lets each voter drag the strengths toward their own choices, biasing the SD *down* —
that is, toward the cheaper study.

**Point estimate 0.0, 95% upper bound 0.5.** The zero is a floor, not an absence: only 21
voters cast 10+ ballots, and with that panel the observed spread (mean û² = 0.299) is smaller
than the sampling term alone (mean 1/V = 0.340). What the null does establish is that the
*expensive* end is ruled out — a true SD of 0.8 would essentially never have produced a zero
here (5th percentile 0.38), and 1.2 never.

The transfer caveat: wave 2 judged `overall` on ordinary pairs. A′ asks a less familiar
question — botanical plausibility on mixed pairs — where voters may spread out more. So 0.5 is
a lower anchor, not the value to plan on.

## Recommendation

**Budget 120 voters, about $256.** That is powered at 80% for a 13-point marginal gap under
the conservative sd = 0.8, and comfortably above 80% if voters behave as wave 2 suggests. It
sits inside the spec's $150–300 range, which turns out to have been the right budget for the
wrong reason. A 9-point gap would need $512 and is not worth pre-committing to before C′ says
whether the gate's rejections are real.

Two design consequences to carry into the pair schedule:

- **Recruit wide, not deep.** The 5% per-voter ballot cap already in the design is doing real
  work; wave 2 violated it badly (one session cast 151 of 943 ballots, another 101).
- **Balance criterion assignment across voters, not ballots.** Half the voters per arm is what
  the contrast is estimated from.

## What this does not settle

Power is computed against a *simulated* effect, not a measured one. The 56 historical mixed
votes (24 rejected / 18 admitted decisive) are one session's exploratory data and are not used
here as an effect-size estimate. Whether any of the three gaps is the real one is what A′ is
for; the go/no-go on running it at all still rests on C′.
