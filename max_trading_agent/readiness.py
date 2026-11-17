"""Read-only Trading Room release-readiness observations. Never a permissions gate.

Counts are local EventJournal observations, not broker attestation or a
software release approval. No input can enable DEMO or LIVE execution.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

_ALLOWED = {
    ("READ_ONLY", "accept_mt5_terminal"): "terminal_checks",
    ("SIGNAL", "research_signal"): "signal_research",
    ("PAPER", "paper_demo"): "paper_drills",
    ("SHADOW", "shadow_open"): "shadow_intents",
    ("SHADOW", "shadow_settle"): "shadow_settlements",
}
_RUN_STATES = frozenset({"RUNNING", "COMPLETED", "FAILED"})
_GATES = (
    ("M01", "Owner-authorized MT5 terminal verification",
     "OWNER_ACCEPTANCE_PENDING", "No independent real-terminal acceptance is recorded by hosted CI"),
    ("M02", "SIGNAL evidence and strategy evaluation",
     "ACCEPTANCE_PENDING", "Model research and offline signals are not validated broker decisions"),
    ("M03", "AUTO_DEMO native checks and reconciliation",
     "BROKER_EXECUTION_UNIMPLEMENTED", "Fake paper fills cannot prove real order_check, margin or deal readback"),
    ("M04", "Offline RL out-of-sample robustness",
     "RESEARCH_NOT_PROMOTED", "Synthetic holdout is not live account performance"),
    ("M05", "Hybrid RAG source verification and recall",
     "SOURCE_ACCEPTANCE_PENDING", "Curated documentation URLs have no independent live freshness verification"),
    ("M06", "Generated research tool production admission",
     "REVIEW_REQUIRED", "T0 Docker oracle results cannot grant risk or order permissions"),
    ("M07", "Owner-authorized live trading release",
     "LIVE_NOT_IMPLEMENTED", "No independently signed staged LIVE authorization or broker executor exists"),
)


class ReadinessError(ValueError):
    """Sanitized error; no private rows, account IDs or SQL embedded."""


class TradingReadiness:
    """Read only a previously created local journal; never create an empty DB."""

    def __init__(self, journal_path: Path):
        self.path = Path(journal_path)

    def summary(self) -> dict:
        observations = {
            name: {"attempts": 0, "completed": 0, "failed": 0, "running": 0}
            for name in dict.fromkeys(_ALLOWED.values())
        }
        report = {
            "status": "BLOCKED",
            "decision": "NO_TRADE",
            "broker_execution": False,
            "demo_authorized": False,
            "live_authorized": False,
            "owner_acceptance": "PENDING",
            "observations_status": "NOT_OBSERVED",
            "observations": observations,
            "gates": [
                {"id": label, "name": name, "status": status, "why": why}
                for label, name, status, why in _GATES
            ],
            "limitations": (
                "Local journal observations are not broker attestations, "
                "CI release approval, verified trade fills or trading authority."
            ),
        }
        if self.path.is_symlink():
            raise ReadinessError("READINESS_JOURNAL_SYMLINK")
        if not self.path.is_file():
            return report
        try:
            with closing(sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
            )) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                columns = {
                    row[1] for row in db.execute("PRAGMA table_info(runs)")
                }
                if not {"task", "mode", "status"}.issubset(columns):
                    raise ReadinessError("READINESS_SCHEMA_INVALID")
                rows = db.execute(
                    "SELECT mode,task,status,COUNT(*) FROM runs "
                    "GROUP BY mode,task,status"
                ).fetchall()
            observed = 0
            for mode, task, status, count in rows:
                if (mode, task) not in _ALLOWED:
                    # Unknown task modes are not evidence for any release gate.
                    if task in {t for _, t in _ALLOWED}:
                        raise ReadinessError("READINESS_MODE_INTEGRITY")
                    continue
                if status not in _RUN_STATES or type(count) is not int or count < 1:
                    raise ReadinessError("READINESS_STATUS_INTEGRITY")
                target = observations[_ALLOWED[(mode, task)]]
                target["attempts"] += count
                target[status.lower() if status != "RUNNING" else "running"] += count
                observed += count
            report["observations_status"] = (
                "LOCAL_RUNS_NOT_INDEPENDENTLY_VERIFIED" if observed
                else "NO_RECORDED_ACCEPTANCE_RUNS"
            )
            return report
        except ReadinessError:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
            raise ReadinessError("READINESS_JOURNAL_INTEGRITY_BLOCKED") from None
