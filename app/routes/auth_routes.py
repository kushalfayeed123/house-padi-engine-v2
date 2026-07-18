from fastapi import APIRouter, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from typing import Literal
from logging import getLogger

logger = getLogger("uvicorn")

router = APIRouter(prefix="/api/auth")

class AuthCredentials(BaseModel):
    email: EmailStr
    password: str

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: str | None = None
    role: Literal['landlord', 'renter', 'admin']

@router.post("/register")
async def register_user(body: RegisterRequest, request: Request):
    # Access the client initialized in main.py via the request object
    supabase = request.app.state.system.supabase
    try:
        # Route metadata directly into GoTrue options mapping context
        signup_response = supabase.auth.sign_up({
            "email": body.email,
            "password": body.password,
            "options": {
                "data": {
                    "first_name": body.first_name,
                    "last_name": body.last_name,
                    "full_name": f"{body.first_name} {body.last_name}",
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

@router.post("/login")
async def login_user(body: AuthCredentials, request: Request):
    supabase = request.app.state.system.supabase
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
    
    
    
    
    
    