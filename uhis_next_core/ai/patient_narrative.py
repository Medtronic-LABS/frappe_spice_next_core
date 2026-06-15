"""
AI-powered patient narrative generation.

Aggregates across all cases for a patient, reusing existing Case.ai_summary
where available so no extra LLM calls are made for cases already summarised.

Cascade:  case_narrative.generate_narrative()
          → enqueue_patient_narrative()
          → generate_patient_narrative()
          → enqueue_household_narrative()
"""

import json

import frappe

from uhis_next_core.ai.client import call_ai

_SYSTEM_PROMPT = (
	"You are a clinical supervisor assistant for a community health worker programme "
	"in sub-Saharan Africa. Given a patient's aggregate health data across all "
	"programmes (JSON, no identifiers), write a 2-3 sentence patient health profile "
	"covering: overall status across all programmes, the highest-priority concern, "
	"and the single most important recommended action. "
	"Be concise and clinical. Do not use patient names or any identifiers. "
	"Respond with plain text only — no markdown, no bullet points."
)


@frappe.whitelist()
def enqueue_patient_narrative(patient_name):
	"""Whitelisted endpoint called by patient.js when summary is stale."""
	frappe.enqueue(
		"uhis_next_core.ai.patient_narrative.generate_patient_narrative",
		patient_name=patient_name,
		queue="default",
		now=False,
	)


def generate_patient_narrative(patient_name):
	"""Background job: build context, call AI, store result, cascade to household."""
	cfg = frappe.get_single("UHIS Settings")
	if not cfg.ai_insights_enabled:
		return
	needs_key = cfg.ai_provider in ("Claude", "OpenAI")
	if needs_key:
		try:
			key = cfg.get_password("ai_api_key") or ""
		except Exception:
			key = ""
		if not key:
			return

	ctx = _build_patient_context(patient_name)
	text = call_ai(_SYSTEM_PROMPT, json.dumps(ctx))
	if not text:
		return

	provider = cfg.ai_provider or "Claude"
	model = cfg.ai_model or "claude-haiku-4-5-20251001"
	stamped = f"{text}\n\n— Generated {frappe.utils.today()} · {provider} / {model}"

	frappe.db.set_value(
		"Patient",
		patient_name,
		{
			"ai_summary": stamped,
			"ai_summary_generated_at": frappe.utils.now_datetime(),
		},
		update_modified=False,
	)

	# Cascade — mark household stale and enqueue its narrative
	household = frappe.db.get_value("Patient", patient_name, "primary_household")
	if household:
		frappe.db.set_value("Household", household, "ai_summary_generated_at", None, update_modified=False)
		frappe.enqueue(
			"uhis_next_core.ai.household_narrative.generate_household_narrative",
			household_name=household,
			queue="default",
			now=False,
		)


def _build_patient_context(patient_name):
	"""Assemble de-identified patient context across all cases."""
	today = frappe.utils.today()

	cases = frappe.get_all(
		"Case",
		filters={"patient": patient_name},
		fields=["name", "programme", "status", "risk_level", "ai_summary", "ai_summary_generated_at"],
		order_by="modified desc",
	)
	case_names = [c.name for c in cases]

	# Case count by status
	case_count = {"active": 0, "closed": 0, "referred": 0, "resolved": 0}
	for c in cases:
		key = (c.status or "").lower()
		if key in case_count:
			case_count[key] += 1

	# Highest risk
	_risk_rank = {"High": 3, "Moderate": 2, "Low": 1}
	highest_risk = max(
		(c.risk_level for c in cases if c.risk_level),
		key=lambda r: _risk_rank.get(r, 0),
		default="Unknown",
	)

	active_progs = list({c.programme for c in cases if c.status == "Active" and c.programme})

	# Conditions across all cases
	conditions = []
	if case_names:
		conditions = frappe.get_all(
			"Condition",
			filters={"case": ["in", case_names]},
			fields=["concept", "clinical_status"],
		)

	# Latest vitals across all cases
	latest_vitals = {}
	if case_names:
		for row in frappe.get_all(
			"Observation",
			filters={"case": ["in", case_names]},
			fields=["concept", "value", "observed_dt"],
			order_by="observed_dt desc",
		):
			if row.concept not in latest_vitals:
				try:
					latest_vitals[row.concept] = float(row.value)
				except (ValueError, TypeError):
					latest_vitals[row.concept] = row.value

	# Days since last encounter
	days_since_encounter = None
	if case_names:
		enc_rows = frappe.get_all(
			"Encounter",
			filters={"case": ["in", case_names]},
			fields=["encounter_dt"],
			order_by="encounter_dt desc",
			limit=1,
		)
		if enc_rows and enc_rows[0].encounter_dt:
			enc_date = frappe.utils.getdate(str(enc_rows[0].encounter_dt)[:10])
			days_since_encounter = (frappe.utils.getdate(today) - enc_date).days

	# Open / overdue tasks
	open_tasks = (
		frappe.get_all(
			"Task",
			filters={"case": ["in", case_names], "status": ["in", ["Open", "In Progress"]]},
			fields=["due_date"],
		)
		if case_names
		else []
	)
	overdue_count = sum(
		1 for t in open_tasks if t.due_date and frappe.utils.getdate(t.due_date) < frappe.utils.getdate(today)
	)

	active_referral = bool(
		frappe.get_all(
			"Referral",
			filters={"case": ["in", case_names], "status": ["in", ["Pending", "Accepted"]]},
			limit=1,
		)
		if case_names
		else []
	)

	# Reuse existing Case-level AI text — avoids N extra LLM calls
	case_summaries = [
		c.ai_summary.split("\n\n— Generated")[0].strip()
		for c in cases
		if c.ai_summary and c.ai_summary_generated_at
	]

	return {
		"active_programmes": active_progs,
		"case_count": case_count,
		"highest_risk_level": highest_risk,
		"conditions": [{"concept": c.concept, "status": c.clinical_status} for c in conditions],
		"latest_vitals": latest_vitals,
		"days_since_encounter": days_since_encounter,
		"open_tasks": len(open_tasks),
		"overdue_tasks": overdue_count,
		"active_referral": active_referral,
		"case_summaries": case_summaries,
	}
