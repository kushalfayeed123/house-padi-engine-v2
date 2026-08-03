import asyncio
import logging
from app.services.property_ai_service import PropertyAIService

logger = logging.getLogger("uvicorn")

# Lazy initialization so it doesn't run at import time
_ai_service = None

def get_ai_service() -> PropertyAIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = PropertyAIService()
    return _ai_service

async def run_pending_property_enrichment_sweep():
    """Background sweep loop that periodically checks for properties whose status is not available."""
    ai_service = get_ai_service()
    while True:
        try:
            response = ai_service.supabase.table("properties") \
                .select("id") \
                .neq("status", "available") \
                .limit(20) \
                .execute()
            
            props = getattr(response, "data", []) or []
            for prop in props:
                await ai_service.enrich_property(prop["id"])
                await asyncio.sleep(1)  # Buffer to prevent rate limits
        except Exception as e:
            logger.error(f"Error in property enrichment sweep background task: {e}")
        
        # Run sweep every 10 minutes
        await asyncio.sleep(600)