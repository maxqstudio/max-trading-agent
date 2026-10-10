"""Versioned ONNX-02 data-intake API; scientific execution is not exposed."""

from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .onnx_data_contract import WINDOW_NAMES
from .onnx_data_service import OnnxDataService
from .onnx_data_source import OnnxDataSourceError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/onnx/data", tags=["ONNX data intake v2"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourcePreflightRequest(StrictRequest):
    source_path: str | None = Field(default=None, min_length=1, max_length=1024)
    timezone_provenance: str = Field(min_length=1, max_length=160)

    @field_validator("source_path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("source_path cannot be blank; omit it to use the canonical filename")
        return value


class SnapshotRequest(SourcePreflightRequest):
    expected_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class DuplicateResolutionRequest(StrictRequest):
    expected_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: Literal["REMOVE_IDENTICAL_DUPLICATES"]


class WindowRange(StrictRequest):
    start: str = Field(alias="from", min_length=19, max_length=35)
    end: str = Field(alias="to", min_length=19, max_length=35)


class WindowValidationRequest(StrictRequest):
    snapshot_id: str = Field(min_length=68, max_length=68)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timezone_provenance: str = Field(min_length=1, max_length=160)
    windows: dict[str, WindowRange]

    @model_validator(mode="after")
    def exact_windows(self) -> "WindowValidationRequest":
        if set(self.windows) != set(WINDOW_NAMES) or len(self.windows) != 3:
            raise ValueError("exactly DISCOVERY, TOURNAMENT, and FORWARD windows are required")
        return self


def get_data_service() -> OnnxDataService:
    return OnnxDataService()


def _raise_api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnnxDataSourceError):
        return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, sqlite3.Error):
        logger.exception("ONNX data intake storage operation failed")
        return HTTPException(status_code=503, detail={"code": "ONNX_DATA_STORAGE_UNAVAILABLE", "message": "Local immutable data evidence storage is unavailable."})
    if isinstance(exc, OSError):
        logger.exception("ONNX data intake filesystem operation failed")
        return HTTPException(status_code=503, detail={"code": "ONNX_DATA_FILESYSTEM_UNAVAILABLE", "message": "Local data source or snapshot filesystem operation failed."})
    message = str(exc)
    conflict = any(token in message for token in ("CHANGED", "MISMATCH", "IMMUTABLE", "CONFLICT", "ALREADY", "REQUIRED"))
    return HTTPException(status_code=409 if conflict else 400, detail={"code": message or "ONNX_DATA_REQUEST_INVALID", "message": message or "ONNX data request is invalid."})


@router.get("/workspace")
def get_data_workspace(service: OnnxDataService = Depends(get_data_service)) -> dict:
    try:
        return service.workspace()
    except (OnnxDataSourceError, ValueError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc


# End of the versioned ONNX-02 data router.


@router.post("/preflight")
def preflight_source(payload: SourcePreflightRequest, service: OnnxDataService = Depends(get_data_service)) -> dict:
    try:
        return service.preflight(source_path=payload.source_path, timezone_provenance=payload.timezone_provenance)
    except (OnnxDataSourceError, ValueError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc


@router.post("/snapshots")
def create_snapshot(payload: SnapshotRequest, service: OnnxDataService = Depends(get_data_service)) -> dict:
    try:
        return service.create_snapshot(
            source_path=payload.source_path,
            timezone_provenance=payload.timezone_provenance,
            expected_source_sha256=payload.expected_source_sha256,
        )
    except (OnnxDataSourceError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc
@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, service: OnnxDataService = Depends(get_data_service)) -> dict:
    try:
        return service.snapshot_details(snapshot_id)
    except (OnnxDataSourceError, ValueError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc


@router.post("/snapshots/{snapshot_id}/resolve-identical-duplicates")
def resolve_identical_duplicates(
    snapshot_id: str,
    payload: DuplicateResolutionRequest,
    service: OnnxDataService = Depends(get_data_service),
) -> dict:
    try:
        return service.resolve_identical_duplicates(
            snapshot_id=snapshot_id,
            expected_snapshot_sha256=payload.expected_snapshot_sha256,
            confirmation=payload.confirmation,
        )
    except (OnnxDataSourceError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc


@router.post("/windows/validate")
def validate_windows(payload: WindowValidationRequest, service: OnnxDataService = Depends(get_data_service)) -> dict:
    try:
        windows = {
            name: {"from": item.start, "to": item.end}
            for name, item in payload.windows.items()
        }
        return service.validate_windows(
            snapshot_id=payload.snapshot_id,
            snapshot_sha256=payload.snapshot_sha256,
            timezone_provenance=payload.timezone_provenance,
            windows=windows,
        )
    except (OnnxDataSourceError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        raise _raise_api_error(exc) from exc
