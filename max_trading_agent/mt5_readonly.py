"""Strictly read-only Python MetaTrader 5 adapter.

No order_send, login, symbol_select, order_check, or mutable MT5 method is
reachable from this module. MT5 must be configured by the user beforehand.
The library is imported only for an explicitly requested local probe.

Official reference: https://www.mql5.com/en/docs/python_metatrader5
"""
from __future__ import annotations

import importlib
import math
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .identity import IdentityError, seal_snapshot

# The native MT5 Python package uses a terminal-wide session. Serialize probes.
_SESSION_LOCK = threading.RLock()
_TIMEFRAMES = {
    "M1": ("TIMEFRAME_M1", 60),
    "M5": ("TIMEFRAME_M5", 300),
    "M15": ("TIMEFRAME_M15", 900),
    "M30": ("TIMEFRAME_M30", 1800),
    "H1": ("TIMEFRAME_H1", 3600),
    "H4": ("TIMEFRAME_H4", 14400),
    "D1": ("TIMEFRAME_D1", 86400),
}
_SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,39}\Z")
_ACCOUNT_NUMBERS = ("balance", "equity", "margin", "margin_free", "margin_level")
_SYMBOL_NUMBERS = (
    "digits", "point", "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "volume_min", "volume_max", "volume_step", "trade_stops_level",
    "trade_freeze_level", "trade_mode", "trade_exemode", "filling_mode",
)
_POSITION_NUMBERS = ("ticket", "type", "volume", "price_open", "sl", "tp", "profit")
_ORDER_NUMBERS = ("ticket", "type", "volume_initial", "price_open", "sl", "tp")


class MT5ReadError(Exception):
    """Safe error code; does not expose broker paths, IDs, or raw MT5 error text."""

    def __init__(self, code: str, api_code: int | None = None):
        self.code = code
        self.api_code = api_code
        super().__init__(code)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "api_code": self.api_code}


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    # MT5 copy_rates_from_pos returns NumPy structured rows (np.void).
    # They expose fields through row["time"], not necessarily attributes.
    names = getattr(getattr(obj, "dtype", None), "names", None)
    if isinstance(names, tuple):
        if key not in names:
            return default
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise MT5ReadError("INVALID_RATE_FIELD") from None
    return getattr(obj, key, default)


def _number(value: Any, key: str) -> float | int | None:
    if value is None:
        return None
    # NumPy scalars from structured OHLC arrays are not all Python
    # int/float, and cannot always be serialized directly to JSON.
    if type(value).__module__.split(".", 1)[0] == "numpy":
        try:
            value = value.item()
        except (AttributeError, TypeError, ValueError):
            raise MT5ReadError("INVALID_NUMERIC_FIELD") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MT5ReadError("INVALID_NUMERIC_FIELD")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        raise MT5ReadError("NON_FINITE_FIELD") from None
    if not finite:
        raise MT5ReadError("NON_FINITE_FIELD")
    return value


def _fields(obj: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: _number(_field(obj, key), key) for key in allowed}


def _utc(seconds: int | float) -> str:
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds")
    except (OverflowError, OSError, ValueError):
        raise MT5ReadError("INVALID_TIMESTAMP") from None


