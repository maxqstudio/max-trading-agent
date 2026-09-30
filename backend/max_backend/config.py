import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "state"
EVIDENCE_DIR = ROOT / "evidence"
ARTIFACT_ROOT = ROOT / "artifacts"
OPTIMIZER_ARTIFACT_ROOT = ARTIFACT_ROOT / "optimizer"
CHALLENGER_ARTIFACT_ROOT = ARTIFACT_ROOT / "challengers"
BACKTEST_ARTIFACT_ROOT = ARTIFACT_ROOT / "backtests"
CHALLENGER_OPERATION_ARTIFACT_ROOT = ARTIFACT_ROOT / "challenger_operations"
LEGACY_CHALLENGER_ARTIFACT_ROOT = ARTIFACT_ROOT / "strategy_challengers"
LEGACY_OPTIMIZER_EVIDENCE_ROOT = EVIDENCE_DIR / "optimizer"
LEGACY_M06_BACKTEST_EVIDENCE_ROOT = EVIDENCE_DIR / "m06" / "challenger_backtests"
CHAMPION_CURRENT_ROOT = ROOT / "ea" / "champion" / "current"
STRATEGY_HISTORY_ROOT = ARTIFACT_ROOT / "strategy_history"
PROMOTION_RECOVERY_ROOT = STATE_DIR / "promotion_recovery"
M04_EVIDENCE_ROOT = EVIDENCE_DIR / "m04"
EA_BASELINE = ROOT / "ea" / "baseline" / "Max_MTF.mq5"
EA_MANIFEST = ROOT / "ea" / "baseline" / "manifest.json"
DATABASE_PATH = STATE_DIR / "max.db"

PROJECT_NAME = "MAX Rebuild"
PROJECT_PHASE = "Phase 1"
BUILD_METADATA_PATH = ROOT / "build_metadata.json"
_BUILD_METADATA = json.loads(BUILD_METADATA_PATH.read_text(encoding="utf-8"))
MILESTONE = str(_BUILD_METADATA["milestone"])
_BASELINE_IDENTITY = json.loads(EA_MANIFEST.read_text(encoding="utf-8"))
EA_VERSION = str(_BASELINE_IDENTITY["ea_version"])
STRATEGY_CONTRACT = str(_BASELINE_IDENTITY["strategy_contract"])
