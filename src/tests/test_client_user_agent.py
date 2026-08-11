"""Tests for podcast-client User-Agent normalization."""

from app.client_user_agent import normalize_client_user_agent


def test_normalize_apple_podcasts_variants():
    assert (
        normalize_client_user_agent("Podcasts/1.0 CFNetwork/1490.0.4 Darwin/23.2.0")
        == "Apple Podcasts"
    )
    assert (
        normalize_client_user_agent(
            "AppleCoreMedia/1.0.0.21E236 (iPhone; U; CPU OS 17_4 like Mac OS X)"
        )
        == "Apple Podcasts"
    )


def test_normalize_common_clients():
    assert normalize_client_user_agent("Overcast/2024.1") == "Overcast"
    assert normalize_client_user_agent("AntennaPod/3.5.0") == "AntennaPod"
    assert normalize_client_user_agent("Pocket Casts") == "Pocket Casts"
    assert normalize_client_user_agent("Castro 2024.1") == "Castro"


def test_normalize_empty_and_unknown():
    assert normalize_client_user_agent(None) is None
    assert normalize_client_user_agent("   ") is None
    assert normalize_client_user_agent("MyCustomPodClient/9.0") == "MyCustomPodClient"
