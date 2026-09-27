from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .challenger_selection import create_selected_challengers
from .optimizer_candidates import qualified_candidates_page
from .optimizer_core import contract_payload, freeze_request
from .optimizer_scientist import scientist_route_status
from .optimizer_jobs import (
    job_detail,
    latest_job_detail,
    resume_optimizer,
    start_optimizer,
    stop_optimizer,
)

router = APIRouter(prefix="/api/optimizer", tags=["optimizer"])


class QualifiedSelection(BaseModel):
    round: int = Field(ge=1)
    pass_: int = Field(alias="pass", ge=0)

    model_config = {"populate_by_name": True}


class PromoteQualifiedRequest(BaseModel):
    selections: list[QualifiedSelection]


@router.get("/contract")
def get_contract() -> dict:
    return contract_payload()


@router.get("/scientist/status")
def scientist_status() -> dict:
    return scientist_route_status()


@router.post("/preview")
def preview_request(payload: dict) -> dict:
    try:
        frozen = freeze_request(payload)
        return {
            "status": "VALID",
            "trade_sample": frozen["trade_sample"],
            "search_space_cardinality": frozen["search_space_cardinality"],
            "optimizer_fitness": frozen["optimizer_fitness"],
            "ea": frozen["ea"],
            "mt5": frozen["mt5"],
            "kpi": frozen["kpi"],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/current")
def current_job() -> dict | None:
    return latest_job_detail()


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_detail(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Optimizer job not found")
    return job


@router.post("/start")
def start(payload: dict) -> dict:
    try:
        return start_optimizer(payload)
    except RuntimeError as exc:
        message = str(exc)
        code = 409 if "already active" in message else 400
        raise HTTPException(status_code=code, detail=message) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/qualified-candidates")
def get_qualified_candidates(
    job_id: str,
    sort: str = "mean_r",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    q: str = "",
    round: int | None = None,
) -> dict:
    try:
        return qualified_candidates_page(
            job_id,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
            query=q,
            round_no=round,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Optimizer job not found") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/challengers")
def promote_qualified_candidates(
    job_id: str,
    payload: PromoteQualifiedRequest,
) -> dict:
    try:
        selections = [
            {"round": item.round, "pass": item.pass_}
            for item in payload.selections
        ]
        return create_selected_challengers(job_id, selections)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Optimizer job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/stop")
def stop(job_id: str) -> dict:
    try:
        return stop_optimizer(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Optimizer job not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str) -> dict:
    try:
        return resume_optimizer(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Optimizer job not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
