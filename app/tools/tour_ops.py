import json
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from app.database import supabase_client, db
from app.schemas.payloads import TourPayload

# The LLM now only needs to supply the target property and the date string!
class BookTourInput(BaseModel):
    property_id: str = Field(..., description="The unique UUID string of the property asset listing.")
    tour_date: str = Field(..., description="The preferred ISO timestamp format (YYYY-MM-DD HH:MM:SS) for the tour appointment.")


@tool("book_tour_worker", args_schema=BookTourInput)
async def book_tour_worker(
    property_id: str, 
    tour_date: str, 
    config: RunnableConfig
) -> str:
    """Schedules and creates a new physical property site viewing appointment using the logged-in user's profile info."""
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "Security Guardrail: Request denied. User context missing."

    try:
        # 1. Automatically fetch the user's personal details from the profiles table
        profile_res = await db.execute(
            supabase_client.table("profiles")
            .select("full_name, phone")  # Adjust column names if they differ in your profiles table
            .eq("id", user_id)
            .single()
            .execute
        )
        
        if not profile_res.data:
            return "Execution Error: Authenticated profile record could not be located."
            
        visitor_name = profile_res.data.get("full_name") or "Authenticated User"
        visitor_contact = profile_res.data.get("phone_number") or "No Contact Provided"

        # 2. Map data seamlessly into your existing Pydantic validation payload
        validated = TourPayload(
            property_id=property_id,
            visitor_name=visitor_name,
            visitor_contact=visitor_contact,
            tour_date=tour_date
        )
        
        payload = validated.model_dump(mode="json")
        payload["visitor_id"] = user_id
        
        # 3. Commit to public.tours
        res = await db.execute(supabase_client.table("tours").insert(payload).execute)
        return f"Success: Tour finalized inside tracker. Reference ID: {res.data[0].get('id')}"
        
    except Exception as e:
        return f"Database Interface Exception: {str(e)}"


@tool("list_tours_worker")
async def list_tours_worker(config: RunnableConfig) -> str:
    """Retrieves and lists all scheduled property tours, site appointments, and viewings belonging to the user."""
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "Security Guardrail: Request denied."

    try:
        res = await db.execute(
            supabase_client.table("tours")
            .select("*, properties(address, base_price)")
            .eq("visitor_id", user_id)
            .execute
        )
        return json.dumps(res.data)
        
    except Exception as e:
        return f"Database Interface Exception: {str(e)}"