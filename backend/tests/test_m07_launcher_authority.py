from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from max_backend.config import EA_VERSION, PROJECT_NAME, STRATEGY_CONTRACT
from max_backend.schema import CURRENT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "launcher_authority.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("m07_launcher_authority", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_authority() -> dict:
    helper = load_helper()
    authority = helper.expected_authority()
    assert authority == {
        "project": PROJECT_NAME,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "ea_version": EA_VERSION,
        "ea_sha256": "827c4caddedbe37081353e08bba35eac5f01e96314dd8650d7ea17ad109ae725",
        "baseline_status": "BASELINE_NOT_CHAMPION",
        "strategy_contract": STRATEGY_CONTRACT,
    }
    return authority


def good_overview(*, champion=None) -> dict:
    authority = current_authority()
    return {
        "project": authority["project"],
        "milestone": "M08",  # display metadata only; launcher does not validate it
        "backend": {"status": "READY"},
        "database": {
            "status": "READY",
            "schema_version": authority["schema_version"],
        },
        "ea_baseline": {
            "ea_version": authority["ea_version"],
            "sha256": authority["ea_sha256"],
            "status": authority["baseline_status"],
        },
        "current_strategy_champion": champion,
        "mt5": {
            "status": "READY_EXECUTABLE_AND_DATA_ROOT",
            "reason": "READY",
        },
    }


def good_scientist() -> dict:
    return {
        "status": "READY",
        "knowledge_status": "READY",
        "read_only": True,
    }


def test_current_runtime_overview_with_no_champion_is_accepted() -> None:
    helper = load_helper()
    assert helper.overview_problem(good_overview()) is None


def test_future_milestone_metadata_does_not_change_launcher_readiness() -> None:
    helper = load_helper()
    overview = good_overview()
    overview["milestone"] = "M09"
    assert helper.overview_problem(overview) is None


def test_stale_schema_is_rejected() -> None:
    helper = load_helper()
    overview = good_overview()
    overview["database"]["schema_version"] = CURRENT_SCHEMA_VERSION - 1
    assert helper.overview_problem(overview) == f"SQLite schema version is not {CURRENT_SCHEMA_VERSION}"


def test_stale_ea_hash_is_rejected() -> None:
    helper = load_helper()
    overview = good_overview()
    overview["ea_baseline"]["ea_version"] = "2.00"
    overview["ea_baseline"]["sha256"] = (
        "9fa6e50231cec4d907b14e3237a118bf3bac22c55682f83ea06303e73c140345"
    )
    assert helper.overview_problem(overview) == "EA baseline version mismatch"

    overview["ea_baseline"]["ea_version"] = EA_VERSION
    assert helper.overview_problem(overview) == "EA baseline SHA-256 mismatch"


def test_wrong_project_is_rejected() -> None:
    helper = load_helper()
    overview = good_overview()
    overview["project"] = "NOT MAX Rebuild"
    assert helper.overview_problem(overview) == "unexpected project identity"


@pytest.mark.parametrize(
    ("champion", "message"),
    [
        ({"strategy_id": "", "status": "CURRENT"}, "Strategy Champion identity is invalid"),
        (
            {"strategy_id": "STRAT-VALID-ID", "status": "HISTORICAL"},
            "Strategy Champion status is not CURRENT",
        ),
    ],
)
def test_invalid_champion_is_rejected(champion: dict, message: str) -> None:
    helper = load_helper()
    assert helper.overview_problem(good_overview(champion=champion)) == message


def test_future_valid_current_champion_is_allowed() -> None:
    helper = load_helper()
    champion = {"strategy_id": "STRAT-FUTURE-001", "status": "CURRENT"}
    assert helper.overview_problem(good_overview(champion=champion)) is None


def test_mt5_not_ready_is_rejected() -> None:
    helper = load_helper()
    overview = good_overview()
    overview["mt5"] = {"status": "UNAVAILABLE", "reason": "DATA_ROOT_MISSING"}
    assert (
        helper.overview_problem(overview)
        == "MT5 preflight is not READY: DATA_ROOT_MISSING"
    )


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"status": "NOT_READY"}, "Scientist status is not READY"),
        (
            {"status": "READY", "knowledge_status": "STALE", "read_only": True},
            "Scientist knowledge is not READY: STALE",
        ),
        (
            {"status": "READY", "knowledge_status": "READY", "read_only": False},
            "Scientist read-only contract is not active",
        ),
    ],
)
def test_scientist_not_ready_or_not_read_only_is_rejected(
    patch: dict, message: str
) -> None:
    helper = load_helper()
    status = good_scientist()
    status.update(patch)
    assert helper.scientist_problem(status) == message


def test_scientist_ready_read_only_is_accepted() -> None:
    helper = load_helper()
    assert helper.scientist_problem(good_scientist()) is None


def test_frontend_proxy_overview_must_match_backend_authority() -> None:
    helper = load_helper()
    backend = good_overview()
    proxy = copy.deepcopy(backend)
    assert helper.proxy_match_problem(backend, proxy) is None

    proxy["current_strategy_champion"] = {
        "strategy_id": "STRAT-OTHER",
        "status": "CURRENT",
    }
    assert (
        helper.proxy_match_problem(backend, proxy)
        == "frontend proxy overview does not match backend authority"
    )
