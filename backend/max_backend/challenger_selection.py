from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .challenger_bundle import (
    _build_bundle,
    _verify_manifest,
    current_baseline_sha256,
)
from .challenger_registry import challenger_id_for_source
from .challenger_store import get_challenger_by_source
from .config import CHALLENGER_ARTIFACT_ROOT, DATABASE_PATH, ROOT
from .workflow_store import (
    get_batch,
    insert_challenger_batch_rows,
    prepare_batch,
    update_batch,
)
from .optimizer_candidates import revalidate_candidate_for_registration
from .optimizer_core import sha256_file
from .optimizer_store import utc_now
from .workflow_contract import ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE


def _normalized_selections(values: Any) -> list[dict[str, int]]:
    if not isinstance(values, list) or not values:
        raise ValueError("at least one qualified candidate selection is required")
    seen: set[tuple[int, int]] = set()
    result: list[dict[str, int]] = []
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError("candidate selection must be an object")
        try:
            round_no = int(raw["round"])
            pass_no = int(raw["pass"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("candidate selection requires integer round/pass") from exc
        if round_no < 1 or pass_no < 0:
            raise ValueError("candidate selection round/pass is invalid")
        key = (round_no, pass_no)
        if key in seen:
            continue
        seen.add(key)
        result.append({"round": round_no, "pass": pass_no})
    result.sort(key=lambda item: (item["round"], item["pass"]))
    if not result:
        raise ValueError("no unique qualified candidates selected")
    return result


def _batch_id(job_id: str, selections: list[dict[str, int]]) -> str:
    canonical = json.dumps(
        {"job_id": str(job_id), "selections": selections},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "CBATCH-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _verify_staged_bundle(
    path: Path,
    *,
    challenger_id: str,
    candidate: dict[str, Any],
) -> dict[str, str]:
    manifest, _hashes = _verify_manifest(path)
    if manifest.get("challenger_id") != challenger_id:
        raise RuntimeError("CHALLENGER_BATCH_MANIFEST_ID_MISMATCH")
    metadata_path = path / "challenger.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("role_origin") != ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE:
        raise RuntimeError("CHALLENGER_BATCH_ROLE_MISMATCH")
    source = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
    expected = {
        "optimizer_job": candidate["job_id"],
        "round": candidate["round"],
        "mt5_pass": candidate["pass"],
        "strategy_contract": candidate["strategy_contract"],
        "strategy_geometry": candidate["strategy_geometry"],
        "xml_sha256": candidate["report_sha256"],
        "sidecar_sha256": candidate["sidecar_sha256"],
    }
    for key, value in expected.items():
        if source.get(key) != value:
            raise RuntimeError(f"CHALLENGER_BATCH_SOURCE_MISMATCH:{key}")
    return {
        "challenger_ea_sha256": sha256_file(
            path / f"Max_Challenger_{challenger_id}.mq5"
        ),
        "set_sha256": sha256_file(
            path / f"Max_Challenger_{challenger_id}.set"
        ),
        "metadata_sha256": sha256_file(metadata_path),
        "manifest_sha256": sha256_file(path / "manifest.json"),
    }


def _source_payload(
    candidate: dict[str, Any],
    *,
    challenger_id: str,
) -> dict[str, Any]:
    request = candidate["request"]
    return {
        "role_origin": ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE,
        "job_id": candidate["job_id"],
        "round": int(candidate["round"]),
        "pass": int(candidate["pass"]),
        "request": request,
        "request_path": Path(candidate["request_path"]),
        "params": candidate["params"],
        "kpi": {
            "profit_factor": candidate["profit_factor"],
            "recovery_factor": candidate["recovery_factor"],
            "mean_r": candidate["mean_r"],
            "weighted_r": candidate["weighted_r"],
            "trades": candidate["trades"],
            "required_trades": candidate["required_trades"],
        },
        "hard_gates": candidate["hard_gates"],
        "candidate": {
            "schema": "MAX_REBUILD_QUALIFIED_CANDIDATE_V1",
            "selection_authority": "OWNER_EXPLICIT_SELECTION",
            "challenger_id": challenger_id,
            "job_id": candidate["job_id"],
            "round": candidate["round"],
            "pass": candidate["pass"],
            "rank_at_validation": candidate["rank"],
            "params": candidate["params"],
            "profit_factor": candidate["profit_factor"],
            "recovery_factor": candidate["recovery_factor"],
            "mean_r": candidate["mean_r"],
            "weighted_r": candidate["weighted_r"],
            "trades": candidate["trades"],
            "required_trades": candidate["required_trades"],
            "hard_gates": candidate["hard_gates"],
            "ea_sha256": candidate["ea_sha256"],
            "strategy_contract": candidate["strategy_contract"],
            "strategy_geometry": candidate["strategy_geometry"],
            "report_sha256": candidate["report_sha256"],
            "sidecar_sha256": candidate["sidecar_sha256"],
        },
        "xml_path": Path(candidate["report_path"]),
        "xml_sha256": candidate["report_sha256"],
        "sidecar_path": Path(candidate["sidecar_path"]),
        "sidecar_sha256": candidate["sidecar_sha256"],
        "passes_path": Path(candidate["passes_path"]),
        "audit_path": Path(candidate["audit_path"]),
        "report_provenance_path": Path(candidate["report_provenance_path"]),
        "run_nonce": int(candidate["run_nonce"]),
        "round_lineage": list(candidate["round_lineage"]),
    }


def create_selected_challengers(
    job_id: str,
    selections: Any,
    *,
    path: Path = DATABASE_PATH,
    artifact_root: Path = CHALLENGER_ARTIFACT_ROOT,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    normalized = _normalized_selections(selections)
    batch_id = _batch_id(job_id, normalized)
    existing_batch = get_batch(batch_id, path=path)
    if existing_batch is not None and existing_batch["state"] == "COMMITTED":
        return existing_batch

    candidates: list[dict[str, Any]] = []
    challenger_ids: list[str] = []

    # Phase 1: all canonical evidence is revalidated before any mutation.
    for selection in normalized:
        candidate = revalidate_candidate_for_registration(
            job_id,
            selection["round"],
            selection["pass"],
            path=path,
        )
        if str(candidate["ea_sha256"]) != current_baseline_sha256():
            raise RuntimeError("QUALIFIED_CANDIDATE_EA_SHA_MISMATCH")
        existing = get_challenger_by_source(
            job_id,
            selection["round"],
            selection["pass"],
            path=path,
        )
        if existing is not None:
            raise RuntimeError(
                "QUALIFIED_CANDIDATE_ALREADY_REGISTERED:"
                + str(existing["challenger_id"])
            )
        cid = challenger_id_for_source(
            job_id,
            selection["round"],
            selection["pass"],
        )
        candidates.append(candidate)
        challenger_ids.append(cid)

    batch = prepare_batch(
        batch_id,
        job_id=job_id,
        selections=normalized,
        challenger_ids=challenger_ids,
        path=path,
    )
    if batch["state"] not in {"PREPARED", "VALIDATED", "STAGED", "FAILED", "RECOVERY_REQUIRED"}:
        raise RuntimeError(f"CHALLENGER_BATCH_NOT_RECOVERABLE:{batch['state']}")

    created_utc = str(batch["created_utc"])
    update_batch(batch_id, "VALIDATED", item_state="VALIDATED", path=path)
    artifact_root.mkdir(parents=True, exist_ok=True)
    staging_root = artifact_root / ".batch-staging" / batch_id
    staging_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    final_committed = any(
        (artifact_root / challenger_id).exists()
        for challenger_id in challenger_ids
    )
    try:
        for candidate, challenger_id in zip(
            candidates,
            challenger_ids,
            strict=True,
        ):
            final_bundle = artifact_root / challenger_id
            staged_bundle = staging_root / challenger_id
            source = _source_payload(candidate, challenger_id=challenger_id)
            if final_bundle.exists():
                hashes = _verify_staged_bundle(
                    final_bundle,
                    challenger_id=challenger_id,
                    candidate=candidate,
                )
            else:
                if staged_bundle.exists():
                    _verify_staged_bundle(
                        staged_bundle,
                        challenger_id=challenger_id,
                        candidate=candidate,
                    )
                else:
                    _build_bundle(
                        challenger_id,
                        created_utc,
                        source,
                        staged_bundle,
                    )
                hashes = _verify_staged_bundle(
                    staged_bundle,
                    challenger_id=challenger_id,
                    candidate=candidate,
                )

            rows.append(
                {
                    "challenger_id": challenger_id,
                    "source_job_id": job_id,
                    "source_round": candidate["round"],
                    "source_pass": candidate["pass"],
                    "created_utc": created_utc,
                    "ea_version": candidate["request"]["ea"]["version"],
                    "baseline_ea_sha256": current_baseline_sha256(),
                    **hashes,
                    "bundle_path": (
                        artifact_root / challenger_id
                    ).resolve().relative_to(project_root.resolve()).as_posix(),
                    "params": candidate["params"],
                    "kpi": source["kpi"],
                    "hard_gates": candidate["hard_gates"],
                    "source_request": candidate["request"],
                    "candidate": source["candidate"],
                    "provenance": {
                        "batch_id": batch_id,
                        "selection_authority": "OWNER_EXPLICIT_SELECTION",
                        "report_sha256": candidate["report_sha256"],
                        "sidecar_sha256": candidate["sidecar_sha256"],
                        "round_lineage": candidate["round_lineage"],
                    },
                    "winning_xml_sha256": candidate["report_sha256"],
                    "winning_sidecar_sha256": candidate["sidecar_sha256"],
                }
            )

        update_batch(batch_id, "STAGED", item_state="STAGED", path=path)

        # Phase 2: commit all prepared directories before one atomic DB mutation.
        for candidate, challenger_id in zip(
            candidates,
            challenger_ids,
            strict=True,
        ):
            final_bundle = artifact_root / challenger_id
            staged_bundle = staging_root / challenger_id
            if final_bundle.exists():
                _verify_staged_bundle(
                    final_bundle,
                    challenger_id=challenger_id,
                    candidate=candidate,
                )
                continue
            if not staged_bundle.is_dir():
                raise RuntimeError("CHALLENGER_BATCH_STAGING_MISSING")
            os.replace(staged_bundle, final_bundle)
            final_committed = True
            _verify_staged_bundle(
                final_bundle,
                challenger_id=challenger_id,
                candidate=candidate,
            )

        challengers = insert_challenger_batch_rows(batch_id, rows, path=path)
        shutil.rmtree(staging_root, ignore_errors=True)
        parent = staging_root.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        result = get_batch(batch_id, path=path)
        if result is None or result["state"] != "COMMITTED":
            raise RuntimeError("CHALLENGER_BATCH_COMMIT_NOT_DURABLE")
        return {
            **result,
            "challengers": challengers,
        }
    except Exception as exc:
        state = "RECOVERY_REQUIRED" if final_committed else "FAILED"
        try:
            update_batch(batch_id, state, error=str(exc), path=path)
        except Exception:
            pass
        if not final_committed:
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
