from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .scientist_chat import ScientistChatError, safe_route_status, send_scientist_message
from .scientist_context import domain_authority_fingerprint
from .scientist_knowledge import knowledge_status, load_knowledge, safe_knowledge_summary
from .scientist_provider import (
    ProviderSettingsError,
    discover_models,
    get_provider_settings,
    provider_catalog,
    save_provider_settings,
    save_ui_state,
)
from .scientist_store import (
    clear_chat,
    get_active_thread,
    list_messages,
    scientist_database_status,
)

router = APIRouter(prefix="/api/scientist", tags=["scientist"])


class ScientistMessageRequest(BaseModel):
    request_id: str
    content: str
    model: str | None = None
    context_scope: str = "AUTO"


class ScientistProviderRequest(BaseModel):
    provider_key: str
    base_url: str
    auth_mode: str = "API_KEY"
    timeout_sec: int = 60
    primary_model: str = ""
    autonomous_fallback: list[str] = Field(default_factory=list)
    chat_fallback: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    api_key: str | None = None


class ScientistUiStateRequest(BaseModel):
    left_nav_open: bool | None = None
    scientist_drawer_open: bool | None = None
    scientist_chat_model: str | None = None
    scientist_context: str | None = None


def _provider_error(exc: ProviderSettingsError) -> HTTPException:
    validation = {
        "SCIENTIST_PROVIDER_TYPE_INVALID",
        "SCIENTIST_PROVIDER_URL_REQUIRED",
        "SCIENTIST_PROVIDER_URL_INVALID",
        "SCIENTIST_PROVIDER_ENDPOINT_LOCKED",
        "SCIENTIST_OLLAMA_LOCALHOST_REQUIRED",
        "SCIENTIST_REMOTE_HTTPS_REQUIRED",
        "SCIENTIST_REMOTE_AUTH_REQUIRED",
        "SCIENTIST_AUTH_MODE_INVALID",
        "SCIENTIST_TIMEOUT_INVALID",
        "SCIENTIST_MODEL_REQUIRED",
        "SCIENTIST_CREDENTIAL_REQUIRED",
        "SCIENTIST_REMOTE_PRIVATE_HOST_REJECTED",
        "SCIENTIST_PROVIDER_HOST_UNRESOLVED",
    }
    return HTTPException(
        status_code=400 if exc.code in validation else 503,
        detail=exc.code,
    )


@router.get("/status")
def get_status() -> dict:
    database = scientist_database_status()
    knowledge = knowledge_status()
    route = safe_route_status()
    return {
        "status": (
            "READY"
            if database.get("status") == "READY"
            and knowledge.get("status") == "READY"
            else "NOT_READY"
        ),
        "read_only": True,
        "knowledge_status": knowledge.get("status"),
        "knowledge_sha256": knowledge.get("knowledge_sha256"),
        "provider_configured": route["configured"],
        "provider_status": route["status"],
        "provider": route["provider"],
        "model": route["model"],
        "provider_source": route.get("source"),
        "provider_auth_mode": route.get("auth_mode"),
        "credential_status": route.get("credential_status"),
        "chat_schema_version": database.get("schema_version"),
    }


@router.get("/provider-catalog")
def get_scientist_provider_catalog() -> list[dict]:
    return provider_catalog()


@router.get("/provider-settings")
def get_scientist_provider_settings() -> dict:
    try:
        return get_provider_settings()
    except ProviderSettingsError as exc:
        raise _provider_error(exc) from exc


@router.post("/provider-settings/connect")
def connect_scientist_provider(payload: ScientistProviderRequest) -> dict:
    before = domain_authority_fingerprint()
    try:
        body = payload.model_dump(exclude={"api_key"})
        result = discover_models(body, api_key=payload.api_key)
    except ProviderSettingsError as exc:
        raise _provider_error(exc) from exc
    after = domain_authority_fingerprint()
    if before["sha256"] != after["sha256"]:
        raise HTTPException(
            status_code=500,
            detail="SCIENTIST_DOMAIN_AUTHORITY_MUTATED",
        )
    return {**result, "domain_authority_unchanged": True}


