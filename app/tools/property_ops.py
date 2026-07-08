import json
from typing import Any, Dict, List, Optional
import asyncio
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.schemas.payloads import PropertyPayload
from app.vector_service import get_model, vectorize_property_data_async, vectorize_search_query
from pydantic import BaseModel, Field
from logging import getLogger


logger = getLogger("uvicorn")



# ==========================================
# 1. SEARCH / LIST TOOL
# ==========================================
class SearchPropertiesInput(BaseModel):
    location: str = Field(description="The city, area, or neighborhood to search for properties.")
    base_price: Optional[float] = Field(None, description="The upper budget limit for the lease/rent.")
    bedrooms: Optional[int] = Field(None, description="Exact or minimum number of bedrooms needed.")
    bathrooms: Optional[int] = Field(None, description="Exact or minimum number of bathrooms needed.")
    amenities: Optional[List[str]] = Field(None, description="List of required amenities strings (e.g., ['pool', 'gym']).")


@tool("search_properties_worker", args_schema=SearchPropertiesInput)
async def search_properties_worker(
    location: str,
    config: RunnableConfig,
    base_price: Optional[float]=None,
    bedrooms: Optional[int]=None,
    bathrooms: Optional[int]=None,
    amenities: Optional[List[str]]=None
) -> str:
    """Queries the HousePadi real estate marketplace index to find matching properties for renters."""
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    user_role = safe_config.get("configurable", {}).get("user_role", "renter")
    
    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."
        
    specs = {}
    if bedrooms is not None:
        specs["bedrooms"] = bedrooms
    if bathrooms is not None:
        specs["bathrooms"] = bathrooms
    if amenities:
        specs["amenities"] = [a.strip() for a in amenities if isinstance(a, str) and a.strip()]
        
    logger.info(f"[SEARCH EXECUTION] Role: {user_role} | Location: {location} | Specs: {specs}")

    try:
        
        # --- HERE IS WHERE AND HOW YOU USE IT ---
        # Offload the clean vectorizer to an isolated thread using standard asyncio
        query_vector_list = await asyncio.to_thread(vectorize_search_query, location)
        
        logger.info(f"[SEARCH] Clean vectorization complete. Dimensions: {len(query_vector_list)}")
        
        rpc_filter_id = None if user_role == "renter" else user_id
        
        # Execute Supabase Match
        query_call = supabase_client.rpc(
                "match_properties",
                {
                    "query_embedding": query_vector_list,  # Clean boilerplate-free vector
                    "match_threshold": 0.4,
                    "match_count": 10,
                    "filter_owner_id": rpc_filter_id,
                    "filters": specs,
                    "budget_limit": base_price 
                }
            ).execute
        
        res = await asyncio.to_thread(query_call)
        properties_list = res.data if isinstance(res.data, list) else []
        
        
        if not properties_list:
            return f"Search execution complete. 0 properties found matching location '{location}'."

        sanitized_results = []
        for property_item in properties_list:
            if isinstance(property_item, dict):
                sanitized_results.append({
                    "id": property_item.get("id"),
                    "title": property_item.get("title"),
                    "location": property_item.get("location"),
                    "price": property_item.get("price"),
                    "bedrooms": property_item.get("bedrooms"),
                    "bathrooms": property_item.get("bathrooms"),
                    "amenities": property_item.get("amenities")
                })

        logger.info(f"[SEARCH COMPLETE]  packaged properties {sanitized_results}.")
        return json.dumps(sanitized_results)
        
    except Exception as e:
        # CRITICAL: Print this so you can see it in your Uvicorn console!
        logger.error(f"[SEARCH DATABASE ERROR] Exception caught: {str(e)}")
        import traceback
        traceback.print_exc() 
        return f"Database Interface Exception during search: {str(e)}"


# ==========================================
# 2. CREATE / CATALOG TOOL
# ==========================================
class CreatePropertyInput(BaseModel):
    address: str = Field(..., description="Full physical street address of the property asset")
    base_price: float = Field(..., description="The base price listing value of the property asset")
    location: str = Field(..., description="City or general region area where property is located")
    specs: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional amenities/specs metadata dictionary")


@tool("create_property_worker", args_schema=CreatePropertyInput)
async def create_property_worker(
    address: str,
    base_price: float,
    location: str,
    config: RunnableConfig,
    specs: Optional[Dict[str, Any]]=None
) -> str:
    """Catalogs a new real estate property asset. Restricted strictly to Property Owners/Landlords."""
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    user_role = safe_config.get("configurable", {}).get("user_role", "renter")
    
    # Explicit role authorization layer
    if user_role == "renter":
        return "Security Guardrail: Operation denied. Renters are strictly unauthorized to create property listings."
        
    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."

    logger.info(f"[CREATE EXECUTION] Owner: {user_id} | Cataloging: {address}, {location}")

    try:
        validated = PropertyPayload(
            address=address,
            base_price=base_price,
            specs=specs or {},
            location=location
        )
        payload = validated.model_dump()
        payload["owner_id"] = user_id
        
        try:
            payload["vector_embedding"] = await vectorize_property_data_async(
                address=address, ownerId=user_id, location=location, specs=specs or {}
            )
        except Exception:
            payload["vector_embedding"] = None

        res = await db.execute(supabase_client.table("properties").insert(payload).execute)
        return f"Success: Asset cataloged. Node ID Reference: {res.data[0].get('id')}"
        
    except Exception as e:
        return f"Database Interface Exception during creation: {str(e)}"
