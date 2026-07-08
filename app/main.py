
import json
import os
from contextlib import asynccontextmanager
from logging import getLogger
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

from app.agent_engine import housepadi_agent_graph
from langchain.agents.middleware.types import InputAgentState

from app.vector_service import get_model

logger = getLogger("uvicorn")


class SystemStateContainer:

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP PHASE (Runs before the app goes live) ---
    
    # 1. Target the absolute file location
    current_dir = Path(__file__).resolve().parent
    env_path = current_dir.parent / ".env"
    logger.info(f"Explicitly targeting env configuration file path context: {env_path}")
    
    # 2. Pure Python Fallback parser to completely bypass python-dotenv issues
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines or comment lines
                    if not line or line.startswith("#"):
                        continue
                    # Parse standard KEY=VALUE lines
                    if "=" in line:
                        key, val = line.split("=", 1)
                        # Clean up surrounding whitespaces or raw quote marks
                        key = key.strip()
                        val = val.strip().strip("'").strip('"')
                        
                        # Inject directly into application environment runtime mapping
                        os.environ[key] = val
                        logger.info(f"Manually registered config key context: {key}")
        except Exception as file_err:
            logger.error(f"Failed to read .env file manually: {str(file_err)}")

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        logger.error("CRITICAL CONFIG ERROR: Missing SUPABASE_URL or SUPABASE_KEY in system environment.")
        logger.error(f"Execution working dir path: {os.getcwd()}")
        logger.error(f"Does targeted .env file physically exist? -> {env_path.exists()}")
        
        # Fallback structures
        supabase_url = supabase_url or "https://placeholder-url.supabase.co"
        supabase_key = supabase_key or "placeholder-anon-key"

    # 3. Initialize dependency pool and register to global state
    logger.info("Initializing persistent Supabase Client connection instance pool...")
    supabase_client = create_client(supabase_url, supabase_key)
    app.state.system = SystemStateContainer(supabase_client=supabase_client)

    # 4. Pre-warm semantic memory weights
    logger.info("Pre-warming semantic model memory...")
    get_model()

    # ------------------------------------------------------------------
    yield  # Hand over control to FastAPI. Server is now officially ONLINE.
    # ------------------------------------------------------------------

    # --- SHUTDOWN PHASE (Runs when the server is stopping) ---
    logger.info("Tearing down service resources cleanly...")


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

# --- Input Schema Contracts ---


class AuthCredentials(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    phone: str | None = None
    role: Literal['landlord', 'renter', 'admin']

# --- REST Endpoint Routes ---


@app.get("/")
async def root_health_check():
    return {
        "status": "online",
        "service": "HousePadi Backend Engine",
        "version": "2.0.0",
        "documentation": "/docs"
    }


@app.post("/api/auth/register")
async def register_user(body: RegisterRequest):
    supabase = app.state.system.supabase
    try:
        # Route metadata directly into GoTrue options mapping context
        signup_response = supabase.auth.sign_up({
            "email": body.email,
            "password": body.password,
            "options": {
                "data": {
                    "full_name": body.full_name,
                    "phone": body.phone,
                    "role": body.role
                }
            }
        })
        
        # Guard checking if account requires confirming a link via email confirmation first
        is_confirmed = signup_response.session is not None

        return {
            "status": "success",
            "message": "User registration initialization processed successfully.",
            "requires_email_confirmation": not is_confirmed,
            "user": {
                "id": signup_response.user.id,
                "email": signup_response.user.email,
                "user_metadata": signup_response.user.user_metadata
            },
            # Return tokens immediately if Supabase email confirmation rule is toggled off
            "access_token": signup_response.session.access_token if is_confirmed else None,
            "refresh_token": signup_response.session.refresh_token if is_confirmed else None
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"User registration failure trace caught: {error_msg}")
        
        # Intercept duplicate registration attempts cleanly
        if "already registered" in error_msg.lower() or "user_already_exists" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email address already exists."
            )
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration request execution failed: {error_msg}"
        )


@app.post("/api/auth/login")
async def login_user(body: AuthCredentials):
    supabase = app.state.system.supabase
    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": body.email,
            "password": body.password
        })
        
        return {
            "access_token": auth_response.session.access_token,
            "refresh_token": auth_response.session.refresh_token,
            "user": {
                "id": auth_response.user.id,
                "email": auth_response.user.email,
                "user_metadata": auth_response.user.user_metadata,
                "identities": auth_response.user.identities
            }
        }
    except Exception as e:
        error_msg = str(e).lower()
        if "timeout" in error_msg or "handshake" in error_msg:
            raise HTTPException(
                status_code=504,
                detail="Database connection timeout. Please check server network availability."
            )
            
        raise HTTPException(status_code=401, detail="Invalid email or password.")

# --- Live Stream Channel Gateway WebSocket ---


@app.websocket("/ws/v1/agent")
async def secure_agent_stream_channel(websocket: WebSocket, token: str=Query(...)):
    """Handles real-time user-agent conversations over secure, authenticated WebSocket channels."""
    supabase = app.state.system.supabase

    try:
        user_response = supabase.auth.get_user(token)
        user = user_response.user
        user_id = user.id
        
        user_role = user.user_metadata.get("role", "renter") if user.user_metadata else "renter"
        
    except Exception as e:
        logger.error(f"WebSocket Handshake Denied: {str(e)}")
        try:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            pass
        return

    await websocket.accept()

    execution_config: RunnableConfig = {
        "configurable": {
            "thread_id": f"thread_tenant_{user_id}",
            "user_id": user_id,
            "user_role": user_role
        }
    }

    try:
        while True:
            raw_payload = await websocket.receive_text()
            data = json.loads(raw_payload)
            user_prompt = data.get("message", "").strip()

            if not user_prompt:
                continue

            input_state: InputAgentState = {
                "messages": [HumanMessage(content=user_prompt)]
            }
            
            final_output = ""
            is_interrupted = False
            
            # Streaming engine updates mapped strictly over your state emissions
            async for event in housepadi_agent_graph.astream(input_state, config=execution_config):
                # 1. Catch standard direct messages channel
                if "messages" in event:
                    final_output = event["messages"][-1].content
                elif isinstance(event, dict):
                    # 2. Traverse deepagents custom todo/planning node structures dynamically
                    for node_data in event.values():
                        if isinstance(node_data, dict) and "messages" in node_data:
                            messages_list = node_data["messages"]
                            if messages_list:
                                final_output = messages_list[-1].content

            # Inspect if the state machine halted on a human-in-the-loop gate
            updated_state = housepadi_agent_graph.get_state(execution_config)
            if updated_state.next:
                is_interrupted = True

            # If the model executed planning/tools but text output wasn't generated yet
            if not final_output:
                final_output = "I have initiated the execution plan and updated my objectives."

            await websocket.send_json({
                "status": "success" if not is_interrupted else "requires_verification",
                "response": final_output,
                "paused_for_human_review": is_interrupted
            })

    except WebSocketDisconnect:
        logger.info(f"Tenant {user_id} closed connection cleanly.")
    except Exception as e:
        logger.error(f"Execution engine failure on streaming socket: {str(e)}")
        try:
            await websocket.send_json({"status": "error", "message": "Internal worker execution pool error."})
        except Exception:
            pass
