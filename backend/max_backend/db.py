from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH, STRATEGY_CONTRACT
from .ea import verify_baseline_snapshot
from .schema import CURRENT_SCHEMA_VERSION

SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA = 1
SCHEMA16_ARCHIVE_COMPATIBILITY = "SCHEMA16_ARCHIVE_EXTENSION_V1"
_SCHEMA15_CORE_OBJECT_COUNT = 42
_SCHEMA15_CORE_OBJECT_SHA256 = "d1ac3e384457c5c8f659dea2280fdabe58bb03f2d0631d1227a28b8b59f2475a"
_SCHEMA16_ARCHIVE_OBJECT_COUNT = 11
_SCHEMA16_ARCHIVE_OBJECT_SHA256 = "635467e318829432881f89ff5c546ff3dd5fb4d0ff827f2fa5df554236d3530f"
_SCHEMA16_ARCHIVE_OBJECT_NAMES = {
    ("index", "ix_onnx_data_snapshots_created"),
    ("index", "ix_onnx_data_windows_snapshot"),
    ("table", "onnx_data_readiness_evidence"),
    ("table", "onnx_data_snapshots"),
    ("table", "onnx_data_window_configs"),
    ("trigger", "onnx_data_readiness_no_delete"),
    ("trigger", "onnx_data_readiness_no_update"),
    ("trigger", "onnx_data_snapshots_no_delete"),
    ("trigger", "onnx_data_snapshots_no_update"),
    ("trigger", "onnx_data_windows_no_delete"),
    ("trigger", "onnx_data_windows_no_update"),
}


