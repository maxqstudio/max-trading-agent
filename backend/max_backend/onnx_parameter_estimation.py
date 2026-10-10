"""Pure ONNX-00 parameter/capacity estimators; not wired to training or runtime.

Counts derived from an explicit module ledger are static planning evidence only.
They must be checked against instantiated model parameters and measured memory
before any later runtime admission.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite, prod
from typing import Any


NOT_APPLICABLE = "NOT_APPLICABLE"
OPEN_AUTHORITY_GAP = "OPEN_AUTHORITY_GAP"
NOT_AVAILABLE_PRE_FIT = "NOT_AVAILABLE_PRE_FIT"
RAW_DECLARED_LEDGER_COUNT = "RAW_DECLARED_LEDGER_COUNT"
CANDIDATE_SPEC_STATICALLY_VALIDATED = "CANDIDATE_SPEC_STATICALLY_VALIDATED"
MEASURED_INSTANTIATED_MODEL = "MEASURED_INSTANTIATED_MODEL"
SUPPORTED_RECURRENT_VARIANT = "simple_unidirectional_bias_enabled_unprojected"

_NEURAL_FAMILIES = {"gru", "tcn", "itransformer", "patchtst", "tft", "transformer_moe", "lstm"}
REQUIRED_MODULE_ROLES_BY_FAMILY = {
    "gru": {"recurrent", "output_head"},
    "tcn": {"causal_convolution", "output_head"},
    "itransformer": {"variate_projection", "attention", "feedforward", "normalization", "output_head"},
    "patchtst": {"patch_projection", "attention", "feedforward", "normalization", "output_head"},
    "tft": {"variable_selection", "temporal_processing", "gating", "attention", "output_head"},
    "transformer_moe": {"attention", "router", "expert", "output_head"},
    "lstm": {"recurrent", "output_head"},
}


def _positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def recurrent_parameter_count(
    family: str,
    *,
    input_features: int,
    hidden_size: int,
    num_layers: int,
    output_dimensions: int,
) -> int:
    """Return the formula count; this alone is not CandidateSpec validation."""
    if family not in {"gru", "lstm"}:
        raise ValueError("family must be exactly 'gru' or 'lstm'")
    input_features = _positive_int("input_features", input_features)
    hidden_size = _positive_int("hidden_size", hidden_size)
    num_layers = _positive_int("num_layers", num_layers)
    output_dimensions = _positive_int("output_dimensions", output_dimensions)
    gates = 3 if family == "gru" else 4
    first_layer = gates * hidden_size * (input_features + hidden_size + 2)
    later_layers = (num_layers - 1) * gates * hidden_size * (2 * hidden_size + 2)
    output_head = hidden_size * output_dimensions + output_dimensions
    return first_layer + later_layers + output_head


def dense_transformer_block_parameter_estimate(
    *, d_model: int, ffn_multiplier: int, num_layers: int
) -> int:
    """Return the stated dominant-term approximation, excluding other modules."""
    d_model = _positive_int("d_model", d_model)
    ffn_multiplier = _positive_int("ffn_multiplier", ffn_multiplier)
    num_layers = _positive_int("num_layers", num_layers)
    return num_layers * (4 * d_model**2 + 2 * d_model * (d_model * ffn_multiplier))


def moe_transformer_block_parameter_estimate(
    *,
    d_model: int,
    ffn_mult: int | None,
    expert_ffn_mult: int,
    shared_ffn_present: bool,
    expert_sharing_policy: str,
    num_layers: int,
    num_experts: int,
    top_k: int,
    router_parameters_per_layer: int,
) -> dict[str, int | str]:
    """Return a structural MoE estimate with separate shared/expert widths.

    Router parameters are required explicitly because ONNX-00 does not authorize
    a router implementation or silently assume its size. This is not a frozen
    architecture validation or a measured memory/ONNX compatibility result.
    """
    d_model = _positive_int("d_model", d_model)
    if not isinstance(shared_ffn_present, bool):
        raise ValueError("shared_ffn_present must be an explicit boolean")
    if shared_ffn_present:
        ffn_mult = _positive_int("ffn_mult", ffn_mult)
    elif ffn_mult is not None:
        raise ValueError("ffn_mult must be absent when the selected MoE has no shared FFN")
    expert_ffn_mult = _positive_int("expert_ffn_mult", expert_ffn_mult)
    if expert_sharing_policy != "independent_per_expert":
        raise ValueError("unsupported expert_sharing_policy; independent_per_expert is required")
    num_layers = _positive_int("num_layers", num_layers)
    num_experts = _positive_int("num_experts", num_experts)
    top_k = _positive_int("top_k", top_k)
    if top_k > num_experts:
        raise ValueError("top_k cannot exceed num_experts")
    if isinstance(router_parameters_per_layer, bool) or not isinstance(router_parameters_per_layer, int) or router_parameters_per_layer < 0:
        raise ValueError("router_parameters_per_layer must be an explicit non-negative integer")
    shared_attention_per_layer = 4 * d_model**2
    shared_ffn_per_layer = (
        2 * d_model * (d_model * ffn_mult) if shared_ffn_present else 0
    )
    one_expert_per_layer = 2 * d_model * (d_model * expert_ffn_mult)
    shared_per_layer = shared_attention_per_layer + shared_ffn_per_layer
    total = num_layers * (
        shared_per_layer + num_experts * one_expert_per_layer + router_parameters_per_layer
    )
    active = num_layers * (
        shared_per_layer + top_k * one_expert_per_layer + router_parameters_per_layer
    )
    return {
        "total_parameter_count": total,
        "active_parameter_count": active,
        "expert_parameter_count": num_layers * num_experts * one_expert_per_layer,
        "active_expert_parameter_count": num_layers * top_k * one_expert_per_layer,
        "shared_parameter_count": num_layers * shared_per_layer,
        "shared_attention_parameter_count": num_layers * shared_attention_per_layer,
        "shared_ffn_parameter_count": (
            num_layers * shared_ffn_per_layer if shared_ffn_present else NOT_APPLICABLE
        ),
        "ffn_mult": ffn_mult if shared_ffn_present else NOT_APPLICABLE,
        "expert_ffn_mult": expert_ffn_mult,
        "shared_ffn_present": shared_ffn_present,
        "expert_sharing_policy": expert_sharing_policy,
        "estimated_weight_bytes": total * 4,
        "estimated_peak_training_bytes": OPEN_AUTHORITY_GAP,
        "estimated_inference_bytes": OPEN_AUTHORITY_GAP,
        "estimation_assumptions": "Structural FP32 weight storage uses all shared and expert parameters at four bytes each; activation, optimizer, framework, workspace, and runtime-provider memory are not measured or inferred.",
        "router_parameter_count": num_layers * router_parameters_per_layer,
        "estimation_confidence": "STRUCTURAL_APPROXIMATION",
        "candidate_spec_validation_status": OPEN_AUTHORITY_GAP,
        "measured_peak_vram": OPEN_AUTHORITY_GAP,
        "onnx_compatibility": OPEN_AUTHORITY_GAP,
    }


def validate_recurrent_candidate_spec(
    *, candidate_spec: Mapping[str, Any], modules: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate one supported recurrent CandidateSpec against an exact tensor ledger.

    Only the explicit unidirectional, bias-enabled, unprojected recurrent layer
    plus linear output-head variant is covered by the approved formulas. The
    semantic parameter IDs and shapes make the ledger independently auditable.
    """
    if not isinstance(candidate_spec, Mapping):
        raise ValueError("candidate_spec must be a mapping")
    family = candidate_spec.get("architecture_family")
    if family not in {"gru", "lstm"}:
        raise ValueError("CandidateSpec family must be exactly 'gru' or 'lstm'")
    version = candidate_spec.get("architecture_version")
    if not isinstance(version, str) or not version:
        raise ValueError("CandidateSpec architecture_version is required")
    input_features = _positive_int("input_feature_count", candidate_spec.get("input_feature_count"))
    output_dimensions = _positive_int("output_dimension", candidate_spec.get("output_dimension"))
    sequence_length = _positive_int("sequence_length", candidate_spec.get("sequence_length"))
    params = candidate_spec.get("exact_architecture_parameters")
    if not isinstance(params, Mapping):
        raise ValueError("CandidateSpec exact_architecture_parameters are required")
    hidden_size = _positive_int("hidden_size", params.get("hidden_size"))
    num_layers = _positive_int("num_layers", params.get("num_layers"))
    required_variant_fields = {
        "recurrent_variant",
        "bias",
        "bidirectional",
        "projection_size",
        "output_head",
    }
    missing = required_variant_fields - set(params)
    if missing:
        raise ValueError(f"CandidateSpec missing recurrent variant fields: {', '.join(sorted(missing))}")
    if params["recurrent_variant"] != SUPPORTED_RECURRENT_VARIANT:
        raise ValueError("unsupported recurrent_variant")
    if params["bias"] is not True or params["bidirectional"] is not False:
        raise ValueError("recurrent formula requires bias=true and bidirectional=false")
    if (
        isinstance(params["projection_size"], bool)
        or not isinstance(params["projection_size"], int)
        or params["projection_size"] != 0
    ):
        raise ValueError("recurrent formula requires projection_size=0")
    if params["output_head"] != "linear":
        raise ValueError("recurrent formula requires output_head='linear'")
    if isinstance(modules, (str, bytes)) or not isinstance(modules, Sequence):
        raise ValueError("modules must be an explicit tensor ledger")

    gates = 3 if family == "gru" else 4
    expected: dict[str, tuple[str, list[int]]] = {}
    for layer in range(num_layers):
        layer_input = input_features if layer == 0 else hidden_size
        prefix = f"recurrent.layer_{layer}"
        expected[f"{prefix}.weight_ih"] = ("recurrent", [gates * hidden_size, layer_input])
        expected[f"{prefix}.weight_hh"] = ("recurrent", [gates * hidden_size, hidden_size])
        expected[f"{prefix}.bias_ih"] = ("recurrent", [gates * hidden_size])
        expected[f"{prefix}.bias_hh"] = ("recurrent", [gates * hidden_size])
    expected["output_head.weight"] = ("output_head", [output_dimensions, hidden_size])
    expected["output_head.bias"] = ("output_head", [output_dimensions])

    observed: dict[str, Mapping[str, Any]] = {}
    ledger_count = 0
    for index, module in enumerate(modules):
        if not isinstance(module, Mapping):
            raise ValueError(f"module {index} must be a mapping")
        parameter_id = module.get("parameter_id")
        if not isinstance(parameter_id, str) or not parameter_id:
            raise ValueError(f"module {index} requires an explicit parameter_id")
        if parameter_id in observed:
            raise ValueError(f"duplicate recurrent parameter_id: {parameter_id}")
        observed[parameter_id] = module
        if module.get("trainable") is not True:
            raise ValueError(f"recurrent CandidateSpec tensor must be trainable: {parameter_id}")
        shape = module.get("shape")
        if not isinstance(shape, list) or any(
            isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0 for dim in shape
        ):
            raise ValueError(f"invalid tensor shape: {parameter_id}")
        ledger_count += prod(shape)

    if set(observed) != set(expected):
        missing_tensors = sorted(set(expected) - set(observed))
        extra_tensors = sorted(set(observed) - set(expected))
        raise ValueError(
            f"recurrent tensor identity mismatch; missing={missing_tensors}, extra={extra_tensors}"
        )
    for parameter_id, (role, shape) in expected.items():
        module = observed[parameter_id]
        if module.get("role") != role or module.get("shape") != shape:
            raise ValueError(f"recurrent tensor role/shape contradicts CandidateSpec: {parameter_id}")

    formula_count = recurrent_parameter_count(
        family,
        input_features=input_features,
        hidden_size=hidden_size,
        num_layers=num_layers,
        output_dimensions=output_dimensions,
    )
    if ledger_count != formula_count:
        raise ValueError("recurrent tensor ledger count does not match approved formula")
    return {
        "architecture_family": family,
        "architecture_version": version,
        "input_feature_count": input_features,
        "output_dimension": output_dimensions,
        "sequence_length": sequence_length,
        "exact_architecture_parameters": dict(params),
        "trainable_parameter_count": formula_count,
        "non_trainable_parameter_count": 0,
        "total_parameter_count": formula_count,
        "raw_declared_ledger_trainable_parameter_count": ledger_count,
        "raw_declared_ledger_non_trainable_parameter_count": 0,
        "raw_declared_ledger_total_parameter_count": ledger_count,
        "candidate_spec_validated_parameter_count": formula_count,
        "candidate_spec_static_validation_status": "PASS",
        "candidate_spec_validation_gap": NOT_APPLICABLE,
        "estimation_method": "APPROVED_RECURRENT_FORMULA_RECONCILED_TO_EXACT_NAMED_TENSOR_SHAPES",
        "estimation_confidence": CANDIDATE_SPEC_STATICALLY_VALIDATED,
        "instantiated_model_verification": "NOT_PERFORMED_IN_ONNX_00",
        "measured_peak_vram": OPEN_AUTHORITY_GAP,
        "onnx_compatibility": OPEN_AUTHORITY_GAP,
    }


