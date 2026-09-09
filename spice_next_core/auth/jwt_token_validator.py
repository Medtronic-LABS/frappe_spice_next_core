"""
JWT payload extraction for mobile-app authenticated public endpoints.

Phase 1 (default, remote_auth_url=None): signature is NOT verified — the
payload is decoded locally and the subject (user id) claim is extracted.
Dev/test convenience only.

Phase 2 (remote_auth_url set): delegates entirely to RemoteAuthClient, which
calls the legacy auth-service's token-introspection endpoint. Local decode is
skipped, not layered underneath — the legacy mobile token is a JWE (encrypted
JWT), structurally incompatible with the plain-JWT decode below, so there is
no local payload to fall back to once a remote URL is configured. Call sites
(e.g. spice_next_core.auth.decorators.require_remote_auth) use only the public
`JWTTokenValidator.extract_user_id()` method and do not need to change between
phases.

Assumption (Phase 1 only): the user id is carried in the standard JWT "sub"
(subject) claim. Change SUBJECT_CLAIM below if the mobile client's tokens use
a different key.
"""

import jwt

from spice_next_core.auth.remote_auth_client import RemoteAuthClient, RemoteAuthError

BEARER_PREFIX = "Bearer "
SUBJECT_CLAIM = "sub"


class JWTDecodeError(Exception):
	"""Raised when a bearer token is missing, malformed, or has no usable payload.

	Callers should catch this narrowly and translate it into a 401 response —
	never surface the raw exception (or the raw token) to the client.
	"""


class JWTTokenValidator:
	"""Extracts the user id claim from a caller-supplied bearer token.

	remote_auth_url=None (default): Phase 1, unverified local decode — see
	module docstring. remote_auth_url set: Phase 2, delegates to
	RemoteAuthClient. Either way this is a small, swappable seam: callers of
	`extract_user_id()` are unaffected by which phase is active.
	"""

	def __init__(self, remote_auth_url=None, client_tag=None, cache_ttl_seconds=60, timeout=5):
		self._remote_client = (
			RemoteAuthClient(remote_auth_url, client_tag=client_tag,
			                 cache_ttl_seconds=cache_ttl_seconds, timeout=timeout)
			if remote_auth_url else None
		)

	def extract_user_id(self, authorization_header, tenant_id=None):
		"""Return the external user id from a bearer token.

		:param authorization_header: raw value of the `Authorization` HTTP header.
		:param tenant_id: forwarded to the remote auth service in Phase 2 only
			(its own /authenticate contract requires it); ignored in Phase 1.
		:raises JWTDecodeError: header missing/malformed, token invalid/undecodable
			(Phase 1), or rejected by the remote auth service (Phase 2).
		"""
		token = self._extract_bearer_token(authorization_header)

		if self._remote_client:
			try:
				return self._remote_client.validate(token, tenant_id)
			except RemoteAuthError as e:
				# Preserve the specific reason (e.g. "rejected token (status 401)"
				# vs "remote auth call failed" for a network/timeout failure) --
				# collapsing both into one generic string here made Remote Auth
				# Activity Log useless for actually diagnosing a rejection.
				raise JWTDecodeError(f"token rejected by remote auth service: {e}") from e

		claims = self._decode_payload(token)
		user_id = claims.get(SUBJECT_CLAIM)
		if not isinstance(user_id, str) or not user_id.strip():
			raise JWTDecodeError(f"JWT payload is missing a non-empty '{SUBJECT_CLAIM}' claim")

		return user_id.strip()

	@staticmethod
	def _extract_bearer_token(authorization_header):
		if not authorization_header:
			raise JWTDecodeError("Authorization header is missing")

		if not authorization_header.startswith(BEARER_PREFIX):
			raise JWTDecodeError("Authorization header is not a Bearer token")

		token = authorization_header[len(BEARER_PREFIX):].strip()
		if not token:
			raise JWTDecodeError("Bearer token is empty")

		return token

	@staticmethod
	def _decode_payload(token):
		"""Decode the JWT payload only — signature verification intentionally
		disabled (see module docstring)."""
		try:
			return jwt.decode(
				token,
				options={
					"verify_signature": False,
					"verify_exp": False,
					"verify_aud": False,
					"verify_iat": False,
					"verify_nbf": False,
				},
			)
		except jwt.exceptions.DecodeError as e:
			raise JWTDecodeError("JWT could not be decoded") from e
		except jwt.exceptions.InvalidTokenError as e:
			raise JWTDecodeError("JWT is not a valid token") from e
