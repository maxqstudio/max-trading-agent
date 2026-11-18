"""M09 bounded owner-only MT5 feed checks, fake-terminal CI (no real accounts)."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.identity import seal_snapshot
from max_trading_agent.runtime import EventJournal
from max_trading_agent.terminal_acceptance import AcceptanceError, TerminalAcceptance
from test_watcher import FakeMT5, fixture, TEST_KEY


def sample(bar_time, *, quote_age=1, login=12345678, server="PRIVATE_BROKER",
           bid=1.05, ask=1.0502, gap=False, missing_spec=False,
           last_close=1.0501, mode="DEMO"):
    snapshot = fixture(bar_time, bid=bid, ask=ask, login=login,
                       server=server, last_close=last_close, mode=mode)
    snapshot.pop("provenance")
    now = datetime.now(timezone.utc)
    snapshot["captured_at_utc"] = now.isoformat()
    snapshot["quote"]["time_utc"] = (
        now - timedelta(seconds=quote_age)
    ).isoformat()
    snapshot["symbol"].update({
        "point": 0.00001, "trade_tick_size": 0.00001,
        "trade_contract_size": 100000, "volume_step": 0.01,
    })
    if gap:
        # Shift only older history backwards: creates one real 2-minute
        # hole but preserves a correctly completed latest candle.
        for row in snapshot["candles"][:7]:
            row["time_utc"] = (
                datetime.fromisoformat(row["time_utc"]) - timedelta(minutes=1)
            ).isoformat()
    if missing_spec:
        snapshot["symbol"]["volume_step"] = None
    return seal_snapshot(snapshot, TEST_KEY, login=login, server=server)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.journal = EventJournal(self.root / "events.sqlite3")
        self.bar = (datetime.now(timezone.utc).replace(second=0, microsecond=0)
                    - timedelta(minutes=1))

    def run_check(self, snapshots, *, samples=2, interval=1, key=TEST_KEY,
                  bars=12, clock=None):
        api = FakeMT5(snapshots)
        gate = TerminalAcceptance(self.journal, api, key, clock=clock)
        output = gate.execute(
            "EURUSD", timeframe="M1", bars=bars, samples=samples,
            sample_interval=interval, sleep=lambda sec: None,
        )
        return output, api

    def journal_events(self, run_id):
        return self.journal.events(run_id)

    def test_progressing_clean_feed_is_consistent_but_no_trade(self):
        first = sample(self.bar, quote_age=12)
        second = sample(self.bar, quote_age=1)
        report, api = self.run_check([first, second])
        self.assertEqual(report["status"], "OBSERVED_CONSISTENT")
        self.assertEqual(report["samples_verified"], 2)
        self.assertTrue(report["new_quote_observed"])
        self.assertEqual(report["owner_acceptance"], "PENDING")
        self.assertFalse(report["broker_execution"])
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["warnings"], [])
        self.assertLessEqual(report["min_quote_age_seconds"],
                             report["max_quote_age_seconds"])
        self.assertLessEqual(report["min_spread_price_units"],
                             report["max_spread_price_units"])
        self.assertEqual(len(api.calls), 2)
        rows = self.journal_events(report["run_id"])
        self.assertEqual([event["event_type"] for event in rows], [
            "ACCEPTANCE_STARTED", "ACCEPTANCE_SAMPLE", "ACCEPTANCE_SAMPLE",
            "ACCEPTANCE_REPORT", "DECISION",
        ])
        self.assertEqual(rows[-1]["details"]["action"], "NO_TRADE")
        self.assertFalse(rows[-1]["details"]["order_send_called"])
        serialized = json.dumps(rows) + json.dumps(report)
        for secret in ("12345678", "PRIVATE_BROKER", "10000"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(self.journal.runs()[0]["mode"], "READ_ONLY")

    def test_signed_replay_five_minutes_old_is_blocked(self):
        now = datetime.now(timezone.utc)
        old_bar = self.bar - timedelta(minutes=8)
        first = sample(old_bar, quote_age=12)
        second = sample(old_bar, quote_age=1)
        for row in (first, second):
            row.pop("provenance")
            row["captured_at_utc"] = (now - timedelta(minutes=5)).isoformat()
            row["quote"]["time_utc"] = (
                now - timedelta(minutes=5, seconds=12 if row is first else 1)
            ).isoformat()
        first = seal_snapshot(first, TEST_KEY, login=12345678,
                              server="PRIVATE_BROKER")
        second = seal_snapshot(second, TEST_KEY, login=12345678,
                               server="PRIVATE_BROKER")
        result, _ = self.run_check([first, second], clock=lambda: now)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["block_reason"], "CAPTURE_NOT_FRESH")
        self.assertEqual(result["samples_verified"], 0)
        self.assertEqual(self.journal.events(result["run_id"])[-1]
                         ["details"]["action"], "NO_TRADE")

    def test_full_requested_history_gap_outside_llm_window_is_inconclusive(self):
        def expanded(quote_age):
            row = sample(self.bar, quote_age=quote_age)
            row.pop("provenance")
            start = datetime.fromisoformat(row["candles"][0]["time_utc"])
            old = []
            for i in range(48, 0, -1):
                bar = row["candles"][0].copy()
                # Create one extra minute of distance in the first 20 bars.
                bar["time_utc"] = (start - timedelta(
                    minutes=i + (1 if i >= 25 else 0))).isoformat()
                old.append(bar)
            row["candles"] = old + row["candles"]
            return seal_snapshot(row, TEST_KEY, login=12345678,
                                 server="PRIVATE_BROKER")
        result, _ = self.run_check([expanded(12), expanded(1)], bars=60)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("HISTORICAL_CANDLE_GAP", result["warnings"])
        self.assertEqual(result["samples_verified"], 2)

    def test_history_revision_outside_llm_window_is_blocked(self):
        def expanded(quote_age):
            row = sample(self.bar, quote_age=quote_age)
            row.pop("provenance")
            start = datetime.fromisoformat(row["candles"][0]["time_utc"])
            old = []
            for i in range(48, 0, -1):
                bar = row["candles"][0].copy()
                bar["time_utc"] = (start - timedelta(minutes=i)).isoformat()
                old.append(bar)
            row["candles"] = old + row["candles"]
            return row
        first = expanded(12)
        second = expanded(1)
        second["candles"][1]["close"] += 0.0001
        first = seal_snapshot(first, TEST_KEY, login=12345678,
                              server="PRIVATE_BROKER")
        second = seal_snapshot(second, TEST_KEY, login=12345678,
                               server="PRIVATE_BROKER")
        result, _ = self.run_check([first, second], bars=60)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["block_reason"], "HISTORICAL_CANDLE_REVISED")

    def test_short_history_cannot_appear_complete(self):
        row1, row2 = sample(self.bar, quote_age=12), sample(self.bar, quote_age=1)
        result, _ = self.run_check([row1, row2], bars=100)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("INCOMPLETE_CANDLE_HISTORY", result["warnings"])

    def test_quiet_market_is_inconclusive_not_fake_pass(self):
        original = sample(self.bar, quote_age=3)
        report, _ = self.run_check([original, original])
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertEqual(report["warnings"], ["NO_NEW_TICK_BETWEEN_SAMPLES"])
        self.assertEqual(self.journal.runs()[0]["status"], "COMPLETED")

    def test_missing_contract_metadata_is_inconclusive(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=12, missing_spec=True),
            sample(self.bar, quote_age=1),
        ])
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertIn("INCOMPLETE_BROKER_SYMBOL_SPEC", report["warnings"])

    def test_gap_and_old_history_are_inconclusive(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=12, gap=True),
            sample(self.bar, quote_age=1, gap=True),
        ])
        self.assertIn("HISTORICAL_CANDLE_GAP", report["warnings"])
        self.assertEqual(report["status"], "INCONCLUSIVE")

        stale_bar = self.bar - timedelta(minutes=7)
        report2, _ = self.run_check([
            sample(stale_bar, quote_age=12),
            sample(stale_bar, quote_age=1),
        ])
        self.assertIn("RECENT_COMPLETED_BAR_MISSING", report2["warnings"])

    def test_same_trade_mode_different_broker_identity_is_blocked(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=12, login=12345678),
            sample(self.bar, quote_age=1, login=98765432),
        ])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["block_reason"], "ACCOUNT_ID_CHANGED")
        self.assertEqual(report["samples_verified"], 1)
        self.assertEqual(self.journal.runs()[0]["status"], "FAILED")
        self.assertNotIn("98765432", json.dumps(self.journal_events(report["run_id"])))

    def test_same_mode_new_server_fails_closed(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=12, server="PRIVATE_BROKER"),
            sample(self.bar, quote_age=1, server="OTHER_PRIVATE_SERVER"),
        ])
        self.assertEqual(report["block_reason"], "ACCOUNT_ID_CHANGED")
        self.assertNotIn("OTHER_PRIVATE_SERVER",
                         json.dumps(self.journal_events(report["run_id"])))

    def test_same_account_trade_mode_switch_is_blocked(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=12, mode="DEMO"),
            sample(self.bar, quote_age=1, mode="REAL"),
        ])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["block_reason"], "ACCOUNT_MODE_CHANGED")

    def test_old_historical_candle_revision_is_blocked(self):
        earlier = sample(self.bar, quote_age=12)
        revised = sample(self.bar, quote_age=1)
        revised.pop("provenance")
        revised["candles"][3]["close"] = 1.0508  # within declared high/low
        revised = seal_snapshot(revised, TEST_KEY,
                               login=12345678, server="PRIVATE_BROKER")
        report, _ = self.run_check([earlier, revised])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["block_reason"], "HISTORICAL_CANDLE_REVISED")

    def test_quote_time_regression_is_blocked(self):
        report, _ = self.run_check([
            sample(self.bar, quote_age=1),
            sample(self.bar, quote_age=12),
        ])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["block_reason"], "QUOTE_TIME_REGRESSION")

    def test_quote_mutation_without_new_time_is_blocked(self):
        same = sample(self.bar, quote_age=3)
        manipulated = json.loads(json.dumps(same))
        manipulated.pop("provenance")
        manipulated["quote"]["bid"] = 1.049
        manipulated = seal_snapshot(manipulated, TEST_KEY,
                                    login=12345678, server="PRIVATE_BROKER")
        report, _ = self.run_check([same, manipulated])
        self.assertEqual(report["block_reason"],
                         "QUOTE_CHANGED_WITHOUT_NEW_TIMESTAMP")

    def test_completed_bar_revision_and_regression_block(self):
        same_time = sample(self.bar, quote_age=12)
        changed = sample(self.bar, quote_age=1, last_close=1.0505)
        result, _ = self.run_check([same_time, changed])
        self.assertEqual(result["block_reason"], "COMPLETED_BAR_REVISED")
        earlier = sample(self.bar - timedelta(minutes=1), quote_age=1)
        result2, _ = self.run_check([same_time, earlier])
        self.assertEqual(result2["block_reason"], "COMPLETED_BAR_REGRESSION")

    def test_corrupted_hmac_or_unsigned_snapshot_is_blocked(self):
        good = sample(self.bar, quote_age=12)
        changed = json.loads(json.dumps(good))
        changed["quote"]["ask"] = 2
        report, _ = self.run_check([changed, good])
        self.assertEqual(report["block_reason"], "SNAPSHOT_MAC_MISMATCH")
        unsigned = json.loads(json.dumps(good))
        unsigned.pop("provenance")
        second, _ = self.run_check([unsigned, good])
        self.assertEqual(second["block_reason"], "SNAPSHOT_PROVENANCE_MISSING")

    def test_wrong_secret_must_not_be_accepted(self):
        report, _ = self.run_check([sample(self.bar)], key=bytes([7]) * 32)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["samples_verified"], 0)
        self.assertEqual(report["block_reason"], "SNAPSHOT_MAC_MISMATCH")

    def test_broker_disconnect_and_bad_quote_fail_closed(self):
        disconnected = sample(self.bar)
        disconnected.pop("provenance")
        disconnected["terminal"]["connected"] = False
        disconnected = seal_snapshot(disconnected, TEST_KEY,
                                     login=12345678, server="PRIVATE_BROKER")
        report, _ = self.run_check([disconnected])
        self.assertEqual(report["block_reason"], "TERMINAL_DISCONNECTED")
        invalid = sample(self.bar)
        invalid.pop("provenance")
        invalid["quote"]["ask"] = invalid["quote"]["bid"]
        invalid = seal_snapshot(invalid, TEST_KEY,
                                login=12345678, server="PRIVATE_BROKER")
        report2, _ = self.run_check([invalid])
        self.assertIn(report2["status"], {"BLOCKED"})

    def test_unexpected_exception_redacted_and_no_trade(self):
        report, api = self.run_check([RuntimeError("SECRET_BROKER_LOGIN/12345")])
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["block_reason"], "ACCEPTANCE_UNEXPECTED_ERROR")
        events = self.journal_events(report["run_id"])
        self.assertNotIn("SECRET_BROKER_LOGIN", json.dumps(events) + json.dumps(report))
        self.assertEqual(events[-1]["details"]["action"], "NO_TRADE")
        self.assertEqual(self.journal.runs()[0]["status"], "FAILED")
        self.assertEqual(len(api.calls), 1)

    def test_operator_interrupt_is_failed_no_trade(self):
        api = FakeMT5([sample(self.bar, quote_age=12)])
        gate = TerminalAcceptance(self.journal, api, TEST_KEY)
        def stop(_):
            raise KeyboardInterrupt()
        result = gate.execute("EURUSD", timeframe="M1", bars=12,
                              samples=2, sample_interval=1, sleep=stop)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["samples_verified"], 1)
        self.assertEqual(result["block_reason"], "OPERATOR_INTERRUPTED")
        self.assertEqual(self.journal.runs()[0]["status"], "FAILED")
        events = self.journal.events(result["run_id"])
        self.assertEqual(events[-1]["details"]["action"], "NO_TRADE")

    def test_invalid_input_rejected_before_adapter_call(self):
        adapter = FakeMT5([sample(self.bar)])
        auditor = TerminalAcceptance(self.journal, adapter, TEST_KEY)
        for pairs in ({"samples": 1}, {"samples": 11},
                      {"sample_interval": 0}, {"sample_interval": 31},
                      {"bars": 1}, {"timeframe": "M2"}):
            with self.subTest(pairs=pairs):
                with self.assertRaisesRegex(AcceptanceError, "INVALID_ACCEPTANCE_BUDGET"):
                    auditor.execute("EURUSD", sleep=lambda _: None, **pairs)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.journal.runs(), [])
        with self.assertRaisesRegex(AcceptanceError, "IDENTITY_KEY_REQUIRED"):
            TerminalAcceptance(self.journal, adapter, b"bad")

    def test_cli_invalid_sample_count_no_mt5_package_or_secret(self):
        result = subprocess.run(
            [sys.executable, "-m", "max_trading_agent", "mt5-acceptance",
             "--symbol", "EURUSD", "--samples", "1",
             "--identity-key-file", str(self.root / "nonexistent.key")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --samples 2-10", result.stderr)
        self.assertFalse((self.root / "nonexistent.key").exists())
        self.assertNotIn("MetaTrader5", result.stderr)


if __name__ == "__main__":
    unittest.main()
