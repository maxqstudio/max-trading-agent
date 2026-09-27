from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "reset_strategy_epoch.py"


def load_reset_module():
    spec = importlib.util.spec_from_file_location("m07_reset_strategy_epoch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_database(path: Path, baseline_sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE ea_baseline(
                id INTEGER PRIMARY KEY,
                ea_version TEXT NOT NULL,
                source_project TEXT NOT NULL,
                source_candidate_build_id TEXT NOT NULL,
                source_tree_signature TEXT NOT NULL,
                source_path TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                imported_at_utc TEXT NOT NULL
            );
            CREATE TABLE optimizer_jobs(job_id TEXT PRIMARY KEY,active INTEGER NOT NULL);
            CREATE TABLE optimizer_rounds(id INTEGER);
            CREATE TABLE strategy_challengers(id INTEGER);
            CREATE TABLE strategy_challenger_backtests(id INTEGER,state TEXT NOT NULL);
            CREATE TABLE strategy_challenger_retirements(id INTEGER);
            CREATE TABLE strategy_promotions(id INTEGER,state TEXT NOT NULL);
            CREATE TABLE strategy_champions(
                strategy_id TEXT,status TEXT NOT NULL,deployment_json TEXT NOT NULL
            );
            CREATE TABLE scientist_threads(id INTEGER);
            CREATE TABLE scientist_messages(id INTEGER);
            CREATE TABLE scientist_chat_requests(id INTEGER,state TEXT NOT NULL);
            """
        )
        conn.execute("INSERT INTO schema_meta VALUES('schema_version','7')")
        conn.execute(
            """
            INSERT INTO ea_baseline VALUES(
                1,'2.00','old','old','old','old','old',?,
                'BASELINE_NOT_CHAMPION','old'
            )
            """,
            (baseline_sha,),
        )
        conn.execute("INSERT INTO optimizer_jobs VALUES('OLD-JOB',0)")
        conn.execute("INSERT INTO optimizer_rounds VALUES(1)")
        conn.execute("INSERT INTO strategy_challengers VALUES(1)")
        conn.execute("INSERT INTO strategy_challenger_backtests VALUES(1,'COMPLETED')")
        conn.execute("INSERT INTO strategy_challenger_retirements VALUES(1)")
        conn.execute("INSERT INTO strategy_promotions VALUES(1,'COMMITTED')")
        conn.execute(
            "INSERT INTO strategy_champions VALUES('OLD-CHAMP','CURRENT','{}')"
        )
        conn.execute("INSERT INTO scientist_threads VALUES(1)")
        conn.execute("INSERT INTO scientist_messages VALUES(1)")
        conn.execute("INSERT INTO scientist_chat_requests VALUES(1,'COMPLETED')")
        conn.commit()
    finally:
        conn.close()


def copy_new_baseline(root: Path) -> dict:
    source_root = ROOT / "ea" / "baseline"
    target = root / "ea" / "baseline"
    target.mkdir(parents=True, exist_ok=True)
    (target / "Max_MTF.mq5").write_bytes((source_root / "Max_MTF.mq5").read_bytes())
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def install_runtime_authority(root: Path, db: Path) -> dict[str, Path]:
    paths = {
        "project_champion_ea": root / "ea" / "champion" / "current" / "Max_MTF.mq5",
        "project_champion_set": root / "ea" / "champion" / "current" / "Max_MTF.set",
        "mt5_source": root / "runtime" / "Max_MTF.mq5",
        "mt5_ex5": root / "runtime" / "Max_MTF.ex5",
        "tester_set": root / "runtime" / "Max_MTF.set",
    }
    for index, path in enumerate(paths.values(), start=1):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"runtime-authority-{index}".encode("ascii"))
    deployment = {
        key: str(paths[key]) for key in ("mt5_source", "mt5_ex5", "tester_set")
    }
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE strategy_champions SET deployment_json=? WHERE status='CURRENT'",
            (json.dumps(deployment),),
        )
        conn.commit()
    finally:
        conn.close()
    return paths


def install_settings_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    localappdata = tmp_path / "localappdata"
    settings_root = localappdata / "MAX_REBUILD"
    settings_root.mkdir(parents=True, exist_ok=True)
    (settings_root / "settings.json").write_text('{"provider":"fixture"}', encoding="utf-8")
    (settings_root / "settings.backup.json").write_text('{"provider":"fixture"}', encoding="utf-8")
    (settings_root / "llm_api_key.dpapi").write_bytes(b"fixture-dpapi")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))


def test_epoch_reset_dry_run_is_non_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = load_reset_module()
    root = tmp_path / "root"
    manifest = copy_new_baseline(root)
    db = root / "state" / "max.db"
    make_database(db, "old-baseline")
    monkeypatch.setattr(reset, "settings_fingerprint", lambda: {"settings": "same"})

    result = reset.reset_strategy_epoch(
        database=db,
        root=root,
        evidence_root=root / "evidence" / "m07" / "epoch_reset",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["previous_current_champion"] == "OLD-CHAMP"
    assert result["previous_counts"]["optimizer_jobs"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategy_champions").fetchone()[0] == 1
        assert conn.execute("SELECT sha256 FROM ea_baseline").fetchone()[0] == "old-baseline"
    assert manifest["snapshot_sha256"] != "old-baseline"


def test_epoch_reset_requires_exact_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = load_reset_module()
    root = tmp_path / "root"
    copy_new_baseline(root)
    db = root / "state" / "max.db"
    make_database(db, "old-baseline")
    monkeypatch.setattr(reset, "settings_fingerprint", lambda: {"settings": "same"})

    with pytest.raises(reset.ResetError, match="EXPLICIT_STRATEGY_EPOCH_RESET_CONFIRMATION_REQUIRED"):
        reset.reset_strategy_epoch(
            database=db,
            root=root,
            evidence_root=root / "evidence" / "m07" / "epoch_reset",
            dry_run=False,
            confirmation="WRONG",
        )


def test_epoch_reset_backups_then_clears_operational_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = load_reset_module()
    root = tmp_path / "root"
    manifest = copy_new_baseline(root)
    db = root / "state" / "max.db"
    make_database(db, "old-baseline")
    monkeypatch.setattr(reset, "settings_fingerprint", lambda: {"settings": "same"})

    evidence = root / "evidence" / "m07" / "epoch_reset"
    result = reset.reset_strategy_epoch(
        database=db,
        root=root,
        evidence_root=evidence,
        dry_run=False,
        confirmation=reset.CONFIRMATION,
    )

    backup = Path(result["backup"]["path"])
    assert backup.is_file()
    assert reset.sha256_file(backup) == result["backup"]["sha256"]
    assert (evidence / "reset_manifest.json").is_file()

    state = reset.inspect_state(db)
    assert state["current_champion"] is None
    assert state["counts"]["champions"] == 0
    assert state["counts"]["challengers"] == 0
    assert state["counts"]["optimizer_jobs"] == 0
    assert state["counts"]["backtests"] == 0
    assert state["counts"]["retirements"] == 0
    assert state["counts"]["promotions"] == 0
    assert state["counts"]["scientist_threads"] == 0
    assert state["baseline"]["sha256"] == manifest["snapshot_sha256"]
    assert state["baseline"]["ea_version"] == "2.11"
    assert state["baseline"]["status"] == "BASELINE_NOT_CHAMPION"
    with sqlite3.connect(db) as conn:
        epoch = conn.execute(
            "SELECT value FROM schema_meta WHERE key='strategy_epoch'"
        ).fetchone()[0]
    assert epoch == "MAX_TRUE_MTF_DYNAMIC_V1"



def test_partial_runtime_remove_failure_restores_everything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = load_reset_module()
    root = tmp_path / "root"
    copy_new_baseline(root)
    db = root / "state" / "max.db"
    make_database(db, "old-baseline")
    runtime_paths = install_runtime_authority(root, db)
    install_settings_fixture(tmp_path, monkeypatch)

    db_sha_before = reset.sha256_file(db)
    runtime_sha_before = {
        key: reset.sha256_file(path) for key, path in runtime_paths.items()
    }
    settings_before = reset.settings_fingerprint()
    original_unlink = reset._unlink_runtime_file
    calls = {"count": 0}

    def fail_second_unlink(path: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise PermissionError("INJECTED_RUNTIME_REMOVE_FAILURE")
        original_unlink(path)

    monkeypatch.setattr(reset, "_unlink_runtime_file", fail_second_unlink)
    with pytest.raises(PermissionError, match="INJECTED_RUNTIME_REMOVE_FAILURE"):
        reset.reset_strategy_epoch(
            database=db,
            root=root,
            evidence_root=root / "evidence" / "m07" / "epoch_reset",
            dry_run=False,
            confirmation=reset.CONFIRMATION,
        )

    assert reset.sha256_file(db) == db_sha_before
    state = reset.inspect_state(db)
    assert state["current_champion"]["strategy_id"] == "OLD-CHAMP"
    assert state["baseline"]["sha256"] == "old-baseline"
    for key, path in runtime_paths.items():
        assert path.is_file(), key
        assert reset.sha256_file(path) == runtime_sha_before[key]
    assert reset.settings_fingerprint() == settings_before


def test_database_mutation_failure_restores_exact_db_and_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset = load_reset_module()
    root = tmp_path / "root"
    copy_new_baseline(root)
    db = root / "state" / "max.db"
    make_database(db, "old-baseline")
    runtime_paths = install_runtime_authority(root, db)
    install_settings_fixture(tmp_path, monkeypatch)

    db_sha_before = reset.sha256_file(db)
    runtime_sha_before = {
        key: reset.sha256_file(path) for key, path in runtime_paths.items()
    }
    settings_before = reset.settings_fingerprint()

    def fail_after_committed_mutation(database: Path, manifest: dict) -> None:
        del manifest
        conn = sqlite3.connect(database)
        try:
            conn.execute("DELETE FROM strategy_champions")
            conn.execute("UPDATE ea_baseline SET sha256='injected-new-baseline' WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        raise RuntimeError("INJECTED_DATABASE_MUTATION_FAILURE")

    monkeypatch.setattr(reset, "mutate_database", fail_after_committed_mutation)
    with pytest.raises(RuntimeError, match="INJECTED_DATABASE_MUTATION_FAILURE"):
        reset.reset_strategy_epoch(
            database=db,
            root=root,
            evidence_root=root / "evidence" / "m07" / "epoch_reset",
            dry_run=False,
            confirmation=reset.CONFIRMATION,
        )

    assert reset.sha256_file(db) == db_sha_before
    state = reset.inspect_state(db)
    assert state["current_champion"]["strategy_id"] == "OLD-CHAMP"
    assert state["baseline"]["sha256"] == "old-baseline"
    for key, path in runtime_paths.items():
        assert path.is_file(), key
        assert reset.sha256_file(path) == runtime_sha_before[key]
    assert reset.settings_fingerprint() == settings_before