def validate_attention_heads(*, d_model: int, attention_heads: int) -> None:
    d_model = _positive_int("d_model", d_model)
    attention_heads = _positive_int("attention_heads", attention_heads)
    if d_model % attention_heads:
        raise ValueError("d_model must be divisible by attention_heads")


def validate_patch_configuration(
    *, sequence_length: int, patch_len: int, patch_stride: int
) -> None:
    sequence_length = _positive_int("sequence_length", sequence_length)
    patch_len = _positive_int("patch_len", patch_len)
    patch_stride = _positive_int("patch_stride", patch_stride)
    if patch_stride > patch_len:
        raise ValueError("patch_stride cannot exceed patch_len")
    if patch_len > sequence_length:
        raise ValueError("patch_len cannot exceed sequence_length")


def validate_tft_covariates(
    *,
    required_covariates: Sequence[str],
    available_covariates: Sequence[str],
) -> None:
    if any(
        isinstance(values, (str, bytes)) or not isinstance(values, Sequence)
        for values in (required_covariates, available_covariates)
    ):
        raise ValueError("covariates must be explicit sequences of identities")
    required_values = list(required_covariates)
    available_values = list(available_covariates)
    if any(not isinstance(name, str) or not name for name in required_values + available_values):
        raise ValueError("covariate identities must be non-empty strings")
    required = set(required_values)
    available = set(available_values)
    missing = sorted(required - available)
    if missing:
        raise ValueError(f"unsupported TFT covariates: {', '.join(missing)}")


