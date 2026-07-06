"""
Mobile app version / feature control endpoint.

  uhis_next_core.api.controls.get_controls

Public endpoint (allow_guest=True) authenticated via a caller-supplied
`X-Auth-Token: Bearer <JWT>` header — NOT Frappe session/token auth. See
uhis_next_core.auth.jwt_token_validator.JWTTokenValidator for the (currently
unverified) JWT payload extraction.

NOTE: the token is carried in a custom `X-Auth-Token` header, not the
standard `Authorization` header. Frappe's own request middleware
(frappe.auth.validate_auth) inspects every `Authorization: <scheme> <token>`
header globally — before allow_guest dispatch — and hard-fails with
AuthenticationError if the scheme isn't a Frappe-recognized OAuth/API-key
credential. Since our unsigned mobile JWT isn't such a credential, using
`Authorization` here would always be rejected before this function ever
runs. A distinct header name sidesteps that middleware entirely.

Request:
  POST /api/method/uhis_next_core.api.controls.get_controls
  Header:  X-Auth-Token: Bearer <jwt>

Response (Frappe wraps in {"message": ...}):
  {
    "minAppVersion": "1.4.0",
    "latestAppVersion": "1.6.2",
    "language": "bn",
    "AIFeature": true
  }

Override rules:
  - latestAppVersion always comes from UHIS Settings (no per-user override).
  - minAppVersion / language: per-user value from User App Control wins if
    non-empty, else the UHIS Settings system-level value.
  - AIFeature: if a User App Control row exists for the caller, its
    ai_feature value (0 or 1) always wins; otherwise UHIS Settings'
    ai_feature_enabled is used.
"""

import frappe
from frappe import _

from uhis_next_core.auth.jwt_token_validator import JWTDecodeError, JWTTokenValidator

_validator = JWTTokenValidator()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def get_controls():
	user_id = _authenticate()

	settings = frappe.get_single("UHIS Settings")
	min_app_version = settings.min_app_version or ""
	latest_app_version = settings.latest_app_version or ""
	language = settings.default_language or ""
	ai_feature = bool(settings.ai_feature_enabled)

	override = _get_user_override(user_id)
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


def _authenticate():
	auth_header = frappe.get_request_header("X-Auth-Token")
	try:
		return _validator.extract_user_id(auth_header)
	except JWTDecodeError:
		frappe.throw(_("Invalid or missing bearer token."), frappe.AuthenticationError)


def _get_user_override(user_id):
	if not frappe.db.exists("User App Control", user_id):
		return None

	return frappe.db.get_value(
		"User App Control",
		user_id,
		["min_app_version", "selected_language", "ai_feature"],
		as_dict=True,
	)
