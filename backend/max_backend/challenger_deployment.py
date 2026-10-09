from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mt5 import detect_mt5
from .optimizer_core import EXPERT_SUBDIR, sha256_file
from .optimizer_runtime import _read_text_flexible, compile_summary


DEPLOYMENT_SCHEMA = "MAX_REBUILD_MT5_CHALLENGER_DEPLOYMENT_V1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _path_identity(value: Any, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError(f"CHALLENGER_MT5_AUTHORITY_MISSING:{field}")
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"CHALLENGER_MT5_AUTHORITY_NOT_ABSOLUTE:{field}")
    return path.resolve()


def resolve_mt5_authority(source_request: dict[str, Any]) -> dict[str, Path]:
    frozen = source_request.get("mt5")
    if not isinstance(frozen, dict):
        raise RuntimeError("CHALLENGER_MT5_AUTHORITY_MISSING")

    live = detect_mt5()
    if live.get("status") != "READY_EXECUTABLE_AND_DATA_ROOT":
        raise RuntimeError(
            "CHALLENGER_MT5_RUNTIME_NOT_READY:"
            + str(live.get("reason") or "UNKNOWN")
        )

    paths: dict[str, Path] = {}
    for field in ("terminal", "metaeditor", "data_root"):
        expected = _path_identity(frozen.get(field), field)
        actual = _path_identity(live.get(field), field)
        if os.path.normcase(str(expected)) != os.path.normcase(str(actual)):
            raise RuntimeError(f"CHALLENGER_MT5_AUTHORITY_STALE:{field}")
        paths[field] = expected

    if not paths["terminal"].is_file():
        raise RuntimeError("CHALLENGER_MT5_TERMINAL_UNAVAILABLE")
    if not paths["metaeditor"].is_file():
        raise RuntimeError("CHALLENGER_METAEDITOR_UNAVAILABLE")

    data_root = paths["data_root"]
    mql5_root = (data_root / "MQL5").resolve()
    if not mql5_root.is_dir() or not mql5_root.is_relative_to(data_root):
        raise RuntimeError("CHALLENGER_MT5_DATA_ROOT_INVALID")
    experts_root = (mql5_root / "Experts").resolve()
    expert_root = (experts_root / EXPERT_SUBDIR).resolve()
    if not experts_root.is_relative_to(mql5_root):
        raise RuntimeError("CHALLENGER_MT5_EXPERTS_PATH_INVALID")
    if not expert_root.is_relative_to(experts_root):
        raise RuntimeError("CHALLENGER_MT5_EXPERTS_PATH_INVALID")
    expert_root.mkdir(parents=True, exist_ok=True)
    paths["expert_root"] = expert_root
    return paths


def resolve_frozen_mt5_expert_root(source_request: dict[str, Any]) -> dict[str, Path]:
    """Resolve the persisted deployment location without requiring a live terminal."""
    frozen = source_request.get("mt5")
    if not isinstance(frozen, dict):
        raise RuntimeError("CHALLENGER_MT5_AUTHORITY_MISSING")
    paths = {
        field: _path_identity(frozen.get(field), field)
        for field in ("terminal", "metaeditor", "data_root")
    }
    data_root = paths["data_root"]
    mql5_root = (data_root / "MQL5").resolve()
    if not mql5_root.is_relative_to(data_root):
        raise RuntimeError("CHALLENGER_MT5_DATA_ROOT_INVALID")
    experts_root = (mql5_root / "Experts").resolve()
    if not experts_root.is_relative_to(mql5_root):
        raise RuntimeError("CHALLENGER_MT5_EXPERTS_PATH_INVALID")
    expert_root = (experts_root / EXPERT_SUBDIR).resolve()
    if not expert_root.is_relative_to(experts_root):
        raise RuntimeError("CHALLENGER_MT5_EXPERTS_PATH_INVALID")
    paths["expert_root"] = expert_root
    return paths


def _deployment_paths(
    *,
    expert_root: Path,
    challenger_id: str,
) -> tuple[Path, str, str]:
    if not _SAFE_ID.fullmatch(str(challenger_id)):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_ID_INVALID")
    challenger_dir = (
        expert_root / "Challengers" / str(challenger_id)
    ).resolve()
    if not challenger_dir.is_relative_to(expert_root.resolve()):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_PATH_INVALID")
    stem = f"Max_Challenger_{challenger_id}"
    return challenger_dir, stem + ".mq5", stem + ".set"


