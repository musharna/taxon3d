# Design: the admissibility paper — evidence plan

**Date:** 2026-09-05. **Status:** draft for review. Supersedes the "C then A" plan (two-arm
gated/ungated wave) that a five-judge panel rejected the same day; the panel record and my
verification of its claims are in project memory (`paper_plan_panel_audit_2026-09-05.md`).

## 1. Claim

Sentence 1 (primary, identifiable): _When a biologically inadmissible output is shown beside an
admissible one from the same task, voters judging visual quality pick the inadmissible one more
often than voters judging botanical plausibility do._ In symbols, for the same population of
mixed pairs,

    H0: P(rejected wins | visual) = P(rejected wins | botanical)
    H1: P(rejected wins | visual) > P(rejected wins | botanical)

A secondary, stronger reading adds `P(rejected wins | visual) > 0.5`.

Sentence 2 (derived, descriptive): a leaderboard that admits only admissible outputs differs
from one that does not, by more than removing the same number of outputs at random would.

"Inadmissible" means what the live gate means: not a single, whole specimen of the target
organism (empty or flat mesh; a detached organ; several organisms or a cluttered scene; junk that
is not the organism). The gate does **not** judge species identity or morphological fidelity
(`app/semantic.py:30` drops `wrong_species`; uncertain admits). The paper must not say
"biologically wrong" in a species sense on this evidence.

## 2. What exists (study DB, 2026-09-05)

| fact                                                                                | value                                                                               |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| non-gold outputs                                                                    | 940                                                                                 |
| excluded by the live gate (structural ∪ completeness ∪ semantic)                    | 333                                                                                 |
| of which semantic-only on outputs completeness calls `complete` — the novel stratum | 101 (multiple 49, not_the_organism 35, sub_part 17)                                 |
| admitted, votable, non-hidden                                                       | 552                                                                                 |
| votes with exactly one rejected side                                                | 56, all June–July internal, 53 from one session; decisive 24 rejected / 18 admitted |
| paid votes                                                                          | 1,276 (pilot 335, wave 2 941), all criterion `overall`                              |
| semantic verdicts with a cached contact sheet                                       | 486                                                                                 |
| human admissibility labels independent of gate tuning                               | 0                                                                                   |

These counts are over ALL non-gold outputs, including the 123 that `hidden_at` marks as
withdrawn. Study C′ audits the visible corpus, so its frame is smaller — 232 rejected and 552
admitted, with the novel stratum at 79 rather than 101. See the sample table in §3.

Consequences: no gate-off board exists; botanical plausibility has no paid votes; the only
human check on the semantic gate is the 32-flag set it was tuned on. The 56 historical mixed
votes are a motivating anecdote at most and will not appear as a figure.

## 3. Study C′ — held-out human audit of the gate

**Purpose.** Establish that the gate's exclusions are real (positive predictive value) and that
its admissions are not leaking inadmissible outputs (false-negative rate), on labels the gate
was never tuned on.

**Construct given to raters** (verbatim from the v2 prompt, `app/semantic.py:84-97`, with the
taxon name): one whole specimen of the named organism, crude or inaccurate is fine. Codes: `ok`,
`multiple`, `sub_part`, `not_the_organism`, `indeterminate`. Structural failures (empty, flat)
are judged from the mesh, not the sheet, by one 3D-literate rater.

**Evidence shown.** The same turntable contact sheet the VLM saw. The interactive mesh is a
sensitivity arm on a 40-item subsample, not the main instrument.

**Raters.** Three independent raters who did not author the trait rubrics or the 32-flag set.
Two label everything; the third adjudicates disagreements blind to the gate's verdict. A
20-item calibration set is labelled and discussed first and excluded from analysis.

**Sample** (stratified, every stratum with its inclusion probability recorded):

Superseded 2026-09-06 by the measured frame. The counts below were taken before the
`hidden_at` filter was applied, so three of them describe outputs release #161 has already
withdrawn from the corpus; the audit's frame is the VISIBLE corpus. The live figures, measured
by `build_populations()` and reproduced in `docs/paper/prereg-cprime.md`, are:

| stratum                                               | population | sample | inclusion prob. |
| ----------------------------------------------------- | ---------- | ------ | --------------- |
| structural `degenerate_bbox`                          | 1          | 1      | 1.000           |
| structural `empty`                                    | 43         | 15     | 0.349           |
| semantic-only on `complete` — `multiple`              | 46         | 46     | 1.000           |
| semantic-only on `complete` — `not_the_organism`      | 31         | 31     | 1.000           |
| semantic-only on `complete` — `sub_part`              | 2          | 2      | 1.000           |
| semantic rejects the completeness gate also catches   | 91         | 30     | 0.330           |
| semantic rejects NOT on `complete` (`sem_only_other`) | 18         | 13     | 0.722           |
| admitted (false-negative cell), stratified by task    | 552        | 128    | 0.232           |

266 items, three labels each on the main set, plus a 20-item calibration set.

Two changes from the original table, both made before any label existed. First, a
`sem_only_other` stratum was added: 18 rejected outputs sit on `partial-organism` or carry no
completeness row at all, and an estimate cannot omit a cell of its own population. Second,
`degenerate_bbox` (4 → 1) and `sub_part`-on-`complete` (17 → 2) are far smaller once hidden
outputs are excluded, so both are now sampled exhaustively and their 18 freed slots were
reallocated to strata with headroom, holding the total at 266. `novel_sub_part` therefore rests
on n=2; the pooled novel-stratum PPV, not that cell alone, is the go/no-go criterion.

