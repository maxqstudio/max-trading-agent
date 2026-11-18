"""Shadow CLI E2E on synthetic snapshots, tested only in GitHub Actions."""
import json
import subprocess
import time
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fixture(when, *, bid, ask):
    candles = [
        {"time_utc": (when - timedelta(minutes=15 * (i + 2))).isoformat(),
         "open": 1.04, "high": 1.06, "low": 1.03, "close": 1.05}
        for i in reversed(range(4))
    ]
    return {
        "source": "MT5_LOCAL_TERMINAL",
        "mode": "READ_ONLY", "execution": "DISABLED",
        "captured_at_utc": when.isoformat(),
        "account": {"login": 987654321, "server": "PRIVATE_SERVER"},
        "symbol": {"name": "EURUSD"}, "timeframe": "M15",
        "quote": {"bid": bid, "ask": ask,
                  "time_utc": (when - timedelta(seconds=1)).isoformat()},
        "candles": candles,
    }


class ShadowCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "market.sqlite3"
        self.journal = self.root / "journal.sqlite3"
        now = datetime.now(timezone.utc)
        self.entry = self.root / "entry.json"
        self.outcome = self.root / "outcome.json"
        self.entry.write_text(json.dumps(fixture(
            now - timedelta(seconds=8), bid=1.0500, ask=1.0502
        )), encoding="utf-8")
        # The outcome must be observed after shadow-open, not prewritten in setUp.

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "max_trading_agent", *args,
             "--market-db", str(self.db), "--journal", str(self.journal)],
            capture_output=True, text=True, timeout=20,
        )

    def test_forward_episode_roundtrip_and_private_data_redaction(self):
        opened = self.cli(
            "shadow-open", "--candidate", "sample_0001",
            "--snapshot", str(self.entry), "--side", "LONG",
            "--slippage", "0.00002", "--fee-per-side", "0.00001",
        )
        self.assertEqual(opened.returncode, 0, opened.stderr)
        episode = json.loads(opened.stdout)["episode"]
        self.assertEqual(episode["status"], "PENDING")
        self.assertFalse(episode["broker_execution"])
        self.assertNotIn("987654321", opened.stdout)
        self.assertNotIn("PRIVATE_SERVER", opened.stdout)

        # A new market observation is generated after the candidate exists.
        # The vendor timestamp is one second behind the capture time.
        time.sleep(1.5)
        self.outcome.write_text(json.dumps(fixture(
            datetime.now(timezone.utc), bid=1.0530, ask=1.0532
        )), encoding="utf-8")
        settled = self.cli(
            "shadow-settle", "--candidate", "sample_0001",
            "--snapshot", str(self.outcome),
        )
        self.assertEqual(settled.returncode, 0, settled.stderr)
        outcome = json.loads(settled.stdout)["episode"]
        self.assertEqual(outcome["status"], "SETTLED")
        self.assertEqual(outcome["mode"], "SHADOW")
        self.assertEqual(outcome["fill_type"], "HYPOTHETICAL")
        self.assertGreater(outcome["net_pnl_price_units"], 0)

        fetched = self.cli("shadow-show", "--candidate", "sample_0001")
        self.assertEqual(fetched.returncode, 0, fetched.stderr)
        self.assertEqual(json.loads(fetched.stdout), outcome)

        replay = self.cli("shadow-settle", "--candidate", "sample_0001",
                          "--snapshot", str(self.outcome))
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout)["episode"], outcome)

    def test_missing_explicit_costs_fail_closed(self):
        result = self.cli(
            "shadow-open", "--candidate", "sample_missing",
            "--snapshot", str(self.entry), "--side", "LONG",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --side --slippage --fee-per-side", result.stderr)


if __name__ == "__main__":
    unittest.main()
