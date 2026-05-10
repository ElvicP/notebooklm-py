"""Tests for cli.profile helpers."""

import pytest

from notebooklm.cli.profile import _PROFILE_NAME_RE, email_to_profile_name


class TestEmailToProfileName:
    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            ("alice@example.com", "alice"),
            ("alice.smith@example.com", "alice-smith"),
            ("bob+work@gmail.com", "bob-work"),
            ("teng.lin.9414@gmail.com", "teng-lin-9414"),
            ("under_score@gmail.com", "under_score"),
            ("dash-already@gmail.com", "dash-already"),
        ],
    )
    def test_sanitization(self, email, expected):
        assert email_to_profile_name(email) == expected

    def test_falls_back_when_local_part_starts_with_punctuation(self):
        # All-punctuation local-part collapses to empty → fallback fires.
        assert email_to_profile_name("...@example.com") == "account"

    def test_uses_provided_fallback(self):
        assert email_to_profile_name("...@example.com", fallback="custom") == "custom"

    def test_no_at_sign_treats_input_as_local_part(self):
        assert email_to_profile_name("plain") == "plain"

    def test_result_always_passes_profile_name_validation(self):
        # Hard property: every output must satisfy the regex used by the
        # `profile create` command, otherwise downstream usage would fail.
        for email in [
            "alice@example.com",
            "a.b.c+d@test.org",
            "...@x.com",  # falls back
            "x" * 64 + "@long.com",
        ]:
            name = email_to_profile_name(email)
            assert _PROFILE_NAME_RE.match(name), name
