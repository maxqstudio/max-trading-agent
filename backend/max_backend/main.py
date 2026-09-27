from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from .optimizer_store import latest_job
from .promotion_service import recover_incomplete_promotions
from .research_api import router as research_router
from .research_store import recover_incomplete_research, research_database_status, latest_research
from .scientist_api import router as scientist_router
from .scientist_store import (
    migrate_m05,
    recover_unconfirmed_requests,
    scientist_database_status,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    ensure_baseline_registered()
    migrate_m04()
    recover_incomplete_promotions()
    migrate_m05()
    recover_unconfirmed_requests()
    migrate_m06()
    recover_incomplete_backtests()
    migrate_current()
    recover_incomplete_research()
    yield


app = FastAPI(title=PROJECT_NAME, version="0.1.0", lifespan=lifespan)
app.include_router(artifact_router)
app.include_router(optimizer_router)
app.include_router(challenger_router)
app.include_router(champion_router)
app.include_router(scientist_router)
app.include_router(research_router)


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
    research_status = research_database_status() if database["status"] == "READY" else {
        "status": "FAIL",
        "reason": "DATABASE_NOT_READY",
    }
    current_research = latest_research() if research_status["status"] == "READY" else None
    research_summary = None
    if current_research is not None:
        research_summary = {
            "research_id": current_research["research_id"],
            "parent_strategy_id": current_research["parent_strategy_id"],
            "current_gate": current_research["current_gate"],
            "gate_state": current_research["gate_state"],
            "training_count": current_research["training_count"],
            "onnx_count": current_research["onnx_count"],
            "research_challenger_count": current_research["research_challenger_count"],
            "champion_mutation": current_research["champion_mutation"],
        }
    return {
        "backend": {"status": "READY"},
        "database": database,
        "research_database": research_status,
        "research": research_summary,
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
