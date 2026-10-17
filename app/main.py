import os
import uuid
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, APIRouter, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from supabase import create_client, Client

# Import your routes (ensure these also don't load heavy AI models at module top-level!)
from app.core.config import settings
from app.routes import (
    application_routes, auth_routes, landlord_routes, lease_routes,
    payment_routes, profile_routes, property_routes, chat_routes, renter_routes, tour_routes
)

logger = getLogger("uvicorn")
load_dotenv()


class SystemStateContainer:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP PHASE ---
    current_dir = Path(__file__).resolve().parent
    env_path = current_dir.parent / ".env"

    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logger.info(f"Loaded environment variables from {env_path}")
    else:
        logger.warning(".env file not found, relying on system environment variables.")

    # Fail fast (and clearly) on unsafe production configuration.
    problems = settings.validate_for_production()
    if problems:
        message = "Invalid production configuration:\n  - " + "\n  - ".join(problems)
        if settings.is_production:
            raise RuntimeError(message)
        logger.warning(message)

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    logger.info("Initializing persistent Supabase Client for Web API...")

    if supabase_url and supabase_key:
        supabase_client = create_client(supabase_url, supabase_key)
        app.state.system = SystemStateContainer(supabase_client)
        app.state.supabase = supabase_client
    else:
        logger.error(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing — the API will start "
            "but authenticated endpoints will fail."
        )

    # EXACTLY ONE YIELD separating startup from shutdown
    yield

    # --- SHUTDOWN PHASE ---
    logger.info("Tearing down web service resources cleanly...")


app = FastAPI(
    title="HousePadi Enterprise Core Gateway",
    version="1.0.0",
    docs_url="/api/v1/docs",
    lifespan=lifespan,
)

# CORS: explicit allow-list from config. Never combine '*' with credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "status": "error",
            "code": "validation_error",
            "message": "Request payload failed validation.",
            "details": exc.errors(),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    # Log the real error server-side; never leak internals to the client.
    logger.error(f"[UNHANDLED] request_id={request_id} {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "code": "internal_error",
            "message": "An unexpected error occurred. Please try again.",
            "request_id": request_id,
        },
    )


# Register Main Routes
app.include_router(auth_routes.router)
app.include_router(property_routes.router)
app.include_router(chat_routes.router)
app.include_router(profile_routes.router)
app.include_router(tour_routes.router)
app.include_router(application_routes.router)
app.include_router(lease_routes.router)
app.include_router(payment_routes.router)
app.include_router(landlord_routes.router)
app.include_router(renter_routes.router)


@app.get("/")
async def root_health_check():
    return {
        "status": "online",
        "service": "HousePadi Backend Engine",
        "version": "1.0.0"
    }


@app.get("/healthz")
async def liveness():
    """Liveness probe: process is up. Must not depend on downstreams."""
    return {"status": "ok"}


@app.get("/readyz")
async def readiness(request: Request):
    """Readiness probe: can we reach our datastore?"""
    supabase = getattr(request.app.state, "supabase", None)
    if supabase is None:
        return JSONResponse(status_code=503, content={"status": "error", "code": "not_configured"})
    try:
        supabase.table("profiles").select("id").limit(1).execute()
    except Exception as exc:
        logger.error(f"[READY] datastore check failed: {exc}")
        return JSONResponse(status_code=503, content={"status": "error", "code": "datastore_unavailable"})
    return {"status": "ready"}


# --- Internal Cron / Enrichment Router ---
internal_router = APIRouter(prefix="/api/internal", tags=["Internal"])


@internal_router.post("/run-enrichment")
async def run_enrichment(background_tasks: BackgroundTasks, x_internal_secret: str = Header(None)):
    expected_secret = settings.INTERNAL_CRON_SECRET
    if not expected_secret or x_internal_secret != expected_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    try:
        # Lazy-import property_cron
        from app.services import property_cron

        # Hand off the task to FastAPI's background thread/task manager
        background_tasks.add_task(property_cron.run_pending_property_enrichment_sweep)

        # Returns 200 OK immediately while the sweep runs in the background
        return {"status": "success", "message": "AI enrichment sweep triggered in the background."}

    except Exception as e:
        logger.error(f"Error triggering enrichment sweep: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to trigger enrichment sweep.")


# Register the internal router to the app
app.include_router(internal_router)
