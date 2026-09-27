from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, ROOT
from .challenger_store import consumed_source_identities
from .mtf_geometry import STRATEGY_CONTRACT, assert_geometry_matches_main
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    FAMILY_WEIGHT_PARAMS,
    optimizer_parameter_bounds_for_keys,
    apply_frozen_gates,
    eligibility_audit,
    optimizer_fitness_for_request,
    parse_optimization_xml,
    report_matches_request,
    sha256_file,
)
from .optimizer_store import attach_rounds, get_job
from .workflow_contract import optimizer_uses_owner_selection

ALLOWED_PAGE_SIZES = {25, 50, 100}
MAX_PAGE_SIZE = 200
SORT_FIELDS = {
    "rank",
    "pass",
    "mean_r",
    "custom_fitness",
    "weighted_r",
    "profit_factor",
    "recovery_factor",
    "trades",
    "required_trades",
    "round",
}


def _resolve_project_path(value: str | Path) -> Path:
    raw = Path(value)
    return raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()


def _job_root(job: dict[str, Any]) -> Path:
    root = _resolve_project_path(str(job["evidence_dir"]))
    if not root.is_dir():
        raise RuntimeError("OPTIMIZER_ARTIFACT_ROOT_MISSING")
    return root


def _round_root(job: dict[str, Any], round_no: int) -> Path:
    return _job_root(job) / f"round_{int(round_no):02d}"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"OPTIMIZER_EVIDENCE_MISSING:{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OPTIMIZER_EVIDENCE_MALFORMED:{path.name}") from exc


