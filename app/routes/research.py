"""Research/analytics JSON, the recon benchmark, the ingestion API and the submission queue."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, difficulty, fidelity, ingest, integrity, kingdoms, service, submissions
from ..database import get_db
from ..models import Task
from ..schemas import CategoryIn, GeneratorIn, TaskIn
from ..web_common import (
    _resolve_category_id,
    _roadmap_or_none,
    require_internal_pages,
    storage,
    templates,
)
from .admin import require_admin_header

router = APIRouter()


@router.get("/api/coverage.json")
def api_coverage(db: Session = Depends(get_db)):
    return service.coverage_summary(db)


@router.get("/api/procedural.json", dependencies=[Depends(require_internal_pages)])
def api_procedural(db: Session = Depends(get_db)):
    return service.procedural_scorecard(db)


@router.get("/api/completeness.json", dependencies=[Depends(require_internal_pages)])
def api_completeness(db: Session = Depends(get_db)):
    return service.completeness_rows(db)


@router.get("/api/dgen.json", dependencies=[Depends(require_internal_pages)])
def api_dgen(db: Session = Depends(get_db)):
    return service.dgen_trajectory(db)


@router.get("/api/fidelity.json", dependencies=[Depends(require_internal_pages)])
def api_fidelity(db: Session = Depends(get_db)):
    return fidelity.fidelity_scorecard(db)


# --------------------------------------------------------- significance + bias


@router.get("/api/significance", dependencies=[Depends(require_internal_pages)])
def api_significance(
    db: Session = Depends(get_db), criterion: str = "overall", category: str = "all"
):
    return service.compute_significance(db, criterion, _resolve_category_id(db, category))


@router.get("/api/bias", dependencies=[Depends(require_internal_pages)])
def api_bias(db: Session = Depends(get_db)):
    return service.compute_bias(db)


def _default_benchmark_task_id(db: Session, category_ids: set[int] | None = None) -> int | None:
    """Return the first task_id that has a ReconTask row AND at least one ok Metric.

    Falls back to tasks[0].id if no scored task exists yet. Returns None if no tasks.
    Used by benchmark_page to avoid defaulting to an unscored task (P1-3 fix).
    `category_ids` (when given) restricts candidates to the active kingdom, mirroring the
    picker list built by benchmark_page.
    """
    from ..models import Metric, ReconTask, Task

    stmt = select(Task)
    if category_ids is not None:
        stmt = stmt.where(Task.category_id.in_(category_ids))
    tasks = db.execute(stmt).scalars().all()
    if not tasks:
        return None
    for t in tasks:
        rt = db.execute(select(ReconTask).where(ReconTask.task_id == t.id)).scalars().first()
        if rt is None:
            continue
        out_ids = [o.id for o in t.outputs if not o.is_gold]
        if not out_ids:
            continue
        has_score = (
            db.execute(select(Metric).where(Metric.output_id.in_(out_ids), Metric.status == "ok"))
            .scalars()
            .first()
        )
        if has_score:
            return t.id
    return tasks[0].id


@router.get(
    "/benchmark",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def benchmark_page(request: Request, db: Session = Depends(get_db), task_id: int | None = None):
    from .. import recon_service
    from ..models import Task

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    stmt = select(Task)
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = db.execute(stmt).scalars().all()
    if task_id is None and tasks:
        task_id = _default_benchmark_task_id(db, category_ids=k_ids)
    board = recon_service.recon_method_leaderboard(db, task_id) if task_id else []
    confounds = recon_service.recon_confounds(db, task_id) if task_id else None
    agree = (
        recon_service.agreement(db, task_id)
        if task_id
        else {"status": "empty", "spearman": None, "rows": []}
    )
    viewer_outputs = recon_service.recon_outputs_for_task(db, task_id) if task_id else []
    reference = recon_service.reference_for_task(db, task_id) if task_id else None
    current = db.get(Task, task_id) if task_id else None
    vote_category = current.category.slug if current and current.category else None
    cross_species = recon_service.cross_species_summary(db)
    return templates.TemplateResponse(
        request,
        "benchmark.html",
        {
            "tasks": tasks,
            "task_id": task_id,
            "board": board,
            "confounds": confounds,
            "agree": agree,
            "viewer_outputs": viewer_outputs,
            "reference": reference,
            "vote_category": vote_category,
            "cross_species": cross_species,
        },
    )


@router.get("/api/benchmark", dependencies=[Depends(require_internal_pages)])
def api_benchmark(db: Session = Depends(get_db), task_id: int | None = None):
    from .. import recon_service

    if task_id is None:
        return JSONResponse({"error": "task_id required"}, status_code=400)
    return JSONResponse(
        {
            "leaderboard": recon_service.recon_method_leaderboard(db, task_id),
            "per_output": recon_service.recon_leaderboard(db, task_id),
            "confounds": recon_service.recon_confounds(db, task_id),
            "agreement": recon_service.agreement(db, task_id),
        }
    )


# ------------------------------------------------------------------ data export


@router.get("/api/export.json")
def export_dataset(request: Request, db: Session = Depends(get_db)):
    """Reproducible research export: every decided comparison with full provenance.

    Generators are revealed here (post-hoc), enabling offline ranking studies. Scoped by the
    active kingdom (query param / cookie), matching every other data page (`all` == unfiltered).
    """
    from .. import dataset as dataset_mod

    return dataset_mod.build_preference_records(db, kingdom=request.state.kingdom)


@router.get("/api/difficulty.json", dependencies=[Depends(require_internal_pages)])
def api_difficulty(db: Session = Depends(get_db)):
    """Per-tier objective scorecard (× generator and × paradigm) over existing metrics, plus the
    recon-reliability triage flags (taxa whose recon completeness is far below text→3D)."""
    from ..completeness import recon_reliability_flags

    return {
        "scorecard": difficulty.tier_scorecard(db),
        "paradigm_grid": difficulty.paradigm_tier_scorecard(db),
        "recon_reliability": recon_reliability_flags(db),
    }


# ----------------------------------------------------------- Mode-C trait scoring


@router.get("/api/trait_scores.json", dependencies=[Depends(require_internal_pages)])
def api_trait_scores(db: Session = Depends(get_db)):
    """Mode-C botanical-accuracy: generator leaderboard + per-output scores."""
    from ..models import TraitScore

    outputs = [
        {
            "output_id": ts.output_id,
            "botanical_accuracy": ts.botanical_accuracy,
            "n_scored": ts.n_scored,
            "n_total": ts.n_total,
        }
        for ts in db.execute(select(TraitScore)).scalars()
    ]
    return {"generators": service.trait_leaderboard(db), "outputs": outputs}


@router.get("/api/traits.json", dependencies=[Depends(require_internal_pages)])
def api_traits(db: Session = Depends(get_db)):
    """The literature-sourced trait rubrics (one per taxon/task)."""
    from ..models import TraitRubric

    rubrics = []
    for r in db.execute(select(TraitRubric)).scalars():
        try:
            traits = json.loads(r.traits_json or "[]")
        except json.JSONDecodeError:
            traits = []
        rubrics.append({"taxon": r.taxon, "task_id": r.task_id, "traits": traits})
    return {"rubrics": rubrics}


# ----------------------------------------------------------- ingestion API (JSON)
# Programmatic surface for generator pipelines. Auth via the X-Admin-Token header.


@router.get("/api/tasks")
def api_list_tasks(db: Session = Depends(get_db)):
    """Discover task ids/slugs so a pipeline knows where to register outputs."""
    out = []
    for t in db.execute(select(Task)).scalars().all():
        out.append(
            {
                "id": t.id,
                "title": t.title,
                "category": t.category.slug,
                "n_outputs": len(t.outputs),
                "active": t.active,
            }
        )
    return {"tasks": out}


@router.post("/api/categories", dependencies=[Depends(require_admin_header)])
def api_upsert_category(body: CategoryIn, db: Session = Depends(get_db)):
    cat = ingest.upsert_category(db, body.slug, body.name, body.description)
    db.commit()
    return {"id": cat.id, "slug": cat.slug, "name": cat.name}


@router.post("/api/generators", dependencies=[Depends(require_admin_header)])
def api_upsert_generator(body: GeneratorIn, db: Session = Depends(get_db)):
    gen = ingest.upsert_generator(db, body.slug, body.name, body.kind, body.description)
    db.commit()
    return {"id": gen.id, "slug": gen.slug, "name": gen.name, "kind": gen.kind}


@router.post("/api/tasks", dependencies=[Depends(require_admin_header)])
def api_create_task(body: TaskIn, db: Session = Depends(get_db)):
    try:
        task = ingest.create_task(db, body.category, body.title, body.prompt, body.criteria_note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"id": task.id, "title": task.title, "category": task.category.slug}


@router.post("/api/outputs", dependencies=[Depends(require_admin_header)])
async def api_register_output(
    task_id: int = Form(...),
    generator_slug: str = Form(...),
    generator_name: str | None = Form(default=None),
    title: str = Form(default=""),
    meta: str = Form(default="{}"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Register a generator's baked 3D asset for a task (validated + deduped)."""
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    try:
        meta_dict = json.loads(meta) if meta else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"meta must be valid JSON: {exc}") from exc
    try:
        output, created = ingest.register_output(
            db,
            task_id=task_id,
            generator_slug=generator_slug,
            data=await file.read(),
            ext=ext,
            title=title,
            meta=meta_dict,
            generator_name=generator_name,
        )
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "id": output.id,
        "created": created,  # False if an identical asset was already registered
        "task_id": output.task_id,
        "generator_id": output.generator_id,
        "asset_url": storage.url_for(output.asset_path),
        "format": output.asset_format,
        "meta": json.loads(output.meta_json),
    }


