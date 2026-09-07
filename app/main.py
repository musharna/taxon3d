"""FastAPI application — arena, voting, leaderboard, tasks, and admin tools.

Routes live in three places: the routers under app/routes/ (admin, vote, research) and, for the
public product surface (home, leaderboard family, models, organisms, dataset, static pages, OG
images, crawler files, auth, health), this module. Helpers shared across those live in
app/web_common.py; this module re-exports the names tests import from `app.main`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload, selectinload

from . import (
    config,
    dataset,
    difficulty,
    fidelity,
    integrity,
    kingdoms,
    indexnow,
    matchmaking,
    og,
    organisms,
    paradigms,
    ranking,
    service,
    variants,
)
from .database import SessionLocal, get_db, init_db
from .models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    ModelOutput,
    Rating,
    Task,
    User,
    Vote,
    VoterSession,
)
from .routes import admin as admin_routes
from .routes import research as research_routes
from .routes import vote as vote_routes
from .web_common import (
    APP_DIR,
    _abs_url,
    _effective_category_ids,
    _kingdom_is_live,
    _resolve_category_id,
    _roadmap_or_none,
    _hero_stats,
    require_internal_pages,
    storage,
    templates,
)

# Re-exported for tests and scripts that import these from `app.main`; the code lives in the
# routers now.
from .routes.admin import (  # noqa: F401
    ADMIN_COOKIE,
    _admin_token_of,
    _require_admin,
    require_admin_cookie,
    require_admin_header,
)
from .routes.research import _default_benchmark_task_id  # noqa: F401
from .routes.vote import (  # noqa: F401
    BALLOT_MODE_CALIBRATION,
    BALLOT_MODE_KWISE,
    BALLOT_MODE_PAIR,
    MEDIA_MAX_AGE,
    _arena_asset_url,
    _arena_lod_url,
    _build_ballot,
    _build_calibration_comparison,
    _build_comparison,
    _build_gold_comparison,
    _build_kwise_comparison,
    _criterion_or_default,
    _default_criterion,
    _serialize,
    _serialize_output,
    _uniform_lod_urls,
    _vote_pool_predicate,
    _withheld,
)

logger = logging.getLogger(__name__)

config.ensure_dirs()


def _init_db_safely(initializer=init_db) -> bool:
    """Run schema init, and survive it failing.

    This used to be a bare `init_db()` at module level. When the database became unreachable it
    raised during IMPORT, so uvicorn never started, the process exited 1, and the machine
    reboot-looped — the platform then served 503 for everything, including static assets and
    pages needing no database. A dependency outage became a total process failure.

    Deliberately not a silent swallow: the traceback is logged at ERROR, and `/healthz` reports
    the database separately so the state is visible rather than inferred. What changes is only
    whether it is FATAL. Schema creation is a convenience for dev and first boot; it is not a
    reason for a running site to refuse to serve its own privacy page.
    """
    try:
        initializer()
    except Exception:  # noqa: BLE001 — any driver error here must not stop the app booting
        logger.exception("init_db failed; starting anyway with the database marked unavailable")
        return False
    return True


DB_SCHEMA_READY = _init_db_safely()

#: Served for a 503. Inline styles and no template render on purpose: this page exists for the
#: moments when the usual machinery is the thing that is broken, so it must not depend on a
#: template loader, a static asset, or a font that has to be fetched. Colours are the site's,
#: hardcoded, and it reads in either theme.
_OUTAGE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Temporarily unavailable · Taxon3D</title>
<style>
  :root { color-scheme: dark light; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#12161c; color:#eef1f5;
         font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; padding:1.5rem; }
  main { max-width:34rem; }
  h1 { font-size:1.4rem; margin:0 0 .75rem; }
  p { margin:0 0 .75rem; color:#aeb7c2; line-height:1.55; }
  a { color:#4ec98b; }
</style></head>
<body><main>
  <h1>Taxon3D is temporarily unavailable</h1>
  <p>The database is not reachable right now, so comparisons and leaderboards cannot be
     served. Nothing has been lost &mdash; votes already recorded are safe.</p>
  <p>Please try again shortly. If this persists, it is worth reporting on
     <a href="https://github.com/musharna/taxon3d/issues">GitHub</a>.</p>
</main></body></html>
"""

app = FastAPI(title="Taxon3D", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
# Local backend serves assets from disk; the S3 backend serves them from the bucket/CDN.
if not storage.remote:
    app.mount("/assets", StaticFiles(directory=str(config.ASSET_DIR)), name="assets")

SESSION_COOKIE = "bio3d_session"


def _client_ip(request: Request) -> str:
    """Resolve the client IP for per-IP rate limiting, preferring headers a client cannot forge.

    X-Forwarded-For alone is not safe. Cloudflare "will append the IP address of the HTTP proxy
    connecting to Cloudflare to the header", so a request carrying `X-Forwarded-For: 1.2.3.4`
    reaches the origin as `1.2.3.4, <real client>` — element [0] is the caller's choice, and a
    vote farmer who rotates it never meets the per-IP cap.

    So prefer the headers the edge sets and overwrites itself, each trusted only when we have
    declared we sit behind that edge. Cloudflare outranks Fly because when both are in the chain
    Fly-Client-IP is Cloudflare's EDGE address — identical for every visitor, which would put the
    whole world in one rate-limit bucket.
    """
    if config.BEHIND_CLOUDFLARE:
        cf = request.headers.get("cf-connecting-ip", "")
        if cf.strip():
            return cf.strip()
    if config.TRUST_FLY_CLIENT_IP:
        fly = request.headers.get("fly-client-ip", "")
        if fly.strip():
            return fly.strip()
    if config.TRUST_FORWARDED_FOR:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


#: Read-only public pages: no forms, no per-visitor content, nothing a shared cache must not
#: hold. Everything absent from here — /arena, /submit, /admin, /auth, /api — keeps today's
#: behaviour exactly.
_CACHEABLE_PATHS = frozenset(
    {
        "/",
        "/leaderboard",
        "/models",
        "/organisms",
        "/dataset",
        "/methodology",
        "/terms",
        "/privacy",
        "/licenses",
        "/coverage",
        "/robots.txt",
        "/llms.txt",
        "/sitemap.xml",
    }
)
_CACHEABLE_PREFIXES = ("/organisms/", "/models/", "/leaderboard/")

#: Five minutes at the edge, then serve stale for ten more while revalidating in the background.
#: `max-age=0` keeps the BROWSER revalidating, so a voter watching a board still sees fresh
#: numbers; it is the shared cache we want absorbing crawlers.
_PUBLIC_CACHE = "public, max-age=0, s-maxage=300, stale-while-revalidate=600"


def _is_cacheable_page(path: str) -> bool:
    return path in _CACHEABLE_PATHS or path.startswith(_CACHEABLE_PREFIXES)


#: Asset routes. These carry bytes, never per-visitor content, and their handlers set their own
#: `Cache-Control` + `ETag`. The one thing they need from `ensure_session` is to be left alone:
#: a `Set-Cookie` on an asset is what stops a shared cache from storing it at all. Measured
#: 2026-08-20, minutes after the Cloudflare flip — `/static/og-default.png` came back
#: `cf-cache-status: BYPASS` purely because it minted a session.
_ASSET_PREFIXES = ("/media/", "/static/", "/assets/")


def _is_asset(path: str) -> bool:
    return path.startswith(_ASSET_PREFIXES)


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    """Attach an anonymous session id (cookie) used for light dedup + history.

    Public read-only pages are exempted from the cookie and marked cacheable. Those two go
    together and cannot be separated: no HTML response used to carry a `Cache-Control` header,
    Cloudflare does not cache HTML without one, so every view reached Postgres — a 447-request
    crawl became 447 database renders, exhausted the monthly transfer quota, and suspended the
    database. But simply adding `public` here would have been a vulnerability, because this
    middleware issues a session cookie to any request arriving without one, and a crawler never
    sends cookies. The responses a crawler triggers are exactly the ones carrying `Set-Cookie`,
    and a shared cache holding one would serve a single visitor's session id to everyone after
    them. A page is cacheable only because it is cookie-free.

    `Vary: Cookie` covers the second personalization channel: `bio3d_kingdom` changes what a
    board shows, so a visitor who has chosen a kingdom must not be served another visitor's
    view. Crawlers and first-time visitors send no cookies, share one cache entry, and are the
    traffic this is for.
    """
    sid = request.cookies.get(SESSION_COOKIE)
    is_new = sid is None
    if is_new:
        sid = uuid.uuid4().hex
    request.state.session_id = sid
    request.state.client_ip = _client_ip(request)
    from . import auth

    request.state.login_enabled = auth._login_enabled()
    # Resolve the verified user (if any) for templates — one light lookup per request.
    request.state.user = None
    try:
        from .database import SessionLocal
        from .models import User, VoterSession

        with SessionLocal() as _db:
            _vs = _db.get(VoterSession, sid)
            if _vs is not None and _vs.user_id is not None:
                request.state.user = _db.get(User, _vs.user_id)
    except Exception:  # noqa: BLE001 — never let user-resolution break a page
        request.state.user = None
    _kq = request.query_params.get("kingdom")
    _kingdom = kingdoms.normalize_kingdom(
        _kq if _kq is not None else request.cookies.get("bio3d_kingdom")
    )
    request.state.kingdom = _kingdom
    # Kingdom-scoped stats strip (`.b3d-kstats`) — HTML pages only, never /api (matchmaking's
    # /api/next must stay fast) or static/asset/health/auth routes. try/except + None default
    # so a stats failure can never 500 a page.
    request.state.kingdom_stats = None
    if not request.url.path.startswith(("/api", "/static", "/assets", "/healthz", "/auth")):
        try:
            from .database import SessionLocal

            with SessionLocal() as _stats_db:
                request.state.kingdom_stats = service.kingdom_scope_stats(_stats_db, _kingdom)
        except Exception:  # noqa: BLE001 — never let stats computation break a page
            request.state.kingdom_stats = None
    response = await call_next(request)
    # A `?kingdom=` request writes a preference cookie, so it is personalized by definition and
    # stays uncached. Those are the query permutations a crawler multiplies anyway (447 URLs
    # from 86 real ones), and they carry a canonical tag pointing at the bare page.
    cacheable = (
        request.method in ("GET", "HEAD")
        and _kq is None
        and response.status_code == 200
        and _is_cacheable_page(request.url.path)
    )
    # Both halves of "a shared cache may hold this" must stay cookie-free, and for the same
    # reason: an edge that cached a `Set-Cookie` would hand one visitor's session id to everyone
    # served after them. Assets are checked on PATH ALONE, deliberately — gating them on
    # `status_code == 200` the way pages are would put a cookie back on a 304, which is the
    # revalidation a warm cache issues most.
    if is_new and not cacheable and not _is_asset(request.url.path):
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
            secure=config.COOKIE_SECURE,
        )
    if _kq is not None:
        response.set_cookie(
            "bio3d_kingdom",
            _kingdom,
            max_age=60 * 60 * 24 * 365,
            samesite="lax",
        )
    if cacheable:
        response.headers["Cache-Control"] = _PUBLIC_CACHE
        vary = response.headers.get("Vary")
        response.headers["Vary"] = f"{vary}, Cookie" if vary else "Cookie"
    return response


