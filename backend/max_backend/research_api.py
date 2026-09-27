from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .research_service import (
    current_research,
    research_contract_overview,
    research_detail,
    r00_preflight,
    start_r00,
)
from .research_settings import (
    get_research_sample_configuration,
    set_research_sample_configuration,
)
from .research_r01_service import (
    prepare_r01_verified_source,
    r01_detail,
    r01_preflight,
    r01_source_overview,
    start_r01,
)


class R00StartRequest(BaseModel):
    expected_parent_strategy_id: str
    expected_parent_authority_sha256: str
    owner_confirmation: str
    cumulative_strategy_e2e_authority: str
    h1_minimum_trades_per_month: int | None = Field(default=None, gt=0)
    confirmed: bool


class ResearchSampleConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h1_minimum_trades_per_month: int = Field(gt=0)


class R01SourcePrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_id: str
    expected_parent_strategy_id: str
    owner_confirmation: str
    from_date: str
    to_date: str
    confirmed: bool


class R01StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_id: str
    expected_parent_strategy_id: str
    owner_confirmation: str
    source_id: str
    discovery_from: str
    discovery_to: str
    locked_oos_from: str
    locked_oos_to: str
    fresh_forward_from: str
    fresh_forward_to: str
    confirmed: bool


router = APIRouter(tags=["research"])


@router.get("/api/research/contracts")
def get_research_contracts() -> dict:
    return research_contract_overview()


@router.get("/api/research/sample-config")
def get_research_sample_config() -> dict:
    try:
        config = get_research_sample_configuration()
        return {
            "configured": bool(config["configured"]),
            "h1_minimum_trades_per_month": config[
                "h1_minimum_trades_per_month"
            ],
            "updated_utc": config["updated_utc"],
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/research/sample-config")
def put_research_sample_config(
    payload: ResearchSampleConfigurationRequest,
) -> dict:
    try:
        return set_research_sample_configuration(
            payload.h1_minimum_trades_per_month
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/research/r00/preflight")
def get_r00_preflight() -> dict:
    try:
        return r00_preflight()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/research/current")
def get_current_research() -> dict:
    try:
        return current_research()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/research/r01/source")
def get_r01_source() -> dict:
    try:
        return r01_source_overview()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/research/r01/source/prepare")
def prepare_research_r01_source(payload: R01SourcePrepareRequest) -> dict:
    try:
        return prepare_r01_verified_source(payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
        conflict_tokens = (
            "STALE","COLLISION","ALREADY","INTEGRITY","MISMATCH",
            "IMMUTABLE","REQUIRED","UNAVAILABLE","UNPROVEN","NOT_READY",
        )
        code = 409 if any(token in message for token in conflict_tokens) else 400
        raise HTTPException(status_code=code, detail=message) from exc


@router.get("/api/research/r01/preflight")
def get_r01_preflight() -> dict:
    try:
        return r01_preflight()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/research/r01/detail")
def get_r01_detail() -> dict:
    try:
        return r01_detail()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/research/r01/start")
def start_research_r01(payload: R01StartRequest) -> dict:
    try:
        return start_r01(payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
        conflict_tokens = (
            "STALE","COLLISION","ALREADY","INTEGRITY","MISMATCH",
            "IMMUTABLE","REQUIRED","BOUND",
        )
        code = 409 if any(token in message for token in conflict_tokens) else 400
        raise HTTPException(status_code=code, detail=message) from exc


@router.get("/api/research/{research_id}")
def get_research_detail(research_id: str) -> dict:
    try:
        return research_detail(research_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Research identity not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/research/r00/start")
def start_research_r00(payload: R00StartRequest) -> dict:
    try:
        return start_r00(payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        conflict_tokens = (
            "STALE",
            "COLLISION",
            "IN_PROGRESS",
            "NOT_VERIFIED",
            "INTEGRITY",
            "MISMATCH",
            "MISSING",
        )
        code = 409 if any(token in message for token in conflict_tokens) else 400
        raise HTTPException(status_code=code, detail=message) from exc
