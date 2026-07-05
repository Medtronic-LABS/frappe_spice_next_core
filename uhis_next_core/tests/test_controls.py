"""
Unit tests for uhis_next_core.auth.jwt_token_validator and the override-merge
logic in api/controls.py. Pure Python — no Frappe DB required, matching the
style of tests/test_auth.py.
"""

import unittest

from uhis_next_core.auth.jwt_token_validator import JWTDecodeError, JWTTokenValidator


class TestJWTTokenValidator(unittest.TestCase):

    def setUp(self):
        self.validator = JWTTokenValidator()

    def test_missing_header_raises(self):
        with self.assertRaises(JWTDecodeError):
            self.validator.extract_user_id(None)

    def test_non_bearer_header_raises(self):
        with self.assertRaises(JWTDecodeError):
            self.validator.extract_user_id("Basic abc123")

    def test_empty_bearer_token_raises(self):
        with self.assertRaises(JWTDecodeError):
            self.validator.extract_user_id("Bearer ")

    def test_valid_token_extracts_sub(self):
        token = (
            "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
            "eyJzdWIiOiJleHQtdXNlci00MiJ9."
        )
        user_id = self.validator.extract_user_id(f"Bearer {token}")
        self.assertEqual(user_id, "ext-user-42")

    def test_token_without_sub_claim_raises(self):
        token = (
            "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
            "eyJpYXQiOjE3NTE1MDAwMDB9."
        )
        with self.assertRaises(JWTDecodeError):
            self.validator.extract_user_id(f"Bearer {token}")

    def test_malformed_token_raises(self):
        with self.assertRaises(JWTDecodeError):
            self.validator.extract_user_id("Bearer not-a-jwt")


class TestControlsOverrideMerge(unittest.TestCase):
    """Mirrors the merge logic in api/controls.get_controls without DB access."""

    @staticmethod
    def _merge(settings, override):
        min_app_version = settings["min_app_version"]
        latest_app_version = settings["latest_app_version"]
        language = settings["default_language"]
        ai_feature = bool(settings["ai_feature_enabled"])
        if override:
            if override.get("min_app_version"):
                min_app_version = override["min_app_version"]
            if override.get("selected_language"):
                language = override["selected_language"]
            ai_feature = bool(override.get("ai_feature"))
        return {
            "minAppVersion": min_app_version,
            "latestAppVersion": latest_app_version,
            "language": language,
            "AIFeature": ai_feature,
        }

    def test_no_override_uses_system_defaults(self):
        settings = {
            "min_app_version": "1.4.0",
            "latest_app_version": "1.6.2",
            "default_language": "en",
            "ai_feature_enabled": 0,
        }
        result = self._merge(settings, None)
        self.assertEqual(result["minAppVersion"], "1.4.0")
        self.assertEqual(result["language"], "en")
        self.assertFalse(result["AIFeature"])

    def test_full_override_wins(self):
        settings = {
            "min_app_version": "1.4.0",
            "latest_app_version": "1.6.2",
            "default_language": "en",
            "ai_feature_enabled": 0,
        }
        override = {"min_app_version": "1.5.0", "selected_language": "bn", "ai_feature": 1}
        result = self._merge(settings, override)
        self.assertEqual(result["minAppVersion"], "1.5.0")
        self.assertEqual(result["language"], "bn")
        self.assertTrue(result["AIFeature"])
        self.assertEqual(result["latestAppVersion"], "1.6.2")  # never overridden

    def test_partial_override_falls_back_per_field(self):
        settings = {
            "min_app_version": "1.4.0",
            "latest_app_version": "1.6.2",
            "default_language": "en",
            "ai_feature_enabled": 0,
        }
        override = {"min_app_version": "", "selected_language": "bn", "ai_feature": 0}
        result = self._merge(settings, override)
        self.assertEqual(result["minAppVersion"], "1.4.0")  # falls back, override was blank
        self.assertEqual(result["language"], "bn")           # overridden
        self.assertFalse(result["AIFeature"])                 # row exists -> its value wins


if __name__ == "__main__":
    unittest.main()
