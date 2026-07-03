"""FastAPI application entry point."""
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
# Importing models registers them on SQLModel.metadata (needed by Alembic +
# init_db). The side-effect import is intentional; do not remove.
from app import models  # noqa: F401
from app.api import auth_router, bills_router, kyc_router, wallet_router, webhooks_router
from app.api.health import router as health_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.metrics import HTTP_REQUESTS, HTTP_REQUEST_SECONDS
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.telegram import (
    start_bot,
    stop_bot,
    webhook_router as telegram_webhook_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # Background workers (single process by design — see Dockerfile).
    # Order matters: scheduler first so a job that fires on startup
    # doesn't race with bot startup; bot second so a Telegram update
    # that triggers DB work sees a ready connection.
    start_scheduler()
    await start_bot()
    try:
        yield
    finally:
        await stop_bot()
        stop_scheduler()


app = FastAPI(
    title="AutoPay AI",
    version=__version__,
    description="AI-powered bill automation for Nigerian users.",
    lifespan=lifespan,
)


# ── HTTP request metrics middleware ─────────────────────────────────
# Wraps every request in a timer and increments the
# `app_http_requests_total` counter. The route label uses the URL
# path *template* (e.g. `/api/v1/bills/{bill_id}`), not the resolved
# path, so high-cardinality URLs don't blow up the metric.
@app.middleware("http")
async def _http_metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        # The exception handler will turn this into a 500 response;
        # count it as 500 so we don't lose the signal.
        status_code = 500
        raise
    finally:
        elapsed = time.perf_counter() - started
        # `request.scope["route"].path` is the templated path
        # (e.g. `/api/v1/bills/{bill_id}`); fall back to the raw path
        # if the request didn't match a route (404s on unknown paths).
        route = request.scope.get("route")
        path_template = getattr(route, "path", request.url.path)
        method = request.method
        try:
            HTTP_REQUESTS.labels(
                method=method,
                route=path_template,
                status=str(status_code),
            ).inc()
            HTTP_REQUEST_SECONDS.labels(
                method=method,
                route=path_template,
            ).observe(elapsed)
        except Exception:  # noqa: BLE001
            # Never let metrics collection break a request.
            pass
    return response


# Health (unversioned — used by Docker, k8s, load balancers)
app.include_router(health_router)

# Versioned API
app.include_router(auth_router,    prefix="/api/v1/auth")
app.include_router(bills_router,   prefix="/api/v1/bills")
app.include_router(kyc_router,     prefix="/api/v1/kyc")
app.include_router(wallet_router,  prefix="/api/v1/wallet")

# Webhooks (unversioned — provider-side; Paystack + Telegram hardcode the URL)
app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(telegram_webhook_router)

# ── SPA + static files ───────────────────────────────────────────────
# The frontend is a hash-routed SPA (/#/dashboard, /#/bills, etc.).
# The browser only ever requests "/" for HTML; the hash fragment is
# client-side only. Static assets (CSS, JS) live under /static/.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@app.get("/", include_in_schema=False)
async def serve_index():
    """Serve the SPA shell at the root path."""
    return FileResponse(str(_TEMPLATE_DIR / "index.html"))


# Mount /static AFTER all API routes so /api/v1/*, /webhooks/*,
# /healthz, etc. are matched first. StaticFiles is a sub-app so
# it only handles requests whose path starts with /static/.
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


