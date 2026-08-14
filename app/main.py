import os
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

# Import your routes (ensure these also don't load heavy AI models at module top-level!)
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

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    logger.info("Initializing persistent Supabase Client for Web API...")

    if supabase_url and supabase_key:
        supabase_client = create_client(supabase_url, supabase_key)
        app.state.system = SystemStateContainer(supabase_client)
        app.state.supabase = supabase_client

    # EXACTLY ONE YIELD separating startup from shutdown
    yield  

    # --- SHUTDOWN PHASE ---
    logger.info("Tearing down web service resources cleanly...")


app = FastAPI(
    title="HousePadi Enterprise Core Gateway",
    version="1.0.0",
    docs_url="/api/v1/docs",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


# --- Internal Cron / Enrichment Router ---
internal_router = APIRouter(prefix="/api/internal", tags=["Internal"])

@internal_router.post("/run-enrichment")
async def run_enrichment(x_internal_secret: str = Header(None)):
    expected_secret = os.getenv("INTERNAL_CRON_SECRET", "house-padi-super-secret")
    if x_internal_secret != expected_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    try:
        # Lazy-import property_cron here so it only loads into memory when the cron endpoint is called, 
        # completely preventing startup blocking and port timeouts on Render.
        from app.services import property_cron
        await property_cron.run_pending_property_enrichment_sweep()
        return {"status": "success", "message": "AI enrichment sweep completed."}
    except Exception as e:
        logger.error(f"Error in enrichment sweep: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Register the internal router to the app
app.include_router(internal_router)