**Report.** Per stratum: PPV of the rejection, sensitivity and specificity re-weighted to the
population, raw and class-specific agreement, and Krippendorff's α. Kappa only alongside raw
agreement. No gate tuning happens on these labels; if the gate changes, C′ is re-run on fresh
items.

**Go/no-go.** Proceed to A′ only if PPV on the novel stratum is at least 0.8 and the admitted
false-negative rate is below 0.1. Below either, the paper's next step is fixing the gate, not a
wave.

## 4. Study A′ — mixed-pair wave

**Unit.** A pair from the same task and paradigm with exactly one gate-admitted and one
gate-rejected output, the rejected side drawn from C′'s validated strata (structural failures
excluded from the primary analysis; they are a floor effect, reported separately).

**Pair schedule.** Fixed in advance and written to a table; the live matchmaker is not used
because it prefers least-compared outputs and would flood ballots with never-voted rejects.
Design target: about 60% mixed pairs, 40% admitted–admitted filler so the session looks like
the arena and yields a gated-board byproduct. Side and order randomized. Reject codes and tasks
balanced.

**Criteria.** Two: `visual_quality` and `botanical_plausibility`. Each voter is assigned one
criterion for the whole session; every pair is judged under both criteria by different voters.
The botanical instruction gets a one-screen definition matching the gate's construct.

**Participants.** Prolific, paid by expected session time at the platform's recommended rate,
not by vote count. Per-voter cap of 5% of total ballots. Gold pairs and trust gating as in
wave 2.

**Primary analysis.** Mixed-effects logistic model: outcome = rejected side chosen; fixed
effects criterion, reject code, and their interaction; random intercepts for pair and voter.
The estimate of interest is the criterion contrast. Report with voter-clustered bootstrap CIs.

**Power.** Simulated before recruitment from the fitted Bradley–Terry model and wave-2
cluster sizes, under effect sizes 0.10, 0.15, 0.20 on the win-rate difference. The panel's
analytic sketch puts 60:40 versus 50:50 at roughly 150–200 independent judgments per criterion
before clustering; the simulation replaces that number. Budget is whatever the simulation
says buys 80% power on the primary contrast, expected in the $150–300 range.

**Secondary.** Win rate of rejected outputs by code and criterion; the gated board from the
filler pairs.

## 5. Sentence 2 — the board comparison, correctly framed

Three estimands are reported for every generator: conditional quality (admitted outputs
only), ungated quality (from A′'s full pool, descriptive), and end-to-end utility, in which a
rejected output counts as a loss. Admission rate is plotted beside conditional preference as a
Pareto frontier. Any rank-displacement statistic is reported per paradigm, never across
paradigms, and only against two nulls: a same-pool refit null and placebo gates that remove the
same number of outputs stratified by task and separately by generator.

## 6. Novelty package

1. Criterion crossover on mixed pairs (A′) — the human dissociation no prior 3D benchmark
   measures.
2. Incremental table: exclusions from semantic-only versus structural ∪ completeness, so the
   reader sees what the VLM predicate adds beyond a sanity filter.
3. Protocol delta versus 3DGen-Bench's Plant category, quoted from their paper, not asserted.
   (`docs/paper/related-work.md` corrected 2026-09-05.)
4. A held-out human-labelled admissibility set (C′), released with the rubric, prompt, model
   version, and verdict logs.
5. Optional, strong: the frozen gate applied to a probability sample of 3DGen-Bench's Plant
   assets with an expert audit. Depends on asset licensing; decide after C′.

## 7. Engineering needed

- **Contact-sheet export** for C′: write the 486 cached sheets plus renders for the sampled
  admitted items to a directory with anonymized ids; a CSV manifest with stratum and inclusion
  probability; a label sheet per rater. No new UI; raters label in a spreadsheet.
- **Study pool for A′:** a cohort-scoped pair schedule. The arena must serve a fixed pair list
  to voters in a study cohort, including outputs the public gate hides, without changing what the
  public sees. The per-route `hidden_at` invariant stays; the study route reads the schedule
  table, not the pool.
- **Analysis scripts:** `scripts/paper/` for the C′ weighted estimates, the A′ mixed model, the
  power simulation, and the placebo-gate nulls. R + ggplot2 for figures, one house theme file.
- **Preregistration:** the claim, H0, sample, analysis, and go/no-go above, committed before any
  label or vote is collected. July results are labelled exploratory.

## 8. Out of scope

A wave-3 for vote volume; SEO or UI work; any change to the public leaderboard's default
estimand; generator roster refresh; the trait-level "third of judgments cannot be made"
headline as the paper's frame (it becomes a descriptive section).

## 9. Open questions

- Rater sourcing for C′: three Prolific raters screened on a botany quiz, or two known
  colleagues plus one screened rater. Recommendation: the latter, for adjudication quality.
- Whether `overall` should be a third criterion in A′ so the 1,276 existing paid votes become
  a comparable gated baseline. Recommendation: no; it dilutes power on the primary contrast.

**Status 2026-09-06.** C′ is built and preregistered: sampling frame, exporter, statistics,
ingest, figure, rater instructions and `docs/paper/prereg-cprime.md` are committed and tested.
Nothing further can run until the first open question above is answered — raters are the only
remaining input. The mechanical step before hand-off is rendering 9 missing contact sheets
(ids listed in the prereg), after which the export command produces the blind sheets.