def challenger_deployment_directory(
    *, expert_root: Path, challenger_id: str
) -> Path:
    return _deployment_paths(
        expert_root=expert_root,
        challenger_id=challenger_id,
    )[0]


def inspect_challenger_deployment(
    *,
    challenger_id: str,
    source_identity: dict[str, Any],
    bundle_manifest_sha256: str,
    source_sha256: str,
    set_sha256: str,
    authority: dict[str, Path],
) -> dict[str, Any]:
    final_dir = challenger_deployment_directory(
        expert_root=authority["expert_root"],
        challenger_id=challenger_id,
    )
    if final_dir.is_symlink():
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_SYMLINK_REJECTED")
    present = final_dir.exists()
    if present:
        verify_challenger_deployment(
            final_dir,
            challenger_id=challenger_id,
            source_identity=source_identity,
            bundle_manifest_sha256=bundle_manifest_sha256,
            source_sha256=source_sha256,
            set_sha256=set_sha256,
        )
        if any(item.is_symlink() for item in final_dir.rglob("*")):
            raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_SYMLINK_REJECTED")
    return {"final_dir": final_dir, "present": present}


def _deployment_payload(
    directory: Path,
    *,
    challenger_id: str,
    source_identity: dict[str, Any],
    bundle_manifest_sha256: str,
    source_sha256: str,
    set_sha256: str,
    metaeditor_sha256: str,
    compile_result: dict[str, Any],
) -> dict[str, Any]:
    ea_name = f"Max_Challenger_{challenger_id}.mq5"
    set_name = f"Max_Challenger_{challenger_id}.set"
    ex5 = directory / f"Max_Challenger_{challenger_id}.ex5"
    payload = {
        "schema": DEPLOYMENT_SCHEMA,
        "challenger_id": challenger_id,
        "source_identity": source_identity,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "source_mq5": ea_name,
        "source_mq5_sha256": source_sha256,
        "set_file": set_name,
        "set_sha256": set_sha256,
        "compiled_ex5": ex5.name,
        "compiled_ex5_sha256": sha256_file(ex5),
        "metaeditor_sha256": metaeditor_sha256,
        "compile_summary": compile_result,
        "compiled_utc": datetime.now(timezone.utc).isoformat(),
        "expert_name": (
            f"{EXPERT_SUBDIR}\\Challengers\\{challenger_id}\\"
            f"Max_Challenger_{challenger_id}"
        ),
    }
    manifest = directory / "deployment.json"
    manifest.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def verify_challenger_deployment(
    directory: Path,
    *,
    challenger_id: str,
    source_identity: dict[str, Any],
    bundle_manifest_sha256: str,
    source_sha256: str,
    set_sha256: str,
) -> dict[str, Any]:
    manifest_path = directory / "deployment.json"
    if not manifest_path.is_file():
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_MANIFEST_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_MANIFEST_INVALID") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_MANIFEST_INVALID")
    expected = {
        "schema": DEPLOYMENT_SCHEMA,
        "challenger_id": challenger_id,
        "source_identity": source_identity,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "source_mq5": f"Max_Challenger_{challenger_id}.mq5",
        "source_mq5_sha256": source_sha256,
        "set_file": f"Max_Challenger_{challenger_id}.set",
        "set_sha256": set_sha256,
        "compiled_ex5": f"Max_Challenger_{challenger_id}.ex5",
        "expert_name": (
            f"{EXPERT_SUBDIR}\\Challengers\\{challenger_id}\\"
            f"Max_Challenger_{challenger_id}"
        ),
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_AUTHORITY_MISMATCH")

    ea = directory / f"Max_Challenger_{challenger_id}.mq5"
    set_path = directory / f"Max_Challenger_{challenger_id}.set"
    ex5 = directory / f"Max_Challenger_{challenger_id}.ex5"
    if not ea.is_file() or sha256_file(ea) != source_sha256:
        raise RuntimeError("CHALLENGER_MT5_DEPLOYED_SOURCE_MISMATCH")
    if not set_path.is_file() or sha256_file(set_path) != set_sha256:
        raise RuntimeError("CHALLENGER_MT5_DEPLOYED_SET_MISMATCH")
    if (
        not ex5.is_file()
        or sha256_file(ex5) != str(manifest.get("compiled_ex5_sha256") or "")
    ):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYED_EX5_MISMATCH")
    summary = manifest.get("compile_summary")
    try:
        clean_compile = (
            isinstance(summary, dict)
            and summary.get("found") is True
            and int(summary.get("errors", -1)) == 0
            and int(summary.get("warnings", -1)) == 0
        )
    except (TypeError, ValueError, OverflowError):
        clean_compile = False
    if not clean_compile:
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_COMPILE_NOT_CLEAN")
    return {
        "challenger_id": challenger_id,
        "status": "VERIFIED",
        "expert_name": manifest["expert_name"],
        "source_mq5_sha256": source_sha256,
        "set_sha256": set_sha256,
        "compiled_ex5_sha256": manifest["compiled_ex5_sha256"],
    }


def prepare_challenger_deployment(
    *,
    challenger_id: str,
    source_request: dict[str, Any],
    source_identity: dict[str, Any],
    bundle_manifest_sha256: str,
    source_ea: Path,
    source_set: Path,
    authority: dict[str, Path],
    staging_root: Path,
) -> dict[str, Any]:
    expert_root = authority["expert_root"]
    frozen_mt5 = source_request.get("mt5")
    if not isinstance(frozen_mt5, dict):
        raise RuntimeError("CHALLENGER_MT5_AUTHORITY_MISSING")
    for field in ("terminal", "metaeditor", "data_root"):
        expected_path = _path_identity(frozen_mt5.get(field), field)
        actual_path = _path_identity(authority.get(field), field)
        if os.path.normcase(str(expected_path)) != os.path.normcase(str(actual_path)):
            raise RuntimeError(f"CHALLENGER_MT5_AUTHORITY_MISMATCH:{field}")
    final_dir, ea_name, set_name = _deployment_paths(
        expert_root=expert_root,
        challenger_id=challenger_id,
    )
    source_sha = sha256_file(source_ea)
    set_sha = sha256_file(source_set)
    if final_dir.exists():
        verified = verify_challenger_deployment(
            final_dir,
            challenger_id=challenger_id,
            source_identity=source_identity,
            bundle_manifest_sha256=bundle_manifest_sha256,
            source_sha256=source_sha,
            set_sha256=set_sha,
        )
        return {
            "challenger_id": challenger_id,
            "final_dir": final_dir,
            "staged_dir": None,
            "deployment": {**verified, "status": "VERIFIED_EXISTING"},
            "expected": {
                "source_identity": source_identity,
                "bundle_manifest_sha256": bundle_manifest_sha256,
                "source_sha256": source_sha,
                "set_sha256": set_sha,
            },
        }

    stage_dir = (staging_root / uuid.uuid4().hex).resolve()
    if not stage_dir.is_relative_to(staging_root.resolve()):
        raise RuntimeError("CHALLENGER_MT5_STAGING_PATH_INVALID")
    if stage_dir.exists():
        raise RuntimeError("CHALLENGER_MT5_STAGING_COLLISION")
    stage_dir.mkdir(parents=True, exist_ok=False)
    # Keep compiler input paths short. MT5 data roots can already be long on
    # Windows, and appending a batch ID, challenger ID, and full EA filename
    # can exceed the legacy MAX_PATH limit before the EA is installed.
    staged_ea = stage_dir / "candidate.mq5"
    staged_set = stage_dir / "candidate.set"
    shutil.copy2(source_ea, staged_ea)
    shutil.copy2(source_set, staged_set)
    if sha256_file(staged_ea) != source_sha:
        raise RuntimeError("CHALLENGER_MT5_SOURCE_COPY_HASH_MISMATCH")
    if sha256_file(staged_set) != set_sha:
        raise RuntimeError("CHALLENGER_MT5_SET_COPY_HASH_MISMATCH")

    staged_ex5 = staged_ea.with_suffix(".ex5")
    log_path = stage_dir / "candidate.log"
    command = [
        str(authority["metaeditor"]),
        f"/compile:{staged_ea}",
        "/log",
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_text = _read_text_flexible(log_path)
    if not log_text:
        log_text = (process.stdout or "") + (process.stderr or "")
    summary = compile_summary(log_text)
    if not summary["found"]:
        raise RuntimeError("CHALLENGER_METAEDITOR_COMPILE_SUMMARY_MISSING")
    if int(summary["errors"]) != 0 or int(summary["warnings"]) != 0:
        raise RuntimeError(
            "CHALLENGER_METAEDITOR_COMPILE_NOT_CLEAN:"
            f"errors={summary['errors']}:warnings={summary['warnings']}"
        )
    # The compiler runs in a brand-new, operation-owned staging directory, so
    # an existing EX5 cannot be mistaken for fresh output. Avoid timestamp
    # comparisons here: Windows filesystems may have coarser timestamp
    # resolution than time.time_ns().
    if not staged_ex5.is_file():
        raise RuntimeError("CHALLENGER_METAEDITOR_EX5_MISSING")

    compiled_source = stage_dir / ea_name
    compiled_set = stage_dir / set_name
    ex5 = stage_dir / f"Max_Challenger_{challenger_id}.ex5"
    os.replace(staged_ea, compiled_source)
    os.replace(staged_set, compiled_set)
    os.replace(staged_ex5, ex5)
    compile_result = {
        "found": True,
        "errors": int(summary["errors"]),
        "warnings": int(summary["warnings"]),
        "line": summary["line"],
        "process_returncode": int(process.returncode),
        "returncode_authority": "DIAGNOSTIC_ONLY",
    }
    deployment = _deployment_payload(
        stage_dir,
        challenger_id=challenger_id,
        source_identity=source_identity,
        bundle_manifest_sha256=bundle_manifest_sha256,
        source_sha256=source_sha,
        set_sha256=set_sha,
        metaeditor_sha256=sha256_file(authority["metaeditor"]),
        compile_result=compile_result,
    )
    return {
        "challenger_id": challenger_id,
        "final_dir": final_dir,
        "staged_dir": stage_dir,
        "deployment": {
            "challenger_id": challenger_id,
            "status": "COMPILED_STAGED",
            "expert_name": deployment["expert_name"],
            "source_mq5_sha256": source_sha,
            "set_sha256": set_sha,
            "compiled_ex5_sha256": deployment["compiled_ex5_sha256"],
        },
        "expected": {
            "source_identity": source_identity,
            "bundle_manifest_sha256": bundle_manifest_sha256,
            "source_sha256": source_sha,
            "set_sha256": set_sha,
        },
    }


def commit_challenger_deployment(prepared: dict[str, Any]) -> dict[str, Any]:
    final_dir = Path(prepared["final_dir"])
    expected = prepared["expected"]
    if prepared.get("staged_dir") is None:
        return verify_challenger_deployment(
            final_dir,
            challenger_id=str(prepared["challenger_id"]),
            source_identity=expected["source_identity"],
            bundle_manifest_sha256=expected["bundle_manifest_sha256"],
            source_sha256=expected["source_sha256"],
            set_sha256=expected["set_sha256"],
        )

    staged_dir = Path(prepared["staged_dir"])
    if final_dir.exists():
        verified = verify_challenger_deployment(
            final_dir,
            challenger_id=str(prepared["challenger_id"]),
            source_identity=expected["source_identity"],
            bundle_manifest_sha256=expected["bundle_manifest_sha256"],
            source_sha256=expected["source_sha256"],
            set_sha256=expected["set_sha256"],
        )
        shutil.rmtree(staged_dir)
        return {**verified, "status": "VERIFIED_EXISTING"}

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_dir, final_dir)
    return verify_challenger_deployment(
        final_dir,
        challenger_id=str(prepared["challenger_id"]),
        source_identity=expected["source_identity"],
        bundle_manifest_sha256=expected["bundle_manifest_sha256"],
        source_sha256=expected["source_sha256"],
        set_sha256=expected["set_sha256"],
    )


def quarantine_challenger_deployment(
    *,
    challenger_id: str,
    source_identity: dict[str, Any],
    bundle_manifest_sha256: str,
    source_sha256: str,
    set_sha256: str,
    authority: dict[str, Path],
    staging_root: Path,
    quarantine_path: Path | None = None,
) -> dict[str, Any] | None:
    """Move one verified runtime EA out of MT5's searchable Experts tree."""
    final_dir, _ea_name, _set_name = _deployment_paths(
        expert_root=authority["expert_root"],
        challenger_id=challenger_id,
    )
    if not final_dir.exists():
        if final_dir.is_symlink():
            raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_SYMLINK_REJECTED")
        return None
    verified = verify_challenger_deployment(
        final_dir,
        challenger_id=challenger_id,
        source_identity=source_identity,
        bundle_manifest_sha256=bundle_manifest_sha256,
        source_sha256=source_sha256,
        set_sha256=set_sha256,
    )
    if any(item.is_symlink() for item in final_dir.rglob("*")):
        raise RuntimeError("CHALLENGER_MT5_DEPLOYMENT_SYMLINK_REJECTED")
    quarantine_dir = (
        quarantine_path.resolve()
        if quarantine_path is not None
        else (staging_root / f"removed-{uuid.uuid4().hex}").resolve()
    )
    if not quarantine_dir.is_relative_to(staging_root.resolve()):
        raise RuntimeError("CHALLENGER_MT5_STAGING_PATH_INVALID")
    if quarantine_dir.exists() or quarantine_dir.is_symlink():
        raise RuntimeError("CHALLENGER_MT5_QUARANTINE_PATH_OCCUPIED")
    quarantine_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(final_dir, quarantine_dir)
    verify_challenger_deployment(
        quarantine_dir,
        challenger_id=challenger_id,
        source_identity=source_identity,
        bundle_manifest_sha256=bundle_manifest_sha256,
        source_sha256=source_sha256,
        set_sha256=set_sha256,
    )
    return {
        "challenger_id": challenger_id,
        "original_dir": final_dir,
        "quarantine_dir": quarantine_dir,
        "source_identity": source_identity,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "source_sha256": source_sha256,
        "set_sha256": set_sha256,
        "expert_name": verified["expert_name"],
    }


def restore_quarantined_challenger_deployment(
    quarantine: dict[str, Any] | None,
) -> None:
    if quarantine is None:
        return
    original_dir = Path(quarantine["original_dir"])
    quarantine_dir = Path(quarantine["quarantine_dir"])
    if not quarantine_dir.is_dir():
        raise RuntimeError("CHALLENGER_MT5_QUARANTINE_MISSING")
    if original_dir.exists() or original_dir.is_symlink():
        raise RuntimeError("CHALLENGER_MT5_RESTORE_TARGET_OCCUPIED")
    os.replace(quarantine_dir, original_dir)
    verify_challenger_deployment(
        original_dir,
        challenger_id=str(quarantine["challenger_id"]),
        source_identity=quarantine["source_identity"],
        bundle_manifest_sha256=str(quarantine["bundle_manifest_sha256"]),
        source_sha256=str(quarantine["source_sha256"]),
        set_sha256=str(quarantine["set_sha256"]),
    )


def new_challenger_staging_root(
    *,
    expert_root: Path,
    operation_id: str,
) -> Path:
    if not _SAFE_ID.fullmatch(str(operation_id)):
        raise RuntimeError("CHALLENGER_MT5_OPERATION_ID_INVALID")
    # Keep temporary EA files outside MQL5/Experts so MT5 cannot discover or
    # load a partially compiled candidate while a batch/promotion is in flight.
    mql5_root = expert_root.resolve().parent.parent
    parent = (mql5_root / ".MaxMTF-Staging").resolve()
    if not parent.is_relative_to(mql5_root):
        raise RuntimeError("CHALLENGER_MT5_STAGING_PATH_INVALID")
    parent.mkdir(parents=True, exist_ok=True)
    operation_tag = hashlib.sha256(str(operation_id).encode("utf-8")).hexdigest()[:8]
    stage_root = (parent / f"{operation_tag}-{uuid.uuid4().hex}").resolve()
    if not stage_root.is_relative_to(parent):
        raise RuntimeError("CHALLENGER_MT5_STAGING_PATH_INVALID")
    stage_root.mkdir(parents=True, exist_ok=False)
    return stage_root


def cleanup_challenger_staging_root(stage_root: Path | None) -> None:
    if stage_root is None:
        return
    marker_root = stage_root.parent.resolve()
    resolved = stage_root.resolve()
    if resolved.parent == marker_root and marker_root.name == ".MaxMTF-Staging":
        shutil.rmtree(resolved, ignore_errors=True)