class DatabaseSchemaGuardError(RuntimeError):
    """A database cannot safely be opened by this application build."""

    def __init__(
        self,
        status: str,
        message: str,
        *,
        database_schema_version: int | None = None,
        compatibility_profile: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.database_schema_version = database_schema_version
        self.compatibility_profile = compatibility_profile
        self.message = message

    def diagnostic(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "reason": self.status,
            "message": self.message,
            "supported_schema_version": CURRENT_SCHEMA_VERSION,
            "reset_available": False,
        }
        if self.database_schema_version is not None:
            result["database_schema_version"] = self.database_schema_version
        if self.compatibility_profile is not None:
            result["compatibility_profile"] = self.compatibility_profile
        return result


def _schema_object_fingerprint(rows: list[tuple[Any, ...]]) -> str:
    encoded = "\n".join(
        "\x1f".join(str(value or "") for value in row)
        for row in rows
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema16_archive_profile_error(
    conn: sqlite3.Connection,
) -> str | None:
    rows = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT type,name,tbl_name,COALESCE(sql,'')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type,name
            """
        ).fetchall()
    ]
    archive_rows = [
        row for row in rows
        if str(row[1]).lower().startswith("onnx_data_")
        or str(row[2]).lower().startswith("onnx_data_")
    ]
    core_rows = [
        row for row in rows
        if not (
            str(row[1]).lower().startswith(("onnx_data_", "research_"))
            or str(row[2]).lower().startswith(("onnx_data_", "research_"))
        )
    ]
    unexpected_research_bindings = [
        row for row in rows
        if str(row[1]).lower().startswith("research_")
        and not str(row[2]).lower().startswith("research_")
        and str(row[0]).lower() in {"index", "trigger"}
    ]
    if len(core_rows) != _SCHEMA15_CORE_OBJECT_COUNT:
        return "SCHEMA16_CORE_OBJECT_INVENTORY_MISMATCH"
    if _schema_object_fingerprint(core_rows) != _SCHEMA15_CORE_OBJECT_SHA256:
        return "SCHEMA16_CORE_SCHEMA_MISMATCH"
    if len(archive_rows) != _SCHEMA16_ARCHIVE_OBJECT_COUNT:
        return "SCHEMA16_ARCHIVE_OBJECT_INVENTORY_MISMATCH"
    if {(str(row[0]), str(row[1])) for row in archive_rows} != _SCHEMA16_ARCHIVE_OBJECT_NAMES:
        return "SCHEMA16_ARCHIVE_OBJECT_NAMES_MISMATCH"
    if _schema_object_fingerprint(archive_rows) != _SCHEMA16_ARCHIVE_OBJECT_SHA256:
        return "SCHEMA16_ARCHIVE_SCHEMA_MISMATCH"
    if unexpected_research_bindings:
        return "SCHEMA16_RESEARCH_OBJECT_BOUND_TO_CORE_SCHEMA"
    return None


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing database without creating files or granting write access."""
    database = Path(path)
    uri = database.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def is_schema16_archive_compatible(schema: dict[str, Any]) -> bool:
    return schema.get("compatibility_profile") == SCHEMA16_ARCHIVE_COMPATIBILITY


def assert_automatic_recovery_supported(path: Path) -> dict[str, Any]:
    schema = assert_database_schema_compatible(path, allow_missing=False)
    if is_schema16_archive_compatible(schema):
        raise DatabaseSchemaGuardError(
            "SCHEMA16_AUTOMATIC_RECOVERY_DISABLED",
            "Schema-16 compatibility preserves persisted authority; automatic recovery is disabled until an explicit, evidence-backed reconciliation is authorized.",
            database_schema_version=16,
            compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
        )
    return schema


def assert_reset_supported(path: Path) -> dict[str, Any]:
    schema = assert_database_schema_compatible(path, allow_missing=False)
    if is_schema16_archive_compatible(schema):
        raise DatabaseSchemaGuardError(
            "SCHEMA16_RESET_DISABLED",
            "Reset is disabled for the preserved schema-16 archive profile; no operational or historical evidence was changed.",
            database_schema_version=16,
            compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
        )
    return schema


def verify_schema16_database_integrity(path: Path) -> dict[str, Any]:
    """Prove SQLite and FK integrity for an already structurally verified schema-16 DB."""
    schema = assert_database_schema_compatible(path, allow_missing=False)
    if not is_schema16_archive_compatible(schema):
        raise DatabaseSchemaGuardError(
            "SCHEMA16_COMPATIBILITY_PROFILE_REQUIRED",
            "The integrity verifier only accepts the exact historical schema-16 compatibility profile.",
            database_schema_version=schema.get("schema_version"),
        )
    conn: sqlite3.Connection | None = None
    try:
        conn = open_read_only(path)
        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        integrity = [str(row[0]) for row in integrity_rows]
        foreign_key_errors = [
            tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
        ]
    except sqlite3.Error as exc:
        raise DatabaseSchemaGuardError(
            "SCHEMA16_DATABASE_INTEGRITY_UNVERIFIABLE",
            "SQLite integrity could not be verified read-only; startup is blocked without changing the database.",
            database_schema_version=16,
            compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
        ) from exc
    finally:
        if conn is not None:
            conn.close()
    if integrity != ["ok"] or foreign_key_errors:
        raise DatabaseSchemaGuardError(
            "SCHEMA16_DATABASE_INTEGRITY_FAILED",
            "The structurally recognized schema-16 database failed SQLite integrity or foreign-key validation; no recovery or reset was attempted.",
            database_schema_version=16,
            compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
        )
    return {
        "status": "PASS",
        "schema_version": 16,
        "compatibility_profile": SCHEMA16_ARCHIVE_COMPATIBILITY,
        "physical_integrity": "ok",
        "foreign_key_violations": 0,
    }


def assert_database_schema_compatible(
    path: Path = DATABASE_PATH,
    *,
    allow_missing: bool = True,
) -> dict[str, Any]:
    """Read schema authority without creating or writing database/journal files."""
    database = Path(path)
    if not database.exists():
        if allow_missing:
            return {"status": "FRESH_INSTALL", "schema_version": None}
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_METADATA_INVALID",
            "Database file is missing; initialize it before running a migration.",
        )
    if not database.is_file():
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_READ_FAILED",
            "Database path is not a readable SQLite file; no startup mutation was attempted.",
        )

    # Opening a WAL database read-only can still update its shared-memory index.
    # A hot rollback journal may also be replayed by SQLite during open. Refuse
    # these ambiguous states before connecting, so preflight never changes them.
    if Path(str(database) + "-wal").exists() or Path(str(database) + "-shm").exists():
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_WAL_PENDING",
            "SQLite WAL/shared-memory sidecars are present. Startup is blocked without opening or changing the database; use a schema-compatible build to resolve the journal state.",
        )
    if Path(str(database) + "-journal").exists():
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_RECOVERY_PENDING",
            "An SQLite rollback journal is present. Startup and automatic recovery are blocked without opening or changing the database.",
        )

    conn: sqlite3.Connection | None = None
    try:
        # A file: URI with mode=ro prevents SQLite from creating a missing file or
        # accepting writes during this preflight.
        uri = database.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.execute("PRAGMA query_only=ON")
        schema_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if schema_table is None:
            raise DatabaseSchemaGuardError(
                "DATABASE_SCHEMA_METADATA_INVALID",
                "Existing database has no schema_meta table; startup and recovery are blocked without changing it.",
            )
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(schema_meta)")
        }
        if not {"key", "value"}.issubset(columns):
            raise DatabaseSchemaGuardError(
                "DATABASE_SCHEMA_METADATA_INVALID",
                "Existing database schema_meta table is malformed; startup and recovery are blocked without changing it.",
            )
        versions = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchall()
    except DatabaseSchemaGuardError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_READ_FAILED",
            "SQLite schema metadata could not be read safely; startup and recovery are blocked without changing the database.",
        ) from exc
    finally:
        if conn is not None:
            conn.close()

    if len(versions) != 1 or not isinstance(versions[0][0], str):
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_METADATA_INVALID",
            "Existing database must contain exactly one textual schema_version; startup and recovery are blocked without changing it.",
        )
    raw_version = versions[0][0]
    if not raw_version.isascii() or not raw_version.isdecimal() or raw_version.startswith("0"):
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_METADATA_INVALID",
            "Existing database schema_version is malformed; startup and recovery are blocked without changing it.",
        )
    version = int(raw_version)
    if version < MIN_SUPPORTED_SCHEMA:
        raise DatabaseSchemaGuardError(
            "DATABASE_SCHEMA_METADATA_INVALID",
            "Existing database schema_version is outside the supported migration history; startup and recovery are blocked without changing it.",
            database_schema_version=version,
        )
    if version == 16:
        conn = None
        try:
            conn = open_read_only(database)
            profile_error = _schema16_archive_profile_error(conn)
        except sqlite3.Error as exc:
            raise DatabaseSchemaGuardError(
                "DATABASE_SCHEMA_READ_FAILED",
                "The historical schema-16 structure could not be verified read-only; the database was not modified.",
                database_schema_version=version,
            ) from exc
        finally:
            if conn is not None:
                conn.close()
        if profile_error is not None:
            raise DatabaseSchemaGuardError(
                "INCOMPATIBLE_SCHEMA_LAYOUT",
                f"Schema 16 does not match the verified historical archive compatibility profile ({profile_error}); the database was not modified.",
                database_schema_version=version,
            )
        return {
            "status": "SUPPORTED_EXTENSION",
            "schema_version": version,
            "compatibility_profile": SCHEMA16_ARCHIVE_COMPATIBILITY,
        }
    if version > CURRENT_SCHEMA_VERSION:
        raise DatabaseSchemaGuardError(
            "INCOMPATIBLE_NEWER_SCHEMA",
            f"Database schema {version} is newer than this application supports ({CURRENT_SCHEMA_VERSION}). The database was not modified; use a compatible application build.",
            database_schema_version=version,
        )
    return {"status": "SUPPORTED", "schema_version": version}


