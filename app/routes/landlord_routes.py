from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from app.core.database import db, supabase_client
from logging import getLogger

from app.core.dependecies import get_optional_user_context

logger = getLogger("uvicorn")

router = APIRouter(prefix="/api/landlord", tags=["Landlord Dashboard"])


@router.get("/overview")
async def get_landlord_overview(config: dict = Depends(get_optional_user_context)) -> Dict[str, Any]:
    """
    Aggregates landlord dashboard metrics utilizing the exact database tables 
    and query structures from the property, tour, application, and wallet workers.
    """
    user_id = config.get("id") or config.get("configurable", {}).get("user_id") if config else None

    if not user_id:
        raise HTTPException(status_code=401, detail="Security Guardrail: User context missing.")

    try:
        # 1. Fetch Properties
        prop_res = await db.execute(
            lambda: supabase_client.table("properties")
            .select("id, title, address_full, price, currency, status, features, images, created_at, owner_id")
            .eq("owner_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        properties = prop_res.data if prop_res.data else []
        active_listings = [p for p in properties if p.get("status") == "available"]

        # 2. Fetch Tours
        tour_res = await db.execute(
            lambda: supabase_client.table("tours")
            .select("*, properties(id, title, address_full, owner_id)")
            .eq("properties.owner_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        tours = tour_res.data if tour_res.data else []
        pending_tours = [t for t in tours if t.get("status") == "pending_approval"]

        # 3. Fetch Applications
        app_res = await db.execute(
            lambda: supabase_client.table("applications")
            .select("*, properties!inner(id, title, address_full, owner_id)")
            .eq("properties.owner_id", user_id)
            .order("applied_at", desc=True)
            .execute()
        )
        applications = app_res.data if app_res.data else []
        pending_applications = [a for a in applications if a.get("status") == "pending_landlord_approval"]

        # 4. Fetch Wallet Balance
        wallet_res = await db.execute(
            lambda: supabase_client.table("wallets")
            .select("balance")
            .eq("userId", user_id)
            .maybe_single()
            .execute()
        )
        wallet_balance = wallet_res.data.get("balance", 0) if wallet_res.data else 0

        return {
            "status": "success",
            "metrics": {
                "total_properties": len(properties),
                "active_listings": len(active_listings),
                "total_tours": len(tours),
                "pending_tours": len(pending_tours),
                "total_applications": len(applications),
                "pending_applications": len(pending_applications),
                "wallet_balance": wallet_balance
            },
            "data": {
                "properties": properties,
                "tours": tours,
                "applications": applications
            }
        }

    except Exception as e:
        logger.error(f"[LANDLORD OVERVIEW ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load landlord overview: {str(e)}")


@router.get("/properties")
async def list_landlord_properties(config: dict = Depends(get_optional_user_context)):
    """Exposes landlord properties matching get_landlord_properties_worker."""
    user_id = config.get("id") or config.get("configurable", {}).get("user_id") if config else None
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Security Guardrail: User context missing.")

    res = await db.execute(
        lambda: supabase_client.table("properties")
        .select("*")
        .eq("owner_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data if res.data else []


@router.get("/tours")
async def list_landlord_tours(config: dict = Depends(get_optional_user_context)):
    """Exposes tour requests matching list_tours_worker for owners."""
    user_id = config.get("id") or config.get("configurable", {}).get("user_id") if config else None
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Security Guardrail: User context missing.")

    res = await db.execute(
        lambda: supabase_client.table("tours")
        .select("*, properties(id, title, address_full, owner_id)")
        .eq("properties.owner_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data if res.data else []


@router.get("/applications")
async def list_landlord_applications(config: dict = Depends(get_optional_user_context)):
    """Exposes rental applications matching get_applications_worker."""
    user_id = config.get("id") or config.get("configurable", {}).get("user_id") if config else None
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Security Guardrail: User context missing.")

    res = await db.execute(
        lambda: supabase_client.table("applications")
        .select("*, properties!inner(id, title, address_full, owner_id)")
        .eq("properties.owner_id", user_id)
        .order("applied_at", desc=True)
        .execute()
    )
    return res.data if res.data else []