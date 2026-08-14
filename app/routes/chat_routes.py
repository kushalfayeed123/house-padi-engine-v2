import urllib.parse
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from app.core.dependecies import get_optional_user_context
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_message
from logging import getLogger


logger = getLogger("uvicorn")
router = APIRouter()


def _gate_redirect(target_url: str, context: Optional[dict], thread_id) -> Optional[ChatResponse]:
    """
    Auth/role gating for any redirect_url, whether it came from the
    INTENT_UI_MAP shortcut (chat_service step 1) or from a tool called
    mid-conversation (e.g. trigger_property_ui_worker). Returns a
    ChatResponse to send instead if the redirect is blocked, or None if
    it's fine to proceed with the requested redirect.
    """
    if ("dashboard" in target_url or "account" in target_url) and not context:
        encoded_return = urllib.parse.quote(target_url, safe="")
        return ChatResponse(
            thread_id=thread_id,
            status="success",
            type="redirect",
            response="Please click the link below to sign in and continue your request.",
            redirect_url=f"/login?returnUrl={encoded_return}"
        )

    if "landlord" in target_url:
        if not context:
            encoded_return = urllib.parse.quote(target_url, safe="")
            return ChatResponse(
                thread_id=thread_id,
                status="success",
                type="redirect",
                response="Please click the link below to sign in as a landlord and complete your request.",
                redirect_url=f"/login?returnUrl={encoded_return}"
            )
        if context.get("role") != "owner":
            return ChatResponse(
                thread_id=thread_id,
                status="error",
                type="response",
                response="You are not authorized to access landlord features."
            )

    return None


@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(
    body: ChatRequest,
    context: Optional[dict] = Depends(get_optional_user_context)
):
    try:
        service_result = await process_chat_message(body.message, body.thread_id, context)

        # Handle Redirect Path (INTENT_UI_MAP shortcut — chat_service step 1)
        if service_result.get("type") == "redirect":
            target_url = service_result.get("redirect_url", "")

            gated = _gate_redirect(target_url, context, service_result.get("thread_id"))
            if gated:
                return gated

            # If authorized or public redirect, prompt the user to click the link
            return ChatResponse(
                thread_id=service_result.get("thread_id"),
                status="success",
                type="redirect",
                response="Please click the link below to complete your request.",
                redirect_url=target_url
            )

        # Handle standard Chat Response Path
        content = (service_result.get("content") or "").lower()
        if not context and (
            "user context missing" in content
            or "sign in first" in content
            or "log in or create an account" in content
            or "please sign in" in content
            or service_result.get("requires_login")
        ):
            return ChatResponse(
                thread_id=service_result.get("thread_id"),
                status="success",
                type="redirect",
                response="Please sign in to continue your request.",
                redirect_url=service_result.get("redirect_url") or "/login?returnUrl=/"
            )

        # A tool called mid-conversation (e.g. trigger_property_ui_worker) may
        # have surfaced its own redirect_url — gate it the same way as the
        # dedicated redirect path above, but keep the model's actual reply
        # text (e.g. the property-creation form prompt) unless the redirect
        # is blocked outright.
        redirect_url = service_result.get("redirect_url")
        if redirect_url:
            gated = _gate_redirect(redirect_url, context, service_result.get("thread_id"))
            if gated:
                return gated

        return ChatResponse(
            thread_id=service_result.get("thread_id"),
            status="success",
            type="response",
            response=service_result["content"],
            data=service_result.get("data"),
            redirect_url=redirect_url,
        )

    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))