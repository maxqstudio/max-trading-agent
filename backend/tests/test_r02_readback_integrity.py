from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

from max_backend.db import connect
from max_backend.research_contract import candidate_id as derive_candidate_id
from max_backend.research_contract import stable_hash
from max_backend.research_r02_service import r02_preflight
from max_backend.research_r02_store import (
    get_r02_authorization,
    get_r02_discovery_block,
    get_r02_outcome_ledger,
    get_r02_terminal,
    validate_r02_outcome_ledger_integrity,
)
from test_r02_authorization_store import (
    RESEARCH_ID,
    _authorization,
    _database,
    _frozen_block,
    _outcomes,
    _plan,
)


_UPDATE_TRIGGERS = (
    "research_r02_authorization_no_update",
    "research_r02_block_no_update",
    "research_r02_candidate_no_update",
    "research_r02_outcome_no_update",
    "research_r02_terminal_no_update",
)
_DELETE_TRIGGERS = (
    "research_r02_authorization_no_delete",
    "research_r02_block_no_delete",
    "research_r02_candidate_no_delete",
    "research_r02_outcome_no_delete",
    "research_r02_terminal_no_delete",
)


def _drop_test_triggers(conn, *, allow_delete: bool = False) -> None:
    for name in _UPDATE_TRIGGERS:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    if allow_delete:
        for name in _DELETE_TRIGGERS:
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')


def _tamper(db: Path, sql: str, params: tuple = (), *, allow_delete: bool = False) -> None:
    with connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA ignore_check_constraints=ON")
        _drop_test_triggers(conn, allow_delete=allow_delete)
        conn.execute(sql, params)


def _readback(db: Path, reader: Callable):
    with connect(db) as conn:
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        with patch("max_backend.research_r02_store.migrate_current", lambda _path: None):
            return reader()
    return reader()


def _fail_closed(db: Path) -> str:
    with pytest.raises(RuntimeError, match="^R02_LEDGER_INTEGRITY_") as first:
        _readback(
            db,
            lambda: validate_r02_outcome_ledger_integrity(RESEARCH_ID, path=db),
        )
    with pytest.raises(RuntimeError, match="^R02_LEDGER_INTEGRITY_") as replay:
        _readback(
            db,
            lambda: validate_r02_outcome_ledger_integrity(RESEARCH_ID, path=db),
        )
    assert str(first.value) == str(replay.value)
    return str(first.value)


def _complete_ledger(tmp_path: Path) -> tuple[Path, dict]:
    db, block = _frozen_block(tmp_path)
    get_r02_outcome_ledger(RESEARCH_ID, path=db)
    from max_backend.research_r02_store import commit_r02_terminal_outcomes

    ledger = commit_r02_terminal_outcomes(RESEARCH_ID, _outcomes(block), path=db)
    return db, ledger


