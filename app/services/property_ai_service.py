import asyncio
import json
import logging
import os
import httpx
from supabase import create_client
from app.services.vector_service import get_model

logger = logging.getLogger("uvicorn")

class PropertyAIService:
    def __init__(self):
        self.model = get_model()
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")
        self.supabase = create_client(supabase_url, supabase_key)

    async def analyze_property(self, title: str, description: str, location: str | None) -> dict:
        system_prompt = """You are a Real Estate Marketing Expert. 
        Return ONLY a JSON object with this exact structure:
        {
          "ai_summary": "A 2-sentence captivating sales pitch.",
          "search_tags": ["tag1", "tag2", "tag3"],
          "features": {
            "bedrooms": 0,
            "bathrooms": 0,
            "is_luxury": false,
            "has_electricity_backup": false,
            "furnished": false
          }
        }"""
        
        fallback = {
            "search_tags": ["Property"],
            "features": {"bedrooms": 0, "bathrooms": 0, "has_electricity_backup": False, "furnished": False, "is_luxury": True},
            "ai_summary": title,
        }

        api_key = os.getenv("OPENROUTER_API_KEY")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://housepadi.com",
            "X-Title": "HousePadi Python",
        }
        payload = {
            "model": "openrouter/free",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Title: {title}\nDescription: {description}\nLocation: {location or 'Not specified'}"}
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 1000,
            "temperature": 0.1,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code != 200:
                    logger.error(f"OpenRouter error: {response.text}")
                    return fallback
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            return fallback

    async def generate_embedding(self, text: str) -> list[float]:
        # Generate embedding tensor and convert to list for Supabase vector column
        embedding_tensor = self.model.encode(text, convert_to_tensor=True)
        return embedding_tensor.cpu().tolist()

    async def enrich_property(self, property_id: str):
        # 1. Fetch property from Supabase
        response = self.supabase.table("properties").select("*").eq("id", property_id).execute()
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not data:
            return

        prop = data[0]
        if not isinstance(prop, dict):
            return

        title = prop.get("title")
        description = prop.get("description")
        location = prop.get("location")
        address_full = prop.get("address_full")
        metadata = prop.get("metadata") or {}

        if not description or not title:
            return

        logger.info(f"Enriching property {property_id}...")

        # 2. Run AI Analysis & Embedding generation
        ai_result = await self.analyze_property(title, description, location)
        text_to_embed = f"Title: {title}. Location: {location}. Address: {address_full}. Description: {description}"
        embedding = await self.generate_embedding(text_to_embed)

        # 3. Update Supabase record
        updated_metadata = {
            **metadata,
            "search_tags": ai_result.get("search_tags", [])
        }

        update_data = {
            "metadata": updated_metadata,
            "features": ai_result.get("features", {}),
            "embedding": embedding,
            "status": "available",  # Updates status from draft/pending to available
            "aiSummary": ai_result.get("ai_summary", title),
        }

        self.supabase.table("properties").update(update_data).eq("id", property_id).execute()
        logger.info(f"✅ Property {property_id} successfully enriched.")
        
        
    async def run_enrichment_sweep(self):
        """Background sweep loop that periodically checks for properties whose status is not available."""
        while True:
            try:
                response = self.supabase.table("properties") \
                    .select("id") \
                    .neq("status", "available") \
                    .limit(20) \
                    .execute()
                
                props = getattr(response, "data", []) or []
                for prop in props:
                    await self.enrich_property(prop["id"])
                    await asyncio.sleep(1)  # Buffer to prevent rate limits
            except Exception as e:
                logger.error(f"Error in property enrichment sweep background task: {e}")
            
            # Run sweep every 10 minutes
            await asyncio.sleep(600)