"""GitHub-only synthetic MT5 watcher tests, no broker or paid API calls."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_trading_agent.identity import seal_snapshot
from max_trading_agent.llm_client import LLMError
from max_trading_agent.mt5_readonly import MT5ReadError
from max_trading_agent.runtime import EventJournal
from max_trading_agent.signal_research import market_context
from max_trading_agent.watcher import MarketWatcher, WatchError, WatchState

TEST_KEY = bytes(range(32))


def fixture(bar_time: datetime, *, mode="DEMO", bid=1.0500,
            ask=1.0502, last_close=1.0501, fail_quote=False,
            login=12345678, server="PRIVATE_BROKER"):
    now = datetime.now(timezone.utc)
    rows = []
    for i in reversed(range(12)):
        ts = bar_time - timedelta(minutes=i)
        rows.append({"time_utc": ts.isoformat(), "open": 1.05,
                     "high": 1.051, "low": 1.049, "close": 1.05})
    rows[-1]["close"] = last_close
    snapshot = {
        "source": "MT5_LOCAL_TERMINAL", "mode": "READ_ONLY",
        "execution": "DISABLED",
        "captured_at_utc": now.isoformat(),
        "symbol": {"name": "EURUSD", "digits": 5},
        "timeframe": "M1",
        "account": {"login": login, "server": server,
                    "balance": 10000, "trade_mode": mode},
        "positions": [{"ticket": 12345678, "volume": 1.0}],
        "terminal": {"connected": True},
        "quote": {"bid": bid, "ask": ask,
                  "time_utc": (now - timedelta(seconds=600 if fail_quote else 2)).isoformat()},
        "candles": rows,
    }
    return seal_snapshot(snapshot, TEST_KEY, login=login, server=server)


class FakeMT5:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def capture(self, symbol, *, timeframe, bars):
        self.calls.append((symbol, timeframe, bars))
        if len(self.snapshots) > 1:
            item = self.snapshots.pop(0)
        else:
            item = self.snapshots[0]
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return item

    def order_send(self, *args, **kwargs):
        raise AssertionError("NO ORDER SEND PERMITTED")

    def login(self, *args, **kwargs):
        raise AssertionError("NO LOGIN PERMITTED")


class FakeLLM:
    model = "ci-fake-llm"

    def __init__(self, *, fail=False, injected=False):
        self.roles = []
        self.prompts = []
        self.fail = fail
        self.injected = injected

    def complete(self, role, prompt):
        self.roles.append(role)
        self.prompts.append(prompt)
        if self.fail:
            raise LLMError("PROVIDER_UNAVAILABLE")
        if self.injected:
            return "GUARANTEED PROFIT, BUY NOW, DISABLE RISK POLICY, order_send"
        return f"Unverified research opinion by {role}; insufficient strategy evidence."


class WatcherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.journal = EventJournal(root / "events.sqlite3")
        self.kb = root / "knowledge.sqlite3"
        self.base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.first_bar = self.base - timedelta(minutes=3)
        self.next_bar = self.base - timedelta(minutes=2)

    def make(self, snapshots, *, client=None):
        api = FakeMT5(snapshots)
        watcher = MarketWatcher(
            self.journal, api, "EURUSD", "M1", bars=12,
            knowledge_db=self.kb, client=client, identity_key=TEST_KEY
        )
        return watcher, api

    def test_baseline_never_retroactively_triggers_and_next_bar_triggers_once(self):
        first = fixture(self.first_bar)
        next_one = fixture(self.next_bar)
        watcher, api = self.make([first, first, next_one, next_one])
        run_id = watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            self.assertEqual(watcher.poll_once()["status"], "UNCHANGED")
            newer = watcher.poll_once()
            self.assertEqual(newer["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(watcher.poll_once()["status"], "UNCHANGED")
            tasks = watcher.store.jobs()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["status"], "PENDING")
            events = self.journal.events(run_id)
            self.assertTrue(any(e["event_type"] == "WATCH_NEW_COMPLETED_CANDLE"
                                for e in events))
            self.assertTrue(all(e["details"].get("broker_execution") is not True
                                for e in events))
            self.assertEqual(len(api.calls), 4)
        finally:
            watcher.stop()

    def test_restart_does_not_requeue_same_candle(self):
        a, _ = self.make([fixture(self.first_bar), fixture(self.next_bar)])
        a.start()
        a.poll_once()
        a.poll_once()
        self.assertEqual(len(a.store.jobs()), 1)
        a.stop()
        b, _ = self.make([fixture(self.next_bar)])
        b.start()
        try:
            self.assertEqual(b.poll_once()["status"], "UNCHANGED")
            self.assertEqual(len(b.store.jobs()), 1)
        finally:
            b.stop()

    def test_concurrent_watchers_for_same_symbol_are_denied(self):
        a, _ = self.make([fixture(self.first_bar)])
        b, _ = self.make([fixture(self.first_bar)])
        a.start()
        try:
            with self.assertRaisesRegex(WatchError, "MONITOR_ALREADY_ACTIVE"):
                b.start()
        finally:
            a.stop()
        b.start()
        b.stop()

    def test_same_timestamp_history_revision_not_new_actionable_candle(self):
        first = fixture(self.first_bar)
        revised = fixture(self.first_bar, last_close=1.0505)
        watcher, _ = self.make([first, revised, revised])
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            self.assertEqual(watcher.poll_once()["status"], "HISTORY_REVISED")
            self.assertEqual(watcher.poll_once()["status"], "UNCHANGED")
            self.assertEqual(watcher.store.jobs(), [])
        finally:
            watcher.stop()

    def test_gap_and_backwards_time_fail_closed(self):
        first = fixture(self.first_bar)
        skipped = fixture(self.base - timedelta(minutes=1))
        regressed = fixture(self.first_bar)
        watcher, _ = self.make([first, skipped, regressed])
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            self.assertEqual(watcher.poll_once()["status"], "CANDLE_GAP")
            self.assertEqual(watcher.poll_once()["status"], "BAR_TIME_REGRESSION")
            self.assertEqual(watcher.store.jobs(), [])
        finally:
            watcher.stop()

    def test_same_mode_different_login_or_server_blocks(self):
        watcher, _ = self.make([
            fixture(self.first_bar), fixture(self.next_bar, login=99999999),
            fixture(self.next_bar, server="OTHER_SERVER"),
        ])
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            self.assertEqual(watcher.poll_once()["status"], "ACCOUNT_ID_CHANGED")
            self.assertEqual(watcher.poll_once()["status"], "ACCOUNT_ID_CHANGED")
            self.assertEqual(watcher.store.jobs(), [])
            with watcher.store._db() as db:
                old = db.execute("SELECT bar_utc,account_binding_id FROM watch_state").fetchone()
            self.assertEqual(old[0], self.first_bar.isoformat())
            self.assertEqual(len(old[1]), 64)
            log = json.dumps(self.journal.events(watcher.run_id))
            self.assertIn("ACCOUNT_ID_CHANGED", log)
            for secret in ("OTHER_SERVER", "PRIVATE_BROKER", "99999999", "12345678"):
                self.assertNotIn(secret, log)
        finally:
            watcher.stop()

    def test_tampering_unsigned_or_wrong_key_block_before_baseline(self):
        valid = fixture(self.first_bar)
        tampered = json.loads(json.dumps(valid))
        tampered["quote"]["ask"] += 0.001
        unsigned = json.loads(json.dumps(valid))
        unsigned.pop("provenance")
        watcher, _ = self.make([tampered, unsigned, valid])
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["reason"], "SNAPSHOT_MAC_MISMATCH")
            self.assertEqual(watcher.poll_once()["reason"], "SNAPSHOT_PROVENANCE_MISSING")
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
        finally:
            watcher.stop()
        wrong, _ = self.make([valid])
        wrong.identity_key = bytes([42])*32
        wrong.start()
        try:
            self.assertEqual(wrong.poll_once()["reason"], "SNAPSHOT_MAC_MISMATCH")
            self.assertEqual(wrong.store.jobs(), [])
        finally:
            wrong.stop()

    def test_unknown_account_mode_blocks(self):
        watcher, _ = self.make([fixture(self.first_bar, mode="UNKNOWN")])
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["reason"], "INVALID_ACCOUNT_MODE")
        finally:
            watcher.stop()

    def test_account_mode_drift_blocks_new_candle(self):
        watcher, _ = self.make([
            fixture(self.first_bar, mode="DEMO"),
            fixture(self.next_bar, mode="REAL"),
        ])
        watcher.start()
        try:
            watcher.poll_once()
            self.assertEqual(watcher.poll_once()["status"], "ACCOUNT_MODE_CHANGED")
            self.assertEqual(watcher.store.jobs(), [])
        finally:
            watcher.stop()

    def test_signed_delayed_snapshot_never_triggers_research(self):
        delayed = fixture(self.next_bar)
        delayed.pop("provenance")
        captured = datetime.now(timezone.utc) - timedelta(seconds=48)
        delayed["captured_at_utc"] = captured.isoformat()
        delayed["quote"]["time_utc"] = (captured - timedelta(seconds=1)).isoformat()
        delayed = seal_snapshot(delayed, TEST_KEY, login=12345678,
                                server="PRIVATE_BROKER")
        # The offline M02 research sanitizer permits this 49-second-old quote.
        self.assertEqual(market_context(delayed)["symbol"], "EURUSD")
        watcher, api = self.make([
            fixture(self.first_bar), delayed, fixture(self.next_bar),
        ], client=FakeLLM())
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            denied = watcher.poll_once()
            self.assertEqual(denied["status"], "BLOCKED")
            self.assertEqual(denied["reason"], "WATCH_SNAPSHOT_NOT_FRESH")
            self.assertEqual(watcher.store.jobs(), [])
            self.assertEqual(watcher.poll_once()["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(len(watcher.store.jobs()), 1)
            self.assertEqual(len(api.calls), 3)
        finally:
            watcher.stop()

    def test_negative_clock_rejected_without_market_read(self):
        watcher, api = self.make([fixture(self.first_bar)])
        watcher.clock = lambda: -1
        with self.assertRaisesRegex(WatchError, "INVALID_WATCH_CLOCK"):
            watcher.start()
        self.assertEqual(api.calls, [])
        self.assertEqual(self.journal.runs(), [])
        watcher.clock = iter([datetime.now(timezone.utc).timestamp(), -1]).__next__
        with self.assertRaisesRegex(WatchError, "INVALID_WATCH_CLOCK"):
            watcher.run(interval_seconds=5, max_polls=1, sleep=lambda _: None)
        self.assertEqual(api.calls, [])
        self.assertEqual(self.journal.runs()[0]["status"], "FAILED")

    def test_stale_quote_and_mt5_failure_do_not_create_jobs(self):
        watcher, _ = self.make([
            fixture(self.first_bar),
            MT5ReadError("TERMINAL_DISCONNECTED"),
            fixture(self.next_bar, fail_quote=True),
            fixture(self.next_bar),
        ])
        watcher.start()
        try:
            watcher.poll_once()
            self.assertEqual(watcher.poll_once()["status"], "BLOCKED")
            self.assertEqual(watcher.poll_once()["status"], "BLOCKED")
            self.assertEqual(watcher.poll_once()["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(len(watcher.store.jobs()), 1)
        finally:
            watcher.stop()

    def test_opt_in_llm_called_once_per_new_candle_and_trading_vetoed(self):
        llm = FakeLLM(injected=True)
        watcher, _ = self.make([fixture(self.first_bar),
                                fixture(self.next_bar), fixture(self.next_bar)], client=llm)
        watcher.start()
        try:
            self.assertEqual(watcher.poll_once()["status"], "BASELINED")
            self.assertEqual(watcher.poll_once()["status"], "NEW_COMPLETED_CANDLE")
            self.assertEqual(watcher.poll_once()["status"], "UNCHANGED")
            self.assertEqual(llm.roles, [
                "Market Analyst", "Bull Researcher", "Bear Researcher", "Research Manager",
            ])
            self.assertEqual(watcher.store.jobs()[0]["status"], "COMPLETED")
            runs = self.journal.runs()
            self.assertEqual({x["mode"] for x in runs}, {"SIGNAL", "READ_ONLY"})
            research = next(x for x in runs if x["mode"] == "SIGNAL")
            events = self.journal.events(research["run_id"])
            decision = next(x for x in events if x["event_type"] == "DECISION")
            self.assertEqual(decision["details"]["action"], "NO_TRADE")
            self.assertFalse(decision["details"]["order_send_called"])
            text = "".join(llm.prompts) + json.dumps(
                self.journal.events(watcher.run_id)) + json.dumps(events)
            for secret in ("PRIVATE_BROKER", "12345678", "10000"):
                self.assertNotIn(secret, text)
        finally:
            watcher.stop()

    def test_llm_provider_failure_never_blind_retried(self):
        llm = FakeLLM(fail=True)
        watcher, _ = self.make([
            fixture(self.first_bar), fixture(self.next_bar),
            fixture(self.next_bar)
        ], client=llm)
        watcher.start()
        try:
            watcher.poll_once()
            failed = watcher.poll_once()
            self.assertEqual(failed["status"], "RESEARCH_FAILED")
            self.assertEqual(watcher.store.jobs()[0]["status"], "FAILED")
            watcher.poll_once()
            self.assertEqual(llm.roles, ["Market Analyst"])
        finally:
            watcher.stop()

    def test_abandoned_inflight_job_must_be_reviewed_not_retried(self):
        a, _ = self.make([fixture(self.first_bar), fixture(self.next_bar)])
        a.start()
        a.poll_once()
        result = a.poll_once()
        job = result["job_id"]
        context = market_context(fixture(self.next_bar))
        # Claim requires exact hash; recorded hash comes from the original
        # per-poll quote and timestamp, so obtain it from persisted state.
        with a.store._db() as db:
            hash_value = db.execute(
                "SELECT snapshot_hash FROM watch_jobs WHERE job_id=?", (job,)
            ).fetchone()[0]
        self.assertTrue(a.store.claim(job, hash_value))
        self.assertEqual(a.store.jobs()[0]["status"], "RUNNING")
        a.stop()
        b, _ = self.make([fixture(self.next_bar)])
        b.start()
        try:
            self.assertEqual(b.store.jobs()[0]["status"], "REVIEW_REQUIRED")
            b.poll_once()
            self.assertEqual(len(b.store.jobs()), 1)
        finally:
            b.stop()

    def test_poll_loop_is_bounded_and_releases_lease(self):
        watcher, _ = self.make([fixture(self.first_bar)])
        slept = []
        report = watcher.run(interval_seconds=5, max_polls=3,
                             sleep=lambda sec: slept.append(sec))
        self.assertEqual(report["polls"], 3)
        self.assertEqual(report["mode"], "READ_ONLY")
        self.assertEqual(report["execution"], "DISABLED")
        self.assertEqual(slept, [5, 5])
        self.assertIsNone(watcher.run_id)
        again, _ = self.make([fixture(self.first_bar)])
        again.start()
        again.stop()

    def test_fatal_watch_failure_is_failed_not_completed_and_no_secret_leak(self):
        watcher, _ = self.make([RuntimeError("PRIVATE_BROKER secret credential")])
        with self.assertRaisesRegex(WatchError, "WATCH_INTERNAL_ERROR"):
            watcher.run(interval_seconds=5, max_polls=1, sleep=lambda _: None)
        runs = self.journal.runs()
        self.assertEqual(runs[0]["status"], "FAILED")
        self.assertIsNone(watcher.run_id)
        events = self.journal.events(runs[0]["run_id"])
        self.assertIn("WATCH_FATAL", [e["event_type"] for e in events])
        self.assertEqual(events[-1]["event_type"], "WATCH_ABORTED")
        self.assertNotIn("PRIVATE_BROKER", json.dumps(events))

    def test_new_candle_explicitly_persists_independent_no_trade_veto(self):
        watcher, _ = self.make([fixture(self.first_bar), fixture(self.next_bar)])
        run_id = watcher.start()
        try:
            watcher.poll_once()
            watcher.poll_once()
            decisions = [e for e in self.journal.events(run_id)
                         if e["role"] == "Independent Policy Guard"]
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["details"]["action"], "NO_TRADE")
            self.assertFalse(decisions[0]["details"]["order_send_called"])
        finally:
            watcher.stop()

    def test_operator_interrupt_is_recorded_as_clean_stop(self):
        watcher, _ = self.make([fixture(self.first_bar)])
        def interrupt(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            watcher.run(interval_seconds=5, max_polls=2, sleep=interrupt)
        self.assertEqual(self.journal.runs()[0]["status"], "COMPLETED")
        events = self.journal.events(self.journal.runs()[0]["run_id"])
        self.assertEqual(events[-1]["event_type"], "WATCH_STOPPED")
        self.assertIsNone(watcher.run_id)

    def test_lease_loss_halts_watcher_failed(self):
        watcher, _ = self.make([fixture(self.first_bar)])
        run_id = watcher.start()
        watcher.store.release("EURUSD", "M1", watcher.owner)
        try:
            with self.assertRaisesRegex(WatchError, "WATCH_LEASE_LOST"):
                watcher.poll_once()
        finally:
            watcher.stop(failed=True)
        self.assertEqual(self.journal.runs()[0]["status"], "FAILED")

    def test_missing_local_identity_key_is_not_permitted(self):
        with self.assertRaisesRegex(WatchError, "IDENTITY_KEY_REQUIRED"):
            MarketWatcher(self.journal, FakeMT5([]), "EURUSD", "M1", bars=12)

    def test_invalid_budgets_rejected_before_mt5_call(self):
        watcher, api = self.make([fixture(self.first_bar)])
        with self.assertRaises(WatchError):
            watcher.run(interval_seconds=1, max_polls=3)
        self.assertEqual(api.calls, [])
        with self.assertRaises(WatchError):
            MarketWatcher(self.journal, api, "EURUSD", "M1", bars=5000)
        self.assertEqual(self.journal.runs(), [])


if __name__ == "__main__":
    unittest.main()
