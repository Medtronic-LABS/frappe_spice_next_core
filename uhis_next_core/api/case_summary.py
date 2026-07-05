"""
Case summary API — returns all data needed to render the Case Overview panel.
Mirrors the structure of api/patient_summary.py::get_health_summary.
"""

import frappe

# LOINC/SNOMED concept keys — must stay in sync with _VITALS_META in patient.js
_VITALS_CONCEPTS = [
	"LOINC|8480-6",  # Systolic BP
	"LOINC|8462-4",  # Diastolic BP
	"LOINC|2339-0",  # Blood glucose
	"LOINC|4548-4",  # HbA1c
	"LOINC|29463-7",  # Weight
	"LOINC|39156-5",  # BMI
	"LOINC|8302-2",  # Height
]

# Concept thresholds mirrored from risk/score.py for inline "alert" flags
_VITALS_WARN = {
	"LOINC|8480-6": 139.0,
	"LOINC|8462-4": 89.0,
	"LOINC|2339-0": 6.9,
	"LOINC|4548-4": 6.9,
	"LOINC|39156-5": 24.9,
}

# Concept → human-readable label
_VITALS_LABEL = {
	"LOINC|8480-6": "Systolic BP",
	"LOINC|8462-4": "Diastolic BP",
	"LOINC|2339-0": "Blood Glucose",
	"LOINC|4548-4": "HbA1c",
	"LOINC|29463-7": "Weight",
	"LOINC|39156-5": "BMI",
	"LOINC|8302-2": "Height",
}

_VITALS_UNIT = {
	"LOINC|8480-6": "mmHg",
	"LOINC|8462-4": "mmHg",
	"LOINC|2339-0": "mmol/L",
	"LOINC|4548-4": "%",
	"LOINC|29463-7": "kg",
	"LOINC|39156-5": "kg/m²",
	"LOINC|8302-2": "cm",
}


@frappe.whitelist(methods=["POST"])
def get_case_summary(case):
	doc = frappe.get_doc("Case", case)

	# Patient demographics
	patient_doc = frappe.get_doc("Patient", doc.patient) if doc.patient else None
	patient_name = getattr(patient_doc, "full_name", "—") if patient_doc else "—"
	patient_gender = getattr(patient_doc, "gender", "—") if patient_doc else "—"
	patient_dob = str(patient_doc.dob) if (patient_doc and patient_doc.dob) else None

	# Days open
	today = frappe.utils.today()
	days_open = None
	if doc.opened_on:
		days_open = (frappe.utils.getdate(today) - frappe.utils.getdate(doc.opened_on)).days

	# Encounters
	enc_rows = frappe.get_all(
		"Encounter",
		filters={"case": case},
		fields=["encounter_type", "encounter_dt", "provider", "facility"],
		order_by="encounter_dt desc",
	)
	last_encounter = None
	days_since_encounter = None
	if enc_rows:
		e = enc_rows[0]
		last_encounter = {
			"encounter_type": e.encounter_type,
			"encounter_dt": str(e.encounter_dt)[:10],
			"provider": e.provider,
			"facility": e.facility,
		}
		if e.encounter_dt:
			enc_date = frappe.utils.getdate(str(e.encounter_dt)[:10])
			days_since_encounter = (frappe.utils.getdate(today) - enc_date).days

	# Latest vitals (filtered to known concepts, most-recent-first)
	obs_rows = frappe.get_all(
		"Observation",
		filters={"case": case, "concept": ["in", _VITALS_CONCEPTS]},
		fields=["concept", "value", "unit", "observed_dt"],
		order_by="observed_dt desc",
	)
	latest_vitals = {}
	for row in obs_rows:
		if row.concept not in latest_vitals:
			warn_above = _VITALS_WARN.get(row.concept)
			is_alert = False
			if warn_above is not None:
				try:
					is_alert = float(row.value) > warn_above
				except (ValueError, TypeError):
					pass
			latest_vitals[row.concept] = {
				"label": _VITALS_LABEL.get(row.concept, row.concept),
				"value": row.value,
				"unit": _VITALS_UNIT.get(row.concept, row.unit or ""),
				"date": str(row.observed_dt)[:10],
				"is_alert": is_alert,
			}

	# Conditions
	conditions = frappe.get_all(
		"Condition",
		filters={"case": case},
		fields=["concept", "clinical_status", "onset_date"],
		order_by="onset_date asc",
	)
	condition_list = []
	for c in conditions:
		label = frappe.db.get_value("Concept", c.concept, "display") if c.concept else c.concept
		condition_list.append(
			{
				"concept": c.concept,
				"label": label or c.concept,
				"status": c.clinical_status,
			}
		)

	# Tasks
	all_tasks = frappe.get_all(
		"Task",
		filters={"case": case, "status": ["in", ["Open", "In Progress"]]},
		fields=["description", "due_date", "status"],
		order_by="due_date asc",
	)
	overdue_tasks = [
		t for t in all_tasks if t.due_date and frappe.utils.getdate(t.due_date) < frappe.utils.getdate(today)
	]

	# Active referral
	referral = None
	ref_rows = frappe.get_all(
		"Referral",
		filters={"case": case, "status": ["in", ["Pending", "Accepted"]]},
		fields=["to_facility", "status"],
		limit=1,
	)
	if ref_rows:
		referral = {"to_facility": ref_rows[0].to_facility, "status": ref_rows[0].status}

	return {
		"patient_name": patient_name,
		"patient_gender": patient_gender,
		"patient_dob": patient_dob,
		"care_team": doc.care_team,
		"programme": doc.programme,
		"status": doc.status,
		"days_open": days_open,
		"last_encounter": last_encounter,
		"days_since_encounter": days_since_encounter,
		"encounter_count": len(enc_rows),
		"latest_vitals": latest_vitals,
		"conditions": condition_list,
		"open_task_count": len(all_tasks),
		"overdue_tasks": [{"description": t.description, "due_date": str(t.due_date)} for t in overdue_tasks],
		"referral": referral,
		"risk_level": doc.risk_level or None,
		"risk_factors": doc.risk_factors or "",
		"ai_summary": doc.ai_summary or "",
		"ai_summary_generated_at": str(doc.ai_summary_generated_at) if doc.ai_summary_generated_at else None,
		"ai_insights_enabled": _ai_enabled(),
	}


def _ai_enabled():
	try:
		return bool(frappe.get_single("UHIS Settings").ai_insights_enabled)
	except Exception:
		return False
