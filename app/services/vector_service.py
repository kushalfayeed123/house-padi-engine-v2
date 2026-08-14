"""
Embedding generation via OpenRouter's hosted embeddings endpoint.

Previously this ran sentence-transformers locally (ONNX backend), but the
model + runtime routinely pushed process memory past 512MB on free-tier
hosts, causing OOM kills mid-request — specifically during search/agent
calls that touched embeddings. Calling OpenRouter removes the model and
its runtime from this process entirely.

IMPORTANT: dimensions=384 matches the existing all-MiniLM-L6-v2 embeddings
already stored in Supabase's `properties.embedding` pgvector column. If
you ever change EMBEDDING_MODEL or EMBEDDING_DIMENSIONS, every existing
row needs to be re-embedded and the column's vector dimension altered —
otherwise cosine similarity search will silently return bad results for
old rows.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 384  # matches the old all-MiniLM-L6-v2 output size

_HEADERS_BASE = {
    "Content-Type": "application/json",
    "HTTP-Referer": "https://housepadi.com",
    "X-Title": "HousePadi Python",
}


def _payload(text: str) -> Dict[str, Any]:
    return {
        "model": EMBEDDING_MODEL,
        "input": text,
        "dimensions": EMBEDDING_DIMENSIONS,
    }


def _headers() -> Dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return {**_HEADERS_BASE, "Authorization": f"Bearer {api_key}"}


async def embed_text_async(text: str) -> List[float]:
    """Core async call — everything else in this module builds a context
    string and routes through this."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(OPENROUTER_EMBEDDINGS_URL, headers=_headers(), json=_payload(text))
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


def embed_text(text: str) -> List[float]:
    """Sync call, for any non-async caller."""
    with httpx.Client(timeout=20.0) as client:
        response = client.post(OPENROUTER_EMBEDDINGS_URL, headers=_headers(), json=_payload(text))
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


def _clean_specs(specs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (specs or {}).items() if v is not None}


async def vectorize_property_data_async(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """Async: property listing fields -> embedding."""
    clean_specs = _clean_specs(specs)
    context_string = (
        f"Property Address: {address.strip().lower()}. "
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    return await embed_text_async(context_string)


def vectorize_property_data(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    clean_specs = _clean_specs(specs)
    context_string = (
        f"Property Address: {address.strip().lower()}. "
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    return embed_text(context_string)


async def vectorize_search_query_async(location: str, bedrooms: Optional[int] = None) -> List[float]:
    """Async: user search criteria -> embedding, mirroring the property schema
    above so cosine similarity is comparing like with like."""
    clean_specs = f"{{'bedrooms': {bedrooms}}}" if bedrooms is not None else "{}"
    query_context = (
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    return await embed_text_async(query_context)


def vectorize_search_query(location: str, bedrooms: Optional[int] = None) -> List[float]:
    clean_specs = f"{{'bedrooms': {bedrooms}}}" if bedrooms is not None else "{}"
    query_context = (
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    return embed_text(query_context)