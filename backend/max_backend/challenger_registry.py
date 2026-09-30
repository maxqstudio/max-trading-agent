from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .challenger_bundle import (
    WINNER_RANKING,
    current_baseline_sha256,
    _build_bundle,
    _json,
    _same_number,
    _same_params,
    _sha,
    apply_params_to_challenger_ea,
    validate_full_params,
    verify_challenger_bundle,
    verify_parameter_parity,
    write_challenger_set,
)
from .challenger_store import (
    REGISTERING_STATUS,
    VISIBLE_STATUS,
    challenger_id_exists,
    finalize_challenger,
    get_challenger,
    get_challenger_by_source,
    list_challengers as list_registry_rows,
    mark_registration_error,
    reserve_challenger,
)
from .config import CHALLENGER_ARTIFACT_ROOT, EA_BASELINE, ROOT
from .optimizer_core import (
    ABSOLUTE_BOUNDS,
    optimizer_parameter_bounds_for_keys,
    apply_frozen_gates,
    eligibility_audit,
    optimizer_fitness_for_request,
    parse_optimization_xml,
    read_ea_optimizer_defaults,
    report_matches_request,
    select_winner,
    trade_sample,
)
from .mtf_geometry import STRATEGY_CONTRACT, assert_geometry_matches_main
from .optimizer_runtime import OPTIMIZER_EVIDENCE_ROOT
from .optimizer_store import get_job, get_round, get_rounds, utc_now

def _winner_job_allowed(job: dict[str, Any]) -> bool:
    terminal = str(job.get("terminal_result") or "")
    status = str(job.get("status") or "")
    allowed = {
        ("ELIGIBLE_WINNER_FOUND", "ELIGIBLE_WINNER_FOUND"),
        ("REGISTERING_CHALLENGER", "ELIGIBLE_WINNER_FOUND"),
        ("CHALLENGER_REGISTRATION_FAILED", "ELIGIBLE_WINNER_FOUND"),
        ("RESUMING", "ELIGIBLE_WINNER_FOUND"),
        ("STRATEGY_CHALLENGER_FOUND", "STRATEGY_CHALLENGER_FOUND"),
    }
    return (status, terminal) in allowed


def _metric_equal(label: str, actual: Any, expected: Any) -> None:
    if not _same_number(actual, expected, tolerance=5e-7):
        raise RuntimeError(
            f"WINNER_EVIDENCE_MISMATCH:{label}:actual={actual}:expected={expected}"
        )


