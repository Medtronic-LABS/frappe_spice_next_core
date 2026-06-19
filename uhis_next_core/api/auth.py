"""
Auth endpoint for the Flutter CHW client.

  uhis_next_core.api.auth.login   PIN login → api_key / api_secret + SK profile

Request:
  {
    "username": "rk@uhis.org",
    "password": "<pin or password>",
    "device": {
      "device_id": "<stable uuid>",
      "model": "Samsung Galaxy A52",
      "os_version": "Android 13",
      "app_version": "1.0.0"
    }
  }

Response (Frappe wraps in {"message": ...}):
  {
    "api_key": "...",
    "api_secret": "...",
    "profile": {
      "user_id": "rk@uhis.org",
      "full_name": "Rina Khatun",
      "full_name_bn": "রিনা খাতুন",
      "programme": "ncd",
      "geography_id": "geo-dhaka-mirpur",
      "photo_url": null
    }
  }
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def login(username=None, password=None, device=None):
	# Accept both flat JSON body keys (Flutter client) and a legacy `payload` string.
	username = username or ""
	password = password or ""
	device = frappe.parse_json(device) if isinstance(device, str) else (device or {})

	if not username or not password:
		frappe.throw(_("username and password are required"), frappe.AuthenticationError)

	# Authenticate against Frappe's user table.
	try:
		frappe.local.login_manager.authenticate(username, password)
	except frappe.AuthenticationError:
		frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)

	user = frappe.get_doc("User", username)

	# Ensure user has an API key/secret pair; generate if missing.
	# Only save when we generated new credentials — saving with an unchanged
	# password field can corrupt the stored hash in Frappe v15.
	needs_save = False
	if not user.api_key:
		user.api_key = frappe.generate_hash(length=15)
		needs_save = True
	if not user.api_secret:
		api_secret = frappe.generate_hash(length=15)
		user.api_secret = api_secret
		needs_save = True
	else:
		api_secret = user.get_password("api_secret")

	if needs_save:
		user.save(ignore_permissions=True)

	# Register / update the device record so admins can revoke access per-device.
	try:
		_upsert_device(username, device)
	except Exception:
		pass  # Device registration is non-critical; never block authentication.

	profile = _get_sk_profile(username)

	return {
		"api_key": user.api_key,
		"api_secret": api_secret,
		"profile": profile,
	}


def _upsert_device(user, device):
	device_id = device.get("device_id")
	if not device_id:
		return

	if frappe.db.exists("SK Device", device_id):
		frappe.db.set_value(
			"SK Device",
			device_id,
			{
				"model": device.get("model") or "",
				"os_version": device.get("os_version") or "",
				"app_version": device.get("app_version") or "",
				"last_seen": frappe.utils.now(),
			},
			update_modified=False,
		)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "SK Device",
				"name": device_id,
				"user": user,
				"model": device.get("model") or "",
				"os_version": device.get("os_version") or "",
				"app_version": device.get("app_version") or "",
				"last_seen": frappe.utils.now(),
				"is_active": 1,
			}
		)
		doc.insert(ignore_permissions=True)


def _get_sk_profile(user):
	provider = frappe.db.get_value(
		"Provider",
		{"user": user},
		["name", "full_name", "full_name_bn", "programme", "geography_node", "photo"],
		as_dict=True,
	)

	if provider:
		return {
			"user_id": user,
			"full_name": provider.full_name or user,
			"full_name_bn": provider.full_name_bn or "",
			"programme": provider.programme or "",
			"geography_id": provider.geography_node or "",
			"photo_url": provider.photo or None,
		}

	# Fallback for System Manager / admin accounts without a Provider record.
	full_name = frappe.db.get_value("User", user, "full_name") or user
	return {
		"user_id": user,
		"full_name": full_name,
		"full_name_bn": "",
		"programme": "",
		"geography_id": "",
		"photo_url": None,
	}
