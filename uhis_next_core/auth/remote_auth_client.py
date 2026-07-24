"""
Validates a caller-supplied bearer token against the legacy platform's
auth-service token-introspection endpoint (POST /authenticate).

Success is determined ONLY by HTTP status: exactly 200 = valid, anything
else (including a request exception/timeout) = invalid. The token itself is
opaque to this client — it's forwarded as-is in the Authorization header and
never parsed or assumed to be any particular format (e.g. JWT).

If the response body is JSON shaped like the legacy ContextsDTO,
userDetail.username (falling back to userDetail.id) is used as the external
user id — but this is best-effort, not a success requirement: a 200 with an
unparseable or user-id-less body is still a valid authentication, just with
no known user id (current_remote_user_id() then reads as None).

Validated tokens are cached (Frappe's own cache, keyed by a hash of the token
— never the raw token) for cache_ttl_seconds, so a burst of requests from the
same session doesn't each cost a remote round trip.
"""

import hashlib

import frappe

_CACHE_PREFIX = "uhis_next_core:remote_auth:"


class RemoteAuthError(Exception):
	"""Raised when the remote token-introspection call fails or rejects the
	token — missing token, non-200 response, or a request exception/timeout.
	Callers should catch this narrowly and never surface the raw token or the
	remote response to the client."""


class RemoteAuthClient:
	def __init__(self, base_url, client_tag=None, cache_ttl_seconds=60, timeout=5):
		self.base_url = base_url
		self.client_tag = client_tag or ""
		self.cache_ttl_seconds = cache_ttl_seconds
		self.timeout = timeout

	def validate(self, token):
		"""Return the external user id (or None) for a valid token; raise
		RemoteAuthError for anything other than an HTTP 200 response."""
		if not token:
			raise RemoteAuthError("token is empty")

		cache_key = _CACHE_PREFIX + hashlib.sha256(token.encode()).hexdigest()
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

		user_id = self._call_remote(token)
		frappe.cache().set_value(cache_key, user_id, expires_in_sec=self.cache_ttl_seconds)
		return user_id

	def _call_remote(self, token):
		import requests

		headers = {"Authorization": f"Bearer {token}", "client": self.client_tag}
		try:
			resp = requests.post(self.base_url, headers=headers, timeout=self.timeout)
		except requests.RequestException as e:
			raise RemoteAuthError("remote auth call failed") from e

		if resp.status_code != 200:
			raise RemoteAuthError(f"remote auth rejected token (status {resp.status_code})")

		return self._extract_user_id(resp)

	@staticmethod
	def _extract_user_id(resp):
		try:
			body = resp.json()
		except ValueError:
			return None
		user_detail = body.get("userDetail") or {}
		user_id = user_detail.get("username") or user_detail.get("id")
		return str(user_id) if user_id else None
