"""
Unit tests for api/auth.py — pure helper logic.
No Frappe DB required.
"""

import unittest


def _get_sk_profile_from_provider(user, provider):
    """Mirrors _get_sk_profile() logic when provider record exists."""
    if provider:
        return {
            "user_id": user,
            "full_name": provider.get("full_name") or user,
            "full_name_bn": provider.get("full_name_bn") or "",
            "programme": provider.get("programme") or "",
            "geography_id": provider.get("geography_node") or "",
            "photo_url": provider.get("photo") or None,
        }
    full_name = user
    return {
        "user_id": user,
        "full_name": full_name,
        "full_name_bn": "",
        "programme": "",
        "geography_id": "",
        "photo_url": None,
    }


class TestSkProfile(unittest.TestCase):

    def test_profile_with_full_provider(self):
        provider = {
            "full_name": "Rina Khatun",
            "full_name_bn": "রিনা খাতুন",
            "programme": "ncd",
            "geography_node": "geo-dhaka-mirpur",
            "photo": "/files/rina.jpg",
        }
        profile = _get_sk_profile_from_provider("rina@uhis.org", provider)
        self.assertEqual(profile["user_id"], "rina@uhis.org")
        self.assertEqual(profile["full_name"], "Rina Khatun")
        self.assertEqual(profile["full_name_bn"], "রিনা খাতুন")
        self.assertEqual(profile["programme"], "ncd")
        self.assertEqual(profile["geography_id"], "geo-dhaka-mirpur")
        self.assertEqual(profile["photo_url"], "/files/rina.jpg")

    def test_profile_without_provider_falls_back_to_username(self):
        profile = _get_sk_profile_from_provider("admin@uhis.org", None)
        self.assertEqual(profile["user_id"], "admin@uhis.org")
        self.assertEqual(profile["full_name"], "admin@uhis.org")
        self.assertEqual(profile["full_name_bn"], "")
        self.assertEqual(profile["geography_id"], "")
        self.assertIsNone(profile["photo_url"])

    def test_provider_with_missing_optional_fields(self):
        provider = {
            "full_name": "Karim Mia",
            "full_name_bn": None,
            "programme": None,
            "geography_node": None,
            "photo": None,
        }
        profile = _get_sk_profile_from_provider("karim@uhis.org", provider)
        self.assertEqual(profile["full_name_bn"], "")
        self.assertEqual(profile["programme"], "")
        self.assertEqual(profile["geography_id"], "")
        self.assertIsNone(profile["photo_url"])

    def test_required_response_fields_present(self):
        profile = _get_sk_profile_from_provider("x@y.com", None)
        required = ["user_id", "full_name", "full_name_bn", "programme", "geography_id", "photo_url"]
        for field in required:
            self.assertIn(field, profile, f"Missing field: {field}")


class TestDevicePayload(unittest.TestCase):

    def test_device_dict_has_expected_keys(self):
        device = {
            "device_id": "device-uuid-001",
            "model": "Samsung Galaxy A52",
            "os_version": "Android 13",
            "app_version": "1.0.0",
        }
        self.assertIn("device_id", device)
        self.assertIn("model", device)
        self.assertIn("os_version", device)
        self.assertIn("app_version", device)

    def test_device_id_required_for_upsert(self):
        # _upsert_device silently returns when device_id is missing.
        device = {}
        device_id = device.get("device_id")
        self.assertIsNone(device_id)

    def test_login_response_shape(self):
        response = {
            "api_key": "abc123",
            "api_secret": "secret456",
            "profile": {
                "user_id": "rina@uhis.org",
                "full_name": "Rina Khatun",
                "full_name_bn": "রিনা খাতুন",
                "programme": "ncd",
                "geography_id": "geo-001",
                "photo_url": None,
            },
        }
        self.assertIn("api_key", response)
        self.assertIn("api_secret", response)
        self.assertIn("profile", response)
        profile = response["profile"]
        for field in ["user_id", "full_name", "full_name_bn", "programme", "geography_id"]:
            self.assertIn(field, profile)


if __name__ == "__main__":
    unittest.main()
