"""
FHIR inbound pull — fetches resources from a configured FHIR R4 server and
upserts them as Frappe DocType records.

Dependency order: Patient → EpisodeOfCare(Case) → Encounter → Observation → Condition.
Uses _lastUpdated filter so each run only fetches changes since the previous pull.
"""

import frappe
from frappe.utils import now_datetime

from uhis_next_core.api.fhir import _gender_from_fhir


@frappe.whitelist()
def trigger_fhir_pull():
	"""Whitelisted: enqueue a pull job from the Desk."""
	cfg = frappe.get_single("UHIS Settings")
	if not cfg.fhir_sync_enabled or not cfg.fhir_server_url:
		frappe.throw("FHIR sync is not enabled or FHIR Server URL is not configured in UHIS Settings.")
	frappe.enqueue("uhis_next_core.fhir.pull.do_pull", queue="long", now=False)
	return {"status": "enqueued"}


def do_pull():
	"""Background job: pull FHIR resources and upsert into Frappe."""
	cfg = frappe.get_single("UHIS Settings")
	if not cfg.fhir_sync_enabled or not cfg.fhir_server_url:
		return

	base_url = cfg.fhir_server_url.rstrip("/")
	token = cfg.get_password("fhir_auth_token") if cfg.fhir_auth_token else None
	since_dt = str(cfg.fhir_last_pull_dt)[:19].replace(" ", "T") if cfg.fhir_last_pull_dt else None

	counts = {}
	for resource_type, upsert_fn in [
		("Patient", _upsert_patient),
		("EpisodeOfCare", _upsert_episode_of_care),
		("Encounter", _upsert_encounter),
		("Observation", _upsert_observation),
		("Condition", _upsert_condition),
	]:
		n = 0
		try:
			for resource in _iter_pages(base_url, resource_type, since_dt, token):
				try:
					upsert_fn(resource)
					n += 1
				except Exception:
					frappe.log_error(
						frappe.get_traceback(),
						f"FHIR pull upsert failed: {resource_type}/{resource.get('id')}",
					)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FHIR pull fetch failed: {resource_type}")
		counts[resource_type] = n

	frappe.db.set_value(
		"UHIS Settings",
		"UHIS Settings",
		"fhir_last_pull_dt",
		now_datetime(),
		update_modified=False,
	)
	frappe.db.commit()
	return counts


# ── page iterator ─────────────────────────────────────────────────────────────


def _iter_pages(base_url, resource_type, since_dt, token):
	"""Yield every resource from paginated FHIR search results."""
	import requests

	headers = {"Accept": "application/fhir+json"}
	if token:
		headers["Authorization"] = f"Bearer {token}"

	params = {"_count": 200}
	if since_dt:
		params["_lastUpdated"] = f"gt{since_dt}"

	url = f"{base_url}/{resource_type}"
	while url:
		resp = requests.get(url, params=params, headers=headers, timeout=30)
		resp.raise_for_status()
		bundle = resp.json()
		for entry in bundle.get("entry", []):
			resource = entry.get("resource", {})
			if resource.get("resourceType") == resource_type:
				yield resource
		# follow Bundle.link[relation=next]
		url = next(
			(lnk["url"] for lnk in bundle.get("link", []) if lnk.get("relation") == "next"),
			None,
		)
		params = {}  # next URL already has params encoded


# ── upsert functions ──────────────────────────────────────────────────────────


def _upsert_patient(r):
	uid = r.get("id", "")
	if not uid:
		return
	if frappe.db.exists("Patient", uid):
		return  # already present; push handles updates
	name_text = (r.get("name") or [{}])[0].get("text") or uid
	phone = next(
		(t.get("value") for t in r.get("telecom", []) if t.get("system") == "phone"),
		None,
	)
	doc = frappe.get_doc(
		{
			"doctype": "Patient",
			"client_uuid": uid,
			"full_name": name_text,
			"dob": r.get("birthDate"),
			"gender": _gender_from_fhir(r.get("gender")),
			"phone": phone,
		}
	)
	doc.insert(ignore_permissions=True)


def _upsert_episode_of_care(r):
	uid = r.get("id", "")
	if not uid or frappe.db.exists("Case", uid):
		return
	patient_ref = (r.get("patient") or {}).get("reference", "")
	patient_id = patient_ref.split("/")[-1] if patient_ref else ""
	if not frappe.db.exists("Patient", patient_id):
		return  # parent missing; skip
	status_map = {"active": "Active", "finished": "Closed", "waitlist": "Active"}
	status = status_map.get(r.get("status", "active"), "Active")
	programme = (r.get("type") or [{}])[0].get("text", "")
	period = r.get("period") or {}
	frappe.get_doc(
		{
			"doctype": "Case",
			"client_uuid": uid,
			"patient": patient_id,
			"programme": programme if frappe.db.exists("Programme", programme) else None,
			"status": status,
			"opened_on": (period.get("start") or "")[:10] or None,
			"closed_on": (period.get("end") or "")[:10] or None,
		}
	).insert(ignore_permissions=True)


