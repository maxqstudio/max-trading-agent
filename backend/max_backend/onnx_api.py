"""Read-only ONNX workspace contract for the ONNX-01 shell.

This module intentionally has no database, filesystem, MT5, model, or training
dependencies. Its response describes the capability boundary of this phase; it
is not persisted operational cycle state or scientific evidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class WorkspaceStatus(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_STARTED = "NOT_STARTED"
    NOT_PROVEN = "NOT_PROVEN"
    UNAVAILABLE = "UNAVAILABLE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


PageId = Literal[
    "data_intake",
    "discovery",
    "cpcv",
    "tournament",
    "monte_carlo",
    "challenger",
    "champion",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StateSection(ContractModel):
    status: WorkspaceStatus
    availability: WorkspaceStatus
    reason: str


class ValueSection(StateSection):
    value: str | int | float | None


class OperationalState(StateSection):
    status: Literal[WorkspaceStatus.NOT_STARTED]
    availability: Literal[WorkspaceStatus.NOT_IMPLEMENTED]
    persisted: Literal[False]
    cycle_id: None


class DatasetState(StateSection):
    dataset_id: None
    snapshot_id: None
    symbol: None
    timeframe: None


class ResearchWindowsState(StateSection):
    items: None


class HardwareCapacityState(StateSection):
    gpu_vram_bytes: None
    system_ram_bytes: None


class ProgressState(StateSection):
    completed: None
    total: None


class QualifiedPoolState(StateSection):
    candidates: None


class DiscoveryState(StateSection):
    budget: ValueSection
    experiment_progress: ProgressState
    qualified_pool: QualifiedPoolState


class StagePageState(StateSection):
    page_id: PageId
    prerequisites: list[str]


class ForwardState(StateSection):
    prerequisites: list[str]


class ChallengerCandidatesState(StateSection):
    items: None


class ChallengerState(ContractModel):
    candidates: ChallengerCandidatesState
    forward: ForwardState


class ChampionState(StateSection):
    identity: None


class CheckpointState(StateSection):
    identity: None


class FirstBlocker(StateSection):
    status: Literal[WorkspaceStatus.NOT_IMPLEMENTED]
    availability: Literal[WorkspaceStatus.NOT_IMPLEMENTED]
    code: str
    message: str


class RecoveryState(StateSection):
    status: Literal[WorkspaceStatus.UNAVAILABLE]
    availability: Literal[WorkspaceStatus.UNAVAILABLE]
    available: Literal[False]


class OnnxWorkspaceSnapshot(ContractModel):
    contract_version: Literal["1.0"]
    source: Literal["BACKEND_ONNX_01_SKELETON"]
    operational_state: OperationalState
    dataset: DatasetState
    research_windows: ResearchWindowsState
    scientific_authority: StateSection
    hardware_capacity: HardwareCapacityState
    discovery: DiscoveryState
    stage_pages: list[StagePageState]
    challenger: ChallengerState
    champion: ChampionState
    current_stage: ValueSection
    checkpoint: CheckpointState
    first_blocker: FirstBlocker
    recovery: RecoveryState


_NO_CYCLE_REASON = (
    "ONNX-01 has no operational cycle store; the planning state machine is "
    "not persisted operational state."
)
_NO_RUNTIME_REASON = (
    "Scientific execution and its operational state are not implemented in ONNX-01."
)


def _stage_page(page_id: PageId, prerequisites: list[str]) -> StagePageState:
    return StagePageState(
        page_id=page_id,
        status=WorkspaceStatus.NOT_STARTED,
        availability=WorkspaceStatus.NOT_IMPLEMENTED,
        reason=_NO_RUNTIME_REASON,
        prerequisites=prerequisites,
    )


def build_onnx_workspace_snapshot() -> OnnxWorkspaceSnapshot:
    """Return the fixed ONNX-01 capability snapshot without operational reads."""
    data_intake = _stage_page(
        "data_intake",
        ["Separate ONNX-02 authorization and accepted data-source authority."],
    )
    discovery = _stage_page(
        "discovery",
        ["Verified DATA_READY snapshot, frozen windows, and a complete Owner-authorized KPI contract."],
    )
    cpcv = _stage_page("cpcv", ["Sealed WFA-qualified pool from Discovery."])
    tournament = _stage_page("tournament", ["Terminal sealed CPCV evidence."])
    monte_carlo = _stage_page(
        "monte_carlo",
        ["CPCV survivors and terminal Tournament evidence under the frozen KPI contract."],
    )
    challenger = _stage_page(
        "challenger",
        ["Monte Carlo survivors, untouched Forward PASS, final-fit/export/parity/manifest gates."],
    )
    champion = _stage_page(
        "champion",
        ["Verified CHALLENGER_READY identity and explicit Owner promotion."],
    )

    return OnnxWorkspaceSnapshot(
        contract_version="1.0",
        source="BACKEND_ONNX_01_SKELETON",
        operational_state=OperationalState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            persisted=False,
            cycle_id=None,
            reason=_NO_CYCLE_REASON,
        ),
        dataset=DatasetState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.UNAVAILABLE,
            dataset_id=None,
            snapshot_id=None,
            symbol=None,
            timeframe=None,
            reason="Dataset intake and immutable snapshots are not implemented in ONNX-01.",
        ),
        research_windows=ResearchWindowsState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.UNAVAILABLE,
            items=None,
            reason="No persisted cycle exists from which to read frozen research windows.",
        ),
        scientific_authority=StateSection(
            status=WorkspaceStatus.NOT_PROVEN,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            reason="ONNX-00 is frozen planning authority only; no scientific execution evidence exists.",
        ),
        hardware_capacity=HardwareCapacityState(
            status=WorkspaceStatus.NOT_PROVEN,
            availability=WorkspaceStatus.UNAVAILABLE,
            gpu_vram_bytes=None,
            system_ram_bytes=None,
            reason="ONNX-01 does not inspect or claim hardware capacity.",
        ),
        discovery=DiscoveryState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            reason="Discovery execution and its experiment ledger are not implemented.",
            budget=ValueSection(
                status=WorkspaceStatus.NOT_STARTED,
                availability=WorkspaceStatus.UNAVAILABLE,
                value=None,
                reason="No frozen operational cycle budget exists.",
            ),
            experiment_progress=ProgressState(
                status=WorkspaceStatus.NOT_STARTED,
                availability=WorkspaceStatus.UNAVAILABLE,
                completed=None,
                total=None,
                reason="No operational experiment ledger exists.",
            ),
            qualified_pool=QualifiedPoolState(
                status=WorkspaceStatus.NOT_STARTED,
                availability=WorkspaceStatus.NOT_IMPLEMENTED,
                candidates=None,
                reason="Qualified Pool admission is not implemented; Cheap Screen has no qualification authority.",
            ),
        ),
        stage_pages=[
            data_intake,
            discovery,
            cpcv,
            tournament,
            monte_carlo,
            challenger,
            champion,
        ],
        challenger=ChallengerState(
            candidates=ChallengerCandidatesState(
                status=WorkspaceStatus.NOT_STARTED,
                availability=WorkspaceStatus.UNAVAILABLE,
                items=None,
                reason="No persisted ONNX Challenger registry exists.",
            ),
            forward=ForwardState(
                status=WorkspaceStatus.NOT_STARTED,
                availability=WorkspaceStatus.NOT_IMPLEMENTED,
                reason=_NO_RUNTIME_REASON,
                prerequisites=["Monte Carlo survivor and frozen Forward window."],
            ),
        ),
        champion=ChampionState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            identity=None,
            reason="ONNX Champion publication is not implemented; promotion remains explicit Owner-only authority.",
        ),
        current_stage=ValueSection(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.UNAVAILABLE,
            value=None,
            reason="No operational cycle stage can be read.",
        ),
        checkpoint=CheckpointState(
            status=WorkspaceStatus.NOT_STARTED,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            identity=None,
            reason="ONNX checkpoint persistence is not implemented.",
        ),
        first_blocker=FirstBlocker(
            status=WorkspaceStatus.NOT_IMPLEMENTED,
            availability=WorkspaceStatus.NOT_IMPLEMENTED,
            code="ONNX_EXECUTION_NOT_IMPLEMENTED",
            message=(
                "ONNX-01 is a read-only workspace shell; scientific execution is "
                "not implemented or authorized by this phase."
            ),
            reason="The current first blocker is the explicit ONNX-01 capability boundary.",
        ),
        recovery=RecoveryState(
            status=WorkspaceStatus.UNAVAILABLE,
            availability=WorkspaceStatus.UNAVAILABLE,
            available=False,
            reason="No ONNX operational state or candidate recovery cursor exists.",
        ),
    )


router = APIRouter(prefix="/api/v1/onnx", tags=["ONNX workspace v1"])


@router.get("/workspace", response_model=OnnxWorkspaceSnapshot)
def get_onnx_workspace() -> OnnxWorkspaceSnapshot:
    return build_onnx_workspace_snapshot()
