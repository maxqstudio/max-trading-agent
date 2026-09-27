from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .optimizer_core import OptimizationPass, deterministic_refine
from .optimizer_runtime import round_evidence_dir, write_json
from .optimizer_scientist import (
    ScientistAdvisoryError,
    build_bounded_payload,
    parse_proposal,
    propose_optimizer_ranges,
    sanitized_request_evidence,
    scientist_route_status,
    sha256_json,
    utc_now as scientist_utc_now,
    validate_proposal,
)
from .optimizer_store import get_round, update_job, upsert_round
from .scientist_provider import credential_for_route, fallback_error_category





def _autonomous_provider_call(
    route: dict[str, Any],
    payload: dict[str, Any],
):
    primary = dict(route)
    fallback_models = [
        str(model).strip()
        for model in (primary.get("fallback_models") or [])
        if str(model).strip()
        and str(model).strip() != str(primary.get("model") or "")
    ]
    routes = [{**primary, "fallback_models": []}]
    routes.extend(
        {**primary, "model": model, "fallback_models": []}
        for model in fallback_models
    )
    attempts: list[dict[str, Any]] = []
    confirmed_calls = 0
    for index, candidate in enumerate(routes):
        try:
            result = propose_optimizer_ranges(candidate, payload)
            confirmed_calls += 1
            provenance = dict(result.provenance)
            attempts.append(
                {
                    "model": candidate.get("model"),
                    "status": "PASS",
                }
            )
            provenance["route_attempts"] = attempts
            provenance["confirmed_provider_calls"] = confirmed_calls
            provenance["provider_route_fallbacks"] = index
            provenance["fallback_route_used"] = index > 0
            return type(result)(text=result.text, provenance=provenance)
        except ScientistAdvisoryError as exc:
            if exc.actual_llm_call:
                confirmed_calls += 1
            attempts.append(
                {
                    "model": candidate.get("model"),
                    "status": "FAIL",
                    "error_category": exc.category,
                }
            )
            may_fallback = (
                fallback_error_category(exc) is not None
                and index + 1 < len(routes)
            )
            if may_fallback:
                continue
            provenance = dict(exc.provenance)
            provenance["route_attempts"] = attempts
            provenance["confirmed_provider_calls"] = confirmed_calls
            provenance["provider_route_fallbacks"] = index
            provenance["fallback_route_used"] = index > 0
            raise ScientistAdvisoryError(
                exc.category,
                str(exc),
                actual_llm_call=confirmed_calls > 0,
                provenance=provenance,
            ) from exc


def _round_transition(job_id: str, round_no: int) -> dict[str, Any] | None:
    record = get_round(job_id, round_no)
    if record is None:
        return None
    state = record.get("state") if isinstance(record.get("state"), dict) else {}
    transition = state.get("scientist_transition")
    return dict(transition) if isinstance(transition, dict) else None


def _persist_scientist_transition(
    job_id: str,
    round_no: int,
    transition: dict[str, Any],
) -> dict[str, Any]:
    record = get_round(job_id, round_no)
    if record is None:
        raise RuntimeError("Scientist transition requires a parsed source round")
    state = dict(record.get("state") or {})
    state["scientist_transition"] = dict(transition)
    saved = upsert_round(
        job_id,
        round_no,
        phase=str(record["phase"]),
        state=state,
    )
    return dict(saved["state"]["scientist_transition"])


