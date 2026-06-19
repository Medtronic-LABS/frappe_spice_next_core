"""
CDSS endpoint — clinical decision support for a single encounter.

  uhis_next_core.api.cdss.recommend
  uhis_next_core.api.cdss.suggest_symptoms

Request (Flutter sends CdssInput.toJson()):
  {
    "patient_uuid": "<uuidv7>",
    "encounter_uuid": "<uuidv7>",
    "programme": "ncd",
    "observations": {
      "LOINC|8480-6": "152",
      "LOINC|8462-4": "96",
      "LOINC|2339-0": "8.4"
    },
    "symptoms": ["fever", "headache"],
    "patient_context": {"age_months": 540, "gender": "Female"}
  }

Response (Frappe wraps in {"message": ...}):
  {
    "recommendation": "Hypertension Stage 1. ...",
    "risk_level": "moderate",
    "rationale": {
      "guideline_ids": ["JNC8-HTN"],
      "source_observations": ["LOINC|8480-6"],
      "human_review_required": true,
      "model_version": "rule-v1",
      "confidence": 0.9,
      "summary": "..."
    },
    "findings": [
      {
        "rule_name": "...",
        "severity": "moderate",
        "action_type": "followup_7d",
        "action_label": "...",
        "rationale": "...",
        "guideline_id": "JNC8-HTN",
        "source": "rule"
      }
    ],
    "steps": [...],
    "source": "online"
  }

Layer 1 (always): evaluate Recommendation Rule DocType → deterministic findings.
Layer 2 (online only): Ollama enrichment via ai/client.py adds narrative paragraph.
Flutter never calls Ollama or Claude directly — only via this endpoint.
"""

import frappe
from ..ai.client import call_ai

_SEVERITY_ORDER = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
_SEVERITY_NAMES = ["Low", "Moderate", "High", "Critical"]


# ── public endpoints ──────────────────────────────────────────────────────────

@frappe.whitelist()
def recommend(payload=None, **kwargs):
	env = frappe.parse_json(payload) if payload else frappe.local.form_dict
	patient_uuid = env.get("patient_uuid") or ""
	encounter_uuid = env.get("encounter_uuid") or ""
	programme = env.get("programme") or ""
	observations = env.get("observations") or {}
	symptoms = env.get("symptoms") or []
	patient_context = env.get("patient_context") or {}

	visit_context = _build_visit_context(observations, symptoms, patient_context, programme)

	findings = _evaluate_recommendation_rules(visit_context, programme)

	overall_severity = "low"
	for f in findings:
		if _SEVERITY_ORDER.get(f["severity"], 0) > _SEVERITY_ORDER.get(overall_severity, 0):
			overall_severity = f["severity"]

	risk_level = overall_severity
	guideline_ids = list({f["guideline_id"] for f in findings if f.get("guideline_id")})
	source_obs = list({k for k in observations})
	steps = _build_steps(findings, risk_level)
	summary = _build_summary(findings, risk_level, symptoms)

	recommendation = _build_recommendation(risk_level)

	ai_narrative = _ai_enrich(patient_uuid, encounter_uuid, programme, observations, findings, risk_level, symptoms)
	if ai_narrative:
		recommendation = ai_narrative

	result = {
		"recommendation": recommendation,
		"risk_level": risk_level,
		"rationale": {
			"guideline_ids": guideline_ids,
			"source_observations": source_obs,
			"human_review_required": True,
			"model_version": "rule-v1",
			"confidence": 0.9 if findings else 0.7,
			"summary": summary,
		},
		"findings": findings,
		"steps": steps,
		"source": "online",
	}

	_store_cdss_result(encounter_uuid, result)
	return result


