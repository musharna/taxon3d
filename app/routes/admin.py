"""Admin surface: the moderation cookie, the token dependencies, and every /admin route."""

from __future__ import annotations

import hmac
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, ingest, service, submissions
from ..database import get_db
from ..models import Category, Criterion, Generator, ModelOutput, OutputFlag, Task
from ..models import _utcnow as _models_utcnow
from ..web_common import storage, templates

router = APIRouter()


#: Set by the moderation page after a successful `?token=` visit so that the forms and redirects
#: on it no longer have to carry the secret in URLs, which land in edge/access logs, browser
#: history and Referer headers. HttpOnly; the browser sends it, scripts cannot read it.
ADMIN_COOKIE = "bio3d_admin"


def _require_admin(token: str | None) -> None:
    if not token or not hmac.compare_digest(token, config.ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def _admin_token_of(request: Request, token: str | None) -> str | None:
    """The token a request presents: explicit (query/form) first, else the admin cookie."""
    return token or request.cookies.get(ADMIN_COOKIE)


def require_admin_header(x_admin_token: str | None = Header(default=None)) -> None:
    """Dependency for programmatic JSON/upload endpoints (token via X-Admin-Token)."""
    _require_admin(x_admin_token)


def require_admin_cookie(request: Request) -> None:
    """Dependency for admin HTML pages: the admin cookie set by `POST /admin/login`, and nothing
    else. These GET pages render admin/moderation data (incl. submitter PII + un-vetted asset
    URLs), so they must not be world-readable even though the mutating POSTs are token-gated.

    Until 2026-09-06 these pages also took `?token=`. A secret in a URL is a secret in browser
    history, in the Referer header of every asset the page loads, and in every proxy log between
    the operator and this process; the cookie path already existed, so the query form is gone."""
    _require_admin(request.cookies.get(ADMIN_COOKIE))


_ADMIN_NEXT_ALLOWED = ("/admin", "/admin/moderation")


def _set_admin_cookie(resp: Response, request: Request) -> None:
    resp.set_cookie(
        ADMIN_COOKIE,
        config.ADMIN_TOKEN,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=8 * 3600,
    )


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, next: str = "/admin"):
    return templates.TemplateResponse(request, "admin_login.html", {"next": next})


@router.post("/admin/login")
def admin_login(request: Request, token: str = Form(...), next: str = Form("/admin")):
    """Exchange the admin token, sent once in a POST body, for the admin cookie. `next` is
    restricted to admin paths: an open redirect here would turn a phishing link into a bounce
    through a logged-in operator."""
    _require_admin(token)
    target = next if next in _ADMIN_NEXT_ALLOWED else "/admin"
    resp = RedirectResponse(target, status_code=303)
    _set_admin_cookie(resp, request)
    return resp


# ------------------------------------------------------------------------ admin


