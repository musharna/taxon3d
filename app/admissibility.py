# app/admissibility.py
"""Pre-vote admissibility gate: an output is admitted iff every predicate in the active rubric
has AFFIRMATIVELY admitted it — a predicate that never evaluated an output it applies to does not
admit it. Predicates are pluggable (structural geometry, completeness category, ...); the pool
gate calls non_admitted_output_ids() — the single composer. Generalizes by swapping the rubric
(a list of predicate names), machinery unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from . import config, flags


@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str = ""  # "" when admit; else a short machine code, e.g. "degenerate_bbox"
    detail: dict = field(default_factory=dict)


class Predicate(Protocol):
    name: str
    version: str

    def rejected_output_ids(self, db: Session) -> set[int]:
        """Output ids this predicate evaluated and did NOT admit (precomputed; keeps the gate
        O(1))."""
        ...

    def unevaluated_output_ids(self, db: Session) -> set[int]:
        """Output ids this predicate APPLIES to but has never produced a verdict for.

        Required, not optional: a predicate that cannot say what it failed to look at cannot be
        part of a gate that fails closed. Returning an empty set is a claim of total coverage, and
        must be justified in the implementation, not assumed by omission."""
        ...


class CompletenessPredicate:
    name = "completeness"
    version = "completeness-v1"

    def rejected_output_ids(self, db: Session) -> set[int]:
        # Reuse the existing completeness-category exclusion verbatim (no re-scoring, no drift).
        return flags.excluded_output_ids_by_completeness(
            db, config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES
        )

    def unevaluated_output_ids(self, db: Session) -> set[int]:
        """Applicable outputs with no completeness row. Applicability is narrow here — only tasks
        carrying a TraitRubric whose taxon has an organ inventory — so this gates the outputs we
        meant to score and never scored, and leaves alone the ones the metric does not cover."""
        from sqlalchemy import select

        from .completeness import applicable_output_ids
        from .models import Completeness

        scored = {oid for (oid,) in db.execute(select(Completeness.output_id)).all()}
        return applicable_output_ids(db) - scored


DEFAULT_RUBRIC: list[str] = ["structural", "completeness"]


def _effective_rubric() -> list[str]:
    """The default rubric, plus 'semantic' iff the semantic predicate is in gate mode. DEFAULT_RUBRIC
    stays static (advisory/off never auto-exclude); the composer only reaches the semantic predicate
    for the default (rubric=None) call, which is the live pool gate. Explicit rubric= calls are used
    verbatim, unaffected by the mode."""
    rubric = list(DEFAULT_RUBRIC)
    if config.SEMANTIC_ADMISSIBILITY_MODE == "gate":
        rubric.append("semantic")
    return rubric


def _registry() -> dict[str, Predicate]:
    # Function-local imports: structural.py and semantic.py import Verdict from this module, so a
    # module-level import here would be a real circular import. Direct (unguarded) — a genuine
    # ImportError must fail loud, not degrade to "predicate absent".
    from .semantic import SemanticPredicate
    from .structural import StructuralPredicate

    return {
        "completeness": CompletenessPredicate(),
        "structural": StructuralPredicate(),
        "semantic": SemanticPredicate(),
    }


class UnevaluatedOutputs(Exception):
    """Raised when a caller asks to publish outputs the rubric never actually evaluated."""


def assert_rubric_coverage(
    db: Session, output_ids: set[int], rubric: list[str] | None = None
) -> None:
    """Fail loud if any of `output_ids` is applicable to a rubric predicate that has no verdict
    for it.

    non_admitted_output_ids already keeps these outputs away from voters, but silently: they
    simply vanish from the pool and from a release bundle. A release that quietly ships fewer
    outputs than intended reads as success — that exact failure (a posture default dropping 232
    provider-licensed outputs) shipped once and was caught only by eyeballing a manifest. So the
    release asks the question explicitly and refuses, naming the predicate and the ids, instead of
    discovering it in a diff afterwards."""
    reg = _registry()
    names = _effective_rubric() if rubric is None else rubric
    missing: dict[str, list[int]] = {}
    for name in names:
        gap = sorted(reg[name].unevaluated_output_ids(db) & set(output_ids))
        if gap:
            missing[name] = gap
    if missing:
        detail = "; ".join(
            f"{name}: {len(ids)} output(s) {ids[:20]}{' ...' if len(ids) > 20 else ''}"
            for name, ids in missing.items()
        )
        raise UnevaluatedOutputs(
            f"refusing to proceed: outputs are applicable to a rubric predicate that never "
            f"evaluated them — {detail}. Run the corresponding scorer, or exclude them "
            f"deliberately."
        )


def non_admitted_output_ids(db: Session, rubric: list[str] | None = None) -> set[int]:
    """Ids NOT admitted by the rubric: for each predicate, the outputs it rejected UNION the
    outputs it applies to but never evaluated. rubric=None -> the mode-aware effective rubric
    (DEFAULT_RUBRIC + semantic-if-gate). Unknown predicate name -> KeyError (fail-loud).

    The second half is what makes this gate fail CLOSED, and it is the whole point. This function
    used to return rejections only, which silently equated "no verdict row" with "evaluated and
    passed" — so an output nothing had looked at was admitted by default. Release v6 shipped 10
    such outputs into the public vote pool, 2 of them genuinely inadmissible, because generating an
    output records a structural verdict and nothing else; semantic and completeness are separate
    passes that a release can simply outrun.

    Absence of evidence is not evidence of admissibility. An output reaches a human voter only if
    every predicate that applies to it has affirmatively said yes."""
    reg = _registry()
    names = _effective_rubric() if rubric is None else rubric
    out: set[int] = set()
    for name in names:
        pred = reg[name]
        out |= pred.rejected_output_ids(db)
        out |= pred.unevaluated_output_ids(db)
    return out
