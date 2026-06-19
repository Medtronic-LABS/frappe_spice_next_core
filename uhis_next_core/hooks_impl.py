"""
Implementation of doc_events handlers wired in hooks.py.

  advance_sync_seq  — atomically increments Sync Seq Counter and stamps doc.sync_seq
  extract_observations — on_submit of program-form DocTypes, writes Observation rows
  validate_clinical_fields — validation gate: rejects DocFields without sva_ft mapping
"""

import json

import frappe

# ── advance_sync_seq ──────────────────────────────────────────────────────────


def advance_sync_seq(doc, event):
	"""Atomically increment the global Sync Seq Counter and stamp doc.sync_seq."""
	if not frappe.db.table_exists("Sync Seq Counter"):
		return
	if not frappe.db.exists("Sync Seq Counter", "global"):
		frappe.get_doc({"doctype": "Sync Seq Counter", "counter_name": "global", "current_seq": 0}).insert(
			ignore_permissions=True
		)
	frappe.db.sql(
		"UPDATE `tabSync Seq Counter` SET current_seq = LAST_INSERT_ID(current_seq + 1) WHERE name = 'global'"
	)
	seq = frappe.db.sql("SELECT LAST_INSERT_ID()")[0][0]
	frappe.db.set_value(doc.doctype, doc.name, "sync_seq", seq, update_modified=False)
	doc.sync_seq = seq


# ── extract_observations ──────────────────────────────────────────────────────


def extract_observations(doc, event):
	"""
	On submit of a program-form DocType, read each clinical field's sva_ft
	Property Setter and write one Observation row per coded field.
	Guardrail 1 — extraction seam.
	"""
	if not frappe.db.table_exists("Observation"):
		return

	meta = frappe.get_meta(doc.doctype)
	for field in meta.fields:
		mapping = _get_sva_ft(doc.doctype, field.fieldname)
		if not mapping:
			continue
		value = doc.get(field.fieldname)
		if value is None or value == "":
			continue

		concept_name = _ensure_concept(mapping)
		frappe.get_doc(
			{
				"doctype": "Observation",
				"client_uuid": frappe.generate_hash(length=36),
				"encounter": getattr(doc, "encounter", None),
				"case": getattr(doc, "case", None),
				"concept": concept_name,
				"value": str(value),
				"unit": mapping.get("unit", ""),
				"observed_dt": doc.creation,
				"source_form": doc.doctype,
				"source_docname": doc.name,
			}
		).insert(ignore_permissions=True)


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


def _ensure_concept(mapping):
	code_system = mapping.get("code_system", "")
	code = mapping.get("code", "")
	name = f"{code_system}|{code}"
	if not frappe.db.exists("Concept", name):
		frappe.get_doc(
			{
				"doctype": "Concept",
				"name": name,
				"code_system": code_system,
				"code": code,
				"display": mapping.get("fhir_concept", name),
			}
		).insert(ignore_permissions=True)
	return name


# ── set_case_geography_node ───────────────────────────────────────────────────


def set_case_geography_node(doc, event):
	"""Denormalize geography_node onto Case from Patient → Household at insert time."""
	if doc.geography_node:
		return  # already set (e.g. sync push carried the field)
	if not doc.patient:
		return
	household = frappe.db.get_value("Patient", doc.patient, "primary_household")
	if household:
		doc.geography_node = frappe.db.get_value("Household", household, "geography_node")


# ── validate_clinical_fields ──────────────────────────────────────────────────


def validate_clinical_fields(doc, event):
	"""
	Guardrail 2 — now a no-op. Clinical field validation moved to
	ClinicalQuestion.before_save (checks fhir_concept when clinical=1).
	"""
	pass
