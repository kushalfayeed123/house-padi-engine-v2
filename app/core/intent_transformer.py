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


# Minimum gap the top-scoring bucket must have over the runner-up to be
# trusted as a clean, single-purpose match. A small margin here IS the
# definition of a compound/ambiguous message (e.g. "what's my balance,
# and can I pay rent" scores close on both a redirect bucket and its
# read-only counterpart) — same concept as _FORCE_CLUSTER_MARGIN in
# lightweight_agent.py, applied at the routing layer instead of the
# tool-forcing layer.
AMBIGUITY_MARGIN = 0.08


async def dynamic_intent_router(
    text: str,
    precomputed_embedding: Optional[List[float]] = None,
) -> Optional[str]:
    """Dynamically routes intent based on semantic similarity.

    Returns None when either (a) no bucket clears SIMILARITY_THRESHOLD, or
    (b) the top bucket doesn't clearly beat the runner-up by
    AMBIGUITY_MARGIN — the latter catches compound messages that
    legitimately match two different intents at once (a balance check
    phrased alongside a payment request, say) rather than forcing a
    single winner that discards half of what the user asked.

    Both cases correctly fall through to the normal tool-bound agent path
    in invoke_lightweight_agent, letting the model itself decide what to
    answer now vs. confirm before acting on.
    """
    prototype_embeddings = await _get_prototype_embeddings()
    user_embedding = precomputed_embedding if precomputed_embedding is not None else await embed_text_async(text)

    scored: List[tuple[str, float]] = []
    for intent, embeddings in prototype_embeddings.items():
        max_score = max(_cosine_similarity(user_embedding, proto) for proto in embeddings)
        scored.append((intent, max_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_intent, top_score = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0.0

    if top_score <= SIMILARITY_THRESHOLD:
        return None
    if (top_score - runner_up_score) < AMBIGUITY_MARGIN:
        return None

    return top_intent