"""Minimal OpenAI-compatible chat transport for *research-only* role invocations.

Secrets stay in environment variables; no request/response body is logged.
Redirects and insecure remote HTTP endpoints are rejected.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class LLMError(Exception):
    """Sanitized provider failure; never expose raw response bodies or API keys."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise LLMError("INVALID_ENDPOINT")
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        is_local = host in ("localhost", "127.0.0.1", "::1")
        allowed = parsed.scheme == "https" or (parsed.scheme == "http" and is_local)
        if not allowed or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LLMError("INVALID_ENDPOINT")
        if not parsed.path.endswith("/chat/completions") or not host:
            raise LLMError("INVALID_ENDPOINT")
    except ValueError:
        raise LLMError("INVALID_ENDPOINT") from None
    return endpoint


class OpenAICompatibleClient:
    """A single configured model can fulfill multiple independently logged roles."""

    def __init__(self, endpoint: str, model: str, api_key: str = "",
                 *, timeout_seconds: int = 25, max_tokens: int = 600):
        self.endpoint = validate_endpoint(endpoint)
        if not isinstance(model, str) or not 1 <= len(model) <= 128:
            raise LLMError("INVALID_MODEL")
        if not 1 <= timeout_seconds <= 120 or not 64 <= max_tokens <= 2048:
            raise LLMError("INVALID_BUDGET")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._tokens = max_tokens

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        return cls(
            os.getenv("MAX_TRADING_LLM_ENDPOINT", ""),
            os.getenv("MAX_TRADING_LLM_MODEL", ""),
            os.getenv("MAX_TRADING_LLM_API_KEY", ""),
        )

    def complete(self, role: str, prompt: str) -> str:
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 24000:
            raise LLMError("PROMPT_BUDGET_EXCEEDED")
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "You are a trading RESEARCH role, not a broker. Your output is "
                    "a bounded written opinion, NOT independent evidence of trading edge. "
                    "Never invent market history, past performance, fills or test metrics. "
                    "Never instruct tools to execute trades or bypass risk policy. "
                    "Identify missing evidence and permit NO_TRADE."
                )},
                {"role": "user", "content": f"ROLE: {role}\n{prompt}"},
            ],
            "max_tokens": self._tokens,
            "temperature": 0.1,
        }, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self._timeout) as response:
                raw = response.read(128 * 1024 + 1)
        except HTTPError as exc:
            # Never retain server error messages; they could echo secrets.
            raise LLMError("PROVIDER_HTTP_ERROR") from None
        except (URLError, TimeoutError, OSError):
            raise LLMError("PROVIDER_UNAVAILABLE") from None
        if len(raw) > 128 * 1024:
            raise LLMError("RESPONSE_TOO_LARGE")
        try:
            decoded = json.loads(raw)
            message = decoded["choices"][0]["message"]["content"]
        except (TypeError, ValueError, KeyError, IndexError):
            raise LLMError("INVALID_PROVIDER_RESPONSE") from None
        if not isinstance(message, str) or not message.strip():
            raise LLMError("EMPTY_ROLE_REPORT")
        return message[:4500]


ROLE_INSTRUCTIONS = {
    "Market Analyst": (
        "Review only the supplied timestamps, completed OHLC candles, and bid/ask. "
        "Distinguish observation from hypothesis; never assert profit metrics or predict certainty."
    ),
    "Bull Researcher": (
        "Construct the strongest plausible bullish case and exactly how it could fail. "
        "Challenge data quality and after-cost edge; do not recommend an executable order."
    ),
    "Bear Researcher": (
        "Construct the strongest plausible bearish case and exactly how it could fail. "
        "Challenge the bullish thesis and highlight contrary evidence."
    ),
    "Research Manager": (
        "Synthesize the market and bull/bear reports. State conflicting hypotheses, "
        "limitations, and specific evidence missing. No backtest exists in this milestone; "
        "treat all directional claims as unverified and conclude RESEARCH_ONLY."
    ),
}
