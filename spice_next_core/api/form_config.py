"""
Serialize Programme + Programme Form + DocType field metadata into the
form-config-schema.md wire format consumed by the Flutter client.
"""

import json

import frappe

_FIELDTYPE_MAP = {
	"Data": "string",
	"Int": "integer",
	"Float": "number",
	"Check": "boolean",
	"Date": "date",
	"Datetime": "datetime",
	"Select": "select",
	"Text": "text",
	"Link": "link",
	"Table": "table",
}


def serialize_programme(programme_name):
	forms = frappe.get_all(
		"Programme Form",
		filters={"parent": programme_name},
		fields=[
			"form_doctype",
			"sequence",
			"mandatory",
			"trigger_field",
			"trigger_operator",
			"trigger_value",
			"next_form",
		],
		order_by="sequence asc",
	)

	result = []
	for pf in forms:
		fields = _serialize_form_fields(pf.form_doctype)
		entry = {
			"form_doctype": pf.form_doctype,
			"sequence": pf.sequence,
			"mandatory": bool(pf.mandatory),
			"fields": fields,
		}
		if pf.trigger_field:
			entry["trigger"] = {
				"field": pf.trigger_field,
				"operator": pf.trigger_operator,
				"value": pf.trigger_value,
				"next_form": pf.next_form,
			}
		result.append(entry)
	return result


def serialize_clinical_questions():
	"""
	Return all Clinical Question records as the form-config-schema-compatible list.
	Included in sync.config under the 'clinical_questions' key.
	"""
	questions = frappe.get_all(
		"Clinical Question",
		fields=[
			"name",
			"label",
			"question_type",
			"sequence",
			"fieldtype",
			"options",
			"mandatory",
			"clinical",
			"fhir_concept",
			"min_age_months",
			"max_age_months",
			"gender",
			"visibility_expression",
			"mandatory_expression",
		],
		ignore_permissions=True,
	)

	programmes_by_question = {}
	for row in frappe.get_all(
		"Clinical Question Programme",
		filters={"parent": ["in", [q.name for q in questions]] if questions else ["", ]},
		fields=["parent", "programme"],
	):
		programmes_by_question.setdefault(row.parent, []).append(row.programme)

	# `programmes` (not form_group) is the sole scoping field now -- order by
	# each question's first programme, then its within-programme sequence.
	questions.sort(key=lambda q: ((programmes_by_question.get(q.name) or ["~"])[0], q.sequence))

	result = []
	for q in questions:
		if q.clinical and not q.fhir_concept:
			frappe.throw(f"Clinical question '{q.name}' has clinical=1 but no FHIR concept mapping.")
		concept = None
		if q.fhir_concept:
			c = frappe.get_value("Concept", q.fhir_concept, ["code_system", "code"], as_dict=True)
			if c:
				concept = {
					"fhir_concept": q.fhir_concept,
					"code_system": c.code_system,
					"code": c.code,
				}
		result.append(
			{
				"question_id": q.name,
				"label": q.label,
				"programmes": programmes_by_question.get(q.name, []),
				"question_type": q.question_type or "assessment",
				"sequence": q.sequence,
				"type": q.fieldtype,
				"mandatory": bool(q.mandatory),
				"clinical": bool(q.clinical),
				"options": [o.strip() for o in (q.options or "").split("\n") if o.strip()],
				"eligibility": {
					"min_age_months": q.min_age_months,
					"max_age_months": q.max_age_months,
					"gender": q.gender or "Any",
				},
				"visibility_expression": q.visibility_expression or None,
				"mandatory_expression": q.mandatory_expression or None,
				"concept": concept,
			}
		)
	return result


def _serialize_form_fields(doctype):
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return []

	fields = []
	for field in meta.fields:
		if field.fieldtype in ("Section Break", "Column Break", "Tab Break", "HTML", "Button"):
			continue
		entry = {
			"fieldname": field.fieldname,
			"label": field.label or field.fieldname,
			"type": _FIELDTYPE_MAP.get(field.fieldtype, "string"),
			"required": bool(field.reqd),
			"read_only": bool(field.read_only),
		}
		if field.fieldtype == "Select" and field.options:
			entry["options"] = [o.strip() for o in field.options.split("\n") if o.strip()]

		sva_ft = frappe.db.get_value(
			"Property Setter",
			{"doc_type": doctype, "field_name": field.fieldname, "property": "sva_ft"},
			"value",
		)
		if sva_ft:
			try:
				entry["sva_ft"] = json.loads(sva_ft)
			except (ValueError, TypeError):
				pass

		fields.append(entry)
	return fields
