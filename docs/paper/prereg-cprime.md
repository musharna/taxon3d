# Preregistration — Study C′: held-out human audit of the Taxon3D admissibility gate

Registered 2026-09-06. The analysis code is frozen at commit `7e95a08`, the last
commit touching `scripts/paper/`. Labels do not exist yet.

## Question

Do the gate's exclusions correspond to what independent humans call inadmissible (positive
predictive value), and do its admissions leak inadmissible outputs (false-negative rate)?

## Gate under audit

Structural predicate `structural-v1` (`app/structural.py`), completeness gate on categories
`isolated-organ, fragment`, semantic predicate `semantic-v2` (`app/semantic.py`, reject codes
`multiple, sub_part, not_the_organism`; `ok`/`uncertain` admit). Frozen: no verdict row changes
during the study.

## Population and sample (seed 20260905, `scripts/paper/cprime_export.py`)

Population measured 2026-09-06 against `data/study/arena-study.db` (read-only) by
`build_populations()`: non-gold outputs with `hidden_at IS NULL`, rejection taken from the
app's own gate composer over the full three-predicate rubric. 33 outputs rejected by the
completeness predicate alone are outside this audit's scope; 0 are unevaluated.

| stratum | population | sampled | inclusion probability |
| ------- | ---------- | ------- | --------------------- |
| `struct_degenerate_bbox` | 1 | 1 | 1.0000 |
| `struct_empty` | 43 | 15 | 0.3488 |
| `novel_multiple` | 46 | 46 | 1.0000 |
| `novel_not_the_organism` | 31 | 31 | 1.0000 |
| `novel_sub_part` | 2 | 2 | 1.0000 |
| `sem_also_completeness` | 91 | 30 | 0.3297 |
| `sem_only_other` | 18 | 13 | 0.7222 |
| `admitted` | 552 | 128 | 0.2319 |
| **total** | **784** | **266** | |

Two strata are sampled exhaustively (inclusion probability 1) because the visible corpus
cannot fill them: release #161 hid 3 of the 4 `degenerate_bbox` outputs and 15 of the 17
`sub_part`-on-`complete` outputs. The design spec's original targets were computed before
that filter was applied. Their 18 freed slots were reallocated BEFORE any label existed, to
the two remaining novel cells (to their full population), +3 to `sem_only_other` and +8 to
`admitted`, holding the total at 266. `novel_sub_part` therefore rests on n=2 and its
interval will be uninformative on its own; the pooled novel-stratum PPV is the criterion.
The `admitted` row's 0.2319 is the overall rate: that stratum is allocated across tasks by
largest remainder, so each item's recorded inclusion probability is its own task cell's
sampled/population, and the Horvitz-Thompson weights use those per-cell values.

Calibration set: 20 items, excluded. Sensitivity arm: 40 main items re-labelled from the mesh.

## Raters

Three people who did not write the trait rubrics or the 32-item semantic flag set. Raters 1
and 2 label all main items blind to every gate verdict; rater 3 labels only disagreements,
blind to both the gate and the other two labels. One 3D-literate rater labels the struct\_\*
items from the mesh.

## Analysis (fixed in `scripts/paper/cprime_ingest.py`)

- Final label: agreement of raters 1 and 2, else rater 3.
- `indeterminate` is excluded from PPV and rates and reported as a count.
- PPV per stratum with Wilson 95% CI; pooled PPV over the three novel strata.
- Admitted false-negative rate = share of admitted-cell items labelled inadmissible, Wilson CI.
- Population-weighted sensitivity and specificity by Horvitz–Thompson with weights 1/inclusion probability.
- Agreement: raw, per class, Krippendorff's α (nominal) for raters 1–2 and all three.
- Sensitivity arm: raw agreement between sheet-based and mesh-based labels for the same rater.

## Go/no-go for Study A′

GO iff pooled novel-stratum PPV ≥ 0.80 AND admitted false-negative rate < 0.10. Otherwise the
next step is a gate fix followed by a fresh C′ on new items; A′ does not run.

## What will not happen

No gate tuning on these labels. No re-sampling after seeing results. No change to the public
leaderboard.

## Exact commands

Export the audit set (READ-ONLY against the study DB; the DB's mtime is checked before and after
and must not change):

```bash
mkdir -p data/paper
stat -c %Y data/study/arena-study.db > /tmp/cprime_mtime_before
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db?mode=ro&uri=true" \
BIO3D_DATA_DIR="$(pwd)/data" \
timeout 300 .venv/bin/python scripts/paper/cprime_export.py \
  --out data/paper/cprime --seed 20260905 | tee data/paper/cprime/export_counts.json
[ "$(stat -c %Y data/study/arena-study.db)" = "$(cat /tmp/cprime_mtime_before)" ] \
  && echo "study DB untouched" || echo "ERROR: study DB was modified"
```

The export refuses to run if any sampled output has no cached contact sheet, because skipping one
would invalidate the inclusion probability recorded for its stratum. As of 2026-09-06, 9 of the
286 sampled outputs need a sheet rendered first (23 across the whole frame of 784):

```bash
for id in 136 138 143 169 171 172 186 187 205; do
  timeout 300 .venv/bin/python scripts/render_one_sheet.py "$id" turntable
done
```

After labels are returned, analysis and figure:

```bash
timeout 300 .venv/bin/python scripts/paper/cprime_ingest.py \
  --dir data/paper/cprime \
  --r1 data/paper/cprime/labels_r1.csv \
  --r2 data/paper/cprime/labels_r2.csv \
  --r3 data/paper/cprime/labels_r3.csv \
  --structural data/paper/cprime/labels_struct.csv
timeout 200 Rscript scripts/paper/fig_cprime_ppv.R \
  data/paper/cprime/results.json data/paper/cprime/fig_cprime_ppv.png
```

Nothing in this study writes to the database, and no script here imports a write guard because
none writes.

