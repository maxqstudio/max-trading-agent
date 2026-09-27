from __future__ import annotations

import copy
import ctypes
import ctypes.wintypes
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .optimizer_scientist import (
    DEFAULT_ROUTE,
    _route_config_from_environment_only,
    sanitize_route_config,
    scientist_route_status,
)

SETTINGS_SCHEMA = "MAX_REBUILD_USER_SETTINGS_V1"
DEFAULT_TIMEOUT_SEC = 60
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
CHAT_CONTEXTS = {
    "AUTO",
    "STRATEGY",
    "OPTIMIZER",
    "CHALLENGERS",
    "CHAMPION",
    "PROJECT CONTRACT",
}
PERSISTENT_UI_KEYS = {
    "left_nav_open",
    "scientist_drawer_open",
    "scientist_chat_model",
    "scientist_context",
}
FALLBACK_SAFE_CATEGORIES = {
    "SCIENTIST_TIMEOUT",
    "SCIENTIST_OLLAMA_UNAVAILABLE",
    "SCIENTIST_MODEL_UNAVAILABLE",
    "SCIENTIST_PROVIDER_ERROR",
    "SCIENTIST_PROVIDER_5XX",
    "SCIENTIST_RATE_LIMITED",
}

PROVIDERS: dict[str, dict[str, Any]] = {
    "gemini": {
        "key": "gemini",
        "display_name": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "requires_api_key": True,
        "description": "Gemini OpenAI-compatible API",
    },
    "openai": {
        "key": "openai",
        "display_name": "OpenAI",
        "default_base_url": "https://api.openai.com/v1/",
        "requires_api_key": True,
        "description": "OpenAI API",
    },
    "groq": {
        "key": "groq",
        "display_name": "Groq",
        "default_base_url": "https://api.groq.com/openai/v1/",
        "requires_api_key": True,
        "description": "Groq OpenAI-compatible API",
    },
    "openrouter": {
        "key": "openrouter",
        "display_name": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1/",
        "requires_api_key": True,
        "description": "OpenRouter OpenAI-compatible API",
    },
    "deepseek": {
        "key": "deepseek",
        "display_name": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/",
        "requires_api_key": True,
        "description": "DeepSeek OpenAI-compatible API",
    },
    "ollama": {
        "key": "ollama",
        "display_name": "Ollama",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "requires_api_key": False,
        "description": "Local Ollama daemon; Ollama owns local/cloud authentication",
    },
    "custom": {
        "key": "custom",
        "display_name": "Custom OpenAI-compatible",
        "default_base_url": "",
        "requires_api_key": True,
        "description": "Owner-supplied OpenAI-compatible endpoint",
    },
}


class ProviderSettingsError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def settings_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "MAX_REBUILD"
    return Path.home() / "AppData" / "Local" / "MAX_REBUILD"


def _paths(root: Path | None = None) -> tuple[Path, Path, Path]:
    base = Path(root) if root is not None else settings_root()
    return (
        base / "settings.json",
        base / "settings.backup.json",
        base / "llm_api_key.dpapi",
    )


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise ProviderSettingsError("SCIENTIST_SECURE_STORAGE_UNAVAILABLE")
    source, keepalive = _blob(data)
    target = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source),
        "MAX REBUILD LLM API key",
        None,
        None,
        None,
        0x1,
        ctypes.byref(target),
    )
    _ = keepalive
    if not ok:
        raise ProviderSettingsError("SCIENTIST_SECURE_STORAGE_FAILED")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise ProviderSettingsError("SCIENTIST_SECURE_STORAGE_UNAVAILABLE")
    source, keepalive = _blob(data)
    target = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(target),
    )
    _ = keepalive
    if not ok:
        raise ProviderSettingsError("SCIENTIST_SECURE_STORAGE_READ_FAILED")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        last_error: Exception | None = None
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(min(0.025 * (attempt + 1), 0.25))
        if last_error is not None:
            raise last_error
        raise RuntimeError("SCIENTIST_SETTINGS_ATOMIC_WRITE_FAILED")
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _store_secret(secret: str, *, root: Path | None = None) -> None:
    value = str(secret)
    if not value:
        raise ProviderSettingsError("SCIENTIST_CREDENTIAL_REQUIRED")
    _settings, _backup, secret_path = _paths(root)
    _atomic_write(secret_path, _dpapi_protect(value.encode("utf-8")))


