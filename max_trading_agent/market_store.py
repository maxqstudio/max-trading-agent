"""Private, immutable MT5-compatible historical candle datasets.

No market feed is fetched and no broker tool is available in this module.
Data must be explicitly provided and attributed by the operator.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_MARKET_DB = Path(".local/market_evidence.sqlite3")
TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}
_COLUMNS = ("time_utc", "open", "high", "low", "close")
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.#-]{0,39}\Z")


class MarketDataError(ValueError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _parse_timestamp(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise MarketDataError("INVALID_TIMESTAMP") from None


def _finite_positive(value: str, field: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise MarketDataError("INVALID_" + field.upper()) from None
    if not math.isfinite(number) or number <= 0:
        raise MarketDataError("INVALID_" + field.upper())
    return number


class MarketStore:
    """SQLite transactions protect an immutable dataset and experiment ledger."""

    def __init__(self, path: Path = DEFAULT_MARKET_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                quote_basis TEXT NOT NULL,
                source TEXT NOT NULL,
                rights TEXT NOT NULL,
                raw_sha256 TEXT NOT NULL,
                count INTEGER NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                gap_count INTEGER NOT NULL,
                missing_intervals INTEGER NOT NULL,
                imported_at_utc TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS candles (
                dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
                idx INTEGER NOT NULL,
                time_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                PRIMARY KEY (dataset_id, idx)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
                strategy_id TEXT NOT NULL,
                config_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                recorded_at_utc TEXT NOT NULL
            )""")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def import_csv(self, path: Path, *, symbol: str, timeframe: str,
                   source: str, quote_basis: str = "MID",
                   rights: str = "USER_PROVIDED") -> dict:
        """Import *completed* bars; nothing is interpolated or made up."""
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise MarketDataError("INVALID_SYMBOL")
        if timeframe not in TIMEFRAME_SECONDS:
            raise MarketDataError("INVALID_TIMEFRAME")
        if quote_basis not in {"MID", "BID", "ASK"}:
            raise MarketDataError("INVALID_QUOTE_BASIS")
        if not all(isinstance(x, str) and 1 <= len(x.strip()) <= 160
                   for x in (source, rights)):
            raise MarketDataError("INVALID_PROVENANCE")
        try:
            file_path = Path(path)
            if file_path.stat().st_size > 30_000_000:
                raise MarketDataError("FILE_TOO_LARGE")
            raw = file_path.read_bytes()
            source_text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError):
            raise MarketDataError("UNREADABLE_CSV") from None
        digest = hashlib.sha256(raw).hexdigest()
        try:
            reader = csv.DictReader(io.StringIO(source_text))
            if reader.fieldnames is None or set(reader.fieldnames) != set(_COLUMNS):
                raise MarketDataError("INVALID_CSV_HEADER")
            bars = []
            previous = None
            gap_count = 0
            missing = 0
            step = TIMEFRAME_SECONDS[timeframe]
            now = datetime.now(timezone.utc).timestamp()
            for row in reader:
                if len(bars) >= 200_000 or None in row:
                    raise MarketDataError("INVALID_ROW_COUNT")
                timestamp = _parse_timestamp(row["time_utc"])
                epoch = timestamp.timestamp()
                if int(epoch) != epoch or int(epoch) % step:
                    raise MarketDataError("UNALIGNED_TIMESTAMP")
                if epoch + step > now:
                    raise MarketDataError("FUTURE_OR_UNFINISHED_BAR")
                if previous is not None:
                    delta = epoch - previous
                    if delta <= 0:
                        raise MarketDataError("UNSORTED_OR_DUPLICATE_BARS")
                    if delta != step:
                        gap_count += 1
                        missing += max(0, int(delta // step) - 1)
                previous = epoch
                values = {field: _finite_positive(row[field], field)
                          for field in ("open", "high", "low", "close")}
                if not (values["low"] <= min(values["open"], values["close"])
                        <= max(values["open"], values["close"]) <= values["high"]):
                    raise MarketDataError("INVALID_OHLC")
                bars.append({"time_utc": timestamp.isoformat(), **values})
        except (csv.Error, OverflowError):
            raise MarketDataError("INVALID_CSV_FORMAT") from None
        if len(bars) < 128:
            raise MarketDataError("INSUFFICIENT_HISTORICAL_BARS")

        # Content ID includes the instrument/timeframe: avoid relying on filename.
        content = {"symbol": symbol, "timeframe": timeframe, "bars": bars}
        dataset_id = hashlib.sha256(_json(content).encode("utf-8")).hexdigest()
        metadata = {"symbol": symbol, "timeframe": timeframe,
                    "source": source.strip(), "rights": rights.strip(),
                    "quote_basis": quote_basis}
        imported = datetime.now(timezone.utc).isoformat()
        with self._db() as db:
            existing = db.execute(
                "SELECT symbol,timeframe,source,rights,quote_basis FROM datasets "
                "WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
            if existing:
                if tuple(existing) != (metadata["symbol"], metadata["timeframe"],
                                      metadata["source"], metadata["rights"],
                                      metadata["quote_basis"]):
                    raise MarketDataError("DATASET_PROVENANCE_CONFLICT")
                result = self.manifest(dataset_id)
                result["existing"] = True
                return result
            db.execute("""INSERT INTO datasets (
                dataset_id,symbol,timeframe,quote_basis,source,rights,raw_sha256,
                count,start_utc,end_utc,gap_count,missing_intervals,imported_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                dataset_id, symbol, timeframe, quote_basis, metadata["source"],
                metadata["rights"], digest, len(bars), bars[0]["time_utc"],
                bars[-1]["time_utc"], gap_count, missing, imported,
            ))
            db.executemany("""INSERT INTO candles
                (dataset_id,idx,time_utc,open,high,low,close)
                VALUES (?,?,?,?,?,?,?)""", [
                (dataset_id, idx, bar["time_utc"],
                 bar["open"], bar["high"], bar["low"], bar["close"])
                for idx, bar in enumerate(bars)
            ])
        result = self.manifest(dataset_id)
        result["existing"] = False
        return result

    def manifest(self, dataset_id: str) -> dict:
        fields = ("dataset_id", "symbol", "timeframe", "quote_basis", "source",
                  "rights", "raw_sha256", "count", "start_utc", "end_utc",
                  "gap_count", "missing_intervals", "imported_at_utc")
        with self._db() as db:
            row = db.execute(
                "SELECT " + ",".join(fields) + " FROM datasets WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        if row is None:
            raise MarketDataError("DATASET_NOT_FOUND")
        return dict(zip(fields, row))

    def candles(self, dataset_id: str) -> list[dict]:
        self.manifest(dataset_id)
        with self._db() as db:
            rows = db.execute(
                "SELECT time_utc,open,high,low,close FROM candles "
                "WHERE dataset_id=? ORDER BY idx", (dataset_id,),
            ).fetchall()
        return [dict(zip(("time_utc", "open", "high", "low", "close"), row))
                for row in rows]

    def record_experiment(self, dataset_id: str, strategy_id: str,
                          config: dict, result: dict) -> dict:
        self.manifest(dataset_id)
        experiment_id = hashlib.sha256(
            _json({"dataset_id": dataset_id, "strategy_id": strategy_id,
                   "config": config, "result": result}).encode("utf-8")
        ).hexdigest()
        recorded = datetime.now(timezone.utc).isoformat()
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO experiments VALUES (?,?,?,?,?,?)",
                (experiment_id, dataset_id, strategy_id, _json(config),
                 _json(result), recorded),
            )
        return {"experiment_id": experiment_id, "dataset_id": dataset_id,
                "strategy_id": strategy_id, "decision": "RESEARCH_ONLY",
                "execution": "DISABLED"}

    def experiment(self, experiment_id: str) -> dict:
        with self._db() as db:
            row = db.execute(
                "SELECT dataset_id,strategy_id,config_json,result_json,recorded_at_utc "
                "FROM experiments WHERE experiment_id=?", (experiment_id,),
            ).fetchone()
        if row is None:
            raise MarketDataError("EXPERIMENT_NOT_FOUND")
        return {"experiment_id": experiment_id, "dataset_id": row[0],
                "strategy_id": row[1], "config": json.loads(row[2]),
                "result": json.loads(row[3]), "recorded_at_utc": row[4]}
