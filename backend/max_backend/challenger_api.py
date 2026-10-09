from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .backtest_control import (
    backtest_action_preflight,
    challenger_delete_preflight,
    clean_backtest_runtime,
    delete_backtest,
    delete_challenger,
    execute_backtest_action,
    runtime_inventory,
    verified_retained_report,
)
from .challenger_operations import (
    backtest_detail,
    backtest_registry_page,
    backtest_history,
    challenger_bundle_artifact_path,
    challenger_registry_page,
    retire_challenger,
    run_challenger_backtest,
)
from .challenger_registry import challenger_detail, list_challengers


class RetirementConfirmation(BaseModel):
    expected_challenger_manifest_sha256: str
    confirmed: bool


class DeleteConfirmation(BaseModel):
    confirmed: bool


class BacktestSelection(BaseModel):
    backtest_ids: list[str]


class BacktestBulkAction(BacktestSelection):
    action: str
    confirmed: bool


router = APIRouter(prefix="/api/challengers", tags=["challengers"])


def _domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    message = str(exc)
    conflict_tokens = (
        "STALE",
        "NOT_ACTIVE",
        "BLOCKED",
        "ALREADY",
        "INTEGRITY",
        "RUNNING",
        "INTERRUPTED",
    )
    status = 409 if any(token in message for token in conflict_tokens) else 400
    return HTTPException(status_code=status, detail=message)


@router.get("")
def get_challengers() -> list[dict]:
    """Compatibility surface: active CHALLENGER rows only."""
    return list_challengers()


@router.get("/registry")
def get_challenger_registry(
    view: str = "active",
    q: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    try:
        return challenger_registry_page(
            view=view,
            query=q,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except (ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.post("/backtests/preflight")
def backtest_preflight(payload: BacktestBulkAction) -> dict[str, Any]:
    try:
        return backtest_action_preflight(
            payload.backtest_ids,
            action=payload.action,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.post("/backtests/action")
def backtest_bulk_action(payload: BacktestBulkAction) -> dict[str, Any]:
    try:
        return execute_backtest_action(
            payload.backtest_ids,
            action=payload.action,
            confirmed=payload.confirmed,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/backtests")
def get_backtests(
    challenger_id: str = "",
    q: str = "",
    state: str = "",
    sort: str = "created",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    try:
        return backtest_registry_page(
            challenger_id=challenger_id or None,
            query=q,
            state=state,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except (ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/backtests/{backtest_id}")
def get_backtest_detail(backtest_id: str) -> dict[str, Any]:
    try:
        detail = backtest_detail(backtest_id)
        detail["runtime_inventory"] = runtime_inventory(backtest_id)
        return detail
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/backtests/{backtest_id}/report")
def open_backtest_report(backtest_id: str):
    try:
        report = verified_retained_report(backtest_id)
        return FileResponse(
            path=report,
            media_type="text/html",
            filename=report.name,
            content_disposition_type="inline",
        )
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.post("/backtests/{backtest_id}/clean-runtime")
def clean_runtime(backtest_id: str) -> dict[str, Any]:
    try:
        return clean_backtest_runtime(backtest_id)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.delete("/backtests/{backtest_id}")
def delete_backtest_endpoint(
    backtest_id: str,
    payload: DeleteConfirmation,
) -> dict[str, Any]:
    try:
        return delete_backtest(backtest_id, confirmed=payload.confirmed)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/{challenger_id}/delete-preflight")
def get_challenger_delete_preflight(challenger_id: str) -> dict[str, Any]:
    try:
        return challenger_delete_preflight(challenger_id)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.delete("/{challenger_id}")
def delete_challenger_endpoint(
    challenger_id: str,
    payload: DeleteConfirmation,
) -> dict[str, Any]:
    try:
        return delete_challenger(challenger_id, confirmed=payload.confirmed)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/{challenger_id}")
def get_challenger(challenger_id: str) -> dict:
    try:
        return challenger_detail(challenger_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Strategy Challenger not found") from exc


@router.get("/{challenger_id}/ea")
def download_challenger_ea(challenger_id: str):
    try:
        artifact = challenger_bundle_artifact_path(challenger_id, "ea")
        return FileResponse(
            path=artifact,
            media_type="application/octet-stream",
            filename=artifact.name,
            content_disposition_type="attachment",
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/{challenger_id}/set")
def download_challenger_set(challenger_id: str):
    try:
        artifact = challenger_bundle_artifact_path(challenger_id, "set")
        return FileResponse(
            path=artifact,
            media_type="application/octet-stream",
            filename=artifact.name,
            content_disposition_type="attachment",
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.get("/{challenger_id}/backtests")
def get_challenger_backtests(challenger_id: str) -> list[dict[str, Any]]:
    try:
        return backtest_history(challenger_id)
    except (FileNotFoundError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.post("/{challenger_id}/backtest")
def run_backtest(
    challenger_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return run_challenger_backtest(
            challenger_id,
            request_payload=payload or {},
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc


@router.post("/{challenger_id}/retire")
def retire(
    challenger_id: str,
    payload: RetirementConfirmation,
) -> dict[str, Any]:
    try:
        return retire_challenger(
            challenger_id,
            expected_manifest_sha256=payload.expected_challenger_manifest_sha256,
            confirmed=payload.confirmed,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise _domain_error(exc) from exc
