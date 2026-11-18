"""Read-only, account-safe summary of *hypothetical* forward SHADOW evidence.

No market calls, credentials, LLM, order tools or data mutations. The view
must show ALL opted-in attempts, not just favorable settled candidates.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

MIN_FORWARD_SAMPLE = 30
_HEX32 = re.compile(r"[a-f0-9]{32}\Z")
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_JOB_STATES = frozenset({"PENDING", "RUNNING", "COMPLETED", "FAILED",
                         "REVIEW_REQUIRED", "BLOCKED"})
_EPISODE_STATES = frozenset({"PENDING", "SETTLED"})


class EvidenceError(ValueError):
    """Sanitized error: never include private DB contents."""


def _utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("not UTC aware")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED") from None


def _finite(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
    return number


def _number(value: float) -> float:
    return round(float(value), 12)


def _range(values: list[float]) -> dict:
    return {"min": _number(min(values)), "max": _number(max(values))}


def _matches_price(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, abs_tol=1e-12, rel_tol=1e-12)


class ForwardEvidence:
    """Computes a current SQLite snapshot, never creating a missing database."""

    def __init__(self, journal_path: Path):
        self.path = Path(journal_path)

    def summary(self) -> dict:
        report = {
            "status": "NO_DATA",
            "mode": "SHADOW_EVIDENCE_ONLY",
            "decision": "NO_TRADE",
            "broker_execution": False,
            "financial_profit_proven": False,
            "minimum_completed_sample": MIN_FORWARD_SAMPLE,
            "units": "PRICE_UNITS_PER_ONE_ASSET_UNIT",
            "totals": {
                "attempted_cycles": 0, "completed_jobs": 0, "failed_jobs": 0,
                "blocked_jobs": 0, "review_required_jobs": 0,
                "running_jobs": 0, "pending_jobs": 0, "abstained": 0,
                "rejected": 0, "settled_episodes": 0, "pending_episodes": 0,
                "legacy_manual_excluded": 0, "unmatched_cycles": 0,
            },
            "symbols": [],
            "limitations": (
                "Hypothetical price differences, not broker fills or account-currency P/L. "
                "No global P/L aggregation across symbols/accounts. "
                "A sample threshold never grants order permission."
            ),
        }
        if not self.path.is_file():
            return report
        try:
            # mode=ro ensures GET cannot create or change the private journal.
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "watch_jobs" not in tables and "shadow_episodes" not in tables:
                    return report
                jobs = []
                episodes = []
                events = []
                if "watch_jobs" in tables:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(watch_jobs)")}
                    if "shadow_requested" not in columns:
                        # Unknown historical opt-in state: NEVER report as zero.
                        raise EvidenceError("EVIDENCE_LEGACY_SCHEMA")
                    all_jobs = [dict(row) for row in db.execute(
                        "SELECT job_id,symbol,timeframe,bar_utc,status,research_run_id,"
                        "shadow_requested FROM watch_jobs"
                    )]
                    # No SQLite CHECK on this flag: invalid values must
                    # never disappear silently from attempted-cycle counts.
                    if any(type(job["shadow_requested"]) is not int or
                           job["shadow_requested"] not in (0, 1)
                           for job in all_jobs):
                        raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                    jobs = [job for job in all_jobs if job["shadow_requested"] == 1]
                if "shadow_episodes" in tables:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(shadow_episodes)")}
                    fields = ("candidate_id","origin","symbol","side","status",
                              "entry_snapshot_hash","entry_quote_utc","entry_bid",
                              "entry_ask","entry_price","created_utc",
                              "exit_snapshot_hash","exit_quote_utc","exit_bid",
                              "exit_ask","exit_price","net_pnl_price_units",
                              "recorded_utc","slippage","fee_per_side",
                              "account_binding_id","research_run_id","watch_job_id")
                    if not set(fields).issubset(columns):
                        raise EvidenceError("EVIDENCE_LEGACY_SCHEMA")
                    episodes = [dict(row) for row in db.execute(
                        "SELECT " + ",".join(fields) + " FROM shadow_episodes"
                    )]
                if "events" in tables:
                    events = [dict(row) for row in db.execute(
                        "SELECT event_type,details FROM events "
                        "WHERE event_type IN ('HYPOTHESIS_ABSTAINED','HYPOTHESIS_REJECTED')"
                    )]
                return self._summarize(report, jobs, episodes, events)
        except EvidenceError:
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError):
            raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED") from None

    @staticmethod
    def _summarize(report: dict, jobs: list[dict],
                   episodes: list[dict], events: list[dict]) -> dict:
        totals = report["totals"]
        jobs_by_id = {}
        symbols = defaultdict(lambda: {"jobs": [], "episodes": [],
                                      "abstained": 0, "rejected": 0})
        for job in jobs:
            job_id, symbol, state = job["job_id"], job["symbol"], job["status"]
            if (not isinstance(job_id, str) or not _HEX64.fullmatch(job_id)
                    or not isinstance(symbol, str) or not symbol
                    or state not in _JOB_STATES or job_id in jobs_by_id):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            jobs_by_id[job_id] = job
            symbols[symbol]["jobs"].append(job)
            totals["attempted_cycles"] += 1
            label = {
                "COMPLETED": "completed_jobs", "FAILED": "failed_jobs",
                "BLOCKED": "blocked_jobs", "REVIEW_REQUIRED": "review_required_jobs",
                "RUNNING": "running_jobs", "PENDING": "pending_jobs",
            }[state]
            totals[label] += 1
        recognized = {"HYPOTHESIS_ABSTAINED": "abstained",
                      "HYPOTHESIS_REJECTED": "rejected"}
        event_seen = set()
        for event in events:
            try:
                details = json.loads(event["details"])
                jid = details.get("watch_job_id")
            except (ValueError, TypeError, AttributeError):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED") from None
            kind = recognized.get(event["event_type"])
            if jid not in jobs_by_id or kind is None:
                continue
            unique = (jid, kind)
            if unique not in event_seen:
                event_seen.add(unique)
                totals[kind] += 1
                symbols[jobs_by_id[jid]["symbol"]][kind] += 1
        if set(j for j, k in event_seen if k == "abstained") & set(
            j for j, k in event_seen if k == "rejected"
        ):
            raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")

        candidate_jobs = set()
        for ep in episodes:
            if ep["origin"] not in ("LLM_RESEARCH", "USER_RESEARCH"):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            if ep["origin"] != "LLM_RESEARCH":
                totals["legacy_manual_excluded"] += 1
                continue
            if ep["account_binding_id"] is None:
                # Unverified automated records must not be reclassified as
                # harmless manual history and silently hidden from the gate.
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            job_id = ep["watch_job_id"]
            if (job_id not in jobs_by_id or job_id in candidate_jobs
                    or ep["symbol"] != jobs_by_id[job_id]["symbol"]
                    or ep["side"] not in ("LONG", "SHORT")
                    or ep["status"] not in _EPISODE_STATES
                    or not isinstance(ep["account_binding_id"], str)
                    or not _HEX64.fullmatch(ep["account_binding_id"])
                    or not isinstance(ep["research_run_id"], str)
                    or not _HEX32.fullmatch(ep["research_run_id"])):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            linked = jobs_by_id[job_id]["research_run_id"]
            if linked is not None and linked != ep["research_run_id"]:
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            if (job_id, "abstained") in event_seen or (job_id, "rejected") in event_seen:
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            candidate_jobs.add(job_id)
            entry_price = _finite(ep["entry_price"], positive=True)
            entry_bid = _finite(ep["entry_bid"], positive=True)
            entry_ask = _finite(ep["entry_ask"], positive=True)
            fee = _finite(ep["fee_per_side"])
            slip = _finite(ep["slippage"])
            if (fee < 0 or slip < 0 or entry_ask < entry_bid
                    or not isinstance(ep["entry_snapshot_hash"], str)
                    or not _HEX64.fullmatch(ep["entry_snapshot_hash"])):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            expected_entry = (entry_ask + slip if ep["side"] == "LONG"
                              else entry_bid - slip)
            if not _matches_price(entry_price, expected_entry):
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
            entry_time = _utc(ep["entry_quote_utc"])
            created_time = _utc(ep["created_utc"])
            exit_fields = ("exit_snapshot_hash", "exit_quote_utc", "exit_bid",
                           "exit_ask", "exit_price", "net_pnl_price_units",
                           "recorded_utc")
            if ep["status"] == "SETTLED":
                if (not isinstance(ep["exit_snapshot_hash"], str)
                        or not _HEX64.fullmatch(ep["exit_snapshot_hash"])):
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                exit_time = _utc(ep["exit_quote_utc"])
                _utc(ep["recorded_utc"])
                if exit_time <= entry_time or exit_time <= created_time:
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                exit_bid = _finite(ep["exit_bid"], positive=True)
                exit_ask = _finite(ep["exit_ask"], positive=True)
                exit_price = _finite(ep["exit_price"], positive=True)
                if exit_ask < exit_bid:
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                expected_exit = (exit_bid - slip if ep["side"] == "LONG"
                                 else exit_ask + slip)
                if not _matches_price(exit_price, expected_exit):
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                recorded = _finite(ep["net_pnl_price_units"])
                expected = ((exit_price - entry_price)
                            if ep["side"] == "LONG"
                            else (entry_price - exit_price)) - 2 * fee
                if not math.isclose(recorded, expected, abs_tol=1e-9, rel_tol=1e-9):
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                totals["settled_episodes"] += 1
            else:
                if any(ep[field] is not None for field in exit_fields):
                    raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")
                totals["pending_episodes"] += 1
            symbols[ep["symbol"]]["episodes"].append(ep)
        for jid, kind in event_seen:
            if jid in candidate_jobs:
                raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")

        totals["unmatched_cycles"] = totals["attempted_cycles"] - (
            len(candidate_jobs) + totals["abstained"] + totals["rejected"]
        )
        if totals["unmatched_cycles"] < 0:
            raise EvidenceError("EVIDENCE_INTEGRITY_BLOCKED")

        for symbol in sorted(symbols):
            group = symbols[symbol]
            eps = group["episodes"]
            identities = {ep["account_binding_id"] for ep in eps}
            done = [ep for ep in eps if ep["status"] == "SETTLED"]
            metrics = {
                "symbol": symbol,
                "attempts": len(group["jobs"]),
                "abstained": group["abstained"], "rejected": group["rejected"],
                "pending": len(eps) - len(done),
                "settled": len(done),
                "failed_or_blocked": sum(
                    j["status"] in ("FAILED", "BLOCKED", "REVIEW_REQUIRED")
                    for j in group["jobs"]),
                "account_scope": "MIXED" if len(identities) > 1
                                 else "SINGLE" if len(identities) == 1 else "NONE",
                "evidence_grade": "NO_DATA",
                "hypothetical_pnl": None,
                "execution_assumptions": None,
            }
            if len(identities) == 1:
                metrics["execution_assumptions"] = {
                    "modeled_fee_per_side": _range(
                        [float(ep["fee_per_side"]) for ep in eps]),
                    "modeled_slippage_per_side": _range(
                        [float(ep["slippage"]) for ep in eps]),
                    "recorded_entry_spread": _range(
                        [float(ep["entry_ask"] - ep["entry_bid"]) for ep in eps]),
                    "recorded_exit_spread": (
                        _range([float(ep["exit_ask"] - ep["exit_bid"]) for ep in done])
                        if done else None),
                    "units": "PRICE_UNITS_PER_ONE_ASSET_UNIT",
                }
            if len(identities) > 1:
                metrics["evidence_grade"] = "ACCOUNT_MIXED"
            elif not done:
                metrics["evidence_grade"] = "INSUFFICIENT_FORWARD_SAMPLE"
            else:
                ordered = sorted(done, key=lambda e: (e["exit_quote_utc"],
                                                      e["candidate_id"]))
                outcomes = [float(e["net_pnl_price_units"]) for e in ordered]
                cumulative = peak = drawdown = 0.0
                for pnl in outcomes:
                    cumulative += pnl
                    peak = max(peak, cumulative)
                    drawdown = max(drawdown, peak - cumulative)
                wins = sum(v > 0 for v in outcomes)
                losses = sum(v < 0 for v in outcomes)
                metrics["evidence_grade"] = (
                    "DESCRIPTIVE_ONLY" if len(done) >= MIN_FORWARD_SAMPLE
                    else "INSUFFICIENT_FORWARD_SAMPLE"
                )
                metrics["hypothetical_pnl"] = {
                    "net_price_units_per_unit": _number(sum(outcomes)),
                    "mean_price_units_per_unit": _number(sum(outcomes) / len(outcomes)),
                    "max_realized_drawdown_price_units_per_unit": _number(drawdown),
                    "winners": wins, "losers": losses,
                    "flat": len(done) - wins - losses,
                    "win_rate": round(wins / len(done), 6),
                    "no_trade_baseline_price_units": 0,
                }
            report["symbols"].append(metrics)
        if totals["attempted_cycles"] or totals["legacy_manual_excluded"]:
            report["status"] = "OBSERVED"
        return report
