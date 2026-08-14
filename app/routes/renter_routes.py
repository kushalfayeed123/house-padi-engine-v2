"""
Renter Router
Endpoints for the renter-facing dashboard. Mirrors landlord_routes.py's
aggregate /overview pattern, but landlord_routes filters everything by
properties.owner_id — which is always empty for a renter — so this is the
renter-scoped equivalent, not a variant of the same endpoint.
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from app.core.database import db, supabase_client
from logging import getLogger

from app.core.dependecies import get_optional_user_context

logger = getLogger("uvicorn")

router = APIRouter(prefix="/api/renter", tags=["Renter Dashboard"])

_RENTED_STATUSES = ("active", "completed")


@router.get("/overview")
async def get_renter_overview(config: dict = Depends(get_optional_user_context)) -> Dict[str, Any]:
    """
    Aggregates renter dashboard metrics in a single call — tours, applications,
    derived active leases, and wallet balance — replacing what the frontend
    previously had to assemble from three separate round trips plus a
    per-property lookup for each active lease.
    """
    user_id = config.get("id") or config.get("configurable", {}).get("user_id") if config else None

    if not user_id:
        raise HTTPException(status_code=401, detail="Security Guardrail: User context missing.")

    try:
        # 1. Tours booked by this renter
        tour_res = await db.execute(
            lambda: supabase_client.table("tours")
            .select("*, properties(id, title, address_full, price, currency, owner_id)")
            .eq("visitor_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        tours = tour_res.data if tour_res.data else []

        # 2. Applications submitted by this renter
        app_res = await db.execute(
            lambda: supabase_client.table("applications")
            .select("*, properties(id, title, address_full, price, currency, owner_id)")
            .eq("renter_id", user_id)
            .order("applied_at", desc=True)
            .execute()
        )
        applications = app_res.data if app_res.data else []
        pending_applications = [
            a for a in applications if a.get("status") == "pending_landlord_approval"
        ]

        # 3. Derive active leases from applications that reached one — there's
        # no separately owner-scoped "leases" resource to query directly; a
        # lease is a side effect of an approved-and-paid application.
        rented_properties = [
            {"lease_id": a.get("lease_id"), "property": a.get("properties")}
            for a in applications
            if a.get("lease_id") and (a.get("status") or "").lower() in _RENTED_STATUSES
        ]

        # 4. Wallet balance
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
                "total_tours": len(tours),
                "total_applications": len(applications),
                "pending_applications": len(pending_applications),
                "rented_properties": len(rented_properties),
                "wallet_balance": wallet_balance,
            },
            "data": {
                "tours": tours,
                "applications": applications,
                "rented_properties": rented_properties,
            },
        }

    except Exception as e:
        logger.error(f"[RENTER OVERVIEW ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load renter overview: {str(e)}")