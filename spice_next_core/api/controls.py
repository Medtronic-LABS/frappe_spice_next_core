"""
Mobile app version / feature control endpoint.

  spice_next_core.api.controls.get_controls

Public endpoint (allow_guest=True, implied by remote_auth=True below)
authenticated via a caller-supplied `X-Auth-Token: Bearer <JWT>` header — NOT
Frappe session/token auth. Guarded by spice_next_core.auth.decorators.whitelist's
remote_auth=True, which validates the token against the remote auth-service
(or falls back to unverified local decode when Spice Settings.remote_auth_url
is blank — dev/test only). See spice_next_core.auth.jwt_token_validator.JWTTokenValidator
for both phases.

NOTE: the token is carried in a custom `X-Auth-Token` header, not the
standard `Authorization` header. Frappe's own request middleware
(frappe.auth.validate_auth) inspects every `Authorization: <scheme> <token>`
header globally — before allow_guest dispatch — and hard-fails with
AuthenticationError if the scheme isn't a Frappe-recognized OAuth/API-key
credential. Since our unsigned mobile JWT isn't such a credential, using
`Authorization` here would always be rejected before this function ever
runs. A distinct header name sidesteps that middleware entirely.

Request:
  POST /api/method/spice_next_core.api.controls.get_controls
  Header:  X-Auth-Token: Bearer <jwt>

Response (Frappe wraps in {"message": ...}):
  {
    "minAppVersion": "1.4.0",
    "latestAppVersion": "1.6.2",
    "language": "bn",
    "AIFeature": true,
    "aiWidgets": {
      "step1SummaryEnabled": true,
      "step1AsrEnabled": true,
      "step2AsrEnabled": true,
      "step3SummaryEnabled": true,
      "step3ReferralAlertEnabled": true,
      "step3WhatsAppEnabled": true
    },
    "vadTuning": {
      "enterMarginDb": 9,
      "sustainMarginDb": 6,
      "floorCeilingDbfs": -35,
      "floorAlpha": 0.08,
      "bootstrapMs": 500,
      "debounceMs": 180,
      "hangoverMs": 700,
      "preRollMs": 350
    }
  }

Override rules:
  - latestAppVersion always comes from Spice Settings (no per-user override).
  - minAppVersion / language: per-user value from User App Control wins if
    non-empty, else the Spice Settings system-level value.
  - AIFeature: if a User App Control row exists for the caller, its
    ai_feature value (0 or 1) always wins; otherwise Spice Settings'
    ai_feature_enabled is used.
  - aiWidgets / vadTuning: system-level only, always read straight from
    Spice Settings — no per-user override exists for these yet (matches the
    Flutter client's own three-tier doctrine: build-time default -> on-device
    override -> this system-level tier; a future per-user tier can be added
    here without changing the response shape).
"""

import frappe

from spice_next_core.auth.decorators import current_remote_user_id, whitelist


@whitelist(methods=["POST"], remote_auth=True)
def get_controls():
	user_id = current_remote_user_id()

	settings = frappe.get_single("Spice Settings")
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
		"aiWidgets": _ai_widgets(settings),
		"vadTuning": _vad_tuning(settings),
	}


def _ai_widgets(settings):
	return {
		"step1SummaryEnabled": bool(settings.step1_summary_enabled),
		"step1AsrEnabled": bool(settings.step1_asr_enabled),
		"step2AsrEnabled": bool(settings.step2_asr_enabled),
		"step3SummaryEnabled": bool(settings.step3_summary_enabled),
		"step3ReferralAlertEnabled": bool(settings.step3_referral_alert_enabled),
		"step3WhatsAppEnabled": bool(settings.step3_whatsapp_enabled),
	}


def _vad_tuning(settings):
	return {
		"enterMarginDb": settings.vad_enter_margin_db,
		"sustainMarginDb": settings.vad_sustain_margin_db,
		"floorCeilingDbfs": settings.vad_floor_ceiling_dbfs,
		"floorAlpha": settings.vad_floor_alpha,
		"bootstrapMs": settings.vad_bootstrap_ms,
		"debounceMs": settings.vad_debounce_ms,
		"hangoverMs": settings.vad_hangover_ms,
		"preRollMs": settings.vad_preroll_ms,
	}


def _get_user_override(user_id):
	if not frappe.db.exists("User App Control", user_id):
		return None

	return frappe.db.get_value(
		"User App Control",
		user_id,
		["min_app_version", "selected_language", "ai_feature"],
		as_dict=True,
	)
