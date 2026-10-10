from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import ROOT

KNOWLEDGE_PATH = ROOT / "scientist" / "knowledge" / "phase1_knowledge.json"
KNOWLEDGE_SCHEMA = "MAX_REBUILD_PHASE1_SCIENTIST_KNOWLEDGE_V1"
CLASSIFICATIONS = (
    "EXISTING",
    "EXTENSION",
    "EXPERIMENT",
    "NEW",
    "CONFLICT",
    "OUTSIDE_CURRENT_CONTRACT",
)
SOURCE_ALLOWLIST = (
    "pyproject.toml",
    "docs/PRD.md",
    "docs/ROADMAP.md",
    "docs/onnx/ONNX_02_DATA_INTAKE.md",
    ".workflow/onnx_data_authority.json",
    "docs/ARCHITECTURE_PHASE1.md",
    "docs/ARTIFACT_CONTROL_PLANE.md",
    "ea/baseline/manifest.json",
    "backend/max_backend/config.py",
    "backend/max_backend/schema.py",
    "backend/max_backend/workflow_contract.py",
    "backend/max_backend/workflow_store.py",
    "backend/max_backend/mtf_geometry.py",
    "backend/max_backend/optimizer_core.py",
    "backend/max_backend/optimizer_store.py",
    "backend/max_backend/optimizer_worker.py",
    "backend/max_backend/optimizer_api.py",
    "backend/max_backend/optimizer_candidates.py",
    "backend/max_backend/challenger_selection.py",
    "backend/max_backend/optimizer_scientist.py",
    "backend/max_backend/optimizer_scientist_transition.py",
    "backend/max_backend/challenger_store.py",
    "backend/max_backend/challenger_bundle.py",
    "backend/max_backend/challenger_operations_store.py",
    "backend/max_backend/challenger_operations.py",
    "backend/max_backend/challenger_api.py",
    "backend/max_backend/backtest_report.py",
    "backend/max_backend/backtest_control.py",
    "backend/max_backend/path_safety.py",
    "backend/max_backend/artifact_control.py",
    "backend/max_backend/artifact_api.py",
    "backend/max_backend/champion_store.py",
    "backend/max_backend/champion_bundle.py",
    "backend/max_backend/promotion_service.py",
    "backend/max_backend/main.py",
    "backend/max_backend/onnx_data_contract.py",
    "backend/max_backend/onnx_data_source.py",
    "backend/max_backend/onnx_data_store.py",
    "backend/max_backend/onnx_data_service.py",
    "backend/max_backend/onnx_data_api.py",
    "frontend/src/onnxDataApi.ts",
    "frontend/src/OnnxDataIntake.tsx",
    "backend/max_backend/scientist_knowledge.py",
    "backend/max_backend/scientist_context.py",
    "backend/max_backend/scientist_store.py",
    "backend/max_backend/scientist_provider.py",
    "backend/max_backend/scientist_chat.py",
    "backend/max_backend/scientist_api.py",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    canonical = text.replace("\r\n", "\n").replace("\r", "\n")
    return _sha256_bytes(canonical.encode("utf-8"))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def load_knowledge(
    *,
    root: Path = ROOT,
    knowledge_path: Path = KNOWLEDGE_PATH,
) -> dict[str, Any]:
    if not knowledge_path.is_file():
        raise RuntimeError("SCIENTIST_KNOWLEDGE_MISSING")
    try:
        snapshot = json.loads(knowledge_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("SCIENTIST_KNOWLEDGE_INVALID") from exc
    if snapshot.get("schema") != KNOWLEDGE_SCHEMA:
        raise RuntimeError("SCIENTIST_KNOWLEDGE_SCHEMA_INVALID")
    manifest = snapshot.get("source_manifest")
    if not isinstance(manifest, list):
        raise RuntimeError("SCIENTIST_KNOWLEDGE_MANIFEST_INVALID")
    paths = tuple(str(item.get("path", "")) for item in manifest if isinstance(item, dict))
    if paths != SOURCE_ALLOWLIST:
        raise RuntimeError("SCIENTIST_KNOWLEDGE_SOURCE_ALLOWLIST_INVALID")
    for item in manifest:
        relative = str(item["path"])
        expected = str(item.get("sha256") or "")
        source = root / relative
        if not source.is_file():
            raise RuntimeError("SCIENTIST_KNOWLEDGE_STALE")
        actual = source_sha256(source)
        if actual != expected:
            raise RuntimeError("SCIENTIST_KNOWLEDGE_STALE")
    return snapshot


def knowledge_sha256(snapshot: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(snapshot))


def knowledge_status(
    *,
    root: Path = ROOT,
    knowledge_path: Path = KNOWLEDGE_PATH,
) -> dict[str, Any]:
    try:
        snapshot = load_knowledge(root=root, knowledge_path=knowledge_path)
        return {
            "status": "READY",
            "knowledge_sha256": knowledge_sha256(snapshot),
            "schema": snapshot["schema"],
            "source_count": len(snapshot["source_manifest"]),
        }
    except RuntimeError as exc:
        code = str(exc)
        return {
            "status": "STALE" if code == "SCIENTIST_KNOWLEDGE_STALE" else "FAIL",
            "reason": code,
        }


def safe_knowledge_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "READY",
        "knowledge_sha256": knowledge_sha256(snapshot),
        "authority_summary": snapshot["authority_summary"],
        "implemented_capabilities": snapshot["implemented_capabilities"],
        "planned_capabilities": snapshot["planned_capabilities"],
        "explicit_deferred_capabilities": snapshot["explicit_deferred_capabilities"],
        "classification_legend": snapshot["classification_legend"],
    }


def static_evidence(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contracts = snapshot.get("contracts")
    if not isinstance(contracts, list):
        raise RuntimeError("SCIENTIST_KNOWLEDGE_CONTRACTS_INVALID")
    result: dict[str, dict[str, Any]] = {}
    for item in contracts:
        if not isinstance(item, dict):
            raise RuntimeError("SCIENTIST_KNOWLEDGE_CONTRACTS_INVALID")
        ref = str(item.get("ref") or "")
        if not ref or ref in result:
            raise RuntimeError("SCIENTIST_KNOWLEDGE_CONTRACT_REF_INVALID")
        result[ref] = {
            "kind": "contract",
            "title": str(item.get("title") or ref),
            "classification": str(item.get("classification") or ""),
            "facts": list(item.get("facts") or []),
        }
    return result
