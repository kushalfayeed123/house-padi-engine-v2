from sentence_transformers import util
import torch

from app.services.vector_service import get_model


_router_model = get_model()

intent_prototypes = {
    "TRIGGER_PROPERTY_UI": ["create a new property", "list my apartment", "publish a listing", "add a new house"],
    "property-specialist": ["search for houses", "find an apartment", "show me listings"],
    "tour-specialist": ["book a tour", "schedule a visit", "see the place"],
    "lease-specialist": ["sign lease agreement", "contract terms", "lease application"],
    "payment-specialist": ["pay rent", "transfer money", "check wallet balance"],
    "kyc-specialist": ["verify identity", "upload id", "kyc status"],
    "chat-specialist": ["message landlord", "contact owner", "start a chat"]
}

# Pre-compute embeddings for these prototypes
prototype_embeddings = {
    intent: _router_model.encode(phrases, convert_to_tensor=True) 
    for intent, phrases in intent_prototypes.items()
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