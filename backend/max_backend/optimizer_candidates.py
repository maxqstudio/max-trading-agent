from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, ROOT
from .db import connect
from .challenger_store import consumed_source_identities, get_challenger_by_source
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
from .optimizer_store import (
    attach_rounds,
    get_job,
    optimizer_candidate_identities,
    optimizer_candidate_identity,
    persist_candidate_projection,
)
from .optimizer_runtime import committed_round_dir, verify_committed_round_bundle
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
    round_root = _round_root(job, round_no)
    root = committed_round_dir(job["job_id"], round_no)
    if not root.is_dir():
        root = round_root
    else:
        verify_committed_round_bundle(root, job_id=job["job_id"], round_no=round_no)
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


def candidate_projection_payload(
    *,
    job_id: str,
    request: dict[str, Any],
    round_no: int,
    passes_payload: list[dict[str, Any]],
    report_path: str | Path,
    sidecar_path: str | Path,
    report_sha256: str,
    sidecar_sha256: str,
    bundle_path: str | Path,
    request_path: str | Path,
    run_nonce: int,
    existing_candidate_identities: set[str] | None = None,
) -> dict[str, Any]:
    bundle = Path(bundle_path)
    report = _resolve_project_path(report_path)
    sidecar = _resolve_project_path(sidecar_path)
    if not report.is_file() or not sidecar.is_file():
        raise RuntimeError("OPTIMIZER_PROJECTION_SOURCE_EVIDENCE_MISSING")
    candidates_by_identity: dict[str, dict[str, Any]] = {}
    already_projected = existing_candidate_identities or set()
    for payload in passes_payload:
        if not isinstance(payload, dict):
            raise RuntimeError("OPTIMIZER_PROJECTION_PASS_INVALID")
        qualifies, _reasons, normalized = qualification_check(payload, request)
        if not qualifies:
            continue
        candidate = {
            **normalized,
            "job_id": job_id,
            "round": int(round_no),
            "report_sha256": report_sha256,
            "sidecar_sha256": sidecar_sha256,
            "report_path": str(report),
            "sidecar_path": str(sidecar),
            "passes_path": str(bundle / "passes.json"),
            "audit_path": str(bundle / "eligibility_audit.json"),
            "report_provenance_path": str(bundle / "report_provenance.json"),
            "strategy_contract": request["strategy_contract"],
            "strategy_geometry": request["strategy_geometry"],
            "ea_sha256": request["ea"]["sha256"],
            "request_path": str(request_path),
            "run_nonce": int(run_nonce),
        }
        candidate_identity = optimizer_candidate_identity(candidate)
        if candidate_identity in already_projected:
            continue
        search_text = (
            f"{int(round_no)} {int(candidate['pass'])} "
            + json.dumps(candidate["params"], sort_keys=True, separators=(",", ":"))
        ).casefold()
        projected_candidate = {
            "pass": int(candidate["pass"]),
            "mean_r": float(candidate["mean_r"]),
            "custom_fitness": candidate.get("custom_fitness"),
            "weighted_r": float(candidate["weighted_r"]),
            "profit_factor": float(candidate["profit_factor"]),
            "recovery_factor": float(candidate["recovery_factor"]),
            "trades": int(candidate["trades"]),
            "required_trades": int(candidate["required_trades"]),
            "search_text": search_text,
            "candidate": candidate,
        }
        previous = candidates_by_identity.get(candidate_identity)
        if previous is None or int(projected_candidate["pass"]) < int(
            previous["pass"]
        ):
            candidates_by_identity[candidate_identity] = projected_candidate
    candidates = sorted(
        candidates_by_identity.values(),
        key=lambda item: int(item["pass"]),
    )
    content = json.dumps(candidates, sort_keys=True, separators=(",", ":"))
    return {
        "report_sha256": str(report_sha256),
        "sidecar_sha256": str(sidecar_sha256),
        "projection_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "candidates": candidates,
    }


