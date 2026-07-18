from fastapi import Request, HTTPException, Header, Depends
from typing import Annotated

# This dependency extracts the client from the app state
def get_supabase(request: Request):
    return request.app.state.system.supabase

# This dependency handles the auth context
async def get_user_context(
    authorization: Annotated[str, Header()],
    supabase = Depends(get_supabase)
):
    token = authorization.replace("Bearer ", "").strip()
    try:
        user = supabase.auth.get_user(token).user
        return {"id": user.id, "role": user.user_metadata.get("role", "renter")}
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")