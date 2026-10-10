from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from max_backend.config import EA_BASELINE, EA_MANIFEST
from max_backend.db import connect
from max_backend.main import app
from max_backend.onnx_data_api import get_data_service
from max_backend.onnx_data_contract import TRAINING_COLUMNS
from max_backend.onnx_data_service import OnnxDataService
from max_backend.onnx_data_store import migrate_onnx_data, persist_data_ready_evidence


def _row(minute: int) -> list[str]:
    fields = ["0.1"] * 49
    fields[0] = "CP32_TRUE_MTF_V1"
    fields[1] = f"2024.01.01 00:{minute:02d}"
    fields[2] = fields[1]
    fields[3] = "EURUSD"
    fields[4] = "5"
    fields[5:10] = ["1.1000", "1.1200", "1.0900", "1.1100", "0.0100"]
    fields[10:13] = ["1.1099", "1.1100", "1.0"]
    fields[13:17] = ["1.0", "2.0", "24", "0.25"]
    return fields


def _training_csv(*, duplicate: bool = False, gap: bool = False) -> bytes:
    minutes = (0, 5, 10, 15, 20, 25) if not gap else (0, 5, 20, 25)
    rows = [_row(minute) for minute in minutes]
    if duplicate:
        rows.insert(2, rows[1].copy())
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
    writer.writerow(TRAINING_COLUMNS)
    writer.writerows(rows)
    return stream.getvalue().encode("ascii")


def _service(tmp_path: Path, *, duplicate: bool = False, gap: bool = False) -> OnnxDataService:
    common = tmp_path / "Common" / "Files"
    common.mkdir(parents=True)
    source = common / "Max_MTF_Training.csv"
    source.write_bytes(_training_csv(duplicate=duplicate, gap=gap))
    source.with_name(source.name + ".lock").write_bytes(b"")
    database = tmp_path / "state" / "max.db"
    with connect(database) as conn:
        migrate_onnx_data(conn)
    return OnnxDataService(
        common_files_root=common,
        snapshot_root=tmp_path / "state" / "onnx_data" / "snapshots",
        database_path=database,
        manifest_path=EA_MANIFEST,
        baseline_path=EA_BASELINE,
        synthetic_test_evidence=True,
    )


def _client(service: OnnxDataService) -> httpx.AsyncClient:
    app.dependency_overrides[get_data_service] = lambda: service
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def clear_data_service_override():
    yield
    app.dependency_overrides.pop(get_data_service, None)


def _run(awaitable):
    return asyncio.run(awaitable)


def _preflight_payload() -> dict[str, str]:
    return {"timezone_provenance": "Owner-declared broker server time; UTC offset not independently verified"}


def test_v2_preflight_snapshot_windows_and_synthetic_readiness_are_truthful(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        async with _client(service) as client:
            initial = await client.get("/api/v2/onnx/data/workspace")
            assert initial.status_code == 200
            assert initial.json()["status"] == "NOT_STARTED"
            assert initial.json()["real_data_readiness"] == "NOT_PROVEN"

            preflight = await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())
            assert preflight.status_code == 200
            preflight_body = preflight.json()
            assert preflight_body["status"] == "PREFLIGHT_PASS"
            assert preflight_body["source_identity"]["sha256"]
            assert preflight_body["authority"]["feature_contract"] == "CP32_TRUE_MTF_V1"

            created = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight_body["source_identity"]["sha256"], "confirmed": True},
            )
            assert created.status_code == 200
            snapshot = created.json()["snapshot"]
            assert created.json()["status"] == "SYNTHETIC_TEST_EVIDENCE"
            assert snapshot["snapshot_id"] == "SNP-" + snapshot["sha256"]
            assert snapshot["evidence_class"] == "SYNTHETIC_TEST_EVIDENCE"

            windows = {
                "DISCOVERY": {"from": "2024-01-01T00:00:00", "to": "2024-01-01T00:05:00"},
                "TOURNAMENT": {"from": "2024-01-01T00:10:00", "to": "2024-01-01T00:15:00"},
                "FORWARD": {"from": "2024-01-01T00:20:00", "to": "2024-01-01T00:25:00"},
            }
            validated = await client.post(
                "/api/v2/onnx/data/windows/validate",
                json={
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_sha256": snapshot["sha256"],
                    "timezone_provenance": _preflight_payload()["timezone_provenance"],
                    "windows": windows,
                },
            )
            assert validated.status_code == 200
            assert validated.json()["status"] == "SYNTHETIC_TEST_EVIDENCE"
            assert validated.json()["readiness"] is None

            workspace = (await client.get("/api/v2/onnx/data/workspace")).json()
            assert workspace["status"] == "SYNTHETIC_TEST_EVIDENCE"
            assert workspace["real_data_readiness"] == "NOT_PROVEN"
            assert workspace["first_blocker"] == "SYNTHETIC_TEST_EVIDENCE_NOT_REAL_DATA"

            frozen_v1 = (await client.get("/api/v1/onnx/workspace")).json()
            assert frozen_v1["contract_version"] == "1.0"
            assert frozen_v1["dataset"]["snapshot_id"] is None

    _run(scenario())


