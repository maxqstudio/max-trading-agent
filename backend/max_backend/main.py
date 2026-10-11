from contextlib import asynccontextmanager
import logging
import sqlite3

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
from .challenger_operations import recover_retired_challenger_deployments
from .champion_api import router as champion_router
from .champion_store import champion_database_status, current_champion, migrate_m04
from .config import DATABASE_PATH, MILESTONE, PROJECT_NAME, PROJECT_PHASE
from .db import (
    DatabaseSchemaGuardError,
    assert_database_schema_compatible,
    ensure_baseline_registered,
    initialize_database,
    is_schema16_archive_compatible,
    open_read_only,
    read_baseline,
    verify_schema16_database_integrity,
)
from .mt5 import detect_mt5
from .workflow_store import migrate_current
from .optimizer_api import router as optimizer_router
from .optimizer_jobs import reconcile_optimizer_startup
from .optimizer_store import ACTIVE_STATES as OPTIMIZER_ACTIVE_STATES
from .optimizer_store import latest_job
from .promotion_service import recover_incomplete_promotions, verify_current_strategy_champion
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
STARTUP_SCHEMA_BLOCKER: dict | None = None


class CorruptStateResetConfirmation(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global RECOVERY_REQUIRED, RECOVERY_REASON, STARTUP_SCHEMA_BLOCKER
    RECOVERY_REQUIRED = False
    RECOVERY_REASON = None
    STARTUP_SCHEMA_BLOCKER = None
    try:
        _run_startup_migrations(DATABASE_PATH)
    except DatabaseSchemaGuardError as exc:
        STARTUP_SCHEMA_BLOCKER = exc.diagnostic()
        logger.error(
            "MAX startup blocked by database preflight (%s): %s",
            exc.status,
            exc.message,
        )
    except Exception:
        recovery = database_recovery_status(path=DATABASE_PATH)
        if recovery["status"] != "RECOVERY_REQUIRED":
            raise
        RECOVERY_REQUIRED = True
        RECOVERY_REASON = str(recovery["reason"])
        logger.exception("MAX startup entered explicit Recovery Required mode")
    yield


def _run_startup_migrations(path) -> None:
    """Guard the existing database before any initialization or recovery write."""
    schema = assert_database_schema_compatible(path, allow_missing=True)
    if is_schema16_archive_compatible(schema):
        verify_schema16_database_integrity(path)
        ensure_baseline_registered(path)
        _validate_schema16_operational_state(path, schema)
        return
    initialize_database(path)
    ensure_baseline_registered(path)
    migrate_m04(path)
    recover_incomplete_promotions(path=path)
    migrate_m05(path)
    recover_unconfirmed_requests(path=path)
    migrate_m06(path)
    recover_incomplete_backtests(path)
    recover_retired_challenger_deployments(path=path)
    migrate_current(path)
    reconcile_optimizer_startup(path=path)


def _validate_schema16_operational_state(path, schema: dict) -> None:
    """Read-only startup gate: never replay schema-16 recovery side effects."""
    conn = open_read_only(path)
    try:
        pending: dict[str, int] = {}
        optimizer_rows = conn.execute(
            "SELECT status,active FROM optimizer_jobs"
        ).fetchall()
        optimizer_pending = sum(
            1
            for row in optimizer_rows
            if bool(row["active"]) or str(row["status"]) in OPTIMIZER_ACTIVE_STATES
        )
        if optimizer_pending:
            pending["optimizer"] = optimizer_pending
        checks = (
            (
                "promotion",
                "SELECT COUNT(*) FROM strategy_promotions "
                "WHERE state IN ('PREPARED','ARTIFACTS_STAGED','FILES_COMMITTED')",
            ),
            (
                "backtest",
                "SELECT COUNT(*) FROM strategy_challenger_backtests "
                "WHERE state IN ('PREPARED','RUNNING')",
            ),
            (
                "scientist_request",
                "SELECT COUNT(*) FROM scientist_chat_requests "
                "WHERE state='CALL_IN_FLIGHT'",
            ),
            (
                "challenger_batch",
                "SELECT COUNT(*) FROM strategy_challenger_batches "
                "WHERE state IN ('PREPARED','VALIDATED','STAGED','RECOVERY_REQUIRED')",
            ),
            (
                "challenger_retirement",
                "SELECT COUNT(*) FROM strategy_challenger_retirements "
                "WHERE state='PREPARED'",
            ),
            (
                "challenger_registration",
                "SELECT COUNT(*) FROM strategy_challengers WHERE status='REGISTERING'",
            ),
        )
        for name, query in checks:
            count = int(conn.execute(query).fetchone()[0])
            if count:
                pending[name] = count
    except sqlite3.Error as exc:
        raise DatabaseSchemaGuardError(
            "SCHEMA16_OPERATIONAL_STATE_UNVERIFIABLE",
            "Schema-16 operational invariants could not be checked read-only; startup is blocked without recovery or mutation.",
            database_schema_version=16,
            compatibility_profile=schema["compatibility_profile"],
        ) from exc
    finally:
        conn.close()

    try:
        champion = verify_current_strategy_champion(path=path)
    except Exception:
        # The failure may concern a persisted path, artifact, or current
        # Champion projection. Keep its details out of the public startup
        # diagnostic and preserve the database for explicit reconciliation.
        pending["champion_integrity_unverifiable"] = 1
    else:
        if champion.get("status") not in {"VERIFIED", "NONE"}:
            pending["champion_integrity"] = 1
    if pending:
        categories = ", ".join(
            f"{name}={count}" for name, count in sorted(pending.items())
        )
        raise DatabaseSchemaGuardError(
            "SCHEMA16_OPERATIONAL_RECONCILIATION_REQUIRED",
            "Schema 16 is structurally compatible, but persisted operational authority needs explicit reconciliation "
            f"({categories}). No startup recovery, migration, Champion change, file cleanup, or database mutation was performed.",
            database_schema_version=16,
            compatibility_profile=schema["compatibility_profile"],
        )


app = FastAPI(title=PROJECT_NAME, version="0.1.0", lifespan=lifespan)
app.include_router(artifact_router)
app.include_router(optimizer_router)
app.include_router(challenger_router)
app.include_router(champion_router)
app.include_router(scientist_router)


@app.middleware("http")
async def recovery_required_gate(request, call_next):
    if STARTUP_SCHEMA_BLOCKER is not None:
        if request.url.path in {"/api/health", "/api/recovery/status"}:
            return await call_next(request)
        return JSONResponse(status_code=503, content=STARTUP_SCHEMA_BLOCKER)
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
    if STARTUP_SCHEMA_BLOCKER is not None:
        return dict(STARTUP_SCHEMA_BLOCKER)
    status = database_recovery_status(path=DATABASE_PATH)
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
    if STARTUP_SCHEMA_BLOCKER is not None:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_SCHEMA_GUARD_ACTIVE: reset and recovery are disabled",
        )
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
    if STARTUP_SCHEMA_BLOCKER is not None:
        return JSONResponse(
            status_code=503,
            content={
                "status": STARTUP_SCHEMA_BLOCKER["status"],
                "project": PROJECT_NAME,
                "phase": PROJECT_PHASE,
                "milestone": MILESTONE,
                "database": STARTUP_SCHEMA_BLOCKER,
            },
        )
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
