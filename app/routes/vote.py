"""The voting surface: /arena, /study, the ballot + vote APIs, and the opaque /media/o routes."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
from collections.abc import Sequence

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, integrity, kingdoms, matchmaking, mesh_lod, service, study
from ..database import get_db
from ..models import (
    CalibrationPair,
    Category,
    Comparison,
    Criterion,
    KBallot,
    ModelOutput,
    Task,
    Vote,
    VoterSession,
)
from ..schemas import FlagIn, KVoteIn, VoteIn
from ..storage import content_type_for
from ..web_common import (
    _effective_category_ids,
    _hero_stats,
    _resolve_category_id,
    _roadmap_or_none,
    require_internal_pages,
    storage,
    templates,
)
from .admin import ADMIN_COOKIE

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_criterion(db: Session) -> Criterion:
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        raise HTTPException(500, "No 'overall' criterion — run the seed.")
    return crit


def _arena_asset_url(o: ModelOutput) -> str:
    """Opaque, output-scoped asset URL for the ANONYMIZED arena payloads. The raw asset_path can
    encode gold status or generator identity (e.g. `gold/task19__bad.glb`), which a voter could
    read in devtools to unmask the planted-bad decoy or the model — so the arena never exposes it;
    the /media/o/{id} route (below) resolves the id back to the file. output_id is already in the
    payload (K-wise picks reference it), so this leaks nothing new. The extension is cosmetic."""
    return f"/media/o/{o.id}.{o.asset_format}"


def _arena_lod_url(o: ModelOutput) -> str | None:
    """URL of the low-detail companion, or None when this output has none.

    Read from `meta_json.lod`, which the export stamps on rows it actually generated an LOD for.
    The alternative — probing storage per output — would cost an S3 round trip per mesh on every
    single ballot, on the exact request path this feature exists to make faster.

    A missing or unparseable flag yields None, so the ballot silently keeps today's behaviour of
    fetching the full mesh. That is the correct failure direction: the full mesh is always right,
    while a wrongly advertised LOD is a 404 in front of a voter.
    """
    if o.asset_format != "glb":
        return None
    try:
        meta = json.loads(o.meta_json or "{}") or {}
    except (ValueError, TypeError):
        return None
    if not meta.get("lod"):
        return None
    return f"/media/o/{o.id}.lod.glb"


def _uniform_lod_urls(outputs: Sequence[ModelOutput]) -> list[str | None]:
    """LOD urls for a whole ballot — all of them, or none of them.

    Whether an output HAS a decimated companion is decided per mesh by a byte-size threshold
    (`is_lod_candidate`, 1 MB). Mesh size is a property of the GENERATOR: dense neural meshes
    clear it, LLM-authored procedural ones do not. So the threshold hands out decimated meshes
    along generator lines. Measured on production 2026-08-04: 6 generators always had one, 43
    never did, and **113 of 120 served k-ballots (94.2%) put a decimated mesh beside a
    full-resolution one**.

    On a fidelity benchmark that is a confound rather than a preference — the voter compares our
    decimation of one model against another model's real geometry, and because the threshold
    selects the heaviest meshes, the models most likely to carry fine structure are exactly the
    ones degraded at first paint. The card-size delta is small (silhouette IoU 0.9985) and the
    swap-on-zoom fires, so this bites voters who judge without zooming; small is not zero, and it
    is not random with respect to model identity.

    Raising or lowering the threshold does not fix it — ANY threshold on a generator-correlated
    quantity reproduces it. The fidelity tier has to be a property of the ballot, which is what
    this enforces: if one slot cannot be served decimated, none are.

    Cost is real and accepted: on a mixed ballot every slot falls back to the full mesh, which is
    the payload this feature exists to avoid.

    DO NOT try to close that gap by raising coverage — measured 2026-08-07, it cannot be closed.
    An earlier version of this docstring said "closing that gap means raising LOD coverage toward
    100%". That is FALSE, and the arithmetic is why: this rule makes coverage a PRODUCT, since a
    ballot is uniform only if every one of its k slots has a companion.

        per-output coverage 17.5% (before the texture tier) -> ~0% of k=4 ballots
        per-output coverage 33.4% (after  the texture tier) -> 1.4% of k=4 ballots  (0.334^4)

    Doubling coverage bought 1.4%. Going further is not available: 335 of 527 outputs sit BELOW
    `mesh_lod.LOD_MIN_SOURCE_BYTES` and were never candidates, and running the real reducer over
    20 of them with only that threshold bypassed kept just 5 — the rest are 1-80 KB meshes with
    nothing to remove. Extrapolated, every lever together reaches ~50% coverage, i.e. ~6% of
    ballots. LOD is structurally OFF for k-wise ballots on this corpus.

    The reason is not tunable: bias-free means degrading EVERY slot or NONE, and "every" is
    physically impossible when some meshes are already minimal. So the honest state is that this
    invariant and the LOD payload win are incompatible here, and the invariant wins — the LODs
    still serve the k=2 path and cost nothing to keep. If someone later decides the payload
    matters more, that is a deliberate trade against a measured card-size delta of silhouette
    IoU 0.9985, not a coverage bug to fix.
    """
    urls = [_arena_lod_url(o) for o in outputs]
    if any(u is None for u in urls):
        return [None] * len(urls)
    return urls


def _serialize(
    comparison: Comparison,
    task: Task,
    crit: Criterion,
    out_a: ModelOutput,
    out_b: ModelOutput,
    references: list[dict] | None = None,
) -> dict:
    """Anonymized arena payload — never leaks generator identity or gold status. `references` is
    the subject's reference gallery (input photo + CC species photos — what the organism should
    look like), shown so voters can judge fidelity — not identity-revealing (shared across both
    candidates)."""
    from ..public_export import is_commercial_model

    # Both slots or neither — see _uniform_lod_urls. A pair is not exempt from the rule: it is
    # still two models judged side by side, and `?set=pair` remains reachable.
    lod_a, lod_b = _uniform_lod_urls([out_a, out_b])

    return {
        "comparison_id": comparison.id,
        "task": {
            "title": task.title,
            "prompt": task.prompt,
            "category": task.category.name,
            "references": references or [],
        },
        "criterion": {"slug": crit.slug, "name": crit.name},
        "a": {
            "url": _arena_asset_url(out_a),
            "lod_url": lod_a,
            "format": out_a.asset_format,
            "output_id": out_a.id,
            "machine_generated": is_commercial_model(out_a.source),
            "attribution": out_a.attribution or None,
        },
        "b": {
            "url": _arena_asset_url(out_b),
            "lod_url": lod_b,
            "format": out_b.asset_format,
            "output_id": out_b.id,
            "machine_generated": is_commercial_model(out_b.source),
            "attribution": out_b.attribution or None,
        },
    }


def _serialize_output(o: ModelOutput, lod_url: str | None) -> dict:
    """Anonymized per-output payload for the 4-up K-wise ballot — the SAME fields `_serialize`
    exposes for a single output (url/format/output_id + the AUP machine-generated label). Never
    leaks generator identity. machine_generated/attribution carry the mandatory AI-provenance
    label so a commercial-model output shows the same badge in the K-wise grid as in the pair
    view — the labeling requirement is display-posture-wide, not pair-only.

    `lod_url` is passed in rather than derived from `o`, and is deliberately REQUIRED. Deriving it
    here is what made the level of detail a property of the mesh — and therefore of the generator
    — instead of the ballot; see `_uniform_lod_urls`. Requiring the caller to resolve it across
    the whole ballot means a future ballot builder cannot silently reintroduce that confound: it
    gets a TypeError instead of a biased ballot.
    """
    from ..public_export import is_commercial_model

    return {
        "output_id": o.id,
        "url": _arena_asset_url(o),
        "lod_url": lod_url,
        "format": o.asset_format,
        "machine_generated": is_commercial_model(o.source),
        "attribution": o.attribution or None,
    }


def _vote_pool_predicate(db: Session):
    """The ONE definition of "not servable to a human voter", shared by the pairwise and k-wise
    builders and by pick_task/pick_pair within each.

    Both builders previously carried their own copy of this closure. That duplication is the
    exact shape of a bug already fixed once here: pick_task and pick_pair disagreeing about
    what was excluded made pick_task offer a task pick_pair then rejected, which surfaced as
    intermittent /api/next 404s. One definition, four call sites.

    Excluded:
      * raw reference scans — render as ugly point clouds and confound metric<->vote agreement
      * untextured (geometry-only) outputs — flat grey blobs lose votes for lack of texture,
        not shape. Both of the above stay on the Mode-B board.
      * outputs auto-hidden by the flag threshold, or gated by the admissibility rubric
        (structural u completeness u semantic-when-gating)
      * app-hidden generators — AgriGen internal testers, never in the pool anywhere
      * generators off the vote roster (config.ARENA_VOTE_PARADIGMS) — these are NOT hidden;
        they keep their pages and boards, they just don't spend scarce human votes
    """
    from .. import admissibility
    from ..sourcing import is_reference_scan, is_untextured_output

    # Precomputed ONCE per request so the per-output predicate stays O(1).
    gated = admissibility.non_admitted_output_ids(db)
    app_hidden_gids = service.app_hidden_generator_ids(db)
    off_roster_gids = service.vote_pool_excluded_generator_ids(db)

    def excluded(o) -> bool:
        return (
            is_reference_scan(o.source)
            or is_untextured_output(o)
            or o.hidden_at is not None
            or o.id in gated
            or o.generator_id in app_hidden_gids
            or o.generator_id in off_roster_gids
        )

    return excluded


def _criterion_or_default(db: Session, criterion_slug: str | None) -> Criterion:
    """Resolve a criterion slug, falling back to the default when absent or unknown."""
    if criterion_slug:
        crit = (
            db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
        )
        if crit is not None:
            return crit
    return _default_criterion(db)


def _build_gold_comparison(db: Session, session_id: str, crit: Criterion) -> dict | None:
    """Build a gold attention-check comparison (good vs decoy) with a known answer."""
    gp = matchmaking.pick_gold_pair(db)
    if gp is None:
        return None
    good = db.get(ModelOutput, gp.good_output_id)
    bad = db.get(ModelOutput, gp.bad_output_id)
    task = db.get(Task, gp.task_id)
    if good is None or bad is None or task is None:
        # Dangling gold pair: a referenced task/output was deleted (e.g. a data purge). The
        # create_all-only schema has no FK cascade, so guard rather than 500 the vote path.
        # Returning None makes the caller fall through to a real comparison.
        logger.warning("skipping dangling gold pair %s (task/output deleted)", gp.id)
        return None
    # Randomize which slot holds the good asset; gold_expected records it.
    if random.random() < 0.5:
        out_a, out_b, expected = good, bad, "a"
    else:
        out_a, out_b, expected = bad, good, "b"
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
        is_gold=True,
        gold_expected=expected,
    )
    db.add(comparison)
    db.commit()
    return _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )


def _build_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
    kingdom: str = "all",
) -> dict | None:
    """Pick a task + pair, persist it, return anon payload.

    Gold attention checks are NOT injected here — see `_build_ballot`, which owns that decision
    for every ballot shape.
    """
    crit = _criterion_or_default(db, criterion_slug)

    category_id = _resolve_category_id(db, category_slug)

    # Same predicate for task AND pair selection so pick_task never returns a task whose only
    # outputs pick_pair then excludes (which caused intermittent /api/next 404s).
    _vote_excluded = _vote_pool_predicate(db)

    # Pairings this session already voted on: the /api/vote guard 409s a re-vote of any of
    # them, so exclude them from BOTH task and pair selection (same set for both, mirroring
    # the _vote_excluded parity) — else a session dead-ends re-served an already-voted pair.
    voted_pairs = integrity.voted_pairs_for(db, session_id, crit.id)

    # Kingdom scoping: an explicit ?category= is always within a kingdom, so both filters apply
    # together — pick_task's category_ids kwarg takes precedence over category_id, so intersect
    # them into one set here rather than pass both.
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    task = matchmaking.pick_task(
        db,
        category_ids=_effective_category_ids(k_ids, category_id),
        exclude_fn=_vote_excluded,
        voted_pairs=voted_pairs,
    )
    if task is None:
        return None
    pair = matchmaking.pick_pair(db, task, exclude_fn=_vote_excluded, voted_pairs=voted_pairs)
    if pair is None:
        return None
    out_a, out_b = pair
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    return _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )


def _build_kwise_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
    kingdom: str = "all",
) -> dict | None:
    """Serve a 4-up K-ballot (no gold in kwise). Falls back to a pairwise comparison when no task
    has >=4 admitted same-paradigm fresh outputs."""
    import json as _json
    import random as _random

    crit = _criterion_or_default(db, criterion_slug)

    category_id = _resolve_category_id(db, category_slug)
    _vote_excluded = _vote_pool_predicate(db)

    seen = integrity.seen_quads_for(db, session_id, crit.id)
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = list(db.execute(stmt).scalars().all())
    _random.shuffle(tasks)
    for task in tasks:
        quad = matchmaking.pick_quad(db, task, exclude_fn=_vote_excluded, seen_quads=seen)
        if quad is None:
            continue
        ballot = KBallot(
            task_id=task.id,
            criterion_id=crit.id,
            session_id=session_id,
            output_ids_json=_json.dumps([o.id for o in quad]),
        )
        db.add(ballot)
        db.commit()
        return {
            "kind": "kwise",
            "ballot_id": ballot.id,
            "task": {
                "id": task.id,
                "title": task.title,
                "prompt": task.prompt,
                # Feeds the same category chip the 2-up ballot fills. Without it the k-wise
                # view had nothing to show there and displayed the literal word "K-wise".
                "category": task.category.name if task.category else "",
                # Same reference photos the 2-up ballot serves. Fidelity against a real organism
                # is what this board measures, so the reference has to be on screen for EVERY
                # ballot shape — omitting it here would have made the default ballot a beauty
                # contest the moment k-wise stopped being opt-in.
                "references": service.reference_images_for_task(db, task),
            },
            "criterion": {"slug": crit.slug, "name": crit.name},
            # Resolved across the WHOLE quad, not per output: if any of the four cannot be
            # served decimated, none of them are. See _uniform_lod_urls.
            "outputs": [
                _serialize_output(o, lod)
                for o, lod in zip(quad, _uniform_lod_urls(quad), strict=True)
            ],
        }
    # No quad anywhere → transparent pairwise fallback.
    return _build_comparison(db, session_id, criterion_slug, category_slug, kingdom=kingdom)


def _build_calibration_comparison(db: Session, session_id: str) -> dict | None:
    """Serve the next un-voted CalibrationPair for this session (with progress).

    Pairs whose task/criterion/outputs were deleted (dangling refs — the create_all schema
    has no FK cascade, so a data purge can leave them) are skipped: excluded from `total` AND
    never selected as target, so they can't 500 this path the way the gold path once did."""
    all_pairs = db.execute(select(CalibrationPair)).scalars().all()
    total = 0
    voted = 0
    target = None
    target_rows = None
    for cp in all_pairs:
        crit = db.get(Criterion, cp.criterion_id)
        task = db.get(Task, cp.task_id)
        out_a = db.get(ModelOutput, cp.output_a_id)
        out_b = db.get(ModelOutput, cp.output_b_id)
        if crit is None or task is None or out_a is None or out_b is None:
            continue  # dangling pair — unusable; exclude from progress + target selection
        if out_a.hidden_at is not None or out_b.hidden_at is not None:
            # A hidden member is withheld at /media (see _withheld), so the ballot would show a
            # 404 mesh — the gold bug of 2026-08-28, on the calibration path. Curated pairs are
            # deliberately NOT run through _vote_pool_predicate (roster/texture rules are for
            # the open pool); withholding is the one rule every serving path must share.
            continue
        total += 1
        already = integrity.already_voted_pair(
            db, session_id, cp.output_a_id, cp.output_b_id, cp.criterion_id
        )
        if already:
            voted += 1
        elif target is None:
            target = cp
            target_rows = (crit, task, out_a, out_b)
    progress = {"voted": voted, "total": total}
    if target_rows is None:
        return {"set": "calibration", "done": True, "progress": progress}

    crit, task, out_a, out_b = target_rows
    if random.random() < 0.5:
        out_a, out_b = out_b, out_a
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    payload = _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )
    payload["set"] = "calibration"
    payload["progress"] = progress
    return payload


