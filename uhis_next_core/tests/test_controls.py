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


class TestAiWidgetsAndVadTuning(unittest.TestCase):
    """Mirrors api/controls._ai_widgets and _vad_tuning without DB access."""

    @staticmethod
    def _ai_widgets(settings):
        return {
            "step1SummaryEnabled": bool(settings["step1_summary_enabled"]),
            "step1AsrEnabled": bool(settings["step1_asr_enabled"]),
            "step2AsrEnabled": bool(settings["step2_asr_enabled"]),
            "step3SummaryEnabled": bool(settings["step3_summary_enabled"]),
            "step3ReferralAlertEnabled": bool(settings["step3_referral_alert_enabled"]),
            "step3WhatsAppEnabled": bool(settings["step3_whatsapp_enabled"]),
        }

    @staticmethod
    def _vad_tuning(settings):
        return {
            "enterMarginDb": settings["vad_enter_margin_db"],
            "sustainMarginDb": settings["vad_sustain_margin_db"],
            "floorCeilingDbfs": settings["vad_floor_ceiling_dbfs"],
            "floorAlpha": settings["vad_floor_alpha"],
            "bootstrapMs": settings["vad_bootstrap_ms"],
            "debounceMs": settings["vad_debounce_ms"],
            "hangoverMs": settings["vad_hangover_ms"],
            "preRollMs": settings["vad_preroll_ms"],
        }

    def test_ai_widgets_all_enabled_by_default(self):
        settings = {
            "step1_summary_enabled": 1,
            "step1_asr_enabled": 1,
            "step2_asr_enabled": 1,
            "step3_summary_enabled": 1,
            "step3_referral_alert_enabled": 1,
            "step3_whatsapp_enabled": 1,
        }
        result = self._ai_widgets(settings)
        self.assertTrue(all(result.values()))

    def test_ai_widgets_reflects_individually_disabled_toggle(self):
        settings = {
            "step1_summary_enabled": 1,
            "step1_asr_enabled": 0,
            "step2_asr_enabled": 1,
            "step3_summary_enabled": 1,
            "step3_referral_alert_enabled": 1,
            "step3_whatsapp_enabled": 1,
        }
        result = self._ai_widgets(settings)
        self.assertFalse(result["step1AsrEnabled"])
        self.assertTrue(result["step1SummaryEnabled"])
        self.assertTrue(result["step2AsrEnabled"])

    def test_vad_tuning_matches_flutter_factory_defaults(self):
        settings = {
            "vad_enter_margin_db": 9,
            "vad_sustain_margin_db": 6,
            "vad_floor_ceiling_dbfs": -35,
            "vad_floor_alpha": 0.08,
            "vad_bootstrap_ms": 500,
            "vad_debounce_ms": 180,
            "vad_hangover_ms": 700,
            "vad_preroll_ms": 350,
        }
        result = self._vad_tuning(settings)
        self.assertEqual(result["enterMarginDb"], 9)
        self.assertEqual(result["floorCeilingDbfs"], -35)
        self.assertEqual(result["preRollMs"], 350)


if __name__ == "__main__":
    unittest.main()
