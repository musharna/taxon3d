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
    "novel_multiple",
    "novel_not_the_organism",
    "novel_sub_part",
    "sem_also_completeness",
    "sem_only_other",
    "admitted",
)

TARGETS: dict[str, int] = {
    "struct_degenerate_bbox": 1,
    "novel_multiple": 46,
    "novel_not_the_organism": 31,
    "novel_sub_part": 2,
    "sem_also_completeness": 30,
    "sem_only_other": 13,
    "admitted": 143,
}

# Population per stratum in the live frame, measured 2026-09-06 against data/study/arena-study.db
# via build_populations() (non-gold, hidden_at IS NULL, rejection from the app's own gate
# composer over the full three-predicate rubric).
#
# The spec's original targets were sized from reject counts that did NOT filter `hidden_at`, so
# they asked for 4 degenerate_bbox and 17 sub_part-on-complete outputs that the visible corpus no
# longer contains — release #161 hid 3 and 15 of them respectively. Those two strata are now
# sampled exhaustively (inclusion probability 1), and their 18 freed slots were reallocated to
# strata that still have headroom, keeping the total at 266: the two remaining `novel` cells to
# their full population, +3 to sem_only_other, and +8 to `admitted`, which carries the
# false-negative rate that the second go/no-go criterion reads.
#
# Amendment 2026-09-07 (before any label existed): the `struct_empty` stratum (43 in frame, 15
# sampled) is GONE. Every one of those 43 is a capture-scan point cloud (Plant3D, Crops3D, ROSE-X,
# ICRISAT, IPK MRI, ROMI), rejected on FORMAT — zero faces — not on biology; structural-v2 now
# names the reason `point_cloud`. A human asked "is this a whole organism?" sees a whole plant
# on every one, so the stratum could only ever have measured the wrong thing. Its 15 slots go to
# `admitted` (128 -> 143), the false-negative cell. The 43 are reported as a format exclusion.
FRAME_SIZES: dict[str, int] = {
    "struct_degenerate_bbox": 1,
    "novel_multiple": 46,
    "novel_not_the_organism": 31,
    "novel_sub_part": 2,
    "sem_also_completeness": 91,
    "sem_only_other": 18,
    "admitted": 552,
}

CALIBRATION_N = 20
SENSITIVITY_N = 40

# Structural reasons that describe the asset's FORMAT, not the organism. `empty` is what
# structural-v1 wrote for the same files before the rename; both spellings leave the frame.
FORMAT_REASONS: frozenset[str] = frozenset({"empty", "point_cloud"})
EXCLUDED_FORMAT = "excluded_format"  # sentinel returned by assign_stratum; never a stratum

_COMPLETENESS_REJECTS = {"fragment", "isolated-organ"}
_SEMANTIC_REJECTS = {"multiple", "sub_part", "not_the_organism"}


def assign_stratum(
    *,
    admitted: bool,
    structural_reason: str,
    semantic_code: str | None,
    completeness_category: str | None,
) -> str:
    """Format rejections (point clouds) leave the frame under EXCLUDED_FORMAT. Other structural
    failures take precedence (they are judged from the mesh, not the sheet); then
    semantic rejects split by what the completeness gate said about the same output. An output
    rejected ONLY by completeness is not part of this audit and is an error to classify."""
    if admitted:
        return "admitted"
    if structural_reason in FORMAT_REASONS:
        return EXCLUDED_FORMAT
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
            alloc = _largest_remainder(
                {t: len(v) for t, v in by_task.items()}, min(target, len(pop))
            )
            for t in sorted(by_task, key=str):
                k = alloc[t]
                if k == 0:
                    continue
                cell = by_task[t]
                for oid in rng.sample(cell, k):
                    rows.append(
                        {"output_id": oid, "stratum": stratum, "inclusion_prob": k / len(cell)}
                    )
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
        s: sorted(set(populations.get(s, [])) - main_ids)
        for s in STRATA
        if set(populations.get(s, [])) - main_ids
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
