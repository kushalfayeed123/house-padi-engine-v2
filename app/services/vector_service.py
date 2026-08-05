import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Global model instance for singleton lifecycle execution caching
_model: SentenceTransformer | None = None
# Explicit thread lock to prevent concurrent initialization race conditions from asyncio.to_thread
_model_lock = threading.Lock()


def get_model() -> SentenceTransformer:
    """
    Retrieves the global cached SentenceTransformer model instance (Singleton).
    Guarantees thread-safe access so weights are loaded into memory exactly once.
    """
    global _model
    
    # Double-checked locking pattern for optimal performance
    if _model is None:
        with _model_lock:
            if _model is None:
                logger.info("Initializing SentenceTransformer model 'all-MiniLM-L6-v2' thread-safely...")
                _model = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("SentenceTransformer model successfully cached in memory global context.")
    return _model


def vectorize_property_data(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """
    Synchronous baseline mapping execution utility.
    Transforms structural asset metadata into a clean semantic text string 
    and returns its multi-dimensional mathematical vector representation.
    """
    clean_specs = {k: v for k, v in specs.items() if v is not None} if specs else {}
    
    # Standardize metadata context without including raw system UUIDs
    context_string = (
        f"Property Address: {address.strip().lower()}. "
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    
    model = get_model()
    embedding = model.encode(context_string)
    return embedding.tolist()


def vectorize_search_query(location: str, bedrooms: Optional[int] = None) -> List[float]:
    """
    Encodes user search criteria into a structured text format 
    that mirrors the property embedding schema to maximize cosine similarity.
    """
    clean_specs = f"{{'bedrooms': {bedrooms}}}" if bedrooms is not None else "{}"
    
    query_context = (
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    
    model = get_model()
    embedding = model.encode(query_context)
    return embedding.tolist()


async def vectorize_search_query_async(location: str, bedrooms: Optional[int] = None) -> List[float]:
    """Asynchronous wrapper for query vectorization to keep the event loop unblocked."""
    return await asyncio.to_thread(
        vectorize_search_query,
        location,
        bedrooms
    )


async def vectorize_property_data_async(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """
    Asynchronous concurrency wrapper block.
    Offloads heavy tokenization and tensor computation to an isolated system thread pool.
    """
    return await asyncio.to_thread(
        vectorize_property_data, 
        address, 
        ownerId, 
        location, 
        specs
    )