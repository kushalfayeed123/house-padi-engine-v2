import json
from typing import Optional
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from app.database import supabase_client, db
from logging import getLogger

logger = getLogger("uvicorn")


class BookTourInput(BaseModel):
    property_id: str = Field(..., description="The unique UUID string of the property asset listing.")
    tour_date: str = Field(..., description="The preferred ISO timestamp format (YYYY-MM-DD HH:MM:SS) for the tour appointment.")


def generate_google_maps_link(latitude: Optional[float], 
    longitude: Optional[float], address: str) -> str:
    """Generate a Google Maps link for property directions."""
    if not latitude or not longitude:
        return f"https://www.google.com/maps/search/{address.replace(' ', '+')}"
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


@tool("book_tour_worker", args_schema=BookTourInput)
async def book_tour_worker(
    property_id: str, 
    tour_date: str, 
    config: RunnableConfig
) -> str:
    """Schedules a new physical property site viewing appointment.
    
    Includes automatic direction link generation using property coordinates.
    Fetches property details and user profile info automatically.
    Sends notifications to landlord for approval.
    """
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "Security Guardrail: Request denied. User context missing."

    try:
        # 1. Fetch the renter's profile details
        profile_res = await db.execute(
            supabase_client.table("profiles")
            .select("first_name, last_name, phone_number, email")
            .eq("id", user_id)
            .single()
            .execute
        )
        
        if not profile_res.data:
            return "Execution Error: Authenticated profile record could not be located."
            
        renter_first_name = profile_res.data.get("first_name", "")
        renter_last_name = profile_res.data.get("last_name", "")
        renter_email = profile_res.data.get("email", "")
        renter_phone = profile_res.data.get("phone_number", "No Contact")
        visitor_name = f"{renter_first_name} {renter_last_name}".strip()

        # 2. Fetch property details (including coordinates for directions)
        property_res = await db.execute(
            supabase_client.table("properties")
            .select("id, title, address_full, location, owner_id, coords")
            .eq("id", property_id)
            .single()
            .execute
        )
        
        if not property_res.data:
            return f"Execution Error: Property {property_id} not found."
        
        property_data = property_res.data
        owner_id = property_data.get("owner_id")
        address_full = property_data.get("address_full", "")
        
        # Generate directions link
        coords = property_data.get("coords", {})
        latitude = coords.get("latitude") if isinstance(coords, dict) else None
        longitude = coords.get("longitude") if isinstance(coords, dict) else None
        directions_link = generate_google_maps_link(latitude, longitude, address_full)

        # 3. Create tour record in database
        tour_payload = {
            "property_id": property_id,
            "visitor_id": user_id,
            "visitor_name": visitor_name,
            "visitor_contact": renter_phone,
            "visitor_email": renter_email,
            "tour_date": tour_date,
            "status": "pending_approval",  # Awaiting landlord approval
            "directions_link": directions_link
        }
        
        tour_res = await db.execute(supabase_client.table("tours").insert(tour_payload).execute)
        tour_id = tour_res.data[0].get('id')
        
        logger.info(f"[TOUR BOOKED] Tour {tour_id} scheduled for {tour_date}. Awaiting landlord approval.")
        
        # 4. Create notification for landlord (would trigger in real implementation)
        notification_payload = {
            "recipient_id": owner_id,
            "type": "tour_request",
            "content": f"{visitor_name} has requested a tour on {tour_date}",
            "related_tour_id": tour_id,
            "status": "unread"
        }
        
        try:
            await db.execute(supabase_client.table("notifications").insert(notification_payload).execute)
            logger.info(f"[NOTIFICATION] Sent tour request to landlord {owner_id}")
        except Exception as e:
            logger.warning(f"[NOTIFICATION ERROR] Failed to send notification: {str(e)}")
        
        return f"Success: Tour scheduled for {tour_date}. Directions: {directions_link}. Property owner will receive a notification to approve the booking. Reference ID: {tour_id}"
        
    except Exception as e:
        logger.error(f"[TOUR BOOKING ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Database Interface Exception: {str(e)}"


@tool("list_tours_worker")
async def list_tours_worker(config: RunnableConfig) -> str:
    """Retrieves all scheduled property tours and viewings for the authenticated user.
    
    Returns tours with directions links and approval status.
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if not user_id:
        return "Security Guardrail: Request denied."

    try:
        # Different queries based on role (renters see their bookings, landlords see requests for their properties)
        if user_role == "landlord":
            # Fetch tours for properties owned by this landlord
            res = await db.execute(
                supabase_client.table("tours")
                .select("*, properties(id, title, address_full, owner_id)")
                .eq("properties.owner_id", user_id)
                .execute
            )
        else:
            # Fetch tours booked by this renter
            res = await db.execute(
                supabase_client.table("tours")
                .select("*, properties(id, title, address_full, owner_id, coords)")
                .eq("visitor_id", user_id)
                .execute
            )
        
        # Enhance tour data with formatted information
        tours_enhanced = []
        if res.data:
            for tour in res.data:
                tour_info = {
                    "id": tour.get("id"),
                    "property_id": tour.get("property_id"),
                    "tour_date": tour.get("tour_date"),
                    "status": tour.get("status"),
                    "visitor_name": tour.get("visitor_name"),
                    "visitor_contact": tour.get("visitor_contact"),
                    "directions_link": tour.get("directions_link")
                }
                
                # Add property details if available
                if tour.get("properties"):
                    prop = tour.get("properties")
                    tour_info["property"] = {
                        "title": prop.get("title"),
                        "address": prop.get("address_full")
                    }
                
                tours_enhanced.append(tour_info)
        
        logger.info(f"[TOURS LISTED] Found {len(tours_enhanced)} tours for user {user_id}")
        return json.dumps(tours_enhanced)
        
    except Exception as e:
        logger.error(f"[LIST TOURS ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Database Interface Exception: {str(e)}"


@tool("approve_tour_worker")
async def approve_tour_worker(tour_id: str, config: RunnableConfig) -> str:
    """Approves a tour request (landlord only).
    
    Updates tour status to 'approved' and notifies the renter.
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if user_role != "landlord":
        return "Security Guardrail: Only landlords can approve tours."
    
    if not user_id:
        return "Security Guardrail: Request denied."

    try:
        # Verify the tour belongs to a property owned by this landlord
        tour_res = await db.execute(
            supabase_client.table("tours")
            .select("*, properties(owner_id)")
            .eq("id", tour_id)
            .single()
            .execute
        )
        
        if not tour_res.data:
            return f"Tour {tour_id} not found."
        
        tour = tour_res.data
        property_owner = tour.get("properties", {}).get("owner_id")
        
        if property_owner != user_id:
            return "Security Guardrail: You can only approve tours for your own properties."
        
        # Update tour status
        update_res = await db.execute(
            supabase_client.table("tours")
            .update({"status": "approved"})
            .eq("id", tour_id)
            .execute
        )
        
        logger.info(f"[TOUR APPROVED] Tour {tour_id} approved by landlord {user_id}")
        
        # Notify renter
        renter_id = tour.get("visitor_id")
        notification_payload = {
            "recipient_id": renter_id,
            "type": "tour_approved",
            "content": f"Your tour request has been approved for {tour.get('tour_date')}",
            "related_tour_id": tour_id,
            "status": "unread"
        }
        
        try:
            await db.execute(supabase_client.table("notifications").insert(notification_payload).execute)
        except Exception as e:
            logger.warning(f"Failed to notify renter: {str(e)}")
        
        return f"Success: Tour {tour_id} approved. Renter has been notified."
        
    except Exception as e:
        logger.error(f"[APPROVE TOUR ERROR] {str(e)}")
        return f"Database Interface Exception: {str(e)}"
