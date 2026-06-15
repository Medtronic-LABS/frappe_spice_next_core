"""
FHIR R4 egress mappers — invariant 7: FHIR is egress-only.
Nothing is stored as FHIR; these mappers read DocType rows and produce R4 resources.
"""

import json

import frappe

# ── whitelisted read endpoints ────────────────────────────────────────────────


@frappe.whitelist()
def read_patient(patient):
	doc = frappe.get_doc("Patient", patient)
	resource = {
		"resourceType": "Patient",
		"id": doc.client_uuid or doc.name,
		"identifier": [{"system": "urn:uhis:patient", "value": doc.client_uuid or doc.name}],
		"name": [{"text": doc.full_name}],
		"gender": _gender_fhir(doc.gender),
	}
	if doc.dob:
		resource["birthDate"] = str(doc.dob)
	if doc.phone:
		resource["telecom"] = [{"system": "phone", "value": doc.phone}]
	return resource


@frappe.whitelist()
def read_encounter(encounter):
	enc = frappe.get_doc("Encounter", encounter)
	patient_id = _patient_from_case(enc.case) if enc.case else ""
	case_uuid = frappe.db.get_value("Case", enc.case, "client_uuid") if enc.case else ""
	resource = {
		"resourceType": "Encounter",
		"id": enc.client_uuid or enc.name,
		"status": "finished",
		"class": {
			"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
			"code": "AMB",
		},
		"type": [{"text": enc.encounter_type or ""}],
		"subject": {"reference": f"Patient/{patient_id}"},
		"episodeOfCare": [{"reference": f"EpisodeOfCare/{case_uuid}"}],
		"period": {"start": str(enc.encounter_dt or enc.creation)},
	}
	if enc.facility:
		resource["serviceProvider"] = {"reference": f"Organization/{enc.facility}"}
	return resource


@frappe.whitelist()
def read_condition(condition):
	cond = frappe.get_doc("Condition", condition)
	patient_id = _patient_from_case(cond.case) if cond.case else ""
	concept = frappe.get_doc("Concept", cond.concept) if cond.concept else None
	resource = {
		"resourceType": "Condition",
		"id": cond.client_uuid or cond.name,
		"clinicalStatus": {
			"coding": [
				{
					"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
					"code": _condition_status(cond.clinical_status),
				}
			]
		},
		"subject": {"reference": f"Patient/{patient_id}"},
	}
	if concept:
		resource["code"] = {
			"coding": [
				{
					"system": _code_system_uri(concept.code_system),
					"code": concept.code,
					"display": concept.display,
				}
			]
		}
	if cond.onset_date:
		resource["onsetDateTime"] = str(cond.onset_date)
	return resource


@frappe.whitelist()
def read_referral(referral):
	ref = frappe.get_doc("Referral", referral)
	case = frappe.get_doc("Case", ref.case)
	patient_uuid = frappe.db.get_value("Patient", case.patient, "client_uuid") or case.patient
	case_uuid = case.client_uuid or case.name

	_status = {
		"Pending": "active",
		"Accepted": "active",
		"Completed": "completed",
		"Cancelled": "revoked",
	}.get(ref.status, "unknown")

	resource = {
		"resourceType": "ServiceRequest",
		"id": ref.client_uuid or ref.name,
		"status": _status,
		"intent": "order",
		"category": [
			{
				"coding": [
					{
						"system": "http://snomed.info/sct",
						"code": "3457005",
						"display": "Patient referral",
					}
				]
			}
		],
		"priority": "routine",
		"subject": {"reference": f"Patient/{patient_uuid}"},
		"basedOn": [{"reference": f"EpisodeOfCare/{case_uuid}"}],
		"authoredOn": str(ref.creation)[:19],
	}
	if ref.from_facility:
		resource["requester"] = {"reference": f"Organization/{ref.from_facility}"}
	if ref.to_facility:
		resource["performer"] = [{"reference": f"Organization/{ref.to_facility}"}]
	if ref.reason:
		resource["reasonCode"] = [{"text": ref.reason}]
	return resource


@frappe.whitelist()
def read_household(household):
	doc = frappe.get_doc("Household", household)
	patients = frappe.get_all(
		"Patient",
		filters={"primary_household": household},
		fields=["client_uuid", "name"],
	)
	return {
		"resourceType": "Group",
		"id": doc.client_uuid or doc.name,
		"type": "person",
		"actual": True,
		"member": [{"entity": {"reference": f"Patient/{p.client_uuid or p.name}"}} for p in patients],
	}


