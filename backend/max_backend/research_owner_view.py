from __future__ import annotations

from typing import Any

_STATE_LABELS = {
    "NOT_STARTED": "Not started",
    "READY_TO_START": "Ready to initialize",
    "READY_TO_CONFIGURE": "Ready to configure",
    "BLOCKED": "Blocked",
    "NOT_INITIALIZED": "Not initialized",
    "STARTING": "Preparing",
    "VALIDATING": "Validating",
    "PASS_WAITING_OWNER": "Ready for Owner review",
    "FAIL_WAITING_OWNER": "Review required",
    "ERROR_WAITING_OWNER": "Attention required",
    "PASS": "Passed",
    "FAIL": "Review required",
    "READY": "Ready",
    "VERIFIED": "Verified",
    "SEALED": "Sealed",
    "PROTECTED": "Protected",
    "NOT_RUN": "Not started",
    "NOT_READY": "Not ready",
    "CP32_PARITY_REQUIRED": "Feature parity validation required",
    "PARENT_FIRST_BARRIER_CONTRACT": "Parent-bound label validation required",
    "OWNER_PARTITION_BOUNDARIES_REQUIRED": "Research and protected windows require Owner input",
    "BOUND_IN_R01_AUTHORIZATION": "Protected windows frozen",
}

_DECISION_LABELS = {
    "RESEARCH_H1_MINIMUM_SAMPLE_POLICY": "Research sample requirement",
    "RESEARCH_KPI_NUMERIC_THRESHOLDS": "Research KPI thresholds",
    "LABEL_CONTRACT": "Research label definition",
}

_DECISION_STATUS_LABELS = {
    "OWNER_DECISION_REQUIRED": "Owner decision required",
    "GATE_SPECIFIC_OWNER_FREEZE_REQUIRED": "Owner approval required before qualification",
    "R01_REQUIRED": "Defined during Data validation",
    "FROZEN": "Frozen Research authority",
}


