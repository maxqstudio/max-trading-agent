"""Offline-only, deterministic DEMO proposal veto. NEVER an order authorization."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .identity import IdentityError, verify_snapshot

_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,39}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
_UNMET = (
    "BROKER_MARGIN_UNCHECKED", "TRADE_SESSION_UNCHECKED",
    "BROKER_ORDER_CHECK_UNCHECKED", "DEMO_RECONCILIATION_UNIMPLEMENTED",
    "OWNER_ACCEPTANCE_PENDING",
)


class PreflightError(ValueError):
    """Sanitized code, never an account ID, secret, input or path."""


def _dec(raw: Any, *, positive: bool = False) -> Decimal:
    if type(raw) not in (int, float, str, Decimal):
        raise PreflightError("INVALID_NUMBER")
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or (value <= 0 if positive else value < 0):
            raise ValueError()
        if len(value.as_tuple().digits) > 22 or abs(value.adjusted()) > 18:
            raise ValueError()
        return value
    except (InvalidOperation, ValueError, OverflowError):
        raise PreflightError("INVALID_NUMBER") from None


def _int(raw: Any, maximum: int = 1000000) -> int:
    if type(raw) is not int or not 0 <= raw <= maximum:
        raise PreflightError("INVALID_INTEGER")
    return raw


def _time(raw: Any) -> datetime:
    if not isinstance(raw, str) or len(raw) > 40:
        raise PreflightError("INVALID_TIME")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise PreflightError("INVALID_TIME") from None


def _get(data: dict, key: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise PreflightError("INCOMPLETE_PRECHECK_DATA")
    return data[key]


def check_demo_preflight(snapshot: dict, proposal: dict, limits: dict,
                         key: bytes, *, now: datetime | None = None) -> dict:
    """HMAC-bound risk checks using captured broker facts. No MT5 API calls.

    Tick value is informative, not independently verified broker margin/cost.
    Passing static checks never unlocks order_send, even in DEMO.
    """
    if not all(isinstance(x, dict) for x in (snapshot, proposal, limits)):
        raise PreflightError("INVALID_INPUT")
    try:
        verify_snapshot(snapshot, key)
    except IdentityError:
        raise PreflightError("SNAPSHOT_UNVERIFIED") from None

    name = _get(proposal, "symbol")
    side = _get(proposal, "side")
    request_id = _get(proposal, "request_id")
    allowed = _get(limits, "allowed_symbols")
    if not isinstance(name, str) or not _SYMBOL.fullmatch(name):
        raise PreflightError("INVALID_SYMBOL")
    if type(side) is not str or side not in ("LONG", "SHORT"):
        raise PreflightError("INVALID_SIDE")
    if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
        raise PreflightError("INVALID_REQUEST_ID")
    if (not isinstance(allowed, list) or not 1 <= len(allowed) <= 32
            or any(not isinstance(x, str) or not _SYMBOL.fullmatch(x) for x in allowed)):
        raise PreflightError("INVALID_ALLOWLIST")

    volume = _dec(_get(proposal, "volume"), positive=True)
    stop = _dec(_get(proposal, "stop_loss"), positive=True)
    target = _dec(_get(proposal, "take_profit"), positive=True)
    risk_fraction = _dec(_get(limits, "max_risk_fraction"), positive=True)
    max_spread = _dec(_get(limits, "max_spread_points"), positive=True)
    max_positions = _int(_get(limits, "max_positions"))
    max_orders = _int(_get(limits, "max_orders"))
    kill = _get(limits, "kill_switch")
    if type(kill) is not bool or risk_fraction > Decimal("0.05"):
        raise PreflightError("UNSAFE_POLICY")

    account = _get(snapshot, "account")
    terminal = _get(snapshot, "terminal")
    symbol = _get(snapshot, "symbol")
    quote = _get(snapshot, "quote")
    if not all(isinstance(x, dict) for x in (account, terminal, symbol, quote)):
        raise PreflightError("INVALID_BROKER_SCHEMA")
    positions = _get(snapshot, "positions")
    orders = _get(snapshot, "orders")
    if not isinstance(positions, list) or not isinstance(orders, list):
        raise PreflightError("INVALID_INVENTORY")
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise PreflightError("INVALID_OBSERVATION_TIME")
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured = _time(_get(snapshot, "captured_at_utc"))
    quoted = _time(_get(quote, "time_utc"))

    bid = _dec(_get(quote, "bid"), positive=True)
    ask = _dec(_get(quote, "ask"), positive=True)
    point = _dec(_get(symbol, "point"), positive=True)
    tick_size = _dec(_get(symbol, "trade_tick_size"), positive=True)
    tick_value = _dec(_get(symbol, "trade_tick_value"), positive=True)
    min_vol = _dec(_get(symbol, "volume_min"), positive=True)
    max_vol = _dec(_get(symbol, "volume_max"), positive=True)
    step = _dec(_get(symbol, "volume_step"), positive=True)
    _dec(_get(symbol, "trade_contract_size"), positive=True)
    stop_points = _int(_get(symbol, "trade_stops_level"))
    _int(_get(symbol, "trade_freeze_level"))
    symbol_trade_mode = _int(_get(symbol, "trade_mode"), 100)
    balance = _dec(_get(account, "balance"), positive=True)
    equity = _dec(_get(account, "equity"), positive=True)

    vetoes: list[str] = []
    def veto(failure: bool, code: str) -> None:
        if failure:
            vetoes.append(code)

    veto(kill, "KILL_SWITCH")
    veto(name not in allowed or symbol.get("name") != name, "SYMBOL_DENIED")
    veto(account.get("trade_mode") != "DEMO", "NON_DEMO_ACCOUNT")
    veto(terminal.get("connected") is not True, "DISCONNECTED")
    veto(account.get("trade_allowed") is not True
         or account.get("trade_expert") is not True
         or terminal.get("trade_allowed") is not True
         or terminal.get("tradeapi_disabled") is not False, "BROKER_PERMISSIONS_UNKNOWN")
    veto(symbol.get("visible") is not True or symbol_trade_mode == 0, "SYMBOL_DISABLED")
    veto(bid >= ask, "INVALID_SPREAD")
    veto(not -2 <= (clock - captured).total_seconds() <= 120
         or not -2 <= (captured - quoted).total_seconds() <= 90, "QUOTE_STALE")
    veto(volume < min_vol or volume > max_vol
         or (volume - min_vol) % step != 0, "INVALID_VOLUME")
    veto(len(positions) >= max_positions or len(orders) >= max_orders, "EXPOSURE_CAP")
    veto((ask - bid) / point > max_spread, "SPREAD_CAP")
    entry = ask if side == "LONG" else bid
    stop_distance = (bid - stop) if side == "LONG" else (stop - ask)
    target_distance = (target - ask) if side == "LONG" else (bid - target)
    veto(stop_distance <= 0 or target_distance <= 0, "INVALID_STOPS")
    veto(stop_distance < point * stop_points, "STOP_LEVEL")
    indicative_risk = abs(entry - stop) / tick_size * tick_value * volume
    veto(indicative_risk > min(balance, equity) * risk_fraction, "RISK_CAP")
    return {
        "mode": "DEMO_PREFLIGHT_ONLY", "decision": "NO_TRADE",
        "broker_execution": False, "owner_acceptance": "PENDING",
        "checks": "PASS_CHECKS" if not vetoes else "BLOCKED",
        "vetoes": vetoes, "unmet_broker_gates": list(_UNMET),
        "symbol": name, "side": side,
        "risk_model": "INDICATIVE_TICK_VALUE_NO_COMMISSION_OR_REAL_FILL",
    }
