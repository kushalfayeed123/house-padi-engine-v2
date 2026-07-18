import json
from typing import Any, Dict, List, Optional
import asyncio
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.database import supabase_client, db
from app.services.cache_service import cache_get, cache_invalidate, cache_set
from app.services.vector_service import  vectorize_property_data_async, vectorize_search_query
from pydantic import BaseModel, Field, field_validator
from logging import getLogger

logger = getLogger("uvicorn")

# ==========================================
# 1. SEARCH / LIST TOOL (WITH CACHING)
# ==========================================


class SearchPropertiesInput(BaseModel):
    location: str = Field(..., description="The city or district to search in.")
    base_price: Optional[float] = Field(None, description="Max budget for the property.")
    bedrooms: Optional[int] = Field(None, description="Number of bedrooms.")


@tool("search_properties_worker")
async def search_properties_worker(location: str, base_price: Optional[float]=None, bedrooms: Optional[int]=None) -> str:
    """Finds properties matching location and filter criteria."""
    
    # 1. Vectorize query
    query_vector = await asyncio.to_thread(vectorize_search_query, location)
    print(query_vector)

    # 2. Call Supabase RPC
    try:
        res = await asyncio.to_thread(
            supabase_client.rpc("match_properties", {
                "query_embedding": query_vector,
                "match_threshold": 0.2,
                "match_count": 5,
                "filter_owner_id": None,
                "budget_limit": base_price,
                "filters": {"bedrooms": str(bedrooms) if bedrooms else None}
            }).execute
        )
        
        print(res)
        
        # 3. Handle data/Type safety
        if not res.data or not isinstance(res.data, list):
            return json.dumps([])

        sanitized = [{
            "id": item.get("id"),
            "title": item.get("title"),
            "address": item.get("address"),
            "location": item.get("location"),
            "price": item.get("price"),
            "bedrooms": item.get("bedrooms"),
            "bathrooms": item.get("bathrooms"),
            "amenities": item.get("amenities"),
            "images": item.get("images", []),  # Capture the list of image URLs
            "similarity": item.get("similarity")
        } for item in res.data if isinstance(item, dict)]

        return json.dumps(sanitized)
        
    except Exception as e:
        return json.dumps({"error": str(e)})


# ==========================================
# 2. CREATE / CATALOG TOOL (SCHEMA-ALIGNED)
# ==========================================
class CreatePropertyInput(BaseModel):
    title: str = Field(..., description="Property title/name")
    address_full: str = Field(..., description="Full street address")
    location: str = Field(..., description="City or general region area")
    price: float = Field(..., gt=0, description="Monthly rent price")
    description: Optional[str] = Field(None, description="Property description")
    currency: str = Field("USD", description="Currency code")
    latitude: Optional[float] = Field(None, description="Property latitude for directions")
    longitude: Optional[float] = Field(None, description="Property longitude for directions")
    images: Optional[List[str]] = Field(default_factory=list, description="Image URLs")
    features: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Amenities/features")
    lease_duration_months: Optional[int] = Field(12, description="Default lease duration")
    agreement_content: Optional[str] = Field(None, description="Lease agreement template")

    @field_validator("price", mode="before")
    @classmethod
    def coerce_numeric_string(cls, v: Any) -> float:
        """Coerces stringified numbers into floats."""
        if isinstance(v, str):
            sanitized = v.replace("₦", "").replace("$", "").replace(",", "").strip()
            try:
                return float(sanitized)
            except ValueError:
                raise ValueError(f"Failed to parse price string '{v}' into a valid float.")
        return v


