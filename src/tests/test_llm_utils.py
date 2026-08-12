"""Tests for shared.llm_utils provider quirk helpers."""

from shared.llm_utils import (
    model_uses_max_completion_tokens,
    supports_json_object_response_format,
)


def test_model_uses_max_completion_tokens_gpt5() -> None:
    assert model_uses_max_completion_tokens("gpt-5")
    assert model_uses_max_completion_tokens("openai/gpt-4o-mini")
    assert not model_uses_max_completion_tokens("openai/google/gemma-4-12b")
    assert not model_uses_max_completion_tokens(None)


def test_supports_json_object_default_and_cloud() -> None:
    assert supports_json_object_response_format(None)
    assert supports_json_object_response_format("https://api.openai.com/v1")
    assert supports_json_object_response_format("https://openrouter.ai/api/v1")
    assert supports_json_object_response_format("https://api.groq.com/openai/v1")


def test_supports_json_object_rejects_local_and_lan() -> None:
    assert not supports_json_object_response_format("http://127.0.0.1:1234/v1")
    assert not supports_json_object_response_format("http://localhost:1234/v1")
    assert not supports_json_object_response_format("http://192.168.1.24:1234/v1")
    assert not supports_json_object_response_format("http://10.0.0.5:1234/v1")
    assert not supports_json_object_response_format("http://172.18.0.2:1234/v1")
