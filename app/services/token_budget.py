"""
Shared history-trimming and TPM-budget helpers.

Extracted from agentic_graph.py so the multi-agent and lightweight backends
use identical trimming logic — avoids the two drifting apart as one gets
tuned and the other doesn't.
"""
from typing import List
from langchain_core.messages import BaseMessage, convert_to_messages, trim_messages


def _rough_token_len(messages) -> int:
    """Conservative ~4 chars/token estimate handling string, list, or dict content."""
    total_chars = 0
    for m in messages:
        content = getattr(m, "content", m)
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += sum(len(str(item)) for item in content)
        else:
            total_chars += len(str(content))
    return total_chars // 4


def trim_conversation_history(messages: List[dict], max_tokens: int = 4000) -> List[BaseMessage]:
    """
    Cap the conversation history sent to the model, keeping the most recent
    turns. `max_tokens` should already account for fixed per-call overhead
    (system prompt + tool schemas + response reserve) — see budget_for_model().
    """
    converted = convert_to_messages(messages)
    return trim_messages(
        converted,
        token_counter=_rough_token_len,
        max_tokens=max_tokens,
        strategy="last",
        include_system=True,
        allow_partial=False,
    )


# The naive fix (a flat max_tokens constant) doesn't account for the fixed
# overhead every call pays regardless of conversation length: the system
# prompt, tool schemas, and Groq's reserved output-token allowance.
MODEL_TPM_CEILINGS = {
    "llama-3.1-8b-instant": 6000,
    "llama-3.3-70b-versatile": 12000,
}
FIXED_OVERHEAD_ESTIMATE = 2200  # system prompt + tool schemas + response reserve


def budget_for_model(model_name: str) -> int:
    """Message-history token budget that leaves room for fixed per-call overhead."""
    ceiling = MODEL_TPM_CEILINGS.get(model_name, 6000)
    return max(500, ceiling - FIXED_OVERHEAD_ESTIMATE)