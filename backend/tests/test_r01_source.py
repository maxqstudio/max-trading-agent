from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import max_backend.research_source as source
from max_backend.research_api import R01SourcePrepareRequest, R01StartRequest
from max_backend.db import connect, initialize_database
from max_backend.mtf_geometry import resolve_strategy_geometry
from max_backend.research_contract import FEATURE_CONTRACT, stable_hash
from max_backend.research_dataset import sha256_file
from max_backend.research_store import (
    create_authorization,
    create_research,
    update_gate_state,
)
from max_backend.workflow_store import migrate_current


def _parent() -> dict:
    geometry = resolve_strategy_geometry("H1")
    authority = stable_hash({"source-parent": "r01"})
    return {
        "research_id": "RSRCH-SOURCE-TEST",
        "research_parent_id": "RPAR-SOURCE-TEST",
        "parent_strategy_id": "STRAT-SOURCE-TEST",
        "source_challenger_id": "STRAT-FROZEN-SOURCE",
        "parent_authority_sha256": authority,
        "ea_sha256": "a" * 64,
        "ea_semantic_version": "2.11",
        "feature_contract": FEATURE_CONTRACT,
        "strategy_contract": geometry["contract"],
        "mtf_resolver_version": geometry["resolver_version"],
        "strategy_geometry": geometry,
        "main_symbol": "XAUUSD.m",
        "relative_symbol": "EURUSD.m",
        "strategy_parameters": {
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
        },
        "deterministic_risk": {
            "risk_percent_equity": 0.5,
            "daily_loss_limit_percent": 3.0,
            "sl_atr": 1.0,
            "tp_atr": 1.5,
            "max_hold_bars": 6,
            "model_owns_risk": False,
        },
    }


