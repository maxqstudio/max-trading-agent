from __future__ import annotations

import csv
import io
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_backend.onnx_data_contract import (
    TRAINING_COLUMNS,
    audit_training_csv,
    validate_research_windows,
)
from max_backend.onnx_data_source import (
    OnnxDataSourceError,
    PublishedSnapshot,
    _exclusive_writer_lock,
    capture_training_source,
    publish_snapshot,
    resolve_training_source,
    verify_ea_authority,
    verify_snapshot_file,
)
from max_backend.onnx_data_store import persist_snapshot
from max_backend import onnx_data_source
from max_backend.config import EA_BASELINE, EA_MANIFEST


def _row(
    minute: int,
    *,
    symbol: str = "EURUSD",
    period: int = 5,
    timestamp: str | None = None,
) -> list[str]:
    values = ["0.1"] * 49
    values[0] = "CP32_TRUE_MTF_V1"
    values[1] = timestamp or f"2024.01.01 00:{minute:02d}"
    values[2] = values[1]
    values[3] = symbol
    values[4] = str(period)
    values[5:10] = ["1.1000", "1.1200", "1.0900", "1.1100", "0.0100"]
    values[10:13] = ["1.1099", "1.1100", "1.0"]
    values[13:17] = ["1.0", "2.0", "24", "0.25"]
    return values


def _csv_bytes(rows: list[list[str]], *, header: list[str] | None = None) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(header or list(TRAINING_COLUMNS))
    writer.writerows(rows)
    return buffer.getvalue().encode("ascii")


def _valid_rows() -> list[list[str]]:
    return [_row(minute) for minute in (0, 5, 10, 15, 20, 25)]


def test_actual_cp32_schema_and_broker_wall_clock_are_audited_without_utc_conversion() -> None:
    report = audit_training_csv(_csv_bytes(_valid_rows()), timezone_provenance="Broker server time; UTC offset not independently verified")

    assert len(TRAINING_COLUMNS) == 49
    assert report.schema_status == "PASS"
    assert report.row_count == 6
    assert report.symbol == "EURUSD"
    assert report.timeframe == "M5"
    assert report.timestamp_timezone == "NAIVE_BROKER_SOURCE_TIME"
    assert report.timestamp_min == "2024-01-01T00:00:00"
    assert report.timestamp_max == "2024-01-01T00:25:00"


@pytest.mark.parametrize(
    "raw,reason",
    [
        (_csv_bytes(_valid_rows(), header=["wrong", *TRAINING_COLUMNS[1:]]), "SCHEMA_MISMATCH"),
        (_csv_bytes(_valid_rows()).replace(b";", b","), "SCHEMA_MISMATCH"),
        (_csv_bytes(_valid_rows())[:-2], "PARTIAL_TRAILING_ROW"),
    ],
)
def test_invalid_header_delimiter_or_partial_trailing_row_fails_closed(raw: bytes, reason: str) -> None:
    report = audit_training_csv(raw, timezone_provenance="broker server time")
    assert report.status == "BLOCKED"
    assert reason in {issue.code for issue in report.issues}


def test_wrong_feature_contract_and_nonfinite_feature_fail_closed() -> None:
    wrong_contract = _valid_rows()
    wrong_contract[0][0] = "OTHER_CONTRACT"
    assert "FEATURE_CONTRACT_MISMATCH" in {
        issue.code for issue in audit_training_csv(_csv_bytes(wrong_contract), timezone_provenance="broker").issues
    }

    nonfinite = _valid_rows()
    nonfinite[0][17] = "NaN"
    report = audit_training_csv(_csv_bytes(nonfinite), timezone_provenance="broker")
    assert report.status == "BLOCKED"
    assert "NONFINITE_NUMERIC_VALUE" in {issue.code for issue in report.issues}


def test_ohlc_atr_quote_and_row_completeness_are_enforced() -> None:
    bad_ohlc = _valid_rows()
    bad_ohlc[0][6] = "1.0000"
    report = audit_training_csv(_csv_bytes(bad_ohlc), timezone_provenance="broker")
    assert "OHLC_INCONSISTENT" in {issue.code for issue in report.issues}

    missing = _valid_rows()
    missing[0][17] = ""
    assert "EMPTY_REQUIRED_FIELD" in {
        issue.code for issue in audit_training_csv(_csv_bytes(missing), timezone_provenance="broker").issues
    }


