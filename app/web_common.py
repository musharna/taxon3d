"""Shared web plumbing for app.main and the routers under app.routes.

Everything here used to live in app.main. It is the set of things more than one router needs
(the template environment, the storage backend, kingdom/category scoping, the roadmap gate, the
hero counts, the internal-pages dependency). Routers import from here and never from app.main,
so the dependency graph stays acyclic: app.main -> app.routes.* -> app.web_common.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, kingdoms, matchmaking, service
from .models import Category, Generator, ModelOutput, Task
from .storage import get_storage

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_STATIC_DIR = APP_DIR / "static"


def _asset_url(path: str) -> str:
    """Cache-busting static URL. Appends the file's mtime as ?v= so a changed
    asset gets a fresh URL — this stops a browser from pairing freshly-deployed
    HTML with a stale cached JS/CSS (which throws null-element errors when the
    markup and script drift apart)."""
    rel = path.lstrip("/")
    try:
        version = int((_STATIC_DIR / rel).stat().st_mtime)
    except OSError:
        return f"/static/{rel}"
    return f"/static/{rel}?v={version}"


def _abs_url(path: str) -> str:
    """Absolute URL for Open Graph tags (og:url / og:image require an absolute URL). Joins the
    request path onto config.PUBLIC_BASE_URL (set per deploy)."""
    return f"{config.PUBLIC_BASE_URL}/{path.lstrip('/')}"


templates.env.globals["asset"] = _asset_url
templates.env.globals["abs_url"] = _abs_url
# Votes below which a generator's rank is flagged "provisional" — available to every template
# (the leaderboard route also passes it in context, which harmlessly shadows this global).
templates.env.globals["firm_vote_threshold"] = service.FIRM_VOTE_THRESHOLD
templates.env.globals["site_name"] = config.SITE_NAME
templates.env.globals["site_tagline"] = config.SITE_TAGLINE
templates.env.globals["og_image_path"] = config.OG_IMAGE_PATH
# Read live (not the value at import) so tests/deploys can toggle config.INTERNAL_PAGES_ENABLED
# and both the route guard and the nav conditionals see the same current value.
templates.env.globals["internal_pages"] = lambda: config.INTERNAL_PAGES_ENABLED
# Same live-read reason: the verification tokens are set per deploy, and an instance that has
# not been given one must render no tag at all rather than an empty ownership claim.
templates.env.globals["google_site_verification"] = lambda: config.GOOGLE_SITE_VERIFICATION
templates.env.globals["bing_site_verification"] = lambda: config.BING_SITE_VERIFICATION
# Same live-read reason again. Unset on any instance not behind Cloudflare, which must render no
# beacon at all rather than a request to an analytics property that does not exist.
templates.env.globals["cf_analytics_token"] = lambda: config.CF_ANALYTICS_TOKEN
# Same live-read reason as above. Returns a dict rather than two globals so a template can
# never render the widget while missing the key it needs — the two travel together.
templates.env.globals["captcha"] = lambda: {
    "enabled": bool(config.REQUIRE_CAPTCHA),
    "provider": config.CAPTCHA_PROVIDER,
    "site_key": config.CAPTCHA_SITE_KEY,
}

# Local backend serves assets from disk; the S3 backend serves them from the bucket/CDN.
storage = get_storage()


def _resolve_category_id(db: Session, category_slug: str | None) -> int | None:
    if not category_slug or category_slug == "all":
        return None
    cat = db.execute(select(Category).where(Category.slug == category_slug)).scalars().first()
    return cat.id if cat else None


def _effective_category_ids(k_ids: set[int] | None, category_id: int | None) -> set[int] | None:
    """Intersect an explicit `?category=` selector with the active kingdom's category set (a
    chosen category is always within a kingdom in normal use — /api/meta only ever offers
    in-kingdom categories — but this keeps the pool correct even for a stale/out-of-kingdom
    selector). None means 'no restriction'; pick_task's `category_ids` kwarg takes precedence
    over its `category_id` kwarg, so a single combined set is what must be passed."""
    if k_ids is None:
        return {category_id} if category_id is not None else None
    if category_id is None:
        return k_ids
    return {category_id} if category_id in k_ids else set()


def _kingdom_is_live(db: Session, kingdom: str) -> bool:
    """True when `kingdom` has >=1 active Task in its mapped categories. `all` (no scoping)
    is always live. A kingdom whose categories exist but have zero tasks yet (e.g. Animals —
    seeded as a category placeholder, self-activates the moment its first task is added, same
    convention as the `coming_soon` category flag in /api/meta) is NOT live — the data pages
    route to the roadmap screen instead of rendering an empty board."""
    kingdom = kingdoms.normalize_kingdom(kingdom)
    if kingdom == "all":
        return True
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if not k_ids:
        return False
    return (
        db.execute(select(Task.id).where(Task.category_id.in_(k_ids), Task.active.is_(True)))
        .scalars()
        .first()
        is not None
    )


def _roadmap_or_none(request: Request, db: Session) -> HTMLResponse | None:
    """Return the coming-soon roadmap page when the request's active kingdom isn't live yet;
    else None so the caller renders its normal template. Applied at the top of every
    kingdom-scoped data route (Leaderboard, Arena, Difficulty, Significance, Benchmark,
    Coverage, Tasks, Dataset) — Home/Methodology/Submit/Spotlight are never gated."""
    kingdom = request.state.kingdom
    if kingdom == "all" or _kingdom_is_live(db, kingdom):
        return None
    return templates.TemplateResponse(
        request,
        "_kingdom_roadmap.html",
        {
            "kingdom": kingdom,
            "kingdom_label": kingdoms.KINGDOM_LABEL.get(kingdom, kingdom.title()),
        },
    )


def require_internal_pages() -> None:
    """Dependency for the internal research/analytics pages. On the public instance
    (config.INTERNAL_PAGES_ENABLED is False) they hard-404, so novel methodology is
    unpublished — not merely admin-gated — on the public deploy. Read live so a deploy/test
    toggle of config.INTERNAL_PAGES_ENABLED takes effect without re-importing."""
    if not config.INTERNAL_PAGES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _hero_stats(db: Session) -> dict:
    """Cheap headline counts shared by the home hero and the arena hero, so both
    pages report the same numbers (votes cast / distinct models / active tasks /
    live kingdoms)."""

    return {
        "total_votes": matchmaking.total_votes(db),
        # The homepage strip leads with THIS, not total_votes: the corpus is what verifiably
        # exists, whereas the vote total invites a reading about community participation that the
        # session distribution does not support (2026-08-08: 340 votes, 18 sessions ever, 93.8%
        # from two internal ones). Counts visible outputs only — hidden ones are not on display,
        # so claiming them would be the same overstatement in a different place.
        "outputs_count": db.execute(
            select(func.count(ModelOutput.id)).where(ModelOutput.hidden_at.is_(None))
        ).scalar_one(),
        "models_count": db.execute(
            select(func.count(func.distinct(Generator.id))).where(Generator.kind == "model")
        ).scalar_one(),
        "tasks_count": db.execute(
            select(func.count(Task.id)).where(Task.active.is_(True))
        ).scalar_one(),
        "kingdoms_live": sum(1 for k in kingdoms.KINGDOMS if _kingdom_is_live(db, k)),
    }
