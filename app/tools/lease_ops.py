import json
from typing import Literal
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.schemas.payloads import LeasePayload


@tool
async def lease_database_worker(action: Literal["draft", "finalize"], payload_json: str, config: RunnableConfig) -> str:
    """Handles contractual document processing. Writing or updating legally binding flags triggers a Human Review Interrupt."""
    user_id = config.get("configurable", {}).get("user_id")
    if not user_id:
        return "Security Guardrail: Execution context validation failed."

    try:
        parsed_data = json.loads(payload_json) if payload_json else {}
        
        if action == "draft":
            validated = LeasePayload(**parsed_data)
            payload = validated.model_dump(mode="json")
            payload["landlord_id"] = user_id
            payload["status"] = "pending_human_verification"
            
            res = await db.execute(supabase_client.table("leases").insert(payload).execute)
            return f"Success: Draft created. Pending Human Approval Loop: {json.dumps(res.data)}"

        elif action == "finalize":
            lease_id = parsed_data.get("lease_id")
            if not lease_id:
                return "Error: Missing parameter validation 'lease_id'."
                
            res = await db.execute(
                supabase_client.table("leases")
                .update({"status": "active_signed"})
                .eq("id", lease_id)
                .eq("landlord_id", user_id)
                .execute
            )
            return f"Success: Contract validated. Lease {lease_id} is marked ACTIVE."

        return "Error: Action execution format mismatch."
    except Exception as e:
        return f"Contract System Failure: {str(e)}"