def test_source_change_after_preflight_cannot_be_snapshotted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source = service.common_files_root / "Max_MTF_Training.csv"

    async def scenario() -> None:
        async with _client(service) as client:
            preflight = await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())
            digest = preflight.json()["source_identity"]["sha256"]
            source.write_bytes(_training_csv(gap=True))
            response = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": digest, "confirmed": True},
            )
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "SOURCE_CHANGED_SINCE_PREFLIGHT"
            assert service.workspace()["latest_snapshot"] is None

    _run(scenario())


def test_same_content_with_conflicting_source_provenance_cannot_reuse_snapshot_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        async with _client(service) as client:
            first_preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            first = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": first_preflight["source_identity"]["sha256"], "confirmed": True},
            )
            assert first.status_code == 200

            conflicting_provenance = {"timezone_provenance": "Different unverified server-time claim"}
            second_preflight = (await client.post("/api/v2/onnx/data/preflight", json=conflicting_provenance)).json()
            second = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**conflicting_provenance, "expected_source_sha256": second_preflight["source_identity"]["sha256"], "confirmed": True},
            )
            assert second.status_code == 409
            assert second.json()["detail"]["code"] == "SNAPSHOT_PERSISTENCE_IDENTITY_CONFLICT"
            assert service.workspace()["latest_snapshot"]["timezone_provenance"] == _preflight_payload()["timezone_provenance"]

    _run(scenario())


def test_identical_duplicate_repair_is_new_immutable_snapshot_with_lineage(tmp_path: Path) -> None:
    service = _service(tmp_path, duplicate=True)

    async def scenario() -> None:
        async with _client(service) as client:
            preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            raw = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight["source_identity"]["sha256"], "confirmed": True},
            )
            assert raw.status_code == 200
            raw_snapshot = raw.json()["snapshot"]
            assert raw_snapshot["dq_status"] == "BLOCKED"
            assert raw_snapshot["dq"]["identical_duplicate_rows"] == 1

            corrected = await client.post(
                f"/api/v2/onnx/data/snapshots/{raw_snapshot['snapshot_id']}/resolve-identical-duplicates",
                json={
                    "expected_snapshot_sha256": raw_snapshot["sha256"],
                    "confirmation": "REMOVE_IDENTICAL_DUPLICATES",
                },
            )
            assert corrected.status_code == 200
            derived = corrected.json()["snapshot"]
            assert derived["snapshot_id"] != raw_snapshot["snapshot_id"]
            assert derived["parent_snapshot_id"] == raw_snapshot["snapshot_id"]
            assert derived["correction"]["before_sha256"] == raw_snapshot["sha256"]
            assert derived["correction"]["backup_snapshot_sha256"] == raw_snapshot["sha256"]
            assert derived["correction"]["after_sha256"] == derived["sha256"]
            assert derived["correction"]["removed_rows"] == 1
            assert derived["dq_status"] == "PASS"
            assert (await client.get(f"/api/v2/onnx/data/snapshots/{raw_snapshot['snapshot_id']}")).json()["snapshot"]["sha256"] == raw_snapshot["sha256"]

    _run(scenario())