def _ensure_candidate_projection(
    job: dict[str, Any],
    *,
    path: Path,
) -> None:
    with connect(path) as conn:
        projected = {
            (str(row["job_id"]), int(row["round_no"]))
            for row in conn.execute(
                "SELECT job_id,round_no FROM optimizer_candidate_projection_rounds "
                "WHERE job_id=?",
                (str(job["job_id"]),),
            ).fetchall()
        }
    for record in job.get("rounds") or []:
        round_no = int(record["round_no"])
        if record.get("phase") != "PARSED" or (job["job_id"], round_no) in projected:
            continue
        evidence = _verify_round_files(job, record)
        projection = candidate_projection_payload(
            job_id=str(job["job_id"]),
            request=job["request"],
            round_no=round_no,
            passes_payload=evidence["rows"],
            report_path=evidence["report_path"],
            sidecar_path=evidence["sidecar_path"],
            report_sha256=evidence["report_sha256"],
            sidecar_sha256=evidence["sidecar_sha256"],
            bundle_path=evidence["root"],
            request_path=_job_root(job) / "request.json",
            run_nonce=int((record.get("state") or {}).get("optimizer_run_nonce") or 0),
            existing_candidate_identities=optimizer_candidate_identities(
                str(job["job_id"]), path=path
            ),
        )
        persist_candidate_projection(
            str(job["job_id"]), round_no, projection, path=path
        )


