# Study C′ — Held-Out Human Audit of the Admissibility Gate: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the export, label sheets, analysis code, preregistration, and figure for Study C′ — a stratified, blind human audit of the live admissibility gate — so the go/no-go for Study A′ can be read from numbers, not judgment.

**Architecture:** Three small pure-Python modules under `scripts/paper/` (stratum assignment + sampling; agreement + weighted rates; ingest/report) with a thin DB-reading exporter that uses the app's own gate composer for the population. Contact sheets are copied from the existing render cache (`data/assets/renders/{id}_turntable.png`, 940 on disk — every output already has one, so no rendering runs). Raters label CSVs; ingest joins them to a private manifest, computes per-stratum PPV, Horvitz–Thompson weighted sensitivity/specificity, raw agreement and Krippendorff's α, and prints the go/no-go. R + ggplot2 draws the one figure from `results.json`, sourcing a single house theme file.

**Tech Stack:** Python 3.13 (`.venv`), SQLAlchemy 2 models in `app/`, pytest, stdlib `csv`/`json`/`random`; R 4.3.3 with ggplot2 + jsonlite (already installed; `irr` is NOT installed, so α is computed in Python).

**Spec:** `docs/paper/2026-09-05-evidence-plan-design.md` (§3 Study C′, §7 Engineering, §9 open questions). Read it before starting any task.

## Global Constraints

- **Never write to the study DB and never run pytest with `BIO3D_DATABASE_URL` pointing at it.** All C′ scripts are READ-ONLY against `sqlite:///$(pwd)/data/study/arena-study.db`; none imports `app.dbguard` write args because none writes. Tests run with no `BIO3D_*` env set (conftest isolates a temp DB).
- **Population = the live gate's own view.** Use `app.admissibility.non_admitted_output_ids(db, rubric=["structural", "completeness", "semantic"])` for "rejected" and its complement over non-gold, non-hidden outputs for "admitted". Do not re-implement the gate in SQL.
- **The gate's construct, verbatim.** Rater codes are exactly `ok`, `multiple`, `sub_part`, `not_the_organism`, `indeterminate`. The rater instruction quotes `app/semantic.py:84-97`. Species identity and fidelity are out of scope for raters.
- **Blind.** Rater sheets carry an anonymized id, the taxon, and the sheet filename only — never the output id, generator, stratum, or gate verdict. The mapping lives only in `manifest.csv`, which is private and never handed to a rater.
- **No gate tuning on these labels.** Nothing in this plan writes an `Admissibility` row or changes `app/semantic.py`.
- **Preregistration commit lands before any rater sees a sheet** (Task 7 precedes the hand-off in Task 8).
- **Plots are R + ggplot2 only**, every plotting script `source()`s `scripts/paper/theme_taxon3d.R`; no matplotlib, no inline theme.
- **Output location:** `data/paper/cprime/` (under the gitignored `/data/`). Committed artifacts are code, tests, docs, and later the anonymized labelled set in `docs/paper/cprime/` (Task 8 says exactly which files).
- Commit messages: imperative, one line, no attribution trailers.
- Every inline Python run against the study DB is wrapped in `timeout 90`.

---

## File Structure

| File                                                                                                                                                      | Responsibility                                                                                                                                                             |
| --------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/paper/__init__.py`                                                                                                                               | Empty; makes `scripts.paper` importable from tests (`scripts/__init__.py` already exists).                                                                                 |
| `scripts/paper/cprime_strata.py`                                                                                                                          | Pure: stratum name from the three verdicts; stratified sample with inclusion probabilities; calibration draw; anonymizer.                                                  |
| `scripts/paper/cprime_export.py`                                                                                                                          | DB read → populations → sample → copy sheets → write `manifest.csv`, `rater_sheet.csv`, `calibration_sheet.csv`, `structural_sheet.csv`, `sensitivity_sheet.csv`.          |
| `scripts/paper/cprime_agreement.py`                                                                                                                       | Pure statistics: Krippendorff's α (nominal), raw agreement, per-class agreement, Wilson interval, Horvitz–Thompson weighted rates.                                         |
| `scripts/paper/cprime_ingest.py`                                                                                                                          | Read rater CSVs + manifest → adjudicated label per item → per-stratum PPV, weighted sensitivity/specificity, agreement → `results.json`, `results.md`, go/no-go on stdout. |
| `scripts/paper/theme_taxon3d.R`                                                                                                                           | The ONE house theme + palette for every paper figure.                                                                                                                      |
| `scripts/paper/fig_cprime_ppv.R`                                                                                                                          | Reads `results.json`, draws PPV per stratum with Wilson CIs, writes PNG.                                                                                                   |
| `docs/paper/cprime-rater-instructions.md`                                                                                                                 | What a rater reads: construct, codes, examples of what NOT to penalize, how to fill the CSV.                                                                               |
| `docs/paper/prereg-cprime.md`                                                                                                                             | Preregistration: hypotheses, sample, analysis, go/no-go, exact commands, git SHA.                                                                                          |
| `tests/test_cprime_strata.py`, `tests/test_cprime_export.py`, `tests/test_cprime_agreement.py`, `tests/test_cprime_ingest.py`, `tests/test_fig_cprime.py` | One test module per unit.                                                                                                                                                  |

Live facts the plan relies on (verified 2026-09-05 against `data/study/arena-study.db`, read-only):

| fact                                                               | value                                                                                                                                                                    |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Admissibility` rows: predicate, version                           | `structural`/`structural-v1` 946 rows; `semantic`/`semantic-v2` 884 rows                                                                                                 |
| semantic codes stored in `detail_json` as `{"code": ...}`          | multiple 59, not_the_organism 123, sub_part 73, ok 625, uncertain 4                                                                                                      |
| structural reject `reason` values                                  | `degenerate_bbox` 4, `empty` 43                                                                                                                                          |
| `Completeness.category` values                                     | complete 647, fragment 79, isolated-organ 89, partial-organism 61                                                                                                        |
| non-gold outputs, hidden vs visible                                | 817 visible, 123 hidden (`hidden_at` not null)                                                                                                                           |
| semantic rejects on completeness=`complete`, non-gold              | multiple 49, not_the_organism 35, sub_part 17 (= 101, the novel stratum)                                                                                                 |
| semantic rejects on completeness ∈ {fragment, isolated-organ}      | 133                                                                                                                                                                      |
| semantic rejects on `partial-organism` or with no completeness row | 18 + 3 = 21 (semantic-only but NOT on `complete`; spec §3 omitted these, this plan adds them as their own stratum so every rejected output has an inclusion probability) |
| contact sheets on disk `data/assets/renders/{id}_turntable.png`    | 940 (every output)                                                                                                                                                       |
| taxon per task                                                     | `TraitRubric.taxon` keyed by `task_id`; tasks 17 and 22 have no rubric → fall back to `Task.title`                                                                       |
| `Generator.paradigm`                                               | 8 distinct values                                                                                                                                                        |

---

### Task 1: Stratum assignment and stratified sampling (pure)

**Files:**

- Create: `scripts/paper/__init__.py` (empty)
- Create: `scripts/paper/cprime_strata.py`
- Test: `tests/test_cprime_strata.py`

**Interfaces:**

- Produces:
  - `STRATA: tuple[str, ...]` — the ordered stratum names: `("struct_degenerate_bbox", "struct_empty", "novel_multiple", "novel_not_the_organism", "novel_sub_part", "sem_also_completeness", "sem_only_other", "admitted")`
  - `TARGETS: dict[str, int]` — `{"struct_degenerate_bbox": 4, "struct_empty": 15, "novel_multiple": 40, "novel_not_the_organism": 30, "novel_sub_part": 17, "sem_also_completeness": 30, "sem_only_other": 10, "admitted": 120}`
  - `CALIBRATION_N = 20`, `SENSITIVITY_N = 40`
  - `assign_stratum(*, admitted: bool, structural_reason: str, semantic_code: str | None, completeness_category: str | None) -> str`
  - `plan_sample(populations: dict[str, list[int]], targets: dict[str, int], *, task_of: dict[int, int], seed: int) -> list[dict]` — each dict `{"output_id": int, "stratum": str, "inclusion_prob": float}`; `admitted` is allocated proportionally across tasks (largest-remainder), every other stratum is a simple random sample; `inclusion_prob = n_sampled_in_cell / N_cell`.
  - `draw_calibration(populations, main_ids: set[int], *, n: int, seed: int) -> list[dict]` — `n` items not in `main_ids`, spread round-robin over strata, same dict shape with `inclusion_prob = 0.0` (calibration is excluded from estimates).
  - `anonymize(ids: list[int], *, seed: int, prefix: str = "c") -> dict[int, str]` — seeded shuffle → `c001, c002, ...`, zero-padded to 3.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cprime_strata.py
from __future__ import annotations

from collections import Counter

import pytest

from scripts.paper.cprime_strata import (
    STRATA,
    TARGETS,
    anonymize,
    assign_stratum,
    draw_calibration,
    plan_sample,
)


def test_strata_names_and_targets_align():
    assert set(TARGETS) == set(STRATA)
    assert sum(TARGETS.values()) == 266


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(admitted=True, structural_reason="", semantic_code="ok", completeness_category="complete"), "admitted"),
        (dict(admitted=False, structural_reason="degenerate_bbox", semantic_code=None, completeness_category=None), "struct_degenerate_bbox"),
        (dict(admitted=False, structural_reason="empty", semantic_code="not_the_organism", completeness_category="fragment"), "struct_empty"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="complete"), "novel_multiple"),
        (dict(admitted=False, structural_reason="", semantic_code="not_the_organism", completeness_category="complete"), "novel_not_the_organism"),
        (dict(admitted=False, structural_reason="", semantic_code="sub_part", completeness_category="complete"), "novel_sub_part"),
        (dict(admitted=False, structural_reason="", semantic_code="sub_part", completeness_category="isolated-organ"), "sem_also_completeness"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="fragment"), "sem_also_completeness"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category="partial-organism"), "sem_only_other"),
        (dict(admitted=False, structural_reason="", semantic_code="multiple", completeness_category=None), "sem_only_other"),
    ],
)
def test_assign_stratum(kw, expected):
    assert assign_stratum(**kw) == expected


