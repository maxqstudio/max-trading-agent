from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
EA_SOURCE = ROOT / "ea" / "baseline" / "Max_MTF.mq5"


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^)]*\)\s*\{{", source)
    assert match is not None, f"MQL function not found: {name}"
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"Unclosed MQL function: {name}")


def test_optimizer_finalization_replays_all_received_frames_into_fresh_sidecar() -> None:
    source = EA_SOURCE.read_text(encoding="utf-8")
    deinit = _function_body(source, "OnTesterDeinit")
    finalize = _function_body(source, "StrategyOptimizerFinalizeMetricFrames")

    assert "StrategyOptimizerFinalizeMetricFrames()" in deinit
    assert "StrategyOptimizerResetMetricSidecar()" in finalize
    assert "FrameFirst()" in finalize
    replay_call = "StrategyOptimizerProcessMetricFrames(written_count)"
    assert replay_call in finalize
    assert finalize.index("FrameFirst()") < finalize.index(replay_call)


def test_optimizer_finalizer_fails_closed_when_frame_replay_or_output_reset_fails() -> None:
    source = EA_SOURCE.read_text(encoding="utf-8")
    reset = _function_body(source, "StrategyOptimizerResetMetricSidecar")
    finalize = _function_body(source, "StrategyOptimizerFinalizeMetricFrames")

    assert "FileDelete(InpOptimizerMetricsFile)" in reset
    assert "FileOpen(InpOptimizerMetricsFile" in reset
    assert "MAX_OPTIMIZER_METRICS_RESET_FAIL" in reset
    assert "MAX_OPTIMIZER_FRAME_REPLAY_FAIL" in finalize
    assert "return false" in finalize
