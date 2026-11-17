"""M06 HMAC, local key, and legacy migration checks (synthetic only)."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.identity import (
    IdentityError, load_or_create_key, seal_snapshot, verify_snapshot,
)
from max_trading_agent.signal_research import market_context
from max_trading_agent.watcher import WatchState
from test_watcher import fixture

KEY = bytes(range(32))
WRONG = bytes(reversed(range(32)))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_key_reuse_and_corruption_never_resets_secret(self):
        file = self.root / "private" / "mt5.key"
        key = load_or_create_key(file)
        self.assertEqual(len(key), 32)
        self.assertEqual(load_or_create_key(file), key)
        if os.name == "posix":
            self.assertEqual(file.stat().st_mode & 0o077, 0)
        file.write_bytes(b"broken")
        with self.assertRaisesRegex(IdentityError, "INVALID_IDENTITY_KEY"):
            load_or_create_key(file)
        self.assertEqual(file.read_bytes(), b"broken")

    @unittest.skipIf(os.name != "posix", "POSIX permissions")
    def test_permissive_file_mode_rejected(self):
        file = self.root / "mt5.key"
        load_or_create_key(file)
        file.chmod(0o644)
        with self.assertRaisesRegex(IdentityError, "IDENTITY_KEY_PERMISSIONS"):
            load_or_create_key(file)

    def test_symlink_key_rejected(self):
        target = self.root / "target"
        target.write_bytes(KEY)
        link = self.root / "link"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks disabled")
        with self.assertRaisesRegex(IdentityError, "IDENTITY_KEY_SYMLINK"):
            load_or_create_key(link)

    def test_snapshot_mac_tamper_detection_and_llm_privacy(self):
        snap = fixture(datetime.now(timezone.utc) - timedelta(minutes=2))
        self.assertEqual(len(verify_snapshot(snap, KEY)), 64)
        public = market_context(snap)
        self.assertNotIn("provenance", public)
        self.assertNotIn("account", public)
        self.assertNotIn("PRIVATE_BROKER", json.dumps(public))
        changed = json.loads(json.dumps(snap))
        changed["account"]["balance"] += 1
        with self.assertRaisesRegex(IdentityError, "SNAPSHOT_MAC_MISMATCH"):
            verify_snapshot(changed, KEY)
        with self.assertRaisesRegex(IdentityError, "SNAPSHOT_MAC_MISMATCH"):
            verify_snapshot(snap, WRONG)

    def test_missing_provenance_and_bad_identity_rejected(self):
        snap = {"source": "MT5_LOCAL_TERMINAL", "mode": "READ_ONLY",
                "execution": "DISABLED"}
        with self.assertRaisesRegex(IdentityError, "SNAPSHOT_PROVENANCE_MISSING"):
            verify_snapshot(snap, KEY)
        with self.assertRaisesRegex(IdentityError, "ACCOUNT_ID_UNAVAILABLE"):
            seal_snapshot(snap, KEY, login=0, server="X")
        with self.assertRaisesRegex(IdentityError, "ACCOUNT_ID_UNAVAILABLE"):
            seal_snapshot(snap, KEY, login=123, server="")

    def test_legacy_watcher_db_is_quarantined_not_rebound(self):
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE watch_state(
                symbol TEXT NOT NULL,timeframe TEXT NOT NULL,
                bar_utc TEXT NOT NULL,bar_hash TEXT NOT NULL,
                account_mode TEXT NOT NULL,seen_utc TEXT NOT NULL,
                PRIMARY KEY(symbol,timeframe))""")
            db.execute("INSERT INTO watch_state VALUES(?,?,?,?,?,?)",(
                "EURUSD","M1","2026-11-17T01:00:00+00:00",
                "old_hash","DEMO","2026-11-17T01:01:00+00:00"))
        state = WatchState(path)
        sample = fixture(datetime.now(timezone.utc) - timedelta(minutes=2))
        outcome = state.observe(
            market_context(sample), "DEMO",
            observed_at=datetime.now(timezone.utc).isoformat(),
            account_binding_id=verify_snapshot(sample, KEY),
        )
        self.assertEqual(outcome["status"], "ACCOUNT_BINDING_REQUIRED")
        self.assertEqual(state.jobs(), [])
        with closing(sqlite3.connect(path)) as db:
            row = db.execute("SELECT bar_utc,account_binding_id FROM watch_state").fetchone()
        self.assertEqual(row[0], "2026-11-17T01:00:00+00:00")
        self.assertIsNone(row[1])


if __name__ == "__main__":
    unittest.main()
