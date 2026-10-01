from __future__ import annotations

import json

from max_backend.db import connect, initialize_database
from max_backend.optimizer_draft import (
    get_optimizer_draft,
    put_optimizer_draft,
)
from max_backend.workflow_store import migrate_current
from max_backend.schema import CURRENT_SCHEMA_VERSION


def make_database(tmp_path):
    path = tmp_path / "state" / "max.db"
    initialize_database(path)
    migrate_current(path)
    return path


def test_optimizer_draft_survives_database_reopen_and_stores_all_parameter_ranges(tmp_path):
    path = make_database(tmp_path)
    initial = get_optimizer_draft(path=path)

    assert len(initial["draft"]["search_space"]) == 17
    assert set(initial["draft"]["search_space"]) == set(
        initial["draft"]["optimize_params"]
    )

    changed = json.loads(json.dumps(initial["draft"]))
    changed["symbol"] = "XAUUSD.synthetic"
    changed["relative_symbol"] = "EURUSD.synthetic"
    changed["max_rounds"] = 4
    changed["search_space"]["InpEntryThreshold"] = {
        "start": 0.24,
        "step": 0.02,
        "stop": 0.44,
    }
    saved = put_optimizer_draft(changed, revision=initial["revision"] + 1, path=path)

    reopened = get_optimizer_draft(path=path)
    assert saved["status"] == "READY"
    assert reopened["revision"] == saved["revision"]
    assert reopened["draft"] == changed


def test_optimizer_draft_rejects_stale_save_without_overwriting_newer_values(tmp_path):
    path = make_database(tmp_path)
    initial = get_optimizer_draft(path=path)
    newer = json.loads(json.dumps(initial["draft"]))
    newer["symbol"] = "NEW"
    current = put_optimizer_draft(newer, revision=initial["revision"] + 2, path=path)

    stale = json.loads(json.dumps(initial["draft"]))
    stale["symbol"] = "STALE"
    result = put_optimizer_draft(stale, revision=initial["revision"] + 1, path=path)

    assert result["status"] == "STALE_WRITE_IGNORED"
    assert result["draft"] == current["draft"]
    assert get_optimizer_draft(path=path)["draft"]["symbol"] == "NEW"


def test_optimizer_draft_corruption_is_explicit_and_does_not_touch_jobs(tmp_path):
    path = make_database(tmp_path)
    from max_backend.db import connect

    with connect(path) as conn:
        conn.execute(
            "INSERT INTO optimizer_drafts(draft_id,revision,draft_json,updated_utc) "
            "VALUES(1,1,?,?)",
            ("{broken", "test"),
        )

    result = get_optimizer_draft(path=path)
    assert result["status"] == "RECOVERY_REQUIRED"
    assert result["reason"] == "DRAFT_CORRUPT"
    assert result["draft"]["symbol"] == ""
    with connect(path) as conn:
        jobs = conn.execute("SELECT COUNT(*) AS n FROM optimizer_jobs").fetchone()["n"]
    assert jobs == 0


def test_schema_15_upgrades_version_14_optimizer_storage_without_job_rows(tmp_path):
    path = make_database(tmp_path)
    with connect(path) as conn:
        conn.execute("DROP TABLE optimizer_candidate_projection")
        conn.execute("DROP TABLE optimizer_candidate_projection_rounds")
        conn.execute("DROP TABLE optimizer_drafts")
        conn.execute("ALTER TABLE optimizer_jobs DROP COLUMN worker_identity_json")
        conn.execute("ALTER TABLE optimizer_jobs DROP COLUMN launch_token")
        conn.execute("UPDATE schema_meta SET value='14' WHERE key='schema_version'")

    migrate_current(path)

    with connect(path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        job_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(optimizer_jobs)")
        }
        job_count = conn.execute("SELECT COUNT(*) AS n FROM optimizer_jobs").fetchone()["n"]
    assert version == str(CURRENT_SCHEMA_VERSION) == "15"
    assert {
        "optimizer_drafts",
        "optimizer_candidate_projection_rounds",
        "optimizer_candidate_projection",
    }.issubset(tables)
    assert {"launch_token", "worker_identity_json"}.issubset(job_columns)
    assert job_count == 0
