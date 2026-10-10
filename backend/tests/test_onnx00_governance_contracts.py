import json
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_ESTIMATOR_PATH = ROOT / "backend" / "max_backend" / "onnx_parameter_estimation.py"
_ESTIMATOR_SPEC = importlib.util.spec_from_file_location("onnx_parameter_estimation_candidate", _ESTIMATOR_PATH)
assert _ESTIMATOR_SPEC is not None and _ESTIMATOR_SPEC.loader is not None
_ESTIMATOR = importlib.util.module_from_spec(_ESTIMATOR_SPEC)
_ESTIMATOR_SPEC.loader.exec_module(_ESTIMATOR)

count_module_parameter_ledger = _ESTIMATOR.count_module_parameter_ledger
dense_transformer_block_parameter_estimate = _ESTIMATOR.dense_transformer_block_parameter_estimate
estimate_neural_candidate = _ESTIMATOR.estimate_neural_candidate
estimate_neural_memory_bytes = _ESTIMATOR.estimate_neural_memory_bytes
estimate_tree_complexity_bound = _ESTIMATOR.estimate_tree_complexity_bound
moe_transformer_block_parameter_estimate = _ESTIMATOR.moe_transformer_block_parameter_estimate
REQUIRED_MODULE_ROLES_BY_FAMILY = _ESTIMATOR.REQUIRED_MODULE_ROLES_BY_FAMILY
recurrent_parameter_count = _ESTIMATOR.recurrent_parameter_count
validate_recurrent_candidate_spec = _ESTIMATOR.validate_recurrent_candidate_spec
validate_attention_heads = _ESTIMATOR.validate_attention_heads
validate_patch_configuration = _ESTIMATOR.validate_patch_configuration
validate_search_proposal = _ESTIMATOR.validate_search_proposal
validate_tft_covariates = _ESTIMATOR.validate_tft_covariates

def _json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _edge_exists(transitions: list[dict], source: str, target: str) -> bool:
    return any(
        source in (edge.get("from") if isinstance(edge.get("from"), list) else [edge.get("from")])
        and target in (edge.get("to") if isinstance(edge.get("to"), list) else [edge.get("to")])
        for edge in transitions
    )


def _recurrent_candidate_spec(
    family: str = "gru",
    *,
    input_features: int = 32,
    hidden_size: int = 8,
    num_layers: int = 1,
    output_dimensions: int = 3,
) -> dict:
    return {
        "architecture_family": family,
        "architecture_version": "planning-v1",
        "input_feature_count": input_features,
        "output_dimension": output_dimensions,
        "sequence_length": 128,
        "exact_architecture_parameters": {
            "hidden_size": hidden_size,
            "num_layers": num_layers,
            "recurrent_variant": "simple_unidirectional_bias_enabled_unprojected",
            "bias": True,
            "bidirectional": False,
            "projection_size": 0,
            "output_head": "linear",
        },
    }


def _recurrent_tensor_ledger(
    family: str = "gru",
    *,
    input_features: int = 32,
    hidden_size: int = 8,
    num_layers: int = 1,
    output_dimensions: int = 3,
) -> list[dict]:
    gates = 3 if family == "gru" else 4
    tensors = []
    for layer in range(num_layers):
        layer_input = input_features if layer == 0 else hidden_size
        prefix = f"recurrent.layer_{layer}"
        for suffix, shape in (
            ("weight_ih", [gates * hidden_size, layer_input]),
            ("weight_hh", [gates * hidden_size, hidden_size]),
            ("bias_ih", [gates * hidden_size]),
            ("bias_hh", [gates * hidden_size]),
        ):
            tensors.append({
                "parameter_id": f"{prefix}.{suffix}",
                "role": "recurrent",
                "shape": shape,
                "trainable": True,
            })
    tensors.extend([
        {"parameter_id": "output_head.weight", "role": "output_head", "shape": [output_dimensions, hidden_size], "trainable": True},
        {"parameter_id": "output_head.bias", "role": "output_head", "shape": [output_dimensions], "trainable": True},
    ])
    return tensors


def _reachable_states(initial: str, transitions: list[dict]) -> set[str]:
    edges: dict[str, set[str]] = {}
    for transition in transitions:
        sources = transition.get("from")
        sources = sources if isinstance(sources, list) else [sources]
        targets = transition.get("to")
        targets = targets if isinstance(targets, list) else ([targets] if targets is not None else [])
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


def _promotion_is_eligible(policy: dict, selected_state: str, unrelated_candidates: list[dict]) -> bool:
    if selected_state != policy["requires_selected_candidate_state"] or not policy["owner_selection_required"]:
        return False
    if not policy["unrelated_candidate_terminal_disposition_required"]:
        return True
    terminal = set(policy["terminal_dispositions"])
    return all(candidate.get("disposition") in terminal for candidate in unrelated_candidates)


def _cycle_closure_is_eligible(policy: dict, forward_pass_candidates: list[dict]) -> bool:
    terminal = set(policy["terminal_disposition_set"])
    if not policy["requires_every_forward_pass_candidate_disposition_committed"]:
        return False
    if any(candidate.get("cursor_active") or candidate.get("owned_process_active") for candidate in forward_pass_candidates):
        return False
    return all(
        candidate.get("disposition") in terminal and candidate.get("disposition_committed") is True
        for candidate in forward_pass_candidates
    )


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
    assert machine["initial"] in machine["states"]
    assert set(machine["cycle_terminal_states"]) <= set(machine["states"])
    for edge in cycle_edges:
        sources = edge.get("from") if isinstance(edge.get("from"), list) else [edge.get("from")]
        targets = edge.get("to")
        targets = targets if isinstance(targets, list) else ([targets] if targets is not None else [])
        targets += edge.get("to_one_of", [])
        assert set(sources + targets) <= set(machine["states"])
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


