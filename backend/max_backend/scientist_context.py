from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .champion_store import current_champion, get_promotion
from .challenger_store import get_challenger
from .challenger_operations_store import (
    get_backtest,
    list_backtests,
    list_retirements,
    migrate_m06,
)
from .config import (
    CHALLENGER_ARTIFACT_ROOT,
    LEGACY_CHALLENGER_ARTIFACT_ROOT,
    DATABASE_PATH,
    EA_BASELINE,
    EA_MANIFEST,
    RESEARCH_ARTIFACT_ROOT,
    ROOT,
)
from .db import connect, read_baseline
from .optimizer_store import get_job, get_rounds, latest_job
from .research_service import canonical_research_stage
from .scientist_knowledge import (
    knowledge_sha256,
    load_knowledge,
    static_evidence,
)
from .scientist_store import list_messages

ACTIVE_CHALLENGER_LIMIT = 20
RETIRED_CHALLENGER_LIMIT = 12
BACKTEST_HISTORY_LIMIT = 10
PROMOTION_HISTORY_LIMIT = 12
CONVERSATION_MESSAGE_LIMIT = 12
LATEST_JOB_ROUND_LIMIT = 3
EXPLICIT_JOB_ROUND_LIMIT = 8
ANSWER_MAX_CHARS = 6000

STRATEGY_RE = re.compile(r"\bSTRAT-\d{8}-\d{6}-R\d{2,}-P\d+\b")
PROMOTION_RE = re.compile(r"\bPROMOTE-\d{8}-\d{6}-[0-9a-fA-F]{8}\b")
BACKTEST_RE = re.compile(r"\bBT-\d{8}-\d{6}-[0-9a-fA-F]{8}\b")
JOB_RE = re.compile(r"\b\d{8}_\d{6}_[0-9a-fA-F]{8}\b")
PATH_RE = re.compile(r"(?:\b[A-Za-z]:\\|\\\\|/etc/|/home/|/Users/)")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _compact_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if job is None:
        return None
    winner = job.get("winner") if isinstance(job.get("winner"), dict) else None
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "active": bool(job["active"]),
        "current_round": int(job["current_round"]),
        "max_rounds": int(job["max_rounds"]),
        "terminal_result": job.get("terminal_result"),
        "first_blocker": job.get("first_blocker"),
        "winner": (
            {
                "round": winner.get("round"),
                "mt5_pass": winner.get("mt5_pass"),
                "profit_factor": winner.get("profit_factor"),
                "recovery_factor": winner.get("recovery_factor"),
                "mean_r": winner.get("mean_r"),
                "weighted_r": winner.get("weighted_r"),
                "trades": winner.get("trades"),
                "minimum_required_trades": winner.get("minimum_required_trades"),
            }
            if winner
            else None
        ),
    }


