"""Source-grounded MAX CP32 training CSV and ONNX research-window contracts.

The timestamps in this file are deliberately naive: Max_MTF writes broker/server
wall-clock strings without an offset. This module never converts them to UTC.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence


FEATURE_CONTRACT = "CP32_TRUE_MTF_V1"
STRATEGY_CONTRACT = "MAX_TRUE_MTF_DYNAMIC_V1"
DATASET_SCHEMA_ID = "MAX_MTF_TRAINING_CSV_49_V1"
DATASET_SCHEMA_VERSION = "1.0"

TRAINING_COLUMNS: tuple[str, ...] = (
    "contract", "signal_time", "decision_bar_time", "symbol", "period",
    "open", "high", "low", "close", "atr", "decision_bid", "decision_ask",
    "spread_points", "sl_atr", "tp_atr", "max_hold_bars", "consensus",
    "ret1_atr", "ret3_atr", "ret6_atr", "fast_ma_gap_atr", "slow_ma_gap_atr",
    "fast_ma_slope_atr", "adx_scaled", "plus_di_scaled", "minus_di_scaled",
    "rsi_centered", "atr_percent", "atr_ratio", "bollinger_z",
    "bollinger_width_pct", "efficiency10", "candle_body_atr", "upper_wick_atr",
    "lower_wick_atr", "range_atr", "tick_volume_z", "hour_sin", "hour_cos",
    "weekday_sin", "weekday_cos", "trend_family", "range_family",
    "breakout_family", "pullback_family", "session_family", "shock_family",
    "relative_family", "rule_meta_score",
)
FEATURE_COLUMNS: tuple[str, ...] = TRAINING_COLUMNS[17:]
IDENTITY_COLUMNS = ("contract", "symbol", "period", "signal_time")
WINDOW_NAMES = ("DISCOVERY", "TOURNAMENT", "FORWARD")

# MQL5 ENUM_TIMEFRAMES identifiers (not all values are minute counts).
TIMEFRAME_MINUTES: dict[int, tuple[str, int | None]] = {
    1: ("M1", 1), 2: ("M2", 2), 3: ("M3", 3), 4: ("M4", 4), 5: ("M5", 5),
    6: ("M6", 6), 10: ("M10", 10), 12: ("M12", 12), 15: ("M15", 15),
    20: ("M20", 20), 30: ("M30", 30), 16385: ("H1", 60),
    16386: ("H2", 120), 16387: ("H3", 180), 16388: ("H4", 240),
    16390: ("H6", 360), 16392: ("H8", 480), 16396: ("H12", 720),
    16408: ("D1", 1440), 32769: ("W1", 10080), 49153: ("MN1", None),
}

_TIME_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}$")
# Broker symbol suffixes are not centrally standardized (for example `.m`, `#`,
# or `+`). Preserve any printable ASCII identifier except the CSV delimiter.
_SYMBOL_PATTERN = re.compile(r"^[\x21-\x3A\x3C-\x7E]{1,64}$")


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str = "BLOCKER"


@dataclass(frozen=True)
class DataQualityReport:
    status: str
    schema_status: str
    row_count: int
    symbol: str | None
    timeframe: str | None
    period_enum: int | None
    timestamp_timezone: str
    timezone_provenance: str | None
    timestamp_min: str | None
    timestamp_max: str | None
    identical_duplicate_rows: int
    conflicting_duplicate_identities: int
    timestamp_discontinuity_status: str
    broker_reconciliation_status: str
    issues: tuple[DataQualityIssue, ...]
    _timestamps: tuple[datetime, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class ResearchWindow:
    name: str
    start: str
    end: str
    row_count: int


@dataclass(frozen=True)
class WindowValidation:
    status: str
    timezone_provenance: str | None
    windows: tuple[ResearchWindow, ...]
    issues: tuple[DataQualityIssue, ...]


def _issue(code: str, message: str, severity: str = "BLOCKER") -> DataQualityIssue:
    return DataQualityIssue(code=code, message=message, severity=severity)


def _timestamp(value: str) -> datetime:
    if not _TIME_PATTERN.fullmatch(value):
        raise ValueError("TIMESTAMP_FORMAT_INVALID")
    return datetime.strptime(value, "%Y.%m.%d %H:%M")


def _iso_naive(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError("WINDOW_TIMEZONE_MUST_MATCH_SOURCE_NAIVE")
    return parsed


def audit_training_csv(raw: bytes, *, timezone_provenance: str | None) -> DataQualityReport:
    """Audit an exact byte snapshot of the MAX EA's semicolon-delimited CSV."""
    issues: list[DataQualityIssue] = []
    if not raw or not raw.endswith(b"\n"):
        return DataQualityReport(
            "BLOCKED", "INVALID", 0, None, None, None,
            "NAIVE_BROKER_SOURCE_TIME", timezone_provenance, None, None,
            0, 0, "NOT_ASSESSED", "NOT_ASSESSED",
            (_issue("PARTIAL_TRAILING_ROW", "Source must end at a complete flushed CSV row."),),
        )
    if len(raw) > 256 * 1024 * 1024:
        return DataQualityReport(
            "BLOCKED", "INVALID", 0, None, None, None,
            "NAIVE_BROKER_SOURCE_TIME", timezone_provenance, None, None,
            0, 0, "NOT_ASSESSED", "NOT_ASSESSED",
            (_issue("SOURCE_SIZE_UNSUPPORTED", "CSV exceeds the versioned 256 MiB safe-audit source bound."),),
        )
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return DataQualityReport(
            "BLOCKED", "INVALID", 0, None, None, None,
            "NAIVE_BROKER_SOURCE_TIME", timezone_provenance, None, None,
            0, 0, "NOT_ASSESSED", "NOT_ASSESSED",
            (_issue("UNSUPPORTED_SOURCE_ENCODING", "MAX v1 CSV source must use the verified ANSI-compatible ASCII field representation."),),
        )
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
        header = next(reader, None)
        if header != list(TRAINING_COLUMNS):
            return DataQualityReport(
                "BLOCKED", "INVALID", 0, None, None, None,
                "NAIVE_BROKER_SOURCE_TIME", timezone_provenance, None, None,
                0, 0, "NOT_ASSESSED", "NOT_ASSESSED",
                (_issue("SCHEMA_MISMATCH", "Header, delimiter, column count, or column order differs from Max_MTF.mq5 v1."),),
            )
        records = list(reader)
    except (csv.Error, StopIteration):
        return DataQualityReport(
            "BLOCKED", "INVALID", 0, None, None, None,
            "NAIVE_BROKER_SOURCE_TIME", timezone_provenance, None, None,
            0, 0, "NOT_ASSESSED", "NOT_ASSESSED",
            (_issue("CSV_PARSE_ERROR", "CSV syntax is malformed."),),
        )

    if not records:
        issues.append(_issue("EMPTY_DATASET", "The source contains the header but no training rows."))
    if not timezone_provenance or not timezone_provenance.strip():
        issues.append(_issue("TIMEZONE_PROVENANCE_REQUIRED", "Owner-declared broker/server timezone provenance is required; no UTC assumption is made."))
    elif len(timezone_provenance) > 160 or any(ord(ch) < 32 for ch in timezone_provenance):
        issues.append(_issue("TIMEZONE_PROVENANCE_INVALID", "Timezone provenance must be a concise, printable declaration."))

    symbols: set[str] = set()
    periods: set[int] = set()
    timestamps: list[datetime] = []
    first_rows: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    identical_duplicate_rows = 0
    conflicting_duplicate_identities = 0
    previous_timestamp: datetime | None = None
    timestamp_discontinuity_status = "NO_OBSERVED_DISCONTINUITY"
    expected_minutes: int | None = None

    for line_no, fields in enumerate(records, start=2):
        if len(fields) != len(TRAINING_COLUMNS):
            issues.append(_issue("ROW_COLUMN_COUNT_MISMATCH", f"CSV row {line_no} has {len(fields)} columns; exactly 49 are required."))
            continue
        if any(value == "" for value in fields):
            issues.append(_issue("EMPTY_REQUIRED_FIELD", f"CSV row {line_no} contains an empty field."))
            continue
        if fields[0] != FEATURE_CONTRACT:
            issues.append(_issue("FEATURE_CONTRACT_MISMATCH", f"CSV row {line_no} is not {FEATURE_CONTRACT}."))
            continue
        symbol = fields[3]
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            issues.append(_issue("SYMBOL_INVALID", f"CSV row {line_no} has an unsupported symbol identifier."))
            continue
        try:
            period = int(fields[4], 10)
            if str(period) != fields[4]:
                raise ValueError
            signal_time = _timestamp(fields[1])
            _timestamp(fields[2])
        except (ValueError, OverflowError):
            issues.append(_issue("IDENTITY_OR_TIMESTAMP_INVALID", f"CSV row {line_no} has an invalid period or timestamp."))
            continue
        if period not in TIMEFRAME_MINUTES:
            issues.append(_issue("UNSUPPORTED_TIMEFRAME", f"CSV row {line_no} uses unsupported MQL timeframe enum {period}."))
            continue
        try:
            numbers = [float(fields[index]) for index in range(5, 49)]
        except (ValueError, OverflowError):
            issues.append(_issue("NUMERIC_PARSE_ERROR", f"CSV row {line_no} contains a non-numeric value in a numeric column."))
            continue
        if not all(math.isfinite(number) for number in numbers):
            issues.append(_issue("NONFINITE_NUMERIC_VALUE", f"CSV row {line_no} contains NaN or infinity."))
            continue

        open_price, high, low, close, atr = numbers[:5]
        bid, ask, spread = numbers[5:8]
        if min(open_price, high, low, close, bid, ask) <= 0 or atr <= 0:
            issues.append(_issue("PRICE_OR_ATR_NOT_POSITIVE", f"CSV row {line_no} has non-positive price or ATR."))
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            issues.append(_issue("OHLC_INCONSISTENT", f"CSV row {line_no} violates OHLC high/low ordering."))
        if ask < bid or spread < 0:
            issues.append(_issue("QUOTE_OR_SPREAD_INVALID", f"CSV row {line_no} has ask below bid or negative spread."))
        if not numbers[10].is_integer() or numbers[10] < 0:
            issues.append(_issue("MAX_HOLD_BARS_INVALID", f"CSV row {line_no} has an invalid max_hold_bars value."))

        identity = (fields[0], symbol, fields[4], fields[1])
        existing = first_rows.get(identity)
        if existing is not None:
            if existing == tuple(fields):
                identical_duplicate_rows += 1
            else:
                conflicting_duplicate_identities += 1
            continue
        first_rows[identity] = tuple(fields)
        symbols.add(symbol)
        periods.add(period)
        timestamps.append(signal_time)
        if period in TIMEFRAME_MINUTES:
            expected_minutes = TIMEFRAME_MINUTES[period][1]
        if previous_timestamp is not None:
            if signal_time < previous_timestamp:
                issues.append(_issue("NONCHRONOLOGICAL_TIMESTAMP", f"CSV row {line_no} is earlier than its preceding unique row."))
            elif expected_minutes is None and period == 49153:
                timestamp_discontinuity_status = "GAP_DETECTION_UNAVAILABLE"
                issues.append(_issue("TIMEFRAME_GAP_INTERVAL_UNDEFINED", "MQL monthly bars have variable calendar length; discontinuity cannot be classified using a fixed interval.", "RECONCILIATION_PENDING"))
            elif expected_minutes and (signal_time - previous_timestamp).total_seconds() > expected_minutes * 60:
                timestamp_discontinuity_status = "OBSERVED_TIMESTAMP_DISCONTINUITY"
                issues.append(_issue(
                    "OBSERVED_TIMESTAMP_DISCONTINUITY",
                    f"Rows at {previous_timestamp.isoformat()} and {signal_time.isoformat()} are more than one {TIMEFRAME_MINUTES[period][0]} interval apart; this is not proof of a missing broker bar.",
                    "RECONCILIATION_PENDING",
                ))
        previous_timestamp = signal_time

    if len(symbols) > 1:
        issues.append(_issue("MIXED_SYMBOL", "One ONNX dataset must contain exactly one symbol."))
    if len(periods) > 1:
        issues.append(_issue("MIXED_TIMEFRAME", "One ONNX dataset must contain exactly one MQL timeframe."))
    if identical_duplicate_rows:
        issues.append(_issue("IDENTICAL_DUPLICATES_REQUIRE_EXPLICIT_RESOLUTION", f"Found {identical_duplicate_rows} identical duplicate row(s); no row was silently removed."))
    if conflicting_duplicate_identities:
        issues.append(_issue("CONFLICTING_DUPLICATE_IDENTITY", f"Found {conflicting_duplicate_identities} conflicting row(s) with the same contract/symbol/period/signal_time identity."))

    status = "PASS" if not any(issue.severity == "BLOCKER" for issue in issues) else "BLOCKED"
    period_enum = next(iter(periods)) if len(periods) == 1 else None
    timeframe = TIMEFRAME_MINUTES[period_enum][0] if period_enum is not None else None
    ordered = sorted(timestamps)
    return DataQualityReport(
        status=status,
        schema_status="PASS",
        row_count=len(records),
        symbol=next(iter(symbols)) if len(symbols) == 1 else None,
        timeframe=timeframe,
        period_enum=period_enum,
        timestamp_timezone="NAIVE_BROKER_SOURCE_TIME",
        timezone_provenance=timezone_provenance.strip() if timezone_provenance else None,
        timestamp_min=ordered[0].isoformat() if ordered else None,
        timestamp_max=ordered[-1].isoformat() if ordered else None,
        identical_duplicate_rows=identical_duplicate_rows,
        conflicting_duplicate_identities=conflicting_duplicate_identities,
        timestamp_discontinuity_status=timestamp_discontinuity_status,
        # ONNX-02 has no broker-history evidence source or correction authority.
        # Absence of an observed timestamp gap is not broker completeness proof.
        broker_reconciliation_status="BROKER_RECONCILIATION_PENDING",
        issues=tuple(issues),
        _timestamps=tuple(timestamps),
    )


