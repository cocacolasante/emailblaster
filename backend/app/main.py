import logging
import time

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings, validate_required_settings
from app.database import get_tenant_context
from app.services.credentials import MissingCredential
from app.services.ownership import NotAMember
from app.routers import (
    agent,
    auth,
    owners,
    team,
    icp,
    intent_profiles,
    signals,
    analytics,
    campaigns,
    connected_accounts,
    leads,
    linkedin_accounts,
    preview,
    report_builder,
    reports,
    research_client,
    sequences,
    social_radar,
    settings as settings_router,
    webhooks,
    crm,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Email Blaster",
    version="0.1.0",
)


# Fail fast on missing API keys / insecure defaults.  Without this the
# app boots happily with an empty ANTHROPIC_API_KEY and only errors at
# the first compose attempt — long after the user has uploaded leads
# and started a campaign.  Running this at import time means
# ``docker compose up`` prints the problem and exits non-zero.
try:
    validate_required_settings()
except Exception as exc:  # noqa: BLE001
    logger.critical("Config validation failed: %s", exc)
    raise

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_EXEMPT_PREFIXES = ("/webhooks/", "/unsubscribe/")


@app.middleware("http")
async def csrf_origin_check(request: Request, call_next):
    """Defence in depth on top of SameSite=Lax cookies: a state-changing
    request that carries a session cookie AND an Origin header must come
    from the frontend origin.  (Non-browser clients send no Origin.)"""
    if (
        request.method in _UNSAFE_METHODS
        and "eb_session" in request.cookies
        and not request.url.path.startswith(_CSRF_EXEMPT_PREFIXES)
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings.FRONTEND_URL.rstrip("/"):
            return JSONResponse(status_code=403, content={"detail": "cross-origin request blocked"})
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Emit a single info line per request: method, path, status, duration."""
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %d (%.1fms)",
            request.method, request.url.path, response.status_code, duration_ms,
        )
        return response
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "%s %s -> error after %.1fms",
            request.method, request.url.path, duration_ms,
        )
        raise


@app.exception_handler(MissingCredential)
async def missing_credential_handler(request: Request, exc: MissingCredential):
    """A feature needed a provider the workspace hasn't connected.  409 with
    a machine-readable code so the frontend can deep-link to Integrations."""
    return JSONResponse(
        status_code=409,
        content={
            "error": "integration_not_configured",
            "provider": exc.provider,
            "detail": str(exc),
        },
    )


@app.exception_handler(NotAMember)
async def not_a_member_handler(request: Request, exc: NotAMember):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all for truly unexpected failures.

    FastAPI's built-in HTTPException + RequestValidationError handlers run
    BEFORE this one; their responses keep the standard ``{"detail": ...}``
    shape clients already depend on. This handler only fires when something
    propagates past those.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    # Starlette's ServerErrorMiddleware wraps this handler OUTSIDE CORSMiddleware,
    # so responses from here would otherwise lack CORS headers — making 500s
    # surface in the browser as opaque CORS errors instead of real failures.
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if origin and origin == settings.FRONTEND_URL:
        headers["access-control-allow-origin"] = origin
        headers["access-control-allow-credentials"] = "true"
        headers["vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": "An internal error occurred"},
        headers=headers,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}


# Every feature router is authenticated + tenant-scoped router-wide; only
# auth (login/signup/invite) and webhooks/unsubscribe are public.
_AUTHED = [Depends(get_tenant_context)]
app.include_router(auth.router)
app.include_router(team.router, dependencies=_AUTHED)
app.include_router(owners.router, dependencies=_AUTHED)
app.include_router(campaigns.router, dependencies=_AUTHED)
app.include_router(leads.router, dependencies=_AUTHED)
app.include_router(preview.router, dependencies=_AUTHED)
app.include_router(analytics.router, dependencies=_AUTHED)
app.include_router(webhooks.router)
app.include_router(connected_accounts.router, dependencies=_AUTHED)
app.include_router(linkedin_accounts.router, dependencies=_AUTHED)
app.include_router(settings_router.router, dependencies=_AUTHED)
app.include_router(sequences.router, dependencies=_AUTHED)
app.include_router(research_client.router, dependencies=_AUTHED)
app.include_router(social_radar.router, dependencies=_AUTHED)
app.include_router(crm.router, dependencies=_AUTHED)
app.include_router(reports.router, dependencies=_AUTHED)
app.include_router(report_builder.router, dependencies=_AUTHED)
app.include_router(agent.router, dependencies=_AUTHED)
app.include_router(signals.router, dependencies=_AUTHED)
app.include_router(icp.router, dependencies=_AUTHED)
app.include_router(intent_profiles.router, dependencies=_AUTHED)
