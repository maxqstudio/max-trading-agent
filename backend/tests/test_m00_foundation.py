import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from max_backend.champion_store import current_champion
from max_backend.config import EA_BASELINE
from max_backend.db import connect, database_status, ensure_baseline_registered, initialize_database
from max_backend.ea import verify_baseline_snapshot
from max_backend.main import app
from max_backend.mt5 import detect_mt5
from max_backend.optimizer_store import migrate_m01


def _fake_install(tmp_path: Path, *, metaeditor: bool = True) -> Path:
    install = tmp_path / "MetaTrader 5"
    install.mkdir(parents=True)
    terminal = install / "terminal64.exe"
    terminal.write_bytes(b"MZ")
    if metaeditor:
        (install / "metaeditor64.exe").write_bytes(b"MZ")
    return terminal


def _fake_data_root(appdata: Path, install: Path, name: str) -> Path:
    data_root = appdata / "MetaQuotes" / "Terminal" / name
    (data_root / "MQL5").mkdir(parents=True)
    (data_root / "origin.txt").write_text(
        str(install.parent.resolve()),
        encoding="utf-16",
    )
    return data_root



def test_fresh_database_bootstrap_records_current_strategy_epoch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fresh" / "max.db"
    initialize_database(database)
    manifest = verify_baseline_snapshot()
    with connect(database) as conn:
        epoch = conn.execute(
            "SELECT value FROM schema_meta WHERE key='strategy_epoch'"
        ).fetchone()
        epoch_sha = conn.execute(
            "SELECT value FROM schema_meta "
            "WHERE key='strategy_epoch_baseline_sha256'"
        ).fetchone()

    assert epoch is not None
    assert epoch["value"] == manifest["strategy_contract"]
    assert epoch_sha is not None
    assert epoch_sha["value"] == manifest["snapshot_sha256"]


def test_existing_database_missing_epoch_is_not_silently_backfilled(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy" / "max.db"
    database.parent.mkdir(parents=True)
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version','1')"
        )
        conn.commit()
    finally:
        conn.close()

    initialize_database(database)

    with connect(database) as conn:
        epoch = conn.execute(
            "SELECT value FROM schema_meta WHERE key='strategy_epoch'"
        ).fetchone()
    assert epoch is None

def test_ea_snapshot_hash_parity() -> None:
    manifest = verify_baseline_snapshot()
    assert manifest["snapshot_sha256"] == "10fadcd986a93cc075e13a6a383f1b00ee5c097c2a70d6edee315668111d5e20"
    assert EA_BASELINE.is_file()


def test_baseline_registration_restart_and_database_ready(tmp_path: Path) -> None:
    db = tmp_path / "max.db"
    first = ensure_baseline_registered(db)
    second = ensure_baseline_registered(db)
    status = database_status(db)

    assert first["status"] == "BASELINE_NOT_CHAMPION"
    assert first["sha256"] == second["sha256"]
    assert status == {
        "status": "READY",
        "schema_version": 1,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }


def test_database_status_is_not_hardcoded_ready(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    assert database_status(missing) == {
        "status": "FAIL",
        "reason": "DATABASE_FILE_MISSING",
    }


def test_foundation_status_accepts_cumulative_schema_versions(tmp_path: Path) -> None:
    for schema_version in (1, 3, 5):
        db = tmp_path / f"schema-{schema_version}.db"
        ensure_baseline_registered(db)
        with connect(db) as conn:
            conn.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(schema_version),),
            )
        assert database_status(db) == {
            "status": "READY",
            "schema_version": schema_version,
            "baseline_status": "BASELINE_NOT_CHAMPION",
        }


def test_foundation_status_rejects_schema_below_minimum_or_missing(tmp_path: Path) -> None:
    below = tmp_path / "schema-0.db"
    ensure_baseline_registered(below)
    with connect(below) as conn:
        conn.execute(
            "UPDATE schema_meta SET value='0' WHERE key='schema_version'"
        )
    result = database_status(below)
    assert result["status"] == "FAIL"
    assert result["reason"] == "SCHEMA_VERSION_MISMATCH"
    assert result["schema_version"] == 0

    missing = tmp_path / "schema-missing.db"
    ensure_baseline_registered(missing)
    with connect(missing) as conn:
        conn.execute("DELETE FROM schema_meta WHERE key='schema_version'")
    assert database_status(missing) == {
        "status": "FAIL",
        "reason": "SCHEMA_VERSION_MISSING",
    }