#: `?set=` values that route to a builder other than the default. Anything else — including a
#: typo — falls through to the default, which always serves *something* rather than 404ing on a
#: malformed URL.
BALLOT_MODE_PAIR = "pair"
BALLOT_MODE_KWISE = "kwise"
BALLOT_MODE_CALIBRATION = "calibration"


def _build_ballot(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
    *,
    kingdom: str = "all",
    mode: str | None = None,
) -> dict | None:
    """The ONE definition of "what ballot comes next", shared by /api/next and the follow-up
    `next` embedded in the /api/vote and /api/kvote responses.

    The default is PAIRWISE. This reverses the k-wise default, which was argued on information
    yield: a pair yields one Bradley-Terry relation and a quad yields three, so serving a pair
    where a quad existed looked like discarding two thirds of what a voter offered. Two
    corrections and one observation overturned that:

    * The 4-up ballot collects a single best-of pick, not a ranking, so a quad says nothing
      about how the three losers rank against each other. The comparison at equal mesh cost is
      one quad (4 meshes, 3 relations) against two pairs (4 meshes, 2 relations) — 1.5x per
      mesh delivered, not 3x per ballot.
    * A quad is roughly twice the ballot bytes of a pair, and since fidelity tier became a
      property of the whole ballot, LOD eligibility multiplies across slots: ~33% per-output
      coverage leaves 0.33^4 (~1.4%) of quads eligible against 0.33^2 (~11%) of pairs.
    * A voter using the live arena reported the 4-up ballot as overwhelming. COMPLETED ballots,
      not relations per ballot, are the scarce input — a shape that costs completions loses
      even at a favourable relation ratio.

    `?set=kwise` is the explicit opt-in and keeps the 4-up builder, endpoint, grid and reveal
    reachable rather than dead code. `?set=pair` remains accepted (it lands on the default) so
    links already in the wild keep working.

    Centralizing this routing is what keeps EITHER default safe. /api/vote once built its
    follow-up with the pairwise builder unconditionally, which pinned any voter who landed on a
    pair to pairs for the rest of the session. That trap is not a property of which shape is
    default — it is a property of a follow-up that ignores the mode. Flipping the default just
    moves the exposed side of it to k-wise, so the mode threads through every caller and both
    directions are pinned by tests.
    """
    if mode == BALLOT_MODE_CALIBRATION:
        # A calibration set is a fixed, fully-enumerated list of pairs; a gold check inserted
        # into it would not belong to the set and would break its progress count.
        return _build_calibration_comparison(db, session_id)

    # Gold attention checks are a property of serving a ballot to a human, NOT of the pairwise
    # builder that used to host them. While pairwise was the default entry point those two were
    # indistinguishable; the moment the default changed, gold would have gone dark — the k-wise
    # builder reaches `_build_comparison` only as a fallback, and most tasks can fill a quad, so
    # the fallback (and with it every attention check) would almost never fire. Hoisting the
    # injection to the routing point makes the check independent of which ballot shape follows.
    # Whether to check is a question about THIS voter, not an independent coin per ballot. A
    # flat rate leaves coverage to chance — (1 - rate)^n of sessions are never measured at all —
    # so the decision reads how many ballots this session has gone unchecked and whether we have
    # ever gotten a reading on them. See integrity.should_serve_gold.
    _vs = db.get(VoterSession, session_id)
    if integrity.should_serve_gold(
        integrity.ballots_since_last_gold(db, session_id),
        _vs.gold_seen if _vs is not None else 0,
    ):
        gold = _build_gold_comparison(db, session_id, _criterion_or_default(db, criterion_slug))
        if gold is not None:
            return gold
        # No gold pair available (none configured, or the pair was purged). Falling through
        # serves a real ballot, and because the counter only restarts on a check that was
        # actually SERVED, the deadline stays due and the next ballot tries again.

    if mode == BALLOT_MODE_KWISE:
        return _build_kwise_comparison(
            db, session_id, criterion_slug, category_slug, kingdom=kingdom
        )
    # BALLOT_MODE_PAIR lands here too, as does an unrecognized `?set=` value.
    return _build_comparison(db, session_id, criterion_slug, category_slug, kingdom=kingdom)