def connect(path: Path = DATABASE_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def initialize_database(path: Path = DATABASE_PATH) -> None:
    schema = assert_database_schema_compatible(path, allow_missing=True)
    if is_schema16_archive_compatible(schema):
        return
    with connect(path) as conn:
        schema_meta_preexisting = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_meta'"
        ).fetchone() is not None
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ea_baseline (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ea_version TEXT NOT NULL,
                source_project TEXT NOT NULL,
                source_candidate_build_id TEXT NOT NULL,
                source_tree_signature TEXT NOT NULL,
                source_path TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status = 'BASELINE_NOT_CHAMPION'),
                imported_at_utc TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        if not schema_meta_preexisting:
            manifest = verify_baseline_snapshot()
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key,value) "
                "VALUES('strategy_epoch',?)",
                (STRATEGY_CONTRACT,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta(key,value) "
                "VALUES('strategy_epoch_baseline_sha256',?)",
                (str(manifest["snapshot_sha256"]),),
            )


def ensure_baseline_registered(path: Path = DATABASE_PATH) -> dict[str, Any]:
    schema = assert_database_schema_compatible(path, allow_missing=True)
    manifest = verify_baseline_snapshot()
    values = (
        manifest["ea_version"],
        manifest["source_project"],
        manifest["source_candidate_build_id"],
        manifest["source_tree_signature"],
        manifest["source_path"],
        manifest["snapshot_relative_path"],
        manifest["snapshot_sha256"],
        "BASELINE_NOT_CHAMPION",
        manifest["imported_at_utc"],
    )
    if is_schema16_archive_compatible(schema):
        expected = dict(
            zip(
                (
                    "ea_version",
                    "source_project",
                    "source_candidate_build_id",
                    "source_tree_signature",
                    "source_path",
                    "snapshot_path",
                    "sha256",
                    "status",
                    "imported_at_utc",
                ),
                values,
                strict=True,
            )
        )
        conn = open_read_only(path)
        try:
            row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        finally:
            conn.close()
        if row is None:
            raise DatabaseSchemaGuardError(
                "SCHEMA16_BASELINE_MISSING",
                "A schema-16 compatibility database must already contain its registered baseline; startup will not insert or repair it.",
                database_schema_version=16,
                compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
            )
        if any(row[key] != value for key, value in expected.items()):
            raise DatabaseSchemaGuardError(
                "SCHEMA16_BASELINE_MISMATCH",
                "The persisted baseline differs from source authority; schema-16 startup is blocked without changing the database.",
                database_schema_version=16,
                compatibility_profile=SCHEMA16_ARCHIVE_COMPATIBILITY,
            )
        return dict(row)
    initialize_database(path)
    with connect(path) as conn:
        current = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        if current is not None and current["sha256"] != manifest["snapshot_sha256"]:
            raise RuntimeError("Registered EA baseline differs; explicit re-registration required")
        if current is None:
            conn.execute(
                """
                INSERT INTO ea_baseline(
                    id,ea_version,source_project,source_candidate_build_id,
                    source_tree_signature,source_path,snapshot_path,sha256,
                    status,imported_at_utc
                ) VALUES(1,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
        row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
        return dict(row)


def read_baseline(path: Path = DATABASE_PATH) -> dict[str, Any]:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("EA baseline row missing")
    return dict(row)


def database_status(path: Path = DATABASE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "FAIL", "reason": "DATABASE_FILE_MISSING"}
    try:
        # Status reads are still an application entrypoint: never open the
        # database through the normal connection unless this build supports its
        # persisted schema. In particular, a newer database must not look READY.
        schema_authority = assert_database_schema_compatible(path, allow_missing=False)
    except DatabaseSchemaGuardError as exc:
        return exc.diagnostic()

    try:
        connection = open_read_only(path) if is_schema16_archive_compatible(schema_authority) else connect(path)
        with connection as conn:
            schema = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            baseline = conn.execute(
                "SELECT status FROM ea_baseline WHERE id=1"
            ).fetchone()
        if schema is None:
            return {"status": "FAIL", "reason": "SCHEMA_VERSION_MISSING"}
        actual_schema = int(schema["value"])
        if actual_schema < MIN_SUPPORTED_SCHEMA:
            return {
                "status": "FAIL",
                "reason": "SCHEMA_VERSION_MISMATCH",
                "schema_version": actual_schema,
            }
        if baseline is None:
            return {"status": "FAIL", "reason": "EA_BASELINE_ROW_MISSING"}
        if baseline["status"] != "BASELINE_NOT_CHAMPION":
            return {
                "status": "FAIL",
                "reason": "EA_BASELINE_STATUS_INVALID",
                "baseline_status": baseline["status"],
            }
        result = {
            "status": "READY",
            "schema_version": actual_schema,
            "baseline_status": baseline["status"],
        }
        if is_schema16_archive_compatible(schema_authority):
            result["compatibility_profile"] = SCHEMA16_ARCHIVE_COMPATIBILITY
        return result
    except Exception as exc:
        return {
            "status": "FAIL",
            "reason": "DATABASE_UNAVAILABLE",
            "detail": str(exc),
        }