def validate_search_proposal(
    proposal: Mapping[str, Any], envelope: Mapping[str, Mapping[str, Any]]
) -> None:
    """Reject out-of-envelope proposals; this function never clamps values."""
    if not isinstance(proposal, Mapping) or not isinstance(envelope, Mapping):
        raise ValueError("proposal and active envelope must be mappings")
    missing = set(envelope) - set(proposal)
    if missing:
        raise ValueError(f"proposal missing required parameters: {', '.join(sorted(missing))}")
    for name, value in proposal.items():
        bounds = envelope.get(name)
        if bounds is None:
            raise ValueError(f"parameter has no authorized active envelope: {name}")
        if not isinstance(bounds, Mapping) or "min" not in bounds or "max" not in bounds:
            raise ValueError(f"parameter has malformed active bounds: {name}")
        minimum, maximum = bounds["min"], bounds["max"]
        if (
            not isinstance(minimum, (int, float))
            or isinstance(minimum, bool)
            or not isinstance(maximum, (int, float))
            or isinstance(maximum, bool)
            or not isfinite(minimum)
            or not isfinite(maximum)
            or minimum > maximum
        ):
            raise ValueError(f"parameter has invalid active bounds: {name}")
        parameter_type = bounds.get("type")
        if parameter_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"parameter must be an integer: {name}")
        elif parameter_type in (None, "number"):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"parameter must be a finite number: {name}")
        else:
            raise ValueError(f"parameter must be numeric: {name}")
        if value < minimum or value > maximum:
            raise ValueError(f"parameter outside active envelope: {name}")


