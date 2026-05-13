import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import (
    analytics,
    campaigns,
    connected_accounts,
    leads,
    linkedin_accounts,
    preview,
    sequences,
    settings as settings_router,
    webhooks,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


app.include_router(campaigns.router)
app.include_router(leads.router)
app.include_router(preview.router)
app.include_router(analytics.router)
app.include_router(webhooks.router)
app.include_router(connected_accounts.router)
app.include_router(linkedin_accounts.router)
app.include_router(settings_router.router)
app.include_router(sequences.router)
