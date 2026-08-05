from typing import Dict, Optional
from sentence_transformers import util
import torch

from app.core.ui_registry import INTENT_PROTOTYPES
from app.services.vector_service import get_model

# Global cache for prototype embeddings
_prototype_embeddings: Optional[Dict[str, torch.Tensor]] = None


def _get_prototype_embeddings() -> Dict[str, torch.Tensor]:
    """Lazy-load and cache prototype embeddings on first call."""
    global _prototype_embeddings
    if _prototype_embeddings is None:
        model = get_model()
        _prototype_embeddings = {
            intent: model.encode(phrases, convert_to_tensor=True)
            for intent, phrases in INTENT_PROTOTYPES.items()
        }
    return _prototype_embeddings


def dynamic_intent_router(text: str) -> str:
    """Dynamically routes intent based on semantic similarity."""
    model = get_model()
    prototype_embeddings = _get_prototype_embeddings()
    
    user_embedding = model.encode(text, convert_to_tensor=True)
    
    best_intent = "supervisor"
    highest_score = 0.0
    
    for intent, embeddings in prototype_embeddings.items():
        # Compute cosine similarity
        scores = util.cos_sim(user_embedding, embeddings)
        max_score = torch.max(scores).item()
        
        if max_score > 0.6 and max_score > highest_score:
            highest_score = max_score
            best_intent = intent
            
    return best_intent