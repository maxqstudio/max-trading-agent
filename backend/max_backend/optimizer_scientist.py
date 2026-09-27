from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

PHASE = "STRATEGY_OPTIMIZER_RANGE_PROPOSAL"
TEMPERATURE = 0.10
TOP_PASS_LIMIT = 12

SECRET_KEY_NAMES = {
    "api_key", "apikey", "authorization", "secret", "token", "password",
    "credential", "access_token", "refresh_token", "client_secret",
}
AUTH_MODES = {"API_KEY", "OLLAMA_LOCAL", "NONE"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_ROUTE_KEYS = {
    "provider", "base_url", "model", "auth_mode", "api_key_env", "timeout_sec",
    "fallback_models",
}
DEFAULT_ROUTE = {
    "provider": "gemini",
    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "model": "gemini-3.5-flash",
    "auth_mode": "API_KEY",
    "api_key_env": "COMPLEXPOLICY_LLM_API_KEY",
    "timeout_sec": 60,
}


class ScientistAdvisoryError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        actual_llm_call: bool,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.actual_llm_call = bool(actual_llm_call)
        self.provenance = dict(provenance or {})


@dataclass(frozen=True)
class ScientistCallResult:
    text: str
    provenance: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sanitize_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            lowered = name.casefold()
            if lowered in SECRET_KEY_NAMES:
                continue
            if any(
                token in lowered
                for token in ("authorization", "bearer", "client_secret")
            ):
                continue
            clean[name] = sanitize_recursive(item)
        return clean
    if isinstance(value, list):
        return [sanitize_recursive(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_recursive(item) for item in value]
    return value


def _route_config_from_environment_only() -> dict[str, Any]:
    return sanitize_route_config(
        {
            "provider": os.environ.get(
                "MAX_SCIENTIST_PROVIDER", DEFAULT_ROUTE["provider"]
            ),
            "base_url": os.environ.get(
                "MAX_SCIENTIST_BASE_URL", DEFAULT_ROUTE["base_url"]
            ),
            "model": os.environ.get(
                "MAX_SCIENTIST_MODEL", DEFAULT_ROUTE["model"]
            ),
            "auth_mode": os.environ.get(
                "MAX_SCIENTIST_AUTH_MODE", DEFAULT_ROUTE["auth_mode"]
            ),
            "api_key_env": os.environ.get(
                "MAX_SCIENTIST_API_KEY_ENV", DEFAULT_ROUTE["api_key_env"]
            ),
            "timeout_sec": os.environ.get(
                "MAX_SCIENTIST_TIMEOUT_SEC", str(DEFAULT_ROUTE["timeout_sec"])
            ),
            "fallback_models": [],
        }
    )


def route_config_from_environment() -> dict[str, Any]:
    try:
        from .scientist_provider import saved_primary_route

        saved = saved_primary_route()
        if saved is not None:
            return saved
    except Exception:
        pass
    return _route_config_from_environment_only()


def _route_host(base_url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(base_url))
    except Exception as exc:
        raise ValueError("Scientist base_url is invalid") from exc
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Scientist base_url contains unsupported URL components")
    return str(parsed.hostname or "").casefold()


def sanitize_route_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    unexpected = sorted(set(source) - ALLOWED_ROUTE_KEYS)
    if unexpected:
        raise ValueError(f"Unsupported Scientist route keys: {unexpected}")

    provider = str(source.get("provider") or "").strip()
    base_url = str(source.get("base_url") or "").strip()
    model = str(source.get("model") or "").strip()
    auth_mode = str(source.get("auth_mode") or DEFAULT_ROUTE["auth_mode"]).strip().upper()
    api_key_env = str(source.get("api_key_env") or "").strip()
    fallback_raw = source.get("fallback_models") or []
    if not isinstance(fallback_raw, (list, tuple)):
        raise ValueError("Scientist fallback_models must be a list")
    fallback_models: list[str] = []
    for item in fallback_raw:
        value = str(item or "").strip()
        if value and value != model and value not in fallback_models:
            fallback_models.append(value)
    fallback_models = fallback_models[:8]
    try:
        timeout = int(source.get("timeout_sec") or DEFAULT_ROUTE["timeout_sec"])
    except Exception as exc:
        raise ValueError("Scientist timeout_sec must be an integer") from exc
    if timeout < 1 or timeout > 120:
        raise ValueError("Scientist timeout_sec must be between 1 and 120")
    if auth_mode not in AUTH_MODES:
        raise ValueError("Scientist auth_mode is invalid")

    parsed = urllib.parse.urlsplit(base_url) if base_url else None
    host = _route_host(base_url) if base_url else ""
    scheme = str(parsed.scheme).casefold() if parsed else ""
    if base_url and scheme not in {"http", "https"}:
        raise ValueError("Scientist base_url must use HTTP or HTTPS")
    if auth_mode in {"OLLAMA_LOCAL", "NONE"}:
        if host not in LOCAL_HOSTS:
            raise ValueError("Unauthenticated Scientist routes must be explicit localhost")
        if scheme not in {"http", "https"}:
            raise ValueError("Local Scientist route must use HTTP or HTTPS")
    elif auth_mode == "API_KEY":
        if host in LOCAL_HOSTS:
            if scheme not in {"http", "https"}:
                raise ValueError("Local Scientist route must use HTTP or HTTPS")
        elif base_url and scheme != "https":
            raise ValueError("API-key remote Scientist routes must use HTTPS")
    if api_key_env and not all(ch.isalnum() or ch == "_" for ch in api_key_env):
        raise ValueError("Scientist api_key_env contains invalid characters")

    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "auth_mode": auth_mode,
        "api_key_env": api_key_env,
        "timeout_sec": timeout,
        "fallback_models": fallback_models,
    }


def scientist_route_status(
    route: dict[str, Any] | None = None,
    *,
    credential_override: str | None = None,
) -> dict[str, Any]:
    config = sanitize_route_config(
        route if route is not None else route_config_from_environment()
    )
    if route is None and credential_override is None and config["auth_mode"] == "API_KEY":
        try:
            from .scientist_provider import credential_for_route

            credential_override = credential_for_route(config)
        except Exception:
            credential_override = None
    route_ready = bool(config["provider"] and config["base_url"] and config["model"])
    if config["auth_mode"] == "API_KEY":
        credential_available = bool(
            credential_override
            or (
                os.environ.get(config["api_key_env"], "")
                if config["api_key_env"]
                else ""
            )
        )
    else:
        credential_available = True
    if not route_ready:
        status = "ROUTE_UNAVAILABLE"
    elif config["auth_mode"] == "API_KEY" and not credential_available:
        status = "MISSING_CREDENTIAL"
    else:
        status = "READY"
    return {
        **config,
        "status": status,
        "credential_status": (
            "NOT_REQUIRED"
            if config["auth_mode"] != "API_KEY"
            else "AVAILABLE" if credential_available else "MISSING"
        ),
        "temperature": TEMPERATURE,
        "phase": PHASE,
    }


def _near_miss_sort_key(row: Any) -> tuple[Any, ...]:
    weighted = float(row.weighted_r)
    weighted_ok = math.isfinite(weighted) and (
        weighted >= float(row.min_weighted_r_required)
    )
    return (
        int(row.trades >= row.minimum_trades_required)
        + int(row.profit_factor >= row.min_profit_factor_required)
        + int(row.recovery_factor >= row.min_recovery_factor_required)
        + int(row.expectancy_r >= row.min_expectancy_r_required),
        row.expectancy_r,
        row.profit_factor,
        row.recovery_factor,
        int(weighted_ok),
        weighted if math.isfinite(weighted) else float("-inf"),
        -row.pass_no,
    )


def _failed_gates(row: Any) -> list[str]:
    failed: list[str] = []
    if row.trades < row.minimum_trades_required:
        failed.append("MINIMUM_TRADES")
    if row.profit_factor < row.min_profit_factor_required:
        failed.append("PROFIT_FACTOR")
    if row.recovery_factor < row.min_recovery_factor_required:
        failed.append("RECOVERY_FACTOR")
    if row.expectancy_r < row.min_expectancy_r_required:
        failed.append("MEAN_R")
    if (
        not math.isfinite(float(row.weighted_r))
        or row.weighted_r < row.min_weighted_r_required
    ):
        failed.append("WEIGHTED_R")
    return failed


def build_bounded_payload(
    current_space: dict[str, Any],
    rows: list[Any],
    request: dict[str, Any],
    *,
    source_round: int,
    target_round: int,
) -> dict[str, Any]:
    from .optimizer_core import (
        normalize_optimize_params,
        optimizer_parameter_bounds_for_keys,
        search_space_cardinality,
        validate_search_space,
    )

    current = validate_search_space(current_space)
    bounds = optimizer_parameter_bounds_for_keys(current)
    selected = normalize_optimize_params(request["optimize_params"], bounds=bounds)
    selected_set = set(selected)
    kpi = dict(request["kpi"])
    sample = dict(request["trade_sample"])

    finite = [
        row
        for row in rows
        if all(
            math.isfinite(float(value))
            for value in (
                row.profit_factor,
                row.recovery_factor,
                row.expectancy_r,
            )
        )
    ]
    top = sorted(finite, key=_near_miss_sort_key, reverse=True)[:TOP_PASS_LIMIT]
    top_rows = []
    for row in top:
        weighted: float | None = float(row.weighted_r)
        if not math.isfinite(weighted):
            weighted = None
        top_rows.append(
            {
                "mt5_pass": int(row.pass_no),
                "pf": float(row.profit_factor),
                "rf": float(row.recovery_factor),
                "mean_r": float(row.expectancy_r),
                "weighted_r": weighted,
                "trades": int(row.trades),
                "failed_gates": _failed_gates(row),
                "params": {name: row.params[name] for name in selected},
            }
        )

    return {
        "schema": "MAX_REBUILD_SCIENTIST_BOUNDED_PAYLOAD_V1",
        "phase": PHASE,
        "source_round": int(source_round),
        "target_round": int(target_round),
        "hard_gates": {
            "minimum_trades": int(sample["minimum_trades"]),
            "min_profit_factor": float(kpi["min_profit_factor"]),
            "min_recovery_factor": float(kpi["min_recovery_factor"]),
            "min_mean_r": float(kpi["min_expectancy_r"]),
            "min_weighted_r": float(kpi["min_weighted_r"]),
        },
        "selected_parameters": selected,
        "current_ranges": {name: dict(current[name]) for name in selected},
        "hard_bounds": {
            name: {
                "min": bounds[name][0],
                "max": bounds[name][1],
                "base_step": bounds[name][2],
                "type": bounds[name][3],
            }
            for name in selected
        },
        "search_space_cardinality": search_space_cardinality(current, selected),
        "frozen_nonselected_parameters": [
            name for name in current if name not in selected_set
        ],
        "top_mt5_passes": top_rows,
    }


def system_contract() -> str:
    return (
        "You are a read-only search-space adviser for an MT5 strategy optimizer. "
        "Return one JSON object only. The only allowed top-level fields are ranges "
        "and reason. ranges must contain exactly the supplied selected optimizer "
        "parameters, and each range must contain exactly start, step, stop. "
        "You may not change KPI gates, minimum-trades policy, strategy family count, "
        "EA logic, symbol, relative symbol, timeframe, date range, tick model, "
        "deposit, leverage, optimizer mode, max rounds, selected parameter set, "
        "validation policy, deterministic ranking, promotion, Challenger, or "
        "Champion state. Seven strategy-family weights must remain strictly positive. "
        "Hard bounds and base/minimum steps are deterministic authority. "
        "Do not propose code, tools, commands, or any field outside ranges/reason."
    )


def build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_contract()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    **payload,
                    "response_contract": {
                        "ranges": {
                            "<selected_parameter>": {
                                "start": "number",
                                "step": "number",
                                "stop": "number",
                            }
                        },
                        "reason": "short evidence-based reason",
                    },
                },
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
        },
    ]


