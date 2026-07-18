import json
import logging
from typing import Dict, Any
from app.agent_engine import invoke_housepadi_agent
from app.intent_transformer import dynamic_intent_router
from app.ui_registry import INTENT_UI_MAP

logger = logging.getLogger("uvicorn")

TOOL_KEY_MAP = {
    "search_properties_worker": "properties",
    "get_wallet_balance_worker": "wallet",
    "list_tours_worker": "tours",
    "get_messages_worker": "chat_history",
    "get_kyc_status_worker": "kyc_status"
}

async def process_chat_message(message: str, thread_id: str | None) -> Dict[str, Any]:
    # 1. Intent Detection
    intent = dynamic_intent_router(message)
    
    # 2. Check UI Redirect
    if intent in INTENT_UI_MAP:
        return {
            "type": "redirect",
            "redirect_url": INTENT_UI_MAP[intent]
        }

    # 3. Agent Orchestration
    augmented_messages = [
        {"role": "system", "content": f"Context Hint: Related to {intent}. Use to inform sub-agent selection."},
        {"role": "user", "content": message}
    ]

    result = await invoke_housepadi_agent(
        messages=augmented_messages,
        thread_id=thread_id or "default_thread"
    )

    # 4. Data Aggregation
    dynamic_data = {}
    for msg in result["messages"]:
        if msg.type == "tool":
            key = TOOL_KEY_MAP.get(msg.name, msg.name)
            try:
                dynamic_data[key] = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                dynamic_data[key] = msg.content

    return {
        "type": "response",
        "content": result["messages"][-1].content,
        "data": dynamic_data
    }