def count_module_parameter_ledger(
    *,
    architecture_family: str,
    architecture_version: str,
    input_feature_count: int,
    output_dimension: int,
    sequence_length: int,
    exact_architecture_parameters: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
    required_module_roles: Sequence[str] = (),
    num_experts: int | None = None,
    top_k: int | None = None,
) -> dict[str, int | str]:
    """Count declared tensor shapes without claiming CandidateSpec validation."""
    if architecture_family not in _NEURAL_FAMILIES:
        raise ValueError("module ledger estimator supports only the seven temporal neural families")
    if (
        not isinstance(architecture_version, str)
        or not architecture_version
        or isinstance(modules, (str, bytes))
        or not isinstance(modules, Sequence)
        or not modules
        or not isinstance(exact_architecture_parameters, Mapping)
        or not exact_architecture_parameters
    ):
        raise ValueError("architecture version, exact parameters, and a non-empty module ledger are required")
    input_feature_count = _positive_int("input_feature_count", input_feature_count)
    output_dimension = _positive_int("output_dimension", output_dimension)
    sequence_length = _positive_int("sequence_length", sequence_length)
    roles: set[str] = set()
    trainable_count = 0
    non_trainable_count = 0
    groups: dict[str, int] = {"shared": 0, "router": 0, "expert": 0, "other": 0}
    expert_counts: dict[tuple[int, int], int] = {}
    parameter_ids: set[str] = set()
    moe_params: Mapping[str, Any] = exact_architecture_parameters

    if architecture_family == "transformer_moe":
        if num_experts is None or top_k is None:
            raise ValueError("MoE ledgers require explicit num_experts and top_k")
        num_experts = _positive_int("num_experts", num_experts)
        top_k = _positive_int("top_k", top_k)
        if (
            moe_params.get("num_experts") != num_experts
            or moe_params.get("top_k") != top_k
        ):
            raise ValueError("MoE estimator arguments must match exact architecture parameters")
        moe_layers = _positive_int("num_layers", moe_params.get("num_layers"))
        _positive_int("expert_ffn_mult", moe_params.get("expert_ffn_mult"))
        if moe_params.get("expert_sharing_policy") != "independent_per_expert":
            raise ValueError("unsupported expert_sharing_policy; independent_per_expert is required")
        shared_ffn_present = moe_params.get("shared_ffn_present")
        if not isinstance(shared_ffn_present, bool):
            raise ValueError("MoE exact parameters require explicit shared_ffn_present")
        if shared_ffn_present:
            _positive_int("ffn_mult", moe_params.get("ffn_mult"))
        elif moe_params.get("ffn_mult") != NOT_APPLICABLE:
            raise ValueError("MoE ffn_mult must be NOT_APPLICABLE when shared FFN is absent")
        if top_k > num_experts:
            raise ValueError("top_k cannot exceed num_experts")

    for index, module in enumerate(modules):
        if not isinstance(module, Mapping):
            raise ValueError(f"module {index} must be a mapping")
        role = module.get("role")
        shape = module.get("shape")
        trainable = module.get("trainable")
        group = module.get("group", "other")
        if not isinstance(role, str) or not role:
            raise ValueError(f"module {index} requires an explicit role")
        if not isinstance(shape, list) or not shape:
            raise ValueError(f"module {index} requires an explicit parameter shape")
        if not isinstance(trainable, bool):
            raise ValueError(f"module {index} requires an explicit trainable flag")
        if not isinstance(group, str) or group not in groups:
            raise ValueError(f"module {index} has an unsupported parameter group")
        count = prod(_positive_int(f"module {index} shape dimension", dim) for dim in shape)
        if architecture_family == "transformer_moe":
            parameter_id = module.get("parameter_id")
            if not isinstance(parameter_id, str) or not parameter_id:
                raise ValueError(f"MoE module {index} requires an explicit parameter_id")
            if parameter_id in parameter_ids:
                raise ValueError(f"duplicate MoE parameter_id: {parameter_id}")
            parameter_ids.add(parameter_id)
            required_group_by_role = {
                "attention": "shared",
                "router": "router",
                "expert": "expert",
                "output_head": "shared",
                "shared_ffn": "shared",
            }
            expected_group = required_group_by_role.get(role)
            if expected_group is not None and group != expected_group:
                raise ValueError(f"MoE role/group mismatch for {role}")
            if group in {"router", "expert"} and role != group:
                raise ValueError(f"MoE {group} group cannot contain role {role}")
            if role == "shared_ffn" and not moe_params["shared_ffn_present"]:
                raise ValueError("shared FFN tensor is absent from the exact MoE configuration")
        roles.add(role)
        groups[group] += count
        if group == "expert":
            expert_index = module.get("expert_index")
            if isinstance(expert_index, bool) or not isinstance(expert_index, int) or expert_index < 0:
                raise ValueError(f"expert module {index} requires an explicit non-negative expert_index")
            if architecture_family == "transformer_moe" and expert_index >= num_experts:
                raise ValueError(f"MoE expert index is outside the exact expert universe: {expert_index}")
            if architecture_family == "transformer_moe":
                layer_index = module.get("layer_index")
                if isinstance(layer_index, bool) or not isinstance(layer_index, int) or layer_index < 0:
                    raise ValueError(f"MoE expert module {index} requires an explicit non-negative layer_index")
                if layer_index >= moe_layers:
                    raise ValueError(f"MoE expert layer_index is outside the exact layer universe: {layer_index}")
                expert_key = (layer_index, expert_index)
            else:
                expert_key = (0, expert_index)
            expert_counts[expert_key] = expert_counts.get(expert_key, 0) + count
        elif "expert_index" in module:
            raise ValueError(f"non-expert module {index} cannot declare expert_index")
        if trainable:
            trainable_count += count
        else:
            non_trainable_count += count

    missing_roles = (REQUIRED_MODULE_ROLES_BY_FAMILY[architecture_family] | set(required_module_roles)) - roles
    if missing_roles:
        raise ValueError(f"module ledger missing required roles: {', '.join(sorted(missing_roles))}")

    total = trainable_count + non_trainable_count
    result: dict[str, int | str] = {
        "architecture_family": architecture_family,
        "architecture_version": architecture_version,
        "input_feature_count": input_feature_count,
        "output_dimension": output_dimension,
        "sequence_length": sequence_length,
        "exact_architecture_parameters": dict(exact_architecture_parameters),
        "trainable_parameter_count": trainable_count,
        "non_trainable_parameter_count": non_trainable_count,
        "total_parameter_count": total,
        "estimation_method": "EXPLICIT_VERSIONED_PARAMETER_TENSOR_LEDGER",
        "raw_declared_ledger_trainable_parameter_count": trainable_count,
        "raw_declared_ledger_non_trainable_parameter_count": non_trainable_count,
        "raw_declared_ledger_total_parameter_count": total,
        "candidate_spec_validated_parameter_count": OPEN_AUTHORITY_GAP,
        "candidate_spec_static_validation_status": OPEN_AUTHORITY_GAP,
        "candidate_spec_validation_gap": (
            "Presence of required role labels and a tensor ledger do not establish exact architecture topology, sharing, or CandidateSpec coherence."
        ),
        "estimation_confidence": RAW_DECLARED_LEDGER_COUNT,
        "instantiated_model_verification": "NOT_PERFORMED_IN_ONNX_00",
    }
    if architecture_family == "transformer_moe":
        if ("shared_ffn" in roles) != moe_params["shared_ffn_present"]:
            raise ValueError("MoE shared FFN ledger does not match exact architecture parameters")
        per_layer_expert_sizes: list[list[int]] = []
        for layer_index in range(moe_layers):
            layer_experts = {
                expert_index: count
                for (recorded_layer, expert_index), count in expert_counts.items()
                if recorded_layer == layer_index
            }
            if set(layer_experts) != set(range(num_experts)):
                raise ValueError(
                    f"MoE expert ledger must match the exact expert universe in layer {layer_index}"
                )
            per_layer_expert_sizes.append(sorted(layer_experts.values()))
        active_expert_min = sum(sum(sizes[:top_k]) for sizes in per_layer_expert_sizes)
        active_expert_max = sum(sum(sizes[-top_k:]) for sizes in per_layer_expert_sizes)
        active_shared_and_router = groups["shared"] + groups["router"] + groups["other"]
        exact_active_expert_count: int | str = (
            active_expert_min if active_expert_min == active_expert_max else OPEN_AUTHORITY_GAP
        )
        exact_active_count: int | str = (
            active_shared_and_router + active_expert_min
            if active_expert_min == active_expert_max
            else OPEN_AUTHORITY_GAP
        )
        result.update(
            {
                "expert_parameter_count": sum(expert_counts.values()),
                "shared_parameter_count": groups["shared"],
                "router_parameter_count": groups["router"],
                "other_parameter_count": groups["other"],
                "shared_ffn_parameter_count": (
                    sum(
                        prod(module["shape"])
                        for module in modules
                        if module.get("role") == "shared_ffn"
                    )
                    if moe_params["shared_ffn_present"]
                    else NOT_APPLICABLE
                ),
                "active_parameter_count": exact_active_count,
                "active_parameter_count_min": active_shared_and_router + active_expert_min,
                "active_parameter_count_max": active_shared_and_router + active_expert_max,
                "active_expert_parameter_count": exact_active_expert_count,
                "active_expert_parameter_count_min": active_expert_min,
                "active_expert_parameter_count_max": active_expert_max,
                "active_expert_count_range_semantics": "Lower/upper bounds sum the smallest/largest top_k expert parameter totals independently in each layer; exact only when each layer's experts have equal sizes.",
                "moe_candidate_spec_static_validation_status": OPEN_AUTHORITY_GAP,
                "moe_ledger_note": "Expert width is recorded in CandidateSpec but the open architecture topology is not validated by role labels; ledger counts remain raw.",
            }
        )
    else:
        result.update(
            {
                "expert_parameter_count": NOT_APPLICABLE,
                "shared_parameter_count": NOT_APPLICABLE,
                "router_parameter_count": NOT_APPLICABLE,
                "active_parameter_count": NOT_APPLICABLE,
                "active_expert_parameter_count": NOT_APPLICABLE,
            }
        )
    return result