class ReadOnlyMT5:
    """Opt-in market snapshot; not a trade engine or a signal generator."""

    def __init__(self, api: Any, *, clock: Callable[[], float] | None = None,
                 max_tick_age_seconds: int = 90, identity_key: bytes | None = None):
        if not 1 <= max_tick_age_seconds <= 3600:
            raise ValueError("max_tick_age_seconds must be between 1 and 3600")
        self._api = api
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._max_tick_age = max_tick_age_seconds
        self._identity_key = identity_key

    @classmethod
    def from_local_terminal(cls, **kwargs: Any) -> "ReadOnlyMT5":
        try:
            api = importlib.import_module("MetaTrader5")
        except (ImportError, OSError) as exc:
            raise MT5ReadError("MT5_PYTHON_PACKAGE_UNAVAILABLE") from exc
        return cls(api, **kwargs)

    def _api_error_code(self) -> int | None:
        try:
            value = self._api.last_error()
            code = value[0] if isinstance(value, (tuple, list)) and value else None
            return int(code) if isinstance(code, int) and not isinstance(code, bool) else None
        except Exception:
            return None

    def _require(self, result: Any, error: str) -> Any:
        if result is None:
            raise MT5ReadError(error, self._api_error_code())
        return result

    @contextmanager
    def _connection(self) -> Iterator[None]:
        with _SESSION_LOCK:
            try:
                if self._api.initialize() is not True:
                    raise MT5ReadError("INITIALIZE_FAILED", self._api_error_code())
                yield
            finally:
                # A probe must release the local terminal bridge even on failure.
                self._api.shutdown()

    def _mode(self, trade_mode: int | None) -> str:
        for label in ("DEMO", "REAL", "CONTEST"):
            mt5_value = getattr(self._api, f"ACCOUNT_TRADE_MODE_{label}", None)
            if mt5_value is not None and trade_mode == mt5_value:
                return label
        return "UNKNOWN"

    def capture(self, symbol: str, *, timeframe: str = "M15",
                bars: int = 100) -> dict[str, Any]:
        """Read a self-contained broker snapshot and reject unsafe/incomplete data.

        Copy starts from bar index 1, not index 0 (current unfinished candle).
        Times and quote freshness are assessed in UTC.
        """
        if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol):
            raise MT5ReadError("INVALID_SYMBOL")
        if timeframe not in _TIMEFRAMES:
            raise MT5ReadError("INVALID_TIMEFRAME")
        if not isinstance(bars, int) or isinstance(bars, bool) or not 2 <= bars <= 2000:
            raise MT5ReadError("INVALID_BAR_COUNT")

        timeframe_attr, interval_seconds = _TIMEFRAMES[timeframe]
        with self._connection():
            now = float(self._clock())
            if not math.isfinite(now):
                raise MT5ReadError("INVALID_CLOCK")
            terminal = self._require(self._api.terminal_info(), "TERMINAL_UNAVAILABLE")
            if _field(terminal, "connected") is not True:
                raise MT5ReadError("TERMINAL_DISCONNECTED")
            account = self._require(self._api.account_info(), "ACCOUNT_UNAVAILABLE")
            spec = self._require(self._api.symbol_info(symbol), "SYMBOL_NOT_FOUND")
            if _field(spec, "visible") is not True:
                # Deliberately does not call symbol_select (mutates Market Watch).
                raise MT5ReadError("SYMBOL_NOT_VISIBLE")

            tick = self._require(self._api.symbol_info_tick(symbol), "TICK_UNAVAILABLE")
            bid = _number(_field(tick, "bid"), "bid")
            ask = _number(_field(tick, "ask"), "ask")
            if bid is None or ask is None or bid <= 0 or ask <= bid:
                raise MT5ReadError("INVALID_BID_ASK")
            msec = _field(tick, "time_msc")
            tsec = _field(tick, "time")
            tick_time = (float(msec) / 1000.0) if msec is not None else float(tsec or 0)
            if not math.isfinite(tick_time) or not -2 <= now - tick_time <= self._max_tick_age:
                raise MT5ReadError("STALE_OR_FUTURE_TICK")

            interval = getattr(self._api, timeframe_attr, None)
            if interval is None:
                raise MT5ReadError("TIMEFRAME_NOT_SUPPORTED_BY_API")
            data = self._require(
                self._api.copy_rates_from_pos(symbol, interval, 1, bars),
                "RATES_UNAVAILABLE",
            )
            if len(data) != bars:
                raise MT5ReadError("INSUFFICIENT_COMPLETED_BARS")
            candles = []
            previous = None
            for row in data:
                timestamp = _number(_field(row, "time"), "time")
                if timestamp is None or timestamp <= 0 or (
                    previous is not None and timestamp <= previous
                ) or timestamp + interval_seconds > now + 2:
                    raise MT5ReadError("INVALID_BAR_TIME")
                previous = timestamp
                candle = _fields(row, ("open", "high", "low", "close",
                                       "tick_volume", "spread", "real_volume"))
                if any(candle[k] is None for k in ("open", "high", "low", "close")):
                    raise MT5ReadError("MISSING_OHLC")
                if candle["low"] > min(candle["open"], candle["close"]) or (
                    candle["high"] < max(candle["open"], candle["close"])
                ):
                    raise MT5ReadError("INVALID_OHLC")
                candles.append({"time_utc": _utc(timestamp), **candle})

            positions = self._require(self._api.positions_get(symbol=symbol), "POSITIONS_UNAVAILABLE")
            orders = self._require(self._api.orders_get(symbol=symbol), "ORDERS_UNAVAILABLE")
            account_trade_mode = _number(_field(account, "trade_mode"), "trade_mode")
            # Account switching between the beginning and end of the read
            # must NEVER stamp observations with the wrong account identity.
            final_account = self._require(
                self._api.account_info(), "ACCOUNT_UNAVAILABLE"
            )
            if any(_field(account, key) != _field(final_account, key)
                   for key in ("login", "server", "trade_mode")):
                raise MT5ReadError("ACCOUNT_CHANGED_DURING_CAPTURE")
            # Explicit allowlists prevent leaking login, server, name and paths.
            result = {
                "source": "MT5_LOCAL_TERMINAL",
                "captured_at_utc": _utc(now),
                "mode": "READ_ONLY",
                "execution": "DISABLED",
                "account": {
                    "trade_mode": self._mode(account_trade_mode),
                    "trade_allowed": _field(account, "trade_allowed") is True,
                    "trade_expert": _field(account, "trade_expert") is True,
                    "currency": str(_field(account, "currency", "UNKNOWN"))[:12],
                    "leverage": _number(_field(account, "leverage"), "leverage"),
                    "margin_mode": _number(_field(account, "margin_mode"), "margin_mode"),
                    **_fields(account, _ACCOUNT_NUMBERS),
                },
                "terminal": {
                    "connected": True,
                    "trade_allowed": _field(terminal, "trade_allowed") is True,
                    "tradeapi_disabled": _field(terminal, "tradeapi_disabled") is True,
                    "maxbars": _number(_field(terminal, "maxbars"), "maxbars"),
                },
                "symbol": {"name": symbol, "visible": True, **_fields(spec, _SYMBOL_NUMBERS)},
                "timeframe": timeframe,
                "quote": {"bid": bid, "ask": ask, "spread_price": ask - bid,
                          "time_utc": _utc(tick_time), "age_seconds": now - tick_time},
                "candles": candles,
                "positions": [_fields(x, _POSITION_NUMBERS) for x in positions],
                "orders": [_fields(x, _ORDER_NUMBERS) for x in orders],
                "decision": "NOT_EVALUATED",
            }
            if self._identity_key is not None:
                try:
                    result = seal_snapshot(
                        result, self._identity_key,
                        login=_field(account, "login"),
                        server=_field(account, "server"),
                    )
                except IdentityError as exc:
                    raise MT5ReadError(exc.code) from None
            return result
