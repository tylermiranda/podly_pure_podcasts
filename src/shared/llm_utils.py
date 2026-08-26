"""Shared helpers for working with LLM provider quirks."""

from __future__ import annotations

from typing import Final
from urllib.parse import urlparse

# Patterns for models that require the `max_completion_tokens` parameter
# instead of the legacy `max_tokens`. OpenAI began enforcing this on the
# newer gpt-4o / gpt-5 / o1 style models.
_MAX_COMPLETION_TOKEN_MODELS: Final[tuple[str, ...]] = (
    "gpt-5",
    "gpt-4o",
    "o1-",
    "o1_",
    "o1/",
    "chatgpt-4o-latest",
)

# Hosts known to accept OpenAI's response_format type=json_object.
_JSON_OBJECT_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    "openai.com",
    "openrouter.ai",
    "groq.com",
    "googleapis.com",
    "azure.com",
    "azure-api.net",
)


def model_uses_max_completion_tokens(model_name: str | None) -> bool:
    """Return True when the target model expects `max_completion_tokens`."""
    if not model_name:
        return False
    model_lower = model_name.lower()
    return any(pattern in model_lower for pattern in _MAX_COMPLETION_TOKEN_MODELS)


def resolve_llm_model(
    config: object,
    override_attr: str,
    *,
    fallback_attr: str = "llm_model",
    default: str = "gpt-4o",
) -> str:
    """Return override model when set, otherwise the primary classify model."""
    override = getattr(config, override_attr, None)
    if isinstance(override, str) and override.strip():
        return override.strip()
    fallback = getattr(config, fallback_attr, None)
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return default


def supports_json_object_response_format(base_url: str | None) -> bool:
    """Return True when the API accepts response_format type=json_object.

    Local OpenAI-compatible servers (LM Studio, Ollama, etc.) often only allow
    ``json_schema`` or ``text`` and reject ``json_object`` with HTTP 400.
    """
    if not base_url:
        # Default litellm/OpenAI route
        return True
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    # RFC1918 / link-local — typically LAN inference servers
    if host.startswith("192.168.") or host.startswith("10."):
        return False
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            second = -1
        if 16 <= second <= 31:
            return False
    return any(host == s or host.endswith("." + s) for s in _JSON_OBJECT_HOST_SUFFIXES)