def remove_identical_duplicates(raw: bytes) -> tuple[bytes, int]:
    """Build a new derived CSV only when every repeated identity is byte-value identical."""
    report = audit_training_csv(raw, timezone_provenance="explicit duplicate correction from verified immutable raw snapshot")
    allowed_issues = {
        "IDENTICAL_DUPLICATES_REQUIRE_EXPLICIT_RESOLUTION",
        "OBSERVED_TIMESTAMP_DISCONTINUITY",
        "TIMEFRAME_GAP_INTERVAL_UNDEFINED",
    }
    if report.conflicting_duplicate_identities or any(issue.code not in allowed_issues for issue in report.issues):
        raise ValueError("DUPLICATE_CORRECTION_BLOCKED_CONFLICT_OR_INVALID_DATA")
    if not report.identical_duplicate_rows:
        raise ValueError("DUPLICATE_CORRECTION_NOT_REQUIRED")
    reader = csv.reader(io.StringIO(raw.decode("ascii"), newline=""), delimiter=";", strict=True)
    header = next(reader)
    seen: set[tuple[str, str, str, str]] = set()
    rows: list[list[str]] = []
    removed = 0
    for fields in reader:
        identity = (fields[0], fields[3], fields[4], fields[1])
        if identity in seen:
            removed += 1
            continue
        seen.add(identity)
        rows.append(fields)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue().encode("ascii"), removed