def _load_secret(*, root: Path | None = None) -> str:
    _settings, _backup, secret_path = _paths(root)
    if not secret_path.is_file():
        return ""
    try:
        return _dpapi_unprotect(secret_path.read_bytes()).decode("utf-8")
    except ProviderSettingsError:
        raise
    except Exception as exc:
        raise ProviderSettingsError(
            "SCIENTIST_SECURE_STORAGE_READ_FAILED"
        ) from exc


def _clear_secret(*, root: Path | None = None) -> None:
    _settings, _backup, secret_path = _paths(root)
    try:
        secret_path.unlink(missing_ok=True)
    except TypeError:
        if secret_path.exists():
            secret_path.unlink()


def provider_catalog() -> list[dict[str, Any]]:
    return [copy.deepcopy(PROVIDERS[key]) for key in PROVIDERS]


def _normalize_base_url(value: str) -> str:
    base = str(value or "").strip()
    if not base:
        return ""
    for suffix in ("/chat/completions", "/models"):
        trimmed = base.rstrip("/")
        if trimmed.endswith(suffix):
            base = trimmed[: -len(suffix)]
            break
    return base.rstrip("/")


def _parse_url(value: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(str(value))
    except Exception as exc:
        raise ProviderSettingsError("SCIENTIST_PROVIDER_URL_INVALID") from exc
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderSettingsError("SCIENTIST_PROVIDER_URL_INVALID")
    return parsed


def _is_local_host(host: str) -> bool:
    return str(host or "").casefold() in LOCAL_HOSTS


def _validate_remote_resolution(parsed: urllib.parse.SplitResult) -> None:
    host = str(parsed.hostname or "").casefold()
    if _is_local_host(host):
        return
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(
                host,
                parsed.port or 443,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ProviderSettingsError(
                "SCIENTIST_PROVIDER_HOST_UNRESOLVED"
            ) from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
    if not addresses:
        raise ProviderSettingsError("SCIENTIST_PROVIDER_HOST_UNRESOLVED")
    if any(not address.is_global for address in addresses):
        raise ProviderSettingsError("SCIENTIST_REMOTE_PRIVATE_HOST_REJECTED")


def _dedupe_models(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        model = str(item or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        result.append(model)
    return result[:100]


def _provider_requires_api_key(
    provider_key: str,
    auth_mode: str,
 ) -> bool:
    if provider_key == "ollama":
        return False
    if provider_key == "custom":
        return auth_mode == "API_KEY"
    return True


def normalize_provider_config(raw: dict[str, Any]) -> dict[str, Any]:
    provider_key = str(raw.get("provider_key") or "").strip().casefold()
    if provider_key not in PROVIDERS:
        raise ProviderSettingsError("SCIENTIST_PROVIDER_TYPE_INVALID")
    spec = PROVIDERS[provider_key]
    base_url = _normalize_base_url(
        str(raw.get("base_url") or spec["default_base_url"])
    )
    if not base_url:
        raise ProviderSettingsError("SCIENTIST_PROVIDER_URL_REQUIRED")
    parsed = _parse_url(base_url)
    host = str(parsed.hostname or "").casefold()

    requested_auth = str(raw.get("auth_mode") or "").strip().upper()
    if provider_key == "ollama":
        auth_mode = "OLLAMA_LOCAL"
        if not _is_local_host(host):
            raise ProviderSettingsError("SCIENTIST_OLLAMA_LOCALHOST_REQUIRED")
    elif provider_key == "custom":
        auth_mode = requested_auth or "API_KEY"
        if auth_mode not in {"API_KEY", "NONE"}:
            raise ProviderSettingsError("SCIENTIST_AUTH_MODE_INVALID")
        if _is_local_host(host):
            if parsed.scheme.casefold() not in {"http", "https"}:
                raise ProviderSettingsError("SCIENTIST_PROVIDER_URL_INVALID")
        else:
            if parsed.scheme.casefold() != "https":
                raise ProviderSettingsError("SCIENTIST_REMOTE_HTTPS_REQUIRED")
            if auth_mode != "API_KEY":
                raise ProviderSettingsError("SCIENTIST_REMOTE_AUTH_REQUIRED")
            _validate_remote_resolution(parsed)
    else:
        expected = _normalize_base_url(spec["default_base_url"])
        if base_url != expected:
            raise ProviderSettingsError("SCIENTIST_PROVIDER_ENDPOINT_LOCKED")
        auth_mode = "API_KEY"
        if parsed.scheme.casefold() != "https":
            raise ProviderSettingsError("SCIENTIST_REMOTE_HTTPS_REQUIRED")

    try:
        timeout_sec = int(raw.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
    except Exception as exc:
        raise ProviderSettingsError("SCIENTIST_TIMEOUT_INVALID") from exc
    if not 1 <= timeout_sec <= 120:
        raise ProviderSettingsError("SCIENTIST_TIMEOUT_INVALID")

    primary_model = str(raw.get("primary_model") or "").strip()
    models = _dedupe_models(raw.get("models"))
    autonomous_fallback = _dedupe_models(raw.get("autonomous_fallback"))
    chat_fallback = _dedupe_models(raw.get("chat_fallback"))
    if primary_model:
        autonomous_fallback = [
            model for model in autonomous_fallback if model != primary_model
        ]
    return {
        "provider_key": provider_key,
        "base_url": base_url,
        "auth_mode": auth_mode,
        "timeout_sec": timeout_sec,
        "primary_model": primary_model,
        "autonomous_fallback": autonomous_fallback,
        "chat_fallback": chat_fallback,
        "models": models,
    }


def _default_ui(primary_model: str = "") -> dict[str, Any]:
    return {
        "left_nav_open": True,
        "scientist_drawer_open": True,
        "scientist_chat_model": str(primary_model or ""),
        "scientist_context": "AUTO",
    }


def _default_payload_from_environment() -> dict[str, Any]:
    route = _route_config_from_environment_only()
    provider_key = str(route.get("provider") or "custom").casefold()
    if provider_key not in PROVIDERS:
        provider_key = "custom"
    provider = normalize_provider_config(
        {
            "provider_key": provider_key,
            "base_url": route.get("base_url") or DEFAULT_ROUTE["base_url"],
            "auth_mode": route.get("auth_mode") or "API_KEY",
            "timeout_sec": route.get("timeout_sec") or DEFAULT_TIMEOUT_SEC,
            "primary_model": route.get("model") or "",
            "autonomous_fallback": [],
            "chat_fallback": [],
            "models": [],
        }
    )
    return {
        "schema": SETTINGS_SCHEMA,
        "provider_explicit": False,
        "provider": provider,
        "ui_state": _default_ui(provider["primary_model"]),
    }


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != SETTINGS_SCHEMA:
        raise ProviderSettingsError("SCIENTIST_SETTINGS_SCHEMA_INVALID")
    provider = normalize_provider_config(
        dict(payload.get("provider") or {})
    )
    raw_ui = (
        payload.get("ui_state")
        if isinstance(payload.get("ui_state"), dict)
        else {}
    )
    ui = _default_ui(provider["primary_model"])
    for key in PERSISTENT_UI_KEYS:
        if key in raw_ui:
            ui[key] = raw_ui[key]
    ui["left_nav_open"] = bool(ui["left_nav_open"])
    ui["scientist_drawer_open"] = bool(ui["scientist_drawer_open"])
    ui["scientist_chat_model"] = str(
        ui["scientist_chat_model"] or ""
    )
    scope = str(ui["scientist_context"] or "AUTO").upper()
    ui["scientist_context"] = (
        scope if scope in CHAT_CONTEXTS else "AUTO"
    )
    return {
        "schema": SETTINGS_SCHEMA,
        "provider_explicit": bool(payload.get("provider_explicit", False)),
        "provider": provider,
        "ui_state": ui,
    }


def load_settings(
    *,
    root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings_path, backup_path, _secret = _paths(root)
    for candidate, is_backup in (
        (settings_path, False),
        (backup_path, True),
    ):
        if not candidate.is_file():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            payload = _normalize_payload(raw)
            if not payload["provider_explicit"]:
                legacy = _default_payload_from_environment()
                payload["provider"] = legacy["provider"]
            return payload, {
                "source": (
                    "BACKUP"
                    if is_backup and payload["provider_explicit"]
                    else "SAVED_SETTINGS"
                    if payload["provider_explicit"]
                    else "SAVED_UI_LEGACY_ENV"
                ),
                "recovered_from_backup": is_backup,
                "path": str(candidate),
            }
        except Exception:
            continue
    return _default_payload_from_environment(), {
        "source": "LEGACY_ENV",
        "recovered_from_backup": False,
        "path": None,
    }


def _save_payload(
    payload: dict[str, Any],
    *,
    root: Path | None = None,
) -> None:
    settings_path, backup_path, _secret = _paths(root)
    normalized = _normalize_payload(payload)
    if settings_path.is_file():
        try:
            current = json.loads(
                settings_path.read_text(encoding="utf-8")
            )
            _normalize_payload(current)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(settings_path, backup_path)
        except Exception:
            # Keep last known-good backup if current settings are corrupt.
            pass
    encoded = (
        json.dumps(
            normalized,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_write(settings_path, encoded)


def credential_status(*, root: Path | None = None) -> str:
    try:
        return (
            "CONFIGURED"
            if _load_secret(root=root)
            else "NOT_CONFIGURED"
        )
    except ProviderSettingsError:
        return "UNAVAILABLE"


def get_provider_settings(
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    payload, meta = load_settings(root=root)
    provider = dict(payload["provider"])
    needs_key = _provider_requires_api_key(
        provider["provider_key"],
        provider["auth_mode"],
    )
    secret_status = (
        "NOT_REQUIRED"
        if not needs_key
        else credential_status(root=root)
    )
    primary = provider["primary_model"]
    status = "ROUTE_UNAVAILABLE"
    if primary:
        route = route_for_model(provider, primary)
        status = scientist_route_status(
            route,
            credential_override=(
                "configured"
                if secret_status == "CONFIGURED"
                else None
            ),
        )["status"]
    return {
        **provider,
        "provider": copy.deepcopy(
            PROVIDERS[provider["provider_key"]]
        ),
        "credential_status": secret_status,
        "status": status,
        "source": meta["source"],
        "recovered_from_backup": meta["recovered_from_backup"],
        "ui_state": copy.deepcopy(payload["ui_state"]),
    }


def save_provider_settings(
    raw: dict[str, Any],
    *,
    api_key: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    payload, meta = load_settings(root=root)
    previous = dict(payload["provider"])
    provider = normalize_provider_config(raw)
    if not provider["primary_model"]:
        raise ProviderSettingsError("SCIENTIST_MODEL_REQUIRED")
    needs_key = _provider_requires_api_key(
        provider["provider_key"],
        provider["auth_mode"],
    )
    same_credential_target = (
        previous.get("provider_key") == provider["provider_key"]
        and previous.get("base_url") == provider["base_url"]
        and previous.get("auth_mode") == provider["auth_mode"]
    )
    if needs_key:
        supplied = str(api_key or "")
        if supplied:
            _store_secret(supplied, root=root)
        elif not same_credential_target or not _load_secret(root=root):
            raise ProviderSettingsError("SCIENTIST_CREDENTIAL_REQUIRED")
    else:
        _clear_secret(root=root)

    ui = dict(payload.get("ui_state") or {})
    current_chat_model = str(ui.get("scientist_chat_model") or "").strip()
    if (
        not current_chat_model
        or (
            provider["models"]
            and current_chat_model not in provider["models"]
        )
    ):
        ui["scientist_chat_model"] = provider["primary_model"]
    payload = {
        "schema": SETTINGS_SCHEMA,
        "provider_explicit": True,
        "provider": provider,
        "ui_state": ui,
    }
    _save_payload(payload, root=root)
    result = get_provider_settings(root=root)
    result["previous_source"] = meta["source"]
    return result


def save_ui_state(
    patch: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    payload, _meta = load_settings(root=root)
    ui = dict(payload.get("ui_state") or {})
    for key, value in patch.items():
        if key not in PERSISTENT_UI_KEYS:
            continue
        ui[key] = value
    payload["ui_state"] = ui
    _save_payload(payload, root=root)
    return get_provider_settings(root=root)["ui_state"]


def route_for_model(
    provider: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    normalized = normalize_provider_config(provider)
    selected = str(model or "").strip()
    if not selected:
        raise ProviderSettingsError("SCIENTIST_MODEL_REQUIRED")
    return sanitize_route_config(
        {
            "provider": normalized["provider_key"],
            "base_url": normalized["base_url"],
            "model": selected,
            "auth_mode": normalized["auth_mode"],
            "api_key_env": "",
            "timeout_sec": normalized["timeout_sec"],
        }
    )


def saved_primary_route(
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    payload, _meta = load_settings(root=root)
    if not payload["provider_explicit"]:
        return None
    provider = dict(payload["provider"])
    model = str(provider.get("primary_model") or "").strip()
    if not model:
        return None
    return sanitize_route_config(
        {
            **route_for_model(provider, model),
            "fallback_models": list(
                provider.get("autonomous_fallback") or []
            ),
        }
    )
def resolve_provider_runtime(
    *,
    model: str | None = None,
    purpose: str = "chat",
    root: Path | None = None,
) -> tuple[dict[str, Any], str | None, str]:
    payload, meta = load_settings(root=root)
    if not payload["provider_explicit"]:
        route = _route_config_from_environment_only()
        if model:
            route = sanitize_route_config(
                {**route, "model": str(model).strip()}
            )
        secret = (
            os.environ.get(
                str(route.get("api_key_env") or ""),
                "",
            )
            if route.get("auth_mode") == "API_KEY"
            else ""
        )
        return route, secret or None, "LEGACY_ENV"

    provider = dict(payload["provider"])
    if model is not None:
        selected = str(model).strip()
    elif purpose == "autonomous":
        selected = str(provider.get("primary_model") or "").strip()
    else:
        selected = str(
            payload.get("ui_state", {}).get("scientist_chat_model")
            or provider.get("primary_model")
            or ""
        ).strip()
    route = route_for_model(provider, selected)
    secret = (
        _load_secret(root=root)
        if _provider_requires_api_key(
            provider["provider_key"],
            provider["auth_mode"],
        )
        else ""
    )
    return route, secret or None, meta["source"]


def autonomous_route_configs(
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    payload, _meta = load_settings(root=root)
    if not payload["provider_explicit"]:
        return [_route_config_from_environment_only()]
    provider = dict(payload["provider"])
    models = _dedupe_models(
        [
            provider.get("primary_model"),
            *(provider.get("autonomous_fallback") or []),
        ]
    )
    return [route_for_model(provider, model) for model in models]


def chat_fallback_models(
    *,
    root: Path | None = None,
) -> list[str]:
    payload, _meta = load_settings(root=root)
    if not payload["provider_explicit"]:
        return []
    return list(payload["provider"].get("chat_fallback") or [])


def credential_for_route(
    route: dict[str, Any],
    *,
    root: Path | None = None,
) -> str | None:
    payload, _meta = load_settings(root=root)
    if not payload["provider_explicit"]:
        env_name = str(route.get("api_key_env") or "")
        return os.environ.get(env_name, "") or None if env_name else None
    provider = payload["provider"]
    if (
        str(route.get("provider") or "").casefold() != provider["provider_key"]
        or _normalize_base_url(str(route.get("base_url") or ""))
        != provider["base_url"]
    ):
        return None
    if not _provider_requires_api_key(
        provider["provider_key"],
        provider["auth_mode"],
    ):
        return None
    return _load_secret(root=root) or None


def _headers(
    secret: str | None,
    auth_mode: str,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if auth_mode == "API_KEY" and secret:
        headers["Authorization"] = f"Bearer {secret}"
    elif auth_mode == "OLLAMA_LOCAL":
        headers["Authorization"] = "Bearer ollama"
    return headers
def _json_get(
    url: str,
    *,
    secret: str | None,
    auth_mode: str,
    timeout_sec: int,
) -> dict[str, Any]:
    parsed = _parse_url(url)
    if not _is_local_host(str(parsed.hostname or "")):
        _validate_remote_resolution(parsed)
    request = urllib.request.Request(
        url,
        headers=_headers(secret, auth_mode),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        if exc.code in {401, 403}:
            code = "SCIENTIST_AUTHENTICATION_REQUIRED"
        elif exc.code == 404:
            code = "SCIENTIST_MODEL_ENDPOINT_UNAVAILABLE"
        elif exc.code == 429:
            code = "SCIENTIST_RATE_LIMITED"
        elif 500 <= int(exc.code) <= 599:
            code = "SCIENTIST_PROVIDER_5XX"
        else:
            code = "SCIENTIST_PROVIDER_ERROR"
        raise ProviderSettingsError(
            code,
            f"HTTP {exc.code}: {detail or exc.reason}",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ProviderSettingsError(
            "SCIENTIST_TIMEOUT",
            "Provider timeout",
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderSettingsError(
            (
                "SCIENTIST_OLLAMA_UNAVAILABLE"
                if _is_local_host(str(parsed.hostname or ""))
                else "SCIENTIST_PROVIDER_ERROR"
            ),
            str(exc.reason),
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProviderSettingsError(
            "SCIENTIST_PROVIDER_RESPONSE_INVALID"
        ) from exc


def _extract_models(body: dict[str, Any]) -> list[str]:
    result: list[str] = []
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                result.append(str(item["id"]))
            elif isinstance(item, str):
                result.append(item)
    models = body.get("models")
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict):
                value = item.get("name") or item.get("model") or item.get("id")
                if value:
                    result.append(str(value))
            elif isinstance(item, str):
                result.append(item)
    return result


def _is_chat_model(model: str) -> bool:
    lowered = str(model).casefold()
    blocked = (
        "embedding",
        "embed-",
        "text-embedding",
        "moderation",
        "imagen",
        "image-generation",
        "gpt-image",
        "veo",
        "lyria",
        "tts",
        "transcribe",
        "whisper",
        "speech",
        "rerank",
        "reranker",
    )
    return not any(token in lowered for token in blocked)
def _model_discovery_url(provider: dict[str, Any]) -> str:
    base = provider["base_url"].rstrip("/")
    if provider["provider_key"] == "deepseek":
        parsed = urllib.parse.urlsplit(base)
        if parsed.path in {"", "/"}:
            return base + "/v1/models"
    return base + "/models"


def discover_models(
    raw: dict[str, Any],
    *,
    api_key: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    provider = normalize_provider_config(raw)
    needs_key = _provider_requires_api_key(
        provider["provider_key"],
        provider["auth_mode"],
    )
    secret = str(api_key or "")
    if needs_key and not secret:
        payload, meta = load_settings(root=root)
        saved = payload["provider"]
        same_target = (
            payload["provider_explicit"]
            and saved["provider_key"] == provider["provider_key"]
            and saved["base_url"] == provider["base_url"]
            and saved["auth_mode"] == provider["auth_mode"]
        )
        if same_target:
            secret = _load_secret(root=root)
    if needs_key and not secret:
        raise ProviderSettingsError("SCIENTIST_CREDENTIAL_REQUIRED")

    started = time.perf_counter()
    models_url = _model_discovery_url(provider)
    try:
        body = _json_get(
            models_url,
            secret=secret or None,
            auth_mode=provider["auth_mode"],
            timeout_sec=provider["timeout_sec"],
        )
        models = _extract_models(body)
        source = "OPENAI_MODELS"
    except ProviderSettingsError as first_error:
        if provider["provider_key"] != "ollama":
            raise
        root_url = provider["base_url"].rstrip("/")
        if root_url.endswith("/v1"):
            root_url = root_url[:-3]
        body = _json_get(
            root_url.rstrip("/") + "/api/tags",
            secret=None,
            auth_mode="OLLAMA_LOCAL",
            timeout_sec=provider["timeout_sec"],
        )
        models = _extract_models(body)
        source = "OLLAMA_TAGS"
        if not models:
            raise first_error

    filtered = sorted(
        {
            model.strip()
            for model in models
            if model.strip() and _is_chat_model(model)
        },
        key=str.casefold,
    )
    if not filtered:
        raise ProviderSettingsError("SCIENTIST_NO_CHAT_MODELS")
    return {
        "status": "CONNECTED",
        "provider_key": provider["provider_key"],
        "base_url": provider["base_url"],
        "models": filtered,
        "model_count": len(filtered),
        "discovery_source": source,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "credential_status": "NOT_REQUIRED" if not needs_key else "CONFIGURED",
    }


def fallback_error_category(exc: BaseException) -> str | None:
    category = str(getattr(exc, "category", "") or "").upper()
    if category in FALLBACK_SAFE_CATEGORIES:
        return category
    code = str(getattr(exc, "code", "") or "").upper()
    if code in FALLBACK_SAFE_CATEGORIES:
        return code
    return None
