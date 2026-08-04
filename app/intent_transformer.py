from sentence_transformers import util
import torch

from app.services.vector_service import get_model
from app.ui_registry import INTENT_PROTOTYPES


_router_model = get_model()



# Pre-compute embeddings for these prototypes
prototype_embeddings = {
    intent: _router_model.encode(phrases, convert_to_tensor=True) 
    for intent, phrases in INTENT_PROTOTYPES.items()
}

def dynamic_intent_router(text: str) -> str:
    """Dynamically routes intent based on semantic similarity."""
    user_embedding = _router_model.encode(text, convert_to_tensor=True)
    
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