def estimate_neural_memory_bytes(
    *,
    total_parameter_count: int,
    trainable_parameter_count: int,
    activation_peak_bytes: int | None,
    temporary_peak_bytes: int | None,
    inference_workspace_bytes: int | None,
    parameter_bytes: int = 4,
    gradient_bytes: int = 4,
    optimizer_state_bytes_per_parameter: int = 8,
) -> dict[str, int | str]:
    """Return component memory estimates; unknown activation/workspace stays open."""
    total_parameter_count = _positive_int("total_parameter_count", total_parameter_count)
    trainable_parameter_count = _positive_int("trainable_parameter_count", trainable_parameter_count)
    if trainable_parameter_count > total_parameter_count:
        raise ValueError("trainable_parameter_count cannot exceed total_parameter_count")
    parameter_bytes = _positive_int("parameter_bytes", parameter_bytes)
    gradient_bytes = _positive_int("gradient_bytes", gradient_bytes)
    optimizer_state_bytes_per_parameter = _positive_int(
        "optimizer_state_bytes_per_parameter", optimizer_state_bytes_per_parameter
    )
    for field, value in (
        ("activation_peak_bytes", activation_peak_bytes),
        ("temporary_peak_bytes", temporary_peak_bytes),
        ("inference_workspace_bytes", inference_workspace_bytes),
    ):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{field} must be a non-negative integer or None")

    weights = total_parameter_count * parameter_bytes
    gradients = trainable_parameter_count * gradient_bytes
    optimizer = trainable_parameter_count * optimizer_state_bytes_per_parameter
    peak = (
        weights + gradients + optimizer + activation_peak_bytes + temporary_peak_bytes
        if activation_peak_bytes is not None and temporary_peak_bytes is not None
        else OPEN_AUTHORITY_GAP
    )
    inference = (
        weights + inference_workspace_bytes
        if inference_workspace_bytes is not None
        else OPEN_AUTHORITY_GAP
    )
    return {
        "estimated_weight_bytes": weights,
        "estimated_gradient_bytes": gradients,
        "estimated_optimizer_state_bytes": optimizer,
        "estimated_activation_peak_bytes": activation_peak_bytes if activation_peak_bytes is not None else OPEN_AUTHORITY_GAP,
        "estimated_peak_training_bytes": peak,
        "estimated_inference_bytes": inference,
        "estimation_assumptions": "Explicit tensor ledger and dtype byte sizes; FP32 AdamW baseline is 4-byte weights + 4-byte gradients + 8-byte two-moment state per trainable parameter; activation/temporary/workspace bytes are not inferred.",
        "estimation_confidence": RAW_DECLARED_LEDGER_COUNT,
    }