@frappe.whitelist()
def suggest_symptoms(payload=None, **kwargs):
	"""Return AI-suggested likely symptoms based on patient history.

	Used in Step 2 (Symptom Discovery) to highlight — not pre-select — symptoms
	the AI thinks are likely given the patient's prior encounters.

	Response:
	  [{"symptom_question_id": "ncd_symptom_headache", "label": "Headache", "confidence": 0.85}]
	"""
	env = frappe.parse_json(payload) if payload else frappe.local.form_dict
	patient_uuid = env.get("patient_uuid") or ""
	programme = env.get("programme") or ""

	if not patient_uuid:
		return []

	history = _get_patient_history(patient_uuid)
	if not history:
		return []

	symptom_questions = frappe.get_all(
		"Clinical Question",
		filters={"question_type": "symptom", "form_group": programme} if programme else {"question_type": "symptom"},
		fields=["name", "label"],
		order_by="sequence asc",
	)
	if not symptom_questions:
		return []

	symptom_labels = [q.label for q in symptom_questions]
	question_map = {q.label: q.name for q in symptom_questions}

	system = (
		"You are a clinical decision support assistant. Based on the patient's history, "
		"suggest which symptoms from the provided list are most likely to be present today. "
		"Return ONLY a JSON array of objects with keys: label (string), confidence (0.0-1.0). "
		"Include only symptoms with confidence >= 0.6. Maximum 5 suggestions."
	)
	user_content = (
		f"Programme: {programme}\n"
		f"Patient history (last 3 encounters):\n{history}\n\n"
		f"Available symptoms: {', '.join(symptom_labels)}\n\n"
		"Return JSON array only."
	)

	raw = call_ai(system, user_content)
	if not raw:
		return []

	try:
		import json as _json
		suggestions = _json.loads(raw) if isinstance(raw, str) else raw
		result = []
		for s in suggestions[:5]:
			label = s.get("label", "")
			question_id = question_map.get(label)
			if question_id:
				result.append({
					"symptom_question_id": question_id,
					"label": label,
					"confidence": float(s.get("confidence", 0.7)),
				})
		return result
	except Exception:
		return []


# ── rule evaluation ───────────────────────────────────────────────────────────

def _evaluate_recommendation_rules(visit_context, programme=None):
	"""Evaluate all active Recommendation Rules against visit context.

	Returns a list of finding dicts — one per matching rule.
	"""
	filters = {"active": 1}
	rules = frappe.get_all(
		"Recommendation Rule",
		filters=filters,
		fields=["name", "rule_name", "programme", "condition_logic", "severity", "action_type", "action_label", "rationale_template", "guideline_id", "priority"],
		order_by="priority desc",
	)

	findings = []
	for rule in rules:
		if rule.programme and rule.programme != programme:
			continue

		conditions = frappe.get_all(
			"Rule Condition",
			filters={"parenttype": "Recommendation Rule", "parent": rule.name},
			fields=["condition_field", "condition_operator", "condition_value"],
			order_by="idx asc",
		)
		if not conditions:
			continue

		logic = rule.condition_logic or "AND"
		if not _evaluate_conditions(conditions, logic, visit_context):
			continue

		rationale = _interpolate_template(rule.rationale_template or "", visit_context)
		findings.append({
			"rule_name": rule.rule_name,
			"severity": rule.severity,
			"action_type": rule.action_type,
			"action_label": rule.action_label or "",
			"rationale": rationale,
			"guideline_id": rule.guideline_id or "",
			"source": "rule",
			"confidence": 1.0,
		})

	return findings


def _evaluate_conditions(conditions, logic, context):
	results = [_evaluate_single(c, context) for c in conditions]
	return any(results) if logic == "OR" else all(results)


def _evaluate_single(condition, context):
	field = condition.condition_field
	op = condition.condition_operator
	threshold = condition.condition_value

	raw = context.get(field)
	if raw is None:
		return False

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
	if op == "contains":
		return rhs_s in lhs_s
	return False


# ── context + helpers ─────────────────────────────────────────────────────────