def test_mixed_symbol_or_timeframe_fails_closed() -> None:
    mixed_symbol = _valid_rows()
    mixed_symbol[-1][3] = "GBPUSD"
    assert "MIXED_SYMBOL" in {
        issue.code for issue in audit_training_csv(_csv_bytes(mixed_symbol), timezone_provenance="broker").issues
    }

    mixed_period = _valid_rows()
    mixed_period[-1][4] = "16385"
    assert "MIXED_TIMEFRAME" in {
        issue.code for issue in audit_training_csv(_csv_bytes(mixed_period), timezone_provenance="broker").issues
    }


def test_duplicate_identity_is_contract_symbol_period_and_signal_time() -> None:
    rows = _valid_rows()
    rows.insert(2, rows[1].copy())
    report = audit_training_csv(_csv_bytes(rows), timezone_provenance="broker")
    assert report.status == "BLOCKED"
    assert report.identical_duplicate_rows == 1
    assert "IDENTICAL_DUPLICATES_REQUIRE_EXPLICIT_RESOLUTION" in {issue.code for issue in report.issues}

    conflicting = _valid_rows()
    duplicate = conflicting[1].copy()
    duplicate[7] = "1.1050"
    conflicting.insert(2, duplicate)
    report = audit_training_csv(_csv_bytes(conflicting), timezone_provenance="broker")
    assert "CONFLICTING_DUPLICATE_IDENTITY" in {issue.code for issue in report.issues}


def test_nonchronological_rows_fail_and_timestamp_gaps_remain_unconfirmed() -> None:
    out_of_order = [_row(10), _row(5)]
    report = audit_training_csv(_csv_bytes(out_of_order), timezone_provenance="broker")
    assert "NONCHRONOLOGICAL_TIMESTAMP" in {issue.code for issue in report.issues}

    gap = audit_training_csv(_csv_bytes([_row(0), _row(15)]), timezone_provenance="broker")
    assert gap.status == "PASS"
    assert gap.timestamp_discontinuity_status == "OBSERVED_TIMESTAMP_DISCONTINUITY"
    assert gap.broker_reconciliation_status == "BROKER_RECONCILIATION_PENDING"
    assert "OBSERVED_TIMESTAMP_DISCONTINUITY" in {issue.code for issue in gap.issues}
    assert "BROKER_CONFIRMED_MISSING" not in {issue.code for issue in gap.issues}


def test_unknown_period_empty_dataset_and_missing_timezone_provenance_fail_closed() -> None:
    unknown_period = [_row(0, period=99)]
    report = audit_training_csv(_csv_bytes(unknown_period), timezone_provenance="broker")
    assert "UNSUPPORTED_TIMEFRAME" in {issue.code for issue in report.issues}

    empty = _csv_bytes([])
    report = audit_training_csv(empty, timezone_provenance="broker")
    assert "EMPTY_DATASET" in {issue.code for issue in report.issues}

    report = audit_training_csv(_csv_bytes(_valid_rows()), timezone_provenance="")
    assert "TIMEZONE_PROVENANCE_REQUIRED" in {issue.code for issue in report.issues}


def test_exact_three_windows_are_ordered_and_supported_by_snapshot_rows() -> None:
    report = audit_training_csv(_csv_bytes(_valid_rows()), timezone_provenance="broker server time")
    windows = {
        "DISCOVERY": {"from": "2024-01-01T00:00:00", "to": "2024-01-01T00:05:00"},
        "TOURNAMENT": {"from": "2024-01-01T00:10:00", "to": "2024-01-01T00:15:00"},
        "FORWARD": {"from": "2024-01-01T00:20:00", "to": "2024-01-01T00:25:00"},
    }
    result = validate_research_windows(report, windows, timezone_provenance="broker server time")
    assert result.status == "PASS"
    assert [window.name for window in result.windows] == ["DISCOVERY", "TOURNAMENT", "FORWARD"]
    assert all(window.row_count > 0 for window in result.windows)

    overlap = {**windows, "TOURNAMENT": {"from": "2024-01-01T00:05:00", "to": "2024-01-01T00:15:00"}}
    assert "WINDOWS_OVERLAP_OR_ORDER_INVALID" in {
        issue.code for issue in validate_research_windows(report, overlap, timezone_provenance="broker").issues
    }

    unsupported = {**windows, "FORWARD": {"from": "2024-02-01T00:00:00", "to": "2024-02-02T00:00:00"}}
    assert "WINDOW_OUTSIDE_SNAPSHOT_COVERAGE" in {
        issue.code for issue in validate_research_windows(report, unsupported, timezone_provenance="broker").issues
    }


