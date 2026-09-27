from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from max_backend.scientist_provider import (
    PROVIDERS,
    ProviderSettingsError,
    autonomous_route_configs,
    chat_fallback_models,
    discover_models,
    get_provider_settings,
    load_settings,
    normalize_provider_config,
    provider_catalog,
    resolve_provider_runtime,
    save_provider_settings,
    save_ui_state,
)


def ollama_config(**overrides):
    value = {
        "provider_key": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "auth_mode": "OLLAMA_LOCAL",
        "timeout_sec": 60,
        "primary_model": "gpt-oss:120b-cloud",
        "autonomous_fallback": ["gemma4:cloud"],
        "chat_fallback": [],
        "models": ["gpt-oss:120b-cloud", "gemma4:cloud"],
    }
    value.update(overrides)
    return value


def api_config(provider_key="gemini", **overrides):
    spec = PROVIDERS[provider_key]
    value = {
        "provider_key": provider_key,
        "base_url": spec["default_base_url"],
        "auth_mode": "API_KEY",
        "timeout_sec": 45,
        "primary_model": "model-a",
        "autonomous_fallback": ["model-b"],
        "chat_fallback": ["model-c"],
        "models": ["model-a", "model-b", "model-c"],
    }
    value.update(overrides)
    return value
def test_provider_catalog_matches_required_presets() -> None:
    catalog = {item["key"]: item for item in provider_catalog()}
    assert list(catalog) == [
        "gemini",
        "openai",
        "groq",
        "openrouter",
        "deepseek",
        "ollama",
        "custom",
    ]
    assert catalog["gemini"]["default_base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert catalog["openai"]["default_base_url"] == "https://api.openai.com/v1/"
    assert catalog["groq"]["default_base_url"] == "https://api.groq.com/openai/v1/"
    assert catalog["openrouter"]["default_base_url"] == "https://openrouter.ai/api/v1/"
    assert catalog["deepseek"]["default_base_url"] == "https://api.deepseek.com/"
    assert catalog["ollama"]["default_base_url"] == "http://127.0.0.1:11434/v1"
    assert catalog["ollama"]["requires_api_key"] is False


def test_ollama_no_secret_persists_models_and_ui(tmp_path: Path) -> None:
    saved = save_provider_settings(ollama_config(), root=tmp_path)
    assert saved["provider_key"] == "ollama"
    assert saved["credential_status"] == "NOT_REQUIRED"
    assert saved["primary_model"] == "gpt-oss:120b-cloud"
    assert saved["autonomous_fallback"] == ["gemma4:cloud"]
    assert saved["chat_fallback"] == []
    assert not (tmp_path / "llm_api_key.dpapi").exists()

    ui = save_ui_state(
        {
            "left_nav_open": False,
            "scientist_drawer_open": True,
            "scientist_chat_model": "gemma4:cloud",
            "scientist_context": "CHAMPION",
            "api_key": "must-not-persist",
        },
        root=tmp_path,
    )
    assert ui["left_nav_open"] is False
    assert ui["scientist_chat_model"] == "gemma4:cloud"
    assert ui["scientist_context"] == "CHAMPION"
    text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "must-not-persist" not in text
    assert "api_key" not in text


@pytest.mark.parametrize(
    "provider_key",
    ["gemini", "openai", "groq", "openrouter", "deepseek"],
)
def test_known_remote_providers_require_api_key(
    tmp_path: Path,
    provider_key: str,
) -> None:
    with pytest.raises(ProviderSettingsError) as caught:
        save_provider_settings(api_config(provider_key), root=tmp_path)
    assert caught.value.code == "SCIENTIST_CREDENTIAL_REQUIRED"
def test_custom_url_validation_and_local_no_auth() -> None:
    custom_local = normalize_provider_config(
        api_config(
            "custom",
            base_url="http://localhost:9000/v1",
            auth_mode="NONE",
        )
    )
    assert custom_local["auth_mode"] == "NONE"

    with pytest.raises(ProviderSettingsError) as unsafe:
        normalize_provider_config(
            api_config(
                "custom",
                base_url="file:///tmp/model",
                auth_mode="NONE",
            )
        )
    assert unsafe.value.code == "SCIENTIST_PROVIDER_URL_INVALID"

    with pytest.raises(ProviderSettingsError) as remote_none:
        normalize_provider_config(
            api_config(
                "custom",
                base_url="https://example.com/v1",
                auth_mode="NONE",
            )
        )
    assert remote_none.value.code == "SCIENTIST_REMOTE_AUTH_REQUIRED"

    with pytest.raises(ProviderSettingsError) as insecure:
        normalize_provider_config(
            api_config(
                "custom",
                base_url="http://8.8.8.8/v1",
                auth_mode="API_KEY",
            )
        )
    assert insecure.value.code == "SCIENTIST_REMOTE_HTTPS_REQUIRED"

    with pytest.raises(ProviderSettingsError) as remote_ollama:
        normalize_provider_config(
            ollama_config(base_url="https://example.com/v1")
        )
    assert remote_ollama.value.code == "SCIENTIST_OLLAMA_LOCALHOST_REQUIRED"


def test_dpapi_secret_absent_from_json_and_sqlite(tmp_path: Path) -> None:
    secret = "provider-secret-fixture-4f33a6d8"
    saved = save_provider_settings(
        api_config("openai"),
        api_key=secret,
        root=tmp_path,
    )
    assert saved["credential_status"] == "CONFIGURED"
    assert "api_key" not in saved
    assert secret not in json.dumps(saved)
    settings_bytes = (tmp_path / "settings.json").read_bytes()
    encrypted = (tmp_path / "llm_api_key.dpapi").read_bytes()
    assert secret.encode() not in settings_bytes
    assert secret.encode() not in encrypted
    assert list(tmp_path.rglob("*.db")) == []

    route, resolved, source = resolve_provider_runtime(
        model="model-a",
        root=tmp_path,
    )
    assert route["provider"] == "openai"
    assert resolved == secret
    assert source == "SAVED_SETTINGS"
def test_settings_backup_recovers_without_erasing_owner_config(
    tmp_path: Path,
) -> None:
    save_provider_settings(ollama_config(), root=tmp_path)
    save_provider_settings(
        ollama_config(primary_model="gemma4:cloud"),
        root=tmp_path,
    )
    assert (tmp_path / "settings.backup.json").is_file()

    (tmp_path / "settings.json").write_text("{corrupt", encoding="utf-8")
    payload, meta = load_settings(root=tmp_path)
    assert meta["source"] == "BACKUP"
    assert meta["recovered_from_backup"] is True
    assert payload["provider"]["primary_model"] == "gpt-oss:120b-cloud"

    save_ui_state({"left_nav_open": False}, root=tmp_path)
    payload2, meta2 = load_settings(root=tmp_path)
    assert meta2["source"] == "SAVED_SETTINGS"
    assert payload2["provider"]["primary_model"] == "gpt-oss:120b-cloud"


def test_saved_settings_override_legacy_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_SCIENTIST_MODEL", "legacy-model")
    save_provider_settings(ollama_config(), root=tmp_path)
    route, secret, source = resolve_provider_runtime(root=tmp_path)
    assert route["model"] == "gpt-oss:120b-cloud"
    assert route["provider"] == "ollama"
    assert secret is None
    assert source == "SAVED_SETTINGS"


def test_autonomous_and_chat_fallback_are_separate(tmp_path: Path) -> None:
    save_provider_settings(
        ollama_config(
            autonomous_fallback=["gemma4:cloud", "lfm2.5:8b"],
            chat_fallback=["glm-5.3-flash:cloud"],
        ),
        root=tmp_path,
    )
    routes = autonomous_route_configs(root=tmp_path)
    assert [row["model"] for row in routes] == [
        "gpt-oss:120b-cloud",
        "gemma4:cloud",
        "lfm2.5:8b",
    ]
    assert chat_fallback_models(root=tmp_path) == [
        "glm-5.3-flash:cloud"
    ]
def test_openai_models_discovery_filters_non_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def fake_get(url, **_kwargs):
        seen.append(url)
        return {
            "data": [
                {"id": "chat-a"},
                {"id": "text-embedding-3-small"},
                {"id": "chat-b"},
            ]
        }

    monkeypatch.setattr(
        "max_backend.scientist_provider._json_get",
        fake_get,
    )
    result = discover_models(
        ollama_config(models=[]),
        root=tmp_path,
    )
    assert result["status"] == "CONNECTED"
    assert result["models"] == ["chat-a", "chat-b"]
    assert result["discovery_source"] == "OPENAI_MODELS"
    assert seen == ["http://127.0.0.1:11434/v1/models"]


def test_ollama_tags_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def fake_get(url, **_kwargs):
        seen.append(url)
        if url.endswith("/v1/models"):
            raise ProviderSettingsError(
                "SCIENTIST_MODEL_ENDPOINT_UNAVAILABLE"
            )
        return {
            "models": [
                {"name": "gpt-oss:120b-cloud"},
                {"name": "gemma4:cloud"},
            ]
        }

    monkeypatch.setattr(
        "max_backend.scientist_provider._json_get",
        fake_get,
    )
    result = discover_models(
        ollama_config(models=[]),
        root=tmp_path,
    )
    assert result["models"] == [
        "gemma4:cloud",
        "gpt-oss:120b-cloud",
    ]
    assert result["discovery_source"] == "OLLAMA_TAGS"
    assert seen == [
        "http://127.0.0.1:11434/v1/models",
        "http://127.0.0.1:11434/api/tags",
    ]


def test_ui_state_persistence_does_not_promote_legacy_env_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_SCIENTIST_MODEL", "legacy-model-a")
    save_ui_state(
        {
            "left_nav_open": False,
            "scientist_drawer_open": True,
        },
        root=tmp_path,
    )
    payload, meta = load_settings(root=tmp_path)
    assert payload["provider_explicit"] is False
    assert meta["source"] == "SAVED_UI_LEGACY_ENV"

    monkeypatch.setenv("MAX_SCIENTIST_MODEL", "legacy-model-b")
    route, _secret, source = resolve_provider_runtime(root=tmp_path)
    assert source == "LEGACY_ENV"
    assert route["model"] == "legacy-model-b"


def test_deepseek_uses_v1_models_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def fake_get(url, **_kwargs):
        seen.append(url)
        return {"data": [{"id": "deepseek-chat"}]}

    monkeypatch.setattr(
        "max_backend.scientist_provider._json_get",
        fake_get,
    )
    result = discover_models(
        api_config("deepseek", models=[]),
        api_key="fixture-key",
        root=tmp_path,
    )
    assert result["models"] == ["deepseek-chat"]
    assert seen == ["https://api.deepseek.com/v1/models"]


def test_chat_fallback_uses_only_explicit_chat_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from max_backend.optimizer_scientist import (
        ScientistAdvisoryError,
        ScientistCallResult,
    )
    from max_backend.scientist_chat import _chat_provider_call

    monkeypatch.setattr(
        "max_backend.scientist_chat.chat_fallback_models",
        lambda: ["chat-fallback"],
    )
    monkeypatch.setattr(
        "max_backend.scientist_chat.resolve_provider_runtime",
        lambda model=None, purpose="chat": (
            {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": model,
                "auth_mode": "OLLAMA_LOCAL",
                "api_key_env": "",
                "timeout_sec": 60,
                "fallback_models": [],
            },
            None,
            "SAVED_SETTINGS",
        ),
    )
    calls = []

    def fake_call(route, _messages, **_kwargs):
        calls.append(route["model"])
        if route["model"] == "primary":
            raise ScientistAdvisoryError(
                "SCIENTIST_MODEL_UNAVAILABLE",
                "missing",
                actual_llm_call=True,
                provenance={"status": "FAIL"},
            )
        return ScientistCallResult(
            text='{"answer":"ok"}',
            provenance={"status": "PASS", "actual_model": route["model"]},
        )

    monkeypatch.setattr(
        "max_backend.scientist_chat.openai_compatible_call",
        fake_call,
    )
    result = _chat_provider_call(
        {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "primary",
            "auth_mode": "OLLAMA_LOCAL",
            "api_key_env": "",
            "timeout_sec": 60,
            "fallback_models": ["autonomous-must-not-be-used"],
        },
        [{"role": "user", "content": "test"}],
        secret=None,
    )
    assert calls == ["primary", "chat-fallback"]
    assert result.provenance["fallback_route_used"] is True
    assert result.provenance["chat_fallback_count"] == 1


def test_chat_timeout_is_unconfirmed_class_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from max_backend.optimizer_scientist import ScientistAdvisoryError
    from max_backend.scientist_chat import _chat_provider_call

    monkeypatch.setattr(
        "max_backend.scientist_chat.chat_fallback_models",
        lambda: ["chat-fallback"],
    )
    monkeypatch.setattr(
        "max_backend.scientist_chat.resolve_provider_runtime",
        lambda model=None, purpose="chat": (
            {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": model,
                "auth_mode": "OLLAMA_LOCAL",
                "api_key_env": "",
                "timeout_sec": 60,
                "fallback_models": [],
            },
            None,
            "SAVED_SETTINGS",
        ),
    )
    calls = []

    def timeout(route, _messages, **_kwargs):
        calls.append(route["model"])
        raise ScientistAdvisoryError(
            "SCIENTIST_TIMEOUT",
            "timeout",
            actual_llm_call=True,
            provenance={"status": "FAIL"},
        )

    monkeypatch.setattr(
        "max_backend.scientist_chat.openai_compatible_call",
        timeout,
    )
    with pytest.raises(ScientistAdvisoryError) as caught:
        _chat_provider_call(
            {
                "provider": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "primary",
                "auth_mode": "OLLAMA_LOCAL",
                "api_key_env": "",
                "timeout_sec": 60,
                "fallback_models": [],
            },
            [{"role": "user", "content": "test"}],
            secret=None,
        )
    assert caught.value.category == "SCIENTIST_TIMEOUT"
    assert calls == ["primary"]


def test_known_provider_endpoint_is_locked_and_custom_private_network_is_rejected() -> None:
    with pytest.raises(ProviderSettingsError) as locked:
        normalize_provider_config(
            api_config(
                "openai",
                base_url="https://example.com/v1",
            )
        )
    assert locked.value.code == "SCIENTIST_PROVIDER_ENDPOINT_LOCKED"

    with pytest.raises(ProviderSettingsError) as private:
        normalize_provider_config(
            api_config(
                "custom",
                base_url="https://127.0.0.2/v1",
                auth_mode="API_KEY",
            )
        )
    assert private.value.code == "SCIENTIST_REMOTE_PRIVATE_HOST_REJECTED"


def test_ollama_offline_fails_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def offline_get(url, **_kwargs):
        seen.append(url)
        raise ProviderSettingsError("SCIENTIST_OLLAMA_UNAVAILABLE")

    monkeypatch.setattr(
        "max_backend.scientist_provider._json_get",
        offline_get,
    )
    with pytest.raises(ProviderSettingsError) as caught:
        discover_models(
            ollama_config(models=[]),
            root=tmp_path,
        )
    assert caught.value.code == "SCIENTIST_OLLAMA_UNAVAILABLE"
    assert seen == [
        "http://127.0.0.1:11434/v1/models",
        "http://127.0.0.1:11434/api/tags",
    ]
