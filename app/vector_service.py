import asyncio
import logging
import threading
from typing import Dict, Any, List

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
                # 384-dimension vector output space matches your Supabase vector(384) schema configuration
                _model = SentenceTransformer('all-MiniLM-L6-v2')
                logger.info("SentenceTransformer model successfully cached in memory global context.")
    return _model


def vectorize_property_data(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """
    Synchronous baseline mapping execution utility.
    Transforms structural asset metadata into a clean semantic text string 
    and returns its multi-dimensional mathematical vector representation.
    """
    # Filter out empty structures safely
    clean_specs = {k: v for k, v in specs.items() if v is not None} if specs else {}
    
    # Standardize metadata construction to maximize cosine similarity accuracy
    context_string = (
        f"Property Address: {address.strip()}. "
        f"Location Area: {location.strip()}. "
        f"Physical Attributes and Features: {clean_specs}. "
        f"System Owner ID Context: {ownerId}."
    )
    
    model = get_model()
    # Execute the vectorization pipeline locally
    embedding = model.encode(context_string)
    
    return embedding.tolist()


def vectorize_search_query(query_text: str) -> List[float]:
    """
    Encodes raw, user-facing search strings cleanly without schema boilerplate.
    Prevents template noise from corrupting cosine similarity calculations.
    """
    model = get_model()
    # Explicitly use encode_query if available or standard encode on raw text
    embedding = model.encode(query_text.strip())
    return embedding.tolist()


async def vectorize_property_data_async(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """
    Asynchronous concurrency wrapper block.
    Offloads heavy tokenization and tensor computation to an isolated system thread pool 
    to preserve downstream FastAPI streaming performance.
    """
    return await asyncio.to_thread(
        vectorize_property_data, 
        address, 
        ownerId, 
        location, 
        specs
    )