def sanitized_request_evidence(
    route: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    clean_route = sanitize_route_config(route)
    request = {
        "schema": "MAX_REBUILD_SCIENTIST_REQUEST_V1",
        "phase": PHASE,
        "temperature": TEMPERATURE,
        "route": clean_route,
        "system_contract": system_contract(),
        "payload": sanitize_recursive(payload),
    }
    request["request_payload_sha256"] = sha256_json(
        {
            "system_contract": request["system_contract"],
            "payload": request["payload"],
        }
    )
    return request


def _extract_json_text(text: str) -> str:
    stripped = str(text or "").strip()
    fence = chr(96) * 3
    if stripped.startswith(fence):
        lines = stripped.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith(fence):
            raise ValueError("Scientist response has malformed JSON fence")
        opener = lines[0].strip().lower()
        if opener not in (fence, fence + "json"):
            raise ValueError("Scientist response fence must be JSON")
        stripped = "\n".join(lines[1:-1]).strip()
    return stripped


def parse_proposal(text: str) -> dict[str, Any]:
    payload = _extract_json_text(text)
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("SCIENTIST_MALFORMED_RESPONSE") from exc
    if not isinstance(obj, dict):
        raise ValueError("SCIENTIST_MALFORMED_RESPONSE")
    extra = sorted(set(obj) - {"ranges", "reason"})
    if extra:
        raise ValueError(
            "SCIENTIST_SCHEMA_REJECTED: unexpected top-level fields "
            + ",".join(extra)
        )
    if "ranges" not in obj:
        raise ValueError("SCIENTIST_SCHEMA_REJECTED: ranges missing")
    reason = obj.get("reason")
    if reason is None or not str(reason).strip():
        obj["reason"] = "Scientist returned a proposal without a narrative reason."
    else:
        obj["reason"] = str(reason).strip()[:1000]
    return obj


def validate_proposal(
    current_space: dict[str, Any],
    proposal: dict[str, Any],
    optimize_params: Any,
) -> tuple[dict[str, dict[str, float | int]], dict[str, dict[str, float | int]]]:
    from .optimizer_core import (
        FAMILY_WEIGHT_PARAMS,
        normalize_optimize_params,
        optimizer_parameter_bounds_for_keys,
        validate_search_space,
    )

    if not isinstance(proposal, dict):
        raise ValueError("SCIENTIST_SCHEMA_REJECTED: proposal must be an object")
    extra_top = sorted(set(proposal) - {"ranges", "reason"})
    if extra_top:
        raise ValueError(
            "SCIENTIST_SCHEMA_REJECTED: unexpected top-level fields "
            + ",".join(extra_top)
        )
    current = validate_search_space(current_space)
    bounds = optimizer_parameter_bounds_for_keys(current)
    selected = normalize_optimize_params(optimize_params, bounds=bounds)
    ranges = proposal.get("ranges")
    if not isinstance(ranges, dict):
        raise ValueError("SCIENTIST_SCHEMA_REJECTED: ranges must be an object")
    if set(ranges) != set(selected):
        raise ValueError(
            "SCIENTIST_PARAMETER_SET_MISMATCH: ranges must contain exactly "
            "the frozen selected parameter set"
        )

    normalized: dict[str, dict[str, float | int]] = {}
    for name in selected:
        spec = ranges[name]
        if not isinstance(spec, dict):
            raise ValueError(
                f"SCIENTIST_SCHEMA_REJECTED: {name} range must be an object"
            )
        if set(spec) != {"start", "step", "stop"}:
            raise ValueError(
                f"SCIENTIST_SCHEMA_REJECTED: {name} requires exactly start/step/stop"
            )
        lo, hi, base_step, kind = bounds[name]
        values: dict[str, float] = {}
        for field in ("start", "step", "stop"):
            raw = spec[field]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(
                    f"SCIENTIST_SCHEMA_REJECTED: {name}.{field} must be numeric"
                )
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(
                    f"SCIENTIST_SCHEMA_REJECTED: {name}.{field} is non-finite"
                )
            values[field] = value
        if values["start"] < lo - 1e-12 or values["stop"] > hi + 1e-12:
            raise ValueError(
                f"SCIENTIST_OUT_OF_BOUNDS: {name} outside {lo}..{hi}"
            )
        if values["stop"] < values["start"]:
            raise ValueError(
                f"SCIENTIST_SCHEMA_REJECTED: {name}.stop below start"
            )
        if values["step"] <= 0:
            raise ValueError(
                f"SCIENTIST_SCHEMA_REJECTED: {name}.step must be positive"
            )
        if values["step"] < float(base_step) - 1e-12:
            raise ValueError(
                f"SCIENTIST_SCHEMA_REJECTED: {name}.step finer than base step "
                f"{base_step}"
            )
        if name in FAMILY_WEIGHT_PARAMS and values["start"] <= 0:
            raise ValueError(
                f"SCIENTIST_OUT_OF_BOUNDS: {name} must remain strictly positive"
            )
        if kind == "int":
            if any(abs(value - round(value)) > 1e-12 for value in values.values()):
                raise ValueError(
                    f"SCIENTIST_SCHEMA_REJECTED: {name} requires integer values"
                )
            normalized[name] = {
                field: int(round(value)) for field, value in values.items()
            }
        else:
            normalized[name] = {
                field: round(value, 10) for field, value in values.items()
            }

    merged = {name: dict(spec) for name, spec in current.items()}
    for name in selected:
        merged[name] = dict(normalized[name])
    return validate_search_space(merged), normalized


def _chat_completion_url(base_url: str) -> str:
    return str(base_url).rstrip("/") + "/chat/completions"


def _classify_network_error(exc: Exception, config: dict[str, Any]) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "SCIENTIST_TIMEOUT"
    if isinstance(exc, urllib.error.HTTPError):
        if int(exc.code) in {401, 403}:
            return "SCIENTIST_AUTHENTICATION_REQUIRED"
        if int(exc.code) == 404:
            return "SCIENTIST_MODEL_UNAVAILABLE"
        if int(exc.code) == 429:
            return "SCIENTIST_RATE_LIMITED"
        if 500 <= int(exc.code) <= 599:
            return "SCIENTIST_PROVIDER_5XX"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "SCIENTIST_TIMEOUT"
        if str(config.get("provider") or "").casefold() == "ollama":
            return "SCIENTIST_OLLAMA_UNAVAILABLE"
    return "SCIENTIST_PROVIDER_ERROR"


def openai_compatible_call(
    route: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float = TEMPERATURE,
    phase: str = PHASE,
    credential: str | None = None,
) -> ScientistCallResult:
    config = sanitize_route_config(route)
    status = scientist_route_status(config, credential_override=credential)
    if status["status"] == "ROUTE_UNAVAILABLE":
        raise ScientistAdvisoryError(
            "SCIENTIST_ROUTE_UNAVAILABLE",
            "Scientist provider/model route is unavailable",
            actual_llm_call=False,
            provenance={
                "configured_provider": config["provider"],
                "configured_model": config["model"],
                "base_url": config["base_url"],
                "auth_mode": config["auth_mode"],
                "phase": phase,
                "temperature": temperature,
                "status": "UNAVAILABLE",
            },
        )
    if status["status"] == "MISSING_CREDENTIAL":
        raise ScientistAdvisoryError(
            "SCIENTIST_CREDENTIAL_MISSING",
            "Scientist API credential is unavailable",
            actual_llm_call=False,
            provenance={
                "configured_provider": config["provider"],
                "configured_model": config["model"],
                "base_url": config["base_url"],
                "auth_mode": config["auth_mode"],
                "phase": phase,
                "temperature": temperature,
                "status": "MISSING_CREDENTIAL",
            },
        )

    if config["auth_mode"] == "API_KEY":
        secret = credential or (
            os.environ.get(config["api_key_env"], "") if config["api_key_env"] else ""
        )
    elif config["auth_mode"] == "OLLAMA_LOCAL":
        secret = "ollama"
    else:
        secret = ""

    headers = {"Content-Type": "application/json"}
    if config["auth_mode"] != "NONE":
        headers["Authorization"] = f"Bearer {secret}"
    body = {
        "model": config["model"],
        "messages": messages,
        "temperature": float(temperature),
    }
    request = urllib.request.Request(
        _chat_completion_url(config["base_url"]),
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = utc_now()
    started_perf = time.perf_counter()
    try:
        with urllib.request.urlopen(
            request,
            timeout=int(config["timeout_sec"]),
        ) as response:
            raw = response.read()
    except Exception as exc:
        category = _classify_network_error(exc, config)
        raise ScientistAdvisoryError(
            category,
            f"{category}: {str(exc)[:300]}",
            actual_llm_call=True,
            provenance={
                "configured_provider": config["provider"],
                "configured_model": config["model"],
                "base_url": config["base_url"],
                "auth_mode": config["auth_mode"],
                "phase": phase,
                "temperature": temperature,
                "timestamp": started,
                "latency_ms": int((time.perf_counter() - started_perf) * 1000),
                "status": "FAIL",
                "error_category": category,
                "http_status": getattr(exc, "code", None),
            },
        ) from exc

    try:
        response_payload = json.loads(raw.decode("utf-8"))
        choices = response_payload.get("choices")
        text = choices[0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty response text")
    except Exception as exc:
        raise ScientistAdvisoryError(
            "SCIENTIST_MALFORMED_RESPONSE",
            "Scientist provider response did not contain chat text",
            actual_llm_call=True,
            provenance={
                "configured_provider": config["provider"],
                "configured_model": config["model"],
                "base_url": config["base_url"],
                "auth_mode": config["auth_mode"],
                "phase": phase,
                "temperature": temperature,
                "timestamp": started,
                "latency_ms": int((time.perf_counter() - started_perf) * 1000),
                "status": "MALFORMED_PROVIDER_RESPONSE",
            },
        ) from exc

    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        usage = None
    actual_model = str(response_payload.get("model") or config["model"])
    return ScientistCallResult(
        text=text,
        provenance={
            "configured_provider": config["provider"],
            "configured_model": config["model"],
            "actual_provider": config["provider"],
            "actual_model": actual_model,
            "base_url": config["base_url"],
            "auth_mode": config["auth_mode"],
            "phase": phase,
            "temperature": float(temperature),
            "timestamp": started,
            "latency_ms": int((time.perf_counter() - started_perf) * 1000),
            "status": "PASS",
            "usage": sanitize_recursive(usage) if usage is not None else None,
            "cost": None,
            "fallback_route_used": False,
        },
    )


def propose_optimizer_ranges(
    route: dict[str, Any],
    payload: dict[str, Any],
    *,
    credential: str | None = None,
    transport: Callable[
        [dict[str, Any], list[dict[str, str]]], ScientistCallResult
    ] | None = None,
) -> ScientistCallResult:
    resolved_credential = credential
    if resolved_credential is None:
        try:
            from .scientist_provider import credential_for_route

            resolved_credential = credential_for_route(route)
        except Exception:
            resolved_credential = None
    call = transport or (
        lambda cfg, messages: openai_compatible_call(
            cfg,
            messages,
            temperature=TEMPERATURE,
            credential=resolved_credential,
        )
    )
    return call(sanitize_route_config(route), build_messages(payload))