def test_candidate_promotion_is_independent_from_safe_cycle_closure():
    authority = _json(".workflow/onnx_v1_authority.json")
    machine = authority["state_machine"]
    lifecycle = machine["candidate_disposition_contract"]
    defects = {item["id"]: item for item in _json(".workflow/known_defects.json")["defects"]}
    acceptance = {item["id"]: item for item in _json(".workflow/acceptance.json")["requirements"]}

    assert "ONNX00-CANDIDATE-CYCLE-CLOSURE" in defects
    assert "ONNX00-CANDIDATE-CYCLE-CLOSURE" in acceptance

    assert lifecycle["scope"] == "PER_FORWARD_PASS_CANDIDATE_ID"
    assert lifecycle["immutable_identity_fields"] == [
        "cycle_id",
        "candidate_id",
        "candidate_spec_sha256",
        "forward_stage_seal_sha256",
    ]
    assert lifecycle["promotion_eligibility"]["requires_selected_candidate_state"] == "CHALLENGER_READY"
    assert lifecycle["promotion_eligibility"]["unrelated_candidate_terminal_disposition_required"] is False
    assert lifecycle["cycle_closure"]["requires_every_forward_pass_candidate_disposition_committed"] is True
    assert lifecycle["cycle_closure"]["requires_no_active_candidate_cursor_or_owned_process"] is True
    assert lifecycle["cycle_closure"]["terminal_disposition_set"] == [
        "PROMOTED",
        "OWNER_DECLINED",
        "OWNER_CANCELLED",
        "RUNTIME_BLOCKED_UNRECOVERABLE",
    ]
    assert lifecycle["cancellation"]["requires_verified_no_active_owned_process"] is True
    assert lifecycle["disposition_recovery"]["idempotent"] is True
    assert lifecycle["disposition_recovery"]["replays_candidate_execution"] is False
    assert lifecycle["disposition_recovery"]["idempotency_key_fields"] == [
        "cycle_id",
        "disposition_transaction_id",
    ]
    assert "cannot change another candidate's scientific PASS" in lifecycle["candidate_failure_isolation"]
    candidate = machine["candidate_runtime_pipeline"]
    unrecoverable = next(
        edge for edge in candidate["transitions"]
        if edge.get("from") == "RUNTIME_BLOCKED" and edge.get("to") == "RUNTIME_BLOCKED_UNRECOVERABLE"
    )
    assert "atomically commit" in unrecoverable["guard"].lower()

    assert "CHAMPION_ACTIVE" not in machine["cycle_terminal_states"]
    for source in ("PROMOTION_PENDING_OWNER", "CHAMPION_ACTIVE"):
        edge = next(
            item for item in machine["transitions"]
            if item.get("from") == source and item.get("to") == "NO_CYCLE"
        )
        guard = edge["guard"].lower()
        assert "every forward-pass candidate" in guard
        assert "committed disposition" in guard
        assert "no active candidate cursor" in guard

    promotion_policy = lifecycle["promotion_eligibility"]
    closure_policy = lifecycle["cycle_closure"]
    ready_a = {"state": "CHALLENGER_READY"}
    processing_b = {
        "state": "ONNX_PARITY",
        "cursor_active": True,
        "owned_process_active": True,
        "disposition": None,
        "disposition_committed": False,
    }
    assert _promotion_is_eligible(promotion_policy, ready_a["state"], [processing_b])
    assert not _cycle_closure_is_eligible(closure_policy, [
        {"disposition": "PROMOTED", "disposition_committed": True},
        processing_b,
    ])

    blocked_b = {
        "state": "RUNTIME_BLOCKED",
        "cursor_active": True,
        "owned_process_active": False,
        "disposition": None,
        "disposition_committed": False,
    }
    assert _promotion_is_eligible(promotion_policy, ready_a["state"], [blocked_b])
    assert not _cycle_closure_is_eligible(closure_policy, [
        {"disposition": "PROMOTED", "disposition_committed": True},
        blocked_b,
    ])
    all_terminal = [
        {"disposition": "PROMOTED", "disposition_committed": True},
        {"disposition": "RUNTIME_BLOCKED_UNRECOVERABLE", "disposition_committed": True},
    ]
    assert _cycle_closure_is_eligible(closure_policy, all_terminal)
    unsafe_cancel = [
        {"disposition": "PROMOTED", "disposition_committed": True},
        {
            "disposition": "OWNER_CANCELLED",
            "disposition_committed": True,
            "cursor_active": False,
            "owned_process_active": True,
        },
    ]
    assert not _cycle_closure_is_eligible(closure_policy, unsafe_cancel)

    human = (ROOT / "docs/onnx/ONNX_00_SCIENTIFIC_AUTHORITY.md").read_text(encoding="utf-8").lower()
    assert "promotion eligibility is independent of cycle-closure eligibility" in human
    assert "must not silently cancel, invalidate, or discard" in human
    assert "safe cycle closure" in human


def test_promotion_recovery_preserves_transaction_identity_and_is_idempotent():
    authority = _json(".workflow/onnx_v1_authority.json")
    machine = authority["state_machine"]
    recovery = machine["promotion_transaction_recovery"]
    defects = {item["id"]: item for item in _json(".workflow/known_defects.json")["defects"]}
    acceptance = {item["id"]: item for item in _json(".workflow/acceptance.json")["requirements"]}

    assert "ONNX00-PROMOTION-RECOVERY-GAP" in defects
    assert "ONNX00-PROMOTION-RECOVERY-GAP" in acceptance

    assert recovery["required_context_fields"] == [
        "original_state",
        "cycle_id",
        "promotion_transaction_id",
        "candidate_id",
        "owner_authorization_id",
        "prior_champion_identity",
    ]
    assert recovery["idempotency_key_fields"] == [
        "cycle_id",
        "promotion_transaction_id",
    ]
    assert recovery["pre_commit_recovery_target"] == "PROMOTION_PENDING_OWNER"
    assert recovery["post_commit_recovery_target"] == "CHAMPION_ACTIVE"
    assert recovery["promotion_origin_may_resume_scientific_stage"] is False
    assert recovery["promotion_origin_may_close_on_uncertain_evidence"] is False
    before = recovery["before_atomic_commit"]
    assert before["assume_success"] is False
    assert before["automatic_new_owner_decision"] is False
    assert before["resume_requires_same_transaction_identity"] is True
    after = recovery["after_atomic_commit"]
    assert after["verify_committed_champion_identity"] is True
    assert after["verify_artifact_publication_and_prior_champion_archive"] is True
    assert after["repeat_promotion_commit"] == "FORBIDDEN"
    assert after["repeated_recovery_is_idempotent"] is True
    assert recovery["uncertain_or_conflicting_evidence"]["fail_closed"] is True
    assert recovery["uncertain_or_conflicting_evidence"]["automatic_rollback"] is False
    assert recovery["rollback"]["requires_separate_explicit_owner_authorization"] is True
    assert recovery["recovery_must_not_resume_scientific_training"] is True

    edges = machine["transitions"]
    pending_recovery = next(
        edge for edge in edges
        if edge.get("from") == "RECOVERY_REQUIRED" and edge.get("to") == "PROMOTION_PENDING_OWNER"
    )
    champion_recovery = next(
        edge for edge in edges
        if edge.get("from") == "RECOVERY_REQUIRED" and edge.get("to") == "CHAMPION_ACTIVE"
    )
    assert "original_state" in pending_recovery["guard"]
    assert "not committed" in pending_recovery["guard"].lower()
    assert "original_state" in champion_recovery["guard"]
    assert "already committed" in champion_recovery["guard"].lower()
    assert "archive" in champion_recovery["guard"].lower()
    assert "reconcile" in champion_recovery["guard"].lower()

    paused_recovery = next(
        edge for edge in edges
        if edge.get("from") == "RECOVERY_REQUIRED" and edge.get("to") == "PAUSED_AT_SAFE_BOUNDARY"
    )
    assert "promotion" in paused_recovery["guard"].lower()
    no_cycle_recovery = next(
        edge for edge in edges
        if edge.get("from") == "RECOVERY_REQUIRED" and edge.get("to") == "NO_CYCLE"
    )
    assert "promotion-origin" in no_cycle_recovery["guard"].lower()


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