def _write_test_source(root: Path, rows: list[list[str]] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "Max_MTF_Training.csv"
    path.write_bytes(_csv_bytes(rows or _valid_rows()))
    path.with_name(path.name + ".lock").write_bytes(b"")
    return path


def test_preflight_is_read_only_hashes_exact_source_and_requires_ea_lock(tmp_path: Path) -> None:
    root = tmp_path / "Common" / "Files"
    path = _write_test_source(root)
    capture, report = capture_training_source(common_files_root=root, timezone_provenance="Owner-declared broker server time")
    assert capture.path == path.resolve()
    assert capture.sha256
    assert report.status == "PASS"
    assert path.with_name(path.name + ".lock").read_bytes() == b""

    path.with_name(path.name + ".lock").unlink()
    with pytest.raises(OnnxDataSourceError, match="lock file") as exc:
        capture_training_source(common_files_root=root, timezone_provenance="broker")
    assert exc.value.code == "WRITER_LOCK_MISSING"
    assert not path.with_name(path.name + ".lock").exists()


def test_owner_override_must_remain_inside_common_files_and_rejects_links(tmp_path: Path) -> None:
    root = tmp_path / "Common" / "Files"
    path = _write_test_source(root)
    assert resolve_training_source(str(path), common_files_root=root) == path.resolve()
    with pytest.raises(OnnxDataSourceError) as exc:
        resolve_training_source(str(tmp_path / "outside.csv"), common_files_root=root)
    assert exc.value.code == "SOURCE_PATH_OUTSIDE_COMMON_FILES"

    linked = root / "linked.csv"
    try:
        linked.symlink_to(path)
    except OSError as exc:
        pytest.fail(f"Windows test host does not permit the required symlink safety test: {exc}")
    with pytest.raises(OnnxDataSourceError) as exc:
        resolve_training_source(str(linked), common_files_root=root)
    assert exc.value.code == "SOURCE_PATH_UNSAFE"


def test_missing_and_permission_denied_sources_have_distinct_blockers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "Common" / "Files"
    source = _write_test_source(root)
    source.unlink()
    with pytest.raises(OnnxDataSourceError) as missing:
        resolve_training_source(str(source), common_files_root=root)
    assert missing.value.code == "SOURCE_MISSING"

    source.write_bytes(_csv_bytes(_valid_rows()))
    source_path_resolve = Path.resolve

    def deny_selected_source(path: Path, *args, **kwargs):
        if path == source:
            raise PermissionError("synthetic permission denial")
        return source_path_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", deny_selected_source)
    with pytest.raises(OnnxDataSourceError) as denied:
        resolve_training_source(str(source), common_files_root=root)
    assert denied.value.code == "SOURCE_INACCESSIBLE"
    assert denied.value.status_code == 403


def test_active_ea_writer_lock_blocks_source_read_without_retry_or_mutation(tmp_path: Path) -> None:
    root = tmp_path / "Common" / "Files"
    path = _write_test_source(root)
    lock = path.with_name(path.name + ".lock")
    with _exclusive_writer_lock(lock):
        with pytest.raises(OnnxDataSourceError) as exc:
            capture_training_source(common_files_root=root, timezone_provenance="broker")
        assert exc.value.code == "WRITER_ACTIVE"
    assert path.exists()


def test_source_identity_change_during_locked_copy_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "Common" / "Files"
    _write_test_source(root)
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        if calls == 2:
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns + 1,
                st_birthtime_ns=getattr(observed, "st_birthtime_ns", 0),
            )
        return observed

    monkeypatch.setattr(onnx_data_source.os, "fstat", changing_fstat)
    with pytest.raises(OnnxDataSourceError) as exc:
        capture_training_source(common_files_root=root, timezone_provenance="broker server time")
    assert exc.value.code == "SOURCE_UNSTABLE"


def test_capture_exceeding_ea_lock_budget_fails_without_publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "Common" / "Files"
    source = _write_test_source(root)
    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(onnx_data_source.time, "monotonic", lambda: next(times))
    with pytest.raises(OnnxDataSourceError) as exc:
        capture_training_source(common_files_root=root, timezone_provenance="broker server time")
    assert exc.value.code == "SOURCE_CAPTURE_LOCK_BUDGET_EXCEEDED"
    assert source.exists()
    assert not list((tmp_path / "private").glob("**/*.csv"))