def verify_optimizer_winner(
    job_id: str,
    *,
    expected_round: int | None = None,
    expected_pass: int | None = None,
) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise RuntimeError("CHALLENGER_SOURCE_JOB_UNKNOWN")
    if not _winner_job_allowed(job):
        raise RuntimeError(
            f"CHALLENGER_SOURCE_NOT_ELIGIBLE_WINNER:{job.get('status')}:"
            f"{job.get('terminal_result')}"
        )

    job_root = OPTIMIZER_EVIDENCE_ROOT / job_id
    winner_path = job_root / "eligible_winner.json"
    request_path = job_root / "request.json"
    if not winner_path.is_file():
        raise RuntimeError("ELIGIBLE_WINNER_EVIDENCE_MISSING")
    if not request_path.is_file():
        raise RuntimeError("OPTIMIZER_REQUEST_EVIDENCE_MISSING")

    winner_evidence = _json(winner_path)
    request_evidence = _json(request_path)
    if request_evidence != job["request"]:
        raise RuntimeError("OPTIMIZER_REQUEST_DB_EVIDENCE_MISMATCH")
    if request_evidence.get("schema") != "MAX_REBUILD_OPTIMIZER_REQUEST_V3":
        raise RuntimeError("OPTIMIZER_REQUEST_SCHEMA_UNSUPPORTED")
    if request_evidence.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("OPTIMIZER_STRATEGY_CONTRACT_MISMATCH")
    expected_geometry = assert_geometry_matches_main(
        dict(request_evidence.get("strategy_geometry") or {}),
        str(request_evidence.get("period") or ""),
    )
    if request_evidence.get("strategy_geometry") != expected_geometry:
        raise RuntimeError("OPTIMIZER_STRATEGY_GEOMETRY_MISMATCH")
    recomputed_sample = trade_sample(
        request_evidence["period"],
        request_evidence["from_date"],
        request_evidence["to_date"],
        request_evidence["kpi"],
    )
    frozen_sample = request_evidence.get("trade_sample")
    if not isinstance(frozen_sample, dict):
        raise RuntimeError("OPTIMIZER_TRADE_SAMPLE_MISSING")
    for key in (
        "minimum_trades",
        "scaled_trades_per_month",
        "timeframe",
        "timeframe_scaling",
    ):
        if frozen_sample.get(key) != recomputed_sample.get(key):
            raise RuntimeError(f"OPTIMIZER_TRADE_SAMPLE_MISMATCH:{key}")

    if str(winner_evidence.get("job_id") or "") != job_id:
        raise RuntimeError("WINNER_JOB_ID_MISMATCH")
    if winner_evidence.get("strategy_contract") != STRATEGY_CONTRACT:
        raise RuntimeError("WINNER_STRATEGY_CONTRACT_MISMATCH")
    if winner_evidence.get("strategy_geometry") != expected_geometry:
        raise RuntimeError("WINNER_STRATEGY_GEOMETRY_MISMATCH")
    raw_round = winner_evidence.get("round")
    raw_pass = winner_evidence.get("mt5_pass")
    round_no = int(raw_round) if raw_round is not None else 0
    pass_no = int(raw_pass) if raw_pass is not None else -1
    if expected_round is not None and round_no != int(expected_round):
        raise RuntimeError("WINNER_ROUND_IDENTITY_MISMATCH")
    if expected_pass is not None and pass_no != int(expected_pass):
        raise RuntimeError("WINNER_PASS_IDENTITY_MISMATCH")
    if round_no < 1 or pass_no < 0:
        raise RuntimeError("WINNER_IDENTITY_INVALID")

    if str(winner_evidence.get("ea_sha256") or "") != current_baseline_sha256():
        raise RuntimeError("WINNER_EA_SHA_MISMATCH")
    if request_evidence.get("ea", {}).get("sha256") != current_baseline_sha256():
        raise RuntimeError("REQUEST_EA_SHA_MISMATCH")
    if _sha(EA_BASELINE) != current_baseline_sha256():
        raise RuntimeError("BASELINE_EA_SHA_MISMATCH")

    params = validate_full_params(winner_evidence.get("params"))
    round_record = get_round(job_id, round_no)
    if round_record is None or round_record["phase"] != "PARSED":
        raise RuntimeError("WINNING_ROUND_NOT_PARSED")
    if int(round_record.get("winner_pass") if round_record.get("winner_pass") is not None else -1) != pass_no:
        raise RuntimeError("WINNING_ROUND_DB_PASS_MISMATCH")

    round_root = job_root / f"round_{round_no:02d}"
    xml_path = round_root / "Max_MTF.xml"
    sidecar_path = round_root / "Max_MTF_metrics.csv"
    passes_path = round_root / "passes.json"
    audit_path = round_root / "eligibility_audit.json"
    provenance_path = round_root / "report_provenance.json"
    for source in (
        xml_path,
        sidecar_path,
        passes_path,
        audit_path,
        provenance_path,
    ):
        if not source.is_file():
            raise RuntimeError(f"WINNER_SOURCE_EVIDENCE_MISSING:{source.name}")

    if not report_matches_request(xml_path, request_evidence):
        raise RuntimeError("WINNER_REPORT_IDENTITY_MISMATCH")
    xml_sha = _sha(xml_path)
    sidecar_sha = _sha(sidecar_path)
    if xml_sha != str(winner_evidence.get("source_report_sha256") or ""):
        raise RuntimeError("WINNER_XML_SHA_MISMATCH")
    if sidecar_sha != str(winner_evidence.get("sidecar_sha256") or ""):
        raise RuntimeError("WINNER_SIDECAR_SHA_MISMATCH")
    if xml_sha != str(round_record.get("report_sha256") or ""):
        raise RuntimeError("WINNER_XML_DB_SHA_MISMATCH")
    if sidecar_sha != str(round_record.get("sidecar_sha256") or ""):
        raise RuntimeError("WINNER_SIDECAR_DB_SHA_MISMATCH")

    run_nonce = int(round_record["state"]["optimizer_run_nonce"])
    if run_nonce != int(winner_evidence.get("run_nonce") or -1):
        raise RuntimeError("WINNER_RUN_NONCE_MISMATCH")

    rows = parse_optimization_xml(
        xml_path,
        round_no=round_no,
        metrics_path=sidecar_path,
        expected_nonce=run_nonce,
        fixed_param_values=request_evidence["fixed_param_values"],
        optimize_params=request_evidence["optimize_params"],
        optimizer_fitness=optimizer_fitness_for_request(request_evidence),
    )
    apply_frozen_gates(rows, request_evidence)
    deterministic_winner = select_winner(rows)
    if deterministic_winner is None:
        raise RuntimeError("WINNER_NOT_DETERMINISTICALLY_ELIGIBLE")
    if deterministic_winner.pass_no != pass_no:
        raise RuntimeError(
            f"WINNER_NOT_DETERMINISTIC_SELECTED:{deterministic_winner.pass_no}"
        )
    if not deterministic_winner.eligible:
        raise RuntimeError("WINNER_FAILED_HARD_GATES")
    if not _same_params(deterministic_winner.params, params):
        raise RuntimeError("WINNER_PARAM_VECTOR_MISMATCH")

    _metric_equal(
        "profit_factor",
        deterministic_winner.profit_factor,
        winner_evidence.get("profit_factor"),
    )
    _metric_equal(
        "recovery_factor",
        deterministic_winner.recovery_factor,
        winner_evidence.get("recovery_factor"),
    )
    _metric_equal(
        "mean_r",
        deterministic_winner.expectancy_r,
        winner_evidence.get("mean_r"),
    )
    _metric_equal(
        "weighted_r",
        deterministic_winner.weighted_r,
        winner_evidence.get("weighted_r"),
    )
    if deterministic_winner.trades != int(winner_evidence.get("trades") or -1):
        raise RuntimeError("WINNER_TRADES_MISMATCH")
    if deterministic_winner.minimum_trades_required != int(
        winner_evidence.get("minimum_required_trades") or -1
    ):
        raise RuntimeError("WINNER_MINIMUM_TRADES_MISMATCH")

    expected_gates = {
        "min_profit_factor": deterministic_winner.min_profit_factor_required,
        "min_recovery_factor": deterministic_winner.min_recovery_factor_required,
        "min_expectancy_r": deterministic_winner.min_expectancy_r_required,
        "min_weighted_r": deterministic_winner.min_weighted_r_required,
    }
    frozen_gates = winner_evidence.get("frozen_gates")
    if not isinstance(frozen_gates, dict):
        raise RuntimeError("WINNER_HARD_GATES_MISSING")
    for key, expected in expected_gates.items():
        _metric_equal(key, frozen_gates.get(key), expected)
    if winner_evidence.get("ranking_authority") != WINNER_RANKING:
        raise RuntimeError("WINNER_RANKING_AUTHORITY_MISMATCH")

    passes_evidence = _json(passes_path)
    pass_rows = passes_evidence.get("passes")
    if not isinstance(pass_rows, list):
        raise RuntimeError("WINNER_PASSES_EVIDENCE_INVALID")
    pass_evidence = next(
        (
            item
            for item in pass_rows
            if isinstance(item, dict) and int(item.get("pass_no", -1)) == pass_no
        ),
        None,
    )
    if pass_evidence is None:
        raise RuntimeError("WINNER_PASS_NOT_IN_PASSES_EVIDENCE")
    if str(pass_evidence.get("eligibility") or "") != "ELIGIBLE":
        raise RuntimeError("WINNER_PASS_EVIDENCE_NOT_ELIGIBLE")
    if not _same_params(pass_evidence.get("params") or {}, params):
        raise RuntimeError("WINNER_PASSES_PARAM_MISMATCH")

    audit = _json(audit_path)
    recomputed_audit = eligibility_audit(rows)
    if audit != recomputed_audit:
        raise RuntimeError("WINNER_ELIGIBILITY_AUDIT_RECOMPUTE_MISMATCH")
    if int(audit.get("eligible_passes") or 0) < 1:
        raise RuntimeError("WINNER_ELIGIBILITY_AUDIT_HAS_ZERO_ELIGIBLE")
    if int(audit.get("winner_pass") if audit.get("winner_pass") is not None else -1) != pass_no:
        raise RuntimeError("WINNER_ELIGIBILITY_AUDIT_MISMATCH")

    kpi = {
        "profit_factor": deterministic_winner.profit_factor,
        "recovery_factor": deterministic_winner.recovery_factor,
        "mean_r": deterministic_winner.expectancy_r,
        "weighted_r": deterministic_winner.weighted_r,
        "trades": deterministic_winner.trades,
        "required_trades": deterministic_winner.minimum_trades_required,
    }
    hard_gates = {
        "minimum_trades": deterministic_winner.minimum_trades_required,
        **expected_gates,
    }

    round_lineage: list[dict[str, Any]] = []
    for record in get_rounds(job_id):
        if record["phase"] != "PARSED":
            continue
        number = int(record["round_no"])
        item: dict[str, Any] = {
            "round": number,
            "report_sha256": record.get("report_sha256"),
            "sidecar_sha256": record.get("sidecar_sha256"),
            "parsed_passes": record.get("parsed_passes"),
            "eligible_passes": record.get("eligible_passes"),
            "winner_pass": record.get("winner_pass"),
            "decision": (
                "ELIGIBLE_WINNER"
                if record.get("winner_pass") is not None
                else "NO_ELIGIBLE_WINNER"
            ),
        }
        scientist_path = (
            job_root / f"round_{number:02d}" / "scientist_proposal.json"
        )
        if scientist_path.is_file():
            scientist = _json(scientist_path)
            item["scientist"] = {
                "source_round": scientist.get("source_round"),
                "target_round": scientist.get("target_round"),
                "decision_sha256": _sha(scientist_path),
                "accepted": scientist.get("accepted"),
                "mode": scientist.get("mode"),
                "effective_range_source": scientist.get("effective_range_source"),
            }
        round_lineage.append(item)

    return {
        "job": job,
        "job_id": job_id,
        "round": round_no,
        "pass": pass_no,
        "request": request_evidence,
        "winner": winner_evidence,
        "params": params,
        "kpi": kpi,
        "hard_gates": hard_gates,
        "xml_path": xml_path,
        "xml_sha256": xml_sha,
        "sidecar_path": sidecar_path,
        "sidecar_sha256": sidecar_sha,
        "passes_path": passes_path,
        "audit_path": audit_path,
        "report_provenance_path": provenance_path,
        "winner_path": winner_path,
        "request_path": request_path,
        "run_nonce": run_nonce,
        "round_lineage": round_lineage,
    }