def _build_visit_context(observations, symptoms, patient_context, programme):
	ctx = {}
	ctx.update(observations)
	for s in symptoms:
		ctx[f"symptom_{s}"] = True
	ctx.update(patient_context)
	ctx["programme"] = programme
	return ctx


def _interpolate_template(template, context):
	if not template:
		return ""
	try:
		return template.format_map({k: v for k, v in context.items()})
	except (KeyError, ValueError):
		return template


def _get_patient_history(patient_uuid):
	encounters = frappe.get_all(
		"Encounter",
		filters={"case": ["in", frappe.get_all("Case", filters={"patient": patient_uuid}, pluck="name")]},
		fields=["name", "encounter_dt"],
		order_by="encounter_dt desc",
		limit=3,
	)
	if not encounters:
		return ""

	lines = []
	for enc in encounters:
		obs_rows = frappe.get_all(
			"Observation",
			filters={"encounter": enc.name},
			fields=["concept", "value"],
		)
		if obs_rows:
			obs_str = ", ".join(f"{o.concept}={o.value}" for o in obs_rows)
			lines.append(f"- {enc.encounter_dt}: {obs_str}")

	return "\n".join(lines)


def _build_recommendation(risk_level):
	return {
		"critical": "Critical findings. Refer to facility immediately. Do not leave patient alone.",
		"high": "High risk detected. Refer to health facility today. Do not leave patient alone.",
		"moderate": "Moderate risk. Schedule follow-up within 7 days. Counsel on lifestyle changes.",
		"low": "No immediate concern detected. Routine follow-up as scheduled.",
	}.get(risk_level, "No immediate concern detected.")


def _build_summary(findings, risk_level, symptoms=None):
	parts = []
	if symptoms:
		parts.append(f"Reported symptoms: {', '.join(symptoms)}.")
	if findings:
		labels = [f["action_label"] or f["rule_name"] for f in findings]
		parts.append(f"{len(findings)} rule(s) matched. Overall risk: {risk_level}. Actions: {'; '.join(labels)}.")
	else:
		parts.append("No clinical rules matched. No abnormal thresholds detected.")
	return " ".join(parts)


def _build_steps(findings, risk_level):
	steps = []
	if risk_level in ("critical", "high"):
		steps.append("Contact supervising clinician before leaving patient.")
	seen = set()
	for f in findings:
		label = f.get("action_label")
		if label and label not in seen:
			steps.append(label)
			seen.add(label)
	if not steps:
		steps.append("Continue routine monitoring per programme schedule.")
	steps.append("Document all findings in the visit form before syncing.")
	return steps


def _ai_enrich(patient_uuid, encounter_uuid, programme, observations, findings, risk_level, symptoms=None):
	if not findings and not symptoms:
		return None

	finding_lines = "\n".join(
		f"  - {f['rule_name']}: {f['severity']} — {f['rationale']}"
		for f in findings
	) if findings else "  None"
	symptom_line = f"Reported symptoms: {', '.join(symptoms)}" if symptoms else "None reported"
	system = (
		"You are a clinical decision support assistant for community health workers in Bangladesh. "
		"Provide a brief (2-3 sentence), plain-English recommendation based on the rule findings. "
		"Always recommend human clinician review for moderate, high, or critical risk. "
		"Do not diagnose — describe findings and next steps only."
	)
	user_content = (
		f"Programme: {programme}\n"
		f"Risk level: {risk_level}\n"
		f"Symptoms: {symptom_line}\n"
		f"Clinical rule findings:\n{finding_lines}\n\n"
		"Provide a brief clinical recommendation for the community health worker."
	)
	return call_ai(system, user_content)


def _store_cdss_result(encounter_uuid, result):
	if not encounter_uuid:
		return
	if not frappe.db.exists("Encounter", encounter_uuid):
		return
	try:
		import json
		frappe.db.set_value(
			"Encounter",
			encounter_uuid,
			"cdss_result_json",
			json.dumps(result),
			update_modified=False,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CDSS: failed to store result on encounter")
