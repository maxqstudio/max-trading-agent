from __future__ import annotations

import csv
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from max_backend.db import initialize_database
from max_backend.mtf_geometry import resolve_strategy_geometry
from max_backend.research_contract import FEATURE_CONTRACT, stable_hash
from max_backend.research_cp32 import (
    FEATURE_NAMES,
    market_snapshot,
    prepare_role_series,
)
from max_backend.research_dataset import (
    LABEL_CONTRACT_ID,
    MT5_PERIOD_ENUM,
    SOURCE_BUNDLE_SCHEMA,
    SOURCE_PRODUCER_SCHEMA,
    SOURCE_PRODUCER_VERSION,
    _future_bar_outcome,
    build_dataset,
    build_labels,
    dependency_report,
    reproduce_row_cp32,
    sha256_file,
    validate_feature_input_contract,
    validate_source_row_contiguity,
)
from max_backend.research_leakage import (
    assign_protected_partition_rows,
    bind_protected_partition_rows,
    bind_protected_target_dependency_authority,
    boundary_safe_partition_rows,
    dependency_boundary_is_legal,
    partition_supervised_eligible,
    protected_partition_manifest,
    run_adversarial_suite,
    validate_feature_offset,
    validate_preprocessing_fit_scope,
    validate_source_fill_method,
)


ROLE_ORDER = ("context", "structure", "main", "timing")


def _parent() -> dict:
    geometry = resolve_strategy_geometry("H1")
    params = {
        "InpWeightTrend": 1.7,
        "InpWeightRange": 1.7,
        "InpWeightBreakout": 0.2,
        "InpWeightPullback": 1.5,
        "InpWeightSession": 0.3,
        "InpWeightShock": 1.5,
        "InpWeightRelative": 0.8,
        "InpEntryThreshold": 0.18,
        "InpExitReverseThreshold": 0.25,
        "InpMinConsensus": 0.7,
        "InpSL_ATR": 1.0,
        "InpTP_ATR": 1.5,
        "InpMaxHoldBars": 6,
        "InpShockHaltATR": 3.5,
        "InpRelativeLookback": 12,
        "InpMinRelativeCorr": 0.1,
    }
    authority = stable_hash({"parent": "r01-dataset-test"})
    return {
        "research_id": "RSRCH-R01-TEST",
        "research_parent_id": "RPAR-R01-TEST",
        "parent_strategy_id": "STRAT-R01-TEST",
        "parent_authority_sha256": authority,
        "strategy_contract": geometry["contract"],
        "feature_contract": FEATURE_CONTRACT,
        "mtf_resolver_version": geometry["resolver_version"],
        "strategy_geometry": geometry,
        "main_symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "ea_semantic_version": "2.11",
        "ea_sha256": "a" * 64,
        "strategy_parameters": params,
        "deterministic_risk": {
            "risk_percent_equity": 0.5,
            "daily_loss_limit_percent": 3.0,
            "sl_atr": 1.0,
            "tp_atr": 1.5,
            "max_hold_bars": 6,
            "model_owns_risk": False,
        },
        "parent_artifact_hashes": {
            "project_ea_sha256": "a" * 64,
        },
    }


def _bar(open_time: datetime, index: int, *, relative: bool) -> dict:
    drift = 0.012 if not relative else 0.009
    wave = math.sin(index / 9.0) * (0.3 if not relative else 0.25)
    close = 100.0 + drift * index + wave
    open_value = close - math.sin(index / 5.0) * 0.08
    high = max(open_value, close) + 0.20 + abs(math.sin(index / 7.0)) * 0.05
    low = min(open_value, close) - 0.20 - abs(math.cos(index / 8.0)) * 0.05
    return {
        "open_time": open_time,
        "source_open_time": open_time,
        "open": open_value,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": 1000.0 + (index % 37) * 11.0,
        "spread_points": 2.0,
    }


def _role_bars(parent: dict, *, relative: bool) -> dict[str, list[dict]]:
    end = datetime(2025, 6, 1, tzinfo=timezone.utc)
    counts = {
        "context": 260,
        "structure": 780,
        "main": 3120,
        "timing": 9360,
    }
    result: dict[str, list[dict]] = {}
    for role in ROLE_ORDER:
        minutes = int(parent["strategy_geometry"][f"{role}_minutes"])
        count = counts[role]
        start = end - timedelta(minutes=minutes * count)
        result[role] = [
            _bar(start + timedelta(minutes=minutes * index), index, relative=relative)
            for index in range(count)
        ]
    return result


