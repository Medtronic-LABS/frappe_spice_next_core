"""
Reusable guard for endpoints authenticated via a caller-supplied
`X-Auth-Token: Bearer <token>` header (not Frappe's own session/API-key auth
— see spice_next_core.api.controls for why a distinct header name is required).

Usage (single decorator, preferred):
	from spice_next_core.auth.decorators import whitelist, current_remote_user_id

	@whitelist(methods=["POST"], remote_auth=True)
	def my_endpoint():
		user_id = current_remote_user_id()
		tenant_id = current_remote_tenant_id()  # None if the caller sent no tenantId header
		...

Usage (manual composition, if you need require_remote_auth standalone):
	from spice_next_core.auth.decorators import require_remote_auth, current_remote_user_id

	@frappe.whitelist(allow_guest=True, methods=["POST"])
	@require_remote_auth
	def my_endpoint():
		user_id = current_remote_user_id()
		...

`frappe.whitelist(...)` must always end up as the OUTERMOST decorator on the
module-level function: it registers the function it wraps by object identity
in Frappe's own global whitelist registries (frappe/__init__.py), not via an
attribute — wrapping it from the outside unbinds that registered object from
the module-level name, and the endpoint would fail Frappe's own whitelist
check at request time. `whitelist()` below gets this right internally (calls
frappe.whitelist(...) last); do not reverse the order if composing manually.
"""

import functools

import frappe
from frappe import _
from frappe.utils import now_datetime

from spice_next_core.auth.jwt_token_validator import JWTDecodeError, JWTTokenValidator


def _build_validator():
	settings = frappe.get_single("Spice Settings")
	return JWTTokenValidator(
		remote_auth_url=settings.remote_auth_url or None,
		client_tag=settings.remote_auth_client_tag or None,
	)


def require_remote_auth(fn):
	"""Validate the X-Auth-Token header before calling fn; reject otherwise.

	Sets frappe.local.remote_user_id (read via current_remote_user_id()) on
	success. Raises frappe.AuthenticationError on a missing/invalid token.
	Every call (success or rejection) is logged to Remote Auth Activity Log
	in the background — see _log_activity.
	"""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		endpoint = f"{fn.__module__}.{fn.__name__}"
		auth_header = frappe.get_request_header("X-Auth-Token")
		tenant_id = frappe.get_request_header("tenantId")
		try:
			user_id = _build_validator().extract_user_id(auth_header, tenant_id)
		except JWTDecodeError as e:
			_log_activity(endpoint, None, "Rejected", str(e))
			frappe.throw(_("Invalid or missing bearer token."), frappe.AuthenticationError)
			return  # unreachable — frappe.throw always raises

		frappe.local.remote_user_id = user_id
		frappe.local.remote_tenant_id = tenant_id
		_log_activity(endpoint, user_id, "Success", None)
		return fn(*args, **kwargs)

	return wrapper


def _log_activity(endpoint, user_id, status, reason):
	"""Enqueue a Remote Auth Activity Log write so it never adds latency to
	the request/response path itself."""
	frappe.enqueue(
		"spice_next_core.auth.decorators._create_activity_log",
		queue="short",
		now=False,
		endpoint=endpoint,
		user_id=user_id,
		status=status,
		reason=reason,
	)


def _create_activity_log(endpoint, user_id, status, reason):
	frappe.get_doc({
		"doctype": "Remote Auth Activity Log",
		"endpoint": endpoint,
		"user_id": user_id,
		"status": status,
		"reason": reason,
		"called_at": now_datetime(),
	}).insert(ignore_permissions=True)
	frappe.db.commit()


def current_remote_user_id():
	"""The user id resolved by @require_remote_auth for the current request."""
	return getattr(frappe.local, "remote_user_id", None)


def current_remote_tenant_id():
	"""The tenantId header value captured by @require_remote_auth for the
	current request. None if the caller didn't send one."""
	return getattr(frappe.local, "remote_tenant_id", None)


def whitelist(*args, remote_auth=False, **kwargs):
	"""Drop-in wrapper around frappe.whitelist(...) that optionally also
	requires a valid X-Auth-Token, collapsing two stacked decorators into
	one. All frappe.whitelist args/kwargs pass through unchanged.

	remote_auth=True implies allow_guest=True unless the caller already set
	it explicitly — this flow only exists for endpoints that bypass Frappe's
	own session auth in the first place.
	"""
	if remote_auth:
		kwargs.setdefault("allow_guest", True)

	def decorator(fn):
		if remote_auth:
			fn = require_remote_auth(fn)
		return frappe.whitelist(*args, **kwargs)(fn)

	return decorator