OAUTH_STATE_COOKIE = "bio3d_oauth_state"


@app.get("/auth/login")
def auth_login(request: Request):
    from . import auth

    if not auth._login_enabled():
        return RedirectResponse("/", status_code=302)
    state = auth.new_state()
    redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
    resp = RedirectResponse(auth.authorize_url(state, redirect_uri), status_code=302)
    resp.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
    )
    return resp


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    from . import auth

    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not auth._login_enabled() or not code or not state or state != cookie_state:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    try:
        redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
        token = auth.exchange_code(code, redirect_uri)
        info = auth.fetch_userinfo(token)
    except auth.AuthError:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    user = db.execute(select(User).where(User.hf_id == info["hf_id"])).scalars().first()
    if user is None:
        user = User(hf_id=info["hf_id"], username=info["username"])
        db.add(user)
        db.flush()
    else:
        user.username = info["username"]
    vs = integrity.get_or_create_session(db, request.state.session_id)
    vs.user_id = user.id
    db.commit()
    resp = RedirectResponse("/?login=ok", status_code=302)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    return resp


@app.post("/auth/logout")
def auth_logout(request: Request, db: Session = Depends(get_db)):
    vs = db.get(VoterSession, request.state.session_id)
    if vs is not None:
        vs.user_id = None
        db.commit()
    return RedirectResponse("/", status_code=302)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    """Marketing/landing page. Never kingdom-gated (see `_roadmap_or_none` docstring) —
    it's the one screen every visitor should be able to load regardless of scope."""

    stats = _hero_stats(db)
    total_votes = stats["total_votes"]
    models_count = stats["models_count"]
    tasks_count = stats["tasks_count"]
    kingdoms_live = stats["kingdoms_live"]

    # "Choose a kingdom" cards below the hero — live/task-count are real per-kingdom queries
    # (not the top-level `kingdoms_live`/`tasks_count`, which are the all-kingdoms totals).
    kingdom_blurbs = {
        "plants": "Flowers, crops, and foliage — the founding kingdom of the benchmark.",
        "fungi": "Mushrooms and fruiting bodies — complement-aware completeness beyond plants.",
        # Written while animals were still being seeded; they shipped, so the card was
        # rendering "arriving as tasks are seeded" directly beside its own LIVE badge and a
        # real task count. The blurb is static text next to live per-kingdom queries — it
        # cannot go stale silently again if it stops making a claim about readiness.
        "animals": "Vertebrates and invertebrates — bilateral body plans, a third kingdom.",
    }
    kingdom_cards = []
    for k in kingdoms.KINGDOMS:
        k_ids = kingdoms.category_ids_for_kingdom(db, k)
        k_task_count = (
            db.execute(
                select(func.count(Task.id)).where(
                    Task.category_id.in_(k_ids), Task.active.is_(True)
                )
            ).scalar_one()
            if k_ids
            else 0
        )
        kingdom_cards.append(
            {
                "slug": k,
                "emoji": kingdoms.KINGDOM_EMOJI[k],
                "name": kingdoms.KINGDOM_LABEL[k],
                "latin": kingdoms.KINGDOM_LATIN.get(k, ""),
                "live": _kingdom_is_live(db, k),
                "blurb": kingdom_blurbs[k],
                "task_count": k_task_count,
            }
        )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "total_votes": total_votes,
            "outputs_count": stats["outputs_count"],
            "models_count": models_count,
            "tasks_count": tasks_count,
            "kingdoms_live": kingdoms_live,
            "kingdom_cards": kingdom_cards,
        },
    )


# Crawler-facing files. Both 404'd until the 2026-07-27 pre-release audit — a public launch
# with nothing telling a crawler what to index or skip.
#
# The allowlist below is deliberately explicit rather than derived from app.routes: the router
# also carries internal research pages, admin surfaces, JSON APIs and parameterised media
# routes, and a sitemap built by filtering all of those would advertise a new internal page the
# day someone adds one. Listing the public product surface by hand means a new page is absent
# until someone says otherwise, which is the safe direction to fail.
_SITEMAP_PATHS = (
    "/",
    "/arena",
    "/leaderboard",
    "/models",
    "/dataset",
    # No /spotlight: it is an internal page (see spotlight_index) and 404s on the public
    # instance, so advertising it here would hand crawlers a dead URL.
    "/methodology",
    "/coverage",
    "/tasks",
    "/organisms",
    "/submit",
    "/terms",
    "/privacy",
    "/licenses",
)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    """Allow the product surface, keep crawlers off write/admin/API paths.

    Disallowing /api/ is not secrecy — those endpoints are already public where they should
    be. It stops a crawler burning the vote endpoints' rate limit and indexing JSON that has
    no standalone meaning.
    """
    lines = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /media/",
        "Allow: /",
        "",
        f"Sitemap: {config.PUBLIC_BASE_URL}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


@app.get(indexnow.KEY_PATH, response_class=PlainTextResponse)
def indexnow_key_file():
    """Proof of domain ownership for IndexNow — the file's whole content is the key.

    404s when unconfigured rather than serving an empty file: an empty key file verifies
    nothing and the API answers 403 for it, which is a confusing way to learn the key was
    never set. See app/indexnow.py for why this is a fixed path rather than `/{key}.txt`.
    """
    if not config.INDEXNOW_KEY:
        raise HTTPException(status_code=404, detail="Not Found")
    return config.INDEXNOW_KEY


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt():
    """A short factual map of the site for a model reading it directly.

    Increasingly people arrive by asking a model rather than a search engine, and a model
    answering "is there a benchmark for AI-generated 3D models of organisms" is reading text,
    not rendering a page. This is that text: what the site is, what it covers, and what the
    numbers currently do and do not support.

    It states the unranked position on purpose. That is the single fact most likely to be
    stated wrongly by something summarising this site — an arena with a leaderboard reads as
    an arena with results — and it is the one this project has been most careful about
    everywhere else.

    Same rule as the sitemap: internal research surfaces 404 publicly and are not named here.
    """
    base = config.PUBLIC_BASE_URL
    return "\n".join(
        [
            "# Taxon3D",
            "",
            "> A blind comparison benchmark for generative 3D models of real organisms.",
            "> Two anonymised 3D outputs of the same species are shown side by side against",
            "> CC-licensed reference photographs, so the judgement is biological fidelity",
            "> rather than visual appeal.",
            "",
            "Rankings use Bradley-Terry with bootstrap confidence intervals, computed within a",
            "single generation method: scores from different methods come from disconnected",
            "match pools and are not comparable. A generator without enough comparisons is",
            "reported as unranked rather than given a point estimate, and at present most",
            "entrants are unranked for want of votes.",
            "",
            "## Pages",
            "",
            f"- [Arena]({base}/arena): vote on a pair of anonymised outputs.",
            f"- [Leaderboard]({base}/leaderboard): per-method Bradley-Terry standings.",
            f"- [Models]({base}/models): every generator, its coverage and its record.",
            f"- [Organisms]({base}/organisms): the corpus by species, with reference photos.",
            f"- [Task catalog]({base}/tasks): every benchmarked task and its difficulty tier.",
            f"- [Methodology]({base}/methodology): how ranking, matchmaking and gating work.",
            f"- [Dataset]({base}/dataset): the citable, licensed benchmark release.",
            f"- [Coverage]({base}/coverage): what the corpus covers and what it does not.",
            f"- [Licenses]({base}/licenses): attribution for everything redistributed.",
            "",
            "## Scope",
            "",
            "Three kingdoms (plants, fungi, animals) across four generation methods:",
            "single-image reconstruction, text-to-3D, LLM-authored procedural geometry, and",
            "agentic render-critique-revise pipelines.",
            "",
            "Source code: https://github.com/musharna/taxon3d (MIT).",
            "",
        ]
    )


def _sitemap_content_paths(db: Session) -> list[str]:
    """The per-model, per-modality and per-organism pages, derived rather than hand-listed.

    The static allowlist above is the right shape for the top-level product surface, where a new
    page should be absent until someone says otherwise. It is the wrong shape for content that
    arrives with the data: 37 model detail pages were serving 200 with unique titles and real
    per-task tables while the sitemap named none of them, because nobody edits a tuple when a
    generator is added.

    Both visibility rules are read from the single source the ROUTES use, never restated:

      * `_model_cards(db, None)` already drops app-hidden testers and generators with no
        coverage row — exactly the set `/models` links to and `/models/{slug}` serves.
      * A modality board is listed only if some visible generator carries that paradigm. That
        covers the app-hidden paradigms for free (no visible generator has one) and, separately,
        keeps the reserved-but-unused names — video, texturing, sketch — out: their boards
        render empty, and advertising empty pages is thin content, not coverage.

    Global scope (`k_ids=None`) on purpose: a sitemap is not viewed through a kingdom filter.
    This costs one `_model_cards` pass, the same work `/models` does; the sitemap is fetched
    rarely enough that sharing the route's own query beats a faster copy that can drift.
    """
    cards = _model_cards(db, None)
    paths = [f"/models/{c['slug']}" for c in cards if c["slug"]]
    populated = {c["paradigm"] for c in cards if c["paradigm"]}
    paths += [f"/leaderboard/{p}" for p in paradigms.PARADIGMS if p in populated]
    # Organism pages come from the same index the /organisms hub renders, so a page that exists
    # is declared and one that does not cannot be.
    paths += [f"/organisms/{o['slug']}" for o in organisms.organism_index(db)]
    return paths


@app.get("/sitemap.xml")
def sitemap_xml(db: Session = Depends(get_db)):
    # Escaped because the dynamic half interpolates generator slugs, and nothing in the schema
    # keeps a slug free of `&` or `<`. An unescaped one does not cost you its own entry — it
    # makes the whole document unparseable, so every URL in it goes unread.
    paths = list(_SITEMAP_PATHS) + _sitemap_content_paths(db)
    urls = "".join(f"<url><loc>{xml_escape(config.PUBLIC_BASE_URL + p)}</loc></url>" for p in paths)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return Response(content=body, media_type="application/xml")


# ------------------------------------------------------------------ leaderboard

# Provenance chips (paper/code/data) are a HEURISTIC, not real url fields — Generator /
# ModelOutput carry no paper/code/dataset link columns. The design brief's literal
# kind-based rule (model/agent/scan/baseline) doesn't match this repo's actual
# Generator.kind values (a full-repo grep finds only "model"/"decoy" ever set); `paradigm`
# is the axis that actually varies per generator, so the heuristic keys off paradigm
# instead, with the same intent: an approximate visual cue, never a claim of a real link.
_PARADIGM_PROVENANCE: dict[str, list[str]] = {
    "capture_scan": ["data"],
    "retrieval": ["data"],
    "agentic": ["code"],
    "procedural_llm": ["code"],
    "procedural_expert": ["paper", "code"],
    "image_recon": ["paper", "code"],
    "text_native": ["paper", "code"],
}


