from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from max_backend import research_r01_service as r01


def _parent() -> dict:
    return {
        "research_id": "RSRCH-CURRENT-R00",
        "research_parent_id": "RPAR-CURRENT-R00",
        "parent_strategy_id": "STRAT-CURRENT-R00",
        "parent_authority_sha256": "a" * 64,
        "r00_parent_manifest_sha256": "b" * 64,
        "strategy_contract": "MAX_TRUE_MTF_DYNAMIC_V1",
        "feature_contract": "CP32_TRUE_MTF_V1",
        "mtf_resolver_version": "TRUE_MTF_LOG_RATIO_V1",
        "strategy_geometry": {"main_tf": "H1"},
    }


def _detail(parent: dict) -> dict:
    parent_manifest = deepcopy(parent)
    parent_manifest["strategy_champion_id"] = parent["parent_strategy_id"]
    parent_manifest["owner_authorization"] = {
        "authorization_id": "RAUTH-R00-CURRENT",
        "authority": "OWNER",
        "h1_minimum_trades_per_month": 4,
    }
    return {
        "research_id": parent["research_id"],
        "research_parent_id": parent["research_parent_id"],
        "parent_strategy_id": parent["parent_strategy_id"],
        "parent_authority_sha256": parent["parent_authority_sha256"],
        "parent_manifest_sha256": parent["r00_parent_manifest_sha256"],
        "parent_manifest": parent_manifest,
        "historical_r00": {"state": "PASS_WAITING_OWNER"},
        "integrity": {"status": "VERIFIED"},
        "research_policy": {
            "sample_policy": {"h1_minimum_sample_trade_policy": {"value": 4}}
        },
        "owner_authorization": {
            "authorization_id": "RAUTH-R00-CURRENT",
            "gate": "R00",
            "action": "START",
            "confirmed": True,
            "expected_parent_strategy_id": parent["parent_strategy_id"],
            "expected_parent_authority_sha256": parent["parent_authority_sha256"],
            "h1_minimum_trades_per_month": 4,
        },
    }


def _install(monkeypatch: pytest.MonkeyPatch, parent: dict, detail: dict | None = None) -> None:
    current_detail = deepcopy(detail if detail is not None else _detail(parent))
    monkeypatch.setattr(r01, "latest_research", lambda **_kwargs: {"research_id": parent["research_id"]})
    monkeypatch.setattr(r01, "research_detail", lambda research_id, **_kwargs: deepcopy(current_detail))
    monkeypatch.setattr(r01, "verify_no_training_side_effects", lambda research_id, **_kwargs: {"status": "PASS"})


def test_current_r00_lineage_is_resolved_from_latest_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    _install(monkeypatch, parent)
    resolved = r01._normalized_r00_parent(path=Path("unused.db"))
    assert resolved["research_id"] == parent["research_id"]
    assert resolved["research_parent_id"] == parent["research_parent_id"]
    assert resolved["parent_strategy_id"] == parent["parent_strategy_id"]
    assert resolved["parent_authority_sha256"] == parent["parent_authority_sha256"]
    assert resolved["historical_r00_h1_minimum_trades_per_month"] == 4


def test_wrong_current_r00_research_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["research_id"] = "RSRCH-WRONG"
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_RESEARCH_ID_MISMATCH"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_wrong_current_r00_parent_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["research_parent_id"] = "RPAR-WRONG"
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_PARENT_ID_MISMATCH"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_wrong_current_r00_strategy_parent_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["parent_strategy_id"] = "STRAT-WRONG"
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_CHAMPION_ID_MISMATCH"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_unaccepted_current_r00_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["historical_r00"]["state"] = "FAIL_WAITING_OWNER"
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_TERMINAL_PASS_REQUIRED"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_tampered_current_r00_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["integrity"]["status"] = "INTEGRITY_FAIL"
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_INTEGRITY_REQUIRED"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_current_r00_parent_authority_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _parent()
    detail = _detail(parent)
    detail["parent_authority_sha256"] = "c" * 64
    _install(monkeypatch, parent, detail)
    with pytest.raises(RuntimeError, match="R01_R00_PARENT_AUTHORITY_MISMATCH"):
        r01._normalized_r00_parent(path=Path("unused.db"))


def test_cross_epoch_r00_request_is_rejected_before_source_resolution() -> None:
    parent = _parent()
    request = {
        "confirmed": True,
        "owner_confirmation": r01.OWNER_R01_CONFIRMATION,
        "research_id": "RSRCH-PREVIOUS-EPOCH",
        "expected_parent_strategy_id": parent["parent_strategy_id"],
        "source_id": "SHOULD-NOT-BE-RESOLVED",
    }
    with pytest.raises(RuntimeError, match="R01_RESEARCH_ID_STALE"):
        r01._validate_start_request(request, parent=parent, path=Path("unused.db"))


def test_retired_previous_epoch_ids_are_not_product_authority() -> None:
    source = Path(r01.__file__).read_text(encoding="utf-8")
    for token in (
        "ACCEPTED_R00_RESEARCH_ID",
        "ACCEPTED_R00_PARENT_ID",
        "ACCEPTED_R00_CHAMPION_ID",
        "RSRCH-653cc6cff84e14f835ef764f",
        "RPAR-df9e3db4563ac1c9f119a850",
        "STRAT-20260924-115344-R01-P8912",
    ):
        assert token not in source