def test_foundation_status_rejects_missing_baseline_authority(tmp_path: Path) -> None:
    db = tmp_path / "baseline-missing.db"
    ensure_baseline_registered(db)
    with connect(db) as conn:
        conn.execute("DELETE FROM ea_baseline WHERE id=1")
    assert database_status(db) == {
        "status": "FAIL",
        "reason": "EA_BASELINE_ROW_MISSING",
    }


def test_auto_detected_valid_terminal_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _fake_install(tmp_path)
    appdata = tmp_path / "appdata"
    data_root = _fake_data_root(appdata, terminal, "AUTO")
    monkeypatch.delenv("MAX_MT5_TERMINAL", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))

    result = detect_mt5()

    assert result["status"] == "READY_EXECUTABLE_AND_DATA_ROOT"
    assert result["reason"] == "READY"
    assert Path(result["terminal"]) == terminal
    assert Path(result["metaeditor"]) == terminal.with_name("metaeditor64.exe")
    assert Path(result["data_root"]) == data_root
    assert result["terminal_authority"] == "AUTO_DETECTED"


def test_explicit_valid_terminal_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _fake_install(tmp_path)
    appdata = tmp_path / "appdata"
    data_root = _fake_data_root(appdata, terminal, "A")
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(terminal))
    monkeypatch.setenv("APPDATA", str(appdata))

    result = detect_mt5()

    assert result["status"] == "READY_EXECUTABLE_AND_DATA_ROOT"
    assert result["reason"] == "READY"
    assert Path(result["terminal"]) == terminal
    assert Path(result["data_root"]) == data_root


def test_explicit_missing_terminal_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing" / "terminal64.exe"
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(missing))

    result = detect_mt5()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "EXPLICIT_TERMINAL_NOT_FOUND"
    assert result["terminal"] == str(missing)


def test_explicit_wrong_executable_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = tmp_path / "not-terminal.exe"
    wrong.write_bytes(b"MZ")
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(wrong))

    result = detect_mt5()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "WRONG_EXECUTABLE"


def test_metaeditor_missing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _fake_install(tmp_path, metaeditor=False)
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(terminal))

    result = detect_mt5()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "METAEDITOR_NOT_FOUND"


def test_data_root_missing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _fake_install(tmp_path)
    appdata = tmp_path / "empty-appdata"
    appdata.mkdir()
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(terminal))
    monkeypatch.setenv("APPDATA", str(appdata))

    result = detect_mt5()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "DATA_ROOT_MISSING"


def test_data_root_ambiguous_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _fake_install(tmp_path)
    appdata = tmp_path / "appdata"
    _fake_data_root(appdata, terminal, "A")
    _fake_data_root(appdata, terminal, "B")
    monkeypatch.setenv("MAX_MT5_TERMINAL", str(terminal))
    monkeypatch.setenv("APPDATA", str(appdata))

    result = detect_mt5()

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "DATA_ROOT_AMBIGUOUS"