def _provenance_chips(paradigm: str | None, kind: str) -> list[str]:
    if kind == "baseline":
        return []
    return _PARADIGM_PROVENANCE.get(paradigm or "", ["code"])


def _avatar_initials(display_name: str) -> str:
    """2 uppercase initials from a generator's display name, e.g. 'Radim/gaussian (full)'
    -> 'RG', 'google/gemini-2.5-pro (agentic)' -> 'GG', single-word -> first 2 chars."""
    base = display_name.split("(")[0].strip()
    parts = [p for p in re.split(r"[/\s_·-]+", base) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts and len(parts[0]) >= 2:
        return parts[0][:2].upper()
    if parts:
        return (parts[0] + "?").upper()
    return "??"


def _avatar_hue(key: str) -> int:
    """Stable 0-360 hue derived from a generator's slug, so the avatar tile color is
    deterministic across requests/processes. Python's builtin `hash()` is per-process
    randomized for strings (security feature), so a stable hash (md5) substitutes for the
    literal `hash(slug) % 360` the brief describes."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 360


def _trend_polyline(values: list[float | None], width: int = 64, height: int = 20) -> str:
    """SVG polyline `points` for the trend sparkline. Win-rate values are already a 0..1
    fraction. Empty/all-None -> a flat baseline (never a fabricated shape)."""
    pad = 2.0
    usable = [v for v in values if v is not None]
    if not usable:
        mid = height / 2.0
        return f"0,{mid:.1f} {width},{mid:.1f}"
    n = len(values)
    step = width / max(n - 1, 1)
    last_y = height / 2.0
    pts = []
    for i, v in enumerate(values):
        x = round(i * step, 1)
        if v is not None:
            last_y = pad + (1.0 - v) * (height - 2 * pad)
        pts.append(f"{x},{round(last_y, 1)}")
    return " ".join(pts)


def _trend_title(values: list[float | None]) -> str:
    """A readable hover title for the trend sparkline: the actual win-rate per period so the line
    conveys magnitude, not just shape. Win-rate values are a 0..1 fraction; None periods (no votes
    yet) are skipped rather than drawn as a fabricated 0%."""
    pct = [f"{v * 100:.0f}%" for v in values if v is not None]
    if not pct:
        return "No vote history yet"
    return "Win-rate by period: " + " → ".join(pct)


def _momentum(values: list[float | None]) -> str:
    """'up'/'down'/'flat' derived from the trend series — NOT a rank-vs-last-period delta
    (no historical rank snapshot table exists)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "flat"
    delta = vals[-1] - vals[0]
    if delta > 0.03:
        return "up"
    if delta < -0.03:
        return "down"
    return "flat"


def _enrich_leaderboard_rows(
    rows: list[dict], trend_by_gid: dict[int, list[float | None]]
) -> list[dict]:
    """Attach the prototype's display-only fields (avatar, provenance chips, trend
    sparkline + momentum, model-detail link, provisional flag, podium medal) to already-ranked
    leaderboard rows. Never touches bt_score/rank/ci_* — pure presentation enrichment."""
    # A medal is a claim of SEPARATION, so only a top-3 rank that no other displayed row shares —
    # and that real votes back — earns one. rank_by_ci lets CI-overlapping models share a rank; on
    # a thin board that can tie every model at rank 1, and five gold medals would read as five
    # winners. Those rows still show their (shared) rank number, which is the honest signal.
    rank_counts: dict[int, int] = {}
    for r in rows:
        rank_counts[r.get("rank", 0)] = rank_counts.get(r.get("rank", 0), 0) + 1
    for r in rows:
        rank = r.get("rank", 0)
        r["podium"] = r.get("n_games", 0) > 0 and rank <= 3 and rank_counts.get(rank, 0) == 1
    for r in rows:
        r["avatar"] = _avatar_initials(r["generator"])
        r["avatar_hue"] = _avatar_hue(r.get("slug") or r["generator"])
        r["provenance"] = _provenance_chips(r.get("paradigm"), r.get("kind", "model"))
        gid = r.get("generator_id")
        trend = trend_by_gid.get(gid, []) if gid is not None else []
        r["trend"] = trend
        r["trend_points"] = _trend_polyline(trend)
        r["trend_title"] = _trend_title(trend)
        r["momentum"] = _momentum(trend)
        r["provisional"] = r.get("n_games", 0) < service.FIRM_VOTE_THRESHOLD
        # Votes-until-firm signal for the board's Status column ({"firm": bool, "label": str}),
        # computed HERE so the template stays free of ranking/threshold logic.
        r["status"] = service.firm_status(r.get("n_games", 0))
        r["detail_url"] = f"/models/{r['slug']}" if r.get("slug") else "#"
    return rows


def _leaderboard_rows(
    db: Session,
    criterion_slug: str = "overall",
    category_slug: str | None = None,
    paradigm: str | None = None,
    kingdom: str = "all",
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    category_id = _resolve_category_id(db, category_slug)
    ref_gens = service.mode_a_excluded_generator_ids(db)
    names = service.generator_display_names(db)
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if k_ids is not None:
        # A kingdom (≠ "all") is active: the cached `Rating` table is keyed by a single
        # category_id and cannot represent a SET of categories. `KingdomRating` (keyed by the
        # kingdom STRING) covers the whole-kingdom scope and is refreshed by /admin/recompute;
        # read it first and only fall back to live BT (kingdom_leaderboard_rows) on a cache miss
        # (nothing cached yet) — the page must be correct before the first recompute, just slow
        # that once. A narrower `?category=` selector WITHIN the kingdom has no cached
        # counterpart (the cache is whole-kingdom only), so it always computes live.
        ids = _effective_category_ids(k_ids, category_id)
        rows = None
        if category_id is None:
            rows = service.cached_kingdom_leaderboard_rows(db, criterion_slug, kingdom)
        if rows is None:
            rows = service.kingdom_leaderboard_rows(db, criterion_slug, ids)
        rows = [r for r in rows if not paradigm or r["paradigm"] == paradigm]
    else:
        scope = (
            Rating.category_id.is_(None)
            if category_id is None
            else Rating.category_id == category_id
        )
        ratings = (
            db.execute(select(Rating).where(Rating.criterion_id == crit.id, scope)).scalars().all()
        )
        # One bulk load instead of a db.get() per rating row — 54 single-row generator SELECTs
        # on the live corpus, each a network round trip against managed Postgres.
        _gens = {
            g.id: g
            for g in db.execute(
                select(Generator).where(Generator.id.in_({r.generator_id for r in ratings}))
            ).scalars()
        }
        rows = []
        for r in ratings:
            if r.generator_id in ref_gens:
                continue  # GT/reference scans don't compete in the Mode-A perceptual board
            gen = _gens.get(r.generator_id)
            if gen is None:
                continue  # stale rating row (generator deleted); skip rather than crash
            if paradigm and gen.paradigm != paradigm:
                continue
            rows.append(
                {
                    "generator": names.get(r.generator_id, gen.name),
                    "kind": gen.kind,
                    "paradigm": gen.paradigm,
                    "generator_id": r.generator_id,
                    "slug": gen.slug,
                    "elo": round(r.elo, 1),
                    "bt_score": round(r.bt_score, 1),
                    "bt_lower": round(r.bt_lower, 1),
                    "bt_upper": round(r.bt_upper, 1),
                    "n_games": r.n_games,
                }
            )
    # ONE flat table for the selected tab (design-parity task-lb): a `paradigm` filter
    # already narrowed `rows` to a single paradigm above, so "rank 1..N by bt_score desc"
    # is unambiguous there. With NO filter ("Overall") this DELIBERATELY merges rows from
    # every paradigm into one BT-desc ranking for the prototype's unified board — BT scores
    # across paradigms come from disconnected match components (spec §D's "never rank
    # across paradigms" invariant), so this is a display-only ordering, not a statistical
    # claim that e.g. rank 3 beats rank 4 across paradigms. The template/legend must footnote
    # that within-paradigm comparison (i.e. selecting a single paradigm tab) is the rigorous
    # one. finalize_rows() supplies the shared rank + whisker geometry (also used by the
    # verified board); CI-tie grouping still applies, just over the whole merged set.
    return service.finalize_rows(rows)


def _group_rank_judge_rows(rows: list[dict]) -> list[dict]:
    """Rank + CI-bar geometry computed WITHIN each paradigm group, mirroring
    _leaderboard_rows — cross-paradigm BT scores come from disconnected match
    components, so a single flat cross-paradigm ranking would be meaningless (I3b).
    Shared by the cached (global) and live (kingdom) judge-board paths."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["paradigm"], []).append(r)
    grouped_rows: list[dict] = []
    for pgm in sorted(groups):
        grows = groups[pgm]
        grows.sort(key=lambda x: x["bt_score"], reverse=True)
        ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in grows])
        for row, rank in zip(grows, ranks):
            row["rank"] = rank
        lo = min(r["bt_lower"] for r in grows)
        hi = max(r["bt_upper"] for r in grows)
        span = (hi - lo) or 1.0
        for r in grows:
            r["ci_left"] = round(100.0 * (r["bt_lower"] - lo) / span, 1)
            r["ci_width"] = round(100.0 * (r["bt_upper"] - r["bt_lower"]) / span, 1)
            r["ci_point"] = round(100.0 * (r["bt_score"] - lo) / span, 1)
            r["ci_lo"] = round(lo, 1)  # domain endpoints for the axis label (mirrors finalize_rows)
            r["ci_hi"] = round(hi, 1)
        grouped_rows.extend(grows)
    return grouped_rows


def _judge_leaderboard_rows(
    db: Session, criterion_slug: str = "overall", view_condition: str = "multi4"
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    ratings = (
        db.execute(
            select(JudgeRating).where(
                JudgeRating.criterion_id == crit.id,
                JudgeRating.view_condition == view_condition,
                JudgeRating.category_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    ref_gens = service.mode_a_excluded_generator_ids(db)
    names = service.generator_display_names(db)
    rows = []
    for r in ratings:
        if r.generator_id in ref_gens:
            continue  # GT/reference scans don't compete in the Mode-A perceptual board
        gen = db.get(Generator, r.generator_id)
        if gen is None:
            continue  # stale rating row (generator deleted); skip rather than crash
        rows.append(
            {
                "generator": names.get(r.generator_id, gen.name),
                "kind": gen.kind,
                "paradigm": gen.paradigm,
                "elo": round(r.elo, 1),
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    return _group_rank_judge_rows(rows)


def _kingdom_judge_leaderboard_rows(
    db: Session,
    criterion_slug: str,
    view_condition: str,
    kingdom: str,
    category_ids: set[int] | None,
    category_id: int | None = None,
) -> list[dict]:
    """VLM-judge board for an active kingdom — mirrors _leaderboard_rows' kingdom branch: read
    the `KingdomJudgeRating` cache (refreshed by /admin/recompute) first, falling back to live BT
    (service.kingdom_judge_leaderboard_rows) on a cache miss or when a narrower `?category=`
    selector within the kingdom is active (the cache is whole-kingdom only, same convention as
    the human board)."""
    rows = None
    if category_id is None:
        rows = service.cached_kingdom_judge_leaderboard_rows(
            db, criterion_slug, view_condition, kingdom
        )
    if rows is None:
        rows = service.kingdom_judge_leaderboard_rows(
            db, criterion_slug, view_condition, category_ids
        )
    return _group_rank_judge_rows(rows)


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    paradigm: str | None = None,
    verified: bool = False,
    show_all: bool = False,
):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    paradigm = paradigm or None  # "" (unset <select>) and None both mean "no filter"
    kingdom = request.state.kingdom
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    category_id_sel = _resolve_category_id(db, category)
    cat_ids = _effective_category_ids(k_ids, category_id_sel) if k_ids is not None else None
    # `all_rows` is the UNIVERSE for this scope (every paradigm). It is NEVER displayed as one
    # merged board — the cross-paradigm ranking is gone (paradigms are disconnected match pools,
    # so a merged BT ordering was never a statistical claim). It feeds the trend series, the
    # rated/unrated counts, the tab list, and the per-modality grouping below.
    #
    # `verified` is a SCOPE MODIFIER (which votes count), not a board: it swaps the row source
    # (BT refit over signed-in votes only) and nothing else. Both scopes render the same two
    # surfaces — the modality hub (no paradigm) or ONE paradigm's board.
    if verified:
        all_rows = service.verified_leaderboard_rows(db, criterion, category, category_ids=cat_ids)
    else:
        all_rows = _leaderboard_rows(db, criterion, category, None, kingdom)

    # Paradigm -> that paradigm's rows. SHALLOW COPIES on purpose: service.finalize_rows() sorts
    # its list and rewrites `rank`/`ci_*` IN PLACE, so grouping the live all_rows dicts would let
    # a board's re-rank leak back into the universe that the counts/tabs/trend read from.
    groups: dict[str | None, list[dict]] = {}
    for r in all_rows:
        groups.setdefault(r.get("paradigm"), []).append(dict(r))

    total = matchmaking.total_votes(db)
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    # Real Vote.created-derived trend sparkline, scoped exactly like the rows above (kingdom
    # ids take precedence over a plain category id, mirroring _matches_for_scope elsewhere).
    crit_row = db.execute(select(Criterion).where(Criterion.slug == criterion)).scalars().first()
    trend_by_gid: dict[int, list[float | None]] = {}
    if all_rows and crit_row is not None:
        trend_by_gid = service.generator_trend_series(
            db,
            crit_row.id,
            None if cat_ids is not None else category_id_sel,
            category_ids=cat_ids,
        )

    def _finish(board_rows: list[dict]) -> list[dict]:
        """Select → RANK → enrich ONE board's rows. Rated-only by default (`show_all` reveals the
        never-voted entrants; fall back to all if none are rated).

        The rated filter runs BEFORE service.finalize_rows(), so `rank` is a CI-grouped 1..N over
        exactly the rows that will be DISPLAYED. Ranking first and filtering after (the old order)
        left the displayed rank a slice of a wider ranking — a board could start at rank 2 — which
        is why the template fell back to printing `loop.index` under the "Rank" header, inventing a
        strict order among CI-tied rows (on a zero-vote board: six models, identical BT, identical
        CIs, printed 1..6). Now the template prints the real CI-grouped rank: it starts at 1 and
        models that are not statistically separable SHARE a rank.

        `show_all` only reaches the USER on a single-paradigm board — the hub does not call this
        (service.modality_hub_cards owns its own rated/unrated split, since a card shows both a
        rated top-3 and an unvoted modality's empty state)."""
        rated = [r for r in board_rows if r.get("n_games", 0) > 0]
        shown = board_rows if (show_all or not rated) else rated
        enriched = _enrich_leaderboard_rows(service.finalize_rows(shown), trend_by_gid)
        # Fold re-hosts of one model (TRELLIS on fal / Replicate / local) under their canonical
        # row: three hosts of one model can otherwise take three separate rank slots and read as
        # three competitors. nest_variants re-ranks the top level so they consume none, and keeps
        # each variant's own BT + votes (see app/variants.py — scores are NOT merged).
        return variants.nest_variants(enriched)

    # Paradigms present in this (criterion/category/kingdom) scope — drives the tabs on a single
    # board. Derived from the merged all_rows so a tab never vanishes on click.
    paradigms_in_rows = sorted({r["paradigm"] for r in all_rows if r.get("paradigm")})

    # Share affordance + kingdom-scoped OG card for this board (rides on both the hub and a single
    # board, so it is built before the branch). See _leaderboard_share_context / render_leaderboard_card.
    lb_scope_label = (
        config.SITE_NAME
        if kingdom == "all"
        else kingdoms.KINGDOM_LABEL.get(kingdom, config.SITE_NAME)
    )
    lb_share = _leaderboard_share_context(lb_scope_label, kingdom)

    # Page-level chrome (_leaderboard_controls.html) — the category/criterion filters + the
    # Trusted/Verified scope toggle + the bias audit ride on BOTH the hub and a single board, so
    # they are built before the hub branch returns. `selected` flags are precomputed in Python so
    # the template avoids `==` (which the HTML formatter mangles inside Jinja tags).
    category_options = [
        {
            "slug": "all",
            "name": "All categories",
            "selected": category == "all",
            "coming_soon": False,
        }
    ]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug, "coming_soon": not c.tasks}
        for c in cats
    ]
    criterion_options = [
        {"slug": c.slug, "name": c.name, "selected": criterion == c.slug} for c in crits
    ]
    controls_ctx = {
        "category_options": category_options,
        "criterion_options": criterion_options,
        "bias": service.compute_bias(db),
        "sel_criterion": criterion,
        "sel_category": category,
        "verified": verified,
    }

    # NO paradigm filter (in EITHER scope) = the modality HUB: one card per visible modality,
    # linking to that modality's own board. There is no cross-paradigm ranking any more — BT
    # scores from different paradigms come from disconnected match pools. Each card re-ranks its
    # own group (a fresh within-paradigm 1..N — the only rigorous comparison); finalize_rows()
    # ranks whatever rows it is handed, so grouping the already-computed universe is identical to
    # issuing one row-query per paradigm, at a single query/BT read.
    #
    # The card set is the ROSTER's modalities (_visible_modalities), not "the modalities that have
    # votes in this scope": an unvoted modality still has a board (and /api/leaderboard still
    # publishes one), so it gets a card in an honest empty state rather than vanishing. rows_fn
    # hands modality_hub_cards the WHOLE group (rated + unrated) — it owns the split.
    if paradigm is None:
        cards = service.modality_hub_cards(lambda p: groups.get(p, []), _visible_modalities(db))
        return templates.TemplateResponse(
            request,
            "leaderboard_hub.html",
            {
                "cards": cards,
                "total_votes": total,
                "lb_share": lb_share,
                "firm_vote_threshold": service.FIRM_VOTE_THRESHOLD,
                **controls_ctx,
            },
        )

    # ONE paradigm's board, in either scope. Its rank is a FRESH within-paradigm 1..N (never a
    # slice of a merged ranking, which would read 3/7/9): finalize_rows() re-ranks this
    # paradigm's rows alone.
    rows = _finish(groups.get(paradigm, []))
    board_title = paradigms.DISPLAY_NAMES.get(paradigm, paradigm)
    # Global rated/unrated counts (for the single Show-all toggle) from the merged universe.
    total_generators = len(all_rows)
    unrated_count = sum(1 for r in all_rows if r.get("n_games", 0) == 0)
    # Tabs reflect the FULL paradigm universe for this scope (from all_rows, so a tab never
    # vanishes on click): "All methods" (back to the modality hub) first, then one tab per
    # paradigm. There is no cross-paradigm "Overall" tab — that ranking no longer exists.
    # `mode` tells the template which href/params to build.
    paradigm_options = [{"mode": "hub", "value": None, "tab": "All methods", "selected": False}] + [
        {
            "mode": "paradigm",
            "value": p,
            "display": paradigms.DISPLAY_NAMES.get(p, p),
            "tab": paradigms.SHORT_NAMES.get(p, p),
            "selected": paradigm == p,
        }
        for p in paradigms_in_rows
    ]
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "rows": rows,
            "board_title": board_title,
            # Plain-language "what this measures" line for THIS modality (never hard-coded copy —
            # paradigms.WHAT_THIS_MEASURES is the one source, shared with the hub cards).
            "board_what": paradigms.WHAT_THIS_MEASURES.get(paradigm, ""),
            # True when this modality is outside the current human vote roster
            # (config.ARENA_VOTE_PARADIGMS). Its rows keep the votes already cast but accrue no
            # new ones, so the board must say that rather than let permanently-"provisional"
            # rows read as an un-voted backlog. Keyed off live config, not a hard-coded list.
            "off_roster": bool(config.ARENA_VOTE_PARADIGMS)
            and paradigm not in config.ARENA_VOTE_PARADIGMS,
            "sel_paradigm": paradigm,
            "total_votes": total,
            "lb_share": lb_share,
            "paradigm_options": paradigm_options,
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "show_all": show_all,
            "unrated_count": unrated_count,
            "total_generators": total_generators,
            "firm_vote_threshold": service.FIRM_VOTE_THRESHOLD,
            # The judge board is NOT computed here — it is fitted lazily by GET /leaderboard/judge
            # when the collapsed <details> is expanded (leaderboard.js), so the main render never
            # blocks on the ~11s judge BT fit for a kingdom on a cold cache.
            **controls_ctx,
        },
    )


