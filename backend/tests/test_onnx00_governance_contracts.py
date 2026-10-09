import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _edge_exists(transitions: list[dict], source: str, target: str) -> bool:
    return any(
        source in (edge.get("from") if isinstance(edge.get("from"), list) else [edge.get("from")])
        and target in (edge.get("to") if isinstance(edge.get("to"), list) else [edge.get("to")])
        for edge in transitions
    )


def _reachable_states(initial: str, transitions: list[dict]) -> set[str]:
    edges: dict[str, set[str]] = {}
    for transition in transitions:
        sources = transition.get("from")
        sources = sources if isinstance(sources, list) else [sources]
        targets = transition.get("to")
        targets = targets if isinstance(targets, list) else [targets]
        targets += transition.get("to_one_of", [])
        for source in sources:
            edges.setdefault(source, set()).update(targets)

    reached = {initial}
    pending = [initial]
    while pending:
        for target in edges.get(pending.pop(), set()) - reached:
            reached.add(target)
            pending.append(target)
    return reached


def test_optimizer_resource_authority_supersedes_d039_without_clearing_memory_defect():
    decisions = {item["id"]: item for item in _json(".workflow/decisions.json")["decisions"]}
    d039 = decisions["D-039"]
    replacement = decisions["D-042"]

    assert d039["status"] == "SUPERSEDED"
    assert d039["superseded_by"] == "D-042"
    assert replacement["status"] == "CURRENT"
    for required in (
        "PR #18",
        "203f6ab3dc618e3edd247841aa25e1fa0951a19a",
        "terminal64.exe",
        "/config",
        "external active-process cap",
        "estimated-memory START gate",
        "new explicit Owner decision",
    ):
        assert required.lower() in replacement["decision"].lower()

    authority = _json(".workflow/authority.json")
    optimizer = next(item for item in authority["authorities"] if item["concern"] == "optimizer")
    assert "203f6ab3dc618e3edd247841aa25e1fa0951a19a" in optimizer["meaning"]
    assert "PR #18" in optimizer["meaning"]
    assert "estimated-memory START gate" in optimizer["meaning"]
    assert "NOT_PROVEN" in optimizer["meaning"]

    defects = {item["id"]: item for item in _json(".workflow/known_defects.json")["defects"]}
    memory = defects["OPTIMIZER-COMMIT-MEMORY-EXHAUSTION"]
    assert memory["status"] == "IN_PROGRESS"
    assert "NOT_PROVEN" in memory["evidence"]
    assert "neither change proves" in memory["evidence"].lower()
    repaired_ids = (
        "ONNX00-STALE-OPTIMIZER-RESOURCE-AUTHORITY",
        "ONNX00-CHALLENGER-ADMISSION-ORDER",
        "ONNX00-KPI-AUTHORIZATION-GAP",
    )
    assert all(defects[defect_id]["status"] == "FIXED/ACCEPTED_CANDIDATE" for defect_id in repaired_ids)
    acceptance = {item["id"]: item for item in _json(".workflow/acceptance.json")["requirements"]}
    assert all(defect_id in acceptance for defect_id in repaired_ids)


