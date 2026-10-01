from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .artifact_api import router as artifact_router
from .challenger_api import router as challenger_router
from .challenger_operations_store import (
    challenger_operations_database_status,
    migrate_m06,
    recover_incomplete_backtests,
)
from .champion_api import router as champion_router
from .champion_store import champion_database_status, current_champion, migrate_m04
from .config import MILESTONE, PROJECT_NAME, PROJECT_PHASE
from .db import ensure_baseline_registered, initialize_database, read_baseline
from .mt5 import detect_mt5
from .workflow_store import migrate_current
from .optimizer_api import router as optimizer_router
from .optimizer_jobs import reconcile_optimizer_startup
from .optimizer_store import latest_job
from .promotion_service import recover_incomplete_promotions
from .scientist_api import router as scientist_router
from .scientist_store import (
    migrate_m05,
    recover_unconfirmed_requests,
    scientist_database_status,
)
from .strategy_reset import (
    RECOVERY_RESET_CONFIRMATION,
    StrategyResetError,
    backup_and_reset_corrupt_database,
    database_recovery_status,
)

logger = logging.getLogger(__name__)
RECOVERY_REQUIRED = False
RECOVERY_REASON: str | None = None


class CorruptStateResetConfirmation(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global RECOVERY_REQUIRED, RECOVERY_REASON
    RECOVERY_REQUIRED = False
    RECOVERY_REASON = None
    try:
        initialize_database()
        ensure_baseline_registered()
        migrate_m04()
        recover_incomplete_promotions()
        migrate_m05()
        recover_unconfirmed_requests()
        migrate_m06()
        recover_incomplete_backtests()
        migrate_current()
        reconcile_optimizer_startup()
    except Exception:
        recovery = database_recovery_status()
        if recovery["status"] != "RECOVERY_REQUIRED":
            raise
        RECOVERY_REQUIRED = True
        RECOVERY_REASON = str(recovery["reason"])
        logger.exception("MAX startup entered explicit Recovery Required mode")
    yield


app = FastAPI(title=PROJECT_NAME, version="0.1.0", lifespan=lifespan)
app.include_router(artifact_router)
app.include_router(optimizer_router)
app.include_router(challenger_router)
app.include_router(champion_router)
app.include_router(scientist_router)


@app.middleware("http")
async def recovery_required_gate(request, call_next):
    if RECOVERY_REQUIRED and request.url.path not in {
        "/api/health",
        "/api/recovery/status",
        "/api/recovery/reset",
    }:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "RECOVERY_REQUIRED: application operations are disabled",
                "reason": RECOVERY_REASON,
            },
        )
    return await call_next(request)


@app.get("/api/recovery/status")
def recovery_status() -> dict:
    status = database_recovery_status()
    return {
        "status": "RECOVERY_REQUIRED" if RECOVERY_REQUIRED else status["status"],
        "reason": RECOVERY_REASON if RECOVERY_REQUIRED else status["reason"],
        "reset_confirmation": RECOVERY_RESET_CONFIRMATION,
        "data_loss_boundary": [
            "Corrupt operational database is preserved in local quarantine",
            "A new current-schema database is created with the accepted EA baseline",
            "Generated Strategy and Research operational state is not restored",
            "External Scientist provider settings are left untouched",
        ],
    }


@app.post("/api/recovery/reset")
def reset_corrupt_state(payload: CorruptStateResetConfirmation) -> dict:
    global RECOVERY_REQUIRED, RECOVERY_REASON
    if not RECOVERY_REQUIRED:
        raise HTTPException(status_code=409, detail="RECOVERY_REQUIRED_MODE_NOT_ACTIVE")
    try:
        result = backup_and_reset_corrupt_database(
            confirmed=payload.confirmation
        )
    except (StrategyResetError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    RECOVERY_REQUIRED = False
    RECOVERY_REASON = None
    return result


def _foundation_state() -> dict:
    database = challenger_operations_database_status()
    baseline = read_baseline() if database["status"] == "READY" else None
    champion = current_champion() if database["status"] == "READY" else None
    current_optimizer = latest_job() if database["status"] == "READY" else None
    optimizer_summary = None
    if current_optimizer is not None:
        optimizer_summary = {
            "job_id": current_optimizer["job_id"],
            "status": current_optimizer["status"],
            "active": current_optimizer["active"],
            "current_round": current_optimizer["current_round"],
            "max_rounds": current_optimizer["max_rounds"],
            "terminal_result": current_optimizer["terminal_result"],
            "first_blocker": current_optimizer["first_blocker"],
        }
    return {
        "backend": {"status": "READY"},
        "database": database,
        "ea_baseline": baseline,
        "current_strategy_champion": (
            {"strategy_id": champion["strategy_id"], "status": champion["status"]}
            if champion
            else None
        ),
        "optimizer_job": optimizer_summary,
        "mt5": detect_mt5(),
    }


@app.get("/api/health")
def health() -> dict:
    if RECOVERY_REQUIRED:
        return {
            "status": "RECOVERY_REQUIRED",
            "project": PROJECT_NAME,
            "phase": PROJECT_PHASE,
            "milestone": MILESTONE,
            "database": {"status": "RECOVERY_REQUIRED", "reason": RECOVERY_REASON},
        }
    state = _foundation_state()
    return {
        "status": state["backend"]["status"],
        "project": PROJECT_NAME,
        "phase": PROJECT_PHASE,
        "milestone": MILESTONE,
        "database": state["database"],
    }


@app.get("/api/ea/baseline")
def ea_baseline() -> dict:
    return read_baseline()


@app.get("/api/mt5/preflight")
def mt5_preflight() -> dict:
    return detect_mt5()


@app.get("/api/overview")
def overview() -> dict:
    state = _foundation_state()
    return {
        "project": PROJECT_NAME,
        "phase": PROJECT_PHASE,
        "milestone": MILESTONE,
        **state,
    }