def _visible_modalities(db: Session) -> list[str]:
    """The modalities that exist as a public surface — every paradigm carried by ≥1 generator in
    the roster, minus config.APP_HIDDEN_PARADIGMS, in paradigms.PARADIGMS order. This is the same
    universe the modality hub cards cover, so the judge page's switcher mirrors the human boards
    (rather than the judge board's own row set, which would drop a modality the judge hasn't
    scored yet and make the two surfaces disagree about which boards exist)."""
    present = set(db.execute(select(Generator.paradigm).distinct()).scalars())
    return [
        p
        for p in paradigms.PARADIGMS
        if p in present and p not in config.APP_HIDDEN_PARADIGMS and p is not None
    ]


@app.get("/leaderboard/judge", response_class=HTMLResponse)
def leaderboard_judge(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    modality: str | None = None,
    verified: bool = False,
    fragment: bool = False,
):
    """The VLM-judge board — a SEPARATE surface from the human-vote board, never intermixed with
    it (its BT scores come from VLM ballots, not human votes; the page says so in plain language).

    ONE route, TWO consumers:

    * A BROWSER arriving from a board's "see the AI-judge board →" link gets a full page
      (leaderboard_judge.html: site chrome, disclaimer, modality switcher, human-board backlink).
    * `app/static/leaderboard.js` lazy-fetches the SAME route into the collapsed <details> on the
      leaderboard and assigns the response to `innerHTML`, so it needs the BARE FRAGMENT
      (_leaderboard_judge.html) — a full page there would nest <html> inside a <div>. It is
      selected explicitly by `?fragment=1` (what the rendered `data-judge-url` carries) or by the
      `X-Requested-With` header leaderboard.js already sends, so a hand-typed URL — which has
      neither — always lands on the page.

    Rows are the same cache-first, live-fallback path the main route used to run inline (see
    _kingdom_judge_leaderboard_rows); the fit is ~11s cold, which is why the leaderboard never
    computes it on the main render. `verified` is accepted for URL symmetry with the human board;
    the judge board is unaffected by it (judge ballots have no signed-in scope)."""
    if modality is not None and (
        modality in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(modality)
    ):
        # Same contract as /leaderboard/{modality}: an app-hidden modality is internal-only and
        # must not exist as a public surface at all, judge board included.
        raise HTTPException(status_code=404, detail="Unknown modality")
    kingdom = request.state.kingdom
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    category_id_sel = _resolve_category_id(db, category)
    cat_ids = _effective_category_ids(k_ids, category_id_sel) if k_ids is not None else None
    judge_rows = (
        _kingdom_judge_leaderboard_rows(db, criterion, "multi4", kingdom, cat_ids, category_id_sel)
        if k_ids is not None
        else _judge_leaderboard_rows(db, criterion, "multi4")
    )
    # Belt-and-braces with the generator-level hiding in service.app_hidden_generator_ids(): a
    # hidden paradigm never gets a heading, even if a stale rating row slipped through.
    judge_rows = [r for r in judge_rows if r.get("paradigm") not in config.APP_HIDDEN_PARADIGMS]
    if modality is not None:
        judge_rows = [r for r in judge_rows if r.get("paradigm") == modality]
    ctx = {"judge_rows": judge_rows, "paradigm_display_names": paradigms.DISPLAY_NAMES}
    if fragment or request.headers.get("x-requested-with"):
        return templates.TemplateResponse(request, "_leaderboard_judge.html", ctx)
    visible = _visible_modalities(db)
    return templates.TemplateResponse(
        request,
        "leaderboard_judge.html",
        {
            **ctx,
            # Suppresses the fragment's own inline provenance line — the page carries a louder one.
            "on_page": True,
            "sel_modality": modality,
            "sel_criterion": criterion,
            "sel_category": category,
            "board_title": paradigms.DISPLAY_NAMES.get(modality, modality) if modality else None,
            "board_what": paradigms.WHAT_THIS_MEASURES.get(modality, "") if modality else "",
            "modality_options": [
                {
                    "value": p,
                    "tab": paradigms.SHORT_NAMES.get(p, p),
                    "display": paradigms.DISPLAY_NAMES.get(p, p),
                    "selected": modality == p,
                }
                for p in visible
            ],
        },
    )


