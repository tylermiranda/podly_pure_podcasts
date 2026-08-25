from types import SimpleNamespace

from podcast_processor.llm_model_call_utils import (
    extract_litellm_content,
    extract_litellm_finish_reason,
    extract_litellm_usage,
)


def test_extract_litellm_content_from_object_message() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Treat credits as CONTENT.")
            )
        ]
    )
    assert extract_litellm_content(response) == "Treat credits as CONTENT."


def test_extract_litellm_content_from_dict_response_and_choice() -> None:
    response = {
        "choices": [{"message": {"content": "Cut midroll sponsor reads."}}],
    }
    assert extract_litellm_content(response) == "Cut midroll sponsor reads."


def test_extract_litellm_content_from_content_parts_list() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        {"type": "text", "text": "Rule A."},
                        {"type": "text", "text": "Rule B."},
                    ]
                )
            )
        ]
    )
    assert extract_litellm_content(response) == "Rule A.\nRule B."


def test_extract_litellm_content_empty_message_falls_back_to_text() -> None:
    response = SimpleNamespace(choices=[SimpleNamespace(message=None, text="legacy")])
    assert extract_litellm_content(response) == "legacy"


def test_extract_litellm_finish_reason_from_object_choice() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=None)]
    )

    assert extract_litellm_finish_reason(response) == "length"


def test_extract_litellm_finish_reason_from_dict_choice() -> None:
    response = SimpleNamespace(choices=[{"finish_reason": "stop"}])

    assert extract_litellm_finish_reason(response) == "stop"


def test_extract_litellm_usage_handles_object_and_numeric_strings() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens="101",
            completion_tokens=22,
            total_tokens=123,
        )
    )

    assert extract_litellm_usage(response) == {
        "prompt_tokens": 101,
        "completion_tokens": 22,
        "total_tokens": 123,
    }


def test_extract_litellm_usage_handles_dict_response() -> None:
    response = {
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}
    }

    assert extract_litellm_usage(response) == {
        "prompt_tokens": 5,
        "completion_tokens": 9,
        "total_tokens": 14,
    }