def _candidate_consumed_sql(alias: str = "p") -> str:
    return f"""(
        EXISTS (
            SELECT 1 FROM strategy_challengers c
            WHERE c.source_job_id={alias}.job_id
              AND c.source_round={alias}.round_no
              AND c.source_pass={alias}.pass_no
        ) OR EXISTS (
            SELECT 1
            FROM strategy_challenger_batch_items i
            JOIN strategy_challenger_batches b ON b.batch_id=i.batch_id
            WHERE b.job_id={alias}.job_id AND b.state='COMMITTED'
              AND i.source_round={alias}.round_no
              AND i.source_pass={alias}.pass_no
        )
    )"""


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
    _ensure_candidate_projection(job, path=path)
    consumed_sql = _candidate_consumed_sql()
    sort_columns = {
        "rank": "p.rank_value",
        "pass": "p.pass_no",
        "mean_r": "p.mean_r",
        "custom_fitness": "p.custom_fitness",
        "weighted_r": "p.weighted_r",
        "profit_factor": "p.profit_factor",
        "recovery_factor": "p.recovery_factor",
        "trades": "p.trades",
        "required_trades": "p.required_trades",
        "round": "p.round_no",
    }
    where = ["p.job_id=?", f"NOT {consumed_sql}"]
    params: list[Any] = [job_id]
    if round_no is not None:
        where.append("p.round_no=?")
        params.append(int(round_no))
    needle = str(query or "").strip().casefold()
    if needle:
        where.append("p.search_text LIKE ? ESCAPE '\\'")
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    where_sql = " AND ".join(where)
    sort_direction = direction.upper()
    tie_direction = "ASC" if direction == "desc" else "DESC"
    with connect(path) as conn:
        raw_count = int(conn.execute(
            "SELECT COALESCE(SUM(parsed_passes),0) AS n FROM optimizer_rounds "
            "WHERE job_id=? AND phase='PARSED'",
            (job_id,),
        ).fetchone()["n"])
        eligible_pass_count = int(conn.execute(
            "SELECT COALESCE(SUM(eligible_passes),0) AS n FROM optimizer_rounds "
            "WHERE job_id=? AND phase='PARSED'",
            (job_id,),
        ).fetchone()["n"])
        historical_qualified_count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM optimizer_candidate_projection WHERE job_id=?",
            (job_id,),
        ).fetchone()["n"])
        consumed_count = int(conn.execute(
            f"SELECT COUNT(*) AS n FROM optimizer_candidate_projection p "
            f"WHERE p.job_id=? AND {consumed_sql}",
            (job_id,),
        ).fetchone()["n"])
        qualified_count = historical_qualified_count - consumed_count
        total = int(conn.execute(
            "SELECT COUNT(*) AS n FROM optimizer_candidate_projection p WHERE " + where_sql,
            tuple(params),
        ).fetchone()["n"])
        pages = max(1, math.ceil(total / size)) if total else 1
        bounded_page = min(requested_page, pages)
        offset = (bounded_page - 1) * size
        rows = conn.execute(
            "SELECT p.rank_value,p.candidate_json FROM optimizer_candidate_projection p "
            "WHERE " + where_sql +
            f" ORDER BY {sort_columns[sort]} {sort_direction}, "
            f"p.round_no {tie_direction},p.pass_no {tie_direction} LIMIT ? OFFSET ?",
            tuple(params + [size, offset]),
        ).fetchall()
    items = []
    for row in rows:
        candidate = json.loads(str(row["candidate_json"]))
        candidate["rank"] = int(row["rank_value"])
        items.append(candidate)
    return {
        "job_id": job_id,
        "raw_count": raw_count,
        "qualified_count": qualified_count,
        "historical_qualified_count": historical_qualified_count,
        "consumed_count": historical_qualified_count - qualified_count,
        "deduplicated_count": max(0, eligible_pass_count - historical_qualified_count),
        "rejected_count": raw_count - eligible_pass_count,
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


def _revalidate_candidate(
    job_id: str,
    round_no: int,
    pass_no: int,
    *,
    path: Path = DATABASE_PATH,
    allowed_existing_challenger_id: str | None = None,
) -> dict[str, Any]:
    """Reparse canonical MT5 evidence and reapply all frozen gates."""
    consumed = consumed_source_identities(job_id, path=path)
    identity = (int(round_no), int(pass_no))
    if identity in consumed:
        if allowed_existing_challenger_id is None:
            raise RuntimeError("QUALIFIED_CANDIDATE_ALREADY_CONSUMED")
        consumer = get_challenger_by_source(
            job_id,
            round_no,
            pass_no,
            path=path,
        )
        if (
            consumer is None
            or str(consumer["challenger_id"])
            != str(allowed_existing_challenger_id)
        ):
            raise RuntimeError("QUALIFIED_CANDIDATE_CONSUMER_MISMATCH")
        if str(consumer["status"]) != "CHALLENGER":
            raise RuntimeError("QUALIFIED_CANDIDATE_CONSUMER_NOT_ACTIVE")
    elif allowed_existing_challenger_id is not None:
        raise RuntimeError("QUALIFIED_CANDIDATE_CONSUMER_MISMATCH")
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


def revalidate_candidate_for_registration(
    job_id: str,
    round_no: int,
    pass_no: int,
    *,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Revalidate an unconsumed candidate before creating its Challenger row."""
    return _revalidate_candidate(
        job_id,
        round_no,
        pass_no,
        path=path,
    )


def revalidate_candidate_for_promotion(
    job_id: str,
    round_no: int,
    pass_no: int,
    *,
    challenger_id: str,
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Deep-revalidate only the exact active Challenger owning this source."""
    consumer = get_challenger_by_source(
        job_id,
        round_no,
        pass_no,
        path=path,
    )
    if consumer is None or str(consumer["challenger_id"]) != str(challenger_id):
        raise RuntimeError("QUALIFIED_CANDIDATE_CONSUMER_MISMATCH")
    if str(consumer["status"]) != "CHALLENGER":
        raise RuntimeError("QUALIFIED_CANDIDATE_CONSUMER_NOT_ACTIVE")
    return _revalidate_candidate(
        job_id,
        round_no,
        pass_no,
        path=path,
        allowed_existing_challenger_id=challenger_id,
    )
