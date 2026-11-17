"""Full offline CLI roundtrip exercised only by GitHub Actions CI."""
import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MarketCliContractTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.data_path = self.root / "synthetic.csv"
        self.db_path = self.root / "store.sqlite3"
        self.journal_path = self.root / "journal.sqlite3"
        zero = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with self.data_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(("time_utc", "open", "high", "low", "close"))
            for i in range(180):
                center = 1.05 + 0.003 * math.sin(i / 3)
                writer.writerow((
                    (zero + timedelta(minutes=15 * i)).isoformat(),
                    f"{center:.8f}", f"{center+0.002:.8f}",
                    f"{center-0.002:.8f}", f"{center+0.0001:.8f}"
                ))

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "max_trading_agent", *args,
             "--market-db", str(self.db_path),
             "--journal", str(self.journal_path)],
            capture_output=True, text=True, timeout=20,
        )

    def test_import_backtest_inspect_and_replay(self):
        imported = self.cli(
            "market-import", "--file", str(self.data_path),
            "--symbol", "EURUSD", "--timeframe", "M15",
            "--source", "SYNTHETIC_TEST", "--rights", "TEST_ONLY",
            "--quote-basis", "MID",
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        ds = json.loads(imported.stdout)
        self.assertEqual(ds["count"], 180)
        self.assertFalse(ds["existing"])

        imported_again = self.cli(
            "market-import", "--file", str(self.data_path),
            "--symbol", "EURUSD", "--timeframe", "M15",
            "--source", "SYNTHETIC_TEST", "--rights", "TEST_ONLY",
            "--quote-basis", "MID",
        )
        self.assertEqual(imported_again.returncode, 0, imported_again.stderr)
        self.assertTrue(json.loads(imported_again.stdout)["existing"])

        backtest = self.cli(
            "market-backtest", "--dataset", ds["dataset_id"],
            "--spread", "0.0002", "--slippage", "0.00003",
            "--fee-per-side", "0.00001",
        )
        self.assertEqual(backtest.returncode, 0, backtest.stderr)
        outcome = json.loads(backtest.stdout)
        self.assertEqual(outcome["decision"], "NO_TRADE")
        self.assertEqual(outcome["execution"], "DISABLED")
        self.assertIsInstance(outcome["holdout"]["trades"], int)

        fetched = self.cli("experiment-show", "--experiment", outcome["experiment_id"])
        self.assertEqual(fetched.returncode, 0, fetched.stderr)
        record = json.loads(fetched.stdout)
        self.assertEqual(record["result"]["decision"], "NO_TRADE")
        self.assertEqual(record["experiment_id"], outcome["experiment_id"])

        replay = self.cli(
            "market-backtest", "--dataset", ds["dataset_id"],
            "--spread", "0.0002", "--slippage", "0.00003",
            "--fee-per-side", "0.00001",
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout)["experiment_id"],
                         outcome["experiment_id"])

    def test_backtest_requires_explicit_costs_and_valid_dataset(self):
        omitted = self.cli("market-backtest", "--dataset", "fake")
        self.assertNotEqual(omitted.returncode, 0)
        self.assertIn("requires --dataset --spread", omitted.stderr)
        nonexistent = self.cli(
            "market-backtest", "--dataset", "a" * 64, "--spread", "0.0001",
            "--slippage", "0", "--fee-per-side", "0",
        )
        self.assertEqual(nonexistent.returncode, 2)
        self.assertIn("DATASET_NOT_FOUND", nonexistent.stderr)
        self.assertNotIn("order_send", nonexistent.stdout)


if __name__ == "__main__":
    unittest.main()