# --------------------------------------------------- community submission queue


@router.get("/submit", response_class=HTMLResponse)
def submit_page(request: Request, db: Session = Depends(get_db)):
    tasks = db.execute(select(Task).where(Task.active.is_(True))).scalars().all()
    return templates.TemplateResponse(
        request,
        "submit.html",
        {"tasks": [{"id": t.id, "title": t.title, "category": t.category.name} for t in tasks]},
    )


@router.post("/api/submit")
async def api_submit(
    request: Request,
    task_id: int = Form(...),
    generator_slug: str = Form(...),
    generator_name: str = Form(default=""),
    title: str = Form(default=""),
    submitter: str = Form(default=""),
    meta: str = Form(default="{}"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_captcha_token: str | None = Header(default=None),
):
    """Public: queue a community 3D output for moderation (rate-limited, validated)."""
    sid = request.state.session_id
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.captcha_ok_for_session(db, sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    # Read one byte past the cap: the whole body would otherwise sit in RAM before validation.
    data = await file.read(config.SUBMIT_MAX_BYTES + 1)
    if len(data) > config.SUBMIT_MAX_BYTES:
        raise HTTPException(413, f"Upload exceeds {config.SUBMIT_MAX_BYTES} bytes")
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    try:
        meta_dict = json.loads(meta) if meta else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"meta must be valid JSON: {exc}") from exc
    try:
        sub = submissions.create_submission(
            db,
            task_id=task_id,
            generator_slug=generator_slug,
            data=data,
            ext=ext,
            title=title,
            meta=meta_dict,
            submitter=submitter,
            session_id=sid,
            generator_name=generator_name,
        )
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"status": "pending", "submission_id": sub.id}


@router.get("/api/submissions", dependencies=[Depends(require_admin_header)])
def api_submissions(db: Session = Depends(get_db), status: str | None = None):
    subs = submissions.list_submissions(db, status)
    return {
        "submissions": [
            {
                "id": s.id,
                "task_id": s.task_id,
                "generator_slug": s.generator_slug,
                "title": s.title,
                "status": s.status,
                "submitter": s.submitter,
                "asset_url": storage.url_for(s.asset_path),
                "format": s.asset_format,
            }
            for s in subs
        ]
    }
