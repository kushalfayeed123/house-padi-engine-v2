import os
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

# Import your routes
from app.routes import (
    application_routes, auth_routes, landlord_routes, lease_routes, 
    payment_routes, profile_routes, property_routes, chat_routes, tour_routes
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

# Register Routes
app.include_router(auth_routes.router)
app.include_router(property_routes.router)
app.include_router(chat_routes.router)
app.include_router(profile_routes.router)
app.include_router(tour_routes.router)
app.include_router(application_routes.router)
app.include_router(lease_routes.router)
app.include_router(payment_routes.router)
app.include_router(landlord_routes.router)


@app.get("/")
async def root_health_check():
    return {
        "status": "online",
        "service": "HousePadi Backend Engine",
        "version": "1.0.0"
    }