def _upsert_encounter(r):
	uid = r.get("id", "")
	if not uid or frappe.db.exists("Encounter", uid):
		return
	# resolve case from episodeOfCare reference
	eoc_refs = r.get("episodeOfCare") or []
	case_id = eoc_refs[0].get("reference", "").split("/")[-1] if eoc_refs else ""
	if not case_id or not frappe.db.exists("Case", case_id):
		return
	enc_type = (r.get("type") or [{}])[0].get("text", "Routine")
	period = r.get("period") or {}
	facility_ref = (r.get("serviceProvider") or {}).get("reference", "")
	facility = facility_ref.split("/")[-1] if facility_ref else None
	frappe.get_doc(
		{
			"doctype": "Encounter",
			"client_uuid": uid,
			"case": case_id,
			"encounter_type": enc_type
			if enc_type in ("Routine", "Followup", "Emergency", "Referral")
			else "Routine",
			"encounter_dt": (period.get("start") or "")[:19].replace("T", " ") or None,
			"facility": facility if facility and frappe.db.exists("Facility", facility) else None,
		}
	).insert(ignore_permissions=True)


def _upsert_observation(r):
	uid = r.get("id", "")
	if not uid or frappe.db.exists("Observation", uid):
		return
	# resolve case via subject → Patient → most-recent case (best effort)
	subject_ref = (r.get("subject") or {}).get("reference", "")
	patient_id = subject_ref.split("/")[-1] if subject_ref else ""
	cases = frappe.get_all(
		"Case", filters={"patient": patient_id}, fields=["name"], order_by="opened_on desc", limit=1
	)
	if not cases:
		return
	case_id = cases[0].name
	# resolve concept from coding
	concept_name = _concept_from_coding(r.get("code") or {})
	if not concept_name:
		return
	value = _obs_value(r)
	if value is None:
		return
	frappe.get_doc(
		{
			"doctype": "Observation",
			"client_uuid": uid,
			"case": case_id,
			"concept": concept_name,
			"value": str(value),
			"observed_dt": (r.get("effectiveDateTime") or "")[:19].replace("T", " ") or None,
		}
	).insert(ignore_permissions=True)


def _upsert_condition(r):
	uid = r.get("id", "")
	if not uid or frappe.db.exists("Condition", uid):
		return
	subject_ref = (r.get("subject") or {}).get("reference", "")
	patient_id = subject_ref.split("/")[-1] if subject_ref else ""
	cases = frappe.get_all(
		"Case", filters={"patient": patient_id}, fields=["name"], order_by="opened_on desc", limit=1
	)
	if not cases:
		return
	case_id = cases[0].name
	concept_name = _concept_from_coding(r.get("code") or {})
	if not concept_name:
		return
	status_code = ((r.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code", "active")
	status_map = {"active": "Active", "resolved": "Resolved", "inactive": "Inactive"}
	onset = r.get("onsetDateTime", "")
	frappe.get_doc(
		{
			"doctype": "Condition",
			"client_uuid": uid,
			"case": case_id,
			"concept": concept_name,
			"clinical_status": status_map.get(status_code, "Active"),
			"onset_date": onset[:10] if onset else None,
		}
	).insert(ignore_permissions=True)


# ── concept resolution ────────────────────────────────────────────────────────


def _concept_from_coding(code_block):
	"""Resolve FHIR code block → Frappe Concept name (SYSTEM|code). Creates if absent."""
	for coding in code_block.get("coding", []):
		system_uri = coding.get("system", "")
		code = coding.get("code", "")
		if not system_uri or not code:
			continue
		system = _uri_to_system(system_uri)
		concept_name = f"{system}|{code}"
		if not frappe.db.exists("Concept", concept_name):
			try:
				frappe.get_doc(
					{
						"doctype": "Concept",
						"name": concept_name,
						"code_system": system,
						"code": code,
						"display": coding.get("display", concept_name),
					}
				).insert(ignore_permissions=True)
			except Exception:
				pass
		return concept_name
	return None


def _uri_to_system(uri):
	_map = {
		"http://loinc.org": "LOINC",
		"http://snomed.info/sct": "SNOMED",
		"http://hl7.org/fhir/sid/icd-10": "ICD10",
	}
	return _map.get(uri, "Custom")


def _obs_value(r):
	if "valueQuantity" in r:
		return r["valueQuantity"].get("value")
	if "valueString" in r:
		return r["valueString"]
	if "valueBoolean" in r:
		return "1" if r["valueBoolean"] else "0"
	if "valueInteger" in r:
		return r["valueInteger"]
	return None
