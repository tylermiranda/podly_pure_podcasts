"""Tests for feed access token secret generation."""

from __future__ import annotations

import string

from app.writer.actions.feeds import _generate_feed_token_secret


def test_feed_token_secret_is_alphanumeric_only():
    """YouTube Music paste breaks on secrets that start with '-' (token_urlsafe)."""
    alphabet = set(string.ascii_letters + string.digits)
    for _ in range(50):
        secret = _generate_feed_token_secret()
        assert secret
        assert set(secret) <= alphabet
        assert "-" not in secret
        assert "_" not in secret
