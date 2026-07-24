"""
Unit tests for uhis_next_core.auth.remote_auth_client, the Phase 2 branch of
JWTTokenValidator, the require_remote_auth decorator, and the whitelist()
wrapper that collapses @frappe.whitelist + @require_remote_auth into one.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
import requests

from uhis_next_core.auth.decorators import current_remote_user_id, require_remote_auth, whitelist
from uhis_next_core.auth.jwt_token_validator import JWTDecodeError, JWTTokenValidator
from uhis_next_core.auth.remote_auth_client import RemoteAuthClient, RemoteAuthError


class TestRemoteAuthClient(unittest.TestCase):

	def setUp(self):
		self.client = RemoteAuthClient("http://auth-service:8089/authenticate", client_tag="api")

	def test_empty_token_raises(self):
		with self.assertRaises(RemoteAuthError):
			self.client.validate("")

	@patch("requests.post")
	def test_success_extracts_username(self, mock_post):
		mock_post.return_value = MagicMock(
			status_code=200,
			json=lambda: {"userDetail": {"username": "ext-user-1", "id": 1}},
		)
		user_id = self.client.validate("token-a")
		self.assertEqual(user_id, "ext-user-1")
		mock_post.assert_called_once()
		_, kwargs = mock_post.call_args
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token-a")
		self.assertEqual(kwargs["headers"]["client"], "api")

	@patch("requests.post")
	def test_success_falls_back_to_id_when_no_username(self, mock_post):
		mock_post.return_value = MagicMock(
			status_code=200,
			json=lambda: {"userDetail": {"id": 42}},
		)
		user_id = self.client.validate("token-b")
		self.assertEqual(user_id, "42")

	@patch("requests.post")
	def test_non_200_status_raises(self, mock_post):
		# Success is determined ONLY by status == 200 — 400, 401, 403, 500,
		# whatever the provider returns, all fail the same way.
		for status in (400, 401, 403, 500):
			mock_post.return_value = MagicMock(status_code=status, json=lambda: {})
			with self.assertRaises(RemoteAuthError):
				self.client.validate(f"token-c-{status}")

	@patch("requests.post")
	def test_connection_error_raises(self, mock_post):
		mock_post.side_effect = requests.ConnectionError("boom")
		with self.assertRaises(RemoteAuthError):
			self.client.validate("token-d")

	@patch("requests.post")
	def test_200_with_no_user_id_in_body_still_succeeds(self, mock_post):
		# A 200 is a valid authentication even if the body has no parseable
		# user id — the caller just gets None back, not a rejection.
		mock_post.return_value = MagicMock(status_code=200, json=lambda: {"userDetail": {}})
		self.assertIsNone(self.client.validate("token-e"))

	@patch("requests.post")
	def test_200_with_non_json_body_still_succeeds(self, mock_post):
		resp = MagicMock(status_code=200)
		resp.json.side_effect = ValueError("not json")
		mock_post.return_value = resp
		self.assertIsNone(self.client.validate("token-f2"))

	@patch("frappe.cache")
	@patch("requests.post")
	def test_cache_hit_skips_second_remote_call(self, mock_post, mock_cache):
		# Frappe's test runner doesn't persist real Redis writes the same way a
		# live request does, so this stubs frappe.cache() with a plain dict to
		# deterministically test the cache-hit branch itself.
		store = {}
		mock_cache.return_value.get_value.side_effect = store.get
		mock_cache.return_value.set_value.side_effect = lambda k, v, expires_in_sec=None: store.__setitem__(k, v)
		mock_post.return_value = MagicMock(
			status_code=200,
			json=lambda: {"userDetail": {"username": "ext-user-cached"}},
		)
		first = self.client.validate("token-f")
		second = self.client.validate("token-f")
		self.assertEqual(first, second)
		mock_post.assert_called_once()


class TestJWTTokenValidatorPhase2(unittest.TestCase):

	def test_no_remote_url_uses_local_decode(self):
		validator = JWTTokenValidator()
		token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJleHQtdXNlci00MiJ9."
		self.assertEqual(validator.extract_user_id(f"Bearer {token}"), "ext-user-42")

	@patch.object(RemoteAuthClient, "validate")
	def test_remote_url_delegates_to_remote_client(self, mock_validate):
		mock_validate.return_value = "ext-user-legacy-1"
		validator = JWTTokenValidator(remote_auth_url="http://auth-service:8089/authenticate")
		user_id = validator.extract_user_id("Bearer some-jwe-token")
		self.assertEqual(user_id, "ext-user-legacy-1")
		mock_validate.assert_called_once_with("some-jwe-token")

	@patch.object(RemoteAuthClient, "validate")
	def test_remote_auth_error_becomes_jwt_decode_error(self, mock_validate):
		mock_validate.side_effect = RemoteAuthError("rejected")
		validator = JWTTokenValidator(remote_auth_url="http://auth-service:8089/authenticate")
		with self.assertRaises(JWTDecodeError):
			validator.extract_user_id("Bearer some-jwe-token")


class TestRequireRemoteAuthDecorator(unittest.TestCase):

	@patch("uhis_next_core.auth.decorators._build_validator")
	@patch("frappe.get_request_header")
	def test_missing_token_raises_authentication_error(self, mock_header, mock_build):
		mock_header.return_value = None
		mock_build.return_value.extract_user_id.side_effect = JWTDecodeError("missing")

		@require_remote_auth
		def protected():
			return "should not run"

		with self.assertRaises(frappe.AuthenticationError):
			protected()

	@patch("uhis_next_core.auth.decorators._build_validator")
	@patch("frappe.get_request_header")
	def test_valid_token_calls_wrapped_fn_and_sets_user_id(self, mock_header, mock_build):
		mock_header.return_value = "Bearer good-token"
		mock_build.return_value.extract_user_id.return_value = "ext-user-9"

		@require_remote_auth
		def protected():
			return current_remote_user_id()

		self.assertEqual(protected(), "ext-user-9")


class TestWhitelistWrapper(unittest.TestCase):
	"""frappe.whitelist(...) registers by object identity, not by attribute
	(see frappe/__init__.py:whitelist) — these checks confirm whitelist()
	still calls it as the outermost step, not just that nothing crashes."""

	def test_registers_in_frappe_whitelist_registries(self):
		@whitelist(methods=["POST"], remote_auth=True)
		def sample_endpoint():
			return "ok"

		self.assertIn(sample_endpoint, frappe.whitelisted)
		self.assertIn(sample_endpoint, frappe.allowed_http_methods_for_whitelisted_func)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[sample_endpoint], ["POST"])

	def test_remote_auth_true_implies_allow_guest(self):
		@whitelist(methods=["POST"], remote_auth=True)
		def sample_endpoint_2():
			return "ok"

		self.assertIn(sample_endpoint_2, frappe.guest_methods)

	def test_remote_auth_false_does_not_imply_allow_guest(self):
		@whitelist(methods=["POST"])
		def sample_endpoint_3():
			return "ok"

		self.assertNotIn(sample_endpoint_3, frappe.guest_methods)

	def test_remote_auth_false_skips_auth_guard_entirely(self):
		@whitelist(methods=["POST"])
		def sample_endpoint_4():
			return "ran"

		# No X-Auth-Token in this (non-request) test context; if the guard were
		# mistakenly applied, this would raise AuthenticationError instead.
		self.assertEqual(sample_endpoint_4(), "ran")

	@patch("uhis_next_core.auth.decorators._build_validator")
	@patch("frappe.get_request_header")
	def test_remote_auth_true_rejects_invalid_token(self, mock_header, mock_build):
		mock_header.return_value = None
		mock_build.return_value.extract_user_id.side_effect = JWTDecodeError("missing")

		@whitelist(methods=["POST"], remote_auth=True)
		def sample_endpoint_5():
			return "should not run"

		with self.assertRaises(frappe.AuthenticationError):
			sample_endpoint_5()

	@patch("uhis_next_core.auth.decorators._build_validator")
	@patch("frappe.get_request_header")
	def test_remote_auth_true_calls_fn_with_valid_token(self, mock_header, mock_build):
		mock_header.return_value = "Bearer good-token"
		mock_build.return_value.extract_user_id.return_value = "ext-user-42"

		@whitelist(methods=["POST"], remote_auth=True)
		def sample_endpoint_6():
			return current_remote_user_id()

		self.assertEqual(sample_endpoint_6(), "ext-user-42")


if __name__ == "__main__":
	unittest.main()