def challenger_id_for_source(
    job_id: str,
    round_no: int,
    pass_no: int,
) -> str:
    match = re.search(r"(20\d{6})[_-]?(\d{6})", str(job_id))
    if match:
        date_part, time_part = match.group(1), match.group(2)
    else:
        job = get_job(job_id)
        created = str((job or {}).get("created_utc") or "")
        compact = re.sub(r"\D", "", created)[:14]
        if len(compact) != 14:
            raise RuntimeError("CHALLENGER_SOURCE_TIMESTAMP_UNAVAILABLE")
        date_part, time_part = compact[:8], compact[8:14]
    return (
        f"STRAT-{date_part}-{time_part}-"
        f"R{int(round_no):02d}-P{int(pass_no)}"
    )


def _allocate_challenger_id(
    base: str,
    *,
    path: Path | None = None,
) -> str:
    kwargs = {"path": path} if path is not None else {}
    if not challenger_id_exists(base, **kwargs):
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix:02d}"
        if not challenger_id_exists(candidate, **kwargs):
            return candidate
    raise RuntimeError("CHALLENGER_ID_SPACE_EXHAUSTED")


def ensure_challenger_for_winner(
    job_id: str,
    *,
    expected_round: int | None = None,
    expected_pass: int | None = None,
) -> dict[str, Any]:
    source = verify_optimizer_winner(
        job_id,
        expected_round=expected_round,
        expected_pass=expected_pass,
    )
    existing = get_challenger_by_source(
        job_id,
        source["round"],
        source["pass"],
    )
    if existing is not None and existing["status"] == VISIBLE_STATUS:
        integrity = verify_challenger_bundle(existing["challenger_id"])
        return {**existing, "artifact_integrity": integrity}

    if existing is None:
        base = challenger_id_for_source(job_id, source["round"], source["pass"])
        challenger_id = _allocate_challenger_id(base)
        bundle_path = (
            Path("artifacts")
            / "strategy_challengers"
            / challenger_id
        ).as_posix()
        reservation = reserve_challenger(
            {
                "challenger_id": challenger_id,
                "source_job_id": job_id,
                "source_round": source["round"],
                "source_pass": source["pass"],
                "created_utc": utc_now(),
                "ea_version": source["request"]["ea"]["version"],
                "baseline_ea_sha256": current_baseline_sha256(),
                "bundle_path": bundle_path,
                "params": source["params"],
                "kpi": source["kpi"],
                "hard_gates": source["hard_gates"],
                "source_request": source["request"],
                "winner": source["winner"],
                "provenance": {
                    "round_lineage": source["round_lineage"],
                    "winner_evidence_sha256": _sha(source["winner_path"]),
                    "run_nonce": source["run_nonce"],
                },
                "winning_xml_sha256": source["xml_sha256"],
                "winning_sidecar_sha256": source["sidecar_sha256"],
            }
        )
    else:
        reservation = existing
        challenger_id = reservation["challenger_id"]
        if not _same_params(reservation["params"], source["params"]):
            raise RuntimeError("REGISTERING_CHALLENGER_SOURCE_PARAM_CONFLICT")
        if reservation["winning_xml_sha256"] != source["xml_sha256"]:
            raise RuntimeError("REGISTERING_CHALLENGER_SOURCE_XML_CONFLICT")
        if reservation["winning_sidecar_sha256"] != source["sidecar_sha256"]:
            raise RuntimeError("REGISTERING_CHALLENGER_SOURCE_SIDECAR_CONFLICT")

    final_bundle = ROOT / reservation["bundle_path"]
    staging = CHALLENGER_ARTIFACT_ROOT / f".staging-{challenger_id}"

    try:
        if final_bundle.exists():
            if reservation["status"] != REGISTERING_STATUS:
                integrity = verify_challenger_bundle(challenger_id)
                return {**reservation, "artifact_integrity": integrity}
            verified = verify_challenger_bundle(
                challenger_id,
                allow_registering=True,
            )
            finalized = finalize_challenger(
                challenger_id,
                challenger_ea_sha256=verified["ea_sha256"],
                set_sha256=verified["set_sha256"],
                metadata_sha256=verified["metadata_sha256"],
                manifest_sha256=verified["manifest_sha256"],
            )
            return {
                **finalized,
                "artifact_integrity": verify_challenger_bundle(challenger_id),
            }

        if staging.exists():
            shutil.rmtree(staging)

        CHALLENGER_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        build = _build_bundle(
            challenger_id,
            reservation["created_utc"],
            source,
            staging,
        )
        os.replace(staging, final_bundle)

        # Verify final committed directory before exposing CHALLENGER status.
        temp_integrity = verify_challenger_bundle(
            challenger_id,
            allow_registering=True,
        )
        if build["manifest_sha256"] != temp_integrity["manifest_sha256"]:
            raise RuntimeError("CHALLENGER_POST_COMMIT_MANIFEST_MISMATCH")
        finalized = finalize_challenger(
            challenger_id,
            challenger_ea_sha256=build["ea_sha256"],
            set_sha256=build["set_sha256"],
            metadata_sha256=build["metadata_sha256"],
            manifest_sha256=build["manifest_sha256"],
        )
        integrity = verify_challenger_bundle(challenger_id)
        return {**finalized, "artifact_integrity": integrity}
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            mark_registration_error(challenger_id, str(exc))
        except Exception:
            pass
        raise


