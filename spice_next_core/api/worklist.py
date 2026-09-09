"""
Worklist endpoint — returns today's priority-sorted visit list for the SK's catchment.

  spice_next_core.api.worklist.today   GET (no body required)

Response (Frappe wraps in {"message": ...}):
  [
    {
      "patient_uuid": "<uuidv7>",
      "patient_name": "Fatema Khatun",
      "patient_name_bn": "ফাতেমা খাতুন",
      "programme": "ncd",
      "priority": "high",          # low | normal | high
      "priority_score": 230,       # sum of matching Priority Rule weights
      "priority_reasons": ["BP uncontrolled — 3+ visits", "No visit in 30+ days"],
      "scheduled_date": "2026-06-17",
      "care_gaps": ["BP uncontrolled", "Medication not refilled"]
    },
    ...
  ]

Sort order: priority_score DESC, then scheduled_date ASC (overdue surfaces early).
Falls back to risk_level-based score if no Priority Rules are configured.
"""

import frappe
from datetime import date
from .sync import get_user_catchment

_PRIORITY_RULES_CACHE = None


def _load_priority_rules():
	global _PRIORITY_RULES_CACHE
	if _PRIORITY_RULES_CACHE is None:
		_PRIORITY_RULES_CACHE = frappe.get_all(
			"Priority Rule",
			filters={"active": 1},
			fields=["name", "rule_name", "programme", "condition_field", "condition_operator", "condition_value", "weight", "reason_label"],
			order_by="weight desc",
		)
	return _PRIORITY_RULES_CACHE


def _compute_priority_score(case_context, programme, priority_rules):
	total = 0
	reasons = []
	for rule in priority_rules:
		if rule.programme and rule.programme != programme:
			continue
		raw = case_context.get(rule.condition_field)
		if raw is None:
			continue
		if _evaluate_rule_condition(raw, rule.condition_operator, rule.condition_value):
			total += rule.weight or 0
			if rule.reason_label:
				reasons.append(rule.reason_label)
	return total, reasons


def _evaluate_rule_condition(raw, op, threshold):
	try:
		lhs = float(raw)
		rhs = float(threshold)
		if op == "=":
			return lhs == rhs
		if op == "!=":
			return lhs != rhs
		if op == ">":
			return lhs > rhs
		if op == ">=":
			return lhs >= rhs
		if op == "<":
			return lhs < rhs
		if op == "<=":
			return lhs <= rhs
	except (ValueError, TypeError):
		pass
	lhs_s = str(raw).strip().lower()
	rhs_s = str(threshold).strip().lower()
	if op == "=":
		return lhs_s == rhs_s
	if op == "!=":
		return lhs_s != rhs_s
	if op == "in":
		return lhs_s in [v.strip().lower() for v in rhs_s.split(",")]
	return False


def _build_case_context(case):
	today = frappe.utils.today()
	last_enc = case.last_encounter_dt
	days_since = 0
	if last_enc:
		try:
			delta = frappe.utils.date_diff(today, str(last_enc)[:10])
			days_since = max(0, delta)
		except Exception:
			pass
	else:
		days_since = 999

	open_referrals = frappe.db.count("Referral", {"case": case.name, "status": "Open"}) or 0

	return {
		"risk_level": (case.risk_level or "Low").lower(),
		"days_since_last_encounter": days_since,
		"programme": (case.programme or "").lower(),
		"open_referral_count": open_referrals,
	}


@frappe.whitelist(methods=["POST"])
def today():
	catchment = get_user_catchment(frappe.session.user)

	geo_filter = {}
	if catchment is not None:
		if not catchment:
			return []
		geo_filter = {"geography_node": ["in", list(catchment)]}

	case_filters = {"status": ["in", ["Active", "Referred"]]}
	case_filters.update(geo_filter)

	cases = frappe.get_all(
		"Case",
		filters=case_filters,
		fields=[
			"name",
			"patient",
			"programme",
			"risk_level",
			"risk_factors",
			"opened_on",
			"last_encounter_dt",
		],
		order_by="opened_on asc",
		limit=100,
	)

	if not cases:
		return []

	patient_names = list({c.patient for c in cases})
	patients = frappe.get_all(
		"Patient",
		filters={"name": ["in", patient_names]},
		fields=["name", "client_uuid", "full_name", "full_name_bn"],
	)
	patient_map = {p.name: p for p in patients}
	priority_rules = _load_priority_rules()

	entries = []
	for case in cases:
		patient = patient_map.get(case.patient)
		if not patient:
			continue

		care_gaps = _parse_care_gaps(case.risk_factors)
		case_ctx = _build_case_context(case)
		programme = case.programme or ""

		score, reasons = _compute_priority_score(case_ctx, programme, priority_rules)

		# Fallback: if no rules configured, derive score from risk_level
		if not priority_rules:
			score = {"high": 100, "moderate": 50, "low": 10}.get((case.risk_level or "Low").lower(), 10)
			reasons = care_gaps

		priority = _score_to_priority(score)

		entries.append(
			{
				"patient_uuid": patient.client_uuid or patient.name,
				"patient_name": patient.full_name or "",
				"patient_name_bn": patient.full_name_bn or "",
				"programme": programme,
				"priority": priority,
				"priority_score": score,
				"priority_reasons": reasons,
				"scheduled_date": str(case.opened_on or frappe.utils.today()),
				"care_gaps": care_gaps,
			}
		)

	entries.sort(key=lambda e: (-e["priority_score"], e["scheduled_date"]))
	return entries


@frappe.whitelist(methods=["POST"])
def notifications():
	"""Pending high-priority referrals for the logged-in SK's catchment."""
	catchment = get_user_catchment(frappe.session.user)

	geo_filter = {}
	if catchment is not None:
		if not catchment:
			return {"items": [], "total": 0}
		geo_filter = {"geography_node": ["in", list(catchment)]}

	referral_filters = {"status": "Referred", "risk_level": "High"}
	referral_filters.update(geo_filter)

	referrals = frappe.get_all(
		"Case",
		filters=referral_filters,
		fields=["name", "patient", "programme", "opened_on"],
		limit=50,
	)

	items = []
	for r in referrals:
		patient_name = (
			frappe.db.get_value("Patient", r.patient, "full_name") or r.patient
		)
		items.append(
			{
				"id": r.name,
				"type": "referral",
				"title": patient_name,
				"programme": r.programme or "",
				"created_at": str(r.opened_on or frappe.utils.today()),
			}
		)

	return {"items": items, "total": len(items)}


def _score_to_priority(score):
	if score >= 100:
		return "high"
	if score >= 40:
		return "normal"
	return "low"


def _risk_to_priority(risk_level):
	return {
		"High": "high",
		"Moderate": "normal",
		"Low": "low",
	}.get(risk_level or "Low", "low")


def _parse_care_gaps(risk_factors):
	if not risk_factors:
		return []
	return [f.strip() for f in risk_factors.split("·") if f.strip()]