def test_observed_gap_is_not_broker_confirmation_and_does_not_claim_data_ready(tmp_path: Path) -> None:
    service = _service(tmp_path, gap=True)

    async def scenario() -> None:
        async with _client(service) as client:
            preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            created = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight["source_identity"]["sha256"], "confirmed": True},
            )
            snapshot = created.json()["snapshot"]
            assert snapshot["timestamp_discontinuity_status"] == "OBSERVED_TIMESTAMP_DISCONTINUITY"
            assert snapshot["broker_reconciliation_status"] == "BROKER_RECONCILIATION_PENDING"
            assert all(issue["code"] != "BROKER_CONFIRMED_MISSING" for issue in snapshot["dq"]["issues"])
            windows = {
                "DISCOVERY": {"from": "2024-01-01T00:00:00", "to": "2024-01-01T00:05:00"},
                "TOURNAMENT": {"from": "2024-01-01T00:20:00", "to": "2024-01-01T00:20:00"},
                "FORWARD": {"from": "2024-01-01T00:25:00", "to": "2024-01-01T00:25:00"},
            }
            result = await client.post(
                "/api/v2/onnx/data/windows/validate",
                json={
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_sha256": snapshot["sha256"],
                    "timezone_provenance": _preflight_payload()["timezone_provenance"],
                    "windows": windows,
                },
            )
            assert result.status_code == 200
            assert result.json()["status"] == "SYNTHETIC_TEST_EVIDENCE"
            assert result.json()["validation"]["status"] == "PASS"
            assert result.json()["readiness"] is None

    _run(scenario())


def test_real_data_ready_is_blocked_until_broker_reconciliation_is_verified(tmp_path: Path) -> None:
    evidence = persist_data_ready_evidence(
        snapshot={
            "snapshot_id": "SNP-" + "a" * 64,
            "snapshot_sha256": "a" * 64,
            "dq_status": "PASS",
            "timestamp_discontinuity_status": "NO_OBSERVED_DISCONTINUITY",
            "broker_reconciliation_status": "BROKER_RECONCILIATION_PENDING",
        },
        window_config={
            "snapshot_sha256": "a" * 64,
            "window_config_id": "WIN-" + "b" * 64,
            "validation": {"status": "PASS"},
        },
        evidence_class="OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI",
        path=tmp_path / "state" / "never-created.db",
    )
    assert evidence is None
    assert not (tmp_path / "state" / "never-created.db").exists()


def test_snapshot_tampering_fails_closed_and_database_rows_are_immutable(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def scenario() -> None:
        async with _client(service) as client:
            preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            created = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight["source_identity"]["sha256"], "confirmed": True},
            )
            snapshot = created.json()["snapshot"]
            artifact = service.snapshot_root / f"{snapshot['sha256']}.csv"
            artifact.chmod(0o666)
            artifact.write_bytes(b"tampered\r\n")
            response = await client.get("/api/v2/onnx/data/workspace")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "IMMUTABLE_SNAPSHOT_HASH_MISMATCH"

    _run(scenario())
    with connect(service.database_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="ONNX_DATA_SNAPSHOT_IMMUTABLE"):
            conn.execute("UPDATE onnx_data_snapshots SET dq_status='PASS'")
        with pytest.raises(sqlite3.IntegrityError, match="ONNX_DATA_SNAPSHOT_IMMUTABLE"):
            conn.execute("DELETE FROM onnx_data_snapshots")


