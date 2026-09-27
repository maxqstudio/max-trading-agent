from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

import max_backend.research_api as research_api
import max_backend.research_r01_service as research_r01_service
import max_backend.research_service as research_service
from max_backend.challenger_operations_store import migrate_m06
from max_backend.champion_store import migrate_m04
from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.main import app
from max_backend.research_settings import get_research_sample_configuration
from max_backend.scientist_store import migrate_m05
from max_backend.workflow_store import migrate_current


def _fresh_database(tmp_path: Path) -> Path:
    database = tmp_path / "state" / "max.db"
    initialize_database(database)
    ensure_baseline_registered(database)
    migrate_m04(database)
    migrate_m05(database)
    migrate_m06(database)
    migrate_current(database)
    return database


def test_fresh_database_research_read_endpoints_return_blocked_empty_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _fresh_database(tmp_path)
    monkeypatch.setattr(
        research_api,
        "current_research",
        lambda: research_service.current_research(path=database),
    )
    monkeypatch.setattr(
        research_api,
        "r00_preflight",
        lambda: research_service.r00_preflight(path=database),
    )
    monkeypatch.setattr(
        research_api,
        "r01_preflight",
        lambda: research_r01_service.r01_preflight(path=database),
    )
    monkeypatch.setattr(
        research_api,
        "r01_source_overview",
        lambda: research_r01_service.r01_source_overview(path=database),
    )
    monkeypatch.setattr(
        research_api,
        "r01_detail",
        lambda: research_r01_service.r01_detail(path=database),
    )
    monkeypatch.setattr(
        research_api,
        "get_research_sample_configuration",
        lambda: get_research_sample_configuration(path=database),
    )

    async def exercise_api() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            current = await client.get("/api/research/current")
            r00 = await client.get("/api/research/r00/preflight")
            r01 = await client.get("/api/research/r01/preflight")
            source = await client.get("/api/research/r01/source")
            detail = await client.get("/api/research/r01/detail")

        assert [current.status_code, r00.status_code, r01.status_code, source.status_code, detail.status_code] == [
            200,
            200,
            200,
            200,
            200,
        ]
        assert current.json()["status"] == "NOT_STARTED"
        assert current.json()["current"] is None
        assert current.json()["preflight"]["status"] == "BLOCKED"
        assert current.json()["preflight"]["current_champion"] is None
        assert "verified Strategy Champion" in current.json()["owner_view"]["next_step"]
        assert r00.json()["status"] == "BLOCKED"
        assert r00.json()["current_champion"] is None
        assert r01.json()["status"] == "BLOCKED"
        assert r01.json()["research_id"] is None
        assert r01.json()["dataset_status"] == "NOT_STARTED"
        assert source.json()["status"] == "NOT_INITIALIZED"
        assert source.json()["source_count"] == 0
        assert detail.json()["status"] == "NOT_STARTED"
        assert detail.json()["research_id"] is None
        assert detail.json()["model_training"] == 0
        assert detail.json()["onnx"] == 0
        assert detail.json()["research_challenger"] == 0

    asyncio.run(exercise_api())


def test_fresh_research_empty_state_does_not_suppress_existing_authority_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _fresh_database(tmp_path)
    monkeypatch.setattr(
        research_service,
        "current_champion",
        lambda *, path: None,
    )
    monkeypatch.setattr(
        research_service,
        "latest_research",
        lambda *, path: {"research_id": "EXISTING-RESEARCH"},
    )

    def reject_existing_authority(*, path: Path) -> dict:
        raise RuntimeError("R00_CURRENT_CHAMPION_NOT_VERIFIED")

    monkeypatch.setattr(research_service, "_parent_authority", reject_existing_authority)
    with pytest.raises(RuntimeError, match="R00_CURRENT_CHAMPION_NOT_VERIFIED"):
        research_service.r00_preflight(path=database)


def test_empty_read_models_do_not_hide_orphaned_research_authority(
    tmp_path: Path,
) -> None:
    database = _fresh_database(tmp_path)
    with connect(database) as conn:
        conn.execute(
            """
            INSERT INTO research_authorizations(
                authorization_id,gate,action,confirmed,
                expected_parent_strategy_id,expected_parent_authority_sha256,
                cumulative_strategy_e2e_authority,h1_minimum_trades_per_month,
                payload_sha256,authorized_utc
            ) VALUES(
                'ORPHAN-AUTH','R00','START',1,'STRAT-PARENT','parent-sha',
                'OWNER_AUTHORITY',4,'payload-sha','2026-09-27T00:00:00+00:00'
            )
            """
        )

    readers = (
        lambda: research_service.r00_preflight(path=database),
        lambda: research_r01_service.r01_preflight(path=database),
        lambda: research_r01_service.r01_source_overview(path=database),
        lambda: research_r01_service.r01_detail(path=database),
    )
    for read in readers:
        with pytest.raises(RuntimeError, match="RESEARCH_AUTHORITY_WITHOUT_CURRENT_PROJECT"):
            read()
