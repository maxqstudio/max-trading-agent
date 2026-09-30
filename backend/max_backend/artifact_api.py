from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .artifact_control import (
    artifact_page,
    artifact_preflight,
    artifact_trace,
    clean_generated_data,
    execute_artifact_action,
    global_cleanup_preflight,
    reconcile_artifacts,
)
from .strategy_reset import (
    reset_strategy_workspace,
    strategy_reset_preflight,
)


router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


class ArtifactSelection(BaseModel):
    artifact_ids: list[str] = Field(min_length=1)


class ArtifactAction(ArtifactSelection):
    action: Literal["clean", "delete"]
    confirmed: bool


class GlobalCleanupConfirmation(BaseModel):
    confirmed: bool


class StrategyWorkspaceResetConfirmation(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


@router.post("/reconcile")
def reconcile() -> dict:
    try:
        return reconcile_artifacts()
    except (ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Artifact not found")
    message = str(exc)
    conflict = any(
        token in message
        for token in ("BLOCKED", "IN_USE", "ACTIVE", "PROTECTED", "REPARSE", "ESCAPE")
    )
    return HTTPException(status_code=409 if conflict else 400, detail=message)


@router.get("")
def list_artifacts(
    q: str = "",
    type: str = "",
    producer: str = "",
    status: str = "",
    retention: str = "",
    in_use: str = "",
    storage: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    try:
        return artifact_page(
            query=q,
            artifact_type=type,
            producer=producer,
            status=status,
            retention=retention,
            in_use=in_use,
            storage=storage,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except (ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/preflight")
def preflight(payload: ArtifactSelection) -> dict:
    try:
        return artifact_preflight(payload.artifact_ids)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/action")
def act(payload: ArtifactAction) -> dict:
    try:
        return execute_artifact_action(
            payload.artifact_ids,
            action=payload.action,
            confirmed=payload.confirmed,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/cleanup/preflight")
def cleanup_preflight() -> dict:
    try:
        return global_cleanup_preflight()
    except RuntimeError as exc:
        raise _error(exc) from exc


@router.post("/cleanup")
def cleanup(payload: GlobalCleanupConfirmation) -> dict:
    try:
        return clean_generated_data(confirmed=payload.confirmed)
    except RuntimeError as exc:
        raise _error(exc) from exc


@router.get("/strategy-reset/preflight")
def strategy_workspace_reset_preflight() -> dict:
    try:
        return strategy_reset_preflight()
    except (ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/strategy-reset")
def strategy_workspace_reset(payload: StrategyWorkspaceResetConfirmation) -> dict:
    try:
        return reset_strategy_workspace(confirmed=payload.confirmation)
    except (ValueError, RuntimeError, OSError) as exc:
        raise _error(exc) from exc


@router.get("/{artifact_id}/trace")
def trace(artifact_id: str) -> dict:
    try:
        return artifact_trace(artifact_id)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _error(exc) from exc