@app.get("/leaderboard/{modality}", response_class=HTMLResponse)
def leaderboard_modality(
    modality: str,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    verified: bool = False,
    show_all: bool = False,
):
    """One modality's human-vote board — the destination of every hub card, and the canonical
    URL for a board (the `?paradigm=` form still works and renders the identical page).

    DECLARED AFTER `/leaderboard/judge` ON PURPOSE: Starlette matches routes in declaration
    order, so a `{modality}` route placed above it would swallow `/leaderboard/judge` and 404
    the judge board on the paradigm validation below (locked in by
    tests/test_modality_board_route.py::test_judge_route_is_not_shadowed_by_the_modality_path).

    Unknown OR app-hidden paradigms 404 rather than rendering an empty board: an internal-only
    modality (config.APP_HIDDEN_PARADIGMS) must not exist as a public surface at all."""
    if modality in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(modality):
        raise HTTPException(status_code=404, detail="Unknown modality")
    # Delegate to the single-paradigm branch of the existing handler — one board renderer, so the
    # path and query forms can never drift apart.
    return leaderboard(
        request,
        db,
        criterion=criterion,
        category=category,
        paradigm=modality,
        verified=verified,
        show_all=show_all,
    )


API_LEADERBOARD_NOTE = (
    "Every board ranks exactly ONE modality (paradigm). `rank` is always WITHIN a paradigm: "
    "BT scores from different paradigms come from disconnected match pools (models never face "
    "another modality), so a merged cross-paradigm ordering is not a statistical claim and this "
    "API does not emit one. `rows` is the concatenation of `boards` in modality order; pass "
    "?paradigm=<modality> for a single board."
)


@app.get("/api/leaderboard")
def api_leaderboard(
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    paradigm: str | None = None,
    verified: bool = False,
):
    """The JSON twin of the HTML leaderboard, under the SAME invariant: no cross-paradigm rank.

    It used to hand back one merged BT ordering ranked 1..N over every paradigm at once (and to
    ignore `?paradigm=` entirely in the `verified` branch) — the last surface still publishing a
    ranking across disconnected match pools. Now it mirrors the pages:

    * `?paradigm=<modality>` → that ONE board, freshly ranked 1..N within itself (the same
      service.finalize_rows() call the HTML board runs on the paradigm-filtered subset — no BT
      refit, no change to the ranking math). Unknown / app-hidden modalities 404, as on the page.
    * no `?paradigm=` → `boards`: one per modality, each independently ranked. `rows` stays for
      back-compat (app/client.py's `leaderboard()` reads it) as the boards concatenated in
      modality order — so every `rank` it carries is a within-paradigm one, and there are as many
      rank-1 rows as there are modalities. There is no merged rank anywhere in the response.

    `verified` is a SCOPE modifier (which votes count), never a board of its own: it swaps the row
    source and is then grouped/ranked identically.
    """
    paradigm = paradigm or None  # "" (unset filter) and None both mean "no filter"
    if paradigm is not None and (
        paradigm in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(paradigm)
    ):
        raise HTTPException(status_code=404, detail="Unknown modality")

    if verified:
        all_rows = service.verified_leaderboard_rows(db, criterion, category)
    else:
        all_rows = _leaderboard_rows(db, criterion, category, None)
    # Belt-and-braces with the generator-level hiding already applied by both row sources
    # (service.app_hidden_generator_ids covers APP_HIDDEN_PARADIGMS): an internal-only modality is
    # not a public surface, JSON included.
    all_rows = [r for r in all_rows if r.get("paradigm") not in config.APP_HIDDEN_PARADIGMS]

    # Group first, rank second. COPIES: service.finalize_rows() sorts and rewrites rank/ci_* in
    # place, so a board's re-rank must not leak back into the merged rows it came from.
    groups: dict[str, list[dict]] = {}
    for r in all_rows:
        groups.setdefault(r.get("paradigm") or "", []).append(dict(r))
    if paradigm is not None:
        keys = [paradigm]
    else:
        # Registry order first, then any leftover value (notably "" — un-backfilled generators,
        # which form ONE match pool of their own per paradigms.same_paradigm).
        keys = [p for p in paradigms.PARADIGMS if p in groups]
        keys += sorted(k for k in groups if k not in paradigms.PARADIGMS)
    boards = [
        {
            "paradigm": k or None,
            "display_name": paradigms.DISPLAY_NAMES.get(k) or "Unclassified",
            # Fresh within-paradigm 1..N (never a slice of a merged ranking).
            "rows": service.finalize_rows(groups.get(k, [])),
        }
        for k in keys
    ]
    return {
        "criterion": criterion,
        "category": category,
        "paradigm": paradigm,
        "verified": verified,
        "note": API_LEADERBOARD_NOTE,
        "boards": boards,
        "rows": [r for b in boards for r in b["rows"]],
    }


def _model_cards(db: Session, k_ids: set[int] | None) -> list[dict]:
    """Per-generator directory rows for /models: coverage stats + a WITHIN-METHOD BT score/rank,
    matched by the same unique display name `coverage_summary`/`_leaderboard_rows` compute
    internally (both derive it from `service.generator_display_names`, so matching on it is safe).
    No fabricated org/company field — `Generator` has none (name/kind/paradigm/description only).

    The `rank` is computed PER PARADIGM (the same group-then-finalize_rows pattern the /leaderboard
    route runs), over that modality's RATED entrants — never over the merged universe. The grid this
    feeds is sectioned by modality (_model_sections) for the same reason the leaderboard is: BT
    scores from different paradigms come from disconnected match pools, so one BT-descending order
    over every generator at once is not a comparison. /models used to BE that order — sorting the
    whole public grid by the merged BT number and printing it uncaveated on every card — which is
    the last cross-paradigm ranking on the site; the merged number itself is unchanged (a
    generator's BT does not depend on how rows are grouped), only what it is sorted by and how it
    is labelled.
    """
    names = service.generator_display_names(db)
    cov_by_name = {
        r["generator"]: r for r in service.coverage_summary(db, category_ids=k_ids)["generators"]
    }
    # One read of the BT universe; only the per-generator NUMBERS (bt_score/n_games) are taken from
    # it. Its own merged rank/order is deliberately discarded — see the rank pass below.
    universe = _leaderboard_rows(db, "overall", "all", None)
    bt_by_name = {r["generator"]: r for r in universe}
    by_paradigm: dict[str | None, list[dict]] = {}
    for r in universe:
        by_paradigm.setdefault(r.get("paradigm") or None, []).append(dict(r))  # copies: see below
    # Within-method rank: finalize_rows() (the boards' own ranker — no BT refit, no change to the
    # ranking math) over each modality's rated rows alone, so it starts at 1 and CI-tied models
    # share a number. Unrated entrants get no rank — they carry only the default prior.
    rank_by_name: dict[str, int] = {}
    for rows in by_paradigm.values():
        for r in service.finalize_rows([r for r in rows if r.get("n_games", 0) > 0]):
            rank_by_name[r["generator"]] = r["rank"]
    app_hidden = service.app_hidden_generator_ids(db)

    cards = []
    for g in db.execute(select(Generator)).scalars().all():
        if g.id in app_hidden:
            continue  # AgriGen internal testers: hidden from the app UI (kept in DB for analysis)
        disp_name = names.get(g.id, g.name)
        cov = cov_by_name.get(disp_name)
        if cov is None:
            continue  # gold-only / empty generators don't appear (mirrors coverage_summary)
        bt = bt_by_name.get(disp_name)
        cards.append(
            {
                "slug": g.slug,
                "name": disp_name,
                "kind": g.kind,
                "avatar": _avatar_initials(disp_name),
                "avatar_hue": _avatar_hue(g.slug or disp_name),
                "paradigm": g.paradigm,
                "paradigm_display": paradigms.DISPLAY_NAMES.get(g.paradigm, g.paradigm)
                if g.paradigm
                else "",
                "description": g.description,
                "bt_score": bt["bt_score"] if bt else None,
                "rank": rank_by_name.get(disp_name),
                "votes": cov["votes"],
                "tasks": cov["tasks"],
                "confidence": cov["confidence"],
            }
        )
    # Sorted WITHIN a modality (BT desc, unscored last) — the flat list is only ever consumed
    # per-section (_model_sections) or by slug (model_detail); it is not a ranking of its own.
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    cards.sort(
        key=lambda c: (
            c["paradigm"] is None,
            order.get(c["paradigm"], len(order)),
            c["bt_score"] is None,
            -(c["bt_score"] or 0),
            c["name"],
        )
    )
    return cards


