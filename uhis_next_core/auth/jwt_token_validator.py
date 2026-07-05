"""
JWT payload extraction for mobile-app authenticated public endpoints.

Phase 1 (current): signature is NOT verified — there is no shared secret or
identity-provider integration configured yet. Only the payload is decoded,
and the subject (user id) claim is extracted.

Phase 2 (future): swap the body of `_decode_payload` for an HTTP call to the
token's issuing provider's verification/introspection endpoint. Call sites
(e.g. uhis_next_core.api.controls.get_controls) use only the public
`JWTTokenValidator.extract_user_id()` method and do not need to change.

Assumption: the user id is carried in the standard JWT "sub" (subject) claim.
Change SUBJECT_CLAIM below if the mobile client's tokens use a different key.
"""

import jwt

BEARER_PREFIX = "Bearer "
SUBJECT_CLAIM = "sub"


class JWTDecodeError(Exception):
	"""Raised when a bearer token is missing, malformed, or has no usable payload.

	Callers should catch this narrowly and translate it into a 401 response —
	never surface the raw exception (or the raw token) to the client.
	"""


class JWTTokenValidator:
	"""Extracts the user id claim from a caller-supplied JWT.

	No signature verification is performed in this phase — see module
	docstring. This is intentionally a small, swappable seam: everything
	upstream of `extract_user_id()` should be unaffected when real
	verification is added later.
	"""

	def extract_user_id(self, authorization_header):
		"""Return the external user id (JWT `sub` claim) from a bearer token.

		:param authorization_header: raw value of the `Authorization` HTTP header.
		:raises JWTDecodeError: header missing/malformed, token undecodable,
			or the payload has no usable subject claim.
		"""
		token = self._extract_bearer_token(authorization_header)
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
