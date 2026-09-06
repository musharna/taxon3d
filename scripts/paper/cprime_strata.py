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