@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin_cookie)])
def admin_page(request: Request, db: Session = Depends(get_db)):
    ctx = {
        "categories": db.execute(select(Category)).scalars().all(),
        "criteria": db.execute(select(Criterion)).scalars().all(),
        "generators": db.execute(select(Generator)).scalars().all(),
        "tasks": db.execute(select(Task)).scalars().all(),
    }
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.post("/admin/category")
def admin_create_category(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Category(slug=slug, name=name, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/criterion")
def admin_create_criterion(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Criterion(slug=slug, name=name, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/generator")
def admin_create_generator(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    kind: str = Form("model"),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Generator(slug=slug, name=name, kind=kind, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/task")
def admin_create_task(
    token: str = Form(...),
    category_id: int = Form(...),
    title: str = Form(...),
    prompt: str = Form(...),
    criteria_note: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Task(category_id=category_id, title=title, prompt=prompt, criteria_note=criteria_note))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/output")
async def admin_upload_output(
    token: str = Form(...),
    task_id: int = Form(...),
    generator_id: int = Form(...),
    title: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    rel = str(Path("uploads") / f"{uuid.uuid4().hex}.{ext}").replace("\\", "/")
    storage.save(rel, await file.read())
    db.add(
        ModelOutput(
            task_id=task_id,
            generator_id=generator_id,
            title=title,
            asset_path=rel,
            asset_format=ext,
            meta_json=json.dumps({"uploaded": True, "filename": file.filename}),
        )
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/recompute")
def admin_recompute(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    detail = service.recompute_all(db)
    return JSONResponse({"status": "recomputed", "detail": detail})


@router.post("/admin/recompute_judge")
def admin_recompute_judge(
    token: str = Form(...), view_condition: str = Form("multi4"), db: Session = Depends(get_db)
):
    _require_admin(token)
    detail = service.recompute_judge_all(db, view_condition=view_condition)
    return JSONResponse({"status": "recomputed", "detail": detail})


@router.post("/admin/rescore")
def admin_rescore(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    from .. import recon_service, structure_service

    detail = recon_service.rescore_all(db)
    # Second Mode-B axis: organ-structure fidelity for structure-known (procedural) outputs.
    organ_detail = structure_service.rescore_all(db)
    return JSONResponse({"status": "rescored", "detail": detail, "organ": organ_detail})


@router.get(
    "/admin/moderation",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_cookie)],
)
def moderation_page(request: Request, db: Session = Depends(get_db)):
    pending = submissions.list_submissions(db, status="pending")
    rows = []
    for s in pending:
        task = db.get(Task, s.task_id)
        rows.append(
            {
                "id": s.id,
                "task": task.title if task else f"#{s.task_id}",
                "generator_slug": s.generator_slug,
                "generator_name": s.generator_name,
                "title": s.title,
                "submitter": s.submitter,
                "asset_url": storage.url_for(s.asset_path),
                "format": s.asset_format,
            }
        )

    from .. import flags as _flags

    flagged_outputs = (
        db.query(ModelOutput)
        .join(OutputFlag, OutputFlag.output_id == ModelOutput.id)
        .distinct()
        .all()
    )
    flagged = []
    for o in flagged_outputs:
        flagged.append(
            {
                "id": o.id,
                "asset_url": storage.url_for(o.asset_path),
                "task": o.task.title if o.task else f"#{o.task_id}",
                "flags": _flags.distinct_flag_count(db, o.id),
                "hidden": o.hidden_at is not None,
            }
        )
    resp = templates.TemplateResponse(
        request, "moderation.html", {"pending": rows, "flagged": flagged}
    )
    return resp


@router.post("/admin/submissions/{submission_id}/approve")
def admin_approve(
    submission_id: int,
    request: Request,
    token: str = Form(default=""),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _require_admin(_admin_token_of(request, token))
    try:
        submissions.approve(db, submission_id, note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse("/admin/moderation", status_code=303)


@router.post("/admin/submissions/{submission_id}/reject")
def admin_reject(
    submission_id: int,
    request: Request,
    token: str = Form(default=""),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _require_admin(_admin_token_of(request, token))
    try:
        submissions.reject(db, submission_id, note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse("/admin/moderation", status_code=303)


@router.post("/admin/outputs/{output_id}/hide")
def admin_hide_output(
    output_id: int, request: Request, token: str = Form(default=""), db: Session = Depends(get_db)
):
    _require_admin(_admin_token_of(request, token))
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    if out.hidden_at is None:
        out.hidden_at = _models_utcnow()
    db.commit()
    return RedirectResponse("/admin/moderation", status_code=303)


@router.post("/admin/outputs/{output_id}/restore")
def admin_restore_output(
    output_id: int, request: Request, token: str = Form(default=""), db: Session = Depends(get_db)
):
    _require_admin(_admin_token_of(request, token))
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    out.hidden_at = None
    db.commit()
    return RedirectResponse("/admin/moderation", status_code=303)