@frappe.whitelist()
def read_patient_bundle(patient):
	entries = []

	# Patient
	try:
		entries.append({"resource": read_patient(patient)})
	except Exception:
		pass

	cases = frappe.get_all("Case", filters={"patient": patient}, fields=["name"])
	case_names = [c.name for c in cases]

	# EpisodeOfCare (Cases)
	for case in cases:
		try:
			entries.append({"resource": to_episode_of_care(case.name)})
		except Exception:
			pass

	if case_names:
		# Encounters
		for enc in frappe.get_all("Encounter", filters={"case": ["in", case_names]}, fields=["name"]):
			try:
				entries.append({"resource": read_encounter(enc.name)})
			except Exception:
				pass

		# Observations
		for obs in frappe.get_all("Observation", filters={"case": ["in", case_names]}, fields=["name"]):
			try:
				entries.append({"resource": to_fhir_observation(obs.name)})
			except Exception:
				pass

		# Conditions
		for cond in frappe.get_all("Condition", filters={"case": ["in", case_names]}, fields=["name"]):
			try:
				entries.append({"resource": read_condition(cond.name)})
			except Exception:
				pass

	return {
		"resourceType": "Bundle",
		"type": "searchset",
		"total": len(entries),
		"entry": entries,
	}


# ── legacy endpoints (kept for backwards compatibility) ───────────────────────


@frappe.whitelist()
def to_questionnaire_response(form_doc_name, form_doctype):
	doc = frappe.get_doc(form_doctype, form_doc_name)
	meta = frappe.get_meta(form_doctype)
	items = []
	for field in meta.fields:
		if field.fieldtype in ("Section Break", "Column Break", "Tab Break", "HTML", "Button"):
			continue
		value = doc.get(field.fieldname)
		if value is None:
			continue
		mapping = _get_sva_ft(form_doctype, field.fieldname)
		answer = _fhir_answer(field.fieldtype, value)
		item = {
			"linkId": field.fieldname,
			"text": field.label or field.fieldname,
			"answer": [answer],
		}
		if mapping:
			item["definition"] = f"{mapping.get('code_system', '')}/{mapping.get('code', '')}"
		items.append(item)

	return {
		"resourceType": "QuestionnaireResponse",
		"id": form_doc_name,
		"status": "completed",
		"subject": {"reference": f"Patient/{getattr(doc, 'patient', '')}"},
		"authored": str(doc.creation),
		"item": items,
	}


@frappe.whitelist()
def to_fhir_observation(obs_name):
	obs = frappe.get_doc("Observation", obs_name)
	concept = frappe.get_doc("Concept", obs.concept) if obs.concept else None
	patient_id = _patient_from_case(obs.case) if obs.case else ""

	resource = {
		"resourceType": "Observation",
		"id": obs.client_uuid or obs_name,
		"status": "final",
		"subject": {"reference": f"Patient/{patient_id}"},
		"effectiveDateTime": str(obs.observed_dt or obs.creation),
		"valueString": obs.value,
	}

	if concept:
		resource["code"] = {
			"coding": [
				{
					"system": _code_system_uri(concept.code_system),
					"code": concept.code,
					"display": concept.display,
				}
			]
		}

	if obs.unit:
		resource["valueQuantity"] = {
			"value": _try_float(obs.value),
			"unit": obs.unit,
		}
		resource.pop("valueString", None)

	return resource


@frappe.whitelist()
def to_episode_of_care(case_name):
	case = frappe.get_doc("Case", case_name)
	return {
		"resourceType": "EpisodeOfCare",
		"id": case.client_uuid or case_name,
		"status": _eoc_status(case.status),
		"patient": {"reference": f"Patient/{case.patient}"},
		"period": {
			"start": str(case.opened_on or case.creation),
			**({"end": str(case.closed_on)} if case.closed_on else {}),
		},
		"type": [{"text": case.get("programme", "")}],
	}


# ── helpers ───────────────────────────────────────────────────────────────────


def _patient_from_case(case_name):
	return frappe.db.get_value("Case", case_name, "patient") or ""


def _gender_fhir(gender):
	return {"Male": "male", "Female": "female", "Other": "other"}.get(gender, "unknown")


def _gender_from_fhir(gender):
	return {"male": "Male", "female": "Female", "other": "Other"}.get(gender or "", "Prefer not to say")


def _condition_status(status):
	return {"Active": "active", "Resolved": "resolved", "Inactive": "inactive"}.get(status or "", "active")


def _eoc_status(status):
	return {"Active": "active", "Closed": "finished", "Resolved": "finished", "Referred": "active"}.get(
		status, "active"
	)


def _code_system_uri(system):
	_map = {
		"LOINC": "http://loinc.org",
		"SNOMED": "http://snomed.info/sct",
		"ICD10": "http://hl7.org/fhir/sid/icd-10",
	}
	return _map.get(system, system)


def _get_sva_ft(doctype, fieldname):
	raw = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": "sva_ft"},
		"value",
	)
	if not raw:
		return None
	try:
		return json.loads(raw)
	except (ValueError, TypeError):
		return None


def _fhir_answer(fieldtype, value):
	if fieldtype == "Check":
		return {"valueBoolean": bool(value)}
	if fieldtype == "Int":
		return {"valueInteger": int(value)}
	if fieldtype == "Float":
		return {"valueDecimal": float(value)}
	if fieldtype == "Date":
		return {"valueDate": str(value)}
	return {"valueString": str(value)}


def _try_float(val):
	try:
		return float(val)
	except (ValueError, TypeError):
		return None