def test_health_and_overview_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    ready_database = {
        "status": "READY",
        "schema_version": 7,
        "baseline_status": "BASELINE_NOT_CHAMPION",
    }
    ready_baseline = {
        "ea_version": "2.11",
        "sha256": "10fadcd986a93cc075e13a6a383f1b00ee5c097c2a70d6edee315668111d5e20",
        "status": "BASELINE_NOT_CHAMPION",
    }
    ready_mt5 = {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": "fixture/terminal64.exe",
        "metaeditor": "fixture/metaeditor64.exe",
        "data_root": "fixture/data-root",
        "execution_truth": "MT5_STRATEGY_TESTER",
        "terminal_authority": "FIXTURE",
    }
    monkeypatch.setattr(
        "max_backend.main.challenger_operations_database_status",
        lambda: ready_database,
    )
    monkeypatch.setattr("max_backend.main.read_baseline", lambda: ready_baseline)
    monkeypatch.setattr("max_backend.main.current_champion", lambda: None)
    monkeypatch.setattr("max_backend.main.latest_job", lambda: None)
    monkeypatch.setattr("max_backend.main.detect_mt5", lambda: ready_mt5)

    async def exercise_api() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "READY"
            assert health.json()["database"] == ready_database

            overview = await client.get("/api/overview")
            assert overview.status_code == 200
            data = overview.json()
            assert data["backend"]["status"] == "READY"
            assert data["database"] == ready_database
            assert data["ea_baseline"] == ready_baseline
            assert data["current_strategy_champion"] is None
            assert data["optimizer_job"] is None
            assert data["mt5"] == ready_mt5

    asyncio.run(exercise_api())


def test_onnx_workspace_read_api_is_truthful_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from max_backend.main import RECOVERY_REQUIRED

    async def exercise_api() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/onnx/workspace")
            assert response.status_code == 200
            payload = response.json()
            assert payload["contract_version"] == "1.0"
            assert payload["source"] == "BACKEND_ONNX_01_SKELETON"
            assert payload["operational_state"] == {
                "status": "NOT_STARTED",
                "availability": "NOT_IMPLEMENTED",
                "persisted": False,
                "cycle_id": None,
                "reason": "ONNX-01 has no operational cycle store; the planning state machine is not persisted operational state.",
            }
            assert payload["dataset"]["dataset_id"] is None
            assert payload["dataset"]["snapshot_id"] is None
            assert payload["dataset"]["status"] == "NOT_STARTED"
            assert payload["dataset"]["availability"] == "UNAVAILABLE"
            assert payload["research_windows"]["items"] is None
            assert payload["scientific_authority"]["status"] == "NOT_PROVEN"
            assert payload["hardware_capacity"]["status"] == "NOT_PROVEN"
            assert payload["discovery"]["qualified_pool"]["candidates"] is None
            assert payload["challenger"]["forward"]["status"] == "NOT_STARTED"
            assert payload["challenger"]["candidates"]["items"] is None
            assert payload["champion"]["identity"] is None
            assert payload["recovery"]["status"] == "UNAVAILABLE"
            assert payload["first_blocker"]["status"] == "NOT_IMPLEMENTED"
            assert [item["page_id"] for item in payload["stage_pages"]] == [
                "data_intake", "discovery", "cpcv", "tournament",
                "monte_carlo", "challenger", "champion",
            ]
            assert all(item["availability"] == "NOT_IMPLEMENTED" for item in payload["stage_pages"])

            onnx_paths = {
                path: operation
                for path, methods in app.openapi()["paths"].items()
                if path.startswith("/api/v1/onnx")
                for method, operation in methods.items()
                if method.lower() == "get"
            }
            assert list(onnx_paths) == ["/api/v1/onnx/workspace"]
            assert set(app.openapi()["paths"]["/api/v1/onnx/workspace"]) == {"get"}

    assert RECOVERY_REQUIRED is False
    monkeypatch.setattr("max_backend.main.challenger_operations_database_status", lambda: (_ for _ in ()).throw(AssertionError("ONNX API read database")))
    monkeypatch.setattr("max_backend.main.read_baseline", lambda: (_ for _ in ()).throw(AssertionError("ONNX API read baseline")))
    asyncio.run(exercise_api())


def test_onnx_workspace_read_api_preserves_global_recovery_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("max_backend.main.RECOVERY_REQUIRED", True)
    monkeypatch.setattr("max_backend.main.RECOVERY_REASON", "DATABASE_UNOPENABLE")

    async def exercise_api() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/onnx/workspace")
            assert response.status_code == 503
            assert response.json() == {
                "detail": "RECOVERY_REQUIRED: application operations are disabled",
                "reason": "DATABASE_UNOPENABLE",
            }

    asyncio.run(exercise_api())
