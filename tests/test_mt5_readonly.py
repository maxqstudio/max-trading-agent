"""Fake-MT5 contract tests. No native package, broker, account or credentials."""
import copy
import unittest
from types import SimpleNamespace as NS

from max_trading_agent.mt5_readonly import MT5ReadError, ReadOnlyMT5

NOW = 1_800_000_000
PERIOD = 900


class FakeMT5:
    TIMEFRAME_M15 = 15
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self):
        self.calls = []
        self.connected = True
        self.init_ok = True
        self.visible = True
        self.trade_mode = 0
        self.offset_seconds = 12
        self.bad_rates = None

    def initialize(self):
        self.calls.append(("initialize",))
        return self.init_ok

    def shutdown(self):
        self.calls.append(("shutdown",))

    def last_error(self):
        return (-10003, "Sensitive private broker path must never be returned")

    def terminal_info(self):
        self.calls.append(("terminal_info",))
        return NS(connected=self.connected, trade_allowed=True,
                  tradeapi_disabled=False, maxbars=100000, path="SECRET/PATH")

    def account_info(self):
        self.calls.append(("account_info",))
        return NS(login=12345678, server="PRIVATE", name="PRIVATE",
                  trade_mode=self.trade_mode, balance=1000.0, equity=999.0,
                  currency="USD", margin=1.0, margin_free=998.0,
                  margin_level=200.0, leverage=100, margin_mode=0,
                  trade_allowed=True, trade_expert=True)

    def symbol_info(self, symbol):
        self.calls.append(("symbol_info", symbol))
        return NS(name=symbol, visible=self.visible, digits=5, point=0.00001,
                  trade_contract_size=100000.0, trade_tick_size=0.00001,
                  trade_tick_value=1.0, volume_min=0.01, volume_max=10.0,
                  volume_step=0.01, trade_stops_level=10, trade_freeze_level=0,
                  trade_mode=4, trade_exemode=2, filling_mode=1)

    def symbol_info_tick(self, symbol):
        self.calls.append(("symbol_info_tick", symbol))
        return NS(bid=1.05000, ask=1.05015,
                  time_msc=int((NOW - self.offset_seconds) * 1000),
                  time=int(NOW - self.offset_seconds))

    def copy_rates_from_pos(self, symbol, frame, start, count):
        self.calls.append(("copy_rates_from_pos", symbol, frame, start, count))
        self.start_pos = start
        if self.bad_rates is not None:
            return self.bad_rates
        return [
            {"time": NOW - PERIOD * (count - i), "open": 1.04, "high": 1.06,
             "low": 1.03, "close": 1.05, "tick_volume": 10,
             "spread": 12, "real_volume": 0}
            for i in range(count)
        ]

    def positions_get(self, *, symbol):
        self.calls.append(("positions_get", symbol))
        return (NS(ticket=123, type=0, volume=0.1, price_open=1.05,
                   sl=1.04, tp=1.07, profit=2.0, comment="SECRET"),)

    def orders_get(self, *, symbol):
        self.calls.append(("orders_get", symbol))
        return ()

    def order_send(self, *args, **kwargs):
        raise AssertionError("MUTATION ATTEMPTED")

    def login(self, *args, **kwargs):
        raise AssertionError("MUTATION ATTEMPTED")

    def symbol_select(self, *args, **kwargs):
        raise AssertionError("MUTATION ATTEMPTED")