def estimate_neural_candidate(
    *,
    architecture_family: str,
    architecture_version: str,
    input_feature_count: int,
    output_dimension: int,
    sequence_length: int,
    exact_architecture_parameters: Mapping[str, Any],
    modules: Sequence[Mapping[str, Any]],
    activation_peak_bytes: int | None,
    temporary_peak_bytes: int | None,
    inference_workspace_bytes: int | None,
    num_experts: int | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    """Build raw ledger evidence and validate only supported recurrent topology."""
    counts = count_module_parameter_ledger(
        architecture_family=architecture_family,
        architecture_version=architecture_version,
        input_feature_count=input_feature_count,
        output_dimension=output_dimension,
        sequence_length=sequence_length,
        exact_architecture_parameters=exact_architecture_parameters,
        modules=modules,
        num_experts=num_experts,
        top_k=top_k,
    )
    if architecture_family in {"gru", "lstm"}:
        recurrent_spec = {
            "architecture_family": architecture_family,
            "architecture_version": architecture_version,
            "input_feature_count": input_feature_count,
            "output_dimension": output_dimension,
            "sequence_length": sequence_length,
            "exact_architecture_parameters": dict(exact_architecture_parameters),
        }
        validated = validate_recurrent_candidate_spec(candidate_spec=recurrent_spec, modules=modules)
        counts.update(validated)
    else:
        counts["candidate_spec_validated_parameter_count"] = OPEN_AUTHORITY_GAP
        counts["candidate_spec_static_validation_status"] = OPEN_AUTHORITY_GAP
        counts["candidate_spec_validation_gap"] = (
            f"No complete frozen module topology validator is available for {architecture_family}; "
            "the reported ledger count is raw and unvalidated."
        )
    memory = estimate_neural_memory_bytes(
        total_parameter_count=int(counts["total_parameter_count"]),
        trainable_parameter_count=int(counts["trainable_parameter_count"]),
        activation_peak_bytes=activation_peak_bytes,
        temporary_peak_bytes=temporary_peak_bytes,
        inference_workspace_bytes=inference_workspace_bytes,
    )
    return {
        **counts,
        **memory,
        "estimation_confidence": counts["estimation_confidence"],
        "measured_peak_vram": OPEN_AUTHORITY_GAP,
        "onnx_compatibility": OPEN_AUTHORITY_GAP,
    }


def estimate_tree_complexity_bound(
    *,
    architecture_family: str,
    architecture_version: str,
    input_feature_count: int,
    output_dimension: int,
    n_estimators: int,
    max_depth: int,
    trees_per_round: int,
    num_leaves: int | None = None,
) -> dict[str, Any]:
    """Return structural tree bounds separately from post-fit actual structure."""
    if architecture_family not in {"lightgbm", "xgboost"}:
        raise ValueError("tree estimator supports only lightgbm and xgboost")
    input_feature_count = _positive_int("input_feature_count", input_feature_count)
    output_dimension = _positive_int("output_dimension", output_dimension)
    n_estimators = _positive_int("n_estimators", n_estimators)
    max_depth = _positive_int("max_depth", max_depth)
    trees_per_round = _positive_int("trees_per_round", trees_per_round)
    if not architecture_version:
        raise ValueError("architecture_version is required")
    if architecture_family == "lightgbm":
        num_leaves = _positive_int("num_leaves", num_leaves)
        leaves_per_tree_bound = min(num_leaves, 2**max_depth)
    else:
        if num_leaves is not None:
            raise ValueError("num_leaves is not an XGBoost parameter in this estimator contract")
        leaves_per_tree_bound = 2**max_depth
    tree_count_bound = n_estimators * trees_per_round
    total_leaf_bound = tree_count_bound * leaves_per_tree_bound
    return {
        "architecture_family": architecture_family,
        "architecture_version": architecture_version,
        "input_feature_count": input_feature_count,
        "output_dimension": output_dimension,
        "sequence_length": NOT_APPLICABLE,
        "exact_architecture_parameters": {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "num_leaves": num_leaves if architecture_family == "lightgbm" else NOT_APPLICABLE,
            "trees_per_round": trees_per_round,
        },
        "trainable_parameter_count": NOT_APPLICABLE,
        "non_trainable_parameter_count": NOT_APPLICABLE,
        "total_parameter_count": NOT_APPLICABLE,
        "active_parameter_count": NOT_APPLICABLE,
        "expert_parameter_count": NOT_APPLICABLE,
        "shared_parameter_count": NOT_APPLICABLE,
        "tree_count_actual": NOT_AVAILABLE_PRE_FIT,
        "tree_count_upper_bound": tree_count_bound,
        "total_leaf_count_actual": NOT_AVAILABLE_PRE_FIT,
        "total_leaf_count_upper_bound": total_leaf_bound,
        "total_split_node_count_actual": NOT_AVAILABLE_PRE_FIT,
        "total_split_node_count_upper_bound": max(0, total_leaf_bound - tree_count_bound),
        "tree_depth_distribution_actual": NOT_AVAILABLE_PRE_FIT,
        "serialized_model_bytes": NOT_AVAILABLE_PRE_FIT,
        "estimated_weight_bytes": NOT_APPLICABLE,
        "estimated_gradient_bytes": NOT_APPLICABLE,
        "estimated_optimizer_state_bytes": NOT_APPLICABLE,
        "estimated_activation_peak_bytes": OPEN_AUTHORITY_GAP,
        "estimated_peak_training_bytes": OPEN_AUTHORITY_GAP,
        "estimated_inference_bytes": OPEN_AUTHORITY_GAP,
        "estimation_method": "THEORETICAL_TREE_STRUCTURAL_UPPER_BOUND_NOT_ACTUAL_GROWN_TREE_COUNT",
        "estimation_assumptions": "Binary-tree depth bound and exact trees_per_round supplied by the frozen objective/class contract; actual leaves, splits, depth distribution and serialized size require the fitted tree artifact.",
        "estimation_confidence": "STRUCTURAL_APPROXIMATION",
    }