def _model_sections(cards: list[dict], show_all: bool) -> list[dict]:
    """Group the model directory BY MODALITY, in `paradigms.PARADIGMS` order — the same spine the
    leaderboard hub uses, so /models and /leaderboard agree about what a BT score means (a rank
    within ONE method). Each section shows its rated models by default; `show_all` reveals the
    never-voted entrants, and a section whose models are all unrated keeps an honest empty state
    rather than disappearing. Generators with no paradigm land in a trailing "Unclassified"
    section (they are one match pool of their own — see paradigms.same_paradigm)."""
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    by_p: dict[str | None, list[dict]] = {}
    for c in cards:
        by_p.setdefault(c["paradigm"] or None, []).append(c)
    sections = []
    for p in sorted(by_p, key=lambda x: (x is None, order.get(x, len(order)))):
        group = by_p[p]
        rated = [c for c in group if c.get("votes", 0) > 0]
        sections.append(
            {
                "paradigm": p,
                "display": paradigms.DISPLAY_NAMES.get(p, p) if p else "Unclassified",
                "what": paradigms.WHAT_THIS_MEASURES.get(p, "") if p else "",
                "cards": group if show_all else rated,
                "model_count": len(group),
                "rated_count": len(rated),
                # An app-hidden paradigm has no public board (its generators are already filtered
                # out of `cards`, so this is belt-and-braces), and neither has "Unclassified".
                "board_url": f"/leaderboard/{p}"
                if p and p not in config.APP_HIDDEN_PARADIGMS
                else None,
            }
        )
    return sections


@app.get("/models", response_class=HTMLResponse)
def models_index(request: Request, db: Session = Depends(get_db), show_all: bool = False):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    cards = _model_cards(db, k_ids)
    # Rated-only by default: generators never voted on (votes == 0) carry only the default prior
    # BT and flood the grid — hide them behind a "Show all" toggle. Fall back to all if none rated.
    total_generators = len(cards)
    rated_cards = [c for c in cards if c.get("votes", 0) > 0]
    unrated_count = total_generators - len(rated_cards)
    sections = _model_sections(cards, show_all or not rated_cards)
    return templates.TemplateResponse(
        request,
        "models.html",
        {
            "sections": sections,
            "shown_count": sum(len(s["cards"]) for s in sections),
            "show_all": show_all,
            "unrated_count": unrated_count,
            "total_generators": total_generators,
        },
    )


# --------------------------------------------------------------- shareable result cards (#75)
# A shared model link must unfurl into a card showing that model's CURRENT standing — ranks move
# as votes land, so the image is a live route, not a baked asset. The bytes are cached in-process
# against the data they were drawn from, so a burst of unfurls redraws once.
_OG_CARD_CACHE: dict[str, tuple[str, bytes]] = {}


def _og_cache_key(db: Session, gen: Generator) -> str:
    """Everything the card can change with, in two cheap aggregates: the ratings' `updated` stamp
    (a recompute rewrites every row, so a rank shift anywhere invalidates every card) and this
    generator's own vote tally (which moves per-vote, before the next recompute)."""
    ratings_v = db.execute(select(func.max(Rating.updated))).scalar()
    votes_v = db.execute(
        select(func.sum(ModelOutput.n_comparisons)).where(ModelOutput.generator_id == gen.id)
    ).scalar()
    return f"{gen.id}:{ratings_v}:{votes_v}"


def _model_share_context(db: Session, gen: Generator, cards: list[dict] | None = None) -> dict:
    """The facts behind a model's share card + og:description.

    GLOBAL scope on purpose (`_model_cards(db, None)`): a shared link must unfurl the same way for
    everyone, not according to whichever kingdom the sharer happened to have selected.

    The rank is a WITHIN-METHOD rank and is labelled as one everywhere it appears — every board on
    this site ranks exactly one paradigm (disconnected match pools), so a bare site-wide "#2"
    would be a claim the ranking math does not back. `rank_of` counts the models actually RANKED
    in this method (finalize_rows' rated set — an unrated entrant carries only the default prior
    and is not a rung on the ladder).
    """
    if cards is None:
        cards = _model_cards(db, None)
    card = next((c for c in cards if c["slug"] == gen.slug), None)
    modality = paradigms.DISPLAY_NAMES.get(gen.paradigm, gen.paradigm) or "an unclassified method"
    name = card["name"] if card else gen.name
    votes = int(card["votes"]) if card else 0
    rank = card["rank"] if card else None
    bt_score = card["bt_score"] if card else None
    rank_of = (
        sum(1 for c in cards if c["paradigm"] == gen.paradigm and c["rank"] is not None)
        if rank
        else 0
    )
    status = service.firm_status(votes)
    standing = og.model_standing(
        modality=modality,
        rank=rank,
        rank_of=rank_of,
        bt_score=bt_score,
        votes=votes,
        firm=status["firm"],
        firm_label=status["label"],
    )
    description = og.share_description(
        name=name,
        modality=modality,
        standing=standing,
        bt_score=bt_score,
        votes=votes,
        site_name=config.SITE_NAME,
    )
    page_url = _abs_url(f"/models/{gen.slug}")
    tweet = f"{name} — {standing['headline']} on {config.SITE_NAME}."
    return {
        "name": name,
        "modality": modality,
        "bt_score": bt_score,
        "rank": rank,
        "rank_of": rank_of,
        "votes": votes,
        "firm": status["firm"],
        "firm_label": status["label"],
        "standing": standing,
        "description": description,
        "page_url": page_url,
        "og_image_url": _abs_url(f"/og/models/{gen.slug}.png"),
        "x_intent_url": (f"https://x.com/intent/post?text={quote(tweet)}&url={quote(page_url)}"),
    }


@app.get("/og/models/{slug}.png")
def model_og_card(slug: str, db: Session = Depends(get_db)):
    """The per-model Open Graph card, drawn from CURRENT data (app.og). 404s for an app-hidden
    generator exactly like /models/{slug} does — an unfurl must never leak an internal model."""
    gen = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
    if gen is None or gen.id in service.app_hidden_generator_ids(db):
        raise HTTPException(404, "Unknown generator")
    key = _og_cache_key(db, gen)
    cached = _OG_CARD_CACHE.get(slug)
    if cached is not None and cached[0] == key:
        png = cached[1]
    else:
        ctx = _model_share_context(db, gen)
        png = og.render_model_card(
            name=ctx["name"],
            modality=ctx["modality"],
            bt_score=ctx["bt_score"],
            rank=ctx["rank"],
            rank_of=ctx["rank_of"],
            votes=ctx["votes"],
            firm=ctx["firm"],
            firm_label=ctx["firm_label"],
        )
        _OG_CARD_CACHE[slug] = (key, png)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


def _leaderboard_card_facts(db: Session, kingdom: str) -> tuple[str, int, int, int]:
    """(scope_label, n_models, n_methods, votes) for a kingdom's board card. Scope label is the
    site name for the all-kingdoms view (a card headline of "All kingdoms leaderboard" reads worse
    than "Taxon3D leaderboard"); a specific kingdom keeps its own label. Vote count is the
    site-wide total, matching the leaderboard page header's own framing."""
    rows = _leaderboard_rows(db, "overall", "all", None, kingdom)
    n_methods = len({r.get("paradigm") for r in rows if r.get("paradigm")})
    scope_label = (
        config.SITE_NAME
        if kingdom == "all"
        else kingdoms.KINGDOM_LABEL.get(kingdom, config.SITE_NAME)
    )
    return scope_label, len(rows), n_methods, matchmaking.total_votes(db)


def _leaderboard_share_context(scope_label: str, kingdom: str) -> dict:
    """Share affordance for a leaderboard view: the canonical page URL (humans keep their own
    kingdom), the kingdom-scoped OG card (so the unfurl preview shows the sharer's board), and an
    X-intent. Mirrors _model_share_context so leaderboard.js reuses the same share.js handlers."""
    page_url = _abs_url("/leaderboard")
    tweet = (
        f"{scope_label} — Bradley–Terry 3D-generation rankings from blind human votes "
        f"on {config.SITE_NAME}."
    )
    return {
        "scope_label": scope_label,
        "page_url": page_url,
        "og_image_url": _abs_url(f"/og/leaderboard.png?scope={quote(kingdom)}"),
        "x_intent_url": f"https://x.com/intent/post?text={quote(tweet)}&url={quote(page_url)}",
    }


# Cached like the per-model card: the BT refit inside _leaderboard_card_facts is not free, and an
# unfurl bot may hit this repeatedly. Keyed on (kingdom, total_votes) so it self-invalidates the
# moment a vote lands.
_LB_OG_CACHE: dict[str, tuple[int, bytes]] = {}


@app.get("/og/leaderboard.png")
def leaderboard_og_card(scope: str = "all", db: Session = Depends(get_db)):
    """The leaderboard Open Graph card, kingdom-scoped, drawn from current data (app.og).

    The kingdom is passed as `scope`, NOT `kingdom`, on purpose: the http middleware reads a
    `?kingdom=` query param on every request and persists it to a cookie, so naming it `kingdom`
    here would let an unfurl of one kingdom's card flip the viewer's own board scope."""
    kingdom = kingdoms.normalize_kingdom(scope)
    total = matchmaking.total_votes(db)
    cached = _LB_OG_CACHE.get(kingdom)
    if cached is not None and cached[0] == total:
        png = cached[1]
    else:
        scope_label, n_models, n_methods, votes = _leaderboard_card_facts(db, kingdom)
        png = og.render_leaderboard_card(
            scope_label=scope_label, n_models=n_models, n_methods=n_methods, votes=votes
        )
        _LB_OG_CACHE[kingdom] = (total, png)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@app.get("/models/{slug}", response_class=HTMLResponse)
def model_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    gen = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
    if gen is None or gen.id in service.app_hidden_generator_ids(db):
        raise HTTPException(404, "Unknown generator")  # app-hidden testers: not reachable by URL
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    cards = _model_cards(db, k_ids)
    card = next((c for c in cards if c["slug"] == slug), None)
    # The share card / meta description are kingdom-INDEPENDENT (see _model_share_context); reuse
    # the cards we already have only when the current scope IS the global one.
    share = _model_share_context(db, gen, cards=cards if k_ids is None else None)

    outs = [o for o in gen.outputs if not o.is_gold and o.hidden_at is None]
    by_task: dict[int, dict] = {}
    for o in outs:
        t = o.task
        if t is None:
            continue
        row = by_task.setdefault(
            t.id,
            {
                "task": t.title,
                "category": t.category.name if t.category else "",
                "outputs": 0,
                "votes": 0,
            },
        )
        row["outputs"] += 1
        row["votes"] += o.n_comparisons
    task_rows = sorted(by_task.values(), key=lambda r: (-r["outputs"], r["task"]))

    samples = []
    for o in outs[:6]:
        samples.append(
            {
                "id": o.id,
                "title": o.title or (o.task.title if o.task else ""),
                "asset_url": storage.url_for(o.asset_path),
                "format": o.asset_format,
            }
        )

    # Head-to-head record (#74): the evidence behind the rank. Opponents are same-paradigm by
    # construction (see service.head_to_head_record) — the template must label it as a
    # within-method record, and ties are shown, not folded away.
    names = service.generator_display_names(db)
    h2h = [
        {**rec, "opponent_name": names.get(rec["opponent_id"], "Unknown")}
        for rec in service.head_to_head_record(db, gen.id, "overall", category_ids=k_ids)
    ]

    return templates.TemplateResponse(
        request,
        "model_detail.html",
        {
            "gen": gen,
            "card": card,
            "task_rows": task_rows,
            "samples": samples,
            "h2h": h2h,
            "share": share,
        },
    )