@tool("create_property_worker", args_schema=CreatePropertyInput)
async def create_property_worker(
    title: str,
    address_full: str,
    location: str,
    price: float,
    config: RunnableConfig,
    description: Optional[str]=None,
    currency: str="USD",
    latitude: Optional[float]=None,
    longitude: Optional[float]=None,
    images: Optional[List[str]]=None,
    features: Optional[Dict[str, Any]]=None,
    lease_duration_months: Optional[int]=12,
    agreement_content: Optional[str]=None
) -> str:
    """Catalogs a new real estate property asset aligned with new schema.
    Restricted strictly to Property Owners/Landlords.
    
    Automatically generates directions by storing latitude/longitude for property tours.
    Invalidates search cache to ensure renters see new listings immediately.
    """
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    user_role = safe_config.get("configurable", {}).get("user_role", "renter")
    
    # Explicit role authorization layer
    if user_role == "renter":
        return "Security Guardrail: Operation denied. Renters are strictly unauthorized to create property listings."
        
    if not user_id:
        return "Security Guardrail: Execution halted due to absent tenant identity context."

    logger.info(f"[CREATE EXECUTION] Owner: {user_id} | Property: {title} at {address_full}")

    try:
        # Build property payload aligned with new schema
        property_data = {
            "owner_id": user_id,
            "title": title,
            "address_full": address_full,
            "location": location,
            "price": price,
            "currency": currency,
            "description": description,
            "images": images or [],
            "features": features or {},
            "lease_duration_months": lease_duration_months,
            "agreement_content": agreement_content,
            "status": "draft",  # Default to draft, landlord must publish
            "is_featured": False
        }
        
        # Store coordinates if provided (critical for tour directions)
        if latitude and longitude:
            property_data["coords"] = {
                "latitude": latitude,
                "longitude": longitude
            }
        
        # Generate vector embedding for semantic search
        try:
            property_data["embedding"] = await vectorize_property_data_async(
                address=address_full, ownerId=user_id, location=location, specs=features or {}
            )
        except Exception:
            property_data["embedding"] = None

        # Insert into properties table
        res = await db.execute(supabase_client.table("properties").insert(property_data).execute)
        property_id = res.data[0].get('id')
        
        # Invalidate cache so new property appears in searches immediately
        cache_invalidate("property_search:")
        logger.info(f"[CACHE INVALIDATED] Property search cache cleared for new listing")
        
        return f"Success: Property '{title}' cataloged. Property ID: {property_id}. Landlord can now publish for renters to see."
        
    except Exception as e:
        logger.error(f"[CREATE ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return f"Database Interface Exception during creation: {str(e)}"


# ==========================================
# 3. GET FEATURED PROPERTIES TOOL
# ==========================================
class GetFeaturedInput(BaseModel):
    limit: Optional[int] = Field(6, description="Number of featured properties to retrieve.")


@tool("get_featured_worker", args_schema=GetFeaturedInput)
async def get_featured_worker(limit: int=6) -> str:
    """
    Retrieves the curated list of featured properties for the HousePadi marketplace.
    Optimized for front-page display.
    """
    cache_key = "featured_properties"
    
    # === CACHE CHECK ===
    cached_result = cache_get(cache_key)
    if cached_result:
        logger.info("[CACHE HIT] Returning featured properties.")
        return cached_result

    logger.info("[FEATURED EXECUTION] Fetching from database...")

    try:
        # Fetch properties where is_featured is TRUE
        res = await asyncio.to_thread(
            supabase_client.table("properties")
            .select("*")
            .eq("is_featured", True)
            .limit(limit)
            .execute
        )
        
        properties = res.data if res.data else []
        
        if not properties:
            return "[]"

        result = json.dumps(properties)
        
        # Cache for 1 hour as featured properties change less frequently
        cache_set(cache_key, result, ttl_hours=3600) 
        return result
        
    except Exception as e:
        logger.error(f"[FEATURED ERROR] {str(e)}")
        return f"Database error fetching featured properties: {str(e)}"
    
# ==========================================
# 4. GET LANDLORD PROPERTIES TOOL
# ==========================================


@tool("get_landlord_properties_worker")
async def get_landlord_properties_worker(config: RunnableConfig) -> str:
    """
    Retrieves all property listings created by the authenticated landlord.
    Restricted to Property Owners.
    """
    safe_config = config or {}
    print(safe_config)
    user_id = safe_config.get("configurable", {}).get("user_id")
    user_role = safe_config.get("configurable", {}).get("user_role", "renter")

    # Security check
    if user_role == "renter":
        return "Security Guardrail: Unauthorized. Only landlords can view their property dashboard."
    
    if not user_id:
        return "Security Guardrail: Identity context missing."

    logger.info(f"[MY PROPERTIES] Fetching listings for owner: {user_id}")

    try:
        res = await asyncio.to_thread(
            supabase_client.table("properties")
            .select("id, title, address_full, price, currency, status, features, images, created_at, owner_id")
            .eq("owner_id", user_id)
            .order("created_at", desc=True)
            .execute
        )
        
        properties = res.data if res.data else []
        return json.dumps(properties)
        
    except Exception as e:
        logger.error(f"[MY PROPERTIES ERROR] {str(e)}")
        return json.dumps({"error": f"Failed to retrieve properties: {str(e)}"})


class GetPropertyDetailsInput(BaseModel):
    property_id: str = Field(..., description="The unique ID of the property to retrieve.")


@tool("get_property_details_worker", args_schema=GetPropertyDetailsInput)
async def get_property_details_worker(property_id: str) -> str:
    """Retrieves full details for a specific property by ID."""
    try:
        res = await asyncio.to_thread(
            supabase_client.table("properties")
            .select("*")
            .eq("id", property_id)
            .single()
            .execute
        )
        
        if not res.data:
            return json.dumps({"error": "Property not found."})
            
        return json.dumps(res.data)
    except Exception as e:
        logger.error(f"[GET DETAILS ERROR] {str(e)}")
        return json.dumps({"error": str(e)})


class UpdatePropertyInput(BaseModel):
    property_id: str = Field(..., description="The ID of the property to update.")
    update_data: Dict[str, Any] = Field(..., description="Dictionary of fields to update (e.g., {'price': 500, 'status': 'archived'}).")


@tool("update_property_worker", args_schema=UpdatePropertyInput)
async def update_property_worker(property_id: str, update_data: Dict[str, Any], config: RunnableConfig) -> str:
    """Updates property details. Automatically re-vectorizes if location/content changes."""
    safe_config = config or {}
    user_id = safe_config.get("configurable", {}).get("user_id")
    
    if not user_id:
        return "Security Guardrail: Identity context missing."

    try:
        # 1. Fetch current data to check ownership and compare for re-vectorization
        response = await asyncio.to_thread(
            supabase_client.table("properties")
            .select("*")
            .eq("id", property_id)
            .single()
            .execute
        )
        
        current_data = response.data
        if not isinstance(current_data, dict):
            return "Error: Property not found."

        if current_data.get("owner_id") != user_id:
            return "Security Guardrail: Unauthorized. You do not own this property."

        # 2. Check if we need to re-vectorize
        # Fields that influence search/recommendations
        fields_to_watch = ["address_full", "description", "features", "location"]
        needs_revectorization = any(field in update_data for field in fields_to_watch)

        if needs_revectorization:
            # Prepare data for vectorizer
            new_address = update_data.get("address_full", current_data.get("address_full"))
            new_location = update_data.get("location", current_data.get("location"))
            new_features = update_data.get("features", current_data.get("features", {}))
            
            # Generate new embedding
            new_embedding = await vectorize_property_data_async(
                address=new_address,
                ownerId=user_id,
                location=new_location,
                specs=new_features
            )
            update_data["embedding"] = new_embedding

        # 3. Perform the update
        await asyncio.to_thread(
            supabase_client.table("properties")
            .update(update_data)
            .eq("id", property_id)
            .execute
        )

        cache_invalidate("property_search:")
        return f"Success: Property {property_id} updated and search index refreshed."
        
    except Exception as e:
        logger.error(f"[UPDATE ERROR] {str(e)}")
        return f"Database error during update: {str(e)}"