def _compact_round(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    transition = state.get("scientist_transition")
    scientist = None
    if isinstance(transition, dict):
        decision = transition.get("decision")
        scientist = {
            "status": transition.get("status"),
            "provider_call_state": transition.get("provider_call_state"),
            "confirmed_provider_calls": transition.get("confirmed_provider_calls"),
            "unconfirmed_provider_attempts": transition.get("unconfirmed_provider_attempts"),
            "decision": (
                {
                    "mode": decision.get("mode"),
                    "accepted": decision.get("accepted"),
                    "effective_range_source": decision.get("effective_range_source"),
                    "error_category": decision.get("error_category"),
                }
                if isinstance(decision, dict)
                else None
            ),
        }
    return {
        "round_no": int(row["round_no"]),
        "phase": row["phase"],
        "parsed_passes": row.get("parsed_passes"),
        "eligible_passes": row.get("eligible_passes"),
        "winner_pass": row.get("winner_pass"),
        "scientist_transition": scientist,
    }


def _recent_challengers(path: Path) -> tuple[int, list[dict[str, Any]]]:
    with connect(path) as conn:
        count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers WHERE status='CHALLENGER'"
        ).fetchone()["n"])
        rows = conn.execute(
            """
            SELECT challenger_id,status,source_job_id,source_round,source_pass,
                   created_utc,ea_version,kpi_json,manifest_sha256
            FROM strategy_challengers
            WHERE status='CHALLENGER'
            ORDER BY created_utc DESC, challenger_id DESC
            LIMIT ?
            """,
            (ACTIVE_CHALLENGER_LIMIT,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["kpi"] = json.loads(item.pop("kpi_json"))
        result.append(item)
    return count, result


def _recent_retired_challengers(path: Path) -> tuple[int, list[dict[str, Any]]]:
    with connect(path) as conn:
        count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM strategy_challengers WHERE status='RETIRED'"
        ).fetchone()["n"])
        rows = conn.execute(
            """
            SELECT challenger_id,status,source_job_id,source_round,source_pass,
                   created_utc,retired_utc,ea_version,kpi_json,manifest_sha256
            FROM strategy_challengers
            WHERE status='RETIRED'
            ORDER BY retired_utc DESC, challenger_id DESC
            LIMIT ?
            """,
            (RETIRED_CHALLENGER_LIMIT,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["kpi"] = json.loads(item.pop("kpi_json"))
        result.append(item)
    return count, result


def _compact_backtest(row: dict[str, Any]) -> dict[str, Any]:
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    return {
        "backtest_id": row["backtest_id"],
        "challenger_id": row["challenger_id"],
        "state": row["state"],
        "created_utc": row["created_utc"],
        "started_utc": row.get("started_utc"),
        "completed_utc": row.get("completed_utc"),
        "source_manifest_sha256": row["source_manifest_sha256"],
        "ea_sha256": row["ea_sha256"],
        "set_sha256": row["set_sha256"],
        "ex5_sha256": row.get("ex5_sha256"),
        "report_sha256": row.get("report_sha256"),
        "evidence_path": row["evidence_path"],
        "error": row.get("error"),
        "request": {
            "symbol": request.get("symbol"),
            "relative_symbol": request.get("relative_symbol"),
            "period": request.get("period"),
            "from_date": request.get("from_date"),
            "to_date": request.get("to_date"),
        },
        "result": {
            "execution_truth": result.get("execution_truth"),
            "parameter_mutation": result.get("parameter_mutation"),
            "live_authority": result.get("live_authority"),
        } if result else None,
    }


def _recent_promotions(path: Path) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT promotion_id,previous_champion_id,new_champion_id,state,
                   created_utc,completed_utc
            FROM strategy_promotions
            ORDER BY created_utc DESC, promotion_id DESC
            LIMIT ?
            """,
            (PROMOTION_HISTORY_LIMIT,),
        ).fetchall()
    return [dict(row) for row in rows]


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone()
    return row is not None


def _read_bound_research_json(
    canonical_path: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    candidate = Path(str(canonical_path)).resolve()
    root = RESEARCH_ARTIFACT_ROOT.resolve()
    if not candidate.is_relative_to(root):
        raise RuntimeError("SCIENTIST_RESEARCH_EVIDENCE_PATH_OUTSIDE_AUTHORITY")
    if not candidate.is_file():
        raise RuntimeError("SCIENTIST_RESEARCH_EVIDENCE_MISSING")
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if not expected_sha256 or actual != str(expected_sha256):
        raise RuntimeError("SCIENTIST_RESEARCH_EVIDENCE_HASH_MISMATCH")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("SCIENTIST_RESEARCH_EVIDENCE_INVALID") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("SCIENTIST_RESEARCH_EVIDENCE_INVALID")
    return payload


def _research_evidence(path: Path) -> tuple[str | None, dict[str, Any] | None]:
    with connect(path) as conn:
        if not _table_exists(conn, "research_projects"):
            return None, None
        project = conn.execute(
            """
            SELECT research_id,parent_strategy_id,current_gate,gate_state,
                   training_count,onnx_count,research_challenger_count,
                   champion_mutation,created_utc
            FROM research_projects
            ORDER BY created_utc DESC,research_id DESC
            LIMIT 1
            """
        ).fetchone()
        if project is None:
            return None, None
        project_row = dict(project)
        research_id = str(project_row["research_id"])

        run = None
        if _table_exists(conn, "research_r01_runs"):
            row = conn.execute(
                """
                SELECT run_id,research_id,state,dataset_id,output_manifest_sha,
                       artifact_ids_json,error,created_utc,updated_utc
                FROM research_r01_runs
                WHERE research_id=?
                ORDER BY created_utc DESC,run_id DESC
                LIMIT 1
                """,
                (research_id,),
            ).fetchone()
            run = dict(row) if row is not None else None

        artifact_rows: list[dict[str, Any]] = []
        if (
            run is not None
            and str(run["state"]) in {
                "PASS_WAITING_OWNER",
                "FAIL_WAITING_OWNER",
                "ERROR_WAITING_OWNER",
            }
            and _table_exists(conn, "artifact_registry")
        ):
            artifact_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT artifact_type,canonical_path,sha256,status
                    FROM artifact_registry
                    WHERE owner_type='RESEARCH_R01'
                      AND owner_id=?
                      AND source_type='RESEARCH_R01_RUN'
                      AND source_id=?
                    ORDER BY artifact_id
                    """,
                    (research_id, str(run["run_id"])),
                ).fetchall()
            ]

    current_stage = canonical_research_stage(research_id, path=path)
    facts: dict[str, Any] = {
        "research_id": research_id,
        "parent_strategy_id": project_row["parent_strategy_id"],
        "current_stage": current_stage,
        "historical_r00": {
            "internal_gate": project_row["current_gate"],
            "state": project_row["gate_state"],
        },
        "training_count": int(project_row["training_count"]),
        "onnx_count": int(project_row["onnx_count"]),
        "research_challenger_count": int(project_row["research_challenger_count"]),
        "champion_mutation": project_row["champion_mutation"],
        "r01": None,
    }
    if run is not None:
        r01_summary: dict[str, Any] = {
            "state": run["state"],
            "error": run["error"],
            "created_utc": run["created_utc"],
            "updated_utc": run["updated_utc"],
            "evidence": {},
        }
        allowed_reports = {
            "data_quality_report.json": "data_quality",
            "discovery_label_summary.json": "discovery_label_summary",
            "feature_parity_report.json": "feature_parity",
            "label_manifest.json": "label",
            "dependency_report.json": "dependency",
            "leakage_report.json": "leakage",
            "protected_data_manifest.json": "protected_data",
        }
        by_name = {Path(str(row["canonical_path"])).name: row for row in artifact_rows}
        for filename, key in allowed_reports.items():
            row = by_name.get(filename)
            if row is None or str(row["status"]) != str(run["state"]):
                continue
            payload = _read_bound_research_json(
                str(row["canonical_path"]),
                str(row["sha256"] or ""),
            )
            if key == "data_quality":
                r01_summary["evidence"][key] = {
                    "status": payload.get("status"),
                    "physical_rows": payload.get("physical_rows"),
                    "dataset_start": payload.get("dataset_start"),
                    "dataset_end": payload.get("dataset_end"),
                    "duplicate_timestamps": payload.get("duplicate_timestamps"),
                    "monotonicity": payload.get("monotonicity"),
                    "invalid_ohlc": payload.get("invalid_ohlc"),
                    "non_finite_values": payload.get("non_finite_values"),
                    "missing_source_data": payload.get("missing_source_data"),
                    "mtf_alignment_status": payload.get("mtf_alignment_status"),
                    "relative_symbol_alignment_status": payload.get(
                        "relative_symbol_alignment_status"
                    ),
                    "cp32_completeness": payload.get("cp32_completeness"),
                }
            elif key == "discovery_label_summary":
                r01_summary["evidence"][key] = {
                    "scope": payload.get("scope"),
                    "protected_outcome_rows_included": payload.get(
                        "protected_outcome_rows_included"
                    ),
                    "discovery_from": payload.get("discovery_from"),
                    "discovery_to": payload.get("discovery_to"),
                    "physical_rows": payload.get("physical_rows"),
                    "boundary_safe_physical_rows": payload.get(
                        "boundary_safe_physical_rows"
                    ),
                    "boundary_excluded_from_supervision_rows": payload.get(
                        "boundary_excluded_from_supervision_rows"
                    ),
                    "supervised_rows": payload.get("supervised_rows"),
                    "context_only_rows": payload.get("context_only_rows"),
                    "target_invalid_rows": payload.get("target_invalid_rows"),
                    "ambiguous_rows": payload.get("ambiguous_rows"),
                    "incomplete_horizon_rows": payload.get("incomplete_horizon_rows"),
                    "sell": payload.get("sell"),
                    "skip": payload.get("skip"),
                    "buy": payload.get("buy"),
                    "sell_ratio": payload.get("sell_ratio"),
                    "skip_ratio": payload.get("skip_ratio"),
                    "buy_ratio": payload.get("buy_ratio"),
                    "label_horizon_main_bars": payload.get("label_horizon_main_bars"),
                    "effective_boundary_purge_main_bars": payload.get(
                        "effective_boundary_purge_main_bars"
                    ),
                    "last_eligible_discovery_source_row_id": payload.get(
                        "last_eligible_discovery_source_row_id"
                    ),
                    "latest_eligible_discovery_target_end_source_row_id": payload.get(
                        "latest_eligible_discovery_target_end_source_row_id"
                    ),
                    "locked_oos_first_source_row_id": payload.get(
                        "locked_oos_first_source_row_id"
                    ),
                    "target_overlap_check": payload.get("target_overlap_check"),
                }
            elif key == "feature_parity":
                r01_summary["evidence"][key] = {
                    "status": payload.get("status"),
                    "feature_count": payload.get("feature_count"),
                    "compared_rows": payload.get("compared_rows"),
                    "absolute_tolerance": payload.get("absolute_tolerance"),
                    "max_abs_error": payload.get("max_abs_error"),
                }
            elif key == "label":
                r01_summary["evidence"][key] = {
                    "contract_id": payload.get("contract_id"),
                    "classes": payload.get("classes"),
                    "thresholds": payload.get("thresholds"),
                    "ambiguous_same_bar_policy": payload.get("ambiguous_same_bar_policy"),
                    "incomplete_horizon_policy": payload.get("incomplete_horizon_policy"),
                }
            elif key == "dependency":
                r01_summary["evidence"][key] = {
                    "label_dependency_main_bars": payload.get("label_dependency_main_bars"),
                    "full_base_dependency_main_bars": payload.get("full_base_dependency_main_bars"),
                    "minimum_legal_purge_main_bars": payload.get("minimum_legal_purge_main_bars"),
                    "minimum_legal_embargo_main_bars": payload.get("minimum_legal_embargo_main_bars"),
                    "future_candidate_rule": payload.get("future_candidate_rule"),
                }
            elif key == "leakage":
                r01_summary["evidence"][key] = {
                    "status": payload.get("status"),
                    "legal_pipeline": payload.get("legal_pipeline"),
                    "deliberately_leaky_pipeline": payload.get("deliberately_leaky_pipeline"),
                    "gate_count": payload.get("gate_count"),
                }
            elif key == "protected_data":
                r01_summary["evidence"][key] = {
                    "authority": payload.get("authority"),
                    "partition_identity": payload.get("partition_identity"),
                    "locked_oos_access": payload.get("locked_oos"),
                    "fresh_forward_access": payload.get("fresh_forward"),
                    "research_memory_adaptive_feedback_from_protected": payload.get(
                        "research_memory_adaptive_feedback_from_protected"
                    ),
                }
        facts["r01"] = r01_summary

    return f"db:research:{research_id}", {
        "kind": "database",
        "title": "Current Research authority",
        "facts": facts,
    }


