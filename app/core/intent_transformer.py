"""
Intent routing via semantic similarity against a small set of hand-written
prototype phrases per intent (see INTENT_PROTOTYPES in ui_registry.py).

Previously this ran sentence-transformers + torch locally. Both are gone
now (see vector_service.py) — this calls the same OpenRouter embeddings
endpoint instead, and cosine similarity is computed by hand since one dot
product doesn't need a tensor library.
"""
import asyncio
import math
from typing import Dict, List, Optional

from app.core.ui_registry import INTENT_PROTOTYPES
from app.services.vector_service import embed_text_async, embed_texts_async

SIMILARITY_THRESHOLD = 0.6

_prototype_embeddings: Optional[Dict[str, List[List[float]]]] = None
_prototype_lock = asyncio.Lock()


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _get_prototype_embeddings() -> Dict[str, List[List[float]]]:
    """Lazy-load and cache prototype embeddings on first call. Locked so
    concurrent first requests don't each fire the same batch of embedding
    calls."""
    global _prototype_embeddings
    if _prototype_embeddings is not None:
        return _prototype_embeddings

    async with _prototype_lock:
        if _prototype_embeddings is None:  # re-check: another request may have won the race
            result: Dict[str, List[List[float]]] = {}
            for intent, phrases in INTENT_PROTOTYPES.items():
                result[intent] = await embed_texts_async(phrases)
            _prototype_embeddings = result

    return _prototype_embeddings


async def dynamic_intent_router(text: str) -> str:
    """Dynamically routes intent based on semantic similarity."""
    prototype_embeddings = await _get_prototype_embeddings()
    user_embedding = await embed_text_async(text)

    best_intent = "supervisor"
    highest_score = 0.0

    for intent, embeddings in prototype_embeddings.items():
        max_score = max(_cosine_similarity(user_embedding, proto) for proto in embeddings)
        if max_score > SIMILARITY_THRESHOLD and max_score > highest_score:
            highest_score = max_score
            best_intent = intent

    return best_intent