def _write_bars(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "open_time","open","high","low","close","tick_volume","spread_points"
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "open_time": row["open_time"].isoformat(),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "tick_volume": row["tick_volume"],
                "spread_points": row["spread_points"],
            })


def _write_ea_rows(
    path: Path,
    *,
    parent: dict,
    main_roles: dict[str, list[dict]],
    relative_roles: dict[str, list[dict]],
) -> list[dict]:
    prepared = {role: prepare_role_series(main_roles[role]) for role in ROLE_ORDER}
    relative_prepared = {
        role: prepare_role_series(relative_roles[role])
        for role in ROLE_ORDER
    }
    main = main_roles["main"]
    rows: list[dict] = []
    for decision_index in range(2500, 2540):
        decision_time = main[decision_index]["open_time"]
        signal_time = main[decision_index - 1]["open_time"]
        shell = {
            "source_row_id": len(rows),
            "signal_time": signal_time,
            "decision_time": decision_time,
            "symbol": parent["main_symbol"],
            "period": MT5_PERIOD_ENUM["H1"],
        }
        features = reproduce_row_cp32(
            shell,
            prepared=prepared,
            relative_prepared=relative_prepared,
            params=parent["strategy_parameters"],
        )
        main_snapshot = market_snapshot(prepared["main"], decision_index - 1)
        bid = float(main[decision_index]["open"])
        ask = bid + 0.02
        record = {
            "contract": FEATURE_CONTRACT,
            "signal_time": signal_time.isoformat(),
            "decision_bar_time": decision_time.isoformat(),
            "symbol": parent["main_symbol"],
            "period": MT5_PERIOD_ENUM["H1"],
            "open": main_snapshot["open1"],
            "high": main_snapshot["high1"],
            "low": main_snapshot["low1"],
            "close": main_snapshot["close1"],
            "atr": main_snapshot["atr"],
            "decision_bid": bid,
            "decision_ask": ask,
            "spread_points": 2.0,
            "sl_atr": parent["deterministic_risk"]["sl_atr"],
            "tp_atr": parent["deterministic_risk"]["tp_atr"],
            "max_hold_bars": parent["deterministic_risk"]["max_hold_bars"],
            "consensus": 0.5,
        }
        record.update(dict(zip(FEATURE_NAMES, features, strict=True)))
        rows.append(record)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _bundle(tmp_path: Path) -> tuple[Path, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    parent = _parent()
    main_roles = _role_bars(parent, relative=False)
    relative_roles = _role_bars(parent, relative=True)

    role_files: dict[str, dict[str, dict]] = {"main": {}, "relative": {}}
    for side, symbol, source in (
        ("main", parent["main_symbol"], main_roles),
        ("relative", parent["relative_symbol"], relative_roles),
    ):
        for role in ROLE_ORDER:
            path = tmp_path / f"{side}_{role}.csv"
            _write_bars(path, source[role])
            role_files[side][role] = {
                "path": path.name,
                "sha256": sha256_file(path),
                "symbol": symbol,
                "timeframe": parent["strategy_geometry"][f"{role}_tf"],
            }

    ea_path = tmp_path / "ea_cp32.csv"
    _write_ea_rows(
        ea_path,
        parent=parent,
        main_roles=main_roles,
        relative_roles=relative_roles,
    )
    source_identity = {
        "schema": "MAX_RESEARCH_MT5_SOURCE_IDENTITY_R01_V1",
        "provider": "TEST_MT5_PROVIDER",
        "terminal_authority": "TEST_VERIFIED",
        "terminal_name": "MetaTrader 5",
        "terminal_company": "TEST-BROKER",
        "terminal_build": 9999,
        "commondata_authority": "C:/TEST/MetaQuotes/Terminal/Common",
        "account_server": "TEST-FEED",
        "account_company": "TEST-BROKER",
        "main_symbol": {
            "name": parent["main_symbol"],
            "path": "Test\\XAUUSD",
            "digits": 2,
            "point": 0.01,
        },
        "relative_symbol": {
            "name": parent["relative_symbol"],
            "path": "Test\\EURUSD",
            "digits": 5,
            "point": 0.00001,
        },
        "source_timezone": "UTC",
        "source_timezone_authority": "TEST_UTC_EPOCH_AUTHORITY",
    }
    source_identity["source_identity_sha256"] = stable_hash(source_identity)

    bundle = {
        "schema": SOURCE_BUNDLE_SCHEMA,
        "producer_schema": SOURCE_PRODUCER_SCHEMA,
        "producer_version": SOURCE_PRODUCER_VERSION,
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "ea_sha256": parent["ea_sha256"],
        "ea_semantic_version": parent["ea_semantic_version"],
        "feature_contract": FEATURE_CONTRACT,
        "strategy_contract": parent["strategy_contract"],
        "resolver_version": parent["mtf_resolver_version"],
        "strategy_geometry": parent["strategy_geometry"],
        "main_symbol": parent["main_symbol"],
        "relative_symbol": parent["relative_symbol"],
        "source_timezone": "UTC",
        "source_timezone_authority": "TEST_UTC_EPOCH_AUTHORITY",
        "broker": "TEST-BROKER",
        "feed": "TEST-FEED",
        "mt5_source_identity": source_identity,
        "source_identity_sha256": source_identity["source_identity_sha256"],
        "source_time_coverage": {
            "ea_cp32": {
                "rows": 40,
                "start_utc": main_roles["main"][2499]["open_time"].isoformat(),
                "end_utc": main_roles["main"][2538]["open_time"].isoformat(),
            },
            **{
                f"{side}:{role}": {
                    "rows": len(source[role]),
                    "start_utc": source[role][0]["open_time"].isoformat(),
                    "end_utc": source[role][-1]["open_time"].isoformat(),
                }
                for side, source in (
                    ("main", main_roles),
                    ("relative", relative_roles),
                )
                for role in ROLE_ORDER
            },
        },
        "creation_provenance": {
            "producer": "TEST",
            "created_utc": "2026-09-25T00:00:00+00:00",
        },
        "point_size": 0.01,
        "files": {
            "ea_cp32": {
                "path": ea_path.name,
                "sha256": sha256_file(ea_path),
            },
            "role_bars": role_files,
        },
    }
    bundle_path = tmp_path / "source_bundle.json"
    bundle_path.write_text(
        json.dumps(bundle, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return bundle_path, parent


def test_r01_dataset_is_deterministic_and_cp32_exact(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    first = build_dataset(bundle, parent=parent)
    second = build_dataset(bundle, parent=parent)

    assert first["dataset_id"] == second["dataset_id"]
    assert (
        first["dataset_manifest"]["manifest_sha256"]
        == second["dataset_manifest"]["manifest_sha256"]
    )
    assert first["feature_parity_report"]["status"] == "PASS"
    assert first["feature_manifest"]["feature_order"] == list(FEATURE_NAMES)
    assert first["feature_manifest"]["feature_count"] == 32
    assert first["feature_manifest"]["target_derived_features"] == []
    assert first["label_manifest"]["contract_id"] == LABEL_CONTRACT_ID
    assert first["label_manifest"]["thresholds"] is None
    assert first["data_quality_report"]["physical_rows"] == 40
    assert first["data_quality_report"]["supervised_rows"] > 0
    assert first["data_quality_report"]["duplicate_timestamps"] == 0
    assert first["chronology_report"]["status"] == "PASS"


def test_r01_dataset_identity_changes_with_label_data(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    first = build_dataset(bundle, parent=parent)
    changed = deepcopy(parent)
    changed["deterministic_risk"]["tp_atr"] = 0.5

    raw = list(csv.DictReader((tmp_path / "ea_cp32.csv").open(encoding="utf-8")))
    for row in raw:
        row["tp_atr"] = "0.5"
    with (tmp_path / "ea_cp32_changed.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(raw[0]))
        writer.writeheader()
        writer.writerows(raw)

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    manifest["files"]["ea_cp32"] = {
        "path": "ea_cp32_changed.csv",
        "sha256": sha256_file(tmp_path / "ea_cp32_changed.csv"),
    }
    changed_bundle = tmp_path / "source_bundle_changed.json"
    changed_bundle.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    second = build_dataset(changed_bundle, parent=changed)
    assert first["dataset_id"] != second["dataset_id"]
    assert (
        first["data_quality_report"]["label_hash"]
        != second["data_quality_report"]["label_hash"]
    )


def test_r01_declared_source_time_coverage_must_match_files(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["source_time_coverage"]["main:main"]["rows"] += 1
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="R01_SOURCE_TIME_COVERAGE_MISMATCH"):
        build_dataset(bundle, parent=parent)


def test_r01_source_hash_parent_and_timezone_authority_fail_closed(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)

    tampered = tmp_path / "main_timing.csv"
    tampered.write_text(
        tampered.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="R01_SOURCE_HASH_MISMATCH"):
        build_dataset(bundle, parent=parent)

    bundle, parent = _bundle(tmp_path / "fresh")
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["parent_strategy_id"] = "STRAT-WRONG"
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="R01_SOURCE_BUNDLE_PARENT_MISMATCH"):
        build_dataset(bundle, parent=parent)

    bundle, parent = _bundle(tmp_path / "timezone")
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["source_timezone"] = ""
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="R01_SOURCE_TIMEZONE_REQUIRED"):
        build_dataset(bundle, parent=parent)

    bundle, parent = _bundle(tmp_path / "claimed-broker")
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["broker"] = "USER-CLAIMED-BROKER"
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="R01_MT5_SOURCE_IDENTITY_MISMATCH"):
        build_dataset(bundle, parent=parent)


def test_r01_three_class_label_semantics_are_directional_and_threshold_free() -> None:
    parent = _parent()
    parent["deterministic_risk"].update({
        "sl_atr": 1.0,
        "tp_atr": 1.0,
        "max_hold_bars": 1,
    })
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scenarios = [
        (2, 102.0, 100.5, 101.0),  # BUY: long TP only
        (0, 99.5, 98.0, 99.0),     # SELL: short TP only
        (1, 100.4, 99.6, 100.0),   # SKIP: no positive directional edge at exit
    ]
    for expected, high, low, exit_open in scenarios:
        bars = [
            {
                "open_time": start,
                "open": 100.0,
                "high": high,
                "low": low,
                "close": 100.0,
                "tick_volume": 1000.0,
                "spread_points": 0.0,
            },
            {
                "open_time": start + timedelta(hours=1),
                "open": exit_open,
                "high": exit_open,
                "low": exit_open,
                "close": exit_open,
                "tick_volume": 1000.0,
                "spread_points": 0.0,
            },
        ]
        row = {
            "source_row_id": 0,
            "signal_time": start - timedelta(hours=1),
            "decision_time": start,
            "atr": 1.0,
            "decision_bid": 100.0,
            "decision_ask": 100.0,
            "spread_points": 0.0,
        }
        labeled, manifest = build_labels(
            [row],
            main_bars=bars,
            point_size=0.01,
            parent=parent,
        )
        assert labeled[0]["target_valid"] is True
        assert labeled[0]["label"] == expected
        assert manifest["thresholds"] is None
        assert manifest["classes"] == {"0": "SELL", "1": "SKIP", "2": "BUY"}


def test_r01_parent_execution_geometry_mismatch_fails(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    rows = list(csv.DictReader((tmp_path / "ea_cp32.csv").open(encoding="utf-8")))
    rows[0]["sl_atr"] = "99"
    bad = tmp_path / "ea_cp32_bad.csv"
    with bad.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    manifest["files"]["ea_cp32"] = {
        "path": bad.name,
        "sha256": sha256_file(bad),
    }
    bad_bundle = tmp_path / "source_bundle_bad.json"
    bad_bundle.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="R01_EA_SL_GEOMETRY_MISMATCH"):
        build_dataset(bad_bundle, parent=parent)


def test_r01_relative_timestamp_alignment_defect_fails_closed(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    relative_path = tmp_path / "relative_main.csv"
    rows = list(csv.DictReader(relative_path.open(encoding="utf-8")))
    target = 2495
    rows[target]["open_time"] = (
        datetime.fromisoformat(rows[target]["open_time"]) + timedelta(seconds=1)
    ).isoformat()
    with relative_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    manifest["files"]["role_bars"]["relative"]["main"]["sha256"] = sha256_file(relative_path)
    bundle.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="R01_SOURCE_ALIGNMENT_FAIL"):
        build_dataset(bundle, parent=parent)


def test_r01_duplicate_chronology_fails_closed(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    main_path = tmp_path / "main_main.csv"
    rows = list(csv.DictReader(main_path.open(encoding="utf-8")))
    rows.insert(10, deepcopy(rows[10]))
    with main_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    manifest = json.loads(bundle.read_text(encoding="utf-8"))
    manifest["files"]["role_bars"]["main"]["main"]["sha256"] = sha256_file(main_path)
    bundle.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="R01_DUPLICATE_TIMESTAMPS"):
        build_dataset(bundle, parent=parent)


def test_r01_max_hold_time_exit_uses_next_decision_bar_open() -> None:
    future = [
        {
            "open": 100.0,
            "high": 100.4,
            "low": 99.6,
            "close": 100.25,
            "spread_points": 2.0,
        },
        {
            "open": 100.25,
            "high": 100.5,
            "low": 99.8,
            "close": 100.1,
            "spread_points": 2.0,
        },
    ]
    exit_bar = {
        "open": 101.0,
        "high": 101.2,
        "low": 100.8,
        "close": 101.1,
        "spread_points": 2.0,
    }
    long_r, long_ambiguous = _future_bar_outcome(
        future,
        time_exit_bar=exit_bar,
        direction="long",
        entry_bid=100.0,
        entry_ask=100.02,
        stop_distance=2.0,
        take_distance=4.0,
        point_size=0.01,
    )
    short_r, short_ambiguous = _future_bar_outcome(
        future,
        time_exit_bar=exit_bar,
        direction="short",
        entry_bid=100.0,
        entry_ask=100.02,
        stop_distance=2.0,
        take_distance=4.0,
        point_size=0.01,
    )
    assert long_ambiguous is False
    assert short_ambiguous is False
    assert long_r == pytest.approx((101.0 - 100.02) / 2.0)
    assert short_r == pytest.approx((100.0 - 101.02) / 2.0)
    assert long_r != pytest.approx((future[-1]["close"] - 100.02) / 2.0)


def test_r01_label_ambiguity_and_incomplete_tail_preserve_rows() -> None:
    parent = _parent()
    parent["deterministic_risk"].update({"sl_atr": 1.0, "tp_atr": 1.0, "max_hold_bars": 2})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        {
            "open_time": start + timedelta(hours=index),
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "tick_volume": 1000.0,
            "spread_points": 0.0,
        }
        for index in range(4)
    ]
    rows = [
        {
            "source_row_id": 0,
            "signal_time": start,
            "decision_time": start + timedelta(hours=1),
            "atr": 1.0,
            "decision_bid": 100.0,
            "decision_ask": 100.0,
            "spread_points": 0.0,
        },
        {
            "source_row_id": 1,
            "signal_time": start + timedelta(hours=2),
            "decision_time": start + timedelta(hours=3),
            "atr": 1.0,
            "decision_bid": 100.0,
            "decision_ask": 100.0,
            "spread_points": 0.0,
        },
    ]
    labeled, manifest = build_labels(
        rows,
        main_bars=bars,
        point_size=0.01,
        parent=parent,
    )
    assert len(labeled) == 2
    assert labeled[0]["target_reason"] == "AMBIGUOUS_TP_SL_SAME_BAR"
    assert labeled[0]["supervised_eligible"] is False
    assert labeled[1]["target_reason"] == "INCOMPLETE_FUTURE_HORIZON"
    assert labeled[1]["supervised_eligible"] is False
    assert manifest["ambiguous_rows"] == 1
    assert manifest["incomplete_horizon_rows"] == 1


def test_r01_dependency_and_adversarial_suite(tmp_path: Path) -> None:
    bundle, parent = _bundle(tmp_path)
    dataset = build_dataset(bundle, parent=parent)
    start = dataset["data_quality_report"]["dataset_start"]
    end = dataset["data_quality_report"]["dataset_end"]
    rows = dataset["rows"]
    boundary1 = rows[13]["signal_time"].isoformat()
    boundary2 = rows[27]["signal_time"].isoformat()
    protected = protected_partition_manifest(
        discovery_from=start,
        discovery_to=boundary1,
        locked_oos_from=boundary1,
        locked_oos_to=boundary2,
        fresh_forward_from=boundary2,
        fresh_forward_to=end,
    )
    protected = bind_protected_partition_rows(rows, protected)
    protected = bind_protected_target_dependency_authority(
        rows,
        protected,
        dataset["dependency_report"],
    )
    memory_path = tmp_path / "memory.db"
    initialize_database(memory_path)
    report = run_adversarial_suite(
        dataset,
        protected_manifest=protected,
        research_id=parent["research_id"],
        memory_path=memory_path,
    )
    assert report["status"] == "PASS"
    assert report["legal_pipeline"] == "PASS"
    assert report["deliberately_leaky_pipeline"] == "DETECTED_FAIL"
    assert report["gate_count"] == 25
    assert all(item["status"] == "PASS" for item in report["gates"][:24])
    assert report["gates"][24]["status"] == "DETECTED"

    by_attack = {item["attack"]: item for item in report["gates"]}
    future = by_attack["1_FUTURE_PERTURBATION_EVERY_FEATURE_FAMILY"]["evidence"]
    assert future["representative_row_count"] >= 2
    assert future["all_32_features_checked"] is True
    assert set(future["max_abs_change_by_feature"]) == set(FEATURE_NAMES)
    assert future["max_abs_change_by_family"]

    adjacency = by_attack["15_COMPRESSED_INDEX_FAKE_ADJACENCY"]["evidence"]
    assert adjacency["production_validator"] == "validate_source_row_contiguity"
    assert adjacency["gap_rejected"] is True
    assert adjacency["legal_ids"] == [0, 1, 2]
    assert adjacency["attacked_ids"] == [0, 2]

    direct = by_attack["10_DIRECT_TARGET_CONTAMINATION"]["evidence"]
    assert direct["production_validator"] == "validate_feature_input_contract"
    assert direct["rejected_fields"] == {
        "label": True,
        "long_r": True,
        "short_r": True,
    }

    indirect = by_attack["11_INDIRECT_TARGET_DERIVED_FEATURE_CONTAMINATION"]["evidence"]
    assert indirect["row_contract_rejected"] is True
    assert indirect["information_boundary"]["passed"] is True

    locked_memory = by_attack["20_LOCKED_OOS_FEEDBACK_TO_ADAPTIVE_RESEARCH"]["evidence"]
    fresh_memory = by_attack["21_FRESH_FORWARD_FEEDBACK_TO_SAME_RESEARCH"]["evidence"]
    assert locked_memory["rejected"] is True
    assert locked_memory["no_insertion"] is True
    assert fresh_memory["rejected"] is True
    assert fresh_memory["no_insertion"] is True

    discovery_boundary = by_attack["22_DISCOVERY_TO_LOCKED_TARGET_DEPENDENCY"]["evidence"]
    locked_boundary = by_attack["23_LOCKED_TO_FRESH_TARGET_DEPENDENCY"]["evidence"]
    assert discovery_boundary["internal_target_changed"] is True
    assert discovery_boundary["touching_boundary_eligible"] is False
    assert discovery_boundary["strictly_before_boundary_eligible"] is True
    assert discovery_boundary["public_adaptive_projection_invariant"] is True
    assert discovery_boundary["changed_downstream_bar_only"] is True
    assert locked_boundary["internal_target_changed"] is True
    assert locked_boundary["touching_boundary_eligible"] is False
    assert locked_boundary["strictly_before_boundary_eligible"] is True
    assert locked_boundary["public_adaptive_projection_invariant"] is True
    assert locked_boundary["changed_downstream_bar_only"] is True

    dependency = dataset["dependency_report"]
    base = dependency["full_base_dependency_main_bars"]
    assert dependency["minimum_legal_purge_main_bars"] == base
    assert dependency["minimum_legal_embargo_main_bars"] == base
    assert dependency_boundary_is_legal(
        base_dependency=base,
        purge=base,
        embargo=base,
    )
    assert not dependency_boundary_is_legal(
        base_dependency=base,
        purge=base - 1,
        embargo=base,
    )


def test_r01_adversarial_policy_negative_controls() -> None:
    with pytest.raises(ValueError, match="FUTURE_FEATURE_OFFSET"):
        validate_feature_offset(1)
    with pytest.raises(ValueError, match="FIT_SCOPE"):
        validate_preprocessing_fit_scope("LOCKED_OOS")
    with pytest.raises(ValueError, match="SOURCE_FILL"):
        validate_source_fill_method("BACKWARD_FILL_FROM_FUTURE")


def test_feature_input_contract_rejects_direct_and_indirect_target_contamination() -> None:
    base = {
        "source_row_id": 0,
        "signal_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "decision_time": datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        "symbol": "XAUUSD.m",
        "period": MT5_PERIOD_ENUM["H1"],
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "atr": 1.0,
        "decision_bid": 100.5,
        "decision_ask": 100.52,
        "spread_points": 2.0,
        "sl_atr": 1.0,
        "tp_atr": 1.5,
        "max_hold_bars": 6.0,
        "consensus": 0.5,
        **{name: 0.0 for name in FEATURE_NAMES},
    }
    validate_feature_input_contract(base)
    direct = deepcopy(base)
    direct["label"] = 2
    with pytest.raises(RuntimeError, match="DIRECT_TARGET_CONTAMINATION"):
        validate_feature_input_contract(direct)
    indirect = deepcopy(base)
    indirect["future_target_proxy"] = 1.25
    with pytest.raises(RuntimeError, match="FEATURE_INPUT_CONTRACT_EXTRA_FIELD"):
        validate_feature_input_contract(indirect)


def test_source_row_contiguity_rejects_compressed_fake_adjacency() -> None:
    legal = [{"source_row_id": 0}, {"source_row_id": 1}, {"source_row_id": 2}]
    assert validate_source_row_contiguity(legal)["status"] == "PASS"
    compressed = [legal[0], legal[2]]
    with pytest.raises(RuntimeError, match="SOURCE_ROW_ID_GAP:0->2"):
        validate_source_row_contiguity(compressed)


@pytest.mark.parametrize("max_hold", [1, 6, 17, 37])
def test_label_horizon_is_runtime_derived_from_frozen_parent(max_hold: int) -> None:
    parent = _parent()
    parent["deterministic_risk"]["max_hold_bars"] = max_hold
    parent["strategy_parameters"]["InpMaxHoldBars"] = max_hold
    report = dependency_report(parent)
    assert report["label_dependency_main_bars"] == max_hold


def test_protected_partition_exact_boundary_ownership() -> None:
    manifest = protected_partition_manifest(
        discovery_from="2026-01-01T00:00:00+00:00",
        discovery_to="2026-02-01T00:00:00+00:00",
        locked_oos_from="2026-02-01T00:00:00+00:00",
        locked_oos_to="2026-03-01T00:00:00+00:00",
        fresh_forward_from="2026-03-01T00:00:00+00:00",
        fresh_forward_to="2026-04-01T00:00:00+00:00",
    )
    assert manifest["discovery"]["training_access"] is True
    assert manifest["discovery"]["training_access_scope"] == "DISCOVERY_ONLY"
    assert manifest["discovery"]["training_authorization_required"] == (
        "VALIDATED_OWNER_AUTHORIZED_R02_FROZEN_BLOCK"
    )
    assert manifest["locked_oos"]["training_access"] is False
    assert manifest["fresh_forward"]["training_access"] is False
    rows = [
        {"source_row_id": 0, "signal_time": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        {"source_row_id": 1, "signal_time": datetime(2026, 1, 31, tzinfo=timezone.utc)},
        {"source_row_id": 2, "signal_time": datetime(2026, 2, 1, tzinfo=timezone.utc)},
        {"source_row_id": 3, "signal_time": datetime(2026, 3, 1, tzinfo=timezone.utc)},
        {"source_row_id": 4, "signal_time": datetime(2026, 4, 1, tzinfo=timezone.utc)},
    ]
    assignment = assign_protected_partition_rows(rows, manifest)
    assert assignment["boundary_semantics"] == {
        "discovery": "[from,to)",
        "locked_oos": "[from,to)",
        "fresh_forward": "[from,to]",
    }
    assert assignment["overlap_count"] == 0
    assert assignment["unassigned_count"] == 0
    assert assignment["partitions"]["discovery"]["row_count"] == 2
    assert assignment["partitions"]["discovery"]["last_owned_row"]["source_row_id"] == 1
    assert assignment["partitions"]["locked_oos"]["row_count"] == 1
    assert assignment["partitions"]["locked_oos"]["first_owned_row"]["source_row_id"] == 2
    assert assignment["partitions"]["fresh_forward"]["row_count"] == 2
    assert assignment["partitions"]["fresh_forward"]["first_owned_row"]["source_row_id"] == 3
    assert assignment["partitions"]["fresh_forward"]["last_owned_row"]["source_row_id"] == 4
    bound = bind_protected_partition_rows(rows, manifest)
    assert bound["overlap_count"] == 0
    assert bound["unassigned_count"] == 0


def test_r01_discovery_training_access_requires_scoped_owner_authority(
    tmp_path: Path,
) -> None:
    bundle, parent = _bundle(tmp_path)
    dataset = build_dataset(bundle, parent=parent)
    rows = dataset["rows"]
    start = dataset["data_quality_report"]["dataset_start"]
    end = dataset["data_quality_report"]["dataset_end"]
    first_boundary = rows[13]["signal_time"].isoformat()
    second_boundary = rows[27]["signal_time"].isoformat()
    protected = protected_partition_manifest(
        discovery_from=start,
        discovery_to=first_boundary,
        locked_oos_from=first_boundary,
        locked_oos_to=second_boundary,
        fresh_forward_from=second_boundary,
        fresh_forward_to=end,
    )
    protected = bind_protected_partition_rows(rows, protected)
    protected = bind_protected_target_dependency_authority(
        rows,
        protected,
        dataset["dependency_report"],
    )
    protected["discovery"]["training_access"] = False
    memory_path = tmp_path / "memory.db"
    initialize_database(memory_path)

    report = run_adversarial_suite(
        dataset,
        protected_manifest=protected,
        research_id=parent["research_id"],
        memory_path=memory_path,
    )

    assert report["status"] == "FAIL"
    assert report["legal_pipeline"] == "FAIL"
    scope_gate = next(
        item for item in report["gates"]
        if item["attack"] == "24_DISCOVERY_TRAINING_OWNER_SCOPE"
    )
    assert scope_gate["status"] == "FAIL"


def test_protected_target_boundary_uses_original_row_identity_and_exact_old_semantics() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {
            "source_row_id": index,
            "signal_time": start + timedelta(hours=index),
            "label": index % 3,
            "long_r": float(index),
            "short_r": -float(index),
            "target_valid": True,
            "target_reason": "VALID",
            "supervised_eligible": True,
        }
        for index in range(12)
    ]
    manifest = protected_partition_manifest(
        discovery_from=rows[0]["signal_time"].isoformat(),
        discovery_to=rows[6]["signal_time"].isoformat(),
        locked_oos_from=rows[6]["signal_time"].isoformat(),
        locked_oos_to=rows[9]["signal_time"].isoformat(),
        fresh_forward_from=rows[9]["signal_time"].isoformat(),
        fresh_forward_to=rows[-1]["signal_time"].isoformat(),
    )
    bound = bind_protected_partition_rows(rows, manifest)
    dependency = {
        "label_dependency_main_bars": 2,
        "full_base_dependency_main_bars": 2,
        "minimum_legal_purge_main_bars": 2,
        "minimum_legal_embargo_main_bars": 2,
    }
    bound = bind_protected_target_dependency_authority(rows, bound, dependency)

    discovery = bound["target_dependency_authority"]["discovery_to_locked"]
    assert discovery["eligibility_cutoff_source_row_id_exclusive"] == 4
    assert discovery["last_boundary_safe_source_row_id"] == 3
    assert discovery["latest_boundary_safe_target_end_source_row_id"] == 5
    assert discovery["first_downstream_source_row_id"] == 6
    assert discovery["overlap_check"] == "PASS"
    assert [row["source_row_id"] for row in boundary_safe_partition_rows(
        rows, bound, partition="discovery"
    )] == [0, 1, 2, 3]
    assert partition_supervised_eligible(rows[3], bound, partition="discovery") is True
    assert partition_supervised_eligible(rows[4], bound, partition="discovery") is False
    assert partition_supervised_eligible(rows[5], bound, partition="discovery") is False
    assert 3 + 2 < 6
    assert 4 + 2 == 6
    assert 5 + 2 > 6

    locked = bound["target_dependency_authority"]["locked_to_fresh"]
    assert locked["eligibility_cutoff_source_row_id_exclusive"] == 7
    assert locked["last_boundary_safe_source_row_id"] == 6
    assert locked["latest_boundary_safe_target_end_source_row_id"] == 8
    assert locked["first_downstream_source_row_id"] == 9
    assert partition_supervised_eligible(rows[6], bound, partition="locked_oos") is True
    assert partition_supervised_eligible(rows[7], bound, partition="locked_oos") is False

    compressed = [rows[0], rows[3], rows[4], rows[6]]
    assert [row["source_row_id"] for row in compressed] == [0, 3, 4, 6]
    assert partition_supervised_eligible(compressed[2], bound, partition="discovery") is False
    assert (
        bound["target_dependency_authority"]["physical_context_rows_preserved"]
        is True
    )


def test_protected_partition_timezone_naive_input_rejected() -> None:
    with pytest.raises(ValueError, match="PARTITION_TIMEZONE_REQUIRED"):
        protected_partition_manifest(
            discovery_from="2026-01-01T00:00:00",
            discovery_to="2026-02-01T00:00:00+00:00",
            locked_oos_from="2026-02-01T00:00:00+00:00",
            locked_oos_to="2026-03-01T00:00:00+00:00",
            fresh_forward_from="2026-03-01T00:00:00+00:00",
            fresh_forward_to="2026-04-01T00:00:00+00:00",
        )


def test_protected_partitions_require_contiguous_explicit_boundaries() -> None:
    with pytest.raises(ValueError, match="NOT_CONTIGUOUS"):
        protected_partition_manifest(
            discovery_from="2026-01-01T00:00:00+00:00",
            discovery_to="2026-02-01T00:00:00+00:00",
            locked_oos_from="2026-02-02T00:00:00+00:00",
            locked_oos_to="2026-03-01T00:00:00+00:00",
            fresh_forward_from="2026-03-01T00:00:00+00:00",
            fresh_forward_to="2026-04-01T00:00:00+00:00",
        )