def challenger_detail(challenger_id: str) -> dict[str, Any]:
    row = get_challenger(challenger_id)
    if row is None or row["status"] not in {VISIBLE_STATUS, "RETIRED"}:
        raise FileNotFoundError(challenger_id)
    try:
        integrity = verify_challenger_bundle(
            challenger_id,
            allow_retired=row["status"] == "RETIRED",
        )
    except Exception as exc:
        integrity = {
            "status": "INTEGRITY_FAIL",
            "challenger_id": challenger_id,
            "reason": str(exc),
        }
    baseline_params = read_ea_optimizer_defaults(EA_BASELINE)
    try:
        retained_bounds = optimizer_parameter_bounds_for_keys(row["params"])
    except ValueError as exc:
        raise RuntimeError(
            f"CHALLENGER_PARAMETER_UNIVERSE_INVALID:{exc}"
        ) from exc
    comparison = []
    for name in retained_bounds:
        baseline_value = baseline_params[name]
        challenger_value = row["params"][name]
        comparison.append(
            {
                "parameter": name,
                "baseline": baseline_value,
                "challenger": challenger_value,
                "different": not _same_number(
                    baseline_value,
                    challenger_value,
                ),
            }
        )
    return {
        **row,
        "artifact_integrity": integrity,
        "parameter_comparison": comparison,
    }


def list_challengers() -> list[dict[str, Any]]:
    result = []
    for row in list_registry_rows():
        result.append(
            {
                "challenger_id": row["challenger_id"],
                "status": row["status"],
                "role_origin": row["role_origin"],
                "created_utc": row["created_utc"],
                "source_job_id": row["source_job_id"],
                "source_round": row["source_round"],
                "source_pass": row["source_pass"],
                "kpi": row["kpi"],
                "integrity": "NOT_CHECKED",
            }
        )
    return result