@router.get("/arena", response_class=HTMLResponse)
def arena_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    # Tag a recruited arrival before any ballot is served, so every vote this session casts is
    # attributable to the cohort. No-op without `?c=<label>`, so ambient traffic is untouched
    # and creates no VoterSession row it would not otherwise have created.
    study.stamp_cohort(
        db,
        request.state.session_id,
        study.campaign_label(request.query_params.get(study.CAMPAIGN_PARAM)),
    )
    ctx = _hero_stats(db)
    ctx["study"] = (
        study.completion_state(db, request.state.session_id) if study.study_enabled() else None
    )
    return templates.TemplateResponse(request, "arena.html", ctx)


def _study_progress(request: Request, db: Session) -> dict | None:
    """Ballot progress to ride back on a vote response, or None for an ordinary voter.

    Returned per-vote so the arena's counter can update without a page load. Gated on the voter
    actually being in a cohort: an ambient voter's client must not be told it is partway through
    a paid task. Carries no credential by construction — see `study.progress`.
    """
    if not study.study_enabled():
        return None
    state = study.progress(db, request.state.session_id)
    return state if state.get("cohort") else None


@router.get("/study", response_class=HTMLResponse)
def study_page(request: Request, db: Session = Depends(get_db)):
    """Progress page for a recruited participant, carrying the completion code once earned.

    404s unless a study is actually configured: the page invites someone to finish a paid task,
    and an instance not running one must not make that offer.
    """
    if not study.study_enabled():
        raise HTTPException(status_code=404, detail="no study is running")
    return templates.TemplateResponse(
        request, "study.html", {"study": study.completion_state(db, request.state.session_id)}
    )