def _db(tmp_path: Path, parent: dict) -> Path:
    db = tmp_path / "state" / "max.db"
    initialize_database(db)
    migrate_current(db)
    auth = create_authorization(
        {
            "authorization_id": "RAUTH-R00-SOURCE-TEST",
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": parent["parent_strategy_id"],
            "expected_parent_authority_sha256": parent["parent_authority_sha256"],
            "cumulative_strategy_e2e_authority": "OWNER_EXPLICIT_STRATEGY_E2E_SATISFIED",
            "h1_minimum_trades_per_month": 4,
            "payload_sha256": stable_hash({"source-test": True}),
            "authorized_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    create_research(
        {
            "research_id": parent["research_id"],
            "research_parent_id": parent["research_parent_id"],
            "parent_strategy_id": parent["parent_strategy_id"],
            "parent_authority_sha256": parent["parent_authority_sha256"],
            "parent_manifest_path": "artifacts/research/test/parent.json",
            "parent_manifest_sha256": "b" * 64,
            "feature_contract": FEATURE_CONTRACT,
            "current_gate": "R00",
            "gate_state": "STARTING",
            "gate_input_manifest_sha": "c" * 64,
            "gate_output_manifest_sha": None,
            "owner_authorization_id": auth["authorization_id"],
            "authorized_utc": auth["authorized_utc"],
            "hardware_snapshot_path": "artifacts/research/test/hardware.json",
            "hardware_snapshot_sha256": "d" * 64,
            "label_authority": {},
            "candidate_identity_contract": {},
            "artifact_lineage_contract": {},
            "research_policy": {},
            "unresolved_authority": [],
            "candidate_ids": [],
            "qualification_states": {},
            "system_recommendation": None,
            "owner_selected_ids": [],
            "training_count": 0,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "created_utc": "2026-09-25T00:00:00+00:00",
        },
        path=db,
    )
    update_gate_state(parent["research_id"], gate_state="PASS_WAITING_OWNER", path=db)
    return db


def _capture(variant: str = "A"):
    def run(parent, *, staging, from_date, to_date, token):
        role_files = {"main": {}, "relative": {}}
        coverage = {}
        for side, symbol in (
            ("main", parent["main_symbol"]),
            ("relative", parent["relative_symbol"]),
        ):
            for role in ("context", "structure", "main", "timing"):
                item = staging / f"{side}_{role}.csv"
                item.write_text(
                    "open_time,open,high,low,close,tick_volume,spread_points\n"
                    f"2026-01-01T00:00:00+00:00,1,2,0.5,1.5,10,2\n{variant}\n",
                    encoding="utf-8",
                )
                role_files[side][role] = {
                    "path": item.name,
                    "sha256": sha256_file(item),
                    "symbol": symbol,
                    "timeframe": parent["strategy_geometry"][f"{role}_tf"],
                }
                coverage[f"{side}:{role}"] = {
                    "rows": 1,
                    "start_utc": "2026-01-01T00:00:00+00:00",
                    "end_utc": "2026-01-01T00:00:00+00:00",
                }
        ea = staging / "ea_cp32.csv"
        ea.write_text("contract\n" + variant + "\n", encoding="utf-8")
        coverage["ea_cp32"] = {
            "rows": 1,
            "start_utc": "2026-01-01T00:00:00+00:00",
            "end_utc": "2026-01-01T00:00:00+00:00",
        }
        identity = {
            "schema": "MAX_RESEARCH_MT5_SOURCE_IDENTITY_R01_V1",
            "provider": "TEST_MT5_PROVIDER",
            "terminal_authority": "TEST_VERIFIED",
            "terminal_name": "MetaTrader 5",
            "terminal_company": "TEST-BROKER",
            "terminal_build": 9999,
            "commondata_authority": "C:/TEST/MetaQuotes/Terminal/Common",
            "account_server": "TEST-FEED",
            "account_company": "TEST-BROKER",
            "main_symbol": {"name": parent["main_symbol"], "point": 0.01},
            "relative_symbol": {"name": parent["relative_symbol"], "point": 0.00001},
            "source_timezone": "UTC",
            "source_timezone_authority": "METATRADER5_COPY_RATES_UTC_EPOCH",
        }
        identity["source_identity_sha256"] = stable_hash(identity)
        return {
            "broker": "TEST-BROKER",
            "feed": "TEST-FEED",
            "source_timezone": "UTC",
            "point_size": 0.01,
            "identity": identity,
            "role_files": role_files,
            "ea_cp32": {"path": ea.name, "sha256": sha256_file(ea)},
            "coverage": coverage,
            "capture_provenance": {
                "accepted_parent_ea_sha256": parent["ea_sha256"],
                "compiled_parent_ex5_sha256": "1" * 64,
                "parent_compile_log_sha256": "2" * 64,
                "parent_test_set_sha256": "3" * 64,
                "parent_test_ini_sha256": "4" * 64,
                "raw_ea_training_sha256": "5" * 64,
            },
        }
    return run


def _request(parent: dict) -> dict:
    return {
        "research_id": parent["research_id"],
        "expected_parent_strategy_id": parent["parent_strategy_id"],
        "owner_confirmation": source.SOURCE_PREPARE_CONFIRMATION,
        "from_date": "2021-01-01",
        "to_date": "2026-06-30",
        "confirmed": True,
    }


def test_managed_source_identity_is_deterministic_and_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent()
    db = _db(tmp_path, parent)
    managed = tmp_path / "artifacts" / "research_data"
    monkeypatch.setattr(source, "RESEARCH_DATA_ROOT", managed)
    frozen = tmp_path / "frozen_parent.mq5"
    frozen.write_text("// frozen parent\n", encoding="utf-8")
    frozen_set = tmp_path / "frozen_parent.set"
    frozen_set.write_text("InpEntryThreshold=0.2||0.2||0||0.2||N\n", encoding="utf-8")
    monkeypatch.setattr(
        source,
        "_frozen_parent_ea_path",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        source,
        "_frozen_parent_set_path",
        lambda *_args, **_kwargs: frozen_set,
    )

    first = source.prepare_r01_source(
        _request(parent),
        parent=parent,
        path=db,
        capture_fn=_capture("A"),
    )
    second = source.prepare_r01_source(
        _request(parent),
        parent=parent,
        path=db,
        capture_fn=_capture("A"),
    )
    assert first["source_id"] == second["source_id"]
    assert first["bundle_sha256"] == second["bundle_sha256"]
    assert first["created_utc"] == second["created_utc"]
    bundle_path = Path(first["bundle_path"])
    assert bundle_path.resolve().is_relative_to(managed.resolve())

    bundle = source.resolve_prepared_source(
        first["source_id"],
        parent=parent,
        path=db,
    )
    manifest = bundle["loaded"]["manifest"]
    assert manifest["research_id"] == parent["research_id"]
    assert manifest["research_parent_id"] == parent["research_parent_id"]
    assert manifest["parent_strategy_id"] == parent["parent_strategy_id"]
    assert manifest["parent_authority_sha256"] == parent["parent_authority_sha256"]
    assert manifest["ea_sha256"] == parent["ea_sha256"]
    assert manifest["ea_semantic_version"] == parent["ea_semantic_version"]
    assert manifest["source_timezone"] == "UTC"
    assert manifest["source_timezone_authority"] == "METATRADER5_COPY_RATES_UTC_EPOCH"
    assert manifest["broker"] == "TEST-BROKER"
    assert manifest["feed"] == "TEST-FEED"
    assert manifest["point_size"] == 0.01
    assert manifest["creation_provenance"]["producer_version"] == source.SOURCE_PRODUCER_VERSION

    with connect(db) as conn:
        with pytest.raises(Exception, match="R01_SOURCE_IMMUTABLE"):
            conn.execute(
                "UPDATE research_r01_sources SET broker='TAMPERED' WHERE source_id=?",
                (first["source_id"],),
            )
    with connect(db) as conn:
        with pytest.raises(Exception, match="R01_SOURCE_IMMUTABLE"):
            conn.execute(
                "DELETE FROM research_r01_sources WHERE source_id=?",
                (first["source_id"],),
            )

    changed = source.prepare_r01_source(
        _request(parent),
        parent=parent,
        path=db,
        capture_fn=_capture("B"),
    )
    assert changed["source_id"] != first["source_id"]
    assert changed["bundle_sha256"] != first["bundle_sha256"]


def test_managed_source_identity_ignores_volatile_capture_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent()
    db = _db(tmp_path, parent)
    managed = tmp_path / "artifacts" / "research_data"
    monkeypatch.setattr(source, "RESEARCH_DATA_ROOT", managed)
    frozen = tmp_path / "frozen_parent.mq5"
    frozen.write_text("// frozen parent\n", encoding="utf-8")
    frozen_set = tmp_path / "frozen_parent.set"
    frozen_set.write_text("InpEntryThreshold=0.2||0.2||0||0.2||N\n", encoding="utf-8")
    monkeypatch.setattr(
        source,
        "_frozen_parent_ea_path",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        source,
        "_frozen_parent_set_path",
        lambda *_args, **_kwargs: frozen_set,
    )

    first = source.prepare_r01_source(
        _request(parent),
        parent=parent,
        path=db,
        capture_fn=_capture("A"),
    )

    def same_source_different_log(parent, *, staging, from_date, to_date, token):
        result = _capture("A")(
            parent,
            staging=staging,
            from_date=from_date,
            to_date=to_date,
            token=token,
        )
        result["capture_provenance"]["parent_compile_log_sha256"] = "9" * 64
        return result

    second = source.prepare_r01_source(
        _request(parent),
        parent=parent,
        path=db,
        capture_fn=same_source_different_log,
    )
    assert second["source_id"] == first["source_id"]
    assert second["bundle_sha256"] == first["bundle_sha256"]
    assert second["provenance"] == first["provenance"]


def test_managed_source_rejects_unproven_identity_and_external_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent()
    db = _db(tmp_path, parent)
    managed = tmp_path / "artifacts" / "research_data"
    monkeypatch.setattr(source, "RESEARCH_DATA_ROOT", managed)
    frozen = tmp_path / "frozen_parent.mq5"
    frozen.write_text("// frozen parent\n", encoding="utf-8")
    frozen_set = tmp_path / "frozen_parent.set"
    frozen_set.write_text("InpEntryThreshold=0.2||0.2||0||0.2||N\n", encoding="utf-8")
    monkeypatch.setattr(
        source,
        "_frozen_parent_ea_path",
        lambda *_args, **_kwargs: frozen,
    )
    monkeypatch.setattr(
        source,
        "_frozen_parent_set_path",
        lambda *_args, **_kwargs: frozen_set,
    )

    def bad_capture(parent, *, staging, from_date, to_date, token):
        result = _capture("A")(
            parent,
            staging=staging,
            from_date=from_date,
            to_date=to_date,
            token=token,
        )
        result["identity"]["account_server"] = "FORGED-FEED"
        return result

    with pytest.raises(RuntimeError, match="SOURCE_IDENTITY_HASH_INVALID"):
        source.prepare_r01_source(
            _request(parent),
            parent=parent,
            path=db,
            capture_fn=bad_capture,
        )

    external = tmp_path / "external" / "source_bundle.json"
    external.parent.mkdir(parents=True)
    external.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SOURCE_PATH_OUTSIDE_MANAGED_ROOT"):
        source._managed_path(external)



def test_feature_capture_set_preserves_frozen_parent_values() -> None:
    frozen = (
        "InpEntryThreshold=0.18||0.18||0||0.18||N\n"
        "InpMaxDailyLossPct=3\n"
        "InpWriteTrainingData=false\n"
    )
    rendered = source._parent_training_set(
        frozen,
        training_filename="R01_CAPTURE.csv",
    )
    assert "InpEntryThreshold=0.18||0.18||0||0.18||N" in rendered
    assert "InpMaxDailyLossPct=3" in rendered
    assert "InpWriteTrainingData=true" in rendered
    assert "InpTrainingFile=R01_CAPTURE.csv" in rendered
    assert "InpRiskPct=" not in rendered


def test_frozen_parent_ea_resolves_from_retained_source_challenger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent()
    root = tmp_path / "repo"
    bundle = root / "artifacts" / "challengers" / parent["source_challenger_id"]
    bundle.mkdir(parents=True)
    ea = bundle / f"Max_Challenger_{parent['source_challenger_id']}.mq5"
    ea.write_text("// immutable accepted parent\n", encoding="utf-8")
    frozen_set = bundle / f"Max_Challenger_{parent['source_challenger_id']}.set"
    frozen_set.write_text(
        "InpEntryThreshold=0.2||0.2||0||0.2||N\n",
        encoding="utf-8",
    )
    parent["ea_sha256"] = sha256_file(ea)
    monkeypatch.setattr(source, "ROOT", root)
    monkeypatch.setattr(
        source,
        "verify_challenger_bundle",
        lambda challenger_id, **_kwargs: {
            "status": "VERIFIED",
            "challenger_id": challenger_id,
            "bundle_path": str(bundle.relative_to(root)),
            "ea_sha256": parent["ea_sha256"],
            "set_sha256": sha256_file(frozen_set),
        },
    )
    resolved = source._frozen_parent_ea_path(parent, path=tmp_path / "max.db")
    resolved_set = source._frozen_parent_set_path(parent, path=tmp_path / "max.db")
    assert resolved == ea.resolve()
    assert resolved_set == frozen_set.resolve()
    assert sha256_file(resolved) == parent["ea_sha256"]
    assert sha256_file(resolved_set) == sha256_file(frozen_set)


def test_r01_api_rejects_manual_source_authority_fields() -> None:
    prepare = {
        "research_id": "RSRCH-X",
        "expected_parent_strategy_id": "STRAT-X",
        "owner_confirmation": source.SOURCE_PREPARE_CONFIRMATION,
        "from_date": "2021-01-01",
        "to_date": "2026-06-30",
        "confirmed": True,
        "broker": "USER-CLAIMED-BROKER",
    }
    with pytest.raises(ValidationError):
        R01SourcePrepareRequest.model_validate(prepare)

    start = {
        "research_id": "RSRCH-X",
        "expected_parent_strategy_id": "STRAT-X",
        "owner_confirmation": "OWNER_EXPLICIT_R01_START",
        "source_id": "RSRC-X",
        "discovery_from": "2021-01-01T00:00:00+00:00",
        "discovery_to": "2024-01-01T00:00:00+00:00",
        "locked_oos_from": "2024-01-01T00:00:00+00:00",
        "locked_oos_to": "2026-01-01T00:00:00+00:00",
        "fresh_forward_from": "2026-01-01T00:00:00+00:00",
        "fresh_forward_to": "2026-06-30T00:00:00+00:00",
        "confirmed": True,
        "source_bundle_path": "D:/manual/source_bundle.json",
    }
    with pytest.raises(ValidationError):
        R01StartRequest.model_validate(start)
