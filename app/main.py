import os
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

# Import your routes
from app.routes import auth_routes, property_routes, chat_routes
# Import model loader
from app.services.vector_service import get_model

logger = getLogger("uvicorn")


# 1. Define Container
class SystemStateContainer:

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client


# 2. Define Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP PHASE ---
    current_dir = Path(__file__).resolve().parent
    env_path = current_dir.parent / ".env"
    
    # Load the environment variables
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logger.info(f"Loaded environment variables from {env_path}")
    else:
        logger.warning(".env file not found, relying on system environment variables.")

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("CRITICAL CONFIG ERROR: Missing SUPABASE_URL or SUPABASE_KEY.")
        supabase_url = "https://placeholder-url.supabase.co"
        supabase_key = "placeholder-anon-key"

    logger.info("Initializing persistent Supabase Client...")
    supabase_client = create_client(supabase_url, supabase_key)
    app.state.system = SystemStateContainer(supabase_client=supabase_client)

    logger.info("Pre-warming semantic model memory...")
    get_model()

    yield 
    
    # --- SHUTDOWN PHASE ---
    logger.info("Tearing down service resources cleanly...")


# 3. Initialize App
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

# 4. Register Routes
app.include_router(auth_routes.router)
app.include_router(property_routes.router)
app.include_router(chat_routes.router)


@app.get("/")
async def root_health_check():
    return {
        "status": "online",
        "service": "HousePadi Backend Engine",
        "version": "1.0.0"
    }
