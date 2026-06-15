"""
FHIR outbound push — enqueued via doc_events after clinical records are saved.
Silent no-op when FHIR sync is disabled or server URL is not configured in UHIS Settings.
"""

import frappe

_FHIR_MAP = {
	"Patient": ("Patient", "uhis_next_core.api.fhir.read_patient"),
	"Encounter": ("Encounter", "uhis_next_core.api.fhir.read_encounter"),
	"Observation": ("Observation", "uhis_next_core.api.fhir.to_fhir_observation"),
	"Case": ("EpisodeOfCare", "uhis_next_core.api.fhir.to_episode_of_care"),
	"Condition": ("Condition", "uhis_next_core.api.fhir.read_condition"),
	"Referral": ("ServiceRequest", "uhis_next_core.api.fhir.read_referral"),
}


def enqueue_fhir_push(doc, event):
	"""doc_events hook: enqueue a background push if FHIR sync is configured."""
	if doc.doctype not in _FHIR_MAP:
		return
	try:
		cfg = frappe.get_single("UHIS Settings")
	except Exception:
		return
	if not cfg.fhir_sync_enabled or not cfg.fhir_server_url:
		return
	frappe.enqueue(
		"uhis_next_core.fhir.push.do_push",
		doctype=doc.doctype,
		name=doc.name,
		queue="default",
		now=False,
	)


def do_push(doctype, name):
	"""Background job: map DocType record → FHIR resource → HTTP PUT to server."""
	cfg = frappe.get_single("UHIS Settings")
	if not cfg.fhir_sync_enabled or not cfg.fhir_server_url:
		return

	base_url = cfg.fhir_server_url.rstrip("/")
	resource_type, mapper_path = _FHIR_MAP[doctype]

	try:
		mapper_fn = frappe.get_attr(mapper_path)
		resource = mapper_fn(name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"FHIR map failed: {doctype}/{name}")
		return

	resource_id = resource.get("id") or name
	url = f"{base_url}/{resource_type}/{resource_id}"

	headers = {"Content-Type": "application/fhir+json"}
	if cfg.fhir_auth_token:
		token = cfg.get_password("fhir_auth_token")
		headers["Authorization"] = f"Bearer {token}"

	try:
		import requests

		resp = requests.put(url, json=resource, headers=headers, timeout=10)
		resp.raise_for_status()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"FHIR push failed: {url}")
