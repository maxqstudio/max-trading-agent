from __future__ import annotations

import json

from max_backend.db import connect, ensure_baseline_registered, initialize_database
from max_backend.schema import CURRENT_SCHEMA_VERSION
from max_backend.workflow_store import migrate_current


def _research_objects(path) -> list[tuple[str, str]]:
    with connect(path) as conn:
        return [
            (str(row["type"]), str(row["name"]))
            for row in conn.execute(
                """
                SELECT type,name FROM sqlite_master
                WHERE substr(lower(name),1,9)='research_'
                  AND type IN ('table','view','trigger','index')
                ORDER BY type,name
                """
            ).fetchall()
        ]


def test_upgrade_purges_retired_research_authority_and_preserves_strategy_state(
    tmp_path,
) -> None:
    database = tmp_path / "state" / "max.db"
    initialize_database(database)
    ensure_baseline_registered(database)
    migrate_current(database)

    with connect(database) as conn:
        baseline_before = dict(conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone())
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
            ("strategy_owner_setting", json.dumps({"retained": True})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
            ("research_sample_configuration_v1", json.dumps({"retained": False})),
        )
        conn.executescript(
            """
            CREATE TABLE research_projects(
                research_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE research_r02_discovery_blocks(
                block_id TEXT PRIMARY KEY,
                research_id TEXT NOT NULL REFERENCES research_projects(research_id)
            );
            CREATE TRIGGER research_retired_immutable
            BEFORE DELETE ON research_projects
            BEGIN SELECT RAISE(ABORT,'RESEARCH_IMMUTABLE'); END;
            """
        )
        conn.execute(
            "INSERT INTO research_projects VALUES('RESEARCH-OLD','COMPLETE')"
        )
        conn.execute(
            "INSERT INTO research_r02_discovery_blocks VALUES('BLOCK-OLD','RESEARCH-OLD')"
        )
        conn.execute(
            """
            INSERT INTO artifact_registry(
                artifact_id,artifact_type,producer,owner_type,owner_id,
                created_utc,updated_utc,canonical_path,status,retention_class
            ) VALUES('ART-RESEARCH','RESEARCH_DATASET','RESEARCH_R01',
                     'RESEARCH_R01','RESEARCH-OLD','now','now',
                     'C:\\retired\\research','RETIRED','REGENERABLE')
            """
        )

    migrate_current(database)

    assert _research_objects(database) == []
    with connect(database) as conn:
        baseline_after = dict(conn.execute("SELECT * FROM ea_baseline WHERE id=1").fetchone())
        strategy_setting = conn.execute(
            "SELECT value FROM schema_meta WHERE key='strategy_owner_setting'"
        ).fetchone()
        research_setting = conn.execute(
            "SELECT value FROM schema_meta WHERE key='research_sample_configuration_v1'"
        ).fetchone()
        research_artifacts = int(conn.execute(
            "SELECT COUNT(*) AS n FROM artifact_registry WHERE upper(owner_type) LIKE 'RESEARCH%'"
        ).fetchone()["n"])
        schema = int(conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"])
        assert conn.execute("SELECT 1 FROM optimizer_jobs LIMIT 1").fetchone() is None

    assert baseline_after == baseline_before
    assert json.loads(strategy_setting["value"]) == {"retained": True}
    assert research_setting is None
    assert research_artifacts == 0
    assert schema == CURRENT_SCHEMA_VERSION