def owner_state_label(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return "Not available"
    if token in _STATE_LABELS:
        return _STATE_LABELS[token]
    return "Review required"


def owner_decisions(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in items or []:
        key = str(item.get("item") or "")
        status = str(item.get("status") or "")
        result.append(
            {
                "label": _DECISION_LABELS.get(key, "Research decision"),
                "status": _DECISION_STATUS_LABELS.get(
                    status,
                    owner_state_label(status),
                ),
            }
        )
    return result


def _lifecycle_views(
    *,
    research_initialized: bool,
    data_state: str | None,
) -> list[dict[str, str]]:
    if not research_initialized:
        data_availability = "Waiting for Research setup"
        discovery_availability = "Waiting for validated Data"
    else:
        data_availability = "Available"
        discovery_availability = (
            "Waiting for Owner authorization"
            if data_state == "PASS_WAITING_OWNER"
            else "Waiting for validated Data"
        )

    return [
        {
            "key": "overview",
            "label": "Overview",
            "availability": "Available",
            "description": "Research identity, frozen parent, lifecycle state and next legal action.",
            "scientific_rule": "Research controls lifecycle authority; detailed Data evidence stays in Data.",
            "required_authority": "Current Research authority",
        },
        {
            "key": "data",
            "label": "Data",
            "availability": data_availability,
            "description": "Source, physical integrity, labels, dependency protection and research windows.",
            "scientific_rule": "Data is first-class scientific authority and remains separate from lifecycle control.",
            "required_authority": "Initialized and frozen Research authority",
        },
        {
            "key": "model-discovery",
            "label": "Model Discovery",
            "availability": discovery_availability,
            "description": "Proposal, search and cheap screening allocate compute.",
            "scientific_rule": "Cheap Screen does not qualify candidates for the Qualified Pool.",
            "required_authority": "Accepted Data validation plus explicit Owner authorization",
        },
        {
            "key": "wfa-pool",
            "label": "WFA / Qualified Pool",
            "availability": "Waiting for qualified Discovery candidates",
            "description": "Full Walk-Forward qualification establishes Qualified Pool admission.",
            "scientific_rule": "Only Full Walk-Forward evidence qualifies Pool admission.",
            "required_authority": "Authorized Discovery completion",
        },
        {
            "key": "cpcv",
            "label": "CPCV",
            "availability": "Waiting for qualified Pool and Owner selection",
            "description": "Owner-selected qualified candidates enter CPCV validation.",
            "scientific_rule": "CPCV is separate from Discovery and cannot be bypassed by earlier performance.",
            "required_authority": "Qualified Pool plus explicit Owner selection",
        },
        {
            "key": "tournament",
            "label": "Tournament",
            "availability": "Waiting for CPCV qualification",
            "description": "Deterministic comparison receives CPCV-qualified candidates only.",
            "scientific_rule": "Tournament cannot rescue a failed CPCV candidate.",
            "required_authority": "Accepted CPCV evidence",
        },
        {
            "key": "monte-carlo",
            "label": "Monte Carlo",
            "availability": "Waiting for Tournament survivors",
            "description": "Robustness simulation receives deterministic Tournament survivors.",
            "scientific_rule": "Candidate identity remains immutable into robustness testing.",
            "required_authority": "Accepted Tournament survivors",
        },
        {
            "key": "locked-oos",
            "label": "Locked OOS",
            "availability": "Waiting for robustness qualification",
            "description": "Protected out-of-sample evaluation remains sealed until authorized.",
            "scientific_rule": "Protected outcomes cannot become tuning, proposal or Scientist adaptive feedback.",
            "required_authority": "Accepted robustness evidence",
        },
        {
            "key": "fresh-forward",
            "label": "Fresh / Forward",
            "availability": "Waiting for Locked OOS acceptance",
            "description": "Fresh protected evaluation remains distinct from Locked OOS.",
            "scientific_rule": "Locked OOS and Fresh / Forward are separate protected authorities.",
            "required_authority": "Accepted Locked OOS evidence",
        },
        {
            "key": "final-fit",
            "label": "Final Fit / ONNX",
            "availability": "Waiting for protected validation",
            "description": "Final fit and production model export occur after protected validation.",
            "scientific_rule": "Protected validation cannot be recycled into earlier search or tuning.",
            "required_authority": "Accepted Fresh / Forward evidence",
        },
        {
            "key": "research-challenger",
            "label": "Research Challenger",
            "availability": "Waiting for eligible final model package",
            "description": "Eligible final model packages may become Research Challengers under immutable lineage.",
            "scientific_rule": "Research cannot self-promote; promotion remains explicit Owner governance.",
            "required_authority": "Accepted final fit and model export authority",
        },
    ]


def research_owner_view(
    *,
    research_initialized: bool,
    foundation_state: str | None,
    data_state: str | None,
    sample_requirement: int | None,
    execution_sample_requirement: int | None,
    execution_sample_source: str,
    unresolved_authority: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if not research_initialized:
        current_stage = "Research setup"
        current_state = owner_state_label(foundation_state or "NOT_STARTED")
        next_step = (
            "Establish a verified Strategy Champion before initializing Research."
            if foundation_state == "BLOCKED"
            else "Configure the Research sample requirement and initialize Research."
        )
        sample_source = (
            "Current Research configuration"
            if sample_requirement is not None
            else "Owner configuration required"
        )
    elif data_state is None:
        current_stage = "Data preparation"
        current_state = owner_state_label(foundation_state)
        next_step = "Verify the Research data source and define research and protected windows."
        sample_source = (
            "Current Research configuration"
            if sample_requirement is not None
            else "Owner configuration required"
        )
    else:
        current_stage = "Data validation"
        current_state = owner_state_label(data_state)
        sample_source = (
            "Current Research configuration"
            if sample_requirement is not None
            else "Owner configuration required"
        )
        if data_state == "PASS_WAITING_OWNER":
            next_step = "Review Data validation evidence and decide whether to authorize Model Discovery."
        elif data_state in {"FAIL_WAITING_OWNER", "ERROR_WAITING_OWNER"}:
            next_step = "Review Data validation evidence before any later Research action."
        else:
            next_step = "Complete deterministic Data validation."

    return {
        "current_stage": current_stage,
        "current_state": current_state,
        "next_step": next_step,
        "sample_requirement": {
            "value": sample_requirement,
            "source": sample_source,
        },
        "execution_sample_requirement": {
            "value": execution_sample_requirement,
            "source": execution_sample_source,
        },
        "capacity_authority": (
            "Bound by supported execution scope, available hardware, and scientific evidence."
        ),
        "owner_decisions": owner_decisions(unresolved_authority),
        "lifecycle": _lifecycle_views(
            research_initialized=research_initialized,
            data_state=data_state,
        ),
    }


def data_owner_view(
    *,
    state: str,
    source_status: str,
    dataset_status: str,
    feature_readiness: str,
    label_readiness: str,
    leakage_status: str,
    data_quality_status: str,
    protected_data_state: str,
    integrity_status: str,
    sample_requirement: int | None,
    execution_sample_requirement: int | None,
    execution_sample_source: str,
    physical_integrity: dict[str, Any] | None,
) -> dict[str, Any]:
    if state == "READY_TO_CONFIGURE":
        if source_status == "VERIFIED":
            next_step = "Define research and protected windows, then prepare Data validation."
        else:
            next_step = "Verify the broker-backed Research data source."
    elif state == "PASS_WAITING_OWNER":
        next_step = "Review Data validation evidence before authorizing the next Research stage."
    elif state in {"FAIL_WAITING_OWNER", "ERROR_WAITING_OWNER"}:
        next_step = "Review Data validation evidence and repair the reported issue."
    else:
        next_step = "Complete deterministic Data validation."

    if sample_requirement is None:
        next_step = (
            "Configure the current Research sample requirement before "
            "starting the next Data validation execution."
        )

    return {
        "state": owner_state_label(state),
        "next_step": next_step,
        "sample_requirement": {
            "value": sample_requirement,
            "source": (
                "Current Research configuration"
                if sample_requirement is not None
                else "Owner configuration required"
            ),
        },
        "execution_sample_requirement": {
            "value": execution_sample_requirement,
            "source": execution_sample_source,
        },
        "source_status": owner_state_label(source_status),
        "dataset_status": owner_state_label(dataset_status),
        "feature_readiness": owner_state_label(feature_readiness),
        "label_readiness": owner_state_label(label_readiness),
        "leakage_status": owner_state_label(leakage_status),
        "data_quality_status": owner_state_label(data_quality_status),
        "protected_data_state": owner_state_label(protected_data_state),
        "integrity_status": owner_state_label(integrity_status),
        "physical_integrity_state": (
            "Validated physical-integrity evidence available"
            if physical_integrity is not None
            else "Physical-integrity evidence is not available until Data validation completes"
        ),
        "physical_integrity": (
            {
                "chronology": owner_state_label(
                    physical_integrity.get("chronology_status")
                ),
                "duplicates": (
                    "No duplicate timestamps found"
                    if int(physical_integrity.get("duplicate_timestamps") or 0) == 0
                    else (
                        str(int(physical_integrity.get("duplicate_timestamps") or 0))
                        + " duplicate timestamps found"
                    )
                ),
                "missing_source_data": (
                    "No missing source rows found"
                    if int(physical_integrity.get("missing_source_data") or 0) == 0
                    else (
                        str(int(physical_integrity.get("missing_source_data") or 0))
                        + " missing source rows found"
                    )
                ),
                "multi_timeframe_alignment": owner_state_label(
                    physical_integrity.get("mtf_alignment_status")
                ),
                "relative_symbol_alignment": owner_state_label(
                    physical_integrity.get("relative_symbol_alignment_status")
                ),
                "feature_completeness": owner_state_label(
                    physical_integrity.get("cp32_completeness")
                ),
            }
            if physical_integrity is not None
            else None
        ),
        "row_identity": "Original physical source rows",
        "boundary_safety": "A target must end before the next protected window to be eligible.",
        "protected_outcomes": "Locked OOS and Fresh / Forward outcomes remain protected until their authorized validation stages.",
    }
