"""
AI-powered case narrative generation.

Lifecycle:
  1. Observation.after_insert → _mark_summary_stale() clears ai_summary_generated_at
  2. Case form load (case.js) detects stale state → calls enqueue_narrative()
  3. Background job generate_narrative() calls the configured AI provider and
     stores the result in Case.ai_summary + Case.ai_summary_generated_at

The context sent to the AI is de-identified: no patient names or IDs, only
clinical codes and aggregate counts.
"""

import json

import frappe

from spice_next_core.ai.client import call_ai

_SYSTEM_PROMPT = (
	"You are a clinical supervisor assistant for a community health worker programme "
	"in sub-Saharan Africa. Given structured case data (JSON), write a 2-3 sentence "
	"clinical briefing covering: current health status, key concerns, and the single "
	"most important recommended next action. Be concise and clinical. "
	"Do not use patient names or any identifiers. "
	"Respond with plain text only — no markdown, no bullet points."
)


@frappe.whitelist()
def enqueue_narrative(case_name):
	"""Whitelisted endpoint called by case.js when summary is stale."""
	frappe.enqueue(
		"spice_next_core.ai.case_narrative.generate_narrative",
		case_name=case_name,
		queue="default",
		now=False,
	)


def _mark_summary_stale(doc, event):
	"""doc_events hook — Observation.after_insert.
	Invalidates the stored AI summary so the next form load triggers regeneration.
	"""
	case_name = getattr(doc, "case", None)
	if case_name:
		frappe.db.set_value("Case", case_name, "ai_summary_generated_at", None, update_modified=False)


def generate_narrative(case_name):
	"""Background job: build context, call AI, store result."""
	cfg = frappe.get_single("Spice Settings")
	if not cfg.ai_insights_enabled:
		return
	# Local (Ollama) needs no API key; cloud providers do
	needs_key = cfg.ai_provider in ("Claude", "OpenAI")
	if needs_key and not cfg.ai_api_key:
		return

	ctx = _build_context(case_name)
	text = call_ai(_SYSTEM_PROMPT, json.dumps(ctx))
	if not text:
		return

	provider = cfg.ai_provider or "Claude"
	model = cfg.ai_model or "claude-haiku-4-5-20251001"
	stamped = f"{text}\n\n— Generated {frappe.utils.today()} · {provider} / {model}"

	frappe.db.set_value(
		"Case",
		case_name,
		{
			"ai_summary": stamped,
			"ai_summary_generated_at": frappe.utils.now_datetime(),
		},
		update_modified=False,
	)

	# Cascade — mark patient summary stale and regenerate
	patient = frappe.db.get_value("Case", case_name, "patient")
	if patient:
		frappe.db.set_value("Patient", patient, "ai_summary_generated_at", None, update_modified=False)
		frappe.enqueue(
			"spice_next_core.ai.patient_narrative.generate_patient_narrative",
			patient_name=patient,
			queue="default",
			now=False,
		)


def _build_context(case_name):
	"""Assemble de-identified clinical context for the AI prompt."""
	case = frappe.get_doc("Case", case_name)

	today = frappe.utils.today()
	days_open = (
		(frappe.utils.getdate(today) - frappe.utils.getdate(case.opened_on)).days if case.opened_on else None
	)

	# Latest encounter
	enc_rows = frappe.get_all(
		"Encounter",
		filters={"case": case_name},
		fields=["encounter_type", "encounter_dt"],
		order_by="encounter_dt desc",
		limit=1,
	)
	days_since_encounter = None
	if enc_rows and enc_rows[0].encounter_dt:
		enc_date = frappe.utils.getdate(str(enc_rows[0].encounter_dt)[:10])
		days_since_encounter = (frappe.utils.getdate(today) - enc_date).days

	# Conditions
	conditions = frappe.get_all(
		"Condition",
		filters={"case": case_name},
		fields=["concept", "clinical_status"],
	)

	# Latest vitals (concept code → numeric value)
	obs_rows = frappe.get_all(
		"Observation",
		filters={"case": case_name},
		fields=["concept", "value", "observed_dt"],
		order_by="observed_dt desc",
	)
	latest_vitals = {}
	for row in obs_rows:
		if row.concept not in latest_vitals:
			try:
				latest_vitals[row.concept] = float(row.value)
			except (ValueError, TypeError):
				latest_vitals[row.concept] = row.value

	# Tasks
	open_tasks = frappe.get_all(
		"Task",
		filters={"case": case_name, "status": ["in", ["Open", "In Progress"]]},
		fields=["description", "due_date", "status"],
	)
	overdue_count = sum(
		1 for t in open_tasks if t.due_date and frappe.utils.getdate(t.due_date) < frappe.utils.getdate(today)
	)

	# Active referral
	referral = frappe.get_all(
		"Referral",
		filters={"case": case_name, "status": ["in", ["Pending", "Accepted"]]},
		fields=["to_facility", "status"],
		limit=1,
	)

	return {
		"programme": case.programme,
		"status": case.status,
		"days_open": days_open,
		"risk_level": case.risk_level or "Unknown",
		"risk_factors": case.risk_factors or "",
		"conditions": [{"concept": c.concept, "status": c.clinical_status} for c in conditions],
		"latest_vitals": latest_vitals,
		"days_since_encounter": days_since_encounter,
		"open_task_count": len(open_tasks),
		"overdue_task_count": overdue_count,
		"active_referral": bool(referral),
		"referral_destination": referral[0].to_facility if referral else None,
	}