def _verify_round_files(
    job: dict[str, Any],
    round_record: dict[str, Any],
) -> dict[str, Any]:
    round_no = int(round_record["round_no"])
    if str(round_record.get("phase") or "") != "PARSED":
        raise RuntimeError("QUALIFIED_SOURCE_ROUND_NOT_PARSED")
    root = _round_root(job, round_no)
    passes_path = root / "passes.json"
    audit_path = root / "eligibility_audit.json"
    provenance_path = root / "report_provenance.json"
    passes = _load_json(passes_path)
    audit = _load_json(audit_path)
    provenance = _load_json(provenance_path)

    report_value = round_record.get("report_path")
    sidecar_value = round_record.get("sidecar_path")
    if not report_value or not sidecar_value:
        raise RuntimeError("QUALIFIED_SOURCE_REPORT_EVIDENCE_MISSING")
    report_path = _resolve_project_path(str(report_value))
    sidecar_path = _resolve_project_path(str(sidecar_value))
    if not report_path.is_file() or not sidecar_path.is_file():
        raise RuntimeError("QUALIFIED_SOURCE_REPORT_FILE_MISSING")
    report_sha = sha256_file(report_path)
    sidecar_sha = sha256_file(sidecar_path)
    if report_sha != str(round_record.get("report_sha256") or ""):
        raise RuntimeError("QUALIFIED_SOURCE_REPORT_HASH_MISMATCH")
    if sidecar_sha != str(round_record.get("sidecar_sha256") or ""):
        raise RuntimeError("QUALIFIED_SOURCE_SIDECAR_HASH_MISMATCH")

    rows = passes.get("passes") if isinstance(passes, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("QUALIFIED_SOURCE_PASSES_INVALID")
    if int(audit.get("parsed_passes") or -1) != len(rows):
        raise RuntimeError("QUALIFIED_SOURCE_AUDIT_COUNT_MISMATCH")

    request = job["request"]
    if not report_matches_request(report_path, request):
        raise RuntimeError("QUALIFIED_SOURCE_REPORT_IDENTITY_MISMATCH")
    run_nonce = int((round_record.get("state") or {}).get("optimizer_run_nonce") or 0)
    reparsed = parse_optimization_xml(
        report_path,
        round_no=round_no,
        metrics_path=sidecar_path,
        expected_nonce=run_nonce,
        fixed_param_values=request["fixed_param_values"],
        optimize_params=request["optimize_params"],
        optimizer_fitness=optimizer_fitness_for_request(request),
    )
    apply_frozen_gates(reparsed, request)
    reparsed_payload = [row.payload() for row in reparsed]
    if reparsed_payload != rows:
        raise RuntimeError("QUALIFIED_SOURCE_PASSES_RECOMPUTE_MISMATCH")
    recomputed_audit = eligibility_audit(reparsed)
    if optimizer_uses_owner_selection(request):
        recomputed_audit = {
            **recomputed_audit,
            "winner_pass": None,
            "selection_authority": "OWNER_EXPLICIT_QUALIFIED_CANDIDATE_SELECTION",
        }
    if recomputed_audit != audit:
        raise RuntimeError("QUALIFIED_SOURCE_AUDIT_RECOMPUTE_MISMATCH")
    return {
        "root": root,
        "passes_path": passes_path,
        "audit_path": audit_path,
        "provenance_path": provenance_path,
        "report_path": report_path,
        "sidecar_path": sidecar_path,
        "report_sha256": report_sha,
        "sidecar_sha256": sidecar_sha,
        "rows": rows,
        "audit": audit,
        "provenance": provenance,
    }


def validate_candidate_params(params: Any) -> dict[str, int | float]:
    if not isinstance(params, dict):
        raise RuntimeError("QUALIFIED_CANDIDATE_PARAMS_INVALID")
    try:
        bounds = optimizer_parameter_bounds_for_keys(params)
    except ValueError as exc:
        raise RuntimeError("QUALIFIED_CANDIDATE_PARAMS_INVALID") from exc
    result: dict[str, int | float] = {}
    for name, (lo, hi, _step, kind) in bounds.items():
        try:
            value = float(params[name])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"QUALIFIED_CANDIDATE_PARAM_INVALID:{name}") from exc
        if not math.isfinite(value):
            raise RuntimeError(f"QUALIFIED_CANDIDATE_PARAM_NONFINITE:{name}")
        if value < lo - 1e-12 or value > hi + 1e-12:
            raise RuntimeError(f"QUALIFIED_CANDIDATE_PARAM_OUT_OF_BOUNDS:{name}")
        if name in FAMILY_WEIGHT_PARAMS and value <= 0:
            raise RuntimeError(f"QUALIFIED_CANDIDATE_FAMILY_WEIGHT_NONPOSITIVE:{name}")
        if kind == "int":
            if abs(value - round(value)) > 1e-9:
                raise RuntimeError(f"QUALIFIED_CANDIDATE_INTEGER_PARAM_INVALID:{name}")
            result[name] = int(round(value))
        else:
            result[name] = float(value)
    return result


def _finite_number(payload: dict[str, Any], key: str) -> float:
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"QUALIFIED_CANDIDATE_VALUE_INVALID:{key}") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"QUALIFIED_CANDIDATE_VALUE_NONFINITE:{key}")
    return value