def _conversation(thread_id: str | None, request_id: str | None, path: Path) -> list[dict[str, str]]:
    if not thread_id:
        return []
    messages = list_messages(thread_id, path=path)
    filtered = [
        message
        for message in messages
        if not (request_id and message.get("request_id") == request_id)
    ]
    bounded = filtered[-CONVERSATION_MESSAGE_LIMIT:]
    return [{"role": str(item["role"]), "content": str(item["content"])} for item in bounded]


def _champion_evidence(champion: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    if champion is None:
        return "db:champion:none", {
            "kind": "database",
            "title": "Current Strategy Champion",
            "facts": {"status": "NONE"},
        }
    ref = f"db:champion:{champion['strategy_id']}"
    return ref, {
        "kind": "database",
        "title": f"Current Strategy Champion — {champion['strategy_id']}",
        "facts": {
            "strategy_id": champion["strategy_id"],
            "status": champion["status"],
            "source_challenger_id": champion["source_challenger_id"],
            "source_job_id": champion["source_job_id"],
            "source_round": champion["source_round"],
            "source_pass": champion["source_pass"],
            "promotion_id": champion["promotion_id"],
            "promoted_utc": champion["promoted_utc"],
            "kpi": champion["kpi"],
        },
    }


def build_scientist_context(
    question: str,
    *,
    thread_id: str | None = None,
    request_id: str | None = None,
    scope: str = "AUTO",
    path: Path = DATABASE_PATH,
) -> dict[str, Any]:
    migrate_m06(path)
    snapshot = load_knowledge()
    evidence = static_evidence(snapshot)

    baseline = read_baseline(path)
    evidence["db:baseline"] = {
        "kind": "database",
        "title": "EA baseline authority",
        "facts": {
            "ea_version": baseline["ea_version"],
            "status": baseline["status"],
            "sha256": baseline["sha256"],
        },
    }

    champion = current_champion(path=path)
    champion_ref, champion_item = _champion_evidence(champion)
    evidence[champion_ref] = champion_item

    latest = latest_job(path=path)
    if latest is not None:
        ref = f"db:optimizer-job:{latest['job_id']}"
        request = latest.get("request") if isinstance(latest.get("request"), dict) else {}
        rounds = get_rounds(str(latest["job_id"]), path=path)[-LATEST_JOB_ROUND_LIMIT:]
        evidence[ref] = {
            "kind": "database",
            "title": f"Latest optimizer job — {latest['job_id']}",
            "facts": {
                **(_compact_job(latest) or {}),
                "request": {
                    "kpi": request.get("kpi"),
                    "trade_sample": request.get("trade_sample"),
                    "optimize_params": request.get("optimize_params"),
                    "scientist_assist": request.get("scientist_assist"),
                },
                "rounds": [_compact_round(row) for row in rounds],
                "round_limit": LATEST_JOB_ROUND_LIMIT,
            },
        }

    active_count, recent = _recent_challengers(path)
    evidence["db:challengers:active-summary"] = {
        "kind": "database",
        "title": "Active Strategy Challengers",
        "facts": {
            "count": active_count,
            "included": len(recent),
            "limit": ACTIVE_CHALLENGER_LIMIT,
            "recent": recent,
        },
    }

    retired_count, retired_recent = _recent_retired_challengers(path)
    evidence["db:challengers:retired-summary"] = {
        "kind": "database",
        "title": "Retired / Archive Strategy Challengers",
        "facts": {
            "count": retired_count,
            "included": len(retired_recent),
            "limit": RETIRED_CHALLENGER_LIMIT,
            "recent": retired_recent,
            "retirement": "NON_DESTRUCTIVE",
        },
    }

    research_ref, research_item = _research_evidence(path)
    if research_ref is not None and research_item is not None:
        evidence[research_ref] = research_item

    promotions = _recent_promotions(path)
    evidence["db:promotions:recent"] = {
        "kind": "database",
        "title": "Recent Strategy promotions",
        "facts": {"included": len(promotions), "limit": PROMOTION_HISTORY_LIMIT, "items": promotions},
    }


    exact_strategies = sorted(set(STRATEGY_RE.findall(question)))
    exact_jobs = sorted(set(JOB_RE.findall(question)))
    exact_promotions = sorted(set(PROMOTION_RE.findall(question)))
    exact_backtests = sorted(set(BACKTEST_RE.findall(question)))
    unresolved: list[str] = []

    for strategy_id in exact_strategies:
        challenger = get_challenger(strategy_id, path=path)
        if challenger is None:
            unresolved.append(strategy_id)
            continue
        evidence[f"db:challenger:{strategy_id}"] = {
            "kind": "database",
            "title": f"Strategy Challenger — {strategy_id}",
            "facts": {
                "challenger_id": challenger["challenger_id"],
                "status": challenger["status"],
                "role_origin": challenger["role_origin"],
                "source_job_id": challenger["source_job_id"],
                "source_round": challenger["source_round"],
                "source_pass": challenger["source_pass"],
                "ea_version": challenger["ea_version"],
                "manifest_sha256": challenger["manifest_sha256"],
                "params": challenger["params"],
                "kpi": challenger["kpi"],
                "hard_gates": challenger["hard_gates"],
                "retired_utc": challenger.get("retired_utc"),
                "backtests": [
                    _compact_backtest(item)
                    for item in list_backtests(
                        strategy_id,
                        limit=BACKTEST_HISTORY_LIMIT,
                        path=path,
                    )
                ],
                "retirements": list_retirements(strategy_id, path=path),
            },
        }

    for backtest_id in exact_backtests:
        backtest = get_backtest(backtest_id, path=path)
        if backtest is None:
            unresolved.append(backtest_id)
            continue
        evidence[f"db:challenger-backtest:{backtest_id}"] = {
            "kind": "database",
            "title": f"Strategy Challenger backtest — {backtest_id}",
            "facts": _compact_backtest(backtest),
        }

    for job_id in exact_jobs:
        job = get_job(job_id, path=path)
        if job is None:
            unresolved.append(job_id)
            continue
        rounds = get_rounds(job_id, path=path)[-EXPLICIT_JOB_ROUND_LIMIT:]
        request = job.get("request") if isinstance(job.get("request"), dict) else {}
        evidence[f"db:optimizer-job:{job_id}"] = {
            "kind": "database",
            "title": f"Optimizer job — {job_id}",
            "facts": {
                **(_compact_job(job) or {}),
                "request": {
                    "kpi": request.get("kpi"),
                    "trade_sample": request.get("trade_sample"),
                    "optimize_params": request.get("optimize_params"),
                    "scientist_assist": request.get("scientist_assist"),
                },
                "rounds": [_compact_round(row) for row in rounds],
                "round_limit": EXPLICIT_JOB_ROUND_LIMIT,
            },
        }


    for promotion_id in exact_promotions:
        promotion = get_promotion(promotion_id, path=path)
        if promotion is None:
            unresolved.append(promotion_id)
            continue
        evidence[f"db:promotion:{promotion_id}"] = {
            "kind": "database",
            "title": f"Strategy promotion — {promotion_id}",
            "facts": {
                "promotion_id": promotion["promotion_id"],
                "challenger_id": promotion["challenger_id"],
                "previous_champion_id": promotion["previous_champion_id"],
                "new_champion_id": promotion["new_champion_id"],
                "state": promotion["state"],
                "created_utc": promotion["created_utc"],
                "completed_utc": promotion["completed_utc"],
            },
        }

    if champion is not None:
        source_id = str(champion["source_challenger_id"])
        source = get_challenger(source_id, path=path)
        if source is not None:
            evidence.setdefault(
                f"db:challenger:{source_id}",
                {
                    "kind": "database",
                    "title": f"Champion source Challenger — {source_id}",
                    "facts": {
                        "challenger_id": source["challenger_id"],
                        "status": source["status"],
                        "source_job_id": source["source_job_id"],
                        "source_round": source["source_round"],
                        "source_pass": source["source_pass"],
                        "ea_version": source["ea_version"],
                        "manifest_sha256": source["manifest_sha256"],
                        "kpi": source["kpi"],
                    },
                },
            )
        promotion_id = str(champion["promotion_id"])
        promotion = get_promotion(promotion_id, path=path)
        if promotion is not None:
            evidence.setdefault(
                f"db:promotion:{promotion_id}",
                {
                    "kind": "database",
                    "title": f"Current Champion promotion — {promotion_id}",
                    "facts": {
                        "promotion_id": promotion["promotion_id"],
                        "previous_champion_id": promotion["previous_champion_id"],
                        "new_champion_id": promotion["new_champion_id"],
                        "state": promotion["state"],
                        "completed_utc": promotion["completed_utc"],
                    },
                },
            )


    availability = []
    if PATH_RE.search(question):
        availability.append("NOT_AVAILABLE_IN_SCIENTIST_CONTEXT")
    if unresolved:
        availability.append("UNKNOWN_PROJECT_ENTITY: " + ", ".join(unresolved))

    selected_scope = str(scope or "AUTO").upper()
    allowed_scopes = {
        "AUTO",
        "STRATEGY",
        "OPTIMIZER",
        "CHALLENGERS",
        "CHAMPION",
        "RESEARCH",
        "PROJECT CONTRACT",
    }
    if selected_scope not in allowed_scopes:
        raise ValueError("SCIENTIST_CONTEXT_SCOPE_INVALID")

    if selected_scope != "AUTO":
        prefixes = {
            "STRATEGY": (
                "db:baseline",
                "db:champion:",
                "db:challenger:",
                "db:challengers:",
                "db:challenger-backtest:",
                "db:promotion:",
                "db:promotions:",
            ),
            "OPTIMIZER": ("db:optimizer-job:",),
            "CHALLENGERS": (
                "db:challenger:",
                "db:challengers:",
                "db:challenger-backtest:",
                "db:champion:",
            ),
            "CHAMPION": ("db:champion:", "db:promotion:", "db:promotions:", "db:challenger:"),
            "RESEARCH": ("db:research:",),
            "PROJECT CONTRACT": (),
        }[selected_scope]
        evidence = {
            ref: item
            for ref, item in evidence.items()
            if ref.startswith("contract:") or any(ref.startswith(prefix) for prefix in prefixes)
        }

    context = {
        "schema": "MAX_REBUILD_SCIENTIST_CONTEXT_V1",
        "knowledge_sha256": knowledge_sha256(snapshot),
        "question": str(question),
        "selected_context": selected_scope,
        "classification_legend": snapshot["classification_legend"],
        "limits": {
            "active_challengers": ACTIVE_CHALLENGER_LIMIT,
            "retired_challengers": RETIRED_CHALLENGER_LIMIT,
            "challenger_backtests": BACKTEST_HISTORY_LIMIT,
            "promotion_history": PROMOTION_HISTORY_LIMIT,
            "conversation_messages": CONVERSATION_MESSAGE_LIMIT,
            "latest_job_rounds": LATEST_JOB_ROUND_LIMIT,
            "explicit_job_rounds": EXPLICIT_JOB_ROUND_LIMIT,
            "answer_max_chars": ANSWER_MAX_CHARS,
        },
        "availability": availability,
        "conversation_context_only": _conversation(thread_id, request_id, path),
        "evidence": evidence,
    }
    context["context_sha256"] = sha256_json(context)
    return context


PROTECTED_TABLES = (
    "ea_baseline",
    "optimizer_jobs",
    "optimizer_rounds",
    "strategy_challengers",
    "strategy_challenger_backtests",
    "strategy_challenger_retirements",
    "strategy_champions",
    "strategy_promotions",
    "artifact_registry",
    "research_authorizations",
    "research_projects",
    "research_gate_events",
    "research_memory_events",
    "research_gate_authorizations_v2",
    "research_r01_runs",
    "research_r01_sources",
)


def _safe_hash(path: Path, *, allowed_root: Path) -> str:
    resolved = path.resolve()
    allowed = allowed_root.resolve()
    if not resolved.is_relative_to(allowed):
        raise RuntimeError("DOMAIN_FINGERPRINT_PATH_OUTSIDE_ALLOWLIST")
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def domain_authority_fingerprint(*, path: Path = DATABASE_PATH) -> dict[str, Any]:
    migrate_m06(path)
    tables: dict[str, list[dict[str, Any]]] = {}
    with connect(path) as conn:
        existing_tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in PROTECTED_TABLES:
            if table not in existing_tables:
                tables[table] = []
                continue
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
            rows.sort(key=lambda item: canonical_json_bytes(item))
            tables[table] = rows
        challenger_paths = [
            str(row["bundle_path"])
            for row in conn.execute(
                "SELECT bundle_path FROM strategy_challengers ORDER BY challenger_id"
            ).fetchall()
        ]

    artifacts: dict[str, str] = {
        str(EA_BASELINE.relative_to(ROOT)).replace("\\", "/"): _safe_hash(EA_BASELINE, allowed_root=ROOT),
        str(EA_MANIFEST.relative_to(ROOT)).replace("\\", "/"): _safe_hash(EA_MANIFEST, allowed_root=ROOT),
    }
    champion_dir = ROOT / "ea" / "champion" / "current"
    if champion_dir.is_dir():
        for name in ("Max_MTF.mq5", "Max_MTF.set"):
            candidate = champion_dir / name
            if candidate.is_file():
                rel = str(candidate.relative_to(ROOT)).replace("\\", "/")
                artifacts[rel] = _safe_hash(candidate, allowed_root=champion_dir)

    challenger_roots = (
        CHALLENGER_ARTIFACT_ROOT.resolve(),
        LEGACY_CHALLENGER_ARTIFACT_ROOT.resolve(),
    )
    for bundle_path in challenger_paths:
        bundle = (ROOT / bundle_path).resolve()
        allowed_root = next(
            (root for root in challenger_roots if bundle.is_relative_to(root)),
            None,
        )
        if allowed_root is None:
            raise RuntimeError("DOMAIN_FINGERPRINT_CHALLENGER_PATH_INVALID")
        for candidate in sorted(bundle.iterdir()):
            if candidate.is_file() and (
                candidate.name == "manifest.json"
                or candidate.suffix.lower() in {".mq5", ".set"}
            ):
                rel = str(candidate.relative_to(ROOT)).replace("\\", "/")
                artifacts[rel] = _safe_hash(candidate, allowed_root=allowed_root)

    payload = {"tables": tables, "artifacts": artifacts}
    return {
        "schema": "MAX_REBUILD_DOMAIN_AUTHORITY_FINGERPRINT_V1",
        "sha256": sha256_json(payload),
        "table_counts": {name: len(rows) for name, rows in tables.items()},
        "artifact_count": len(artifacts),
    }