def test_forward_pass_cannot_bypass_candidate_runtime_readiness_chain():
    authority = _json(".workflow/onnx_v1_authority.json")
    machine = authority["state_machine"]
    cycle_edges = machine["transitions"]
    assert "CHALLENGER_READY" not in machine["states"]
    assert "RUNTIME_BLOCKED" not in machine["states"]
    assert "CANDIDATE_RUNTIME_PIPELINE" in machine["states"]
    assert "RUNTIME_NO_SURVIVOR" in machine["cycle_terminal_states"]
    assert set(machine["states"]) <= _reachable_states(machine["initial"], cycle_edges)
    assert all(edge.get("guard") for edge in cycle_edges)
    assert not _edge_exists(cycle_edges, "FORWARD_RUNNING", "CHALLENGER_READY")
    assert not _edge_exists(cycle_edges, "FORWARD_RUNNING", "PROMOTION_PENDING_OWNER")
    assert _edge_exists(cycle_edges, "FORWARD_RUNNING", "CANDIDATE_RUNTIME_PIPELINE")

    candidate = machine["candidate_runtime_pipeline"]
    assert candidate["scope"] == "PER_CANDIDATE_ID"
    assert set(candidate["states"]) <= _reachable_states(candidate["initial"], candidate["transitions"])
    assert all(edge.get("guard") for edge in candidate["transitions"])
    states = candidate["states"]
    path = [
        "SCIENTIFICALLY_QUALIFIED",
        "FINAL_PERMITTED_FIT",
        "ONNX_EXPORT",
        "ONNX_PARITY",
        "RUNTIME_MANIFEST_VALIDATION",
        "CHALLENGER_REGISTRATION",
        "CHALLENGER_READY",
    ]
    assert all(state in states for state in path)
    edges = candidate["transitions"]
    assert _edge_exists(edges, "AWAITING_FORWARD_PASS", "SCIENTIFICALLY_QUALIFIED")
    assert all(_edge_exists(edges, left, right) for left, right in zip(path, path[1:]))
    ready_edges = [edge for edge in edges if edge.get("to") == "CHALLENGER_READY"]
    assert [edge["from"] for edge in ready_edges] == ["CHALLENGER_REGISTRATION"]

    runtime_states = path[1:-1]
    assert all(_edge_exists(edges, state, "RUNTIME_BLOCKED") for state in runtime_states)
    recovery = next(edge for edge in edges if edge.get("from") == "RUNTIME_BLOCKED" and "to_one_of" in edge)
    assert "same candidate" in recovery["guard"].lower()
    assert "without rerunning" in recovery["guard"].lower()
    assert "discovery" in recovery["guard"].lower()
    assert _edge_exists(edges, "RUNTIME_BLOCKED", "RUNTIME_BLOCKED_UNRECOVERABLE")
    assert not _edge_exists(edges, "RUNTIME_BLOCKED", "CHALLENGER_READY")
    assert "Forward outcomes" in candidate["final_permitted_fit"]["forbidden_training_inputs"]
    assert "frozen before Forward" in candidate["final_permitted_fit"]["boundary"]

    promotion = next(edge for edge in cycle_edges if edge.get("from") == "CANDIDATE_RUNTIME_PIPELINE" and edge.get("to") == "PROMOTION_PENDING_OWNER")
    assert "at least one" in promotion["guard"].lower()
    assert "blocked candidate" in promotion["guard"].lower()
    owner_promotion = next(edge for edge in cycle_edges if edge.get("from") == "PROMOTION_PENDING_OWNER" and edge.get("to") == "CHAMPION_ACTIVE")
    assert "Owner" in owner_promotion["guard"]
    assert "exact challenger_ready candidate" in owner_promotion["guard"].lower()
    assert "CANDIDATE_ONLY" in authority["stage_authority"]["challenger"]["runtime_failure_scope"]

    flow = authority["scientific_flow"]
    required_flow = [
        "FORWARD_VALIDATION",
        "SCIENTIFICALLY_QUALIFIED",
        "FINAL_PERMITTED_FIT",
        "ONNX_EXPORT",
        "ONNX_PARITY",
        "RUNTIME_MANIFEST_VALIDATION",
        "CHALLENGER_REGISTRATION",
        "CHALLENGER_READY",
    ]
    positions = [flow.index(state) for state in required_flow]
    assert positions == sorted(positions)


def test_model_lab_expectancy_values_are_proposals_not_executable_kpi_gates():
    authority = _json(".workflow/onnx_v1_authority.json")
    kpi = authority["kpi_authority"]
    proposed = kpi["reference_proposals"]["model_lab_v3"]

    assert proposed["source"]
    assert proposed["authority_status"] == "SCIENTIFIC_REFERENCE_ONLY_NOT_MAX_OWNER_AUTHORITY"
    assert proposed["max_owner_authorized"] is False
    assert proposed["executable_hard_gates"] is False
    assert proposed["values"] == {
        "full_wfa_aggregate_expectancy_min_r": 0.0,
        "cpcv_worst_aggregate_expectancy_min_r": 0.0,
    }
    assert kpi["executable_contract_status"] == "NOT_AUTHORIZED_NOT_FROZEN"
    assert kpi["freeze_before_cycle_start"] is True
    assert kpi["complete_versioned_contract_required_before"] == "ONNX-03"
    assert set(kpi["required_metric_families"]) == {
        "expectancy_mean_r",
        "profit_factor",
        "recovery_factor",
        "drawdown",
        "sample_adequacy_and_trade_count",
        "temporal_stability",
        "out_of_sample_performance",
        "monte_carlo_tail_risk",
    }

    stages = authority["stage_authority"]
    assert "adapted_model_lab_hard_floor" not in stages["full_wfa"]
    assert "adapted_model_lab_hard_floor" not in stages["cpcv"]
    for stage in ("full_wfa", "cpcv"):
        reference = stages[stage]["reference_proposal_only"]
        assert reference["max_owner_authorized"] is False
        assert reference["executable_hard_gate"] is False

    human = (ROOT / "docs/onnx/ONNX_00_SCIENTIFIC_AUTHORITY.md").read_text(encoding="utf-8")
    assert "reference/proposed thresholds only" in human.lower()
    assert "neither value is an approved executable hard gate" in human.lower()
    assert "complete versioned KPI contract" in human