def qualification_check(
    payload: dict[str, Any],
    request: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    sample = request.get("trade_sample") if isinstance(request.get("trade_sample"), dict) else {}
    kpi = request.get("kpi") if isinstance(request.get("kpi"), dict) else {}
    try:
        required_trades = int(sample["minimum_trades"])
        trades = int(payload["trades"])
        pf = _finite_number(payload, "profit_factor")
        rf = _finite_number(payload, "recovery_factor")
        mean_r = _finite_number(payload, "expectancy_r")
        custom_fitness = (
            _finite_number(payload, "custom_fitness")
            if payload.get("custom_fitness") is not None
            else None
        )
        weighted_r = _finite_number(payload, "weighted_r")
        params = validate_candidate_params(payload.get("params"))
    except Exception as exc:
        return False, [str(exc)], {}

    gates = {
        "minimum_trades": required_trades,
        "min_profit_factor": float(kpi["min_profit_factor"]),
        "min_recovery_factor": float(kpi["min_recovery_factor"]),
        "min_expectancy_r": float(kpi["min_expectancy_r"]),
        "min_weighted_r": float(kpi["min_weighted_r"]),
    }
    if trades < gates["minimum_trades"]:
        reasons.append("TRADES_BELOW_MINIMUM")
    if pf < gates["min_profit_factor"]:
        reasons.append("PROFIT_FACTOR_BELOW_MINIMUM")
    if rf < gates["min_recovery_factor"]:
        reasons.append("RECOVERY_FACTOR_BELOW_MINIMUM")
    if mean_r < gates["min_expectancy_r"]:
        reasons.append("MEAN_R_BELOW_MINIMUM")
    if weighted_r < gates["min_weighted_r"]:
        reasons.append("WEIGHTED_R_BELOW_MINIMUM")
    return not reasons, reasons, {
        "pass": int(payload["pass_no"]),
        "profit_factor": pf,
        "recovery_factor": rf,
        "mean_r": mean_r,
        "custom_fitness": custom_fitness,
        "weighted_r": weighted_r,
        "trades": trades,
        "required_trades": required_trades,
        "params": params,
        "hard_gates": gates,
    }


def _load_job(job_id: str, *, path: Path = DATABASE_PATH) -> dict[str, Any]:
    job = attach_rounds(get_job(job_id, path=path), path=path)
    if job is None:
        raise FileNotFoundError(job_id)
    request = job.get("request")
    if not isinstance(request, dict):
        raise RuntimeError("OPTIMIZER_REQUEST_MISSING")
    if request.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("OPTIMIZER_STRATEGY_CONTRACT_MISMATCH")
    geometry = assert_geometry_matches_main(
        dict(request.get("strategy_geometry") or {}),
        str(request.get("period") or ""),
    )
    if geometry != request.get("strategy_geometry"):
        raise RuntimeError("OPTIMIZER_STRATEGY_GEOMETRY_MISMATCH")
    request_path = _job_root(job) / "request.json"
    if _load_json(request_path) != request:
        raise RuntimeError("OPTIMIZER_REQUEST_DB_EVIDENCE_MISMATCH")
    return job


def _round_lineage(job: dict[str, Any], through_round: int) -> list[dict[str, Any]]:
    root = _job_root(job)
    lineage: list[dict[str, Any]] = []
    for record in job.get("rounds") or []:
        if str(record.get("phase") or "") != "PARSED":
            continue
        number = int(record["round_no"])
        if number > int(through_round):
            continue
        item: dict[str, Any] = {
            "round": number,
            "report_sha256": record.get("report_sha256"),
            "sidecar_sha256": record.get("sidecar_sha256"),
            "parsed_passes": record.get("parsed_passes"),
            "eligible_passes": record.get("eligible_passes"),
            "decision": (
                "QUALIFIED_POOL_AVAILABLE"
                if int(record.get("eligible_passes") or 0) > 0
                else "NO_QUALIFIED_CANDIDATE"
            ),
        }
        scientist_path = root / f"round_{number:02d}" / "scientist_proposal.json"
        if scientist_path.is_file():
            scientist = _load_json(scientist_path)
            item["scientist"] = {
                "source_round": scientist.get("source_round"),
                "target_round": scientist.get("target_round"),
                "decision_sha256": sha256_file(scientist_path),
                "accepted": scientist.get("accepted"),
                "mode": scientist.get("mode"),
                "effective_range_source": scientist.get("effective_range_source"),
            }
        lineage.append(item)
    return lineage


def _all_candidates(job: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    request = job["request"]
    qualified: list[dict[str, Any]] = []
    raw_count = 0
    for round_record in job.get("rounds") or []:
        if str(round_record.get("phase") or "") != "PARSED":
            continue
        evidence = _verify_round_files(job, round_record)
        rows = evidence["rows"]
        raw_count += len(rows)
        for payload in rows:
            if not isinstance(payload, dict):
                continue
            ok, _reasons, normalized = qualification_check(payload, request)
            if not ok:
                continue
            normalized.update(
                {
                    "job_id": job["job_id"],
                    "round": int(round_record["round_no"]),
                    "report_sha256": evidence["report_sha256"],
                    "sidecar_sha256": evidence["sidecar_sha256"],
                    "report_path": str(evidence["report_path"]),
                    "sidecar_path": str(evidence["sidecar_path"]),
                    "passes_path": str(evidence["passes_path"]),
                    "audit_path": str(evidence["audit_path"]),
                    "report_provenance_path": str(evidence["provenance_path"]),
                    "strategy_contract": request["strategy_contract"],
                    "strategy_geometry": request["strategy_geometry"],
                    "ea_sha256": request["ea"]["sha256"],
                    "request_path": str(_job_root(job) / "request.json"),
                    "run_nonce": int(
                        (round_record.get("state") or {}).get("optimizer_run_nonce") or 0
                    ),
                    "round_lineage": _round_lineage(
                        job,
                        int(round_record["round_no"]),
                    ),
                }
            )
            qualified.append(normalized)
    ranked = sorted(
        qualified,
        key=lambda item: (
            -float(item["mean_r"]),
            -float(item["weighted_r"]),
            -float(item["profit_factor"]),
            -float(item["recovery_factor"]),
            int(item["round"]),
            int(item["pass"]),
        ),
    )
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked, raw_count


def qualified_candidates_page(
    job_id: str,
    *,
    sort: str = "mean_r",
    order: str = "desc",
    page: int = 1,
    page_size: int = 25,
    query: str = "",
    round_no: int | None = None,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    if sort not in SORT_FIELDS:
        raise ValueError("unsupported qualified-candidate sort")
    direction = str(order).lower()
    if direction not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")
    size = int(page_size)
    if size not in ALLOWED_PAGE_SIZES:
        if size > MAX_PAGE_SIZE:
            raise ValueError("page_size exceeds hard maximum")
        raise ValueError("page_size must be 25, 50, or 100")
    requested_page = max(1, int(page))
    job = _load_job(job_id, path=path)
    candidates, raw_count = _all_candidates(job)
    historical_qualified_count = len(candidates)
    consumed = consumed_source_identities(job_id, path=path)
    candidates = [
        item
        for item in candidates
        if (int(item["round"]), int(item["pass"])) not in consumed
    ]
    qualified_count = len(candidates)
    needle = str(query or "").strip().casefold()
    filtered = [
        item
        for item in candidates
        if (round_no is None or int(item["round"]) == int(round_no))
        and (
            not needle
            or needle in str(item["pass"]).casefold()
            or needle in str(item["round"]).casefold()
            or needle in json.dumps(item["params"], sort_keys=True).casefold()
        )
    ]
    reverse = direction == "desc"
    key_map = {
        "rank": lambda row: int(row["rank"]),
        "pass": lambda row: int(row["pass"]),
        "mean_r": lambda row: float(row["mean_r"]),
        "custom_fitness": lambda row: (
            float(row["custom_fitness"])
            if row.get("custom_fitness") is not None
            else float("-inf")
        ),
        "weighted_r": lambda row: float(row["weighted_r"]),
        "profit_factor": lambda row: float(row["profit_factor"]),
        "recovery_factor": lambda row: float(row["recovery_factor"]),
        "trades": lambda row: int(row["trades"]),
        "required_trades": lambda row: int(row["required_trades"]),
        "round": lambda row: int(row["round"]),
    }
    filtered.sort(
        key=lambda row: (key_map[sort](row), -int(row["round"]), -int(row["pass"])),
        reverse=reverse,
    )
    total = len(filtered)
    pages = max(1, math.ceil(total / size)) if total else 1
    bounded_page = min(requested_page, pages)
    start = (bounded_page - 1) * size
    items = filtered[start : start + size]
    return {
        "job_id": job_id,
        "raw_count": raw_count,
        "qualified_count": qualified_count,
        "historical_qualified_count": historical_qualified_count,
        "consumed_count": historical_qualified_count - qualified_count,
        "rejected_count": raw_count - historical_qualified_count,
        "page": bounded_page,
        "page_size": size,
        "pages": pages,
        "total": total,
        "sort": sort,
        "order": direction,
        "query": query,
        "round": round_no,
        "items": items,
    }


def qualified_candidate(
    job_id: str,
    round_no: int,
    pass_no: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    job = _load_job(job_id, path=path)
    candidates, _ = _all_candidates(job)
    match = next(
        (
            item
            for item in candidates
            if int(item["round"]) == int(round_no)
            and int(item["pass"]) == int(pass_no)
        ),
        None,
    )
    if match is None:
        raise RuntimeError("QUALIFIED_CANDIDATE_NOT_FOUND_OR_REJECTED")
    return {**match, "request": job["request"]}


def revalidate_candidate_for_registration(
    job_id: str,
    round_no: int,
    pass_no: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Reparse canonical MT5 evidence and reapply all frozen gates."""
    consumed = consumed_source_identities(job_id, path=path)
    if (int(round_no), int(pass_no)) in consumed:
        raise RuntimeError("QUALIFIED_CANDIDATE_ALREADY_CONSUMED")
    candidate = qualified_candidate(job_id, round_no, pass_no, path=path)
    request = candidate["request"]
    if not optimizer_uses_owner_selection(request):
        raise RuntimeError("OWNER_SELECTION_REQUIRES_CURRENT_OPTIMIZER_WORKFLOW")
    if not report_matches_request(candidate["report_path"], request):
        raise RuntimeError("QUALIFIED_CANDIDATE_REPORT_IDENTITY_MISMATCH")
    rows = parse_optimization_xml(
        candidate["report_path"],
        round_no=int(round_no),
        metrics_path=candidate["sidecar_path"],
        expected_nonce=int(candidate["run_nonce"]),
        fixed_param_values=request["fixed_param_values"],
        optimize_params=request["optimize_params"],
        optimizer_fitness=optimizer_fitness_for_request(request),
    )
    apply_frozen_gates(rows, request)
    recomputed_audit = {
        **eligibility_audit(rows),
        "winner_pass": None,
        "selection_authority": "OWNER_EXPLICIT_QUALIFIED_CANDIDATE_SELECTION",
    }
    committed_audit = _load_json(Path(candidate["audit_path"]))
    if recomputed_audit != committed_audit:
        raise RuntimeError("QUALIFIED_CANDIDATE_AUDIT_RECOMPUTE_MISMATCH")
    selected = next(
        (row for row in rows if int(row.pass_no) == int(pass_no)),
        None,
    )
    if selected is None:
        raise RuntimeError("QUALIFIED_CANDIDATE_PASS_NOT_IN_REPORT")
    if not selected.eligible:
        raise RuntimeError("QUALIFIED_CANDIDATE_FAILED_RECOMPUTED_GATES")
    if selected.params != candidate["params"]:
        raise RuntimeError("QUALIFIED_CANDIDATE_PARAM_VECTOR_MISMATCH")
    comparisons = {
        "profit_factor": (selected.profit_factor, candidate["profit_factor"]),
        "recovery_factor": (selected.recovery_factor, candidate["recovery_factor"]),
        "mean_r": (selected.expectancy_r, candidate["mean_r"]),
        "weighted_r": (selected.weighted_r, candidate["weighted_r"]),
    }
    for label, (actual, expected) in comparisons.items():
        if not math.isclose(
            float(actual),
            float(expected),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise RuntimeError(f"QUALIFIED_CANDIDATE_METRIC_MISMATCH:{label}")
    if int(selected.trades) != int(candidate["trades"]):
        raise RuntimeError("QUALIFIED_CANDIDATE_TRADES_MISMATCH")
    if int(selected.minimum_trades_required) != int(candidate["required_trades"]):
        raise RuntimeError("QUALIFIED_CANDIDATE_REQUIRED_TRADES_MISMATCH")
    provenance = _load_json(Path(candidate["report_provenance_path"]))
    if str(provenance.get("report_sha256") or "") != candidate["report_sha256"]:
        raise RuntimeError("QUALIFIED_CANDIDATE_PROVENANCE_REPORT_HASH_MISMATCH")
    if str(provenance.get("sidecar_sha256") or "") != candidate["sidecar_sha256"]:
        raise RuntimeError("QUALIFIED_CANDIDATE_PROVENANCE_SIDECAR_HASH_MISMATCH")
    return candidate
