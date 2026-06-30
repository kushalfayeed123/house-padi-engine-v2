import json
from typing import Literal
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.schemas.payloads import TourPayload


@tool
async def tour_database_worker(action: Literal["book", "list"], payload_json: str, config: RunnableConfig) -> str:
    """Manages tracking for physical property site viewings and schedules."""
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "Security Guardrail: Request denied."

    try:
        parsed_data = json.loads(payload_json) if payload_json else {}
        
        if action == "book":
            validated = TourPayload(**parsed_data)
            payload = validated.model_dump(mode="json")
            payload["visitor_id"] = user_id
            
            res = await db.execute(supabase_client.table("tours").insert(payload).execute)
            return f"Success: Tour finalized inside tracker. Reference ID: {res.data[0].get('id')}"

        elif action == "list":
            res = await db.execute(
                supabase_client.table("tours")
                .select("*, properties(address, base_price)")
                .eq("visitor_id", user_id)
                .execute
            )
            return json.dumps(res.data)

        return "Error: Unsupported execution request parameter."
    except Exception as e:
        return f"Database Interface Exception: {str(e)}"