@app.get("/organisms", response_class=HTMLResponse)
def organisms_index(request: Request, db: Session = Depends(get_db)):
    """The corpus indexed by subject. Also the crawl hub for the detail pages: a crawler that
    never fetches the sitemap still reaches every organism by following links from here."""
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    rows = organisms.organism_index(db)
    grouped = []
    for kingdom in ("plants", "fungi", "animals", "all"):
        group = [r for r in rows if r["kingdom"] == kingdom]
        if group:
            grouped.append(
                (
                    {
                        "label": kingdoms.KINGDOM_LABEL.get(kingdom, kingdom),
                        "emoji": kingdoms.KINGDOM_EMOJI.get(kingdom, kingdoms.KINGDOM_EMOJI["all"]),
                    },
                    group,
                )
            )
    return templates.TemplateResponse(request, "organisms_index.html", {"grouped": grouped})


@app.get("/organisms/{slug}", response_class=HTMLResponse)
def organism_page(slug: str, request: Request, db: Session = Depends(get_db)):
    """One organism, every task set on it and every model that attempted it.

    NOT kingdom-scoped: an organism page is about that organism, and rendering it differently
    according to whichever kingdom filter the visitor happens to be carrying would give one URL
    two bodies — which is the shape a crawler reads as unstable content.
    """
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    org = organisms.build_organism(db, slug)
    if org is None:
        raise HTTPException(404, "Unknown organism")
    return templates.TemplateResponse(
        request,
        "organism.html",
        {
            "org": org,
            "description": organisms.meta_description(org),
            "breadcrumbs": organisms.breadcrumbs(org, config.PUBLIC_BASE_URL),
        },
    )


@app.get("/dataset", response_class=HTMLResponse)
def dataset_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    releases_dir = config.RELEASES_DIR
    releases = []
    if releases_dir.is_dir():
        for d in sorted(releases_dir.iterdir(), reverse=True):
            vf = d / "VERSION"
            if d.is_dir() and vf.is_file():
                releases.append({"version": d.name, "version_text": vf.read_text()})
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    composition = dataset.dataset_composition(db, k_ids)
    return templates.TemplateResponse(
        request,
        "dataset.html",
        {
            "releases": releases,
            "composition": composition,
            "dataset_ld": _dataset_jsonld(releases),
            "hf_dataset_url": config.hf_dataset_url(),
        },
    )


def _dataset_jsonld(releases: list[dict]) -> dict:
    """schema.org/Dataset for the release page — the only markup Google Dataset Search reads.

    `license` is the licences PAGE, not an SPDX identifier, and that is deliberate: a release's
    LICENSE file is a ROLLUP of per-item terms (`dataset.license_rollup`), because the corpus
    mixes CC0, several CC-BY variants and outputs whose model terms permit display but not
    redistribution. There is no single identifier that is true of the whole thing, and naming
    one would be a licence claim this project cannot support.

    `distribution` is emitted only for releases that exist. The key asserts a retrievable file,
    so on an instance with no release cut it is absent rather than pointing at a 404 — markup
    promising a download that isn't there is worse than no markup.

    The Hugging Face corpus is the only distribution here that names an actual file. Every release
    entry sets `contentUrl` to this same page, so a crawler following one lands back where it
    started and the record has no retrievable download at all. `config.HF_DATASET_REPO` is empty
    by default and gated on for exactly the reason above: an instance that has published no corpus
    must not advertise one.
    """
    ld: dict = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Taxon3D benchmark",
        "description": (
            "A blind comparison benchmark for generative 3D models of real organisms. Contains "
            "benchmark tasks across plants, fungi and animals, generated 3D outputs from "
            "multiple generation methods, baked ground-truth reference renders, CC-licensed "
            "reference photographs, and objective completeness and fidelity metrics."
        ),
        "url": f"{config.PUBLIC_BASE_URL}/dataset",
        "license": f"{config.PUBLIC_BASE_URL}/licenses",
        "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": "Jaret Arnold"},
        "keywords": [
            "3D generation",
            "benchmark",
            "plant phenotyping",
            "computer vision",
            "Bradley-Terry",
        ],
    }
    distribution: list[dict] = []
    hf_url = config.hf_dataset_url()
    if hf_url:
        distribution.append(
            {
                "@type": "DataDownload",
                "name": "Admissibility-gated organism corpus (Hugging Face)",
                "contentUrl": hf_url,
                "encodingFormat": "application/json",
            }
        )
    if releases:
        ld["version"] = releases[0]["version"]
        distribution += [
            {
                "@type": "DataDownload",
                "name": r["version"],
                "contentUrl": f"{config.PUBLIC_BASE_URL}/dataset",
            }
            for r in releases
        ]
    if distribution:
        ld["distribution"] = distribution
    return ld


@app.get("/methodology", response_class=HTMLResponse)
def methodology_page(request: Request):
    return templates.TemplateResponse(
        request,
        "methodology.html",
        {
            "rate_limit": config.VOTE_RATE_LIMIT,
            "rate_window": int(config.VOTE_RATE_WINDOW),
            "gold_rate": config.GOLD_RATE,
            "trust_threshold": config.TRUST_THRESHOLD,
            "require_captcha": config.REQUIRE_CAPTCHA,
        },
    )


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/licenses", response_class=HTMLResponse)
def licenses_page(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ModelOutput.license, ModelOutput.attribution, ModelOutput.source).distinct()
    ).all()
    return templates.TemplateResponse(request, "licenses.html", {"licenses": rows})


@app.get("/coverage", response_class=HTMLResponse)
def coverage_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    summary = service.coverage_summary(db, category_ids=k_ids)
    return templates.TemplateResponse(
        request,
        "coverage.html",
        {
            "generators": summary["generators"],
            "tasks": summary["tasks"],
            "by_paradigm": summary.get("by_paradigm", {}),
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "firm_threshold": service.FIRM_VOTE_THRESHOLD,
            "trait_board": service.trait_leaderboard(db),
            "mode_c_experimental": not service.accepted_trait_classes(db),
        },
    )


@app.get(
    "/research",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def research_hub(request: Request):
    """Single sidebar entry point for the analysis boards, which used to occupy three
    separate top-level slots (/benchmark, /significance, /difficulty) while /fidelity and
    /procedural had no nav entry at all. Static links only — each board loads its own data,
    so the hub adds no queries."""
    return templates.TemplateResponse(request, "research.html", {})


@app.get(
    "/procedural",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def procedural_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "procedural.html",
        {"rows": service.procedural_scorecard(db)},
    )


@app.get(
    "/fidelity",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def fidelity_board(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "fidelity.html", {"board": fidelity.fidelity_scorecard(db)}
    )


@app.get(
    "/significance",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def significance_page(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    show_all: bool = False,
):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    category_id = _resolve_category_id(db, category)
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    # Rated-only by default: a never-voted generator has no significance signal and floods the
    # forest plot + P(A>B) matrix. `?show_all=true` includes them.
    if k_ids is not None:
        sig = service.compute_significance(
            db,
            criterion,
            category_ids=_effective_category_ids(k_ids, category_id),
            rated_only=not show_all,
        )
    else:
        sig = service.compute_significance(db, criterion, category_id, rated_only=not show_all)
    # Forest-plot CI bounds: REUSE the leaderboard's cached BT confidence interval per
    # generator (same kingdom scoping the leaderboard route branches on) rather than
    # computing new stats — bt_lower/bt_upper are absolute values so merging rows across
    # paradigm groups here is safe even though _leaderboard_rows computes rank/percent
    # geometry per-paradigm internally. A generator sig.ranked knows about but that has no
    # leaderboard row (scope mismatch) simply has no entry; the template renders it as a
    # bare point rather than fabricating an interval.
    lb_rows = _leaderboard_rows(db, criterion, category, None, request.state.kingdom)
    ci_map = {r["generator"]: (r["bt_lower"], r["bt_upper"]) for r in lb_rows}
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    category_options = [
        {
            "slug": "all",
            "name": "All categories",
            "selected": category == "all",
            "coming_soon": False,
        }
    ]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug, "coming_soon": not c.tasks}
        for c in cats
    ]
    criterion_options = [
        {"slug": c.slug, "name": c.name, "selected": criterion == c.slug} for c in crits
    ]
    return templates.TemplateResponse(
        request,
        "significance.html",
        {
            "sig": sig,
            "bias": service.compute_bias(db),
            "category_options": category_options,
            "criterion_options": criterion_options,
            "ci_map": ci_map,
            "show_all": show_all,
            "sel_criterion": criterion,
            "sel_category": category,
        },
    )


# ------------------------------------------------------------------------ tasks


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db)):

    from .models import ReconTask, TaskDifficulty

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    # Eager-loaded because every row below walks `t.outputs` (twice: the count and
    # `_paradigm_label`) and `t.category`, and each output's `.generator`. Left lazy that is a
    # round trip per task and per output — this page measured 137 statements on the real
    # corpus, the same defect as service._scope_rows one layer up.
    stmt = (
        select(Task)
        .order_by(Task.id)
        .options(
            joinedload(Task.category),
            selectinload(Task.outputs).joinedload(ModelOutput.generator),
        )
    )
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = db.execute(stmt).unique().scalars().all()
    task_ids = [t.id for t in tasks]

    # Real vote totals per task (non-gold decisive votes) — reused for both the per-row
    # VOTES column and the "votes across tasks" stat card, so the two numbers can never
    # silently disagree.
    vote_counts: dict[int, int] = (
        dict(
            db.execute(
                select(Comparison.task_id, func.count(Vote.id))
                .select_from(Vote)
                .join(Comparison, Vote.comparison_id == Comparison.id)
                .where(Comparison.is_gold.is_(False), Comparison.task_id.in_(task_ids))
                .group_by(Comparison.task_id)
            ).all()
        )
        if task_ids
        else {}
    )

    # Difficulty tier, keyed by task — same table/join the /difficulty page reads from.
    # Tasks without a curated row simply have no tier (shown as "—"), never a guess.
    tier_by_task: dict[int, str] = (
        dict(
            db.execute(
                select(TaskDifficulty.task_id, TaskDifficulty.tier).where(
                    TaskDifficulty.task_id.in_(task_ids)
                )
            ).all()
        )
        if task_ids
        else {}
    )

    # Latin binomial, keyed by task — same ReconTask.species_name the /difficulty page's
    # tier_species header line reads. Tasks with no recon-GT bundle fall back to the title.
    species_by_task: dict[int, str] = (
        dict(
            db.execute(
                select(ReconTask.task_id, ReconTask.species_name).where(
                    ReconTask.task_id.in_(task_ids)
                )
            ).all()
        )
        if task_ids
        else {}
    )

    # Distinct paradigms actually exercised by non-gold outputs in scope — the third stat
    # card. Omitted entirely (not zeroed) if nothing is tagged, so the card never fakes 0.
    paradigm_stmt = (
        (
            select(Generator.paradigm)
            .distinct()
            .join(ModelOutput, ModelOutput.generator_id == Generator.id)
            .where(ModelOutput.is_gold.is_(False), ModelOutput.task_id.in_(task_ids))
        )
        if task_ids
        else None
    )
    paradigms_exercised = (
        {p for p in db.execute(paradigm_stmt).scalars().all() if p}
        if paradigm_stmt is not None
        else set()
    )

    def _paradigm_label(t: Task) -> str:
        tagged = {o.generator.paradigm for o in t.outputs if not o.is_gold and o.generator.paradigm}
        if len(tagged) == 1:
            return paradigms.DISPLAY_NAMES.get(next(iter(tagged)), next(iter(tagged)))
        if len(tagged) > 1:
            return "Multiple"
        return "—"

    # Organisms that actually HAVE a page. This catalog lists retired tasks alongside live ones
    # (see the `active` field below) but an organism page is built from the LIVE corpus only, so
    # linking every row's subject unconditionally invents links to pages that do not exist —
    # `cucurbita-pepo` (the de-corpused pumpkin) and `hordeum-vulgare` on the real corpus.
    # Reading the set the /organisms hub renders means the link exists iff its target does.
    linkable_organisms = {o["slug"] for o in organisms.organism_index(db)}

    rows = []
    for t in tasks:
        cat = t.category
        kingdom = kingdoms.KINGDOM_OF.get(cat.slug, "all")
        species_name = species_by_task.get(t.id) or ""
        organism_slug = organisms.url_slug(organisms.binomial_of_title(t.title))
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "category": cat.name,
                "n_outputs": len(t.outputs),
                "active": t.active,
                "kingdom_emoji": kingdoms.KINGDOM_EMOJI.get(kingdom, kingdoms.KINGDOM_EMOJI["all"]),
                "species_name": species_name,
                # Derived from the title, not from `species_by_task`: only 5 of the 20 active
                # tasks carry a ReconTask row, so keying the link off that would leave three
                # quarters of the catalog unlinked. None when the organism has no live page.
                "organism_slug": organism_slug if organism_slug in linkable_organisms else None,
                "paradigm": _paradigm_label(t),
                "tier": tier_by_task.get(t.id),
                "votes": vote_counts.get(t.id, 0),
            }
        )
    stats = {
        "live_tasks": sum(1 for t in tasks if t.active),
        "votes_total": sum(vote_counts.values()) if vote_counts else None,
        "n_paradigms": len(paradigms_exercised) if paradigms_exercised else None,
    }
    return templates.TemplateResponse(request, "tasks.html", {"tasks": rows, "stats": stats})


