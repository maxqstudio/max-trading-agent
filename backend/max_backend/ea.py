from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import EA_BASELINE, EA_MANIFEST, EA_VERSION
from .mtf_geometry import RESOLVER_VERSION, STRATEGY_CONTRACT


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path = EA_MANIFEST) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "MAX_REBUILD_EA_BASELINE_V1":
        raise RuntimeError("Unsupported EA baseline manifest schema")
    return data


def verify_baseline_snapshot(
    ea_path: Path = EA_BASELINE,
    manifest_path: Path = EA_MANIFEST,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if not ea_path.is_file():
        raise FileNotFoundError(f"EA baseline snapshot missing: {ea_path}")
    actual = sha256_file(ea_path)
    expected = str(manifest.get("snapshot_sha256") or "")
    if actual != expected:
        raise RuntimeError("EA baseline snapshot hash mismatch")
    if str(manifest.get("source_sha256") or "") != expected:
        raise RuntimeError("EA source/snapshot parity is not proven")
    if str(manifest.get("ea_version") or "") != EA_VERSION:
        raise RuntimeError("EA baseline semantic version mismatch")
    if str(manifest.get("strategy_contract") or "") != STRATEGY_CONTRACT:
        raise RuntimeError("EA baseline Strategy contract mismatch")
    if str(manifest.get("mtf_resolver_version") or "") != RESOLVER_VERSION:
        raise RuntimeError("EA baseline MTF resolver version mismatch")
    return {**manifest, "verified_sha256": actual}