def test_owner_proposed_parameter_envelopes_and_capacity_references_are_exact():
    authority = _json(".workflow/onnx_v1_authority.json")

    def bounds(envelope, field):
        value = envelope[field]
        assert isinstance(value, dict)
        return value.get("min"), value.get("max")

    active = authority["active_search_envelopes"]
    expected_active = {
        "lightgbm": {
            "n_estimators": (150, 1500), "num_leaves": (8, 128), "max_depth": (3, 12),
            "min_child_samples": (10, 500), "learning_rate": (0.005, 0.12),
            "subsample": (0.6, 1.0), "colsample_bytree": (0.6, 1.0),
            "reg_alpha": (0.0, 20.0), "reg_lambda": (0.01, 100.0),
        },
        "xgboost": {
            "n_estimators": (150, 1500), "max_depth": (2, 10), "min_child_weight": (0.5, 100.0),
            "learning_rate": (0.005, 0.12), "subsample": (0.6, 1.0),
            "colsample_bytree": (0.6, 1.0), "reg_alpha": (0.0, 20.0), "reg_lambda": (0.01, 100.0),
        },
        "gru": {
            "sequence_length": (24, 192), "hidden_size": (32, 256), "num_layers": (1, 3),
            "dropout": (0.0, 0.35), "learning_rate": (0.0001, 0.003),
            "batch_size": (32, 512), "max_epochs": (20, 120), "weight_decay": (1e-6, 0.02),
        },
        "tcn": {
            "sequence_length": (32, 256), "tcn_channels": (32, 192), "tcn_blocks": (2, 6),
            "kernel_size": (2, 5), "dropout": (0.0, 0.35), "learning_rate": (0.0001, 0.003),
            "batch_size": (32, 512), "max_epochs": (20, 120), "weight_decay": (1e-6, 0.02),
        },
        "patchtst": {
            "sequence_length": (64, 256), "d_model": (32, 192), "num_layers": (1, 4),
            "attention_heads": (1, 8), "ffn_mult": (2, 4), "patch_len": (8, 48),
            "patch_stride": (4, 24), "dropout": (0.05, 0.35), "learning_rate": (5e-5, 0.002),
            "batch_size": (16, 256), "max_epochs": (20, 100), "weight_decay": (1e-6, 0.02),
        },
    }
    assert set(active) == set(expected_active)
    for family, parameters in expected_active.items():
        for field, expected in parameters.items():
            assert bounds(active[family], field) == expected, (family, field)

    proposed = authority["proposed_active_search_envelopes"]
    expected_new = {
        "itransformer": {
            "sequence_length": (32, 512), "d_model": (32, 384), "num_layers": (1, 6),
            "attention_heads": (1, 12), "ffn_mult": (2, 8), "dropout": (0.05, 0.35),
            "learning_rate": (5e-5, 2e-3), "batch_size": (16, 256), "max_epochs": (20, 100),
            "weight_decay": (1e-6, 0.02), "patience": 8,
        },
        "tft": {
            "sequence_length": (32, 384), "hidden_size": (32, 256), "num_layers": (1, 3),
            "attention_heads": (1, 8), "grn_hidden_size": (32, 512), "dropout": (0.05, 0.35),
            "learning_rate": (5e-5, 2e-3), "batch_size": (16, 128), "max_epochs": (20, 100),
            "weight_decay": (1e-6, 0.02), "patience": 8,
        },
        "transformer_moe": {
            "sequence_length": (32, 384), "d_model": (64, 384), "num_layers": (1, 6),
            "attention_heads": (1, 12), "ffn_mult": (2, 4), "num_experts": (2, 16),
            "top_k": (1, 4), "expert_ffn_mult": (2, 4), "capacity_factor": (1.0, 2.0),
            "router_aux_loss_weight": (1e-4, 0.1), "dropout": (0.05, 0.35),
            "learning_rate": (1e-5, 1e-3), "batch_size": (8, 128), "max_epochs": (20, 100),
            "weight_decay": (1e-6, 0.02), "patience": 10,
        },
        "lstm": {
            "sequence_length": (24, 192), "hidden_size": (32, 256), "num_layers": (1, 3),
            "dropout": (0.0, 0.35), "learning_rate": (1e-4, 3e-3), "batch_size": (32, 512),
            "max_epochs": (20, 120), "weight_decay": (1e-6, 0.02), "patience": 6,
        },
    }
    for family, parameters in expected_new.items():
        contract = proposed[family]
        for field, expected in parameters.items():
            if field == "patience":
                actual = contract[field]
            else:
                actual = bounds(contract, field)
            assert actual == expected, (family, field)
    assert proposed["status"] == "PROPOSED_ACTIVE_SEARCH_ENVELOPE_NOT_LEGAL_MAXIMA_OR_EXECUTABLE_AUTHORITY"
    assert proposed["acceptance_required_before_execution"] is True
    assert proposed["itransformer"]["embedding_mode"] == "variates_as_tokens"
    assert proposed["tft"]["ffn_mult"].startswith("ARCHITECTURE_SPECIFIC")
    assert proposed["transformer_moe"]["top_k"]["min"] >= 1
    assert proposed["lstm"]["bidirectional"] is False
    assert proposed["lstm"]["projection_size"] == 0

    references = authority["parameter_capacity_contract"]["reference_estimates"]["approximate_parameters"]
    assert references == {
        "gru": "~0.26M",
        "lstm": "~0.35M",
        "tcn": "~0.36M",
        "itransformer": "~0.61M",
        "patchtst": "~0.60M",
        "tft": "~3-6M implementation-dependent; not an exact regression expectation",
        "transformer_moe_total": "~3.37M",
        "transformer_moe_active_per_token": "~0.99M",
    }
    assert "not one simultaneously trained network" in authority["parameter_capacity_contract"]["portfolio_illustration"]