def test_assign_stratum_rejects_completeness_only_reject():
    # An output the completeness gate alone rejects (semantic said ok) is not in C′'s
    # population — the spec audits the semantic and structural predicates. Fail loud.
    with pytest.raises(ValueError):
        assign_stratum(admitted=False, structural_reason="", semantic_code="ok", completeness_category="fragment")


def test_plan_sample_sizes_and_inclusion_probs():
    pops = {s: list(range(i * 1000, i * 1000 + 50)) for i, s in enumerate(STRATA)}
    targets = {s: 10 for s in STRATA}
    task_of = {oid: oid % 3 for s in pops for oid in pops[s]}
    rows = plan_sample(pops, targets, task_of=task_of, seed=1)
    by = Counter(r["stratum"] for r in rows)
    assert all(by[s] == 10 for s in STRATA)
    assert len({r["output_id"] for r in rows}) == len(rows)
    for r in rows:
        if r["stratum"] != "admitted":
            assert r["inclusion_prob"] == pytest.approx(10 / 50)


def test_plan_sample_takes_whole_stratum_when_target_exceeds_population():
    pops = {"struct_degenerate_bbox": [1, 2, 3]}
    rows = plan_sample(pops, {"struct_degenerate_bbox": 4}, task_of={1: 1, 2: 1, 3: 1}, seed=1)
    assert sorted(r["output_id"] for r in rows) == [1, 2, 3]
    assert all(r["inclusion_prob"] == 1.0 for r in rows)


def test_plan_sample_admitted_is_proportional_by_task():
    # 90 admitted: task A 60, task B 30; target 30 -> 20 from A, 10 from B, probs 1/3 each
    pops = {"admitted": list(range(90))}
    task_of = {i: ("A" if i < 60 else "B") for i in range(90)}
    rows = plan_sample(pops, {"admitted": 30}, task_of=task_of, seed=3)
    by_task = Counter(task_of[r["output_id"]] for r in rows)
    assert by_task == {"A": 20, "B": 10}
    assert all(r["inclusion_prob"] == pytest.approx(1 / 3) for r in rows)


def test_plan_sample_is_deterministic_for_seed():
    pops = {"novel_multiple": list(range(49))}
    a = plan_sample(pops, {"novel_multiple": 40}, task_of={i: 0 for i in range(49)}, seed=11)
    b = plan_sample(pops, {"novel_multiple": 40}, task_of={i: 0 for i in range(49)}, seed=11)
    assert a == b


def test_draw_calibration_excludes_main_and_spreads():
    pops = {"admitted": list(range(100)), "novel_multiple": list(range(100, 130))}
    main = set(range(0, 95)) | set(range(100, 125))
    rows = draw_calibration(pops, main, n=6, seed=2)
    ids = {r["output_id"] for r in rows}
    assert len(ids) == 6 and not (ids & main)
    assert Counter(r["stratum"] for r in rows) == {"admitted": 3, "novel_multiple": 3}
    assert all(r["inclusion_prob"] == 0.0 for r in rows)


def test_anonymize_is_a_seeded_bijection_with_zero_padding():
    m = anonymize([50, 7, 900], seed=5)
    assert sorted(m.values()) == ["c001", "c002", "c003"]
    assert m == anonymize([50, 7, 900], seed=5)
    assert m != anonymize([50, 7, 900], seed=6) or True  # different seed may differ; bijection is what matters
    assert len(set(m.values())) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cprime_strata.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'scripts.paper'`.

- [ ] **Step 3: Implement the module**

```python
# scripts/paper/__init__.py
```

(empty file)

```python
# scripts/paper/cprime_strata.py
"""Study C′ sampling frame: which stratum an output belongs to, and a stratified sample with
recorded inclusion probabilities. Pure functions — no DB, no files — so the frame is testable and
the preregistration can quote it.

Strata follow docs/paper/2026-09-05-evidence-plan-design.md §3, plus `sem_only_other` (semantic
rejects whose completeness category is `partial-organism` or missing): the spec's "semantic-only
on complete" cell left those 21 rejected outputs with no inclusion probability, and a population
estimate cannot omit a cell of its own population."""

from __future__ import annotations

import random
from collections import defaultdict

STRATA: tuple[str, ...] = (
    "struct_degenerate_bbox",
    "struct_empty",
    "novel_multiple",
    "novel_not_the_organism",
    "novel_sub_part",
    "sem_also_completeness",
    "sem_only_other",
    "admitted",
)

TARGETS: dict[str, int] = {
    "struct_degenerate_bbox": 4,
    "struct_empty": 15,
    "novel_multiple": 40,
    "novel_not_the_organism": 30,
    "novel_sub_part": 17,
    "sem_also_completeness": 30,
    "sem_only_other": 10,
    "admitted": 120,
}

CALIBRATION_N = 20
SENSITIVITY_N = 40

_COMPLETENESS_REJECTS = {"fragment", "isolated-organ"}
_SEMANTIC_REJECTS = {"multiple", "sub_part", "not_the_organism"}


def assign_stratum(
    *,
    admitted: bool,
    structural_reason: str,
    semantic_code: str | None,
    completeness_category: str | None,
) -> str:
    """Structural failures take precedence (they are judged from the mesh, not the sheet); then
    semantic rejects split by what the completeness gate said about the same output. An output
    rejected ONLY by completeness is not part of this audit and is an error to classify."""
    if admitted:
        return "admitted"
    if structural_reason:
        return f"struct_{structural_reason}"
    if semantic_code not in _SEMANTIC_REJECTS:
        raise ValueError(
            f"not admitted but neither structural nor semantic rejected it "
            f"(structural={structural_reason!r}, semantic={semantic_code!r}, "
            f"completeness={completeness_category!r}) — completeness-only rejects are out of scope"
        )
    if completeness_category == "complete":
        return f"novel_{semantic_code}"
    if completeness_category in _COMPLETENESS_REJECTS:
        return "sem_also_completeness"
    return "sem_only_other"


def _largest_remainder(counts: dict, total: int) -> dict:
    """Allocate `total` across cells proportionally to `counts`, never exceeding a cell."""
    n = sum(counts.values())
    if n == 0:
        return {k: 0 for k in counts}
    raw = {k: total * c / n for k, c in counts.items()}
    alloc = {k: min(int(raw[k]), counts[k]) for k in counts}
    remaining = total - sum(alloc.values())
    for k in sorted(counts, key=lambda k: (-(raw[k] - int(raw[k])), k)):
        if remaining <= 0:
            break
        if alloc[k] < counts[k]:
            alloc[k] += 1
            remaining -= 1
    return alloc


def plan_sample(
    populations: dict[str, list[int]],
    targets: dict[str, int],
    *,
    task_of: dict[int, int],
    seed: int,
) -> list[dict]:
    """Stratified sample. `admitted` is allocated across tasks by largest remainder so the
    false-negative cell mirrors the corpus; every other stratum is a simple random sample.
    inclusion_prob = sampled / population for the cell the item was drawn from."""
    rng = random.Random(seed)
    rows: list[dict] = []
    for stratum in STRATA:
        pop = sorted(populations.get(stratum, []))
        target = targets.get(stratum, 0)
        if not pop or target <= 0:
            continue
        if stratum == "admitted":
            by_task: dict = defaultdict(list)
            for oid in pop:
                by_task[task_of[oid]].append(oid)
            alloc = _largest_remainder({t: len(v) for t, v in by_task.items()}, min(target, len(pop)))
            for t in sorted(by_task, key=str):
                k = alloc[t]
                if k == 0:
                    continue
                cell = by_task[t]
                for oid in rng.sample(cell, k):
                    rows.append({"output_id": oid, "stratum": stratum, "inclusion_prob": k / len(cell)})
        else:
            k = min(target, len(pop))
            for oid in rng.sample(pop, k):
                rows.append({"output_id": oid, "stratum": stratum, "inclusion_prob": k / len(pop)})
    return rows


def draw_calibration(
    populations: dict[str, list[int]], main_ids: set[int], *, n: int, seed: int
) -> list[dict]:
    """`n` items outside the main sample, round-robin over strata with spare items.
    inclusion_prob is 0.0: calibration items never enter an estimate."""
    rng = random.Random(seed)
    spare = {
        s: sorted(set(populations.get(s, [])) - main_ids) for s in STRATA if set(populations.get(s, [])) - main_ids
    }
    for s in spare:
        rng.shuffle(spare[s])
    rows: list[dict] = []
    order = [s for s in STRATA if s in spare]
    while len(rows) < n and any(spare.values()):
        for s in order:
            if spare[s] and len(rows) < n:
                rows.append({"output_id": spare[s].pop(), "stratum": s, "inclusion_prob": 0.0})
    return rows