def test_frozen_onnx_v1_scientific_contract_is_preserved():
    authority = _json(".workflow/onnx_v1_authority.json")
    universe = authority["model_universe"]
    assert universe["active_family_count"] == 14
    assert set(universe["active_standalone_models"]) == {
        "lightgbm", "xgboost", "gru", "tcn", "transformer", "patchtst"
    }
    assert set(universe["active_hybrid_models"]) == {
        "hybrid::gru::lightgbm", "hybrid::gru::xgboost",
        "hybrid::tcn::lightgbm", "hybrid::tcn::xgboost",
        "hybrid::transformer::lightgbm", "hybrid::transformer::xgboost",
        "hybrid::patchtst::lightgbm", "hybrid::patchtst::xgboost",
    }
    assert set(universe["excluded_from_v1"]) == {
        "random_forest", "lstm", "itransformer", "tft", "transformer_moe",
        "all_other_hybrid_compositions",
    }

    temporal = authority["temporal_training_contract"]
    assert temporal["optimizer"] == "AdamW"
    assert temporal["validation"] == "PURGED_CHRONOLOGICAL_INTERNAL_VALIDATION"
    assert temporal["random_train_validation_split"] == "FORBIDDEN"
    hybrid = authority["hybrid_contract"]
    assert hybrid["stacking_authority"] == "TEMPORAL_TO_TREE_PURGED_OOF_STACKING"
    assert hybrid["in_sample_temporal_predictions_for_policy_training"] == "FORBIDDEN"
    assert len(hybrid["oof_requirements"]) >= 6
    assert any("purge" in requirement.lower() for requirement in hybrid["oof_requirements"])
    assert any("inner-validation" in requirement.lower() for requirement in hybrid["oof_requirements"])

    candidate_identity = authority["candidate_identity"]
    assert "training_seed" in candidate_identity["candidate_spec_fields"]
    assert candidate_identity["candidate_spec_hash"]
    assert candidate_identity["threshold_boundary"].startswith("Thresholds are not included")
    assert candidate_identity["duplicate_exposure"].startswith("Exact experiment fingerprints are deduplicated")

    windows = authority["windows_contract"]
    assert windows["exactly_three_windows"] == ["DISCOVERY", "TOURNAMENT", "FORWARD"]
    assert "DiscoveryTo < TournamentFrom" in windows["ordering"]
    assert "TournamentTo < ForwardFrom" in windows["ordering"]
    stages = authority["stage_authority"]
    assert stages["cheap_screen"]["qualification_authority"] is False
    assert stages["full_wfa"]["folds"] == 3
    assert stages["qualified_pool"]["maximum"] == 12
    assert (stages["cpcv"]["groups"], stages["cpcv"]["test_groups"], stages["cpcv"]["combination_count"]) == (6, 2, 15)
    assert stages["cpcv"]["temporal_or_hybrid_confirmation_seed_order"] == [42, 11, 77]
    assert stages["tournament"]["window"] == "TOURNAMENT only"
    assert stages["tournament"]["candidate_mutation_search_threshold_tuning_or_failure_feedback"] is False
    assert stages["monte_carlo"]["result"] == ["PASS", "FAIL"]
    assert stages["monte_carlo"]["candidates"] == "Every Tournament survivor"
    assert stages["monte_carlo"]["training_or_parameter_changes"] is False
    assert stages["forward"]["window"] == "Untouched FORWARD only"
    assert stages["forward"]["tuning_or_parameter_changes"] is False
    assert stages["champion"]["promotion"] == "Explicit Owner action only; no automatic promotion."
    assert stages["champion"]["rollback"].startswith("Only from an archived artifact")
    assert authority["evidence_and_checkpoint_contract"]["canonical_authority"] == "LAST_VALID_COMMITTED_CHECKPOINT"