@router.get("/api/meta")
def api_meta(request: Request, db: Session = Depends(get_db)):
    """Categories + criteria for populating arena/leaderboard selectors."""
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    # Scope the category selector to the active kingdom so it never offers a category the
    # arena pool wouldn't actually serve (kingdom=all -> k_ids is None -> no filtering).
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    if k_ids is not None:
        cats = [c for c in cats if c.id in k_ids]
    # `coming_soon`: a category with no tasks is a roadmap placeholder (e.g. Fungi/Animals/
    # Microbes) — it self-activates the moment its first task is added. No schema flag.
    return {
        "categories": [{"slug": c.slug, "name": c.name, "coming_soon": not c.tasks} for c in cats],
        "criteria": [{"slug": c.slug, "name": c.name} for c in crits],
    }


@router.get("/api/next")
def api_next(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    mode: str | None = Query(default=None, alias="set"),
):
    if not integrity.check_next_rate_limit(request.state.client_ip):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    payload = _build_ballot(
        db,
        request.state.session_id,
        criterion,
        category,
        kingdom=request.state.kingdom,
        mode=mode,
    )
    if payload is None:
        return JSONResponse({"error": "no-comparisons-available"}, status_code=404)
    return payload


@router.post("/api/vote")
def api_vote(
    vote_in: VoteIn,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    mode: str | None = Query(default=None, alias="set"),
    x_captcha_token: str | None = Header(default=None),
):
    sid = request.state.session_id

    # 1. Rate limiting — per session AND per IP (the IP layer caps cookie-reset farming).
    #    Cheapest check first: the captcha gate below may make a blocking siteverify call,
    #    so it must never be reachable by a request the limiter would have refused.
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.check_ip_rate_limit(request.state.client_ip):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    # 2. Human verification (no-op unless REQUIRE_CAPTCHA is enabled).
    if not integrity.captcha_ok_for_session(db, sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")

    comparison = db.get(Comparison, vote_in.comparison_id)
    if comparison is None:
        raise HTTPException(404, "Unknown comparison")
    if comparison.session_id != sid:
        # Ballots are served to ONE session. Ids are sequential, so without this a script
        # could consume other voters' ballots and have its gold answers scored against the
        # wrong session.
        raise HTTPException(403, "Comparison was not served to this session")
    if comparison.vote is not None:
        raise HTTPException(409, "Comparison already voted")
    # 3. Dedup: a session may not re-vote the same (non-gold) pairing.
    if not comparison.is_gold and integrity.already_voted_pair(
        db, sid, comparison.output_a_id, comparison.output_b_id, comparison.criterion_id
    ):
        raise HTTPException(409, "You have already voted on this pairing")

    vote = Vote(comparison_id=comparison.id, winner=vote_in.winner, session_id=sid)
    db.add(vote)
    try:
        db.flush()
    except IntegrityError:
        # Two requests for the same ballot passed the `comparison.vote is None` check together;
        # Vote.comparison_id is UNIQUE, so the loser surfaces here rather than as a 500.
        db.rollback()
        raise HTTPException(409, "Comparison already voted")
    integrity.note_vote(db, sid)

    if comparison.is_gold:
        # Attention check: update trust, do NOT feed rankings. A non-binary answer abstains
        # rather than failing — see integrity.gold_outcome for why that is not the same trait.
        outcome = integrity.gold_outcome(vote_in.winner, comparison.gold_expected)
        if outcome is not None:
            integrity.record_gold_outcome(db, sid, outcome)
    else:
        service.apply_vote(db, vote)
    db.commit()
    # Keep the same criterion/category filter (+ active kingdom, + ballot mode) for the follow-up.
    # Routed through _build_ballot so the follow-up is whatever /api/next would serve for these
    # same params — a pairwise vote can hand back a k-wise ballot, which is the point.
    nxt = _build_ballot(db, sid, criterion, category, kingdom=request.state.kingdom, mode=mode)

    # Post-vote reveal (Feature C): real generator names for the just-voted pair, ONLY for
    # non-gold comparisons — gold is an attention-check decoy, so revealing it would leak the
    # answer. Purely additive: never affects vote recording, dedup, or `next` above.
    reveal = None
    if not comparison.is_gold:
        out_a = db.get(ModelOutput, comparison.output_a_id)
        out_b = db.get(ModelOutput, comparison.output_b_id)
        names = service.generator_display_names(db)

        # Defensive: an output deleted between comparison-build and vote would be None here;
        # never 500 the (already-committed) vote's reveal — mirror the kvote guard below.
        def _rev_side(o: ModelOutput | None) -> dict:
            return {"name": names.get(o.generator_id, "Unknown") if o else "Unknown"}

        reveal = {"a": _rev_side(out_a), "b": _rev_side(out_b), "winner": vote_in.winner}
    return {"status": "ok", "next": nxt, "reveal": reveal, "study": _study_progress(request, db)}


@router.post("/api/kvote")
def api_kvote(
    kvote_in: KVoteIn,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    mode: str | None = Query(default=None, alias="set"),
    x_captcha_token: str | None = Header(default=None),
):
    import json as _json

    sid = request.state.session_id
    # Limiter first, captcha second — same order and reason as /api/vote.
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.check_ip_rate_limit(request.state.client_ip):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.captcha_ok_for_session(db, sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    ballot = db.get(KBallot, kvote_in.ballot_id)
    if ballot is None:
        raise HTTPException(404, "Unknown ballot")
    if ballot.session_id != sid:
        raise HTTPException(403, "Ballot was not served to this session")
    if ballot.resolved:
        raise HTTPException(409, "Ballot already resolved")
    ids = _json.loads(ballot.output_ids_json)
    if kvote_in.best_output_id is not None and kvote_in.best_output_id not in ids:
        raise HTTPException(400, "best_output_id not among the shown outputs")
    service.resolve_kballot(db, ballot, kvote_in.best_output_id, sid)
    integrity.note_vote(db, sid)  # ONE rate-accounting per ballot, not per derived vote
    db.commit()
    nxt = _build_ballot(db, sid, criterion, category, kingdom=request.state.kingdom, mode=mode)

    # Post-vote reveal (Feature C): real generator names for every output shown in the ballot +
    # which one was picked, so the grid can label each card. K-wise never serves gold (see
    # _build_kwise_comparison docstring), so no omission case is needed here.
    names = service.generator_display_names(db)
    reveal_outputs = []
    for oid in ids:
        out = db.get(ModelOutput, oid)
        if out is None:
            continue  # defensive: dangling id, shouldn't happen but never 500 the reveal
        reveal_outputs.append({"output_id": oid, "name": names.get(out.generator_id, "Unknown")})
    reveal = {"outputs": reveal_outputs, "best_output_id": kvote_in.best_output_id}
    return {"status": "ok", "next": nxt, "reveal": reveal}


@router.post("/api/flag", dependencies=[Depends(require_internal_pages)])
def api_flag(flag_in: FlagIn, request: Request, db: Session = Depends(get_db)):
    """Report an output as not the organism / failed. CURATOR-ONLY: gated to the internal instance
    (the public deploy 404s here and renders no flag button), so it hides at the first flag
    (FLAG_HIDE_THRESHOLD default 1). Rate-limited; one flag per session per output; never advances."""
    from .. import flags

    sid = request.state.session_id
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if db.get(ModelOutput, flag_in.output_id) is None:
        raise HTTPException(404, "Unknown output")
    hidden, count = flags.record_flag(
        db, flag_in.output_id, sid, flag_in.reason, config.FLAG_HIDE_THRESHOLD
    )
    db.commit()
    return {"status": "ok", "hidden": hidden, "flags": count}


#: How long a voter's browser may reuse an arena mesh without asking again.
#:
#: These responses previously carried NO cache headers at all, so every ballot re-downloaded
#: every mesh — including a model the voter had already seen in an earlier ballot. On Fast 4G a
#: 4-up ballot is a measured 8.0 MB / 20.9 s (2026-07-31), so that is the dominant cost of
#: voting on a phone.
#:
#: An hour rather than a year, and deliberately NOT `immutable`: these URLs are keyed by output
#: id, not by content, and a release rewrites blobs IN PLACE — the 2026-07-31 recompression
#: replaced 581 objects at their existing keys. `immutable` would pin voters to superseded
#: geometry with no way to correct it. An hour covers a voting session; the ETag makes anything
#: past it a 304 instead of another download.
MEDIA_MAX_AGE = 3600


def _media_headers(etag: str) -> dict[str, str]:
    return {"Cache-Control": f"public, max-age={MEDIA_MAX_AGE}", "ETag": etag}


def _withheld(o: ModelOutput, token: str | None) -> bool:
    """Is this output hidden from someone holding no admin token?

    Hiding an output used to stop it being VOTED on and nothing else — the media routes resolved
    the row, found a blob, and served it. Measured on taxon3d.org 2026-08-11: all 14 hidden
    outputs returned 200, twelve of them hidden as the LICENSING control because their input
    photos have no provenance sidecar. Withholding that is not enforced at the byte-serving
    route is not withholding.

    Admins keep access because moderation has to be able to look at what it just hid, and
    /admin/moderation renders these same assets; the bypass is the admin cookie that
    `POST /admin/login` sets, never a query parameter (a URL is not a place for a secret).
    """
    if o.hidden_at is None:
        return False
    return not token or not hmac.compare_digest(token, config.ADMIN_TOKEN)


@router.get("/media/o/{output_id}.lod.{ext}")
def media_asset_lod(
    output_id: int,
    ext: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """The low-detail companion mesh, when the release pipeline produced one.

    DECLARED BEFORE `media_asset`: Starlette matches routes in registration order and `{ext}` is
    happy to swallow `lod.glb`, so registering the generic route first would send every LOD
    request to the full mesh — a silent no-op that looks exactly like a working feature while
    every voter still downloads the thing the LOD exists to avoid.

    404 rather than falling back to the full mesh. A fallback would make a missing LOD invisible:
    the ballot would still work, nobody would be faster, and no signal would say so. The client
    only ever asks after `lod_url` appeared in its payload, so a 404 here means the bundle and the
    database disagree — which is worth surfacing, not smoothing over.
    """
    o = db.get(ModelOutput, output_id)
    # A withheld output answers exactly like one that never existed: these URLs are deliberately
    # opaque and output-scoped, so 403 would confirm the id is real.
    if o is None or _withheld(o, request.cookies.get(ADMIN_COOKIE)):
        raise HTTPException(404, "Unknown output")
    rel = mesh_lod.lod_path(o.asset_path)
    if not storage.exists(rel):
        raise HTTPException(404, "No LOD for this output")
    ctype = content_type_for(rel)
    if getattr(storage, "remote", False):
        body = storage.read(rel)
        etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=_media_headers(etag))
        return Response(content=body, media_type=ctype, headers=_media_headers(etag))
    path = config.ASSET_DIR / rel
    if not path.is_file():
        raise HTTPException(404, "No LOD for this output")
    st = path.stat()
    etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_media_headers(etag))
    return FileResponse(path, media_type=ctype, headers=_media_headers(etag))


@router.get("/media/o/{output_id}.{ext}")
def media_asset(
    output_id: int,
    ext: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Resolve an opaque, output-scoped asset URL (emitted by _arena_asset_url) back to the real
    file, so the anonymized arena never exposes the descriptive asset_path. Serves by output id;
    `ext` is cosmetic (helps 3D viewers). Streams through the app on remote (S3) storage so the
    object key — which can encode identity — is never revealed to the client either."""
    o = db.get(ModelOutput, output_id)
    if o is None or _withheld(o, request.cookies.get(ADMIN_COOKIE)):
        raise HTTPException(404, "Unknown output")
    ctype = content_type_for(o.asset_path)
    if getattr(storage, "remote", False):
        body = storage.read(o.asset_path)
        # Hash the bytes we already had to fetch. Deriving the validator from the output id
        # instead would keep matching after a re-export replaced the blob, and voters would hold
        # the superseded mesh until max-age expired.
        etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=_media_headers(etag))
        return Response(content=body, media_type=ctype, headers=_media_headers(etag))
    path = config.ASSET_DIR / o.asset_path
    if not path.is_file():
        raise HTTPException(404, "Asset missing")
    # Local assets are the ORIGINAL uncompressed files (up to 59 MB before the release pipeline
    # touches them), so they are stat-keyed rather than hashed — reading each one into memory per
    # request to compute a digest would trade the bug for a worse one.
    st = path.stat()
    etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=_media_headers(etag))
    return FileResponse(path, media_type=ctype, headers=_media_headers(etag))
