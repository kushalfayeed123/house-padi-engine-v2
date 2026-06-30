import jwt
from datetime import datetime, timedelta, timezone
from fastapi import status, HTTPException, WebSocket
from app.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # Token lifespan: 12 Hours


def create_backend_access_token(user_id: str, role: str) -> str:
    """Generates an authoritative, server-owned access claim token for frontend tracking."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    claims = {
        "sub": str(user_id),
        "role": str(role),
        "exp": expire
    }
    return jwt.encode(claims, settings.BACKEND_JWT_SECRET, algorithm=ALGORITHM)


def verify_ws_handshake_token(token: str) -> dict:
    """Validates backend token parameters during persistent WebSocket connection loops."""
    try:
        payload = jwt.decode(
            token,
            settings.BACKEND_JWT_SECRET,
            algorithms=[ALGORITHM]
        )
        user_id = payload.get("sub")
        role = payload.get("role")
        if not user_id or not role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token claims.")
        return {"user_id": user_id, "role": role}
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credentials expired or unverified.")