def test_interrupted_snapshot_publication_is_not_exposed_and_retry_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    common = tmp_path / "Common" / "Files"
    source = _write_test_source(common)
    capture, report = capture_training_source(
        str(source), common_files_root=common, timezone_provenance="broker server time",
    )
    root = tmp_path / "private" / "snapshots"
    expected = root / f"{capture.sha256}.csv"
    authority = verify_ea_authority(EA_MANIFEST, EA_BASELINE)

    def interrupted_rename(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic interruption before atomic publication")

    with monkeypatch.context() as patch:
        patch.setattr(onnx_data_source.os, "rename", interrupted_rename)
        with pytest.raises(OSError, match="synthetic interruption"):
            publish_snapshot(capture, report, snapshot_root=root, authority=authority)
    assert not expected.exists()
    assert list(root.glob("*.csv")) == []
    assert list(root.glob(".snapshot-*.tmp"))

    published = publish_snapshot(capture, report, snapshot_root=root, authority=authority)
    assert published.sha256 == hashlib.sha256(capture.raw).hexdigest()
    assert verify_snapshot_file(root, capture.sha256) == expected
    assert [path.name for path in root.glob("*.csv")] == [expected.name]


def test_structurally_invalid_csv_is_rejected_by_publisher_before_creating_snapshot_root(tmp_path: Path) -> None:
    common = tmp_path / "Common" / "Files"
    source = _write_test_source(common)
    source.write_bytes(source.read_bytes()[:-2])
    capture, report = capture_training_source(
        str(source), common_files_root=common, timezone_provenance="broker server time",
    )
    root = tmp_path / "private" / "snapshots"
    authority = verify_ea_authority(EA_MANIFEST, EA_BASELINE)

    with pytest.raises(OnnxDataSourceError) as caught:
        publish_snapshot(capture, report, snapshot_root=root, authority=authority)

    assert caught.value.code == "SNAPSHOT_SOURCE_STRUCTURALLY_INVALID"
    assert not root.exists()


def test_structurally_invalid_csv_is_rejected_by_storage_before_database_creation(tmp_path: Path) -> None:
    common = tmp_path / "Common" / "Files"
    source = _write_test_source(common)
    raw = source.read_bytes()[:-2]
    source.write_bytes(raw)
    capture, report = capture_training_source(
        str(source), common_files_root=common, timezone_provenance="broker server time",
    )
    artifact = tmp_path / "forged-published-artifact.csv"
    artifact.write_bytes(raw)
    authority = verify_ea_authority(EA_MANIFEST, EA_BASELINE)
    snapshot = PublishedSnapshot(
        snapshot_id=f"SNP-{capture.sha256}",
        dataset_id="DS-test",
        sha256=capture.sha256,
        size_bytes=len(raw),
        path=artifact,
        source_path=str(source),
        source_fingerprint=capture.fingerprint,
        report=report,
    )
    database = tmp_path / "state" / "should-not-be-created.db"

    with pytest.raises(OnnxDataSourceError) as caught:
        persist_snapshot(snapshot, authority=authority, path=database)

    assert caught.value.code == "SNAPSHOT_SOURCE_STRUCTURALLY_INVALID"
    assert not database.exists()


def test_snapshot_publication_rejects_reparse_parent_before_creating_outside_directory(tmp_path: Path) -> None:
    common = tmp_path / "Common" / "Files"
    source = _write_test_source(common)
    capture, report = capture_training_source(
        str(source), common_files_root=common, timezone_provenance="broker server time",
    )
    authority = verify_ea_authority(EA_MANIFEST, EA_BASELINE)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "private"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.fail(f"Windows test host does not permit the required snapshot-path safety test: {exc}")

    with pytest.raises(OnnxDataSourceError) as caught:
        publish_snapshot(capture, report, snapshot_root=linked_parent / "snapshots", authority=authority)

    assert caught.value.code == "SNAPSHOT_ROOT_UNSAFE"
    assert not (outside / "snapshots").exists()


def test_concurrent_same_hash_publication_returns_one_immutable_artifact(tmp_path: Path) -> None:
    common = tmp_path / "Common" / "Files"
    source = _write_test_source(common)
    capture, report = capture_training_source(
        str(source), common_files_root=common, timezone_provenance="broker server time",
    )
    root = tmp_path / "private" / "snapshots"
    authority = verify_ea_authority(EA_MANIFEST, EA_BASELINE)

    def publish(_index: int):
        return publish_snapshot(capture, report, snapshot_root=root, authority=authority)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (1, 2)))
    assert {result.snapshot_id for result in results} == {f"SNP-{capture.sha256}"}
    assert {result.sha256 for result in results} == {capture.sha256}
    assert verify_snapshot_file(root, capture.sha256).read_bytes() == capture.raw
    assert len(list(root.glob("*.csv"))) == 1