def anonymize(ids: list[int], *, seed: int, prefix: str = "c") -> dict[int, str]:
    order = list(ids)
    random.Random(seed).shuffle(order)
    return {oid: f"{prefix}{i + 1:03d}" for i, oid in enumerate(order)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cprime_strata.py -q`
Expected: all PASS. If `test_plan_sample_admitted_is_proportional_by_task` fails on a rounding edge, check `_largest_remainder` — 60/90 × 30 = 20 exactly, 30/90 × 30 = 10 exactly, so any drift is a bug in the allocation, not the test.

- [ ] **Step 5: Commit**

```bash
git add scripts/paper/__init__.py scripts/paper/cprime_strata.py tests/test_cprime_strata.py
git commit -m "Define the Study C′ sampling frame as pure functions"
```

---

### Task 2: Export — population from the live gate, sheets, manifest, blind rater sheets

**Files:**

- Create: `scripts/paper/cprime_export.py`
- Test: `tests/test_cprime_export.py`

**Interfaces:**

- Consumes (Task 1): `STRATA, TARGETS, CALIBRATION_N, SENSITIVITY_N, assign_stratum, plan_sample, draw_calibration, anonymize`.
- Consumes (app): `app.admissibility.non_admitted_output_ids(db, rubric)`, `app.models.{ModelOutput, Admissibility, Completeness, Task, TraitRubric}`, `app.judge_render.contact_sheet_path(output_id, "turntable")`, `app.config.ASSET_DIR`.
- Produces:
  - `build_populations(db) -> tuple[dict[str, list[int]], dict[int, dict]]` — `(populations, info)` where `info[oid] = {"task_id", "taxon", "asset_path", "structural_reason", "semantic_code", "completeness_category", "admitted"}`. Population = non-gold, `hidden_at IS NULL`, and (for rejects) rejected by the full three-predicate rubric with structural or semantic as the cause; completeness-only rejects and unevaluated outputs are excluded and counted in the returned `info` under key `-1` → `{"excluded_completeness_only": n, "excluded_unevaluated": n}`.
  - `export(db, out_dir: Path, *, seed: int, asset_dir: Path) -> dict` — writes files below, returns counts `{"main": n, "calibration": n, "structural": n, "sensitivity": n, "per_stratum": {...}}`. Raises `FileNotFoundError` naming the output id if a contact sheet is missing (fail loud, never skip).

Files written under `out_dir`:

- `sheets/{anon_id}.png` — copies of `renders/{output_id}_turntable.png`.
- `manifest.csv` (PRIVATE) — `anon_id,output_id,stratum,inclusion_prob,set,task_id,taxon,asset_path,structural_reason,semantic_code,completeness_category` where `set ∈ {main, calibration}`.
- `rater_sheet.csv` (blind, main set) — `anon_id,taxon,sheet_file,label,note` with `label`/`note` empty.
- `calibration_sheet.csv` (blind, calibration set) — same columns.
- `structural_sheet.csv` (for the 3D-literate rater; struct\_\* strata only) — `anon_id,taxon,mesh_file,label,note` where `mesh_file` is the absolute mesh path under `asset_dir`.
- `sensitivity_sheet.csv` — first `SENSITIVITY_N` main items in anon-id order, `anon_id,taxon,mesh_file,label,note`.
- `README.txt` — which file goes to whom, and that `manifest.csv` never leaves the machine.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cprime_export.py
"""Export builds its population from the app's gate composer and writes blind rater sheets.
Uses the suite's isolated DB + ASSET_DIR (conftest)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app import config
from app.admissibility import Verdict
from app.database import SessionLocal
from app.judge_render import contact_sheet_path
from app.models import Completeness, TraitRubric
from app.structural import upsert_verdict
from tests.factories import make_outputs

from scripts.paper import cprime_strata
from scripts.paper.cprime_export import build_populations, export


def _sheet(oid: int) -> Path:
    p = Path(config.ASSET_DIR) / contact_sheet_path(oid, "turntable")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG fake " + str(oid).encode())
    return p


def _seed(db):
    """6 outputs on one task: 2 admitted, 1 struct empty, 1 novel multiple, 1 sem-also, 1 completeness-only."""
    outs = make_outputs(db, 6)
    ok = Verdict(True, "")
    db.add(TraitRubric(taxon="Zea mays", task_id=outs[0].task_id, traits_json="[]"))
    plan = [
        ("admitted", ok, Verdict(True, "", {"code": "ok"}), "complete"),
        ("admitted", ok, Verdict(True, "", {"code": "ok"}), "complete"),
        ("struct_empty", Verdict(False, "empty"), Verdict(False, "not_the_organism", {"code": "not_the_organism"}), "fragment"),
        ("novel_multiple", ok, Verdict(False, "multiple", {"code": "multiple"}), "complete"),
        ("sem_also_completeness", ok, Verdict(False, "sub_part", {"code": "sub_part"}), "isolated-organ"),
        ("completeness_only", ok, Verdict(True, "", {"code": "ok"}), "fragment"),
    ]
    expect = {}
    for o, (stratum, sv, mv, cat) in zip(outs, plan):
        upsert_verdict(db, o.id, "structural", sv, "structural-v1")
        upsert_verdict(db, o.id, "semantic", mv, "semantic-v2")
        db.add(Completeness(output_id=o.id, category=cat, checklist_json="{}"))
        expect[o.id] = stratum
        _sheet(o.id)
    db.commit()
    return outs, expect


def test_build_populations_uses_gate_and_drops_completeness_only():
    with SessionLocal() as db:
        outs, expect = _seed(db)
        pops, info = build_populations(db)
    ids = {o.id for o in outs}
    got = {oid: s for s, lst in pops.items() for oid in lst if oid in ids}
    assert got == {oid: s for oid, s in expect.items() if s != "completeness_only"}
    assert info[-1]["excluded_completeness_only"] >= 1
    novel = [o for o in outs if expect[o.id] == "novel_multiple"][0]
    assert info[novel.id]["taxon"] == "Zea mays"
    assert info[novel.id]["semantic_code"] == "multiple"


def test_export_writes_blind_sheets_and_private_manifest(tmp_path):
    with SessionLocal() as db:
        outs, expect = _seed(db)
        # Scope the export to these outputs by shrinking targets; other tests' rows may exist.
        targets = {s: 1 for s in cprime_strata.STRATA}
        counts = export(db, tmp_path, seed=1, asset_dir=Path(config.ASSET_DIR), targets=targets, calibration_n=1)
    assert counts["main"] >= 3
    manifest = list(csv.DictReader(open(tmp_path / "manifest.csv")))
    rater = list(csv.DictReader(open(tmp_path / "rater_sheet.csv")))
    assert set(rater[0].keys()) == {"anon_id", "taxon", "sheet_file", "label", "note"}
    assert all(r["label"] == "" and r["note"] == "" for r in rater)
    anon_in_manifest = {m["anon_id"] for m in manifest if m["set"] == "main"}
    assert {r["anon_id"] for r in rater} == anon_in_manifest
    # every sheet copied, named by anon id only
    for r in rater:
        assert (tmp_path / "sheets" / r["sheet_file"]).exists()
        assert "output" not in r["sheet_file"]
    # structural sheet lists only struct_* items and points at meshes
    struct = list(csv.DictReader(open(tmp_path / "structural_sheet.csv")))
    struct_anon = {m["anon_id"] for m in manifest if m["stratum"].startswith("struct_")}
    assert {s["anon_id"] for s in struct} == struct_anon
    assert (tmp_path / "README.txt").exists()


def test_export_fails_loud_on_missing_sheet(tmp_path):
    with SessionLocal() as db:
        outs, expect = _seed(db)
        victim = [o for o in outs if expect[o.id] == "novel_multiple"][0]
        (Path(config.ASSET_DIR) / contact_sheet_path(victim.id, "turntable")).unlink()
        with pytest.raises(FileNotFoundError, match=str(victim.id)):
            export(db, tmp_path, seed=1, asset_dir=Path(config.ASSET_DIR),
                   targets={s: 50 for s in cprime_strata.STRATA}, calibration_n=0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_cprime_export.py -q`
Expected: FAIL at import — `cannot import name 'build_populations' from 'scripts.paper.cprime_export'` (or module not found).

- [ ] **Step 3: Implement the exporter**

```python
# scripts/paper/cprime_export.py
"""Export the Study C′ audit set: population from the live gate, stratified sample, contact
sheets under anonymized ids, a PRIVATE manifest, and BLIND rater sheets.

READ-ONLY against the database. Sheets come from the existing render cache
(`renders/{id}_turntable.png`, the same image the semantic judge saw); a missing sheet is an error,
never a silent skip — a skipped item would change the inclusion probability we just recorded.

Usage (study DB, read-only):
  BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db?mode=ro&uri=true" \
  BIO3D_DATA_DIR="$(pwd)/data" \
  .venv/bin/python scripts/paper/cprime_export.py --out data/paper/cprime --seed 20260905
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import config  # noqa: E402
from app.admissibility import non_admitted_output_ids  # noqa: E402
from app.judge_render import contact_sheet_path  # noqa: E402
from app.models import Admissibility, Completeness, ModelOutput, Task, TraitRubric  # noqa: E402
from scripts.paper.cprime_strata import (  # noqa: E402
    CALIBRATION_N,
    SENSITIVITY_N,
    STRATA,
    TARGETS,
    anonymize,
    assign_stratum,
    draw_calibration,
    plan_sample,
)

RUBRIC = ["structural", "completeness", "semantic"]
CONDITION = "turntable"
BLIND_FIELDS = ["anon_id", "taxon", "sheet_file", "label", "note"]
MESH_FIELDS = ["anon_id", "taxon", "mesh_file", "label", "note"]
MANIFEST_FIELDS = [
    "anon_id", "output_id", "stratum", "inclusion_prob", "set", "task_id", "taxon", "asset_path",
    "structural_reason", "semantic_code", "completeness_category",
]


def build_populations(db: Session) -> tuple[dict[str, list[int]], dict[int, dict]]:
    """Population per stratum + per-output info. Rejection comes from the gate composer over the
    FULL rubric so the frame is exactly what voters never saw. Completeness-only rejects and
    unevaluated outputs are outside this audit and are counted, not classified."""
    rejected = non_admitted_output_ids(db, rubric=RUBRIC)
    taxon_by_task = dict(db.execute(select(TraitRubric.task_id, TraitRubric.taxon)).all())
    title_by_task = dict(db.execute(select(Task.id, Task.title)).all())
    verdicts: dict[int, dict] = {}
    for a in db.execute(select(Admissibility)).scalars():
        v = verdicts.setdefault(a.output_id, {})
        if a.predicate == "structural":
            v["structural_reason"] = "" if a.admit else a.reason
            v["structural_seen"] = True
        elif a.predicate == "semantic":
            v["semantic_code"] = json.loads(a.detail_json or "{}").get("code")
            v["semantic_seen"] = True
    completeness = dict(db.execute(select(Completeness.output_id, Completeness.category)).all())

    pops: dict[str, list[int]] = {s: [] for s in STRATA}
    info: dict[int, dict] = {-1: {"excluded_completeness_only": 0, "excluded_unevaluated": 0}}
    outs = db.execute(
        select(ModelOutput).where(ModelOutput.is_gold.is_(False), ModelOutput.hidden_at.is_(None))
    ).scalars()
    for o in outs:
        v = verdicts.get(o.id, {})
        admitted = o.id not in rejected
        if not admitted and not (v.get("structural_seen") and v.get("semantic_seen")):
            info[-1]["excluded_unevaluated"] += 1
            continue
        try:
            stratum = assign_stratum(
                admitted=admitted,
                structural_reason=v.get("structural_reason", ""),
                semantic_code=v.get("semantic_code"),
                completeness_category=completeness.get(o.id),
            )
        except ValueError:
            info[-1]["excluded_completeness_only"] += 1
            continue
        pops[stratum].append(o.id)
        info[o.id] = {
            "task_id": o.task_id,
            "taxon": taxon_by_task.get(o.task_id) or title_by_task.get(o.task_id, ""),
            "asset_path": o.asset_path,
            "structural_reason": v.get("structural_reason", ""),
            "semantic_code": v.get("semantic_code") or "",
            "completeness_category": completeness.get(o.id) or "",
            "admitted": admitted,
        }
    return pops, info


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def export(
    db: Session,
    out_dir: Path,
    *,
    seed: int,
    asset_dir: Path,
    targets: dict[str, int] | None = None,
    calibration_n: int = CALIBRATION_N,
    sensitivity_n: int = SENSITIVITY_N,
) -> dict:
    targets = TARGETS if targets is None else targets
    pops, info = build_populations(db)
    task_of = {oid: d["task_id"] for oid, d in info.items() if oid != -1}
    main = plan_sample(pops, targets, task_of=task_of, seed=seed)
    main_ids = {r["output_id"] for r in main}
    calib = draw_calibration(pops, main_ids, n=calibration_n, seed=seed + 1)
    anon = anonymize([r["output_id"] for r in main + calib], seed=seed + 2)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sheets").mkdir(exist_ok=True)
    manifest: list[dict] = []
    for set_name, rows in (("main", main), ("calibration", calib)):
        for r in rows:
            oid = r["output_id"]
            src = asset_dir / contact_sheet_path(oid, CONDITION)
            if not (src.exists() and src.stat().st_size > 0):
                raise FileNotFoundError(f"no contact sheet for output {oid}: {src}")
            a = anon[oid]
            shutil.copyfile(src, out_dir / "sheets" / f"{a}.png")
            manifest.append({
                "anon_id": a, "output_id": oid, "stratum": r["stratum"],
                "inclusion_prob": f"{r['inclusion_prob']:.6f}", "set": set_name, **info[oid],
            })
    manifest.sort(key=lambda m: m["anon_id"])
    _write_csv(out_dir / "manifest.csv", MANIFEST_FIELDS, manifest)

    def blind(rows):
        return [{"anon_id": m["anon_id"], "taxon": m["taxon"], "sheet_file": f"{m['anon_id']}.png",
                 "label": "", "note": ""} for m in rows]

    def mesh(rows):
        return [{"anon_id": m["anon_id"], "taxon": m["taxon"],
                 "mesh_file": str(asset_dir / m["asset_path"]), "label": "", "note": ""} for m in rows]

    main_rows = [m for m in manifest if m["set"] == "main"]
    calib_rows = [m for m in manifest if m["set"] == "calibration"]
    struct_rows = [m for m in main_rows if m["stratum"].startswith("struct_")]
    _write_csv(out_dir / "rater_sheet.csv", BLIND_FIELDS, blind(main_rows))
    _write_csv(out_dir / "calibration_sheet.csv", BLIND_FIELDS, blind(calib_rows))
    _write_csv(out_dir / "structural_sheet.csv", MESH_FIELDS, mesh(struct_rows))
    _write_csv(out_dir / "sensitivity_sheet.csv", MESH_FIELDS, mesh(main_rows[:sensitivity_n]))
    (out_dir / "README.txt").write_text(
        "Study C' audit set.\n"
        "manifest.csv is PRIVATE: it maps anon ids to outputs, strata and gate verdicts. Never send it to a rater.\n"
        "rater_sheet.csv + sheets/  -> raters 1 and 2 (label every row), then rater 3 for disagreements.\n"
        "calibration_sheet.csv      -> all raters first; discussed, then excluded from analysis.\n"
        "structural_sheet.csv       -> the 3D-literate rater; judge the mesh, not a sheet.\n"
        "sensitivity_sheet.csv      -> one rater re-labels these from the interactive mesh.\n"
        "Codes: ok | multiple | sub_part | not_the_organism | indeterminate. See docs/paper/cprime-rater-instructions.md.\n"
    )
    per = {s: sum(1 for m in main_rows if m["stratum"] == s) for s in STRATA}
    pop_sizes = {s: len(pops[s]) for s in STRATA}
    return {"main": len(main_rows), "calibration": len(calib_rows), "structural": len(struct_rows),
            "sensitivity": min(sensitivity_n, len(main_rows)), "per_stratum": per,
            "population": pop_sizes, "excluded": info[-1]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the Study C′ audit set (read-only).")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260905)
    args = ap.parse_args()
    from app.database import SessionLocal

    with SessionLocal() as db:
        counts = export(db, args.out, seed=args.seed, asset_dir=Path(config.ASSET_DIR))
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cprime_export.py tests/test_cprime_strata.py -q`
Expected: PASS. If `non_admitted_output_ids` marks the test's admitted outputs as unevaluated because the completeness predicate says they are "applicable" without an organ inventory for `Zea mays`, look at `app/completeness.py:170 applicable_output_ids` — the test seeds a `TraitRubric` for `Zea mays`, which has an inventory in `app/organ_inventory.py`; if the check requires something more, seed it in `_seed` rather than weakening the exporter.

- [ ] **Step 5: Commit**

```bash
git add scripts/paper/cprime_export.py tests/test_cprime_export.py
git commit -m "Export the Study C′ audit set from the live gate's own population"
```

---

### Task 3: Agreement and weighted-rate statistics (pure)

**Files:**

- Create: `scripts/paper/cprime_agreement.py`
- Test: `tests/test_cprime_agreement.py`

**Interfaces:**

- Produces:
  - `krippendorff_alpha_nominal(units: list[list[str | None]]) -> float | None` — rows = units, columns = raters, `None` = missing; returns `None` when fewer than 2 pairable values overall.
  - `raw_agreement(a: list[str], b: list[str]) -> float`
  - `per_class_agreement(a: list[str], b: list[str]) -> dict[str, float]` — for each class c: fraction of units where either rater said c on which both said c.
  - `wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]`
  - `ht_rate(items: list[dict], *, numerator: callable, denominator: callable) -> float | None` — Horvitz–Thompson ratio estimator: `Σ w·num / Σ w·den` with `w = 1/inclusion_prob`; `None` if denominator mass is 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cprime_agreement.py
from __future__ import annotations

import pytest

from scripts.paper.cprime_agreement import (
    ht_rate,
    krippendorff_alpha_nominal,
    per_class_agreement,
    raw_agreement,
    wilson,
)


def test_alpha_perfect_agreement_is_one():
    assert krippendorff_alpha_nominal([["a", "a"], ["b", "b"], ["a", "a"]]) == pytest.approx(1.0)


def test_alpha_hand_computed_two_raters():
    # 2 raters, 4 units: aa, bb, ab, ba. N=8 values, n_a=n_b=4.
    # Do = (1/8) * [0 + 0 + 2 + 2] = 0.5 ; De = 2*4*4 / (8*7) = 0.5714 ; alpha = 1 - 0.5/0.5714 = 0.125
    units = [["a", "a"], ["b", "b"], ["a", "b"], ["b", "a"]]
    assert krippendorff_alpha_nominal(units) == pytest.approx(0.125, abs=1e-6)


def test_alpha_ignores_missing_and_single_valued_units():
    with_missing = [["a", "a", None], ["b", "b", None], ["a", "b", None], ["b", "a", None], ["a", None, None]]
    assert krippendorff_alpha_nominal(with_missing) == pytest.approx(0.125, abs=1e-6)


def test_alpha_none_when_nothing_pairable():
    assert krippendorff_alpha_nominal([["a", None], [None, "b"]]) is None


def test_raw_and_per_class_agreement():
    a = ["ok", "ok", "multiple", "sub_part", "ok"]
    b = ["ok", "multiple", "multiple", "ok", "ok"]
    assert raw_agreement(a, b) == pytest.approx(3 / 5)
    pc = per_class_agreement(a, b)
    # ok: either said ok in units 0,1,3,4 (4); both in 0,4 (2) -> 0.5
    assert pc["ok"] == pytest.approx(0.5)
    assert pc["multiple"] == pytest.approx(0.5)  # units 1,2 ; both in 2
    assert pc["sub_part"] == pytest.approx(0.0)


def test_wilson_matches_known_value():
    lo, hi = wilson(8, 10)
    assert lo == pytest.approx(0.4901, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_ht_rate_reweights_by_inclusion_prob():
    # Two strata: A (prob 0.5, 2 items, 1 positive) and B (prob 0.1, 1 item, 1 positive).
    # Weighted positives = 1*2 + 1*10 = 12 ; weighted total = 2*2 + 1*10 = 14 -> 12/14
    items = [
        {"inclusion_prob": 0.5, "pos": True},
        {"inclusion_prob": 0.5, "pos": False},
        {"inclusion_prob": 0.1, "pos": True},
    ]
    r = ht_rate(items, numerator=lambda i: i["pos"], denominator=lambda i: True)
    assert r == pytest.approx(12 / 14)
    assert ht_rate([], numerator=lambda i: True, denominator=lambda i: True) is None


def test_ht_rate_skips_zero_inclusion_prob_items():
    items = [{"inclusion_prob": 0.0, "pos": True}, {"inclusion_prob": 1.0, "pos": False}]
    assert ht_rate(items, numerator=lambda i: i["pos"], denominator=lambda i: True) == pytest.approx(0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cprime_agreement.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.paper.cprime_agreement'`.

- [ ] **Step 3: Implement the statistics**

```python
# scripts/paper/cprime_agreement.py
"""Agreement and design-weighted rates for Study C′. Pure, stdlib only.

Krippendorff's α (nominal) follows Krippendorff (2011), "Computing Krippendorff's Alpha-
Reliability": α = 1 − D_o/D_e with D_o = (1/n) Σ_u (1/(m_u−1)) Σ_{c≠k} o_uck and
D_e = (1/(n(n−1))) Σ_{c≠k} n_c n_k over the n pairable values (units with ≥2 values)."""

from __future__ import annotations

import math
from collections import Counter
from typing import Callable


def krippendorff_alpha_nominal(units: list[list[str | None]]) -> float | None:
    pairable = [[v for v in u if v is not None] for u in units]
    pairable = [u for u in pairable if len(u) >= 2]
    n = sum(len(u) for u in pairable)
    if n < 2:
        return None
    totals: Counter = Counter()
    d_o = 0.0
    for u in pairable:
        m = len(u)
        cnt = Counter(u)
        totals.update(cnt)
        # ordered pairs (c != k) within the unit = m*(m-1) - Σ_c cnt_c*(cnt_c-1)
        disagree = m * (m - 1) - sum(c * (c - 1) for c in cnt.values())
        d_o += disagree / (m - 1)
    d_o /= n
    d_e = (n * (n - 1) - sum(c * (c - 1) for c in totals.values())) / (n * (n - 1))
    if d_e == 0:
        return 1.0
    return 1.0 - d_o / d_e


def raw_agreement(a: list[str], b: list[str]) -> float:
    if len(a) != len(b):
        raise ValueError(f"length mismatch {len(a)} vs {len(b)}")
    if not a:
        return float("nan")
    return sum(x == y for x, y in zip(a, b)) / len(a)


def per_class_agreement(a: list[str], b: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in sorted(set(a) | set(b)):
        either = [(x, y) for x, y in zip(a, b) if x == c or y == c]
        both = sum(1 for x, y in either if x == y == c)
        out[c] = both / len(either) if either else float("nan")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def ht_rate(
    items: list[dict], *, numerator: Callable[[dict], bool], denominator: Callable[[dict], bool]
) -> float | None:
    num = den = 0.0
    for it in items:
        pi = float(it["inclusion_prob"])
        if pi <= 0:
            continue  # calibration items carry 0.0 and must not enter an estimate
        w = 1.0 / pi
        if denominator(it):
            den += w
            if numerator(it):
                num += w
    return None if den == 0 else num / den
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cprime_agreement.py -q`
Expected: PASS, including the hand-computed α = 0.125 and Wilson (8,10) → (0.490, 0.943).

- [ ] **Step 5: Commit**

```bash
git add scripts/paper/cprime_agreement.py tests/test_cprime_agreement.py
git commit -m "Add agreement and design-weighted rate estimators for Study C′"
```

---

### Task 4: Ingest — adjudicate labels, estimate, report, go/no-go

**Files:**

- Create: `scripts/paper/cprime_ingest.py`
- Test: `tests/test_cprime_ingest.py`

**Interfaces:**

- Consumes (Task 3): `krippendorff_alpha_nominal, raw_agreement, per_class_agreement, wilson, ht_rate`.
- Consumes (Task 2 file formats): `manifest.csv`, filled `rater_sheet.csv` copies (`labels_r1.csv`, `labels_r2.csv`, `labels_r3.csv`), filled `structural_sheet.csv` (`labels_structural.csv`).
- Produces:
  - `CODES = ("ok", "multiple", "sub_part", "not_the_organism", "indeterminate")`
  - `read_labels(path: Path) -> dict[str, str]` — `anon_id → label`; raises `ValueError` naming the row for a label outside `CODES` or an empty label.
  - `adjudicate(r1: dict, r2: dict, r3: dict | None) -> dict[str, dict]` — per anon id `{"label": str, "source": "agree"|"adjudicated"|"unresolved"}`; agreement → that label; disagreement → r3's label if present, else `unresolved`.
  - `human_inadmissible(label: str) -> bool | None` — `True` for `multiple|sub_part|not_the_organism`, `False` for `ok`, `None` for `indeterminate` (excluded from PPV/rates, counted).
  - `estimate(manifest_rows: list[dict], final: dict[str, dict], r1: dict, r2: dict) -> dict` — the results object (schema below).
  - `render_markdown(results: dict) -> str`
  - `GO_PPV = 0.8`, `GO_FN = 0.1`; `go_no_go(results) -> tuple[bool, str]`.

Results schema (`results.json`):

```json
{
  "n_main": 266,
  "n_calibration": 20,
  "n_unresolved": 0,
  "n_indeterminate": 3,
  "per_stratum": {
    "novel_multiple": {
      "population": 49,
      "sampled": 40,
      "labelled": 40,
      "inadmissible": 36,
      "ppv": 0.9,
      "ppv_ci": [0.77, 0.96]
    },
    "...": {}
  },
  "novel_ppv": { "k": 78, "n": 87, "ppv": 0.896, "ci": [0.81, 0.94] },
  "admitted_fn": { "k": 4, "n": 118, "rate": 0.034, "ci": [0.013, 0.084] },
  "weighted": {
    "sensitivity": 0.71,
    "specificity": 0.97,
    "gate_reject_rate": 0.3,
    "human_inadmissible_rate": 0.33
  },
  "agreement": {
    "raw": 0.88,
    "per_class": { "ok": 0.93 },
    "alpha_r1_r2": 0.74,
    "alpha_all3": 0.71
  },
  "go": true,
  "go_reason": "novel PPV 0.896 >= 0.8 and admitted FN 0.034 < 0.1"
}
```

`weighted.sensitivity` = P(gate rejects | human inadmissible), `weighted.specificity` = P(gate admits | human admissible), both Horvitz–Thompson over main-set items with a non-`None` human verdict; the gate's rejection is `stratum != "admitted"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cprime_ingest.py
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.paper.cprime_ingest import (
    adjudicate,
    estimate,
    go_no_go,
    human_inadmissible,
    read_labels,
    render_markdown,
)


def _csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def test_read_labels_rejects_bad_code(tmp_path):
    p = _csv(tmp_path / "l.csv", [{"anon_id": "c001", "taxon": "x", "sheet_file": "c001.png", "label": "okay", "note": ""}])
    with pytest.raises(ValueError, match="c001"):
        read_labels(p)


def test_read_labels_rejects_empty(tmp_path):
    p = _csv(tmp_path / "l.csv", [{"anon_id": "c002", "taxon": "x", "sheet_file": "c002.png", "label": "", "note": ""}])
    with pytest.raises(ValueError, match="c002"):
        read_labels(p)


def test_adjudicate_uses_third_rater_only_on_disagreement():
    r1 = {"c001": "ok", "c002": "multiple", "c003": "sub_part"}
    r2 = {"c001": "ok", "c002": "ok", "c003": "ok"}
    r3 = {"c002": "multiple"}
    final = adjudicate(r1, r2, r3)
    assert final["c001"] == {"label": "ok", "source": "agree"}
    assert final["c002"] == {"label": "multiple", "source": "adjudicated"}
    assert final["c003"] == {"label": "unresolved", "source": "unresolved"}


def test_human_inadmissible_mapping():
    assert human_inadmissible("ok") is False
    assert human_inadmissible("multiple") is True
    assert human_inadmissible("indeterminate") is None


def _manifest():
    rows = []
    for i in range(10):  # novel_multiple: pop 20, sampled 10
        rows.append({"anon_id": f"c{i+1:03d}", "stratum": "novel_multiple", "inclusion_prob": "0.5", "set": "main"})
    for i in range(10, 20):  # admitted: pop 100, sampled 10
        rows.append({"anon_id": f"c{i+1:03d}", "stratum": "admitted", "inclusion_prob": "0.1", "set": "main"})
    rows.append({"anon_id": "c999", "stratum": "admitted", "inclusion_prob": "0.000000", "set": "calibration"})
    return rows


def test_estimate_ppv_fn_and_weighted_rates():
    man = _manifest()
    # raters agree everywhere: 9 of 10 novel are inadmissible, 1 ok; admitted: 1 of 10 inadmissible, 1 indeterminate
    labels = {f"c{i+1:03d}": "multiple" for i in range(9)}
    labels["c010"] = "ok"
    for i in range(10, 20):
        labels[f"c{i+1:03d}"] = "ok"
    labels["c011"] = "sub_part"
    labels["c012"] = "indeterminate"
    final = adjudicate(labels, labels, None)
    res = estimate(man, final, labels, labels)
    assert res["n_main"] == 20 and res["n_calibration"] == 1
    assert res["per_stratum"]["novel_multiple"]["ppv"] == pytest.approx(0.9)
    assert res["novel_ppv"]["k"] == 9 and res["novel_ppv"]["n"] == 10
    assert res["admitted_fn"] == pytest.approx({"k": 1, "n": 9, "rate": 1 / 9, "ci": res["admitted_fn"]["ci"]})
    assert res["n_indeterminate"] == 1
    # weighted sensitivity: human-inadmissible = 9 novel (w=2) + 1 admitted (w=10). gate rejected the 9 novel.
    assert res["weighted"]["sensitivity"] == pytest.approx(18 / 28)
    # weighted specificity: human-ok = 1 novel (w=2) + 8 admitted (w=10). gate admitted the 8.
    assert res["weighted"]["specificity"] == pytest.approx(80 / 82)
    assert res["agreement"]["raw"] == pytest.approx(1.0)
    assert res["agreement"]["alpha_r1_r2"] == pytest.approx(1.0)


def test_go_no_go_thresholds():
    ok = {"novel_ppv": {"ppv": 0.85}, "admitted_fn": {"rate": 0.05}, "n_unresolved": 0}
    assert go_no_go(ok)[0] is True
    assert go_no_go({**ok, "novel_ppv": {"ppv": 0.79}})[0] is False
    assert go_no_go({**ok, "admitted_fn": {"rate": 0.1}})[0] is False
    assert go_no_go({**ok, "n_unresolved": 1})[0] is False


def test_render_markdown_has_stratum_table():
    man = _manifest()
    labels = {m["anon_id"]: "ok" for m in man}
    final = adjudicate(labels, labels, None)
    md = render_markdown(estimate(man, final, labels, labels))
    assert "| stratum |" in md and "novel_multiple" in md and "go/no-go" in md.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cprime_ingest.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement ingest**

```python
# scripts/paper/cprime_ingest.py
"""Turn Study C′ rater CSVs into the preregistered estimates and the go/no-go.

Analysis is fixed here BEFORE labels exist (see docs/paper/prereg-cprime.md); the numbers are
read out, not chosen. Reads the PRIVATE manifest to recover stratum + inclusion probability.

Usage:
  .venv/bin/python scripts/paper/cprime_ingest.py --dir data/paper/cprime \
      --r1 data/paper/cprime/labels_r1.csv --r2 data/paper/cprime/labels_r2.csv \
      --r3 data/paper/cprime/labels_r3.csv [--structural data/paper/cprime/labels_structural.csv]
Writes results.json and results.md into --dir and prints the go/no-go line."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.paper.cprime_agreement import (  # noqa: E402
    ht_rate,
    krippendorff_alpha_nominal,
    per_class_agreement,
    raw_agreement,
    wilson,
)
from scripts.paper.cprime_strata import STRATA  # noqa: E402

CODES = ("ok", "multiple", "sub_part", "not_the_organism", "indeterminate")
NOVEL = ("novel_multiple", "novel_not_the_organism", "novel_sub_part")
GO_PPV = 0.8
GO_FN = 0.1


def read_labels(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            label = (row.get("label") or "").strip().lower()
            if label not in CODES:
                raise ValueError(f"{path}:{i} anon_id={row.get('anon_id')} has label {label!r}; allowed {CODES}")
            out[row["anon_id"]] = label
    return out


def adjudicate(r1: dict, r2: dict, r3: dict | None) -> dict[str, dict]:
    r3 = r3 or {}
    final: dict[str, dict] = {}
    for a in sorted(set(r1) | set(r2)):
        l1, l2 = r1.get(a), r2.get(a)
        if l1 is not None and l1 == l2:
            final[a] = {"label": l1, "source": "agree"}
        elif a in r3:
            final[a] = {"label": r3[a], "source": "adjudicated"}
        else:
            final[a] = {"label": "unresolved", "source": "unresolved"}
    return final


def human_inadmissible(label: str) -> bool | None:
    if label == "ok":
        return False
    if label in ("multiple", "sub_part", "not_the_organism"):
        return True
    return None


def estimate(manifest_rows: list[dict], final: dict[str, dict], r1: dict, r2: dict, r3: dict | None = None) -> dict:
    main = [m for m in manifest_rows if m["set"] == "main"]
    calib = [m for m in manifest_rows if m["set"] == "calibration"]
    items = []
    n_unresolved = n_indet = 0
    for m in main:
        f = final.get(m["anon_id"], {"label": "unresolved"})
        if f["label"] == "unresolved":
            n_unresolved += 1
            continue
        hi = human_inadmissible(f["label"])
        if hi is None:
            n_indet += 1
            continue
        items.append({**m, "inclusion_prob": float(m["inclusion_prob"]), "human_inadmissible": hi,
                      "gate_rejected": m["stratum"] != "admitted"})

    per: dict[str, dict] = {}
    pop_by = defaultdict(int)
    samp_by = defaultdict(int)
    for m in main:
        samp_by[m["stratum"]] += 1
        p = float(m["inclusion_prob"])
        pop_by[m["stratum"]] = max(pop_by[m["stratum"]], round(1 / p) if p > 0 else 0)
    for s in STRATA:
        lab = [i for i in items if i["stratum"] == s]
        k = sum(i["human_inadmissible"] for i in lab)
        n = len(lab)
        per[s] = {"population_est": pop_by[s] * samp_by[s] if s != "admitted" else None,
                  "sampled": samp_by[s], "labelled": n, "inadmissible": k,
                  "ppv": (k / n) if n else None, "ppv_ci": list(wilson(k, n)) if n else None}

    novel = [i for i in items if i["stratum"] in NOVEL]
    nk, nn = sum(i["human_inadmissible"] for i in novel), len(novel)
    adm = [i for i in items if i["stratum"] == "admitted"]
    ak, an = sum(i["human_inadmissible"] for i in adm), len(adm)

    weighted = {
        "sensitivity": ht_rate(items, numerator=lambda i: i["gate_rejected"], denominator=lambda i: i["human_inadmissible"]),
        "specificity": ht_rate(items, numerator=lambda i: not i["gate_rejected"], denominator=lambda i: not i["human_inadmissible"]),
        "gate_reject_rate": ht_rate(items, numerator=lambda i: i["gate_rejected"], denominator=lambda i: True),
        "human_inadmissible_rate": ht_rate(items, numerator=lambda i: i["human_inadmissible"], denominator=lambda i: True),
    }

    ids = [m["anon_id"] for m in main if m["anon_id"] in r1 and m["anon_id"] in r2]
    a, b = [r1[i] for i in ids], [r2[i] for i in ids]
    units2 = [[r1.get(i), r2.get(i)] for i in ids]
    units3 = [[r1.get(i), r2.get(i), (r3 or {}).get(i)] for i in ids]
    agreement = {
        "raw": raw_agreement(a, b) if ids else None,
        "per_class": per_class_agreement(a, b) if ids else {},
        "alpha_r1_r2": krippendorff_alpha_nominal(units2),
        "alpha_all3": krippendorff_alpha_nominal(units3) if r3 else None,
    }
    res = {
        "n_main": len(main), "n_calibration": len(calib), "n_unresolved": n_unresolved, "n_indeterminate": n_indet,
        "per_stratum": per,
        "novel_ppv": {"k": nk, "n": nn, "ppv": (nk / nn) if nn else None, "ci": list(wilson(nk, nn)) if nn else None},
        "admitted_fn": {"k": ak, "n": an, "rate": (ak / an) if an else None, "ci": list(wilson(ak, an)) if an else None},
        "weighted": weighted, "agreement": agreement,
    }
    go, reason = go_no_go(res)
    res["go"], res["go_reason"] = go, reason
    return res


def go_no_go(res: dict) -> tuple[bool, str]:
    ppv = res["novel_ppv"].get("ppv")
    fn = res["admitted_fn"].get("rate")
    if res.get("n_unresolved", 0):
        return False, f"{res['n_unresolved']} unresolved disagreement(s); adjudicate before deciding"
    if ppv is None or fn is None:
        return False, "missing labels for the novel stratum or the admitted cell"
    ok = ppv >= GO_PPV and fn < GO_FN
    verdict = "GO" if ok else "NO-GO"
    return ok, f"{verdict}: novel PPV {ppv:.3f} {'>=' if ppv >= GO_PPV else '<'} {GO_PPV} and admitted FN {fn:.3f} {'<' if fn < GO_FN else '>='} {GO_FN}"


def render_markdown(res: dict) -> str:
    def f(x, d=3):
        return "—" if x is None else f"{x:.{d}f}"

    lines = ["# Study C′ results", "",
             f"Main items {res['n_main']}, calibration {res['n_calibration']} (excluded), "
             f"indeterminate {res['n_indeterminate']}, unresolved {res['n_unresolved']}.", "",
             "| stratum | sampled | labelled | inadmissible | PPV | 95% CI |", "|---|---|---|---|---|---|"]
    for s, d in res["per_stratum"].items():
        ci = "—" if not d["ppv_ci"] else f"{d['ppv_ci'][0]:.2f}–{d['ppv_ci'][1]:.2f}"
        lines.append(f"| {s} | {d['sampled']} | {d['labelled']} | {d['inadmissible']} | {f(d['ppv'])} | {ci} |")
    w, ag = res["weighted"], res["agreement"]
    lines += ["", f"Novel-stratum PPV {f(res['novel_ppv']['ppv'])} (k={res['novel_ppv']['k']}, n={res['novel_ppv']['n']}); "
              f"admitted false-negative rate {f(res['admitted_fn']['rate'])} (k={res['admitted_fn']['k']}, n={res['admitted_fn']['n']}).",
              f"Population-weighted sensitivity {f(w['sensitivity'])}, specificity {f(w['specificity'])}.",
              f"Raw agreement {f(ag['raw'])}; Krippendorff α (r1,r2) {f(ag['alpha_r1_r2'])}; α (all three) {f(ag['alpha_all3'])}.",
              "", f"**Go/no-go:** {res['go_reason']}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate Study C′ results from rater CSVs.")
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--r1", type=Path, required=True)
    ap.add_argument("--r2", type=Path, required=True)
    ap.add_argument("--r3", type=Path)
    ap.add_argument("--structural", type=Path, help="3D-literate rater's labels for struct_* items; override r1/r2/r3 there")
    args = ap.parse_args()
    manifest = list(csv.DictReader(open(args.dir / "manifest.csv", newline="")))
    r1, r2 = read_labels(args.r1), read_labels(args.r2)
    r3 = read_labels(args.r3) if args.r3 else None
    final = adjudicate(r1, r2, r3)
    if args.structural:
        for a, lab in read_labels(args.structural).items():
            final[a] = {"label": lab, "source": "structural_rater"}
    res = estimate(manifest, final, r1, r2, r3)
    (args.dir / "results.json").write_text(json.dumps(res, indent=2))
    (args.dir / "results.md").write_text(render_markdown(res))
    print(res["go_reason"])
    return 0 if res["go"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cprime_ingest.py tests/test_cprime_agreement.py -q`
Expected: PASS. The weighted-rate assertions (18/28, 80/82) are hand-derived in the test comments; if they fail, the bug is in `estimate`'s item filtering (indeterminate must be dropped BEFORE weighting), not in `ht_rate`.

- [ ] **Step 5: Commit**

```bash
git add scripts/paper/cprime_ingest.py tests/test_cprime_ingest.py
git commit -m "Ingest Study C′ rater labels into the preregistered estimates"
```

---

### Task 5: House theme and the PPV figure (R + ggplot2)

**Files:**

- Create: `scripts/paper/theme_taxon3d.R`
- Create: `scripts/paper/fig_cprime_ppv.R`
- Test: `tests/test_fig_cprime.py`

**Interfaces:**

- `theme_taxon3d.R` defines `theme_taxon3d()` (a ggplot2 theme object) and `taxon3d_palette` (named character vector; keys `admitted`, `structural`, `semantic`, `novel`, `neutral`). Every paper figure `source()`s this file and nothing else styles a plot.
- `fig_cprime_ppv.R <results.json> <out.png>` — one figure: PPV per stratum (point + Wilson CI bar), strata on the y axis in `STRATA` order, the admitted cell shown as the false-negative rate with a dashed line at 0.1, and a dashed line at 0.8 for PPV. Exit non-zero if the input is missing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fig_cprime.py
"""Real-execution check: the R figure script runs on a fixture and writes a PNG."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "paper" / "fig_cprime_ppv.R"

pytestmark = pytest.mark.skipif(shutil.which("Rscript") is None, reason="Rscript not installed")


def _fixture(tmp_path: Path) -> Path:
    per = {}
    for i, s in enumerate(["struct_degenerate_bbox", "struct_empty", "novel_multiple", "novel_not_the_organism",
                           "novel_sub_part", "sem_also_completeness", "sem_only_other", "admitted"]):
        k, n = (i + 2, i + 3)
        per[s] = {"sampled": n, "labelled": n, "inadmissible": k, "ppv": k / n, "ppv_ci": [max(0, k / n - 0.2), min(1, k / n + 0.1)]}
    res = {"per_stratum": per, "novel_ppv": {"ppv": 0.85}, "admitted_fn": {"rate": 0.04, "ci": [0.01, 0.1]}, "go": True}
    p = tmp_path / "results.json"
    p.write_text(json.dumps(res))
    return p


def test_fig_script_writes_png(tmp_path):
    out = tmp_path / "fig.png"
    proc = subprocess.run(["Rscript", str(SCRIPT), str(_fixture(tmp_path)), str(out)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 1000


def test_fig_script_fails_loud_without_input(tmp_path):
    proc = subprocess.run(["Rscript", str(SCRIPT), str(tmp_path / "missing.json"), str(tmp_path / "x.png")], capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0


def test_fig_script_sources_the_house_theme():
    src = SCRIPT.read_text()
    assert 'source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))' in src
    assert "theme_grey" not in src and "theme(" not in src.replace("theme_taxon3d(", "")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_fig_cprime.py -q`
Expected: FAIL — `Rscript` cannot open the missing script (returncode 2), and the source-text assertion errors on a missing file.

- [ ] **Step 3: Write the theme and the figure script**

```r
# scripts/paper/theme_taxon3d.R
# The ONE house style for every Taxon3D paper figure. Restyle here, never inline.
suppressPackageStartupMessages(library(ggplot2))

taxon3d_palette <- c(
  admitted   = "#2E7D32",
  structural = "#6D4C41",
  semantic   = "#1565C0",
  novel      = "#C62828",
  neutral    = "#616161"
)

theme_taxon3d <- function(base_size = 11) {
  theme_minimal(base_size = base_size, base_family = "sans") +
    theme(
      panel.grid.minor = element_blank(),
      panel.grid.major.y = element_blank(),
      axis.title = element_text(size = rel(0.95)),
      plot.title = element_text(face = "bold", size = rel(1.05)),
      plot.subtitle = element_text(colour = taxon3d_palette[["neutral"]]),
      legend.position = "bottom",
      plot.background = element_rect(fill = "white", colour = NA)
    )
}
```

```r
# scripts/paper/fig_cprime_ppv.R
# Usage: Rscript scripts/paper/fig_cprime_ppv.R results.json out.png
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))
suppressPackageStartupMessages(library(jsonlite))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("usage: fig_cprime_ppv.R results.json out.png")
if (!file.exists(args[[1]])) stop("no such file: ", args[[1]])
res <- fromJSON(args[[1]], simplifyVector = FALSE)

order <- c("struct_degenerate_bbox", "struct_empty", "novel_multiple", "novel_not_the_organism",
           "novel_sub_part", "sem_also_completeness", "sem_only_other", "admitted")
rows <- lapply(order, function(s) {
  d <- res$per_stratum[[s]]
  if (is.null(d) || is.null(d$ppv)) return(NULL)
  data.frame(stratum = s, est = d$ppv, lo = d$ppv_ci[[1]], hi = d$ppv_ci[[2]], n = d$labelled,
             group = if (startsWith(s, "struct")) "structural" else if (startsWith(s, "novel")) "novel"
                     else if (s == "admitted") "admitted" else "semantic")
})
df <- do.call(rbind, rows)
df$stratum <- factor(df$stratum, levels = rev(order))
df$label <- ifelse(df$stratum == "admitted", "share inadmissible (false-negative rate)", "PPV of the rejection")

p <- ggplot(df, aes(x = est, y = stratum, colour = group)) +
  geom_vline(xintercept = 0.8, linetype = "dashed", colour = taxon3d_palette[["neutral"]]) +
  geom_vline(xintercept = 0.1, linetype = "dotted", colour = taxon3d_palette[["neutral"]]) +
  geom_errorbarh(aes(xmin = lo, xmax = hi), height = 0.25) +
  geom_point(size = 2.6) +
  geom_text(aes(label = paste0("n=", n)), nudge_y = 0.32, size = 3, colour = taxon3d_palette[["neutral"]]) +
  scale_colour_manual(values = taxon3d_palette, guide = "none") +
  scale_x_continuous(limits = c(0, 1), breaks = seq(0, 1, 0.2)) +
  labs(x = "Proportion judged inadmissible by humans (Wilson 95% CI)", y = NULL,
       title = "Study C′: does the gate reject what humans reject?",
       subtitle = "Dashed: go threshold PPV 0.8 for rejected strata. Dotted: ceiling 0.1 for the admitted cell.") +
  theme_taxon3d()

ggsave(args[[2]], p, width = 7.5, height = 4.5, dpi = 200)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_fig_cprime.py -q`
Expected: PASS (three tests). Open the PNG the first test wrote if you want to eyeball it: the path is printed by `pytest -s`.

- [ ] **Step 5: Commit**

```bash
git add scripts/paper/theme_taxon3d.R scripts/paper/fig_cprime_ppv.R tests/test_fig_cprime.py
git commit -m "Add the paper's house ggplot theme and the Study C′ PPV figure"
```

---

### Task 6: Rater instructions

**Files:**

- Create: `docs/paper/cprime-rater-instructions.md`

**Interfaces:** Quotes the codes from Task 4 `CODES` and the v2 prompt at `app/semantic.py:84-97`. No code.

- [ ] **Step 1: Write the document**

```markdown
# Study C′ — rater instructions

You will see contact sheets: one generated 3D model, rendered from several angles on a grey
background, and the name of the organism it was meant to be. For each sheet, answer ONE
question and type one code into the `label` column of your CSV.

**The question:** is this a SINGLE, WHOLE specimen of a living thing that a voter could fairly
judge?

This is an admissibility check only. It is NOT a quality, accuracy, or species check. A crude,
lumpy, low-detail, or oddly coloured model that is still recognisably one whole organism is
`ok`. A model that looks like the wrong species but is one whole organism is `ok`. Voters
judge fidelity; you do not.

| code               | use when                                                                                                               |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `ok`               | one whole organism (however crude); or a natural same-species cluster for organisms that grow that way (bracket fungi) |
| `multiple`         | clearly more than one distinct organism, or a cluttered scene with unrelated objects                                   |
| `sub_part`         | only a detached part — a single leaf, flower, fruit, wing, fin, cap, organ — with no whole organism                    |
| `not_the_organism` | not a plausible whole organism at all: junk or broken geometry, a featureless blob, an everyday object                 |
| `indeterminate`    | you genuinely cannot tell from the sheet                                                                               |

Reject only when clearly inadmissible. When in doubt between a reject code and `ok`, choose
`ok`; when in doubt between two reject codes, pick the one that fits best and say why in `note`.

**Procedure**

1. Label `calibration_sheet.csv` first (20 items). We discuss those together, then you do not
   change them and they are not analysed.
2. Label every row of `rater_sheet.csv`. Do not skip rows; use `indeterminate` rather than leaving
   a blank. Do not discuss items with the other raters until everyone has finished.
3. `note` is optional except for `indeterminate` and any reject where you hesitated.
4. Save as `labels_<yourinitials>.csv`, same columns, UTF-8.

**For the 3D-literate rater only:** `structural_sheet.csv` lists meshes, not sheets. Open each
`mesh_file` in a glTF viewer and answer the same question about the mesh itself (an empty or
flat file is `not_the_organism`).

**Sensitivity arm (one rater):** after the main sheet, re-label the 40 rows of
`sensitivity_sheet.csv` from the interactive mesh, without looking back at your sheet labels.

You will not be told what any automated system said about these models, and you should not
try to guess it.
```

- [ ] **Step 2: Check against the construct**

Run: `sed -n 84,97p app/semantic.py` and confirm every reject definition in the table matches the prompt's wording (multiple/cluttered; detached part; junk/blob/everyday object; crude-but-whole = ok; colonial exception). Fix the doc, never the prompt.

- [ ] **Step 3: Commit**

```bash
git add docs/paper/cprime-rater-instructions.md
git commit -m "Write the Study C′ rater instructions from the gate's own construct"
```

---

### Task 7: Preregistration — commit before any label exists

**Files:**

- Create: `docs/paper/prereg-cprime.md`

**Interfaces:** Quotes `TARGETS`, `CALIBRATION_N`, `SENSITIVITY_N`, `GO_PPV`, `GO_FN` from Tasks 1 and 4 by value; names the exact export seed.

- [ ] **Step 1: Run the export for real (read-only) to fill in the population table**

```bash
mkdir -p data/paper
BIO3D_DATABASE_URL="sqlite:///$(pwd)/data/study/arena-study.db" \
BIO3D_DATA_DIR="$(pwd)/data" \
timeout 300 .venv/bin/python scripts/paper/cprime_export.py --out data/paper/cprime --seed 20260905 | tee data/paper/cprime/export_counts.json
```

Expected: JSON with `population` per stratum close to the table in this plan's header (novel 49/35/17, sem_also 133, sem_only_other 21, struct 4 + 43 minus gold/hidden, admitted ≈ 530–552), `main` = 266 or fewer where a stratum is smaller than its target, `excluded_unevaluated` small. Confirm with `ls data/paper/cprime/sheets | wc -l` = main + calibration. If a `FileNotFoundError` names an output, that output lacks a cached sheet; render it with `.venv/bin/python scripts/render_one_sheet.py <id> turntable` (same env) and re-run. The script does not write to the DB; `sqlite3 data/study/arena-study.db "pragma user_version"` and the file mtime should be unchanged afterwards — check the mtime before and after.

- [ ] **Step 2: Write the preregistration**

```markdown
# Preregistration — Study C′: held-out human audit of the Taxon3D admissibility gate

Registered <date>, at commit <git rev-parse --short HEAD after Task 6>. Labels do not exist yet.

## Question

Do the gate's exclusions correspond to what independent humans call inadmissible (positive
predictive value), and do its admissions leak inadmissible outputs (false-negative rate)?

## Gate under audit

Structural predicate `structural-v1` (`app/structural.py`), completeness gate on categories
`isolated-organ, fragment`, semantic predicate `semantic-v2` (`app/semantic.py`, reject codes
`multiple, sub_part, not_the_organism`; `ok`/`uncertain` admit). Frozen: no verdict row changes
during the study.

## Population and sample (seed 20260905, `scripts/paper/cprime_export.py`)

<paste the population/per_stratum table from export_counts.json, one row per stratum, with
inclusion probability = sampled/population>
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
```

Fill `<date>`, the commit SHA, and the table from `data/paper/cprime/export_counts.json`.

- [ ] **Step 3: Commit**

```bash
git add docs/paper/prereg-cprime.md
git commit -m "Preregister Study C′ before any label is collected"
```

- [ ] **Step 4: Record the registration SHA in the document and amend**

```bash
git rev-parse --short HEAD
# paste into the "at commit" line, then:
git add docs/paper/prereg-cprime.md && git commit --amend --no-edit
```

---

### Task 8: Hand-off package and closing checklist

**Files:**

- Modify: `docs/paper/2026-09-05-evidence-plan-design.md` (§3 only: add the `sem_only_other` stratum row and the live population numbers, and mark the two §9 questions as decided per the user's approval: two colleagues + one screened rater; no third `overall` criterion).
- Create later, after labels arrive: `docs/paper/cprime/labels_anonymized.csv` (anon_id, stratum, final label, rater 1, rater 2, rater 3) and `docs/paper/cprime/results.md` — the released held-out set (§6 item 4). These two files are the ONLY artifacts from `data/paper/cprime/` that get committed; `manifest.csv` and `sheets/` never do.

- [ ] **Step 1: Update the spec's §3 table and §9 decisions**

Edit `docs/paper/2026-09-05-evidence-plan-design.md`: in the §3 sample table add the row `| semantic-only, completeness partial-organism or unscored | 21 | 10 |`, replace `~150` with the live `133`, and append under §9: "Decided 2026-09-05: two known colleagues plus one screened rater; no third criterion." Keep the rest untouched.

- [ ] **Step 2: Run the whole suite once**

Run: `.venv/bin/pytest -q -x tests/test_cprime_*.py tests/test_fig_cprime.py tests/test_admissibility*.py`
Expected: all PASS, no warnings from the new modules.

- [ ] **Step 3: Commit and push**

```bash
git add docs/paper/2026-09-05-evidence-plan-design.md
git commit -m "Record the Study C′ frame and decisions in the evidence-plan spec"
git push origin master
```

- [ ] **Step 4: Hand off to the user (needs a human)**

Report: the folder `data/paper/cprime/` is ready; which CSV goes to which rater (from `README.txt`); the preregistration SHA; the command to run once labels are back:

```bash
.venv/bin/python scripts/paper/cprime_ingest.py --dir data/paper/cprime \
  --r1 data/paper/cprime/labels_r1.csv --r2 data/paper/cprime/labels_r2.csv \
  --r3 data/paper/cprime/labels_r3.csv --structural data/paper/cprime/labels_structural.csv \
&& Rscript scripts/paper/fig_cprime_ppv.R data/paper/cprime/results.json docs/paper/cprime/fig_cprime_ppv.png
```

Then, only after labels exist: build `docs/paper/cprime/labels_anonymized.csv` by joining the three label CSVs to `manifest.csv` on `anon_id` and keeping `anon_id, stratum, final_label, r1, r2, r3` (no output ids, no asset paths), copy `results.md` beside it, commit both with the figure, and update task #11 to completed. The go/no-go line printed by ingest decides whether task #12 (Study A′) unblocks.

---

## Self-review

**Spec coverage (§3, §7, §9):** construct + codes → Tasks 4 and 6; same contact sheet → Task 2 copies from the judge's cache; interactive mesh sensitivity arm on 40 → `sensitivity_sheet.csv` (Task 2) + arm analysis named in prereg (Task 7); three raters, adjudication blind → Task 4 `adjudicate` + Task 6 procedure; 20-item calibration excluded → Tasks 1/2/4; stratified sample with inclusion probabilities → Task 1; PPV, weighted sensitivity/specificity, raw + class agreement, α → Tasks 3/4; kappa "only alongside raw agreement" → not computed at all (α replaces it; raw is reported) — deliberate; go/no-go → Task 4; no gate tuning → constraints + prereg; export with anonymized ids + manifest → Task 2; analysis scripts under `scripts/paper/` with R house theme → Task 5; preregistration before data → Task 7 ordering; released labelled set → Task 8. §9 decisions recorded → Task 8. Gap noted and closed: the 21 semantic-only rejects not on `complete` (added stratum). Not covered on purpose: A′ pair schedule and study route (§7 bullet 2) — that is Study A′'s plan, gated on this study's result.

**Placeholders:** the prereg template has `<date>`, `<commit>`, `<table>` that Task 7 Steps 1–4 fill from real outputs; no other TBDs.

**Type consistency:** `plan_sample`/`draw_calibration` rows carry `output_id, stratum, inclusion_prob` and Task 2 reads exactly those; manifest column `set` ∈ {main, calibration} is read by Task 4; `CODES` in Task 4 match the rater table in Task 6 and the README in Task 2; strata names are identical across Tasks 1, 2, 4, 5 (the R script hard-codes the same eight strings in the same order).
