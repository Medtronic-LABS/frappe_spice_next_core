"""
AI-powered household narrative generation.

Aggregates patient-level summaries for all members of a household.
Reuses Patient.ai_summary where available.

Cascade:  patient_narrative.generate_patient_narrative()
          → generate_household_narrative()
          (Facility summary is on-demand only.)
"""

import json

import frappe

from uhis_next_core.ai.client import call_ai

_SYSTEM_PROMPT = (
	"You are a CHW programme supervisor in sub-Saharan Africa. "
	"Given a household's aggregate health profile (JSON, no names or identifiers), "
	"write a 2-3 sentence household brief covering: the overall health burden, "
	"the highest-priority member concern, and the most important recommended "
	"home visit or action. "
	"Be concise and clinical. Respond with plain text only — no markdown, no bullet points."
)


@frappe.whitelist()
def enqueue_household_narrative(household_name):
	"""Whitelisted endpoint called by household.js when summary is stale."""
	frappe.enqueue(
		"uhis_next_core.ai.household_narrative.generate_household_narrative",
		household_name=household_name,
		queue="default",
		now=False,
	)


def generate_household_narrative(household_name):
	"""Background job: build context, call AI, store result."""
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

	ctx = _build_household_context(household_name)
	text = call_ai(_SYSTEM_PROMPT, json.dumps(ctx))
	if not text:
		return

	provider = cfg.ai_provider or "Claude"
	model = cfg.ai_model or "claude-haiku-4-5-20251001"
	stamped = f"{text}\n\n— Generated {frappe.utils.today()} · {provider} / {model}"

	frappe.db.set_value(
		"Household",
		household_name,
		{
			"ai_summary": stamped,
			"ai_summary_generated_at": frappe.utils.now_datetime(),
		},
		update_modified=False,
	)


def _age_group(dob_str):
	if not dob_str:
		return "unknown"
	today = frappe.utils.getdate(frappe.utils.today())
	dob = frappe.utils.getdate(str(dob_str)[:10])
	age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
	if age < 18:
		return "child"
	if age <= 60:
		return "adult"
	return "elder"


def _build_household_context(household_name):
	"""Assemble de-identified household context from member patient data."""
	patients = frappe.get_all(
		"Patient",
		filters={"primary_household": household_name},
		fields=["name", "dob", "gender", "ai_summary", "ai_summary_generated_at"],
	)

	member_profiles = []
	high_risk_count = 0
	active_cases_total = 0
	pending_referrals_total = 0

	for pt in patients:
		cases = frappe.get_all(
			"Case",
			filters={"patient": pt.name},
			fields=["name", "status", "risk_level"],
		)
		case_names = [c.name for c in cases]
		active = [c for c in cases if c.status == "Active"]
		active_cases_total += len(active)

		risk_levels = [c.risk_level for c in cases if c.risk_level]
		is_high_risk = "High" in risk_levels
		if is_high_risk:
			high_risk_count += 1

		if case_names:
			refs = frappe.get_all(
				"Referral",
				filters={"case": ["in", case_names], "status": ["in", ["Pending", "Accepted"]]},
				limit=1,
			)
			if refs:
				pending_referrals_total += 1

		summary_text = None
		if pt.ai_summary and pt.ai_summary_generated_at:
			summary_text = pt.ai_summary.split("\n\n— Generated")[0].strip()

		member_profiles.append(
			{
				"gender": (pt.gender or "Unknown")[0],
				"age_group": _age_group(pt.dob),
				"active_cases": len(active),
				"high_risk": is_high_risk,
				"summary": summary_text,
			}
		)

	return {
		"member_count": len(patients),
		"members_with_cases": sum(1 for m in member_profiles if m["active_cases"] > 0),
		"high_risk_count": high_risk_count,
		"active_cases": active_cases_total,
		"pending_referrals": pending_referrals_total,
		"member_profiles": member_profiles,
	}
