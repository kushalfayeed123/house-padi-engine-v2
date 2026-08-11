from fastapi import APIRouter, Depends, HTTPException
from app.core.database import db, supabase_client
from app.core.dependecies import get_optional_user_context

router = APIRouter(prefix="/api/leases", tags=["Leases"])

@router.get("/{lease_id}/document")
async def get_lease_document(
    lease_id: str,
    context: dict = Depends(get_optional_user_context)
):
    """Generates a temporary signed URL to view/download the active lease PDF."""
    user_id = context.get("id") if context else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Fetch lease
    lease_res = await db.execute(
        supabase_client.table("leases")
        .select("id, owner_id, renter_id, is_active, contract_url")
        .eq("id", lease_id)
        .single()
        .execute
    )
    
    if not lease_res.data:
        raise HTTPException(status_code=404, detail="Lease not found.")

    lease = lease_res.data

    # Security check: User must be landlord or renter
    if user_id not in [lease.get("owner_id"), lease.get("renter_id")]:
        raise HTTPException(status_code=403, detail="Access denied.")

    if not lease.get("is_active") or not lease.get("contract_url"):
        raise HTTPException(status_code=400, detail="Lease is not yet active or document has not been generated.")

    storage_path = lease["contract_url"]

    # Generate 60-minute signed URL from Supabase Storage
    try:
        res = supabase_client.storage.from_("house-padi-assets").create_signed_url(
            path=storage_path,
            expires_in=3600
        )
        return {"signed_url": res.get("signedUrl")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve document: {str(e)}")