class ReadOnlyMT5Tests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMT5()
        self.reader = ReadOnlyMT5(self.fake, clock=lambda: NOW)

    def assert_code(self, code, callback):
        with self.assertRaises(MT5ReadError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_success_snapshot_no_execution_and_no_secrets(self):
        result = self.reader.capture("EURUSD", bars=3)
        self.assertEqual(result["account"]["trade_mode"], "DEMO")
        self.assertEqual(result["mode"], "READ_ONLY")
        self.assertEqual(result["execution"], "DISABLED")
        self.assertEqual(result["decision"], "NOT_EVALUATED")
        self.assertEqual(len(result["candles"]), 3)
        self.assertEqual(self.fake.start_pos, 1)
        self.assertNotIn("login", result["account"])
        self.assertNotIn("server", result["account"])
        self.assertNotIn("path", result["terminal"])
        self.assertNotIn("comment", result["positions"][0])
        names = [x[0] for x in self.fake.calls]
        self.assertEqual(names[0], "initialize")
        self.assertEqual(names[-1], "shutdown")
        self.assertFalse(set(names) & {"order_send", "login", "symbol_select", "order_check"})

    def test_real_account_is_labeled_not_guessed(self):
        self.fake.trade_mode = 2
        self.assertEqual(self.reader.capture("XAUUSD", bars=2)["account"]["trade_mode"], "REAL")
        self.fake.trade_mode = 9
        self.assertEqual(self.reader.capture("XAUUSD", bars=2)["account"]["trade_mode"], "UNKNOWN")

    def test_initialize_failure_redacts_private_error(self):
        self.fake.init_ok = False
        with self.assertRaises(MT5ReadError) as error:
            self.reader.capture("EURUSD", bars=2)
        self.assertEqual(error.exception.as_dict(), {"code": "INITIALIZE_FAILED", "api_code": -10003})
        self.assertEqual(self.fake.calls[-1], ("shutdown",))
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_disconnect(self):
        self.fake.connected = False
        self.assert_code("TERMINAL_DISCONNECTED", lambda: self.reader.capture("EURUSD", bars=2))
        self.assertEqual(self.fake.calls[-1], ("shutdown",))

    def test_invisible_symbol_does_not_modify_market_watch(self):
        self.fake.visible = False
        self.assert_code("SYMBOL_NOT_VISIBLE", lambda: self.reader.capture("EURUSD", bars=2))
        self.assertNotIn("symbol_select", [x[0] for x in self.fake.calls])

    def test_stale_or_future_ticks_fail_closed(self):
        for offset in (91, -9):
            self.fake.offset_seconds = offset
            self.assert_code("STALE_OR_FUTURE_TICK", lambda: self.reader.capture("EURUSD", bars=2))

    def test_bad_spread(self):
        original = self.fake.symbol_info_tick
        self.fake.symbol_info_tick = lambda symbol: NS(bid=1.2, ask=1.1, time=NOW-1)
        self.assert_code("INVALID_BID_ASK", lambda: self.reader.capture("EURUSD", bars=2))
        self.fake.symbol_info_tick = original

    def test_missing_rates_and_duplicate_times(self):
        self.fake.bad_rates = []
        self.assert_code("INSUFFICIENT_COMPLETED_BARS",
                         lambda: self.reader.capture("EURUSD", bars=2))
        self.fake.bad_rates = [
            {"time": NOW-900, "open": 1.0, "close": 1.0, "low": 0.9, "high": 1.1},
            {"time": NOW-900, "open": 1.0, "close": 1.0, "low": 0.9, "high": 1.1},
        ]
        self.assert_code("INVALID_BAR_TIME", lambda: self.reader.capture("EURUSD", bars=2))

    def test_missing_ohlc_invalid(self):
        self.fake.bad_rates = [
            {"time": NOW-1800, "open": 1.0, "close": 1.0, "low": 0.9, "high": 1.1},
            {"time": NOW-900, "open": 1.0, "close": 1.3, "low": 0.9, "high": 1.1},
        ]
        self.assert_code("INVALID_OHLC", lambda: self.reader.capture("EURUSD", bars=2))

    def test_rejects_arbitrary_symbol_and_timeframe(self):
        self.assert_code("INVALID_SYMBOL", lambda: self.reader.capture("../../secrets"))
        self.assert_code("INVALID_TIMEFRAME", lambda: self.reader.capture("EURUSD", timeframe="M2"))
        self.assert_code("INVALID_BAR_COUNT", lambda: self.reader.capture("EURUSD", bars=0))
        self.assertEqual(self.fake.calls, [])

    def test_unknown_timeframe_from_api(self):
        del self.fake.TIMEFRAME_M15  # class attribute remains, so override explicitly
        self.fake.TIMEFRAME_M15 = None
        self.assert_code("TIMEFRAME_NOT_SUPPORTED_BY_API",
                         lambda: self.reader.capture("EURUSD", bars=2))

    def test_missing_positions_is_distinct_from_empty(self):
        self.fake.positions_get = lambda **kwargs: None
        self.assert_code("POSITIONS_UNAVAILABLE", lambda: self.reader.capture("EURUSD", bars=2))


if __name__ == "__main__":
    unittest.main()