def test_snapshot_broker_status_column_tampering_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def create_snapshot() -> str:
        async with _client(service) as client:
            preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            response = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight["source_identity"]["sha256"], "confirmed": True},
            )
            assert response.status_code == 200
            return response.json()["snapshot"]["snapshot_id"]

    snapshot_id = _run(create_snapshot())
    with connect(service.database_path) as conn:
        conn.execute("DROP TRIGGER onnx_data_snapshots_no_update")
        conn.execute(
            "UPDATE onnx_data_snapshots SET broker_reconciliation_status='BROKER_CONFIRMED_NOT_MISSING' WHERE snapshot_id=?",
            (snapshot_id,),
        )

    async def reject_tampered_snapshot() -> None:
        async with _client(service) as client:
            response = await client.get("/api/v2/onnx/data/workspace")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "SNAPSHOT_LEDGER_BINDING_MISMATCH"

    _run(reject_tampered_snapshot())


def test_tampered_window_ledger_fails_readback_identity_check(tmp_path: Path) -> None:
    service = _service(tmp_path)

    async def create_configuration() -> None:
        async with _client(service) as client:
            preflight = (await client.post("/api/v2/onnx/data/preflight", json=_preflight_payload())).json()
            created = await client.post(
                "/api/v2/onnx/data/snapshots",
                json={**_preflight_payload(), "expected_source_sha256": preflight["source_identity"]["sha256"], "confirmed": True},
            )
            snapshot = created.json()["snapshot"]
            windows = {
                "DISCOVERY": {"from": "2024-01-01T00:00:00", "to": "2024-01-01T00:05:00"},
                "TOURNAMENT": {"from": "2024-01-01T00:10:00", "to": "2024-01-01T00:15:00"},
                "FORWARD": {"from": "2024-01-01T00:20:00", "to": "2024-01-01T00:25:00"},
            }
            response = await client.post(
                "/api/v2/onnx/data/windows/validate",
                json={
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_sha256": snapshot["sha256"],
                    "timezone_provenance": _preflight_payload()["timezone_provenance"],
                    "windows": windows,
                },
            )
            assert response.status_code == 200

    _run(create_configuration())
    with connect(service.database_path) as conn:
        conn.execute("DROP TRIGGER onnx_data_windows_no_update")
        row = conn.execute("SELECT window_config_id,windows_json FROM onnx_data_window_configs").fetchone()
        altered = json.loads(row["windows_json"])
        altered["DISCOVERY"]["to"] = "2024-01-01T00:00:00"
        conn.execute(
            "UPDATE onnx_data_window_configs SET windows_json=? WHERE window_config_id=?",
            (json.dumps(altered, sort_keys=True, separators=(",", ":")), row["window_config_id"]),
        )

    async def read_tampered_configuration() -> None:
        async with _client(service) as client:
            response = await client.get("/api/v2/onnx/data/workspace")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "WINDOW_CONFIG_IDENTITY_MISMATCH"

    _run(read_tampered_configuration())


def test_recovery_required_503_blocks_v2_operations_with_existing_error_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    from max_backend import main as main_module

    monkeypatch.setattr(main_module, "RECOVERY_REQUIRED", True)
    monkeypatch.setattr(main_module, "RECOVERY_REASON", "synthetic recovery gate test")

    async def scenario() -> None:
        async with _client(service) as client:
            response = await client.get("/api/v2/onnx/data/workspace")
            assert response.status_code == 503
            assert response.json() == {
                "detail": "RECOVERY_REQUIRED: application operations are disabled",
                "reason": "synthetic recovery gate test",
            }

    _run(scenario())


def test_api_has_no_training_or_scientific_mutation_routes() -> None:
    data_routes = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v2/onnx/data")
        for method in operations
    }
    assert data_routes == {
        ("/api/v2/onnx/data/workspace", "GET"),
        ("/api/v2/onnx/data/preflight", "POST"),
        ("/api/v2/onnx/data/snapshots", "POST"),
        ("/api/v2/onnx/data/snapshots/{snapshot_id}", "GET"),
        ("/api/v2/onnx/data/snapshots/{snapshot_id}/resolve-identical-duplicates", "POST"),
        ("/api/v2/onnx/data/windows/validate", "POST"),
    }
