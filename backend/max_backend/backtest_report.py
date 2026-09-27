from __future__ import annotations

import math
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .optimizer_core import sha256_file


class _CellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture = False
        self._parts: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"td", "th"}:
            self._capture = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._capture:
            value = " ".join("".join(self._parts).replace("\xa0", " ").split())
            self.cells.append(value)
            self._capture = False
            self._parts = []


LABELS = {
    "total net profit": "total_net_profit",
    "gross profit": "gross_profit",
    "gross loss": "gross_loss",
    "profit factor": "profit_factor",
    "expected payoff": "expected_payoff",
    "recovery factor": "recovery_factor",
    "sharpe ratio": "sharpe_ratio",
    "total trades": "total_trades",
    "profit trades (% of total)": "profit_trades",
    "loss trades (% of total)": "loss_trades",
    "balance drawdown absolute": "balance_drawdown_absolute",
    "balance drawdown maximal": "balance_drawdown_maximal",
    "balance drawdown relative": "balance_drawdown_relative",
    "equity drawdown absolute": "equity_drawdown_absolute",
    "equity drawdown maximal": "equity_drawdown_maximal",
    "equity drawdown relative": "equity_drawdown_relative",
}


def _read_report(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError("BACKTEST_REPORT_EMPTY")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    sample = raw[:1024]
    if sample.count(b"\x00") > max(4, len(sample) // 10):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise RuntimeError("BACKTEST_REPORT_ENCODING_UNSUPPORTED")


def _label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().rstrip(":")).casefold()


def _number_tokens(value: str) -> list[str]:
    compact = value.replace("\xa0", " ")
    return re.findall(r"[-+]?\d[\d\s.,]*", compact)


def _parse_number_token(token: str) -> float:
    value = token.strip().replace(" ", "")
    if not value:
        raise ValueError("empty numeric token")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        tail = value.rsplit(",", 1)[-1]
        value = value.replace(",", ".") if len(tail) in {1, 2, 3} else value.replace(",", "")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite metric")
    return number


def _number(value: str) -> float:
    tokens = _number_tokens(value)
    if not tokens:
        raise ValueError(f"numeric metric missing: {value}")
    return _parse_number_token(tokens[0])


def _integer(value: str) -> int:
    number = _number(value)
    rounded = int(round(number))
    if abs(number - rounded) > 1e-9:
        raise ValueError(f"integer metric invalid: {value}")
    return rounded


def _count_pct(value: str) -> tuple[int, float | None]:
    tokens = _number_tokens(value)
    if not tokens:
        raise ValueError("trade count missing")
    count = int(round(_parse_number_token(tokens[0])))
    pct = None
    percent_match = re.search(r"([-+]?\d[\d\s.,]*)\s*%", value)
    if percent_match:
        pct = _parse_number_token(percent_match.group(1))
    return count, pct


def _amount_pct(value: str, *, relative: bool) -> tuple[float | None, float | None]:
    tokens = _number_tokens(value)
    if not tokens:
        return None, None
    parsed = [_parse_number_token(token) for token in tokens[:2]]
    first_is_pct = bool(
        re.match(r"^\s*[-+]?\d[\d\s.,]*\s*%", value)
    )
    if relative or first_is_pct:
        pct = parsed[0]
        amount = parsed[1] if len(parsed) > 1 else None
    else:
        amount = parsed[0]
        pct_match = re.search(r"([-+]?\d[\d\s.,]*)\s*%", value)
        pct = _parse_number_token(pct_match.group(1)) if pct_match else None
    return amount, pct


def _metric_pairs(cells: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, cell in enumerate(cells):
        normalized = _label(cell)
        key = LABELS.get(normalized)
        if key is None:
            # Some MT5 builds place "Label: value" in one cell.
            for label, candidate_key in LABELS.items():
                prefix = label + ":"
                if cell.casefold().startswith(prefix):
                    result[candidate_key] = cell[len(prefix):].strip()
                    break
            continue
        for candidate in cells[index + 1 : index + 4]:
            if candidate and _label(candidate) not in LABELS:
                result[key] = candidate
                break
    return result


def parse_mt5_backtest_report(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    actual_sha = sha256_file(source)
    if expected_sha256 is not None and actual_sha != str(expected_sha256):
        raise RuntimeError("BACKTEST_REPORT_HASH_MISMATCH")

    parser = _CellParser()
    try:
        parser.feed(_read_report(source))
    except Exception as exc:
        raise RuntimeError("BACKTEST_REPORT_HTML_PARSE_FAILED") from exc
    pairs = _metric_pairs(parser.cells)
    if not {"total_net_profit", "profit_factor", "total_trades"}.issubset(pairs):
        raise RuntimeError("BACKTEST_REPORT_REQUIRED_METRICS_MISSING")

    metrics: dict[str, Any] = {
        "total_net_profit": _number(pairs["total_net_profit"]),
        "gross_profit": _number(pairs["gross_profit"]) if "gross_profit" in pairs else None,
        "gross_loss": _number(pairs["gross_loss"]) if "gross_loss" in pairs else None,
        "profit_factor": _number(pairs["profit_factor"]),
        "expected_payoff": _number(pairs["expected_payoff"]) if "expected_payoff" in pairs else None,
        "recovery_factor": _number(pairs["recovery_factor"]) if "recovery_factor" in pairs else None,
        "sharpe_ratio": _number(pairs["sharpe_ratio"]) if "sharpe_ratio" in pairs else None,
        "total_trades": _integer(pairs["total_trades"]),
        "profit_trades_count": None,
        "profit_trades_pct": None,
        "loss_trades_count": None,
        "loss_trades_pct": None,
        "balance_drawdown_absolute": (
            _number(pairs["balance_drawdown_absolute"])
            if "balance_drawdown_absolute" in pairs else None
        ),
        "balance_drawdown_maximal_amount": None,
        "balance_drawdown_maximal_pct": None,
        "balance_drawdown_relative_amount": None,
        "balance_drawdown_relative_pct": None,
        "equity_drawdown_maximal_amount": None,
        "equity_drawdown_maximal_pct": None,
        "equity_drawdown_relative_amount": None,
        "equity_drawdown_relative_pct": None,
    }
    if "profit_trades" in pairs:
        metrics["profit_trades_count"], metrics["profit_trades_pct"] = _count_pct(
            pairs["profit_trades"]
        )
    if "loss_trades" in pairs:
        metrics["loss_trades_count"], metrics["loss_trades_pct"] = _count_pct(
            pairs["loss_trades"]
        )
    for prefix in ("balance", "equity"):
        maximal = pairs.get(f"{prefix}_drawdown_maximal")
        if maximal is not None:
            amount, pct = _amount_pct(maximal, relative=False)
            metrics[f"{prefix}_drawdown_maximal_amount"] = amount
            metrics[f"{prefix}_drawdown_maximal_pct"] = pct
        relative_value = pairs.get(f"{prefix}_drawdown_relative")
        if relative_value is not None:
            amount, pct = _amount_pct(relative_value, relative=True)
            metrics[f"{prefix}_drawdown_relative_amount"] = amount
            metrics[f"{prefix}_drawdown_relative_pct"] = pct

    if "equity_drawdown_absolute" in pairs:
        metrics["equity_drawdown_absolute"] = _number(
            pairs["equity_drawdown_absolute"]
        )

    return {
        "schema": "MAX_MT5_BACKTEST_METRICS_V1",
        "report_sha256": actual_sha,
        "metrics": metrics,
        "available_metrics": sorted(
            key for key, value in metrics.items() if value is not None
        ),
    }