def _write_json_idempotent(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(f"Existing Scientist evidence mismatch: {path}")
        return
    write_json(path, payload)


def _write_text_idempotent(path: Path, text: str) -> str:
    encoded = str(text).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if path.exists():
        existing = path.read_bytes()
        if existing != encoded:
            raise RuntimeError(f"Existing Scientist raw response mismatch: {path}")
        return digest
    path.write_bytes(encoded)
    return digest


def _ensure_scientist_evidence(
    job_id: str,
    source_round: int,
    decision: dict[str, Any],
    *,
    request_evidence: dict[str, Any] | None = None,
    raw_response: str | None = None,
    normalized_response: dict[str, Any] | None = None,
) -> None:
    evidence = round_evidence_dir(job_id, source_round)
    if request_evidence is not None:
        _write_json_idempotent(
            evidence / "scientist_request.json",
            request_evidence,
        )
    if raw_response is not None:
        digest = _write_text_idempotent(
            evidence / "scientist_raw_response.txt",
            raw_response,
        )
        if decision.get("raw_response_sha256") not in (None, digest):
            raise RuntimeError("Scientist raw response SHA mismatch")
    if normalized_response is not None:
        _write_json_idempotent(
            evidence / "scientist_normalized_response.json",
            normalized_response,
        )
    _write_json_idempotent(
        evidence / "scientist_proposal.json",
        decision,
    )


def _fallback_decision(
    request: dict[str, Any],
    current_space: dict[str, Any],
    rows: list[OptimizationPass],
    *,
    job_id: str,
    source_round: int,
    target_round: int,
    category: str,
    reason: str,
    actual_llm_call: bool,
    proposal_attempts: int,
    actual_provider_calls: int,
    request_payload_sha256: str | None,
    provider_provenance: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
    rejected_proposal: bool = False,
    effective_source: str = "DETERMINISTIC_REFINEMENT_AFTER_PROVIDER_FAILURE",
    provider_call_state: str | None = None,
    confirmed_provider_calls: int | None = None,
    unconfirmed_provider_attempts: int = 0,
) -> tuple[dict[str, dict[str, float | int]], dict[str, Any]]:
    next_space = deterministic_refine(
        current_space,
        rows,
        request["optimize_params"],
    )
    confirmed_calls = (
        int(actual_provider_calls)
        if confirmed_provider_calls is None
        else int(confirmed_provider_calls)
    )
    call_state = provider_call_state or (
        "CONFIRMED" if confirmed_calls > 0 else "NOT_STARTED"
    )
    decision = {
        "schema": "MAX_REBUILD_SCIENTIST_DECISION_V1",
        "job_id": job_id,
        "transition_id": f"{job_id}:{source_round}:{target_round}",
        "source_round": source_round,
        "target_round": target_round,
        "mode": "SCIENTIST_PROPOSAL" if rejected_proposal else "DETERMINISTIC_FALLBACK",
        "actual_llm_call": bool(actual_llm_call and confirmed_calls > 0),
        "proposal_attempts": int(proposal_attempts),
        "actual_provider_calls": confirmed_calls,
        "confirmed_provider_calls": confirmed_calls,
        "unconfirmed_provider_attempts": int(unconfirmed_provider_attempts),
        "provider_call_state": call_state,
        "accepted_proposals": 0,
        "rejected_proposals": 1 if rejected_proposal else 0,
        "fallbacks": 1,
        "proposal": proposal,
        "reason": str(reason)[:1000],
        "accepted": False,
        "validation": {
            "status": "REJECTED" if rejected_proposal else "FALLBACK",
            "reason": str(category),
        },
        "error_category": str(category),
        "effective_range_source": effective_source,
        "effective_ranges": next_space,
        "request_payload_sha256": request_payload_sha256,
        "provider_provenance": dict(provider_provenance or {}),
        "created_utc": scientist_utc_now(),
    }
    return next_space, decision


def _refine_for_next_round(
    request: dict[str, Any],
    *,
    job_id: str,
    source_round: int,
    current_space: dict[str, Any],
    rows: list[OptimizationPass],
) -> tuple[dict[str, dict[str, float | int]], dict[str, Any]]:
    target_round = source_round + 1
    transition_id = f"{job_id}:{source_round}:{target_round}"
    existing = _round_transition(job_id, source_round)

    if existing is not None:
        if existing.get("transition_id") != transition_id:
            raise RuntimeError("Scientist transition identity mismatch")
        status = str(existing.get("status") or "")
        if status == "COMMITTED":
            decision = dict(existing["decision"])
            _ensure_scientist_evidence(job_id, source_round, decision)
            return decision["effective_ranges"], decision
        if status in {"CALL_RESERVED", "CALL_IN_FLIGHT"}:
            legacy_state = str(existing.get("provider_call_state") or "")
            if status == "CALL_RESERVED":
                call_state = "NOT_STARTED"
                confirmed_calls = 0
                unconfirmed_attempts = 0
            elif legacy_state == "CONFIRMED":
                call_state = "CONFIRMED"
                confirmed_calls = int(
                    existing.get("confirmed_provider_calls")
                    or existing.get("actual_provider_calls")
                    or 0
                )
                unconfirmed_attempts = int(
                    existing.get("unconfirmed_provider_attempts") or 0
                )
            else:
                # Legacy CALL_IN_FLIGHT recorded actual_provider_calls=1 before
                # provider execution was provable. Treat that window as unknown,
                # never retry it, and never manufacture a confirmed call.
                call_state = "UNCONFIRMED"
                confirmed_calls = 0
                unconfirmed_attempts = max(
                    1,
                    int(existing.get("unconfirmed_provider_attempts") or 0),
                )
            next_space, decision = _fallback_decision(
                request,
                current_space,
                rows,
                job_id=job_id,
                source_round=source_round,
                target_round=target_round,
                category="SCIENTIST_UNCONFIRMED_CALL_AFTER_RESTART",
                reason=(
                    "Scientist transition was interrupted before a committed advisory "
                    "decision; deterministic fallback is used and the provider is not called again."
                ),
                actual_llm_call=confirmed_calls > 0,
                proposal_attempts=int(existing.get("proposal_attempts") or 1),
                actual_provider_calls=confirmed_calls,
                confirmed_provider_calls=confirmed_calls,
                unconfirmed_provider_attempts=unconfirmed_attempts,
                provider_call_state=call_state,
                request_payload_sha256=existing.get("request_payload_sha256"),
                provider_provenance=dict(existing.get("provider_provenance") or {}),
                effective_source=(
                    "DETERMINISTIC_FALLBACK_AFTER_UNCONFIRMED_SCIENTIST_CALL"
                ),
            )
            committed = {
                **existing,
                "status": "COMMITTED",
                "provider_call_state": call_state,
                "confirmed_provider_calls": confirmed_calls,
                "unconfirmed_provider_attempts": unconfirmed_attempts,
                "actual_provider_calls": confirmed_calls,
                "decision": decision,
                "committed_utc": scientist_utc_now(),
            }
            _persist_scientist_transition(job_id, source_round, committed)
            _ensure_scientist_evidence(job_id, source_round, decision)
            return next_space, decision
        raise RuntimeError(f"Unknown Scientist transition status: {status}")

    scientist_enabled = bool(request.get("scientist_assist", False))
    if not scientist_enabled:
        next_space = deterministic_refine(
            current_space,
            rows,
            request["optimize_params"],
        )
        decision = {
            "schema": "MAX_REBUILD_SCIENTIST_DECISION_V1",
            "job_id": job_id,
            "transition_id": transition_id,
            "source_round": source_round,
            "target_round": target_round,
            "mode": "DETERMINISTIC_ONLY",
            "actual_llm_call": False,
            "proposal_attempts": 0,
            "actual_provider_calls": 0,
            "confirmed_provider_calls": 0,
            "unconfirmed_provider_attempts": 0,
            "provider_call_state": "NOT_STARTED",
            "accepted_proposals": 0,
            "rejected_proposals": 0,
            "fallbacks": 0,
            "proposal": None,
            "reason": (
                "Scientist advisory is OFF in the frozen optimizer request; "
                "accepted M01 deterministic refinement is used unchanged."
            ),
            "accepted": True,
            "validation": {
                "status": "NOT_APPLICABLE",
                "reason": "SCIENTIST_DISABLED",
            },
            "effective_range_source": "DETERMINISTIC_REFINEMENT_SCIENTIST_OFF",
            "effective_ranges": next_space,
            "request_payload_sha256": None,
            "provider_provenance": {},
            "created_utc": scientist_utc_now(),
        }
        _persist_scientist_transition(
            job_id,
            source_round,
            {
                "transition_id": transition_id,
                "status": "COMMITTED",
                "proposal_attempts": 0,
                "actual_provider_calls": 0,
                "confirmed_provider_calls": 0,
                "unconfirmed_provider_attempts": 0,
                "provider_call_state": "NOT_STARTED",
                "decision": decision,
                "committed_utc": scientist_utc_now(),
            },
        )
        _ensure_scientist_evidence(job_id, source_round, decision)
        return next_space, decision

    payload = build_bounded_payload(
        current_space,
        rows,
        request,
        source_round=source_round,
        target_round=target_round,
    )
    route = dict(request.get("scientist") or {})
    request_evidence = sanitized_request_evidence(route, payload)
    request_sha = str(request_evidence["request_payload_sha256"])
    reserved = {
        "transition_id": transition_id,
        "status": "CALL_RESERVED",
        "proposal_attempts": 1,
        "actual_provider_calls": 0,
        "confirmed_provider_calls": 0,
        "unconfirmed_provider_attempts": 0,
        "provider_call_state": "NOT_STARTED",
        "request_payload_sha256": request_sha,
        "provider_provenance": {},
        "created_utc": scientist_utc_now(),
    }
    _persist_scientist_transition(job_id, source_round, reserved)
    _write_json_idempotent(
        round_evidence_dir(job_id, source_round) / "scientist_request.json",
        request_evidence,
    )

    credential = credential_for_route(route)
    route_status = scientist_route_status(
        route,
        credential_override=credential,
    )
    if route_status["status"] != "READY":
        category = (
            "SCIENTIST_CREDENTIAL_MISSING"
            if route_status["status"] == "MISSING_CREDENTIAL"
            else "SCIENTIST_ROUTE_UNAVAILABLE"
        )
        source = (
            "DETERMINISTIC_REFINEMENT_AFTER_MISSING_CREDENTIAL"
            if category == "SCIENTIST_CREDENTIAL_MISSING"
            else "DETERMINISTIC_REFINEMENT_AFTER_ROUTE_UNAVAILABLE"
        )
        next_space, decision = _fallback_decision(
            request,
            current_space,
            rows,
            job_id=job_id,
            source_round=source_round,
            target_round=target_round,
            category=category,
            reason=(
                "Scientist advisory route is unavailable for this transition; "
                "deterministic refinement continues the optimizer."
            ),
            actual_llm_call=False,
            proposal_attempts=1,
            actual_provider_calls=0,
            request_payload_sha256=request_sha,
            provider_provenance={
                "configured_provider": route.get("provider"),
                "configured_model": route.get("model"),
                "base_url": route.get("base_url"),
                "phase": "STRATEGY_OPTIMIZER_RANGE_PROPOSAL",
                "temperature": 0.10,
                "status": route_status["status"],
            },
            effective_source=source,
        )
        _persist_scientist_transition(
            job_id,
            source_round,
            {
                **reserved,
                "status": "COMMITTED",
                "decision": decision,
                "committed_utc": scientist_utc_now(),
            },
        )
        _ensure_scientist_evidence(
            job_id,
            source_round,
            decision,
            request_evidence=request_evidence,
        )
        return next_space, decision

    in_flight = {
        **reserved,
        "status": "CALL_IN_FLIGHT",
        "actual_provider_calls": 0,
        "confirmed_provider_calls": 0,
        "unconfirmed_provider_attempts": 1,
        "provider_call_state": "UNCONFIRMED",
        "provider_provenance": {
            "configured_provider": route.get("provider"),
            "configured_model": route.get("model"),
            "base_url": route.get("base_url"),
            "phase": "STRATEGY_OPTIMIZER_RANGE_PROPOSAL",
            "temperature": 0.10,
            "status": "CALL_IN_FLIGHT",
        },
    }
    _persist_scientist_transition(job_id, source_round, in_flight)
    update_job(
        job_id,
        status="SCIENTIST_REQUESTING",
        active=True,
        current_round=source_round,
        message=(
            f"Round {source_round} has no eligible winner; requesting one bounded "
            f"Scientist proposal for round {target_round}."
        ),
        first_blocker="OPTIMIZER_KPI_ELIGIBILITY",
    )

    raw_response: str | None = None
    normalized: dict[str, Any] | None = None
    call_checkpoint = dict(in_flight)
    try:
        call = _autonomous_provider_call(route, payload)
        provenance = dict(call.provenance)
        confirmed_calls = int(
            provenance.get("confirmed_provider_calls") or 1
        )
        call_checkpoint = {
            **in_flight,
            "provider_call_state": "CONFIRMED",
            "confirmed_provider_calls": confirmed_calls,
            "unconfirmed_provider_attempts": 0,
            "actual_provider_calls": confirmed_calls,
            "provider_provenance": provenance,
        }
        _persist_scientist_transition(job_id, source_round, call_checkpoint)
        secret_value = credential or os.environ.get(
            str(route.get("api_key_env") or ""),
            "",
        )
        if secret_value and secret_value in call.text:
            raw_response = None
            raise ScientistAdvisoryError(
                "SCIENTIST_SECRET_ECHO_BLOCKED",
                "Scientist response contained credential material and was rejected",
                actual_llm_call=True,
                provenance=provenance,
            )
        raw_response = call.text
        try:
            normalized = parse_proposal(call.text)
            next_space, proposed_ranges = validate_proposal(
                current_space,
                normalized,
                request["optimize_params"],
            )
            decision = {
                "schema": "MAX_REBUILD_SCIENTIST_DECISION_V1",
                "job_id": job_id,
                "transition_id": transition_id,
                "source_round": source_round,
                "target_round": target_round,
                "mode": "SCIENTIST_PROPOSAL",
                "actual_llm_call": True,
                "proposal_attempts": confirmed_calls,
                "actual_provider_calls": confirmed_calls,
                "confirmed_provider_calls": confirmed_calls,
                "unconfirmed_provider_attempts": 0,
                "provider_call_state": "CONFIRMED",
                "accepted_proposals": 1,
                "rejected_proposals": 0,
                "fallbacks": 0,
                "proposal": normalized,
                "proposed_ranges": proposed_ranges,
                "reason": str(normalized.get("reason") or "")[:1000],
                "accepted": True,
                "validation": {
                    "status": "ACCEPTED",
                    "reason": (
                        "Proposal passed deterministic selected-parameter, type, "
                        "step-floor, hard-bound, and full-space validation."
                    ),
                },
                "effective_range_source": "SCIENTIST_PROPOSAL",
                "effective_ranges": next_space,
                "request_payload_sha256": request_sha,
                "provider_provenance": provenance,
                "created_utc": scientist_utc_now(),
            }
        except Exception as validation_exc:
            category = (
                str(validation_exc).split(":", 1)[0]
                if str(validation_exc).startswith("SCIENTIST_")
                else "SCIENTIST_SCHEMA_REJECTED"
            )
            next_space, decision = _fallback_decision(
                request,
                current_space,
                rows,
                job_id=job_id,
                source_round=source_round,
                target_round=target_round,
                category=category,
                reason=str(validation_exc)[:1000],
                actual_llm_call=True,
                proposal_attempts=confirmed_calls,
                actual_provider_calls=confirmed_calls,
                confirmed_provider_calls=confirmed_calls,
                request_payload_sha256=request_sha,
                provider_provenance=provenance,
                proposal=normalized,
                rejected_proposal=True,
                effective_source=(
                    "DETERMINISTIC_REFINEMENT_AFTER_REJECTED_SCIENTIST_PROPOSAL"
                ),
            )
    except ScientistAdvisoryError as call_exc:
        confirmed = bool(call_exc.actual_llm_call)
        confirmed_calls = int(
            call_exc.provenance.get("confirmed_provider_calls")
            or (1 if confirmed else 0)
        )
        proposal_attempts = max(
            1,
            len(call_exc.provenance.get("route_attempts") or []),
        )
        if confirmed:
            call_checkpoint = {
                **in_flight,
                "provider_call_state": "CONFIRMED",
                "confirmed_provider_calls": confirmed_calls,
                "unconfirmed_provider_attempts": 0,
                "actual_provider_calls": confirmed_calls,
                "provider_provenance": dict(call_exc.provenance),
            }
            _persist_scientist_transition(job_id, source_round, call_checkpoint)
        next_space, decision = _fallback_decision(
            request,
            current_space,
            rows,
            job_id=job_id,
            source_round=source_round,
            target_round=target_round,
            category=call_exc.category,
            reason=str(call_exc)[:1000],
            actual_llm_call=confirmed,
            proposal_attempts=proposal_attempts,
            actual_provider_calls=confirmed_calls,
            confirmed_provider_calls=confirmed_calls,
            unconfirmed_provider_attempts=0 if confirmed else 1,
            provider_call_state="CONFIRMED" if confirmed else "UNCONFIRMED",
            request_payload_sha256=request_sha,
            provider_provenance=call_exc.provenance,
            effective_source="DETERMINISTIC_REFINEMENT_AFTER_PROVIDER_FAILURE",
        )

    if raw_response is not None:
        decision["raw_response_sha256"] = hashlib.sha256(
            raw_response.encode("utf-8")
        ).hexdigest()
    if normalized is not None:
        decision["normalized_response_sha256"] = sha256_json(normalized)

    committed = {
        **call_checkpoint,
        "status": "COMMITTED",
        "actual_provider_calls": int(decision["actual_provider_calls"]),
        "confirmed_provider_calls": int(decision.get("confirmed_provider_calls") or 0),
        "unconfirmed_provider_attempts": int(
            decision.get("unconfirmed_provider_attempts") or 0
        ),
        "provider_call_state": str(
            decision.get("provider_call_state") or "NOT_STARTED"
        ),
        "provider_provenance": dict(decision.get("provider_provenance") or {}),
        "decision": decision,
        "committed_utc": scientist_utc_now(),
    }
    _persist_scientist_transition(job_id, source_round, committed)
    _ensure_scientist_evidence(
        job_id,
        source_round,
        decision,
        request_evidence=request_evidence,
        raw_response=raw_response,
        normalized_response=normalized,
    )
    return next_space, decision