@app.get(
    "/spotlight",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def spotlight_index(request: Request, db: Session = Depends(get_db)):
    from . import spotlight

    subjects = sorted(spotlight.SPOTLIGHTS, key=lambda s: (not s["featured"], s["order"]))
    kingdom = kingdoms.normalize_kingdom(request.state.kingdom)
    if kingdom != "all":
        subjects = [s for s in subjects if s.get("kingdom") == kingdom]
    counts = spotlight.model_counts(db)
    subjects = [{**s, "model_count": counts.get(s["slug"])} for s in subjects]
    return templates.TemplateResponse(request, "spotlight_index.html", {"subjects": subjects})


@app.get(
    "/spotlight/{slug}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def spotlight_page(slug: str, request: Request, db: Session = Depends(get_db)):
    from . import spotlight

    data = spotlight.build_spotlight(db, slug)
    if data is None:
        raise HTTPException(404, "spotlight not found")
    return templates.TemplateResponse(request, "spotlight.html", {"s": data})


@app.get(
    "/difficulty",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def difficulty_page(request: Request, db: Session = Depends(get_db)):
    """Render the per-tier objective scorecard + the cross-tier degradation gradient."""
    from .models import ReconTask, Task, TaskDifficulty

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    scorecard = difficulty.tier_scorecard(db, category_ids=k_ids)
    tiers = list(difficulty.TIERS)

    # Species sitting in each tier (for the per-tier header line).
    tier_species: dict[str, list[str]] = {}
    stmt = (
        select(TaskDifficulty, Task, ReconTask)
        .join(Task, Task.id == TaskDifficulty.task_id)
        .join(ReconTask, ReconTask.task_id == TaskDifficulty.task_id, isouter=True)
    )
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    rows = db.execute(stmt).all()
    for td, task, rt in rows:
        name = (rt.species_name if rt else None) or task.title
        tier_species.setdefault(td.tier, []).append(name)

    # Cross-tier gradient: generators scored in >=2 tiers, mean chamfer per tier — this is the
    # headline (does a method degrade easy→moderate→hard?).
    by_gen: dict[str, dict[str, float | None]] = {}
    for block in scorecard:
        if block["tier"] not in tiers:
            continue
        for r in block["rows"]:
            if r["mean_chamfer"] is not None:
                by_gen.setdefault(r["generator"], {})[block["tier"]] = r["mean_chamfer"]
    gradient = [
        {"generator": gen, "chamfer": {t: ch.get(t) for t in tiers}}
        for gen, ch in by_gen.items()
        if sum(1 for t in tiers if ch.get(t) is not None) >= 2
    ]
    gradient.sort(key=lambda g: g["generator"].lower())

    # perceptual/trait_tiers/reliability stay global (JudgeVote/TraitScore paths have no
    # category-scoping wired yet — see report for the explicit scoped-vs-global split).
    perceptual = service.tier_perceptual_ranking(db)
    trait_tiers = service.tier_trait_accuracy(db)
    paradigm_grid = difficulty.paradigm_tier_scorecard(db, category_ids=k_ids)
    # Reference/capture-quality triage: taxa where recon completeness is far below text (the
    # recon INPUT is suspect). Sorted by gap desc; flagged ones shown first.
    from .completeness import recon_reliability_flags

    reliability = recon_reliability_flags(db)

    return templates.TemplateResponse(
        request,
        "difficulty.html",
        {
            "scorecard": scorecard,
            "tier_species": tier_species,
            "tiers": tiers,
            "gradient": gradient,
            "perceptual": perceptual,
            "trait_tiers": trait_tiers,
            "paradigm_grid": paradigm_grid,
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "reliability": reliability,
        },
    )


@app.get(
    "/trait/{output_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def trait_scorecard_page(output_id: int, request: Request, db: Session = Depends(get_db)):
    """Per-output Mode-C scorecard: each verdict joined to its rubric trait + the score."""
    from .models import TraitRubric, TraitScore, TraitVerdict

    output = db.get(ModelOutput, output_id)
    if output is None:
        raise HTTPException(404, "Unknown output")

    rubric = (
        db.execute(select(TraitRubric).where(TraitRubric.task_id == output.task_id))
        .scalars()
        .first()
    )
    rubric_by_key: dict[str, dict] = {}
    if rubric is not None:
        try:
            for t in json.loads(rubric.traits_json or "[]"):
                rubric_by_key[t.get("key")] = t
        except json.JSONDecodeError:
            pass

    accepted = service.accepted_trait_classes(db)
    verdicts = (
        db.execute(select(TraitVerdict).where(TraitVerdict.output_id == output_id)).scalars().all()
    )
    rows = []
    for v in verdicts:
        meta = rubric_by_key.get(v.trait_key, {})
        rows.append(
            {
                "trait_key": v.trait_key,
                "trait_class": v.trait_class,
                "expected": meta.get("expected", "—"),
                "citation": meta.get("citation", "—"),
                "verdict": v.verdict,
                "rationale": v.rationale,
                "calibrated": v.trait_class in accepted,
            }
        )
    rows.sort(key=lambda r: (not r["calibrated"], r["trait_class"], r["trait_key"]))

    score = (
        db.execute(select(TraitScore).where(TraitScore.output_id == output_id)).scalars().first()
    )
    task = db.get(Task, output.task_id)
    return templates.TemplateResponse(
        request,
        "trait_scorecard.html",
        {
            "output_id": output_id,
            "task": task,
            "taxon": rubric.taxon if rubric else None,
            "rows": rows,
            "score": score,
            "mode_c_experimental": not accepted,
        },
    )


@app.get("/healthz")
def healthz():
    """LIVENESS only — is this process serving? Touches nothing external, by design.

    fly.toml points its health check here with a 5s timeout and a 30s interval. Anything slow in
    this handler becomes a failed check, and a failed check gets the machine killed and
    restarted. A database probe therefore does NOT belong here: a merely SLOW database would
    take down a process that is serving correctly, which is the same "dependency outage becomes
    total outage" failure that motivated this work in the first place. Database state lives on
    /readyz.
    """
    return {"status": "ok", "version": app.version}


@app.get("/readyz")
def readyz():
    """READINESS — can this process actually do its job? Probes the database.

    Separate from /healthz so that monitoring can alert on a real outage without the platform
    treating the same signal as "kill this machine". Still returns 200 with `database: down`
    rather than a failure status, for the same reason.
    """
    db_state = "ok"
    try:
        with SessionLocal() as _db:
            _db.execute(sa_text("SELECT 1"))
    except Exception:  # noqa: BLE001 — reporting the outage IS this endpoint's job
        logger.warning("readyz: database unreachable", exc_info=True)
        db_state = "down"
    return {
        "status": "ok",
        "version": app.version,
        "database": db_state,
        "schema_initialized": DB_SCHEMA_READY,
    }


# The routers. No route in one router can shadow a route in another (every parameterised path
# lives beside its static siblings in the same router — /media/o/... in vote, /admin/... in
# admin, /leaderboard/... and /models/... here), so only the order WITHIN each router matters,
# and each keeps the relative order the routes had when they were declared in this file.
app.include_router(admin_routes.router)
app.include_router(vote_routes.router)
app.include_router(research_routes.router)


@app.exception_handler(OperationalError)
async def _database_unavailable(request: Request, exc: OperationalError):
    """Answer a database outage with 503 and a readable page, not a 500 and a traceback.

    The distinction is not cosmetic: 503 means "try later" and 500 means "broken". Crawlers
    treat them differently, and a site being indexed cannot afford 500s. Visitors cannot act on
    a stack trace either, and it should not be shown to them.
    """
    logger.error("database unavailable serving %s", request.url.path, exc_info=exc)
    wants_json = request.url.path.startswith("/api") or "application/json" in request.headers.get(
        "accept", ""
    )
    if wants_json:
        return JSONResponse(
            {"error": "database-unavailable", "detail": "Temporarily unavailable."},
            status_code=503,
        )
    return HTMLResponse(_OUTAGE_PAGE, status_code=503)