@router.put("/provider-settings")
def put_scientist_provider_settings(payload: ScientistProviderRequest) -> dict:
    before = domain_authority_fingerprint()
    try:
        body = payload.model_dump(exclude={"api_key"})
        result = save_provider_settings(body, api_key=payload.api_key)
    except ProviderSettingsError as exc:
        raise _provider_error(exc) from exc
    after = domain_authority_fingerprint()
    if before["sha256"] != after["sha256"]:
        raise HTTPException(
            status_code=500,
            detail="SCIENTIST_DOMAIN_AUTHORITY_MUTATED",
        )
    return {**result, "domain_authority_unchanged": True}


@router.put("/ui-settings")
def put_scientist_ui_settings(payload: ScientistUiStateRequest) -> dict:
    patch = {
        key: value
        for key, value in payload.model_dump().items()
        if value is not None
    }
    try:
        return save_ui_state(patch)
    except ProviderSettingsError as exc:
        raise _provider_error(exc) from exc


@router.get("/knowledge")
def get_knowledge() -> dict:
    status = knowledge_status()
    if status.get("status") != "READY":
        raise HTTPException(
            status_code=503,
            detail=str(
                status.get("reason")
                or "SCIENTIST_KNOWLEDGE_UNAVAILABLE"
            ),
        )
    return safe_knowledge_summary(load_knowledge())




@router.get("/chat")
def get_scientist_chat() -> dict:
    return get_active_thread()


@router.post("/chat/clear")
def clear_scientist_chat() -> dict:
    before = domain_authority_fingerprint()
    try:
        thread = clear_chat()
    except RuntimeError as exc:
        if str(exc) == "SCIENTIST_CLEAR_BLOCKED_CALL_IN_FLIGHT":
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    after = domain_authority_fingerprint()
    if before["sha256"] != after["sha256"]:
        raise HTTPException(
            status_code=500,
            detail="SCIENTIST_DOMAIN_AUTHORITY_MUTATED",
        )
    return {
        "thread": thread,
        "domain_authority_unchanged": True,
    }


@router.get("/threads")
def get_threads() -> list[dict]:
    # Backward-compatible alias: M05 Owner UX now has exactly one chat room.
    return [get_active_thread()]


@router.post("/threads")
def post_thread() -> dict:
    # Backward-compatible alias: never create a second Owner chat room.
    return get_active_thread()


@router.get("/threads/{thread_id}/messages")
def get_thread_messages(thread_id: str) -> list[dict]:
    try:
        return list_messages(thread_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Scientist thread not found",
        ) from exc


@router.post("/threads/{thread_id}/messages")
def post_thread_message(
    thread_id: str,
    payload: ScientistMessageRequest,
) -> dict:
    try:
        return send_scientist_message(
            thread_id,
            request_id=payload.request_id,
            content=payload.content,
            model=payload.model,
            context_scope=payload.context_scope,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Scientist thread not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ScientistChatError as exc:
        unavailable = {
            "SCIENTIST_KNOWLEDGE_STALE",
            "SCIENTIST_KNOWLEDGE_MISSING",
            "SCIENTIST_CREDENTIAL_MISSING",
            "SCIENTIST_ROUTE_UNAVAILABLE",
            "SCIENTIST_OLLAMA_UNAVAILABLE",
            "SCIENTIST_MODEL_UNAVAILABLE",
            "SCIENTIST_AUTHENTICATION_REQUIRED",
            "SCIENTIST_TIMEOUT",
            "SCIENTIST_RATE_LIMITED",
            "SCIENTIST_PROVIDER_5XX",
        }
        conflict = {
            "SCIENTIST_CALL_IN_FLIGHT",
            "SCIENTIST_UNCONFIRMED_CALL_AFTER_RESTART",
        }
        code = (
            503
            if exc.code in unavailable
            else 409
            if exc.code in conflict
            else 502
        )
        raise HTTPException(status_code=code, detail=exc.code) from exc
