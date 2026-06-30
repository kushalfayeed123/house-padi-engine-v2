import asyncio
import logging
from typing import Dict, Any, List

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Global model instance for singleton lifecycle execution caching
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """
    Retrieves the global cached SentenceTransformer model instance (Singleton).
    Ensures model weights are loaded into system memory exactly once.
    """
    global _model
    if _model is None:
        logger.info("Initializing SentenceTransformer model 'all-MiniLM-L6-v2'...")
        # 384-dimension vector output space matches your Supabase vector(384) schema configuration
        _model = SentenceTransformer('all-MiniLM-L6-v2')
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