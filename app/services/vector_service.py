import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_model_lock = threading.Lock()

def get_model() -> SentenceTransformer:
    """
    Retrieves the global cached SentenceTransformer model instance using 
    the lightweight ONNX backend to minimize memory consumption.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                logger.info("Initializing SentenceTransformer with lightweight ONNX backend...")
                # Forces ONNX runtime instead of heavy PyTorch
                _model = SentenceTransformer(
                    'all-MiniLM-L6-v2',
                    backend="onnx",
                    model_kwargs={"file_name": "onnx/model.onnx"}
                    )
                logger.info("ONNX model successfully cached in memory global context.")
    return _model

def vectorize_property_data(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """
    Synchronous baseline mapping execution utility.
    Transforms structural property metadata into a clean semantic text string 
    and returns its multi-dimensional mathematical vector representation via ONNX.
    """
    clean_specs = {k: v for k, v in specs.items() if v is not None} if specs else {}
    context_string = (
        f"Property Address: {address.strip().lower()}. "
        f"Location Area: {location.strip().lower()}. "
        f"Physical Attributes and Features: {clean_specs}."
    )
    model = get_model()
    embedding = model.encode(context_string)
    return embedding.tolist()

async def vectorize_property_data_async(address: str, ownerId: str, location: str, specs: Dict[str, Any]) -> List[float]:
    """Asynchronous wrapper for property data vectorization to keep the event loop unblocked."""
    return await asyncio.to_thread(vectorize_property_data, address, ownerId, location, specs)

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