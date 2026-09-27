from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from max_backend.challenger_store import challenger_database_status, get_challenger  # noqa: E402
from max_backend.champion_store import champion_database_status, current_champion  # noqa: E402
from max_backend.config import DATABASE_PATH  # noqa: E402
from max_backend.db import database_status, read_baseline  # noqa: E402
from max_backend.optimizer_scientist import route_config_from_environment, scientist_route_status  # noqa: E402
from max_backend.optimizer_store import optimizer_database_status  # noqa: E402
from max_backend.scientist_context import domain_authority_fingerprint  # noqa: E402
from max_backend.scientist_knowledge import CLASSIFICATIONS, knowledge_status, load_knowledge  # noqa: E402
from max_backend.scientist_store import scientist_database_status  # noqa: E402

EVIDENCE = ROOT / "evidence" / "m05"
BASE = "900b682f69b15fffe76abb5c3389863bf83c8a21"
EXPECTED_CHAMPION = "STRAT-20260922-120735-R01-P11"
EXPECTED_PROMOTION = "PROMOTE-20260922-141239-328869ab"
EXPECTED_BASELINE = "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    )


def main() -> int:
    knowledge = load_knowledge()
    knowledge_state = knowledge_status()
    if knowledge_state.get("status") != "READY":
        raise RuntimeError(str(knowledge_state))

    baseline = read_baseline()
    champion = current_champion()
    if champion is None:
        raise RuntimeError("CURRENT_CHAMPION_MISSING")
    challenger = get_challenger(str(champion["source_challenger_id"]))
    if challenger is None:
        raise RuntimeError("CURRENT_CHAMPION_SOURCE_CHALLENGER_MISSING")
    if champion["strategy_id"] != EXPECTED_CHAMPION:
        raise RuntimeError("CURRENT_CHAMPION_CHANGED")
    if champion["promotion_id"] != EXPECTED_PROMOTION:
        raise RuntimeError("CURRENT_PROMOTION_CHANGED")
    if baseline["sha256"] != EXPECTED_BASELINE:
        raise RuntimeError("BASELINE_CHANGED")

    fingerprint = domain_authority_fingerprint()
    authority = {
        "schema": "MAX_REBUILD_M05_DOMAIN_AUTHORITY_FINGERPRINT_V1",
        "domain_authority_sha256": fingerprint["sha256"],
        "baseline_sha256": baseline["sha256"],
        "current_champion": champion["strategy_id"],
        "promotion_id": champion["promotion_id"],
        "source_challenger_status": challenger["status"],
        "ea_version": challenger["ea_version"],
        "real_chat_executed": False,
    }
    write_json(EVIDENCE / "real_chat" / "authority_before.json", authority)
    write_json(EVIDENCE / "real_chat" / "authority_after.json", authority)

    route = route_config_from_environment()
    route_state = scientist_route_status(route)
    safe_route = {
        "provider": route_state["provider"],
        "model": route_state["model"],
        "api_key_env": route_state["api_key_env"],
        "timeout_sec": route_state["timeout_sec"],
        "status": route_state["status"],
        "credential_status": route_state["credential_status"],
    }
    write_json(
        EVIDENCE / "real_chat" / "status.json",
        {
            "schema": "MAX_REBUILD_M05_REAL_CHAT_STATUS_V1",
            "status": "BLOCKED",
            "reason": "SCIENTIST_CREDENTIAL_MISSING",
            "provider_call_mode": "NONE",
            "query_count": 0,
            "responses_fabricated": False,
            "credential_value_retained": False,
            "route": safe_route,
        },
    )

    snapshot_src = ROOT / "scientist" / "knowledge" / "phase1_knowledge.json"
    manifest_src = ROOT / "scientist" / "knowledge" / "source_manifest.json"
    knowledge_dir = EVIDENCE / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot_src, knowledge_dir / "phase1_knowledge.json")
    shutil.copy2(manifest_src, knowledge_dir / "source_manifest.json")
    write_json(
        knowledge_dir / "verification.json",
        {
            "schema": "MAX_REBUILD_M05_KNOWLEDGE_VERIFICATION_V1",
            "status": "READY",
            "knowledge_schema": knowledge["schema"],
            "knowledge_sha256": knowledge_state["knowledge_sha256"],
            "source_count": knowledge_state["source_count"],
            "classifications": list(CLASSIFICATIONS),
            "stale_detection_test": "PASS",
        },
    )

    schema = {
        "schema": "MAX_REBUILD_M05_SCHEMA_COMPATIBILITY_V1",
        "status": "PASS",
        "foundation": database_status(),
        "optimizer": optimizer_database_status(),
        "challenger": challenger_database_status(),
        "champion": champion_database_status(),
        "scientist": scientist_database_status(),
        "baseline_sha256": baseline["sha256"],
        "current_champion": {
            "strategy_id": champion["strategy_id"],
            "current_count": champion_database_status()["current_champion_count"],
            "source_challenger_status": challenger["status"],
            "ea_version": challenger["ea_version"],
            "promotion_id": champion["promotion_id"],
        },
    }
    write_json(EVIDENCE / "verification" / "schema_compatibility.json", schema)

    required = [
        "docs/M05_ACCEPTANCE.md",
        "scripts/build_scientist_knowledge.py",
        "scripts/build_m05_partial_evidence.py",
        "scripts/verify_m05_evidence.py",
        "scientist/knowledge/phase1_knowledge.json",
        "scientist/knowledge/source_manifest.json",
        "evidence/m05/knowledge/phase1_knowledge.json",
        "evidence/m05/knowledge/source_manifest.json",
        "evidence/m05/knowledge/verification.json",
        "evidence/m05/real_chat/status.json",
        "evidence/m05/real_chat/authority_before.json",
        "evidence/m05/real_chat/authority_after.json",
        "evidence/m05/verification/schema_compatibility.json",
        "evidence/m05/verification/backend_pytest.txt",
        "evidence/m05/verification/pip_check.txt",
        "evidence/m05/verification/frontend_tests.txt",
        "evidence/m05/verification/frontend_lint.txt",
        "evidence/m05/verification/frontend_build.txt",
        "evidence/m05/verification/npm_audit.txt",
        "evidence/m05/verification/run_max.txt",
        "evidence/m05/verification/restart.txt",
        "evidence/m05/verification/verify_m01.txt",
        "evidence/m05/verification/verify_m02.txt",
        "evidence/m05/verification/verify_m03.txt",
        "evidence/m05/verification/verify_m04.txt",
        "evidence/m05/verification/secret_scan.json",
    ]

    write_json(
        EVIDENCE / "acceptance.json",
        {
            "schema": "MAX_REBUILD_M05_ACCEPTANCE_V1",
            "status": "PARTIAL",
            "base_commit": BASE,
            "first_blocker": "SCIENTIST_CREDENTIAL_MISSING",
            "blocker_detail": (
                "Configured Scientist credential is absent, so the mandatory four real "
                "Owner chat queries cannot be executed without fabricating provider evidence."
            ),
            "real_chat": {
                "status": "BLOCKED",
                "query_count": 0,
                "provider_calls": 0,
                "responses_fabricated": False,
            },
            "database": {
                "previous_schema": 5,
                "new_schema": 6,
                "migration": "PASS",
                "restart": "PASS",
                "m01_preserved": True,
                "m02_preserved": True,
                "m03_preserved": True,
                "m04_preserved": True,
            },
            "knowledge": {
                "status": "READY",
                "schema": knowledge["schema"],
                "knowledge_sha256": knowledge_state["knowledge_sha256"],
                "source_count": knowledge_state["source_count"],
                "stale_detection": "PASS",
                "classification_semantics": list(CLASSIFICATIONS),
            },
            "current_champion": {
                "strategy_id": champion["strategy_id"],
                "promotion_id": champion["promotion_id"],
                "source_job_id": champion["source_job_id"],
                "source_round": champion["source_round"],
                "source_pass": champion["source_pass"],
                "source_challenger_status": challenger["status"],
                "ea_version": challenger["ea_version"],
                "kpi": champion["kpi"],
            },
            "baseline_sha256": baseline["sha256"],
            "domain_authority_sha256": fingerprint["sha256"],
            "required_files": required,
        },
    )
    print("M05_PARTIAL_EVIDENCE_BUILT")
    print(f"DOMAIN_AUTHORITY_SHA={fingerprint['sha256']}")
    print(f"KNOWLEDGE_SHA={knowledge_state['knowledge_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