def validate_research_windows(
    report: DataQualityReport,
    windows: Mapping[str, Mapping[str, str]],
    *,
    timezone_provenance: str | None,
) -> WindowValidation:
    issues: list[DataQualityIssue] = []
    if set(windows) != set(WINDOW_NAMES) or len(windows) != 3:
        return WindowValidation("BLOCKED", timezone_provenance, (), (_issue("EXACT_THREE_WINDOWS_REQUIRED", "Exactly DISCOVERY, TOURNAMENT, and FORWARD windows are required."),))
    if report.status != "PASS":
        issues.append(_issue("SNAPSHOT_NOT_DATA_READY", "Windows may be prepared only against a snapshot whose mandatory data-quality checks pass."))
    if not timezone_provenance or timezone_provenance != report.timezone_provenance:
        issues.append(_issue("WINDOW_TIMEZONE_PROVENANCE_MISMATCH", "Window timezone declaration must exactly match the source snapshot provenance."))
    parsed: dict[str, tuple[datetime, datetime]] = {}
    try:
        for name in WINDOW_NAMES:
            item = windows[name]
            if set(item) != {"from", "to"}:
                raise ValueError("WINDOW_FIELDS_INVALID")
            start, end = _iso_naive(item["from"]), _iso_naive(item["to"])
            if start > end:
                issues.append(_issue("WINDOW_RANGE_REVERSED", f"{name} begins after it ends."))
            parsed[name] = (start, end)
    except (KeyError, TypeError, ValueError) as exc:
        code = str(exc) if str(exc).startswith("WINDOW_") else "WINDOW_TIMESTAMP_INVALID"
        issues.append(_issue(code, "Window boundaries must be valid timezone-naive ISO timestamps."))
        return WindowValidation("BLOCKED", timezone_provenance, (), tuple(issues))

    discovery, tournament, forward = (parsed[name] for name in WINDOW_NAMES)
    if not (discovery[1] < tournament[0] and tournament[1] < forward[0]):
        issues.append(_issue("WINDOWS_OVERLAP_OR_ORDER_INVALID", "Research windows must be chronological and non-overlapping with strict gaps between windows."))
    if not report._timestamps:
        issues.append(_issue("SNAPSHOT_HAS_NO_TIMESTAMP_COVERAGE", "Snapshot has no valid source timestamps."))
    else:
        for name, (start, end) in parsed.items():
            if start < report._timestamps[0] or end > report._timestamps[-1]:
                issues.append(_issue("WINDOW_OUTSIDE_SNAPSHOT_COVERAGE", f"{name} lies outside the immutable snapshot's timestamp coverage."))

    results: list[ResearchWindow] = []
    for name in WINDOW_NAMES:
        start, end = parsed[name]
        row_count = sum(start <= stamp <= end for stamp in report._timestamps)
        if row_count == 0:
            issues.append(_issue("WINDOW_HAS_NO_SOURCE_ROWS", f"{name} contains no source rows."))
        results.append(ResearchWindow(name, start.isoformat(), end.isoformat(), row_count))
    return WindowValidation("PASS" if not issues else "BLOCKED", timezone_provenance, tuple(results), tuple(issues))
