import json
from typing import Literal, Optional
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from app.core.database import db, supabase_client
from logging import getLogger

logger = getLogger("uvicorn")



class BookTourInput(BaseModel):
    property_id: str = Field(
        ...,
        description="The unique UUID string of the property asset listing."
    )

    tour_date: Optional[str] = Field(
        default=None,
        description="Preferred tour datetime. Leave empty if the user has not chosen one yet."
    )
def generate_google_maps_link(latitude: Optional[float], 
    longitude: Optional[float], address: str) -> str:
    """Generate a Google Maps link for property directions."""
    if not latitude or not longitude:
        return f"https://www.google.com/maps/search/{address.replace(' ', '+')}"
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"


@tool("book_tour_worker", args_schema=BookTourInput)
async def book_tour_worker(
    property_id: str, 
    tour_date: Optional[str], 
    config: RunnableConfig
) -> str:
    """Schedule, book, or request a new physical site viewing / tour appointment for a renter. 
    Use this when a user wants to visit, inspect, or book a viewing date/time for a property.
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
        
        if not tour_date:
            return json.dumps({
                "status": "awaiting_datetime",
                "property_id": property_id,
                "ui_component": "calendar_picker",
                "message": "Please choose a preferred date and time."
            })
        
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
            "user_id": owner_id,
            "title": "tour_request",
            "message": f"{visitor_name} has requested a tour on {tour_date}",
            "is_read": False,
            "metadata": {
                "property_id": property_id,
                "tour_id": tour_id,
                "tour_date": tour_date,
            }
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
    """Retrieves already scheduled property tours or viewing history for the current user.
    Do NOT use this to book or schedule a new tour.
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if not user_id:
        return "Security Guardrail: Request denied."

    try:
        # Different queries based on role (renters see their bookings, landlords see requests for their properties)
        if user_role == "owner":
            # Fetch tours for properties owned by this landlord
            res = await db.execute(
                supabase_client.table("tours")
                .select("*, properties(id, title, address_full, owner_id)")
                .eq("properties.owner_id", user_id)
                .order("created_at", desc=True)
                .execute
            )
        else:
            # Fetch tours booked by this renter
            res = await db.execute(
                supabase_client.table("tours")
                .select("*, properties(id, title, address_full, owner_id, coords)")
                .eq("visitor_id", user_id)
                .order("created_at", desc=True)
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


@tool("manage_tour_request_worker")
async def manage_tour_request_worker(
    tour_id: str, 
    action: Literal["approve", "deny"], 
    config: RunnableConfig
) -> str:
    """STRICTLY FOR LANDLORDS: Approves or denies an existing pending tour request.
    
    Args:
        tour_id: The ID of the tour request to update.
        action: Must be either 'approve' or 'deny'.
        config: LangChain runtime config containing user context.
        
    Do NOT use this when a renter wants to book or schedule a new tour.
    """
    user_id = config.get("configurable", {}).get("user_id")
    user_role = config.get("configurable", {}).get("user_role", "renter")
    
    if user_role != "owner":
        return "Security Guardrail: Only landlords can manage tour requests."
    
    if not user_id:
        return "Security Guardrail: Request denied."

    # Normalize action input
    normalized_action = action.lower().strip()
    if normalized_action in ["approve", "approved"]:
        new_status = "approved"
    elif normalized_action in ["deny", "denied", "reject", "rejected"]:
        new_status = "denied"
    else:
        return f"Invalid action '{action}'. Action must be either 'approve' or 'deny'."

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
            return "Security Guardrail: You can only manage tours for your own properties."
        
        # Update tour status
        await db.execute(
            supabase_client.table("tours")
            .update({"status": new_status})
            .eq("id", tour_id)
            .execute
        )
        
        logger.info(f"[TOUR {new_status.upper()}] Tour {tour_id} {new_status} by landlord {user_id}")
        
        # Notify renter
        renter_id = tour.get("visitor_id")
        notification_payload = {
            "recipient_id": renter_id,
            "type": f"tour_{new_status}",
            "message": f"Your tour request has been {new_status} for {tour.get('tour_date')}",
            "related_tour_id": tour_id,
            "status": "unread"
        }
        
        try:
            await db.execute(supabase_client.table("notifications").insert(notification_payload).execute)
        except Exception as e:
            logger.warning(f"Failed to notify renter: {str(e)}")
        
        return f"Success: Tour {tour_id} {new_status}. Renter has been notified."
        
    except Exception as e:
        logger.error(f"[MANAGE TOUR ERROR] {str(e)}")
        return f"Database Interface Exception: {str(e)}"