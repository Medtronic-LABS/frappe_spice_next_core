"""
AI-powered facility narrative generation.

Generates on-demand only (triggered by a button in the Facility form).
Aggregates counts across all cases linked via Care Team → Facility, plus
the top-3 highest-risk patient summaries for qualitative context.
No individual patient data is exposed — only aggregates + anonymised snippets.
"""

import json

import frappe

from spice_next_core.ai.client import call_ai

_SYSTEM_PROMPT = (
	"You are a regional health supervisor in sub-Saharan Africa. "
	"Given facility-level aggregate health data (JSON), write a 3-4 sentence "
	"facility situation report covering: current patient load, the highest-risk "
	"programme areas, key operational concerns (overdue tasks, pending referrals), "
	"and one recommended priority action for facility management. "
	"Be concise and clinical. Respond with plain text only — no markdown, no bullet points."
)


@frappe.whitelist()
def enqueue_facility_narrative(facility_name):
	"""Whitelisted endpoint triggered by the Generate button in Facility form."""
	frappe.enqueue(
		"spice_next_core.ai.facility_narrative.generate_facility_narrative",
		facility_name=facility_name,
		queue="default",
		now=False,
	)


def generate_facility_narrative(facility_name):
	"""Background job: build context, call AI, store result."""
	cfg = frappe.get_single("Spice Settings")
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

	ctx = _build_facility_context(facility_name)
	text = call_ai(_SYSTEM_PROMPT, json.dumps(ctx))
	if not text:
		return

	provider = cfg.ai_provider or "Claude"
	model = cfg.ai_model or "claude-haiku-4-5-20251001"
	stamped = f"{text}\n\n— Generated {frappe.utils.today()} · {provider} / {model}"

	frappe.db.set_value(
		"Facility",
		facility_name,
		{
			"ai_summary": stamped,
			"ai_summary_generated_at": frappe.utils.now_datetime(),
		},
		update_modified=False,
	)


def _build_facility_context(facility_name):
	"""Assemble de-identified facility-level aggregate context."""
	today = frappe.utils.today()

	# Care teams at this facility
	care_teams = frappe.get_all(
		"Care Team",
		filters={"facility": facility_name},
		fields=["name"],
	)
	team_names = [t.name for t in care_teams]

	if not team_names:
		return {
			"facility_type": frappe.db.get_value("Facility", facility_name, "facility_type"),
			"total_active_cases": 0,
			"high_risk_count": 0,
			"cases_by_programme": {},
			"pending_referrals": 0,
			"overdue_tasks": 0,
			"recent_encounters_7d": 0,
			"top_risk_factors": [],
			"top_patient_summaries": [],
		}

	# All cases at this facility
	all_cases = frappe.get_all(
		"Case",
		filters={"care_team": ["in", team_names]},
		fields=["name", "status", "programme", "risk_level", "risk_factors", "patient"],
	)
	case_names = [c.name for c in all_cases]
	active_cases = [c for c in all_cases if c.status == "Active"]

	# Counts by programme
	cases_by_prog = {}
	for c in active_cases:
		prog = c.programme or "Unknown"
		cases_by_prog[prog] = cases_by_prog.get(prog, 0) + 1

	high_risk_count = sum(1 for c in active_cases if c.risk_level == "High")

	# Aggregate risk factors
	factor_counts = {}
	for c in active_cases:
		for factor in (c.risk_factors or "").split(" · "):
			factor = factor.strip()
			if factor:
				factor_counts[factor] = factor_counts.get(factor, 0) + 1
	top_risk_factors = [f"{k}: {v} cases" for k, v in sorted(factor_counts.items(), key=lambda x: -x[1])[:5]]

	# Pending referrals
	pending_referrals = (
		frappe.db.count(
			"Referral",
			{
				"case": ["in", case_names] if case_names else ["=", ""],
				"status": ["in", ["Pending", "Accepted"]],
			},
		)
		if case_names
		else 0
	)

	# Overdue tasks
	overdue_tasks = 0
	if case_names:
		open_tasks = frappe.get_all(
			"Task",
			filters={"case": ["in", case_names], "status": ["in", ["Open", "In Progress"]]},
			fields=["due_date"],
		)
		overdue_tasks = sum(
			1
			for t in open_tasks
			if t.due_date and frappe.utils.getdate(t.due_date) < frappe.utils.getdate(today)
		)

	# Encounters in last 7 days
	seven_days_ago = frappe.utils.add_days(today, -7)
	recent_encounters = (
		frappe.db.count(
			"Encounter",
			{"case": ["in", case_names] if case_names else ["=", ""], "encounter_dt": [">=", seven_days_ago]},
		)
		if case_names
		else 0
	)

	# Top-3 high-risk patient summaries (anonymised)
	top_patient_summaries = []
	high_risk_cases = sorted(
		[c for c in active_cases if c.risk_level == "High"],
		key=lambda c: c.name,
	)[:3]
	for c in high_risk_cases:
		pt_summary = frappe.db.get_value(
			"Patient",
			c.patient,
			["ai_summary", "ai_summary_generated_at"],
			as_dict=True,
		)
		if pt_summary and pt_summary.ai_summary and pt_summary.ai_summary_generated_at:
			snippet = pt_summary.ai_summary.split("\n\n— Generated")[0].strip()
			if snippet:
				top_patient_summaries.append(snippet)

	facility_type = frappe.db.get_value("Facility", facility_name, "facility_type")

	return {
		"facility_type": facility_type,
		"total_active_cases": len(active_cases),
		"high_risk_count": high_risk_count,
		"cases_by_programme": cases_by_prog,
		"pending_referrals": pending_referrals,
		"overdue_tasks": overdue_tasks,
		"recent_encounters_7d": recent_encounters,
		"top_risk_factors": top_risk_factors,
		"top_patient_summaries": top_patient_summaries,
	}
