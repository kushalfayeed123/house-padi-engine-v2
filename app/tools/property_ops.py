import json
from typing import Literal
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.schemas.payloads import PropertyPayload
from app.vector_service import get_model, vectorize_property_data


@tool
async def property_database_worker(action: Literal["create", "list"], payload_json: str, config: RunnableConfig) -> str:
    """Manages the HousePadi core real estate repository index. 
    Accepts 'action' ('create' or 'list') and 'payload_json' metadata string.
    """
    # Defensive fallback: ensure config is never completely empty
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."
    
    print(f"safe config{safe_config}, action: {action}")

    try:
        parsed_data = json.loads(payload_json) if payload_json else {}
        
        if action == "create":
            validated = PropertyPayload(**parsed_data)
            payload = validated.model_dump()
            payload["owner_id"] = user_id
            
            try:
                embedding = vectorize_property_data(
                    address=payload.get("address", ""),
                    ownerId=user_id,
                    location=payload.get("location", ""),
                    specs=payload.get("specs", {})
                )
                payload["vector_embedding"] = embedding
            except Exception:
                payload["vector_embedding"] = None

            res = await db.execute(supabase_client.table("properties").insert(payload).execute)
            return f"Success: Asset cataloged. Node ID Reference: {res.data[0].get('id')}"

        elif action == "list":
            search_query = parsed_data.get("query") or parsed_data.get("city") or ""
            query_vector = get_model().encode(search_query).tolist()
            
            res = await db.execute(
                supabase_client.rpc(
                    "match_properties",
                    {
                        "query_embedding": query_vector,
                        "match_threshold": 0.4,
                        "match_count": 10,
                        "filter_owner_id": user_id
                    }
                ).execute
            )
            print(f"result: {res}")

            return json.dumps(res.data)

        return "Error: Action criteria mapping failure."
    except Exception as e:
        return f"Database Interface Exception: {str(e)}"