def test_frozen_onnx_v1_scientific_contract_is_preserved():
    authority = _json(".workflow/onnx_v1_authority.json")
    universe = authority["model_universe"]
    standalone = {
        "lightgbm", "xgboost", "gru", "tcn", "itransformer", "patchtst", "tft", "transformer_moe", "lstm"
    }
    temporal_families = {"gru", "tcn", "itransformer", "patchtst", "tft", "transformer_moe", "lstm"}
    hybrids = {f"hybrid::{temporal}::{tree}" for temporal in temporal_families for tree in ("lightgbm", "xgboost")}
    assert universe["active_standalone_count"] == 9
    assert universe["active_hybrid_count"] == 14
    assert universe["active_family_count"] == 23
    assert len(universe["active_standalone_models"]) == len(set(universe["active_standalone_models"])) == 9
    assert len(universe["active_hybrid_models"]) == len(set(universe["active_hybrid_models"])) == 14
    assert set(universe["active_standalone_models"]) == standalone
    assert set(universe["active_hybrid_models"]) == hybrids
    assert set(universe["excluded_from_v1"]) == {
        "transformer", "random_forest", "all_other_unapproved_model_families", "all_other_unapproved_hybrid_compositions"
    }
    assert "transformer" not in universe["active_standalone_models"]
    assert not any("hybrid::transformer::" in family for family in universe["active_hybrid_models"])
    assert universe["conventional_transformer_exclusion"]
    assert len(standalone | hybrids) == 23

    decisions = {item["id"]: item for item in _json(".workflow/decisions.json")["decisions"]}
    current_state = _json(".workflow/state.json")
    assert any("exactly nine standalone plus fourteen" in item for item in current_state["proven"])
    assert not any("total 14" in item for item in current_state["proven"])
    assert decisions["D-040"]["status"] == "SUPERSEDED"
    assert decisions["D-040"]["superseded_by"] == "D-043"
    assert "six standalone families" in decisions["D-040"]["decision"]
    assert "gru, tcn, transformer, patchtst" in decisions["D-040"]["decision"]
    assert "lstm, itransformer, tft, transformer_moe" in decisions["D-040"]["decision"]
    assert decisions["D-043"]["status"] == "CURRENT"
    assert decisions["D-044"]["status"] == "CURRENT"
    optimizer_decision = decisions["D-042"]
    assert optimizer_decision["status"] == "CURRENT"
    assert "203f6ab3dc618e3edd247841aa25e1fa0951a19a" in optimizer_decision["decision"]

    families = set(universe["active_standalone_models"])
    for family in sorted(families):
        identity = authority["architecture_contracts"][family]["identity"]
        assert identity == family
    assert authority["architecture_contracts"]["itransformer"]["architecture"].lower().find("variates") >= 0
    assert authority["architecture_contracts"]["tft"]["architecture"].lower().find("variable-selection") >= 0
    assert authority["architecture_contracts"]["transformer_moe"]["architecture"].lower().find("mixture-of-experts") >= 0
    assert "dense Transformer substitution is forbidden" in authority["architecture_contracts"]["transformer_moe"]["architecture"]
    assert authority["architecture_contracts"]["lstm"]["architecture"].startswith("Genuine unidirectional causal LSTM")
    assert authority["hybrid_contract"]["authorized_compositions"] == universe["active_hybrid_models"]
    registry_identity = authority["model_registry_identity_contract"]
    assert registry_identity["authorized_standalone_ids"] == universe["active_standalone_models"]
    assert registry_identity["authorized_hybrid_ids"] == universe["active_hybrid_models"]
    assert registry_identity["aliases"] == []
    assert registry_identity["fallback_families"] == []
    assert registry_identity["cross_family_or_cross_composition_artifact_reuse"] == "FORBIDDEN"

    # Family ID is part of canonical CandidateSpec material, so aliases/compositions cannot hash alike.
    family_hashes = {
        hashlib.sha256(
            json.dumps(
                {"family_id": family_id, "architecture_contract_version": "v1"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).digest()
        for family_id in standalone | hybrids
    }
    assert len(family_hashes) == 23

    parameter_authority = authority["parameter_authority"]
    assert "NONE_AUTHORIZED" in authority["parameter_capacity_contract"]["permanent_trainable_parameter_ceiling"]
    assert parameter_authority["unapproved_or_missing_family_parameters"].startswith("FAIL_CLOSED")
    proposed = authority["proposed_active_search_envelopes"]
    assert proposed["status"] == "PROPOSED_ACTIVE_SEARCH_ENVELOPE_NOT_LEGAL_MAXIMA_OR_EXECUTABLE_AUTHORITY"
    assert proposed["acceptance_required_before_execution"] is True
    for family in ("itransformer", "tft", "transformer_moe", "lstm"):
        assert authority["open_search_envelope_gaps"][family]["status"].startswith("PROPOSED_ACTIVE_SEARCH_ENVELOPE")
        assert family in proposed
        assert authority["open_search_envelope_gaps"][family]["unresolved"]
        assert "blocks" in authority["open_search_envelope_gaps"][family]
    assert proposed["itransformer"]["embedding_mode"] == "variates_as_tokens"
    assert proposed["tft"]["unsupported_inputs"] == "REJECT; NEVER_SYNTHESIZE"
    assert proposed["transformer_moe"]["num_experts"] == {"type": "integer", "min": 2, "max": 16}
    assert proposed["transformer_moe"]["top_k"] == {"type": "integer", "min": 1, "max": 4}
    assert proposed["lstm"]["bidirectional"] is False
    assert proposed["lstm"]["projection_size"] == 0
    assert set(authority["active_search_envelopes"]) == {"lightgbm", "xgboost", "gru", "tcn", "patchtst"}
    current = authority["active_search_envelopes"]
    assert (current["lightgbm"]["n_estimators"]["min"], current["lightgbm"]["n_estimators"]["max"]) == (150, 1500)
    assert (current["lightgbm"]["num_leaves"]["min"], current["lightgbm"]["num_leaves"]["max"]) == (8, 128)
    assert (current["xgboost"]["max_depth"]["min"], current["xgboost"]["max_depth"]["max"]) == (2, 10)
    assert (current["gru"]["sequence_length"]["min"], current["gru"]["sequence_length"]["max"]) == (24, 192)
    assert (current["tcn"]["tcn_blocks"]["min"], current["tcn"]["tcn_blocks"]["max"]) == (2, 6)
    assert (current["patchtst"]["patch_len"]["min"], current["patchtst"]["patch_len"]["max"]) == (8, 48)

    estimator = authority["parameter_capacity_contract"]
    assert estimator["model_parameter_estimate_required_per_candidate_spec"] is True
    assert "NOT_APPLICABLE" in estimator["not_applicable"]
    assert "role labels alone cannot validate architecture topology" in estimator["exact_module_accounting"].lower()
    assert estimator["confidence_values"] == [
        "RAW_DECLARED_LEDGER_COUNT", "CANDIDATE_SPEC_STATICALLY_VALIDATED",
        "STRUCTURAL_APPROXIMATION", "MEASURED_INSTANTIATED_MODEL", "OPEN_AUTHORITY_GAP",
    ]
    assert estimator["candidate_spec_validation_contract"]["recurrent_static_validation"]["supported_variant"] == "simple_unidirectional_bias_enabled_unprojected"
    assert "MEASURED_INSTANTIATED_MODEL is an allowed future evidence label but MUST NOT be emitted" in estimator["candidate_spec_validation_contract"]["measured_model_boundary"]
    moe_formula = estimator["reference_formulas"]["moe_encoder_block"]
    assert "expert_ffn_mult" in moe_formula and "ffn_mult" in moe_formula
    assert "NOT_APPLICABLE" in moe_formula
    moe_accounting = estimator["moe_parameter_accounting"]
    assert moe_accounting["architecture_status"].startswith("OPEN_AUTHORITY_GAP")
    assert "must not be substituted" in moe_accounting["expert_width_binding"]
    assert "shared_ffn_present" in moe_accounting["shared_ffn_binding"]
    assert "all stored weights" in moe_accounting["memory_and_runtime_boundary"]
    assert estimator["memory_estimation"]["fp32_adamw_baseline_bytes_per_trainable_parameter"] == 16
    assert "actual instantiated-model comparison" in estimator["static_estimator_validation"].lower()
    assert estimator["tree_model_accounting"]["neural_parameter_fields"] == "NOT_APPLICABLE"
    assert "actual grown tree structure separately" in estimator["tree_model_accounting"]["distinguish_actual_from_bound"]
    assert "not one simultaneously trained network" in estimator["portfolio_illustration"]
    gates = parameter_authority["hardware_aware_active_envelope_inputs"]
    assert any("GPU VRAM" in item for item in gates)
    assert any("sample adequacy" in item for item in gates)
    assert gates.index("available GPU VRAM") != gates.index("effective sample adequacy")
    assert authority["onnx_compatibility_authority"]["runtime_authorization"] == "NOT_GRANTED_BY_ONNX_00"
    assert authority["onnx_compatibility_authority"]["unsupported_operation_or_parity"].startswith("FAIL_CLOSED")

    temporal = authority["temporal_training_contract"]
    assert set(temporal["families"]) == temporal_families
    assert temporal["optimizer"] == "AdamW"
    assert temporal["validation"] == "PURGED_CHRONOLOGICAL_INTERNAL_VALIDATION"
    assert temporal["random_train_validation_split"] == "FORBIDDEN"
    assert temporal["patience"] == {"gru": 6, "tcn": 8, "itransformer": 8, "patchtst": 8, "tft": 8, "transformer_moe": 10, "lstm": 6}
    assert temporal["patience_authority"]["acceptance_required_before_execution"] is True
    assert set(universe["active_standalone_models"]) == {
        "lightgbm", "xgboost", "gru", "tcn", "itransformer", "patchtst", "tft", "transformer_moe", "lstm"
    }

    hybrid = authority["hybrid_contract"]
    assert hybrid["stacking_authority"] == "TEMPORAL_TO_TREE_PURGED_OOF_STACKING"
    assert hybrid["in_sample_temporal_predictions_for_policy_training"] == "FORBIDDEN"
    assert set(hybrid["temporal_component_allowlist"]) == temporal_families
    assert set(hybrid["policy_component_allowlist"]) == {"lightgbm", "xgboost"}
    assert any("Tournament or Forward outcomes back into training" in requirement for requirement in hybrid["oof_requirements"])
    assert any("artifact under another" in requirement for requirement in hybrid["oof_requirements"])
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


def test_every_temporal_family_has_a_complete_estimator_role_contract():
    authority = _json(".workflow/onnx_v1_authority.json")
    roles_by_family = authority["parameter_capacity_contract"]["required_module_roles_by_family"]
    assert set(roles_by_family) == set(authority["temporal_training_contract"]["families"])
    assert {family: set(roles) for family, roles in roles_by_family.items()} == REQUIRED_MODULE_ROLES_BY_FAMILY
    for family, roles in roles_by_family.items():
        modules = []
        for role in roles:
            if family == "transformer_moe" and role == "expert":
                modules.extend(
                    {"parameter_id": f"expert.{index}.weight", "role": role, "shape": [2, 3], "trainable": True, "group": "expert", "expert_index": index, "layer_index": 0}
                    for index in range(2)
                )
            else:
                group = "router" if family == "transformer_moe" and role == "router" else "shared"
                module = {"role": role, "shape": [2, 3], "trainable": True, "group": group}
                if family == "transformer_moe":
                    module["parameter_id"] = f"{role}.weight"
                modules.append(module)
        result = count_module_parameter_ledger(
            architecture_family=family,
            architecture_version="planning-v1",
            input_feature_count=32,
            output_dimension=3,
            sequence_length=128,
            exact_architecture_parameters=(
                {
                    "family": family,
                    "num_layers": 1,
                    "num_experts": 2,
                    "top_k": 1,
                    "expert_ffn_mult": 2,
                    "expert_sharing_policy": "independent_per_expert",
                    "shared_ffn_present": False,
                    "ffn_mult": "NOT_APPLICABLE",
                }
                if family == "transformer_moe"
                else {"family": family}
            ),
            modules=modules,
            num_experts=2 if family == "transformer_moe" else None,
            top_k=1 if family == "transformer_moe" else None,
        )
        assert result["architecture_family"] == family
        assert result["total_parameter_count"] > 0
        assert result["estimation_confidence"] == "RAW_DECLARED_LEDGER_COUNT"
        assert result["candidate_spec_validated_parameter_count"] == "OPEN_AUTHORITY_GAP"
        assert result["candidate_spec_static_validation_status"] == "OPEN_AUTHORITY_GAP"
        assert result["instantiated_model_verification"] == "NOT_PERFORMED_IN_ONNX_00"


def test_pure_parameter_estimation_and_capacity_guards_are_fail_closed():
    assert recurrent_parameter_count(
        "gru", input_features=32, hidden_size=128, num_layers=3, output_dimensions=3
    ) == 260_739
    assert recurrent_parameter_count(
        "lstm", input_features=32, hidden_size=128, num_layers=3, output_dimensions=3
    ) == 347_523
    assert recurrent_parameter_count(
        "gru", input_features=32, hidden_size=128, num_layers=3, output_dimensions=3
    ) != recurrent_parameter_count(
        "lstm", input_features=32, hidden_size=128, num_layers=3, output_dimensions=3
    )
    assert recurrent_parameter_count(
        "gru", input_features=32, hidden_size=128, num_layers=3, output_dimensions=3
    ) > recurrent_parameter_count(
        "gru", input_features=32, hidden_size=128, num_layers=2, output_dimensions=3
    )
    assert dense_transformer_block_parameter_estimate(d_model=128, ffn_multiplier=4, num_layers=3) == 589_824
    assert dense_transformer_block_parameter_estimate(d_model=128, ffn_multiplier=4, num_layers=3) > dense_transformer_block_parameter_estimate(
        d_model=64, ffn_multiplier=4, num_layers=3
    )

    moe = moe_transformer_block_parameter_estimate(
        d_model=128,
        ffn_mult=None,
        expert_ffn_mult=4,
        shared_ffn_present=False,
        expert_sharing_policy="independent_per_expert",
        num_layers=3,
        num_experts=8,
        top_k=2,
        router_parameters_per_layer=0,
    )
    assert moe["total_parameter_count"] > moe["active_parameter_count"]
    assert moe["expert_parameter_count"] == moe["active_expert_parameter_count"] * 4
    assert 3_300_000 <= moe["total_parameter_count"] <= 3_400_000
    assert 950_000 <= moe["active_parameter_count"] <= 1_050_000
    assert moe["candidate_spec_validation_status"] == "OPEN_AUTHORITY_GAP"
    assert moe["measured_peak_vram"] == "OPEN_AUTHORITY_GAP"
    assert moe["onnx_compatibility"] == "OPEN_AUTHORITY_GAP"
    assert moe["estimated_peak_training_bytes"] == "OPEN_AUTHORITY_GAP"
    more_experts = moe_transformer_block_parameter_estimate(
        d_model=128,
        ffn_mult=None,
        expert_ffn_mult=4,
        shared_ffn_present=False,
        expert_sharing_policy="independent_per_expert",
        num_layers=3,
        num_experts=12,
        top_k=2,
        router_parameters_per_layer=0,
    )
    assert more_experts["total_parameter_count"] > moe["total_parameter_count"]
    assert more_experts["estimated_weight_bytes"] > moe["estimated_weight_bytes"]
    assert more_experts["active_parameter_count"] == moe["active_parameter_count"]
    deeper = moe_transformer_block_parameter_estimate(
        d_model=128,
        ffn_mult=None,
        expert_ffn_mult=4,
        shared_ffn_present=False,
        expert_sharing_policy="independent_per_expert",
        num_layers=4,
        num_experts=8,
        top_k=2,
        router_parameters_per_layer=0,
    )
    assert deeper["total_parameter_count"] > moe["total_parameter_count"]
    with pytest.raises(ValueError, match="top_k"):
        moe_transformer_block_parameter_estimate(
            d_model=128,
            ffn_mult=None,
            expert_ffn_mult=4,
            shared_ffn_present=False,
            expert_sharing_policy="independent_per_expert",
            num_layers=3,
            num_experts=2,
            top_k=3,
            router_parameters_per_layer=0,
        )

    shared_ffn_narrow = moe_transformer_block_parameter_estimate(
        d_model=32, ffn_mult=2, expert_ffn_mult=3, shared_ffn_present=True, expert_sharing_policy="independent_per_expert",
        num_layers=2, num_experts=4, top_k=1, router_parameters_per_layer=17,
    )
    shared_ffn_wide = moe_transformer_block_parameter_estimate(
        d_model=32, ffn_mult=4, expert_ffn_mult=3, shared_ffn_present=True, expert_sharing_policy="independent_per_expert",
        num_layers=2, num_experts=4, top_k=1, router_parameters_per_layer=17,
    )
    assert shared_ffn_narrow["expert_parameter_count"] == shared_ffn_wide["expert_parameter_count"]
    assert shared_ffn_narrow["total_parameter_count"] < shared_ffn_wide["total_parameter_count"]
    expert_wider = moe_transformer_block_parameter_estimate(
        d_model=32, ffn_mult=2, expert_ffn_mult=4, shared_ffn_present=True, expert_sharing_policy="independent_per_expert",
        num_layers=2, num_experts=4, top_k=1, router_parameters_per_layer=17,
    )
    assert expert_wider["expert_parameter_count"] > shared_ffn_narrow["expert_parameter_count"]
    assert expert_wider["total_parameter_count"] > shared_ffn_narrow["total_parameter_count"]
    assert expert_wider["active_parameter_count"] > shared_ffn_narrow["active_parameter_count"]
    top_two = moe_transformer_block_parameter_estimate(
        d_model=32, ffn_mult=2, expert_ffn_mult=3, shared_ffn_present=True, expert_sharing_policy="independent_per_expert",
        num_layers=2, num_experts=4, top_k=2, router_parameters_per_layer=17,
    )
    assert top_two["total_parameter_count"] == shared_ffn_narrow["total_parameter_count"]
    assert top_two["active_parameter_count"] > shared_ffn_narrow["active_parameter_count"]
    assert moe_transformer_block_parameter_estimate(
        d_model=32, ffn_mult=None, expert_ffn_mult=3, shared_ffn_present=False, expert_sharing_policy="independent_per_expert",
        num_layers=2, num_experts=4, top_k=1, router_parameters_per_layer=17,
    )["shared_ffn_parameter_count"] == "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="expert_sharing_policy"):
        moe_transformer_block_parameter_estimate(
            d_model=32, ffn_mult=None, expert_ffn_mult=3, shared_ffn_present=False,
            expert_sharing_policy="shared_experts", num_layers=2, num_experts=4,
            top_k=1, router_parameters_per_layer=17,
        )

    validate_attention_heads(d_model=384, attention_heads=12)
    with pytest.raises(ValueError, match="divisible"):
        validate_attention_heads(d_model=386, attention_heads=12)
    validate_patch_configuration(sequence_length=128, patch_len=16, patch_stride=8)
    with pytest.raises(ValueError, match="patch_len"):
        validate_patch_configuration(sequence_length=8, patch_len=16, patch_stride=8)
    with pytest.raises(ValueError, match="patch_stride"):
        validate_patch_configuration(sequence_length=128, patch_len=8, patch_stride=16)

    validate_tft_covariates(required_covariates=["ohlc", "rsi"], available_covariates=["ohlc", "rsi"])
    with pytest.raises(ValueError, match="unsupported TFT covariates"):
        validate_tft_covariates(required_covariates=["invented_future"], available_covariates=["ohlc"])
    envelope = {"hidden_size": {"min": 32, "max": 256}}
    validate_search_proposal({"hidden_size": 128}, envelope)
    with pytest.raises(ValueError, match="outside active envelope"):
        validate_search_proposal({"hidden_size": 512}, envelope)
    with pytest.raises(ValueError, match="no authorized active envelope"):
        validate_search_proposal({"hidden_size": 128, "fallback_width": 64}, envelope)
    with pytest.raises(ValueError, match="missing required parameters"):
        validate_search_proposal({"hidden_size": 128}, {**envelope, "dropout": {"min": 0.0, "max": 0.35}})
    integer_envelope = {"hidden_size": {"type": "integer", "min": 32, "max": 256}}
    for invalid_value in (float("nan"), float("inf"), float("-inf"), 64.5, True):
        with pytest.raises(ValueError):
            validate_search_proposal({"hidden_size": invalid_value}, integer_envelope)
    with pytest.raises(ValueError, match="invalid active bounds"):
        validate_search_proposal({"hidden_size": 64}, {"hidden_size": {"min": float("nan"), "max": 256}})
    with pytest.raises(ValueError, match="covariates must be explicit sequences"):
        validate_tft_covariates(required_covariates="invented_future", available_covariates=["ohlc"])
    with pytest.raises(ValueError, match="non-empty strings"):
        validate_tft_covariates(required_covariates=[{}], available_covariates=["ohlc"])

    memory = estimate_neural_memory_bytes(
        total_parameter_count=100,
        trainable_parameter_count=90,
        activation_peak_bytes=None,
        temporary_peak_bytes=None,
        inference_workspace_bytes=None,
    )
    assert memory["estimated_weight_bytes"] == 400
    assert memory["estimated_gradient_bytes"] == 360
    assert memory["estimated_optimizer_state_bytes"] == 720
    assert memory["estimated_peak_training_bytes"] == "OPEN_AUTHORITY_GAP"
    assert memory["estimated_inference_bytes"] == "OPEN_AUTHORITY_GAP"
    with pytest.raises(ValueError, match="cannot exceed total"):
        estimate_neural_memory_bytes(
            total_parameter_count=8,
            trainable_parameter_count=9,
            activation_peak_bytes=None,
            temporary_peak_bytes=None,
            inference_workspace_bytes=None,
        )

    recurrent_spec = _recurrent_candidate_spec()
    neural_record = estimate_neural_candidate(
        architecture_family="gru",
        architecture_version=recurrent_spec["architecture_version"],
        input_feature_count=recurrent_spec["input_feature_count"],
        output_dimension=recurrent_spec["output_dimension"],
        sequence_length=recurrent_spec["sequence_length"],
        exact_architecture_parameters=recurrent_spec["exact_architecture_parameters"],
        modules=_recurrent_tensor_ledger(),
        activation_peak_bytes=None,
        temporary_peak_bytes=None,
        inference_workspace_bytes=None,
    )
    assert neural_record["architecture_family"] == "gru"
    assert neural_record["input_feature_count"] == 32
    assert neural_record["output_dimension"] == 3
    assert neural_record["sequence_length"] == 128
    assert neural_record["total_parameter_count"] == 1_035
    assert neural_record["raw_declared_ledger_total_parameter_count"] == 1_035
    assert neural_record["candidate_spec_validated_parameter_count"] == 1_035
    assert neural_record["estimation_confidence"] == "CANDIDATE_SPEC_STATICALLY_VALIDATED"
    assert neural_record["estimated_weight_bytes"] == 4_140
    assert neural_record["active_parameter_count"] == "NOT_APPLICABLE"
    assert neural_record["instantiated_model_verification"] == "NOT_PERFORMED_IN_ONNX_00"


def test_recurrent_candidate_spec_reconciles_formula_and_rejects_arbitrary_ledgers():
    counterexample_modules = [
        {"role": "recurrent", "shape": [8, 12], "trainable": True},
        {"role": "output_head", "shape": [3, 8], "trainable": True},
    ]
    raw = count_module_parameter_ledger(
        architecture_family="gru",
        architecture_version="planning-v1",
        input_feature_count=32,
        output_dimension=3,
        sequence_length=128,
        exact_architecture_parameters={"hidden_size": 8, "num_layers": 1},
        modules=counterexample_modules,
    )
    assert raw["raw_declared_ledger_total_parameter_count"] == 120
    assert raw["estimation_confidence"] == "RAW_DECLARED_LEDGER_COUNT"
    assert raw["candidate_spec_validated_parameter_count"] == "OPEN_AUTHORITY_GAP"
    with pytest.raises(ValueError, match="parameter_id|tensor identity"):
        validate_recurrent_candidate_spec(
            candidate_spec=_recurrent_candidate_spec(), modules=counterexample_modules
        )

    for family, expected in (("gru", 1_035), ("lstm", 1_371)):
        spec = _recurrent_candidate_spec(family)
        result = validate_recurrent_candidate_spec(
            candidate_spec=spec, modules=_recurrent_tensor_ledger(family)
        )
        assert result["trainable_parameter_count"] == expected
        assert result["trainable_parameter_count"] == recurrent_parameter_count(
            family,
            input_features=32,
            hidden_size=8,
            num_layers=1,
            output_dimensions=3,
        )
        assert result["raw_declared_ledger_total_parameter_count"] == expected
        assert result["estimation_confidence"] == "CANDIDATE_SPEC_STATICALLY_VALIDATED"
        assert result["instantiated_model_verification"] == "NOT_PERFORMED_IN_ONNX_00"
        assert result["measured_peak_vram"] == "OPEN_AUTHORITY_GAP"
        assert result["onnx_compatibility"] == "OPEN_AUTHORITY_GAP"

    base = validate_recurrent_candidate_spec(
        candidate_spec=_recurrent_candidate_spec(), modules=_recurrent_tensor_ledger()
    )["candidate_spec_validated_parameter_count"]
    larger_hidden = validate_recurrent_candidate_spec(
        candidate_spec=_recurrent_candidate_spec(hidden_size=16),
        modules=_recurrent_tensor_ledger(hidden_size=16),
    )["candidate_spec_validated_parameter_count"]
    more_layers = validate_recurrent_candidate_spec(
        candidate_spec=_recurrent_candidate_spec(num_layers=2),
        modules=_recurrent_tensor_ledger(num_layers=2),
    )["candidate_spec_validated_parameter_count"]
    assert larger_hidden != base
    assert more_layers > base

    mismatched_input = _recurrent_candidate_spec(input_features=31)
    with pytest.raises(ValueError, match="role/shape contradicts CandidateSpec"):
        validate_recurrent_candidate_spec(
            candidate_spec=mismatched_input, modules=_recurrent_tensor_ledger()
        )
    mismatched_output = _recurrent_candidate_spec(output_dimensions=4)
    with pytest.raises(ValueError, match="tensor identity mismatch|role/shape contradicts"):
        validate_recurrent_candidate_spec(
            candidate_spec=mismatched_output, modules=_recurrent_tensor_ledger()
        )
    unsupported = _recurrent_candidate_spec()
    unsupported["exact_architecture_parameters"]["recurrent_variant"] = "bidirectional_projected"
    with pytest.raises(ValueError, match="unsupported recurrent_variant"):
        validate_recurrent_candidate_spec(
            candidate_spec=unsupported, modules=_recurrent_tensor_ledger()
        )
    invalid_head = _recurrent_candidate_spec()
    invalid_head["exact_architecture_parameters"]["output_head"] = "mlp"
    with pytest.raises(ValueError, match="output_head"):
        validate_recurrent_candidate_spec(
            candidate_spec=invalid_head, modules=_recurrent_tensor_ledger()
        )
    duplicate = _recurrent_tensor_ledger()
    duplicate[-1] = dict(duplicate[-1], parameter_id="output_head.weight")
    with pytest.raises(ValueError, match="duplicate recurrent parameter_id"):
        validate_recurrent_candidate_spec(candidate_spec=_recurrent_candidate_spec(), modules=duplicate)
    assert "MEASURED_INSTANTIATED_MODEL" not in {
        result["estimation_confidence"] for result in (
            validate_recurrent_candidate_spec(candidate_spec=_recurrent_candidate_spec(), modules=_recurrent_tensor_ledger()),
            count_module_parameter_ledger(
                architecture_family="tcn", architecture_version="planning-v1",
                input_feature_count=32, output_dimension=3, sequence_length=128,
                exact_architecture_parameters={"channels": 8},
                modules=[
                    {"role": "causal_convolution", "shape": [2, 2], "trainable": True},
                    {"role": "output_head", "shape": [2, 2], "trainable": True},
                ],
            ),
        )
    }

    tree_record = estimate_tree_complexity_bound(
        architecture_family="lightgbm",
        architecture_version="proposal-v1",
        input_feature_count=32,
        output_dimension=3,
        n_estimators=20,
        max_depth=3,
        trees_per_round=3,
        num_leaves=4,
    )
    assert tree_record["trainable_parameter_count"] == "NOT_APPLICABLE"
    assert tree_record["tree_count_actual"] == "NOT_AVAILABLE_PRE_FIT"
    assert tree_record["tree_count_upper_bound"] == 60
    assert tree_record["total_leaf_count_upper_bound"] == 240
    assert tree_record["total_split_node_count_upper_bound"] == 180
    assert tree_record["estimated_peak_training_bytes"] == "OPEN_AUTHORITY_GAP"
    xgboost_record = estimate_tree_complexity_bound(
        architecture_family="xgboost",
        architecture_version="proposal-v1",
        input_feature_count=32,
        output_dimension=3,
        n_estimators=20,
        max_depth=3,
        trees_per_round=3,
    )
    assert xgboost_record["tree_count_upper_bound"] == 60
    assert xgboost_record["total_leaf_count_upper_bound"] == 480
    assert xgboost_record["total_parameter_count"] == "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="num_leaves"):
        estimate_tree_complexity_bound(
            architecture_family="xgboost",
            architecture_version="proposal-v1",
            input_feature_count=32,
            output_dimension=3,
            n_estimators=20,
            max_depth=3,
            trees_per_round=3,
            num_leaves=4,
        )


def test_parameter_module_ledger_counts_moe_total_and_active_without_fake_defaults():
    modules = [
        {"parameter_id": "attention.weight", "role": "attention", "shape": [4, 4], "trainable": True, "group": "shared"},
        {"parameter_id": "router.weight", "role": "router", "shape": [4, 2], "trainable": True, "group": "router"},
        {"parameter_id": "expert.0.weight", "role": "expert", "shape": [4, 8], "trainable": True, "group": "expert", "expert_index": 0, "layer_index": 0},
        {"parameter_id": "expert.1.weight", "role": "expert", "shape": [4, 8], "trainable": True, "group": "expert", "expert_index": 1, "layer_index": 0},
        {"parameter_id": "output_head.weight", "role": "output_head", "shape": [4, 2], "trainable": True, "group": "shared"},
        {"parameter_id": "running_stat", "role": "running_stat", "shape": [2], "trainable": False, "group": "other"},
    ]
    counts = count_module_parameter_ledger(
        architecture_family="transformer_moe",
        architecture_version="planning-v1",
        input_feature_count=4,
        output_dimension=2,
        sequence_length=8,
        exact_architecture_parameters={
            "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
            "expert_sharing_policy": "independent_per_expert",
            "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
        },
        modules=modules,
        num_experts=2,
        top_k=1,
    )
    assert counts["trainable_parameter_count"] == 96
    assert counts["non_trainable_parameter_count"] == 2
    assert counts["total_parameter_count"] == 98
    assert counts["expert_parameter_count"] == 64
    assert counts["active_expert_parameter_count"] == 32
    assert counts["active_parameter_count"] == 66
    assert counts["shared_parameter_count"] == 24
    assert counts["shared_ffn_parameter_count"] == "NOT_APPLICABLE"
    assert counts["estimation_confidence"] == "RAW_DECLARED_LEDGER_COUNT"
    assert counts["candidate_spec_static_validation_status"] == "OPEN_AUTHORITY_GAP"
    raw_memory = estimate_neural_candidate(
        architecture_family="transformer_moe",
        architecture_version="planning-v1",
        input_feature_count=4,
        output_dimension=2,
        sequence_length=8,
        exact_architecture_parameters={
            "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
            "expert_sharing_policy": "independent_per_expert",
            "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
        },
        modules=modules,
        activation_peak_bytes=None,
        temporary_peak_bytes=None,
        inference_workspace_bytes=None,
        num_experts=2,
        top_k=1,
    )
    assert raw_memory["total_parameter_count"] == 98
    assert raw_memory["active_parameter_count"] == 66
    assert raw_memory["estimated_weight_bytes"] == 98 * 4
    assert raw_memory["estimation_confidence"] == "RAW_DECLARED_LEDGER_COUNT"
    assert raw_memory["candidate_spec_validation_gap"] != "NOT_APPLICABLE"

    router_misgrouped = [dict(module) for module in modules]
    router_misgrouped[1]["group"] = "expert"
    with pytest.raises(ValueError, match="role/group mismatch|group cannot contain"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=4, output_dimension=2, sequence_length=8,
            exact_architecture_parameters={
                "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            }, modules=router_misgrouped, num_experts=2, top_k=1,
        )
    expert_misgrouped = [dict(module) for module in modules]
    expert_misgrouped[2]["group"] = "shared"
    with pytest.raises(ValueError, match="role/group mismatch"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=4, output_dimension=2, sequence_length=8,
            exact_architecture_parameters={
                "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            }, modules=expert_misgrouped, num_experts=2, top_k=1,
        )
    missing_expert = [dict(module) for module in modules if module.get("expert_index") != 1]
    with pytest.raises(ValueError, match="exact expert universe"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=4, output_dimension=2, sequence_length=8,
            exact_architecture_parameters={
                "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            }, modules=missing_expert, num_experts=2, top_k=1,
        )
    duplicate_tensor = [dict(module) for module in modules]
    duplicate_tensor[-1]["parameter_id"] = duplicate_tensor[0]["parameter_id"]
    with pytest.raises(ValueError, match="duplicate MoE parameter_id"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=4, output_dimension=2, sequence_length=8,
            exact_architecture_parameters={
                "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            }, modules=duplicate_tensor, num_experts=2, top_k=1,
        )
    unapproved_sharing = {
        "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
        "expert_sharing_policy": "shared_experts",
        "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
    }
    with pytest.raises(ValueError, match="expert_sharing_policy"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=4, output_dimension=2, sequence_length=8,
            exact_architecture_parameters=unapproved_sharing, modules=modules,
            num_experts=2, top_k=1,
        )
    uneven_experts = [dict(module) for module in modules]
    uneven_experts.append({
        "parameter_id": "expert.0.extra", "role": "expert", "shape": [2, 2],
        "trainable": True, "group": "expert", "expert_index": 0, "layer_index": 0,
    })
    uneven = count_module_parameter_ledger(
        architecture_family="transformer_moe", architecture_version="planning-v1",
        input_feature_count=4, output_dimension=2, sequence_length=8,
        exact_architecture_parameters={
            "num_layers": 1, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
            "expert_sharing_policy": "independent_per_expert",
            "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
        }, modules=uneven_experts, num_experts=2, top_k=1,
    )
    assert uneven["active_parameter_count"] == "OPEN_AUTHORITY_GAP"
    assert uneven["active_parameter_count_min"] < uneven["active_parameter_count_max"]

    layerwise_modules = [
        {"parameter_id": "attention.weight", "role": "attention", "shape": [1], "trainable": True, "group": "shared"},
        {"parameter_id": "router.weight", "role": "router", "shape": [1], "trainable": True, "group": "router"},
        {"parameter_id": "output_head.weight", "role": "output_head", "shape": [1], "trainable": True, "group": "shared"},
        {"parameter_id": "expert.l0.e0", "role": "expert", "shape": [10, 10], "trainable": True, "group": "expert", "expert_index": 0, "layer_index": 0},
        {"parameter_id": "expert.l0.e1", "role": "expert", "shape": [1], "trainable": True, "group": "expert", "expert_index": 1, "layer_index": 0},
        {"parameter_id": "expert.l1.e0", "role": "expert", "shape": [1], "trainable": True, "group": "expert", "expert_index": 0, "layer_index": 1},
        {"parameter_id": "expert.l1.e1", "role": "expert", "shape": [10, 10], "trainable": True, "group": "expert", "expert_index": 1, "layer_index": 1},
    ]
    layerwise = count_module_parameter_ledger(
        architecture_family="transformer_moe", architecture_version="planning-v1",
        input_feature_count=1, output_dimension=1, sequence_length=2,
        exact_architecture_parameters={
            "num_layers": 2, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
            "expert_sharing_policy": "independent_per_expert",
            "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
        }, modules=layerwise_modules, num_experts=2, top_k=1,
    )
    assert layerwise["active_parameter_count_min"] == 5
    assert layerwise["active_parameter_count_max"] == 203
    missing_layer_binding = [dict(module) for module in layerwise_modules]
    del missing_layer_binding[3]["layer_index"]
    with pytest.raises(ValueError, match="layer_index"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe", architecture_version="planning-v1",
            input_feature_count=1, output_dimension=1, sequence_length=2,
            exact_architecture_parameters={
                "num_layers": 2, "num_experts": 2, "top_k": 1, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            }, modules=missing_layer_binding, num_experts=2, top_k=1,
        )
    with pytest.raises(ValueError, match="match exact architecture parameters"):
        count_module_parameter_ledger(
            architecture_family="transformer_moe",
            architecture_version="planning-v1",
            input_feature_count=4,
            output_dimension=2,
            sequence_length=8,
            exact_architecture_parameters={
                "num_layers": 1, "num_experts": 2, "top_k": 2, "expert_ffn_mult": 2,
                "expert_sharing_policy": "independent_per_expert",
                "shared_ffn_present": False, "ffn_mult": "NOT_APPLICABLE",
            },
            modules=modules,
            num_experts=2,
            top_k=1,
        )
    with pytest.raises(ValueError, match="missing required roles"):
        count_module_parameter_ledger(
            architecture_family="tft",
            architecture_version="planning-v1",
            input_feature_count=4,
            output_dimension=3,
            sequence_length=8,
            exact_architecture_parameters={"hidden_size": 16},
            modules=[{"role": "attention", "shape": [4, 4], "trainable": True}],
        )
    with pytest.raises(ValueError, match="seven temporal neural families"):
        count_module_parameter_ledger(
            architecture_family="transformer",
            architecture_version="planning-v1",
            input_feature_count=4,
            output_dimension=3,
            sequence_length=8,
            exact_architecture_parameters={},
            modules=[{"role": "attention", "shape": [4, 4], "trainable": True}],
        )