def test_complete_ledger_is_verified_and_replay_is_identical(tmp_path: Path) -> None:
    db, expected = _complete_ledger(tmp_path)
    first = get_r02_outcome_ledger(RESEARCH_ID, path=db)
    second = get_r02_outcome_ledger(RESEARCH_ID, path=db)
    verification = validate_r02_outcome_ledger_integrity(RESEARCH_ID, path=db)

    assert first is not None
    assert first == second == expected
    assert first["integrity_status"] == "VERIFIED"
    assert verification == first
    assert [row["outcome"]["status"] for row in first["outcomes"]] == [
        "SCREEN_PASS",
        "SCREEN_FAIL",
        "EXECUTION_ERROR",
    ]
    assert first["outcomes"][0]["outcome"]["cheap_screen_qualification_authority"] is False
    assert first["outcomes"][0]["outcome"]["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert first["outcomes"][0]["outcome"]["scientific_qualification"] is False


def _install_valid_preflight(monkeypatch: pytest.MonkeyPatch, research_id: str) -> None:
    from max_backend import research_r02_service as service

    monkeypatch.setattr(
        service,
        "latest_research",
        lambda **_kwargs: {"research_id": research_id},
    )
    monkeypatch.setattr(
        service,
        "get_r01_run",
        lambda *_args, **_kwargs: {
            "research_id": research_id,
            "state": "PASS_WAITING_OWNER",
            "output_manifest_sha": "f" * 64,
        },
    )
    monkeypatch.setattr(
        service,
        "validate_r01_integrity",
        lambda **_kwargs: {"research_id": research_id, "status": "VERIFIED"},
    )
    monkeypatch.setattr(
        service,
        "verify_no_training_side_effects",
        lambda *_args, **_kwargs: {
            "training_count": 0,
            "onnx_count": 0,
            "research_challenger_count": 0,
            "champion_mutation": "NONE",
            "status": "PASS",
        },
    )


def test_preflight_reports_complete_only_after_verified_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _install_valid_preflight(monkeypatch, RESEARCH_ID)

    result = r02_preflight(path=db)

    assert result["status"] == "COMPLETE_WAITING_OWNER"
    assert result["cheap_screen_qualification_authority"] is False
    assert result["qualified_pool_admission_authority"] == "R03_FULL_WFA_ONLY"
    assert result["runtime_start_available"] is False


def test_preflight_fails_closed_for_corrupt_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(
        db,
        "UPDATE research_r02_block_terminals SET outcome_manifest_sha256=?",
        ("0" * 64,),
    )
    _install_valid_preflight(monkeypatch, RESEARCH_ID)

    with pytest.raises(RuntimeError, match="^R02_LEDGER_INTEGRITY_"):
        _readback(db, lambda: r02_preflight(path=db))


@pytest.mark.parametrize(
    ("field", "sql", "params"),
    [
        (
            "payload",
            "UPDATE research_r02_authorizations SET payload_json=?",
            (json.dumps({"gate": "R02", "research_id": RESEARCH_ID}, sort_keys=True, separators=(",", ":")),),
        ),
        (
            "payload_sha256",
            "UPDATE research_r02_authorizations SET payload_sha256=?",
            ("0" * 64,),
        ),
        (
            "authorization_id",
            "UPDATE research_r02_authorizations SET authorization_id=?",
            ("RAUTH-R02-TAMPERED",),
        ),
        (
            "confirmed",
            "UPDATE research_r02_authorizations SET confirmed=0",
            (),
        ),
    ],
)
def test_authorization_readback_corruption_fails_closed(
    tmp_path: Path,
    field: str,
    sql: str,
    params: tuple,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(db, sql, params)
    assert "R02_LEDGER_INTEGRITY_" in _fail_closed(db)
    with pytest.raises(RuntimeError, match="^R02_LEDGER_INTEGRITY_"):
        _readback(
            db,
            lambda: get_r02_authorization(
                _authorization(_plan())["authorization_id"],
                path=db,
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gate", "R03"),
        ("action", "AUTHORIZE_OTHER"),
        ("research_id", "OTHER"),
        ("plan_id", "RPLAN-TAMPERED"),
        ("plan_sha256", "a" * 64),
        ("r01_output_manifest_sha256", "b" * 64),
    ],
)
def test_resealed_authorization_payload_semantics_fail_closed(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    with connect(db) as conn:
        row = conn.execute(
            "SELECT payload_json FROM research_r02_authorizations"
        ).fetchone()
    payload = json.loads(row["payload_json"])
    payload[field] = value
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_sha = stable_hash(payload)
    _tamper(
        db,
        "UPDATE research_r02_authorizations SET authorization_id=?, payload_sha256=?, payload_json=?",
        ("RAUTH-R02-" + payload_sha[:24], payload_sha, payload_json),
    )
    _fail_closed(db)


@pytest.mark.parametrize(
    ("field", "sql", "params"),
    [
        ("research_id", "UPDATE research_r02_discovery_blocks SET research_id=?", ("OTHER",)),
        ("block_id", "UPDATE research_r02_discovery_blocks SET block_id=?", ("RDISC-TAMPERED",)),
        ("authorization_id", "UPDATE research_r02_discovery_blocks SET authorization_id=?", ("RAUTH-R02-OTHER",)),
        ("r01 binding", "UPDATE research_r02_discovery_blocks SET r01_output_manifest_sha256=?", ("1" * 64,)),
        ("state", "UPDATE research_r02_discovery_blocks SET state=?", ("CORRUPT",)),
        ("plan_id", "UPDATE research_r02_discovery_blocks SET plan_id=?", ("RPLAN-TAMPERED",)),
        ("plan_sha256", "UPDATE research_r02_discovery_blocks SET plan_sha256=?", ("2" * 64,)),
        ("candidate_count", "UPDATE research_r02_discovery_blocks SET candidate_count=4", ()),
        (
            "compute_budget",
            "UPDATE research_r02_discovery_blocks SET compute_budget_json=?",
            (json.dumps({"execution_semantics": "FROZEN_ONLY_NOT_EXECUTED", "unit": "FIT_SECONDS", "value": 121}, sort_keys=True, separators=(",", ":")),),
        ),
    ],
)
def test_frozen_block_readback_corruption_fails_closed(
    tmp_path: Path,
    field: str,
    sql: str,
    params: tuple,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(db, sql, params)
    _fail_closed(db)


@pytest.mark.parametrize(
    ("field", "sql", "params"),
    [
        (
            "spec_json",
            "UPDATE research_r02_candidate_specs SET spec_json=? WHERE ordinal=0",
            (),
        ),
        (
            "spec_sha256",
            "UPDATE research_r02_candidate_specs SET spec_sha256=? WHERE ordinal=0",
            ("3" * 64,),
        ),
        (
            "candidate_id",
            "UPDATE research_r02_candidate_specs SET candidate_id=? WHERE ordinal=0",
            ("RCAND-TAMPERED",),
        ),
        (
            "block_id",
            "UPDATE research_r02_candidate_specs SET block_id=? WHERE ordinal=0",
            ("RDISC-OTHER",),
        ),
        (
            "model_family",
            "UPDATE research_r02_candidate_specs SET model_family=? WHERE ordinal=0",
            ("tampered-family",),
        ),
        (
            "seed",
            "UPDATE research_r02_candidate_specs SET seed=999 WHERE ordinal=0",
            (),
        ),
        (
            "ordinal",
            "UPDATE research_r02_candidate_specs SET ordinal=9 WHERE ordinal=0",
            (),
        ),
    ],
)
def test_candidate_spec_readback_corruption_fails_closed(
    tmp_path: Path,
    field: str,
    sql: str,
    params: tuple,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    if field == "spec_json":
        with connect(db) as conn:
            spec = json.loads(
                conn.execute(
                    "SELECT spec_json FROM research_r02_candidate_specs WHERE ordinal=0"
                ).fetchone()["spec_json"]
            )
        spec["topology_spec"]["depth"] = 99
        params = (json.dumps(spec, sort_keys=True, separators=(",", ":")),)
    _tamper(db, sql, params)
    _fail_closed(db)


def test_missing_candidate_fails_closed(tmp_path: Path) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(
        db,
        "DELETE FROM research_r02_candidate_specs WHERE ordinal=0",
        allow_delete=True,
    )
    _fail_closed(db)


def test_extra_candidate_fails_closed(tmp_path: Path) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    with connect(db) as conn:
        first = dict(
            conn.execute(
                "SELECT * FROM research_r02_candidate_specs WHERE ordinal=0"
            ).fetchone()
        )
    spec = json.loads(first["spec_json"])
    spec["seed"] += 1000
    candidate_id = derive_candidate_id(spec)
    _tamper(
        db,
        """
        INSERT INTO research_r02_candidate_specs(
            candidate_id,block_id,ordinal,model_family,seed,spec_sha256,
            spec_json,created_utc
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            first["block_id"],
            3,
            spec["model_family"],
            spec["seed"],
            stable_hash(spec),
            json.dumps(spec, sort_keys=True, separators=(",", ":")),
            first["created_utc"],
        ),
    )
    _fail_closed(db)


@pytest.mark.parametrize(
    ("field", "sql", "params"),
    [
        (
            "outcome_json",
            "UPDATE research_r02_candidate_outcomes SET outcome_json=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            (),
        ),
        (
            "outcome_sha256",
            "UPDATE research_r02_candidate_outcomes SET outcome_sha256=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            ("4" * 64,),
        ),
        (
            "outcome_id",
            "UPDATE research_r02_candidate_outcomes SET outcome_id=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            ("ROUT-TAMPERED",),
        ),
        (
            "status",
            "UPDATE research_r02_candidate_outcomes SET status='SCREEN_FAIL' WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            (),
        ),
        (
            "candidate_id",
            "UPDATE research_r02_candidate_outcomes SET candidate_id=? WHERE outcome_id=(SELECT outcome_id FROM research_r02_candidate_outcomes LIMIT 1)",
            ("RCAND-TAMPERED",),
        ),
        (
            "block_id",
            "UPDATE research_r02_candidate_outcomes SET block_id=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            ("RDISC-OTHER",),
        ),
        (
            "compute_consumed",
            "UPDATE research_r02_candidate_outcomes SET outcome_json=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
            (),
        ),
    ],
)
def test_outcome_readback_corruption_fails_closed(
    tmp_path: Path,
    field: str,
    sql: str,
    params: tuple,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    if field in {"outcome_json", "compute_consumed"}:
        with connect(db) as conn:
            raw = conn.execute(
                "SELECT outcome_json FROM research_r02_candidate_outcomes "
                "WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)"
            ).fetchone()["outcome_json"]
        outcome = json.loads(raw)
        if field == "outcome_json":
            outcome["metrics"]["proxy_score"] = 0.123
        else:
            outcome["compute_consumed"]["value"] = 119
        params = (json.dumps(outcome, sort_keys=True, separators=(",", ":")),)
    _tamper(db, sql, params)
    _fail_closed(db)


def test_missing_outcome_fails_closed(tmp_path: Path) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(
        db,
        "DELETE FROM research_r02_candidate_outcomes WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
        allow_delete=True,
    )
    _fail_closed(db)


def test_extra_outcome_fails_closed(tmp_path: Path) -> None:
    db, ledger = _complete_ledger(tmp_path)
    block_id = ledger["block"]["block_id"]
    _tamper(
        db,
        """
        INSERT INTO research_r02_candidate_outcomes(
            outcome_id,block_id,candidate_id,status,outcome_sha256,
            outcome_json,created_utc
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            "ROUT-EXTRA",
            block_id,
            "RCAND-EXTRA",
            "SCREEN_PASS",
            "5" * 64,
            "{}",
            "2026-09-29T00:00:00+00:00",
        ),
    )
    _fail_closed(db)


@pytest.mark.parametrize(
    ("field", "sql", "params", "pragmas"),
    [
        ("terminal_id", "UPDATE research_r02_block_terminals SET terminal_id=?", ("RTERM-TAMPERED",), ()),
        ("outcome_manifest_sha256", "UPDATE research_r02_block_terminals SET outcome_manifest_sha256=?", ("6" * 64,), ()),
        ("state", "UPDATE research_r02_block_terminals SET state=?", ("CORRUPT",), ("ignore_check_constraints",)),
        ("candidate_count", "UPDATE research_r02_block_terminals SET candidate_count=99", (), ()),
        ("screen_pass_count", "UPDATE research_r02_block_terminals SET screen_pass_count=99", (), ()),
        ("screen_fail_count", "UPDATE research_r02_block_terminals SET screen_fail_count=99", (), ()),
        ("execution_error_count", "UPDATE research_r02_block_terminals SET execution_error_count=99", (), ()),
        (
            "compute_consumed",
            "UPDATE research_r02_block_terminals SET compute_consumed_json=?",
            (json.dumps({"unit": "FIT_SECONDS", "value": 119}, sort_keys=True, separators=(",", ":")),),
            (),
        ),
    ],
)
def test_terminal_readback_corruption_fails_closed(
    tmp_path: Path,
    field: str,
    sql: str,
    params: tuple,
    pragmas: tuple[str, ...],
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    with connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        if "ignore_check_constraints" in pragmas:
            conn.execute("PRAGMA ignore_check_constraints=ON")
        _drop_test_triggers(conn)
        conn.execute(sql, params)
    _fail_closed(db)


def test_partial_outcome_ledger_fails_closed(tmp_path: Path) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    with connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        _drop_test_triggers(conn, allow_delete=True)
        conn.execute("DELETE FROM research_r02_block_terminals")
        conn.execute(
            "DELETE FROM research_r02_candidate_outcomes WHERE candidate_id IN "
            "(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal IN (1,2))"
        )
    _fail_closed(db)


@pytest.mark.parametrize(
    "delete_sql",
    [
        "DELETE FROM research_r02_authorizations WHERE research_id=?",
        "DELETE FROM research_r02_discovery_blocks WHERE research_id=?",
    ],
    ids=["missing-authorization", "missing-frozen-block"],
)
def test_partial_authorization_block_chain_fails_closed(
    tmp_path: Path,
    delete_sql: str,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(db, delete_sql, (RESEARCH_ID,), allow_delete=True)
    _fail_closed(db)


def test_orphaned_child_rows_without_authorization_or_block_fail_closed(
    tmp_path: Path,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    with connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        _drop_test_triggers(conn, allow_delete=True)
        conn.execute(
            "DELETE FROM research_r02_authorizations WHERE research_id=?",
            (RESEARCH_ID,),
        )
        conn.execute(
            "DELETE FROM research_r02_discovery_blocks WHERE research_id=?",
            (RESEARCH_ID,),
        )

    assert _fail_closed(db) == "R02_LEDGER_INTEGRITY_ORPHANED_ROWS"


def test_outcome_with_missing_semantic_fields_fails_closed(tmp_path: Path) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    _tamper(
        db,
        "UPDATE research_r02_candidate_outcomes SET outcome_json=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)",
        ("{}",),
    )
    _fail_closed(db)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("research_r02_authorizations", "payload_json", "{"),
        ("research_r02_authorizations", "payload_json", '{"bad":NaN}'),
        ("research_r02_discovery_blocks", "compute_budget_json", "{"),
        (
            "research_r02_candidate_specs",
            "spec_json",
            "{",
        ),
        (
            "research_r02_candidate_outcomes",
            "outcome_json",
            "{",
        ),
        (
            "research_r02_block_terminals",
            "compute_consumed_json",
            "{",
        ),
    ],
)
def test_malformed_persisted_json_is_integrity_error(
    tmp_path: Path,
    table: str,
    column: str,
    value: str,
) -> None:
    db, _ledger = _complete_ledger(tmp_path)
    if table == "research_r02_authorizations":
        sql = f"UPDATE {table} SET {column}=?"
        params = (value,)
    elif table == "research_r02_discovery_blocks":
        sql = f"UPDATE {table} SET {column}=?"
        params = (value,)
    elif table == "research_r02_candidate_specs":
        sql = f"UPDATE {table} SET {column}=? WHERE ordinal=0"
        params = (value,)
    elif table == "research_r02_candidate_outcomes":
        sql = f"UPDATE {table} SET {column}=? WHERE candidate_id=(SELECT candidate_id FROM research_r02_candidate_specs WHERE ordinal=0)"
        params = (value,)
    else:
        sql = f"UPDATE {table} SET {column}=?"
        params = (value,)
    _tamper(db, sql, params)
    _fail_closed(db)


def test_all_authoritative_getters_reject_tampered_terminal(tmp_path: Path) -> None:
    db, ledger = _complete_ledger(tmp_path)
    _tamper(
        db,
        "UPDATE research_r02_block_terminals SET candidate_count=99",
    )
    readers: tuple[Callable, ...] = (
        lambda: get_r02_discovery_block(RESEARCH_ID, path=db),
        lambda: get_r02_terminal(RESEARCH_ID, path=db),
        lambda: get_r02_outcome_ledger(RESEARCH_ID, path=db),
        lambda: get_r02_authorization(
            ledger["authorization"]["authorization_id"],
            path=db,
        ),
    )
    for reader in readers:
        with pytest.raises(RuntimeError, match="^R02_LEDGER_INTEGRITY_"):
            _readback(db, reader)


@pytest.mark.parametrize("failure_stage", ["migration", "connection"])
def test_public_read_paths_wrap_database_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    from max_backend import research_r02_store as store

    class BrokenConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            raise sqlite3.OperationalError("SELECT private_table")

    if failure_stage == "migration":
        monkeypatch.setattr(
            store,
            "migrate_current",
            lambda _path: (_ for _ in ()).throw(
                sqlite3.OperationalError("migration private_table")
            ),
        )
    else:
        monkeypatch.setattr(store, "migrate_current", lambda _path: None)
        monkeypatch.setattr(store, "connect", lambda _path: BrokenConnection())

    readers: tuple[Callable, ...] = (
        lambda: get_r02_authorization("RAUTH-R02-TEST", path=tmp_path / "db.sqlite"),
        lambda: get_r02_discovery_block(RESEARCH_ID, path=tmp_path / "db.sqlite"),
        lambda: get_r02_terminal(RESEARCH_ID, path=tmp_path / "db.sqlite"),
        lambda: get_r02_outcome_ledger(RESEARCH_ID, path=tmp_path / "db.sqlite"),
        lambda: validate_r02_outcome_ledger_integrity(
            RESEARCH_ID,
            path=tmp_path / "db.sqlite",
        ),
    )
    for reader in readers:
        with pytest.raises(RuntimeError) as error:
            reader()
        assert str(error.value) == "R02_LEDGER_INTEGRITY_READ_FAILED"
